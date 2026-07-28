# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import inspect
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import broker_holding_recorder
from broker_holding_recorder import (
    PRODUCTION_RECOVERY_REVIEW_DUPLICATE,
    PRODUCTION_RECOVERY_REVIEW_INVALID,
    PRODUCTION_RECOVERY_REVIEW_READ_BACK_FAILED,
    PRODUCTION_RECOVERY_REVIEW_STORAGE_FAILED,
    PRODUCTION_RECOVERY_REVIEW_WRITTEN,
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
    def test_gui_creates_timers_without_automatic_start(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        init_source = inspect.getsource(AutoTradeSettingWindow.__init__)
        show_source = inspect.getsource(AutoTradeSettingWindow.showEvent)
        start_source = inspect.getsource(
            AutoTradeSettingWindow.start_periodic_timers_after_recovery
        )
        self.assertNotIn("_time_policy_timer.start()", init_source)
        self.assertNotIn("_runtime_file_timer.start()", init_source)
        self.assertNotIn(".start()", show_source)
        self.assertIn("start_recovery_bound_timers", start_source)

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
        command_at = source.index("command_service.apply_early_close(", transition_at)
        self.assertLess(recovery_at, transition_at)
        self.assertLess(transition_at, command_at)
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
        function_at = source.index("def auto_trade_on_time_policy_timer_tick(")
        recovery_at = source.index("startup_recovery_session_ready", function_at)
        recalculate_at = source.index(
            "recalculate_all_status_by_operation_policy(",
            function_at,
        )
        self.assertLess(recovery_at, recalculate_at)


if __name__ == "__main__":
    unittest.main()
