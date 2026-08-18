# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import inspect
from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import broker_holding_recorder
from broker_holding_recorder import (
    PRODUCTION_RECOVERY_REVIEW_DUPLICATE,
    PRODUCTION_RECOVERY_REVIEW_INVALID,
    PRODUCTION_RECOVERY_REVIEW_READ_BACK_FAILED,
    PRODUCTION_RECOVERY_REVIEW_STORAGE_FAILED,
    PRODUCTION_RECOVERY_REVIEW_WRITTEN,
    resolve_account_holding_snapshot_failures,
    write_production_recovery_review,
)
from operator_reconciliation_service import collect_operator_reconciliation_items
from production_recovery_contract import (
    ACCOUNT_COLLECTING,
    ACCOUNT_COMPLETED,
    ACCOUNT_RECONCILING,
    ACCOUNT_REVIEW_REQUIRED,
    BrokerAccountSnapshot,
    BrokerHoldingSnapshotItem,
    BrokerSnapshotPart,
    STOCK_RESTORED,
    STOCK_REVIEW_REQUIRED,
    create_recovery_session_identity,
)
from production_recovery_state_registry import (
    RECOVERY_ACCOUNT_REVIEW_REQUIRED,
    RECOVERY_COMPLETED,
    RECOVERY_CONTEXT_MISSING,
    RECOVERY_NOT_STARTED,
    RECOVERY_STALE_SESSION,
    RECOVERY_STOCK_REVIEW_REQUIRED,
    ProductionRecoveryStateRegistry,
    check_production_recovery_gate,
    reconcile_production_recovery_snapshot,
)
from production_recovery_timer_lifecycle import (
    start_recovery_bound_timers,
    stop_recovery_bound_timers,
)


def _write_runtime(path: Path, field: str) -> None:
    path.write_text(
        json.dumps({"version": 1, "updated_at": None, field: []}, indent=2) + "\n",
        encoding="utf-8",
    )


def _identity(suffix: str = "A"):
    return create_recovery_session_identity(
        login_session_id=f"LOGIN_{suffix}",
        account_no="1234567890",
        trading_day="2026-07-27",
        requested_at=f"2026-07-27T09:00:0{0 if suffix == 'A' else 1}",
    )


def _review(identity, *, stock_code: str = "005930", reason_code: str = "BROKER_ONLY_ORDER"):
    return {
        "account_no": identity.account_no,
        "trading_day": identity.trading_day,
        "login_session_id": identity.login_session_id,
        "recovery_session_id": identity.recovery_session_id,
        "stock_code": stock_code,
        "reason_code": reason_code,
        "detected_at": "2026-07-27T09:01:00",
        "broker_evidence": {
            "broker_order_no": "BROKER-1",
            "quantity": 3,
        },
        "runtime_evidence": {"matches": []},
        "status": "OPEN",
    }


def _account_snapshot(identity, *, holding_quantity: int = 0):
    holdings = ()
    if holding_quantity:
        holdings = (
            BrokerHoldingSnapshotItem(
                account_no=identity.account_no,
                stock_code="005930",
                stock_name="삼성전자",
                holding_quantity=holding_quantity,
                available_quantity=holding_quantity,
                average_price=Decimal("70000"),
                current_price=Decimal("71000"),
                evaluation_amount=Decimal("71000") * holding_quantity,
                profit_loss=Decimal("1000") * holding_quantity,
                profit_rate=Decimal("1.4"),
            ),
        )
    return BrokerAccountSnapshot(
        account_no=identity.account_no,
        trading_day=identity.trading_day,
        requested_at=identity.requested_at,
        completed_at="2026-07-27T09:01:00",
        request_id="ACCOUNT",
        recovery_session_id=identity.recovery_session_id,
        is_complete=True,
        holdings=holdings,
        open_orders=(),
        source="TEST",
    )


class RecoveryReviewWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.holdings = root / "broker_holdings.json"
        self.queue = root / "order_queue.json"
        self.fills = root / "fills.json"
        self.positions = root / "positions.json"
        _write_runtime(self.holdings, "holdings")
        _write_runtime(self.queue, "orders")
        _write_runtime(self.fills, "fills")
        _write_runtime(self.positions, "positions")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_writes_broker_only_order_without_queue_record(self) -> None:
        result = write_production_recovery_review(_review(_identity()), self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, result["status"])
        self.assertTrue(result["post_write_verified"])
        self.assertEqual([], json.loads(self.queue.read_text(encoding="utf-8"))["orders"])

        rows = collect_operator_reconciliation_items(
            queue_path=self.queue,
            fills_path=self.fills,
            positions_path=self.positions,
            broker_holdings_path=self.holdings,
        )
        self.assertEqual(1, rows["summary"]["production_recovery"])
        item = rows["items"][0]
        self.assertEqual("PRODUCTION_RECOVERY", item["source_type"])
        self.assertEqual("BROKER_ONLY_ORDER", item["reason"])
        self.assertEqual("BROKER-1", item["broker_order_no"])

    def test_account_level_review_uses_blank_stock_code(self) -> None:
        result = write_production_recovery_review(
            _review(
                _identity(),
                stock_code="",
                reason_code="INCOMPLETE_BROKER_SNAPSHOT",
            ),
            self.holdings,
        )
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, result["status"])
        self.assertEqual("", result["record"]["stock_code"])

    def test_deduplicates_same_session_but_allows_new_session(self) -> None:
        first = write_production_recovery_review(_review(_identity("A")), self.holdings)
        duplicate = write_production_recovery_review(_review(_identity("A")), self.holdings)
        other_session = write_production_recovery_review(_review(_identity("B")), self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, first["status"])
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_DUPLICATE, duplicate["status"])
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, other_session["status"])
        data = json.loads(self.holdings.read_text(encoding="utf-8"))
        self.assertEqual(2, len(data["production_recovery_reviews"]))

    def test_duplicate_open_merges_new_broker_result_evidence(self) -> None:
        identity = _identity("A")
        first_review = _review(
            identity,
            stock_code="",
            reason_code="HOLDING_SNAPSHOT_FAILED",
        )
        first_review["broker_evidence"] = {
            "error": "CommRqData failed",
            "errors": [],
        }
        first = write_production_recovery_review(first_review, self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, first["status"])

        repeated_review = dict(first_review)
        repeated_review["broker_evidence"] = {
            "error": "CommRqData failed",
            "errors": [],
            "result": -202,
        }
        updated = write_production_recovery_review(repeated_review, self.holdings)

        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, updated["status"])
        data = json.loads(self.holdings.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["production_recovery_reviews"]))
        saved = data["production_recovery_reviews"][0]
        self.assertEqual("OPEN", saved["status"])
        self.assertEqual(-202, saved["broker_evidence"]["result"])
        self.assertEqual(first_review["detected_at"], saved["detected_at"])

        duplicate = write_production_recovery_review(repeated_review, self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_DUPLICATE, duplicate["status"])

    def test_rejects_missing_identity_and_missing_storage(self) -> None:
        invalid = _review(_identity())
        invalid["login_session_id"] = ""
        result = write_production_recovery_review(invalid, self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_INVALID, result["status"])
        missing = write_production_recovery_review(
            _review(_identity()),
            self.holdings.with_name("missing.json"),
        )
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_STORAGE_FAILED, missing["status"])

    def test_normalizes_evidence_and_reports_read_back_failure(self) -> None:
        review = _review(_identity())
        review["broker_evidence"] = {"set_value": {"B", "A"}}
        written = write_production_recovery_review(review, self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, written["status"])
        self.assertEqual(["A", "B"], written["record"]["broker_evidence"]["set_value"])

        original_read = broker_holding_recorder._read_holdings
        calls = 0

        def read_then_fail(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_read(path)
            return {}, broker_holding_recorder._blocked("test", "read-back failed")

        with mock.patch.object(
            broker_holding_recorder,
            "_read_holdings",
            side_effect=read_then_fail,
        ):
            failed = write_production_recovery_review(
                _review(_identity("B"), stock_code="006400"),
                self.holdings,
            )
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_READ_BACK_FAILED, failed["status"])

    def test_resolves_existing_review_without_creating_duplicate(self) -> None:
        review = _review(_identity())
        self.assertEqual(
            PRODUCTION_RECOVERY_REVIEW_WRITTEN,
            write_production_recovery_review(review, self.holdings)["status"],
        )
        review["status"] = "RESOLVED"
        resolved = write_production_recovery_review(review, self.holdings)
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, resolved["status"])
        data = json.loads(self.holdings.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["production_recovery_reviews"]))
        self.assertEqual("RESOLVED", data["production_recovery_reviews"][0]["status"])

    def test_valid_holding_snapshot_resolves_same_login_account_failure(self) -> None:
        failed_identity = _identity("A")
        successful_identity = create_recovery_session_identity(
            login_session_id=failed_identity.login_session_id,
            account_no=failed_identity.account_no,
            trading_day=failed_identity.trading_day,
            requested_at="2026-07-27T09:00:02",
        )
        failure = _review(
            failed_identity,
            stock_code="",
            reason_code="HOLDING_SNAPSHOT_FAILED",
        )
        failure["broker_evidence"] = {
            "error": "CommRqData failed",
            "result": -202,
        }
        write_production_recovery_review(failure, self.holdings)

        result = resolve_account_holding_snapshot_failures(
            account_no=successful_identity.account_no,
            trading_day=successful_identity.trading_day,
            login_session_id=successful_identity.login_session_id,
            successful_recovery_session_id=successful_identity.recovery_session_id,
            broker_holdings_path=self.holdings,
        )

        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, result["status"])
        self.assertEqual(1, result["resolved_count"])
        data = json.loads(self.holdings.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["production_recovery_reviews"]))
        saved = data["production_recovery_reviews"][0]
        self.assertEqual("RESOLVED", saved["status"])
        self.assertEqual(-202, saved["broker_evidence"]["result"])
        self.assertEqual(failed_identity.recovery_session_id, saved["recovery_session_id"])
        self.assertEqual(
            successful_identity.recovery_session_id,
            saved["runtime_evidence"]["resolution"]["successful_recovery_session_id"],
        )
        reread = collect_operator_reconciliation_items(
            queue_path=self.queue,
            fills_path=self.fills,
            positions_path=self.positions,
            broker_holdings_path=self.holdings,
        )
        self.assertEqual(0, reread["summary"]["production_recovery"])

    def test_repeated_holding_snapshot_failure_remains_open(self) -> None:
        first = _identity("A")
        second = create_recovery_session_identity(
            login_session_id=first.login_session_id,
            account_no=first.account_no,
            trading_day=first.trading_day,
            requested_at="2026-07-27T09:00:02",
        )
        for identity in (first, second):
            result = write_production_recovery_review(
                _review(
                    identity,
                    stock_code="",
                    reason_code="HOLDING_SNAPSHOT_FAILED",
                ),
                self.holdings,
            )
            self.assertEqual(PRODUCTION_RECOVERY_REVIEW_WRITTEN, result["status"])

        saved = json.loads(self.holdings.read_text(encoding="utf-8"))[
            "production_recovery_reviews"
        ]
        self.assertEqual(2, len(saved))
        self.assertTrue(all(item["status"] == "OPEN" for item in saved))

    def test_holding_failure_resolution_is_scoped_and_preserves_history(self) -> None:
        matching = _identity("A")
        other_login = _identity("B")
        other_account = create_recovery_session_identity(
            login_session_id=matching.login_session_id,
            account_no="9999999999",
            trading_day=matching.trading_day,
            requested_at="2026-07-27T09:00:03",
        )
        records = (
            _review(matching, stock_code="", reason_code="HOLDING_SNAPSHOT_FAILED"),
            _review(other_login, stock_code="", reason_code="HOLDING_SNAPSHOT_FAILED"),
            _review(other_account, stock_code="", reason_code="HOLDING_SNAPSHOT_FAILED"),
            _review(matching, stock_code="", reason_code="OPEN_ORDER_SNAPSHOT_FAILED"),
        )
        for record in records:
            write_production_recovery_review(record, self.holdings)

        successful = create_recovery_session_identity(
            login_session_id=matching.login_session_id,
            account_no=matching.account_no,
            trading_day=matching.trading_day,
            requested_at="2026-07-27T09:00:04",
        )
        result = resolve_account_holding_snapshot_failures(
            account_no=successful.account_no,
            trading_day=successful.trading_day,
            login_session_id=successful.login_session_id,
            successful_recovery_session_id=successful.recovery_session_id,
            broker_holdings_path=self.holdings,
        )

        self.assertEqual(1, result["resolved_count"])
        saved = json.loads(self.holdings.read_text(encoding="utf-8"))[
            "production_recovery_reviews"
        ]
        self.assertEqual(4, len(saved))
        self.assertEqual(1, sum(item["status"] == "RESOLVED" for item in saved))
        self.assertEqual(3, sum(item["status"] == "OPEN" for item in saved))

    def test_repeated_resolution_is_idempotent(self) -> None:
        identity = _identity("A")
        write_production_recovery_review(
            _review(identity, stock_code="", reason_code="HOLDING_SNAPSHOT_FAILED"),
            self.holdings,
        )
        kwargs = {
            "account_no": identity.account_no,
            "trading_day": identity.trading_day,
            "login_session_id": identity.login_session_id,
            "successful_recovery_session_id": "RECOVERY_SUCCESS",
            "broker_holdings_path": self.holdings,
        }
        first = resolve_account_holding_snapshot_failures(**kwargs)
        second = resolve_account_holding_snapshot_failures(**kwargs)
        self.assertEqual(1, first["resolved_count"])
        self.assertEqual(PRODUCTION_RECOVERY_REVIEW_DUPLICATE, second["status"])
        self.assertEqual(0, second["resolved_count"])


