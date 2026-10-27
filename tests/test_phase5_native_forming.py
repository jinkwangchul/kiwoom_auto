"""Offline native-source receipts; no broker connection or execution entrypoint."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from kiwoom_api import KiwoomApi
from mock_validation_candle_provider import project_mock_routine_candles
from stock_market_source_plan import initialize_stock_market_source_plan, read_stock_market_source_plan
from stock_instance_day_projection import chart_candle_projection_request, _verified_krx_chart_supply
from tests.test_phase4_krx_calculation import _run

CODE = "005930"
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=SEOUL_TIMEZONE)
FORMING = {"projection": "FORMING_BASE_BAR", "warmup_bars": 2, "history_target_bars": 3}


def native_supply(root, *, now=NOW, source="KRX", requested=None, received=None,
                  projection=FORMING, response_change=None, stamps=None, interval=5):
    initialize_stock_market_source_plan(root, CODE, "Samsung", trading_day=now.date(),
                                       decided_at=now, nxt_available=source == "NXT")
    requested = requested or now
    received = received or requested + timedelta(seconds=1)
    previous = (now - timedelta(days=1)).strftime("%Y%m%d")
    clock = "08" if source == "NXT" else "09"
    stamps = stamps or [previous + clock + "4000", previous + clock + "4500",
                        now.strftime("%Y%m%d%H%M00")]
    rows = [dict(zip(KiwoomApi.OPT10080_FIELDS, (stamp, "100", "101", "99", "100", "10")))
            for stamp in stamps]
    identity = CODE + "_NX" if source == "NXT" else CODE
    calls = []
    def request(code, *, interval, count, callback, **kwargs):
        calls.append((code, interval, count, kwargs))
        result = {"ok": True, "code": code, "interval": interval,
                  "market_data_identity": identity, "market_source": source,
                  "display_only": True, "rows": rows, "rows_count": len(rows),
                  "type": "minute_candles", "trcode": "opt10080",
                  "request_id": f"opt10080_{CODE}_native", "rqname": f"opt10080_{CODE}_native",
                  "connection_epoch": 1, "login_session_id": "session-1",
                  "request_as_of": requested.isoformat(), "received_at": received.isoformat()}
        result.update(response_change or {})
        callback(result)
        return {"ok": True}
    api = SimpleNamespace(**{("request_minute_candles_display_only_nxt" if source == "NXT"
                              else "request_minute_candles_display_only_krx"): request})
    supply = project_mock_routine_candles(
        project_root=root, api=api, stock_code=CODE, rules={"bar": {"bar_minutes": interval}},
        projection_request=projection, as_of=now,
        consumer_scope=f"{source}_CALCULATION_ONLY",
        identity_reader=lambda _: ("test", 1, "session-1", "native"))
    return supply, calls


def test_verified_krx_current_native_m5_reaches_preview_and_chart(tmp_path):
    supply, calls = native_supply(tmp_path)
    assert supply["available"] is True, supply
    result, selected = _run(tmp_path, supply, request=FORMING)
    assert result["reason"] == "KRX_CALCULATION_ONLY_NO_ORDER", result
    assert [row["is_complete"] for row in selected] == [True, True, False]
    assert _verified_krx_chart_supply(supply, CODE, 5,
                                     plan=read_stock_market_source_plan(tmp_path, CODE, NOW.date()), as_of=NOW)
    assert calls[0][1] == 5  # directly requested M5, never M1 rebuild


def test_quickchart_keeps_native_forming_projection_request():
    assert chart_candle_projection_request(FORMING, krx_native=True)["projection"] == "FORMING_BASE_BAR"


def test_delayed_request_time_forming_bar_never_becomes_confirmed(tmp_path):
    supply, _ = native_supply(
        tmp_path, now=NOW.replace(minute=5, second=1),
        requested=NOW.replace(minute=4, second=59), received=NOW.replace(minute=5, second=1),
        projection={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 2},
        stamps=["20261024094000", "20261024094500", "20261025100000"])
    assert all(row["bar_time"] != NOW.isoformat() for row in supply.get("candles", [])), supply


@pytest.mark.parametrize("change", [
    {"request_as_of": "2026-10-24T10:00:00+09:00"},
    {"connection_epoch": 2}, {"login_session_id": "old-session"},
    {"market_data_identity": "005930_AL"}, {"market_source": "INTEGRATED"},
    {"request_as_of": "2026-10-25T10:00:00"},
    {"received_at": "2026-10-25T10:05:00+09:00"},
])
def test_untrusted_native_forming_receipt_is_unavailable(tmp_path, change):
    supply, _ = native_supply(tmp_path, response_change=change)
    assert supply["available"] is False


def test_nxt_premarket_uses_direct_nx_history_and_forming(tmp_path):
    supply, calls = native_supply(tmp_path, now=NOW.replace(hour=8, minute=45), source="NXT")
    assert supply["available"] is True, supply
    assert calls == [(CODE, 5, 5, {"nxt_available": True})]
    assert supply["market_data_identity"] == "005930_NX"
    assert all(row["market_source"] == "NXT" for row in supply["candles"])
    assert [row["is_complete"] for row in supply["candles"]] == [True, True, False]


@pytest.mark.parametrize("hour,minute", [(8, 50), (8, 55), (9, 0)])
def test_nxt_adapter_not_used_after_premarket(tmp_path, hour, minute):
    supply, calls = native_supply(tmp_path, now=NOW.replace(hour=hour, minute=minute), source="NXT")
    assert supply["available"] is False
    assert calls == []


def test_krx_standby_does_not_enter_sor_m1_collection():
    from tests.test_sor_core_auto_m1_refresh import _host, _requirement
    from gui_market_data_host import MarketDataHost
    host = _host()
    result = MarketDataHost.sync_candle_standby_requirements(host, [_requirement(sor=False)])
    assert result["ok"] is True, result
    assert CODE in result["standby_stock_codes"]
    assert host._sor_m1_required_by_stock == {}
    assert host._candle_standby_timeframe_requirements_by_stock == {CODE: {5: 900}}


def test_native_quick_history_does_not_hide_krx_forming_overlay(tmp_path):
    from quick_chart_native_history import apply_native_history_projection, validate_native_history_receipt
    from stock_instance_day_projection import project_operation_timeline
    from tests.test_quick_chart_native_history import _receipt, _raw
    supply, _ = native_supply(tmp_path)
    started, response = _receipt([_raw("2026-10-24", "094000", 95)])
    started.update(market_source="KRX", market_data_identity=CODE)
    response.update(market_source="KRX", market_data_identity=CODE, interval=5)
    start = "2026-10-24T09:00:00+09:00"
    history = validate_native_history_receipt(started, response, (7, "session-7"),
        (7, "session-7"), CODE, 5, start, None, nxt_available=False)
    base = {"stock_code": CODE, "bar_minutes": 5, "nxt_available": False,
            "projection_status": "VALID", "chart_candle_source": "CALCULATION_KRX_NATIVE",
            "candles": supply["candles"], "operation_timeline": project_operation_timeline(
                start, None, now=NOW, source_dates=set(), active=True)}
    shown = apply_native_history_projection(base, history, now=NOW)
    assert shown["candles"][-1]["is_complete"] is False
    assert shown["candles"][-1]["bar_time"] == NOW.isoformat()


def test_historical_quick_chart_still_requests_selected_native_history(tmp_path):
    from gui_stock_instance_chart_window import StockInstanceChartWindow
    previous = NOW - timedelta(days=1)
    plan = initialize_stock_market_source_plan(tmp_path, CODE, "Samsung",
        trading_day=previous.date(), decided_at=previous, nxt_available=False)
    calls = []
    class Window:
        _chart_closed = False
        stock_code = CODE
        _native_history_attempt = None
        _native_history_api = SimpleNamespace(request_minute_candles_display_only_krx=
            lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})
    data = {"stock_code": CODE, "instance_id": "one", "bar_minutes": 5,
            "source_plan": plan, "nxt_available": False,
            "operation_timeline": {"status": "ENDED", "start_at": previous.isoformat(),
                                   "end_at": previous.replace(hour=15).isoformat()}}
    with patch("gui_stock_instance_chart_window.datetime") as clock:
        clock.now.return_value = NOW
        StockInstanceChartWindow._maybe_request_native_history(Window(), data, (1, "session"))
    assert len(calls) == 1
    assert calls[0][1]["interval"] == 5


def test_native_calculation_rejects_corrupt_prices_even_with_matching_hash(tmp_path):
    from candle_manager import canonical_candle_content_hash
    supply, _ = native_supply(tmp_path)
    supply["candles"][-1]["low"] = 200
    digest = canonical_candle_content_hash(supply["candles"])
    supply["source_identity"] = supply["direct_source_receipt"]["source_content_sha256"] = digest
    assert not _verified_krx_chart_supply(supply, CODE, 5,
        plan=read_stock_market_source_plan(tmp_path, CODE, NOW.date()), as_of=NOW)


def test_native_preview_rejects_different_timeframe_than_routine_rules(tmp_path):
    from candle_manager import canonical_candle_content_hash
    supply, _ = native_supply(tmp_path)
    supply["timeframe_minutes"] = 3
    for row in supply["candles"]:
        row["timeframe_minutes"] = 3
    receipt = supply["direct_source_receipt"]
    receipt["timeframe_key"] = "M3"
    supply["source_identity"] = receipt["source_content_sha256"] = canonical_candle_content_hash(supply["candles"])
    result, selected = _run(tmp_path, supply, request=FORMING)
    assert selected == [], result


@pytest.mark.parametrize("interval", [30, 60, 240])
def test_krx_native_bar_spanning_1520_stays_forming(tmp_path, interval):
    now = NOW.replace(hour=15, minute=21)
    supply, _ = native_supply(tmp_path, now=now, interval=interval,
        projection={"projection": "FORMING_BASE_BAR", "warmup_bars": 1},
        stamps=["20261024110000", "20261025150000"])
    assert supply["available"] is True, supply
    assert supply["candles"][-1]["is_complete"] is False
    assert _verified_krx_chart_supply(supply, CODE, interval,
        plan=read_stock_market_source_plan(tmp_path, CODE, now.date()), as_of=now)


def test_krx_after_close_native_bar_spans_1600_without_early_completion(tmp_path):
    now = NOW.replace(hour=16, minute=1)
    supply, _ = native_supply(tmp_path, now=now, interval=30,
        projection={"projection": "FORMING_BASE_BAR", "warmup_bars": 1},
        stamps=["20261024150000", "20261025155000"])
    assert supply["available"] is True, supply
    assert supply["candles"][-1]["is_complete"] is False


def test_native_deadline_never_bridges_source_gaps(tmp_path):
    from stock_market_source_plan import native_bar_end
    krx = initialize_stock_market_source_plan(tmp_path, CODE, "Samsung",
        trading_day=NOW.date(), decided_at=NOW, nxt_available=False)
    assert native_bar_end(krx, NOW.replace(hour=15, minute=0), 240) == NOW.replace(
        hour=15, minute=30)
    nx = initialize_stock_market_source_plan(tmp_path, "000660", "SK",
        trading_day=NOW.date(), decided_at=NOW, nxt_available=True)
    assert native_bar_end(nx, NOW.replace(hour=8, minute=30), 60) == NOW.replace(hour=8, minute=50)


def test_quickchart_does_not_confirm_native_bar_at_1600_segment_boundary(tmp_path):
    from quick_chart_native_history import validate_native_history_receipt
    from tests.test_quick_chart_native_history import _receipt, _raw
    now = NOW.replace(hour=16, minute=1)
    plan = initialize_stock_market_source_plan(tmp_path, CODE, "Samsung",
        trading_day=NOW.date(), decided_at=NOW, nxt_available=False)
    started, response = _receipt([_raw("2026-10-25", "155000", 95)])
    started.update(market_source="KRX", market_data_identity=CODE, request_as_of=now.isoformat())
    response.update(market_source="KRX", market_data_identity=CODE, interval=30,
        request_as_of=now.isoformat(), received_at=(now + timedelta(seconds=1)).isoformat())
    history = validate_native_history_receipt(started, response, (7, "session-7"),
        (7, "session-7"), CODE, 30, "2026-10-25T09:00:00+09:00", None,
        nxt_available=False, source_plan=plan)
    assert history is not None
    assert history["candles"] == []


def test_nx_quickchart_reports_verified_calculation_source(tmp_path):
    import json
    import stock_instance_day_projection as chart
    now = NOW.replace(hour=8, minute=45)
    supply, _ = native_supply(tmp_path, now=now, source="NXT")
    stock_dir = tmp_path / "stocks" / f"{CODE}_Samsung"
    stock_dir.mkdir(parents=True)
    (stock_dir / "config.json").write_text(json.dumps({"assigned_routine_instance_id": "one"}), encoding="utf-8")
    (stock_dir / "state.json").write_text("{}", encoding="utf-8")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"bar":{"bar_minutes":5}}', encoding="utf-8")
    def provider(**kwargs):
        assert kwargs["consumer_scope"] == "NXT_CALCULATION_ONLY"
        return supply
    with patch.object(chart, "routine_instance_by_id", return_value=SimpleNamespace(
            rules_path=rules_path, definition_id="one", display_name="One")), patch.object(
            chart, "routine_definition_by_id", return_value=object()), patch.object(
            chart, "load_routine_callable", return_value=lambda _: FORMING):
        result = chart.project_stock_instance_day(CODE, now.date().isoformat(),
            project_root=tmp_path, now=now, candle_provider=provider)
    assert result["projection_status"] == "VALID", result["diagnostics"]
    assert result["chart_domain_label"] == "NXT _NX"
    assert result["calculation_candle_source"] == "NXT_NATIVE"
    assert result["candles"][-1]["is_complete"] is False
