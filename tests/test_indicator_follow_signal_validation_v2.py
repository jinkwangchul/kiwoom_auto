# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

import gui_indicator_follow_routine_settings_dialog as dialog_module
from gui_indicator_follow_signal_validation_flow import (
    DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT,
    IndicatorFollowSignalValidationFlow,
)
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
    _time_axis_label_records,
    estimated_signal_return_percent,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_signal_validation_snapshot,
    project_signal_validation_rules,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
    ValidationReplayEntry,
    ValidationReplayResult,
    ValidationReplaySnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


class _FakeBroker:
    def __init__(self, connected=True):
        self.connected = connected
        self.connection_checks = 0

    def is_connected(self):
        self.connection_checks += 1
        return self.connected


class _FakeHost(QObject):
    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(self, stock):
        super().__init__()
        self.stock = stock
        self.started = []

    def start(self, snapshot, *, ui_parent=None):
        self.started.append((snapshot, ui_parent))
        session = ValidationSession(
            ValidationRequest(
                self.stock,
                snapshot,
                snapshot.to_dict()["bar"]["bar_minutes"],
            ),
            operation_active_reader=lambda: False,
        )
        self.validation_session_ready.emit(session)
        return session


class _FakeWindow(QDialog):
    validation_run_requested = pyqtSignal(object)
    settings_apply_requested = pyqtSignal(object)

    def __init__(self, stock, seed, parent=None):
        super().__init__(parent)
        self.stock = stock
        self.seed = seed
        self.snapshots = []
        self.errors = []
        self.initial_requests = 0
        self.apply_results = []
        self.historical_candle_count = None

    def set_historical_candle_count(self, count):
        self.historical_candle_count = count

    def set_replay_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def show_validation_error(self, message):
        self.errors.append(message)

    def request_initial_validation(self):
        self.initial_requests += 1

    def show_settings_apply_result(self, message, *, success):
        self.apply_results.append((message, success))


