from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QFrame, QPushButton, QTableWidget

import gui_main_table_loader as table_loader
import gui_windows
from gui_windows import MainWindow


class GuiMainColumnSortBadgesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _badge_host(self):
        host = SimpleNamespace(
            _main_routine_valid_only=True,
            _main_routine_display_level="stock",
            _main_routine_metric_sort_key="",
            _main_routine_metric_sort_active=False,
            _main_routine_initial_buy_sort_mode="",
            _main_routine_initial_buy_sort_next_mode="AMOUNT",
            _main_routine_column_sort_key="",
            _main_routine_valid_button=None,
            _main_routine_level_buttons={},
            _main_routine_metric_buttons={},
            _main_routine_initial_buy_sort_button=None,
            _main_routine_column_sort_buttons={},
            _update_main_routine_filter_badges=None,
        )
        host._create_main_routine_filter_badge = MethodType(
            MainWindow._create_main_routine_filter_badge,
            host,
        )
        host._create_main_routine_filter_separator = MethodType(
            MainWindow._create_main_routine_filter_separator,
            host,
        )
        host._main_routine_filter_badge_style = MainWindow._main_routine_filter_badge_style
        host._main_routine_initial_buy_badge_enabled = lambda: (
            host._main_routine_display_level == "stock"
        )
        host._update_main_routine_filter_badges = MethodType(
            MainWindow._update_main_routine_filter_badges,
            host,
        )
        host._set_main_routine_valid_only = MagicMock()
        host._set_main_routine_display_level = MagicMock()
        host._set_main_routine_metric_sort = MagicMock()
        host._toggle_main_routine_initial_buy_sort_mode = MagicMock()
        host._set_main_routine_column_sort = MagicMock()
        return host

    def test_badges_follow_requested_order_and_level_enablement(self) -> None:
        host = self._badge_host()
        badge_area = MainWindow._create_routine_filter_badge_area(host)
        try:
            labels = [button.text() for button in badge_area.findChildren(QPushButton)]
            self.assertEqual(
                ["운영", "현황", "상태", "방식", "청산"],
                labels[-5:],
            )
            self.assertIsNotNone(
                badge_area.findChild(QFrame, "mainRoutineColumnSortSeparator")
            )
            self.assertTrue(all(button.isEnabled() for button in host._main_routine_column_sort_buttons.values()))

            host._main_routine_display_level = "routine"
            host._update_main_routine_filter_badges()
            self.assertTrue(all(not button.isEnabled() for button in host._main_routine_column_sort_buttons.values()))
        finally:
            badge_area.close()
            badge_area.deleteLater()
            self.app.processEvents()

    def test_column_sort_is_mutually_exclusive_and_reloads_once(self) -> None:
        window = SimpleNamespace(
            _main_routine_display_level="stock",
            _main_routine_metric_sort_key="profit",
            _main_routine_metric_sort_active=True,
            _main_routine_initial_buy_sort_mode="AMOUNT",
            _main_routine_initial_buy_sort_next_mode="QUANTITY",
            _main_routine_column_sort_key="",
            _update_main_routine_filter_badges=MagicMock(),
            _reload_main_routine_table_preserving_view=MagicMock(),
        )
        MainWindow._set_main_routine_column_sort(window, "status")
        self.assertEqual("status", window._main_routine_column_sort_key)
        self.assertEqual("", window._main_routine_metric_sort_key)
        self.assertFalse(window._main_routine_metric_sort_active)
        self.assertEqual("", window._main_routine_initial_buy_sort_mode)
        window._reload_main_routine_table_preserving_view.assert_called_once_with()

        window._main_routine_initial_buy_badge_enabled = lambda: True
        MainWindow._toggle_main_routine_initial_buy_sort_mode(window)
        self.assertEqual("", window._main_routine_column_sort_key)

        window._main_routine_column_sort_key = "operation"
        MainWindow._set_main_routine_metric_sort(window, "holding")
        self.assertEqual("", window._main_routine_column_sort_key)

    def test_all_column_sort_keys_group_rows_by_existing_metadata(self) -> None:
        rows = [
            {
                "code": "2",
                "column_sort_values": {
                    "operation": "수동+ATS",
                    "situation": 3,
                    "status": 1,
                    "method": "시장가",
                    "liquidation": "10분",
                },
            },
            {
                "code": "1",
                "column_sort_values": {
                    "operation": "수동",
                    "situation": 0,
                    "status": 0,
                    "method": "현재가",
                    "liquidation": "5분",
                },
            },
        ]
        expected_first = {
            "operation": "1",
            "situation": "1",
            "status": "1",
            "method": "2",
            "liquidation": "2",
        }
        for sort_key, first_code in expected_first.items():
            with self.subTest(sort_key=sort_key):
                copied = [dict(row) for row in rows]
                table_loader.sort_routine_stock_rows_by_column(copied, sort_key)
                self.assertEqual(first_code, copied[0]["code"])

    def test_stock_row_metadata_reuses_display_values_and_situation_sort_role(self) -> None:
        values = ["005930 삼성전자", "금액 1원", "수동+ATS", "●", "매수/매도", "시장가", "10분"]
        tokens = [{}, {}, {}, {"sort_value": 3}]
        with (
            patch.object(table_loader, "_routine_tree_stock_display_values", return_value=values),
            patch.object(table_loader, "_routine_tree_stock_display_snapshots", return_value=tokens),
            patch.object(
                table_loader,
                "_routine_tree_stock_metric_values",
                return_value=((), "gray", "-", None, {}),
            ),
        ):
            row = table_loader._routine_tree_stock_row(
                SimpleNamespace(),
                definition_id="definition",
                instance_id="instance",
                stock={"code": "005930", "name": "삼성전자"},
            )
        self.assertEqual(
            {
                "operation": "수동+ATS",
                "situation": 3,
                "status": 1,
                "method": "시장가",
                "liquidation": "10분",
            },
            row["column_sort_values"],
        )

    def test_removed_excluded_badge_keeps_shared_scope_switching(self) -> None:
        class Host:
            pass

        host = Host()
        host._main_routine_excluded_only = False
        host._main_routine_stock_scope = "all"
        host._main_routine_metric_sort_key = "profit"
        host._main_routine_metric_sort_active = True
        host._main_routine_initial_buy_sort_mode = "AMOUNT"
        host._main_routine_column_sort_key = "status"
        host._update_main_routine_filter_badges = MagicMock()
        host._reload_main_routine_table_preserving_view = MagicMock()
        self.assertFalse(hasattr(MainWindow, "_create_main_routine_excluded_badge"))

        original_sorts = (
            host._main_routine_metric_sort_key,
            host._main_routine_metric_sort_active,
            host._main_routine_initial_buy_sort_mode,
            host._main_routine_column_sort_key,
        )
        MainWindow._set_main_routine_excluded_only(host, True)
        self.assertTrue(host._main_routine_excluded_only)
        self.assertEqual("excluded", host._main_routine_stock_scope)
        self.assertEqual(
            original_sorts,
            (
                host._main_routine_metric_sort_key,
                host._main_routine_metric_sort_active,
                host._main_routine_initial_buy_sort_mode,
                host._main_routine_column_sort_key,
            ),
        )
        MainWindow._set_main_routine_excluded_only(host, False)
        self.assertFalse(host._main_routine_excluded_only)
        self.assertEqual("all", host._main_routine_stock_scope)
        self.assertEqual(2, host._reload_main_routine_table_preserving_view.call_count)

    def test_shared_summary_excluded_count_uses_fixed_number_slot(self) -> None:
        routine_table = QTableWidget()
        host = SimpleNamespace(
            routine_table=routine_table,
            _set_main_routine_valid_only=MagicMock(),
            _activate_main_routine_summary_badge=MagicMock(),
            _main_routine_display_level="stock",
            _main_routine_stock_scope="all",
            _main_routine_excluded_only=False,
            review_required_window=None,
            btn_review_required=None,
        )
        summary = MainWindow._create_main_routine_summary(host)
        try:
            self.assertIsNone(
                summary.findChild(QPushButton, "mainRoutineExcludedStockBadge")
            )
            excluded_button = host._main_routine_summary_count_buttons["excluded"]
            label, value = host._main_routine_summary_count_labels["excluded"]
            self.assertEqual("mainRoutineSummaryCountBadge", excluded_button.objectName())
            self.assertEqual("제외", label.text())
            expected_number_width = value.width()
            self.assertEqual(
                host._main_routine_summary_number_slot_width,
                expected_number_width,
            )
            for count in (0, 1, 2, 9, 10, 15, 99, 100, 123, 999):
                MainWindow._update_main_routine_summary(
                    host,
                    {"count_badges": (("excluded", "제외", count),)},
                )
                self.assertEqual(str(count), value.text())
                self.assertEqual(expected_number_width, value.width())
        finally:
            summary.close()
            summary.deleteLater()
            routine_table.close()
            routine_table.deleteLater()
            self.app.processEvents()

    def test_instance_stock_reader_separates_normal_and_excluded_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stocks = []
            for code, excluded in (("000001", False), ("000002", True)):
                stock_dir = root / f"{code}_Stock"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "operation_excluded": excluded,
                        }
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED"}),
                    encoding="utf-8",
                )
                stocks.append(
                    {
                        "code": code,
                        "name": "Stock",
                        "stock_path": str(stock_dir),
                    }
                )

            with (
                patch.object(table_loader, "read_base_stocks", return_value=stocks),
                patch.object(
                    table_loader,
                    "load_persisted_routine_instances",
                    return_value=[SimpleNamespace(instance_id="instance-a")],
                ),
            ):
                normal = table_loader._instance_stock_counts(stock_scope="normal")
                excluded = table_loader._instance_stock_counts(stock_scope="excluded")

            self.assertEqual(
                ["000001"],
                [stock["code"] for stock in normal["instance-a"]["stocks"]],
            )
            self.assertEqual(
                ["000002"],
                [stock["code"] for stock in excluded["instance-a"]["stocks"]],
            )
            self.assertEqual(2, normal["instance-a"]["registered"])
            self.assertEqual(2, excluded["instance-a"]["registered"])
            self.assertEqual(1, normal["instance-a"]["normal"])
            self.assertEqual(1, normal["instance-a"]["excluded"])
            self.assertEqual(1, excluded["instance-a"]["normal"])
            self.assertEqual(1, excluded["instance-a"]["excluded"])

    def test_removed_right_side_badge_uses_shared_summary_count_badge(self) -> None:
        api = SimpleNamespace(
            unavailable_reason=lambda: "test double",
            login_state_changed=None,
            raw_chejan_received=None,
        )
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}),
            patch.object(gui_windows.MainWindow, "refresh_all"),
        ):
            window = gui_windows.MainWindow()
        try:
            window.resize(1280, 720)
            window.show()
            self.app.processEvents()

            self.assertFalse(hasattr(MainWindow, "_create_main_routine_excluded_badge"))
            self.assertIsNone(
                window.findChild(QPushButton, "mainRoutineExcludedStockBadge")
            )
            summary = window._main_routine_summary_widget
            button = window._main_routine_summary_count_buttons["excluded"]
            label, value = window._main_routine_summary_count_labels["excluded"]
            self.assertTrue(summary.isAncestorOf(button))
            self.assertEqual("mainRoutineSummaryCountBadge", button.objectName())
            self.assertEqual("제외", label.text())
            self.assertEqual("0", value.text())
            self.assertFalse(button.isChecked())
            inactive_style = button.styleSheet()

            window._main_routine_excluded_only = True
            window._update_main_routine_filter_badges()
            self.assertTrue(button.isChecked())
            self.assertNotEqual(inactive_style, button.styleSheet())
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
