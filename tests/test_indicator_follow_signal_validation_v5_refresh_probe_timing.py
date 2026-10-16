"""Opt-in timing probes for the V5 cached-history refresh path."""
from __future__ import annotations

from datetime import datetime, timedelta
import tempfile
from unittest.mock import patch

from candle_timeframe_aggregation import SEOUL_TIMEZONE
import gui_indicator_follow_signal_validation_v5_flow as flow_module
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from tests.test_indicator_follow_signal_validation_persistent_history_flow import (
    _Host,
    _Window,
    _legacy_session_unavailable,
)


def _rows(count: int, *, offset: int = 0) -> list[dict[str, object]]:
    start = datetime(2026, 10, 1, 9, 0) + timedelta(minutes=offset)
    return [
        {
            "time": (start + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10 + index,
        }
        for index in range(count)
    ]


def _result(stock, rows, *, ok=True, source="KRX", identity=None):
    if not ok:
        return ValidationHistoricalResult(False, snapshot=None)
    return ValidationHistoricalResult(
        True,
        snapshot=ValidationHistoricalSnapshot(
            stock=stock,
            timeframe_minutes=5,
            timeframe_key="M5",
            requested_count=len(rows),
            request_id="REFRESH-PROBE",
            rows=[
                {
                    "체결시간": row["time"], "시가": str(row["open"]),
                    "고가": str(row["high"]), "저가": str(row["low"]),
                    "현재가": str(row["close"]), "거래량": str(row["volume"]),
                }
                for row in rows
            ],
            market_data_identity=stock.code if identity is None else identity,
            market_source=source,
        ),
    )


def _run(*, perf_enabled: bool, result):
    flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}})
    request = IndicatorFollowSignalValidationRunRequest(settings, 10)
    cached, incoming = _rows(120), _rows(121)
    calls, persisted, installed, events = [], [], [], []

    class Provider:
        def __init__(self, _session, _requester):
            pass

        def request_latest(self, count, callback):
            calls.append(count)
            callback(result(stock, incoming))

    with tempfile.TemporaryDirectory() as tmp:
        cache = IndicatorFollowSignalValidationHistoricalCache(tmp)
        flow = flow_module.IndicatorFollowSignalValidationV5Flow(
            object(), host=_Host(), historical_count=120,
            historical_provider_factory=Provider, historical_cache=cache,
            production_session_contract_reader=_legacy_session_unavailable,
        )
        flow._now_factory = lambda: datetime(2026, 10, 13, 9, 0, tzinfo=SEOUL_TIMEZONE)
        target = _Window(stock)
        key = id(target)
        flow._open_windows[key] = target
        flow._request_generation[key] = 0
        required = 120 + __import__(
            "indicator_follow_validation_history_contract"
        ).required_validation_history_context_bars(settings.to_dict())
        # Match the actual fetch envelope while retaining a complete cached pool.
        cached = _rows(required)
        incoming = _rows(required + 1)
        assert flow._persist_history(
            stock, 5, "M5", required, cached,
            market_data_identity=stock.code, market_source="KRX",
        )
        original_persist = flow._persist_history
        original_install = flow._install_acquired_pool

        def persist(*args, **kwargs):
            persisted.append(args[4])
            return original_persist(*args, **kwargs)

        def install(*args, **kwargs):
            installed.append(args[2])
            return original_install(*args, **kwargs)

        def emit(event, started, owner=None, **metadata):
            events.append((event, owner, metadata))

        with (
            patch.object(flow, "_persist_history", side_effect=persist),
            patch.object(flow, "_install_acquired_pool", side_effect=install),
            patch.object(flow_module, "_PERF_ENABLED", perf_enabled),
            patch.object(flow_module, "_perf_emit", side_effect=emit),
        ):
            flow._run_validation(target, request)
    flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
    return calls, persisted, installed, events


def test_cached_refresh_emits_four_ordered_opt_in_probes_without_behavior_change():
    calls, persisted, installed, events = _run(perf_enabled=True, result=_result)

    assert [event[0] for event in events] == [
        "history.refresh.cache_ready",
        "history.refresh.request_start",
        "history.refresh.response_received",
        "history.refresh.merge_persist_done",
    ]
    marker = events[1][1]
    assert marker is events[2][1] is events[3][1]
    assert marker is not events[0][1]
    assert calls == [300]
    assert len(persisted) == len(installed) == 1
    assert all(event[2]["window_key"] for event in events)
    assert len({event[2]["generation"] for event in events}) == 1
    assert type(events[0][2]["generation"]) is int
    assert all(type(event[2]["probe_count"]) is int for event in events)
    assert events[2][2] == {
        "window_key": events[2][2]["window_key"],
        "generation": events[2][2]["generation"],
        "probe_count": 300, "response_ok": True,
    }


def test_cached_refresh_probes_are_absent_when_disabled():
    calls, persisted, installed, events = _run(perf_enabled=False, result=_result)

    assert calls == [300]
    assert len(persisted) == len(installed) == 1
    assert events == []


def test_rapid_probes_have_unique_markers_and_invalid_source_has_no_merge_probe():
    rapid_calls = []

    def rapid_result(stock, rows):
        rapid_calls.append(1)
        return _result(
            stock,
            _rows(1, offset=len(rows) + 1) if len(rapid_calls) == 1 else rows,
        )

    _calls, _persisted, _installed, rapid = _run(
        perf_enabled=True,
        result=rapid_result,
    )
    _calls, persisted, installed, invalid = _run(
        perf_enabled=True,
        result=lambda stock, rows: _result(stock, rows, identity="000660"),
    )

    request_markers = [event[1] for event in rapid if event[0] == "history.refresh.request_start"]
    assert len(request_markers) == 2
    assert request_markers[0] is not request_markers[1]
    assert [event[0] for event in invalid] == [
        "history.refresh.cache_ready",
        "history.refresh.request_start",
        "history.refresh.response_received",
    ]
    assert persisted == []
    assert len(installed) == 1
