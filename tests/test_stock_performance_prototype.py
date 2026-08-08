# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QApplication, QDateEdit, QFrame, QGroupBox, QLabel, QPushButton, QStyle, QWidget

from gui_auto_trade_display import profit_loss_value_color
from gui_stock_performance_window import (
    DAILY_COLUMN_EXTRA_WIDTHS,
    DAILY_ROWS,
    DAILY_VISIBLE_ROW_TARGET,
    GRAPH_SERIES,
    OVERALL_METRICS,
    PERFORMANCE_FILTER_BODY_SPACING,
    PERFORMANCE_FILTER_TOP_MARGIN,
    PERIOD_METRICS,
    PERIOD_RANGES,
    ROUTINE_ROWS,
    StockPerformancePrototypeWindow,
    open_stock_performance_prototype,
    prototype_cycle_outcome,
)


class StockPerformancePrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_prototype_contains_requested_summary_structure(self) -> None:
        dialog = StockPerformancePrototypeWindow()

        self.assertEqual("종목실적 - 293490 카카오게임즈", dialog.windowTitle())
        self.assertEqual(7, dialog.daily_table.rowCount())
        self.assertEqual(8, dialog.daily_table.columnCount())
        self.assertEqual("횟수", dialog.daily_table.horizontalHeaderItem(1).text())
        self.assertNotIn(
            "평균 매수회차",
            [dialog.daily_table.horizontalHeaderItem(column).text() for column in range(8)],
        )
        self.assertEqual("+8,000원", dialog.daily_table.item(0, 4).text())
        self.assertEqual("+38,000원", dialog.period_value_labels["총 손익금"].text())
        self.assertEqual("손익금", dialog.daily_table.horizontalHeaderItem(4).text())
        self.assertEqual("손익율", dialog.daily_table.horizontalHeaderItem(5).text())
        self.assertEqual("상태", dialog.daily_table.horizontalHeaderItem(7).text())
        self.assertEqual(Qt.ScrollBarAlwaysOn, dialog.daily_table.verticalScrollBarPolicy())
        self.assertEqual(Qt.ScrollBarAlwaysOff, dialog.daily_table.horizontalScrollBarPolicy())
        self.assertEqual("손익금", dialog.routine_table.horizontalHeaderItem(3).text())
        self.assertEqual("손익률", dialog.routine_table.horizontalHeaderItem(4).text())
        self.assertEqual(2, dialog.routine_table.rowCount())
        self.assertEqual(6, dialog.routine_table.columnCount())
        self.assertEqual("+16,000원", dialog.routine_table.item(1, 3).text())
        self.assertFalse(hasattr(dialog, "cycle_table"))
        dialog.close()

    def test_routine_performance_is_full_period_fixed_width_and_sortable(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        dialog.show()
        self.app.processEvents()
        routine_group = dialog.findChild(QGroupBox, "stockPerformanceRoutineGroup")
        self.assertEqual("루틴별 실적", routine_group.title())
        self.assertEqual(
            ["루틴", "횟수", "승 / 패", "손익금", "손익률", "승률"],
            [dialog.routine_table.horizontalHeaderItem(column).text() for column in range(6)],
        )
        self.assertEqual(Qt.ScrollBarAlwaysOn, dialog.routine_table.verticalScrollBarPolicy())
        self.assertEqual(Qt.ScrollBarAlwaysOff, dialog.routine_table.horizontalScrollBarPolicy())
        self.assertEqual(90, dialog.routine_table.columnWidth(5))
        self.assertFalse(dialog.routine_table.horizontalHeader().isSortIndicatorShown())
        self.assertEqual([240, 70, 95, 145, 110, 90], [dialog.routine_table.columnWidth(column) for column in range(6)])
        self.assertEqual(
            dialog.routine_table.width(),
            sum(dialog.routine_table.columnWidth(column) for column in range(6))
            + dialog.routine_table.verticalScrollBar().sizeHint().width()
            + dialog.routine_table.frameWidth() * 2,
        )
        routine_margins = routine_group.layout().contentsMargins()
        self.assertEqual(
            routine_group.width(),
            dialog.routine_table.width() + routine_margins.left() + routine_margins.right(),
        )
        self.assertIsNone(dialog.findChild(QLabel, "stockPerformanceChartNotice"))

        header = dialog.routine_table.horizontalHeader()
        header.sectionClicked.emit(3)
        self.assertEqual("+16,000원", dialog.routine_table.item(0, 3).text())
        header.sectionClicked.emit(3)
        self.assertEqual("+22,000원", dialog.routine_table.item(0, 3).text())
        self.assertFalse(header.isSortIndicatorShown())

        for column in range(dialog.routine_table.columnCount()):
            header.sectionClicked.emit(column)
            self.assertEqual(column, dialog._routine_sort_column)
            self.assertEqual(Qt.AscendingOrder, dialog._routine_sort_order)

        routine_snapshot = [
            tuple(dialog.routine_table.item(row, column).text() for column in range(6))
            for row in range(dialog.routine_table.rowCount())
        ]
        dialog.period_buttons["오늘"].click()
        self.assertEqual(
            routine_snapshot,
            [
                tuple(dialog.routine_table.item(row, column).text() for column in range(6))
                for row in range(dialog.routine_table.rowCount())
            ],
        )
        for _ in range(10):
            dialog.routine_table.insertRow(dialog.routine_table.rowCount())
        self.app.processEvents()
        self.assertGreater(dialog.routine_table.verticalScrollBar().maximum(), 0)
        self.assertEqual(0, dialog.routine_table.horizontalScrollBar().maximum())
        dialog.close()

    def test_daily_routine_and_review_status_cells_are_compact_and_contextual(self) -> None:
        dialog = StockPerformancePrototypeWindow()

        multiple_routines = dialog.daily_table.item(0, 6)
        self.assertEqual("지표추종매매-A 외 1루틴", multiple_routines.text())
        self.assertEqual("지표추종매매-A\n지표추종매매-B", multiple_routines.toolTip())

        single_routine = dialog.daily_table.item(1, 6)
        self.assertEqual("지표추종매매-A", single_routine.text())
        self.assertEqual("", single_routine.toolTip())
        self.assertEqual(Qt.AlignCenter, single_routine.textAlignment())

        normal_status = dialog.daily_table.item(0, 7)
        self.assertEqual("정상", normal_status.text())
        self.assertEqual("", normal_status.toolTip())
        self.assertEqual(Qt.AlignCenter, normal_status.textAlignment())

        abnormal_status = dialog.daily_table.item(4, 7)
        self.assertEqual("이상", abnormal_status.text())
        self.assertEqual(
            "체결/포지션 불일치\n주문 상태 확인 필요",
            abnormal_status.toolTip(),
        )
        self.assertEqual("#d97706", abnormal_status.foreground().color().name())
        self.assertNotEqual("#d97706", normal_status.foreground().color().name())
        self.assertEqual(
            {"정상", "이상"},
            {dialog.daily_table.item(row, 7).text() for row in range(dialog.daily_table.rowCount())},
        )
        self.assertLess(dialog.daily_table.columnWidth(6), 300)
        self.assertLess(dialog.daily_table.columnWidth(7), 90)
        self.assertEqual(20, DAILY_COLUMN_EXTRA_WIDTHS[6])
        self.assertEqual(10, DAILY_COLUMN_EXTRA_WIDTHS[7])
        self.assertEqual(32, DAILY_COLUMN_EXTRA_WIDTHS[0])
        self.assertEqual(32, DAILY_COLUMN_EXTRA_WIDTHS[4])
        count_width = dialog.daily_table.columnWidth(1)
        count_header_minimum = (
            dialog.daily_table.fontMetrics().horizontalAdvance("횟수")
            + dialog.daily_table.style().pixelMetric(QStyle.PM_HeaderMargin) * 2
        )
        self.assertGreaterEqual(count_width, count_header_minimum)
        self.assertLess(count_width, 75)
        self.assertTrue(
            all(dialog.daily_table.columnWidth(column) > 50 for column in (0, 2, 3, 4, 5))
        )
        self.assertEqual(
            dialog.daily_table.width(),
            sum(dialog.daily_table.columnWidth(column) for column in range(8))
            + dialog.daily_table.verticalScrollBar().sizeHint().width()
            + dialog.daily_table.frameWidth() * 2,
        )
        dialog.close()

    def test_daily_headers_sort_by_semantic_values_and_toggle_order(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        header = dialog.daily_table.horizontalHeader()
        self.assertTrue(header.sectionsClickable())

        header.sectionClicked.emit(0)
        self.assertEqual("2026-08-01 (토)", dialog.daily_table.item(0, 0).text())
        header.sectionClicked.emit(0)
        self.assertEqual("2026-08-07 (금)", dialog.daily_table.item(0, 0).text())

        header.sectionClicked.emit(4)
        self.assertEqual("-3,800원", dialog.daily_table.item(0, 4).text())
        header.sectionClicked.emit(4)
        self.assertEqual("+12,500원", dialog.daily_table.item(0, 4).text())

        header.sectionClicked.emit(5)
        self.assertEqual("-0.43%", dialog.daily_table.item(0, 5).text())
        self.assertFalse(header.isSortIndicatorShown())
        dialog.close()

    def test_all_daily_headers_are_sortable_and_eighth_row_uses_vertical_scroll(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        dialog.show()
        self.app.processEvents()
        header = dialog.daily_table.horizontalHeader()

        for column in range(dialog.daily_table.columnCount()):
            header.sectionClicked.emit(column)
            self.assertEqual(column, dialog._daily_sort_column)
            self.assertEqual(Qt.AscendingOrder, dialog._daily_sort_order)

        dialog.daily_table.setSortingEnabled(False)
        dialog.daily_table.insertRow(dialog.daily_table.rowCount())
        self.app.processEvents()
        self.assertGreater(dialog.daily_table.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.daily_table.minimumHeight(), dialog.daily_table.maximumHeight())
        self.assertEqual(dialog.daily_table.minimumWidth(), dialog.daily_table.maximumWidth())
        dialog.close()

    def test_profit_and_loss_use_existing_directional_colors(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        self.assertEqual(
            QColor(profit_loss_value_color(8000)),
            dialog.daily_table.item(0, 4).foreground().color(),
        )
        self.assertEqual(
            QColor(profit_loss_value_color(-3800)),
            dialog.daily_table.item(4, 4).foreground().color(),
        )
        dialog.close()

    def test_win_loss_contract_has_no_draw_and_zero_is_loss(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        self.assertEqual("승", prototype_cycle_outcome(1))
        self.assertEqual("패", prototype_cycle_outcome(0))
        self.assertEqual("패", prototype_cycle_outcome(-1))

        overall = dict((metric[0], metric[1]) for metric in OVERALL_METRICS)
        period = dict((metric[0], metric[1]) for metric in PERIOD_METRICS)
        self.assertNotIn("무", " ".join(overall))
        self.assertEqual("143 / 98", overall["승 / 패"])
        self.assertEqual("59.3%", overall["승률"])
        self.assertEqual("9 / 5", period["승 / 패"])
        self.assertEqual("64.3%", period["승률"])

        for row in DAILY_ROWS:
            wins, losses = [int(value.strip()) for value in row[2].split("/")]
            self.assertEqual(int(row[1]), wins + losses)
        for row in ROUTINE_ROWS:
            wins, losses = [int(value.strip()) for value in row[2].split("/")]
            self.assertEqual(int(row[1]), wins + losses)

        self.assertEqual("승 / 패", dialog.daily_table.horizontalHeaderItem(2).text())
        self.assertEqual("승 / 패", dialog.routine_table.horizontalHeaderItem(2).text())
        dialog.close()

    def test_layout_uses_requested_summary_hierarchy(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        self.assertEqual(
            "전체기간결산",
            dialog.findChild(type(dialog.daily_group), "stockPerformanceOverallGroup").title(),
        )
        self.assertEqual("일자별 실적 요약", dialog.daily_group.title())
        self.assertEqual("조회기간 2026-08-01 ~ 2026-08-07", dialog.daily_title_label.text())
        self.assertEqual(
            dialog.findChild(QGroupBox, "stockPerformanceOverallGroup").styleSheet(),
            dialog.daily_group.styleSheet(),
        )
        self.assertNotIn("일자별 실적 요약", dialog.daily_title_label.text())
        self.assertIsNotNone(dialog.findChild(QWidget, "stockPerformancePeriodTotalPanel"))
        self.assertIsNone(dialog.findChild(QGroupBox, "stockPerformancePeriodTotalGroup"))
        self.assertIsNone(dialog.findChild(QLabel, "stockPerformancePrototypeNotice"))
        dialog.close()

    def test_middle_and_lower_sections_use_the_requested_two_column_layout(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        dialog.show()
        self.app.processEvents()

        routine_group = dialog.findChild(QGroupBox, "stockPerformanceRoutineGroup")
        chart_group = dialog.findChild(QGroupBox, "stockPerformanceChartGroup")
        period_total_panel = dialog.findChild(QWidget, "stockPerformancePeriodTotalPanel")
        period_separator = dialog.findChild(QFrame, "stockPerformancePeriodSeparator")
        self.assertIs(period_total_panel.parentWidget(), dialog.daily_group)
        self.assertEqual(dialog.daily_table.y(), period_total_panel.y())
        self.assertEqual(dialog.daily_table.height(), period_total_panel.height())
        self.assertGreater(period_total_panel.x(), dialog.daily_table.x())
        self.assertEqual(QFrame.VLine, period_separator.frameShape())
        self.assertEqual(
            dialog.daily_table.horizontalHeader().sizeHint().height(),
            period_total_panel.layout().contentsMargins().top(),
        )
        self.assertTrue(
            all(
                period_total_panel.layout().rowMinimumHeight(row)
                == dialog.daily_table.verticalHeader().defaultSectionSize()
                for row in range(7)
            )
        )
        self.assertGreaterEqual(
            dialog.daily_table.height(),
            dialog.daily_table.horizontalHeader().height()
            + DAILY_VISIBLE_ROW_TARGET * dialog.daily_table.verticalHeader().defaultSectionSize(),
        )
        self.assertEqual(routine_group.y(), chart_group.y())
        self.assertEqual(routine_group.height(), chart_group.height())
        self.assertGreater(routine_group.x(), chart_group.x())
        self.assertGreater(routine_group.width(), chart_group.width())
        self.assertEqual(
            ["날짜", "횟수", "승 / 패", "승률", "손익금", "손익율", "적용 루틴(종류)", "상태"],
            [dialog.daily_table.horizontalHeaderItem(column).text() for column in range(8)],
        )
        dialog.close()

    def test_period_summary_rows_align_with_daily_rows_and_titles_are_gently_emphasized(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        dialog.show()
        self.app.processEvents()

        titles = dialog.period_total_panel.findChildren(
            QLabel,
            "stockPerformancePeriodMetricTitle",
        )
        self.assertEqual(14, len(titles))
        self.assertTrue(all(title.font().weight() == QFont.Medium for title in titles))

        for row, metric_index in enumerate(range(0, len(PERIOD_METRICS), 2)):
            daily_rect = dialog.daily_table.visualItemRect(dialog.daily_table.item(row, 0))
            daily_center = dialog.daily_table.viewport().mapTo(
                dialog,
                daily_rect.center(),
            ).y()
            value_label = dialog.period_value_labels[PERIOD_METRICS[metric_index][0]]
            summary_center = value_label.mapTo(dialog, value_label.rect().center()).y()
            self.assertLessEqual(abs(daily_center - summary_center), 1)
        dialog.close()

    def test_overall_summary_uses_light_lines_and_roomy_title_value_spacing(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        cells = dialog.findChildren(QFrame, "stockPerformanceSummaryCell")
        self.assertEqual(14, len(cells))
        cell_titles = [cell.findChildren(QLabel)[0].text() for cell in cells]
        self.assertEqual("전체기간", cell_titles[0])
        self.assertEqual("총 매수금액", cell_titles[1])
        self.assertEqual("평균 사이클 손익금", cell_titles[4])
        self.assertEqual("총 사이클", cell_titles[5])
        self.assertEqual("효율", cell_titles[-1])
        self.assertEqual("1.8", cells[-1].findChildren(QLabel)[1].text())
        self.assertNotIn("총 보유시간", cell_titles)
        self.assertIn("평균 보유시간", cell_titles)
        self.assertTrue(all("#D7DCE2" in cell.styleSheet() for cell in cells))
        self.assertTrue(all("background: #FFFFFF" in cell.styleSheet() for cell in cells))
        self.assertTrue(all(cell.minimumHeight() == 76 for cell in cells))
        self.assertTrue(all(cell.layout().spacing() == 10 for cell in cells))
        overall = dialog.findChild(QGroupBox, "stockPerformanceOverallGroup")
        self.assertIn("font-size: 15px", overall.styleSheet())
        self.assertIn("font-weight: 700", overall.styleSheet())
        self.assertEqual(12, overall.layout().contentsMargins().top())
        dialog.close()

    def test_default_period_is_one_week_with_requested_cumulative_series(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        self.assertEqual("1주", dialog.current_period)
        self.assertTrue(dialog.period_buttons["1주"].isChecked())
        self.assertFalse(hasattr(dialog, "start_date"))
        self.assertFalse(hasattr(dialog, "end_date"))
        self.assertEqual([], dialog.findChildren(QDateEdit))
        self.assertNotIn("조회", [button.text() for button in dialog.findChildren(QPushButton)])
        period_group = dialog.findChild(QWidget, "stockPerformancePeriodGroup")
        self.assertIsNotNone(period_group)
        self.assertIs(dialog.daily_group, period_group.parentWidget())
        self.assertEqual(
            ["오늘", "1주", "1개월", "3개월", "6개월", "전체"],
            [dialog.period_buttons[key].text() for key in ("오늘", "1주", "1개월", "3개월", "6개월", "전체")],
        )
        self.assertNotIn(
            "조회기간",
            [label.text() for label in period_group.findChildren(QLabel)],
        )
        self.assertIs(
            dialog.daily_title_label,
            period_group.layout().itemAt(0).widget(),
        )
        self.assertIsNotNone(period_group.layout().itemAt(1).spacerItem())
        self.assertIs(
            dialog.period_buttons["오늘"],
            period_group.layout().itemAt(2).widget(),
        )
        self.assertEqual(
            PERFORMANCE_FILTER_BODY_SPACING,
            dialog.daily_group.layout().spacing(),
        )
        self.assertEqual(
            PERFORMANCE_FILTER_TOP_MARGIN,
            period_group.layout().contentsMargins().top(),
        )
        self.assertEqual(0, period_group.layout().contentsMargins().right())
        self.assertTrue(
            all(button.width() == 58 for button in dialog.period_buttons.values())
        )
        self.assertEqual("일별", dialog.chart.unit_text)
        self.assertEqual(GRAPH_SERIES["1주"][1], dialog.chart.points)
        self.assertEqual(38000, dialog.chart.points[-1][1])
        dialog.close()

    def test_period_buttons_change_dummy_graph_aggregation_unit(self) -> None:
        dialog = StockPerformancePrototypeWindow()
        expected_units = {
            "오늘": "사이클별",
            "1주": "일별",
            "1개월": "일별",
            "3개월": "주별",
            "6개월": "주별",
            "전체": "월별",
        }
        for mode, expected_unit in expected_units.items():
            dialog.period_buttons[mode].click()
            self.assertEqual(mode, dialog.current_period)
            self.assertEqual(expected_unit, dialog.chart.unit_text)
            self.assertEqual("", dialog.graph_unit_label.text())
            self.assertEqual(GRAPH_SERIES[mode][1], dialog.chart.points)
            start, end = PERIOD_RANGES[mode]
            range_text = f"{start.toString('yyyy-MM-dd')} ~ {end.toString('yyyy-MM-dd')}"
            self.assertEqual(f"조회기간 {range_text}", dialog.daily_title_label.text())
        dialog.close()

    def test_opening_requires_a_selected_stock_and_reads_no_stock_data(self) -> None:
        window = Mock()
        window.selected_stock_info.return_value = (object(), "293490", "카카오게임즈")

        with patch("gui_stock_performance_window.StockPerformancePrototypeWindow") as dialog_class:
            open_stock_performance_prototype(window)

        dialog_class.assert_called_once_with(parent=window)
        dialog_class.return_value.exec_.assert_called_once_with()

    def test_no_selection_keeps_existing_single_selection_guard(self) -> None:
        window = Mock()
        window.selected_stock_info.return_value = None

        with (
            patch("gui_stock_performance_window.QMessageBox.warning") as warning,
            patch("gui_stock_performance_window.StockPerformancePrototypeWindow") as dialog_class,
        ):
            open_stock_performance_prototype(window)

        warning.assert_called_once()
        dialog_class.assert_not_called()

    def test_auto_trade_button_text_is_stock_performance(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        with (
            patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None),
            patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None),
            patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()),
        ):
            window = AutoTradeSettingWindow()

        self.assertEqual("종목실적", window.btn_log_view.text())
        self.assertEqual("autoTradeSettingStockPerformanceButton", window.btn_log_view.objectName())
        self.assertEqual(34, window.btn_log_view.minimumHeight())
        window.close()


if __name__ == "__main__":
    unittest.main()
