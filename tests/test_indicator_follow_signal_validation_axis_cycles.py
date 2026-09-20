# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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
    validation_virtual_fill_price,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_MACD_SIGNAL,
    FAMILY_MOVING_AVERAGE,
    FAMILY_OCR_OSC,
    FAMILY_RSI,
    LOWER_AXIS,
    PRICE_AXIS,
    ValidationFilterDescriptor,
    ValidationIndicatorSeriesCache,
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
    def test_virtual_fill_price_requires_complete_ohlc(self):
        self.assertEqual(
            102.5,
            validation_virtual_fill_price({
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 110.0,
            }),
        )
        self.assertIsNone(validation_virtual_fill_price({"close": 110.0}))

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

    def test_aggregate_prefers_virtual_buy_cost_over_buy_count_proxy(self):
        first = IndicatorFollowValidationCompletedCycle(
            cycle_number=1,
            buy_indexes=(0,),
            buy_start_index=0,
            buy_end_index=0,
            buy_count=1,
            average_buy_price=100.0,
            sell_index=1,
            sell_price=110.0,
            estimated_return_percent=10.0,
            buy_quantity=1,
            buy_cost=100.0,
        )
        second = IndicatorFollowValidationCompletedCycle(
            cycle_number=2,
            buy_indexes=(2,),
            buy_start_index=2,
            buy_end_index=2,
            buy_count=1,
            average_buy_price=100.0,
            sell_index=3,
            sell_price=95.0,
            estimated_return_percent=-5.0,
            buy_quantity=4,
            buy_cost=400.0,
        )
        self.assertAlmostEqual(
            -2.0,
            aggregate_completed_cycle_return_percent((first, second)),
        )

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
        expected_buy = sum(validation_virtual_fill_price(candles[index]) for index in (0, 1)) / 2
        expected_sell = validation_virtual_fill_price(candles[2])
        self.assertEqual(expected_buy, cycles[0].average_buy_price)
        self.assertEqual(expected_sell, cycles[0].sell_price)
        self.assertAlmostEqual(
            (expected_sell - expected_buy) / expected_buy * 100.0,
            cycles[0].estimated_return_percent,
        )

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
        expected_buy = validation_virtual_fill_price(candles[1])
        expected_sell = validation_virtual_fill_price(candles[2])
        self.assertAlmostEqual(
            (expected_sell - expected_buy) / expected_buy * 100.0,
            aggregate_completed_cycle_return_percent(cycles),
        )

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
        self.assertEqual(
            validation_virtual_fill_price(candles[0]),
            cycles[0].average_buy_price,
        )
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


