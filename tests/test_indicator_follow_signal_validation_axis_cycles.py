# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QFontMetrics, QPixmap
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QScrollArea, QWidget

from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowValidationCompletedCycle,
    IndicatorFollowSignalValidationChartCanvas,
    IndicatorFollowSignalValidationFixedPriceAxis,
    aggregate_completed_cycle_return_percent,
    completed_validation_cycles,
)
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
)
from routines.지표추종매매.routine_validation_replay import ValidationReplayEntry


def _entry(side: str, index: int, time: str) -> ValidationReplayEntry:
    return ValidationReplayEntry(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=time,
        signal=side,
        reason="fixture",
        signal_index=index,
        signal_time=time,
        delay_bar=0,
        matched_groups=["A"],
        details=[],
        trace={"conditions": [], "groups": [], "aggregations": []},
    )


def _candles(closes, times=None):
    resolved_times = times or [
        f"20260914{9 + index // 60:02d}{index % 60:02d}00"
        for index in range(len(closes))
    ]
    return [
        {
            "time": resolved_times[index],
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1,
        }
        for index, close in enumerate(closes)
    ]


class CompletedValidationCycleTest(unittest.TestCase):
    @staticmethod
    def _cycle(number, average, buy_count, estimated_return):
        return IndicatorFollowValidationCompletedCycle(
            cycle_number=number,
            buy_indexes=tuple(range(buy_count)),
            buy_start_index=0,
            buy_end_index=buy_count - 1,
            buy_count=buy_count,
            average_buy_price=average,
            sell_index=buy_count,
            sell_price=average * (1.0 + estimated_return / 100.0),
            estimated_return_percent=estimated_return,
        )

    def test_aggregate_return_uses_completed_cycle_cost_weighting(self):
        cycle = self._cycle
        cases = (
            ((), None),
            ((cycle(1, 100.0, 1, 10.0),), 10.0),
            ((cycle(1, 100.0, 1, 10.0), cycle(2, 200.0, 1, -5.0)), 0.0),
            ((cycle(1, 100.0, 2, 10.0), cycle(2, 200.0, 1, -5.0)), 2.5),
            ((cycle(1, 110.0, 2, 40.0 / 220.0 * 100.0),), 40.0 / 220.0 * 100.0),
            (
                (
                    cycle(1, 100.0, 1, 10.0),
                    cycle(2, 200.0, 2, -5.0),
                    cycle(3, 50.0, 3, 20.0),
                ),
                20.0 / 650.0 * 100.0,
            ),
            ((cycle(1, 100.0, 10, 10.0), cycle(2, 100.0, 1, -50.0)), 50.0 / 1100.0 * 100.0),
        )
        for cycles, expected in cases:
            with self.subTest(cycles=len(cycles), expected=expected):
                actual = aggregate_completed_cycle_return_percent(cycles)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(expected, actual)

    def test_buy_sell_and_multiple_buys_form_completed_cycles(self):
        candles = _candles([100.0, 110.0, 120.0])
        entries = [
            _entry("BUY", 0, candles[0]["time"]),
            _entry("BUY", 1, candles[1]["time"]),
            _entry("SELL", 2, candles[2]["time"]),
        ]
        cycles = completed_validation_cycles(candles, entries)

        self.assertEqual(1, len(cycles))
        self.assertEqual((0, 1), cycles[0].buy_indexes)
        self.assertEqual(2, cycles[0].buy_count)
        self.assertEqual(105.0, cycles[0].average_buy_price)
        self.assertEqual(120.0, cycles[0].sell_price)
        self.assertAlmostEqual((120.0 - 105.0) / 105.0 * 100.0, cycles[0].estimated_return_percent)

    def test_leading_sell_and_unfinished_buy_segment_are_ignored(self):
        candles = _candles([90.0, 100.0, 110.0, 120.0])
        entries = [
            _entry("SELL", 0, candles[0]["time"]),
            _entry("BUY", 1, candles[1]["time"]),
            _entry("SELL", 2, candles[2]["time"]),
            _entry("BUY", 3, candles[3]["time"]),
        ]
        cycles = completed_validation_cycles(candles, entries)

        self.assertEqual(1, len(cycles))
        self.assertEqual((1,), cycles[0].buy_indexes)
        self.assertEqual(2, cycles[0].sell_index)
        self.assertAlmostEqual(10.0, aggregate_completed_cycle_return_percent(cycles))

    def test_trailing_sell_does_not_change_aggregate_return(self):
        candles = _candles([100.0, 110.0, 105.0])
        completed_entries = [
            _entry("BUY", 0, candles[0]["time"]),
            _entry("SELL", 1, candles[1]["time"]),
        ]
        trailing_entries = completed_entries + [
            _entry("SELL", 2, candles[2]["time"]),
        ]

        completed = completed_validation_cycles(candles, completed_entries)
        trailing = completed_validation_cycles(candles, trailing_entries)

        self.assertEqual(completed, trailing)
        self.assertAlmostEqual(
            aggregate_completed_cycle_return_percent(completed),
            aggregate_completed_cycle_return_percent(trailing),
        )

    def test_cycle_crosses_dates_and_numbering_uses_completed_order(self):
        times = [
            "20260911133000",
            "20260914101000",
            "20260914102000",
            "20260914110500",
            "20260914144000",
        ]
        candles = _candles([100.0, 110.0, 120.0, 125.0, 130.0], times)
        entries = [
            _entry("BUY", 0, times[0]),
            _entry("BUY", 1, times[1]),
            _entry("SELL", 2, times[2]),
            _entry("BUY", 3, times[3]),
            _entry("SELL", 4, times[4]),
        ]
        cycles = completed_validation_cycles(candles, entries)

        self.assertEqual([1, 2], [cycle.cycle_number for cycle in cycles])
        self.assertEqual((0, 1), cycles[0].buy_indexes)
        self.assertEqual((3,), cycles[1].buy_indexes)

    def test_same_bar_buy_sell_closes_prior_segment_without_carrying_buy(self):
        candles = _candles([100.0, 110.0, 120.0, 130.0])
        entries = [
            _entry("BUY", 0, candles[0]["time"]),
            _entry("BUY", 2, candles[2]["time"]),
            _entry("SELL", 2, candles[2]["time"]),
            _entry("SELL", 3, candles[3]["time"]),
        ]
        cycles = completed_validation_cycles(candles, entries)

        self.assertEqual(1, len(cycles))
        self.assertEqual((0,), cycles[0].buy_indexes)
        self.assertEqual(100.0, cycles[0].average_buy_price)
        self.assertEqual(2, cycles[0].sell_index)

    def test_cycle_average_matches_existing_average_context(self):
        candles = _candles([100.0, 110.0, 120.0])
        entries = [
            _entry("BUY", 0, candles[0]["time"]),
            _entry("BUY", 1, candles[1]["time"]),
            _entry("SELL", 2, candles[2]["time"]),
        ]
        cycle = completed_validation_cycles(candles, entries)[0]
        context = build_validation_average_price_context(2, "SELL", candles, entries)

        self.assertEqual(context["average_price"], cycle.average_buy_price)
        self.assertEqual(
            context["validation_trace_context"]["contributing_buy_indexes"],
            list(cycle.buy_indexes),
        )


class FixedValidationPriceAxisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def test_axis_stays_fixed_while_logical_time_view_moves(self):
        candles = _candles([1_500_000.0 + index * 100 for index in range(200)])
        sell_index = 90
        markers = [{
            "side": "SELL",
            "evaluation_index": sell_index,
            "evaluation_time": candles[sell_index]["time"],
            "tooltip": "SELL · 09/14 10:30\n▪ 가격비교 1.2%",
        }]
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, markers)
        canvas.set_time_view(0.0, 100.0)
        scroll.setWidget(canvas)
        axis = IndicatorFollowSignalValidationFixedPriceAxis(scroll)
        axis.set_canvas(canvas)
        layout.addWidget(axis)
        layout.addWidget(scroll)
        self.widgets.extend([host, axis, scroll, canvas])
        host.resize(720, 420)
        host.show()
        self.app.processEvents()

        records = axis.price_axis_records()
        self.assertEqual(5, len(records))
        self.assertEqual("1,519,902", records[0]["label"])
        widest = max(
            QFontMetrics(axis.font()).horizontalAdvance(record["label"])
            for record in records
        )
        self.assertGreaterEqual(axis.width(), widest + axis._HORIZONTAL_PADDING * 2)

        viewport_top = scroll.viewport().geometry().top()
        self.assertLessEqual(max(
            abs(axis_record["y"] - (viewport_top + grid_record["y"]))
            for axis_record, grid_record in zip(records, canvas.grid_line_records())
        ), 1)

        axis_x_before = axis.mapToGlobal(QPoint(0, 0)).x()
        candle_x_before = canvas.mapToGlobal(
            QPoint(int(canvas._x_for_index(sell_index)), 0)
        ).x()
        time_tick_index = canvas.time_axis_records()[-1]["index"]
        time_x_before = canvas.mapToGlobal(
            QPoint(int(canvas._x_for_index(time_tick_index)), 0)
        ).x()
        self.assertEqual(0, scroll.horizontalScrollBar().maximum())
        canvas.set_time_view(50.0, 100.0)
        self.app.processEvents()

        self.assertEqual(axis_x_before, axis.mapToGlobal(QPoint(0, 0)).x())
        candle_x_after = canvas.mapToGlobal(
            QPoint(int(canvas._x_for_index(sell_index)), 0)
        ).x()
        time_x_after = canvas.mapToGlobal(
            QPoint(int(canvas._x_for_index(time_tick_index)), 0)
        ).x()
        self.assertLess(candle_x_after, candle_x_before)
        self.assertLess(time_x_after, time_x_before)
        self.assertEqual(
            sell_index,
            canvas._nearest_candle_index(canvas._x_for_index(sell_index)),
        )
        scale = canvas.price_scale()
        self.assertIsNotNone(scale)
        self.assertEqual(
            markers[0]["tooltip"],
            canvas.marker_tooltip_at(
                canvas._x_for_index(sell_index),
                scale.plot_top - 12,
            ),
        )
        canvas.set_selected_index(sell_index)
        self.assertEqual(sell_index, canvas.selected_index)

        pixmap = QPixmap(host.size())
        host.render(pixmap)
        self.assertFalse(pixmap.isNull())


if __name__ == "__main__":
    unittest.main()
