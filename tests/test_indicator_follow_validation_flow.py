# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from gui_indicator_follow_validation_flow import (
    DEFAULT_VALIDATION_HISTORICAL_COUNT,
    IndicatorFollowValidationFlow,
    bind_indicator_follow_validation_flow,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayResult,
    ValidationReplaySnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


class _FakeDialog(QObject):
    validation_chart_requested = pyqtSignal(object)

    def __init__(self, settings_mode: str = "registration") -> None:
        super().__init__()
        self.settings_mode = settings_mode


class _FakeHost(QObject):
    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(self, *, block_reason: str | None = None) -> None:
        super().__init__()
        self.block_reason = block_reason
        self.started = []

    def start(self, snapshot) -> None:
        self.started.append(snapshot)
        if self.block_reason:
            self.validation_blocked.emit(self.block_reason)


class _FakeBroker:
    def __init__(self) -> None:
        self.calls = []
        self.callback = None
        self.request_minute_candles = Mock()

    def request_minute_candles_read_only(
        self,
        code,
        name,
        *,
        interval,
        count,
        callback,
    ):
        self.calls.append(
            {
                "code": code,
                "name": name,
                "interval": interval,
                "count": count,
                "callback": callback,
            }
        )
        self.callback = callback
        return {"ok": True, "status": "REQUESTED"}

    def complete(self, response) -> None:
        if not callable(self.callback):
            raise AssertionError("historical callback is unavailable")
        self.callback(response)


