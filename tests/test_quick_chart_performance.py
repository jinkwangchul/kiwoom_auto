from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_stock_instance_chart_window as chart
import mock_validation_quick_chart as mock_chart
from mock_validation_contract import payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from tests.test_mock_validation_host_ui import _Api, _reference
from tests.test_stock_instance_chart_auto_refresh import (
    ChartOwner,
    TODAY,
    _completed_result,
    _projection,
)


class ChartRenderPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_paint_reuses_scale_time_range_and_segments(self):
        for count in (100, 300, 600, 1000):
            with self.subTest(candles=count):
                widget = chart.StockInstanceCloseChart()
                start = datetime(2026, 9, 11, 8)
                end = start + timedelta(minutes=count + 2)
                records = [
                    {
                        "bar_time": (start + timedelta(minutes=i)).isoformat(),
                        "close": 100 + i % 17,
                    }
                    for i in range(count)
                ]
                signals = [{
                    "signal_bar_time": records[20]["bar_time"],
                    "signal_bar_close": 103,
                }]
                fills = [{
                    "marker_id": "F",
                    "fill_id": "F",
                    "side": "BUY",
                    "occurred_at": records[30]["bar_time"],
                    "filled_price": 106,
                }]
                widget.set_projection(
                    records,
                    signals,
                    signals,
                    actual_fill_markers=fills,
                    average_price=105,
                    x_range_start=start,
                    x_range_end=end,
                    visible_time_ranges=[
                        (start, start + timedelta(minutes=49)),
                        (start + timedelta(minutes=60), end),
                    ],
                )
                widget.set_live_price_projection(end, 110)
                scales = widget._scale_values()

                series = (
                    widget.close_series
                    + widget.buy_series
                    + widget.sell_series
                    + widget.actual_buy_fill_series
                    + [widget.live_price_point]
                )
                for time_value, value in series:
                    self.assertEqual(
                        widget.position_for(time_value, value),
                        widget.position_for(
                            time_value,
                            value,
                            scales=scales,
                        ),
                    )
                self.assertEqual(
                    widget._live_price_bridge_points(),
                    widget._live_price_bridge_points(scales=scales),
                )

                with patch.object(
                    widget,
                    "_scale_values",
                    wraps=widget._scale_values,
                ) as scale, patch.object(
                    widget,
                    "_time_range",
                    wraps=widget._time_range,
                ) as time_range, patch.object(
                    widget,
                    "_line_segments",
                    wraps=widget._line_segments,
                ) as segments:
                    widget.grab()

                self.assertEqual(1, scale.call_count)
                self.assertEqual(1, time_range.call_count)
                self.assertEqual(1, segments.call_count)
                widget.close()

    def test_six_charts_coalesce_cycle_bar_and_fill_without_losing_unknown_scope(self):
        owner = ChartOwner()
        self.addCleanup(owner.close)
        windows = []
        providers = []
        with patch.object(chart, "_today_trade_date", return_value=TODAY):
            for code in ("005930", "012210", "000660", "005380", "000070", "000080"):
                provider = Mock(return_value=_projection(code))
                window = chart.StockInstanceChartWindow(code, TODAY, owner, projection_provider=provider)
                windows.append(window)
                providers.append(provider)
                self.addCleanup(window.close)
            # A BAR never rebuilds unrelated stocks.
            owner.kiwoom_api.bar_committed.emit({"event_type": "BAR_COMMITTED", "stock_code": "005930", "trade_date": TODAY})
            self.app.processEvents()
            self.assertEqual([2, 1, 1, 1, 1, 1], [p.call_count for p in providers])
            for provider in providers:
                provider.reset_mock()
            for _ in range(3):
                owner.operation_host.operation_cycle_completed.emit(_completed_result())
                for window in windows:
                    owner.kiwoom_api.bar_committed.emit({"event_type": "BAR_COMMITTED", "stock_code": window.stock_code, "trade_date": TODAY})
                    with patch.object(chart, "_open_stock_instance_chart_for_refresh", return_value=window):
                        chart.queue_open_stock_instance_chart_refresh(window.stock_code)
            self.app.processEvents()
            self.assertEqual([1] * 6, [p.call_count for p in providers])
            # Unknown cycle ownership must continue to refresh all six.
            owner.operation_host.operation_cycle_completed.emit(_completed_result())
            self.app.processEvents()
            self.assertEqual([2] * 6, [p.call_count for p in providers])
            for window in windows:
                window.close()
            self.assertEqual(0, owner.operation_host.receivers(owner.operation_host.operation_cycle_completed))
            self.assertEqual(0, owner.kiwoom_api.receivers(owner.kiwoom_api.bar_committed))


    def test_shutdown_clear_cancels_actual_chart_queued_refresh(self):
        owner = ChartOwner()
        self.addCleanup(owner.close)
        with patch.object(chart, "_today_trade_date", return_value=TODAY):
            provider = Mock(return_value=_projection("005930"))
            window = chart.StockInstanceChartWindow("005930", TODAY, owner, projection_provider=provider)
            self.addCleanup(window.close)
            provider.reset_mock()
            window._queue_projection_refresh()
            chart.clear_pending_stock_instance_chart_refreshes()
            self.app.processEvents()
            provider.assert_not_called()
            self.assertFalse(window._bar_committed_refresh_pending)


    def test_shutdown_stops_timer_and_disconnects_without_repeated_global_refresh(self):
        owner = ChartOwner()
        self.addCleanup(owner.close)
        with patch.object(chart, "_today_trade_date", return_value=TODAY):
            window = chart.StockInstanceChartWindow("005930", TODAY, owner, projection_provider=lambda *args: _projection("005930"))
            header_timer = window._operation_header_refresh_timer
            provider = Mock(wraps=window._projection_provider)
            window._projection_provider = provider
            window._queue_projection_refresh()
            owner._main_window_closing = True
            chart._OPEN_STOCK_INSTANCE_CHARTS["005930"] = window
            with patch.object(chart, "_update_common_pnl_refresh_timer") as recalculate:
                window.close()
                self.assertFalse(header_timer.isActive())
                self.app.processEvents()
                recalculate.assert_not_called()
            provider.assert_not_called()
            self.assertNotIn("005930", chart._OPEN_STOCK_INSTANCE_CHARTS)
            self.assertFalse(window._bar_committed_refresh_connected)
            self.assertFalse(window._operation_cycle_refresh_connected)



class MockChartReadReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.now = datetime.fromisoformat("2026-09-07T12:00:00+09:00")
        self.host = MockValidationHost(_Api(), project_root=self.root, now_factory=lambda: self.now)
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)
        document = self.actions.create_waiting_session(_reference())["document"]
        self.sid = document["session"]["validation_session_id"]
        self.target = SimpleNamespace(stock_code="005930", validation_session_id=self.sid, routine_instance_id="A")
        self.owner = SimpleNamespace(mock_validation_host=self.host)
        self.cache = {}

    def test_unchanged_reads_zero_and_official_mutation_refreshes_immediately(self):
        read = lambda: mock_chart._target_document(self.owner, self.target, read_cache=self.cache)
        before = read()
        before_hash = payload_hash(before)
        with patch.object(self.host, "current_session", wraps=self.host.current_session) as reader:
            for _ in range(6):
                self.assertIs(before, read())
                mock_chart._fresh_operation_header_display(self.owner, self.target, read_cache=self.cache)
            reader.assert_not_called()
            self.actions.set_instance_effective_settings("005930", "A", operation_mode="CONTINUOUS")
            reader.reset_mock()
            after = read()
            reader.assert_called_once()
        self.assertGreater(after["revision"], before["revision"])
        self.assertEqual("CONTINUOUS", after["effective_settings_by_instance"]["A"]["operation_mode"])
        self.assertEqual(before_hash, payload_hash(before))
        self.assertEqual(before["reference_snapshot"], after["reference_snapshot"])

    def test_replaced_index_rejects_old_target_and_missing_source_never_reuses(self):
        mock_chart._target_document(self.owner, self.target, read_cache=self.cache)
        self.host.repository._write_current_index("005930", "")
        self.assertIsNone(mock_chart._target_document(self.owner, self.target, read_cache=self.cache))
        path = self.root / "missing.json"
        reader = Mock(return_value=[])
        mock_chart._read_chart_source(self.cache, "missing", (path,), reader)
        mock_chart._read_chart_source(self.cache, "missing", (path,), reader)
        self.assertEqual(2, reader.call_count)

    def test_event_journal_invalidates_independently_of_session_revision(self):
        repo = self.host.repository
        path = repo.root / "events" / f"{self.sid}.json"
        read = lambda: mock_chart._read_chart_source(self.cache, "events", (path,), lambda: repo.read_events(self.sid))
        initial = read()
        revision = repo.read_session(self.sid)["revision"]
        with patch.object(repo, "read_events", wraps=repo.read_events) as reader:
            self.assertIs(initial, read())
            reader.assert_not_called()
            event = dict(initial[0])
            event["event_id"] = "ME-PERFORMANCE-APPEND"
            repo.append_event(event)
            self.assertEqual(len(initial) + 1, len(read()))
            reader.assert_called_once()
        self.assertEqual(revision, repo.read_session(self.sid)["revision"])

if __name__ == "__main__":
    unittest.main()
