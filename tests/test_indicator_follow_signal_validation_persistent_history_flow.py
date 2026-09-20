# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from PyQt5.QtCore import QObject, pyqtSignal

import gui_indicator_follow_signal_validation_flow as flow_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_indicator_follow_signal_validation_flow import (
    IndicatorFollowSignalValidationFlow,
)
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
)
from indicator_follow_signal_validation_visualization import (
    required_validation_warmup_bars,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
)


class _Host(QObject):
    validation_blocked = pyqtSignal(str)


class _Window:
    def __init__(self, stock):
        self.stock = stock
        self.pool_installs = []
        self.snapshots = []
        self.errors = []
        self.signal_marker_batches = []

    def set_historical_candle_pool(self, candles, *, chart_candle_count):
        self.pool_installs.append((len(candles), int(chart_candle_count)))

    def set_signal_marker_entries(self, entries):
        self.signal_marker_batches.append(tuple(entries))

    def set_replay_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def show_validation_error(self, message):
        self.errors.append(str(message))


def _rows(count: int, timeframe: int, latest: datetime):
    rows = []
    for offset in range(count):
        when = latest - timedelta(minutes=timeframe * offset)
        value = 100_000.0 + offset
        rows.append({
            "체결시간": when.strftime("%Y%m%d%H%M%S"),
            "시가": str(value),
            "고가": str(value + 10.0),
            "저가": str(value - 10.0),
            "현재가": str(value + 5.0),
            "거래량": str(100 + offset),
        })
    return rows


