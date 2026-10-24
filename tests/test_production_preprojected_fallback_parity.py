"""Retired KRX fallback inputs stay blocked by the production SOR boundary."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import routine_signal_probe
from candle_timeframe_aggregation import SEOUL_TIMEZONE, normalize_candle_sessions
from gui_market_data_host import MarketDataHost
from routine_main_facts import build_routine_main_facts_from_projection


CODE = "005930"
NOW = datetime(2026, 10, 7, 9, 15, tzinfo=SEOUL_TIMEZONE)
SESSIONS = ({"name": "regular", "start_time": "09:00:00", "end_time": "15:30:00"},)
REQUEST = {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 2, "history_target_bars": 3}


def _minutes():
    start = NOW - timedelta(minutes=15)
    return [
        {
            "time": (start + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 1,
        }
        for index in range(15)
    ]


def _host_supply(raw, rules, *, sessions=SESSIONS, now=NOW, direct=(), request=REQUEST):
    host = MarketDataHost.__new__(MarketDataHost)
    host.kiwoom_api = SimpleNamespace(current_realtime_shadow_bar=lambda _code: None)
    host._direct_timeframe_candles = {(CODE, 5): tuple(direct)}
    with patch("gui_market_data_host.load_candles", return_value=raw), patch(
        "gui_market_data_host.StockRepository"
    ) as repository:
        repository.return_value.resolve_stock_dir.return_value = Path("unused")
        supplied = host.project_routine_candles(
            stock_code=CODE,
            rules=rules,
            projection_request=request,
            as_of=now,
            session_windows=sessions,
            consumer_scope="PRODUCTION",
        )
        return {**supplied, "session_windows_identity": normalize_candle_sessions(sessions)}


def _probe(tmp_path, raw, rules, *, preprojected=None, sessions=SESSIONS, now=NOW, request=REQUEST):
    facts = build_routine_main_facts_from_projection(
        {
            "stock_configs": {CODE: {"assigned_routine_instance_id": "isolated"}},
            "stock_states": {CODE: {"trade_enabled": True, "status": "MONITORING"}},
            "orders": [],
            "fills": [],
            "positions": [],
            "signals": [],
            "market": {
                "raw_candles_by_code": {CODE: raw},
                "candle_supply_by_code": {CODE: preprojected} if preprojected else {},
                "candle_session_windows": sessions,
                "reference_prices_by_code": {},
            },
        }
    ).to_payload()
    observed = []

    def evaluate(context):
        observed.extend(context["candles"])
        raise AssertionError("unverified KRX candles reached signal evaluation")

    routine = SimpleNamespace(
        ROUTINE_TYPE="isolated",
        market_bar_projection_request=lambda _rules: request,
        project_cycle_context=lambda **_facts: {"status": "resolved"},
        evaluate=evaluate,
    )
    stock_dir = tmp_path / f"{CODE}_Test"
    with patch.object(routine_signal_probe, "_load_instance_rules", return_value=rules), patch.object(
        routine_signal_probe, "_append_log"
    ), patch.object(routine_signal_probe, "_maybe_enqueue_signal") as enqueue, patch.object(
        routine_signal_probe, "find_library_stock_by_code", return_value=None
    ), patch.object(routine_signal_probe, "read_reference_price", return_value=None), patch.object(
        routine_signal_probe, "observe_owner_failure_transition"
    ), patch("event_journal_trade_observer.observe_signal_created"), patch.object(
        routine_signal_probe, "project_candle_supply", wraps=routine_signal_probe.project_candle_supply
    ) as fallback:
        result = routine_signal_probe.probe_routine_for_stock(
            routine,
            "isolated",
            stock_dir,
            now.isoformat(timespec="seconds"),
            decision_trace_observer=None,
            main_facts=facts,
        )
    assert result["signal"] == "SKIP", result
    assert result["reason"] == "SOR_CALCULATION_SOURCE_UNAVAILABLE"
    assert result["execution_enabled"] is False
    assert result["queue_connected"] is False
    assert result["send_order_connected"] is False
    assert observed == []
    enqueue.assert_not_called()
    fallback.assert_not_called()
    return result


def test_m1_same_source_and_clock_cannot_authorize_krx_host_or_fallback(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 1}}
    supplied = _host_supply(raw, rules)
    assert supplied["available"] is False
    assert supplied["availability_state"] == "SOR_SOURCE_UNAVAILABLE"
    assert supplied["candles"] == []
    assert _probe(tmp_path, raw, rules, preprojected=supplied) == _probe(
        tmp_path, raw, rules
    )


def _direct_m5(*, days_ago=0):
    source_day = NOW - timedelta(days=days_ago)
    return [
        {
            "bar_time": (source_day - timedelta(minutes=15 - 5 * index)).isoformat(),
            "open": 300,
            "high": 301,
            "low": 299,
            "close": 300,
            "volume": 5,
            "timeframe_minutes": 5,
            "is_complete": True,
            "trade_date": source_day.date().isoformat(),
            "session": "regular",
        }
        for index in range(3)
    ]


def test_m5_direct_krx_history_does_not_become_production_supply():
    supplied = _host_supply(_minutes(), {"bar": {"bar_minutes": 5}}, direct=_direct_m5())
    assert supplied["available"] is False
    assert supplied["availability_state"] == "SOR_SOURCE_UNAVAILABLE"
    assert supplied["direct_timeframe_source"] is True
    assert supplied["required_minute_candles"] == 6
    assert supplied["candles"] == []


def test_m5_krx_source_contract_difference_does_not_trigger_fallback(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 5}}
    supplied = _host_supply(raw, rules, direct=_direct_m5())
    assert _probe(tmp_path, raw, rules, preprojected=supplied) == _probe(
        tmp_path, raw, rules
    )


def test_m5_previous_krx_history_and_today_m1_cannot_form_sor_supply(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 5}}
    # No warmup isolates source selection from the unrelated 600-versus-11 gate.
    request = {"projection": "COMPLETED_TIMEFRAME", "history_target_bars": 6}
    supplied = _host_supply(raw, rules, direct=_direct_m5(days_ago=1), request=request)
    assert supplied["available"] is False
    assert supplied["candles"] == []
    assert _probe(tmp_path, raw, rules, preprojected=supplied, request=request) == (
        _probe(tmp_path, raw, rules, request=request)
    )


def test_m1_changed_krx_source_stays_blocked_under_same_projection_key(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 1}}
    supplied = _host_supply(raw, rules)
    changed = _minutes()
    changed[-1]["close"] = 300
    assert _probe(tmp_path, changed, rules, preprojected=supplied) == _probe(
        tmp_path, changed, rules
    )


def test_m1_changed_krx_session_stays_blocked_under_same_projection_key(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 1}}
    supplied = _host_supply(raw, rules)
    alternate = ({"name": "alternate", "start_time": "09:00:00", "end_time": "15:30:00"},)
    assert _probe(tmp_path, raw, rules, preprojected=supplied, sessions=alternate) == (
        _probe(tmp_path, raw, rules, sessions=alternate)
    )


def test_m1_older_krx_as_of_stays_blocked_under_same_projection_key(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 1}}
    supplied = _host_supply(raw, rules, now=NOW - timedelta(seconds=30))
    assert _probe(tmp_path, raw, rules, preprojected=supplied) == _probe(
        tmp_path, raw, rules
    )


def test_m1_changed_krx_history_target_stays_blocked_under_same_key(tmp_path):
    raw = _minutes()
    rules = {"bar": {"bar_minutes": 1}}
    supplied = _host_supply(raw, rules)
    wider_request = {**REQUEST, "history_target_bars": 4}
    assert _probe(tmp_path, raw, rules, preprojected=supplied, request=wider_request) == (
        _probe(tmp_path, raw, rules, request=wider_request)
    )