class IndicatorFollowSignalValidationV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.rules = json.loads(
            (cls.routine_dir / "rules.json").read_text(encoding="utf-8")
        )
        cls.ui_state = cls.rules["indicator_follow_ui_state"]["state"]
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _seed(self, rules=None, ui_state=None):
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(rules or self.rules),
            ui_state or self.ui_state,
        )

    def _window(self, ui_state=None):
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                self._seed(ui_state=ui_state),
            )
        self.widgets.append(window)
        return window

    @staticmethod
    def _entry(side, index, signal=None):
        return ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=f"2026091114{index:02d}00",
            signal=signal,
            reason="fixture",
            signal_index=index if signal else None,
            signal_time=f"2026091114{index:02d}00" if signal else None,
            delay_bar=0,
            matched_groups=[],
            details=[],
            trace={"conditions": [], "groups": [], "aggregations": []},
        )

    def _replay_snapshot(self, entries, closes=(100.0, 120.0, 150.0)):
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
            for index, close in enumerate(closes)
        ]
        return ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="hash",
            historical_request_id="REQUEST",
            evaluated_start_index=0,
            evaluated_end_index=len(candles) - 1,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )

    def test_entry_button_is_separate_and_preserves_existing_button(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            for mode in ("registration", "edit"):
                kwargs = dict(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode=mode,
                )
                if mode == "edit":
                    kwargs["instance_id"] = "INSTANCE-V2"
                with patch.object(dialog_module.QTimer, "singleShot"):
                    dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(**kwargs)
                self.widgets.append(dialog)
                old_payloads = []
                new_payloads = []
                dialog.validation_chart_requested.connect(old_payloads.append)
                dialog.signal_validation_requested.connect(new_payloads.append)
                dialog.buy_signal_expr_line.setText("A or D")
                dialog.signal_validation_button.click()
                self.assertEqual("검증차트", dialog.validation_chart_button.text())
                self.assertEqual("검증차트2", dialog.signal_validation_button.text())
                self.assertEqual([], old_payloads)
                self.assertEqual(1, len(new_payloads))
                self.assertIsInstance(new_payloads[0], IndicatorFollowSignalValidationSeed)
                self.assertEqual(
                    "A or D",
                    new_payloads[0].to_ui_state()["basic"]["buy_signal_expr_line"],
                )
                signal_bar = new_payloads[0].settings_snapshot.to_dict()["bar"]
                self.assertEqual(self.rules["bar"]["buy_delay_bar"], signal_bar["buy_delay_bar"])
                self.assertEqual(self.rules["bar"]["sell_delay_bar"], signal_bar["sell_delay_bar"])

    def test_seed_and_signal_only_projection_are_detached_and_strict(self):
        source = {
            "bar": {"bar_minutes": 5},
            "principle": {"signal_only": True, "execution_enabled": True},
            "buy": {
                "groups": [{"conditions": [{"target": "CLOSE"}]}],
                "filters": {
                    "rsi": {"enabled": True},
                    "price_compare": {"enabled": True},
                },
                "execution": {"base": {"order_type": "MARKET"}},
            },
            "sell": {
                "method": {"selected_sets": ["A"]},
                "signals": {
                    "macd_sell": {
                        "groups": [{
                            "conditions": [
                                {"target": "MACD"},
                                {"target": "ORDER_PRICE"},
                                {"compare_target": "AVG_PRICE"},
                            ]
                        }]
                    },
                    "profit_rate_sell": {"enabled": True},
                },
            },
            "buy_management": {"enabled": True},
            "order_policy": {"enabled": True},
        }
        ui_state = deepcopy(self.ui_state)
        seed = self._seed(source, ui_state)
        ui_state["basic"]["buy_signal_expr_line"] = "changed"
        self.assertNotEqual("changed", seed.to_ui_state()["basic"]["buy_signal_expr_line"])
        self.assertEqual({"signal_filter"}, set(seed.to_ui_state()["buy_ui"]))
        self.assertNotIn("execution", seed.settings_snapshot.to_dict()["buy"])

        projected = project_signal_validation_rules(source, ui_state=seed.to_ui_state())
        self.assertNotIn("execution", projected["buy"])
        self.assertNotIn("price_compare", projected["buy"]["filters"])
        self.assertNotIn("method", projected["sell"])
        self.assertNotIn("profit_rate_sell", projected["sell"]["signals"])
        conditions = projected["sell"]["signals"]["macd_sell"]["groups"][0]["conditions"]
        self.assertEqual([{"target": "MACD"}], conditions)
        self.assertNotIn("buy_management", projected)
        self.assertNotIn("order_policy", projected)

    def test_v2_uses_shared_signal_controls_without_execution_widgets(self):
        ui_state = deepcopy(self.ui_state)
        ui_state["basic"]["buy_signal_expr_line"] = "A or D"
        ui_state["buy_ui"]["signal_filter"]["buy_rsi_value_line"] = "37"
        window = self._window(ui_state)
        self.assertEqual("A or D", window.buy_signal_expr_line.text())
        self.assertEqual("37", window.buy_rsi_value_line.text())
        for name in (
            "buy_overview_method",
            "buy_overview_method_extra",
            "buy_overview_finish",
            "sell_method_select_a_check",
            "sell_overview_scenario",
            "sell_signal_condition_a_gap_check",
            "sell_signal_condition_b_gap_check",
            "sell_signal_condition_c_gap_check",
        ):
            self.assertFalse(hasattr(window, name), name)
        original_state = ui_state["basic"]["buy_signal_expr_line"]
        window.buy_signal_expr_line.setText("B and C")
        self.assertEqual(original_state, ui_state["basic"]["buy_signal_expr_line"])
        self.assertEqual("A or D", window._signal_validation_seed.to_ui_state()["basic"]["buy_signal_expr_line"])

    def test_v2_snapshot_runs_through_actual_historical_replay_evaluator(self):
        window = self._window()
        run_request = window._request_validation()
        snapshot = run_request.settings_snapshot
        request = ValidationRequest(
            self.stock,
            snapshot,
            snapshot.to_dict()["bar"]["bar_minutes"],
        )
        session = ValidationSession(request, operation_active_reader=lambda: False)
        rows = []
        for index, close in reversed(list(enumerate((100, 101, 102, 101, 103, 104, 102, 105)))):
            rows.append({
                "체결시간": f"2026091309{index:02d}00",
                "시가": str(close),
                "고가": str(close + 1),
                "저가": str(close - 1),
                "현재가": str(close),
                "거래량": "100",
            })
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=request.timeframe_minutes,
            requested_count=len(rows),
            request_id="V2-ACTUAL-EVALUATOR",
            rows=rows,
        )
        result = ValidationHistoricalReplay(session).evaluate(historical)
        self.assertTrue(result.ok, result)
        self.assertEqual(len(rows) * 2, len(result.snapshot.to_entries()))

    def test_fresh_window_snapshot_uses_current_timeframe_and_signal_values(self):
        window = self._window()
        emitted = []
        window.validation_run_requested.connect(emitted.append)
        window.basic_signal_interval_combo.setCurrentText("15")
        window.historical_candle_count_spin.setValue(500)
        window.buy_rsi_value_line.setText("33")
        run_request = window._request_validation()
        self.assertIsInstance(run_request, IndicatorFollowSignalValidationRunRequest)
        self.assertEqual([run_request], emitted)
        self.assertEqual(500, run_request.candle_count)
        snapshot = run_request.settings_snapshot
        rules = snapshot.to_dict()
        self.assertEqual(15, rules["bar"]["bar_minutes"])
        self.assertNotIn("execution", rules["buy"])
        self.assertNotIn("price_compare", rules["buy"].get("filters", {}))
        self.assertNotIn("method", rules["sell"])
        self.assertNotIn("profit_rate_sell", rules["sell"]["signals"])

    def test_time_axis_labels_use_only_real_candle_times_and_range_format(self):
        def candles(times):
            return [{"time": value, "close": 100} for value in times]

        intraday = _time_axis_label_records(candles([
            f"2026091310{minute:02d}00" for minute in range(20)
        ]))
        self.assertTrue(8 <= len(intraday) <= 12)
        self.assertEqual("10:00", intraday[0]["label"])
        self.assertEqual("10:19", intraday[-1]["label"])

        multi_day = _time_axis_label_records(candles([
            "20260913090000", "20260914090000", "20260915090000"
        ]))
        self.assertIn("09/14", {record["label"].split()[0] for record in multi_day})

        long_range = _time_axis_label_records(candles([
            "20260101090000", "20260501090000", "20270101090000"
        ]))
        self.assertTrue(all(len(record["label"].split("/")[0]) == 4 for record in long_range))
        source_times = {item["time"] for item in candles([
            "20260101090000", "20260501090000", "20270101090000"
        ])}
        self.assertTrue(all(record["time"] in source_times for record in long_range))

    def test_estimated_return_uses_all_earlier_buys_and_latest_sell(self):
        snapshot = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("BUY", 1, "BUY"),
            self._entry("SELL", 2, "SELL"),
        ])
        self.assertAlmostEqual((150.0 - 110.0) / 110.0 * 100.0, estimated_signal_return_percent(snapshot))
        self.assertIsNone(estimated_signal_return_percent(self._replay_snapshot([self._entry("SELL", 2, "SELL")])))
        self.assertIsNone(estimated_signal_return_percent(self._replay_snapshot([self._entry("BUY", 0, "BUY")])))
        invalid = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 2, "SELL"),
        ], closes=(0.0, 120.0, 150.0))
        self.assertIsNone(estimated_signal_return_percent(invalid))

    def test_result_chart_markers_selection_trace_and_estimate_are_real(self):
        window = self._window()
        buy = self._entry("BUY", 0, "BUY")
        sell = ValidationReplayEntry(
            evaluation_side="SELL",
            evaluation_index=2,
            evaluation_time="20260911140200",
            signal="SELL",
            reason="actual-reason",
            signal_index=1,
            signal_time="20260911140100",
            delay_bar=1,
            matched_groups=["C"],
            details=["actual-detail"],
            trace={
                "conditions": [{
                    "path": "sell.signals.ui_condition_c.groups[0].conditions[0]",
                    "condition_type": "actual-condition",
                    "operator": "TURN_DOWN",
                    "left_operand": {"value": 3.0},
                    "right_operand": {"value": None},
                    "raw_result": True,
                    "final_result": True,
                    "indicator_snapshots": [],
                }],
                "groups": [{"group": "C"}],
                "aggregations": [{"result": True}],
            },
        )
        snapshot = self._replay_snapshot([buy, sell])
        window.set_replay_snapshot(snapshot)
        self.assertEqual(1, window.canvas.marker_count("BUY"))
        self.assertEqual(1, window.canvas.marker_count("SELL"))
        self.assertEqual(2, window.selected_evaluation_index)
        self.assertIn("SELL reason: actual-reason", window.selection_summary.toPlainText())
        self.assertIn("SELL signal_time: 2026-09-11 14:01", window.selection_summary.toPlainText())
        table_text = " ".join(
            window.filter_result_table.item(row, column).text()
            for row in range(window.filter_result_table.rowCount())
            for column in range(window.filter_result_table.columnCount())
            if window.filter_result_table.item(row, column) is not None
        )
        self.assertIn("actual-condition", table_text)
        self.assertIn("+50.00%", window.estimated_return_label.text())

    def test_auth_is_fresh_and_default_host_is_the_existing_picker_host(self):
        broker = _FakeBroker(False)
        host = _FakeHost(self.stock)
        created = []

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            window_factory=window_factory,
        )
        # Use a real QObject signal carrier to exercise bind_dialog.
        class Carrier(QDialog):
            signal_validation_requested = pyqtSignal(object)
        carrier = Carrier()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        with patch("gui_indicator_follow_signal_validation_flow.show_toast") as toast:
            carrier.signal_validation_requested.emit(self._seed())
            toast.assert_called_once_with(
                carrier,
                "키움 서버에 로그인되어 있지 않습니다.",
                duration_ms=2500,
            )
        self.assertEqual([], host.started)
        broker.connected = True
        carrier.signal_validation_requested.emit(self._seed())
        self.assertEqual(2, broker.connection_checks)
        self.assertEqual(1, len(host.started))
        self.assertEqual(1, len(created))
        self.assertIsNone(created[0].parentWidget())

        created[0].close()
        QTest.qWait(0)
        self.app.processEvents()
        self.assertEqual((), flow.open_windows)

        default_flow = IndicatorFollowSignalValidationFlow(broker)
        self.assertEqual(
            "IndicatorFollowValidationStockPicker",
            default_flow.host._stock_picker_factory.__name__,
        )

    def test_each_run_uses_its_snapshot_timeframe_and_updates_real_replay_result(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        created = []
        sessions = []
        requested_counts = []

        class Provider:
            def __init__(_self, session, requester):
                sessions.append(session)
                _self.session = session

            def request_latest(_self, count, callback):
                requested_counts.append(count)
                request = _self.session.request
                callback(ValidationHistoricalResult(
                    True,
                    snapshot=ValidationHistoricalSnapshot(
                        stock=request.stock,
                        timeframe_minutes=request.timeframe_minutes,
                        requested_count=count,
                        request_id=f"REQ-{request.timeframe_minutes}",
                        rows=[{
                            "체결시간": "20260911143000",
                            "시가": "100",
                            "고가": "101",
                            "저가": "99",
                            "현재가": "100",
                            "거래량": "1",
                        }],
                    ),
                ))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical):
                request = _self.session.request
                return ValidationReplayResult(True, snapshot=ValidationReplaySnapshot(
                    stock=request.stock,
                    timeframe_minutes=request.timeframe_minutes,
                    settings_hash=request.settings_snapshot.rules_hash,
                    historical_request_id=historical.request_id,
                    evaluated_start_index=0,
                    evaluated_end_index=0,
                    dropped_raw_rows_count=0,
                    candles=[{
                        "time": "20260911143000",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 1,
                    }],
                    entries=[],
                ))

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_provider_factory=Provider,
            replay_factory=Replay,
            window_factory=window_factory,
        )
        carrier = type("Carrier", (QDialog,), {"signal_validation_requested": pyqtSignal(object)})()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        carrier.signal_validation_requested.emit(self._seed())
        window = created[0]
        self.assertEqual(DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT, window.historical_candle_count)
        for timeframe, candle_count in ((3, 300), (15, 500)):
            rules = deepcopy(self.rules)
            rules["bar"]["bar_minutes"] = timeframe
            window.validation_run_requested.emit(
                IndicatorFollowSignalValidationRunRequest(
                    build_signal_validation_snapshot(rules, ui_state=self.ui_state),
                    candle_count,
                )
            )
        self.assertEqual([3, 15], [session.request.timeframe_minutes for session in sessions])
        self.assertEqual([300, 500], requested_counts)
        self.assertEqual([3, 15], [snapshot.timeframe_minutes for snapshot in window.snapshots])

    def test_source_boundaries_do_not_create_broker_or_mutation_paths(self):
        source = (self.project_root / "gui_indicator_follow_signal_validation_flow.py").read_text(encoding="utf-8")
        self.assertNotIn("KiwoomApi(", source)
        self.assertNotIn("request_minute_candles(", source)
        for forbidden in ("SendOrder", "Chejan", "StockRepository", "mock_validation"):
            self.assertNotIn(forbidden, source)
        for caller_name in ("gui_windows.py", "gui_auto_trade_setting_window.py"):
            caller = (self.project_root / caller_name).read_text(encoding="utf-8")
            self.assertIn("bind_indicator_follow_validation_flow", caller)
            self.assertIn("bind_indicator_follow_signal_validation_flow", caller)


if __name__ == "__main__":
    unittest.main()
