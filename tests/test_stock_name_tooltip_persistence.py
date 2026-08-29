# -*- coding: utf-8 -*-

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QHelpEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

import gui_auto_trade_setting_window as setting_window
import gui_main_table_loader as main_table_loader
import gui_stock_name_tooltip as stock_tooltip
from gui_windows import MainWindow


class StockNameToolTipPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self) -> None:
        setting_window._INSTANCE_STOCK_SEARCH_DIALOGS.clear()

    def test_filter_uses_long_duration_and_updates_or_hides_by_cell(self) -> None:
        table = QTableWidget(3, 2)
        table.resize(320, 180)
        table.setColumnWidth(1, 120)
        names = ("FIRST-LONG-STOCK-NAME", "SECOND-LONG-STOCK-NAME", "SHORT")
        for row, name in enumerate(names):
            item = QTableWidgetItem(name)
            item.setToolTip(name if row < 2 else "")
            table.setItem(row, 1, item)
        tooltip_filter = stock_tooltip.install_persistent_stock_name_tooltips(
            table,
            {1},
        )
        table.show()
        self.app.processEvents()

        viewport = table.viewport()
        first_index = table.model().index(0, 1)
        first_pos = table.visualRect(first_index).center()
        with (
            patch.object(stock_tooltip.QToolTip, "showText") as show_text,
            patch.object(stock_tooltip.QToolTip, "hideText") as hide_text,
        ):
            QApplication.sendEvent(
                viewport,
                QHelpEvent(
                    QEvent.ToolTip,
                    first_pos,
                    viewport.mapToGlobal(first_pos),
                ),
            )
            self.assertEqual(1, show_text.call_count)
            self.assertEqual(
                stock_tooltip._PERSISTENT_TOOLTIP_MSEC,
                show_text.call_args.args[4],
            )

            second_index = table.model().index(1, 1)
            second_pos = table.visualRect(second_index).center()
            second_global = viewport.mapToGlobal(second_pos)
            QApplication.sendEvent(
                viewport,
                QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(second_pos),
                    QPointF(second_global),
                    Qt.NoButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                ),
            )
            self.assertEqual("SECOND-LONG-STOCK-NAME", show_text.call_args.args[1])

            short_index = table.model().index(2, 1)
            short_pos = table.visualRect(short_index).center()
            short_global = viewport.mapToGlobal(short_pos)
            QApplication.sendEvent(
                viewport,
                QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(short_pos),
                    QPointF(short_global),
                    Qt.NoButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                ),
            )
            hide_text.assert_called_once_with()
            self.assertFalse(tooltip_filter._active_index.isValid())

        self.assertIn("font-size: 12pt", table.styleSheet())
        table.close()

    def test_filter_limits_projected_stock_tooltip_to_code_and_name_columns(self) -> None:
        table = QTableWidget(2, 3)
        table.resize(360, 160)
        for row, text in enumerate(("FIRST ROW", "SECOND ROW")):
            for column in range(3):
                table.setItem(row, column, QTableWidgetItem(f"{row}-{column}"))
            table.item(row, 1).setToolTip(text)
        tooltip_filter = stock_tooltip.install_persistent_stock_name_tooltips(
            table,
            {0, 1},
            source_column=1,
            tooltip_point_size=10.8,
        )
        table.show()
        self.app.processEvents()

        viewport = table.viewport()
        first_index = table.model().index(0, 0)
        first_pos = table.visualRect(first_index).center()
        with (
            patch.object(stock_tooltip.QToolTip, "showText") as show_text,
            patch.object(stock_tooltip.QToolTip, "hideText") as hide_text,
            patch.object(stock_tooltip.QToolTip, "isVisible", return_value=True),
        ):
            QApplication.sendEvent(
                viewport,
                QHelpEvent(QEvent.ToolTip, first_pos, viewport.mapToGlobal(first_pos)),
            )
            self.assertEqual("FIRST ROW", show_text.call_args.args[1])

            same_row_pos = table.visualRect(table.model().index(0, 1)).center()
            QApplication.sendEvent(
                viewport,
                QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(same_row_pos),
                    QPointF(viewport.mapToGlobal(same_row_pos)),
                    Qt.NoButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                ),
            )
            self.assertEqual(1, show_text.call_count)

            status_pos = table.visualRect(table.model().index(0, 2)).center()
            QApplication.sendEvent(
                viewport,
                QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(status_pos),
                    QPointF(viewport.mapToGlobal(status_pos)),
                    Qt.NoButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                ),
            )
            hide_text.assert_called_once_with()

            second_row_pos = table.visualRect(table.model().index(1, 0)).center()
            QApplication.sendEvent(
                viewport,
                QHelpEvent(
                    QEvent.ToolTip,
                    second_row_pos,
                    viewport.mapToGlobal(second_row_pos),
                ),
            )
            self.assertEqual("SECOND ROW", show_text.call_args.args[1])
            QApplication.sendEvent(viewport, QEvent(QEvent.Leave))
            self.assertEqual(2, hide_text.call_count)
            self.assertFalse(tooltip_filter._active_index.isValid())
        self.assertIn("font-size: 10.8pt", table.styleSheet())
        table.close()

    def test_main_stock_tooltip_uses_library_market_and_canonical_current_price(self) -> None:
        snapshot = SimpleNamespace(
            records=(
                {
                    "code": "005930",
                    "market": "KOSPI",
                    "nxt_available": True,
                    "status": "정상 | 증거금40% | 신용가능 | 담보대출",
                },
                {
                    "code": "035720",
                    "market": "KOSDAQ",
                    "nxt_available": False,
                    "status": "",
                },
            )
        )
        with patch.object(
            main_table_loader,
            "load_stock_library_snapshot",
            return_value=snapshot,
        ):
            metadata = main_table_loader._stock_library_tooltip_metadata_by_code()

        self.assertEqual(
            {
                "005930": {
                    "market": "KOSPI",
                    "nxt_available": True,
                    "status": "정상 | 증거금40% | 신용가능 | 담보대출",
                },
                "035720": {
                    "market": "KOSDAQ",
                    "nxt_available": False,
                    "status": "",
                },
            },
            metadata,
        )
        self.assertEqual(
            "▪  005930 삼성전자  |  KOSPI  |  상태 정상  |  NXT\n"
            "▪  현재가 70,000  |  시가 69,000  |  고가 71,000  |  저가 68,500\n"
            "▪  등락률 +1.34%  |  전일대비 -12.43%  |  체결강도 117.2",
            main_table_loader._main_stock_row_tooltip(
                metadata["005930"]["market"],
                "005930",
                "삼성전자",
                70000,
                nxt_available=metadata["005930"]["nxt_available"],
                stock_state={
                    "status": "RUNNING",
                    "open_price": 69000,
                    "high_price": 71000,
                    "low_price": 68500,
                    "change_rate": 1.34,
                    "previous_day_volume_rate": -12.43,
                    "execution_strength": 117.2,
                },
                stock_status=metadata["005930"]["status"],
            ),
        )
        self.assertEqual(
            "▪  035720 카카오  |  KOSDAQ  |  상태 -\n"
            "▪  현재가 45,000  |  시가 -  |  고가 -  |  저가 -\n"
            "▪  등락률 -  |  전일대비 -  |  체결강도 -",
            main_table_loader._main_stock_row_tooltip(
                metadata["035720"]["market"],
                "035720",
                "카카오",
                45000,
                nxt_available=metadata["035720"]["nxt_available"],
            ),
        )
        self.assertEqual(
            "▪  005930 삼성전자  |  KOSPI  |  상태 투자경고 | 관리종목\n"
            "▪  현재가 -  |  시가 -  |  고가 -  |  저가 -\n"
            "▪  등락률 -  |  전일대비 -  |  체결강도 -",
            main_table_loader._main_stock_row_tooltip(
                metadata["005930"]["market"],
                "005930",
                "삼성전자",
                None,
                stock_state={"status": "STOPPED"},
                stock_status="투자경고 | 관리종목 | 증거금40%",
            ),
        )
        state = {"status": "STOPPED", "trade_enabled": False}
        operator_status = main_table_loader.main_stock_operator_status(
            SimpleNamespace(),
            stock_code="005930",
            stock_state=state,
            operation_excluded=False,
            review_required=False,
        )
        self.assertEqual("대기", operator_status)
        self.assertEqual("STOPPED", state["status"])

    def test_main_stock_tooltip_filters_only_nonessential_master_statuses(self) -> None:
        cases = (
            ("정상 | 증거금40% | 신용가능 | 담보대출", "정상"),
            ("투자주의환기종목 | 증거금100%", "투자주의환기종목"),
            ("관리종목 | 거래정지 | 신용가능", "관리종목 | 거래정지"),
            ("투자경고 | 관리종목 | 증거금40%", "투자경고 | 관리종목"),
            ("증거금20% | 담보대출 | 신용가능", "-"),
        )
        for raw_status, expected in cases:
            with self.subTest(raw_status=raw_status):
                self.assertEqual(
                    expected,
                    main_table_loader.main_monitoring_stock_status_text(raw_status),
                )

    def test_main_stock_tooltip_does_not_use_operation_lifecycle_status(self) -> None:
        projection = {
            "market": "KOSDAQ",
            "stock_code": "130500",
            "stock_name": "GH신소재",
            "stock_status": "관리종목 | 거래정지 | 신용가능",
            "operator_status": "감시/대기",
            "stock_state": {"status": "STOPPED"},
        }

        tooltip = main_table_loader.main_stock_row_tooltip_from_projection(projection)

        self.assertIn("상태 관리종목 | 거래정지", tooltip)
        self.assertNotIn("상태 감시/대기", tooltip)
        self.assertNotIn("상태 대기", tooltip)

    def test_main_stock_tooltip_prefers_process_local_live_market_state(self) -> None:
        projection = {
            "market": "KOSPI",
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "current_price": 1,
            "nxt_available": True,
            "stock_status": "정상 | 증거금40% | 신용가능",
            "stock_state": {"status": "STOPPED", "open_price": 2},
            "operator_status": "대기",
        }
        live = SimpleNamespace(
            last_price=70000,
            open_price=69000,
            high_price=71000,
            low_price=68500,
            change_rate=1.34,
            previous_day_volume_rate=-12.43,
            execution_strength=117.2,
        )

        tooltip = main_table_loader.main_stock_row_tooltip_from_projection(
            projection,
            live,
        )

        self.assertIn("현재가 70,000", tooltip)
        self.assertIn("시가 69,000", tooltip)
        self.assertIn("등락률 +1.34%", tooltip)
        self.assertIn("전일대비 -12.43%", tooltip)
        self.assertIn("체결강도 117.2", tooltip)

    def test_main_tooltip_uses_current_session_snapshot_when_tick_count_is_zero(self) -> None:
        projection = {
            "market": "KOSPI",
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "current_price": None,
            "nxt_available": True,
            "stock_state": {"status": "STOPPED"},
        }
        market = SimpleNamespace(
            connection_epoch=7,
            login_session_id="SESSION-7",
            last_price=70000,
            open_price=69000,
            high_price=71000,
            low_price=68000,
            change_rate=1.25,
            previous_day_volume_rate=-12.43,
            execution_strength=117.2,
        )
        host = SimpleNamespace(
            monitoring_market_information_state=Mock(return_value=market),
            high_resolution_market_data_snapshot=Mock(
                return_value=SimpleNamespace(
                    connection_epoch=7,
                    login_session_id="SESSION-7",
                    received_tick_count=0,
                )
            ),
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host)
        )
        index = SimpleNamespace(
            data=Mock(return_value=projection)
        )

        tooltip = MainWindow._main_stock_live_tooltip(owner, index, "fallback")

        self.assertIn("현재가 70,000", tooltip)
        self.assertIn("시가 69,000", tooltip)
        self.assertIn("등락률 +1.25%", tooltip)
        self.assertIn("체결강도 117.2", tooltip)
        host.monitoring_market_information_state.assert_called_once_with("005930")

    def test_persistent_filter_can_resolve_live_text_without_filesystem_io(self) -> None:
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("삼성전자")
        item.setToolTip("STATIC")
        table.setItem(0, 0, item)
        resolver = Mock(return_value="LIVE")
        tooltip_filter = stock_tooltip.install_persistent_stock_name_tooltips(
            table,
            {0},
            tooltip_resolver=resolver,
        )

        index = table.model().index(0, 0)
        self.assertEqual("LIVE", tooltip_filter._tooltip_text(index))
        resolver.assert_called_once_with(index, "STATIC")

    def test_registration_dialog_uses_caller_parent_and_exact_table_width(self) -> None:
        owner = QWidget()
        snapshot = SimpleNamespace(source="TEST", records=[])
        with patch.object(
            setting_window,
            "load_stock_library_snapshot",
            return_value=snapshot,
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                owner,
                instance_metadata={"display_name": "테스트"},
            )
        table = dialog.result_table
        initial_column_widths = tuple(
            table.columnWidth(column) for column in range(table.columnCount())
        )
        initial_dialog_width = dialog.width()
        table.setRowCount(20)
        dialog._normalize_dialog_width_to_result_table()
        dialog.show()
        self.app.processEvents()

        margins = dialog.layout().contentsMargins()
        expected_width = (
            dialog._result_table_required_width()
            + margins.left()
            + margins.right()
        )
        self.assertIs(owner, dialog.parentWidget())
        self.assertTrue(dialog.isWindow())
        self.assertFalse(bool(dialog.windowFlags() & Qt.WindowStaysOnTopHint))
        self.assertEqual(expected_width, dialog.width())
        self.assertEqual(initial_dialog_width, dialog.width())
        self.assertEqual(
            initial_column_widths,
            tuple(table.columnWidth(column) for column in range(table.columnCount())),
        )
        self.assertEqual(
            dialog._fixed_row_number_header_width,
            table.verticalHeader().width(),
        )
        self.assertTrue(all(
            table.horizontalHeader().sectionResizeMode(column) == QHeaderView.Fixed
            for column in range(table.columnCount())
        ))
        self.assertEqual(
            QHeaderView.Fixed,
            table.verticalHeader().sectionResizeMode(0),
        )
        self.assertEqual(0, table.horizontalScrollBar().maximum())
        self.assertEqual(Qt.ScrollBarAlwaysOn, table.verticalScrollBarPolicy())
        dialog.close()

    def test_registration_search_runs_only_from_enter_or_search_button(self) -> None:
        class CountingDialog(setting_window.InstanceStockSearchRegisterDialog):
            def __init__(self, *args, **kwargs) -> None:
                self.search_call_count = 0
                super().__init__(*args, **kwargs)

            def search_stocks(self, *args, **kwargs) -> None:
                self.search_call_count += 1
                super().search_stocks(*args, **kwargs)

        snapshot = SimpleNamespace(
            source="TEST",
            records=(
                {
                    "code": "005930",
                    "name": "삼성전자",
                    "market": "KOSPI",
                    "nxt_available": True,
                },
            ),
        )
        with (
            patch.object(
                setting_window,
                "load_stock_library_snapshot",
                return_value=snapshot,
            ),
            patch.object(
                CountingDialog,
                "_classification_text",
                return_value="등록대기",
            ),
        ):
            dialog = CountingDialog()
        self.addCleanup(dialog.close)
        with (
            patch.object(
                setting_window,
                "load_stock_library_snapshot",
                return_value=snapshot,
            ),
            patch.object(
                CountingDialog,
                "_classification_text",
                return_value="등록대기",
            ),
        ):
            dialog.search_call_count = 0
            dialog.show()
            self.app.processEvents()

            dialog.search_input.setText("삼")
            dialog.search_input.setText("삼성")
            QTest.keyClick(dialog.search_input, Qt.Key_Backspace)
            QTest.keyClick(dialog.search_input, Qt.Key_Delete)
            self.app.processEvents()
            self.assertEqual(0, dialog.search_call_count)
            self.assertEqual(0, dialog.result_table.rowCount())

            dialog.search_input.setText("삼성")
            QTest.keyClick(dialog.search_input, Qt.Key_Return)
            self.app.processEvents()
            self.assertEqual(1, dialog.search_call_count)
            self.assertEqual(1, dialog.result_table.rowCount())

            dialog.btn_search.click()
            self.app.processEvents()
            self.assertEqual(2, dialog.search_call_count)
            self.assertEqual(1, dialog.result_table.rowCount())

    def test_registration_dialog_projects_market_nxt_and_name_alignment(self) -> None:
        records = (
            {
                "code": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "chosung": "ㅅㅅㅈㅈ",
                "nxt_available": True,
            },
            {
                "code": "035720",
                "name": "가나다라마바사아자차카타파하가나다",
                "market": "KOSDAQ",
                "chosung": "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㄱㄴㄷ",
                "nxt_available": False,
            },
        )
        snapshot = SimpleNamespace(source="RUNTIME_LIBRARY", records=records)
        with (
            patch.object(
                setting_window,
                "load_stock_library_snapshot",
                return_value=snapshot,
            ),
            patch.object(
                setting_window.InstanceStockSearchRegisterDialog,
                "_classification_text",
                return_value="등록대기",
            ),
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog()
            dialog.search_input.setText("삼성, 가나다")
            dialog.search_stocks()
        self.addCleanup(dialog.close)

        self.assertEqual(
            [
                "종목코드",
                "종목명",
                "시장",
                "등록상태",
                "분류",
                "비고",
                "현재주가",
                "등락률",
                "체결강도",
                "전일대비",
                "거래대금",
                "거래량",
                "시총",
                "상태",
            ],
            [
                dialog.result_table.horizontalHeaderItem(column).text()
                for column in range(dialog.result_table.columnCount())
            ],
        )
        self.assertEqual("KOSPI", dialog.result_table.item(0, 2).text())
        self.assertEqual("코스닥", dialog.result_table.item(1, 2).text())
        self.assertEqual("등록대기", dialog.result_table.item(0, 3).text())
        self.assertEqual("-", dialog.result_table.item(0, 4).text())
        self.assertEqual("-", dialog.result_table.item(1, 4).text())
        self.assertEqual("NXT", dialog.result_table.item(0, 5).text())
        self.assertEqual("", dialog.result_table.item(1, 5).text())
        self.assertEqual(
            "#1e3a5f",
            dialog.result_table.item(0, 2).foreground().color().name(),
        )
        self.assertEqual(
            "#6b3e2e",
            dialog.result_table.item(1, 2).foreground().color().name(),
        )
        self.assertIsInstance(
            dialog.result_table.itemDelegateForColumn(2),
            setting_window.SelectedTextReadableDelegate,
        )
        self.assertIn("QTableWidget::item", dialog.result_table.styleSheet())
        self.assertIn(
            "QHeaderView#instanceStockSearchHorizontalHeader::section",
            dialog.result_table.styleSheet(),
        )
        self.assertIn(
            "QHeaderView#instanceStockSearchVerticalHeader::section",
            dialog.result_table.styleSheet(),
        )
        grid_line_color = dialog.TABLE_SEPARATOR_COLOR
        self.assertIn(f"gridline-color: {grid_line_color}", dialog.result_table.styleSheet())
        self.assertIn(
            f"border-right: 1px solid {grid_line_color}",
            dialog.result_table.styleSheet(),
        )
        self.assertNotIn(
            f"border-left: 1px solid {grid_line_color}",
            dialog.result_table.styleSheet(),
        )
        self.assertIn(
            f"border: 1px solid {grid_line_color}",
            dialog.result_table.styleSheet(),
        )
        self.assertFalse(dialog.result_table.alternatingRowColors())
        self.assertNotIn("alternate-background-color", dialog.result_table.styleSheet())
        self.assertIn(
            "QScrollBar:vertical { border: none;",
            dialog.result_table.verticalScrollBar().styleSheet(),
        )
        self.assertEqual(
            Qt.ScrollBarAlwaysOn,
            dialog.result_table.verticalScrollBarPolicy(),
        )
        self.assertEqual(Qt.AlignCenter, dialog.result_table.horizontalHeader().defaultAlignment())
        self.assertEqual(Qt.AlignCenter, dialog.result_table.item(0, 1).textAlignment())
        self.assertEqual(
            int(Qt.AlignLeft | Qt.AlignVCenter),
            dialog.result_table.item(1, 1).textAlignment(),
        )
        self.assertEqual("", dialog.result_table.item(0, 1).toolTip())
        self.assertEqual(records[1]["name"], dialog.result_table.item(1, 1).toolTip())
        self.assertNotIn("...", dialog.result_table.item(1, 1).text())
        self.assertEqual(14, dialog.STOCK_NAME_DISPLAY_CHARACTERS)
        self.assertEqual(
            "#111827",
            dialog._stock_name_clip_delegate.selected_text_color.name(),
        )
        code_text_width = dialog.result_table.fontMetrics().horizontalAdvance(
            dialog.result_table.item(0, 0).text()
        )
        common_margin = (
            dialog.result_table.columnWidth(0) - 1 - code_text_width
        ) // 2
        for column in (0, 2, 3, 5):
            item = dialog.result_table.item(0, column)
            usable_width = dialog.result_table.columnWidth(column) - 1
            text_width = dialog.result_table.fontMetrics().horizontalAdvance(item.text())
            self.assertEqual(common_margin * 2, usable_width - text_width)
        expected_row_header_width = dialog._fixed_row_number_header_width
        self.assertEqual(
            expected_row_header_width,
            dialog.result_table.verticalHeader().width(),
        )
        for row in range(dialog.result_table.rowCount()):
            for column in (0, 2, 3, 4, 5):
                self.assertEqual(
                    Qt.AlignCenter,
                    dialog.result_table.item(row, column).textAlignment(),
                )
        dialog.show()
        self.app.processEvents()
        self.assertEqual(0, dialog.result_table.horizontalScrollBar().maximum())

    def test_reused_registration_dialog_is_reparented_to_current_caller(self) -> None:
        first_owner = QWidget()
        second_owner = QWidget()
        snapshot = SimpleNamespace(source="TEST", records=[])
        metadata = {"instance_id": "instance-a", "display_name": "테스트"}
        with patch.object(
            setting_window,
            "load_stock_library_snapshot",
            return_value=snapshot,
        ):
            first = setting_window.open_instance_stock_search_register_dialog(
                first_owner,
                metadata,
            )
            second = setting_window.open_instance_stock_search_register_dialog(
                second_owner,
                metadata,
            )
        self.assertIs(first, second)
        self.assertIs(second_owner, second.parentWidget())
        self.assertTrue(second.isVisible())
        self.assertFalse(second.isMinimized())
        self.assertFalse(bool(second.windowFlags() & Qt.WindowStaysOnTopHint))
        second.close()


if __name__ == "__main__":
    unittest.main()
