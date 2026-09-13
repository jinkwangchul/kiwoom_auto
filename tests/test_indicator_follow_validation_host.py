# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog

from gui_indicator_follow_validation_host import (
    IndicatorFollowValidationHost,
    REASON_INVALID_SELECTED_STOCK,
    REASON_INVALID_SETTINGS_SNAPSHOT,
    REASON_INVALID_TIMEFRAME,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_session import (
    REASON_OPERATION_ACTIVE,
    REASON_OPERATION_ACTIVE_READER_ERROR,
    REASON_OPERATION_ACTIVE_READER_INVALID,
    REASON_OPERATION_ACTIVE_READER_UNAVAILABLE,
    ValidationSession,
)


class _FakePicker:
    def __init__(self, result: int, selected_stock=None) -> None:
        self._result = result
        self.selected_stock = selected_stock
        self.exec_calls = 0

    def exec_(self) -> int:
        self.exec_calls += 1
        return self._result


class IndicatorFollowValidationHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _snapshot(bar_minutes=3) -> ValidationSettingsSnapshot:
        return ValidationSettingsSnapshot(
            {
                "bar": {"bar_minutes": bar_minutes},
                "buy": {"expression": {"operator": "AND"}},
            }
        )

    @staticmethod
    def _host(reader, picker):
        factory = Mock(return_value=picker)
        host = IndicatorFollowValidationHost(
            operation_active_reader=reader,
            stock_picker_factory=factory,
        )
        blocked: list[str] = []
        ready: list[ValidationSession] = []
        host.validation_blocked.connect(blocked.append)
        host.validation_session_ready.connect(ready.append)
        return host, factory, blocked, ready

    def test_invalid_snapshot_blocks_before_picker(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        host, factory, blocked, ready = self._host(Mock(return_value=False), picker)

        result = host.start(object())

        self.assertIsNone(result)
        self.assertEqual([REASON_INVALID_SETTINGS_SNAPSHOT], blocked)
        self.assertEqual([], ready)
        factory.assert_not_called()

    def test_invalid_or_missing_bar_minutes_blocks_before_picker(self) -> None:
        snapshots = (
            ValidationSettingsSnapshot({}),
            ValidationSettingsSnapshot({"bar": {}}),
            self._snapshot(True),
            self._snapshot("3"),
            self._snapshot(0),
            self._snapshot(-1),
        )
        for snapshot in snapshots:
            with self.subTest(rules=snapshot.to_dict()):
                picker = _FakePicker(QDialog.Accepted)
                host, factory, blocked, ready = self._host(
                    Mock(return_value=False), picker
                )

                result = host.start(snapshot)

                self.assertIsNone(result)
                self.assertEqual([REASON_INVALID_TIMEFRAME], blocked)
                self.assertEqual([], ready)
                factory.assert_not_called()

    def test_operation_active_blocks_before_picker(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        reader = Mock(return_value=True)
        host, factory, blocked, ready = self._host(reader, picker)

        result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([REASON_OPERATION_ACTIVE], blocked)
        self.assertEqual([], ready)
        reader.assert_called_once_with()
        factory.assert_not_called()

    def test_operation_reader_error_blocks_before_picker(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        reader = Mock(side_effect=RuntimeError("unavailable"))
        host, factory, blocked, ready = self._host(reader, picker)

        result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([REASON_OPERATION_ACTIVE_READER_ERROR], blocked)
        self.assertEqual([], ready)
        factory.assert_not_called()

    def test_operation_reader_invalid_return_blocks_before_picker(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        host, factory, blocked, ready = self._host(Mock(return_value=1), picker)

        result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([REASON_OPERATION_ACTIVE_READER_INVALID], blocked)
        self.assertEqual([], ready)
        factory.assert_not_called()

    def test_missing_operation_reader_blocks_before_picker(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        host, factory, blocked, ready = self._host(None, picker)

        result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([REASON_OPERATION_ACTIVE_READER_UNAVAILABLE], blocked)
        self.assertEqual([], ready)
        factory.assert_not_called()

    def test_picker_cancel_returns_without_block_or_ready(self) -> None:
        picker = _FakePicker(QDialog.Rejected)
        reader = Mock(return_value=False)
        host, factory, blocked, ready = self._host(reader, picker)

        with patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
            result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([], blocked)
        self.assertEqual([], ready)
        reader.assert_called_once_with()
        factory.assert_called_once_with(None)
        self.assertEqual(1, picker.exec_calls)

    def test_read_only_preflight_does_not_open_picker_or_emit_session(self) -> None:
        picker = _FakePicker(QDialog.Accepted)
        reader = Mock(return_value=False)
        host, factory, blocked, ready = self._host(reader, picker)
        snapshot = self._snapshot(5)

        result = host.preflight_block_reason(snapshot)

        self.assertIsNone(result)
        reader.assert_called_once_with()
        factory.assert_not_called()
        self.assertEqual([], blocked)
        self.assertEqual([], ready)

    def test_valid_selection_builds_request_from_current_snapshot(self) -> None:
        selected = ValidationStockRef("005930", "삼성전자")
        picker = _FakePicker(QDialog.Accepted, selected)
        reader = Mock(side_effect=(False, False))
        host, factory, blocked, ready = self._host(reader, picker)
        snapshot = self._snapshot(7)

        result = host.start(snapshot)

        self.assertIsInstance(result, ValidationSession)
        self.assertIs(result.request.settings_snapshot, snapshot)
        self.assertEqual(snapshot.to_dict(), result.request.settings_snapshot.to_dict())
        self.assertEqual(selected, result.request.stock)
        self.assertEqual(7, result.request.timeframe_minutes)
        self.assertEqual([], blocked)
        self.assertEqual([result], ready)
        self.assertEqual(2, reader.call_count)
        factory.assert_called_once_with(None)

    def test_explicit_ui_parent_is_used_without_changing_host_lifetime_parent(
        self,
    ) -> None:
        owner = QDialog()
        requester = QDialog()
        self.addCleanup(owner.close)
        self.addCleanup(requester.close)
        selected = ValidationStockRef("005930", "삼성전자")
        picker = _FakePicker(QDialog.Accepted, selected)
        factory = Mock(return_value=picker)
        host = IndicatorFollowValidationHost(
            owner,
            operation_active_reader=Mock(side_effect=(False, False)),
            stock_picker_factory=factory,
        )

        result = host.start(self._snapshot(), ui_parent=requester)

        self.assertIsInstance(result, ValidationSession)
        self.assertIs(owner, host.parent())
        factory.assert_called_once_with(requester)

    def test_fresh_operation_recheck_blocks_after_picker(self) -> None:
        picker = _FakePicker(
            QDialog.Accepted,
            ValidationStockRef("005930", "삼성전자"),
        )
        reader = Mock(side_effect=(False, True))
        host, factory, blocked, ready = self._host(reader, picker)

        result = host.start(self._snapshot())

        self.assertIsNone(result)
        self.assertEqual([REASON_OPERATION_ACTIVE], blocked)
        self.assertEqual([], ready)
        self.assertEqual(2, reader.call_count)
        factory.assert_called_once_with(None)

    def test_invalid_selected_stock_fails_closed(self) -> None:
        invalid_selections = (
            None,
            object(),
            ValidationStockRef("", "삼성전자"),
            ValidationStockRef("005930", ""),
        )
        for selected in invalid_selections:
            with self.subTest(selected=selected):
                picker = _FakePicker(QDialog.Accepted, selected)
                reader = Mock(return_value=False)
                host, factory, blocked, ready = self._host(reader, picker)

                result = host.start(self._snapshot())

                self.assertIsNone(result)
                self.assertEqual([REASON_INVALID_SELECTED_STOCK], blocked)
                self.assertEqual([], ready)
                reader.assert_called_once_with()
                factory.assert_called_once_with(None)

    def test_host_imports_exclude_production_execution_and_mock(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "gui_indicator_follow_validation_host.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        forbidden = (
            "kiwoom_api",
            "gui_market_data_host",
            "gui_auto_trade_",
            "operation_policy_gate",
            "routine_signal_",
            "order",
            "execution",
            "send_order",
            "mock_validation",
        )
        for module_name in imported_modules:
            for fragment in forbidden:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
