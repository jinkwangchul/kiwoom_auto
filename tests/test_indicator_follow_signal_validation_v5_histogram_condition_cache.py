from __future__ import annotations

import json
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui_indicator_follow_signal_validation_v5_window as v5_window
import indicator_follow_signal_validation_visualization as visualization
from tests.test_indicator_follow_signal_validation_v5_baseline import candles, descriptor
from tests.qt_test_support import ensure_qapplication


def test_v5_histogram_reuses_descriptor_conditions_across_visible_bars():
    """A 3,500-bar paint must not re-decode each descriptor per bar."""
    ensure_qapplication()
    count = 3500
    turn = descriptor(
        "ocr-turn",
        v5_window.FAMILY_OCR_OSC,
        "OCR turn",
        v5_window.LOWER_AXIS,
        "BUY",
        "OSC",
        {"condition": {"operator": "TURN_UP", "target": "OSC"}},
    )
    threshold = descriptor(
        "ocr-threshold",
        v5_window.FAMILY_OCR_OSC,
        "OCR threshold",
        v5_window.LOWER_AXIS,
        "BUY",
        "OSC",
        {
            "condition": {
                "operator": "GREATER_THAN",
                "target": "OSC",
                "threshold": 0,
            }
        },
    )
    presentation = v5_window.ValidationLowerFilterPresentation(
        key="BUY:ocr",
        side="BUY",
        source_path="buy.ocr",
        filter_token="OCR",
        pane_family=v5_window.FAMILY_OCR_OSC,
        display_order=(0, 1),
        descriptor_identities=(turn.identity, threshold.identity),
        component_descriptor_identities=(turn.identity, threshold.identity),
        component_labels=("OCR turn", "OCR threshold"),
    )
    turn_values = (-1.0, -2.0, 1.0) + (1.0,) * (count - 3)
    threshold_values = (1.0,) * count
    canvas = v5_window.IndicatorFollowSignalValidationChartCanvas(
        candles(count),
        [],
        visualization_descriptors=(turn, threshold),
        visualization_cache=v5_window.ValidationIndicatorSeriesCache(
            count,
            (
                (turn.identity, "OSC", turn_values),
                (threshold.identity, "OSC", threshold_values),
            ),
        ),
        lower_filter_presentations=(presentation,),
    )
    canvas._configured_ocr_turn_delays = {("BUY", turn.identity): 0}
    owners = (turn, threshold)
    v5_window._histogram_condition.cache_clear()

    with patch.object(visualization.json, "loads", wraps=json.loads) as loads:
        colors = [
            canvas._histogram_valid_signal_color(
                index,
                owners,
                ocr_sides=canvas._ocr_histogram_filter_sides(index, owners),
            )
            for index in range(count)
        ]

    assert colors[0] is None
    assert colors[1] is None
    assert colors[2].getRgb() == v5_window._BUY.getRgb()
    assert colors[3] is None
    assert canvas._histogram_valid_signal_color(2, owners, ocr_sides={"SELL"}).getRgb() == v5_window._SELL.getRgb()
    assert canvas._histogram_valid_signal_color(2, owners, ocr_sides={"BUY", "SELL"}) is None
    assert loads.call_count <= 2
