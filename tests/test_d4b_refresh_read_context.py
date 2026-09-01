# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_unregister as unregister_ops
import gui_main_table_loader as main_loader
import gui_review_required_window as review_window
import gui_stock_register_window as stock_register_window
import gui_windows


class D4bUniverseSyncBoundaryTests(unittest.TestCase):
    def test_assignment_dialog_syncs_before_shared_projection(self) -> None:
        operation_context = object()
        calls: list[str] = []
        with patch.object(
            setting_window,
            "sync_auto_trade_monitoring_universe",
            side_effect=lambda _context: calls.append("sync"),
        ), patch.object(
            setting_window,
            "refresh_auto_trade_views",
            side_effect=lambda _context: calls.append("refresh"),
        ):
            setting_window.InstanceStockSearchRegisterDialog._refresh_parent_views(
                operation_context,
                sync_monitoring_universe=True,
            )

        self.assertEqual(calls, ["sync", "refresh"])

    def test_membership_mutation_routes_name_explicit_sync(self) -> None:
        functions = (
            unregister_ops.unregister_selected_auto_trade_stocks,
            setting_window.delete_routine_instance_with_existing_policy,
            setting_window.AutoTradeSettingWindow.unregister_routine_tree_stock,
            stock_register_window.StockRegisterWindow.assign_selected_stocks_to_routine_instance,
            stock_register_window.StockRegisterWindow.unassign_selected_stock_routines,
            stock_register_window.StockRegisterWindow.delete_selected_stock,
            review_window.GlobalReviewRequiredWindow.unassign_selected_review_items,
            review_window.GlobalReviewRequiredWindow.delete_selected_review_items,
            gui_windows.MainWindow.delete_routine_group_completely,
        )
        for function in functions:
            with self.subTest(function=function.__name__):
                self.assertIn(
                    "sync_auto_trade_monitoring_universe",
                    inspect.getsource(function),
                )


class D4bLocalReadContextTests(unittest.TestCase):
    def test_main_refresh_reuses_one_local_stock_snapshot(self) -> None:
        stock_dir = Path("stocks") / "000001_sample"
        inspection = SimpleNamespace(
            state={"phase": "before"},
            review_required=False,
        )
        context = {
            "stock_data_by_dir": {
                str(stock_dir): {
                    "config": {"assigned_routine_instance_id": "instance-1"},
                    "state": inspection.state,
                    "review_inspection": inspection,
                }
            }
        }

        class Owner:
            _main_refresh_read_context = None

            def load_routine_table(self):
                self.first = main_loader._main_refresh_stock_data(self, stock_dir)

            update_budget_panel = Mock()
            update_emergency_button_state = Mock()
            update_review_required_button_text = Mock()
            update_global_operation_button_state = Mock()

        owner = Owner()
        with patch.object(
            gui_windows,
            "build_main_refresh_read_context",
            return_value=context,
        ), patch.object(
            gui_windows,
            "main_load_running_stock_table",
            side_effect=lambda window: setattr(
                window,
                "second",
                main_loader._main_refresh_stock_data(window, stock_dir),
            ),
        ):
            gui_windows.MainWindow.refresh_all(owner)

        self.assertIs(owner.first, owner.second)
        self.assertEqual(owner.first["state"]["phase"], "before")
        self.assertIsNone(owner._main_refresh_read_context)

    def test_refresh_context_reads_config_state_and_review_once(self) -> None:
        stock_path = "stocks/000001_sample"
        inspection = SimpleNamespace(
            state={"review_required": False, "version": 1},
            review_required=False,
        )

        def read_json(path):
            if Path(path).name == "config.json":
                return {"assigned_routine_instance_id": "instance-1"}
            return {"review_required": False, "version": 1}

        with patch.object(main_loader, "load_routine_definitions", return_value=[]), patch.object(
            main_loader,
            "load_persisted_routine_instances",
            return_value=[],
        ), patch.object(main_loader, "get_group_records", return_value=[]), patch.object(
            main_loader,
            "read_base_stocks",
            return_value=[{"code": "000001", "name": "sample", "stock_path": stock_path}],
        ), patch.object(main_loader, "read_json_dict", side_effect=read_json) as read, patch.object(
            main_loader,
            "inspect_stock_review_state",
            return_value=inspection,
        ) as inspect_review:
            context = main_loader.build_main_refresh_read_context()

        self.assertEqual(read.call_count, 2)
        inspect_review.assert_called_once()
        stock_data = next(iter(context["stock_data_by_dir"].values()))
        self.assertEqual(stock_data["state"]["version"], 1)
        self.assertEqual(
            stock_data["config"]["assigned_routine_instance_id"],
            "instance-1",
        )

    def test_settings_full_refresh_uses_refresh_local_snapshot_scope(self) -> None:
        source = inspect.getsource(setting_window.AutoTradeSettingWindow.refresh_all)
        self.assertIn("snapshot_builder()", source)
        self.assertIn("previous_snapshot", source)
        self.assertIn("finally", source)

    def test_main_review_count_reuses_full_refresh_stock_snapshot(self) -> None:
        context = {
            "stocks": ({"code": "000001", "stock_path": "stocks/000001_sample"},),
            "stock_data_by_dir": {
                str(Path(gui_windows.PROJECT_ROOT) / "stocks/000001_sample"): {
                    "config": {},
                    "state": {"review_required": True},
                    "state_issue_reason": "",
                }
            },
        }
        owner = SimpleNamespace(_main_refresh_read_context=context)
        with patch.object(
            gui_windows,
            "collect_global_review_required_rows",
            return_value=[{"code": "000001"}],
        ) as collect:
            count = gui_windows.MainWindow.review_required_stock_count(owner)

        self.assertEqual(count, 1)
        collect.assert_called_once_with(
            preloaded_stocks=context["stocks"],
            preloaded_stock_data_by_dir=context["stock_data_by_dir"],
        )


if __name__ == "__main__":
    unittest.main()
