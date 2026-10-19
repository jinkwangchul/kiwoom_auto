"""Offline regression for a short, overlapping V5 M240 cache refresh."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt5.QtCore import QObject, pyqtSignal

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
)
from indicator_follow_validation_history_contract import required_validation_history_context_bars
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)

class _Host(QObject):
    validation_blocked = pyqtSignal(str)


class _Window:
    def __init__(self, stock):
        self.stock = stock


def _legacy_session_unavailable(*_args):
    return {"status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False}


@pytest.fixture(autouse=True)
def clear_shared_pools():
    flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
    yield
    flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()


def _candles(count, minutes, latest):
    first = latest - timedelta(minutes=minutes * (count - 1))
    return [
        {
            "time": (first + timedelta(minutes=minutes * index)).strftime("%Y%m%d%H%M%S"),
            "open": float(1000 + index),
            "high": float(1010 + index),
            "low": float(990 + index),
            "close": float(1005 + index),
            "volume": float(2000 + index),
        }
        for index in range(count)
    ]


def _result(stock, key, minutes, candles, *, identity, source):
    return ValidationHistoricalResult(
        True,
        snapshot=ValidationHistoricalSnapshot(
            stock=stock,
            timeframe_minutes=minutes,
            timeframe_key=key,
            requested_count=len(candles),
            request_id="OFFLINE-REFRESH",
            rows=flow_module.IndicatorFollowSignalValidationV5Flow._historical_rows_from_candles(candles),
            market_data_identity=identity,
            market_source=source,
            request_as_of="2026-10-20T16:00:00+09:00",
        ),
    )


def _run_refresh(tmp_path, *, key="M240", minutes=240, integrated=True,
                 incoming=None, response_identity=None, response_source=None,
                 response_stock=None):
    stock = ValidationStockRef("000660", "SK hynix")
    settings_rules = {"bar": {"bar_minutes": minutes}}
    if key == "D1":
        settings_rules["validation_timeframe"] = {"key": "D1"}
    settings = ValidationSettingsSnapshot(settings_rules)
    requested = 5000 + required_validation_history_context_bars(settings.to_dict())
    latest = datetime(2026, 10, 19, 0 if key == "D1" else 8)
    cached = _candles(744, 1440 if key == "D1" else minutes, latest)
    if incoming is None:
        incoming = [dict(candle) for candle in cached[-299:]]
        incoming.append(_candles(1, 1, latest + timedelta(
            minutes=1440 if key == "D1" else minutes
        ))[0])
    identity = f"{stock.code}_AL" if integrated else stock.code
    source = "INTEGRATED" if integrated else "KRX"
    calls = []
    installs = []
    errors = []

    class FakeBroker:
        def request_minute_candles_display_only_integrated(self, *_args, **_kwargs):
            raise AssertionError("provider must not call a broker")

        def request_minute_candles_read_only(self, *_args, **_kwargs):
            raise AssertionError("provider must not call a broker")

        def request_period_candles_read_only_integrated(self, *_args, **_kwargs):
            raise AssertionError("provider must not call a broker")

        def request_period_candles_read_only(self, *_args, **_kwargs):
            raise AssertionError("provider must not call a broker")

    broker = FakeBroker()

    class FakeValidationHistoricalProvider:
        def __init__(self, session, requester):
            assert session.request.stock == stock
            assert requester.broker is broker
            assert requester.timeframe_key == key
            assert requester.integrated is integrated

        def request_latest(self, count, callback):
            calls.append(count)
            callback(_result(
                response_stock or stock, key, minutes, incoming,
                identity=identity if response_identity is None else response_identity,
                source=source if response_source is None else response_source,
            ))

    cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        broker,
        host=_Host(),
        historical_count=5000,
        historical_provider_factory=FakeValidationHistoricalProvider,
        historical_cache=cache,
        production_session_contract_reader=_legacy_session_unavailable,
    )
    flow._now_factory = lambda: datetime(2026, 10, 20, 16, tzinfo=SEOUL_TIMEZONE)
    flow._validation_source_by_code[stock.code] = integrated
    assert flow._persist_history(
        stock, minutes, key, requested, cached,
        market_data_identity=identity, market_source=source,
    )
    target = _Window(stock)
    flow._open_windows[id(target)] = target
    flow._request_generation[id(target)] = 0
    library = SimpleNamespace(
        state=flow_module.STOCK_LIBRARY_READY,
        records=[{"code": stock.code, "nxt_available": integrated}],
    )
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=library),
        patch.object(flow, "_install_acquired_pool", side_effect=lambda _window, _session, pool, **kwargs: installs.append((pool, kwargs["replacement_reason"]))),
        patch.object(flow, "_fail_window", side_effect=lambda _window, reason: errors.append(reason)),
    ):
        flow._run_validation(
            target, IndicatorFollowSignalValidationRunRequest(settings, 10)
        )
    saved = cache.load(
        stock.code, minutes, requested, key,
        market_data_identity=identity if integrated else "",
    )
    return requested, cached, calls, installs, errors, saved


def test_m240_short_overlap_updates_and_preserves_all_cached_history(tmp_path):
    latest = datetime(2026, 10, 19, 8)
    original = _candles(744, 240, latest)
    incoming = [dict(candle) for candle in original[-299:]]
    incoming[-1] = {
        "time": "20261019080000", "open": 9000.0, "high": 9020.0,
        "low": 8990.0, "close": 9010.0, "volume": 9900.0,
    }
    incoming.append({
        "time": "20261019120000", "open": 9100.0, "high": 9120.0,
        "low": 9090.0, "close": 9110.0, "volume": 9910.0,
    })

    requested, cached, calls, installs, errors, saved = _run_refresh(
        tmp_path, incoming=incoming
    )

    assert requested == 5900
    assert calls == [300]
    assert errors == []
    assert saved is not None and len(saved.candles) == 745
    assert list(saved.candles)[:445] == cached[:445]
    assert saved.candles[-2] == incoming[-2]
    assert saved.candles[-1] == incoming[-1]
    assert len(installs) == 1 and installs[0][1] == "cache_refresh_merge"
    assert installs[0][0]["candles"] == list(saved.candles)


def test_m240_disjoint_short_response_keeps_cached_pool_after_adaptive_probe(tmp_path):
    incoming = _candles(300, 240, datetime(2027, 1, 8, 12))
    _requested, cached, calls, installs, errors, saved = _run_refresh(
        tmp_path, incoming=incoming
    )

    assert calls == [300, 600, 1200, 2400, 4800, 5900]
    assert errors == []
    assert saved is not None and list(saved.candles) == cached
    assert len(installs) == 1 and installs[0][1] == "persistent_restore"
    assert installs[0][0]["candles"] == cached


@pytest.mark.parametrize(
    ("identity", "source", "response_stock"),
    [
        ("000660", "KRX", None),
        ("005930_AL", "INTEGRATED", ValidationStockRef("005930", "Samsung")),
    ],
)
def test_m240_wrong_market_or_stock_identity_fails_without_persisting(
    tmp_path, identity, source, response_stock
):
    _requested, cached, calls, installs, errors, saved = _run_refresh(
        tmp_path, response_identity=identity, response_source=source,
        response_stock=response_stock,
    )

    assert calls == [300]
    assert errors == ["HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID"]
    assert installs == []
    assert saved is not None and list(saved.candles) == cached


@pytest.mark.parametrize(("key", "minutes"), [("M1", 1), ("M3", 3)])
def test_other_minutes_still_reject_short_overlap(tmp_path, key, minutes):
    _requested, cached, calls, installs, errors, saved = _run_refresh(
        tmp_path, key=key, minutes=minutes, integrated=False
    )

    assert calls == [300]
    assert errors == []
    assert saved is not None and list(saved.candles) == cached
    assert len(installs) == 1 and installs[0][1] == "persistent_restore"


def test_daily_short_overlap_still_merges(tmp_path):
    _requested, cached, calls, installs, errors, saved = _run_refresh(
        tmp_path, key="D1", minutes=5, integrated=False
    )

    assert calls == [300]
    assert errors == []
    assert saved is not None and len(saved.candles) == 745
    assert saved.candles[0] == cached[0]
    assert len(installs) == 1 and installs[0][1] == "cache_refresh_merge"
    assert installs[0][0]["candles"] == list(saved.candles)
