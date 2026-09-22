# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
import json
import tempfile
import time
import unittest

from PyQt5.QtCore import QCoreApplication, QObject, pyqtSignal

import gui_indicator_follow_signal_validation_flow as flow_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_indicator_follow_signal_validation_flow import IndicatorFollowSignalValidationFlow
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
)
from indicator_follow_signal_validation_visualization import required_validation_warmup_bars
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
    ValidationReplayEntry,
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


def _candles(count: int, timeframe: int, latest: datetime, *, base=100_000.0):
    candles = []
    for offset in reversed(range(count)):
        when = latest - timedelta(minutes=timeframe * offset)
        value = base + count - offset
        candles.append({
            "time": when.strftime("%Y%m%d%H%M%S"),
            "open": value,
            "high": value + 10.0,
            "low": value - 10.0,
            "close": value + 5.0,
            "volume": 100.0 + offset,
        })
    return candles


def _result(
    stock,
    timeframe: int,
    candles,
    request_id="TEST",
    *,
    market_data_identity="",
    market_source="",
):
    rows = [{
        "체결시간": candle["time"],
        "시가": str(candle["open"]),
        "고가": str(candle["high"]),
        "저가": str(candle["low"]),
        "현재가": str(candle["close"]),
        "거래량": str(candle["volume"]),
    } for candle in candles]
    return ValidationHistoricalResult(
        True,
        snapshot=ValidationHistoricalSnapshot(
            stock=stock,
            timeframe_minutes=timeframe,
            requested_count=len(rows),
            request_id=request_id,
            rows=rows,
            market_data_identity=market_data_identity,
            market_source=market_source,
        ),
    )


