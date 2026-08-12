# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QTableWidget, QTableWidgetItem

import gui_auto_trade_setting_window as setting_window_module
import gui_stock_instance_chart_window as chart_window_module
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_stock_instance_chart_window import StockInstanceChartWindow


class _CycleHost(QObject):
    operation_cycle_completed = pyqtSignal(dict)


class _Owner(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self._main_monitoring_auto_trade_operation_host = _CycleHost(self)
        self.settings_window = None
        self.refresh_count = 0

    def refresh_auto_trade_assignment_views(self) -> None:
        self.refresh_count += 1
        self.settings_window.refresh_all()


def _projection(trade_date: str) -> dict[str, object]:
    return {
        "stock_code": "005930",
        "stock_name": "Samsung",
        "trade_date": trade_date,
        "instance_id": "instance-1",
        "instance_name": "Instance A",
        "routine_name": "Routine A",
        "bar_minutes": 5,
        "operation_mode_display": "Scheduled",
        "operation_time": "09:00~13:30",
        "current_status_display": "Running",
        "candles": [],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "actual_order_count": 0,
        "diagnostics": {"issues": []},
    }


class OperationExclusionDoubleClickLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_open_chart_survives_name_double_click_and_refresh_is_deferred(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Samsung"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "operation_excluded": False,
                        "assigned_routine_instance_id": "instance-1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            owner = _Owner()
            setting = QDialog(owner)
            owner.settings_window = setting
            table = QTableWidget(1, 3, setting)
            owner.routine_table = table
            setting.stock_table = table
            setting.statusBarMessage = Mock()
            setting.running_registered_operation_targets = lambda: []
            setting.stock_info_from_row = lambda _row: (
                stock_dir,
                "005930",
                "Samsung",
            )
            setting.toggle_stock_operation_exclusion = MethodType(
                AutoTradeSettingWindow.toggle_stock_operation_exclusion,
                setting,
            )

            def populate_table() -> None:
                table.setRowCount(1)
                code_item = QTableWidgetItem("005930")
                code_item.setData(Qt.UserRole, str(stock_dir))
                table.setItem(0, 0, code_item)
                table.setItem(0, 1, QTableWidgetItem("Samsung"))
                table.setItem(0, 2, QTableWidgetItem("Scheduled"))

            def refresh_all() -> None:
                table.clearContents()
                table.setRowCount(0)
                populate_table()

            setting.refresh_all = refresh_all
            populate_table()

            observed_columns: list[int] = []
            table.itemDoubleClicked.connect(
                lambda item: AutoTradeSettingWindow.on_stock_table_item_double_clicked(
                    setting,
                    item,
                )
            )
            table.itemDoubleClicked.connect(
                lambda item: AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                    setting,
                    item,
                )
            )
            table.itemDoubleClicked.connect(
                lambda item: AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                    setting,
                    item,
                )
            )
            table.itemDoubleClicked.connect(
                lambda item: observed_columns.append(item.column())
            )

            trade_date = chart_window_module._today_trade_date()
            projection_provider = Mock(return_value=_projection(trade_date))
            chart = StockInstanceChartWindow(
                "005930",
                trade_date=trade_date,
                parent=setting,
                projection_provider=projection_provider,
            )
            chart._main_monitoring_window = lambda: owner
            chart._operation_stock_context = lambda: (
                stock_dir,
                "005930",
                "Samsung",
                "instance-1",
            )
            chart._update_operation_button_state()
            chart_window_module._OPEN_STOCK_INSTANCE_CHARTS["005930"] = chart
            chart_window_module._update_common_pnl_refresh_timer()
            chart.show()
            self.app.processEvents()

            self.assertFalse(hasattr(chart, "_pnl_refresh_timer"))
            self.assertTrue(
                chart_window_module._common_pnl_refresh_timer().isActive()
            )
            self.assertTrue(chart._operation_cycle_refresh_connected)
            self.assertTrue(chart.early_close_button.isEnabled())

            with (
                patch("gui_auto_trade_status_ops.append_stock_log"),
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.append_production_event"),
                patch("gui_auto_trade_status_ops.show_toast"),
            ):
                name_item = table.item(0, 1)
                table.itemDoubleClicked.emit(name_item)

                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertTrue(saved["operation_excluded"])
                self.assertEqual([1], observed_columns)
                self.assertEqual(1, table.rowCount())
                self.assertEqual(0, owner.refresh_count)

                self.app.processEvents()
                self.assertEqual(1, owner.refresh_count)
                self.assertEqual(1, table.rowCount())
                self.assertFalse(sip.isdeleted(chart))
                self.assertTrue(chart.isVisible())
                self.assertTrue(
                    chart_window_module._common_pnl_refresh_timer().isActive()
                )
                self.assertTrue(chart._operation_cycle_refresh_connected)

                owner._main_monitoring_auto_trade_operation_host.operation_cycle_completed.emit(
                    {"processed": True}
                )
                self.app.processEvents()
                self.assertFalse(chart.early_close_button.isEnabled())

                table.itemDoubleClicked.emit(table.item(0, 1))
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertFalse(saved["operation_excluded"])
                self.app.processEvents()
                owner._main_monitoring_auto_trade_operation_host.operation_cycle_completed.emit(
                    {"processed": True}
                )
                self.app.processEvents()
                self.assertEqual(2, owner.refresh_count)
                self.assertTrue(chart.early_close_button.isEnabled())
                self.assertTrue(
                    chart_window_module._common_pnl_refresh_timer().isActive()
                )
                self.assertTrue(chart._operation_cycle_refresh_connected)

            chart.close()
            setting.close()
            owner.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
