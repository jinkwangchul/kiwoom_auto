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
import gui_auto_trade_policy as auto_trade_policy
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

    def test_emergency_status_precedes_disabled_trade_display(self) -> None:
        state = {
            "status": "EMERGENCY_STOPPED",
            "trade_enabled": False,
            "holding_qty": 0,
        }

        display_status = (
            auto_trade_policy.auto_trade_setting_display_status_for_current_session(
                state,
                {"operation_mode": "SCHEDULED"},
                holding_qty=0,
                current_session_trade_started=False,
                persisted_trade_started=False,
            )
        )

        self.assertEqual("긴급정지", display_status)

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

    def test_first_timer_tick_before_recovery_keeps_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            base_window = self._window()

            class RecoveryBlockedWindow:
                def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                    return False

                def update_startup_recovery_controls(self) -> None:
                    self.update_startup_recovery_controls_mock()

            window = RecoveryBlockedWindow()
            window.__dict__.update(base_window.__dict__)
            window.isVisible = lambda: True
            window.update_startup_recovery_controls_mock = Mock()
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
                patch.object(auto_trade_timer, "probe_all_enabled_routine_stocks_once", None),
            ):
                auto_trade_timer.auto_trade_on_time_policy_timer_tick(window)

            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("RUNNING", state["status"])
        self.assertEqual("", window._last_time_policy_minute_key)
        window.refresh_all.assert_not_called()
        window.update_startup_recovery_controls_mock.assert_called_once_with()

    def test_timer_probes_all_enabled_stocks_without_selected_routine(self) -> None:
        class RecoveryReadyWindow:
            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return True

        window = RecoveryReadyWindow()
        window.isVisible = lambda: True
        window.current_time_policy_minute_key = lambda: "2026-07-25 18:01"
        window._last_time_policy_minute_key = ""
        window.recalculate_all_status_by_operation_policy = Mock(
            return_value={"changed": 0, "failed": 0}
        )
        window.capture_stock_table_view_state = lambda: (set(), 0)
        window.refresh_all = Mock()
        window.restore_stock_table_view_state = Mock()
        window.statusBarMessage = Mock()
        window.parent = lambda: SimpleNamespace(refresh_all=Mock())
        window.rebind_startup_recovery_after_trusted_runtime_update = Mock(
            return_value=True
        )
        window.current_selected_routine_name = lambda: ""
        probe = Mock(
            return_value={
                "checked": 2,
                "logged": 0,
                "error": 0,
                "skip": 0,
                "queued": 0,
            }
        )

        with (
            patch.object(auto_trade_timer, "reset_expired_manual_ats_runtime_selections"),
            patch.object(auto_trade_timer, "manual_ats_market_day_closed", return_value=False),
            patch.object(auto_trade_timer, "probe_all_enabled_routine_stocks_once", probe),
            patch.object(auto_trade_timer, "consume_pending_routine_signals_dry_run", None),
        ):
            auto_trade_timer.auto_trade_on_time_policy_timer_tick(window)

        probe.assert_called_once_with(window, "2026-07-25 18:01")
        self.assertEqual("", window.current_selected_routine_name())

    def test_runtime_file_signature_tracks_central_stocks_without_routine_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = SimpleNamespace(current_selected_routine_dir=lambda: None)

            with patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir):
                signature = auto_trade_timer.auto_trade_current_runtime_file_signature(
                    window
                )

        self.assertIn(str(stock_dir / "state.json"), signature)
        self.assertIn(str(stock_dir / "config.json"), signature)
        self.assertIn(str(stock_dir / "orders.json"), signature)

    def test_runtime_file_tick_refreshes_open_setting_after_central_change(self) -> None:
        window = SimpleNamespace(
            isVisible=lambda: True,
            current_runtime_file_signature=Mock(return_value={"state.json": 2}),
            _runtime_file_snapshot={"state.json": 1},
            capture_stock_table_view_state=Mock(return_value=({"stock"}, 7)),
            load_selected_routine_stocks=Mock(),
            restore_stock_table_view_state=Mock(),
            update_action_buttons=Mock(),
        )

        auto_trade_timer.auto_trade_on_runtime_file_timer_tick(window)

        window.load_selected_routine_stocks.assert_called_once_with()
        window.restore_stock_table_view_state.assert_called_once_with({"stock"}, 7)
        window.update_action_buttons.assert_called_once_with()
        self.assertEqual({"state.json": 2}, window._runtime_file_snapshot)

    def test_missing_runtime_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            (stock_dir / "state.json").unlink()
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

        self.assertEqual(1, result["failed"])
        self.assertFalse((stock_dir / "state.json").exists())

    def test_write_without_matching_read_back_is_reported_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = self._window()
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "write_state_json", return_value=True),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                result = status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )
            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual(1, result["failed"])
        self.assertEqual("RUNNING", state["status"])


if __name__ == "__main__":
    unittest.main()
