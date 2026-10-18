"""Compare the per-paint OCR plan with the unchanged slow matching path."""
from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QImage, QPainter, QPen

import gui_indicator_follow_signal_validation_v5_window as window
from tests.test_indicator_follow_signal_validation_v5_baseline import descriptor
from tests.qt_test_support import ensure_qapplication


def _case():
    ensure_qapplication()
    source = Path(__file__).resolve().parents[1] / (
        "runtime/signal_validation_v2_history_cache/005930_3_10060.json"
    )
    candles = json.loads(source.read_text(encoding="utf-8"))["candles"][-5000:]
    count = len(candles)
    values = tuple(float((-1, -2, 1, 2, 3, 2, 1, -2)[i % 8]) for i in range(count))
    owners = tuple(
        descriptor(
            f"{side}-{kind}", window.FAMILY_OCR_OSC, f"{side} {kind}",
            window.LOWER_AXIS, side, "OSC",
            {"condition": condition},
        )
        for side, turn, comparison in (
            ("BUY", "TURN_UP", "LESS_THAN"),
            ("SELL", "TURN_DOWN", "GREATER_THAN"),
        )
        for kind, condition in (
            ("turn", {"operator": turn, "target": "OCR"}),
            ("threshold", {
                "operator": comparison, "target": "OSC", "threshold": 0,
                "bar_offset": 1,
            }),
        )
    )
    presentations = tuple(
        window.ValidationLowerFilterPresentation(
            key=f"{side}:ocr", side=side, source_path=f"{side}.ocr",
            filter_token="OCR", pane_family=window.FAMILY_OCR_OSC,
            display_order=(0, 1), descriptor_identities=identities,
            component_descriptor_identities=identities,
            component_labels=("turn", "threshold"),
        )
        for side, identities in (
            ("BUY", ("BUY-turn", "BUY-threshold")),
            ("SELL", ("SELL-turn", "SELL-threshold")),
        )
    )
    canvas = window.IndicatorFollowSignalValidationChartCanvas(
        candles, [], visualization_descriptors=owners,
        visualization_cache=window.ValidationIndicatorSeriesCache(
            count, tuple((owner.identity, "OSC", values) for owner in owners),
        ), lower_filter_presentations=presentations,
        not_filter_identities=(("BUY", "BUY-threshold"),),
        inner_not_filter_identities=(("BUY", "BUY-threshold"),),
    )
    canvas.resize(1400, 800)
    canvas.set_ocr_turn_delays({("BUY", "BUY-turn"): 1, ("SELL", "SELL-turn"): 1})
    return canvas, values, owners


def _render(canvas, values, owners, slow=False):
    image = QImage(1400, 240, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    original = canvas._ocr_histogram_filter_matches
    original_color = canvas._histogram_valid_signal_color
    try:
        if slow:
            canvas._ocr_histogram_filter_matches = (
                lambda index, owners, **_kwargs: original(index, owners)
            )
            canvas._histogram_valid_signal_color = (
                lambda index, owners, **kwargs: original_color(
                    index, owners, ocr_sides=kwargs.get("ocr_sides"),
                )
            )
            try:
                canvas._draw_series_histogram(
                    painter, values, window._ValidationPriceScale(-4, 4, 10, 230),
                    QPen(window._OSC_POSITIVE), owners,
                )
            finally:
                canvas._ocr_histogram_filter_matches = original
                canvas._histogram_valid_signal_color = original_color
        else:
            canvas._draw_series_histogram(
                painter, values, window._ValidationPriceScale(-4, 4, 10, 230),
                QPen(window._OSC_POSITIVE), owners,
            )
    finally:
        painter.end()
    return image


def test_ocr_histogram_plan_matches_slow_colors_and_pixels():
    canvas, values, owners = _case()
    try:
        for span in (250, 3500):
            canvas.set_time_view(0, span)
            for valid in (False, True):
                identities = tuple((side, owner.identity) for owner in owners for side in owner.sides)
                canvas.set_valid_filter_identities(
                    identities if valid else (), valid_mode_enabled=valid,
                    delay_ranges={key: ((0, 4999, 1),) for key in identities},
                )
                plan = canvas._prepare_ocr_histogram_filters(owners)
                signal_plan = canvas._prepare_histogram_signal_owners(owners)
                for index in range(span):
                    matches = canvas._ocr_histogram_filter_matches(
                        index, owners, prepared=plan,
                    )
                    assert matches == canvas._ocr_histogram_filter_matches(index, owners)
                    sides = {side for side, _ in matches}
                    fast_color = canvas._histogram_valid_signal_color(
                        index, owners, ocr_sides=sides, prepared=signal_plan,
                    )
                    slow_color = canvas._histogram_valid_signal_color(
                        index, owners, ocr_sides=sides,
                    )
                    assert fast_color == slow_color
                fast = _render(canvas, values, owners)
                slow = _render(canvas, values, owners, slow=True)
                assert fast == slow
        canvas.set_time_view(0, 250)
        canvas.set_not_filter_identities(())
        canvas._toggle_price_legend_identities("BUY", ("BUY-turn", "BUY-threshold"))
        assert _render(canvas, values, owners) == _render(canvas, values, owners, slow=True)
        canvas.set_not_filter_identities(
            (("BUY", "BUY-threshold"),), (("BUY", "BUY-threshold"),),
        )
        assert _render(canvas, values, owners) == _render(canvas, values, owners, slow=True)
        canvas.set_valid_filter_identities((), valid_mode_enabled=False)
        plan = canvas._prepare_ocr_histogram_filters(owners)
        assert canvas._ocr_histogram_filter_matches(3, owners, prepared=plan) == ((
            "BUY", ("BUY-turn", "BUY-threshold"),
        ),)
        assert canvas._ocr_histogram_filter_matches(6, owners, prepared=plan) == ((
            "SELL", ("SELL-turn", "SELL-threshold"),
        ),)
        assert canvas._histogram_valid_signal_color(3, owners, ocr_sides={"BUY"}).getRgb() == window._BUY.getRgb()
        assert canvas._histogram_valid_signal_color(3, owners, ocr_sides={"BUY", "SELL"}) is None
    finally:
        canvas.close()


def test_ocr_histogram_render_benchmark():
    canvas, values, owners = _case()
    try:
        for span in (250, 3500):
            canvas.set_time_view(0, span)
            timings = {"slow": [], "fast": []}
            for slow in (True, False, False, True, True, False):
                start = perf_counter()
                _render(canvas, values, owners, slow=slow)
                timings["slow" if slow else "fast"].append(
                    (perf_counter() - start) * 1000,
                )
            print(f"OCR histogram {span}: slow={timings['slow']} fast={timings['fast']}")
    finally:
        canvas.close()
