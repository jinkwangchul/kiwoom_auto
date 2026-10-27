"""Broker-native quickchart history stays separate from operation candle caches."""

from datetime import datetime, timedelta, timezone
from collections import OrderedDict
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from candle_manager import canonical_candle_content_hash
from quick_chart_native_history import (
    apply_native_history_projection,
    merge_native_history_increment,
    native_history_completed_slot,
    native_history_request_count,
    native_history_source,
    validate_native_history_receipt,
    waiting_display_start,
)
from stock_instance_day_projection import project_operation_timeline
from gui_stock_instance_chart_window import StockInstanceChartWindow
from gui_market_data_host import MarketDataHost
from stock_market_source_plan import initialize_stock_market_source_plan


SEOUL = timezone(timedelta(hours=9))
START = "2026-09-11T10:00:00+09:00"
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=SEOUL)


def _raw(day, clock, close):
    return {"체결시간": f"{day.replace('-', '')}{clock}", "시가": str(close),
            "고가": str(close + 1), "저가": str(close - 1),
            "현재가": str(close), "거래량": "10"}


def _receipt(rows, *, has_more=False):
    started = {"ok": True, "rqname": "opt10080_005930_1",
               "code": "005930", "market_data_identity": "005930_AL",
               "market_source": "INTEGRATED", "connection_epoch": 7,
               "login_session_id": "session-7",
               "request_as_of": "2026-10-25T09:59:00+09:00"}
    response = {"ok": True, "type": "minute_candles", "display_only": True,
                "request_id": started["rqname"], "rqname": started["rqname"],
                "trcode": "opt10080", "code": "005930", "interval": 3,
                "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
                "connection_epoch": 7, "login_session_id": "session-7",
                "request_as_of": started["request_as_of"],
                "received_at": "2026-10-25T09:59:05+09:00",
                "rows": rows, "rows_count": len(rows), "has_more": has_more,
                "warning": "additional pages available" if has_more else ""}
    return started, response


def test_native_request_budget_is_bounded_by_session_day_capacity():
    count = native_history_request_count(START, NOW, 3)
    assert count == 5000
    assert native_history_request_count("2026-10-27T08:00:00+09:00",
                                        datetime(2026, 10, 27, 17, tzinfo=SEOUL), 1) == 721


def test_after_market_source_stays_on_last_approved_session(tmp_path):
    day = datetime(2026, 10, 27, 20, 5, tzinfo=SEOUL)
    plan = initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=day.date(),
        decided_at=day, nxt_available=True)
    assert native_history_source(plan, day) == plan["sessions"][-1]


def test_native_receipt_adds_every_returned_direct_close_and_keeps_missing_dates():
    rows = [_raw("2026-10-23", "195700", 103),
            _raw("2026-09-14", "090300", 102),
            _raw("2026-09-11", "100300", 101)]
    started, response = _receipt(rows, has_more=True)
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"),
        "005930", 3, START, None)
    assert history is not None
    assert [row["close"] for row in history["candles"]] == [101, 102, 103]
    assert all(row["timeframe_minutes"] == 3 for row in history["candles"])
    assert history["source_content_sha256"] == canonical_candle_content_hash(history["candles"])
    timeline = project_operation_timeline(START, None, now=NOW,
                                          source_dates={"2026-10-23"}, active=True)
    base = {"stock_code": "005930", "trade_date": "2026-10-25",
            "instance_id": "instance-1", "bar_minutes": 3, "nxt_available": True,
            "projection_status": "VALID", "chart_candle_source": "CALCULATION_SOR_AL",
            "buy_signal_markers": [{"bar_time": START, "price": 101}],
            "sell_signal_markers": [{"bar_time": "2026-10-23T19:57:00+09:00", "price": 103}],
            "candles": [{"bar_time": "2026-10-23T19:57:00+09:00", "close": 103}],
            "operation_timeline": timeline}
    shown = apply_native_history_projection(base, history, now=NOW)
    assert [row["close"] for row in shown["candles"]] == [101, 102, 103]
    assert shown["buy_signal_markers"] == base["buy_signal_markers"]
    assert shown["sell_signal_markers"] == base["sell_signal_markers"]
    assert shown["operation_timeline"]["start_at"] == base["operation_timeline"]["start_at"]
    assert shown["operation_timeline"]["source_dates"] == [
        "2026-09-11", "2026-09-14", "2026-10-23"]
    assert "2026-09-15" in shown["operation_timeline"]["unverified_calendar_dates"]


