"""Read-only quick-chart lifecycle boundaries with detached SOR evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from candle_manager import canonical_candle_content_hash
from event_journal_writer import EventJournalWriter
from gui_stock_instance_chart_window import StockInstanceChartWindow, StockInstanceCloseChart
from mock_validation_contract import payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_quick_chart import (_mock_operation_timeline,
                                         _projection as mock_projection)
from stock_instance_day_projection import (project_stock_instance_day, project_operation_timeline,
                                           _production_operation_timeline)
from tests.test_mock_indicator_follow_adapter import _buy_rules
from tests.test_mock_validation_host_ui import _Api, _reference


SEOUL = timezone(timedelta(hours=9))
START = "2026-10-19T10:00:17+09:00"
END = "2026-10-20T14:00:29+09:00"
NOW = datetime(2026, 10, 20, 14, 1, tzinfo=SEOUL)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _bar(day: str, clock: str, close: int) -> dict[str, object]:
    return {"bar_time": f"{day}T{clock}+09:00", "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 10,
            "timeframe_minutes": 5, "is_complete": True, "trade_date": day,
            "session": "KRX", "market_source": "INTEGRATED",
            "market_data_identity": "005930_AL", "display_only": True}


def test_timeline_keeps_exact_events_and_only_source_witnessed_days():
    result = project_operation_timeline(
        "2026-10-16T10:00:17+09:00", "2026-10-20T14:00:29+09:00",
        now=NOW, source_dates={"2026-10-16", "2026-10-19", "2026-10-20"},
    )
    assert result["status"] == "ENDED"
    assert result["start_at"].endswith("10:00:17+09:00")
    assert result["end_at"].endswith("14:00:29+09:00")
    assert result["trade_dates"] == ["2026-10-16", "2026-10-19", "2026-10-20"]
    assert project_operation_timeline(START, None, now=NOW, source_dates=set())["status"] == "END_UNKNOWN"


def test_stopped_stock_with_old_start_is_new_operation_waiting_without_start_marker():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        _write(root / "stocks" / "005930_Test" / "state.json",
               {"status": "STOPPED", "trade_enabled": False, "holding_qty": 0})
        _write(root / "runtime" / "operation_state.json",
               {"operation_status": "STOPPED", "operation_participant_stock_codes": []})
        journal = EventJournalWriter(root / "runtime" / "event_journal")
        for when in ("2026-09-11T11:59:00+09:00", "2026-09-11T12:07:00+09:00"):
            assert journal.append_event(
                event_type="OPERATION_STARTED", occurred_at=when, category="OPERATION",
                severity="INFO", template_args={"target": "005930"}, result="SUCCESS",
                source="test", target_type="STOCK", target_id="005930",
                target_name="Test", stock_code="005930")["appended"]
        timeline = _production_operation_timeline(root, "005930", NOW, {"2026-10-19"})
        assert timeline["status"] == "WAITING"
        assert timeline["start_at"] == timeline["end_at"] == ""
        assert timeline["trade_dates"] == []
        app = QApplication.instance() or QApplication([])
        window = StockInstanceChartWindow("005930", "2026-10-20",
                                          projection_provider=lambda _code, _day: {
                                              "stock_code": "005930", "trade_date": "2026-10-20",
                                              "operation_timeline": timeline, "candles": []})
        try:
            assert "NEW OPERATION WAITING" in window.timeline_label.text()
            assert window.chart.operation_timeline["start_at"] == ""
            app.processEvents()
        finally:
            window.close()


def test_active_m3_range_exposes_unverified_dates_without_inventing_candles():
    timeline = project_operation_timeline(
        "2026-09-11T11:59:00+09:00", None,
        now=datetime(2026, 10, 25, 12, tzinfo=SEOUL),
        source_dates={"2026-09-11", "2026-10-23"}, active=True,
    )
    assert timeline["status"] == "ONGOING"
    assert timeline["trade_dates"] == ["2026-09-11", "2026-10-23"]
    assert timeline["display_dates"][0] == "2026-09-11"
    assert timeline["display_dates"][-1] == "2026-10-23"
    assert "2026-09-14" in timeline["unverified_calendar_dates"]
    assert "2026-10-23" not in timeline["unverified_calendar_dates"]
    app = QApplication.instance() or QApplication([])
    rows = [{**_bar(day, "10:00:00", close), "timeframe_minutes": 3}
            for day, close in (("2026-09-11", 101), ("2026-10-23", 103))]
    window = StockInstanceChartWindow("005930", "2026-10-25",
                                      projection_provider=lambda _code, _day: {
                                          "stock_code": "005930", "trade_date": "2026-10-25",
                                          "instance_id": "instance-1", "bar_minutes": 3,
                                          "projection_status": "VALID", "candles": rows,
                                          "operation_timeline": timeline})
    try:
        assert len(window.chart.close_series) == 2
        assert len(window.chart.visible_time_ranges) == len(timeline["display_dates"])
        assert "coverage unverified" in window.timeline_label.text()
        app.processEvents()
    finally:
        window.close()


def test_active_range_identifies_trailing_source_gap_before_sunday():
    timeline = project_operation_timeline(
        "2026-09-11T11:59:00+09:00", None,
        now=datetime(2026, 10, 25, 12, tzinfo=SEOUL),
        source_dates={"2026-09-11", "2026-10-20"}, active=True)
    assert timeline["display_dates"][-1] == "2026-10-23"
    assert timeline["unverified_calendar_dates"][-3:] == [
        "2026-10-21", "2026-10-22", "2026-10-23"]


def test_refresh_replaces_stale_start_when_stock_becomes_waiting():
    app = QApplication.instance() or QApplication([])
    active = {"stock_code": "005930", "trade_date": "2026-10-20",
              "instance_id": "instance-1", "bar_minutes": 3,
              "projection_status": "VALID",
              "candles": [{**_bar("2026-10-20", "10:03:00", 101),
                           "timeframe_minutes": 3}],
              "operation_timeline": project_operation_timeline(
                  START, None, now=NOW, source_dates={"2026-10-20"}, active=True)}
    waiting = {**active, "projection_status": "NO_DAY_DATA", "candles": [],
               "operation_timeline": {"status": "WAITING", "start_at": "",
                                      "end_at": "", "trade_dates": []}}
    current = {"projection": active}
    window = StockInstanceChartWindow(
        "005930", "2026-10-20",
        projection_provider=lambda _code, _day: current["projection"])
    try:
        assert window.chart.close_series
        current["projection"] = waiting
        window.refresh_projection()
        assert window.timeline_label.text() == "NEW OPERATION WAITING"
        assert window.chart.operation_timeline["start_at"] == ""
        assert window.chart.close_series == []
        app.processEvents()
    finally:
        window.close()


def test_active_m3_uses_all_witnessed_native_sor_sessions_without_m1_rebuild():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        stock_dir = root / "stocks" / "005930_Test"
        _write(stock_dir / "config.json", {"assigned_routine_instance_id": "instance-1"})
        _write(stock_dir / "state.json", {"status": "RUNNING"})
        _write(root / "runtime" / "operation_state.json", {
            "operation_status": "RUNNING", "operation_started_at": "2026-10-19T09:59:00+09:00",
            "operation_participant_stock_codes": ["005930"]})
        _write(root / "operation_policy.json", {})
        rules_path = root / "rules.json"
        _write(rules_path, {"bar": {"bar_minutes": 3}})
        journal = EventJournalWriter(root / "runtime" / "event_journal")
        assert journal.append_event(
            event_type="OPERATION_STARTED", occurred_at=START, category="OPERATION",
            severity="INFO", template_args={"target": "005930"}, result="SUCCESS",
            source="test", target_type="STOCK", target_id="005930",
            target_name="Test", stock_code="005930")["appended"]
        bars = [{**_bar(day, clock, close), "timeframe_minutes": 3}
                for day, prices in (("2026-10-19", (101, 102)),
                                    ("2026-10-20", (103, 104)),
                                    ("2026-10-21", (105, 106)))
                for clock, close in zip(("10:03:00", "10:06:00"), prices)]
        scopes = []
        def provider(**kwargs):
            scopes.append(kwargs["consumer_scope"])
            selected = bars[-kwargs["projection_request"].get("history_target_bars", len(bars)):]
            source_hash = canonical_candle_content_hash(selected)
            return {"available": True, "availability_state": "AVAILABLE",
                    "candles": selected, "timeframe_minutes": 3,
                    "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
                    "calculation_only": True, "execution_enabled": False,
                    "queue_connected": False, "send_order_connected": False,
                    "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
                    "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
                    "direct_timeframe_source": True, "source_identity": source_hash,
                    "direct_source_receipt": {"stock_code": "005930",
                                              "timeframe_key": "M3",
                                              "source_content_sha256": source_hash}}
        with (patch("stock_instance_day_projection.routine_instance_by_id",
                    return_value=SimpleNamespace(rules_path=rules_path, definition_id="def",
                                                 display_name="Test")),
              patch("stock_instance_day_projection.routine_definition_by_id",
                    return_value=object()),
              patch("stock_instance_day_projection.load_routine_callable",
                    return_value=lambda _rules: {"projection": "COMPLETED_TIMEFRAME",
                                                 "history_target_bars": 2})):
            projected = project_stock_instance_day(
                "005930", "2026-10-21", project_root=root,
                now=datetime(2026, 10, 21, 14, tzinfo=SEOUL), candle_provider=provider)
        assert scopes == ["SOR_CALCULATION_ONLY"]
        assert projected["operation_timeline"]["status"] == "ONGOING"
        assert projected["operation_timeline"]["unverified_calendar_dates"] == []
        assert [bar["close"] for bar in projected["candles"]] == [101, 102, 103, 104, 105, 106]
        assert projected["calculation_candle_source"] == "SOR_AL"
        app = QApplication.instance() or QApplication([])
        window = StockInstanceChartWindow("005930", "2026-10-21",
                                          projection_provider=lambda _code, _day: projected)
        try:
            assert len(window.chart.close_series) == 6
            assert len(window.chart.visible_time_ranges) == 3
            assert [len(segment) for segment in window.chart._line_segments()] == [2, 2, 2]
            assert "ongoing" in window.timeline_label.text()
            app.processEvents()
        finally:
            window.close()


def test_production_projection_uses_verified_stock_journal_and_sor_without_guessing_end():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        stock_dir = root / "stocks" / "005930_Test"
        _write(stock_dir / "config.json", {"assigned_routine_instance_id": "instance-1"})
        _write(stock_dir / "state.json", {"status": "RUNNING"})
        _write(root / "runtime" / "operation_state.json",
               {"operation_status": "RUNNING", "operation_started_at": "2026-10-19T09:59:00+09:00",
                "operation_participant_stock_codes": ["005930"]})
        _write(root / "operation_policy.json", {"review_policy": {"long_term_holding_enabled": True}})
        rules_path = root / "rules.json"
        _write(rules_path, {"bar": {"bar_minutes": 5}})
        _write(root / "runtime" / "routine_signals.json", {"signals": [
            {"id": "B", "code": "005930", "routine_instance_id": "instance-1", "signal": "BUY",
             "signal_bar_time": "2026-10-19T10:05:00+09:00", "signal_bar_close": 101,
             "signal_timeframe_minutes": 5, "signal_trade_date": "2026-10-19"},
            {"id": "S", "code": "005930", "routine_instance_id": "instance-1", "signal": "SELL",
             "signal_bar_time": "2026-10-20T13:55:00+09:00", "signal_bar_close": 102,
             "signal_timeframe_minutes": 5, "signal_trade_date": "2026-10-20"},
        ]})
        _write(root / "runtime" / "fills.json", {"fills": [
            {"fill_id": "FB", "code": "005930", "side": "BUY", "filled_quantity": 1,
             "filled_price": 101, "broker_order_no": "OB",
             "received_at": "2026-10-19T10:06:13+09:00"},
            {"fill_id": "FS", "code": "005930", "side": "SELL", "filled_quantity": 1,
             "filled_price": 102, "broker_order_no": "OS",
             "received_at": "2026-10-20T13:56:41+09:00"},
        ]})
        journal = EventJournalWriter(root / "runtime" / "event_journal")
        def event(kind: str, when: str, **fields):
            result = journal.append_event(event_type=kind, occurred_at=when, category="OPERATION",
                                          severity="INFO", template_args={"target": "005930 Test",
                                                                          "stock_name": "Test"},
                                          result="SUCCESS" if kind == "OPERATION_STARTED" else "COMPLETED",
                                          source=("operation_close_completion_check_service.check_global_close_completion_after_durable_update"
                                                  if kind == "LIQUIDATION_COMPLETED" else "test"),
                                          target_type="STOCK", target_id="005930", stock_code="005930",
                                          target_name="Test", **fields)
            assert result["appended"], result
        event("OPERATION_STARTED", START)
        bars = [_bar("2026-10-19", "10:05:00", 101), _bar("2026-10-20", "13:55:00", 102)]
        scopes = []
        def provider(**kwargs):
            scopes.append(kwargs["consumer_scope"])
            source_hash = canonical_candle_content_hash(bars)
            return {"available": True, "availability_state": "AVAILABLE", "candles": bars,
                    "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
                    "timeframe_minutes": 5, "calculation_only": True,
                    "execution_enabled": False, "queue_connected": False,
                    "send_order_connected": False, "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
                    "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
                    "direct_timeframe_source": True, "source_identity": source_hash,
                    "direct_source_receipt": {"stock_code": "005930", "timeframe_key": "M5",
                                              "source_content_sha256": source_hash}}
        with patch("stock_instance_day_projection.routine_instance_by_id",
                   return_value=SimpleNamespace(rules_path=rules_path, definition_id="def", display_name="Test")), \
             patch("stock_instance_day_projection.routine_definition_by_id", return_value=object()), \
             patch("stock_instance_day_projection.load_routine_callable",
                   return_value=lambda _rules: {"projection": "COMPLETED_TIMEFRAME"}):
            ongoing = project_stock_instance_day("005930", "2026-10-20", project_root=root,
                                                 now=NOW, candle_provider=provider)
            global_stop = journal.append_event(
                event_type="OPERATION_STOPPED", occurred_at="2026-10-20T13:59:00+09:00",
                category="OPERATION", severity="INFO", template_args={"target": "all"},
                result="COMPLETED", source="test", target_type="OPERATION",
                target_id="global_operation", target_name="all")
            assert global_stop["appended"]
            still_unknown = project_stock_instance_day("005930", "2026-10-20",
                                                       project_root=root, now=NOW,
                                                       candle_provider=provider)
            _write(root / "operation_policy.json", {"review_policy": {"long_term_holding_enabled": False}})
            off_policy = project_stock_instance_day("005930", "2026-10-20",
                                                    project_root=root, now=NOW,
                                                    candle_provider=provider)
            _write(stock_dir / "state.json", {"status": "REVIEW_REQUIRED", "review_required": True})
            review = project_stock_instance_day("005930", "2026-10-20",
                                                project_root=root, now=NOW,
                                                candle_provider=provider)
            _write(stock_dir / "state.json", {"status": "RUNNING"})
            event("LIQUIDATION_COMPLETED", END,
                  details={"completion_status": "DONE", "close_mode": "AUTO_CLOSE"})
            bars[:] = [_bar("2026-10-19", "09:55:00", 99), *bars,
                       _bar("2026-10-20", "14:02:00", 104)]
            ended = project_stock_instance_day("005930", "2026-10-20", project_root=root,
                                               now=datetime(2026, 10, 20, 14, 10,
                                                            tzinfo=SEOUL),
                                               candle_provider=provider)
            unavailable = project_stock_instance_day(
                "005930", "2026-10-20", project_root=root, now=NOW,
                candle_provider=lambda **_kwargs: {"available": False, "candles": [],
                                                     "availability_state": "SOR_SOURCE_UNAVAILABLE"})
        assert scopes == ["SOR_CALCULATION_ONLY"] * 5
        assert ongoing["operation_timeline"]["status"] == "ONGOING"
        assert ongoing["operation_timeline"]["end_at"] == ""
        assert still_unknown["operation_timeline"]["end_at"] == ""
        assert off_policy["operation_timeline"]["status"] == "ONGOING"
        assert review["operation_timeline"]["status"] == "WAITING"
        assert review["operation_timeline"]["start_at"] == ""
        assert ended["operation_timeline"]["status"] == "ENDED"
        assert ended["operation_timeline"]["end_at"] == END
        assert ended["operation_timeline"]["trade_dates"] == ["2026-10-19", "2026-10-20"]
        assert unavailable["candles"] == []
        assert unavailable["operation_timeline"]["source_dates"] == []
        assert [row["bar_time"] for row in ended["candles"]] == [
            "2026-10-19T10:05:00+09:00", "2026-10-20T13:55:00+09:00"]
        assert ended["buy_signal_markers"][0]["signal_bar_time"] == "2026-10-19T10:05:00+09:00"
        assert ended["sell_signal_markers"][0]["signal_bar_time"] == "2026-10-20T13:55:00+09:00"
        assert [(item["fill_id"], item["filled_price"], item["filled_quantity_delta"])
                for item in ended["actual_fill_markers"]] == [("FB", 101, 1), ("FS", 102, 1)]
        app = QApplication.instance() or QApplication([])
        missing_window = StockInstanceChartWindow(
            "005930", "2026-10-20", projection_provider=lambda _code, _day: unavailable)
        try:
            assert "SOR source missing" in missing_window.timeline_label.text()
            assert missing_window.chart.close_series == []
        finally:
            missing_window.close()
        window = StockInstanceChartWindow("005930", "2026-10-20",
                                          projection_provider=lambda _code, _day: ended)
        try:
            assert window.chart.multi_day_timeline
            assert len(window.chart.visible_time_ranges) == 2
            assert len(window.chart.buy_series) == len(window.chart.sell_series) == 1
            assert len(window.chart.actual_fill_marker_records) == 2
            assert window.chart.actual_fill_marker_records[0]["occurred_at"] == "2026-10-19T10:06:13+09:00"
            assert window.chart.actual_fill_marker_records[1]["occurred_at"] == "2026-10-20T13:56:41+09:00"
            assert window.chart.actual_fill_marker_records[0]["_occurred_at"].second == 13
            assert window.chart.position_for("2026-10-19T10:06:13+09:00", 101).x() > \
                   window.chart.position_for("2026-10-19T10:06:00+09:00", 101).x()
            assert "10-20 14:00:29" in window.timeline_label.text()
            window.chart.grab()
            app.processEvents()
        finally:
            window.close()
        assert unavailable["candles"] == []
        assert unavailable["chart_candle_source"] == "SOURCE_UNAVAILABLE"
        assert unavailable["projection_status"] != "VALID"


def test_mock_projection_and_common_widget_show_two_days_without_production_ownership():
    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as temporary:
        clock = {"now": datetime(2026, 10, 19, 10, 0, 17, tzinfo=SEOUL)}
        api = _Api()
        host = MockValidationHost(api, project_root=Path(temporary),
                                  now_factory=lambda: clock["now"])
        try:
            actions = MockValidationUIActions(host)
            rules = _buy_rules(qty=1)
            rules["bar"] = {"bar_minutes": 5}
            reference = _reference("005930", ("A",), rules_by_instance_id={"A": rules})
            reference["calculation_market_source"] = "SOR"
            reference["snapshot_hash"] = payload_hash({k: v for k, v in reference.items()
                                                       if k != "snapshot_hash"})
            document = actions.create_waiting_session(reference)["document"]
            actions.set_instance_effective_settings("005930", "A", operation_mode="CONTINUOUS")
            host.start_instance_operation("005930", "A", as_of=clock["now"])
            session_id = document["session"]["validation_session_id"]
            identity = host.repository.read_session(session_id)["mock_operation_lifecycle"]["instance_operations"]["A"]["operation_session_id"]
            def virtual_trade(side: str, day: str, clock_text: str, price: int):
                clock["now"] = datetime.fromisoformat(f"{day}T{clock_text}+09:00")
                host.repository.append_event({
                    "event_id": f"ME-signal-{side}", "validation_session_id": session_id,
                    "stock_code": "005930", "routine_instance_id": "A",
                    "event_type": "ROUTINE_EVALUATED", "timestamp": clock["now"].isoformat(),
                    "payload": {"signal": side, "signal_bar_time": f"{day}T{clock_text[:5]}:00+09:00",
                                "signal_trade_date": day, "signal_bar_close": price,
                                "operation_identity": identity, "rules_hash": payload_hash(rules)},
                })
                order_id = f"MO-{side}"
                host.session_service.create_order(
                    session_id, routine_instance_id="A", side=side, order_type="LIMIT",
                    requested_qty=1, requested_price=price, mock_order_id=order_id,
                    command_id=f"MC-create-{side}",
                )
                host.session_service.transition_order(
                    session_id, order_id, "OPEN", command_id=f"MC-open-{side}",
                )
                host.session_service.append_fill(
                    session_id, mock_order_id=order_id, qty=1, price=price,
                    market_snapshot_identity=f"SOR-{side}", mock_fill_id=f"MF-{side}",
                    command_id=f"MC-fill-{side}",
                )
            virtual_trade("BUY", "2026-10-19", "10:06:13", 101)
            virtual_trade("SELL", "2026-10-20", "13:56:41", 102)
            clock["now"] = datetime(2026, 10, 20, 14, 0, tzinfo=SEOUL)
            rows = [_bar("2026-10-19", "10:05:00", 101), _bar("2026-10-20", "13:55:00", 102)]
            source_hash = canonical_candle_content_hash(rows)
            host.routine_adapter.market_bar_projection_request = lambda _rules: {
                "projection": "COMPLETED_TIMEFRAME", "history_target_bars": 2}
            def reader(**kwargs):
                return {"available": True, "stock_code": "005930", "timeframe_minutes": 5,
                        "projection": "COMPLETED_TIMEFRAME", "market_source": "INTEGRATED",
                        "market_data_identity": "005930_AL", "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
                        "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
                        "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                        "calculation_only": True, "execution_enabled": False,
                        "queue_connected": False, "send_order_connected": False,
                        "complete": True, "forming_included": False, "direct_timeframe_source": True,
                        "available_timeframe_bars": 2, "source_identity": source_hash,
                        "candles": rows, "calculation_as_of": kwargs["as_of"].isoformat(),
                        "direct_source_receipt": {"stock_code": "005930", "timeframe_key": "M5",
                                                  "source_content_sha256": source_hash,
                                                  "connection_epoch": 1, "session_sha256": "a" * 64,
                                                  "source_as_of": kwargs["as_of"].isoformat()}}
            host._sor_calculation_value_reader = reader
            target = SimpleNamespace(stock_code="005930",
                                     validation_session_id=document["session"]["validation_session_id"],
                                     routine_instance_id="A")
            owner = SimpleNamespace(mock_validation_host=host)
            projected = mock_projection(owner, target, "005930", "2026-10-20")
            assert projected["operation_timeline"]["status"] == "ONGOING"
            assert projected["operation_timeline"]["trade_dates"] == ["2026-10-19", "2026-10-20"]
            assert len(projected["candles"]) == 2
            assert len(projected["buy_signal_markers"]) == 1
            assert len(projected["sell_signal_markers"]) == 1
            assert [(item["fill_id"], item["filled_price"], item["filled_quantity_delta"])
                    for item in projected["actual_fill_markers"]] == [("MF-BUY", 101, 1), ("MF-SELL", 102, 1)]
            assert projected["operation_timeline"]["end_at"] == ""
            window = StockInstanceChartWindow("005930", "2026-10-20",
                                              projection_provider=lambda _code, _day: projected)
            try:
                assert window.chart.multi_day_timeline
                assert len(window.chart.visible_time_ranges) == 2
                assert all((end - start).total_seconds() == 12 * 3600
                           for start, end in window.chart.visible_time_ranges)
                assert window.chart._line_segments() and len(window.chart._line_segments()) == 2
                assert len(window.chart.buy_series) == len(window.chart.sell_series) == 1
                assert len(window.chart.actual_fill_marker_records) == 2
                assert "ongoing" in window.timeline_label.text()
                window.chart.grab()
                app.processEvents()
            finally:
                window.close()
            completed = host.lifecycle.complete_instance_normal_termination(
                target.validation_session_id, routine_instance_id="A",
                as_of=clock["now"], lifecycle_cycle_id="CLOSE-A",
                ats_continuation=False,
            )
            assert completed["status"] == "DONE"
            closed = mock_projection(owner, target, "005930", "2026-10-20")
            assert closed["operation_timeline"]["status"] == "ENDED"
            assert closed["operation_timeline"]["end_at"] == clock["now"].isoformat()
            assert [item["bar_time"] for item in closed["candles"]] == [item["bar_time"] for item in rows]
        finally:
            host.dispose()


def test_mock_off_review_and_on_carryover_follow_official_lifecycle():
    for enabled, expected_state in ((False, "REVIEW_STOPPED"), (True, "ENDED")):
        with TemporaryDirectory() as temporary:
            start = datetime(2026, 10, 19, 10, 0, tzinfo=SEOUL)
            end = datetime(2026, 10, 20, 14, 0, tzinfo=SEOUL)
            host = MockValidationHost(
                _Api(), project_root=Path(temporary), now_factory=lambda: end,
                operation_policy_provider=lambda: {
                    "review_policy": {"long_term_holding_enabled": enabled},
                    "regular_market": {"end_time": "15:30:00"},
                },
            )
            try:
                actions = MockValidationUIActions(host)
                reference = _reference("005930", ("A",),
                                       rules_by_instance_id={"A": _buy_rules(qty=1)})
                created = actions.create_waiting_session(reference)["document"]
                session_id = created["session"]["validation_session_id"]
                actions.set_instance_effective_settings("005930", "A", operation_mode="CONTINUOUS")
                host.start_instance_operation("005930", "A", as_of=start)
                host.session_service.set_instance_position(
                    session_id, "A", holding_qty=1, available_qty=1,
                    average_price=100, realized_cost_basis=100,
                    command_id=f"MC-position-{enabled}",
                )
                before = host.repository.read_session(session_id)
                before_timeline = _mock_operation_timeline(
                    before, "A", host.repository.read_events(session_id), end,
                    {"2026-10-19", "2026-10-20"},
                )
                assert before_timeline["status"] == "ONGOING"
                assert before_timeline["end_at"] == ""
                host.lifecycle.complete_instance_normal_termination(
                    session_id, routine_instance_id="A", as_of=end,
                    lifecycle_cycle_id=f"CLOSE-{enabled}", ats_continuation=False,
                )
                after = host.repository.read_session(session_id)
                operation = after["mock_operation_lifecycle"]["instance_operations"]["A"]
                assert operation["state"] == expected_state
                after_timeline = _mock_operation_timeline(
                    after, "A", host.repository.read_events(session_id), end,
                    {"2026-10-19", "2026-10-20"},
                )
                assert after_timeline["status"] == "ENDED"
                assert after_timeline["end_at"] == end.isoformat()
            finally:
                host.dispose()


def test_multi_day_paint_keeps_one_scale_and_segment_pass():
    app = QApplication.instance() or QApplication([])
    chart = StockInstanceCloseChart()
    try:
        rows = []
        ranges = []
        for day in ("2026-10-19", "2026-10-20"):
            start = datetime.fromisoformat(f"{day}T08:00:00+09:00")
            ranges.append((start, start + timedelta(hours=12)))
            rows.extend({"bar_time": (start + timedelta(minutes=index)).isoformat(),
                         "close": 100 + index % 10} for index in range(300))
        chart.set_projection(rows, [], [], x_range_start=ranges[0][0],
                             x_range_end=ranges[-1][1], visible_time_ranges=ranges,
                             operation_timeline=project_operation_timeline(
                                 START, None, now=NOW,
                                 source_dates={"2026-10-19", "2026-10-20"}, active=True))
        assert len(chart.close_series) == 600
        assert len(chart._line_segments()) == 2
        with patch.object(chart, "_scale_values", wraps=chart._scale_values) as scale, \
             patch.object(chart, "_line_segments", wraps=chart._line_segments) as segments:
            chart.grab()
        assert scale.call_count == 1
        assert segments.call_count == 1
        chart.set_projection(rows[:300], [], [], x_range_start=ranges[0][0],
                             x_range_end=ranges[-1][1], visible_time_ranges=ranges,
                             operation_timeline=chart.operation_timeline)
        chart.set_live_price_projection("2026-10-20T09:00:00+09:00", 105)
        assert chart._live_price_bridge_points() is None
        app.processEvents()
    finally:
        chart.close()