class _FakeChart(QDialog):
    def __init__(self, snapshot, parent=None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot
        self.show_calls = 0

    def show(self) -> None:
        self.show_calls += 1
        super().show()


class IndicatorFollowValidationFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.settings = ValidationSettingsSnapshot(
            {
                "bar": {"bar_minutes": 3},
                "enabled": True,
                "buy": {"groups": []},
                "sell": {"signals": {}},
            }
        )
        self.stock = ValidationStockRef("005930", "삼성전자")
        self.request = ValidationRequest(self.stock, self.settings, 3)
        self.session = ValidationSession(
            self.request,
            operation_active_reader=Mock(return_value=False),
        )
        self.broker = _FakeBroker()

    @staticmethod
    def _rows():
        return [
            {
                "체결시간": "20260911143000",
                "시가": "100",
                "고가": "102",
                "저가": "99",
                "현재가": "101",
                "거래량": "1000",
            },
            {
                "체결시간": "20260911142900",
                "시가": "99",
                "고가": "101",
                "저가": "98",
                "현재가": "100",
                "거래량": "900",
            },
            {
                "체결시간": "20260911142800",
                "시가": "98",
                "고가": "100",
                "저가": "97",
                "현재가": "99",
                "거래량": "800",
            },
        ]

    def _historical_response(self, *, ok=True):
        rows = self._rows()
        if not ok:
            return {"ok": False, "error": "fixture failure"}
        return {
            "ok": True,
            "type": "minute_candles",
            "request_id": "REQUEST-1",
            "code": self.stock.code,
            "name": self.stock.name,
            "interval": 3,
            "rows": rows,
            "rows_count": len(rows),
        }

    def _replay_snapshot(self):
        candles = [
            {
                "time": "20260911142800",
                "open": 98.0,
                "high": 100.0,
                "low": 97.0,
                "close": 99.0,
                "volume": 800.0,
            },
            {
                "time": "20260911142900",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 900.0,
            },
            {
                "time": "20260911143000",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000.0,
            },
        ]
        return ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            settings_hash=self.settings.rules_hash,
            historical_request_id="REQUEST-1",
            evaluated_start_index=0,
            evaluated_end_index=2,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=[],
        )

    def test_dialog_signal_is_forwarded_to_host_start(self) -> None:
        host = _FakeHost()
        flow = IndicatorFollowValidationFlow(self.broker, host=host)
        dialog = _FakeDialog()

        self.assertTrue(flow.bind_dialog(dialog))
        dialog.validation_chart_requested.emit(self.settings)

        self.assertEqual([self.settings], host.started)

    def test_host_block_forwards_failure_without_broker_or_chart(self) -> None:
        host = _FakeHost(block_reason="OPERATION_ACTIVE")
        charts = Mock()
        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            chart_factory=charts,
        )
        failures = []
        flow.validation_failed.connect(failures.append)
        dialog = _FakeDialog()
        flow.bind_dialog(dialog)

        dialog.validation_chart_requested.emit(self.settings)

        self.assertEqual(["OPERATION_ACTIVE"], failures)
        self.assertEqual([], self.broker.calls)
        charts.assert_not_called()

    def test_ready_session_uses_only_injected_read_only_requester(self) -> None:
        host = _FakeHost()
        flow = IndicatorFollowValidationFlow(self.broker, host=host)

        host.validation_session_ready.emit(self.session)

        self.assertEqual(1, len(self.broker.calls))
        call = self.broker.calls[0]
        self.assertEqual("005930", call["code"])
        self.assertEqual("삼성전자", call["name"])
        self.assertEqual(3, call["interval"])
        self.assertEqual(DEFAULT_VALIDATION_HISTORICAL_COUNT, call["count"])
        self.broker.request_minute_candles.assert_not_called()

    def test_historical_failure_blocks_replay_and_chart(self) -> None:
        host = _FakeHost()
        replay_factory = Mock()
        chart_factory = Mock()
        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            replay_factory=replay_factory,
            chart_factory=chart_factory,
        )
        failures = []
        flow.validation_failed.connect(failures.append)
        host.validation_session_ready.emit(self.session)

        self.broker.complete(self._historical_response(ok=False))

        replay_factory.assert_not_called()
        chart_factory.assert_not_called()
        self.assertEqual(1, len(failures))
        self.assertIn("HISTORICAL", failures[0])

    def test_replay_failure_blocks_chart(self) -> None:
        host = _FakeHost()
        replay = Mock()
        replay.evaluate.return_value = ValidationReplayResult(
            False,
            reason="REPLAY_FAILED",
        )
        chart_factory = Mock()
        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            replay_factory=Mock(return_value=replay),
            chart_factory=chart_factory,
        )
        failures = []
        flow.validation_failed.connect(failures.append)
        host.validation_session_ready.emit(self.session)

        self.broker.complete(self._historical_response())

        replay.evaluate.assert_called_once()
        chart_factory.assert_not_called()
        self.assertEqual(["REPLAY: REPLAY_FAILED"], failures)

    def test_success_creates_and_shows_exactly_one_modeless_chart(self) -> None:
        host = _FakeHost()
        replay_snapshot = self._replay_snapshot()
        replay = Mock()
        replay.evaluate.return_value = ValidationReplayResult(
            True,
            snapshot=replay_snapshot,
        )
        created = []

        def chart_factory(snapshot, parent=None):
            chart = _FakeChart(snapshot, parent)
            created.append(chart)
            return chart

        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            replay_factory=Mock(return_value=replay),
            chart_factory=chart_factory,
        )
        host.validation_session_ready.emit(self.session)

        self.broker.complete(self._historical_response())

        self.assertEqual(1, len(created))
        self.assertEqual(1, created[0].show_calls)
        self.assertFalse(created[0].isModal())
        self.assertEqual((created[0],), flow.open_charts)
        self.addCleanup(created[0].close)

    def test_destroyed_chart_reference_is_removed(self) -> None:
        host = _FakeHost()
        replay = Mock()
        replay.evaluate.return_value = ValidationReplayResult(
            True,
            snapshot=self._replay_snapshot(),
        )
        created = []

        def chart_factory(snapshot, parent=None):
            chart = _FakeChart(snapshot, parent)
            created.append(chart)
            return chart

        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            replay_factory=Mock(return_value=replay),
            chart_factory=chart_factory,
        )
        host.validation_session_ready.emit(self.session)
        self.broker.complete(self._historical_response())
        chart = created[0]
        self.assertEqual(1, len(flow.open_charts))

        chart.close()
        QTest.qWait(0)
        self.app.processEvents()

        self.assertEqual((), flow.open_charts)

    def test_unsaved_settings_identity_stays_in_same_session_through_replay(self) -> None:
        host = _FakeHost()
        observed = {}

        class Replay:
            def __init__(_self, session):
                observed["session"] = session

            def evaluate(_self, historical_snapshot):
                observed["historical"] = historical_snapshot
                return ValidationReplayResult(False, reason="fixture stop")

        flow = IndicatorFollowValidationFlow(
            self.broker,
            host=host,
            replay_factory=Replay,
        )
        host.validation_session_ready.emit(self.session)
        self.broker.complete(self._historical_response())

        self.assertIs(self.session, observed["session"])
        self.assertIs(self.settings, observed["session"].request.settings_snapshot)
        self.assertEqual(self.settings.rules_hash, observed["session"].request.settings_snapshot.rules_hash)
        self.assertEqual("REQUEST-1", observed["historical"].request_id)

    def test_registration_and_edit_dialogs_share_one_flow_path(self) -> None:
        host = _FakeHost()
        flow = IndicatorFollowValidationFlow(self.broker, host=host)
        registration = _FakeDialog("registration")
        edit = _FakeDialog("edit")
        flow.bind_dialog(registration)
        flow.bind_dialog(edit)

        registration.validation_chart_requested.emit(self.settings)
        edit.validation_chart_requested.emit(self.settings)

        self.assertEqual([self.settings, self.settings], host.started)

    def test_owner_binding_reuses_one_flow_and_existing_broker(self) -> None:
        owner = SimpleNamespace(kiwoom_api=self.broker)
        registration = _FakeDialog("registration")
        edit = _FakeDialog("edit")

        first = bind_indicator_follow_validation_flow(owner, registration)
        second = bind_indicator_follow_validation_flow(owner, edit)

        self.assertIs(first, second)
        self.assertIs(self.broker, first._broker)
        self.assertIs(first, owner._indicator_follow_validation_flow)

    def test_widget_owner_parents_host_stock_picker_and_chart(self) -> None:
        owner = QDialog()
        owner.kiwoom_api = self.broker
        dialog = _FakeDialog()
        self.addCleanup(owner.close)

        flow = bind_indicator_follow_validation_flow(owner, dialog)

        self.assertIs(owner, flow.host.parent())
        self.assertIs(owner, flow._chart_parent)

    def test_owner_failure_uses_existing_status_seam_only(self) -> None:
        status = Mock()
        owner = SimpleNamespace(kiwoom_api=self.broker, statusBarMessage=status)
        flow = bind_indicator_follow_validation_flow(owner, _FakeDialog())

        flow.validation_failed.emit("OPERATION_ACTIVE")

        status.assert_called_once_with("검증차트: OPERATION_ACTIVE")

    def test_caller_sources_bind_both_actual_dialog_creation_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        auto_source = (root / "gui_auto_trade_setting_window.py").read_text(
            encoding="utf-8-sig"
        )
        main_source = (root / "gui_windows.py").read_text(encoding="utf-8-sig")
        call = "bind_indicator_follow_validation_flow"
        self.assertGreaterEqual(auto_source.count(call), 2)
        self.assertGreaterEqual(main_source.count(call), 2)

    def test_flow_source_does_not_create_or_use_production_mutation_paths(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "gui_indicator_follow_validation_flow.py"
        )
        source = source_path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("KiwoomApi", called_names)
        self.assertNotIn("request_minute_candles", source)
        forbidden_imports = (
            "gui_market_data_host",
            "mock_validation",
            "order",
            "execution",
            "candle_manager",
            "stock_repository",
            "kiwoom_api",
        )
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        for module_name in imported_modules:
            for fragment in forbidden_imports:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
