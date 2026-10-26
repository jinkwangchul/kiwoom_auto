"""Isolated V5 handoff regressions; never import the original V5 file."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from tests.test_phase5_nxt_native import _forming_row, _run_nxt
from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest


def _crossing_receipt(tmp_path, monkeypatch, interval):
    requested = datetime.now(SEOUL_TIMEZONE).replace(
        hour=8, minute=1, second=57, microsecond=874777)
    received = requested.replace(minute=2, second=6, microsecond=867711)
    return _run_nxt(
        tmp_path, monkeypatch, interval=interval,
        request_clock=lambda _now: requested,
        callback_clock=lambda _now: received,
        change_response={"received_at": received.isoformat()},
        extra_candles=lambda now: [_forming_row(now, interval)],
    )


def test_cross_minute_m3_receipt_remains_forming_until_0803(tmp_path, monkeypatch):
    assert "recovery" in str(v5.__file__)
    flow, _calls, prepared, failures, clock, _session = _crossing_receipt(
        tmp_path, monkeypatch, 3)
    assert not failures and prepared
    session, pool = prepared[0]["session"], prepared[0]["pool"]
    view = flow._validation_pool_for_session(session, pool, target_count=11)
    assert view["native_forming_included"] is True
    assert view["candles"][-1]["time"].endswith("080000")
    flow._require_selected_timeframe_pool(session, view)
    clock[0] = clock[0].replace(minute=3)
    expired = flow._validation_pool_for_session(session, pool, target_count=11)
    assert expired["native_forming_included"] is False


def test_long_native_bar_does_not_keep_old_receipt_fresh(tmp_path, monkeypatch):
    flow, _calls, prepared, failures, clock, _session = _crossing_receipt(
        tmp_path, monkeypatch, 240)
    assert not failures and prepared
    session, pool = prepared[0]["session"], prepared[0]["pool"]
    assert flow._validation_pool_for_session(session, pool)["native_forming_included"] is True
    clock[0] += timedelta(seconds=61)
    view = flow._validation_pool_for_session(session, pool)
    assert view["native_forming_included"] is False


def test_reconnect_invalidates_cross_minute_receipt(tmp_path, monkeypatch):
    flow, _calls, prepared, failures, _clock, broker_session = _crossing_receipt(
        tmp_path, monkeypatch, 3)
    assert not failures and prepared
    session, pool = prepared[0]["session"], prepared[0]["pool"]
    broker_session[0] = type(broker_session[0])(
        connected=True, connection_epoch=8, login_session_id="new")
    with pytest.raises(ValueError):
        flow._validation_pool_for_session(session, pool)


@pytest.mark.parametrize("rules,expected", [
    ({"bar": {"bar_minutes": 3}}, 3),
    ({"bar": {"bar_minutes": 5}, "validation_timeframe": {"key": "D1"}}, 0),
])
def test_v5_lease_declares_selected_timeframe_without_offsession_m1(rules, expected):
    stock = v5.ValidationStockRef("005930", "Samsung")
    window = SimpleNamespace(stock=stock)
    calls = []
    market = SimpleNamespace(set_v5_observation_lease=lambda *a, **k:
                             calls.append((a, k)) or {"ok": True})
    owner = SimpleNamespace(existing_market_data_host=lambda: market)
    run = IndicatorFollowSignalValidationRunRequest(
        v5.ValidationSettingsSnapshot(rules), 5)
    flow = SimpleNamespace(
        parent=lambda: owner, _open_windows={id(window): window},
        _last_run_requests={id(window): run}, _v5_lease_hosts={},
        _note_sor_m1_skip=lambda *a: None,
        _bind_sor_m1_observations=lambda: None,
        _request_offsession_sor_m1_once=lambda *a: pytest.fail("prior-day M1 prefetch"),
    )
    v5.IndicatorFollowSignalValidationV5Flow._sync_v5_observation_lease(flow, window)
    assert calls == [((id(window), "005930"), {"timeframe_minutes": expected})]
