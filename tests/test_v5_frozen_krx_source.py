"""Frozen KRX-only V5 selection uses native broker history end to end."""

from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
from indicator_follow_signal_validation_historical_cache import IndicatorFollowSignalValidationHistoricalCache
from stock_market_source_plan import initialize_stock_market_source_plan
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Window


def _rows():
    return v5.IndicatorFollowSignalValidationV5Flow._historical_rows_from_candles([
        {"time": f"2026102309{i * 5:02d}00", "open": 100 + i,
         "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 100}
        for i in range(10)
    ])


def _run_krx(tmp_path, monkeypatch, *, response_source="KRX", response_identity="005930",
             start_source="KRX", start_identity="005930"):
    now = datetime.now(SEOUL_TIMEZONE).replace(hour=12, minute=0, second=0, microsecond=0)
    initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=now.date(),
        decided_at=now, nxt_available=False,
    )
    monkeypatch.setattr(v5, "PROJECT_ROOT", tmp_path)
    flow = _flow()
    flow._now_factory = lambda: now
    stock = v5.ValidationStockRef("005930", "Samsung")
    window = _Window(stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    calls = []
    received = []
    failures = []

    def request_krx(code, name, **kwargs):
        calls.append((code, name, kwargs["interval"], kwargs["count"]))
        kwargs["callback"]({
            "ok": True, "type": "minute_candles", "code": code,
            "interval": kwargs["interval"], "request_id": "krx-1",
            "rqname": "krx-1", "market_data_identity": response_identity,
            "market_source": response_source, "rows": _rows(),
            "rows_count": 10, "trcode": "opt10080",
        })
        return {"ok": True, "rqname": "krx-1", "market_source": start_source,
                "market_data_identity": start_identity, "display_only": True}

    flow._broker = SimpleNamespace(
        request_minute_candles_display_only_krx=request_krx,
        request_minute_candles_display_only_integrated=lambda *_a, **_k: pytest.fail("SOR requested"),
    )
    rules = {
        "enabled": True, "bar": {"bar_minutes": 5},
        "indicators": {"rsi": {"period": 3}},
        "buy": {"filters": {"rsi": {"enabled": True, "conditions": [
            {"enabled": True, "target": "RSI", "operator": "<=", "value": 100},
        ]}}, "groups": []},
        "sell": {"filters": {}, "signals": {}},
    }
    run = IndicatorFollowSignalValidationRunRequest(
        v5.ValidationSettingsSnapshot(rules), 5, force_historical_refresh=True,
    )
    with (patch.object(flow, "_sync_v5_observation_lease"),
          patch.object(flow, "_start_pool_prepare", side_effect=received.append),
          patch.object(flow, "_fail_window", side_effect=lambda _w, reason: failures.append(reason))):
        flow._run_validation(window, run)
    return flow, calls, received, failures


def test_frozen_krx_only_opens_native_m5_and_calculates_filter(tmp_path, monkeypatch):
    flow, calls, received, failures = _run_krx(tmp_path, monkeypatch)
    assert not failures
    assert calls == [("005930", "Samsung", 5, 5900)]
    assert len(received) == 1
    assert received[0]["window"] is not None
    session, pool = received[0]["session"], received[0]["pool"]
    assert pool["market_data_identity"] == "005930"
    assert pool["market_source"] == "KRX"
    assert [row["time"] for row in pool["candles"]] == [
        f"2026102309{i * 5:02d}00" for i in range(10)
    ]
    calculated = flow._prepare_replay_result_from_pool(
        session, pool, history_target=10, evaluation_count=5,
    )
    assert calculated.ok is True
    assert calculated.snapshot is not None
    assert calculated.snapshot.candle_count == 10
    assert calculated.snapshot.to_entries()
    assert [row["close"] for row in calculated.snapshot.to_display_candles()] == [
        105, 106, 107, 108, 109,
    ]


def test_frozen_krx_cache_keeps_source_marker_and_rejects_changed_provenance(
    tmp_path, monkeypatch,
):
    flow, _calls, received, failures = _run_krx(tmp_path, monkeypatch)
    assert not failures
    stock = received[0]["session"].request.stock
    pool = received[0]["pool"]
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path / "history")
    assert flow._persist_history(
        stock, 5, "M5", 5900, pool["candles"],
        market_data_identity="005930", market_source="KRX",
    )
    entry = flow._cached_history(stock, 5, "M5", 5900)
    assert entry is not None and entry.market_source == "KRX"
    marker = flow._validation_cache_source_marker_path(stock, 5, "M5", 5900)
    assert marker is not None
    metadata = json.loads(marker.read_text(encoding="utf-8"))
    metadata["market_source"] = "INTEGRATED"
    marker.write_text(json.dumps(metadata), encoding="utf-8")
    assert flow._cached_history(stock, 5, "M5", 5900) is None


def test_frozen_krx_period_uses_native_read_only_request(tmp_path, monkeypatch):
    now = datetime.now(SEOUL_TIMEZONE)
    initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=now.date(),
        decided_at=now, nxt_available=False,
    )
    monkeypatch.setattr(v5, "PROJECT_ROOT", tmp_path)
    calls = []

    def request_period(code, name, **kwargs):
        calls.append((code, name, kwargs["timeframe_key"], kwargs["count"]))
        kwargs["callback"]({
            "ok": True, "type": "period_candles", "code": code,
            "timeframe_key": "D1", "request_id": "krx-d1", "rqname": "krx-d1",
            "market_data_identity": "005930", "market_source": "KRX",
            "trcode": "opt10081", "rows": [], "rows_count": 0,
        })
        return {"ok": True, "rqname": "krx-d1", "market_data_identity": "005930",
                "market_source": "KRX"}

    requester = v5._V5SelectedSourceRequester(
        SimpleNamespace(request_period_candles_read_only=request_period),
        v5.ValidationStockRef("005930", "Samsung"), "D1", False,
    )
    responses = []
    requester.request_period_candles_read_only(
        "005930", "Samsung", timeframe_key="D1", count=200,
        callback=responses.append,
    )
    assert calls == [("005930", "Samsung", "D1", 200)]
    assert responses and responses[0]["ok"] is True


@pytest.mark.parametrize("source,identity", [
    ("INTEGRATED", "005930_AL"), ("KRX", "005930_AL"),
    ("INTEGRATED", "005930"), (None, None),
])
def test_frozen_krx_only_rejects_mixed_or_missing_provenance(
    tmp_path, monkeypatch, source, identity,
):
    _flow, calls, received, failures = _run_krx(
        tmp_path, monkeypatch, response_source=source, response_identity=identity,
    )
    assert len(calls) == 1
    assert not received
    assert failures


@pytest.mark.parametrize("source,identity", [("INTEGRATED", "005930_AL"), (None, None)])
def test_frozen_krx_only_rejects_mislabeled_request_receipt(
    tmp_path, monkeypatch, source, identity,
):
    _flow, calls, received, failures = _run_krx(
        tmp_path, monkeypatch, start_source=source, start_identity=identity,
    )
    assert len(calls) == 1
    assert not received
    assert failures
