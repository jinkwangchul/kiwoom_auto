# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QDialog, QHeaderView

from gui_indicator_follow_validation_stock_picker import (
    IndicatorFollowValidationStockPicker,
)
from gui_stock_data import (
    STOCK_LIBRARY_EMPTY_SOURCE,
    STOCK_LIBRARY_NOT_SYNCED,
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
)
from gui_stock_library_browser import (
    STOCK_BROWSER_HEADERS,
    stock_browser_table_required_width,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from routines.지표추종매매.routine_validation_operation_reader import (
    ValidationOperationStateReadError,
    is_operation_active,
)
from routines.지표추종매매.routine_validation_session import (
    REASON_OPERATION_ACTIVE_READER_ERROR,
    ValidationSession,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
)


class ValidationOperationReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "operation_state.json"
        self.today = date(2026, 9, 13)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_state(self, operation_date: str, status: str) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "operation_date": operation_date,
                    "operation_status": status,
                }
            ),
            encoding="utf-8",
        )

    def _session(self, reader) -> ValidationSession:
        request = ValidationRequest(
            ValidationStockRef("005930", "삼성전자"),
            ValidationSettingsSnapshot({"bar": {"bar_minutes": 3}}),
            3,
        )
        return ValidationSession(request, operation_active_reader=reader)

    def test_today_running_and_closing_are_active(self) -> None:
        for status in ("RUNNING", "CLOSING"):
            with self.subTest(status=status):
                self._write_state(self.today.isoformat(), status)
                self.assertTrue(
                    is_operation_active(self.state_path, today=self.today)
                )

    def test_normal_ended_and_prior_day_are_inactive(self) -> None:
        self._write_state(self.today.isoformat(), "NORMAL_ENDED")
        self.assertFalse(is_operation_active(self.state_path, today=self.today))
        self._write_state("2026-09-12", "RUNNING")
        self.assertFalse(is_operation_active(self.state_path, today=self.today))

    def test_missing_malformed_and_unknown_fail_closed(self) -> None:
        cases = (
            None,
            "not-json",
            json.dumps({"operation_date": self.today.isoformat()}),
            json.dumps(
                {
                    "operation_date": self.today.isoformat(),
                    "operation_status": "UNKNOWN",
                }
            ),
        )
        for content in cases:
            with self.subTest(content=content):
                if self.state_path.exists():
                    self.state_path.unlink()
                if content is not None:
                    self.state_path.write_text(content, encoding="utf-8")
                reader = lambda: is_operation_active(
                    self.state_path, today=self.today
                )
                availability = self._session(reader).readiness()
                self.assertFalse(availability.allowed)
                self.assertEqual(
                    REASON_OPERATION_ACTIVE_READER_ERROR,
                    availability.reason,
                )
                with self.assertRaises(ValidationOperationStateReadError):
                    reader()

    def test_reader_never_writes(self) -> None:
        self._write_state(self.today.isoformat(), "RUNNING")
        with patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
            self.assertTrue(is_operation_active(self.state_path, today=self.today))

    def test_reader_imports_only_standard_library(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "routines"
            / "지표추종매매"
            / "routine_validation_operation_reader.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertLessEqual(imported_roots, {"__future__", "datetime", "json", "pathlib"})


class IndicatorFollowValidationStockPickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.dialogs: list[IndicatorFollowValidationStockPicker] = []
        self.records = (
            {
                "code": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "classification": "일반종목",
                "chosung": "ㅅㅅㅈㅈ",
                "nxt_available": True,
                "status": "정상",
            },
            {
                "code": "000660",
                "name": "SK하이닉스",
                "market": "KOSPI",
                "classification": "ETF",
                "chosung": "ㅎㅇㄴㅅ",
                "status": "투자주의",
            },
            {
                "code": "035420",
                "name": "NAVER",
                "market": "KOSDAQ",
                "classification": "일반종목",
                "chosung": "ㄴㅇㅂ",
            },
        )

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()

    def _dialog(self, snapshot=None, market_snapshot_api=None):
        if snapshot is None:
            snapshot = StockLibraryLoadSnapshot(
                STOCK_LIBRARY_READY,
                STOCK_LIBRARY_RUNTIME_SOURCE,
                self.records,
            )
        loader = Mock(return_value=snapshot)
        dialog = IndicatorFollowValidationStockPicker(
            snapshot_loader=loader,
            market_snapshot_api=market_snapshot_api,
        )
        self.dialogs.append(dialog)
        loader.assert_called_once_with(None)
        return dialog

    def _production_dialog(self, snapshot):
        import gui_auto_trade_setting_window as setting_window

        loader_patch = patch.object(
            setting_window,
            "load_stock_library_snapshot",
            return_value=snapshot,
        )
        stocks_patch = patch.object(setting_window, "read_base_stocks", return_value=[])
        loader_patch.start()
        stocks_patch.start()
        self.addCleanup(loader_patch.stop)
        self.addCleanup(stocks_patch.stop)
        dialog = setting_window.InstanceStockSearchRegisterDialog(
            instance_metadata={"instance_name": "지표추종매매"},
            kiwoom_api=None,
        )
        self.dialogs.append(dialog)
        return dialog

    @staticmethod
    def _widget_image_bytes(widget) -> tuple[int, int, bytes]:
        image = widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
        bits = image.bits()
        bits.setsize(image.byteCount())
        return image.width(), image.height(), bytes(bits)

    @staticmethod
    def _widget_edge_pixel_samples(widget) -> tuple[object, ...]:
        image = widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
        points = (
            (0, 0),
            (image.width() - 1, 0),
            (0, image.height() - 1),
            (image.width() - 1, image.height() - 1),
            (4, image.height() // 2),
            (image.width() - 5, image.height() // 2),
        )
        return tuple(image.pixelColor(x, y) for x, y in points)

    @staticmethod
    def _visible_codes(dialog) -> list[str]:
        return [
            dialog.stock_table.item(row, 0).text()
            for row in range(dialog.stock_table.rowCount())
        ]

    @staticmethod
    def _search(dialog, keyword: str) -> None:
        dialog.search_input.setText(keyword)
        dialog.btn_search.click()

    def test_valid_snapshot_starts_empty_and_text_change_does_not_search(self) -> None:
        dialog = self._dialog()
        self.assertEqual(0, dialog.result_table.rowCount())
        self.assertFalse(dialog.select_button.isEnabled())

        dialog.search_input.setText("삼성")
        self.assertEqual(0, dialog.result_table.rowCount())
        self.assertFalse(dialog.select_button.isEnabled())

    def test_search_button_filters_by_code_name_market_and_chosung(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "0006")
        self.assertEqual(["000660"], self._visible_codes(dialog))
        self._search(dialog, "삼성")
        self.assertEqual(["005930"], self._visible_codes(dialog))
        self._search(dialog, "코스닥")
        self.assertEqual(["035420"], self._visible_codes(dialog))
        self._search(dialog, "ㅎㅇㄴㅅ")
        self.assertEqual(["000660"], self._visible_codes(dialog))

    def test_enter_runs_explicit_search(self) -> None:
        dialog = self._dialog()
        dialog.search_input.setText("NAVER")
        self.assertEqual(0, dialog.result_table.rowCount())
        dialog.search_input.returnPressed.emit()
        self.assertEqual(["035420"], self._visible_codes(dialog))

    def test_empty_explicit_search_clears_existing_result(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "삼성")
        dialog.result_table.selectRow(0)
        self.assertTrue(dialog.select_button.isEnabled())
        self._search(dialog, "")
        self.assertEqual(0, dialog.result_table.rowCount())
        self.assertFalse(dialog.select_button.isEnabled())

    def test_operator_browser_contract_matches_registration_columns_and_filters(self) -> None:
        dialog = self._dialog()
        self.assertEqual(
            list(STOCK_BROWSER_HEADERS),
            [
                dialog.result_table.horizontalHeaderItem(column).text()
                for column in range(dialog.result_table.columnCount())
            ],
        )
        self.assertEqual(
            QAbstractItemView.SingleSelection,
            dialog.result_table.selectionMode(),
        )
        self.assertEqual("일반종목", dialog.general_stock_button.text())
        self.assertEqual("TOP100 :", dialog.ranking_title_label.text())
        self.assertEqual(
            ["거래량", "거래대금", "급상승", "급하락"],
            [button.text() for button in dialog.ranking_buttons.values()],
        )
        self.assertEqual("선택", dialog.select_button.text())
        self.assertEqual("취소", dialog.cancel_button.text())

    def test_one_selection_returns_validation_stock_ref(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "000660")
        dialog.stock_table.selectRow(0)
        dialog.select_button.click()

        self.assertEqual(QDialog.Accepted, dialog.result())
        self.assertEqual(
            ValidationStockRef("000660", "SK하이닉스"),
            dialog.selected_stock,
        )

    def test_cancel_returns_no_selection(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "삼성")
        dialog.stock_table.selectRow(0)
        dialog.cancel_button.click()

        self.assertEqual(QDialog.Rejected, dialog.result())
        self.assertIsNone(dialog.selected_stock)

    def test_double_click_returns_validation_stock_ref(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "NAVER")
        item = dialog.stock_table.item(0, dialog.NAME_COLUMN)
        dialog._accept_double_clicked(item)
        self.assertEqual(QDialog.Accepted, dialog.result())
        self.assertEqual(
            ValidationStockRef("035420", "NAVER"),
            dialog.selected_stock,
        )

    def test_general_filter_and_local_sort_preserve_single_selection(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "005930,000660,035420")
        dialog.general_stock_button.setChecked(True)
        self.assertFalse(dialog.result_table.isRowHidden(dialog._find_row("005930")))
        self.assertTrue(dialog.result_table.isRowHidden(dialog._find_row("000660")))
        self.assertFalse(dialog.result_table.isRowHidden(dialog._find_row("035420")))

        dialog.general_stock_button.setChecked(False)
        dialog.on_result_header_clicked(dialog.CODE_COLUMN)
        self.assertEqual(
            ["000660", "005930", "035420"],
            self._visible_codes(dialog),
        )

    def test_invalid_snapshot_blocks_selection(self) -> None:
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_NOT_SYNCED,
            STOCK_LIBRARY_EMPTY_SOURCE,
            (),
            "RUNTIME_SYNC_STATE_UNAVAILABLE",
        )
        dialog = self._dialog(snapshot)

        self.assertEqual(0, dialog.stock_table.rowCount())
        self.assertFalse(dialog.select_button.isEnabled())
        dialog.select_button.click()
        self.assertIsNone(dialog.selected_stock)

    def test_picker_actions_do_not_write(self) -> None:
        dialog = self._dialog()
        with patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
            self._search(dialog, "NAVER")
            dialog.stock_table.selectRow(0)
            dialog.select_button.click()
        self.assertEqual(ValidationStockRef("035420", "NAVER"), dialog.selected_stock)

    def test_search_and_ranking_use_only_injected_read_only_snapshot_surfaces(self) -> None:
        class SnapshotApi:
            def __init__(self):
                self.market_requests = []
                self.ranking_requests = []

            def request_initial_market_snapshot(self, codes, *, callback=None):
                self.market_requests.append((tuple(codes), callback))
                return {"ok": True}

            def request_stock_ranking_snapshot(self, source, *, callback=None):
                self.ranking_requests.append((source, callback))
                return {"ok": True}

        api = SnapshotApi()
        dialog = self._dialog(market_snapshot_api=api)
        self.assertEqual([], api.market_requests)
        self.assertEqual([], api.ranking_requests)
        dialog.search_input.setText("삼성")
        self.assertEqual(0, dialog.result_table.rowCount())
        self.assertEqual([], api.market_requests)
        dialog.btn_search.click()
        self.assertEqual(("005930",), api.market_requests[0][0])
        api.market_requests[0][1](
            {
                "ok": True,
                "rows": [
                    {
                        "stock_code": "005930",
                        "current_price": 75000,
                        "change_rate": 1.25,
                        "execution_strength": 117.2,
                        "previous_day_volume_rate": 12.43,
                        "cumulative_trading_value": 287735,
                        "cumulative_volume": 1523650,
                        "market_capitalization": 4321000,
                    }
                ],
            }
        )
        row = dialog._find_row("005930")
        self.assertEqual(
            ["75,000", "+1.25%", "117.20", "+12.43%", "2,877억", "1,523,650주", "4,321,000억"],
            [
                dialog.result_table.item(row, column).text()
                for column in (
                    dialog.CURRENT_PRICE_COLUMN,
                    dialog.CHANGE_RATE_COLUMN,
                    dialog.EXECUTION_STRENGTH_COLUMN,
                    dialog.PREVIOUS_DAY_VOLUME_RATE_COLUMN,
                    dialog.TRADING_VALUE_COLUMN,
                    dialog.VOLUME_COLUMN,
                    dialog.MARKET_CAP_COLUMN,
                )
            ],
        )

        dialog.ranking_buttons["VOLUME_TOP"].click()
        self.assertEqual("VOLUME_TOP", api.ranking_requests[0][0])
        api.ranking_requests[0][1](
            {
                "ok": True,
                "rows": [
                    {
                        "stock_code": "000660",
                        "stock_name": "SK하이닉스",
                        "cumulative_volume": 999999,
                    }
                ],
            }
        )
        self.assertEqual(["000660"], self._visible_codes(dialog))
        self.assertEqual(2, len(api.market_requests))

        market_request_count = len(api.market_requests)
        ranking_request_count = len(api.ranking_requests)
        dialog.general_stock_button.setChecked(True)
        dialog.general_stock_button.setChecked(False)
        self.assertEqual(market_request_count, len(api.market_requests))
        self.assertEqual(ranking_request_count, len(api.ranking_requests))

    def test_empty_search_never_requests_market_snapshot(self) -> None:
        api = Mock()
        dialog = self._dialog(market_snapshot_api=api)
        dialog.btn_search.click()
        api.request_initial_market_snapshot.assert_not_called()
        api.request_stock_ranking_snapshot.assert_not_called()

    def test_geometry_matches_production_registration_dialog(self) -> None:
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            self.records,
        )
        production = self._production_dialog(snapshot)
        dialog = self._dialog(snapshot)

        self.assertEqual(420, production.height())
        self.assertEqual(production.height(), dialog.height())
        production_header = production.result_table.horizontalHeader()
        validation_header = dialog.result_table.horizontalHeader()
        self.assertFalse(production_header.stretchLastSection())
        self.assertFalse(validation_header.stretchLastSection())
        for column in range(len(STOCK_BROWSER_HEADERS)):
            with self.subTest(column=column):
                self.assertEqual(QHeaderView.Fixed, production_header.sectionResizeMode(column))
                self.assertEqual(QHeaderView.Fixed, validation_header.sectionResizeMode(column))
                self.assertEqual(
                    production_header.sectionSize(column),
                    validation_header.sectionSize(column),
                )
        self.assertEqual(
            Qt.ScrollBarAlwaysOff,
            dialog.result_table.horizontalScrollBarPolicy(),
        )
        self.assertEqual(
            Qt.ScrollBarAlwaysOn,
            dialog.result_table.verticalScrollBarPolicy(),
        )
        self.assertEqual(
            production.result_table.verticalHeader().width(),
            dialog.result_table.verticalHeader().width(),
        )
        self.assertEqual(
            production._result_table_required_width(),
            stock_browser_table_required_width(dialog.result_table),
        )
        self.assertEqual(production.width(), dialog.width())

    def test_search_badge_and_table_presentation_matches_production(self) -> None:
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            self.records,
        )
        production = self._production_dialog(snapshot)
        dialog = self._dialog(snapshot)

        self.assertEqual(3, production.layout().count())
        self.assertEqual(3, dialog.layout().count())
        self.assertEqual(production.layout().spacing(), dialog.layout().spacing())
        self.assertEqual(production.layout().contentsMargins(), dialog.layout().contentsMargins())
        self.assertEqual(production.search_input.objectName(), dialog.search_input.objectName())
        self.assertEqual(production.search_input.placeholderText(), dialog.search_input.placeholderText())
        self.assertEqual(production.search_input.styleSheet(), dialog.search_input.styleSheet())
        self.assertEqual(production.search_input.size(), dialog.search_input.size())
        self.assertEqual(
            production.search_input.sizePolicy(),
            dialog.search_input.sizePolicy(),
        )
        self.assertEqual(
            production.ranking_title_label.styleSheet(),
            dialog.ranking_title_label.styleSheet(),
        )
        self.assertEqual(
            production.ranking_title_label.sizePolicy(),
            dialog.ranking_title_label.sizePolicy(),
        )
        self.assertEqual(
            production.general_stock_button.size(),
            dialog.general_stock_button.size(),
        )
        self.assertEqual(
            production.general_stock_button.styleSheet(),
            dialog.general_stock_button.styleSheet(),
        )
        for source, _text in dialog.RANKING_BADGES:
            with self.subTest(source=source):
                self.assertEqual(
                    production.ranking_buttons[source].size(),
                    dialog.ranking_buttons[source].size(),
                )
                self.assertEqual(
                    production.ranking_buttons[source].styleSheet(),
                    dialog.ranking_buttons[source].styleSheet(),
                )

        production_table = production.result_table
        validation_table = dialog.result_table
        self.assertEqual(production_table.objectName(), validation_table.objectName())
        self.assertEqual(production_table.styleSheet(), validation_table.styleSheet())
        self.assertEqual(
            production_table.horizontalHeader().styleSheet(),
            validation_table.horizontalHeader().styleSheet(),
        )
        self.assertEqual(
            production_table.verticalScrollBar().styleSheet(),
            validation_table.verticalScrollBar().styleSheet(),
        )
        self.assertEqual(production_table.showGrid(), validation_table.showGrid())
        self.assertEqual(
            production_table.alternatingRowColors(),
            validation_table.alternatingRowColors(),
        )
        self.assertEqual(
            production_table.horizontalHeader().isSortIndicatorShown(),
            validation_table.horizontalHeader().isSortIndicatorShown(),
        )
        self.assertEqual(
            type(production_table.itemDelegateForColumn(production.NAME_COLUMN)),
            type(validation_table.itemDelegateForColumn(dialog.NAME_COLUMN)),
        )
        self.assertEqual(
            type(production_table.itemDelegateForColumn(production.MARKET_COLUMN)),
            type(validation_table.itemDelegateForColumn(dialog.MARKET_COLUMN)),
        )
        self.assertEqual(QAbstractItemView.ExtendedSelection, production_table.selectionMode())
        self.assertEqual(QAbstractItemView.SingleSelection, validation_table.selectionMode())

    def test_active_badges_and_ranking_highlight_match_production(self) -> None:
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            self.records,
        )
        production = self._production_dialog(snapshot)
        dialog = self._dialog(snapshot)
        production._populate_result_table([dict(self.records[0])], source="SEARCH")
        dialog._render_records((dict(self.records[0]),), source="SEARCH")

        production.general_stock_button.setChecked(True)
        dialog.general_stock_button.setChecked(True)
        self.assertEqual(
            production.general_stock_button.styleSheet(),
            dialog.general_stock_button.styleSheet(),
        )
        production._active_ranking_source = "VOLUME_TOP"
        dialog._active_ranking_source = "VOLUME_TOP"
        production._update_ranking_badge_styles()
        production._update_ranking_column_highlight()
        dialog._update_ranking_styles()
        self.assertEqual(
            production.ranking_buttons["VOLUME_TOP"].styleSheet(),
            dialog.ranking_buttons["VOLUME_TOP"].styleSheet(),
        )
        column = dialog.VOLUME_COLUMN
        for role in (Qt.ForegroundRole, Qt.BackgroundRole):
            self.assertEqual(
                production.result_table.horizontalHeaderItem(column).data(role),
                dialog.result_table.horizontalHeaderItem(column).data(role),
            )
            self.assertEqual(
                production.result_table.item(0, column).data(role),
                dialog.result_table.item(0, column).data(role),
            )

    def test_sample_row_presentation_matches_production(self) -> None:
        long_row = {
            "code": "005930",
            "name": "삼성전자매우긴종목이름표시검증",
            "market": "KOSPI",
            "classification": "일반종목",
            "nxt_available": True,
            "status": ("투자주의", "투자경고", "투자위험", "관리종목"),
        }
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            (long_row,),
        )
        production = self._production_dialog(snapshot)
        dialog = self._dialog(snapshot)
        production._populate_result_table([dict(long_row)], source="SEARCH")
        dialog._render_records((dict(long_row),), source="SEARCH")

        for column in (
            dialog.NAME_COLUMN,
            dialog.MARKET_COLUMN,
            dialog.STOCK_STATUS_COLUMN,
            dialog.CURRENT_PRICE_COLUMN,
        ):
            with self.subTest(column=column):
                production_item = production.result_table.item(0, column)
                validation_item = dialog.result_table.item(0, column)
                self.assertEqual(
                    production_item.textAlignment(),
                    validation_item.textAlignment(),
                )
                self.assertEqual(production_item.toolTip(), validation_item.toolTip())
                self.assertEqual(
                    production_item.foreground().color(),
                    validation_item.foreground().color(),
                )
        self.assertEqual(
            production.result_table.item(0, production.STOCK_STATUS_COLUMN).text(),
            dialog.result_table.item(0, dialog.STOCK_STATUS_COLUMN).text(),
        )

    def test_empty_shell_widget_rendering_matches_production(self) -> None:
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            self.records,
        )
        production = self._production_dialog(snapshot)
        dialog = self._dialog(snapshot)
        production.show()
        dialog.show()
        production.clearFocus()
        dialog.clearFocus()
        self.app.processEvents()

        self.assertEqual(production.result_table.geometry(), dialog.result_table.geometry())
        self.assertEqual(
            self._widget_edge_pixel_samples(production.search_input),
            self._widget_edge_pixel_samples(dialog.search_input),
        )
        for production_widget, validation_widget in (
            (production.general_stock_button, dialog.general_stock_button),
            (production.ranking_title_label, dialog.ranking_title_label),
            (production.result_table, dialog.result_table),
        ):
            with self.subTest(widget=production_widget.objectName()):
                self.assertEqual(
                    self._widget_image_bytes(production_widget),
                    self._widget_image_bytes(validation_widget),
                )

    def test_status_feedback_does_not_add_a_layout_row(self) -> None:
        invalid = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_NOT_SYNCED,
            STOCK_LIBRARY_EMPTY_SOURCE,
            (),
            "RUNTIME_SYNC_STATE_UNAVAILABLE",
        )
        dialog = self._dialog(invalid)
        self.assertEqual(3, dialog.layout().count())
        self.assertTrue(dialog.status_label.isHidden())
        self.assertEqual(
            "종목 라이브러리를 사용할 수 없습니다.",
            dialog.status_label.text(),
        )
        self.assertEqual(dialog.status_label.text(), dialog.result_table.toolTip())

    def test_empty_result_and_ranking_failure_feedback_do_not_add_rows(self) -> None:
        dialog = self._dialog(market_snapshot_api=object())
        self._search(dialog, "없는종목")
        self.assertEqual("검색 결과가 없습니다.", dialog.result_table.toolTip())
        dialog.ranking_buttons["VOLUME_TOP"].click()
        self.assertEqual("TOP100 정보를 사용할 수 없습니다.", dialog.result_table.toolTip())
        self.assertEqual(3, dialog.layout().count())
        self.assertTrue(dialog.status_label.isHidden())

    def test_picker_has_no_mutation_network_or_execution_imports(self) -> None:
        forbidden = (
            "append_base_stock",
            "register_stock",
            "refresh_main",
            "market_data",
            "order_queue",
            "sendorder",
            "chejan",
            "mock_validation",
        )
        source_root = Path(__file__).resolve().parents[1]
        for filename in (
            "gui_indicator_follow_validation_stock_picker.py",
            "gui_stock_library_browser.py",
        ):
            source = (source_root / filename).read_text(encoding="utf-8-sig")
            lowered = source.lower()
            for fragment in forbidden:
                with self.subTest(filename=filename, fragment=fragment):
                    self.assertNotIn(fragment, lowered)

            tree = ast.parse(source)
            imported_modules = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            imported_modules.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            self.assertNotIn("kiwoom_api", imported_modules)
            self.assertFalse(any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "KiwoomApi"
                for node in ast.walk(tree)
            ))
        picker_source = (
            Path(__file__).resolve().parents[1]
            / "gui_indicator_follow_validation_stock_picker.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("#2563EB", picker_source)

    def test_registration_dialog_uses_same_read_only_browser_contract(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "gui_auto_trade_setting_window.py"
        source = source_path.read_text(encoding="utf-8-sig")
        self.assertIn("from gui_stock_library_browser import", source)
        self.assertIn("filter_stock_library_records", source)
        self.assertIn("stock_browser_display_values", source)
        self.assertIn("STOCK_BROWSER_HEADERS", source)


if __name__ == "__main__":
    unittest.main()