def test_krx_native_receipt_keeps_real_close_print_and_afterhours_without_filling_gap():
    rows = [_raw("2026-10-23", clock, close) for clock, close in (
        ("160000", 103), ("154000", 102), ("153000", 101),
    )]
    started, response = _receipt(rows)
    started.update(market_data_identity="005930", market_source="KRX")
    response.update(market_data_identity="005930", market_source="KRX",
                    request_as_of="2026-10-23T16:09:00+09:00",
                    received_at="2026-10-23T16:10:00+09:00")
    started["request_as_of"] = response["request_as_of"]
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"),
        "005930", 3, "2026-10-23T09:00:00+09:00", None,
        nxt_available=False,
    )
    assert history is not None
    assert [row["bar_time"][11:16] for row in history["candles"]] == [
        "15:30", "15:40", "16:00",
    ]
    assert [row["session"] for row in history["candles"]] == [
        "KRX_CLOSING", "KRX_AFTER_CLOSE", "KRX_SINGLE_PRICE",
    ]


def test_sor_native_1535_row_is_displayed_without_synthetic_neighbors():
    started, response = _receipt([_raw("2026-10-23", "153500", 101)])
    started["request_as_of"] = "2026-10-23T15:38:00+09:00"
    response.update(request_as_of=started["request_as_of"],
                    received_at="2026-10-23T15:39:00+09:00")
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"),
        "005930", 3, "2026-10-23T09:00:00+09:00", None,
    )
    assert history is not None
    assert [(row["bar_time"][11:16], row["session"]) for row in history["candles"]] == [
        ("15:35", "AL_TRANSITION"),
    ]


def test_sor_native_m1_1535_receipt_carries_display_only_special_provenance():
    started, response = _receipt([_raw("2026-10-23", "153500", 101)])
    response.update(interval=1, received_at="2026-10-23T15:37:00+09:00")
    started["request_as_of"] = "2026-10-23T15:36:00+09:00"
    response["request_as_of"] = started["request_as_of"]
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"),
        "005930", 1, "2026-10-23T09:00:00+09:00", None,
    )
    assert history is not None
    assert history["candles"][0]["bar_time"][11:16] == "15:35"
    assert history["candles"][0]["display_only_special"] == (
        "BROKER_AL_NATIVE_1535_CLOSING_PRICE"
    )
    at = datetime(2026, 10, 23, 15, 39, tzinfo=SEOUL)
    base = {
        "stock_code": "005930", "bar_minutes": 1, "nxt_available": True,
        "candles": [], "operation_timeline": project_operation_timeline(
            "2026-10-23T09:00:00+09:00", None, now=at,
            source_dates=set(), active=True,
        ),
    }
    shown = apply_native_history_projection(base, history, now=at)
    assert shown["candles"][0]["display_only_special"] == (
        "BROKER_AL_NATIVE_1535_CLOSING_PRICE"
    )


def test_native_receipt_rejects_wrong_session_timeframe_and_broken_rows(tmp_path):
    started, response = _receipt([_raw("2026-09-11", "100300", 101)])
    args = (started, response, (7, "session-7"), (7, "session-7"),
            "005930", 3, START, None)
    assert validate_native_history_receipt(*args) is not None
    assert validate_native_history_receipt(started, {**response, "interval": 1},
                                           *args[2:]) is None
    assert validate_native_history_receipt(started, response,
                                           (7, "session-7"), (8, "session-8"),
                                           *args[4:]) is None
    assert validate_native_history_receipt(started, {**response, "rows": [
        {**response["rows"][0], "저가": "200"}]}, *args[2:]) is None


    plan = initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=NOW.date(),
        decided_at=NOW, nxt_available=True)
    nx_started = {**started, "market_source": "NXT", "market_data_identity": "005930_NX"}
    nx_response = {**response, "market_source": "NXT", "market_data_identity": "005930_NX"}
    assert validate_native_history_receipt(
        nx_started, nx_response, *args[2:], selected_source="NXT",
        source_plan=plan) is None