class RecoveryMainWindowResolutionConnectionTest(unittest.TestCase):
    def test_current_failure_path_has_no_stock_state_fanout(self) -> None:
        import gui_windows

        identity = _identity("A")
        owner = SimpleNamespace(
            _stop_production_recovery_timers=mock.Mock(),
            _record_production_recovery_review=mock.Mock(),
            _production_recovery_status_result=mock.Mock(),
        )
        with (
            mock.patch.object(
                gui_windows.production_recovery_registry,
                "fail_account",
            ) as fail_account,
            mock.patch.object(gui_windows, "append_owner_event_once"),
            mock.patch.object(
                gui_windows,
                "emergency_update_runtime_stock_status",
            ) as stock_writer,
        ):
            gui_windows.MainWindow._fail_production_recovery(
                owner,
                identity,
                "HOLDINGS_SNAPSHOT_FAILED",
                broker_evidence={"result": -202},
            )

        fail_account.assert_called_once_with(identity)
        owner._record_production_recovery_review.assert_called_once_with(
            identity,
            stock_code="",
            reason_code="HOLDINGS_SNAPSHOT_FAILED",
            broker_evidence={"result": -202},
        )
        stock_writer.assert_not_called()

    def test_main_window_recovery_writer_persists_minus_202(self) -> None:
        import gui_windows

        identity = _identity("A")
        with tempfile.TemporaryDirectory() as temp_dir:
            holdings = Path(temp_dir) / "broker_holdings.json"
            _write_runtime(holdings, "holdings")
            with mock.patch.object(
                gui_windows,
                "RECOVERY_BROKER_HOLDINGS_PATH",
                holdings,
            ):
                gui_windows.MainWindow._record_production_recovery_review(
                    SimpleNamespace(),
                    identity,
                    stock_code="",
                    reason_code="HOLDINGS_SNAPSHOT_FAILED",
                    broker_evidence={
                        "error": "CommRqData failed",
                        "errors": [],
                        "result": -202,
                    },
                )

            saved = json.loads(holdings.read_text(encoding="utf-8"))[
                "production_recovery_reviews"
            ]
            self.assertEqual(1, len(saved))
            self.assertEqual(-202, saved[0]["broker_evidence"]["result"])

    def test_valid_holding_callback_connects_existing_resolution_writer(self) -> None:
        import gui_windows

        identity = _identity("A")
        snapshot = BrokerSnapshotPart(
            kind="HOLDINGS",
            account_no=identity.account_no,
            trading_day=identity.trading_day,
            requested_at=identity.requested_at,
            completed_at="2026-07-27T09:01:00",
            request_id="HOLDINGS",
            recovery_session_id=identity.recovery_session_id,
            is_complete=True,
            items=(),
            source="TEST",
        )
        owner = SimpleNamespace(
            _production_recovery_identity=identity,
            _production_recovery_parts={},
            _request_production_recovery_snapshot=mock.Mock(return_value=True),
        )
        resolution = {
            "status": PRODUCTION_RECOVERY_REVIEW_WRITTEN,
            "resolved_count": 1,
        }
        with mock.patch.object(
            gui_windows,
            "resolve_account_holding_snapshot_failures",
            return_value=resolution,
        ) as resolver:
            gui_windows.MainWindow._on_production_recovery_snapshot(
                owner,
                identity,
                "HOLDINGS",
                {"ok": True, "snapshot": snapshot},
            )

        resolver.assert_called_once_with(
            account_no=identity.account_no,
            trading_day=identity.trading_day,
            login_session_id=identity.login_session_id,
            successful_recovery_session_id=identity.recovery_session_id,
            broker_holdings_path=gui_windows.RECOVERY_BROKER_HOLDINGS_PATH,
        )
        self.assertEqual(
            resolution,
            owner._production_recovery_holding_failure_resolution_result,
        )
        owner._request_production_recovery_snapshot.assert_called_once_with(
            identity,
            "OPEN_ORDERS",
        )

    def test_failed_holding_callback_preserves_commrqdata_result_code(self) -> None:
        import gui_windows

        identity = _identity("A")
        owner = SimpleNamespace(
            _production_recovery_identity=identity,
            _production_recovery_parts={},
            _fail_production_recovery=mock.Mock(),
        )
        gui_windows.MainWindow._on_production_recovery_snapshot(
            owner,
            identity,
            "HOLDINGS",
            {"ok": False, "error": "CommRqData failed", "result": -202},
        )
        owner._fail_production_recovery.assert_called_once_with(
            identity,
            "HOLDINGS_SNAPSHOT_FAILED",
            broker_evidence={
                "errors": [],
                "error": "CommRqData failed",
                "result": -202,
            },
        )


class RecoveryRegistryAndGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ProductionRecoveryStateRegistry()
        self.identity = _identity()

    def test_lifecycle_gate_and_idempotency(self) -> None:
        self.assertTrue(self.registry.begin_recovery(self.identity)["ok"])
        self.assertEqual(
            "UNCHANGED",
            self.registry.begin_recovery(self.identity)["status"],
        )
        self.assertTrue(self.registry.mark_collecting(self.identity)["ok"])
        self.assertEqual(
            ACCOUNT_COLLECTING,
            self.registry.snapshot().account_status,
        )
        self.assertTrue(self.registry.mark_reconciling(self.identity)["ok"])
        self.assertEqual(
            ACCOUNT_RECONCILING,
            self.registry.snapshot().account_status,
        )
        self.assertTrue(
            self.registry.set_stock_result(
                self.identity,
                stock_code="005930",
                stock_status=STOCK_RESTORED,
            )["ok"]
        )
        self.assertEqual(
            "UNCHANGED",
            self.registry.set_stock_result(
                self.identity,
                stock_code="005930",
                stock_status=STOCK_RESTORED,
            )["status"],
        )
        self.assertTrue(self.registry.complete_account(self.identity)["ok"])
        self.assertEqual(ACCOUNT_COMPLETED, self.registry.snapshot().account_status)
        decision = check_production_recovery_gate(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            stock_code="005930",
            recovery_session_id=self.identity.recovery_session_id,
            caller_name="TEST",
            registry=self.registry,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(RECOVERY_COMPLETED, decision.reason_code)

    def test_review_cannot_be_overwritten_and_blocks_gate(self) -> None:
        self.registry.begin_recovery(self.identity)
        self.registry.mark_collecting(self.identity)
        self.registry.mark_reconciling(self.identity)
        self.registry.set_stock_result(
            self.identity,
            stock_code="005930",
            stock_status=STOCK_REVIEW_REQUIRED,
            review_required=True,
            reason_codes=("BROKER_ONLY_ORDER",),
        )
        self.registry.complete_account(self.identity)
        self.assertEqual(ACCOUNT_REVIEW_REQUIRED, self.registry.snapshot().account_status)
        overwrite = self.registry.set_stock_result(
            self.identity,
            stock_code="005930",
            stock_status=STOCK_RESTORED,
        )
        self.assertFalse(overwrite["ok"])
        decision = check_production_recovery_gate(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            stock_code="005930",
            registry=self.registry,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(RECOVERY_STOCK_REVIEW_REQUIRED, decision.reason_code)

    def test_review_stock_does_not_block_restored_sibling(self) -> None:
        self.registry.begin_recovery(self.identity)
        self.registry.mark_collecting(self.identity)
        self.registry.mark_reconciling(self.identity)
        self.registry.set_stock_result(
            self.identity,
            stock_code="000660",
            stock_status=STOCK_REVIEW_REQUIRED,
            review_required=True,
            reason_codes=("EMERGENCY_STOPPED",),
        )
        self.registry.set_stock_result(
            self.identity,
            stock_code="005930",
            stock_status=STOCK_RESTORED,
        )
        self.registry.complete_account(self.identity)
        self.assertEqual(
            ACCOUNT_REVIEW_REQUIRED,
            self.registry.snapshot().account_status,
        )

        restored = check_production_recovery_gate(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            stock_code="005930",
            registry=self.registry,
        )
        quarantined = check_production_recovery_gate(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            stock_code="000660",
            registry=self.registry,
        )

        self.assertTrue(restored.allowed)
        self.assertEqual(RECOVERY_COMPLETED, restored.reason_code)
        self.assertFalse(quarantined.allowed)
        self.assertEqual(
            RECOVERY_STOCK_REVIEW_REQUIRED,
            quarantined.reason_code,
        )

    def test_stale_mutation_and_invalidation_fail_closed(self) -> None:
        self.registry.begin_recovery(self.identity)
        stale = self.registry.mark_collecting(_identity("B"))
        self.assertFalse(stale["ok"])
        self.assertEqual(RECOVERY_STALE_SESSION, stale["status"])
        self.registry.invalidate("relogin")
        decision = check_production_recovery_gate(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            stock_code="005930",
            registry=self.registry,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(RECOVERY_NOT_STARTED, decision.reason_code)

    def test_missing_context_and_registry_exception_fail_closed(self) -> None:
        missing = check_production_recovery_gate(
            login_session_id="",
            account_no="",
            trading_day="",
            stock_code="",
            registry=self.registry,
        )
        self.assertEqual(RECOVERY_CONTEXT_MISSING, missing.reason_code)
        broken = mock.Mock()
        broken.snapshot.side_effect = RuntimeError("broken")
        failed = check_production_recovery_gate(
            login_session_id="LOGIN",
            account_no="ACCOUNT",
            trading_day="2026-07-27",
            stock_code="005930",
            registry=broken,
        )
        self.assertFalse(failed.allowed)
        self.assertEqual(RECOVERY_CONTEXT_MISSING, failed.reason_code)

    def test_snapshot_reconciliation_restores_matching_stock(self) -> None:
        self.registry.begin_recovery(self.identity)
        self.registry.mark_collecting(self.identity)
        result = reconcile_production_recovery_snapshot(
            identity=self.identity,
            snapshot=_account_snapshot(self.identity, holding_quantity=2),
            stock_runtime=(
                (
                    "005930",
                    {
                        "account_no": self.identity.account_no,
                        "code": "005930",
                        "quantity": 2,
                        "available_quantity": 2,
                        "average_price": 70000,
                    },
                ),
            ),
            runtime_orders=(),
            registry=self.registry,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(ACCOUNT_COMPLETED, self.registry.snapshot().account_status)
        self.assertEqual(STOCK_RESTORED, self.registry.snapshot().stocks[0].stock_status)

    def test_snapshot_reconciliation_marks_mismatch_for_review(self) -> None:
        self.registry.begin_recovery(self.identity)
        self.registry.mark_collecting(self.identity)
        result = reconcile_production_recovery_snapshot(
            identity=self.identity,
            snapshot=_account_snapshot(self.identity, holding_quantity=2),
            stock_runtime=(("005930", None),),
            runtime_orders=(),
            registry=self.registry,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            ACCOUNT_REVIEW_REQUIRED,
            self.registry.snapshot().account_status,
        )
        self.assertEqual(
            STOCK_REVIEW_REQUIRED,
            self.registry.snapshot().stocks[0].stock_status,
        )


class _FakeTimer:
    def __init__(self) -> None:
        self.active = False
        self.start_calls = 0
        self.stop_calls = 0

    def isActive(self) -> bool:
        return self.active

    def start(self) -> None:
        self.active = True
        self.start_calls += 1

    def stop(self) -> None:
        self.active = False
        self.stop_calls += 1


class RecoveryTimerLifecycleTest(unittest.TestCase):
    def test_settings_window_owns_only_gui_refresh_timers(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        init_source = inspect.getsource(AutoTradeSettingWindow.__init__)
        show_source = inspect.getsource(AutoTradeSettingWindow.showEvent)
        start_source = inspect.getsource(
            AutoTradeSettingWindow.start_periodic_timers_after_recovery
        )
        self.assertNotIn("_time_policy_timer.start()", init_source)
        self.assertNotIn("_runtime_file_timer.start()", init_source)
        self.assertIn("timer.start()", show_source)
        self.assertNotIn("start_recovery_bound_timers", start_source)
        self.assertIn("SETTINGS_GUI_TIMERS_STARTED", start_source)

    def test_timer_waits_for_recovery_and_starts_once(self) -> None:
        registry = ProductionRecoveryStateRegistry()
        identity = _identity()
        timer = _FakeTimer()
        blocked = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )
        self.assertFalse(blocked["started"])
        self.assertEqual(0, timer.start_calls)

        registry.begin_recovery(identity)
        registry.mark_collecting(identity)
        registry.mark_reconciling(identity)
        registry.set_stock_result(
            identity,
            stock_code="005930",
            stock_status=STOCK_RESTORED,
        )
        registry.complete_account(identity)
        started = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )
        repeated = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )
        self.assertTrue(started["started"])
        self.assertTrue(repeated["started"])
        self.assertEqual(1, timer.start_calls)
        stopped = stop_recovery_bound_timers((timer,))
        self.assertTrue(stopped["stopped"])
        self.assertEqual(1, timer.stop_calls)

    def test_timer_start_exception_returns_explicit_failure(self) -> None:
        class FailingTimer(_FakeTimer):
            def start(self) -> None:
                raise RuntimeError("timer boom")

        registry = ProductionRecoveryStateRegistry()
        identity = _identity()
        timer = FailingTimer()
        registry.begin_recovery(identity)
        registry.mark_collecting(identity)
        registry.mark_reconciling(identity)
        registry.set_stock_result(
            identity,
            stock_code="005930",
            stock_status=STOCK_RESTORED,
        )
        registry.complete_account(identity)

        result = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )

        self.assertFalse(result["started"])
        self.assertEqual("RECOVERY_TIMER_START_FAILED", result["reason_code"])

    def test_review_account_starts_timer_for_restored_sibling(self) -> None:
        registry = ProductionRecoveryStateRegistry()
        identity = _identity()
        timer = _FakeTimer()
        registry.begin_recovery(identity)
        registry.mark_collecting(identity)
        registry.mark_reconciling(identity)
        registry.set_stock_result(
            identity,
            stock_code="005930",
            stock_status=STOCK_RESTORED,
        )
        registry.set_stock_result(
            identity,
            stock_code="006400",
            stock_status=STOCK_REVIEW_REQUIRED,
            review_required=True,
        )
        registry.complete_account(identity)
        result = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )
        self.assertTrue(result["started"])
        self.assertEqual("RECOVERY_TIMER_STARTED", result["reason_code"])
        self.assertEqual(1, timer.start_calls)

    def test_all_review_stocks_keep_timer_blocked(self) -> None:
        registry = ProductionRecoveryStateRegistry()
        identity = _identity()
        timer = _FakeTimer()
        registry.begin_recovery(identity)
        registry.mark_collecting(identity)
        registry.mark_reconciling(identity)
        registry.set_stock_result(
            identity,
            stock_code="006400",
            stock_status=STOCK_REVIEW_REQUIRED,
            review_required=True,
        )
        registry.complete_account(identity)

        result = start_recovery_bound_timers(
            identity=identity,
            timers=(timer,),
            registry=registry,
        )

        self.assertFalse(result["started"])
        self.assertEqual("RECOVERY_NO_RESTORED_STOCK", result["reason_code"])
        self.assertEqual(0, timer.start_calls)


