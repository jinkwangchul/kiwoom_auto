# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel

import gui_stock_performance_window as performance_window
from gui_stock_performance_window import (
    OVERALL_METRIC_TITLES,
    PERIOD_METRIC_TITLES,
    PERIOD_MODES,
    StockPerformanceWindow,
    open_stock_performance,
)


class StockPerformanceEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_stock_identity_is_used_without_dummy_identity(self) -> None:
        dialog = StockPerformanceWindow(
            stock_code="005930", stock_name="삼성전자"
        )
        self.addCleanup(dialog.close)

        self.assertEqual("종목실적 - 005930 삼성전자", dialog.windowTitle())
        self.assertNotIn("카카오게임즈", dialog.windowTitle())

    def test_no_performance_data_shows_explicit_empty_state(self) -> None:
        dialog = StockPerformanceWindow()
        self.addCleanup(dialog.close)

        self.assertEqual("거래 실적 없음", dialog.empty_state_label.text())
        self.assertEqual(0, dialog.daily_table.rowCount())
        self.assertEqual(0, dialog.routine_table.rowCount())
        self.assertEqual([], dialog.chart.points)
        self.assertEqual("조회기간 -", dialog.daily_title_label.text())

    def test_summary_values_are_unknown_not_zero(self) -> None:
        dialog = StockPerformanceWindow()
        self.addCleanup(dialog.close)

        overall_values = dialog.findChildren(QLabel, "stockPerformanceSummaryValue")
        self.assertEqual(len(OVERALL_METRIC_TITLES), len(overall_values))
        self.assertTrue(all(label.text() == "-" for label in overall_values))
        self.assertEqual(set(PERIOD_METRIC_TITLES), set(dialog.period_value_labels))
        self.assertTrue(
            all(label.text() == "-" for label in dialog.period_value_labels.values())
        )

    def test_period_filters_do_not_invent_dates_or_graph_points(self) -> None:
        dialog = StockPerformanceWindow()
        self.addCleanup(dialog.close)

        for mode in PERIOD_MODES:
            dialog.period_buttons[mode].click()
            self.assertEqual(mode, dialog.current_period)
            self.assertEqual("조회기간 -", dialog.daily_title_label.text())
            self.assertEqual([], dialog.chart.points)
            self.assertEqual("", dialog.chart.unit_text)

    def test_tables_keep_existing_read_only_structure(self) -> None:
        dialog = StockPerformanceWindow()
        self.addCleanup(dialog.close)

        self.assertEqual(8, dialog.daily_table.columnCount())
        self.assertEqual(6, dialog.routine_table.columnCount())
        self.assertEqual(
            Qt.ScrollBarAlwaysOn, dialog.daily_table.verticalScrollBarPolicy()
        )
        self.assertEqual(
            Qt.ScrollBarAlwaysOff, dialog.daily_table.horizontalScrollBarPolicy()
        )
        self.assertEqual(
            ["루틴", "횟수", "승 / 패", "손익금", "손익률", "승률"],
            [
                dialog.routine_table.horizontalHeaderItem(column).text()
                for column in range(dialog.routine_table.columnCount())
            ],
        )

    def test_module_contains_no_former_hardcoded_performance_contract(self) -> None:
        for name in (
            "PROTOTYPE_STOCK_CODE",
            "PROTOTYPE_STOCK_NAME",
            "PERIOD_RANGES",
            "GRAPH_SERIES",
            "DAILY_ROWS",
            "OVERALL_METRICS",
            "PERIOD_METRICS",
            "ROUTINE_ROWS",
            "prototype_cycle_outcome",
        ):
            self.assertFalse(hasattr(performance_window, name), name)

    def test_opening_passes_selected_real_stock_identity(self) -> None:
        window = Mock()
        window.selected_stock_info.return_value = (object(), "005930", "삼성전자")

        with patch(
            "gui_stock_performance_window.StockPerformanceWindow"
        ) as dialog_class:
            open_stock_performance(window)

        dialog_class.assert_called_once_with(
            parent=window, stock_code="005930", stock_name="삼성전자"
        )
        dialog_class.return_value.show.assert_called_once_with()
        dialog_class.return_value.raise_.assert_called_once_with()
        dialog_class.return_value.activateWindow.assert_called_once_with()

    def test_no_selection_keeps_existing_guard(self) -> None:
        window = Mock()
        window.selected_stock_info.return_value = None

        with (
            patch("gui_stock_performance_window.QMessageBox.warning") as warning,
            patch(
                "gui_stock_performance_window.StockPerformanceWindow"
            ) as dialog_class,
        ):
            open_stock_performance(window)

        warning.assert_called_once()
        dialog_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
