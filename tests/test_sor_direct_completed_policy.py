"""Completed higher-timeframe bars must come from direct SOR _AL receipts."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE, aggregate_minute_candles, project_candle_supply,
)
from indicator_follow_validation_production_session import (
    PRODUCTION_SESSION_READY, project_production_calculation_candles,
)
from tests.test_sor_core_m1_input import NOW, _installed_host, _project


def test_past_m1_cannot_be_projected_as_completed_m3():
    rows = [
        {"time": f"20261020090{i}00", "open": 100, "high": 101,
         "low": 99, "close": 100, "volume": 1}
        for i in range(3)
    ]
    assert aggregate_minute_candles(rows, 3, now=NOW) == []
    supply = project_candle_supply(
        rows, {"bar": {"bar_minutes": 3}},
        {"projection": "COMPLETED_TIMEFRAME"}, now=NOW,
    )
    assert supply["available"] is False
    assert supply["candles"] == []


def test_production_validation_rejects_m1_to_completed_m3():
    contract = {"status": PRODUCTION_SESSION_READY, "ready": True,
                "session_windows": [{"name": "regular", "start_time": "09:00",
                                     "end_time": "15:30"}]}
    result = project_production_calculation_candles(
        [{"time": "20261020090000", "open": 100, "high": 101,
          "low": 99, "close": 100, "volume": 1}], 3, contract, as_of=NOW,
    )
    assert result["ready"] is False
    assert result["candles"] == []


def test_host_blocks_completed_m3_without_direct_sor_bars():
    host = _installed_host()
    result = _project(host, 3, as_of=datetime(2026, 10, 20, 9, 5,
                                               tzinfo=SEOUL_TIMEZONE))
    assert result["available"] is False
    assert result["candles"] == []
    assert result["availability_state"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"


def test_host_uses_direct_m3_even_when_m1_is_unavailable():
    host = _installed_host()
    direct = dict(host._display_timeframe_candles[("005930", 1)][0])
    direct.update(timeframe_minutes=3, close=777, high=777)
    host._display_timeframe_candles[("005930", 3)] = (direct,)
    host._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    host._display_timeframe_candles.pop(("005930", 1))
    result = _project(host, 3)
    assert result["available"] is True
    assert len(result["candles"]) == 1
    assert result["candles"][0]["close"] == 777
    assert result["direct_history_source"] == "BROKER_AL_CONFIRMED_DIRECT"
    early = _project(host, 3, as_of=datetime(2026, 10, 20, 9, 2,
                                             tzinfo=SEOUL_TIMEZONE))
    assert early["available"] is False
    assert early["availability_state"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"


def test_v5_keeps_direct_completed_m5_and_adds_only_current_forming_bar():
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _complete_morning_forming_host, _registered_m1_calculation_case,
    )

    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market, as_of = _complete_morning_forming_host(minute=11)
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key="M5",
        timeframe_minutes=5,
        settings_snapshot=SimpleNamespace(
            to_dict=lambda: {"bar": {"bar_minutes": 5}}),
    ))
    direct = [
        {"time": f"20261020{clock}00", "open": 10, "high": 11,
         "low": 9, "close": 10, "volume": 1}
        for clock in ("0845", "0900", "0905")
    ]
    pool = {"stock": session.request.stock, "candles": direct,
            "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "timeframe_key": "M5", "timeframe_minutes": 5,
            "requested_count": len(direct), "request_id": "direct-al"}
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    view = flow._sor_m1_calculation_view(
        session, pool, dict(pool, display_candles=list(direct)), frozen,
    )
    assert view["candles"][:3] == direct
    assert [row["time"] for row in view["candles"]] == [
        "20261020084500", "20261020090000", "20261020090500",
        "20261020091000",
    ]
    assert view["calculation_source"] == "SELECTED_DIRECT_AL_WITH_LIVE_SOR_FORMING"