class ProductionRecoveryCallPathSourceTest(unittest.TestCase):
    def test_main_window_collects_both_snapshots_before_timer_start(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gui_windows.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("request_account_holdings_snapshot", source)
        self.assertIn("request_open_orders_snapshot", source)
        reconcile_at = source.index("reconcile_production_recovery_snapshot(")
        timer_at = source.index("starter(identity)", reconcile_at)
        self.assertLess(reconcile_at, timer_at)

    def test_early_close_recovery_gate_precedes_transition_and_command(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "gui_auto_trade_close.py"
        ).read_text(encoding="utf-8")
        function_at = source.index("def auto_trade_apply_selected_early_close(")
        recovery_at = source.index('"EARLY_CLOSE_REQUEST"', function_at)
        transition_at = source.index("evaluate_production_transition(", recovery_at)
        intent_at = source.index("apply_close_intent(", transition_at)
        self.assertLess(recovery_at, transition_at)
        self.assertLess(transition_at, intent_at)
        execution_at = source.index("def _start_close_liquidation_execution(")
        execution_gate_at = source.index('f"{reason}_EXECUTION"', execution_at)
        cancel_at = source.index(
            "queue_pending_order_cancellations_for_stock_automatically(",
            execution_at,
        )
        self.assertLess(execution_gate_at, cancel_at)

    def test_timer_checks_recovery_before_time_policy_recalculation(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "gui_auto_trade_timer.py"
        ).read_text(encoding="utf-8")
        function_at = source.index("def auto_trade_run_operation_cycle(")
        recovery_at = source.index("startup_recovery_session_ready", function_at)
        recalculate_at = source.index(
            "recalculate_all_status_by_operation_policy(",
            function_at,
        )
        self.assertLess(recovery_at, recalculate_at)


if __name__ == "__main__":
    unittest.main()
