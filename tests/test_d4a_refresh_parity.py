# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_auto_trade_ats_ops as ats_ops
import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_status_ops as status_ops
import gui_operation_ui_context as ui_context
import gui_review_required_window as review_window
import gui_windows


class _OperationHost:
    def __init__(self) -> None:
        self.sync_count = 0

    def sync_monitoring_universe_for_current_session(self) -> None:
        self.sync_count += 1


class _SettingsWindow:
    def __init__(self) -> None:
        self.refresh_count = 0

    def refresh_all(self) -> None:
        self.refresh_count += 1


class _MainWindow:
    refresh_auto_trade_assignment_views = (
        gui_windows.MainWindow.refresh_auto_trade_assignment_views
    )

    def __init__(self, settings=None) -> None:
        self.host = _OperationHost()
        self.auto_trade_setting_window = settings
        self.refresh_count = 0

    def main_monitoring_auto_trade_operation_host(self):
        return self.host

    def refresh_all(self) -> None:
        self.refresh_count += 1


class _OperationContext:
    pass


class _ReviewWindow:
    def __init__(self) -> None:
        self.load_count = 0

    def load_review_items(self) -> None:
        self.load_count += 1


class D4aRefreshParityTests(unittest.TestCase):
    def test_settings_command_refreshes_main_and_open_settings_once(self) -> None:
        settings = _SettingsWindow()
        main = _MainWindow(settings)
        context = _OperationContext()

        with patch.object(ui_context, "persistent_feature_owner", return_value=main), patch.object(
            gui_windows.sip,
            "isdeleted",
            return_value=False,
        ):
            ui_context.refresh_auto_trade_views(context)

        self.assertEqual(main.host.sync_count, 0)
        self.assertEqual(main.refresh_count, 1)
        self.assertEqual(settings.refresh_count, 1)

    def test_main_command_does_not_create_closed_settings_window(self) -> None:
        main = _MainWindow(None)

        with patch.object(gui_windows.sip, "isdeleted", return_value=False):
            ui_context.refresh_auto_trade_views(main)

        self.assertEqual(main.host.sync_count, 0)
        self.assertEqual(main.refresh_count, 1)
        self.assertIsNone(main.auto_trade_setting_window)

    def test_review_refresh_updates_review_main_and_open_settings(self) -> None:
        settings = _SettingsWindow()
        main = _MainWindow(settings)
        review = _ReviewWindow()

        with patch.object(
            review_window,
            "persistent_feature_owner",
            return_value=main,
        ), patch.object(gui_windows.sip, "isdeleted", return_value=False):
            review_window.GlobalReviewRequiredWindow._refresh_after_review_action(review)

        self.assertEqual(review.load_count, 1)
        self.assertEqual(main.refresh_count, 1)
        self.assertEqual(settings.refresh_count, 1)

    def test_operation_mode_refreshes_only_when_a_mutation_succeeded(self) -> None:
        with patch.object(status_ops, "refresh_auto_trade_views") as refresh:
            status_ops.auto_trade_finalize_operation_mode_result(
                object(),
                {"requested": 1, "succeeded": 1, "failed": 0, "results": []},
            )
        refresh.assert_called_once()

        with patch.object(status_ops, "refresh_auto_trade_views") as refresh, patch.object(
            status_ops.QMessageBox,
            "warning",
        ):
            status_ops.auto_trade_finalize_operation_mode_result(
                object(),
                {
                    "requested": 1,
                    "succeeded": 0,
                    "failed": 1,
                    "results": [
                        {
                            "stock_code": "000001",
                            "stock_name": "blocked",
                            "success": False,
                            "reason": "blocked",
                        }
                    ],
                },
            )
        refresh.assert_not_called()

    def test_manual_ats_refreshes_only_for_changed_readback(self) -> None:
        selected = [(Path("stock"), "000001", "sample")]

        def read_json(path):
            return {"operation_mode": "CONTINUOUS"} if Path(path).name == "config.json" else {}

        with patch.object(ats_ops, "read_json_dict", side_effect=read_json), patch.object(
            ats_ops,
            "write_manual_ats_runtime_selection",
            return_value=True,
        ), patch.object(ats_ops, "append_production_event"), patch.object(
            ats_ops,
            "append_stock_log",
        ), patch.object(ats_ops, "refresh_auto_trade_views") as refresh:
            result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                object(),
                selected,
                {"extra1": True},
            )

        self.assertEqual(result["changed"], 1)
        refresh.assert_called_once()

        with patch.object(ats_ops, "read_json_dict", side_effect=read_json), patch.object(
            ats_ops,
            "manual_ats_runtime_selected_keys",
            return_value=("extra1",),
        ), patch.object(
            ats_ops,
            "write_manual_ats_runtime_selection",
            return_value=True,
        ), patch.object(ats_ops, "append_stock_log"), patch.object(
            ats_ops,
            "refresh_auto_trade_views",
        ) as refresh:
            result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                object(),
                selected,
                {"extra1": True},
            )

        self.assertEqual(result["changed"], 0)
        refresh.assert_not_called()

    def test_settings_mutation_routes_use_shared_refresh(self) -> None:
        functions = (
            setting_window.delete_routine_instance_with_existing_policy,
            setting_window.AutoTradeSettingWindow.toggle_selected_manual_override_flag,
            setting_window.AutoTradeSettingWindow.reset_selected_manual_override,
            setting_window.AutoTradeSettingWindow.unregister_routine_tree_stock,
            setting_window.AutoTradeSettingWindow.finish_routine_instance_name_edit,
            setting_window.AutoTradeSettingWindow.hide_historical_stock_display,
            setting_window.AutoTradeSettingWindow._handle_operation_environment_settings_saved,
            setting_window.AutoTradeSettingWindow.open_selected_stock_policy_settings,
        )
        for function in functions:
            with self.subTest(function=function.__name__):
                self.assertIn("refresh_auto_trade_views", inspect.getsource(function))

    def test_main_budget_and_limit_routes_use_shared_refresh(self) -> None:
        functions = (
            gui_windows.MainWindow.finish_routine_instance_name_edit,
            gui_windows.MainWindow._open_running_budget_adjustment_dialog,
            gui_windows.MainWindow.toggle_routine_stock_initial_buy_mode,
            gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click,
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit,
            gui_windows.MainWindow.handle_routine_instance_buy_limit_double_click,
            gui_windows.MainWindow.finish_routine_instance_buy_limit_edit,
        )
        for function in functions:
            with self.subTest(function=function.__name__):
                self.assertIn("refresh_auto_trade_views", inspect.getsource(function))


if __name__ == "__main__":
    unittest.main()
