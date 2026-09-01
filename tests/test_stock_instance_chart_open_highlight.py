# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

import gui_auto_trade_table_loader as settings_loader
import gui_stock_instance_chart_window as chart_window
from tests.qt_test_support import flush_deferred_deletes
import gui_windows
from gui_main_table_loader import (
    ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_DISPLAY_ROLE,
    ROUTINE_STOCK_METRICS_ROLE,
    ROUTINE_STOCK_PROFIT_LED_ROLE,
    ROUTINE_STOCK_VALUES_ROLE,
)


def _projection(stock_code: str, trade_date: str) -> dict[str, object]:
    return {
        "stock_code": stock_code,
        "stock_name": stock_code,
        "trade_date": trade_date,
        "candles": [],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "diagnostics": {},
        "pnl_available": False,
    }


class _SettingsWindow(QDialog):
    def __init__(self, codes: list[str]) -> None:
        super().__init__()
        self.setObjectName("autoTradeSettingWindow")
        self.stock_table = QTableWidget(len(codes), 1)
        for row, code in enumerate(codes):
            item = QTableWidgetItem(code)
            item.setForeground(QColor("#374151"))
            font = QFont()
            font.setItalic(True)
            item.setFont(font)
            self.stock_table.setItem(row, 0, item)

    def refresh_stock_instance_chart_open_code_styles(self) -> None:
        settings_loader.refresh_auto_trade_chart_open_code_styles(self)


class _MainOwner(QDialog):
    def __init__(self, settings: _SettingsWindow | None = None) -> None:
        super().__init__()
        self.routine_table = QTableWidget(1, 1)
        self.auto_trade_setting_window = settings

    def main_monitoring_auto_trade_operation_host(self):
        return None

    def statusBar(self):
        return None


class StockInstanceChartOpenHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        for window in list(chart_window._OPEN_STOCK_INSTANCE_CHARTS.values()):
            try:
                if not sip.isdeleted(window):
                    window.close()
            except RuntimeError:
                pass
        self.app.processEvents()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()

    def test_open_and_close_updates_settings_and_main_code_style(self) -> None:
        settings = _SettingsWindow(["005930", "035720"])
        owner = _MainOwner(settings)
        settings.show()
        owner.show()
        code_item = settings.stock_table.item(0, 0)
        baseline_color = code_item.foreground().color().name().lower()
        baseline_font = QFont(code_item.font())

        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=_projection,
        ):
            chart = chart_window.open_stock_instance_chart("005930", parent=owner)
        self.app.processEvents()

        self.assertEqual("#2563eb", code_item.foreground().color().name().lower())
        self.assertEqual(baseline_font.weight(), code_item.font().weight())
        self.assertEqual(baseline_font.italic(), code_item.font().italic())
        self.assertEqual(baseline_color, settings.stock_table.item(1, 0).foreground().color().name().lower())
        base_main_font = QFont()
        main_font, main_color = gui_windows._routine_stock_code_chart_style(
            base_main_font,
            "005930",
        )
        self.assertEqual(base_main_font.weight(), main_font.weight())
        self.assertEqual("#2563eb", main_color.name().lower())

        chart.close()
        self.app.processEvents()
        flush_deferred_deletes(self.app)

        self.assertEqual(baseline_color, code_item.foreground().color().name().lower())
        self.assertEqual(baseline_font.bold(), code_item.font().bold())
        self.assertEqual(baseline_font.italic(), code_item.font().italic())
        closed_font, closed_color = gui_windows._routine_stock_code_chart_style(
            QFont(),
            "005930",
        )
        self.assertFalse(closed_font.bold())
        self.assertIsNone(closed_color)
        settings.close()
        owner.close()

    def test_main_custom_delegate_paints_blue_code_even_when_selected(self) -> None:
        table = QTableWidget(1, 1)
        table.resize(760, 48)
        table.setColumnWidth(0, 740)
        table.setRowHeight(0, 32)
        item = QTableWidgetItem("005930 삼성전자")
        item.setData(ROUTINE_ROW_KIND_ROLE, ROUTINE_ROW_STOCK)
        item.setData(ROUTINE_STOCK_CODE_ROLE, "005930")
        item.setData(ROUTINE_STOCK_VALUES_ROLE, ["005930 삼성전자"])
        item.setData(
            ROUTINE_STOCK_DISPLAY_ROLE,
            [{"text": "005930 삼성전자", "foreground": "#111827"}],
        )
        item.setData(ROUTINE_STOCK_METRICS_ROLE, ())
        item.setData(ROUTINE_STOCK_PROFIT_LED_ROLE, "gray")
        item.setData(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE, True)
        table.setItem(0, 0, item)
        delegate = gui_windows._RoutineTreeItemDelegate(table)
        table.setItemDelegateForColumn(0, delegate)
        chart = QDialog()
        chart.show()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"] = chart
        table.show()
        self.app.processEvents()

        class RecordingPainter(QPainter):
            def __init__(self, device) -> None:
                super().__init__(device)
                self.pen_colors: list[str] = []
                self.drawn_text: list[tuple[str, str, int]] = []

            def setPen(self, pen) -> None:  # type: ignore[override]
                color = pen if isinstance(pen, QColor) else (
                    pen.color() if callable(getattr(pen, "color", None)) else None
                )
                if color is not None:
                    self.pen_colors.append(color.name().lower())
                super().setPen(pen)

            def drawText(self, *args) -> None:  # type: ignore[override]
                text = str(args[-1]) if args else ""
                self.drawn_text.append(
                    (text, self.pen().color().name().lower(), self.font().weight())
                )
                super().drawText(*args)

        for selected in (False, True):
            pixmap = QPixmap(760, 32)
            option = QStyleOptionViewItem()
            option.rect = pixmap.rect()
            option.widget = table
            option.font = table.font()
            option.palette = table.palette()
            option.state = QStyle.State_Enabled
            if selected:
                option.state |= QStyle.State_Selected
            painter = RecordingPainter(pixmap)
            try:
                delegate.paint(painter, option, table.model().index(0, 0))
            finally:
                painter.end()
            self.assertIn("#2563eb", painter.pen_colors)
            self.assertIn(
                ("005930", "#2563eb", table.font().weight()),
                painter.drawn_text,
            )
            name_draws = [entry for entry in painter.drawn_text if "삼성전자" in entry[0]]
            self.assertEqual(1, len(name_draws))
            self.assertNotEqual("#2563eb", name_draws[0][1])
            self.assertEqual(table.font().weight(), name_draws[0][2])
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.pop("005930", None)
        chart.close()
        table.close()

    def test_batch_highlights_only_live_codes_and_single_close_preserves_others(self) -> None:
        codes = ["005930", "035720", "068270", "086520", "247540", "028260"]
        settings = _SettingsWindow(codes)
        owner = _MainOwner(settings)
        settings.show()
        owner.show()
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=_projection,
        ):
            charts = [
                chart_window.open_stock_instance_chart(code, parent=owner)
                for code in codes[:5]
            ]
        self.app.processEvents()

        for row in range(5):
            self.assertEqual(
                "#2563eb",
                settings.stock_table.item(row, 0).foreground().color().name().lower(),
            )
        self.assertEqual(
            "#374151",
            settings.stock_table.item(5, 0).foreground().color().name().lower(),
        )

        charts[2].close()
        self.app.processEvents()
        self.assertEqual(
            "#374151",
            settings.stock_table.item(2, 0).foreground().color().name().lower(),
        )
        for row in (0, 1, 3, 4):
            self.assertEqual(
                "#2563eb",
                settings.stock_table.item(row, 0).foreground().color().name().lower(),
            )
        settings.close()
        owner.close()

    def test_settings_reopen_projects_existing_live_registry(self) -> None:
        owner = _MainOwner()
        owner.show()
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=_projection,
        ):
            chart = chart_window.open_stock_instance_chart("005930", parent=owner)

        reopened = _SettingsWindow(["005930"])
        reopened.show()
        settings_loader.refresh_auto_trade_chart_open_code_styles(reopened)
        self.assertEqual(
            "#2563eb",
            reopened.stock_table.item(0, 0).foreground().color().name().lower(),
        )
        self.assertFalse(reopened.stock_table.item(0, 0).font().bold())
        self.assertTrue(reopened.stock_table.item(0, 0).font().italic())
        self.assertIs(
            chart,
            chart_window.open_stock_instance_chart("005930", parent=owner),
        )
        self.assertEqual(
            "#2563eb",
            reopened.stock_table.item(0, 0).foreground().color().name().lower(),
        )
        reopened.close()
        owner.close()

    def test_close_all_clears_all_highlights_and_registry(self) -> None:
        codes = ["005930", "035720"]
        settings = _SettingsWindow(codes)
        owner = _MainOwner(settings)
        settings.show()
        owner.show()
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=_projection,
        ):
            for code in codes:
                chart_window.open_stock_instance_chart(code, parent=owner)
        self.app.processEvents()
        self.assertTrue(
            all(
                not settings.stock_table.item(row, 0).font().bold()
                for row in range(len(codes))
            )
        )

        gui_windows.MainWindow.close_all_persistent_feature_windows(owner)
        self.app.processEvents()

        self.assertEqual({}, chart_window._OPEN_STOCK_INSTANCE_CHARTS)
        for code in codes:
            font, color = gui_windows._routine_stock_code_chart_style(QFont(), code)
            self.assertFalse(font.bold())
            self.assertIsNone(color)
        self.assertTrue(
            all(
                settings.stock_table.item(row, 0).foreground().color().name().lower()
                == "#374151"
                for row in range(len(codes))
            )
        )
        settings.close()
        owner.close()

    def test_deleted_stale_registry_entry_is_not_highlighted(self) -> None:
        owner = _MainOwner()
        owner.show()
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=_projection,
        ):
            chart = chart_window.open_stock_instance_chart("005930", parent=owner)
        chart.close()
        self.app.processEvents()
        flush_deferred_deletes(self.app)
        self.assertTrue(sip.isdeleted(chart))
        chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"] = chart

        self.assertFalse(chart_window.stock_instance_chart_is_open("005930"))
        self.assertNotIn("005930", chart_window._OPEN_STOCK_INSTANCE_CHARTS)
        owner.close()


if __name__ == "__main__":
    unittest.main()
