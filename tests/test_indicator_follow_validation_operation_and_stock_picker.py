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

from PyQt5.QtWidgets import QApplication, QDialog

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
            {"code": "005930", "name": "삼성전자"},
            {"code": "000660", "name": "SK하이닉스"},
            {"code": "035420", "name": "NAVER"},
        )

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()

    def _dialog(self, snapshot=None):
        if snapshot is None:
            snapshot = StockLibraryLoadSnapshot(
                STOCK_LIBRARY_READY,
                STOCK_LIBRARY_RUNTIME_SOURCE,
                self.records,
            )
        loader = Mock(return_value=snapshot)
        dialog = IndicatorFollowValidationStockPicker(snapshot_loader=loader)
        self.dialogs.append(dialog)
        loader.assert_called_once_with(None)
        return dialog

    @staticmethod
    def _visible_codes(dialog) -> list[str]:
        return [
            dialog.stock_table.item(row, 0).text()
            for row in range(dialog.stock_table.rowCount())
        ]

    def test_valid_snapshot_loads_and_filters_by_code_and_name(self) -> None:
        dialog = self._dialog()
        self.assertEqual(["005930", "000660", "035420"], self._visible_codes(dialog))

        dialog.search_input.setText("0006")
        self.assertEqual(["000660"], self._visible_codes(dialog))
        dialog.search_input.setText("삼성")
        self.assertEqual(["005930"], self._visible_codes(dialog))

    def test_one_selection_returns_validation_stock_ref(self) -> None:
        dialog = self._dialog()
        dialog.stock_table.selectRow(1)
        dialog.select_button.click()

        self.assertEqual(QDialog.Accepted, dialog.result())
        self.assertEqual(
            ValidationStockRef("000660", "SK하이닉스"),
            dialog.selected_stock,
        )

    def test_cancel_returns_no_selection(self) -> None:
        dialog = self._dialog()
        dialog.stock_table.selectRow(0)
        dialog.cancel_button.click()

        self.assertEqual(QDialog.Rejected, dialog.result())
        self.assertIsNone(dialog.selected_stock)

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
            dialog.search_input.setText("NAVER")
            dialog.stock_table.selectRow(0)
            dialog.select_button.click()
        self.assertEqual(ValidationStockRef("035420", "NAVER"), dialog.selected_stock)

    def test_picker_has_no_mutation_network_or_execution_imports(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "gui_indicator_follow_validation_stock_picker.py"
        )
        source = source_path.read_text(encoding="utf-8-sig")
        forbidden = (
            "append_base_stock",
            "register_stock",
            "refresh_main",
            "kiwoom_api",
            "market_data",
            "order_queue",
            "sendorder",
            "chejan",
            "mock_validation",
        )
        lowered = source.lower()
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, lowered)


if __name__ == "__main__":
    unittest.main()
