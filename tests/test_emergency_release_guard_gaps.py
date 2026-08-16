# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui_main_emergency_ops as emergency_ops
from gui_auto_trade_run_control import (
    _active_close_or_liquidation,
    _active_queue_reason,
)
from gui_review_required_window import auto_trade_setting_server_mismatch_detected
from production_recovery_contract import create_recovery_session_identity
from production_recovery_state_registry import (
    RECOVERY_IDENTITY_MISMATCH,
    ProductionRecoveryStateRegistry,
    check_production_recovery_gate,
)


class _ReleaseWindow:
    def __init__(
        self,
        *,
        recovery_ready: bool = True,
        recovery_review_required: bool = False,
        recovery_decision: object | None = None,
    ) -> None:
        self.recovery_ready = recovery_ready
        self.recovery_review_required = recovery_review_required
        self.recovery_decision = recovery_decision

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        del refresh
        return self.recovery_ready

    def production_recovery_stock_is_review_required(self, _code: str) -> bool:
        return self.recovery_review_required

    def production_recovery_gate_for_stock(self, _code: str, *, caller_name: str):
        del caller_name
        if self.recovery_decision is not None:
            return self.recovery_decision
        return SimpleNamespace(
            allowed=self.recovery_ready,
            reason_code="" if self.recovery_ready else "RECOVERY_NOT_READY",
        )


class EmergencyReleaseGuardGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "stocks").mkdir()
        (self.root / "runtime").mkdir()
        (self.root / "runtime" / "order_queue.json").write_text(
            json.dumps({"version": 1, "orders": []}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _stock(
        self,
        code: str = "000001",
        **state_extra: object,
    ) -> Path:
        stock_dir = self.root / "stocks" / f"{code}_TEST"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "assigned_routine_instance_id": "instance-test",
                    "routine_instance_name": "테스트 루틴",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        state = {
            "status": "EMERGENCY_STOPPED",
            "trade_enabled": False,
            "holding_qty": 0,
            "avg_price": 0,
            "emergency_stopped_at": "2026-08-11 09:00:00",
            "emergency_reason": "USER_EMERGENCY_STOP",
            "review_required": True,
            "review_status": "PENDING",
            "review_location": "긴급정지",
            "review_reason": "USER_EMERGENCY_STOP",
        }
        state.update(state_extra)
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (stock_dir / "orders.json").write_text(
            json.dumps({"orders": []}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return stock_dir

    def _release(
        self,
        stock_dir: Path,
        *,
        window: _ReleaseWindow | None = None,
    ) -> tuple[str, dict[str, object]]:
        target_window = window or _ReleaseWindow()
        with (
            patch.object(emergency_ops, "append_stock_log"),
            patch.object(
                emergency_ops,
                "ORDER_QUEUE_PATH",
                self.root / "runtime" / "order_queue.json",
            ),
            patch.object(
                emergency_ops,
                "now_text",
                return_value="2026-08-11 10:00:00",
            ),
        ):
            result = emergency_ops.release_emergency_stop_target(
                target_window,
                stock_dir,
                "000001",
                "TEST",
            )
        state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
        return result, state

    def _assert_release_blocked(
        self,
        result: str,
        state: dict[str, object],
        *,
        evidence: str,
        expected_reason: str | None = None,
    ) -> None:
        detail = f"evidence={evidence} actual_result={result} actual_state={state}"
        self.assertEqual("EMERGENCY_STOPPED", state.get("status"), detail)
        self.assertFalse(state.get("trade_enabled"), detail)
        self.assertTrue(state.get("review_required"), detail)
        self.assertEqual("PENDING", state.get("review_status"), detail)
        self.assertEqual("2026-08-11 09:00:00", state.get("emergency_stopped_at"), detail)
        self.assertNotEqual("PASSED", state.get("emergency_release_check"), detail)
        if expected_reason is not None:
            self.assertEqual(
                "사용자 긴급정지 / "
                + emergency_ops.operator_review_reason(expected_reason),
                state.get("review_reason"),
                detail,
            )
            self.assertIn(f"evidence={expected_reason}", state.get("review_detail", ""))

    def test_recovery_completed_control_releases_emergency_but_preserves_review(self) -> None:
        stock_dir = self._stock()

        result, state = self._release(
            stock_dir,
            window=_ReleaseWindow(recovery_ready=True),
        )

        self.assertEqual("review_existing", result)
        self.assertEqual("REVIEW_REQUIRED", state["status"])
        self.assertTrue(state["review_required"])
        self.assertEqual("RESOLVED", state["review_status"])
        self.assertFalse(state["trade_enabled"])
        self.assertEqual("PASSED", state["emergency_release_check"])

    def test_recovery_incomplete_blocks_emergency_release(self) -> None:
        stock_dir = self._stock()

        result, state = self._release(
            stock_dir,
            window=_ReleaseWindow(recovery_ready=False),
        )

        self._assert_release_blocked(
            result,
            state,
            evidence="RECOVERY_NOT_READY",
            expected_reason="RECOVERY_NOT_READY",
        )

    def test_recovery_session_mismatch_blocks_emergency_release(self) -> None:
        identity = create_recovery_session_identity(
            login_session_id="LOGIN-A",
            account_no="1234567890",
            trading_day="2026-08-11",
            requested_at="2026-08-11T08:55:00",
        )
        registry = ProductionRecoveryStateRegistry()
        registry.begin_recovery(identity)
        decision = check_production_recovery_gate(
            login_session_id="LOGIN-B",
            account_no=identity.account_no,
            trading_day=identity.trading_day,
            stock_code="000001",
            recovery_session_id=identity.recovery_session_id,
            caller_name="EmergencyReleaseGuardGapTests",
            registry=registry,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(RECOVERY_IDENTITY_MISMATCH, decision.reason_code)
        stock_dir = self._stock()

        result, state = self._release(
            stock_dir,
            window=_ReleaseWindow(recovery_decision=decision),
        )

        self._assert_release_blocked(
            result,
            state,
            evidence=decision.reason_code,
            expected_reason=decision.reason_code,
        )

    def test_explicit_broker_runtime_mismatch_blocks_emergency_release(self) -> None:
        cases = (
            {"server_mismatch": True},
            {"kiwoom_sync_status": "MISMATCH"},
            {"reconciliation_status": "FAILED"},
        )
        for index, evidence in enumerate(cases, start=1):
            with self.subTest(evidence=evidence):
                stock_dir = self._stock(f"{index:06d}", **evidence)
                state_before = json.loads(
                    (stock_dir / "state.json").read_text(encoding="utf-8")
                )
                self.assertTrue(auto_trade_setting_server_mismatch_detected(state_before))

                result, state = self._release(stock_dir)

                self._assert_release_blocked(
                    result,
                    state,
                    evidence=str(evidence),
                    expected_reason="SERVER_MISMATCH",
                )

    def test_queue_pending_cancel_blocks_emergency_release(self) -> None:
        stock_dir = self._stock()
        queue_path = self.root / "runtime" / "order_queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "orders": [
                        {
                            "stock_code": "000001",
                            "status": "CANCEL_REQUESTED",
                            "action": "CANCEL",
                            "remaining_qty": 0,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual("PENDING_CANCEL", _active_queue_reason("000001", queue_path))

        result, state = self._release(stock_dir)

        self._assert_release_blocked(
            result,
            state,
            evidence="PENDING_CANCEL",
            expected_reason="PENDING_CANCEL",
        )

    def test_stock_orders_cancel_requested_control_is_already_blocked(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "orders.json").write_text(
            json.dumps(
                {
                    "orders": [
                        {
                            "stock_code": "000001",
                            "status": "CANCEL_REQUESTED",
                            "side": "BUY",
                            "remaining_qty": 1,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        result, state = self._release(stock_dir)

        self._assert_release_blocked(
            result,
            state,
            evidence="STOCK_ORDERS_CANCEL_REQUESTED",
        )

    def test_active_close_or_liquidation_blocks_emergency_release(self) -> None:
        cases = (
            {"liquidation_policy_forced": True},
            {"close_routine_final_sell_ordered": True},
            {"operation_command_mode": "EARLY_CLOSE"},
            {"status": "AUTO_CLOSING"},
        )
        now_dt = datetime(2026, 8, 11, 10, 0, 0)
        for index, evidence in enumerate(cases, start=1):
            with self.subTest(evidence=evidence):
                stock_dir = self._stock(f"{index:06d}", **evidence)
                state_before = json.loads(
                    (stock_dir / "state.json").read_text(encoding="utf-8")
                )
                self.assertTrue(_active_close_or_liquidation(state_before, now_dt))

                result, state = self._release(stock_dir)

                self._assert_release_blocked(
                    result,
                    state,
                    evidence=str(evidence),
                    expected_reason="ACTIVE_CLOSE_OR_LIQUIDATION",
                )


if __name__ == "__main__":
    unittest.main()
