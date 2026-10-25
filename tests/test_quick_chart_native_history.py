"""Broker-native quickchart history stays separate from operation candle caches."""

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from candle_manager import canonical_candle_content_hash
from quick_chart_native_history import (
    apply_native_history_projection,
    native_history_request_count,
    validate_native_history_receipt,
)
from stock_instance_day_projection import project_operation_timeline
from gui_stock_instance_chart_window import StockInstanceChartWindow


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


def test_native_request_budget_reaches_september_at_selected_m3_interval():
    count = native_history_request_count(START, NOW, 3)
    assert 5000 < count <= 200_000
    assert native_history_request_count(START, NOW, 1) > count


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
            "candles": [{"bar_time": "2026-10-23T19:57:00+09:00", "close": 103}],
            "operation_timeline": timeline}
    shown = apply_native_history_projection(base, history, now=NOW)
    assert [row["close"] for row in shown["candles"]] == [101, 102, 103]
    assert shown["operation_timeline"]["source_dates"] == [
        "2026-09-11", "2026-09-14", "2026-10-23"]
    assert "2026-09-15" in shown["operation_timeline"]["unverified_calendar_dates"]


def test_native_receipt_rejects_wrong_session_timeframe_and_broken_rows():
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


def test_waiting_projection_cannot_admit_old_broker_history():
    started, response = _receipt([_raw("2026-09-11", "100300", 101)])
    history = validate_native_history_receipt(started, response,
                                               (7, "session-7"), (7, "session-7"),
                                               "005930", 3, START, None)
    base = {"stock_code": "005930", "bar_minutes": 3, "nxt_available": True,
            "candles": [], "operation_timeline": {"status": "WAITING",
                                                  "start_at": "", "end_at": ""}}
    assert apply_native_history_projection(base, history, now=NOW) == base


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


def test_production_window_requests_native_m3_and_displays_uncached_closes():
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
    base = {"stock_code": "005930", "trade_date": "2026-10-25",
            "instance_id": "instance-1", "bar_minutes": 3, "nxt_available": True,
            "projection_status": "NO_DAY_DATA", "chart_candle_source": "SOURCE_UNAVAILABLE",
            "candles": [], "operation_timeline": project_operation_timeline(
                START, None, now=NOW, source_dates=set(), active=True)}
    with patch("gui_stock_instance_chart_window._production_candle_provider",
               return_value=market.project_routine_candles), patch(
                   "gui_stock_instance_chart_window.project_stock_instance_day",
                   return_value=base):
        window = StockInstanceChartWindow("005930", "2026-10-25")
        try:
            assert market.kiwoom_api.code == "005930"
            assert market.kiwoom_api.kwargs["interval"] == 3
            assert market.kiwoom_api.kwargs["count"] > 5000
            started, response = _receipt([_raw("2026-10-23", "195700", 103),
                                          _raw("2026-09-14", "090300", 102),
                                          _raw("2026-09-11", "100300", 101)])
            market.kiwoom_api.kwargs["callback"](response)
            assert [close for _time, close in window.chart.close_series] == [101, 102, 103]
            assert window.last_projection["chart_history_receipt"]["request_id"] == started["rqname"]
            app.processEvents()
        finally:
            window.close()
