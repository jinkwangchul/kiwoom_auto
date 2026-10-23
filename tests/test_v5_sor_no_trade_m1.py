"""Offline SOR _AL no-trade minute evidence and V5 calculation handoff."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from tests import test_display_only_integrated_history as host_tests
from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
    _registered_m1_calculation_case,
)


AS_OF = datetime(2026, 10, 23, 8, 3, tzinfo=SEOUL_TIMEZONE)


def broker_row(stamp, open_, high, low, close, volume):
    return dict(zip(host_tests._opt10080_row(stamp, open_).keys(),
                    (stamp, str(open_), str(high), str(low), str(close), str(volume))))


def hyundai_host(m3=None, *, m3_code="005380"):
    host = host_tests.DisplayOnlyMarketDataHostTest()._host()
    host._v5_observation_leases = {}
    host._candle_execution_required_by_stock = {"005380": 3}
    host._candle_execution_timeframe_requirements_by_stock = {"005380": {1: 3}}
    native = [broker_row("20261023080000", 295000, 298000, 294000, 294000, 19),
              broker_row("20261023080200", 292000, 293000, 291000, 291500, 5790)]
    with patch.object(MarketDataHost, "_nxt_eligible_targets",
                      side_effect=lambda codes: tuple(codes)):
        installed = MarketDataHost.install_display_timeframe_candles(
            host, "005380", 1, native, as_of=AS_OF,
            market_data_identity="005380_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1")
        assert installed["ok"] is True
        if m3 is not None:
            installed = MarketDataHost.install_display_timeframe_candles(
                host, m3_code, 3, [m3], as_of=AS_OF,
                market_data_identity=f"{m3_code}_AL", market_source="INTEGRATED",
                display_only=True, connection_epoch=1, login_session_id="SESSION-1")
            assert installed["ok"] is True
    return host


MATCHING_M3 = broker_row("20261023080000", 295000, 298000,
                         291000, 291500, 5809)


def project(host, *, as_of=AS_OF):
    return MarketDataHost.project_broker_sor_m1_calculation(
        host, stock_code="005380", rules={"bar": {"bar_minutes": 1}},
        projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=as_of)


def test_hyundai_independent_m3_proves_0801_zero_volume_without_mutating_native():
    host = hyundai_host(MATCHING_M3)
    native = deepcopy(host._display_timeframe_candles[("005380", 1)])
    result = project(host)
    assert result["available"] is True, result
    today = [row for row in result["candles"]
             if row["bar_time"].startswith("2026-10-23")]
    assert [row["bar_time"][11:16] for row in today] == ["08:00", "08:01", "08:02"]
    assert (today[1]["open"], today[1]["high"], today[1]["low"],
            today[1]["close"], today[1]["volume"]) == (294000, 294000, 294000, 294000, 0)
    assert host._display_timeframe_candles[("005380", 1)] == native


@pytest.mark.parametrize("m3,code", [
    (None, "005380"),
    (broker_row("20261023080000", 295000, 298000, 291000, 291500, 5810), "005380"),
    (broker_row("20261023080000", 295000, 298000, 290000, 291500, 5809), "005380"),
    (MATCHING_M3, "005930"),
    (broker_row("20261022080000", 295000, 298000, 291000, 291500, 5809), "005380"),
])
def test_unverified_m3_stays_blocked(m3, code):
    host = hyundai_host(m3, m3_code=code)
    assert project(host)["availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"


def test_m3_from_old_login_session_stays_blocked():
    host = hyundai_host(MATCHING_M3)
    host._display_timeframe_session_identity[("005380", 3)] = (1, "OLD-SESSION")
    assert project(host)["availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"


def test_m3_does_not_authorize_two_missing_minutes():
    host = hyundai_host(MATCHING_M3)
    native = list(host._display_timeframe_candles[("005380", 1)])
    native[1] = dict(native[1], bar_time="2026-10-23T08:03:00+09:00")
    host._display_timeframe_candles[("005380", 1)] = tuple(native)
    assert project(host, as_of=AS_OF + timedelta(minutes=1))[
        "availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"


def test_latest_completed_no_trade_minute_requires_same_m3_proof():
    host = hyundai_host(MATCHING_M3)
    native = list(host._display_timeframe_candles[("005380", 1)])
    native[1] = dict(native[1], bar_time="2026-10-23T08:01:00+09:00")
    host._display_timeframe_candles[("005380", 1)] = tuple(native)
    result = project(host)
    assert result["available"] is True, result
    assert [row["bar_time"][11:16] for row in result["candles"]] == [
        "08:00", "08:01", "08:02"]
    assert result["candles"][-1]["volume"] == 0
    host._display_timeframe_session_identity[("005380", 3)] = (1, "STALE")
    assert project(host)["availability_state"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"


def test_v5_chart_and_calculation_use_verified_flat_minute():
    with patch.object(MarketDataHost, "_v5_al_consider_parity"):
        flow, session, pool, market, _ = _registered_m1_calculation_case()
    native = list(market._display_timeframe_candles[("005930", 1)])
    missing = native[2]
    market._display_timeframe_candles[("005930", 1)] = tuple(native[:2] + native[3:])
    market._display_timeframe_candles[("005930", 3)] = ({
        **native[0], "timeframe_minutes": 3,
        "open": native[0]["open"], "high": max(row["high"] for row in native[:2]),
        "low": min(row["low"] for row in native[:2]),
        "close": native[1]["close"],
        "volume": sum(row["volume"] for row in native[:2]),
    },)
    market._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    pool["candles"] = [row for row in pool["candles"]
                       if row["time"] != "20261020090200"]
    original = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    flat = next(row for row in view["candles"] if row["time"] == "20261020090200")
    assert flat == {"time": "20261020090200", "open": native[1]["close"],
                    "high": native[1]["close"], "low": native[1]["close"],
                    "close": native[1]["close"], "volume": 0}
    assert flat in view["display_candles"]
    assert flat in flow_module.project_validation_candles(view["snapshot"])[0]
    assert pool == original
    assert market._display_timeframe_candles[("005930", 1)] == tuple(native[:2] + native[3:])

    window = SimpleNamespace(
        _normal_base_ready=True, _normal_base_requested=True,
        _replay_snapshot=None, validation_range=None,
        marking_display_check=SimpleNamespace(isChecked=lambda: False),
        is_chart_interaction_busy=lambda: False,
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._sor_m1_observed_host = market
    market._current_display_session_identity = lambda: (1, "SESSION-1")
    flow._window_accepts_results = lambda _window: True
    flow._normal_base_history_view = lambda *_args: (pool, 0)
    dynamic = []
    flow._use_pool_for_window = lambda *_args, **kwargs: dynamic.append(kwargs)
    flow._handle_sor_m1_snapshot_accepted({
        "source": "SOR_AL_M3_GAP_PROOF_ACCEPTED",
        "stock_code": "005930", "market_data_identity": "005930_AL",
        "connection_epoch": 1, "login_session_id": "SESSION-1",
        "missing_minute": missing["bar_time"],
        "snapshot_received_at": flow._now_factory().isoformat(),
    })
    assert len(dynamic) == 1
    assert dynamic[0]["live_m1_snapshot_key"] is None


@pytest.mark.parametrize("tail", [False, True])
def test_v5_watch_requests_m3_once_for_gap_then_announces_verified_recalculation(tail):
    host = hyundai_host()
    if tail:
        native = list(host._display_timeframe_candles[("005380", 1)])
        native[1] = dict(native[1], bar_time="2026-10-23T08:01:00+09:00")
        host._display_timeframe_candles[("005380", 1)] = tuple(native)
    proof = hyundai_host(MATCHING_M3)
    host._v5_watch_generation = 1
    host._v5_watch_inflight = False
    host._v5_observation_leases = {1: "005380"}
    host._shutting_down = False
    host._current_display_session_identity = lambda: (1, "SESSION-1")
    host._sor_forming_m1_by_stock = {"005380": {
        "session_identity": (1, "SESSION-1"), "candle": {
            "bar_time": AS_OF.isoformat(),
            "snapshot_received_at": AS_OF.isoformat(),
            "snapshot_source": "BROKER_AL_TR_FORMING_M1",
        }}}
    host.kiwoom_api.sync_v5_al_realtime_targets = lambda codes: None
    events = []
    host.market_data_observed = SimpleNamespace(emit=events.append)
    calls = []

    def request(**kwargs):
        interval = kwargs["timeframe_minutes"]
        calls.append(interval)
        if interval == 3:
            host._display_timeframe_candles[("005380", 3)] = (
                proof._display_timeframe_candles[("005380", 3)])
            host._display_timeframe_session_identity[("005380", 3)] = (1, "SESSION-1")
        kwargs["on_terminal"]({"ok": True})
        return {"ok": True}

    with patch.object(MarketDataHost, "_nxt_eligible_targets",
                      side_effect=lambda codes: tuple(codes)), patch.object(
        MarketDataHost, "display_timeframe_refresh_specs",
        return_value=({"timeframe_minutes": 1, "nxt_available": True,
                       "request_count": 3},)), patch(
        "auto_candle_refresh.request_display_timeframe_candles_for_stock",
        side_effect=request,
    ):
        MarketDataHost._refresh_v5_observation(host, now=AS_OF)
        MarketDataHost._refresh_v5_observation(host, now=AS_OF)
    assert calls == [1, 3, 1]
    assert [event["source"] for event in events] == ["SOR_AL_M3_GAP_PROOF_ACCEPTED"]
    assert project(host)["available"] is True
