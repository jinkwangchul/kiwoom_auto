# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    BrokerAccountSnapshot,
    BrokerHoldingSnapshotItem,
    BrokerOpenOrderSnapshotItem,
    RecoverySessionIdentity,
    recovery_request_id,
)
from runtime_atomic_writer import write_json_atomic
from stock_position_reconciliation_service import (
    STATUS_APPLIED,
    STATUS_BLOCKED_EVIDENCE,
    STATUS_FAILED,
    STATUS_NO_CHANGE,
    reconcile_review_stock_position,
    state_file_sha256,
)


class StockPositionReconciliationServiceTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "000660_SK하이닉스"
        self.stock_dir.mkdir()
        self.state_path = self.stock_dir / "state.json"
        self.queue_path = self.root / "order_queue.json"
        self.queue_path.write_text(
            json.dumps({"version": 1, "revision": 0, "orders": []}),
            encoding="utf-8",
        )
        self.identity = RecoverySessionIdentity(
            recovery_session_id="RECOVERY_SESSION_TEST",
            login_session_id="LOGIN_TEST",
            account_no="81290000",
            trading_day=date(2026, 8, 17).isoformat(),
            requested_at="2026-08-17T09:00:00+09:00",
        )
        self.base_state = {
            "status": "EMERGENCY_STOPPED",
            "holding_qty": 12310,
            "avg_price": 123130,
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": "긴급정지 해제 시 보유잔량 존재",
            "review_location": "긴급정지해제",
            "review_entered_at": "2026-08-04 09:20:19",
            "review_checked_at": "2026-08-17 09:00:01",
            "review_detail": "evidence",
            "emergency_reason": "USER_EMERGENCY_STOP",
            "emergency_stopped_at": "2026-07-30 06:39:02",
            "emergency_scope": "SELECTED",
            "operation_command_mode": "EARLY_CLOSE",
            "early_close_requested_at": "2026-08-01 15:20:00",
            "liquidation_policy_forced": False,
            "trade_enabled": False,
            "active_routine": "지표추종매매B",
            "routine_instance_id": "INSTANCE-1",
            "schedule": {"start": "09:00", "end": "15:20"},
        }
        self.write_state(self.base_state)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def holding(self, *, qty: int = 7, avg: str = "10123") -> BrokerHoldingSnapshotItem:
        return BrokerHoldingSnapshotItem(
            account_no=self.identity.account_no,
            stock_code="000660",
            stock_name="SK하이닉스",
            holding_quantity=qty,
            available_quantity=qty,
            average_price=Decimal(avg),
            current_price=Decimal("10500"),
            evaluation_amount=Decimal("73500"),
            profit_loss=Decimal("2639"),
            profit_rate=Decimal("3.7"),
        )

    def open_order(self) -> BrokerOpenOrderSnapshotItem:
        return BrokerOpenOrderSnapshotItem(
            account_no=self.identity.account_no,
            broker_order_no="BROKER-1",
            original_order_no="",
            stock_code="000660",
            order_side="BUY",
            order_type="NEW",
            order_price=Decimal("10000"),
            order_quantity=1,
            filled_quantity=0,
            unfilled_quantity=1,
            order_status="접수",
            order_time="090001",
        )

    def snapshot(
        self,
        *,
        holdings: tuple[BrokerHoldingSnapshotItem, ...] = (),
        open_orders: tuple[BrokerOpenOrderSnapshotItem, ...] = (),
        complete: bool = True,
        errors: tuple[str, ...] = (),
    ) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            requested_at=self.identity.requested_at,
            completed_at="2026-08-17T09:00:03+09:00",
            request_id=recovery_request_id(self.identity, "ACCOUNT"),
            recovery_session_id=self.identity.recovery_session_id,
            is_complete=complete,
            holdings=holdings,
            open_orders=open_orders,
            source="KIWOOM_OPENAPI",
            errors=errors,
        )

    def reconcile(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "stock_dir": self.stock_dir,
            "stock_code": "000660",
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
        }
        arguments.update(overrides)
        return reconcile_review_stock_position(**arguments)  # type: ignore[arg-type]

    def assert_blocked_unchanged(self, result: dict[str, object], before: bytes) -> None:
        self.assertEqual(STATUS_BLOCKED_EVIDENCE, result["status"])
        self.assertEqual((), result["changed_fields"])
        self.assertEqual(before, self.state_path.read_bytes())

    def test_absent_broker_row_reconciles_stale_holding(self) -> None:
        before = deepcopy(self.base_state)
        result = self.reconcile()
        after = self.read_state()
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual(0, after["holding_qty"])
        self.assertEqual(0, after["avg_price"])
        for key, value in before.items():
            if key not in {"holding_qty", "avg_price"}:
                self.assertEqual(value, after[key], key)

    def test_absent_row_zeros_existing_holding_amount(self) -> None:
        state = deepcopy(self.base_state)
        state["holding_amount"] = 1231000
        self.write_state(state)
        result = self.reconcile()
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual(0, self.read_state()["holding_amount"])

    def test_absent_row_does_not_create_holding_amount(self) -> None:
        self.reconcile()
        self.assertNotIn("holding_amount", self.read_state())

    def test_single_broker_row_applies_canonical_qty_and_average(self) -> None:
        result = self.reconcile(broker_snapshot=self.snapshot(holdings=(self.holding(),)))
        state = self.read_state()
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual(7, state["holding_qty"])
        self.assertEqual(10123, state["avg_price"])

    def test_same_position_returns_no_change_without_write(self) -> None:
        state = deepcopy(self.base_state)
        state["holding_qty"] = 7
        state["avg_price"] = 10123
        self.write_state(state)
        before = self.state_path.read_bytes()
        calls: list[object] = []

        def writer(*args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {"status": "OK"}

        result = self.reconcile(
            broker_snapshot=self.snapshot(holdings=(self.holding(),)),
            atomic_writer=writer,
        )
        self.assertEqual(STATUS_NO_CHANGE, result["status"])
        self.assertEqual([], calls)
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertEqual(result["before_state_sha256"], result["after_state_sha256"])

    def test_review_emergency_close_trade_and_routine_metadata_are_preserved(self) -> None:
        before = deepcopy(self.base_state)
        result = self.reconcile()
        after = self.read_state()
        self.assertEqual(STATUS_APPLIED, result["status"])
        for key in (
            "status",
            "review_required",
            "review_status",
            "review_reason",
            "review_location",
            "review_entered_at",
            "review_checked_at",
            "review_detail",
            "emergency_reason",
            "emergency_stopped_at",
            "emergency_scope",
            "operation_command_mode",
            "early_close_requested_at",
            "liquidation_policy_forced",
            "trade_enabled",
            "active_routine",
            "routine_instance_id",
            "schedule",
        ):
            self.assertEqual(before[key], after[key], key)

    def test_wrong_account_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_account_no="OTHER")
        self.assert_blocked_unchanged(result, before)

    def test_wrong_trading_day_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_trading_day="2026-08-16")
        self.assert_blocked_unchanged(result, before)

    def test_wrong_login_session_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_login_session_id="LOGIN_OTHER")
        self.assert_blocked_unchanged(result, before)

    def test_wrong_recovery_session_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_recovery_session_id="RECOVERY_OTHER")
        self.assert_blocked_unchanged(result, before)

    def test_different_completed_recovery_identity_blocks(self) -> None:
        before = self.state_path.read_bytes()
        other = replace(self.identity, login_session_id="LOGIN_OTHER")
        result = self.reconcile(completed_recovery_identity=other)
        self.assert_blocked_unchanged(result, before)

    def test_incomplete_account_snapshot_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(broker_snapshot=self.snapshot(complete=False, errors=("incomplete",)))
        self.assert_blocked_unchanged(result, before)

    def test_incomplete_holdings_part_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(holdings_complete=False)
        self.assert_blocked_unchanged(result, before)

    def test_incomplete_open_orders_part_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(open_orders_complete=False)
        self.assert_blocked_unchanged(result, before)

    def test_duplicate_broker_holding_rows_block(self) -> None:
        before = self.state_path.read_bytes()
        row = self.holding()
        result = self.reconcile(broker_snapshot=self.snapshot(holdings=(row, row)))
        self.assert_blocked_unchanged(result, before)

    def test_state_sha_mismatch_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_state_sha256="0" * 64)
        self.assertEqual("STATE_STALE", result["reason"])
        self.assert_blocked_unchanged(result, before)

    def test_active_queue_order_blocks(self) -> None:
        self.queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "orders": [
                        {
                            "stock_code": "000660",
                            "status": "ORDER_QUEUED",
                            "order_qty": 1,
                            "filled_qty": 0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        before = self.state_path.read_bytes()
        result = self.reconcile()
        self.assert_blocked_unchanged(result, before)

    def test_broker_open_order_blocks(self) -> None:
        before = self.state_path.read_bytes()
        result = self.reconcile(broker_snapshot=self.snapshot(open_orders=(self.open_order(),)))
        self.assert_blocked_unchanged(result, before)

    def test_terminal_blocked_zero_qty_candidate_does_not_block(self) -> None:
        self.queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "orders": [
                        {
                            "stock_code": "000660",
                            "status": "BLOCKED",
                            "approval_status": "BLOCKED",
                            "execution_enabled": False,
                            "candidate_status": "NO_HOLDING_QTY",
                            "order_type": "SELL_NO_HOLDING_CANDIDATE",
                            "quantity": 0,
                            "pending_qty": 0,
                            "send_order_called": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = self.reconcile()
        self.assertEqual(STATUS_APPLIED, result["status"])

    def test_malformed_queue_blocks(self) -> None:
        self.queue_path.write_text("{", encoding="utf-8")
        before = self.state_path.read_bytes()
        result = self.reconcile()
        self.assert_blocked_unchanged(result, before)

    def test_malformed_state_fails_without_write(self) -> None:
        self.state_path.write_text("{", encoding="utf-8")
        before = self.state_path.read_bytes()
        result = self.reconcile(expected_state_sha256=state_file_sha256(self.state_path))
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(before, self.state_path.read_bytes())

    def test_non_review_state_blocks(self) -> None:
        state = deepcopy(self.base_state)
        state.update(status="STOPPED", review_required=False, review_status="")
        self.write_state(state)
        before = self.state_path.read_bytes()
        result = self.reconcile()
        self.assert_blocked_unchanged(result, before)

    def test_atomic_write_failure_returns_failed(self) -> None:
        before = self.state_path.read_bytes()

        def failed_writer(*args: object, **kwargs: object) -> dict[str, object]:
            return {"status": "ERROR", "written": False}

        result = self.reconcile(atomic_writer=failed_writer)
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(before, self.state_path.read_bytes())

    def test_read_back_mismatch_returns_failed(self) -> None:
        def mismatched_reader(path: Path) -> dict[str, object]:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["trade_enabled"] = True
            return data

        result = self.reconcile(state_reader=mismatched_reader)
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual("READ_BACK_MISMATCH", result["reason"])

    def test_000660_shape_preserves_review_and_emergency_lifecycle(self) -> None:
        before = deepcopy(self.base_state)
        result = self.reconcile()
        after = self.read_state()
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual(("holding_qty", "avg_price"), result["changed_fields"])
        self.assertEqual("EMERGENCY_STOPPED", after["status"])
        self.assertTrue(after["review_required"])
        self.assertEqual("PENDING", after["review_status"])
        self.assertEqual(before["emergency_reason"], after["emergency_reason"])

    def test_323410_shape_zeros_only_stale_average_and_preserves_early_close(self) -> None:
        stock_dir = self.root / "323410_카카오뱅크"
        stock_dir.mkdir()
        state_path = stock_dir / "state.json"
        state = deepcopy(self.base_state)
        state.update(holding_qty=0, avg_price=1000, holding_amount=0)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        result = self.reconcile(
            stock_dir=stock_dir,
            stock_code="323410",
            expected_state_sha256=state_file_sha256(state_path),
        )
        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual(("avg_price",), result["changed_fields"])
        self.assertEqual("EARLY_CLOSE", after["operation_command_mode"])
        self.assertEqual("PENDING", after["review_status"])
        self.assertEqual(state["emergency_reason"], after["emergency_reason"])

    def test_real_atomic_writer_and_read_back_are_used(self) -> None:
        calls: list[Path] = []

        def observed_writer(path: str | Path, data: dict[str, object]) -> dict[str, object]:
            calls.append(Path(path))
            return write_json_atomic(path, data)

        result = self.reconcile(atomic_writer=observed_writer)
        self.assertEqual(STATUS_APPLIED, result["status"])
        self.assertEqual([self.state_path], calls)
        self.assertEqual(state_file_sha256(self.state_path), result["after_state_sha256"])


if __name__ == "__main__":
    unittest.main()
