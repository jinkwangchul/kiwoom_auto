from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import order_manager


class OrderManagerCanonicalPermissionTest(unittest.TestCase):
    NOW = datetime(2026, 8, 10, 10, 0, 0)

    def setUp(self) -> None:
        self.config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        self.operation_state = {
            "operation_date": "2026-08-10",
            "operation_status": "RUNNING",
            "emergency_stop": False,
        }
        self.running_state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "signal_probe_only": False,
            "review_required": False,
            "buy_enabled": False,
            "sell_enabled": False,
        }

    def decide(
        self,
        state: dict[str, object],
        signal: str,
        *,
        now: datetime | None = None,
        operation_state: dict[str, object] | None = None,
        config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return order_manager.decide_routine_order(
            state,
            signal,
            display_status=str(state.get("status") or ""),
            config=dict(self.config if config is None else config),
            operation_state=dict(
                self.operation_state
                if operation_state is None
                else operation_state
            ),
            now_dt=now or self.NOW,
        )

    def assert_sides(self, state: dict[str, object], allowed: bool, **kwargs) -> None:
        for signal in ("BUY", "SELL"):
            with self.subTest(signal=signal, state=state, kwargs=kwargs):
                self.assertIs(self.decide(state, signal, **kwargs)["allowed"], allowed)

    def test_running_uses_canonical_state_not_legacy_flags(self) -> None:
        self.assert_sides(self.running_state, True)
        legacy_true = dict(self.running_state, buy_enabled=True, sell_enabled=True)
        self.assert_sides(legacy_true, True)

    def test_stopped_emergency_review_and_normal_ended_are_blocked(self) -> None:
        self.assert_sides(
            dict(self.running_state, status="STOPPED", buy_enabled=True, sell_enabled=True),
            False,
        )
        self.assert_sides(
            dict(
                self.running_state,
                status="EMERGENCY_STOPPED",
                emergency_reason="USER_EMERGENCY_STOP",
                buy_enabled=True,
                sell_enabled=True,
            ),
            False,
        )
        self.assert_sides(
            dict(
                self.running_state,
                status="REVIEW_REQUIRED",
                review_required=True,
                buy_enabled=True,
                sell_enabled=True,
            ),
            False,
        )
        ended = dict(self.operation_state, operation_status="NORMAL_ENDED")
        self.assert_sides(
            dict(self.running_state, buy_enabled=True, sell_enabled=True),
            False,
            operation_state=ended,
        )

    def test_early_close_routine_allows_before_first_sell_and_blocks_after(self) -> None:
        before = dict(
            self.running_state,
            status="EARLY_CLOSE",
            operation_command_mode="EARLY_CLOSE",
            early_close_requested_at="2026-08-10 10:00:00",
            early_close_method="루틴",
            liquidation_policy_forced=True,
            close_routine_final_sell_ordered=False,
        )
        self.assert_sides(before, True)
        after = dict(
            before,
            close_routine_final_sell_ordered=True,
            close_routine_final_sell_ordered_at="2026-08-10 10:05:00",
        )
        self.assert_sides(after, False)

    def test_auto_close_routine_allows_before_first_sell_and_blocks_after(self) -> None:
        before = dict(
            self.running_state,
            status="AUTO_CLOSE",
            auto_close_requested_at="2026-08-10 13:30:00",
            auto_close_method="루틴",
            close_routine_final_sell_ordered=False,
        )
        self.assert_sides(
            before,
            True,
            now=datetime(2026, 8, 10, 14, 0, 0),
        )
        after = dict(
            before,
            close_routine_final_sell_ordered=True,
            close_routine_final_sell_ordered_at="2026-08-10 14:05:00",
        )
        self.assert_sides(
            after,
            False,
            now=datetime(2026, 8, 10, 14, 6, 0),
        )

    def test_market_and_current_early_close_block_routine_orders(self) -> None:
        for method in ("시장가", "현재가"):
            state = dict(
                self.running_state,
                status="EARLY_CLOSE",
                operation_command_mode="EARLY_CLOSE",
                early_close_requested_at="2026-08-10 10:00:00",
                early_close_method=method,
                buy_enabled=True,
                sell_enabled=True,
            )
            self.assert_sides(state, False)

        forced = dict(
            self.running_state,
            liquidation_policy_forced=True,
            buy_enabled=True,
            sell_enabled=True,
        )
        self.assert_sides(forced, False)

    def test_carry_over_blocks_routine_orders(self) -> None:
        for state in (
            dict(
                self.running_state,
                operation_command_mode="CARRY_OVER",
                buy_enabled=True,
                sell_enabled=True,
            ),
            dict(
                self.running_state,
                early_close_requested_at="2026-08-10 10:00:00",
                early_close_method="이월",
                buy_enabled=True,
                sell_enabled=True,
            ),
        ):
            self.assert_sides(state, False)

    def test_regular_running_obeys_time_policy_but_close_routine_does_not(self) -> None:
        self.assert_sides(
            self.running_state,
            False,
            now=datetime(2026, 8, 10, 14, 0, 0),
        )

    def test_manual_ats_uses_existing_canonical_session_helper(self) -> None:
        manual_config = dict(self.config, operation_mode="CONTINUOUS")
        outside_regular = datetime(2026, 8, 10, 18, 0, 0)
        with patch(
            "routine_order_permission.manual_ats_active_now",
            return_value=True,
        ) as ats_active:
            self.assert_sides(
                self.running_state,
                True,
                now=outside_regular,
                config=manual_config,
            )
        self.assertEqual(2, ats_active.call_count)

    def test_same_day_restart_and_next_day_operation_state_regression(self) -> None:
        restarted = dict(
            self.running_state,
            resumed_at="2026-08-10 09:30:00",
            buy_enabled=False,
            sell_enabled=False,
        )
        self.assert_sides(restarted, True)
        previous_day_ended = {
            "operation_date": "2026-08-09",
            "operation_status": "NORMAL_ENDED",
            "emergency_stop": False,
        }
        self.assert_sides(
            restarted,
            True,
            operation_state=previous_day_ended,
        )

    def test_stock_dir_entry_supplies_config_and_global_operation_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(self.running_state, ensure_ascii=False), encoding="utf-8"
            )
            (stock_dir / "config.json").write_text(
                json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
            )
            ended = dict(self.operation_state, operation_status="NORMAL_ENDED")
            with (
                patch.object(order_manager, "read_operation_state", return_value=ended),
                patch.object(order_manager, "current_datetime", return_value=self.NOW),
            ):
                decision = order_manager.decide_routine_order_for_stock_dir(
                    stock_dir,
                    "BUY",
                    display_status="RUNNING",
                )
        self.assertFalse(decision["allowed"])


if __name__ == "__main__":
    unittest.main()
