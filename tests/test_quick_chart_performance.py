from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_stock_instance_chart_window as chart
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



if __name__ == "__main__":
    unittest.main()