def test_waiting_projection_cannot_admit_old_broker_history():
    started, response = _receipt([_raw("2026-09-11", "100300", 101)])
    history = validate_native_history_receipt(started, response,
                                               (7, "session-7"), (7, "session-7"),
                                               "005930", 3, START, None)
    base = {"stock_code": "005930", "bar_minutes": 3, "nxt_available": True,
            "candles": [], "operation_timeline": {"status": "WAITING",
                                                  "start_at": "", "end_at": ""}}
    assert apply_native_history_projection(base, history, now=NOW) == base


def test_waiting_m3_window_displays_verified_day_without_operation_markers(tmp_path):
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 10, 27, 10, 4, tzinfo=SEOUL)
    plan = initialize_stock_market_source_plan(tmp_path, "005930", "Samsung",
                                               trading_day=now.date(), decided_at=now,
                                               nxt_available=True)

    class Api:
        def broker_session_snapshot(self):
            return SimpleNamespace(connected=True, connection_epoch=7,
                                   login_session_id="session-7")

        def request_minute_candles_display_only_integrated(self, _code, _name, **kwargs):
            self.callback = kwargs["callback"]
            self.count = kwargs["count"]
            started, _ = _receipt([])
            started["request_as_of"] = now.isoformat()
            return started

    class Market:
        kiwoom_api = Api()
        _quickchart_native_histories = OrderedDict()
        _current_display_session_identity = MarketDataHost._current_display_session_identity
        quickchart_native_history = MarketDataHost.quickchart_native_history
        remember_quickchart_native_history = MarketDataHost.remember_quickchart_native_history

        def project_routine_candles(self, **_kwargs):
            raise AssertionError("test supplies projection")

    market = Market()
    base = {"stock_code": "005930", "trade_date": now.date().isoformat(),
            "source_plan": plan, "instance_id": "instance-1", "bar_minutes": 3,
            "nxt_available": True, "projection_status": "NO_DAY_DATA",
            "chart_candle_source": "SOURCE_UNAVAILABLE", "candles": [],
            "buy_signal_markers": [], "sell_signal_markers": [],
            "operation_timeline": {"status": "WAITING", "start_at": "", "end_at": ""}}
    with patch("gui_stock_instance_chart_window._production_candle_provider",
               return_value=market.project_routine_candles), patch(
                   "gui_stock_instance_chart_window.project_stock_instance_day",
                   return_value=base), patch("gui_stock_instance_chart_window.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        window = StockInstanceChartWindow("005930", now.date().isoformat())
        try:
            assert market.kiwoom_api.count == 241
            _, response = _receipt([_raw("2026-10-27", "090000", 101),
                                    _raw("2026-10-27", "090600", 103)])
            response.update(request_as_of=now.isoformat(), received_at=now.isoformat())
            market.kiwoom_api.callback(response)
            assert [value for _, value in window.chart.close_series] == [101, 103]
            assert window.timeline_label.text() == "NEW OPERATION WAITING"
            assert window.last_projection["operation_timeline"]["start_at"] == ""
            assert window.chart.buy_series == window.chart.sell_series == []
            assert len(market._quickchart_native_histories) == 1
            app.processEvents()
        finally:
            window.close()

        reopened = StockInstanceChartWindow("005930", now.date().isoformat())
        try:
            assert [value for _, value in reopened.chart.close_series] == [101, 103]
            assert reopened.chart.buy_series == reopened.chart.sell_series == []
            assert market.kiwoom_api.count == 241
            assert market.quickchart_native_history(
                "005930", 1, "INTEGRATED", waiting_display_start(plan), "") is None
            assert market.quickchart_native_history(
                "000660", 3, "INTEGRATED", waiting_display_start(plan), "") is None
            assert market.quickchart_native_history(
                "005930", 3, "KRX", waiting_display_start(plan), "") is None
            assert market.quickchart_native_history(
                "005930", 3, "INTEGRATED", START, "") is None
            market.kiwoom_api.broker_session_snapshot = lambda: SimpleNamespace(
                connected=True, connection_epoch=8, login_session_id="session-8")
            assert market.quickchart_native_history(
                "005930", 3, "INTEGRATED", waiting_display_start(plan), "") is None
        finally:
            reopened.close()


def test_waiting_m1_keeps_more_than_300_actual_native_closes(tmp_path):
    now = datetime(2026, 10, 27, 15, 19, tzinfo=SEOUL)
    plan = initialize_stock_market_source_plan(tmp_path, "005930", "Samsung",
                                               trading_day=now.date(), decided_at=now,
                                               nxt_available=True)
    times = [now.replace(hour=9, minute=0) + timedelta(minutes=index)
             for index in range(350)]
    started, response = _receipt([_raw("2026-10-27", stamp.strftime("%H%M%S"), 100)
                                  for stamp in times])
    started["request_as_of"] = now.isoformat()
    response.update(interval=1, request_as_of=now.isoformat(), received_at=now.isoformat())
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"), "005930", 1,
        "2026-10-27T08:00:00+09:00", None, requested_count=721,
        selected_source="INTEGRATED", source_plan=plan)
    assert history is not None and len(history["candles"]) == 350
    base = {"stock_code": "005930", "bar_minutes": 1, "nxt_available": True,
            "source_plan": plan, "candles": [], "operation_timeline": {
                "status": "WAITING", "start_at": "", "end_at": ""}}
    shown = apply_native_history_projection(base, history, now=now)
    assert len(shown["candles"]) == 350
    assert shown["operation_timeline"]["start_at"] == ""


