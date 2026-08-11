# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QTableWidget, QTableWidgetItem

import gui_auto_trade_setting_window as auto_trade_window
import gui_stock_instance_chart_window as chart_window
import gui_windows
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_stock_instance_chart_window import StockInstanceChartWindow
from gui_windows import MainWindow


def _table_parent(attribute_name: str) -> tuple[QDialog, QTableWidget]:
    parent = QDialog()
    table = QTableWidget(1, 3, parent)
    table.setItem(0, 0, QTableWidgetItem("005930"))
    table.setItem(0, 1, QTableWidgetItem("삼성전자"))
    table.setItem(0, 2, QTableWidgetItem("시간"))
    setattr(parent, attribute_name, table)
    return parent, table


def _projection() -> dict[str, object]:
    return {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "trade_date": "2026-08-10",
        "instance_id": "instance-1",
        "instance_name": "지표추종-A",
        "bar_minutes": 5,
        "operation_mode_display": "시간",
        "operation_time": "09:00~13:30",
        "current_status_display": "감시/대기",
        "candles": [],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "actual_order_count": 0,
        "diagnostics": {
            "raw_candle_count": 0,
            "completed_candle_count": 0,
            "issues": [],
        },
    }


class StockInstanceChartEntryConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_monitoring_code_column_opens_common_chart_and_other_columns_do_not(self) -> None:
        parent, table = _table_parent("running_stock_table")
        with patch.object(gui_windows, "open_stock_instance_chart") as opener:
            MainWindow.on_running_stock_table_item_double_clicked(
                parent,
                table.item(0, 0),
            )
            opener.assert_called_once_with(
                "005930",
                trade_date=None,
                parent=parent,
            )
            MainWindow.on_running_stock_table_item_double_clicked(
                parent,
                table.item(0, 1),
            )
            self.assertEqual(1, opener.call_count)

            table.item(0, 0).setText("")
            MainWindow.on_running_stock_table_item_double_clicked(
                parent,
                table.item(0, 0),
            )
            self.assertEqual(1, opener.call_count)
        parent.close()

    def test_setting_code_column_opens_common_chart_only(self) -> None:
        parent, table = _table_parent("stock_table")
        with patch.object(auto_trade_window, "open_stock_instance_chart") as opener:
            AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                parent,
                table.item(0, 0),
            )
            opener.assert_called_once_with(
                "005930",
                trade_date=None,
                parent=parent,
            )
            for column in (1, 2):
                AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                    parent,
                    table.item(0, column),
                )
            self.assertEqual(1, opener.call_count)
        parent.close()

    def test_setting_name_and_operation_column_contracts_are_unchanged(self) -> None:
        parent, table = _table_parent("stock_table")
        target = (Path("stocks/005930_삼성전자"), "005930", "삼성전자")
        parent.stock_info_from_row = Mock(return_value=target)
        parent.operation_stock_dir_from_row = Mock(return_value=target[0])
        parent._stock_operation_mode_double_click_pending = False

        with patch.object(
            auto_trade_window,
            "handle_stock_name_operation_exclusion_double_click",
        ) as name_handler, patch.object(
            auto_trade_window,
            "handle_auto_trade_operation_mode_double_click",
        ) as operation_handler, patch.object(
            auto_trade_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                parent,
                table.item(0, 1),
            )
            name_handler.assert_called_once_with(parent, target)
            operation_handler.assert_not_called()

            AutoTradeSettingWindow.on_stock_table_item_double_clicked(
                parent,
                table.item(0, 2),
            )
            operation_handler.assert_called_once_with(parent, target)
            self.assertFalse(parent._stock_operation_mode_double_click_pending)

            AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                parent,
                table.item(0, 0),
            )
            AutoTradeSettingWindow.on_stock_table_item_double_clicked(
                parent,
                table.item(0, 0),
            )
            self.assertEqual(1, name_handler.call_count)
            self.assertEqual(1, operation_handler.call_count)
        parent.close()

    def test_parent_ownership_keeps_repeated_windows_and_both_views_share_projection(self) -> None:
        monitoring, monitoring_table = _table_parent("running_stock_table")
        setting, setting_table = _table_parent("stock_table")
        projected = _projection()
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=projected,
        ) as loader:
            MainWindow.on_running_stock_table_item_double_clicked(
                monitoring,
                monitoring_table.item(0, 0),
            )
            MainWindow.on_running_stock_table_item_double_clicked(
                monitoring,
                monitoring_table.item(0, 0),
            )
            AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                setting,
                setting_table.item(0, 0),
            )
            self.app.processEvents()

        monitoring_windows = monitoring.findChildren(StockInstanceChartWindow)
        setting_windows = setting.findChildren(StockInstanceChartWindow)
        self.assertEqual(2, len(monitoring_windows))
        self.assertEqual(1, len(setting_windows))
        self.assertTrue(all(window.isVisible() for window in monitoring_windows))
        self.assertTrue(setting_windows[0].isVisible())
        self.assertEqual(projected, monitoring_windows[0].last_projection)
        self.assertEqual(
            monitoring_windows[0].last_projection,
            setting_windows[0].last_projection,
        )
        self.assertEqual(3, loader.call_count)
        self.assertTrue(
            all(call.args == ("005930", "2026-08-10") for call in loader.call_args_list)
        )
        monitoring.close()
        setting.close()


if __name__ == "__main__":
    unittest.main()
