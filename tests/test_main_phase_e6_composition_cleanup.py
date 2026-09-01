from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import gui_stock_instance_chart_window
import gui_windows
import stock_repository


class MainPhaseE6CompositionCleanupTest(unittest.TestCase):
    @staticmethod
    def _main_class_node() -> ast.ClassDef:
        source = Path(gui_windows.__file__).read_text(encoding="utf-8")
        module = ast.parse(source)
        return next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
        )

    def test_validated_external_main_contracts_remain_callable(self) -> None:
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

    def test_mainwindow_has_no_participant_storage_or_settings_method_borrowing(
        self,
    ) -> None:
        main_node = self._main_class_node()
        source = ast.unparse(main_node)

        self.assertNotIn(
            "_current_session_operation_participant_stock_codes",
            source,
        )
        borrowed_settings_methods = [
            node
            for node in ast.walk(main_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "AutoTradeSettingWindow"
        ]
        self.assertEqual([], borrowed_settings_methods)

    def test_direct_persistence_is_limited_to_three_classified_boundaries(
        self,
    ) -> None:
        direct_writers: dict[str, set[str]] = {}
        for method in self._main_class_node().body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            writers: set[str] = set()
            for call in (
                node for node in ast.walk(method) if isinstance(node, ast.Call)
            ):
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id == "write_production_recovery_review"
                ):
                    writers.add("write_production_recovery_review")
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "patch_stock_config"
                ):
                    writers.add("StockRepository.patch_stock_config")
            if writers:
                direct_writers[method.name] = writers

        self.assertEqual(
            {
                "_record_production_recovery_review": {
                    "write_production_recovery_review"
                },
                "_write_stock_buy_limit_config": {
                    "StockRepository.patch_stock_config"
                },
                "_write_stock_initial_buy_config": {
                    "StockRepository.patch_stock_config"
                },
            },
            direct_writers,
        )

    def test_mainwindow_contains_no_direct_order_execution_boundary(self) -> None:
        source = ast.unparse(self._main_class_node())

        self.assertNotIn("send_order", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("REAL_READY", source)
        self.assertNotIn("request_hash", source)

    def test_chart_callbacks_are_composition_only(self) -> None:
        build_source = inspect.getsource(
            gui_windows.MainWindow._build_stock_instance_chart_operation_adapter
        )
        execute_source = inspect.getsource(
            gui_windows.MainWindow._execute_stock_instance_chart_operation
        )
        chart_source = Path(gui_stock_instance_chart_window.__file__).read_text(
            encoding="utf-8"
        )

        self.assertIn("MainMonitoringStockOperationAdapter", build_source)
        self.assertIn("auto_trade_apply_selected_early_close", execute_source)
        self.assertNotIn("import gui_windows", chart_source)
        self.assertNotIn("from gui_windows import", chart_source)

    def test_repository_does_not_reacquire_assignment_application_semantics(
        self,
    ) -> None:
        repository_source = Path(stock_repository.__file__).read_text(encoding="utf-8")

        self.assertNotIn("assignment_episode_linkage", repository_source)
        for method_name in (
            "update_stock_routine",
            "update_stock_routine_result",
            "update_stock_routine_instance",
            "update_stock_routine_instance_result",
        ):
            with self.subTest(method_name=method_name):
                self.assertFalse(
                    hasattr(stock_repository.StockRepository, method_name)
                )


if __name__ == "__main__":
    unittest.main()
