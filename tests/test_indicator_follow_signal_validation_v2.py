# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QMouseEvent, QPixmap, QWheelEvent
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
    _marker_records,
    _time_axis_label_records,
    _validation_price_text,
    estimated_signal_return_percent,
)
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRestorePayload,
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
    entry_reset_started = pyqtSignal()
    entry_reset_requested = pyqtSignal(object)
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
        self.entry_commit_count = 0
        self.pool_installs = []
        self.signal_marker_batches = []

    def set_historical_candle_count(self, count):
        self.historical_candle_count = count

    def set_historical_candle_pool(self, candles, *, chart_candle_count):
        self.pool_installs.append((len(candles), int(chart_candle_count)))

    def set_signal_marker_entries(self, entries):
        self.signal_marker_batches.append(tuple(entries))

    def commit_entry_state(self):
        self.entry_commit_count += 1

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
        cls.ui_state["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"] = "-"
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
        self.history_cache_temporary = tempfile.TemporaryDirectory()
        self.default_history_cache = IndicatorFollowSignalValidationHistoricalCache(
            self.history_cache_temporary.name
        )
        self.history_cache_patch = patch.object(
            flow_module,
            "IndicatorFollowSignalValidationHistoricalCache",
            return_value=self.default_history_cache,
        )
        self.history_cache_patch.start()

    def tearDown(self):
        for widget in self.widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()
        self.history_cache_patch.stop()
        self.history_cache_temporary.cleanup()

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

    def _replay_snapshot(
        self,
        entries,
        closes=(100.0, 120.0, 150.0),
        *,
        timeframe_minutes=5,
    ):
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
            timeframe_minutes=timeframe_minutes,
            settings_hash="hash",
            historical_request_id="REQUEST",
            evaluated_start_index=0,
            evaluated_end_index=len(candles) - 1,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )

    def _candle_count_snapshot(self, candle_count, *, include_signals=False):
        start = datetime(2026, 9, 14, 9, 0)
        candles = []
        for index in range(candle_count):
            close = 100.0 + index
            candles.append({
                "time": (start + timedelta(minutes=5 * index)).strftime(
                    "%Y%m%d%H%M%S"
                ),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100 + index,
            })
        entries = []
        if include_signals:
            for side, index in (("BUY", candle_count // 4), ("SELL", candle_count * 3 // 4)):
                entries.append(ValidationReplayEntry(
                    evaluation_side=side,
                    evaluation_index=index,
                    evaluation_time=candles[index]["time"],
                    signal=side,
                    reason="scale fixture",
                    signal_index=index,
                    signal_time=candles[index]["time"],
                    delay_bar=0,
                    matched_groups=[],
                    details=[],
                    trace={"conditions": [], "groups": [], "aggregations": []},
                ))
        return ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="scale-hash",
            historical_request_id=f"SCALE-{candle_count}",
            evaluated_start_index=0,
            evaluated_end_index=candle_count - 1,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )

    def test_click_points_define_validation_range_and_recalculate_cycles(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        entries = [
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 1, "SELL"),
            self._entry("BUY", 2, "BUY"),
            self._entry("SELL", 3, "SELL"),
            self._entry("BUY", 4, "BUY"),
            self._entry("SELL", 5, "SELL"),
        ]
        window.set_replay_snapshot(
            self._replay_snapshot(
                entries,
                closes=(100.0, 110.0, 200.0, 220.0, 300.0, 330.0),
            )
        )
        self.app.processEvents()
        self.assertEqual(0, len(window.completed_cycles))
        self.assertIsNone(window.validation_range)
        self.assertEqual(0, window.completed_cycle_table.rowCount())
        self.assertNotIn("매수신호", window.result_summary_label.text())
        self.assertNotIn("매도신호", window.result_summary_label.text())
        self.assertEqual("", window.estimated_return_label.text())

        def click_candle(index):
            scale = window.canvas.price_scale()
            point = QPoint(
                round(window.canvas._x_for_index(index)),
                round(scale.y_for_price(window._candles[index]["close"])),
            )
            QTest.mouseClick(window.canvas, Qt.LeftButton, pos=point)
            self.app.processEvents()

        click_candle(2)
        self.assertIsNone(window.validation_range)
        self.assertEqual((2, 2), window.canvas.validation_range)
        self.assertEqual(0, len(window.completed_cycles))
        self.assertEqual("", window.estimated_return_label.text())

        click_candle(3)
        self.assertEqual((2, 3), window.validation_range)
        self.assertEqual(1, len(window.completed_cycles))
        cycle = window.completed_cycles[0]
        self.assertEqual(2, cycle.buy_start_index)
        self.assertEqual(3, cycle.sell_index)
        self.assertIn("1", window.completed_cycle_table.item(0, 0).text())
        self.assertIn("2", window.result_summary_label.text())

        click_candle(4)
        self.assertIsNone(window.validation_range)
        self.assertEqual((4, 4), window.canvas.validation_range)
        self.assertEqual(0, len(window.completed_cycles))
        self.assertEqual("", window.estimated_return_label.text())

        click_candle(2)
        self.assertIsNone(window.validation_range)
        self.assertEqual((2, 2), window.canvas.validation_range)
        self.assertEqual(2, window._validation_range_anchor_index)

        click_candle(2)
        self.assertIsNone(window.validation_range)
        self.assertEqual((2, 2), window.canvas.validation_range)
        self.assertEqual(2, window._validation_range_anchor_index)

        click_candle(5)
        self.assertEqual((2, 5), window.validation_range)
        self.assertIsNone(window._validation_range_anchor_index)

    def test_validation_range_survives_prepend_history_by_candle_time(self):
        window = self._window()
        window.set_replay_snapshot(self._candle_count_snapshot(100))
        window._select_validation_range_point(20)
        window._select_validation_range_point(40)
        start_time = window._candles[20]["time"]
        end_time = window._candles[40]["time"]

        first_time = datetime.strptime(
            window._candles[0]["time"],
            "%Y%m%d%H%M%S",
        )
        expanded_candles = []
        for index in range(115):
            candle_time = first_time + timedelta(minutes=5 * (index - 15))
            close = 85.0 + index
            expanded_candles.append({
                "time": candle_time.strftime("%Y%m%d%H%M%S"),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 85 + index,
            })
        expanded = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="scale-hash",
            historical_request_id="PREPENDED-HISTORY",
            evaluated_start_index=0,
            evaluated_end_index=114,
            dropped_raw_rows_count=0,
            candles=expanded_candles,
            entries=[],
        )

        window.set_replay_snapshot(expanded)

        self.assertEqual((35, 55), window.validation_range)
        self.assertEqual((35, 55), window.canvas.validation_range)
        self.assertEqual(start_time, window._candles[35]["time"])
        self.assertEqual(end_time, window._candles[55]["time"])

    def test_loaded_five_thousand_pool_starts_at_latest_one_hundred(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        pool = self._candle_count_snapshot(5_000).to_candles()
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
            historical_request_id="POOL-INITIAL-100",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=pool[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(
            pool,
            chart_candle_count=5_000,
        )
        window.set_replay_snapshot(replay)
        self.app.processEvents()

        self.assertEqual(5_000, window.historical_candle_count)
        self.assertEqual(5_000, len(window._candles))
        self.assertEqual(5_000, window.canvas.candle_count)
        self.assertEqual(100.0, window.visible_candle_span)
        self.assertEqual((4900, 5_000), window.canvas._visible_index_bounds())
        self.assertGreater(window.canvas.price_scale().minimum, 4_000)
        self.assertIsNotNone(window.visualization_cache)
        self.assertEqual(5_000, window.visualization_cache.candle_count)
        self.assertGreater(len(window.visualization_cache.series), 0)

    def test_same_pool_same_timeframe_replay_preserves_user_chart_view(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        pool = self._candle_count_snapshot(5_000).to_candles()

        def replay(settings_hash: str, timeframe_minutes: int = 5):
            return ValidationReplaySnapshot(
                stock=self.stock,
                timeframe_minutes=timeframe_minutes,
                settings_hash=settings_hash,
                historical_request_id="POOL-VIEW-STATE",
                evaluated_start_index=0,
                evaluated_end_index=99,
                dropped_raw_rows_count=0,
                candles=pool[-100:],
                entries=[],
            )

        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(replay("initial-hash"))
        self.app.processEvents()

        window._set_time_view(1_234.0, 64.0, manually_adjusted=True)
        scale = window.canvas.price_scale()
        manual_minimum = scale.minimum + 5.0
        manual_maximum = scale.maximum - 5.0
        window._current_price_minimum = manual_minimum
        window._current_price_maximum = manual_maximum
        window._price_scale_manually_adjusted = True
        window.canvas.set_price_view(manual_minimum, manual_maximum)
        window.select_evaluation_index(1_250)
        expected = window._current_chart_view_state()

        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(replay("averaging-toggled-hash"))
        self.app.processEvents()

        self.assertEqual(expected.visible_start_index, window.visible_start_index)
        self.assertEqual(expected.visible_candle_span, window.visible_candle_span)
        self.assertEqual(
            (expected.current_price_minimum, expected.current_price_maximum),
            window.current_price_bounds,
        )
        self.assertEqual(
            expected.time_scale_manually_adjusted,
            window.time_scale_manually_adjusted,
        )
        self.assertEqual(
            expected.price_scale_manually_adjusted,
            window.price_scale_manually_adjusted,
        )
        self.assertEqual(
            expected.selected_evaluation_index,
            window.selected_evaluation_index,
        )
        self.assertNotEqual(100.0, window.visible_candle_span)

        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(replay("timeframe-changed-hash", 3))
        self.app.processEvents()

        self.assertEqual(100.0, window.visible_candle_span)
        self.assertEqual(4_900.0, window.visible_start_index)
        self.assertFalse(window.time_scale_manually_adjusted)
        self.assertFalse(window.price_scale_manually_adjusted)

    def test_averaging_toggle_apply_preserves_same_pool_chart_view(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        pool = self._candle_count_snapshot(5_000).to_candles()
        initial = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
            historical_request_id="POOL-AVERAGING-APPLY",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=pool[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(initial)
        self.app.processEvents()

        window._set_time_view(1_600.0, 72.0, manually_adjusted=True)
        scale = window.canvas.price_scale()
        manual_minimum = scale.minimum + 5.0
        manual_maximum = scale.maximum - 5.0
        window._current_price_minimum = manual_minimum
        window._current_price_maximum = manual_maximum
        window._price_scale_manually_adjusted = True
        window.canvas.set_price_view(manual_minimum, manual_maximum)
        expected = window._current_chart_view_state()

        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)
        window.validation_averaging_enabled_check.setChecked(True)
        QTest.mouseClick(window.primary_validation_action_button, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(1, len(runs))
        self.assertTrue(
            runs[0].settings_snapshot.to_dict()["validation_execution"]["enabled"]
        )

        updated = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=runs[0].settings_snapshot.rules_hash,
            historical_request_id="POOL-AVERAGING-APPLY",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=pool[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(updated)
        self.app.processEvents()

        self.assertEqual(1, len(applies))
        self.assertEqual(expected.visible_start_index, window.visible_start_index)
        self.assertEqual(expected.visible_candle_span, window.visible_candle_span)
        self.assertEqual(
            (expected.current_price_minimum, expected.current_price_maximum),
            window.current_price_bounds,
        )
        self.assertTrue(window.time_scale_manually_adjusted)
        self.assertTrue(window.price_scale_manually_adjusted)

    def test_full_pool_signal_markers_survive_latest_one_hundred_detail_replay(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        pool = self._candle_count_snapshot(5_000).to_candles()

        def signal_entry(side, index):
            candle_time = str(pool[index]["time"])
            return ValidationReplayEntry(
                evaluation_side=side,
                evaluation_index=index,
                evaluation_time=candle_time,
                signal=side,
                reason="full pool marker",
                signal_index=index,
                signal_time=candle_time,
                delay_bar=0,
                matched_groups=[],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            )

        full_markers = [
            signal_entry("BUY", 125),
            signal_entry("SELL", 4_950),
        ]
        window.set_historical_candle_pool(
            pool,
            chart_candle_count=5_000,
        )
        window.set_signal_marker_entries(full_markers)
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="scale-hash",
            historical_request_id="POOL-MARKERS-100",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=pool[-100:],
            entries=[],
        )
        window.set_replay_snapshot(replay)
        self.app.processEvents()

        marker_records = window.canvas.marker_records()
        self.assertEqual(2, len(marker_records))
        self.assertEqual(
            {(125, "BUY"), (4_950, "SELL")},
            {
                (marker["evaluation_index"], marker["side"])
                for marker in marker_records
            },
        )
        self.assertEqual(2, len(window._signal_marker_entries))
        self.assertEqual([], window._entries)

    def test_wheel_zoom_within_loaded_pool_requests_no_more_history(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        pool = self._candle_count_snapshot(5_000).to_candles()
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="scale-hash",
            historical_request_id="POOL-ZOOM-100",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=pool[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(
            pool,
            chart_candle_count=5_000,
        )
        window.set_replay_snapshot(replay)
        self.app.processEvents()

        runs = []
        window.validation_run_requested.connect(runs.append)
        for _ in range(30):
            cursor_x = window.canvas._x_for_index(4950)
            window._zoom_time_scale_at(cursor_x, -120)

        self.assertEqual([], runs)
        self.assertEqual(5_000, window.historical_candle_count)
        self.assertGreater(window.visible_candle_span, 3_000.0)
        self.assertLessEqual(window.visible_candle_span, 5_000.0)

    def test_warmup_history_is_hidden_but_first_display_ma_is_continuous(self):
        rules = deepcopy(self.rules)
        rules.setdefault("buy", {}).setdefault("filters", {})["moving_average"] = {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": "CROSS_UP",
                "compare_target": "MA200",
            }],
        }
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                self._seed(rules=rules),
            )
        self.widgets.append(window)
        start = datetime(2026, 9, 14, 9, 0)
        candles = []
        for index in range(300):
            close = 100.0 + index
            candles.append({
                "time": (start + timedelta(minutes=3 * index)).strftime(
                    "%Y%m%d%H%M%S"
                ),
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100 + index,
            })
        snapshot = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
            historical_request_id="WARMUP-DISPLAY",
            evaluated_start_index=200,
            evaluated_end_index=299,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=[],
        )

        window.set_replay_snapshot(snapshot)

        self.assertEqual(300, len(window._calculation_candles))
        self.assertEqual(100, len(window._candles))
        self.assertEqual(candles[200]["time"], window._candles[0]["time"])
        self.assertEqual(candles[299]["time"], window._candles[-1]["time"])
        self.assertIsNotNone(window.visualization_cache)
        self.assertEqual(100, window.visualization_cache.candle_count)
        ma200_series = next(
            values
            for _identity, channel, values in window.visualization_cache.series
            if channel == "MA200"
        )
        self.assertEqual(100, len(ma200_series))
        self.assertIsNotNone(ma200_series[0])
        self.assertAlmostEqual(200.5, ma200_series[0])

    def test_v2_entry_button_remains_after_legacy_entry_retirement(self):
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
                new_payloads = []
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
                self.assertFalse(hasattr(dialog, "validation_chart_button"))
                self.assertFalse(hasattr(dialog, "validation_chart_requested"))
                self.assertEqual("검증차트2", dialog.signal_validation_button.text())
                self.assertEqual(1, len(new_payloads))
                self.assertIsInstance(new_payloads[0], IndicatorFollowSignalValidationSeed)
                self.assertEqual(
                    "A or D",
                    new_payloads[0].to_ui_state()["basic"]["buy_signal_expr_line"],
                )
                signal_bar = new_payloads[0].settings_snapshot.to_dict()["bar"]
                self.assertEqual(self.rules["bar"]["buy_delay_bar"], signal_bar["buy_delay_bar"])
                self.assertEqual(self.rules["bar"]["sell_delay_bar"], signal_bar["sell_delay_bar"])

    def test_routine_settings_shell_owns_optional_v2_flow_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(self.rules, ensure_ascii=False),
                encoding="utf-8",
            )
            owner = QDialog()
            owner.kiwoom_api = object()
            self.widgets.append(owner)
            with (
                patch.object(dialog_module.QTimer, "singleShot"),
                patch.object(
                    flow_module,
                    "bind_indicator_follow_signal_validation_flow",
                    return_value=object(),
                ) as bind,
            ):
                dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    parent=owner,
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(dialog)
        bind.assert_called_once_with(owner, dialog)

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
        self.assertIn(
            "price_compare",
            projected["validation_visualization_rules"]["buy"]["filters"],
        )
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

    def test_validation_execution_controls_are_v2_local_and_seeded_from_repeat(self):
        ui_state = deepcopy(self.ui_state)
        ui_state["buy_ui"]["repeat"].update({
            "detail_mode_combo": "예산기준",
            "budget_ratio_line": "2.5",
            "round_operator_combo": "+",
            "round_budget_line": "0.75",
            "active_direction_combo": "상향",
            "active_ratio_line": "1.25",
            "active_compare_combo": "이상",
        })
        window = self._window(ui_state)

        self.assertIs(
            window.validation_execution_box.parentWidget(),
            window.basic_header_widget,
        )
        header_layout = window.basic_header_row
        stock_index = header_layout.indexOf(window.compact_stock_display)
        pre_stock_separator = header_layout.itemAt(stock_index - 1).widget()
        self.assertEqual("|", pre_stock_separator.text())
        self.assertNotEqual(12, pre_stock_separator.contentsMargins().left())
        self.assertNotEqual(12, pre_stock_separator.contentsMargins().right())
        post_stock_separators = [
            header_layout.itemAt(index).widget()
            for index in range(stock_index + 1, header_layout.count())
            if (
                header_layout.itemAt(index).widget() is not None
                and hasattr(header_layout.itemAt(index).widget(), "text")
                and header_layout.itemAt(index).widget().text() == "|"
            )
        ]
        self.assertEqual(4, len(post_stock_separators))
        expected_padding = 12
        for separator in post_stock_separators:
            margins = separator.contentsMargins()
            self.assertEqual(expected_padding, margins.left())
            self.assertEqual(expected_padding, margins.right())

        execution_index = header_layout.indexOf(window.validation_execution_box)
        self.assertEqual(header_layout.count() - 2, execution_index)
        self.assertIsNotNone(header_layout.itemAt(execution_index + 1).spacerItem())
        self.assertEqual("평단관리:", window.validation_averaging_enabled_check.text())
        self.assertFalse(window.validation_averaging_enabled_check.isChecked())
        self.assertFalse(window.validation_execution_detail_widget.isEnabled())
        self.assertEqual("1", window.validation_first_buy_quantity_line.text())
        self.assertEqual("금액증가", window.validation_repeat_mode_combo.currentText())
        self.assertEqual("2.5", window.validation_budget_ratio_line.text())
        projected = window.collect_indicator_follow_ui_state()["validation_execution"]
        self.assertFalse(projected["enabled"])
        self.assertEqual("BUDGET", projected["repeat_mode"])
        self.assertEqual(2.5, projected["budget_ratio"])
        self.assertEqual(1, projected["first_buy_quantity"])
        self.assertEqual("SINGLE", projected["buy_hoga_mode"])
        self.assertEqual("SINGLE", projected["sell_hoga_mode"])

        apply_state = IndicatorFollowSignalValidationApplyPayload(
            window.collect_indicator_follow_ui_state()
        ).to_ui_state()
        self.assertNotIn("validation_execution", apply_state)

    def test_validation_execution_change_enters_run_snapshot_and_restore_returns_entry(self):
        ui_state = deepcopy(self.ui_state)
        ui_state["buy_ui"]["repeat"].update({
            "detail_mode_combo": "예산기준",
            "budget_ratio_line": "2.0",
            "round_operator_combo": "+",
            "round_budget_line": "0.5",
            "active_direction_combo": "상향",
            "active_ratio_line": "0.45",
            "active_compare_combo": "이상",
        })
        ui_state["validation_execution"] = {
            "enabled": True,
            "first_buy_quantity": 1,
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
        }
        window = self._window(ui_state)
        self.assertTrue(window.validation_averaging_enabled_check.isChecked())
        entry = window.commit_entry_state()

        window.validation_averaging_enabled_check.setChecked(False)
        self.assertFalse(window.validation_execution_detail_widget.isEnabled())
        window.validation_first_buy_quantity_line.setText("4")
        window.validation_repeat_mode_combo.setCurrentText("능동매수")
        window.validation_active_ratio_line.setText("10")
        run_request = window._request_validation()
        self.assertIsNotNone(run_request)
        execution = run_request.settings_snapshot.to_dict()["validation_execution"]
        self.assertFalse(execution["enabled"])
        self.assertEqual(4, execution["first_buy_quantity"])
        self.assertEqual("ACTIVE_BUY", execution["repeat_mode"])
        self.assertEqual(10.0, execution["active_ratio"])

        result = window.restore_signal_validation_entry_ui_state(entry.to_ui_state())
        self.assertIn("validation_execution", result["applied"])
        self.assertTrue(window.validation_averaging_enabled_check.isChecked())
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertEqual("1", window.validation_first_buy_quantity_line.text())
        self.assertEqual("금액증가", window.validation_repeat_mode_combo.currentText())
        self.assertEqual("2.0", window.validation_budget_ratio_line.text())

    def test_v2_completed_cycle_uses_virtual_quantity_weighted_average(self):
        window = self._window()
        rules = window._signal_validation_seed.settings_snapshot.to_dict()
        rules["validation_execution"] = {
            "enabled": True,
            "first_buy_quantity": 1,
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
            "round_operator": "ADD",
            "round_budget_value": 0.5,
            "active_direction": "UP",
            "active_ratio": 0.45,
            "active_compare": ">=",
        }
        settings = ValidationSettingsSnapshot(rules)
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
            }
            for index, price in enumerate((100.0, 100.0, 120.0))
        ]
        entries = [
            self._entry("BUY", 0, "BUY"),
            self._entry("BUY", 1, "BUY"),
            self._entry("SELL", 2, "SELL"),
        ]
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=settings.rules_hash,
            historical_request_id="VALIDATION-EXECUTION-CYCLE",
            evaluated_start_index=0,
            evaluated_end_index=2,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )
        window._pending_result_settings_snapshot = settings
        window.set_replay_snapshot(replay)
        window._set_validation_range(0, 2)
        window._refresh_validation_range_results()

        self.assertEqual(1, len(window.completed_cycles))
        cycle = window.completed_cycles[0]
        self.assertEqual(2, cycle.buy_count)
        self.assertEqual(3, cycle.buy_quantity)
        self.assertEqual(300.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)
        self.assertEqual(120.0, cycle.sell_price)
        self.assertAlmostEqual(20.0, cycle.estimated_return_percent)
        summary = window.estimated_return_label.text()
        self.assertIn("기간내 추정손익 +20.00%", summary)
        self.assertIn("추정투입금액 300원", summary)
        self.assertIn("추정수익금액 +60원", summary)
        self.assertIn("추정평단 100원", summary)
        self.assertIn("추정매도가격 120원", summary)
        self.assertNotIn("추정매도가격 360원", summary)

    def test_v2_averaging_disabled_uses_one_share_buys_and_simple_summary(self):
        window = self._window()
        rules = window._signal_validation_seed.settings_snapshot.to_dict()
        rules["validation_execution"] = {
            "enabled": False,
            "first_buy_quantity": 7,
            "repeat_mode": "BUDGET",
            "budget_ratio": 5.0,
            "round_operator": "ADD",
            "round_budget_value": 0.5,
            "active_direction": "UP",
            "active_ratio": 0.45,
            "active_compare": ">=",
        }
        settings = ValidationSettingsSnapshot(rules)
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
            }
            for index, price in enumerate((100.0, 100.0, 120.0))
        ]
        entries = [
            self._entry("BUY", 0, "BUY"),
            self._entry("BUY", 1, "BUY"),
            self._entry("SELL", 2, "SELL"),
        ]
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=settings.rules_hash,
            historical_request_id="VALIDATION-EXECUTION-OFF",
            evaluated_start_index=0,
            evaluated_end_index=2,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )
        window._pending_result_settings_snapshot = settings
        window.set_replay_snapshot(replay)
        window._set_validation_range(0, 2)
        window._refresh_validation_range_results()

        cycle = window.completed_cycles[0]
        self.assertEqual(2, cycle.buy_quantity)
        self.assertEqual(200.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)
        summary = window.estimated_return_label.text()
        self.assertEqual("| 기간내 추정손익 +20.00%", summary)
        self.assertNotIn("추정투입금액", summary)

    def test_v2_prebuilds_visualization_data_without_paint_side_effects(self):
        window = self._window()
        rules = window._signal_validation_seed.settings_snapshot.to_dict()
        rsi_condition = {
            "enabled": True,
            "target": "RSI",
            "operator": "<=",
            "value": 45,
        }
        rules.setdefault("indicators", {})["rsi"] = {"period": 14}
        rules.setdefault("buy", {})["filters"] = {
            "rsi": {
                "enabled": True,
                "conditions": [deepcopy(rsi_condition)],
            },
        }
        rules["validation_visualization_rules"] = {
            "buy": {
                "filters": {
                    "rsi": {
                        "enabled": True,
                        "conditions": [deepcopy(rsi_condition)],
                    },
                },
                "groups": [],
            },
            "sell": {"filters": {}, "signals": {}},
        }
        settings = ValidationSettingsSnapshot(rules)
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.0 + index,
                "volume": 1,
            }
            for index in range(30)
        ]
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=settings.rules_hash,
            historical_request_id="VISUALIZATION-DATA",
            evaluated_start_index=0,
            evaluated_end_index=29,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=[],
        )
        window._pending_result_settings_snapshot = settings
        window.set_replay_snapshot(replay)

        self.assertEqual("", window._visualization_data_error)
        self.assertEqual(
            ["RSI"],
            [item.family for item in window.visualization_descriptors],
        )
        descriptor = window.visualization_descriptors[0]
        self.assertEqual(
            30,
            len(window.visualization_cache.values_for(descriptor.identity, "RSI")),
        )
        self.assertEqual({}, window._visualization_active_by_marker)
        self.assertIsNotNone(window.canvas)
        self.assertEqual(180, window.canvas.indicator_total_height)
        self.assertEqual(
            ["RSI"],
            [pane["family"] for pane in window.canvas.lower_pane_records()],
        )
        self.assertEqual(
            {"RSI", "CRITERION"},
            {record["channel"] for record in window.canvas.visualization_series_records()},
        )

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
        window.set_historical_candle_count(500)
        window.buy_rsi_value_line.setText("33")
        run_request = window._request_validation()
        self.assertIsInstance(run_request, IndicatorFollowSignalValidationRunRequest)
        self.assertEqual(2, len(emitted))
        self.assertEqual(15, emitted[0].settings_snapshot.to_dict()["bar"]["bar_minutes"])
        self.assertEqual(100, emitted[0].candle_count)
        self.assertIs(run_request, emitted[1])
        self.assertEqual(100, run_request.candle_count)
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
        scale = canvas.price_scale()

        self.assertEqual(5, len(records))
        self.assertEqual(250_500.0, records[0]["price"])
        self.assertEqual(249_000.0, records[-1]["price"])
        self.assertEqual(249_750.0, records[2]["price"])
        self.assertEqual("250,500", records[0]["label"])
        self.assertEqual("249,000", records[-1]["label"])
        for price in (scale.minimum, 249_750.0, scale.maximum):
            self.assertAlmostEqual(price, scale.price_for_y(scale.y_for_price(price)))
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

    def test_price_axis_labels_round_only_the_integer_won_display(self):
        self.assertEqual("250,500", _validation_price_text(250500.0))
        self.assertEqual("258,605", _validation_price_text(258605.39))
        self.assertEqual("253,690", _validation_price_text(253690.14))
        self.assertEqual("248,775", _validation_price_text(248774.88))
        self.assertEqual("250,501", _validation_price_text(250500.51))

        canvas = IndicatorFollowSignalValidationChartCanvas([
            {
                "time": "20260911140000",
                "open": 248774.88,
                "high": 258605.39,
                "low": 238944.37,
                "close": 253690.14,
                "volume": 1,
            }
        ], [])
        canvas.resize(canvas.sizeHint().width(), 440)
        canvas.set_price_view(238944.37, 258605.39)
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

        scale = canvas.price_scale()
        grid_records = canvas.grid_line_records()
        axis_records = axis.price_axis_records()
        raw_prices = [record["price"] for record in grid_records]
        raw_y = [record["y"] for record in grid_records]
        candle_y = scale.y_for_price(253690.14)

        self.assertEqual(raw_prices, [record["price"] for record in axis_records])
        self.assertEqual(258605.39, raw_prices[0])
        self.assertEqual("258,605", axis_records[0]["label"])
        self.assertTrue(all("." not in record["label"] for record in axis_records))
        self.assertEqual(238944.37, scale.minimum)
        self.assertEqual(258605.39, scale.maximum)
        self.assertEqual(raw_y, [record["y"] for record in canvas.grid_line_records()])
        self.assertEqual(candle_y, scale.y_for_price(253690.14))
        self.assertAlmostEqual(253690.14, scale.price_for_y(candle_y))

    def test_candle_hover_requires_candle_x_and_high_low_ranges(self):
        candles = [
            {
                "time": f"2026091412{25 + index:02d}00",
                "open": 252250.0 + index,
                "high": 253000.0 + index,
                "low": 252000.0 + index,
                "close": 253000.0 + index,
                "volume": 1,
            }
            for index in range(3)
        ]
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(canvas.sizeHint().width(), 440)
        self.widgets.append(canvas)
        scale = canvas.price_scale()
        self.assertIsNotNone(scale)

        for index in (0, 1, 2):
            candle = candles[index]
            high_y = scale.y_for_price(candle["high"])
            low_y = scale.y_for_price(candle["low"])
            open_y = scale.y_for_price(candle["open"])
            close_y = scale.y_for_price(candle["close"])
            body_y = (open_y + close_y) / 2
            wick_y = (open_y + low_y) / 2
            for hover_y in (high_y, body_y, low_y, wick_y):
                tooltip = canvas.candle_tooltip_at(
                    canvas._x_for_index(index),
                    hover_y,
                )
                self.assertEqual(
                    f"2026-09-14 12:{25 + index:02d}",
                    tooltip.splitlines()[0],
                )
                self.assertNotIn("평가 시각", tooltip)
                self.assertIn(f"시가: {252250.0 + index}", tooltip)
                self.assertIn(f"고가: {253000.0 + index}", tooltip)
                self.assertIn(f"저가: {252000.0 + index}", tooltip)
                self.assertIn(f"종가: {253000.0 + index}", tooltip)

            self.assertEqual("", canvas.candle_tooltip_at(
                canvas._x_for_index(index),
                min(high_y, low_y) - 1,
            ))
            self.assertEqual("", canvas.candle_tooltip_at(
                canvas._x_for_index(index),
                max(high_y, low_y) + 1,
            ))

        half_slot = canvas.pixels_per_candle / 2
        hover_y = (
            scale.y_for_price(candles[0]["high"])
            + scale.y_for_price(candles[0]["low"])
        ) / 2
        self.assertEqual("", canvas.candle_tooltip_at(
            canvas._x_for_index(0) - half_slot - 1,
            hover_y,
        ))
        self.assertEqual("", canvas.candle_tooltip_at(
            canvas._x_for_index(2) + half_slot + 1,
            hover_y,
        ))
        self.assertEqual("", canvas.candle_tooltip_at(
            canvas._x_for_index(1),
            scale.plot_top - 1,
        ))

        canvas.set_time_view(2.0, 1.0)
        self.assertEqual("", canvas.candle_tooltip_at(
            canvas._x_for_index(0),
            hover_y,
        ))

        close_only = IndicatorFollowSignalValidationChartCanvas(
            [{
                "time": "20260914123000",
                "open": None,
                "high": None,
                "low": None,
                "close": 253000.0,
                "volume": 1,
            }],
            [],
        )
        close_only.resize(close_only.sizeHint().width(), 440)
        self.widgets.append(close_only)
        close_scale = close_only.price_scale()
        self.assertEqual("", close_only.candle_tooltip_at(
            close_only._x_for_index(0),
            close_scale.y_for_price(253000.0),
        ))

    def test_candle_hover_tracks_current_time_and_price_transforms_without_replay(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        index = 450
        candle = window.canvas.to_candles()[index]
        expected_time = datetime.strptime(
            candle["time"], "%Y%m%d%H%M%S"
        ).strftime("%Y-%m-%d %H:%M")

        with patch.object(ValidationHistoricalReplay, "evaluate") as replay:
            old_x = window.canvas._x_for_index(index)
            window._zoom_time_scale_at(old_x, 120)
            new_x = window.canvas._x_for_index(index)
            self.assertLessEqual(abs(new_x - old_x), 1.0)

            scale = window.canvas.price_scale()
            hover_y = (
                scale.y_for_price(candle["high"])
                + scale.y_for_price(candle["low"])
            ) / 2
            self.assertIn(
                expected_time,
                window.canvas.candle_tooltip_at(new_x, hover_y),
            )

            window._zoom_price_scale_at(hover_y, 120)
            zoomed_scale = window.canvas.price_scale()
            zoomed_high_y = zoomed_scale.y_for_price(candle["high"])
            zoomed_low_y = zoomed_scale.y_for_price(candle["low"])
            zoomed_hover_y = (zoomed_high_y + zoomed_low_y) / 2
            self.assertIn(
                expected_time,
                window.canvas.candle_tooltip_at(new_x, zoomed_hover_y),
            )
            self.assertEqual("", window.canvas.candle_tooltip_at(
                new_x,
                min(zoomed_high_y, zoomed_low_y) - 1,
            ))
            self.assertEqual("", window.canvas.candle_tooltip_at(
                new_x,
                max(zoomed_high_y, zoomed_low_y) + 1,
            ))
            replay.assert_not_called()

    def test_candle_hover_clips_high_low_range_to_current_plot(self):
        cases = (
            ({"open": 170.0, "high": 200.0, "low": 150.0, "close": 160.0}, "top"),
            ({"open": 140.0, "high": 150.0, "low": 100.0, "close": 130.0}, "bottom"),
        )
        for values, clipped_edge in cases:
            candle = {
                "time": "20260914123000",
                "volume": 1,
                **values,
            }
            canvas = IndicatorFollowSignalValidationChartCanvas([candle], [])
            canvas.resize(canvas.sizeHint().width(), 440)
            canvas.set_price_view(120.0, 180.0)
            self.widgets.append(canvas)
            scale = canvas.price_scale()
            high_y = scale.y_for_price(candle["high"])
            low_y = scale.y_for_price(candle["low"])
            hit_top = max(scale.plot_top, min(high_y, low_y))
            hit_bottom = min(scale.plot_bottom, max(high_y, low_y))
            self.assertLessEqual(hit_top, hit_bottom)
            edge_y = hit_top if clipped_edge == "top" else hit_bottom
            tooltip = canvas.candle_tooltip_at(
                canvas._x_for_index(0),
                edge_y,
            )
            self.assertEqual("2026-09-14 12:30", tooltip.splitlines()[0])
            self.assertNotIn("평가 시각", tooltip)
            outside_y = hit_bottom + 1 if clipped_edge == "top" else hit_top - 1
            self.assertTrue(scale.plot_top <= outside_y <= scale.plot_bottom)
            self.assertEqual("", canvas.candle_tooltip_at(
                canvas._x_for_index(0),
                outside_y,
            ))

        for high, low in ((220.0, 200.0), (100.0, 80.0)):
            canvas = IndicatorFollowSignalValidationChartCanvas(
                [{
                    "time": "20260914123000",
                    "open": low,
                    "high": high,
                    "low": low,
                    "close": high,
                    "volume": 1,
                }],
                [],
            )
            canvas.resize(canvas.sizeHint().width(), 440)
            canvas.set_price_view(120.0, 180.0)
            self.widgets.append(canvas)
            scale = canvas.price_scale()
            self.assertEqual("", canvas.candle_tooltip_at(
                canvas._x_for_index(0),
                (scale.plot_top + scale.plot_bottom) / 2,
            ))

    def test_delayed_signal_marker_uses_activation_evaluation_index(self):
        candles = [
            {
                "time": f"2026091412{25 + index:02d}00",
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.5 + index,
                "volume": 1,
            }
            for index in range(5)
        ]
        entries = [
            ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=4,
                evaluation_time=candles[4]["time"],
                signal="BUY",
                reason="TURN_UP delay fixture",
                signal_index=2,
                signal_time=candles[2]["time"],
                delay_bar=2,
                matched_groups=[],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            )
        ]

        markers = _marker_records(candles, entries)

        self.assertEqual(1, len(markers))
        self.assertEqual(4, markers[0]["evaluation_index"])
        self.assertEqual(candles[4]["time"], markers[0]["evaluation_time"])

    def test_marker_evidence_hover_has_priority_over_candle_hover(self):
        canvas = IndicatorFollowSignalValidationChartCanvas(
            [{
                "time": "20260914122500",
                "open": 252250.0,
                "high": 253000.0,
                "low": 252000.0,
                "close": 253000.0,
                "volume": 1,
            }],
            [],
        )
        canvas.resize(canvas.sizeHint().width(), 440)
        canvas.show()
        self.widgets.append(canvas)
        with patch.object(
            canvas,
            "_marker_at",
            return_value={
                "evaluation_index": 0,
                "side": "BUY",
                "tooltip": "BUY evidence",
            },
        ), patch.object(
            canvas,
            "candle_tooltip_at",
            return_value="Candle OHLC",
        ) as candle_tooltip, patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
        ) as show_tooltip:
            QTest.mouseMove(canvas, QPoint(100, 100))
            self.app.processEvents()

        candle_tooltip.assert_not_called()
        self.assertEqual("BUY evidence", show_tooltip.call_args.args[1])

    def test_candle_hover_hides_tooltip_immediately_outside_candle_hit(self):
        canvas = IndicatorFollowSignalValidationChartCanvas(
            [{
                "time": "20260914122500",
                "open": 252250.0,
                "high": 253000.0,
                "low": 252000.0,
                "close": 253000.0,
                "volume": 1,
            }],
            [],
        )
        canvas.resize(canvas.sizeHint().width(), 440)
        canvas.show()
        self.widgets.append(canvas)
        scale = canvas.price_scale()
        hover_x = int(canvas._x_for_index(0))
        hover_y = int(
            (
                scale.y_for_price(253000.0)
                + scale.y_for_price(252000.0)
            )
            / 2
        )

        with patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
        ) as show_tooltip, patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText",
        ) as hide_tooltip:
            QTest.mouseMove(canvas, QPoint(hover_x, hover_y))
            self.app.processEvents()
            self.assertEqual(1, show_tooltip.call_count)
            self.assertIn("2026-09-14 12:25", show_tooltip.call_args.args[1])

            QTest.mouseMove(canvas, QPoint(hover_x, int(scale.plot_top - 1)))
            self.app.processEvents()
            self.assertGreaterEqual(hide_tooltip.call_count, 1)

    def test_fixed_viewport_separates_total_candles_from_visible_span(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()

        window.set_replay_snapshot(self._candle_count_snapshot(100))
        self.app.processEvents()
        width_100 = window.canvas.width()
        self.assertLessEqual(
            abs(width_100 - window.chart_scroll_area.viewport().width()),
            1,
        )
        self.assertEqual(100.0, window.visible_candle_span)
        self.assertEqual(0.0, window.visible_start_index)
        self.assertEqual(0, window.time_navigation_scrollbar.maximum())
        self.assertFalse(window.time_navigation_scrollbar.isVisible())

        window.set_replay_snapshot(self._candle_count_snapshot(200))
        self.app.processEvents()
        self.assertLessEqual(abs(width_100 - window.canvas.width()), 1)
        self.assertEqual(100.0, window.visible_candle_span)
        self.assertEqual(100.0, window.visible_start_index)
        max_200 = window.time_navigation_scrollbar.maximum()
        self.assertGreater(max_200, 0)
        self.assertTrue(window.time_navigation_scrollbar.isVisible())

        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        self.assertLessEqual(abs(width_100 - window.canvas.width()), 1)
        self.assertEqual(100.0, window.visible_candle_span)
        self.assertEqual(400.0, window.visible_start_index)
        self.assertGreater(window.time_navigation_scrollbar.maximum(), max_200)
        self.assertEqual(
            0,
            window.chart_scroll_area.horizontalScrollBar().maximum(),
        )

    def test_candle_body_width_tracks_time_span_with_small_fixed_gap(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        canvas = window.canvas
        canvas_size = canvas.size()
        marker_indexes = [
            marker["evaluation_index"] for marker in canvas.marker_records()
        ]
        selected_index = window.selected_evaluation_index

        baseline_span = window.visible_candle_span
        baseline_slot = canvas.pixels_per_candle
        baseline_body = canvas._candle_body_width()
        self.assertAlmostEqual(
            canvas._CANDLE_HORIZONTAL_GAP,
            baseline_slot - baseline_body,
        )
        self.assertGreater(baseline_body, 6.0)
        self.assertLess(baseline_body, baseline_slot)

        anchor_index = 450
        anchor_x = canvas._x_for_index(anchor_index)
        window._zoom_time_scale_at(anchor_x, 120)
        zoomed_slot = canvas.pixels_per_candle
        zoomed_body = canvas._candle_body_width()
        self.assertLess(window.visible_candle_span, baseline_span)
        self.assertGreater(zoomed_slot, baseline_slot)
        self.assertGreater(zoomed_body, baseline_body)
        self.assertAlmostEqual(
            canvas._CANDLE_HORIZONTAL_GAP,
            zoomed_slot - zoomed_body,
        )
        self.assertEqual(canvas_size, canvas.size())

        window._zoom_time_scale_at(anchor_x, -240)
        zoomed_out_slot = canvas.pixels_per_candle
        zoomed_out_body = canvas._candle_body_width()
        self.assertGreater(window.visible_candle_span, baseline_span)
        self.assertLess(zoomed_out_slot, baseline_slot)
        self.assertLess(zoomed_out_body, baseline_body)
        self.assertAlmostEqual(
            canvas._CANDLE_HORIZONTAL_GAP,
            zoomed_out_slot - zoomed_out_body,
        )
        self.assertEqual(canvas_size, canvas.size())
        self.assertEqual(selected_index, window.selected_evaluation_index)
        self.assertEqual(
            marker_indexes,
            [marker["evaluation_index"] for marker in canvas.marker_records()],
        )

    def test_chart_wheel_scales_time_and_drag_pans_with_click_threshold(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(
            self._candle_count_snapshot(500, include_signals=True)
        )
        self.app.processEvents()
        canvas = window.canvas
        canvas_width = canvas.width()
        anchor_index = 450
        cursor_x = canvas._x_for_index(anchor_index)
        cursor_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )
        before_x = canvas._x_for_index(anchor_index)
        price_bounds_before = window.current_price_bounds

        def send_chart_wheel(delta):
            local_pos = QPoint(round(cursor_x), cursor_y)
            global_pos = canvas.mapToGlobal(local_pos)
            event = QWheelEvent(
                QPointF(local_pos),
                QPointF(global_pos),
                QPoint(),
                QPoint(0, delta),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(canvas, event)

        with patch.object(ValidationHistoricalReplay, "evaluate") as replay:
            send_chart_wheel(120)
            self.app.processEvents()
            self.assertLess(window.visible_candle_span, 100.0)
            self.assertLessEqual(
                abs(canvas._x_for_index(anchor_index) - before_x),
                1.0,
            )
            self.assertEqual(price_bounds_before, window.current_price_bounds)
            self.assertEqual(canvas_width, canvas.width())
            self.assertTrue(window.time_scale_manually_adjusted)

            window._zoom_time_scale_at(cursor_x, 120 * 100)
            self.assertEqual(
                window._MIN_VISIBLE_CANDLE_SPAN,
                window.visible_candle_span,
            )
            window._zoom_time_scale_at(cursor_x, -120 * 100)
            self.assertEqual(500.0, window.visible_candle_span)
            replay.assert_not_called()

        window._set_time_view(200.0, 100.0)
        selected = []
        canvas.bar_selected.connect(selected.append)
        click_x = round(canvas._x_for_index(240))
        click_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )
        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(click_x, click_y))
        QTest.mouseMove(canvas, QPoint(click_x + 2, click_y + 2))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(click_x + 2, click_y + 2),
        )
        self.assertEqual([240], selected)

        selected.clear()
        span_before_pan = window.visible_candle_span
        start_before_pan = window.visible_start_index
        scale_before_pan = canvas.price_scale()
        range_before_pan = scale_before_pan.maximum - scale_before_pan.minimum
        minimum_before_pan = scale_before_pan.minimum

        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(click_x, click_y))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(click_x + 120, click_y + 60),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(click_x + 120, click_y + 60),
        )
        self.assertEqual(span_before_pan, window.visible_candle_span)
        self.assertLess(window.visible_start_index, start_before_pan)
        scale_after_pan = canvas.price_scale()
        self.assertAlmostEqual(
            range_before_pan,
            scale_after_pan.maximum - scale_after_pan.minimum,
            places=8,
        )
        self.assertGreater(scale_after_pan.minimum, minimum_before_pan)
        self.assertEqual([], selected)

        start_after_right_pan = window.visible_start_index
        minimum_after_down_pan = scale_after_pan.minimum
        click_x = round(canvas._x_for_index(220))
        click_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )
        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(click_x, click_y))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(click_x - 80, click_y - 40),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(click_x - 80, click_y - 40),
        )
        self.assertEqual(span_before_pan, window.visible_candle_span)
        self.assertGreater(window.visible_start_index, start_after_right_pan)
        self.assertLess(canvas.price_scale().minimum, minimum_after_down_pan)
        self.assertEqual([], selected)

        sell_index = 500 * 3 // 4
        window._set_time_view(330.0, 100.0)
        sell_price = float(canvas._candles[sell_index]["high"])
        canvas.set_price_view(sell_price - 50.0, sell_price + 50.0)
        sell_marker_pos = QPoint(
            round(canvas._x_for_index(sell_index)),
            round(canvas._marker_y(sell_index, "SELL", canvas.price_scale())),
        )
        QTest.mouseClick(canvas, Qt.LeftButton, pos=sell_marker_pos)
        self.app.processEvents()
        self.assertEqual((sell_index, "SELL"), canvas.pinned_marker_key)
        self.assertEqual(sell_index, canvas.crosshair_index)

        drag_start = QPoint(
            round(canvas._x_for_index(360)),
            round((canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2),
        )
        QTest.mousePress(canvas, Qt.LeftButton, pos=drag_start)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_start.x() + 80, drag_start.y() + 30),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(drag_start.x() + 80, drag_start.y() + 30),
        )
        self.assertEqual((sell_index, "SELL"), canvas.pinned_marker_key)
        self.assertEqual(sell_index, canvas.crosshair_index)

    def test_price_axis_wheel_scales_price_with_cursor_anchor_and_preserves_time_view(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500, include_signals=True))
        self.app.processEvents()
        canvas = window.canvas
        price_axis = window.fixed_price_axis
        viewport = window.chart_scroll_area.viewport()
        time_view_before = (
            window.visible_start_index,
            window.visible_candle_span,
        )
        canvas_size_before = canvas.size()
        body_width_before = canvas._candle_body_width()
        selected_before = window.selected_evaluation_index
        axis_labels_before = [
            record["label"] for record in window.fixed_price_axis.price_axis_records()
        ]
        scale_before = canvas.price_scale()
        cursor_y = round(
            scale_before.plot_top
            + (scale_before.plot_bottom - scale_before.plot_top) * 0.35
        )
        anchor_price = scale_before.price_for_y(cursor_y)
        range_before = scale_before.maximum - scale_before.minimum

        def send_wheel(delta, *, canvas_y=cursor_y):
            axis_y = viewport.geometry().top() + canvas_y
            axis_pos = QPoint(
                max(0, price_axis.width() // 2),
                round(axis_y),
            )
            global_pos = price_axis.mapToGlobal(axis_pos)
            event = QWheelEvent(
                QPointF(axis_pos),
                QPointF(global_pos),
                QPoint(),
                QPoint(0, delta),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(price_axis, event)

        with patch.object(ValidationHistoricalReplay, "evaluate") as replay:
            bounds_before_axis_miss = window.current_price_bounds
            send_wheel(120, canvas_y=round(scale_before.plot_bottom + 12))
            self.app.processEvents()
            self.assertEqual(bounds_before_axis_miss, window.current_price_bounds)

            send_wheel(120)
            self.app.processEvents()
            scale_zoomed = canvas.price_scale()
            self.assertLess(
                scale_zoomed.maximum - scale_zoomed.minimum,
                range_before,
            )
            self.assertAlmostEqual(
                anchor_price,
                scale_zoomed.price_for_y(cursor_y),
                places=8,
            )
            self.assertTrue(window.price_scale_manually_adjusted)
            self.assertEqual(time_view_before, (
                window.visible_start_index,
                window.visible_candle_span,
            ))
            self.assertEqual(canvas_size_before, canvas.size())
            self.assertEqual(body_width_before, canvas._candle_body_width())
            self.assertEqual(selected_before, window.selected_evaluation_index)
            self.assertNotEqual(
                axis_labels_before,
                [
                    record["label"]
                    for record in window.fixed_price_axis.price_axis_records()
                ],
            )
            self.assertTrue(all(
                "." not in record["label"]
                for record in window.fixed_price_axis.price_axis_records()
            ))
            self.assertTrue(
                not scale_zoomed.minimum.is_integer()
                or not scale_zoomed.maximum.is_integer()
            )

            zoomed_range = scale_zoomed.maximum - scale_zoomed.minimum
            send_wheel(-120)
            self.app.processEvents()
            scale_restored = canvas.price_scale()
            self.assertGreater(
                scale_restored.maximum - scale_restored.minimum,
                zoomed_range,
            )

            for _index in range(100):
                window._zoom_price_scale_at(cursor_y, 120)
            minimum_range = range_before * window._MIN_PRICE_RANGE_RATIO
            self.assertAlmostEqual(
                minimum_range,
                canvas.price_scale().maximum - canvas.price_scale().minimum,
                places=8,
            )
            for _index in range(200):
                window._zoom_price_scale_at(cursor_y, -120)
            maximum_range = range_before * window._MAX_PRICE_RANGE_RATIO
            self.assertAlmostEqual(
                maximum_range,
                canvas.price_scale().maximum - canvas.price_scale().minimum,
                places=6,
            )
            manual_price_bounds = window.current_price_bounds
            window.resize(window.width() + 120, window.height() + 40)
            QTest.qWait(10)
            self.app.processEvents()
            self.assertEqual(manual_price_bounds, window.current_price_bounds)
            self.assertEqual(manual_price_bounds, (
                canvas.price_scale().minimum,
                canvas.price_scale().maximum,
            ))
            self.assertEqual(time_view_before, (
                window.visible_start_index,
                window.visible_candle_span,
            ))
            replay.assert_not_called()

        self.assertEqual(time_view_before, (
            window.visible_start_index,
            window.visible_candle_span,
        ))
        window.set_replay_snapshot(self._candle_count_snapshot(200))
        self.app.processEvents()
        self.assertFalse(window.price_scale_manually_adjusted)
        self.assertEqual((199.0, 300.0), window.current_price_bounds)
        self.assertEqual(window.current_price_bounds, (
            window.canvas.price_scale().minimum,
            window.canvas.price_scale().maximum,
        ))

    def test_logical_scroll_resize_and_render_actions_do_not_run_replay(self):
        window = self._window()
        window.resize(1300, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(
            self._candle_count_snapshot(500, include_signals=True)
        )
        self.app.processEvents()
        original_canvas_size = window.canvas.size()
        original_span = window.visible_candle_span
        original_body_width = window.canvas._candle_body_width()
        original_price_bounds = (
            window.canvas.price_scale().minimum,
            window.canvas.price_scale().maximum,
        )
        marker_indexes = [
            marker["evaluation_index"] for marker in window.canvas.marker_records()
        ]

        with patch.object(ValidationHistoricalReplay, "evaluate") as replay:
            window.time_navigation_scrollbar.setValue(0)
            self.app.processEvents()
            self.assertEqual(0.0, window.visible_start_index)
            self.assertEqual(0, window.canvas._nearest_candle_index(
                window.canvas._x_for_index(0)
            ))
            window.time_navigation_scrollbar.setValue(
                window.time_navigation_scrollbar.maximum()
            )
            self.app.processEvents()
            self.assertEqual(400.0, window.visible_start_index)
            self.assertEqual(499, window.canvas._nearest_candle_index(
                window.canvas._x_for_index(499)
            ))
            self.assertEqual(original_span, window.visible_candle_span)
            self.assertEqual(original_body_width, window.canvas._candle_body_width())
            self.assertEqual(
                original_price_bounds,
                (
                    window.canvas.price_scale().minimum,
                    window.canvas.price_scale().maximum,
                ),
            )

            window.select_evaluation_index(50)
            self.assertLessEqual(window.visible_start_index, 50)
            self.assertLess(50, window.visible_start_index + window.visible_candle_span)
            window.resize(window.width() + 200, window.height())
            QTest.qWait(10)
            self.app.processEvents()
            self.assertEqual(original_span, window.visible_candle_span)
            self.assertEqual(original_canvas_size.height(), window.canvas.height())
            self.assertLessEqual(
                abs(window.canvas.width() - window.chart_scroll_area.viewport().width()),
                1,
            )
            scale = window.canvas.price_scale()
            for hover_index in range(50, 70):
                window.canvas.candle_tooltip_at(
                    window.canvas._x_for_index(hover_index),
                    (scale.plot_top + scale.plot_bottom) / 2,
                )
            replay.assert_not_called()

        self.assertEqual(50, window.selected_evaluation_index)
        self.assertEqual(
            marker_indexes,
            [marker["evaluation_index"] for marker in window.canvas.marker_records()],
        )
        self.assertTrue(all(
            window.visible_start_index
            <= record["index"] + 0.5
            <= window.visible_start_index + window.visible_candle_span
            for record in window.canvas.time_axis_records()
        ))

    def test_validation_context_changes_refresh_without_candidate_emission(self):
        window = self._window()
        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)

        window.basic_signal_interval_combo.setCurrentText("3")
        self.assertEqual(1, len(runs))
        self.assertEqual(
            3,
            runs[-1].settings_snapshot.to_dict()["bar"]["bar_minutes"],
        )
        self.assertEqual(100, runs[-1].candle_count)
        self.assertEqual([], applies)
        window.set_replay_snapshot(
            self._replay_snapshot([], timeframe_minutes=3)
        )
        self.assertIn("3분봉", window.result_summary_label.text())
        self.assertEqual("", window.estimated_return_label.text())
        self.assertEqual([], applies)

        self.assertFalse(hasattr(window, "historical_candle_count_spin"))
        self.assertEqual(5_000, window.historical_candle_count)
        self.assertEqual(1, len(runs))
        self.assertEqual([], applies)

    def test_direct_candle_count_input_is_removed(self):
        window = self._window()
        runs = []
        window.validation_run_requested.connect(runs.append)

        self.assertFalse(hasattr(window, "historical_candle_count_spin"))
        self.assertEqual(5_000, window.historical_candle_count)
        window.set_historical_candle_count(300)

        self.assertEqual(300, window.historical_candle_count)
        self.assertEqual([], runs)

    def test_settings_apply_validates_current_chart_history_then_applies(self):
        window = self._window()
        window.show()
        self.app.processEvents()
        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)

        QTest.mouseClick(window.primary_validation_action_button, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(1, len(runs))
        self.assertEqual(100, runs[0].candle_count)
        self.assertEqual([], applies)
        self.assertTrue(window._settings_apply_after_validation)

        window.set_replay_snapshot(self._candle_count_snapshot(200))

        self.assertEqual(1, len(runs))
        self.assertEqual(1, len(applies))
        self.assertFalse(window._settings_apply_after_validation)

    def test_chart_click_selects_range_point_without_history_request(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(100))
        self.app.processEvents()
        runs = []
        window.validation_run_requested.connect(runs.append)
        selected_index = 20
        click_x = round(window.canvas._x_for_index(selected_index))
        scale = window.canvas.price_scale()
        click_y = round((scale.plot_top + scale.plot_bottom) / 2)

        QTest.mouseClick(
            window.canvas,
            Qt.LeftButton,
            pos=QPoint(click_x, click_y),
        )
        self.app.processEvents()

        self.assertEqual([], runs)
        self.assertEqual(selected_index, window.selected_evaluation_index)
        self.assertIsNone(window.validation_range)
        self.assertEqual(
            (selected_index, selected_index),
            window.canvas.validation_range,
        )

    def test_programmatic_candle_count_set_does_not_request_validation(self):
        window = self._window()
        runs = []
        window.validation_run_requested.connect(runs.append)

        window.set_historical_candle_count(200)

        self.assertEqual(200, window.historical_candle_count)
        self.assertEqual([], runs)

    def test_entry_reset_restores_context_strategy_and_logical_chart_view(self):
        window = self._window()
        window.set_historical_candle_count(100)
        entry = window.commit_entry_state()
        entry_ui_state = entry.to_ui_state()
        entry_buy = entry_ui_state["basic"]["buy_signal_expr_line"]
        entry_sell = entry_ui_state["basic"]["sell_signal_expr_line"]
        snapshot = self._candle_count_snapshot(500, include_signals=True)
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(snapshot)
        self.app.processEvents()
        chart_entry = window._entry_chart_view_state
        geometry = window.geometry()

        runs = []
        resets = []
        window.validation_run_requested.connect(runs.append)
        window.entry_reset_requested.connect(resets.append)
        window.basic_signal_interval_combo.setCurrentText("3")
        window.set_historical_candle_count(500)
        window.buy_signal_expr_line.setText("D")
        window.sell_signal_expr_line.setText("A")
        window._set_time_view(125.0, 50.0, manually_adjusted=True)
        scale = window.canvas.price_scale()
        cursor_y = (scale.plot_top + scale.plot_bottom) / 2
        window._zoom_price_scale_at(cursor_y, 120)
        window.select_evaluation_index(450)
        self.assertNotEqual(chart_entry.visible_candle_span, window.visible_candle_span)
        self.assertTrue(window.price_scale_manually_adjusted)
        window.set_validation_stock(ValidationStockRef("000660", "SK하이닉스"))
        runs_before_reset = len(runs)

        window.reset_button.click()

        self.assertEqual(runs_before_reset + 1, len(runs))
        self.assertEqual(1, len(resets))
        self.assertEqual("5", window.basic_signal_interval_combo.currentText())
        self.assertEqual(100, window.historical_candle_count)
        self.assertEqual(entry_buy, window.buy_signal_expr_line.text())
        self.assertEqual(entry_sell, window.sell_signal_expr_line.text())
        self.assertEqual(self.stock, window.stock)
        self.assertEqual(
            IndicatorFollowSignalValidationRestorePayload(entry_ui_state).to_ui_state(),
            resets[0].to_ui_state(),
        )
        self.assertIsNone(window.replay_snapshot)

        window.set_replay_snapshot(snapshot)
        self.app.processEvents()
        self.assertEqual(chart_entry.visible_start_index, window.visible_start_index)
        self.assertEqual(chart_entry.visible_candle_span, window.visible_candle_span)
        self.assertEqual(
            (chart_entry.current_price_minimum, chart_entry.current_price_maximum),
            window.current_price_bounds,
        )
        self.assertEqual(
            chart_entry.selected_evaluation_index,
            window.selected_evaluation_index,
        )
        self.assertEqual(
            chart_entry.time_scale_manually_adjusted,
            window.time_scale_manually_adjusted,
        )
        self.assertEqual(
            chart_entry.price_scale_manually_adjusted,
            window.price_scale_manually_adjusted,
        )
        self.assertEqual(geometry, window.geometry())
        self.assertTrue(window.primary_validation_action_button.isEnabled())

        window.buy_signal_expr_line.setText("C")
        second_runs = len(runs)
        window.reset_button.click()
        self.assertEqual(second_runs + 1, len(runs))
        self.assertEqual(2, len(resets))
        self.assertIs(entry, window.commit_entry_state())
        self.assertEqual(entry_buy, window.buy_signal_expr_line.text())

    def test_entry_reset_invalidates_stale_result_and_applies_entry_to_source(self):
        pending = []

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                pending.append((_self.session, count, callback))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical, *, display_count=None):
                request = _self.session.request
                candle = {
                    "time": "20260911143000",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1,
                }
                return ValidationReplayResult(True, snapshot=ValidationReplaySnapshot(
                    stock=request.stock,
                    timeframe_minutes=request.timeframe_minutes,
                    settings_hash=request.settings_snapshot.rules_hash,
                    historical_request_id=historical.request_id,
                    evaluated_start_index=0,
                    evaluated_end_index=0,
                    dropped_raw_rows_count=0,
                    candles=[candle],
                    entries=[],
                ))

        class Source(QDialog):
            def __init__(_self):
                super().__init__()
                _self.applied = []
                _self.restored = []

            def apply_signal_validation_candidate_ui_state(_self, state):
                _self.applied.append(deepcopy(state))
                return {"applied": ["state"], "skipped": []}

            def apply_signal_validation_ui_state(_self, state):
                return _self.apply_signal_validation_candidate_ui_state(state)

            def restore_signal_validation_entry_ui_state(_self, state):
                _self.restored.append(deepcopy(state))
                return {"applied": ["state"], "skipped": []}

        def result(session, count, request_id):
            request = session.request
            return ValidationHistoricalResult(True, snapshot=ValidationHistoricalSnapshot(
                stock=request.stock,
                timeframe_minutes=request.timeframe_minutes,
                requested_count=count,
                request_id=request_id,
                rows=[{
                    "체결시간": "20260911143000",
                    "시가": "100",
                    "고가": "101",
                    "저가": "99",
                    "현재가": "100",
                    "거래량": "1",
                }],
            ))

        flow = IndicatorFollowSignalValidationFlow(
            _SnapshotBroker(True),
            host=_FakeHost(self.stock),
            historical_provider_factory=Provider,
            replay_factory=Replay,
            recent_stock_store=_MemoryRecentStockStore((self.stock,)),
        )
        source = Source()
        window = self._window()
        self.widgets.append(source)
        window.set_historical_candle_count(100)
        entry = window.commit_entry_state()
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 0
        flow._market_snapshot_generation[key] = 0
        window.validation_run_requested.connect(
            lambda request: flow._run_validation(window, request)
        )
        window.entry_reset_started.connect(
            lambda: flow._begin_entry_state_reset(window, weakref.ref(source))
        )
        reset_start_generations = []
        window.entry_reset_started.connect(
            lambda: reset_start_generations.append(flow._request_generation[key])
        )
        window.entry_reset_requested.connect(
            lambda payload: flow._restore_entry_state_to_source(
                window,
                weakref.ref(source),
                payload,
            )
        )

        window.basic_signal_interval_combo.setCurrentText("3")
        window.set_historical_candle_count(500)
        window._request_validation()
        stale_session, stale_count, stale_callback = pending[-1]
        window.buy_signal_expr_line.setText("D")
        generation_before_reset = flow._request_generation[key]
        window.reset_button.click()
        latest_session, latest_count, latest_callback = pending[-1]

        self.assertEqual([generation_before_reset + 1], reset_start_generations)
        self.assertEqual(generation_before_reset + 2, flow._request_generation[key])
        self.assertEqual(
            DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
            + flow_module.required_validation_warmup_bars(
                stale_session.request.settings_snapshot.to_dict()
            ),
            stale_count,
        )
        self.assertEqual(
            DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
            + flow_module.required_validation_warmup_bars(
                latest_session.request.settings_snapshot.to_dict()
            ),
            latest_count,
        )
        self.assertEqual("5", window.basic_signal_interval_combo.currentText())
        self.assertEqual([], source.applied)
        self.assertEqual(1, len(source.restored))
        self.assertEqual(
            IndicatorFollowSignalValidationRestorePayload(
                entry.to_ui_state()
            ).to_ui_state(),
            source.restored[0],
        )
        stale_callback(result(stale_session, stale_count, "STALE"))
        self.assertIsNone(window.replay_snapshot)
        latest_callback(result(latest_session, latest_count, "RESET"))
        self.assertEqual("RESET", window.replay_snapshot.historical_request_id)

    def test_entry_reset_aborts_before_local_mutation_without_parent_restore(self):
        flow = IndicatorFollowSignalValidationFlow(
            _SnapshotBroker(True),
            host=_FakeHost(self.stock),
            recent_stock_store=_MemoryRecentStockStore((self.stock,)),
        )
        source = QDialog()
        window = self._window()
        self.widgets.append(source)
        window.set_historical_candle_count(100)
        entry = window.commit_entry_state()
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 0
        flow._market_snapshot_generation[key] = 0
        source_ref = weakref.ref(source)
        window.entry_reset_started.connect(
            lambda: flow._begin_entry_state_reset(window, source_ref)
        )
        window.entry_reset_requested.connect(
            lambda payload: flow._restore_entry_state_to_source(
                window,
                source_ref,
                payload,
            )
        )
        runs = []
        window.validation_run_requested.connect(runs.append)
        window.buy_signal_expr_line.setText("D")

        window.reset_button.click()

        self.assertEqual("D", window.buy_signal_expr_line.text())
        self.assertNotEqual(
            entry.to_ui_state()["basic"]["buy_signal_expr_line"],
            window.buy_signal_expr_line.text(),
        )
        self.assertEqual([], runs)
        self.assertIn("원본 설정창", window.validation_status_label.text())
        self.assertEqual(1, flow._request_generation[key])

    def test_v2_entry_reset_and_parent_undo_keep_distinct_baselines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(self.rules, ensure_ascii=False),
                encoding="utf-8",
            )
            original_bytes = rules_path.read_bytes()
            with patch.object(dialog_module.QTimer, "singleShot"):
                source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(source)
            original_buy = source.buy_signal_expr_line.text()
            source.buy_signal_expr_line.setText("A or D")
            for group_name in "abc":
                getattr(
                    source,
                    f"sell_signal_condition_{group_name}_gap_left_combo",
                ).setCurrentText("평단가")
                getattr(
                    source,
                    f"sell_signal_condition_{group_name}_gap_right_combo",
                ).setCurrentText("현재가")
            seeds = []
            source.signal_validation_requested.connect(seeds.append)
            source.signal_validation_button.click()
            self.assertEqual(1, len(seeds))

            window = IndicatorFollowSignalValidationWindow(self.stock, seeds[0])
            self.widgets.append(window)
            window.commit_entry_state()
            window.entry_reset_requested.connect(
                lambda payload: source.restore_signal_validation_entry_ui_state(
                    payload.to_ui_state()
                )
            )
            source.buy_signal_expr_line.setText("C")
            window.buy_signal_expr_line.setText("D")
            window.reset_button.click()

            self.assertEqual("A or D", window.buy_signal_expr_line.text())
            self.assertEqual("A or D", source.buy_signal_expr_line.text())
            undo = source.restore_settings_undo_snapshot()
            self.assertFalse(undo["available"])
            self.assertEqual(
                dialog_module.STATE_AUTHORITY_CANONICAL_DEFAULT,
                undo["source"],
            )
            self.assertEqual("A or D", source.buy_signal_expr_line.text())
            self.assertEqual(original_bytes, rules_path.read_bytes())

    def test_unresolved_entry_reset_restores_and_runs_without_candidate_approval(self):
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                self._unresolved_seed(),
            )
        self.widgets.append(window)
        window.set_historical_candle_count(100)
        entry = window.commit_entry_state()
        entry_buy = entry.to_ui_state()["basic"]["buy_signal_expr_line"]
        starts = []
        restores = []
        runs = []
        window.entry_reset_started.connect(lambda: starts.append(True))
        window.entry_reset_requested.connect(restores.append)
        window.validation_run_requested.connect(runs.append)
        window.buy_signal_expr_line.setText("D")

        window.reset_button.click()

        self.assertNotIn(
            "V2 진입 상태를 복원할 수 없습니다.",
            window.validation_status_label.text(),
        )
        self.assertEqual(entry_buy, window.buy_signal_expr_line.text())
        self.assertEqual([True], starts)
        self.assertEqual(1, len(restores))
        self.assertEqual(1, len(runs))
        self.assertEqual(100, runs[0].candle_count)
        self.assertIs(runs[0].settings_snapshot, window._signal_validation_seed.settings_snapshot)
        self.assertIsInstance(
            restores[0],
            IndicatorFollowSignalValidationRestorePayload,
        )
        with self.assertRaisesRegex(ValueError, "재선택"):
            IndicatorFollowSignalValidationApplyPayload(entry.to_ui_state())

        candidate_payloads = []
        window.settings_apply_requested.connect(candidate_payloads.append)
        window.set_replay_snapshot(self._candle_count_snapshot(100))
        window._validated_ui_fingerprint = window._current_signal_ui_fingerprint()
        window._set_primary_validation_action_state("apply")
        status_before = window.validation_status_label.text()
        with patch(
            "gui_indicator_follow_signal_validation_window.show_toast"
        ) as toast:
            self.assertIsNone(window._request_settings_apply())
        self.assertEqual([], candidate_payloads)
        toast.assert_called_once()
        self.assertIn("매도조건 A", toast.call_args.args[1])
        self.assertEqual(status_before, window.validation_status_label.text())

    def test_registration_and_edit_restore_unresolved_entry_without_persistence(self):
        unresolved = self._unresolved_seed().to_ui_state()
        entry_buy = unresolved["basic"]["buy_signal_expr_line"]
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                rules_path = Path(temp_dir) / "rules.json"
                rules_path.write_text(
                    json.dumps(self.rules, ensure_ascii=False),
                    encoding="utf-8",
                )
                original_bytes = rules_path.read_bytes()
                kwargs = dict(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode=mode,
                )
                if mode == "edit":
                    kwargs["instance_id"] = "RESTORE-ENTRY-EDIT"
                with patch.object(dialog_module.QTimer, "singleShot"):
                    source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                        **kwargs
                    )
                self.widgets.append(source)
                source.buy_signal_expr_line.setText("D")

                result = source.restore_signal_validation_entry_ui_state(
                    IndicatorFollowSignalValidationRestorePayload(
                        unresolved
                    ).to_ui_state()
                )

                self.assertEqual([], result["skipped"])
                restored = source.collect_indicator_follow_ui_state()
                self.assertEqual(entry_buy, restored["basic"]["buy_signal_expr_line"])
                self.assertEqual(
                    "주문가",
                    restored["sell_ui"]["signal_conditions"]["condition_a"][
                        "gap_left_combo"
                    ],
                )
                source.buy_signal_expr_line.setText("C")
                strict_result = source.apply_signal_validation_ui_state(unresolved)
                self.assertTrue(strict_result["skipped"])
                self.assertEqual("C", source.buy_signal_expr_line.text())
                self.assertEqual(original_bytes, rules_path.read_bytes())

    def test_primary_settings_apply_validates_stale_state_then_emits_once(self):
        window = self._window()
        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)

        self.assertEqual("설정적용", window.primary_validation_action_button.text())
        self.assertIs(window.run_validation_button, window.primary_validation_action_button)
        self.assertFalse(hasattr(window, "apply_settings_button"))
        window.buy_signal_expr_line.setText("A or D")
        self.assertEqual([], runs)
        self.assertEqual([], applies)
        window.primary_validation_action_button.click()
        self.assertEqual(1, len(runs))
        self.assertEqual([], applies)
        window.set_replay_snapshot(
            self._replay_snapshot([
                self._entry("BUY", 0, "BUY"),
                self._entry("SELL", 2, "SELL"),
            ])
        )
        self.assertEqual(1, len(applies))
        self.assertEqual(
            "A or D",
            applies[-1].to_ui_state()["basic"]["buy_signal_expr_line"],
        )
        validation_summary = window.validation_status_label.text()
        window.show_settings_apply_result("설정 적용 완료", success=True)
        self.assertEqual(validation_summary, window.validation_status_label.text())
        self.assertNotIn("설정 적용 완료", window.validation_status_label.text())
        self.assertEqual("설정적용", window.primary_validation_action_button.text())
        self.assertTrue(window.primary_validation_action_button.isEnabled())

        window.primary_validation_action_button.click()
        self.assertEqual(1, len(runs))
        self.assertEqual(2, len(applies))

        window.buy_rsi_value_line.setText("44")
        self.assertEqual(1, len(runs))
        self.assertEqual(2, len(applies))
        window.primary_validation_action_button.click()
        self.assertEqual(2, len(runs))
        window.show_validation_error("검증 실패")
        self.assertEqual(2, len(applies))
        self.assertEqual("설정적용", window.primary_validation_action_button.text())
        self.assertTrue(window.primary_validation_action_button.isEnabled())

        window.set_validation_stock(ValidationStockRef("000660", "SK하이닉스"))
        self.assertEqual("설정적용", window.primary_validation_action_button.text())

    def test_legacy_missing_bollinger_sign_normalizes_and_allows_v2_run_and_apply(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"].pop("buy_bollinger_sign_combo", None)
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = state["sell_ui"]["signal_conditions"][group_name]
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                IndicatorFollowSignalValidationSeed(
                    ValidationSettingsSnapshot(self.rules),
                    state,
                ),
            )
        self.widgets.append(window)

        self.assertEqual(window.buy_bollinger_direction_combo.currentText(), "하단")
        self.assertEqual(window.buy_bollinger_sign_combo.currentText(), "-")
        runs = []
        window.validation_run_requested.connect(runs.append)
        with patch("gui_indicator_follow_signal_validation_window.show_toast") as toast:
            request = window.request_initial_validation()
        self.assertIsNotNone(request)
        self.assertEqual(1, len(runs))
        toast.assert_not_called()
        self.assertEqual("과거 분봉 데이터 조회 중...", window.loading_label.text())
        payload = IndicatorFollowSignalValidationApplyPayload(
            window.collect_indicator_follow_ui_state()
        )
        self.assertEqual(
            "-",
            payload.to_ui_state()["buy_ui"]["signal_filter"]
            ["buy_bollinger_sign_combo"],
        )

    def test_invalid_explicit_bollinger_sign_remains_unresolved_and_blocks_v2_run(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"] = "X"
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = state["sell_ui"]["signal_conditions"][group_name]
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                IndicatorFollowSignalValidationSeed(
                    ValidationSettingsSnapshot(self.rules),
                    state,
                ),
            )
        self.widgets.append(window)

        self.assertEqual(window.buy_bollinger_sign_combo.currentIndex(), -1)
        runs = []
        window.validation_run_requested.connect(runs.append)
        with patch("gui_indicator_follow_signal_validation_window.show_toast") as toast:
            self.assertIsNone(window.request_initial_validation())
        self.assertEqual([], runs)
        toast.assert_called_once()
        self.assertIn("+/- 부호를 선택", toast.call_args.args[1])

    def test_failed_settings_apply_validation_never_emits_candidate(self):
        window = self._window()
        runs = []
        applies = []
        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(applies.append)

        window.buy_signal_expr_line.setText("C")
        window.primary_validation_action_button.click()
        self.assertEqual(1, len(runs))
        window.show_validation_error("REPLAY: INVALID_RESULT")

        self.assertEqual([], applies)
        self.assertFalse(window._settings_apply_after_validation)
        self.assertEqual("설정적용", window.primary_validation_action_button.text())
        self.assertTrue(window.primary_validation_action_button.isEnabled())

    def test_sell_input_error_uses_toast_without_overwriting_run_status(self):
        window = self._window()
        state = window.collect_indicator_follow_ui_state()
        state["basic"]["sell_signal_expr_line"] = "C"
        condition_c = state["sell_ui"]["signal_conditions"]["condition_c"]
        condition_c["gap_check"] = True
        condition_c["gap_left_combo"] = "주문가"
        status_before = window.validation_status_label.text()
        runs = []
        window.validation_run_requested.connect(runs.append)

        with patch.object(
            window,
            "collect_indicator_follow_ui_state",
            return_value=state,
        ), patch(
            "gui_indicator_follow_signal_validation_window.show_toast"
        ) as toast:
            self.assertIsNone(window.request_validation())

        toast.assert_called_once()
        self.assertIn("매도조건 C", toast.call_args.args[1])
        self.assertEqual(status_before, window.validation_status_label.text())
        self.assertEqual([], runs)

    def test_sell_expression_error_precedes_price_error_toast(self):
        window = self._window()
        state = window.collect_indicator_follow_ui_state()
        state["basic"]["sell_signal_expr_line"] = "C AND"
        condition_c = state["sell_ui"]["signal_conditions"]["condition_c"]
        condition_c["gap_check"] = True
        condition_c["gap_left_combo"] = "주문가"
        status_before = window.validation_status_label.text()

        with patch.object(
            window,
            "collect_indicator_follow_ui_state",
            return_value=state,
        ), patch(
            "gui_indicator_follow_signal_validation_window.show_toast"
        ) as toast:
            self.assertIsNone(window.request_validation())

        toast.assert_called_once()
        self.assertIn("조합식", toast.call_args.args[1])
        self.assertNotIn("가격비교", toast.call_args.args[1])
        self.assertEqual(status_before, window.validation_status_label.text())

    def test_sell_input_error_uses_toast_without_emitting_apply_candidate(self):
        window = self._window()
        state = window.collect_indicator_follow_ui_state()
        state["basic"]["sell_signal_expr_line"] = "C"
        condition_c = state["sell_ui"]["signal_conditions"]["condition_c"]
        condition_c["gap_check"] = True
        condition_c["gap_left_combo"] = "주문가"
        window._validated_ui_fingerprint = window._signal_ui_fingerprint(state)
        window._set_primary_validation_action_state("apply")
        status_before = window.validation_status_label.text()
        candidates = []
        window.settings_apply_requested.connect(candidates.append)

        with patch.object(
            window,
            "collect_indicator_follow_ui_state",
            return_value=state,
        ), patch(
            "gui_indicator_follow_signal_validation_window.show_toast"
        ) as toast:
            self.assertIsNone(window._request_settings_apply())

        toast.assert_called_once()
        self.assertIn("매도조건 C", toast.call_args.args[1])
        self.assertEqual(status_before, window.validation_status_label.text())
        self.assertEqual([], candidates)

    def test_apply_payload_checks_only_referenced_active_sell_price_filters(self):
        state = self._window().collect_indicator_follow_ui_state()
        conditions = state["sell_ui"]["signal_conditions"]

        state["basic"]["sell_signal_expr_line"] = "C"
        conditions["condition_a"]["gap_left_combo"] = "주문가"
        conditions["condition_b"]["gap_left_combo"] = "주문가"
        self.assertIsInstance(
            IndicatorFollowSignalValidationApplyPayload(state),
            IndicatorFollowSignalValidationApplyPayload,
        )

        conditions["condition_c"]["gap_check"] = False
        conditions["condition_c"]["gap_left_combo"] = "주문가"
        self.assertIsInstance(
            IndicatorFollowSignalValidationApplyPayload(state),
            IndicatorFollowSignalValidationApplyPayload,
        )

        conditions["condition_c"]["gap_check"] = True
        with self.assertRaisesRegex(ValueError, "매도조건 C"):
            IndicatorFollowSignalValidationApplyPayload(state)

        state["basic"]["sell_signal_expr_line"] = "C AND"
        with self.assertRaisesRegex(ValueError, "조합식 오류"):
            IndicatorFollowSignalValidationApplyPayload(state)

    def test_latest_context_request_generation_owns_result(self):
        pending = []

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                pending.append((_self.session, count, callback))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical, *, display_count=None):
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

        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=_FakeHost(self.stock),
            historical_provider_factory=Provider,
            replay_factory=Replay,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        window = _FakeWindow(self.stock, self._seed())
        self.widgets.append(window)
        window_key = id(window)
        flow._open_windows[window_key] = window
        flow._request_generation[window_key] = 0

        for timeframe, count in ((3, 200), (5, 500)):
            rules = deepcopy(self.rules)
            rules["bar"]["bar_minutes"] = timeframe
            ui_state = self._seed().to_ui_state()
            ui_state["basic"]["basic_signal_interval_combo"] = str(timeframe)
            request = IndicatorFollowSignalValidationRunRequest(
                build_signal_validation_snapshot(rules, ui_state=ui_state),
                count,
            )
            flow._run_validation(window, request)

        def result(session, count, request_id):
            request = session.request
            return ValidationHistoricalResult(
                True,
                snapshot=ValidationHistoricalSnapshot(
                    stock=request.stock,
                    timeframe_minutes=request.timeframe_minutes,
                    requested_count=count,
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

        self.assertEqual(2, len(pending))
        pending[0][2](result(pending[0][0], pending[0][1], "OLD"))
        self.assertEqual([], window.snapshots)
        pending[1][2](result(pending[1][0], pending[1][1], "LATEST"))
        self.assertEqual(1, len(window.snapshots))
        self.assertEqual(5, window.snapshots[0].timeframe_minutes)
        self.assertEqual("LATEST", window.snapshots[0].historical_request_id)

    def test_result_summary_keeps_estimate_in_left_cluster(self):
        window = self._window()
        window._request_validation()
        self.assertEqual("Historical Candle 요청 중", window.validation_status_label.text())
        window.set_replay_snapshot(
            self._replay_snapshot([
                self._entry("BUY", 0, "BUY"),
                self._entry("SELL", 2, "SELL"),
            ])
        )
        window._set_validation_range(0, 2)
        window._refresh_validation_range_results()
        self.assertEqual("", window.validation_status_label.text())
        summary = " ".join(
            " ".join((
                window.validation_status_label.text(),
                window.result_summary_label.text(),
                window.estimated_return_label.text(),
            )).split()
        )
        self.assertEqual(
            "005930 삼성전자 | 5분봉 | 3캔들 | 매수신호 1 | 매도신호 1 | 기간내 추정손익 +50.00%",
            summary,
        )
        self.assertNotIn("검증 완료", summary)
        self.assertNotIn("Candle", summary)
        self.assertNotIn("BUY", summary)
        self.assertNotIn("SELL", summary)
        self.assertNotIn("추정 손익률", summary)
        self.assertEqual(window.result_summary_label.font(), window.estimated_return_label.font())
        self.assertEqual(
            window.result_summary_label.palette().color(window.result_summary_label.foregroundRole()),
            window.estimated_return_label.palette().color(window.estimated_return_label.foregroundRole()),
        )
        layout = window._signal_validation_action_layout
        self.assertEqual(0, layout.indexOf(window.validation_status_label))
        self.assertEqual(1, layout.indexOf(window.result_summary_label))
        self.assertEqual(2, layout.indexOf(window.estimated_return_label))
        self.assertIsNotNone(layout.itemAt(3).spacerItem())
        self.assertEqual(4, layout.indexOf(window.reset_button))
        self.assertEqual(5, layout.indexOf(window.primary_validation_action_button))
        self.assertEqual(6, layout.indexOf(window.close_button))
        self.assertEqual("초기화", window.reset_button.text())
        self.assertEqual(
            window.primary_validation_action_button.sizeHint().height(),
            window.reset_button.sizeHint().height(),
        )
        window.show_validation_error("검증 실패")
        self.assertEqual("검증 실패", window.validation_status_label.text())

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
        window._set_validation_range(0, 2)
        window._refresh_validation_range_results()
        self.assertEqual(1, len(window.completed_cycles))
        self.assertEqual("| 기간내 추정손익 +10.00%", window.estimated_return_label.text())

        losing_cycle = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 1, "SELL"),
        ], closes=(100.0, 90.0))
        window.set_replay_snapshot(losing_cycle)
        window._set_validation_range(0, 1)
        window._refresh_validation_range_results()
        self.assertEqual("| 기간내 추정손익 -10.00%", window.estimated_return_label.text())

        offsetting_cycles = self._replay_snapshot([
            self._entry("BUY", 0, "BUY"),
            self._entry("SELL", 1, "SELL"),
            self._entry("BUY", 2, "BUY"),
            self._entry("SELL", 3, "SELL"),
        ], closes=(100.0, 110.0, 200.0, 190.0))
        window.set_replay_snapshot(offsetting_cycles)
        window._set_validation_range(0, 3)
        window._refresh_validation_range_results()
        self.assertEqual(2, len(window.completed_cycles))
        self.assertAlmostEqual(-5.0, window.completed_cycles[-1].estimated_return_percent)
        self.assertAlmostEqual(0.0, estimated_signal_return_percent(offsetting_cycles))
        self.assertEqual("| 기간내 추정손익 0.00%", window.estimated_return_label.text())

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
        window._set_validation_range(0, 2)
        window._refresh_validation_range_results()
        self.assertEqual(1, window.canvas.marker_count("BUY"))
        self.assertEqual(1, window.canvas.marker_count("SELL"))
        self.assertEqual(2, window.selected_evaluation_index)
        self.assertFalse(hasattr(window, "selection_summary"))
        self.assertFalse(hasattr(window, "result_splitter"))
        sell_entry = next(entry for entry in window._entries if entry.evaluation_side == "SELL")
        self.assertEqual("actual-reason", sell_entry.reason)
        self.assertEqual("20260911140100", sell_entry.signal_time)
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

            def evaluate(_self, historical, *, display_count=None):
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
        self.assertEqual("", window.estimated_return_label.text())
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

            def evaluate(_self, historical, *, display_count=None):
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
        self.assertEqual(5_000, DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT)
        self.assertEqual(DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT, window.historical_candle_count)
        self.assertEqual(1, window.entry_commit_count)
        resolved_ui_state = self._seed().to_ui_state()
        expected_counts = []
        expected_timeframes = []
        for timeframe, candle_count in ((3, 300), (15, 500)):
            rules = deepcopy(self.rules)
            rules["bar"]["bar_minutes"] = timeframe
            snapshot = build_signal_validation_snapshot(
                rules,
                ui_state=resolved_ui_state,
            )
            warmup = flow_module.required_validation_warmup_bars(
                snapshot.to_dict()
            )
            expected_counts.append(
                DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT + warmup
            )
            expected_timeframes.append(timeframe)
            window.validation_run_requested.emit(
                IndicatorFollowSignalValidationRunRequest(
                    snapshot,
                    candle_count,
                )
            )
        self.assertEqual(
            expected_timeframes,
            [session.request.timeframe_minutes for session in sessions],
        )
        self.assertEqual(expected_counts, requested_counts)
        self.assertEqual(
            expected_timeframes,
            [snapshot.timeframe_minutes for snapshot in window.snapshots],
        )

    def test_full_history_loads_once_then_reuses_shared_pool(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        requested_counts = []
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()

        class Provider:
            def __init__(_self, session, requester):
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
                        request_id=f"REQ-{count}",
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

            def evaluate(_self, historical, *, display_count=None):
                request = _self.session.request
                return ValidationReplayResult(
                    True,
                    snapshot=ValidationReplaySnapshot(
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
                    ),
                )

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=1_000,
            historical_provider_factory=Provider,
            replay_factory=Replay,
            window_factory=_FakeWindow,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        seed = self._seed()
        request = IndicatorFollowSignalValidationRunRequest(
            seed.settings_snapshot,
            100,
        )
        warmup = flow_module.required_validation_warmup_bars(
            seed.settings_snapshot.to_dict()
        )

        first = _FakeWindow(self.stock, seed)
        second = _FakeWindow(self.stock, seed)
        self.widgets.extend((first, second))
        flow._open_windows[id(first)] = first
        flow._request_generation[id(first)] = 0
        flow._run_validation(first, request)

        self.assertEqual(
            [1_000 + warmup],
            requested_counts,
        )
        self.assertEqual(
            [(1, 1_000)],
            first.pool_installs,
        )
        self.assertEqual(1, len(first.snapshots))

        flow._run_validation(first, request)

        self.assertEqual(
            [1_000 + warmup],
            requested_counts,
        )
        self.assertEqual(
            [(1, 1_000), (1, 1_000)],
            first.pool_installs,
        )
        self.assertEqual(2, len(first.snapshots))

        changed_rules = seed.settings_snapshot.to_dict()
        changed_rules["validation_execution"] = {
            "enabled": True,
            "first_buy_quantity": 1,
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
            "round_operator": "ADD",
            "round_budget_value": 0.5,
            "active_direction": "UP",
            "active_ratio": 0.45,
            "active_compare": ">=",
        }
        flow._run_validation(
            first,
            IndicatorFollowSignalValidationRunRequest(
                ValidationSettingsSnapshot(changed_rules),
                100,
            ),
        )
        self.assertEqual([1_000 + warmup], requested_counts)
        self.assertEqual(3, len(first.snapshots))

        flow._open_windows[id(second)] = second
        flow._request_generation[id(second)] = 0
        flow._run_validation(second, request)

        self.assertEqual(
            [1_000 + warmup],
            requested_counts,
        )
        self.assertEqual([(1, 1_000)], second.pool_installs)
        self.assertEqual(1, len(second.snapshots))
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()

    def test_persistent_history_incrementally_replays_after_restart(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        cache = IndicatorFollowSignalValidationHistoricalCache(
            Path(self.history_cache_temporary.name) / "restart"
        )
        requested_counts = []
        seed = self._seed()
        timeframe = seed.settings_snapshot.to_dict()["bar"]["bar_minutes"]
        request = IndicatorFollowSignalValidationRunRequest(
            seed.settings_snapshot,
            2,
        )
        warmup = flow_module.required_validation_warmup_bars(
            seed.settings_snapshot.to_dict()
        )
        fetch_count = 8 + warmup
        latest = datetime.now(flow_module.SEOUL_TIMEZONE).replace(
            second=0,
            microsecond=0,
        ) - timedelta(minutes=timeframe * 2)

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                requested_counts.append(count)
                rows = []
                for offset in range(count):
                    candle_time = latest - timedelta(
                        minutes=timeframe * (count - offset - 1)
                    )
                    rows.append({
                        "체결시간": candle_time.strftime("%Y%m%d%H%M%S"),
                        "시가": "100",
                        "고가": "101",
                        "저가": "99",
                        "현재가": str(100 + offset),
                        "거래량": "1",
                    })
                callback(ValidationHistoricalResult(
                    True,
                    snapshot=ValidationHistoricalSnapshot(
                        stock=_self.session.request.stock,
                        timeframe_minutes=timeframe,
                        requested_count=count,
                        request_id=f"REQ-{len(requested_counts)}",
                        rows=rows,
                    ),
                ))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical, *, display_count=None):
                candles, dropped = flow_module.project_validation_candles(historical)
                return ValidationReplayResult(
                    True,
                    snapshot=ValidationReplaySnapshot(
                        stock=_self.session.request.stock,
                        timeframe_minutes=timeframe,
                        settings_hash=_self.session.request.settings_snapshot.rules_hash,
                        historical_request_id=historical.request_id,
                        evaluated_start_index=0,
                        evaluated_end_index=max(0, len(candles) - 1),
                        dropped_raw_rows_count=dropped,
                        candles=candles,
                        entries=[],
                    ),
                )

        first_flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=8,
            historical_provider_factory=Provider,
            replay_factory=Replay,
            recent_stock_store=_MemoryRecentStockStore(),
            historical_cache=cache,
        )
        first_window = _FakeWindow(self.stock, seed)
        self.widgets.append(first_window)
        first_flow._open_windows[id(first_window)] = first_window
        first_flow._request_generation[id(first_window)] = 0
        first_flow._run_validation(first_window, request)
        self.assertEqual([fetch_count], requested_counts)
        self.assertIsNotNone(cache.load(self.stock.code, timeframe, fetch_count))

        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
        second_flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=8,
            historical_provider_factory=Provider,
            replay_factory=Replay,
            recent_stock_store=_MemoryRecentStockStore(),
            historical_cache=cache,
        )
        second_window = _FakeWindow(self.stock, seed)
        self.widgets.append(second_window)
        second_flow._open_windows[id(second_window)] = second_window
        second_flow._request_generation[id(second_window)] = 0
        second_flow._run_validation(second_window, request)

        self.assertEqual(2, len(requested_counts))
        self.assertLess(requested_counts[1], fetch_count)
        self.assertEqual([(fetch_count, 8)], second_window.pool_installs)
        self.assertEqual(1, len(second_window.snapshots))

        second_flow._run_validation(second_window, request)
        self.assertEqual(2, len(requested_counts))
        self.assertEqual(2, len(second_window.snapshots))

    def test_failed_incremental_and_full_fallback_preserve_prior_cache(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        cache = IndicatorFollowSignalValidationHistoricalCache(
            Path(self.history_cache_temporary.name) / "failed-incremental"
        )
        seed = self._seed()
        timeframe = seed.settings_snapshot.to_dict()["bar"]["bar_minutes"]
        warmup = flow_module.required_validation_warmup_bars(
            seed.settings_snapshot.to_dict()
        )
        fetch_count = 6 + warmup
        latest = datetime.now(flow_module.SEOUL_TIMEZONE).replace(
            second=0,
            microsecond=0,
        ) - timedelta(minutes=timeframe * 2)
        cached_candles = [
            {
                "time": (latest - timedelta(minutes=timeframe * offset)).strftime(
                    "%Y%m%d%H%M%S"
                ),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
            }
            for offset in reversed(range(fetch_count))
        ]
        self.assertTrue(cache.store(
            stock_code=self.stock.code,
            stock_name=self.stock.name,
            timeframe_minutes=timeframe,
            requested_count=fetch_count,
            candles=cached_candles,
        ))
        cache_path = cache.path_for(self.stock.code, timeframe, fetch_count)
        original_bytes = cache_path.read_bytes()
        requested_counts = []

        class Provider:
            def __init__(_self, session, requester):
                pass

            def request_latest(_self, count, callback):
                requested_counts.append(count)
                callback(ValidationHistoricalResult(False, reason="FAILED"))

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=6,
            historical_provider_factory=Provider,
            recent_stock_store=_MemoryRecentStockStore(),
            historical_cache=cache,
        )
        window = _FakeWindow(self.stock, seed)
        self.widgets.append(window)
        flow._open_windows[id(window)] = window
        flow._request_generation[id(window)] = 0
        flow._run_validation(
            window,
            IndicatorFollowSignalValidationRunRequest(seed.settings_snapshot, 2),
        )

        self.assertEqual(2, len(requested_counts))
        self.assertLess(requested_counts[0], fetch_count)
        self.assertEqual(fetch_count, requested_counts[1])
        self.assertEqual(original_bytes, cache_path.read_bytes())
        self.assertTrue(window.errors)

    def test_source_boundaries_do_not_create_broker_or_mutation_paths(self):
        source = (self.project_root / "gui_indicator_follow_signal_validation_flow.py").read_text(encoding="utf-8")
        self.assertNotIn("KiwoomApi(", source)
        self.assertNotIn("request_minute_candles(", source)
        for forbidden in ("SendOrder", "Chejan", "StockRepository", "mock_validation"):
            self.assertNotIn(forbidden, source)
        for caller_name in ("gui_windows.py", "gui_auto_trade_setting_window.py"):
            caller = (self.project_root / caller_name).read_text(encoding="utf-8")
            self.assertNotIn("bind_indicator_follow_validation_flow", caller)
            self.assertNotIn("bind_indicator_follow_signal_validation_flow", caller)
        settings_source = (
            self.project_root / "gui_indicator_follow_routine_settings_dialog.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_bind_optional_signal_validation", settings_source)
        chart_source = (
            self.project_root / "gui_indicator_follow_signal_validation_window.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("gui_indicator_follow_validation_chart_window", chart_source)
        self.assertFalse(
            (self.project_root / "gui_indicator_follow_validation_flow.py").exists()
        )
        self.assertFalse(
            (self.project_root / "gui_indicator_follow_validation_chart_window.py").exists()
        )


if __name__ == "__main__":
    unittest.main()
