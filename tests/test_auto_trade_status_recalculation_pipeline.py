from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui_auto_trade_runtime as auto_trade_runtime
import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_timer as auto_trade_timer
from gui_auto_trade_utils import auto_trade_unregister_category
from runtime_io import read_json_dict


class AutoTradeStatusRecalculationPipelineTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        routines_dir = root / "routines"
        routine_dir = routines_dir / "Strategy"
        routine_dir.mkdir(parents=True)

        stocks_dir = root / "stocks"
        stock_dir = stocks_dir / "111111_Stock"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "routine": "Strategy",
                    "operation_mode": "CONTINUOUS",
                    "real_trade_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "holding_qty": 0,
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "orders.json").write_text("[]", encoding="utf-8")
        return routines_dir, stocks_dir, stock_dir

    def _window(self):
        window = SimpleNamespace()
        window.update_stock_status = (
            lambda stock_dir, code, name, status, metadata, log_suffix:
            status_ops.auto_trade_update_stock_status(
                window,
                stock_dir,
                code,
                name,
                status,
                metadata,
                log_suffix,
            )
        )
        window.recalculate_stock_status_by_operation_policy = (
            lambda stock_dir, code, name, reason, silent_unchanged=False:
            status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                window,
                stock_dir,
                code,
                name,
                reason,
                silent_unchanged=silent_unchanged,
            )
        )
        return window

    def test_central_stock_is_recalculated_and_persisted_as_monitoring(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = self._window()
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                result = status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )

            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual(
            {"changed": 1, "unchanged": 0, "protected": 0, "failed": 0},
            result,
        )
        self.assertEqual("MONITORING", state["status"])

    def test_recalculated_state_unblocks_mode_change_and_unregister(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = self._window()
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "current_datetime", return_value=datetime(2026, 7, 25, 18, 0)),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )
                mode_changed = status_ops.auto_trade_update_stock_operation_mode(
                    window,
                    stock_dir,
                    "111111",
                    "Stock",
                    "SCHEDULED",
                )
                unregister = auto_trade_unregister_category(
                    "Strategy",
                    stock_dir,
                    "111111",
                    "Stock",
                )

            config = read_json_dict(stock_dir / "config.json")

        self.assertTrue(mode_changed)
        self.assertEqual("SCHEDULED", config["operation_mode"])
        self.assertEqual("immediate", unregister["category"])

    def test_first_timer_tick_after_recovery_reconciles_stale_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = self._window()
            window.isVisible = lambda: True
            window.startup_recovery_session_ready = lambda refresh=True: True
            window.current_time_policy_minute_key = lambda: "2026-07-25 18:00"
            window._last_time_policy_minute_key = ""
            window.capture_stock_table_view_state = lambda: (set(), 0)
            window.refresh_all = Mock()
            window.restore_stock_table_view_state = Mock()
            window.statusBarMessage = Mock()
            window.parent = lambda: SimpleNamespace(refresh_all=Mock())
            window.recalculate_all_status_by_operation_policy = (
                lambda reason, silent_unchanged=False, write_changelog_when_unchanged=True:
                status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    reason,
                    silent_unchanged,
                    write_changelog_when_unchanged,
                )
            )

            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(auto_trade_timer, "reset_expired_manual_ats_runtime_selections"),
                patch.object(auto_trade_timer, "manual_ats_market_day_closed", return_value=True),
                patch.object(auto_trade_timer, "probe_selected_routine_once", None),
            ):
                auto_trade_timer.auto_trade_on_time_policy_timer_tick(window)

            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("MONITORING", state["status"])
        self.assertEqual("2026-07-25 18:00", window._last_time_policy_minute_key)
        window.refresh_all.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