def test_waiting_merges_separate_verified_nxt_and_sor_without_gap_bars(tmp_path):
    now = datetime(2026, 10, 27, 10, 4, tzinfo=SEOUL)
    plan = initialize_stock_market_source_plan(tmp_path, "005930", "Samsung",
                                               trading_day=now.date(), decided_at=now,
                                               nxt_available=True)
    start = "2026-10-27T08:00:00+09:00"
    sor_start, sor_response = _receipt([_raw("2026-10-27", "090000", 102)])
    sor_start["request_as_of"] = now.isoformat()
    sor_response.update(request_as_of=now.isoformat(), received_at=now.isoformat())
    sor = validate_native_history_receipt(
        sor_start, sor_response, (7, "session-7"), (7, "session-7"),
        "005930", 3, start, None, selected_source="INTEGRATED", source_plan=plan)
    nx_start, nx_response = _receipt([_raw("2026-10-27", "080000", 101)])
    nx_start.update(market_data_identity="005930_NX", market_source="NXT",
                    request_as_of=now.isoformat())
    nx_response.update(market_data_identity="005930_NX", market_source="NXT",
                       request_as_of=now.isoformat(), received_at=now.isoformat())
    nxt = validate_native_history_receipt(
        nx_start, nx_response, (7, "session-7"), (7, "session-7"),
        "005930", 3, start, None, selected_source="NXT", source_plan=plan)
    assert sor is not None and nxt is not None
    base = {"stock_code": "005930", "bar_minutes": 3, "nxt_available": True,
            "source_plan": plan, "candles": [], "operation_timeline": {
                "status": "WAITING", "start_at": "", "end_at": ""}}
    shown = apply_native_history_projection(
        apply_native_history_projection(base, sor, now=now), nxt, now=now)
    assert [row["bar_time"][11:16] for row in shown["candles"]] == ["08:00", "09:00"]
    assert [row["market_data_identity"] for row in shown["candles"]] == [
        "005930_NX", "005930_AL"]
    assert shown["chart_history_coverage"] == "VERIFIED_SOURCES"
    assert set(shown["chart_history_receipts"]) == {"NXT", "INTEGRATED"}
    assert shown["operation_timeline"]["start_at"] == ""


def test_broker_page_cap_marks_partial_first_day_even_when_date_has_a_close():
    started, response = _receipt([_raw("2026-10-23", "195700", 103),
                                  _raw("2026-09-11", "150000", 101)], has_more=True)
    history = validate_native_history_receipt(started, response,
                                               (7, "session-7"), (7, "session-7"),
                                               "005930", 3, START, None)
    base = {"stock_code": "005930", "bar_minutes": 3, "nxt_available": True,
            "candles": [], "operation_timeline": project_operation_timeline(
                START, None, now=NOW, source_dates=set(), active=True)}
    shown = apply_native_history_projection(base, history, now=NOW)
    assert shown["operation_timeline"]["native_history_incomplete"] is True
    assert shown["operation_timeline"]["source_dates"][0] == "2026-09-11"


