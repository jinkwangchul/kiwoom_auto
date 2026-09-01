from __future__ import annotations

import inspect
import unittest

import gui_windows


class MainPhaseE2DelegateCleanupTest(unittest.TestCase):
    def test_dead_mainwindow_compatibility_delegates_are_removed(self) -> None:
        removed = (
            "_style_main_visible_early_close_button",
            "_apply_main_routine_sort",
            "_apply_main_running_sort",
            "load_running_stock_table",
            "routine_name_for_stock_dir",
            "has_emergency_stopped_stock",
            "emergency_review_reason_for_stock",
            "update_runtime_stock_status",
            "execute_emergency_stop",
            "release_emergency_stop",
        )

        for method_name in removed:
            with self.subTest(method_name=method_name):
                self.assertFalse(hasattr(gui_windows.MainWindow, method_name))

    def test_validated_external_mainwindow_contracts_are_preserved(self) -> None:
        contracts = (
            "all_runtime_stock_dirs",
            "current_orderable_cash_for_budget",
            "main_monitoring_auto_trade_operation_host",
            "open_review_required_window",
            "open_routine_instance_stock_register_from_main_table",
            "rebind_startup_recovery_after_trusted_runtime_update",
            "refresh_all",
            "refresh_auto_trade_assignment_views",
            "registered_operation_targets",
            "review_required_stock_count",
            "selected_account_no",
            "startup_recovery_session_ready",
            "toggle_projected_routine_instance_operation",
            "update_global_operation_button_state",
            "update_review_required_button_text",
        )

        for method_name in contracts:
            with self.subTest(method_name=method_name):
                self.assertTrue(callable(getattr(gui_windows.MainWindow, method_name, None)))

    def test_qt_entrypoints_remain_on_mainwindow(self) -> None:
        callbacks = (
            "sort_main_routine_table_by_column",
            "sort_main_running_table_by_column",
            "on_emergency_stop_clicked",
            "open_event_record_window",
            "close_all_persistent_feature_windows",
        )

        for method_name in callbacks:
            with self.subTest(method_name=method_name):
                self.assertTrue(callable(getattr(gui_windows.MainWindow, method_name, None)))

    def test_refresh_all_calls_the_existing_table_loader_owner_directly(self) -> None:
        source = inspect.getsource(gui_windows.MainWindow.refresh_all)

        self.assertIn("main_load_running_stock_table(self)", source)
        self.assertNotIn("self.load_running_stock_table()", source)


if __name__ == "__main__":
    unittest.main()