class SignalValidationPersistentHistoryFlowTest(unittest.TestCase):
    def setUp(self):
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
        self.stock = ValidationStockRef("005930", "삼성전자")
        self.settings = ValidationSettingsSnapshot({"bar": {"bar_minutes": 3}})
        self.run_request = IndicatorFollowSignalValidationRunRequest(self.settings, 100)
        self.latest = datetime.now(SEOUL_TIMEZONE).replace(
            second=0,
            microsecond=0,
        ) - timedelta(minutes=30)

    def tearDown(self):
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()

    @property
    def fetch_count(self):
        return 120 + required_validation_warmup_bars(self.settings.to_dict())

    @property
    def probe_count(self):
        return min(flow_module._PERSISTENT_REFRESH_PROBE_COUNT, self.fetch_count)

    def _store_cache(self, cache, *, candles=None, latest=None):
        stored = candles or _candles(
            self.fetch_count,
            3,
            self.latest if latest is None else latest,
        )
        self.assertTrue(cache.store(
            stock_code=self.stock.code,
            stock_name=self.stock.name,
            timeframe_minutes=3,
            requested_count=self.fetch_count,
            candles=stored,
        ))
        return stored

    def _set_cache_updated_at(self, cache, updated_at: datetime) -> None:
        path = cache.path_for(self.stock.code, 3, self.fetch_count)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["updated_at"] = updated_at.isoformat()
        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        self.assertIsNotNone(
            cache.load(self.stock.code, 3, self.fetch_count)
        )

    def _flow(self, cache, provider_factory):
        class ReplayWithoutSignalScan:
            def __init__(_self, session):
                _self._inner = ValidationHistoricalReplay(session)

            def evaluate(_self, historical, *, display_count=None):
                return _self._inner.evaluate(
                    historical,
                    display_count=display_count,
                )

        flow = IndicatorFollowSignalValidationFlow(
            object(),
            host=_Host(),
            historical_count=120,
            historical_provider_factory=provider_factory,
            replay_factory=ReplayWithoutSignalScan,
            historical_cache=cache,
        )
        # Most tests below exercise the broker-refresh path. Keep their
        # default clock on a weekday after the freshly stored cache; tests
        # for the weekend no-probe optimization override this explicitly.
        flow._now_factory = lambda: self.latest + timedelta(days=1)
        return flow

    def test_market_data_identity_follows_broker_contract_and_unknown_is_krx(self):
        class IntegratedBroker:
            @staticmethod
            def _market_data_request_identity(code):
                return code, f"{code}_AL", "INTEGRATED"

        flow = IndicatorFollowSignalValidationFlow(IntegratedBroker(), host=_Host())
        self.assertEqual("005930_AL", flow._validation_market_data_identity(self.stock))

        fallback = IndicatorFollowSignalValidationFlow(object(), host=_Host())
        self.assertEqual("005930", fallback._validation_market_data_identity(self.stock))

    def test_response_provenance_is_persisted_after_broker_resolver_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            flow = self._flow(cache, lambda *_args: None)
            flow._broker = type(
                "ChangedBroker",
                (),
                {
                    "_market_data_request_identity": staticmethod(
                        lambda code: (code, code, "KRX")
                    )
                },
            )()
            session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, self.settings, 3),
                operation_active_reader=lambda: False,
            )
            window = _Window(self.stock)
            self._register_window(flow, window)
            result = _result(
                self.stock,
                3,
                _candles(self.fetch_count, 3, self.latest),
                "INTEGRATED-RESPONSE",
                market_data_identity="005930_AL",
                market_source="INTEGRATED",
            )

            self.assertTrue(
                flow._handle_historical_result(
                    window,
                    session,
                    result,
                    evaluation_count=100,
                    cache_requested_count=self.fetch_count,
                    source_session=session,
                )
            )

            marker_path = flow._validation_cache_source_marker_path(
                self.stock,
                3,
                "M3",
                self.fetch_count,
            )
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            entry = cache.load(self.stock.code, 3, self.fetch_count, "M3")
            self.assertEqual("005930_AL", marker["market_data_identity"])
            self.assertEqual("INTEGRATED", marker["market_source"])
            self.assertEqual("005930_AL", entry.market_data_identity)
            self.assertEqual("INTEGRATED", entry.market_source)

    @staticmethod
    def _register_window(flow, window):
        key = id(window)
        flow._open_windows[key] = window
        flow._request_generation[key] = 0

    def test_regular_market_only_derives_minute_view_without_mutating_raw_pool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            flow = self._flow(cache, lambda *_args: None)
            candles = [
                {
                    "time": stamp,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 10.0,
                }
                for index, stamp in enumerate((
                    "20260918080000",
                    "20260918090000",
                    "20260918152700",
                    "20260918153000",
                    "20260918160000",
                ))
            ]
            all_settings = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "M3"},
                "validation_market_scope": {"regular_market_only": False},
            })
            regular_settings = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "M3"},
                "validation_market_scope": {"regular_market_only": True},
            })
            all_session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, all_settings, 3),
                operation_active_reader=lambda: False,
            )
            regular_session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, regular_settings, 3),
                operation_active_reader=lambda: False,
            )
            pool = flow._pool_from_candles(
                all_session,
                candles,
                requested_count=len(candles),
                request_id="ALL-SESSIONS",
            )

            all_view = flow._validation_pool_for_session(all_session, pool)
            regular_view = flow._validation_pool_for_session(regular_session, pool)

            self.assertIs(all_view, pool)
            self.assertEqual(candles, pool["candles"])
            self.assertEqual(
                ["20260918090000", "20260918152700", "20260918153000"],
                [item["time"] for item in regular_view["candles"]],
            )
            self.assertEqual(
                ["20260918090000", "20260918152700", "20260918153000"],
                [
                    item["체결시간"]
                    for item in regular_view["snapshot"].to_rows()
                ],
            )
            self.assertEqual(5, len(pool["candles"]))

            period_settings = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "Y1"},
                "validation_market_scope": {"regular_market_only": True},
            })
            period_session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, period_settings, 3),
                operation_active_reader=lambda: False,
            )
            period_pool = flow._pool_from_candles(
                period_session,
                candles,
                requested_count=len(candles),
                request_id="PERIOD-SESSION",
            )
            self.assertIs(
                period_pool,
                flow._validation_pool_for_session(period_session, period_pool),
            )

    def test_regular_market_large_timeframe_fetches_five_minute_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            flow = self._flow(cache, lambda *_args: None)
            settings = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "M120"},
                "validation_market_scope": {"regular_market_only": True},
            })
            session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, settings, 3),
                operation_active_reader=lambda: False,
            )

            fetch_session, minutes, key, count = flow._validation_fetch_contract(
                session,
                5060,
            )

            self.assertEqual(5, minutes)
            self.assertEqual("M5", key)
            self.assertEqual("M5", fetch_session.request.timeframe_key)
            self.assertGreater(count, 100_000)
            self.assertLessEqual(count, 200_000)

            all_market = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "M120"},
                "validation_market_scope": {"regular_market_only": False},
            })
            all_session = flow_module.ValidationSession(
                flow_module.ValidationRequest(self.stock, all_market, 3),
                operation_active_reader=lambda: False,
            )
            same_session, same_minutes, same_key, same_count = (
                flow._validation_fetch_contract(all_session, 5060)
            )
            self.assertIs(all_session, same_session)
            self.assertEqual(3, same_minutes)
            self.assertEqual("M120", same_key)
            self.assertEqual(5060, same_count)

    def test_regular_market_run_fetches_m5_and_installs_target_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            seen = {}
            source_rows = [
                {
                    "time": stamp,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 10.0,
                }
                for stamp, price in (
                    ("20260918130000", 100.0),
                    ("20260918145500", 101.0),
                    ("20260918150000", 102.0),
                    ("20260918151500", 103.0),
                    ("20260918153000", 105.0),
                    ("20260918153500", 130.0),
                    ("20260918160000", 140.0),
                )
            ]

            class Provider:
                def __init__(_self, session, _requester):
                    seen["timeframe_key"] = session.request.timeframe_key
                    seen["timeframe_minutes"] = session.request.timeframe_minutes

                def request_latest(_self, count, callback):
                    seen["count"] = int(count)
                    callback(_result(self.stock, 5, source_rows, "REGULAR-M5"))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            settings = ValidationSettingsSnapshot({
                "bar": {"bar_minutes": 3},
                "validation_timeframe": {"key": "M120"},
                "validation_market_scope": {"regular_market_only": True},
            })
            flow._run_validation(
                window,
                IndicatorFollowSignalValidationRunRequest(settings, 2),
            )

            self.assertEqual("M5", seen["timeframe_key"])
            self.assertEqual(5, seen["timeframe_minutes"])
            self.assertGreater(seen["count"], 120)
            self.assertTrue(window.pool_installs)
            self.assertEqual(2, window.pool_installs[-1][0])

    def test_weekend_verified_cache_reuses_without_broker_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            friday = datetime(2026, 9, 18, 19, 57, tzinfo=SEOUL_TIMEZONE)
            self._store_cache(cache, latest=friday)
            self._set_cache_updated_at(
                cache,
                datetime(2026, 9, 20, 11, 40, tzinfo=SEOUL_TIMEZONE),
            )
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: datetime(
                2026, 9, 20, 12, 40, tzinfo=SEOUL_TIMEZONE
            )
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertEqual(1, len(window.snapshots))
            self.assertEqual([], window.errors)

    def test_friday_verified_cache_keeps_broker_probe_on_weekend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            friday = datetime(2026, 9, 18, 15, 0, tzinfo=SEOUL_TIMEZONE)
            self._store_cache(cache, latest=friday)
            self._set_cache_updated_at(
                cache,
                datetime(2026, 9, 18, 15, 5, tzinfo=SEOUL_TIMEZONE),
            )
            requested_counts = []
            pending = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    pending.append(callback)

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: datetime(
                2026, 9, 20, 12, 40, tzinfo=SEOUL_TIMEZONE
            )
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([], window.pool_installs)
            self.assertEqual(0, len(window.snapshots))

    def test_monday_after_weekend_verification_keeps_broker_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            friday = datetime(2026, 9, 18, 19, 57, tzinfo=SEOUL_TIMEZONE)
            self._store_cache(cache, latest=friday)
            self._set_cache_updated_at(
                cache,
                datetime(2026, 9, 20, 11, 40, tzinfo=SEOUL_TIMEZONE),
            )
            requested_counts = []
            pending = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    pending.append(callback)

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: datetime(
                2026, 9, 21, 8, 5, tzinfo=SEOUL_TIMEZONE
            )
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([], window.pool_installs)
            self.assertEqual(0, len(window.snapshots))

    def test_future_cache_verification_time_fails_closed_to_broker_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            friday = datetime(2026, 9, 18, 19, 57, tzinfo=SEOUL_TIMEZONE)
            self._store_cache(cache, latest=friday)
            self._set_cache_updated_at(
                cache,
                datetime(2026, 9, 20, 13, 0, tzinfo=SEOUL_TIMEZONE),
            )
            requested_counts = []
            pending = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    pending.append(callback)

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: datetime(
                2026, 9, 20, 12, 40, tzinfo=SEOUL_TIMEZONE
            )
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([], window.pool_installs)
            self.assertEqual(0, len(window.snapshots))

    def test_equal_weekday_verification_time_still_requires_broker_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            monday = datetime(2026, 9, 21, 8, 5, tzinfo=SEOUL_TIMEZONE)
            self._store_cache(cache, latest=datetime(
                2026, 9, 18, 19, 57, tzinfo=SEOUL_TIMEZONE
            ))
            self._set_cache_updated_at(cache, monday)
            requested_counts = []
            pending = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    pending.append(callback)

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: monday
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([], window.pool_installs)
            self.assertEqual(0, len(window.snapshots))

    def test_naive_cache_verification_time_is_rejected_and_full_fetches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            path = cache.path_for(self.stock.code, 3, self.fetch_count)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["updated_at"] = "2026-09-20T11:40:00"
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            self.assertIsNone(cache.load(self.stock.code, 3, self.fetch_count))
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    _self.session = session

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(int(count), 3, self.latest),
                        "FULL-AFTER-NAIVE-CACHE",
                    ))

            flow = self._flow(cache, Provider)
            flow._now_factory = lambda: datetime(
                2026, 9, 20, 12, 40, tzinfo=SEOUL_TIMEZONE
            )
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([self.fetch_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertEqual(1, len(window.snapshots))

    def test_persisted_signal_entries_restore_without_full_signal_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            sunday = datetime(2026, 9, 20, 12, 40, tzinfo=SEOUL_TIMEZONE)
            self._set_cache_updated_at(cache, sunday)
            entry = ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=self.fetch_count - 1,
                evaluation_time=self.latest.strftime("%Y%m%d%H%M%S"),
                signal="BUY",
                reason="persisted",
                signal_index=self.fetch_count - 1,
                signal_time=self.latest.strftime("%Y%m%d%H%M%S"),
                delay_bar=0,
                matched_groups=["B"],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            )
            self.assertTrue(cache.store_signal_entries(
                stock_code=self.stock.code,
                timeframe_minutes=3,
                requested_count=self.fetch_count,
                settings_hash=self.settings.rules_hash,
                entries=[entry.to_dict()],
            ))
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))

            class Replay:
                def __init__(_self, session):
                    _self._inner = ValidationHistoricalReplay(session)

                def evaluate(_self, historical, *, display_count=None):
                    return _self._inner.evaluate(
                        historical,
                        display_count=display_count,
                    )

                def scan_signal_entries(_self, historical, *, context_provider=None):
                    raise AssertionError("persisted signal entries must skip full scan")

            flow = IndicatorFollowSignalValidationFlow(
                object(),
                host=_Host(),
                historical_count=120,
                historical_provider_factory=Provider,
                replay_factory=Replay,
                historical_cache=cache,
            )
            flow._now_factory = lambda: sunday
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)
            application = QCoreApplication.instance() or QCoreApplication([])
            deadline = time.monotonic() + 2.0
            while not window.pool_installs and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.005)

            self.assertEqual([], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertEqual(1, len(window.signal_marker_batches))
            self.assertEqual((entry,), window.signal_marker_batches[0])
            self.assertEqual(1, len(window.snapshots))
            self.assertEqual([], window.errors)

    def test_scan_completion_persists_signal_entries_for_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = self._store_cache(cache)
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            session = ValidationHistoricalReplay
            request = flow_module.ValidationRequest(self.stock, self.settings, 3)
            validation_session = flow_module.ValidationSession(
                request,
                operation_active_reader=lambda: False,
            )
            pool = flow._pool_from_candles(
                validation_session,
                candles,
                requested_count=self.fetch_count,
                request_id="PERSIST-SCAN",
            )
            key = id(window)
            flow._historical_pools[key] = pool
            flow._validation_sessions[key] = validation_session
            entry = ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=self.fetch_count - 1,
                evaluation_time=self.latest.strftime("%Y%m%d%H%M%S"),
                signal="BUY",
                reason="persisted-after-scan",
                signal_index=self.fetch_count - 1,
                signal_time=self.latest.strftime("%Y%m%d%H%M%S"),
                delay_bar=0,
                matched_groups=["B"],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            )

            flow._on_signal_scan_completed({
                "window_key": key,
                "generation": flow._request_generation[key],
                "session": validation_session,
                "pool": pool,
                "evaluation_count": 100,
                "settings_hash": self.settings.rules_hash,
                "active_signal_signature": flow._signal_view_signature(pool),
                "entries": (entry,),
                "error": "",
            })

            self.assertEqual(
                [entry.to_dict()],
                cache.load_signal_entries(
                    self.stock.code,
                    3,
                    self.fetch_count,
                    self.settings.rules_hash,
                ),
            )

    def test_signal_entry_cache_is_scoped_to_exact_active_view(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            scan_counts = []

            class Replay:
                def __init__(_self, session):
                    _self._inner = ValidationHistoricalReplay(session)

                def evaluate(_self, historical, *, display_count=None):
                    return _self._inner.evaluate(
                        historical,
                        display_count=display_count,
                    )

                def scan_signal_entries(
                    _self,
                    historical,
                    *,
                    context_provider=None,
                ):
                    scan_counts.append(historical.requested_count)
                    return ()

            flow = IndicatorFollowSignalValidationFlow(
                object(),
                host=_Host(),
                historical_count=120,
                replay_factory=Replay,
                historical_cache=cache,
            )
            request = flow_module.ValidationRequest(self.stock, self.settings, 3)
            session = flow_module.ValidationSession(
                request,
                operation_active_reader=lambda: False,
            )
            pool = flow._pool_from_candles(
                session,
                _candles(240, 3, self.latest),
                requested_count=240,
                request_id="ACTIVE-SIGNATURE",
            )
            window = _Window(self.stock)
            self._register_window(flow, window)
            key = id(window)
            flow._history_targets[key] = 120
            first_view = flow._validation_pool_for_session(
                session,
                pool,
                target_count=120,
            )
            settings_hash = self.settings.rules_hash
            pool["signal_entries_by_settings_hash"][settings_hash] = ()
            pool["signal_entries_signature_by_settings_hash"][settings_hash] = (
                flow._signal_view_signature(first_view)
            )

            application = QCoreApplication.instance() or QCoreApplication([])
            flow._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=100,
            )
            deadline = time.monotonic() + 2.0
            while not window.snapshots and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.005)
            self.assertEqual([], scan_counts)

            window.snapshots.clear()
            flow._history_targets[key] = 240
            flow._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=100,
            )
            deadline = time.monotonic() + 2.0
            while not scan_counts and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.005)

            self.assertEqual([240], scan_counts)

    def test_valid_cache_waits_for_deferred_probe_then_applies_final_pool_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            cached_candles = self._store_cache(cache)
            cache_path = cache.path_for(self.stock.code, 3, self.fetch_count)
            before_bytes = cache_path.read_bytes()
            requested_counts = []
            pending = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    pending.append(callback)

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([], window.pool_installs)
            self.assertEqual(0, len(window.snapshots))

            pending.pop()(_result(
                self.stock,
                3,
                cached_candles[-self.probe_count:],
                "UNCHANGED",
            ))

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertEqual(1, len(window.snapshots))
            self.assertEqual(before_bytes, cache_path.read_bytes())

            flow._run_validation(window, self.run_request)
            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual(2, len(window.snapshots))

    def test_large_cache_refresh_starts_at_three_hundred_then_doubles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            historical_count = 600
            warmup = required_validation_warmup_bars(self.settings.to_dict())
            fetch_count = historical_count + warmup
            cached_latest = self.latest
            cached = _candles(fetch_count, 3, cached_latest)
            self.assertTrue(cache.store(
                stock_code=self.stock.code,
                stock_name=self.stock.name,
                timeframe_minutes=3,
                requested_count=fetch_count,
                candles=cached,
            ))
            requested_counts = []
            remote_latest = cached_latest + timedelta(minutes=3 * 350)

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(int(count), 3, remote_latest),
                        f"LARGE-{count}",
                    ))

            class ReplayWithoutSignalScan:
                def __init__(_self, session):
                    _self._inner = ValidationHistoricalReplay(session)

                def evaluate(_self, historical, *, display_count=None):
                    return _self._inner.evaluate(
                        historical,
                        display_count=display_count,
                    )

            flow = IndicatorFollowSignalValidationFlow(
                object(),
                host=_Host(),
                historical_count=historical_count,
                historical_provider_factory=Provider,
                replay_factory=ReplayWithoutSignalScan,
                historical_cache=cache,
            )
            flow._now_factory = lambda: self.latest + timedelta(days=1)
            window = _Window(self.stock)
            self._register_window(flow, window)

            flow._run_validation(window, self.run_request)

            self.assertEqual([300, 600], requested_counts)
            self.assertEqual(1, len(window.pool_installs))
            self.assertEqual(1, len(window.snapshots))

    def test_adaptive_probe_grows_until_overlap_then_merges_and_installs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            requested_counts = []
            newer_latest = self.latest + timedelta(minutes=6)

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    candles = (
                        _candles(2, 3, newer_latest)
                        if count == 2
                        else _candles(4, 3, newer_latest)
                    )
                    callback(_result(self.stock, 3, candles, f"PROBE-{count}"))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual(1, len(window.pool_installs))
            self.assertEqual(1, len(window.snapshots))
            refreshed = cache.load(self.stock.code, 3, self.fetch_count)
            self.assertIsNotNone(refreshed)
            self.assertEqual(
                newer_latest.strftime("%Y%m%d%H%M%S"),
                refreshed.candles[-1]["time"],
            )

    def test_adaptive_probe_at_fetch_count_rebuilds_from_latest_full_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            requested_counts = []
            newer_latest = self.latest + timedelta(
                minutes=3 * (self.fetch_count + 1)
            )

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(int(count), 3, newer_latest),
                        f"FULL-PROBE-{count}",
                    ))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            expected_counts = []
            count = self.probe_count
            while True:
                expected_counts.append(count)
                if count == self.fetch_count:
                    break
                count = min(self.fetch_count, count * 2)
            self.assertEqual(expected_counts, requested_counts)
            self.assertEqual(1, len(window.pool_installs))
            refreshed = cache.load(self.stock.code, 3, self.fetch_count)
            self.assertIsNotNone(refreshed)
            self.assertGreater(
                refreshed.candles[0]["time"],
                self.latest.strftime("%Y%m%d%H%M%S"),
            )

    def test_refresh_failure_keeps_installed_cache_without_full_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            cache_path = cache.path_for(self.stock.code, 3, self.fetch_count)
            before_bytes = cache_path.read_bytes()
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(ValidationHistoricalResult(False, reason="FAILED"))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertEqual(1, len(window.snapshots))
            self.assertEqual([], window.errors)
            self.assertEqual(before_bytes, cache_path.read_bytes())

    def test_insufficient_overlap_merge_keeps_installed_short_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache, candles=_candles(3, 3, self.latest))
            cache_path = cache.path_for(self.stock.code, 3, self.fetch_count)
            before_bytes = cache_path.read_bytes()
            requested_counts = []
            incoming = _candles(4, 3, self.latest + timedelta(minutes=6))

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(self.stock, 3, incoming, "SHORT-MERGE"))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            self.assertEqual([(3, 120)], window.pool_installs)
            self.assertEqual(1, len(window.snapshots))
            self.assertEqual(before_bytes, cache_path.read_bytes())

    def test_probe_updates_same_timestamp_forming_candle_prices(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self._store_cache(cache)
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(
                            int(count),
                            3,
                            self.latest,
                            base=300_000.0,
                        ),
                        "SAME-TIME-PRICE-REFRESH",
                    ))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.probe_count], requested_counts)
            refreshed = cache.load(self.stock.code, 3, self.fetch_count)
            self.assertIsNotNone(refreshed)
            self.assertEqual(
                self.latest.strftime("%Y%m%d%H%M%S"),
                refreshed.candles[-1]["time"],
            )
            self.assertEqual(
                300_000.0 + self.probe_count + 5.0,
                refreshed.candles[-1]["close"],
            )

    def test_forced_refresh_bypasses_memory_pool_and_persistent_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            cached_candles = self._store_cache(cache)
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(
                            int(count),
                            3,
                            self.latest + timedelta(minutes=3),
                            base=200_000.0,
                        ),
                        "FORCED-REFRESH",
                    ))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            request = flow_module.ValidationRequest(self.stock, self.settings, 3)
            validation_session = flow_module.ValidationSession(
                request,
                operation_active_reader=lambda: False,
            )
            pool = flow._pool_from_candles(
                validation_session,
                cached_candles,
                requested_count=self.fetch_count,
                request_id="STALE-MEMORY-POOL",
            )
            flow._historical_pools[id(window)] = pool

            forced_request = IndicatorFollowSignalValidationRunRequest(
                self.settings,
                100,
                force_historical_refresh=True,
            )
            flow._run_validation(window, forced_request)

            self.assertEqual([self.fetch_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            refreshed = cache.load(self.stock.code, 3, self.fetch_count)
            self.assertIsNotNone(refreshed)
            self.assertEqual(
                (self.latest + timedelta(minutes=3)).strftime("%Y%m%d%H%M%S"),
                refreshed.candles[-1]["time"],
            )

    def test_missing_cache_performs_normal_full_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(int(count), 3, self.latest),
                        "FULL",
                    ))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.fetch_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertIsNotNone(cache.load(self.stock.code, 3, self.fetch_count))

    def test_invalid_cache_performs_normal_full_fetch_without_using_bad_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            cache_path = cache.path_for(self.stock.code, 3, self.fetch_count)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text('{"schema_version":999}', encoding="utf-8")
            requested_counts = []

            class Provider:
                def __init__(_self, session, requester):
                    pass

                def request_latest(_self, count, callback):
                    requested_counts.append(int(count))
                    callback(_result(
                        self.stock,
                        3,
                        _candles(int(count), 3, self.latest),
                        "FULL-AFTER-INVALID",
                    ))

            flow = self._flow(cache, Provider)
            window = _Window(self.stock)
            self._register_window(flow, window)
            flow._run_validation(window, self.run_request)

            self.assertEqual([self.fetch_count], requested_counts)
            self.assertEqual([(self.fetch_count, 120)], window.pool_installs)
            self.assertIsNotNone(cache.load(self.stock.code, 3, self.fetch_count))


if __name__ == "__main__":
    unittest.main()
