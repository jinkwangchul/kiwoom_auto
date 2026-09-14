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

from PyQt5.QtCore import QObject, QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QScrollArea

import gui_indicator_follow_routine_settings_dialog as dialog_module
import gui_indicator_follow_signal_validation_flow as flow_module
from gui_indicator_follow_signal_validation_flow import (
    DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT,
    IndicatorFollowSignalValidationFlow,
)
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationChartCanvas,
    IndicatorFollowSignalValidationFixedPriceAxis,
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


class _SnapshotBroker(_FakeBroker):
    def __init__(self, connected=True):
        super().__init__(connected)
        self.market_snapshot_requests = []

    def request_initial_market_snapshot(self, stock_codes, *, callback=None):
        self.market_snapshot_requests.append((tuple(stock_codes), callback))
        return {
            "ok": True,
            "status": "ENQUEUED",
            "target_stock_codes": list(stock_codes),
            "batch_count": 1,
        }


class _FakeHost(QObject):
    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(self, stock):
        super().__init__()
        self.stock = stock
        self.started = []
        self.preflighted = []
        self.pickers = []
        self.picker_parents = []

    def preflight_block_reason(self, snapshot):
        self.preflighted.append(snapshot)
        return None

    def create_stock_picker(self, ui_parent=None):
        self.picker_parents.append(ui_parent)
        return self.pickers.pop(0)

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
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)

    def __init__(self, stock, seed, parent=None):
        super().__init__(parent)
        self.stock = stock
        self.seed = seed
        self.snapshots = []
        self.errors = []
        self.initial_requests = 0
        self.apply_results = []
        self.historical_candle_count = None
        self.validation_requests = 0
        self.recent_stock_projections = []
        self.stock_metadata_projections = []

    def set_historical_candle_count(self, count):
        self.historical_candle_count = count

    def set_replay_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def show_validation_error(self, message):
        self.errors.append(message)

    def request_initial_validation(self):
        self.initial_requests += 1

    def request_validation(self):
        self.validation_requests += 1
        self.validation_run_requested.emit(
            IndicatorFollowSignalValidationRunRequest(
                self.seed.settings_snapshot,
                self.historical_candle_count,
            )
        )

    def set_validation_stock(self, stock):
        if stock == self.stock:
            return False
        self.stock = stock
        self.snapshots.clear()
        return True

    def set_recent_stocks(self, stocks):
        self.recent_stock_projections.append(tuple(stocks))

    def set_stock_metadata(self, metadata):
        self.stock_metadata_projections.append(metadata)

    def show_settings_apply_result(self, message, *, success):
        self.apply_results.append((message, success))


class _FakePicker:
    def __init__(self, result, selected_stock=None):
        self._result = result
        self.selected_stock = selected_stock

    def exec_(self):
        return self._result