def test_request_count_cutoff_marks_partial_first_day_without_has_more():
    started, response = _receipt([_raw("2026-10-23", "195700", 103),
                                  _raw("2026-09-11", "150000", 101)])
    history = validate_native_history_receipt(
        started, response, (7, "session-7"), (7, "session-7"),
        "005930", 3, START, None, requested_count=2)
    base = {"stock_code": "005930", "bar_minutes": 3, "nxt_available": True,
            "candles": [], "operation_timeline": project_operation_timeline(
                START, None, now=NOW, source_dates=set(), active=True)}
    shown = apply_native_history_projection(base, history, now=NOW)
    assert shown["operation_timeline"]["native_history_incomplete"] is True


def test_completed_slot_uses_source_session_and_skips_auction_gaps(tmp_path):
    day = datetime(2026, 10, 27, 9, 0, tzinfo=SEOUL)
    plan = initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=day.date(),
        decided_at=day, nxt_available=True)
    assert native_history_completed_slot(plan, day, 3, "SOR") is None
    assert native_history_completed_slot(plan, day.replace(minute=3), 3, "SOR") == day.replace(minute=3)
    assert native_history_completed_slot(plan, day.replace(hour=8, minute=55), 3, "NXT") is None
    assert native_history_completed_slot(plan, day.replace(hour=15, minute=25), 3, "SOR") is None
    assert native_history_completed_slot(plan, day.replace(hour=15, minute=35), 3, "SOR") is None
    assert native_history_completed_slot(plan, day.replace(hour=15, minute=40), 3, "SOR") is None


def test_increment_requires_identical_native_overlap_and_preserves_history():
    started, response = _receipt([_raw("2026-10-23", "195700", 103),
                                  _raw("2026-09-11", "100300", 101)])
    old = validate_native_history_receipt(started, response, (7, "session-7"),
                                          (7, "session-7"), "005930", 3, START, None)
    assert old is not None
    newer = dict(old)
    newer["candles"] = [old["candles"][-1],
                        {**old["candles"][-1], "bar_time": "2026-10-23T20:00:00+09:00", "close": 104,
                         "high": 105}]
    newer["source_content_sha256"] = canonical_candle_content_hash(newer["candles"])
    merged = merge_native_history_increment(old, newer)
    assert merged is not None
    assert [row["close"] for row in merged["candles"]] == [101, 103, 104]
    assert merge_native_history_increment(old, {**newer, "candles": newer["candles"][1:]}) is None
    changed = dict(newer)
    changed["candles"] = [{**newer["candles"][0], "close": 999}, newer["candles"][1]]
    assert merge_native_history_increment(old, changed) is None


def test_production_window_requests_native_m3_and_displays_uncached_closes(tmp_path):
    app = QApplication.instance() or QApplication([])
    class Api:
        def broker_session_snapshot(self):
            return SimpleNamespace(connected=True, connection_epoch=7,
                                   login_session_id="session-7")

        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            self.code, self.kwargs = code, kwargs
            started, _response = _receipt([])
            return started

    class Market:
        kiwoom_api = Api()

        def project_routine_candles(self, **_kwargs):
            raise AssertionError("projection is supplied by test")

    market = Market()
    plan = initialize_stock_market_source_plan(tmp_path, "005930", "Samsung",
                                               trading_day=NOW.date(), decided_at=NOW, nxt_available=True)
    base = {"stock_code": "005930", "trade_date": "2026-10-25",
            "source_plan": plan,
            "instance_id": "instance-1", "bar_minutes": 3, "nxt_available": True,
            "projection_status": "NO_DAY_DATA", "chart_candle_source": "SOURCE_UNAVAILABLE",
            "candles": [], "operation_timeline": project_operation_timeline(
                START, None, now=NOW, source_dates=set(), active=True)}
    with patch("gui_stock_instance_chart_window._production_candle_provider",
               return_value=market.project_routine_candles), patch(
                   "gui_stock_instance_chart_window.project_stock_instance_day",
                   return_value=base), patch("gui_stock_instance_chart_window.datetime", wraps=datetime) as clock:
        clock.now.return_value = NOW
        window = StockInstanceChartWindow("005930", "2026-10-25")
        try:
            assert market.kiwoom_api.code == "005930"
            assert market.kiwoom_api.kwargs["interval"] == 3
            assert market.kiwoom_api.kwargs["count"] == 5000
            started, response = _receipt([_raw("2026-10-23", "195700", 103),
                                          _raw("2026-09-14", "090300", 102),
                                          _raw("2026-09-11", "100300", 101)])
            market.kiwoom_api.kwargs["callback"](response)
            assert [close for _time, close in window.chart.close_series] == [101, 102, 103]
            assert window.last_projection["chart_history_receipt"]["request_id"] == started["rqname"]
            app.processEvents()
        finally:
            window.close()


