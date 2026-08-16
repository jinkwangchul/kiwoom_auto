# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_close as close
import gui_auto_trade_policy as policy
import operation_command_service
from close_liquidation_transition_service import (
    TransitionEvidence,
    decide_close_liquidation_transition,
)


class IndividualLiquidationTimeContractTests(unittest.TestCase):
    NOW_DATE = (2026, 8, 16)

    @staticmethod
    def _operation_policy() -> dict[str, object]:
        return {
            "regular_market": {"end_time": "15:20:00"},
            "liquidation": {
                "method": "이월",
                "minutes_before_regular_close": "5",
            },
        }

    @staticmethod
    def _stock(root: Path, *, method: str = "", minutes: str = "5") -> Path:
        stock = root / "stocks" / "005930_Samsung"
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "routine-instance-1"}),
            encoding="utf-8",
        )
        state: dict[str, object] = {
            "status": "RUNNING",
            "holding_qty": 3,
            "trade_enabled": True,
            "trade_started_at": "2026-08-16 09:00:00",
        }
        if method:
            state["individual_liquidation_request"] = {
                "status": "REQUESTED",
                "method": method,
                "minutes_before_regular_close": minutes,
                "command_id": "command-existing",
                "operation_sequence": 1,
                "requested_at": "2026-08-16 13:00:00",
            }
            state["operation_sequence"] = 1
        (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (stock / "orders.json").write_text(json.dumps({"orders": []}), encoding="utf-8")
        return stock

    @staticmethod
    def _window(stock: Path) -> Mock:
        window = Mock()
        window.selected_stock_infos.return_value = [(stock, "005930", "Samsung")]
        window.capture_stock_table_view_state.return_value = ([str(stock)], 0)
        window.current_runtime_file_signature.return_value = ()
        return window

    @staticmethod
    def _guard(**kwargs):
        evidence = TransitionEvidence(
            liquidation_time_window_entered=bool(
                kwargs.get("liquidation_time_window_entered")
            )
        )
        decision = decide_close_liquidation_transition(
            policy_domain=kwargs.get("policy_domain"),
            current_policy=kwargs.get("current_policy"),
            requested_policy=kwargs.get("requested_policy"),
            evidence=evidence,
        )
        return SimpleNamespace(
            allowed=decision.allowed,
            reason_code=decision.reason_code,
            evidence_status="COMPLETE",
        )

    def _apply(self, stock: Path, method: str, now_dt: datetime):
        window = self._window(stock)
        with (
            patch.object(close, "PROJECT_ROOT", stock.parent.parent),
            patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
            patch.object(close, "evaluate_production_transition", side_effect=self._guard),
            patch.object(close, "_start_close_liquidation_execution") as start,
            patch.object(close, "refresh_auto_trade_views"),
            patch.object(close, "append_stock_log"),
            patch.object(operation_command_service, "observe_liquidation_requested"),
        ):
            result = close.auto_trade_apply_selected_individual_liquidation_method(
                window,
                method,
                "5",
                show_error_dialog=False,
                now_dt=now_dt,
            )
        return result, start, window

    def test_setting_market_before_window_only_persists_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            result, start, window = self._apply(
                stock,
                "시장가",
                datetime(*self.NOW_DATE, 13, 0),
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("시장가", state["individual_liquidation_request"]["method"])
        start.assert_not_called()
        window.statusBarMessage.assert_called_once_with(
            "개별청산 설정 완료: 5분/시장가 / 대상 1개"
        )

    def test_market_and_current_can_return_to_carryover_before_window(self) -> None:
        for initial in ("시장가", "현재가"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=initial)
                result, start, _window = self._apply(
                    stock,
                    "이월",
                    datetime(*self.NOW_DATE, 13, 30),
                )
                state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual("이월", state["individual_liquidation_request"]["method"])
                start.assert_not_called()

    def test_market_and_current_cannot_return_to_carryover_after_window_entry(self) -> None:
        for initial in ("시장가", "현재가"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=initial)
                result, start, _window = self._apply(
                    stock,
                    "이월",
                    datetime(*self.NOW_DATE, 15, 15),
                )
                state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
                self.assertFalse(result["ok"])
                self.assertEqual(
                    "정책 전환 차단:LIQUIDATION_TIME_WINDOW_ENTERED",
                    result["message"],
                )
                self.assertEqual(initial, state["individual_liquidation_request"]["method"])
                start.assert_not_called()

    def test_first_individual_policy_cannot_be_set_after_its_time_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            result, start, _window = self._apply(
                stock,
                "시장가",
                datetime(*self.NOW_DATE, 15, 15),
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertNotIn("individual_liquidation_request", state)
        start.assert_not_called()

    def test_new_minutes_cannot_retroactively_enter_an_earlier_window(self) -> None:
        state = {
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            }
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertTrue(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 12),
                    candidate_minutes_before_regular_close="10",
                )
            )

    def test_time_source_uses_regular_end_minus_configured_minutes(self) -> None:
        state = {
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            }
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertFalse(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 14, 59),
                )
            )
            self.assertTrue(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 15, 0),
                )
            )

    def test_newer_individual_policy_overrides_earlier_close_carryover_at_time_gate(self) -> None:
        state = {
            "status": "AUTO_CLOSING",
            "auto_close_method": "이월",
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            },
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertTrue(
                policy.auto_trade_setting_liquidation_active(
                    {},
                    3,
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                    display_status="자동마감",
                    state=state,
                )
            )

    def test_close_carryover_displays_no_liquidation_until_individual_override(self) -> None:
        base_state = {
            "status": "AUTO_CLOSING",
            "auto_close_method": "이월",
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertEqual(
                "-",
                policy.auto_trade_setting_liquidation_text(
                    {"operation_mode": "SCHEDULED"},
                    "자동마감",
                    base_state,
                ),
            )
            self.assertEqual(
                "5분/시장가",
                policy.auto_trade_setting_liquidation_text(
                    {"operation_mode": "SCHEDULED"},
                    "자동마감",
                    {
                        **base_state,
                        "individual_liquidation_request": {
                            "status": "REQUESTED",
                            "method": "시장가",
                            "minutes_before_regular_close": "5",
                            "requested_at": "2026-08-16 13:00:00",
                        },
                    },
                ),
            )

    def test_timer_enters_existing_pipeline_only_at_market_or_current_window(self) -> None:
        for method in ("시장가", "현재가"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=method)
                with (
                    patch(
                        "gui_auto_trade_runtime.all_registered_stock_dirs",
                        return_value=[stock],
                    ),
                    patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                    patch.object(
                        close,
                        "_start_close_liquidation_execution",
                        return_value={"ok": True, "stage": "send_order"},
                    ) as start,
                ):
                    before = close.auto_trade_continue_pending_close_liquidations(
                        Mock(),
                        now_dt=datetime(*self.NOW_DATE, 13, 0),
                    )
                    at_window = close.auto_trade_continue_pending_close_liquidations(
                        Mock(),
                        now_dt=datetime(*self.NOW_DATE, 15, 15),
                    )

                self.assertEqual(0, before["processed"])
                self.assertEqual(1, at_window["processed"])
                start.assert_called_once()

    def test_final_carryover_never_enters_execution_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp), method="이월")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                patch.object(close, "_start_close_liquidation_execution") as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )

        self.assertEqual(0, result["processed"])
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
