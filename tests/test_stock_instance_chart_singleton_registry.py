# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QTableWidget, QTableWidgetItem

import gui_stock_instance_chart_window as chart_window
from gui_auto_trade_context_menu import open_selected_stock_instance_charts
import gui_windows
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_stock_instance_chart_window import StockInstanceChartWindow, open_stock_instance_chart
from gui_window_policy import configure_persistent_feature_window
from gui_main_table_loader import (
    ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_DISPLAY_ROLE,
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_VALUES_ROLE,
)
from gui_windows import MainWindow


def _projection(stock_code: str, trade_date: str) -> dict[str, object]:
    return {
        "stock_code": stock_code,
        "stock_name": stock_code,
        "trade_date": trade_date,
        "instance_id": "instance-1",
        "instance_name": "instance A",
        "bar_minutes": 5,
        "operation_mode_display": "scheduled",
        "operation_time": "09:00~13:30",
        "current_status_display": "running",
        "candles": [],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "actual_order_count": 0,
        "diagnostics": {"issues": []},
    }


class _OperationHost(QObject):
    operation_cycle_completed = pyqtSignal(object)


def _entry_parent(table_name: str, stock_code: str, host: QObject | None = None):
    parent = QDialog()
    table = QTableWidget(1, 3, parent)
    table.setItem(0, 0, QTableWidgetItem(stock_code))
    table.setItem(0, 1, QTableWidgetItem(stock_code))
    table.setItem(0, 2, QTableWidgetItem("scheduled"))
    setattr(parent, table_name, table)
    if host is not None:
        parent._main_monitoring_auto_trade_operation_host = host
    return parent, table


class StockInstanceChartSingletonRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._close_registered_windows()

    def tearDown(self) -> None:
        self._close_registered_windows()

    def _close_registered_windows(self) -> None:
        for window in list(chart_window._OPEN_STOCK_INSTANCE_CHARTS.values()):
            if not sip.isdeleted(window):
                window.close()
        self.app.processEvents()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()

    def test_actual_visible_main_window_code_double_click_opens_chart(self) -> None:
        host = _OperationHost()

        class _Api:
            login_state_changed = None
            raw_chejan_received = None

            @staticmethod
            def unavailable_reason() -> str:
                return "test double"

        with patch.object(gui_windows, "KiwoomApi", return_value=_Api()), patch.object(
            gui_windows, "normalize_base_stock_single_routine_file"
        ), patch.object(
            gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}
        ), patch.object(
            gui_windows.MainWindow, "refresh_all"
        ), patch.object(
            gui_windows.MainWindow,
            "main_monitoring_auto_trade_operation_host",
            return_value=host,
        ), patch.object(
            gui_windows, "append_owner_event_once"
        ), patch.object(
            chart_window, "project_stock_instance_day", side_effect=_projection
        ):
            window = gui_windows.MainWindow()
            window._main_monitoring_auto_trade_operation_host = host
            table = window.routine_table
            table.setRowCount(1)
            item = QTableWidgetItem("")
            values = ["005930 Samsung", "-", "scheduled"]
            item.setData(ROUTINE_ROW_KIND_ROLE, ROUTINE_ROW_STOCK)
            item.setData(ROUTINE_STOCK_CODE_ROLE, "005930")
            item.setData(ROUTINE_STOCK_NAME_ROLE, "Samsung")
            item.setData(ROUTINE_STOCK_VALUES_ROLE, values)
            item.setData(ROUTINE_STOCK_DISPLAY_ROLE, ())
            item.setData(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE, True)
            table.setItem(0, 0, item)
            window.show()
            self.app.processEvents()

            QTest.mouseClick(window.btn_close_all_windows, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertEqual({}, chart_window._OPEN_STOCK_INSTANCE_CHARTS)

            self.assertTrue(table.isVisible())
            self.assertFalse(window.running_stock_table.isVisible())
            index = table.model().index(0, 0)
            code_rect = gui_windows._routine_stock_code_rect(table, index)
            self.assertFalse(code_rect.isNull())

            QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=code_rect.center())
            self.app.processEvents()
            first = chart_window._OPEN_STOCK_INSTANCE_CHARTS.get("005930")
            self.assertIsInstance(first, StockInstanceChartWindow)
            self.assertTrue(first.isVisible())
            self.assertEqual(first.minimumSize(), first.size())
            self.assertTrue(first.windowTitle().startswith("005930 005930 /"))

            QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=code_rect.center())
            self.app.processEvents()
            self.assertIs(first, chart_window._OPEN_STOCK_INSTANCE_CHARTS.get("005930"))
            self.assertEqual(1, len(chart_window._OPEN_STOCK_INSTANCE_CHARTS))

            self.assertEqual("모든창닫기", window.btn_close_all_windows.text())
            self.assertEqual(
                "mainCloseAllWindowsButton",
                window.btn_close_all_windows.objectName(),
            )
            QTest.mouseClick(window.btn_close_all_windows, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertFalse(first.isVisible())
            self.assertEqual({}, chart_window._OPEN_STOCK_INSTANCE_CHARTS)

            window.close()

    def test_settings_chart_uses_main_owner_and_survives_settings_close(self) -> None:
        host = _OperationHost()

        class _Api:
            login_state_changed = None
            raw_chejan_received = None

            @staticmethod
            def unavailable_reason() -> str:
                return "test double"

        with patch.object(gui_windows, "KiwoomApi", return_value=_Api()), patch.object(
            gui_windows, "normalize_base_stock_single_routine_file"
        ), patch.object(
            gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}
        ), patch.object(
            gui_windows.MainWindow, "refresh_all"
        ), patch.object(
            gui_windows.MainWindow,
            "main_monitoring_auto_trade_operation_host",
            return_value=host,
        ), patch.object(
            gui_windows, "append_owner_event_once"
        ), patch.object(
            AutoTradeSettingWindow, "refresh_all"
        ), patch.object(
            AutoTradeSettingWindow, "reset_default_filters_for_open"
        ), patch.object(
            AutoTradeSettingWindow, "update_startup_recovery_controls"
        ), patch.object(
            AutoTradeSettingWindow,
            "current_runtime_file_signature",
            return_value={},
        ), patch.object(
            chart_window, "project_stock_instance_day", side_effect=_projection
        ), patch.object(
            chart_window, "_today_trade_date", return_value="2026-08-11"
        ):
            main = gui_windows.MainWindow()
            main._main_monitoring_auto_trade_operation_host = host
            main.show()
            main.open_auto_trade_setting_window()
            settings = main.auto_trade_setting_window
            self.assertIsInstance(settings, AutoTradeSettingWindow)
            self.assertIsNone(settings.parent())
            self.assertIs(chart_window.persistent_feature_owner(settings), main)
            settings.stock_table.setRowCount(1)
            settings.stock_table.setItem(0, 0, QTableWidgetItem("005930"))
            settings.stock_table.setItem(0, 1, QTableWidgetItem("Samsung"))
            settings.stock_table.setItem(0, 2, QTableWidgetItem("scheduled"))
            settings.show()
            self.app.processEvents()

            code_item = settings.stock_table.item(0, 0)
            code_rect = settings.stock_table.visualItemRect(code_item)
            self.assertTrue(settings.stock_table.isVisible())
            self.assertFalse(code_rect.isNull())
            QTest.mouseClick(
                settings.stock_table.viewport(),
                Qt.LeftButton,
                pos=code_rect.center(),
            )
            QTest.mouseDClick(
                settings.stock_table.viewport(),
                Qt.LeftButton,
                pos=code_rect.center(),
            )
            self.app.processEvents()
            chart = chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"]
            timer = chart_window._common_pnl_refresh_timer()
            self.assertIsNone(chart.parent())
            self.assertIs(chart_window.persistent_feature_owner(chart), main)
            self.assertIs(chart._main_monitoring_window(), main)
            self.assertTrue(chart.isVisible())
            self.assertEqual(chart.minimumSize(), chart.size())
            self.assertTrue(chart.windowTitle().startswith("005930 005930 /"))
            self.assertTrue(timer.isActive())
            self.assertTrue(chart._operation_cycle_refresh_connected)
            with patch.object(
                chart,
                "_operation_stock_context",
                return_value=(Path("stocks/005930_Samsung"), "005930", "Samsung", "instance-1"),
            ):
                adapter = chart._build_stock_operation_adapter()
            self.assertIsNotNone(adapter)
            self.assertIs(adapter._window, main)

            settings.close()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(sip.isdeleted(settings))
            self.assertFalse(sip.isdeleted(chart))
            self.assertTrue(chart.isVisible())
            self.assertTrue(timer.isActive())
            self.assertIs(chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"], chart)

            with patch.object(
                chart_window,
                "project_current_stock_pnl",
                return_value={"available": False},
            ) as pnl_projection:
                chart.refresh_pnl_only()
            pnl_projection.assert_called_once()

            before_cycle = dict(chart.last_projection)
            host.operation_cycle_completed.emit({"processed": True})
            self.app.processEvents()
            self.assertEqual(before_cycle, chart.last_projection)
            chart.refresh_projection()

            table = main.routine_table
            table.setRowCount(1)
            item = QTableWidgetItem("")
            item.setData(ROUTINE_ROW_KIND_ROLE, ROUTINE_ROW_STOCK)
            item.setData(ROUTINE_STOCK_CODE_ROLE, "005930")
            item.setData(ROUTINE_STOCK_NAME_ROLE, "Samsung")
            item.setData(ROUTINE_STOCK_VALUES_ROLE, ["005930 Samsung", "-", "scheduled"])
            item.setData(ROUTINE_STOCK_DISPLAY_ROLE, ())
            item.setData(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE, True)
            table.setItem(0, 0, item)
            self.app.processEvents()
            code_rect = gui_windows._routine_stock_code_rect(
                table, table.model().index(0, 0)
            )
            QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=code_rect.center())
            self.app.processEvents()
            self.assertIs(chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"], chart)

            main.close()
            main.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(sip.isdeleted(chart))
            self.assertFalse(sip.isdeleted(timer))
            self.assertFalse(timer.isActive())
            self.assertNotIn("005930", chart_window._OPEN_STOCK_INSTANCE_CHARTS)

    def test_settings_and_monitoring_each_reuse_the_same_stock_window(self) -> None:
        settings, settings_table = _entry_parent("stock_table", "005930")
        monitoring, monitoring_table = _entry_parent("running_stock_table", "005930")
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                settings, settings_table.item(0, 0)
            )
            first = chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"]
            AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                settings, settings_table.item(0, 0)
            )
            MainWindow.on_running_stock_table_item_double_clicked(
                monitoring, monitoring_table.item(0, 0)
            )
            self.assertIs(first, chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"])
            self.assertEqual(1, len(chart_window._OPEN_STOCK_INSTANCE_CHARTS))

        first.close()
        self.app.processEvents()
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            MainWindow.on_running_stock_table_item_double_clicked(
                monitoring, monitoring_table.item(0, 0)
            )
            second = chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"]
            AutoTradeSettingWindow.on_stock_table_code_item_double_clicked(
                settings, settings_table.item(0, 0)
            )
        self.assertIsNot(first, second)
        self.assertIs(second, chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"])
        settings.close()
        monitoring.close()

    def test_different_stocks_have_independent_windows_and_close_reopens(self) -> None:
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            first = open_stock_instance_chart("005930")
            second = open_stock_instance_chart("035720")
        self.assertIsNot(first, second)
        self.assertEqual({"005930", "035720"}, set(chart_window._OPEN_STOCK_INSTANCE_CHARTS))

        first.close()
        self.app.processEvents()
        self.assertNotIn("005930", chart_window._OPEN_STOCK_INSTANCE_CHARTS)
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            reopened = open_stock_instance_chart("005930")
        self.assertIsNot(first, reopened)
        self.assertIs(reopened, chart_window._OPEN_STOCK_INSTANCE_CHARTS["005930"])

    def test_main_close_all_closes_nine_charts_and_other_feature_windows(self) -> None:
        today = "2026-08-11"
        host = _OperationHost()
        owner = QDialog()
        owner._main_monitoring_auto_trade_operation_host = host
        owner.show()
        features = [QDialog(), QDialog(), QDialog()]
        for feature in features:
            configure_persistent_feature_window(feature, owner)
            feature.show()

        with patch.object(
            chart_window, "project_stock_instance_day", side_effect=_projection
        ), patch.object(chart_window, "_today_trade_date", return_value=today):
            charts = [
                open_stock_instance_chart(f"{index + 1:06d}", today, owner)
                for index in range(9)
            ]
            timer = chart_window._common_pnl_refresh_timer()
            self.app.processEvents()

            self.assertTrue(timer.isActive())
            self.assertEqual(9, host.receivers(host.operation_cycle_completed))
            self.assertEqual(9, len({chart.windowTitle() for chart in charts}))
            self.assertTrue(
                all(
                    chart.windowTitle().startswith(f"{chart.stock_code} {chart.stock_code} /")
                    for chart in charts
                )
            )
            gui_windows.MainWindow.close_all_persistent_feature_windows(owner)
            self.app.processEvents()

            self.assertTrue(owner.isVisible())
            self.assertTrue(all(not chart.isVisible() for chart in charts))
            self.assertTrue(all(not feature.isVisible() for feature in features))
            self.assertTrue(
                all(not chart._operation_cycle_refresh_connected for chart in charts)
            )
            self.assertEqual(0, host.receivers(host.operation_cycle_completed))
            self.assertEqual({}, chart_window._OPEN_STOCK_INSTANCE_CHARTS)
            self.assertFalse(timer.isActive())
            self.assertEqual(0, len(owner._persistent_feature_windows))

            reopened_chart = open_stock_instance_chart("000001", today, owner)
            reopened_feature = QDialog()
            configure_persistent_feature_window(reopened_feature, owner)
            reopened_feature.show()
            self.app.processEvents()
            self.assertTrue(reopened_chart.isVisible())
            self.assertTrue(reopened_feature.isVisible())

            reopened_chart.close()
            reopened_feature.close()
        owner.close()

    def test_batch_opener_creates_three_live_singleton_charts(self) -> None:
        selected = [
            (Path("005930_삼성전자"), "005930", "삼성전자"),
            (Path("012330_현대모비스"), "012330", "현대모비스"),
            (Path("086520_에코프로"), "086520", "에코프로"),
        ]
        owner = QDialog()
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            first_open = open_selected_stock_instance_charts(owner, selected)
            second_open = open_selected_stock_instance_charts(owner, selected)

        self.assertEqual(3, len(first_open))
        self.assertEqual(3, len(second_open))
        self.assertEqual(
            {"005930", "012330", "086520"},
            set(chart_window._OPEN_STOCK_INSTANCE_CHARTS),
        )
        self.assertEqual(first_open, second_open)
        self.assertTrue(
            all(window.size() == window.minimumSize() for window in first_open)
        )
        self.assertEqual(
            1,
            len(
                self.app.findChildren(
                    QTimer,
                    "stockInstanceChartCommonPnlRefreshTimer",
                )
            ),
        )
        owner.close()

    def test_reopening_existing_chart_preserves_user_resized_geometry(self) -> None:
        with patch.object(chart_window, "project_stock_instance_day", side_effect=_projection):
            first = open_stock_instance_chart("005930")
            first.resize(first.minimumWidth() + 160, first.minimumHeight() + 90)
            first.move(first.x() + 37, first.y() + 29)
            resized = first.size()
            moved = first.pos()
            second = open_stock_instance_chart("005930")

        self.assertIs(first, second)
        self.assertEqual(resized, second.size())
        self.assertEqual(moved, second.pos())
        self.assertNotEqual(second.minimumSize(), second.size())

    def test_thirty_charts_share_one_pnl_timer_and_one_tick_each(self) -> None:
        today = "2026-08-11"
        with patch.object(
            chart_window, "project_stock_instance_day", side_effect=_projection
        ), patch.object(
            chart_window, "_today_trade_date", return_value=today
        ):
            windows: list[StockInstanceChartWindow] = []
            timer = None
            for index in range(30):
                window = open_stock_instance_chart(f"{index + 1:06d}", today)
                windows.append(window)
                if index in {0, 9, 29}:
                    current_timer = chart_window._common_pnl_refresh_timer()
                    self.assertIsNotNone(current_timer)
                    self.assertTrue(current_timer.isActive())
                    timer = timer or current_timer
                    self.assertIs(timer, current_timer)
                    self.assertEqual(
                        1,
                        len(
                            self.app.findChildren(
                                QTimer,
                                "stockInstanceChartCommonPnlRefreshTimer",
                            )
                        ),
                    )
            self.assertTrue(all(not hasattr(window, "_pnl_refresh_timer") for window in windows))

            historical = open_stock_instance_chart("999999", "2000-01-01")
            batch_results = {
                window.stock_code: {"available": False}
                for window in windows
            }
            batch_results[windows[0].stock_code] = {
                "available": True,
                "cumulative_profit": 12_500,
                "cumulative_rate": 1.25,
            }
            batch_results[windows[1].stock_code] = {
                "available": True,
                "cumulative_profit": -8_300,
                "cumulative_rate": -0.83,
            }
            with patch.object(
                chart_window,
                "project_current_stock_pnl_snapshot",
                return_value=batch_results,
            ) as pnl_snapshot:
                chart_window._refresh_live_chart_pnl()
            pnl_snapshot.assert_called_once()
            self.assertEqual(
                "+12,500(+1.25%)",
                windows[0].info_labels["cumulative_pnl"].text(),
            )
            self.assertEqual(
                "-8,300(-0.83%)",
                windows[1].info_labels["cumulative_pnl"].text(),
            )
            self.assertEqual(
                "0(0.00%)",
                windows[2].info_labels["cumulative_pnl"].text(),
            )
            self.assertEqual(
                {window.stock_code for window in windows},
                set(pnl_snapshot.call_args.args[0]),
            )
            refreshes = {}
            for window in [*windows, historical]:
                refresh = Mock()
                window.apply_pnl_result = refresh
                refreshes[window.stock_code] = refresh
            with patch.object(
                chart_window,
                "project_current_stock_pnl_snapshot",
                return_value=batch_results,
            ):
                chart_window._refresh_live_chart_pnl()
            for window in windows:
                refreshes[window.stock_code].assert_called_once_with(
                    batch_results[window.stock_code]
                )
            refreshes[historical.stock_code].assert_not_called()

            refreshes[windows[0].stock_code].side_effect = (
                lambda _result: windows[1].close()
            )
            for refresh in refreshes.values():
                refresh.reset_mock()
            with patch.object(
                chart_window,
                "project_current_stock_pnl_snapshot",
                return_value=batch_results,
            ):
                chart_window._refresh_live_chart_pnl()
            refreshes[windows[0].stock_code].assert_called_once_with(
                batch_results[windows[0].stock_code]
            )
            refreshes[windows[1].stock_code].assert_not_called()

            for window in windows[:-1]:
                window.close()
            self.app.processEvents()
            last = windows[-1]
            refreshes[last.stock_code].reset_mock()
            with patch.object(
                chart_window,
                "project_current_stock_pnl_snapshot",
                return_value=batch_results,
            ):
                chart_window._refresh_live_chart_pnl()
            refreshes[last.stock_code].assert_called_once_with(
                batch_results[last.stock_code]
            )
            for window in windows[:-1]:
                self.assertLessEqual(refreshes[window.stock_code].call_count, 1)
            self.assertTrue(timer.isActive())

            last.close()
            self.app.processEvents()
            self.assertFalse(timer.isActive())
            reopened = open_stock_instance_chart("123456", today)
            self.assertIs(timer, chart_window._common_pnl_refresh_timer())
            self.assertTrue(timer.isActive())
            reopened.close()
            historical.close()

    def test_duplicate_open_keeps_one_timer_and_one_operation_cycle_connection(self) -> None:
        host = _OperationHost()
        owner = QDialog()
        owner._main_monitoring_auto_trade_operation_host = host
        calls: list[tuple[str, str]] = []

        def provider(stock_code: str, trade_date: str) -> dict[str, object]:
            calls.append((stock_code, trade_date))
            projected = _projection(stock_code, trade_date)
            projected["stock_name"] = f"종목명-{len(calls)}"
            return projected

        with patch.object(
            chart_window, "project_stock_instance_day", side_effect=provider
        ), patch.object(
            chart_window, "_today_trade_date", return_value="2026-08-11"
        ):
            first = open_stock_instance_chart("005930", "2026-08-11", owner)
            timer = chart_window._common_pnl_refresh_timer()
            with patch.object(first, "show", wraps=first.show) as show, patch.object(
                first, "raise_", wraps=first.raise_
            ) as raise_window, patch.object(
                first, "activateWindow", wraps=first.activateWindow
            ) as activate_window, patch.object(
                first, "refresh_projection", wraps=first.refresh_projection
            ) as refresh_projection:
                second = open_stock_instance_chart("005930", "2026-08-11", owner)
                show.assert_called_once_with()
                raise_window.assert_called_once_with()
                activate_window.assert_called_once_with()
                refresh_projection.assert_called_once_with()
            self.assertIs(first, second)
            self.assertTrue(first.windowTitle().startswith("005930 종목명-2 /"))
            self.assertIs(timer, chart_window._common_pnl_refresh_timer())
            self.assertFalse(hasattr(first, "_pnl_refresh_timer"))
            self.assertTrue(timer.isActive())
            before_cycle = len(calls)
            host.operation_cycle_completed.emit({"processed": True})
            self.app.processEvents()
            self.assertEqual(before_cycle + 1, len(calls))
            self.assertTrue(first._operation_cycle_refresh_connected)
        owner.close()


if __name__ == "__main__":
    unittest.main()
