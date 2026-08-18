# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from legacy_close_reconciliation_service import (
    STATUS_BLOCKED_EVIDENCE,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_CHANGE,
    reconcile_legacy_early_close_no_target,
    state_file_sha256,
)
from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    BrokerAccountSnapshot,
    BrokerHoldingSnapshotItem,
    BrokerOpenOrderSnapshotItem,
    RecoverySessionIdentity,
    recovery_request_id,
)
from runtime_atomic_writer import write_json_atomic


class LegacyCloseReconciliationServiceTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "323410_카카오뱅크"
        self.stock_dir.mkdir()
        self.state_path = self.stock_dir / "state.json"
        self.orders_path = self.stock_dir / "orders.json"
        self.queue_path = self.root / "order_queue.json"
        self.identity = RecoverySessionIdentity(
            recovery_session_id="RECOVERY-7B",
            login_session_id="LOGIN-7B",
            account_no="81290000",
            trading_day=date(2026, 8, 17).isoformat(),
            requested_at="2026-08-17T09:00:00+09:00",
        )
        self.now = datetime(2026, 8, 17, 14, 30, 5, tzinfo=timezone(timedelta(hours=9)))
        self.base_state = {
            "status": "EMERGENCY_STOPPED",
            "holding_qty": 0,
            "avg_price": 0,
            "holding_amount": 0,
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": "복구 상태 오류",
            "review_location": "운영 데이터 불일치",
            "review_entered_at": "2026-08-02 14:17:20",
            "review_checked_at": "2026-08-17 09:00:03",
            "review_detail": "legacy evidence",
            "review_routine": "지표추종매매",
            "emergency_reason": "legacy",
            "emergency_stopped_at": "2026-08-02 14:17:20",
            "emergency_scope": "",
            "emergency_released_at": "",
            "emergency_release_check": {},
            "trade_enabled": False,
            "operation_command_mode": "EARLY_CLOSE",
            "operation_command_id": "CMD-LEGACY",
            "operation_command_source": "operator",
            "operation_command_scope": "STOCK",
            "operation_command_target": "323410",
            "operation_command_sequence": 7,
            "early_close_requested_at": "",
            "operation_notice": "",
            "operation_notice_reason": "",
            "operation_notice_at": "",
            "liquidation_policy_forced": False,
            "close_routine_final_sell_ordered": False,
            "close_routine_final_sell_ordered_at": "",
            "immediate_liquidation_request": {"status": "LEGACY", "command_id": "OLD"},
            "active_routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-323410",
            "schedule": {"start": "09:00", "end": "15:20"},
            "updated_at": "2026-08-02 14:17:20",
        }
        self.write_state(self.base_state)
        self.write_orders([])
        self.write_queue([])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_state(self, value: dict[str, object]) -> None:
        self.state_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_orders(self, orders: list[dict[str, object]]) -> None:
        self.orders_path.write_text(json.dumps({"orders": orders}), encoding="utf-8")

    def write_queue(self, orders: list[dict[str, object]]) -> None:
        self.queue_path.write_text(json.dumps({"version": 1, "revision": 0, "orders": orders}), encoding="utf-8")

    def holding(self, qty: int = 1) -> BrokerHoldingSnapshotItem:
        return BrokerHoldingSnapshotItem(
            account_no=self.identity.account_no,
            stock_code="323410",
            stock_name="카카오뱅크",
            holding_quantity=qty,
            available_quantity=qty,
            average_price=Decimal("25000") if qty else Decimal("0"),
            current_price=Decimal("26000"),
            evaluation_amount=Decimal("26000") * qty,
            profit_loss=Decimal("1000") * qty,
            profit_rate=Decimal("4"),
        )

    def open_order(self) -> BrokerOpenOrderSnapshotItem:
        return BrokerOpenOrderSnapshotItem(
            account_no=self.identity.account_no,
            broker_order_no="BROKER-1",
            original_order_no="",
            stock_code="323410",
            order_side="SELL",
            order_type="NEW",
            order_price=Decimal("0"),
            order_quantity=1,
            filled_quantity=0,
            unfilled_quantity=1,
            order_status="접수",
            order_time="143000",
        )

    def snapshot(self, *, holdings=(), open_orders=(), complete=True, errors=()) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            requested_at=self.identity.requested_at,
            completed_at="2026-08-17T09:00:03+09:00",
            request_id=recovery_request_id(self.identity, "ACCOUNT"),
            recovery_session_id=self.identity.recovery_session_id,
            is_complete=complete,
            holdings=tuple(holdings),
            open_orders=tuple(open_orders),
            source="KIWOOM_OPENAPI",
            errors=tuple(errors),
        )

    def reconcile(self, **overrides: object) -> dict[str, object]:
        args: dict[str, object] = {
            "stock_dir": self.stock_dir,
            "stock_code": "323410",
            "recovery_identity": self.identity,
            "completed_recovery_identity": self.identity,
            "broker_snapshot": self.snapshot(),
            "expected_account_no": self.identity.account_no,
            "expected_trading_day": self.identity.trading_day,
            "expected_login_session_id": self.identity.login_session_id,
            "expected_recovery_session_id": self.identity.recovery_session_id,
            "completed_recovery_status": ACCOUNT_COMPLETED,
            "holdings_complete": True,
            "open_orders_complete": True,
            "expected_state_sha256": state_file_sha256(self.state_path),
            "order_queue_path": self.queue_path,
            "now_provider": lambda: self.now,
        }
        args.update(overrides)
        return reconcile_legacy_early_close_no_target(**args)  # type: ignore[arg-type]

    def assert_blocked_unchanged(self, result: dict[str, object], before: bytes) -> None:
        self.assertEqual(STATUS_BLOCKED_EVIDENCE, result["status"])
        self.assertEqual((), result["changed_fields"])
        self.assertEqual(before, self.state_path.read_bytes())

    def test_323410_shape_completes_with_only_four_fields_changed(self) -> None:
        before = deepcopy(self.base_state)
        result = self.reconcile()
        after = self.read_state()
        self.assertEqual(STATUS_COMPLETED, result["status"])
        self.assertEqual(
            ("operation_notice", "operation_notice_reason", "operation_notice_at", "updated_at"),
            result["changed_fields"],
        )
        self.assertEqual("EARLY_CLOSE_NO_TARGET", after["operation_notice"])
        self.assertEqual("조기마감 대상 없음", after["operation_notice_reason"])
        self.assertEqual("2026-08-17 14:30:05", after["operation_notice_at"])
        for key, value in before.items():
            if key not in result["changed_fields"]:
                self.assertEqual(value, after[key], key)
        for forbidden in (
            "liquidation_completed_at", "liquidation_finished_at",
            "early_close_completed_at", "broker_order_no", "fill_evidence",
        ):
            self.assertNotIn(forbidden, after)

    def test_local_or_broker_holding_blocks(self) -> None:
        state = deepcopy(self.base_state)
        state["holding_qty"] = 1
        self.write_state(state)
        before = self.state_path.read_bytes()
        self.assert_blocked_unchanged(self.reconcile(), before)
        self.write_state(self.base_state)
        before = self.state_path.read_bytes()
        self.assert_blocked_unchanged(self.reconcile(broker_snapshot=self.snapshot(holdings=(self.holding(),))), before)

    def test_pending_buy_sell_and_unknown_block(self) -> None:
        for order in (
            {"status": "OPEN", "side": "BUY", "pending_qty": 2},
            {"status": "OPEN", "side": "SELL", "pending_qty": 2},
            {"status": "OPEN", "side": "BUY"},
        ):
            self.write_orders([order])
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(), before)

    def test_active_queue_and_cancel_pending_block(self) -> None:
        for record in (
            {"stock_code": "323410", "status": "ORDER_QUEUED", "pending_qty": 1},
            {"stock_code": "323410", "status": "CANCEL_REQUESTED", "order_action": "CANCEL", "pending_qty": 1},
        ):
            self.write_queue([record])
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(), before)

    def test_terminal_blocked_zero_quantity_queue_residue_is_not_blocker(self) -> None:
        self.write_queue([{
            "stock_code": "323410", "status": "BLOCKED", "approval_status": "BLOCKED",
            "execution_enabled": False, "candidate_status": "NO_HOLDING_QTY",
            "order_type": "SELL_NO_HOLDING_CANDIDATE", "quantity": 0, "pending_qty": 0,
            "send_order_called": False, "dispatch_claimed": False,
        }])
        self.assertEqual(STATUS_COMPLETED, self.reconcile()["status"])

    def test_broker_open_order_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(broker_snapshot=self.snapshot(open_orders=(self.open_order(),)))
        self.assert_blocked_unchanged(result, before)

    def test_recovery_completeness_gates(self) -> None:
        cases = (
            {"broker_snapshot": self.snapshot(complete=False)},
            {"broker_snapshot": self.snapshot(errors=("-202",))},
            {"holdings_complete": False},
            {"open_orders_complete": False},
            {"completed_recovery_status": "FAILED"},
        )
        for overrides in cases:
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(**overrides), before)

    def test_recovery_identity_and_expected_identity_gates(self) -> None:
        other = replace(self.identity, recovery_session_id="OTHER")
        cases = (
            {"completed_recovery_identity": other},
            {"expected_account_no": "OTHER"},
            {"expected_trading_day": "2026-08-16"},
            {"expected_login_session_id": "OTHER"},
            {"expected_recovery_session_id": "OTHER"},
            {"broker_snapshot": replace(self.snapshot(), account_no="OTHER")},
        )
        for overrides in cases:
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(**overrides), before)

    def test_stale_state_sha_blocks(self) -> None:
        before = self.state_path.read_bytes()
        self.assert_blocked_unchanged(self.reconcile(expected_state_sha256="0" * 64), before)

    def test_active_close_liquidation_and_final_sell_evidence_block(self) -> None:
        variants = (
            {"liquidation_policy_forced": True},
            {"status": "LIQUIDATING"},
            {"individual_liquidation_request": {"status": "REQUESTED"}},
            {"manual_ats_liquidation_request": {"status": "WAITING_CANCEL_CONFIRMATION"}},
            {"close_routine_final_sell_ordered": True},
            {"close_routine_final_sell_ordered_at": "2026-08-17 10:00:00"},
            {"final_sell_order_id": "ORDER-1"},
        )
        for changes in variants:
            state = deepcopy(self.base_state)
            state.update(changes)
            self.write_state(state)
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(), before)

    def test_no_change_does_not_write_or_touch_mtime(self) -> None:
        state = deepcopy(self.base_state)
        state.update({
            "operation_notice": "EARLY_CLOSE_NO_TARGET",
            "operation_notice_reason": "조기마감 대상 없음",
            "operation_notice_at": "2026-08-16 10:00:00",
        })
        self.write_state(state)
        before = self.state_path.read_bytes()
        before_mtime = self.state_path.stat().st_mtime_ns
        writer = Mock(wraps=write_json_atomic)
        result = self.reconcile(atomic_writer=writer)
        self.assertEqual(STATUS_NO_CHANGE, result["status"])
        writer.assert_not_called()
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertEqual(before_mtime, self.state_path.stat().st_mtime_ns)

    def test_other_terminal_completion_is_preserved(self) -> None:
        state = deepcopy(self.base_state)
        state["operation_notice"] = "EARLY_CLOSE_COMPLETED"
        state["operation_notice_at"] = "2026-08-17 13:00:00"
        self.write_state(state)
        before = self.state_path.read_bytes()
        result = self.reconcile()
        self.assertEqual(STATUS_NO_CHANGE, result["status"])
        self.assertEqual(before, self.state_path.read_bytes())

    def test_non_review_or_non_early_close_blocks(self) -> None:
        for changes in (
            {"review_required": False, "review_status": "", "status": "STOPPED"},
            {"operation_command_mode": "NORMAL"},
        ):
            state = deepcopy(self.base_state)
            state.update(changes)
            self.write_state(state)
            before = self.state_path.read_bytes()
            self.assert_blocked_unchanged(self.reconcile(), before)

    def test_atomic_writer_failure_reports_failed_without_fake_success(self) -> None:
        writer = Mock(return_value={"status": "ERROR", "written": False})
        before = self.state_path.read_bytes()
        result = self.reconcile(atomic_writer=writer)
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(before, self.state_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
