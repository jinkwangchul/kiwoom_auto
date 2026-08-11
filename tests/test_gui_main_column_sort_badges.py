from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QFrame, QPushButton

import gui_main_table_loader as table_loader
import gui_windows
from gui_windows import AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT, MainWindow


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

    def test_excluded_badge_is_emphasized_and_toggles_only_view_scope(self) -> None:
        class Host:
            pass

        host = Host()
        host._main_routine_excluded_only = False
        host._main_routine_metric_sort_key = "profit"
        host._main_routine_metric_sort_active = True
        host._main_routine_initial_buy_sort_mode = "AMOUNT"
        host._main_routine_column_sort_key = "status"
        host._update_main_routine_filter_badges = MagicMock()
        host._reload_main_routine_table_preserving_view = MagicMock()
        host._create_main_routine_filter_badge = MethodType(
            MainWindow._create_main_routine_filter_badge,
            host,
        )
        host._set_main_routine_excluded_only = MethodType(
            MainWindow._set_main_routine_excluded_only,
            host,
        )

        button = MainWindow._create_main_routine_excluded_badge(host)
        try:
            self.assertEqual("제외종목(0)", button.text())
            self.assertEqual(
                round(AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT * 1.1),
                button.height(),
            )
            self.assertGreaterEqual(
                button.width(),
                (
                    button.fontMetrics().horizontalAdvance(f"{button.LABEL}(")
                    + table_loader.routine_aggregate_number_slot_width(button.font())
                    + button.fontMetrics().horizontalAdvance(")")
                    + 28
                ),
            )

            original_sorts = (
                host._main_routine_metric_sort_key,
                host._main_routine_metric_sort_active,
                host._main_routine_initial_buy_sort_mode,
                host._main_routine_column_sort_key,
            )
            MainWindow._set_main_routine_excluded_only(host, True)
            self.assertTrue(host._main_routine_excluded_only)
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
            self.assertEqual(2, host._reload_main_routine_table_preserving_view.call_count)
        finally:
            button.close()
            button.deleteLater()

    def test_excluded_badge_uses_fixed_three_digit_centered_number_slot(self) -> None:
        host = SimpleNamespace(
            _set_main_routine_excluded_only=MagicMock(),
            _main_routine_excluded_button=None,
        )
        button = MainWindow._create_main_routine_excluded_badge(host)
        try:
            expected_width = button.width()
            expected_rects = button.content_rects()
            expected_number_width = table_loader.routine_aggregate_number_slot_width(
                button.count_font()
            )
            for count in (0, 1, 2, 9, 10, 15, 99, 100, 123, 999):
                button.set_excluded_count(count)
                label_rect, left_paren_rect, number_rect, right_paren_rect = (
                    button.content_rects()
                )
                self.assertEqual(expected_width, button.width())
                self.assertEqual(expected_rects, button.content_rects())
                self.assertEqual(expected_number_width, number_rect.width())
                self.assertEqual(label_rect.right() + 1, left_paren_rect.left())
                self.assertEqual(left_paren_rect.right() + 1, number_rect.left())
                self.assertEqual(number_rect.right() + 1, right_paren_rect.left())
                number_width = button.fontMetrics().horizontalAdvance(str(count))
                left_space = (number_rect.width() - number_width) // 2
                right_space = number_rect.width() - number_width - left_space
                self.assertLessEqual(abs(left_space - right_space), 1)
                self.assertEqual(f"제외종목({count})", button.text())
            self.assertAlmostEqual(
                button.font().pointSizeF() - 1.0,
                button.count_font().pointSizeF(),
                places=4,
            )
        finally:
            button.close()
            button.deleteLater()

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
                normal = table_loader._instance_stock_counts(False)
                excluded = table_loader._instance_stock_counts(True)

            self.assertEqual(
                ["000001"],
                [stock["code"] for stock in normal["instance-a"]["stocks"]],
            )
            self.assertEqual(
                ["000002"],
                [stock["code"] for stock in excluded["instance-a"]["stocks"]],
            )
            self.assertEqual(1, normal["instance-a"]["registered"])
            self.assertEqual(1, excluded["instance-a"]["registered"])

    def test_excluded_badge_renders_above_table_at_right_with_shared_style(self) -> None:
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

            button = window.findChild(QPushButton, "mainRoutineExcludedStockBadge")
            self.assertIsNotNone(button)
            button_pos = button.mapTo(window, QPoint(0, 0))
            table_pos = window.routine_table.mapTo(window, QPoint(0, 0))
            valid_button = window._main_routine_valid_button
            self.assertLess(button_pos.y(), table_pos.y())
            self.assertLessEqual(
                abs(
                    (button_pos.x() + button.width())
                    - (table_pos.x() + window.routine_table.width())
                ),
                8,
            )
            self.assertEqual(
                round(valid_button.height() * 1.1),
                button.height(),
            )
            self.assertAlmostEqual(
                valid_button.font().pointSizeF() * 1.1,
                button.font().pointSizeF(),
                places=4,
            )
            self.assertGreaterEqual(
                button.width() - button.fontMetrics().horizontalAdvance(button.text()),
                28,
            )
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
