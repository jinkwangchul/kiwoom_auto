# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from threading import Event
import tempfile
import unittest
from unittest.mock import Mock, patch
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
    _completed_cycle_financial_summary,
    _marker_records,
    _time_axis_label_records,
    _validation_price_text,
    estimated_signal_return_percent,
)
from indicator_follow_signal_validation_execution import (
    normalize_validation_execution_policy,
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
    historical_extension_requested = pyqtSignal()
    settings_apply_requested = pyqtSignal(object)
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)
    recent_stock_remove_requested = pyqtSignal(object)

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
        self.discarded_stock_codes = []

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

    def discard_validation_stock_if_matches(self, stock_code):
        code = str(stock_code or "").strip()
        if self.stock is None or self.stock.code != code:
            return False
        self.discarded_stock_codes.append(code)
        self.stock = None
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
        updated = updated[:20]
        if updated == self._stocks:
            return False
        self._stocks = updated
        self.write_count += 1
        return True

    def remove(self, stock):
        code = stock.code if isinstance(stock, ValidationStockRef) else str(stock or "")
        updated = tuple(candidate for candidate in self._stocks if candidate.code != code)
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
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertEqual(0, window.completed_cycle_table.rowCount())
        self.assertNotIn("매수신호", window.result_summary_label.text())
        self.assertNotIn("매도신호", window.result_summary_label.text())
        self.assertEqual("", window.estimated_return_label.text())

        window._begin_validation_range_drag(2)
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertEqual(2, window._validation_range_anchor_index)
        self.assertEqual(0, len(window.completed_cycles))
        self.assertEqual("", window.estimated_return_label.text())

        window._complete_validation_range_drag(2, 3)
        self.assertEqual((2, 3), window.validation_range)
        self.assertEqual((2, 3), window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertEqual(2, window._validation_range_anchor_index)
        self.assertEqual(1, len(window.completed_cycles))
        cycle = window.completed_cycles[0]
        self.assertEqual(2, cycle.buy_start_index)
        self.assertEqual(3, cycle.sell_index)
        self.assertIn("1", window.completed_cycle_table.item(0, 0).text())
        self.assertIn("2", window.result_summary_label.text())

        window._begin_validation_range_drag(4)
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertEqual(4, window._validation_range_anchor_index)
        self.assertEqual(0, len(window.completed_cycles))

        # End must remain strictly to the right of the start candle.
        window._complete_validation_range_drag(4, 2)
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window._validation_range_anchor_index)
        self.assertIsNone(window.canvas.selection_indicator_index)

        window._begin_validation_range_drag(2)
        window._complete_validation_range_drag(2, 5)
        self.assertEqual((2, 5), window.validation_range)
        self.assertEqual(2, window._validation_range_anchor_index)
        self.assertIsNone(window.canvas.selection_indicator_index)

        # A later explicit clear removes period + yellow line.
        window._clear_validation_range_interaction()
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window._validation_range_anchor_index)
        self.assertIsNone(window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)

    def test_validation_range_survives_prepend_history_by_candle_time(self):
        window = self._window()
        window.set_replay_snapshot(self._candle_count_snapshot(1_000))
        window._begin_validation_range_drag(20)
        window._complete_validation_range_drag(20, 40)
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

    def test_loaded_five_thousand_pool_starts_at_latest_two_hundred_fifty(self):
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
        self.assertEqual(250.0, window.visible_candle_span)
        self.assertEqual((4_750, 5_000), window.canvas._visible_index_bounds())
        self.assertGreater(window.canvas.price_scale().minimum, 4_000)
        self.assertIsNotNone(window.visualization_cache)
        self.assertEqual(5_000, window.visualization_cache.candle_count)
        self.assertGreater(len(window.visualization_cache.series), 0)
        self.assertNotIn("\ucd1d", window.result_summary_label.text())
        self.assertNotIn("5000", window.result_summary_label.text())

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
        self.assertNotEqual(250.0, window.visible_candle_span)

        window.set_historical_candle_pool(pool, chart_candle_count=5_000)
        window.set_replay_snapshot(replay("timeframe-changed-hash", 3))
        self.app.processEvents()

        self.assertEqual(250.0, window.visible_candle_span)
        self.assertEqual(4_750.0, window.visible_start_index)
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
        QTest.mouseClick(window.primary_validation_action_button, Qt.LeftButton)
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
        self.assertEqual(4_000.0, window.visible_candle_span)

    def test_left_edge_pan_requests_history_extension_without_validation_rerun(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        replay = self._candle_count_snapshot(500)
        window.set_historical_candle_pool(
            replay.to_candles(),
            chart_candle_count=500,
        )
        window.set_replay_snapshot(replay)
        self.app.processEvents()
        extensions = []
        runs = []
        window.historical_extension_requested.connect(lambda: extensions.append(True))
        window.validation_run_requested.connect(runs.append)
        window._set_time_view(80.0, 250.0, manually_adjusted=True)
        with patch.object(dialog_module.QTimer, "singleShot") as single_shot:
            window._pan_chart_view(100.0, 0.0)
            first_callback = single_shot.call_args.args[1]
            window._pan_chart_view(100.0, 0.0)
            second_callback = single_shot.call_args.args[1]
        self.assertEqual([], extensions)
        first_callback()
        self.assertEqual([], extensions)
        second_callback()
        self.assertEqual([True], extensions)
        self.assertEqual([], runs)

    def test_prepend_history_preserves_visible_time_span_and_apply_state(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        expanded = self._candle_count_snapshot(1_000).to_candles()
        initial = expanded[-500:]
        initial_replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
            historical_request_id="EXTENSION-INITIAL",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=initial[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(initial, chart_candle_count=500)
        window.set_replay_snapshot(initial_replay)
        window._validated_ui_fingerprint = window._current_signal_ui_fingerprint()
        window._set_primary_validation_action_state("apply")
        window._set_time_view(40.0, 120.0, manually_adjusted=True)
        start_time = window._candles[40]["time"]
        expanded_replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=initial_replay.settings_hash,
            historical_request_id="EXTENSION-EXPANDED",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=expanded[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(expanded, chart_candle_count=1_000)
        window.set_replay_snapshot(expanded_replay)
        visible_index = int(window.visible_start_index)
        self.assertEqual(start_time, window._candles[visible_index]["time"])
        self.assertEqual(120.0, window.visible_candle_span)
        self.assertEqual("apply", window._primary_validation_action_state)
        self.assertEqual(1_000, window.historical_candle_count)

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
        self.assertIs(
            window.validation_trading_cost_separator,
            header_layout.itemAt(execution_index + 1).widget(),
        )
        self.assertIs(
            window.validation_trading_cost_box,
            header_layout.itemAt(execution_index + 2).widget(),
        )
        self.assertIs(
            window.validation_regular_market_separator,
            header_layout.itemAt(execution_index + 3).widget(),
        )
        self.assertIs(
            window.validation_regular_market_only_check,
            header_layout.itemAt(execution_index + 4).widget(),
        )
        self.assertIsNotNone(header_layout.itemAt(execution_index + 5).spacerItem())
        self.assertEqual("평단관리:", window.validation_averaging_enabled_check.text())
        self.assertFalse(window.validation_averaging_enabled_check.isChecked())
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertEqual("1", window.validation_first_buy_quantity_line.text())
        self.assertEqual("예산기준", window.validation_repeat_mode_combo.currentText())
        self.assertEqual("시작예산", window.validation_start_budget_label.text())
        self.assertEqual("주", window.validation_first_buy_unit_label.text())
        self.assertEqual(106, window.validation_repeat_mode_combo.width())
        self.assertEqual(308, window.validation_repeat_stack.width())
        self.assertEqual(50, window.validation_round_budget_line.width())
        self.assertEqual(50, window.validation_budget_ratio_line.width())
        self.assertEqual(50, window.validation_active_ratio_line.width())
        self.assertEqual(76, window.validation_active_direction_combo.width())
        self.assertEqual(76, window.validation_active_compare_combo.width())
        self.assertEqual("직전회차", window.validation_round_previous_label.text())
        self.assertEqual("x 시작예산", window.validation_round_start_budget_label.text())
        self.assertEqual(
            "직전예산",
            window.validation_budget_previous_amount_label.text(),
        )
        self.assertEqual("x", window.validation_budget_multiply_label.text())
        self.assertEqual("평단", window.validation_active_average_label.text())
        self.assertEqual("%", window.validation_active_percent_label.text())
        self.assertEqual("2.5", window.validation_budget_ratio_line.text())

        detail_layout = window.validation_execution_detail_widget.layout()
        self.assertEqual(7, detail_layout.spacing())
        self.assertEqual(
            6,
            window.validation_repeat_stack.widget(0).layout().spacing(),
        )
        self.assertEqual(
            6,
            window.validation_repeat_stack.widget(1).layout().spacing(),
        )
        self.assertEqual(
            5,
            window.validation_repeat_stack.widget(2).layout().spacing(),
        )
        for label in (
            window.validation_start_budget_label,
            window.validation_first_buy_unit_label,
            window.validation_round_previous_label,
            window.validation_round_start_budget_label,
            window.validation_budget_previous_amount_label,
            window.validation_budget_multiply_label,
            window.validation_active_average_label,
            window.validation_active_percent_label,
        ):
            self.assertGreaterEqual(
                label.minimumWidth(),
                QFontMetrics(label.font()).horizontalAdvance(label.text()) + 8,
            )
        for page_index in range(window.validation_repeat_stack.count()):
            page = window.validation_repeat_stack.widget(page_index)
            self.assertLessEqual(
                page.layout().sizeHint().width(),
                window.validation_repeat_stack.width(),
            )

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

    def test_validation_execution_apply_keeps_execution_settings_chart_local(self):
        window = self._window()

        window.validation_repeat_mode_combo.setCurrentText("예산기준")
        window.validation_budget_ratio_line.setText("2.5")
        window.validation_averaging_enabled_check.setChecked(True)
        budget_apply = IndicatorFollowSignalValidationApplyPayload(
            window.collect_indicator_follow_ui_state()
        ).to_ui_state()
        self.assertNotIn("validation_execution", budget_apply)
        self.assertNotIn("repeat", budget_apply.get("buy_ui", {}))

        window.validation_averaging_enabled_check.setChecked(False)
        window.validation_repeat_mode_combo.setCurrentText("능동매수")
        window.validation_active_direction_combo.setCurrentText("상하")
        window.validation_active_ratio_line.setText("1.25")
        window.validation_active_compare_combo.setCurrentText("이탈")
        window.validation_averaging_enabled_check.setChecked(True)
        active_apply = IndicatorFollowSignalValidationApplyPayload(
            window.collect_indicator_follow_ui_state()
        ).to_ui_state()
        self.assertNotIn("validation_execution", active_apply)
        self.assertNotIn("repeat", active_apply.get("buy_ui", {}))

    def test_validation_budget_ratio_rejects_one_or_below_and_normalizes_on_finish(self):
        window = self._window()
        line = window.validation_budget_ratio_line

        for invalid in ("0", "0.5", "1", "1.0"):
            with self.subTest(invalid=invalid):
                line.setText(invalid)
                self.assertFalse(line.hasAcceptableInput())

        line.setText("1")
        line.editingFinished.emit()
        self.assertEqual("2.0", line.text())
        self.assertTrue(line.hasAcceptableInput())

        line.setText("1.000001")
        self.assertTrue(line.hasAcceptableInput())

    def test_validation_active_options_preserve_both_within_and_outside(self):
        window = self._window()
        self.assertEqual(
            ["이상", "이하", "이내", "이탈"],
            [
                window.validation_active_compare_combo.itemText(index)
                for index in range(window.validation_active_compare_combo.count())
            ],
        )
        window.validation_repeat_mode_combo.setCurrentText("능동매수")
        window.validation_active_direction_combo.setCurrentText("상하")
        window.validation_active_compare_combo.setCurrentText("이탈")
        window.validation_averaging_enabled_check.setChecked(True)
        execution = window.collect_indicator_follow_ui_state()["validation_execution"]
        self.assertEqual("BOTH", execution["active_direction"])
        self.assertEqual("OUTSIDE", execution["active_compare"])

        window.validation_averaging_enabled_check.setChecked(False)
        window.validation_active_direction_combo.setCurrentText("상향")
        window.validation_active_compare_combo.setCurrentText("이탈")
        window.validation_averaging_enabled_check.setChecked(True)
        free_execution = window.collect_indicator_follow_ui_state()["validation_execution"]
        self.assertEqual("UP", free_execution["active_direction"])
        self.assertEqual("OUTSIDE", free_execution["active_compare"])
        apply_state = IndicatorFollowSignalValidationApplyPayload(
            window.collect_indicator_follow_ui_state()
        ).to_ui_state()
        self.assertNotIn("repeat", apply_state.get("buy_ui", {}))

    def test_validation_cost_and_regular_market_scope_controls_are_validation_only(self):
        window = self._window()

        self.assertEqual(
            "거래비용 :",
            window.validation_trading_cost_enabled_check.text(),
        )
        self.assertFalse(window.validation_trading_cost_enabled_check.isChecked())
        self.assertEqual("0.2", window.validation_trading_cost_percent_line.text())
        self.assertEqual(40, window.validation_trading_cost_percent_line.width())
        self.assertTrue(window.validation_trading_cost_percent_line.isEnabled())
        self.assertEqual(
            "정규장만 표시",
            window.validation_regular_market_only_check.text(),
        )
        self.assertFalse(window.validation_regular_market_only_check.isChecked())

        state = window.collect_indicator_follow_ui_state()
        execution = state["validation_execution"]
        self.assertFalse(execution["trading_cost_enabled"])
        self.assertEqual(0.2, execution["trading_cost_percent"])
        self.assertEqual(
            {"regular_market_only": False},
            state["validation_market_scope"],
        )

        apply_state = IndicatorFollowSignalValidationApplyPayload(state).to_ui_state()
        self.assertNotIn("validation_execution", apply_state)
        self.assertNotIn("validation_market_scope", apply_state)

        runs = []
        window.validation_run_requested.connect(runs.append)
        window.validation_trading_cost_enabled_check.setChecked(True)
        self.assertTrue(window.validation_trading_cost_percent_line.isEnabled())
        self.assertEqual(1, len(runs))
        self.assertTrue(
            runs[-1].settings_snapshot.to_dict()["validation_execution"][
                "trading_cost_enabled"
            ]
        )

        window.validation_trading_cost_enabled_check.setChecked(False)
        self.assertTrue(window.validation_trading_cost_percent_line.isEnabled())
        self.assertEqual(2, len(runs))
        self.assertFalse(
            runs[-1].settings_snapshot.to_dict()["validation_execution"][
                "trading_cost_enabled"
            ]
        )

        window.validation_regular_market_only_check.setChecked(True)
        self.assertEqual(3, len(runs))
        run_rules = runs[-1].settings_snapshot.to_dict()
        self.assertEqual(
            {"regular_market_only": True},
            run_rules["validation_market_scope"],
        )

        window.validation_regular_market_only_check.setChecked(False)
        self.assertEqual(4, len(runs))
        self.assertEqual(
            {"regular_market_only": False},
            runs[-1].settings_snapshot.to_dict()["validation_market_scope"],
        )

    def test_averaging_checked_freezes_controls_without_disabled_appearance(self):
        window = self._window()
        lines = (
            window.validation_first_buy_quantity_line,
            window.validation_round_budget_line,
            window.validation_budget_ratio_line,
            window.validation_active_ratio_line,
        )
        combos = (
            window.validation_repeat_mode_combo,
            window.validation_round_operator_combo,
            window.validation_active_direction_combo,
            window.validation_active_compare_combo,
        )
        unlocked_styles = {
            control: control.styleSheet()
            for control in (*lines, *combos)
        }
        unlocked_focus = {
            combo: combo.focusPolicy()
            for combo in combos
        }

        self.assertFalse(window.validation_averaging_enabled_check.isChecked())
        self.assertFalse(window._validation_execution_locked)
        self.assertTrue(all(not line.isReadOnly() for line in lines))

        window.validation_first_buy_quantity_line.setText("7")
        window.validation_repeat_mode_combo.setCurrentText("예산기준")
        original_quantity = window.validation_first_buy_quantity_line.text()
        original_mode = window.validation_repeat_mode_combo.currentText()

        window.validation_averaging_enabled_check.setChecked(True)

        self.assertTrue(window._validation_execution_locked)
        self.assertTrue(window.validation_averaging_enabled_check.isEnabled())
        self.assertTrue(all(line.isReadOnly() for line in lines))
        for line in lines:
            self.assertIn("background: transparent", line.styleSheet())
            self.assertNotIn("color:", line.styleSheet())
        for combo in combos:
            style = combo.styleSheet()
            self.assertEqual(Qt.NoFocus, combo.focusPolicy())
            self.assertIn("background: transparent", style)
            self.assertIn("QComboBox::down-arrow", style)
            self.assertIn("image: none", style)

        window.validation_first_buy_quantity_line.setFocus()
        QTest.keyClicks(window.validation_first_buy_quantity_line, "99")
        QTest.keyClick(window.validation_repeat_mode_combo, Qt.Key_Down)
        self.app.processEvents()
        self.assertEqual(original_quantity, window.validation_first_buy_quantity_line.text())
        self.assertEqual(original_mode, window.validation_repeat_mode_combo.currentText())

        window.validation_averaging_enabled_check.setChecked(False)

        self.assertFalse(window._validation_execution_locked)
        self.assertTrue(all(not line.isReadOnly() for line in lines))
        for control in (*lines, *combos):
            self.assertEqual(unlocked_styles[control], control.styleSheet())
        for combo in combos:
            self.assertEqual(unlocked_focus[combo], combo.focusPolicy())

    def test_averaging_toggle_is_immediate_and_restore_is_signal_silent(self):
        window = self._window()
        runs = []
        window.validation_run_requested.connect(runs.append)

        window.validation_averaging_enabled_check.setChecked(True)
        self.assertEqual(1, len(runs))
        self.assertTrue(
            runs[-1].settings_snapshot.to_dict()["validation_execution"]["enabled"]
        )
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertTrue(window._validation_execution_locked)
        self.assertTrue(window.validation_first_buy_quantity_line.isReadOnly())

        window.validation_averaging_enabled_check.setChecked(False)
        self.assertEqual(2, len(runs))
        self.assertFalse(
            runs[-1].settings_snapshot.to_dict()["validation_execution"]["enabled"]
        )
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertFalse(window._validation_execution_locked)
        self.assertFalse(window.validation_first_buy_quantity_line.isReadOnly())

        restored = window.collect_indicator_follow_ui_state()
        restored["validation_execution"]["enabled"] = True
        restored["validation_execution"]["trading_cost_enabled"] = True
        restored["validation_market_scope"]["regular_market_only"] = True
        window.restore_signal_validation_entry_ui_state(restored)

        self.assertEqual(2, len(runs))
        self.assertTrue(window.validation_averaging_enabled_check.isChecked())
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertTrue(window.validation_trading_cost_enabled_check.isChecked())
        self.assertTrue(window.validation_trading_cost_percent_line.isEnabled())
        self.assertTrue(window.validation_regular_market_only_check.isChecked())
        self.assertTrue(window._validation_execution_locked)
        self.assertTrue(window.validation_first_buy_quantity_line.isReadOnly())

        restored["validation_execution"]["enabled"] = False
        restored["validation_execution"]["trading_cost_enabled"] = False
        restored["validation_market_scope"]["regular_market_only"] = False
        window.restore_signal_validation_entry_ui_state(restored)

        self.assertEqual(2, len(runs))
        self.assertFalse(window.validation_averaging_enabled_check.isChecked())
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
        self.assertFalse(window.validation_trading_cost_enabled_check.isChecked())
        self.assertTrue(window.validation_trading_cost_percent_line.isEnabled())
        self.assertFalse(window.validation_regular_market_only_check.isChecked())
        self.assertFalse(window._validation_execution_locked)
        self.assertFalse(window.validation_first_buy_quantity_line.isReadOnly())

    def test_invalid_enabled_validation_trading_cost_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "VALIDATION_TRADING_COST_PERCENT_INVALID",
        ):
            normalize_validation_execution_policy({
                "enabled": False,
                "trading_cost_enabled": True,
                "trading_cost_percent": "not-a-number",
            })

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
        self.assertTrue(window._validation_execution_locked)
        entry = window.commit_entry_state()

        window.validation_averaging_enabled_check.setChecked(False)
        self.assertTrue(window.validation_execution_detail_widget.isEnabled())
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
        self.assertEqual("예산기준", window.validation_repeat_mode_combo.currentText())
        self.assertEqual("2.0", window.validation_budget_ratio_line.text())
        self.assertTrue(window._validation_execution_locked)
        self.assertTrue(window.validation_budget_ratio_line.isReadOnly())

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

    def test_range_tooltips_append_only_meaningful_execution_information(self):
        window = self._window()
        rules = window._signal_validation_seed.settings_snapshot.to_dict()
        rules["validation_execution"] = {
            "enabled": False,
            "first_buy_quantity": 1,
            "repeat_mode": "ROUND",
            "round_operator": "ADD",
            "round_budget_value": 0.5,
            "budget_ratio": 0.5,
            "active_direction": "UP",
            "active_ratio": 0.45,
            "active_compare": ">=",
            "trading_cost_enabled": False,
            "trading_cost_percent": 0.2,
        }
        settings = ValidationSettingsSnapshot(rules)
        prices = (100.0, 100.0, 120.0, 125.0, 130.0)
        candles = [
            {
                "time": f"2026091114{index:02d}00",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
            }
            for index, price in enumerate(prices)
        ]
        entries = [
            self._entry("BUY", 0, "BUY"),
            self._entry("BUY", 1, "BUY"),
            self._entry("SELL", 2, "SELL"),
            # No holding remains here, so this displayed SELL has no
            # executable meaning inside the selected range.
            self._entry("SELL", 3, "SELL"),
            # Outside the selected range: must keep the ordinary tooltip.
            self._entry("BUY", 4, "BUY"),
        ]
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=settings.rules_hash,
            historical_request_id="RANGE-TOOLTIP-EXECUTION",
            evaluated_start_index=0,
            evaluated_end_index=4,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )
        window._pending_result_settings_snapshot = settings
        window.set_replay_snapshot(replay)
        base_tooltips = dict(window._signal_tooltips)

        window._set_validation_range(0, 3)
        window._refresh_validation_range_results()

        buy_first = window._signal_tooltips[(0, "BUY")]
        buy_second = window._signal_tooltips[(1, "BUY")]
        sell = window._signal_tooltips[(2, "SELL")]

        self.assertIn("▪1차 / 1주 / 100원", buy_first)
        self.assertIn("▪총 1주 / 100원", buy_first)
        self.assertIn("▪2차 / 1주 / 100원", buy_second)
        self.assertIn("▪총 2주 / 200원", buy_second)

        self.assertIn("▪2주 / 합계 240원", sell)
        self.assertNotIn("차 /", sell)

        # Signals with no virtual fill stay byte-for-byte equivalent to their
        # ordinary (outside-range) tooltip content.
        self.assertEqual(
            base_tooltips[(3, "SELL")],
            window._signal_tooltips[(3, "SELL")],
        )
        self.assertEqual(
            base_tooltips[(4, "BUY")],
            window._signal_tooltips[(4, "BUY")],
        )

        # Cancelling the range restores every marker tooltip to the ordinary
        # non-range content.
        window._set_validation_range(None, None)
        window._refresh_validation_range_results()
        self.assertEqual(base_tooltips, window._signal_tooltips)

    def test_v2_trading_cost_reduces_cycle_return_and_profit_summary(self):
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
            "trading_cost_enabled": True,
            "trading_cost_percent": 0.2,
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
            historical_request_id="VALIDATION-TRADING-COST",
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
        self.assertEqual(300.0, cycle.buy_cost)
        self.assertAlmostEqual(0.6, cycle.trading_cost_amount)
        self.assertAlmostEqual(19.8, cycle.estimated_return_percent)
        invested, profit, average, sell_price = _completed_cycle_financial_summary(
            window.completed_cycles
        )
        self.assertEqual(300.0, invested)
        self.assertAlmostEqual(59.4, profit)
        self.assertEqual(100.0, average)
        self.assertEqual(120.0, sell_price)
        self.assertEqual("300원", window.completed_cycle_table.item(0, 3).text())
        self.assertEqual("+59원", window.completed_cycle_table.item(0, 7).text())
        self.assertEqual("+19.80%", window.completed_cycle_table.item(0, 8).text())
        self.assertIn("+19.80%", window.estimated_return_label.text())

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

    def test_same_settings_second_replay_preserves_visualization_snapshot(self):
        window = self._window()
        request = window._request_validation()
        self.assertIsNotNone(request)
        self.assertNotEqual(
            window._signal_validation_seed.settings_snapshot.rules_hash,
            request.settings_snapshot.rules_hash,
        )
        candles = self._candle_count_snapshot(300).to_candles()
        timeframe = request.settings_snapshot.to_dict()["bar"]["bar_minutes"]

        def replay(request_id):
            return ValidationReplaySnapshot(
                stock=self.stock,
                timeframe_minutes=timeframe,
                settings_hash=request.settings_snapshot.rules_hash,
                historical_request_id=request_id,
                evaluated_start_index=200,
                evaluated_end_index=299,
                dropped_raw_rows_count=0,
                candles=candles[-100:],
                entries=[],
            )

        window.set_historical_candle_pool(candles, chart_candle_count=300)
        window.set_replay_snapshot(replay("CACHE-FIRST"))
        first_snapshot = window._result_settings_snapshot
        first_descriptor_count = len(window.visualization_descriptors)

        self.assertIs(first_snapshot, request.settings_snapshot)
        self.assertGreater(first_descriptor_count, 0)
        self.assertIsNotNone(window.visualization_cache)

        window.set_historical_candle_pool(candles, chart_candle_count=300)
        window.set_replay_snapshot(replay("BACKGROUND-REFRESH"))

        self.assertIs(window._result_settings_snapshot, first_snapshot)
        self.assertEqual(
            first_descriptor_count,
            len(window.visualization_descriptors),
        )
        self.assertIsNotNone(window.visualization_cache)

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
        window.basic_signal_interval_combo.setCurrentText("15\ubd84")
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
        window.set_replay_snapshot(self._candle_count_snapshot(1_500))
        self.app.processEvents()
        window._set_time_view(700.0, 100.0)
        index = 750
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

        window.set_replay_snapshot(self._candle_count_snapshot(1_000))
        self.app.processEvents()
        width_1000 = window.canvas.width()
        self.assertLessEqual(
            abs(width_1000 - window.chart_scroll_area.viewport().width()),
            1,
        )
        self.assertEqual(250.0, window.visible_candle_span)
        self.assertEqual(750.0, window.visible_start_index)
        max_1000 = window.time_navigation_scrollbar.maximum()
        self.assertGreater(max_1000, 0)
        self.assertTrue(window.time_navigation_scrollbar.isVisible())

        window.set_replay_snapshot(self._candle_count_snapshot(1_200))
        self.app.processEvents()
        self.assertLessEqual(abs(width_1000 - window.canvas.width()), 1)
        self.assertEqual(250.0, window.visible_candle_span)
        self.assertEqual(950.0, window.visible_start_index)
        max_1200 = window.time_navigation_scrollbar.maximum()
        self.assertGreater(max_1200, max_1000)
        self.assertTrue(window.time_navigation_scrollbar.isVisible())

        window.set_replay_snapshot(self._candle_count_snapshot(1_500))
        self.app.processEvents()
        self.assertLessEqual(abs(width_1000 - window.canvas.width()), 1)
        self.assertEqual(250.0, window.visible_candle_span)
        self.assertEqual(1_250.0, window.visible_start_index)
        self.assertGreater(window.time_navigation_scrollbar.maximum(), max_1200)
        self.assertEqual(
            0,
            window.chart_scroll_area.horizontalScrollBar().maximum(),
        )

    def test_high_frequency_time_drag_is_coalesced_and_release_is_exact(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(25.0, 50.0)
        self.widgets.append(canvas)
        pan_deltas = []
        canvas.pan_requested.connect(
            lambda delta_x, delta_y: pan_deltas.append((delta_x, delta_y))
        )
        start = QPoint(400, 200)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        for offset in (12, 18, 24, 30):
            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseMove,
                QPointF(start.x() + offset, start.y()),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ))
            self.assertEqual([(12.0, 0.0)], pan_deltas)
        self.assertEqual(18.0, canvas._time_pan_preview_offset_x)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(start.x() + 37, start.y()),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))

        self.assertEqual([(12.0, 0.0), (25.0, 0.0)], pan_deltas)
        self.assertEqual(37.0, sum(delta_x for delta_x, _delta_y in pan_deltas))
        self.assertEqual(0.0, sum(delta_y for _delta_x, delta_y in pan_deltas))
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertFalse(canvas._time_pan_preview_started)

    def test_time_drag_at_latest_edge_has_no_visible_or_committed_motion(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(50.0, 50.0)
        canvas.show()
        self.widgets.append(canvas)
        pan_deltas = []
        canvas.pan_requested.connect(
            lambda delta_x, delta_y: pan_deltas.append((delta_x, delta_y))
        )
        canvas.render(QPixmap(canvas.size()))
        cached_pixmap = canvas._static_chart_cache
        cached_key = canvas._static_chart_cache_key
        start = QPoint(400, 200)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        with patch.object(
            canvas,
            "_paint_static_chart",
            wraps=canvas._paint_static_chart,
        ) as paint_static:
            for offset in (-12, -25, -40):
                QApplication.sendEvent(canvas, QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(start.x() + offset, start.y()),
                    Qt.NoButton,
                    Qt.LeftButton,
                    Qt.NoModifier,
                ))
                canvas.render(QPixmap(canvas.size()))
                self.assertEqual([], pan_deltas)
                self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
                self.assertIs(cached_pixmap, canvas._static_chart_cache)
                self.assertEqual(cached_key, canvas._static_chart_cache_key)

            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(start.x() - 40, start.y()),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            ))
            canvas.render(QPixmap(canvas.size()))
            self.assertEqual(0, paint_static.call_count)

        self.assertEqual([], pan_deltas)
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertEqual(0.0, canvas._time_pan_preview_raw_offset_x)
        self.assertFalse(canvas._time_pan_preview_started)

    def test_time_drag_overscroll_debt_must_reverse_before_moving_from_latest(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(50.0, 50.0)
        self.widgets.append(canvas)
        pan_deltas = []

        def commit_pan(delta_x, delta_y):
            pan_deltas.append((delta_x, delta_y))
            canvas.set_time_view(
                canvas.visible_start_index - delta_x / canvas.pixels_per_candle,
                canvas.visible_candle_span,
            )

        canvas.pan_requested.connect(commit_pan)
        start = QPoint(400, 200)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))

        for offset in (-20, -15, -10):
            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseMove,
                QPointF(start.x() + offset, start.y()),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ))
            self.assertEqual([], pan_deltas)
            self.assertEqual(0.0, canvas._time_pan_preview_offset_x)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(start.x() + 5, start.y()),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        self.assertEqual([], pan_deltas)
        self.assertEqual(5.0, canvas._time_pan_preview_offset_x)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(start.x() + 5, start.y()),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))

        self.assertEqual([(5.0, 0.0)], pan_deltas)
        self.assertAlmostEqual(
            50.0 - 5.0 / canvas.pixels_per_candle,
            canvas.visible_start_index,
        )
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertEqual(0.0, canvas._time_pan_preview_raw_offset_x)

    def test_time_drag_near_latest_edge_commits_only_available_distance(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(49.5, 50.0)
        self.widgets.append(canvas)
        pan_deltas = []

        def commit_pan(delta_x, delta_y):
            pan_deltas.append((delta_x, delta_y))
            canvas.set_time_view(
                canvas.visible_start_index - delta_x / canvas.pixels_per_candle,
                canvas.visible_candle_span,
            )

        canvas.pan_requested.connect(commit_pan)
        available_dx = -0.5 * canvas.pixels_per_candle
        start = QPoint(400, 200)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(start.x() - 20, start.y()),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))

        self.assertEqual(1, len(pan_deltas))
        self.assertAlmostEqual(available_dx, pan_deltas[0][0])
        self.assertEqual(0.0, pan_deltas[0][1])
        self.assertEqual(50.0, canvas.visible_start_index)
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertAlmostEqual(
            -20.0 - available_dx,
            canvas._time_pan_preview_raw_offset_x,
        )

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(start.x() - 20, start.y()),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))
        self.assertEqual(1, len(pan_deltas))
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertEqual(0.0, canvas._time_pan_preview_raw_offset_x)

    def test_time_drag_preview_reuses_static_cache_until_release(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(25.0, 50.0)
        self.widgets.append(canvas)
        pan_deltas = []

        def commit_pan(delta_x, delta_y):
            pan_deltas.append((delta_x, delta_y))
            canvas.set_time_view(
                canvas.visible_start_index - delta_x / canvas.pixels_per_candle,
                canvas.visible_candle_span,
            )

        canvas.pan_requested.connect(commit_pan)
        start = QPoint(400, 200)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))

        with patch.object(
            canvas,
            "_paint_static_chart",
            wraps=canvas._paint_static_chart,
        ) as paint_static:
            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseMove,
                QPointF(start.x() + 12, start.y()),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ))
            canvas.render(QPixmap(canvas.size()))
            self.assertEqual(1, paint_static.call_count)
            paint_static.reset_mock()

            for offset in (18, 24, 30):
                QApplication.sendEvent(canvas, QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(start.x() + offset, start.y()),
                    Qt.NoButton,
                    Qt.LeftButton,
                    Qt.NoModifier,
                ))
                canvas.render(QPixmap(canvas.size()))

            self.assertEqual(0, paint_static.call_count)
            self.assertEqual([(12.0, 0.0)], pan_deltas)

            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(start.x() + 37, start.y()),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            ))
            self.assertEqual([(12.0, 0.0), (25.0, 0.0)], pan_deltas)
            self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
            self.assertFalse(canvas._time_pan_preview_started)
            canvas.render(QPixmap(canvas.size()))
            self.assertEqual(1, paint_static.call_count)
            self.assertEqual(
                canvas._static_chart_state_key(),
                canvas._static_chart_cache_key,
            )

    def test_hiding_time_drag_flushes_and_clears_preview(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        canvas.set_time_view(25.0, 50.0)
        self.widgets.append(canvas)
        pan_deltas = []
        canvas.pan_requested.connect(
            lambda delta_x, delta_y: pan_deltas.append((delta_x, delta_y))
        )
        canvas.show()
        self.app.processEvents()
        start = QPoint(400, 200)
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        for offset in (12, 20, 31):
            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseMove,
                QPointF(start.x() + offset, start.y()),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ))

        self.assertEqual([(12.0, 0.0)], pan_deltas)
        self.assertEqual(19.0, canvas._time_pan_preview_offset_x)
        canvas.hide()
        self.app.processEvents()

        self.assertEqual([(12.0, 0.0), (19.0, 0.0)], pan_deltas)
        self.assertEqual(0.0, canvas._time_pan_preview_offset_x)
        self.assertFalse(canvas._time_pan_preview_started)

    def test_high_frequency_price_drag_release_preserves_exact_delta(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        self.widgets.append(canvas)
        pan_deltas = []
        canvas.pan_requested.connect(
            lambda delta_x, delta_y: pan_deltas.append((delta_x, delta_y))
        )
        start = QPoint(400, 200)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        for offset in (12, 19, 27, 34):
            QApplication.sendEvent(canvas, QMouseEvent(
                QEvent.MouseMove,
                QPointF(start.x() + 2, start.y() + offset),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(start.x() + 3, start.y() + 41),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))

        self.assertLess(len(pan_deltas), 4)
        self.assertEqual(0.0, sum(delta_x for delta_x, _delta_y in pan_deltas))
        self.assertEqual(41.0, sum(delta_y for _delta_x, delta_y in pan_deltas))

    def test_click_without_drag_selects_once_and_emits_no_pan(self):
        candles = self._candle_count_snapshot(100).to_candles()
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
        canvas.resize(1_000, 440)
        self.widgets.append(canvas)
        selected = []
        pan_deltas = []
        canvas.bar_selected.connect(selected.append)
        canvas.pan_requested.connect(
            lambda delta_x, delta_y: pan_deltas.append((delta_x, delta_y))
        )
        click = QPoint(round(canvas._x_for_index(50)), 200)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(click),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(click.x() + 2, click.y() + 2),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))

        self.assertEqual([50], selected)
        self.assertEqual([], pan_deltas)

    def test_marker_index_limits_visible_paint_and_hit_candidates(self):
        candles = self._candle_count_snapshot(100).to_candles()
        markers = [
            {"evaluation_index": 2, "side": "BUY", "tooltip": "outside-left"},
            {"evaluation_index": 50, "side": "BUY", "tooltip": "visible-buy"},
            {"evaluation_index": 95, "side": "SELL", "tooltip": "outside-right"},
            {"evaluation_index": 50, "side": "SELL", "tooltip": "visible-sell"},
        ]
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, markers)
        canvas.resize(1_000, 440)
        canvas.set_time_view(45.0, 10.0)
        self.widgets.append(canvas)

        self.assertEqual(markers, canvas.marker_records())
        self.assertEqual(4, canvas.marker_count())
        self.assertEqual(2, canvas.marker_count("BUY"))
        self.assertEqual(
            ["visible-buy", "visible-sell"],
            [marker["tooltip"] for marker in canvas._visible_marker_records()],
        )

        marker_x = canvas._x_for_index(50)
        marker_y = canvas._marker_y(50, "BUY")
        self.assertIsNotNone(marker_y)
        with patch.object(canvas, "_marker_y", wraps=canvas._marker_y) as marker_y_call:
            hit = canvas._marker_at(marker_x, marker_y)

        self.assertEqual("visible-buy", hit["tooltip"])
        self.assertTrue(marker_y_call.call_args_list)
        self.assertEqual(
            {50},
            {call.args[0] for call in marker_y_call.call_args_list},
        )

        updated_markers = [{
            "evaluation_index": 51,
            "side": "SELL",
            "tooltip": "updated-visible",
        }]
        canvas.set_visualization_projection((), None, {}, updated_markers)
        self.assertEqual(updated_markers, canvas.marker_records())
        self.assertEqual(1, canvas.marker_count("SELL"))
        self.assertEqual(
            ["updated-visible"],
            [marker["tooltip"] for marker in canvas._visible_marker_records()],
        )

    def test_vertical_drag_ignores_micro_horizontal_jitter_until_release(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        window._set_time_view(200.0, 100.0)
        canvas = window.canvas
        start_before = window.visible_start_index
        bounds_before = window.current_price_bounds
        drag_x = round(canvas._x_for_index(240))
        drag_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )

        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(drag_x, drag_y))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_x + 3, drag_y + 60),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_x + 7, drag_y + 110),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(drag_x + 7, drag_y + 110),
        )

        self.assertEqual(start_before, window.visible_start_index)
        self.assertNotEqual(bounds_before, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)
        manual_price_bounds = window.current_price_bounds

        # A new horizontal gesture is free to choose TIME and must preserve
        # the user's vertical price placement exactly.
        drag_x = round(canvas._x_for_index(240))
        drag_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )
        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(drag_x, drag_y))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_x + 80, drag_y + 4),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(drag_x + 80, drag_y + 4),
        )
        self.assertLess(window.visible_start_index, start_before)
        self.assertEqual(manual_price_bounds, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)

    def test_ambiguous_diagonal_drag_waits_for_clear_axis_intent(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        window._set_time_view(200.0, 100.0)
        canvas = window.canvas
        start_before = window.visible_start_index
        bounds_before = window.current_price_bounds
        drag_x = round(canvas._x_for_index(240))
        drag_y = round(
            (canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2
        )

        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(drag_x, drag_y))
        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_x + 24, drag_y + 22),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        self.assertEqual(start_before, window.visible_start_index)
        self.assertEqual(bounds_before, window.current_price_bounds)

        QApplication.sendEvent(canvas, QMouseEvent(
            QEvent.MouseMove,
            QPointF(drag_x + 28, drag_y + 70),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        QTest.mouseRelease(
            canvas,
            Qt.LeftButton,
            pos=QPoint(drag_x + 28, drag_y + 70),
        )
        self.assertEqual(start_before, window.visible_start_index)
        self.assertNotEqual(bounds_before, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)

    def test_micro_time_wheel_input_requires_short_burst_intent_before_autofit(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        canvas = window.canvas
        cursor_x = canvas._x_for_index(450)
        span_before = window.visible_candle_span
        bounds_before = window.current_price_bounds

        for _ in range(3):
            window._zoom_time_scale_at(cursor_x, 15)
        self.assertEqual(span_before, window.visible_candle_span)
        self.assertEqual(bounds_before, window.current_price_bounds)

        window._zoom_time_scale_at(cursor_x, 15)
        self.assertLess(window.visible_candle_span, span_before)
        self.assertFalse(window.price_scale_manually_adjusted)

    def test_horizontal_pan_preserves_price_view_even_when_visible_data_crosses_edges(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(1_500))
        self.app.processEvents()
        canvas = window.canvas

        self.assertEqual(1_250.0, window.visible_start_index)
        self.assertEqual(250.0, window.visible_candle_span)

        # Establish a deliberately narrow/manual price viewport so visible
        # data already extends outside it. Horizontal dragging must still
        # leave this exact vertical view untouched.
        auto_minimum, auto_maximum = window.current_price_bounds
        center = (auto_minimum + auto_maximum) / 2.0
        manual_range = (auto_maximum - auto_minimum) * 0.55
        manual_bounds = (
            center - manual_range / 2.0,
            center + manual_range / 2.0,
        )
        window._current_price_minimum, window._current_price_maximum = manual_bounds
        canvas.set_price_view(*manual_bounds)
        window._price_scale_manually_adjusted = True

        pixels = canvas.pixels_per_candle
        start_before = window.visible_start_index
        visible_before = canvas._compute_price_data_bounds()
        self.assertTrue(
            visible_before[0] < manual_bounds[0]
            or visible_before[1] > manual_bounds[1]
        )

        window._pan_chart_view(pixels * 1.0, 0.0)
        self.assertLess(window.visible_start_index, start_before)
        self.assertEqual(manual_bounds, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)

        window._pan_chart_view(pixels * 40.0, 0.0)
        self.assertEqual(manual_bounds, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)
        visible_after = canvas._compute_price_data_bounds()
        self.assertNotEqual(visible_before, visible_after)

    def test_time_navigation_scrollbar_autofits_visible_price_range(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(1_500))
        self.app.processEvents()

        initial_bounds = window.current_price_bounds
        self.assertEqual(250.0, window.visible_candle_span)

        window._time_navigation_scrollbar_changed(0)
        self.app.processEvents()

        minimum, maximum = window.canvas._compute_price_data_bounds()
        self.assertIsNotNone(minimum)
        self.assertIsNotNone(maximum)
        padding = (maximum - minimum) * window._VISIBLE_PRICE_PADDING_RATIO
        self.assertAlmostEqual(minimum - padding, window.current_price_bounds[0], places=8)
        self.assertAlmostEqual(maximum + padding, window.current_price_bounds[1], places=8)
        self.assertNotEqual(initial_bounds, window.current_price_bounds)
        self.assertFalse(window.price_scale_manually_adjusted)
        self.assertTrue(window.time_scale_manually_adjusted)

    def test_candle_body_width_tracks_time_span_with_small_fixed_gap(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()
        window._set_time_view(400.0, 100.0)
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
        def expected_auto_price_bounds():
            minimum, maximum = canvas._compute_price_data_bounds()
            self.assertIsNotNone(minimum)
            self.assertIsNotNone(maximum)
            if maximum == minimum:
                padding = max(abs(maximum) * 0.01, 1.0)
            else:
                padding = (maximum - minimum) * 0.08
            return minimum - padding, maximum + padding

        def assert_auto_price_bounds():
            expected_minimum, expected_maximum = expected_auto_price_bounds()
            actual = window.current_price_bounds
            self.assertIsNotNone(actual)
            self.assertAlmostEqual(expected_minimum, actual[0], places=8)
            self.assertAlmostEqual(expected_maximum, actual[1], places=8)
            self.assertFalse(window.price_scale_manually_adjusted)

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
            self.assertLess(window.visible_candle_span, 500.0)
            self.assertLessEqual(
                abs(canvas._x_for_index(anchor_index) - before_x),
                1.0,
            )
            assert_auto_price_bounds()
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
        price_bounds_before_pan = window.current_price_bounds
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
        self.assertEqual(price_bounds_before_pan, window.current_price_bounds)
        self.assertEqual([], selected)

        start_after_right_pan = window.visible_start_index
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
        self.assertEqual(price_bounds_before_pan, window.current_price_bounds)
        self.assertEqual([], selected)

        # Price-only movement remains manual; a later horizontal pan should
        # preserve that placement while the newly visible data still fits.
        bounds_before_vertical_pan = window.current_price_bounds
        window._pan_chart_view(0.0, 60.0)
        self.assertTrue(window.price_scale_manually_adjusted)
        self.assertNotEqual(bounds_before_vertical_pan, window.current_price_bounds)
        manual_bounds = window.current_price_bounds
        window._pan_chart_view(80.0, 0.0)
        self.assertEqual(manual_bounds, window.current_price_bounds)
        self.assertTrue(window.price_scale_manually_adjusted)

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
        self.assertEqual((99.0, 300.0), window.current_price_bounds)
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
        window._set_time_view(400.0, 100.0)
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
            minimum, maximum = window.canvas._compute_price_data_bounds()
            padding = (
                max(abs(maximum) * 0.01, 1.0)
                if maximum == minimum
                else (maximum - minimum) * window._VISIBLE_PRICE_PADDING_RATIO
            )
            self.assertAlmostEqual(
                minimum - padding,
                window.canvas.price_scale().minimum,
                places=8,
            )
            self.assertAlmostEqual(
                maximum + padding,
                window.canvas.price_scale().maximum,
                places=8,
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

        window.basic_signal_interval_combo.setCurrentText("3\ubd84")
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
        self.assertEqual(500, window.historical_candle_count)
        self.assertEqual(1, len(runs))
        self.assertEqual([], applies)

    def test_direct_candle_count_input_is_removed(self):
        window = self._window()
        runs = []
        window.validation_run_requested.connect(runs.append)

        self.assertFalse(hasattr(window, "historical_candle_count_spin"))
        self.assertEqual(500, window.historical_candle_count)
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

    def test_chart_single_click_shows_yellow_position_line_without_starting_range(self):
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
        self.assertIsNone(window.canvas.validation_range)
        self.assertEqual(selected_index, window.canvas.selection_indicator_index)
        self.assertEqual(selected_index, window._position_indicator_index)
        self.assertIsNone(window._validation_range_anchor_index)

    def test_chart_double_click_drag_hides_yellow_line_and_click_cancels_range(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(100))
        self.app.processEvents()

        start_index = 20
        end_index = 35
        scale = window.canvas.price_scale()
        click_y = round((scale.plot_top + scale.plot_bottom) / 2)
        start_point = QPointF(
            window.canvas._x_for_index(start_index),
            float(click_y),
        )
        end_point = QPointF(
            window.canvas._x_for_index(end_index),
            float(click_y),
        )

        QApplication.sendEvent(window.canvas, QMouseEvent(
            QEvent.MouseButtonDblClick,
            start_point,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        self.app.processEvents()
        self.assertEqual(
            start_index,
            window.canvas.validation_range_drag_start_index,
        )
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertIsNone(window.validation_range)

        QApplication.sendEvent(window.canvas, QMouseEvent(
            QEvent.MouseMove,
            end_point,
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        self.app.processEvents()
        self.assertEqual(
            (start_index, end_index),
            window.canvas.validation_range,
        )
        self.assertIsNone(window.validation_range)

        QApplication.sendEvent(window.canvas, QMouseEvent(
            QEvent.MouseButtonRelease,
            end_point,
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ))
        self.app.processEvents()

        self.assertEqual((start_index, end_index), window.validation_range)
        self.assertEqual((start_index, end_index), window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertIsNone(window.canvas.validation_range_drag_start_index)

        # First single click after a committed period cancels it and is consumed.
        cancel_index = 50
        cancel_point = QPoint(
            round(window.canvas._x_for_index(cancel_index)),
            click_y,
        )
        QTest.mouseClick(window.canvas, Qt.LeftButton, pos=cancel_point)
        self.app.processEvents()
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window._validation_range_anchor_index)
        self.assertIsNone(window.canvas.validation_range)
        self.assertIsNone(window.canvas.selection_indicator_index)

        # One more single click shows only the yellow position line.
        QTest.mouseClick(window.canvas, Qt.LeftButton, pos=cancel_point)
        self.app.processEvents()
        self.assertEqual(cancel_index, window.canvas.selection_indicator_index)
        self.assertIsNone(window.validation_range)

        # Double-click action hides the yellow line while entering range-drag mode.
        QApplication.sendEvent(window.canvas, QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(cancel_point),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ))
        self.app.processEvents()
        self.assertIsNone(window.canvas.selection_indicator_index)
        self.assertEqual(
            cancel_index,
            window.canvas.validation_range_drag_start_index,
        )

    def test_programmatic_candle_count_set_does_not_request_validation(self):
        window = self._window()
        runs = []
        window.validation_run_requested.connect(runs.append)

        window.set_historical_candle_count(200)

        self.assertEqual(200, window.historical_candle_count)
        self.assertEqual([], runs)

    def test_chart_reset_restores_time_anchored_entry_view_after_history_prepend(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        expanded_snapshot = self._candle_count_snapshot(1_000, include_signals=True)
        expanded_candles = expanded_snapshot.to_candles()
        initial_candles = expanded_candles[-500:]
        initial_replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=expanded_snapshot.settings_hash,
            historical_request_id="RESET-ENTRY-INITIAL",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=initial_candles[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(initial_candles, chart_candle_count=500)
        window.set_replay_snapshot(initial_replay)
        self.app.processEvents()

        entry_view = window._entry_chart_view_state
        self.assertIsNotNone(entry_view)
        entry_center_index = math.floor(
            entry_view.visible_start_index + entry_view.visible_candle_span / 2.0
        )
        entry_center_time = window._candles[entry_center_index]["time"]
        entry_selected_time = window._candles[
            entry_view.selected_evaluation_index
        ]["time"]
        entry_bounds = (
            entry_view.current_price_minimum,
            entry_view.current_price_maximum,
        )
        entry_pane_heights = entry_view.lower_pane_heights

        expanded_replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=expanded_snapshot.settings_hash,
            historical_request_id="RESET-ENTRY-EXPANDED",
            evaluated_start_index=0,
            evaluated_end_index=99,
            dropped_raw_rows_count=0,
            candles=expanded_candles[-100:],
            entries=[],
        )
        window.set_historical_candle_pool(expanded_candles, chart_candle_count=1_000)
        window.set_replay_snapshot(expanded_replay)
        self.app.processEvents()
        original_canvas = window.canvas
        original_replay = window.replay_snapshot
        runs = []
        window.validation_run_requested.connect(runs.append)

        window.buy_signal_expr_line.setText("D")
        window.set_historical_candle_count(777)
        window._set_time_view(120.0, 240.0, manually_adjusted=True)
        window._pan_chart_view(0.0, 80.0)
        if window.canvas.lower_boundary_records():
            first_boundary = window.canvas.lower_boundary_records()[0]
            window.canvas._resize_lower_boundary_to(
                0,
                first_boundary["y"] - 24.0,
            )
            self.assertNotEqual(
                entry_pane_heights,
                window.canvas.lower_pane_heights,
            )
        changed_bounds = window.current_price_bounds
        self.assertNotEqual(
            (entry_view.current_price_minimum, entry_view.current_price_maximum),
            changed_bounds,
        )

        window.reset_button.click()
        self.app.processEvents()

        restored_center_index = math.floor(
            window.visible_start_index + window.visible_candle_span / 2.0
        )
        self.assertEqual([], runs)
        self.assertIs(original_canvas, window.canvas)
        self.assertIs(original_replay, window.replay_snapshot)
        self.assertEqual("D", window.buy_signal_expr_line.text())
        self.assertEqual(777, window.historical_candle_count)
        self.assertEqual(entry_center_time, window._candles[restored_center_index]["time"])
        self.assertEqual(entry_view.visible_candle_span, window.visible_candle_span)
        self.assertEqual(entry_bounds, window.current_price_bounds)
        self.assertNotEqual(
            entry_view.selected_evaluation_index,
            window.selected_evaluation_index,
        )
        self.assertEqual(
            entry_selected_time,
            window._candles[window.selected_evaluation_index]["time"],
        )
        self.assertEqual(
            entry_view.time_scale_manually_adjusted,
            window.time_scale_manually_adjusted,
        )
        self.assertEqual(
            entry_view.price_scale_manually_adjusted,
            window.price_scale_manually_adjusted,
        )
        self.assertEqual(entry_pane_heights, window.canvas.lower_pane_heights)

    def test_chart_reset_keeps_existing_result_and_clears_transient_chart_selection(self):
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        snapshot = self._candle_count_snapshot(500, include_signals=True)
        window.set_replay_snapshot(snapshot)
        self.app.processEvents()

        original_canvas = window.canvas
        original_replay = window.replay_snapshot
        original_candle_count = original_canvas.candle_count

        window._begin_validation_range_drag(120)
        window._complete_validation_range_drag(120, 180)
        self.assertEqual((120, 180), window.validation_range)
        original_canvas._pinned_marker_key = (125, "BUY")
        original_canvas._hover_marker_key = (125, "BUY")
        original_canvas._set_crosshair_pointer(125, 200.0)

        with (
            patch.object(window, "_clear_validation_result") as clear_result,
            patch.object(window, "_request_entry_validation") as request_entry,
        ):
            window.reset_button.click()
            self.app.processEvents()

        clear_result.assert_not_called()
        request_entry.assert_not_called()
        self.assertIs(original_canvas, window.canvas)
        self.assertIs(original_replay, window.replay_snapshot)
        self.assertEqual(original_candle_count, window.canvas.candle_count)
        self.assertIsNone(window.validation_range)
        self.assertIsNone(window._validation_range_anchor_index)
        self.assertIsNone(window.canvas.validation_range)
        self.assertIsNone(window.canvas.pinned_marker_key)
        self.assertIsNone(window.canvas._hover_marker_key)
        self.assertIsNone(window.canvas.crosshair_index)
        self.assertEqual(0, len(window.completed_cycles))

    def test_chart_reset_does_not_touch_flow_generation_parent_or_market_snapshot(self):
        flow = IndicatorFollowSignalValidationFlow(
            _SnapshotBroker(True),
            host=_FakeHost(self.stock),
            recent_stock_store=_MemoryRecentStockStore((self.stock,)),
        )
        window = self._window()
        window.resize(1400, 800)
        window.show()
        self.app.processEvents()
        window.set_replay_snapshot(self._candle_count_snapshot(500))
        self.app.processEvents()

        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 7
        flow._market_snapshot_generation[key] = 11
        runs = []
        window.validation_run_requested.connect(runs.append)

        with (
            patch.object(flow, "_run_validation") as run_validation,
            patch.object(flow, "_request_market_snapshot_for_window") as market_snapshot,
        ):
            window._set_time_view(100.0, 100.0, manually_adjusted=True)
            window.reset_button.click()
            self.app.processEvents()

        self.assertEqual([], runs)
        run_validation.assert_not_called()
        market_snapshot.assert_not_called()
        self.assertEqual(7, flow._request_generation[key])
        self.assertEqual(11, flow._market_snapshot_generation[key])

    def test_chart_reset_does_not_mutate_parent_settings_or_undo_baseline(self):
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
                    routine_name="??????",
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(source)

            seed = IndicatorFollowSignalValidationSeed(
                ValidationSettingsSnapshot(self.rules),
                source.collect_indicator_follow_ui_state(),
            )
            window = IndicatorFollowSignalValidationWindow(self.stock, seed)
            self.widgets.append(window)
            window.resize(1400, 800)
            window.show()
            self.app.processEvents()
            window.set_replay_snapshot(self._candle_count_snapshot(500))
            self.app.processEvents()

            source.buy_signal_expr_line.setText("C")
            parent_before = source.collect_indicator_follow_ui_state()
            window.buy_signal_expr_line.setText("D")
            window._set_time_view(100.0, 100.0, manually_adjusted=True)

            window.reset_button.click()
            self.app.processEvents()

            self.assertEqual(
                parent_before,
                source.collect_indicator_follow_ui_state(),
            )
            self.assertEqual("D", window.buy_signal_expr_line.text())
            self.assertEqual(original_bytes, rules_path.read_bytes())

    def test_chart_reset_before_first_ready_chart_is_noop(self):
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                self._unresolved_seed(),
            )
        self.widgets.append(window)
        runs = []
        window.validation_run_requested.connect(runs.append)
        window.buy_signal_expr_line.setText("D")
        before = window.collect_indicator_follow_ui_state()

        window.reset_button.click()
        self.app.processEvents()

        self.assertEqual([], runs)
        self.assertIsNone(window.replay_snapshot)
        self.assertIsNone(getattr(window, "canvas", None))
        self.assertEqual(before, window.collect_indicator_follow_ui_state())

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
        self.assertEqual("과거 시세 데이터 조회 중...", window.loading_label.text())
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
            [
                "회차",
                "매수구간",
                "매수횟수",
                "매수총액",
                "평단",
                "매도시각",
                "매도가",
                "손익",
                "수익률",
            ],
            [
                window.completed_cycle_table.horizontalHeaderItem(column).text()
                for column in range(window.completed_cycle_table.columnCount())
            ],
        )
        self.assertEqual(
            [
                "1",
                "09/11 14:00",
                "1",
                "100원",
                "100",
                "09/11 14:02",
                "150",
                "+50원",
                "+50.00%",
            ],
            [window.completed_cycle_table.item(0, column).text() for column in range(9)],
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
            self.assertEqual((), second.recent_stock_projections[-1])
            second.snapshots.append("window2-result")

            first.recent_stock_selected.emit(other)
            self.assertEqual(other, first.stock)
            self.assertEqual(self.stock, second.stock)
            self.assertEqual(["window2-result"], second.snapshots)
            self.assertEqual(other, flow.last_selected_stock)
            self.assertEqual((other, self.stock), flow.recent_stocks)
            self.assertEqual(2, recent_store.write_count)
            self.assertEqual((self.stock,), first.recent_stock_projections[-1])
            self.assertEqual((other,), second.recent_stock_projections[-1])
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

    def test_twenty_first_stock_purges_only_oldest_cache_and_pools(self):
        newest = self.stock
        oldest = ValidationStockRef("000001", "가장오래된종목")
        recent = (newest,) + tuple(
            ValidationStockRef(f"{index:06d}", f"종목{index}")
            for index in range(2, 20)
        ) + (oldest,)
        incoming = ValidationStockRef("000021", "새종목")
        cache = SimpleNamespace(delete_stock=Mock(return_value={"ok": True, "deleted_count": 2}))
        broker = _FakeBroker(True)
        store = _MemoryRecentStockStore(recent)
        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=_FakeHost(newest),
            recent_stock_store=store,
            historical_cache=cache,
        )
        window = _FakeWindow(oldest, self._seed())
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._historical_pools[key] = {"stock": oldest, "candles": [{}]}
        flow._validation_sessions[key] = object()
        flow._request_generation[key] = 4
        flow._active_providers[(key, 4)] = object()
        shared_key = (id(broker), oldest.code, "M1", "000001")
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[shared_key] = {
            "stock": oldest, "candles": [{}]
        }

        self.assertTrue(flow._remember_stock(incoming))

        self.assertEqual(20, len(store.recent_stocks))
        self.assertEqual(incoming, store.recent_stocks[0])
        self.assertNotIn(oldest, store.recent_stocks)
        self.assertIn(newest, store.recent_stocks)
        cache.delete_stock.assert_called_once_with(oldest.code)
        self.assertNotIn(key, flow._historical_pools)
        self.assertNotIn(shared_key, flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS)
        self.assertNotIn(key, flow._validation_sessions)
        self.assertNotIn((key, 4), flow._active_providers)
        self.assertEqual([oldest.code], window.discarded_stock_codes)
        self.assertEqual(5, flow._request_generation[key])

    def test_restored_overflow_purges_evicted_stock_cache(self):
        cache = SimpleNamespace(delete_stock=Mock(return_value={"ok": True}))
        store = _MemoryRecentStockStore((self.stock,))
        store.startup_evicted_codes = ("000001",)
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True), host=_FakeHost(self.stock),
            recent_stock_store=store, historical_cache=cache,
        )

        self.assertEqual((self.stock,), flow.recent_stocks)
        cache.delete_stock.assert_called_once_with("000001")

    def test_twenty_first_stock_purges_memory_when_persistent_delete_fails(self):
        first = self.stock
        removed = ValidationStockRef("000001", "가장오래된종목")
        recent = (first,) + tuple(
            ValidationStockRef(f"{index:06d}", f"종목{index}")
            for index in range(2, 20)
        ) + (removed,)
        cache = SimpleNamespace(delete_stock=Mock(return_value={
            "ok": False,
            "deleted_count": 0,
            "reason_code": "CACHE_DELETE_FAILED",
        }))
        broker = _FakeBroker(True)
        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=_FakeHost(first),
            recent_stock_store=_MemoryRecentStockStore(recent),
            historical_cache=cache,
        )
        window = _FakeWindow(first, self._seed())
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._historical_pools[key] = {"stock": removed, "candles": [{}]}
        shared_key = (id(broker), removed.code, "M1", "000001")
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[shared_key] = {
            "stock": removed,
            "candles": [{}],
        }

        flow._remember_stock(ValidationStockRef("000021", "새종목"))

        cache.delete_stock.assert_called_once_with("000001")
        self.assertNotIn(key, flow._historical_pools)
        self.assertNotIn(shared_key, flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS)

    def test_recent_manual_remove_immediately_purges_current_stock(self):
        retained = self.stock
        removed = ValidationStockRef("000660", "SK\ud558\uc774\ub2c9\uc2a4")
        cache = SimpleNamespace(delete_stock=Mock(return_value={"ok": True, "deleted_count": 2}))
        broker = _FakeBroker(True)
        store = _MemoryRecentStockStore((removed, retained))
        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=_FakeHost(removed),
            recent_stock_store=store,
            historical_cache=cache,
        )
        window = _FakeWindow(removed, self._seed())
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 2
        flow._historical_pools[key] = {"stock": removed, "candles": [{}]}
        shared_key = (id(broker), removed.code, "M1", "000660")
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[shared_key] = {
            "stock": removed,
            "candles": [{}],
        }

        with patch.object(flow_module.QTimer, "singleShot"):
            flow._remove_recent_stock_for_window(window, removed)

        self.assertEqual((retained,), store.recent_stocks)
        cache.delete_stock.assert_called_once_with("000660")
        self.assertIsNone(window.stock)
        self.assertEqual(["000660"], window.discarded_stock_codes)
        self.assertNotIn(key, flow._historical_pools)
        self.assertNotIn(shared_key, flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS)
        self.assertEqual(retained, flow.last_selected_stock)

    def test_current_stock_is_hidden_only_from_each_window_projection(self):
        first = self.stock
        second = ValidationStockRef("000660", "SK하이닉스")
        third = ValidationStockRef("035420", "NAVER")
        store = _MemoryRecentStockStore((first, second, third))
        cache = SimpleNamespace(delete_stock=Mock())
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True), host=_FakeHost(first),
            recent_stock_store=store, historical_cache=cache,
        )
        first_window = _FakeWindow(first, self._seed())
        second_window = _FakeWindow(second, self._seed())
        self.widgets.extend((first_window, second_window))
        flow._open_windows[id(first_window)] = first_window
        flow._open_windows[id(second_window)] = second_window

        flow._refresh_open_window_stock_projections()
        self.assertEqual((second, third), first_window.recent_stock_projections[-1])
        self.assertEqual((first, third), second_window.recent_stock_projections[-1])
        self.assertEqual((first, second, third), store.recent_stocks)
        cache.delete_stock.assert_not_called()

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

    def test_large_covering_pool_is_trimmed_to_active_working_set(self):
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=_FakeHost(self.stock),
            window_factory=_FakeWindow,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        seed = self._seed()
        rules = seed.settings_snapshot.to_dict()
        session = ValidationSession(
            ValidationRequest(
                self.stock,
                seed.settings_snapshot,
                rules["bar"]["bar_minutes"],
            ),
            operation_active_reader=lambda: False,
        )
        source = self._candle_count_snapshot(5_060).to_candles()
        pool = flow._pool_from_candles(
            session,
            source,
            requested_count=5_060,
            request_id="LARGE-COVERING-CACHE",
        )
        target_count = 500 + flow_module.required_validation_warmup_bars(rules)

        active = flow._validation_pool_for_session(
            session,
            pool,
            target_count=target_count,
        )

        self.assertEqual(5_060, active["source_requested_count"])
        self.assertEqual(target_count, active["requested_count"])
        self.assertEqual(target_count, len(active["candles"]))
        self.assertEqual(source[-target_count]["time"], active["candles"][0]["time"])
        self.assertEqual(source[-1]["time"], active["candles"][-1]["time"])

    def test_history_extension_coalesces_without_auto_chain(self):
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=_FakeHost(self.stock),
            window_factory=_FakeWindow,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        window = _FakeWindow(self.stock, self._seed())
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 0
        flow._history_targets[key] = 500
        request = IndicatorFollowSignalValidationRunRequest(
            self._seed().settings_snapshot,
            100,
            force_historical_refresh=True,
        )
        flow._last_run_requests[key] = request
        with patch.object(flow, "_run_validation") as run_validation:
            flow._request_history_extension(window)
            flow._request_history_extension(window)
            self.assertEqual(1, run_validation.call_count)
            extension_request = run_validation.call_args.args[1]
            self.assertFalse(extension_request.force_historical_refresh)
            self.assertTrue(run_validation.call_args.kwargs["history_extension"])
            self.assertEqual(1_000, flow._history_targets[key])
            self.assertIn(key, flow._history_extension_inflight)
            flow._complete_history_extension(key, success=True)
            self.assertEqual(1, run_validation.call_count)
            flow._request_history_extension(window)
            self.assertEqual(2, run_validation.call_count)
            self.assertEqual(1_500, flow._history_targets[key])

    def test_history_extension_failure_preserves_existing_chart_result(self):
        flow = IndicatorFollowSignalValidationFlow(
            _FakeBroker(True),
            host=_FakeHost(self.stock),
            window_factory=_FakeWindow,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        window = _FakeWindow(self.stock, self._seed())
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._history_targets[key] = 1_000
        flow._history_extension_inflight[key] = {
            "previous_target": 500,
            "requested_target": 1_000,
        }
        failures = []
        flow.validation_failed.connect(failures.append)

        flow._fail_window(window, "HISTORICAL_REQUEST_ERROR: fixture")

        self.assertEqual([], window.errors)
        self.assertEqual(["HISTORICAL_REQUEST_ERROR: fixture"], failures)
        self.assertEqual(500, flow._history_targets[key])
        self.assertNotIn(key, flow._history_extension_inflight)

    def test_history_extension_requests_next_fixed_window_end_to_end(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        requested_counts = []
        created = []

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                requested_counts.append(count)
                end = datetime(2026, 9, 18, 15, 0)
                rows = []
                for index in range(count):
                    stamp = end - timedelta(minutes=count - 1 - index)
                    price = 100 + index
                    rows.append({
                        "체결시간": stamp.strftime("%Y%m%d%H%M%S"),
                        "시가": str(price),
                        "고가": str(price + 1),
                        "저가": str(price - 1),
                        "현재가": str(price),
                        "거래량": "1",
                    })
                request = _self.session.request
                callback(ValidationHistoricalResult(
                    True,
                    snapshot=ValidationHistoricalSnapshot(
                        stock=request.stock,
                        timeframe_minutes=request.timeframe_minutes,
                        timeframe_key=request.timeframe_key,
                        requested_count=count,
                        request_id=f"EXT-{count}",
                        rows=rows,
                    ),
                ))

        class Replay:
            def __init__(_self, session):
                _self.session = session

            def evaluate(_self, historical, *, display_count=None, **_kwargs):
                candles, _ = flow_module.project_validation_candles(historical)
                count = max(1, min(int(display_count or 1), len(candles)))
                request = _self.session.request
                return ValidationReplayResult(True, snapshot=ValidationReplaySnapshot(
                    stock=request.stock,
                    timeframe_minutes=request.timeframe_minutes,
                    timeframe_key=request.timeframe_key,
                    settings_hash=request.settings_snapshot.rules_hash,
                    historical_request_id=historical.request_id,
                    evaluated_start_index=len(candles) - count,
                    evaluated_end_index=len(candles) - 1,
                    dropped_raw_rows_count=0,
                    candles=candles,
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
        seed = self._seed()
        carrier.signal_validation_requested.emit(seed)
        window = created[0]
        request = IndicatorFollowSignalValidationRunRequest(seed.settings_snapshot, 100)
        window.validation_run_requested.emit(request)
        warmup = flow_module.required_validation_warmup_bars(
            seed.settings_snapshot.to_dict()
        )
        self.assertEqual([500 + warmup], requested_counts)
        self.assertEqual((500 + warmup, 500), window.pool_installs[-1])
        window.historical_extension_requested.emit()
        self.assertEqual([500 + warmup, 1_000 + warmup], requested_counts)
        self.assertEqual((1_000 + warmup, 1_000), window.pool_installs[-1])
        self.assertEqual(1_000, flow._history_targets[id(window)])

        changed_rules = deepcopy(self.rules)
        changed_rules["bar"]["bar_minutes"] = 15
        changed_ui_state = seed.to_ui_state()
        changed_ui_state["basic"]["basic_signal_interval_combo"] = "15분"
        changed_snapshot = build_signal_validation_snapshot(
            changed_rules,
            ui_state=changed_ui_state,
        )
        changed_warmup = flow_module.required_validation_warmup_bars(
            changed_snapshot.to_dict()
        )
        window.validation_run_requested.emit(
            IndicatorFollowSignalValidationRunRequest(changed_snapshot, 100)
        )
        self.assertEqual(500 + changed_warmup, requested_counts[-1])
        self.assertEqual(500, flow._history_targets[id(window)])
        self.assertEqual((500 + changed_warmup, 500), window.pool_installs[-1])

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
        self.assertEqual(500, DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT)
        self.assertEqual(DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT, window.historical_candle_count)
        self.assertEqual(1, window.entry_commit_count)
        resolved_ui_state = self._seed().to_ui_state()
        expected_counts = []
        expected_timeframes = []
        for timeframe, candle_count in ((3, 300), (15, 500)):
            rules = deepcopy(self.rules)
            rules["bar"]["bar_minutes"] = timeframe
            current_ui_state = deepcopy(resolved_ui_state)
            current_ui_state["basic"]["basic_signal_interval_combo"] = (
                f"{timeframe}분"
            )
            snapshot = build_signal_validation_snapshot(
                rules,
                ui_state=current_ui_state,
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

    def test_full_signal_scan_must_finish_before_pool_and_replay_are_applied(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        scan_started = Event()
        release_scan = Event()

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

            def scan_signal_entries(_self, historical, *, context_provider=None):
                scan_started.set()
                release_scan.wait(2.0)
                return ()

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=1_000,
            replay_factory=Replay,
            window_factory=_FakeWindow,
            recent_stock_store=_MemoryRecentStockStore(),
        )
        seed = self._seed()
        request = IndicatorFollowSignalValidationRunRequest(
            seed.settings_snapshot,
            100,
        )
        session = ValidationSession(
            ValidationRequest(
                self.stock,
                seed.settings_snapshot,
                seed.settings_snapshot.to_dict()["bar"]["bar_minutes"],
            ),
            operation_active_reader=lambda: False,
        )
        pool = flow._pool_from_candles(
            session,
            [{
                "time": "20260911143000",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
            }],
            requested_count=1_000,
            request_id="READY-GATE",
        )
        window = _FakeWindow(self.stock, seed)
        self.widgets.append(window)
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 1

        try:
            flow._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=request.candle_count,
            )
            self.assertTrue(scan_started.wait(1.0))
            self.assertEqual([], window.pool_installs)
            self.assertEqual([], window.snapshots)

            release_scan.set()
            for _ in range(100):
                self.app.processEvents()
                if window.snapshots:
                    break
                QTest.qWait(10)

            self.assertEqual([(1, 1_000)], window.pool_installs)
            self.assertEqual([()], window.signal_marker_batches)
            self.assertEqual(1, len(window.snapshots))
        finally:
            release_scan.set()

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
        second_flow._now_factory = lambda: latest + timedelta(days=3)
        second_window = _FakeWindow(self.stock, seed)
        self.widgets.append(second_window)
        second_flow._open_windows[id(second_window)] = second_window
        second_flow._request_generation[id(second_window)] = 0
        second_flow._run_validation(second_window, request)

        self.assertEqual(2, len(requested_counts))
        self.assertEqual(
            min(flow_module._PERSISTENT_REFRESH_PROBE_COUNT, fetch_count),
            requested_counts[1],
        )
        self.assertEqual([(fetch_count, 8)], second_window.pool_installs)
        self.assertEqual(1, len(second_window.snapshots))

        second_flow._run_validation(second_window, request)
        self.assertEqual(2, len(requested_counts))
        self.assertEqual(2, len(second_window.snapshots))

    def test_failed_refresh_preserves_prior_cache_without_full_fallback(self):
        broker = _FakeBroker(True)
        host = _FakeHost(self.stock)
        cache = IndicatorFollowSignalValidationHistoricalCache(
            Path(self.history_cache_temporary.name) / "failed-incremental"
        )
        seed = self._seed()
        seed_rules = seed.settings_snapshot.to_dict()
        timeframe = seed_rules["bar"]["bar_minutes"]
        timeframe_key = str(
            flow_module.validation_timeframe_from_rules(seed_rules)["key"]
        )
        warmup = flow_module.required_validation_warmup_bars(
            seed_rules
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
            timeframe_key=timeframe_key,
        ))
        cache_path = cache.path_for(
            self.stock.code,
            timeframe,
            fetch_count,
            timeframe_key,
        )
        original_bytes = cache_path.read_bytes()
        requested_counts = []

        class Provider:
            def __init__(_self, session, requester):
                pass

            def request_latest(_self, count, callback):
                requested_counts.append(count)
                callback(ValidationHistoricalResult(False, reason="FAILED"))

        class ReplayWithoutSignalScan:
            def __init__(_self, session):
                _self._inner = ValidationHistoricalReplay(session)

            def evaluate(_self, historical, *, display_count=None):
                return _self._inner.evaluate(
                    historical,
                    display_count=display_count,
                )

        flow = IndicatorFollowSignalValidationFlow(
            broker,
            host=host,
            historical_count=6,
            historical_provider_factory=Provider,
            replay_factory=ReplayWithoutSignalScan,
            recent_stock_store=_MemoryRecentStockStore(),
            historical_cache=cache,
        )
        flow._now_factory = lambda: latest + timedelta(days=3)
        window = _FakeWindow(self.stock, seed)
        self.widgets.append(window)
        flow._open_windows[id(window)] = window
        flow._request_generation[id(window)] = 0
        flow._run_validation(
            window,
            IndicatorFollowSignalValidationRunRequest(seed.settings_snapshot, 2),
        )

        self.assertEqual(
            [min(flow_module._PERSISTENT_REFRESH_PROBE_COUNT, fetch_count)],
            requested_counts,
        )
        self.assertEqual([(fetch_count, 6)], window.pool_installs)
        self.assertEqual(1, len(window.snapshots))
        self.assertEqual([], window.errors)
        self.assertEqual(original_bytes, cache_path.read_bytes())

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