class _MemoryRecentStockStore:
    def __init__(self, stocks=(), metadata=None):
        self._stocks = tuple(stocks)
        self._metadata = dict(metadata or {})
        self.write_count = 0

    @property
    def recent_stocks(self):
        return tuple(self._stocks)

    def metadata_for(self, stock):
        if not isinstance(stock, ValidationStockRef):
            return None
        record = self._metadata.get(stock.code)
        return None if record is None else dict(record)

    def activate(self, stock):
        updated = (stock,) + tuple(
            candidate for candidate in self._stocks if candidate.code != stock.code
        )
        updated = updated[:15]
        if updated == self._stocks:
            return False
        self._stocks = updated
        self.write_count += 1
        return True


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
        resolved_state = deepcopy(ui_state or self.ui_state)
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = resolved_state["sell_ui"]["signal_conditions"][group_name]
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(rules or self.rules),
            resolved_state,
        )

    def _unresolved_seed(self, rules=None, ui_state=None):
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(rules or self.rules),
            deepcopy(ui_state or self.ui_state),
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
                for group_name in "abc":
                    getattr(
                        dialog,
                        f"sell_signal_condition_{group_name}_gap_left_combo",
                    ).setCurrentText("평단가")
                    getattr(
                        dialog,
                        f"sell_signal_condition_{group_name}_gap_right_combo",
                    ).setCurrentText("현재가")
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
        self.assertEqual({}, projected["sell"]["signals"])
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
        ):
            self.assertFalse(hasattr(window, name), name)
        for group_name in "abc":
            self.assertTrue(hasattr(window, f"sell_signal_condition_{group_name}_gap_check"))
            for side in ("left", "right"):
                combo = getattr(window, f"sell_signal_condition_{group_name}_gap_{side}_combo")
                self.assertEqual(["현재가", "평단가"], [combo.itemText(i) for i in range(combo.count())])
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

    def test_chart_price_axis_is_fixed_and_uses_five_scaled_ticks(self):
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": price,
                "high": price + 500,
                "low": price - 500,
                "close": price,
                "volume": 1,
            }
            for index, price in enumerate((249_500, 250_000, 249_750))
        ]
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(canvas)
        axis = IndicatorFollowSignalValidationFixedPriceAxis(scroll_area)
        axis.set_canvas(canvas)
        self.widgets.extend([scroll_area, axis])
        scroll_area.resize(640, 440)
        axis.resize(axis.width(), 440)
        scroll_area.show()
        axis.show()
        self.app.processEvents()
        canvas.resize(canvas.sizeHint().width(), 440)
        records = axis.price_axis_records()
        grid_records = canvas.grid_line_records()

        self.assertEqual(5, len(records))
        self.assertEqual(250_500.0, records[0]["price"])
        self.assertEqual(249_000.0, records[-1]["price"])
        self.assertEqual(249_750.0, records[2]["price"])
        self.assertEqual("250,500", records[0]["label"])
        self.assertEqual("249,000", records[-1]["label"])
        viewport_top = scroll_area.viewport().geometry().top()
        self.assertEqual(
            [viewport_top + record["y"] for record in grid_records],
            [record["y"] for record in records],
        )
        metrics = QFontMetrics(axis.font())
        self.assertGreaterEqual(
            axis.width(),
            max(metrics.horizontalAdvance(record["label"]) for record in records)
            + axis._HORIZONTAL_PADDING * 2,
        )
        self.assertEqual(12, canvas.plot_left)
        for index in range(len(candles)):
            self.assertEqual(
                index,
                canvas._nearest_candle_index(canvas._x_for_index(index)),
            )
        canvas.set_selected_index(1)
        canvas.render(QPixmap(canvas.size()))
        self.assertEqual(1, canvas.selected_index)

        expensive = IndicatorFollowSignalValidationChartCanvas(
            [
                {
                    "time": "20260911140000",
                    "open": 1_490_000,
                    "high": 1_500_000,
                    "low": 1_490_000,
                    "close": 1_500_000,
                    "volume": 1,
                }
            ],
            [],
        )
        expensive_scroll = QScrollArea()
        expensive_scroll.setWidget(expensive)
        expensive_axis = IndicatorFollowSignalValidationFixedPriceAxis(expensive_scroll)
        expensive_axis.set_canvas(expensive)
        self.widgets.extend([expensive, expensive_scroll, expensive_axis])
        self.assertEqual("1,500,000", expensive_axis.price_axis_records()[0]["label"])
        self.assertGreater(expensive_axis.width(), axis.width())

    def test_primary_action_requires_matching_validation_before_apply(self):
        window = self._window()
        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)

        self.assertEqual("검증 실행", window.primary_validation_action_button.text())
        self.assertIs(window.run_validation_button, window.primary_validation_action_button)
        self.assertFalse(hasattr(window, "apply_settings_button"))
        window.primary_validation_action_button.click()
        self.assertEqual(1, len(runs))
        self.assertEqual([], applies)
        window.set_replay_snapshot(
            self._replay_snapshot([
                self._entry("BUY", 0, "BUY"),
                self._entry("SELL", 2, "SELL"),
            ])
        )
        self.assertEqual("설정 반영", window.primary_validation_action_button.text())
        self.assertEqual([], applies)

        original_expression = window.buy_signal_expr_line.text()
        blocker = QSignalBlocker(window.buy_signal_expr_line)
        window.buy_signal_expr_line.setText("A or D")
        del blocker
        self.assertIsNone(window._request_settings_apply())
        self.assertEqual([], applies)
        self.assertEqual("검증 실행", window.primary_validation_action_button.text())

        window.buy_signal_expr_line.setText(original_expression)
        window._request_validation()
        window.set_replay_snapshot(self._replay_snapshot([]))
        window.primary_validation_action_button.click()
        self.assertEqual(1, len(applies))
        window.show_settings_apply_result("설정 반영 완료", success=True)
        self.assertEqual("반영 완료", window.primary_validation_action_button.text())
        window.buy_rsi_value_line.setText("44")
        self.assertEqual("검증 실행", window.primary_validation_action_button.text())

        window._request_validation()
        window.show_validation_error("검증 실패")
        self.assertEqual("검증 실행", window.primary_validation_action_button.text())
        window._request_validation()
        window.set_replay_snapshot(self._replay_snapshot([]))
        self.assertEqual("설정 반영", window.primary_validation_action_button.text())
        window.set_validation_stock(ValidationStockRef("000660", "SK하이닉스"))
        self.assertEqual("검증 실행", window.primary_validation_action_button.text())

    def test_result_summary_keeps_estimate_in_left_cluster(self):
        window = self._window()
        window._request_validation()
        window.set_replay_snapshot(
            self._replay_snapshot([
                self._entry("BUY", 0, "BUY"),
                self._entry("SELL", 2, "SELL"),
            ])
        )
        summary = " ".join(
            " ".join((
                window.validation_status_label.text(),
                window.result_summary_label.text(),
                window.estimated_return_label.text(),
            )).split()
        )
        self.assertEqual(
            "검증 완료 005930 삼성전자 | 5분봉 | Candle 3 | BUY 1 | SELL 1 | 추정 손익률 +50.00%",
            summary,
        )
        layout = window._signal_validation_action_layout
        self.assertEqual(0, layout.indexOf(window.validation_status_label))
        self.assertEqual(1, layout.indexOf(window.result_summary_label))
        self.assertEqual(2, layout.indexOf(window.estimated_return_label))
        self.assertIsNotNone(layout.itemAt(3).spacerItem())
        self.assertEqual(4, layout.indexOf(window.primary_validation_action_button))
        self.assertEqual(5, layout.indexOf(window.close_button))

    def test_estimated_return_aggregates_completed_cycles_and_ignores_trailing_sell(self):
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
        trailing_sell = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 1, "SELL"),
            self._entry("SELL", 2, "SELL"),
        ], closes=(100.0, 110.0, 105.0))
        self.assertAlmostEqual(10.0, estimated_signal_return_percent(trailing_sell))

        window = self._window()
        window.set_replay_snapshot(trailing_sell)
        self.assertEqual(1, len(window.completed_cycles))
        self.assertEqual("|  추정 손익률 +10.00%", window.estimated_return_label.text())

        offsetting_cycles = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 1, "SELL"),
            self._entry("BUY", 2, "BUY"),
            self._entry("SELL", 3, "SELL"),
        ], closes=(100.0, 110.0, 200.0, 190.0))
        window.set_replay_snapshot(offsetting_cycles)
        self.assertEqual(2, len(window.completed_cycles))
        self.assertAlmostEqual(-5.0, window.completed_cycles[-1].estimated_return_percent)
        self.assertAlmostEqual(0.0, estimated_signal_return_percent(offsetting_cycles))
        self.assertEqual("|  추정 손익률 +0.00%", window.estimated_return_label.text())

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
        self.assertFalse(hasattr(window, "filter_result_table"))
        self.assertEqual(1, window.completed_cycle_table.rowCount())
        self.assertEqual(
            ["회차", "매수구간", "매수횟수", "추정평단", "매도시각", "매도가", "추정수익률"],
            [
                window.completed_cycle_table.horizontalHeaderItem(column).text()
                for column in range(window.completed_cycle_table.columnCount())
            ],
        )
        self.assertEqual(
            ["1", "09/11 14:00", "1", "100", "09/11 14:02", "150", "+50.00%"],
            [window.completed_cycle_table.item(0, column).text() for column in range(7)],
        )
        self.assertIn("+50.00%", window.estimated_return_label.text())

    def test_auth_is_fresh_and_default_host_is_the_existing_picker_host(self):
        broker = _FakeBroker(False)
        host = _FakeHost(self.stock)
        created = []
        downstream_calls = []

        def forbidden_downstream(*_args, **_kwargs):
            downstream_calls.append(True)
            raise AssertionError("downstream validation must not start without stock")

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_provider_factory=forbidden_downstream,
            replay_factory=forbidden_downstream,
            window_factory=window_factory,
            recent_stock_store=_MemoryRecentStockStore(),
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
        self.assertEqual([], host.started)
        self.assertEqual(1, len(host.preflighted))
        self.assertEqual(1, len(created))
        self.assertIsNone(created[0].stock)
        self.assertEqual(0, created[0].initial_requests)
        self.assertEqual([], downstream_calls)
        self.assertIsNone(created[0].parentWidget())

        created[0].close()
        QTest.qWait(0)
        self.app.processEvents()
        self.assertEqual((), flow.open_windows)

        default_flow = IndicatorFollowSignalValidationFlow(
            broker,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        self.assertEqual(
            "IndicatorFollowValidationStockPicker",
            default_flow.host._stock_picker_factory.__name__,
        )

    def test_unresolved_seed_reaches_window_without_eager_picker(self):
        broker = _FakeBroker(True)
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
            recent_stock_store=_MemoryRecentStockStore(),
        )
        carrier = type(
            "Carrier",
            (QDialog,),
            {"signal_validation_requested": pyqtSignal(object)},
        )()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        seed = self._unresolved_seed()
        carrier.signal_validation_requested.emit(seed)

        self.assertEqual([], host.started)
        self.assertEqual(1, len(host.preflighted))
        self.assertIs(host.preflighted[0], seed.settings_snapshot)
        self.assertEqual(1, len(created))
        self.assertEqual("주문가", created[0].seed.to_ui_state()["sell_ui"][
            "signal_conditions"
        ]["condition_a"]["gap_left_combo"])

    def test_no_stock_run_is_blocked_and_header_double_click_requests_selection(self):
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(None, self._seed())
        self.widgets.append(window)
        runs = []
        selections = []
        window.validation_run_requested.connect(runs.append)
        window.stock_selection_requested.connect(lambda: selections.append(True))

        self.assertIsNone(window.stock)
        self.assertEqual("종목 선택", window.compact_stock_label.text())
        self.assertIsNone(window.request_validation())
        self.assertEqual([], runs)
        self.assertIn("두 번 클릭", window.validation_status_label.text())

        QTest.mouseClick(window.compact_stock_label, Qt.LeftButton)
        self.assertEqual([], selections)
        QTest.mouseDClick(window.compact_stock_label, Qt.LeftButton)
        self.assertEqual([True], selections)

    def test_picker_accept_cancel_same_stock_and_remembered_reentry(self):
        host = _FakeHost(self.stock)
        created = []
        callbacks = []

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        recent_store = _MemoryRecentStockStore()
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=host,
            window_factory=window_factory,
            recent_stock_store=recent_store,
        )
        carrier = type(
            "Carrier",
            (QDialog,),
            {"signal_validation_requested": pyqtSignal(object)},
        )()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        with patch.object(
            flow_module.QTimer,
            "singleShot",
            side_effect=lambda _ms, callback: callbacks.append(callback),
        ):
            carrier.signal_validation_requested.emit(self._seed())
            first = created[-1]
            self.assertIsNone(first.stock)
            self.assertEqual([], host.picker_parents)
            self.assertEqual([], callbacks)

            host.pickers.append(_FakePicker(QDialog.Accepted, self.stock))
            first.stock_selection_requested.emit()
            self.assertEqual(self.stock, first.stock)
            self.assertEqual(self.stock, flow.last_selected_stock)
            self.assertEqual(1, first.validation_requests)
            self.assertIs(first, host.picker_parents[-1])
            self.assertEqual((self.stock,), flow.recent_stocks)
            self.assertEqual(1, recent_store.write_count)

            generation = flow._request_generation[id(first)]
            requests = first.validation_requests
            first.snapshots.append("preserved-result")
            host.pickers.append(_FakePicker(QDialog.Accepted, self.stock))
            first.stock_selection_requested.emit()
            self.assertEqual(generation, flow._request_generation[id(first)])
            self.assertEqual(requests, first.validation_requests)
            self.assertEqual(["preserved-result"], first.snapshots)
            self.assertEqual(1, recent_store.write_count)

            other = ValidationStockRef("000660", "SK하이닉스")
            host.pickers.append(_FakePicker(QDialog.Rejected, other))
            first.stock_selection_requested.emit()
            self.assertEqual(self.stock, first.stock)
            self.assertEqual(self.stock, flow.last_selected_stock)
            self.assertEqual(generation, flow._request_generation[id(first)])
            self.assertEqual(requests, first.validation_requests)
            self.assertEqual(1, recent_store.write_count)

            carrier.signal_validation_requested.emit(self._seed())
            second = created[-1]
            self.assertEqual(self.stock, second.stock)
            self.assertEqual(1, len(callbacks))
            callbacks.pop()()
            self.assertEqual(1, second.initial_requests)
            self.assertEqual((self.stock,), second.recent_stock_projections[-1])
            second.snapshots.append("window2-result")

            first.recent_stock_selected.emit(other)
            self.assertEqual(other, first.stock)
            self.assertEqual(self.stock, second.stock)
            self.assertEqual(["window2-result"], second.snapshots)
            self.assertEqual(other, flow.last_selected_stock)
            self.assertEqual((other, self.stock), flow.recent_stocks)
            self.assertEqual(2, recent_store.write_count)
            self.assertEqual((other, self.stock), first.recent_stock_projections[-1])
            self.assertEqual((other, self.stock), second.recent_stock_projections[-1])
            self.assertEqual(0, second.validation_requests)

            changed_generation = flow._request_generation[id(first)]
            changed_requests = first.validation_requests
            first.snapshots.append("other-result")
            first.recent_stock_selected.emit(other)
            self.assertEqual(changed_generation, flow._request_generation[id(first)])
            self.assertEqual(changed_requests, first.validation_requests)
            self.assertEqual(["other-result"], first.snapshots)
            self.assertEqual(2, recent_store.write_count)

            carrier.signal_validation_requested.emit(self._seed())
            third = created[-1]
            self.assertEqual(other, third.stock)
            self.assertEqual(1, len(callbacks))

    def test_selected_stock_snapshot_merges_static_metadata_and_rejects_stale_result(self):
        first = self.stock
        second = ValidationStockRef("000660", "SK하이닉스")
        broker = _SnapshotBroker(True)
        created = []

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                return None

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        store = _MemoryRecentStockStore(
            (first,),
            metadata={
                first.code: {
                    "market": "KOSPI",
                    "status": "정상",
                    "nxt_available": True,
                },
                second.code: {
                    "market": "KOSPI",
                    "status": "정상",
                    "nxt_available": False,
                },
            },
        )
        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=_FakeHost(first),
            historical_provider_factory=Provider,
            window_factory=window_factory,
            recent_stock_store=store,
        )
        carrier = type(
            "Carrier",
            (QDialog,),
            {"signal_validation_requested": pyqtSignal(object)},
        )()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        with patch.object(flow_module.QTimer, "singleShot"):
            carrier.signal_validation_requested.emit(self._seed())
        window = created[0]

        self.assertEqual([((first.code,), broker.market_snapshot_requests[0][1])], broker.market_snapshot_requests)
        first_callback = broker.market_snapshot_requests[0][1]
        first_callback({
            "ok": True,
            "rows": [{
                "stock_code": first.code,
                "current_price": 249500,
                "open_price": 249000,
                "high_price": 250500,
                "low_price": 248500,
                "change_rate": -3.85,
                "execution_strength": 83.9,
                "previous_day_volume_rate": -38.26,
                "cumulative_trading_value": 123456,
                "cumulative_volume": 7890,
                "market_capitalization": 456789,
            }],
        })
        merged = window.stock_metadata_projections[-1]
        self.assertEqual("KOSPI", merged["market"])
        self.assertEqual("정상", merged["status"])
        self.assertIs(True, merged["nxt_available"])
        self.assertEqual(249500, merged["current_price"])
        self.assertEqual(249000, merged["open_price"])
        self.assertEqual(250500, merged["high_price"])
        self.assertEqual(248500, merged["low_price"])
        self.assertEqual(-3.85, merged["change_rate"])
        self.assertEqual(83.9, merged["execution_strength"])
        self.assertEqual(-38.26, merged["previous_day_volume_rate"])
        self.assertEqual(123456, merged["cumulative_trading_value"])
        self.assertEqual(7890, merged["cumulative_volume"])
        self.assertEqual(456789, merged["market_capitalization"])

        window.recent_stock_selected.emit(second)
        self.assertEqual((second.code,), broker.market_snapshot_requests[-1][0])
        projections_after_selection = len(window.stock_metadata_projections)
        first_callback({
            "ok": True,
            "rows": [{"stock_code": first.code, "current_price": 999999}],
        })
        self.assertEqual(projections_after_selection, len(window.stock_metadata_projections))

        second_callback = broker.market_snapshot_requests[-1][1]
        second_callback({
            "ok": True,
            "rows": [{"stock_code": "A000660", "current_price": 123000}],
        })
        second_metadata = window.stock_metadata_projections[-1]
        self.assertEqual("KOSPI", second_metadata["market"])
        self.assertIs(False, second_metadata["nxt_available"])
        self.assertEqual(123000, second_metadata["current_price"])

    def test_stock_change_clears_result_and_rejects_late_historical_callback(self):
        host = _FakeHost(self.stock)
        created = []
        pending = []

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                pending.append((_self.session.request.stock, count, callback))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical):
                stock = _self.session.request.stock
                return ValidationReplayResult(True, snapshot=ValidationReplaySnapshot(
                    stock=stock,
                    timeframe_minutes=_self.session.request.timeframe_minutes,
                    settings_hash=_self.session.request.settings_snapshot.rules_hash,
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

        def result(stock, request_id):
            return ValidationHistoricalResult(
                True,
                snapshot=ValidationHistoricalSnapshot(
                    stock=stock,
                    timeframe_minutes=5,
                    requested_count=100,
                    request_id=request_id,
                    rows=[{
                        "체결시간": "20260911143000",
                        "시가": "100",
                        "고가": "101",
                        "저가": "99",
                        "현재가": "100",
                        "거래량": "1",
                    }],
                ),
            )

        def window_factory(stock, seed, parent=None):
            window = _FakeWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=host,
            historical_provider_factory=Provider,
            replay_factory=Replay,
            window_factory=window_factory,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        carrier = type(
            "Carrier",
            (QDialog,),
            {"signal_validation_requested": pyqtSignal(object)},
        )()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        carrier.signal_validation_requested.emit(self._seed())
        window = created[0]

        host.pickers.append(_FakePicker(QDialog.Accepted, self.stock))
        window.stock_selection_requested.emit()
        self.assertEqual(self.stock, pending[0][0])

        other = ValidationStockRef("000660", "SK하이닉스")
        host.pickers.append(_FakePicker(QDialog.Accepted, other))
        window.stock_selection_requested.emit()
        self.assertEqual(other, pending[1][0])

        pending[0][2](result(self.stock, "OLD"))
        self.assertEqual([], window.snapshots)
        pending[1][2](result(other, "NEW"))
        self.assertEqual(1, len(window.snapshots))
        self.assertEqual(other, window.snapshots[0].stock)

    def test_real_window_stock_change_removes_previous_result_immediately(self):
        window = self._window()
        window.set_replay_snapshot(
            self._replay_snapshot([self._entry("BUY", 1, "BUY")])
        )
        self.assertIsNotNone(window.replay_snapshot)
        self.assertIsNotNone(window.canvas)
        other = ValidationStockRef("000660", "SK하이닉스")

        self.assertTrue(window.set_validation_stock(other))
        self.assertEqual(other, window.stock)
        self.assertIsNone(window.replay_snapshot)
        self.assertIsNone(window.canvas)
        self.assertEqual([], window._candles)
        self.assertEqual([], window._entries)
        self.assertIsNone(window.selected_evaluation_index)
        self.assertEqual(0, window.completed_cycle_table.rowCount())
        self.assertFalse(window.completed_cycle_table.isHidden())
        self.assertFalse(window.completed_cycle_empty_label.isHidden())
        self.assertEqual("|  추정 손익률 -", window.estimated_return_label.text())
        self.assertIn("준비 중", window.validation_status_label.text())

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
            recent_stock_store=_MemoryRecentStockStore(),
        )
        flow._last_selected_stock = self.stock
        carrier = type("Carrier", (QDialog,), {"signal_validation_requested": pyqtSignal(object)})()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        carrier.signal_validation_requested.emit(self._seed())
        window = created[0]
        self.assertEqual(100, DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT)
        self.assertEqual(DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT, window.historical_candle_count)
        resolved_ui_state = self._seed().to_ui_state()
        for timeframe, candle_count in ((3, 300), (15, 500)):
            rules = deepcopy(self.rules)
            rules["bar"]["bar_minutes"] = timeframe
            window.validation_run_requested.emit(
                IndicatorFollowSignalValidationRunRequest(
                    build_signal_validation_snapshot(rules, ui_state=resolved_ui_state),
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
