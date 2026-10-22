"""Live 300-bar history must not inherit fixed SOR minute-cache floors."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from unittest.mock import patch

from gui_market_data_host import MarketDataHost


CODE = "005930"


def _host():
    return SimpleNamespace(
        kiwoom_api=SimpleNamespace(
            broker_session_snapshot=lambda: SimpleNamespace(
                connected=True,
                connection_epoch=7,
                login_session_id="SESSION-7",
            )
        ),
        _candle_execution_required_by_stock={},
        _candle_observation_required_by_stock={},
        _candle_standby_required_by_stock={},
        _candle_standby_stock_codes=(),
        _candle_standby_timeframe_requirements_by_stock={},
        _candle_execution_timeframe_requirements_by_stock={},
        _candle_observation_timeframe_requirements_by_stock={},
        _display_timeframe_candles={},
        _display_timeframe_session_identity={},
        _sor_forming_m1_by_stock={},
    )


def _live_requirement():
    return {
        "stock_code": CODE,
        "calculation_market_source": "SOR",
        "rules": {"bar": {"bar_minutes": 5}},
        "projection_request": {
            "projection": "FORMING_BASE_BAR",
            "warmup_bars": 15,
            "history_target_bars": 300,
        },
    }


def test_live_m5_300_direct_bars_need_only_forming_m1_window():
    interval, direct, m1 = MarketDataHost._direct_timeframe_requirement(
        _live_requirement()["rules"],
        _live_requirement()["projection_request"],
    )
    assert (interval, direct, m1) == (5, 300, 6)


def test_live_sor_m5_standby_does_not_request_full_day_m1():
    host = _host()
    result = MarketDataHost.sync_candle_standby_requirements(
        host, [_live_requirement()]
    )
    assert result["ok"] is True
    assert host._candle_standby_required_by_stock == {CODE: 6}
    assert host._sor_standby_m1_required_by_stock == {CODE: 6}
    assert MarketDataHost.direct_timeframe_requirements(host, CODE) == {
        1: 6, 5: 300,
    }


def test_live_sor_m1_refresh_uses_its_declared_window():
    host = _host()
    MarketDataHost.sync_candle_standby_requirements(host, [_live_requirement()])
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        specs = MarketDataHost.display_timeframe_refresh_specs(
            host, CODE, "2026-10-20 09:05"
        )
    by_interval = {row["timeframe_minutes"]: row for row in specs}
    assert by_interval[1]["required_count"] == 6
    assert by_interval[1]["request_count"] == 7
    assert by_interval[5]["required_count"] == 300


def _store_nine_sor_m1(host):
    rows = [
        {
            "체결시간": f"2026102009{minute:02d}00",
            "시가": "10000",
            "고가": "10002",
            "저가": "9998",
            "현재가": "10001",
            "거래량": "10",
        }
        for minute in range(9)
    ]
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        return MarketDataHost.install_display_timeframe_candles(
            host,
            CODE,
            1,
            rows,
            as_of=datetime(2026, 10, 20, 9, 15, tzinfo=SEOUL_TIMEZONE),
            market_data_identity=f"{CODE}_AL",
            market_source="INTEGRATED",
            display_only=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
        )


def test_live_sor_minute_cache_drops_old_bars_at_six():
    host = _host()
    MarketDataHost.sync_candle_standby_requirements(host, [_live_requirement()])
    receipt = _store_nine_sor_m1(host)
    assert receipt["ok"] is True
    assert receipt["stored_count"] == 6
    assert len(host._display_timeframe_candles[(CODE, 1)]) == 6


def test_v5_observer_can_keep_its_full_day_minute_window():
    host = _host()
    MarketDataHost.sync_candle_standby_requirements(host, [_live_requirement()])
    host._sor_m1_required_by_stock[CODE] = 700
    receipt = _store_nine_sor_m1(host)
    assert receipt["ok"] is True
    assert receipt["stored_count"] == 9


def test_unregistered_symbol_does_not_fall_back_to_600_minutes():
    host = _host()
    assert MarketDataHost.candle_history_required_count(host, CODE) == 0