class SignalValidationPersistentHistoryFlowTest(unittest.TestCase):
    def setUp(self):
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
        self.stock = ValidationStockRef("005930", "삼성전자")
        self.settings = ValidationSettingsSnapshot({
            "bar": {"bar_minutes": 3},
        })
        self.run_request = IndicatorFollowSignalValidationRunRequest(
            self.settings,
            100,
        )
        self.latest = datetime.now(SEOUL_TIMEZONE).replace(
            second=0,
            microsecond=0,
        ) - timedelta(minutes=9)

    def tearDown(self):
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()

    def _flow(self, cache, requested_counts, *, fail_first=False):
        latest = self.latest
        stock = self.stock
        failure = {"remaining": 1 if fail_first else 0}

        class Provider:
            def __init__(_self, session, requester):
                _self.session = session

            def request_latest(_self, count, callback):
                requested_counts.append(int(count))
                if failure["remaining"]:
                    failure["remaining"] -= 1
                    callback(ValidationHistoricalResult(
                        False,
                        reason="TEST_INCREMENTAL_FAILURE",
                    ))
                    return
                request = _self.session.request
                callback(ValidationHistoricalResult(
                    True,
                    snapshot=ValidationHistoricalSnapshot(
                        stock=request.stock,
                        timeframe_minutes=request.timeframe_minutes,
                        requested_count=int(count),
                        request_id=f"REQ-{len(requested_counts)}-{count}",
                        rows=_rows(int(count), request.timeframe_minutes, latest),
                    ),
                ))

        return IndicatorFollowSignalValidationFlow(
            object(),
            host=_Host(),
            historical_count=120,
            historical_provider_factory=Provider,
            replay_factory=ValidationHistoricalReplay,
            historical_cache=cache,
        )

    @staticmethod
    def _register_window(flow, window):
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 0

    def test_restart_uses_persistent_cache_then_same_process_uses_shared_pool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            requested_counts = []

            first_flow = self._flow(cache, requested_counts)
            first_window = _Window(self.stock)
            self._register_window(first_flow, first_window)
            first_flow._run_validation(first_window, self.run_request)

            warmup = required_validation_warmup_bars(self.settings.to_dict())
            fetch_count = 120 + warmup
            self.assertEqual([fetch_count], requested_counts)
            self.assertEqual(1, len(first_window.snapshots))
            persisted = cache.load(self.stock.code, 3, fetch_count)
            self.assertIsNotNone(persisted)
            self.assertEqual(fetch_count, len(persisted.candles))

            # Simulate an application restart: process-local pools are gone,
            # but the Validation-owned cache directory remains.
            flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
            second_flow = self._flow(cache, requested_counts)
            second_window = _Window(self.stock)
            self._register_window(second_flow, second_window)
            second_flow._run_validation(second_window, self.run_request)

            self.assertEqual(2, len(requested_counts))
            self.assertLess(requested_counts[1], fetch_count)
            self.assertGreaterEqual(requested_counts[1], 2)
            self.assertEqual(1, len(second_window.snapshots))
            self.assertEqual(fetch_count, second_window.pool_installs[-1][0])

            before = list(requested_counts)
            second_flow._run_validation(second_window, self.run_request)

            self.assertEqual(before, requested_counts)
            self.assertEqual(2, len(second_window.snapshots))

    def test_failed_incremental_and_failed_full_leave_prior_cache_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            warmup = required_validation_warmup_bars(self.settings.to_dict())
            fetch_count = 120 + warmup
            candles = []
            for offset in range(fetch_count):
                when = self.latest - timedelta(minutes=3 * (fetch_count - 1 - offset))
                value = 40_000.0 + offset
                candles.append({
                    "time": when.strftime("%Y%m%d%H%M%S"),
                    "open": value,
                    "high": value + 1.0,
                    "low": value - 1.0,
                    "close": value,
                    "volume": 20.0 + offset,
                })
            self.assertTrue(cache.store(
                stock_code=self.stock.code,
                stock_name=self.stock.name,
                timeframe_minutes=3,
                requested_count=fetch_count,
                candles=candles,
            ))
            path = cache.path_for(self.stock.code, 3, fetch_count)
            before_bytes = path.read_bytes()
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    _self.session = session

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(ValidationHistoricalResult(
                        False,
                        reason="TEST_BROKER_FAILURE",
                    ))

            flow = IndicatorFollowSignalValidationFlow(
                object(),
                host=_Host(),
                historical_count=120,
                historical_provider_factory=Provider,
                replay_factory=ValidationHistoricalReplay,
                historical_cache=cache,
            )
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual(2, len(requested_counts))
            self.assertLess(requested_counts[0], fetch_count)
            self.assertEqual(fetch_count, requested_counts[1])
            self.assertEqual([], window.snapshots)
            self.assertEqual(before_bytes, path.read_bytes())
            self.assertIsNotNone(cache.load(self.stock.code, 3, fetch_count))

    def test_failed_restart_incremental_falls_back_to_full_without_losing_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            requested_counts = []
            warmup = required_validation_warmup_bars(self.settings.to_dict())
            fetch_count = 120 + warmup
            candles = []
            for offset in range(fetch_count):
                when = self.latest - timedelta(minutes=3 * (fetch_count - 1 - offset))
                value = 50_000.0 + offset
                candles.append({
                    "time": when.strftime("%Y%m%d%H%M%S"),
                    "open": value,
                    "high": value + 1.0,
                    "low": value - 1.0,
                    "close": value,
                    "volume": 10.0 + offset,
                })
            self.assertTrue(cache.store(
                stock_code=self.stock.code,
                stock_name=self.stock.name,
                timeframe_minutes=3,
                requested_count=fetch_count,
                candles=candles,
            ))
            before = cache.load(self.stock.code, 3, fetch_count)
            self.assertIsNotNone(before)

            flow = self._flow(cache, requested_counts, fail_first=True)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual(2, len(requested_counts))
            self.assertLess(requested_counts[0], fetch_count)
            self.assertEqual(fetch_count, requested_counts[1])
            self.assertEqual(1, len(window.snapshots))
            after = cache.load(self.stock.code, 3, fetch_count)
            self.assertIsNotNone(after)
            self.assertEqual(fetch_count, len(after.candles))


if __name__ == "__main__":
    unittest.main()