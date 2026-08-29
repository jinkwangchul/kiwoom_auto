# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt, QItemSelectionModel
from PyQt5.QtGui import QHelpEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QToolTip

import gui_auto_trade_setting_window as setting_window


class _SnapshotApi:
    def __init__(self) -> None:
        self.requests: list[tuple[tuple[str, ...], object]] = []

    def request_initial_market_snapshot(self, stock_codes, *, callback=None):
        codes = tuple(stock_codes)
        self.requests.append((codes, callback))
        return {
            "ok": True,
            "status": "ENQUEUED",
            "target_stock_codes": list(codes),
            "batch_count": (len(codes) + 99) // 100,
        }

    def emit(self, request_index: int, payload: dict[str, object]) -> None:
        callback = self.requests[request_index][1]
        callback(payload)


class StockRegistrationMarketSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, library, api: _SnapshotApi):
        patches = (
            patch.object(
                setting_window,
                "load_stock_library_snapshot",
                return_value=SimpleNamespace(source="TEST", records=tuple(library)),
            ),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        dialog = setting_window.InstanceStockSearchRegisterDialog(
            None,
            instance_metadata={"target_kind": "unassigned"},
            kiwoom_api=api,
        )
        self.addCleanup(dialog.close)
        return dialog

    @staticmethod
    def _codes(dialog) -> list[str]:
        return [
            dialog.result_table.item(row, dialog.CODE_COLUMN).text()
            for row in range(dialog.result_table.rowCount())
        ]

    def test_existing_table_visual_contract_and_registration_columns_are_preserved(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [{"code": "005930", "name": "삼성전자", "market": "KOSPI"}],
            api,
        )

        self.assertEqual(
            ["종목코드", "종목명", "시장", "등록상태", "분류", "비고"],
            [
                dialog.result_table.horizontalHeaderItem(column).text()
                for column in range(6)
            ],
        )
        self.assertEqual(QAbstractItemView.SelectRows, dialog.result_table.selectionBehavior())
        self.assertEqual(QAbstractItemView.ExtendedSelection, dialog.result_table.selectionMode())
        self.assertFalse(dialog.result_table.alternatingRowColors())
        self.assertFalse(
            dialog.result_table.horizontalHeader().isSortIndicatorShown()
        )
        self.assertEqual(Qt.ScrollBarAlwaysOff, dialog.result_table.horizontalScrollBarPolicy())
        self.assertEqual(Qt.ScrollBarAlwaysOn, dialog.result_table.verticalScrollBarPolicy())
        self.assertIn("background: #dbeafe", dialog.result_table.styleSheet())
        self.assertIn("background: #FFFFFF", dialog.result_table.styleSheet())

    def test_instrument_classification_filter_is_local_and_deselects_hidden_rows(self) -> None:
        api = _SnapshotApi()
        classifications = (
            ("005930", "일반종목"),
            ("005935", "일반종목"),
            ("222222", "ETF"),
            ("333333", "ETN"),
            ("444444", "SPAC"),
            ("555555", "REIT"),
            ("666666", "-"),
        )
        dialog = self._dialog(
            [
                {
                    "code": code,
                    "name": f"분류종목{index}",
                    "market": "KOSPI" if index % 2 else "KOSDAQ",
                    "classification": classification,
                }
                for index, (code, classification) in enumerate(classifications, start=1)
            ],
            api,
        )
        dialog.search_input.setText("분류종목")
        dialog.search_stocks()
        self.assertEqual(1, len(api.requests))
        self.assertFalse(dialog.general_stock_button.isChecked())
        self.assertIn(
            dialog.REGISTRATION_BADGE_INACTIVE_COLOR,
            dialog.general_stock_button.styleSheet(),
        )
        self.assertEqual("#4B5563", dialog.REGISTRATION_BADGE_INACTIVE_COLOR)
        self.assertEqual(
            [
                "일반" if classification == "일반종목" else classification
                for _code, classification in classifications
            ],
            [
                dialog.result_table.item(
                    row,
                    dialog.INSTRUMENT_CLASSIFICATION_COLUMN,
                ).text()
                for row in range(dialog.result_table.rowCount())
            ],
        )

        selection_model = dialog.result_table.selectionModel()
        for code in ("005930", "222222"):
            row = dialog._find_result_row_by_stock_code(code)
            selection_model.select(
                dialog.result_table.model().index(row, dialog.CODE_COLUMN),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )

        dialog.general_stock_button.setChecked(True)
        self.assertIn(
            setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
            dialog.general_stock_button.styleSheet(),
        )
        general_row = dialog._find_result_row_by_stock_code("005930")
        self.assertFalse(dialog.result_table.isRowHidden(general_row))
        preferred_general_row = dialog._find_result_row_by_stock_code("005935")
        self.assertFalse(dialog.result_table.isRowHidden(preferred_general_row))
        self.assertTrue(all(
            dialog.result_table.isRowHidden(
                dialog._find_result_row_by_stock_code(code)
            )
            for code, classification in classifications
            if classification != "일반종목"
        ))
        self.assertEqual({"005930"}, dialog._selected_result_stock_codes())
        self.assertEqual(1, len(api.requests))

        dialog.on_result_header_clicked(dialog.INSTRUMENT_CLASSIFICATION_COLUMN)
        self.assertEqual(1, len(api.requests))
        dialog.general_stock_button.setChecked(False)
        self.assertTrue(all(
            not dialog.result_table.isRowHidden(
                dialog._find_result_row_by_stock_code(code)
            )
            for code, _classification in classifications
        ))
        self.assertEqual(1, len(api.requests))

    def test_snapshot_maps_all_numeric_columns_and_sorts_locally_without_new_request(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [
                {"code": "111111", "name": "첫종목", "market": "KOSPI"},
                {"code": "222222", "name": "둘종목", "market": "KOSDAQ"},
                {"code": "333333", "name": "셋종목", "market": "KOSPI"},
            ],
            api,
        )
        dialog.search_input.setText("종목")
        dialog.search_stocks()
        self.assertEqual(1, len(api.requests))
        self.assertEqual(("111111", "222222", "333333"), api.requests[0][0])

        api.emit(
            0,
            {
                "ok": True,
                "rows": [
                    {
                        "stock_code": "111111",
                        "current_price": 1000,
                        "change_rate": 1.25,
                        "execution_strength": 117.2,
                        "previous_day_volume_rate": 12.43,
                        "cumulative_trading_value": 9876543,
                        "cumulative_volume": 123456,
                        "market_capitalization": 4321000,
                    },
                    {"stock_code": "222222", "current_price": 50},
                    {"stock_code": "333333", "current_price": 700},
                ],
            },
        )

        row = dialog._find_result_row_by_stock_code("111111")
        self.assertEqual("1,000", dialog.result_table.item(row, dialog.CURRENT_PRICE_COLUMN).text())
        self.assertEqual("+1.25%", dialog.result_table.item(row, dialog.CHANGE_RATE_COLUMN).text())
        self.assertEqual("117.20", dialog.result_table.item(row, dialog.EXECUTION_STRENGTH_COLUMN).text())
        self.assertEqual("+12.43%", dialog.result_table.item(row, dialog.PREVIOUS_DAY_VOLUME_RATE_COLUMN).text())
        self.assertEqual("98,765억", dialog.result_table.item(row, dialog.TRADING_VALUE_COLUMN).text())
        self.assertEqual("123,456주", dialog.result_table.item(row, dialog.VOLUME_COLUMN).text())
        self.assertEqual("4,321,000억", dialog.result_table.item(row, dialog.MARKET_CAP_COLUMN).text())

        dialog.on_result_header_clicked(dialog.CURRENT_PRICE_COLUMN)
        self.assertEqual(["222222", "333333", "111111"], self._codes(dialog))
        self.assertFalse(
            dialog.result_table.horizontalHeader().isSortIndicatorShown()
        )
        dialog.on_result_header_clicked(dialog.CURRENT_PRICE_COLUMN)
        self.assertEqual(["111111", "333333", "222222"], self._codes(dialog))
        self.assertFalse(
            dialog.result_table.horizontalHeader().isSortIndicatorShown()
        )
        self.assertEqual(1, len(api.requests))
        dialog.result_table.verticalScrollBar().setValue(1)
        dialog.result_table.selectRow(0)
        self.assertEqual(1, len(api.requests))

    def test_volume_trading_value_and_market_cap_display_units(self) -> None:
        dialog_class = setting_window.InstanceStockSearchRegisterDialog
        volume_cases = (
            (1_523_650, "1,523,650주"),
            (100_000_000, "1억주"),
            (120_000_000, "1.2억주"),
            (350_000_000, "3.5억주"),
        )
        trading_value_cases = (
            (100, "1억"),
            (12_500, "125억"),
            (287_735, "2,877억"),
            (1_250_000, "12,500억"),
        )
        market_cap_cases = (
            (5, "5억"),
            (18_241, "18,241억"),
        )
        for raw, expected in volume_cases:
            with self.subTest(kind="volume", raw=raw):
                self.assertEqual(expected, dialog_class._format_snapshot_volume(raw))
        for raw, expected in trading_value_cases:
            with self.subTest(kind="trading_value", raw=raw):
                self.assertEqual(
                    expected,
                    dialog_class._format_snapshot_trading_value(raw),
                )
        for raw, expected in market_cap_cases:
            with self.subTest(kind="market_cap", raw=raw):
                self.assertEqual(
                    expected,
                    dialog_class._format_snapshot_market_cap(raw),
                )
        for formatter in (
            dialog_class._format_snapshot_volume,
            dialog_class._format_snapshot_trading_value,
            dialog_class._format_snapshot_market_cap,
        ):
            self.assertEqual("-", formatter(None))
            self.assertEqual("-", formatter("invalid"))

    def test_unit_display_columns_keep_raw_numeric_sort_values(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [
                {"code": "111111", "name": "첫종목", "market": "KOSPI"},
                {"code": "222222", "name": "둘종목", "market": "KOSDAQ"},
                {"code": "333333", "name": "셋종목", "market": "KOSPI"},
            ],
            api,
        )
        dialog.search_input.setText("종목")
        dialog.search_stocks()
        api.emit(
            0,
            {
                "ok": True,
                "rows": [
                    {
                        "stock_code": "111111",
                        "cumulative_trading_value": 95_000,
                        "cumulative_volume": 120_000_000,
                    },
                    {
                        "stock_code": "222222",
                        "cumulative_trading_value": 287_735,
                        "cumulative_volume": 95_000_000,
                    },
                    {
                        "stock_code": "333333",
                        "cumulative_trading_value": 1_250_000,
                        "cumulative_volume": 100_000_000,
                    },
                ],
            },
        )

        dialog.on_result_header_clicked(dialog.TRADING_VALUE_COLUMN)
        self.assertEqual(["111111", "222222", "333333"], self._codes(dialog))
        self.assertEqual(
            ["950억", "2,877억", "12,500억"],
            [
                dialog.result_table.item(row, dialog.TRADING_VALUE_COLUMN).text()
                for row in range(dialog.result_table.rowCount())
            ],
        )
        dialog.on_result_header_clicked(dialog.VOLUME_COLUMN)
        self.assertEqual(["222222", "333333", "111111"], self._codes(dialog))
        self.assertEqual(
            ["95,000,000주", "1억주", "1.2억주"],
            [
                dialog.result_table.item(row, dialog.VOLUME_COLUMN).text()
                for row in range(dialog.result_table.rowCount())
            ],
        )
        self.assertEqual(1, len(api.requests))

    def test_old_search_response_is_ignored_and_failure_keeps_registration_available(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [
                {"code": "111111", "name": "이전종목", "market": "KOSPI"},
                {"code": "222222", "name": "현재종목", "market": "KOSDAQ"},
            ],
            api,
        )
        dialog.search_input.setText("이전")
        dialog.search_stocks()
        dialog.search_input.setText("현재")
        dialog.search_stocks()

        api.emit(0, {"ok": True, "rows": [{"stock_code": "111111", "current_price": 9999}]})
        self.assertEqual(["222222"], self._codes(dialog))
        self.assertEqual("-", dialog.result_table.item(0, dialog.CURRENT_PRICE_COLUMN).text())

        api.emit(1, {"ok": False, "error": "snapshot failed", "rows": []})
        dialog.result_table.selectRow(0)
        self.assertTrue(dialog.btn_register.isEnabled())
        self.assertEqual("-", dialog.result_table.item(0, dialog.CURRENT_PRICE_COLUMN).text())

    def test_status_column_uses_single_state_width_and_elides_multiple_state_text(self) -> None:
        api = _SnapshotApi()
        full_multiple_status = "투자경고 | 관리종목 | 거래정지"
        dialog = self._dialog(
            [
                {
                    "code": "111111",
                    "name": "단일상태종목",
                    "market": "KOSPI",
                    "status": "투자주의환기",
                },
                {
                    "code": "222222",
                    "name": "복수상태종목",
                    "market": "KOSDAQ",
                    "status": ["투자경고", "관리종목", "거래정지"],
                },
                {
                    "code": "333333",
                    "name": "미확인상태종목",
                    "market": "KOSPI",
                },
            ],
            api,
        )
        dialog.search_input.setText("상태종목")
        dialog.search_stocks()

        available_width = dialog._stock_status_text_available_width()
        metrics = dialog.result_table.fontMetrics()
        self.assertTrue(all(
            metrics.horizontalAdvance(status) <= available_width
            for status in dialog.STOCK_STATUS_SINGLE_VALUES
        ))

        single_row = dialog._find_result_row_by_stock_code("111111")
        single_item = dialog.result_table.item(single_row, dialog.STOCK_STATUS_COLUMN)
        self.assertEqual("투자주의환기", single_item.text())
        self.assertEqual("투자주의환기", single_item.toolTip())
        self.assertEqual("투자주의환기", single_item.data(Qt.UserRole))

        multiple_row = dialog._find_result_row_by_stock_code("222222")
        multiple_item = dialog.result_table.item(multiple_row, dialog.STOCK_STATUS_COLUMN)
        self.assertTrue(multiple_item.text().endswith("..."))
        self.assertNotEqual(full_multiple_status, multiple_item.text())
        self.assertEqual(full_multiple_status, multiple_item.toolTip())
        self.assertEqual(full_multiple_status, multiple_item.data(Qt.UserRole))

        unknown_row = dialog._find_result_row_by_stock_code("333333")
        unknown_item = dialog.result_table.item(unknown_row, dialog.STOCK_STATUS_COLUMN)
        self.assertEqual("-", unknown_item.text())
        self.assertEqual("", unknown_item.toolTip())

    def test_final_status_item_keeps_full_tooltip_after_snapshot_and_hover(self) -> None:
        api = _SnapshotApi()
        full_status = "정상 | 증거금40% | 신용가능 | 담보대출"
        dialog = self._dialog(
            [
                {
                    "code": "111111",
                    "name": "상태대상종목",
                    "market": "KOSPI",
                    "status": full_status,
                }
            ],
            api,
        )
        dialog.show()
        self.app.processEvents()
        dialog.search_input.setText("상태대상")
        dialog.search_stocks()
        self.assertEqual(1, len(api.requests))

        before = dialog.result_table.item(0, dialog.STOCK_STATUS_COLUMN)
        api.emit(
            0,
            {
                "ok": True,
                "rows": [{"stock_code": "111111", "current_price": 12345}],
            },
        )
        self.app.processEvents()
        final_item = dialog.result_table.item(0, dialog.STOCK_STATUS_COLUMN)

        self.assertIs(before, final_item)
        self.assertTrue(final_item.text().endswith("..."))
        self.assertEqual(full_status, final_item.toolTip())
        self.assertEqual(full_status, final_item.data(Qt.ToolTipRole))
        self.assertEqual(full_status, final_item.data(Qt.UserRole))

        index = dialog.result_table.model().index(0, dialog.STOCK_STATUS_COLUMN)
        rect = dialog.result_table.visualRect(index)
        local_pos = rect.center()
        global_pos = dialog.result_table.viewport().mapToGlobal(local_pos)
        QToolTip.hideText()
        event = QHelpEvent(QEvent.ToolTip, local_pos, global_pos)
        QApplication.sendEvent(dialog.result_table.viewport(), event)
        self.app.processEvents()

        self.assertTrue(QToolTip.isVisible())
        self.assertEqual(full_status, QToolTip.text())
        QToolTip.hideText()

    def test_search_results_do_not_emit_registration_toasts(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [
                {"code": "111111", "name": "단일검색종목", "market": "KOSPI"},
                {"code": "222222", "name": "복수검색종목A", "market": "KOSDAQ"},
                {"code": "333333", "name": "복수검색종목B", "market": "KOSPI"},
            ],
            api,
        )

        with patch.object(setting_window, "show_toast") as toast:
            dialog.search_input.setText("단일검색")
            dialog.search_stocks(notify_empty=True)
            self.assertEqual(1, dialog.result_table.rowCount())
            toast.assert_not_called()

            dialog.search_input.setText("복수검색")
            dialog.search_stocks(notify_empty=True)
            self.assertEqual(2, dialog.result_table.rowCount())
            toast.assert_not_called()

            dialog.search_input.setText("단일검색")
            dialog.search_stocks(notify_empty=True)
            self.assertEqual(1, dialog.result_table.rowCount())
            toast.assert_not_called()

    def test_mouse_search_does_not_invoke_registration_command(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [{"code": "111111", "name": "검색대상종목", "market": "KOSPI"}],
            api,
        )
        dialog.show()
        self.app.processEvents()
        dialog.search_input.setText("검색대상")

        with (
            patch.object(dialog, "register_selected_result_rows") as register,
            patch.object(setting_window, "show_toast") as toast,
            patch.object(setting_window, "append_base_stock") as writer,
        ):
            QTest.mouseClick(dialog.btn_search, Qt.LeftButton)
            self.app.processEvents()

        self.assertEqual(1, dialog.result_table.rowCount())
        register.assert_not_called()
        writer.assert_not_called()
        toast.assert_not_called()

    def test_enter_search_never_activates_register_default_button(self) -> None:
        api = _SnapshotApi()
        dialog = self._dialog(
            [{"code": "111111", "name": "검색대상종목", "market": "KOSPI"}],
            api,
        )
        dialog.show()
        self.app.processEvents()
        dialog.search_input.setText("검색대상")
        dialog.search_stocks()
        dialog.result_table.selectRow(0)
        dialog.search_input.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()

        self.assertFalse(dialog.btn_search.autoDefault())
        self.assertFalse(dialog.btn_search.isDefault())
        self.assertFalse(dialog.btn_register.autoDefault())
        self.assertFalse(dialog.btn_register.isDefault())
        self.assertFalse(dialog.btn_close.autoDefault())
        self.assertFalse(dialog.btn_close.isDefault())

        with (
            patch.object(dialog, "register_selected_result_rows") as register,
            patch.object(setting_window, "show_toast") as toast,
            patch.object(setting_window, "append_base_stock") as writer,
        ):
            QTest.keyClick(dialog.search_input, Qt.Key_Return)
            self.app.processEvents()

        self.assertEqual(1, dialog.result_table.rowCount())
        register.assert_not_called()
        writer.assert_not_called()
        toast.assert_not_called()

    def test_alphanumeric_snapshot_rows_match_and_fill_every_market_value(self) -> None:
        api = _SnapshotApi()
        codes = ("005930", "0134X0", "0164H0", "0165X0")
        dialog = self._dialog(
            [
                {"code": code, "name": f"혼합종목{index}", "market": "KOSPI"}
                for index, code in enumerate(codes)
            ],
            api,
        )
        dialog.search_input.setText("혼합종목")
        dialog.search_stocks()
        self.assertEqual(1, len(api.requests))
        self.assertEqual(codes, api.requests[0][0])

        rows = []
        for index, code in enumerate(codes, start=1):
            rows.append(
                {
                    "stock_code": code,
                    "current_price": 1000 * index,
                    "change_rate": 0.5 * index,
                    "execution_strength": 100 + index,
                    "previous_day_volume_rate": 80 + index,
                    "cumulative_trading_value": 100000 * index,
                    "cumulative_volume": 10000 * index,
                    "market_capitalization": 1000000 * index,
                }
            )
        api.emit(0, {"ok": True, "rows": rows})

        for index, code in enumerate(codes, start=1):
            with self.subTest(code=code):
                row = dialog._find_result_row_by_stock_code(code)
                self.assertGreaterEqual(row, 0)
                self.assertEqual(
                    f"{1000 * index:,}",
                    dialog.result_table.item(row, dialog.CURRENT_PRICE_COLUMN).text(),
                )
                for column in (
                    dialog.CHANGE_RATE_COLUMN,
                    dialog.EXECUTION_STRENGTH_COLUMN,
                    dialog.PREVIOUS_DAY_VOLUME_RATE_COLUMN,
                    dialog.TRADING_VALUE_COLUMN,
                    dialog.VOLUME_COLUMN,
                    dialog.MARKET_CAP_COLUMN,
                ):
                    self.assertNotEqual("-", dialog.result_table.item(row, column).text())


if __name__ == "__main__":
    unittest.main()