def test_ongoing_window_fetches_only_bounded_new_native_slot(tmp_path):
    app = QApplication.instance() or QApplication([])
    clock_time = {"now": datetime(2026, 10, 27, 10, 4, tzinfo=SEOUL)}

    class Api:
        def __init__(self):
            self.calls = []

        def broker_session_snapshot(self):
            return SimpleNamespace(connected=True, connection_epoch=7,
                                   login_session_id="session-7")

        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            self.calls.append(kwargs)
            started, _ = _receipt([])
            started["rqname"] = f"opt10080_005930_{len(self.calls)}"
            started["request_as_of"] = clock_time["now"].isoformat()
            return started

    class Market:
        kiwoom_api = Api()

        def project_routine_candles(self, **_kwargs):
            raise AssertionError("projection is supplied by test")

    market = Market()
    now = clock_time["now"]
    plan = initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=now.date(),
        decided_at=now, nxt_available=True)
    base = {"stock_code": "005930", "trade_date": now.date().isoformat(),
            "source_plan": plan, "instance_id": "instance-1", "bar_minutes": 3,
            "nxt_available": True, "projection_status": "NO_DAY_DATA",
            "chart_candle_source": "SOURCE_UNAVAILABLE", "candles": [],
            "operation_timeline": project_operation_timeline(
                "2026-10-27T09:00:00+09:00", None, now=now,
                source_dates=set(), active=True)}
    with patch("gui_stock_instance_chart_window._production_candle_provider",
               return_value=market.project_routine_candles), patch(
                   "gui_stock_instance_chart_window.project_stock_instance_day",
                   return_value=base), patch("gui_stock_instance_chart_window.datetime", wraps=datetime) as clock:
        clock.now.side_effect = lambda _tz: clock_time["now"]
        window = StockInstanceChartWindow("005930", now.date().isoformat())
        try:
            assert len(market.kiwoom_api.calls) == 1

            def reply(index, times):
                call = market.kiwoom_api.calls[index]
                started, response = _receipt([_raw("2026-10-27", stamp, close)
                                              for stamp, close in times])
                response.update(request_id=f"opt10080_005930_{index + 1}",
                                rqname=f"opt10080_005930_{index + 1}",
                                request_as_of=(datetime(2026, 10, 27, 10, 4 + 3 * index,
                                                        tzinfo=SEOUL).isoformat()),
                                received_at=clock_time["now"].isoformat())
                call["callback"](response)

            reply(0, [("095700", 101), ("100000", 102)])
            assert [value for _, value in window.chart.close_series] == [101, 102]
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 1
            clock_time["now"] = now + timedelta(minutes=3)
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 2
            assert market.kiwoom_api.calls[1]["count"] == 3
            reply(1, [("100000", 102), ("100300", 103)])
            assert [value for _, value in window.chart.close_series] == [101, 102, 103]
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 2
            clock_time["now"] = now + timedelta(minutes=6)
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 3
            base["operation_timeline"] = {**base["operation_timeline"], "status": "STOPPED"}
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 3
            base["source_plan"] = None
            window.refresh_projection()
            assert len(market.kiwoom_api.calls) == 3
            window.close()
            reply(2, [("100300", 103), ("100600", 104)])
            assert window._native_history is None
            assert window._native_histories == {}
            app.processEvents()
        finally:
            window.close()
