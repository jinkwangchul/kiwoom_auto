# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import unittest

import gui_auto_trade_close as close_ops
import gui_auto_trade_operation_host as operation_host
import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_timer as auto_trade_timer
import gui_main_stock_context_menu as main_context_menu


class D4c2FilesystemPollRemovalTests(unittest.TestCase):
    def test_settings_filesystem_poll_symbols_are_removed(self) -> None:
        cls = setting_window.AutoTradeSettingWindow
        self.assertFalse(hasattr(cls, "current_runtime_file_signature"))
        self.assertFalse(hasattr(cls, "on_runtime_file_timer_tick"))
        self.assertFalse(
            hasattr(auto_trade_timer, "auto_trade_current_runtime_file_signature")
        )
        self.assertFalse(
            hasattr(auto_trade_timer, "auto_trade_on_runtime_file_timer_tick")
        )

    def test_settings_init_has_no_runtime_file_timer_or_mtime_baseline(self) -> None:
        source = inspect.getsource(setting_window.AutoTradeSettingWindow.__init__)
        self.assertNotIn("_runtime_file_timer", source)
        self.assertNotIn("_runtime_file_snapshot", source)
        self.assertNotIn("2_000", source)

    def test_full_refresh_does_not_build_a_filesystem_signature(self) -> None:
        source = inspect.getsource(setting_window.AutoTradeSettingWindow.refresh_all)
        self.assertNotIn("runtime_file_signature", source)
        self.assertNotIn("_runtime_file_snapshot", source)
        self.assertIn("load_selected_routine_stocks", source)

    def test_timer_lifecycle_keeps_only_existing_ui_timers(self) -> None:
        for method_name in (
            "showEvent",
            "start_periodic_timers_after_recovery",
            "stop_periodic_timers_for_recovery",
        ):
            source = inspect.getsource(
                getattr(setting_window.AutoTradeSettingWindow, method_name)
            )
            self.assertNotIn("_runtime_file_timer", source)
            self.assertIn("_time_policy_timer", source)
            self.assertIn("_pnl_refresh_timer", source)

    def test_timer_module_has_no_periodic_stock_file_stat_scan(self) -> None:
        source = inspect.getsource(auto_trade_timer)
        self.assertNotIn("st_mtime_ns", source)
        self.assertNotIn('("state.json", "config.json", "orders.json")', source)
        self.assertNotIn("sync_auto_trade_monitoring_universe", source)

    def test_legacy_poll_compatibility_is_removed_from_operation_callers(self) -> None:
        for module in (operation_host, main_context_menu, close_ops):
            source = inspect.getsource(module)
            self.assertNotIn("current_runtime_file_signature", source)
            self.assertNotIn("_runtime_file_snapshot", source)


if __name__ == "__main__":
    unittest.main()