class StaticValidationVisualizationGeometryTest(unittest.TestCase):
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

    @staticmethod
    def _descriptor(identity, family, axis, channels, parameters):
        return ValidationFilterDescriptor(
            identity=identity,
            family=family,
            label=family,
            axis=axis,
            sides=("BUY",),
            series_keys=tuple(channels),
            parameter_json=parameters,
            evidence_keys=(),
            supported_sides=("BUY",),
        )

    def _canvas(self, lower_families=(), *, include_price=True):
        candles = _candles([100.0 + index for index in range(50)])
        descriptors = []
        series = []
        if include_price:
            descriptor = self._descriptor(
                "MA20",
                FAMILY_MOVING_AVERAGE,
                PRICE_AXIS,
                ("MA20",),
                '{"periods":[20]}',
            )
            descriptors.append(descriptor)
            series.append((
                descriptor.identity,
                "MA20",
                tuple(250.0 for _ in candles),
            ))
        for family in lower_families:
            if family == FAMILY_RSI:
                channels = ("RSI",)
                params = '{"period":14}'
                values = {"RSI": tuple(40.0 + index % 20 for index in range(len(candles)))}
            elif family == FAMILY_MACD_SIGNAL:
                channels = ("MACD", "SIGNAL")
                params = '{"fast":12,"signal":9,"slow":26}'
                values = {
                    "MACD": tuple(float(index % 7 - 3) for index in range(len(candles))),
                    "SIGNAL": tuple(float(index % 5 - 2) for index in range(len(candles))),
                }
            else:
                channels = ("OSC",)
                params = '{"fast":12,"signal":9,"slow":26}'
                values = {
                    "OSC": tuple(float(index % 9 - 4) for index in range(len(candles))),
                }
            descriptor = self._descriptor(
                family,
                family,
                LOWER_AXIS,
                channels,
                params,
            )
            descriptors.append(descriptor)
            for channel, channel_values in values.items():
                series.append((descriptor.identity, channel, channel_values))
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            tuple(series),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [],
            visualization_descriptors=tuple(descriptors),
            visualization_cache=cache,
        )
        canvas.resize(800, 500)
        canvas.set_time_view(0.0, 50.0)
        self.widgets.append(canvas)
        return canvas

    def test_lower_h_is_zero_without_lower_filter_and_fixed_for_one_to_three_panes(self):
        no_lower = self._canvas(())
        self.assertEqual(0, no_lower.indicator_total_height)
        self.assertEqual([], no_lower.lower_pane_records())
        self.assertEqual(452, no_lower.price_scale().plot_bottom)

        expected_heights = {
            1: [180],
            2: [90, 90],
            3: [60, 60, 60],
        }
        price_bottoms = []
        families = (FAMILY_RSI, FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC)
        for count in (1, 2, 3):
            canvas = self._canvas(families[:count])
            panes = canvas.lower_pane_records()
            self.assertEqual(180, canvas.indicator_total_height)
            self.assertEqual(
                expected_heights[count],
                [pane["height"] for pane in panes],
            )
            price_bottoms.append(canvas.price_scale().plot_bottom)
        self.assertEqual([248, 248, 248], price_bottoms)

    def test_candle_render_is_clipped_to_price_plot_before_lower_panes(self):
        candles = [{
            "time": "20260918090000",
            "open": 100.0,
            "high": 101.0,
            "low": 50.0,
            "close": 99.0,
            "volume": 1,
        }]
        descriptor = self._descriptor(
            "RSI14",
            FAMILY_RSI,
            LOWER_AXIS,
            ("RSI",),
            '{"period":14}',
        )
        cache = ValidationIndicatorSeriesCache(
            1,
            (("RSI14", "RSI", (None,)),),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [],
            visualization_descriptors=(descriptor,),
            visualization_cache=cache,
        )
        canvas.resize(800, 500)
        canvas.set_time_view(0.0, 1.0)
        canvas.set_price_view(98.0, 102.0)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        scale = canvas.price_scale()
        pane = canvas.lower_pane_records()[0]
        self.assertLess(scale.plot_bottom, pane["top"])
        self.assertGreater(
            scale.y_for_price(candles[0]["low"]),
            pane["top"],
        )

        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        image = pixmap.toImage()
        candle_x = round(canvas._x_for_index(0))
        lower_y = round((pane["top"] + pane["bottom"]) / 2)
        reference_x = min(canvas.width() - canvas._RIGHT - 4, candle_x + 40)

        self.assertEqual(
            image.pixelColor(reference_x, lower_y),
            image.pixelColor(candle_x, lower_y),
        )

    def test_offscreen_marker_does_not_paint_or_hit_but_in_bounds_marker_does(self):
        candles = [
            {
                "time": "20260918090000",
                "open": 100.0,
                "high": 101.0,
                "low": 97.0,
                "close": 99.0,
                "volume": 1,
            },
            {
                "time": "20260918090100",
                "open": 100.0,
                "high": 101.0,
                "low": 98.25,
                "close": 100.0,
                "volume": 1,
            },
        ]
        markers = [
            {
                "side": "BUY",
                "evaluation_index": 0,
                "evaluation_time": candles[0]["time"],
                "tooltip": "offscreen BUY",
            },
            {
                "side": "BUY",
                "evaluation_index": 1,
                "evaluation_time": candles[1]["time"],
                "tooltip": "visible BUY",
            },
        ]
        descriptor = self._descriptor(
            "RSI14",
            FAMILY_RSI,
            LOWER_AXIS,
            ("RSI",),
            '{"period":14}',
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (("RSI14", "RSI", (None, None)),),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            markers,
            visualization_descriptors=(descriptor,),
            visualization_cache=cache,
        )
        canvas.resize(800, 500)
        canvas.set_time_view(0.0, 2.0)
        canvas.set_price_view(98.0, 102.0)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        scale = canvas.price_scale()
        pane = canvas.lower_pane_records()[0]
        offscreen_x = canvas._x_for_index(0)
        offscreen_y = canvas._marker_y(0, "BUY", scale)
        visible_x = canvas._x_for_index(1)
        visible_y = canvas._marker_y(1, "BUY", scale)
        self.assertGreater(offscreen_y, scale.plot_bottom)
        self.assertTrue(pane["top"] <= offscreen_y <= pane["bottom"])
        self.assertTrue(scale.plot_top <= visible_y <= scale.plot_bottom)
        self.assertLessEqual(scale.plot_bottom - visible_y, 7)

        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        image = pixmap.toImage()
        reference_x = round((offscreen_x + visible_x) / 2)
        self.assertEqual(
            image.pixelColor(reference_x, round(offscreen_y)),
            image.pixelColor(round(offscreen_x), round(offscreen_y)),
        )
        self.assertNotEqual(
            image.pixelColor(reference_x, round(visible_y)),
            image.pixelColor(round(visible_x), round(visible_y)),
        )

        self.assertIsNone(canvas._marker_at(offscreen_x, offscreen_y))
        self.assertIsNone(canvas._marker_at(visible_x, scale.plot_bottom + 1))
        self.assertEqual(
            markers[1],
            canvas._marker_at(visible_x, visible_y),
        )

    def test_ocr_osc_uses_histogram_while_other_lower_series_remain_lines(self):
        canvas = self._canvas(
            (
                FAMILY_RSI,
                FAMILY_MACD_SIGNAL,
                FAMILY_OCR_OSC,
            ),
            include_price=False,
        )
        canvas.show()
        self.app.processEvents()
        pixmap = QPixmap(canvas.size())

        self.assertTrue(hasattr(canvas, "_draw_series_histogram"))
        with patch.object(
            canvas,
            "_draw_series_histogram",
            wraps=canvas._draw_series_histogram,
        ) as histogram, patch.object(
            canvas,
            "_draw_series_line",
            wraps=canvas._draw_series_line,
        ) as line:
            canvas._static_chart_cache = None
            canvas._static_chart_cache_key = None
            canvas.render(pixmap)

        self.assertEqual(1, histogram.call_count)
        self.assertEqual(3, line.call_count)
        self.assertFalse(pixmap.isNull())

        pane = next(
            item
            for item in canvas.lower_pane_records()
            if item["family"] == FAMILY_OCR_OSC
        )
        scale = canvas._lower_scale(
            FAMILY_OCR_OSC,
            int(pane["top"]),
            int(pane["bottom"]),
        )
        zero_y = scale.y_for_price(0.0)
        self.assertGreater(scale.y_for_price(-4.0), zero_y)
        self.assertLess(scale.y_for_price(4.0), zero_y)

        image = pixmap.toImage()
        half_bar = canvas._candle_body_width() / 2
        rendered_colors = {}
        for index, value in ((0, -4.0), (8, 4.0)):
            x = round(canvas._x_for_index(index))
            value_y = scale.y_for_price(value)
            y = round((zero_y + value_y) / 2)
            outside_x = round(x + half_bar + 1)
            center_color = image.pixelColor(x, y)
            rendered_colors[value] = center_color.name()
            self.assertNotEqual(
                center_color,
                image.pixelColor(outside_x, y),
            )
        self.assertEqual("#3b82f6", rendered_colors[-4.0])
        self.assertEqual("#fb7185", rendered_colors[4.0])

    def test_legend_swatches_match_rendered_line_pens_and_exclude_ocr_histogram(self):
        canvas = self._canvas(
            (
                FAMILY_RSI,
                FAMILY_MACD_SIGNAL,
                FAMILY_OCR_OSC,
            )
        )

        def assert_legend_matches_rendered_series():
            records = canvas.legend_records()
            by_family = {record["family"]: record for record in records}
            style_by_series = {
                (record["identity"], record["channel"]): record
                for record in canvas.visualization_style_records()
            }

            self.assertEqual(("MA20",), tuple(
                swatch["channel"]
                for swatch in by_family[FAMILY_MOVING_AVERAGE]["swatches"]
            ))
            self.assertEqual(("RSI",), tuple(
                swatch["channel"]
                for swatch in by_family[FAMILY_RSI]["swatches"]
            ))
            self.assertEqual(("MACD", "SIGNAL"), tuple(
                swatch["channel"]
                for swatch in by_family[FAMILY_MACD_SIGNAL]["swatches"]
            ))
            self.assertEqual((), by_family[FAMILY_OCR_OSC]["swatches"])

            for record in records:
                for swatch in record["swatches"]:
                    style = style_by_series[(record["identity"], swatch["channel"])]
                    self.assertEqual(style["color"], swatch["color"])
                    self.assertEqual(style["alpha"], swatch["alpha"])
                    self.assertEqual(style["pen_width"], swatch["pen_width"])
                    self.assertEqual(style["pen_style"], swatch["pen_style"])

        assert_legend_matches_rendered_series()

        marker_key = (0, "BUY")
        canvas._visualization_active_by_marker[marker_key] = tuple(
            descriptor.identity
            for descriptor in canvas._visualization_descriptors
        )
        canvas._set_hover_marker_key(marker_key)
        self.assertTrue(all(
            record["state"] == "ACTIVE"
            for record in canvas.visualization_style_records()
        ))
        assert_legend_matches_rendered_series()

        canvas._visualization_active_by_marker[marker_key] = ()
        self.assertTrue(all(
            record["state"] == "INACTIVE"
            for record in canvas.visualization_style_records()
        ))
        assert_legend_matches_rendered_series()

    def test_price_legend_paints_actual_series_color_swatch(self):
        canvas = self._canvas(())
        canvas.show()
        self.app.processEvents()

        record = next(
            item
            for item in canvas.legend_records()
            if item["family"] == FAMILY_MOVING_AVERAGE
        )
        expected_color = record["swatches"][0]["color"]
        scale = canvas.price_scale()
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        image = pixmap.toImage()

        swatch_left = round(canvas.plot_left + 4)
        swatch_center_y = round(
            scale.plot_top + 2 + (QFontMetrics(canvas.font()).height() + 2) / 2
        )
        rendered = {
            image.pixelColor(x, y).name()
            for x in range(swatch_left, swatch_left + 13)
            for y in range(swatch_center_y - 2, swatch_center_y + 3)
        }
        self.assertIn(expected_color, rendered)

    def test_ocr_criterion_line_gets_only_its_actual_line_swatch(self):
        candles = _candles([100.0 + index for index in range(10)])
        descriptor = self._descriptor(
            "OCR_LIMIT",
            FAMILY_OCR_OSC,
            LOWER_AXIS,
            ("OSC", "CRITERION"),
            '{"criterion_label":"OCR 0 이상","fast":12,"signal":9,"slow":26}',
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (
                (descriptor.identity, "OSC", tuple(float(index - 5) for index in range(10))),
                (descriptor.identity, "CRITERION", tuple(0.0 for _ in candles)),
            ),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [],
            visualization_descriptors=(descriptor,),
            visualization_cache=cache,
        )
        self.widgets.append(canvas)

        record = canvas.legend_records()[0]

        self.assertEqual("OCR 0 이상", record["caption"])
        self.assertEqual(
            ("CRITERION",),
            tuple(swatch["channel"] for swatch in record["swatches"]),
        )

    def test_price_overlay_expands_price_axis_and_static_series_render(self):
        canvas = self._canvas((FAMILY_RSI, FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC))
        scale = canvas.price_scale()
        self.assertIsNotNone(scale)
        self.assertEqual(250.0, scale.maximum)

        records = canvas.visualization_series_records()
        self.assertEqual(
            {"MA20", "RSI", "MACD", "SIGNAL", "OSC"},
            {record["channel"] for record in records},
        )

        canvas.show()
        self.app.processEvents()
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())


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
        self.assertEqual("1,509,902", records[0]["label"])
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

        self.assertEqual(
            [record["label"] for record in records],
            [record["label"] for record in axis.price_axis_records()],
        )
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
                canvas._marker_y(sell_index, "SELL", scale),
            ),
        )
        canvas.set_selected_index(sell_index)
        self.assertEqual(sell_index, canvas.selected_index)

        pixmap = QPixmap(host.size())
        host.render(pixmap)
        self.assertFalse(pixmap.isNull())


if __name__ == "__main__":
    unittest.main()
