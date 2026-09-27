from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_stock_instance_chart_window as chart


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


if __name__ == "__main__":
    unittest.main()
