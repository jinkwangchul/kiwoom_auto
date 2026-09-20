# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationChartCanvas,
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


def _candles(count: int) -> list[dict]:
    return [
        {
            "time": f"2026091809{index:02d}00",
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.0 + index,
            "volume": 1,
        }
        for index in range(count)
    ]


class ValidationEvidenceInteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()

    def test_filter_series_hover_shows_condition_value_and_precedes_candle(self):
        for target in (
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText",
        ):
            active_patch = patch(target)
            active_patch.start()
            self.addCleanup(active_patch.stop)

        candles = _candles(20)
        ma_descriptor = ValidationFilterDescriptor(
            identity="HOVER-MA",
            family=FAMILY_MOVING_AVERAGE,
            label="MA20",
            axis=PRICE_AXIS,
            sides=("BUY",),
            series_keys=("MA20",),
            parameter_json=(
                '{"criterion_label":"현재가 20이평 상향돌파","periods":[20]}'
            ),
            evidence_keys=("MA-KEY",),
            supported_sides=("BUY",),
        )
        rsi_descriptor = ValidationFilterDescriptor(
            identity="HOVER-RSI",
            family=FAMILY_RSI,
            label="RSI",
            axis=LOWER_AXIS,
            sides=("BUY",),
            series_keys=("RSI",),
            parameter_json=(
                '{"criterion_label":"RSI(14) 45 이하","period":14}'
            ),
            evidence_keys=("RSI-KEY",),
            supported_sides=("BUY",),
        )
        osc_descriptor = ValidationFilterDescriptor(
            identity="HOVER-OSC",
            family=FAMILY_OCR_OSC,
            label="OCR / OSC",
            axis=LOWER_AXIS,
            sides=("BUY",),
            series_keys=("OSC",),
            parameter_json=(
                '{"criterion_label":"OCR 상승전환","fast":12,"slow":26,"signal":9}'
            ),
            evidence_keys=("OSC-KEY",),
            supported_sides=("BUY",),
        )
        ma_values = tuple(100.0 + index for index in range(len(candles)))
        rsi_values = tuple(45.0 for _ in candles)
        osc_values = tuple(
            float((index % 9) - 4)
            for index in range(len(candles))
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (
                (ma_descriptor.identity, "MA20", ma_values),
                (rsi_descriptor.identity, "RSI", rsi_values),
                (osc_descriptor.identity, "OSC", osc_values),
            ),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [],
            visualization_descriptors=(
                ma_descriptor,
                rsi_descriptor,
                osc_descriptor,
            ),
            visualization_cache=cache,
        )
        canvas.resize(900, 520)
        canvas.set_time_view(0.0, float(len(candles)))
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        hover_index = 10
        hover_x = canvas._x_for_index(hover_index)
        price_scale = canvas.price_scale()
        price_y = price_scale.y_for_price(ma_values[hover_index])
        price_tooltip = canvas.series_tooltip_at(hover_x, price_y)
        self.assertIn("현재가 20이평 상향돌파", price_tooltip)
        self.assertIn("20이평:", price_tooltip)
        self.assertIn("적용: 매수", price_tooltip)

        pane_by_family = {
            pane["family"]: pane
            for pane in canvas.lower_pane_records()
        }
        rsi_pane = pane_by_family[FAMILY_RSI]
        rsi_scale = canvas._lower_scale(
            FAMILY_RSI,
            rsi_pane["top"],
            rsi_pane["bottom"],
        )
        rsi_tooltip = canvas.series_tooltip_at(
            hover_x,
            rsi_scale.y_for_price(45.0),
        )
        self.assertIn("RSI(14) 45 이하", rsi_tooltip)
        self.assertIn("RSI: 45", rsi_tooltip)

        osc_index = 8
        osc_x = canvas._x_for_index(osc_index)
        osc_value = osc_values[osc_index]
        osc_pane = pane_by_family[FAMILY_OCR_OSC]
        osc_scale = canvas._lower_scale(
            FAMILY_OCR_OSC,
            osc_pane["top"],
            osc_pane["bottom"],
        )
        osc_mid_y = (
            osc_scale.y_for_price(0.0)
            + osc_scale.y_for_price(osc_value)
        ) / 2.0
        osc_tooltip = canvas.series_tooltip_at(osc_x, osc_mid_y)
        self.assertIn("OCR 상승전환", osc_tooltip)
        self.assertIn("OCR:", osc_tooltip)

        with patch.object(
            canvas,
            "candle_tooltip_at",
            side_effect=AssertionError(
                "filter series tooltip must precede candle tooltip"
            ),
        ), patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
        ) as show_tooltip:
            QTest.mouseMove(
                canvas,
                QPoint(round(hover_x), round(price_y)),
            )
            self.app.processEvents()
        self.assertIn(
            "현재가 20이평 상향돌파",
            show_tooltip.call_args.args[1],
        )

    def test_hover_pin_blank_release_and_visual_states(self):
        for target in (
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText",
        ):
            active_patch = patch(target)
            active_patch.start()
            self.addCleanup(active_patch.stop)

        candles = _candles(40)
        active_descriptor = ValidationFilterDescriptor(
            identity="ACTIVE-MA",
            family=FAMILY_MOVING_AVERAGE,
            label="MA20",
            axis=PRICE_AXIS,
            sides=("SELL",),
            series_keys=("MA20",),
            parameter_json='{"periods":[20]}',
            evidence_keys=("ACTIVE-KEY",),
            supported_sides=("SELL",),
        )
        inactive_descriptor = ValidationFilterDescriptor(
            identity="INACTIVE-RSI",
            family=FAMILY_RSI,
            label="RSI",
            axis=LOWER_AXIS,
            sides=("SELL",),
            series_keys=("RSI",),
            parameter_json='{"period":14}',
            evidence_keys=("OTHER-KEY",),
            supported_sides=("SELL",),
        )
        unsupported_descriptor = ValidationFilterDescriptor(
            identity="UNSUPPORTED-MA",
            family=FAMILY_MOVING_AVERAGE,
            label="MA30",
            axis=PRICE_AXIS,
            sides=("SELL",),
            series_keys=("MA30",),
            parameter_json='{"periods":[30]}',
            evidence_keys=("UNSUPPORTED-KEY",),
            unsupported_sides=("SELL",),
            supported=False,
            unavailable_reason="VALIDATION_FILTER_NOT_EVALUATED",
        )
        error_descriptor = ValidationFilterDescriptor(
            identity="ERROR-MA",
            family=FAMILY_MOVING_AVERAGE,
            label="MA40",
            axis=PRICE_AXIS,
            sides=("SELL",),
            series_keys=("MA40",),
            parameter_json='{"periods":[40]}',
            evidence_keys=("ERROR-KEY",),
            supported_sides=("SELL",),
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (
                (
                    active_descriptor.identity,
                    "MA20",
                    tuple(100.0 + index for index in range(len(candles))),
                ),
                (
                    inactive_descriptor.identity,
                    "RSI",
                    tuple(45.0 for _ in candles),
                ),
                (
                    unsupported_descriptor.identity,
                    "MA30",
                    tuple(95.0 + index for index in range(len(candles))),
                ),
            ),
        )
        marker_index = 10
        marker = {
            "side": "SELL",
            "evaluation_index": marker_index,
            "evaluation_time": candles[marker_index]["time"],
            "tooltip": "SELL evidence",
        }
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [marker],
            visualization_descriptors=(
                active_descriptor,
                inactive_descriptor,
                unsupported_descriptor,
                error_descriptor,
            ),
            visualization_cache=cache,
            visualization_active_by_marker={
                (marker_index, "SELL"): (active_descriptor.identity,),
            },
        )
        canvas.resize(900, 520)
        canvas.set_time_view(0.0, 40.0)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        def states():
            return {
                record["identity"]: record["state"]
                for record in canvas.visualization_style_records()
            }

        self.assertEqual(
            {
                "ACTIVE-MA": "NORMAL",
                "INACTIVE-RSI": "NORMAL",
                "UNSUPPORTED-MA": "UNSUPPORTED",
                "ERROR-MA": "ERROR",
            },
            states(),
        )

        scale = canvas.price_scale()
        marker_pos = QPoint(
            round(canvas._x_for_index(marker_index)),
            round(canvas._marker_y(marker_index, "SELL", scale)),
        )
        QTest.mouseMove(canvas, marker_pos)
        self.app.processEvents()
        self.assertEqual((marker_index, "SELL"), canvas.hovered_marker_key)
        self.assertEqual(
            {
                "ACTIVE-MA": "ACTIVE",
                "INACTIVE-RSI": "INACTIVE",
                "UNSUPPORTED-MA": "UNSUPPORTED",
                "ERROR-MA": "ERROR",
            },
            states(),
        )
        records = {
            record["identity"]: record
            for record in canvas.visualization_style_records()
        }
        self.assertEqual(2, records["ACTIVE-MA"]["pen_width"])
        self.assertLess(records["INACTIVE-RSI"]["alpha"], 255)
        self.assertIn("[미지원]", canvas._styled_descriptor_caption(
            unsupported_descriptor
        ))
        self.assertIn("[오류]", canvas._styled_descriptor_caption(
            error_descriptor
        ))

        QTest.mouseClick(canvas, Qt.LeftButton, pos=marker_pos)
        self.app.processEvents()
        self.assertEqual((marker_index, "SELL"), canvas.pinned_marker_key)

        blank_pos = QPoint(
            round(canvas._x_for_index(20)),
            round((scale.plot_top + scale.plot_bottom) / 2),
        )
        QTest.mouseMove(canvas, blank_pos)
        self.app.processEvents()
        self.assertIsNone(canvas.hovered_marker_key)
        self.assertEqual((marker_index, "SELL"), canvas.effective_marker_key)
        self.assertEqual("ACTIVE", states()["ACTIVE-MA"])
        self.assertEqual("INACTIVE", states()["INACTIVE-RSI"])

        QTest.mouseClick(canvas, Qt.LeftButton, pos=blank_pos)
        self.app.processEvents()
        self.assertIsNone(canvas.pinned_marker_key)
        self.assertIsNone(canvas.effective_marker_key)
        self.assertEqual("NORMAL", states()["ACTIVE-MA"])
        self.assertEqual("NORMAL", states()["INACTIVE-RSI"])
        self.assertEqual("UNSUPPORTED", states()["UNSUPPORTED-MA"])
        self.assertEqual("ERROR", states()["ERROR-MA"])

    def test_mouse_crosshair_reuses_static_chart_cache_until_view_changes(self):
        candles = _candles(100)
        descriptor = ValidationFilterDescriptor(
            identity="CACHE-MA",
            family=FAMILY_MOVING_AVERAGE,
            label="MA20",
            axis=PRICE_AXIS,
            sides=("BUY",),
            series_keys=("MA20",),
            parameter_json='{"periods":[20]}',
            evidence_keys=("CACHE-MA",),
            supported_sides=("BUY",),
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            ((
                descriptor.identity,
                "MA20",
                tuple(100.0 + index for index in range(len(candles))),
            ),),
        )
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [],
            visualization_descriptors=(descriptor,),
            visualization_cache=cache,
        )
        canvas.resize(900, 520)
        canvas.set_time_view(0.0, 100.0)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        with patch.object(
            canvas,
            "_paint_static_chart",
            wraps=canvas._paint_static_chart,
        ) as static_paint:
            canvas._static_chart_cache = None
            canvas._static_chart_cache_key = None
            canvas.update()
            self.app.processEvents()
            initial_calls = static_paint.call_count
            self.assertGreaterEqual(initial_calls, 1)

            for index in (10, 20, 30, 40):
                QTest.mouseMove(
                    canvas,
                    QPoint(
                        round(canvas._x_for_index(index)),
                        round((canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2),
                    ),
                )
                self.app.processEvents()
            self.assertEqual(initial_calls, static_paint.call_count)

            canvas.set_time_view(10.0, 50.0)
            self.app.processEvents()
            self.assertGreater(static_paint.call_count, initial_calls)

    def test_signal_markers_follow_signal_candle_high_and_low(self):
        candles = _candles(10)
        marker_index = 5
        markers = [
            {
                "side": "BUY",
                "evaluation_index": marker_index,
                "evaluation_time": candles[marker_index]["time"],
                "tooltip": "BUY evidence",
            },
            {
                "side": "SELL",
                "evaluation_index": marker_index,
                "evaluation_time": candles[marker_index]["time"],
                "tooltip": "SELL evidence",
            },
        ]
        canvas = IndicatorFollowSignalValidationChartCanvas(candles, markers)
        canvas.resize(700, 420)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        scale = canvas.price_scale()
        self.assertIsNotNone(scale)
        sell_y = canvas._marker_y(marker_index, "SELL", scale)
        buy_y = canvas._marker_y(marker_index, "BUY", scale)
        self.assertAlmostEqual(
            scale.y_for_price(candles[marker_index]["high"]) - 12,
            sell_y,
        )
        self.assertAlmostEqual(
            scale.y_for_price(candles[marker_index]["low"]) + 12,
            buy_y,
        )
        self.assertNotEqual(scale.plot_top - 12, sell_y)
        self.assertNotEqual(scale.plot_bottom + 12, buy_y)

    def test_crosshair_snaps_uses_pane_units_and_pin_reprojects(self):
        for target in (
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText",
        ):
            active_patch = patch(target)
            active_patch.start()
            self.addCleanup(active_patch.stop)

        candles = _candles(40)
        descriptors = (
            ValidationFilterDescriptor(
                identity="MA20",
                family=FAMILY_MOVING_AVERAGE,
                label="MA20",
                axis=PRICE_AXIS,
                sides=("SELL",),
                series_keys=("MA20",),
                parameter_json='{"periods":[20]}',
                evidence_keys=("MA20-KEY",),
                supported_sides=("SELL",),
            ),
            ValidationFilterDescriptor(
                identity="RSI14",
                family=FAMILY_RSI,
                label="RSI",
                axis=LOWER_AXIS,
                sides=("SELL",),
                series_keys=("RSI",),
                parameter_json='{"period":14}',
                evidence_keys=("RSI-KEY",),
                supported_sides=("SELL",),
            ),
            ValidationFilterDescriptor(
                identity="MACD",
                family=FAMILY_MACD_SIGNAL,
                label="MACD / Signal",
                axis=LOWER_AXIS,
                sides=("SELL",),
                series_keys=("MACD", "SIGNAL"),
                parameter_json='{"fast":12,"slow":26,"signal":9}',
                evidence_keys=("MACD-KEY",),
                supported_sides=("SELL",),
            ),
            ValidationFilterDescriptor(
                identity="OSC",
                family=FAMILY_OCR_OSC,
                label="OCR / OSC",
                axis=LOWER_AXIS,
                sides=("SELL",),
                series_keys=("OSC",),
                parameter_json='{"fast":12,"slow":26,"signal":9}',
                evidence_keys=("OSC-KEY",),
                supported_sides=("SELL",),
            ),
            ValidationFilterDescriptor(
                identity="UNSUPPORTED",
                family=FAMILY_MOVING_AVERAGE,
                label="MA30",
                axis=PRICE_AXIS,
                sides=("SELL",),
                series_keys=("MA30",),
                parameter_json='{"periods":[30]}',
                evidence_keys=("UNSUPPORTED-KEY",),
                unsupported_sides=("SELL",),
                supported=False,
                unavailable_reason="VALIDATION_FILTER_NOT_EVALUATED",
            ),
            ValidationFilterDescriptor(
                identity="ERROR",
                family=FAMILY_MOVING_AVERAGE,
                label="MA40",
                axis=PRICE_AXIS,
                sides=("SELL",),
                series_keys=("MA40",),
                parameter_json='{"periods":[40]}',
                evidence_keys=("ERROR-KEY",),
                supported_sides=("SELL",),
            ),
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (
                ("MA20", "MA20", tuple(100.0 + index for index in range(40))),
                ("RSI14", "RSI", tuple(50.0 for _ in range(40))),
                ("MACD", "MACD", tuple(float(index % 7 - 3) for index in range(40))),
                ("MACD", "SIGNAL", tuple(float(index % 5 - 2) for index in range(40))),
                ("OSC", "OSC", tuple(float(index % 9 - 4) for index in range(40))),
                ("UNSUPPORTED", "MA30", tuple(95.0 + index for index in range(40))),
            ),
        )
        marker_index = 10
        marker = {
            "side": "SELL",
            "evaluation_index": marker_index,
            "evaluation_time": candles[marker_index]["time"],
            "tooltip": "SELL evidence",
        }
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles,
            [marker],
            visualization_descriptors=descriptors,
            visualization_cache=cache,
            visualization_active_by_marker={
                (marker_index, "SELL"): ("MA20", "MACD"),
            },
        )
        canvas.resize(900, 560)
        canvas.set_time_view(0.0, 40.0)
        canvas.show()
        self.widgets.append(canvas)
        self.app.processEvents()

        price_scale = canvas.price_scale()
        hover_index = 7
        hover_x = round(
            canvas._x_for_index(hover_index)
            + canvas.pixels_per_candle * 0.2
        )
        price_y = round(
            (price_scale.plot_top + price_scale.plot_bottom) / 2
        )
        QTest.mouseMove(canvas, QPoint(hover_x, price_y))
        self.app.processEvents()
        crosshair = canvas.crosshair_records()
        self.assertEqual(hover_index, crosshair["index"])
        self.assertAlmostEqual(
            canvas._x_for_index(hover_index),
            crosshair["x"],
        )
        self.assertEqual("PRICE", crosshair["horizontal"]["pane"])
        self.assertIsInstance(crosshair["horizontal"]["value"], float)

        pane_by_family = {
            pane["family"]: pane
            for pane in canvas.lower_pane_records()
        }
        for family in (FAMILY_RSI, FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC):
            pane = pane_by_family[family]
            pane_y = round((pane["top"] + pane["bottom"]) / 2)
            QTest.mouseMove(canvas, QPoint(hover_x, pane_y))
            self.app.processEvents()
            horizontal = canvas.crosshair_records()["horizontal"]
            self.assertEqual(family, horizontal["pane"])
            self.assertIsInstance(horizontal["value"], float)

        rsi_pane = pane_by_family[FAMILY_RSI]
        rsi_scale = canvas._lower_scale(
            FAMILY_RSI,
            rsi_pane["top"],
            rsi_pane["bottom"],
        )
        rsi_y = round((rsi_scale.plot_top + rsi_scale.plot_bottom) / 2)
        QTest.mouseMove(canvas, QPoint(hover_x, rsi_y))
        self.app.processEvents()
        rsi_value = canvas.crosshair_records()["horizontal"]["value"]
        self.assertLessEqual(abs(rsi_value - 50.0), 1.0)

        marker_pos = QPoint(
            round(canvas._x_for_index(marker_index)),
            round(canvas._marker_y(marker_index, "SELL", price_scale)),
        )
        QTest.mouseClick(canvas, Qt.LeftButton, pos=marker_pos)
        self.app.processEvents()
        self.assertEqual(marker_index, canvas.crosshair_index)
        self.assertTrue(canvas.crosshair_records()["pinned"])

        value_records = canvas.crosshair_value_records()
        states = {
            record.get("identity"): record["state"]
            for record in value_records
            if record["kind"] == "FILTER"
        }
        self.assertEqual("ACTIVE", states["MA20"])
        self.assertEqual("ACTIVE", states["MACD"])
        self.assertEqual("INACTIVE", states["RSI14"])
        self.assertEqual("UNSUPPORTED", states["UNSUPPORTED"])
        self.assertEqual("ERROR", states["ERROR"])
        self.assertFalse(hasattr(canvas, "crosshair_value_lines"))

        x_before = canvas.crosshair_records()["x"]
        canvas.set_time_view(5.0, 20.0)
        self.app.processEvents()
        self.assertEqual(marker_index, canvas.crosshair_index)
        self.assertAlmostEqual(
            canvas._x_for_index(marker_index),
            canvas.crosshair_records()["x"],
        )
        self.assertNotEqual(x_before, canvas.crosshair_records()["x"])

        canvas.resize(1000, 620)
        self.app.processEvents()
        self.assertAlmostEqual(
            canvas._x_for_index(marker_index),
            canvas.crosshair_records()["x"],
        )

        canvas.set_time_view(20.0, 20.0)
        self.app.processEvents()
        self.assertEqual(marker_index, canvas.crosshair_index)
        self.assertFalse(canvas.crosshair_records()["visible"])
        canvas.set_time_view(0.0, 20.0)
        self.app.processEvents()
        self.assertTrue(canvas.crosshair_records()["visible"])
        self.assertAlmostEqual(
            canvas._x_for_index(marker_index),
            canvas.crosshair_records()["x"],
        )

        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())


    def test_crosshair_and_pin_state_are_canvas_local(self):
        for target in (
            "gui_indicator_follow_signal_validation_window.QToolTip.showText",
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText",
        ):
            active_patch = patch(target)
            active_patch.start()
            self.addCleanup(active_patch.stop)

        candles = _candles(20)
        descriptor = ValidationFilterDescriptor(
            identity="MA20",
            family=FAMILY_MOVING_AVERAGE,
            label="MA20",
            axis=PRICE_AXIS,
            sides=("SELL",),
            series_keys=("MA20",),
            parameter_json='{"periods":[20]}',
            evidence_keys=("K",),
            supported_sides=("SELL",),
        )
        cache = ValidationIndicatorSeriesCache(
            len(candles),
            (("MA20", "MA20", tuple(100.0 + index for index in range(20))),),
        )
        marker = {
            "side": "SELL",
            "evaluation_index": 5,
            "evaluation_time": candles[5]["time"],
            "tooltip": "SELL evidence",
        }
        canvases = [
            IndicatorFollowSignalValidationChartCanvas(
                candles,
                [marker],
                visualization_descriptors=(descriptor,),
                visualization_cache=cache,
                visualization_active_by_marker={(5, "SELL"): ("MA20",)},
            )
            for _ in range(2)
        ]
        for canvas in canvases:
            canvas.resize(700, 420)
            canvas.show()
            self.widgets.append(canvas)
        self.app.processEvents()

        scale = canvases[0].price_scale()
        marker_pos = QPoint(
            round(canvases[0]._x_for_index(5)),
            round(canvases[0]._marker_y(5, "SELL", scale)),
        )
        QTest.mouseClick(canvases[0], Qt.LeftButton, pos=marker_pos)
        self.app.processEvents()

        self.assertEqual((5, "SELL"), canvases[0].pinned_marker_key)
        self.assertEqual(5, canvases[0].crosshair_index)
        self.assertIsNone(canvases[1].pinned_marker_key)
        self.assertIsNone(canvases[1].crosshair_index)



if __name__ == "__main__":
    unittest.main()
