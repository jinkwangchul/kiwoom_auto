from __future__ import annotations

from unittest.mock import patch

import gui_indicator_follow_signal_validation_v5_flow as v5_flow
import gui_indicator_follow_signal_validation_v5_window as v5_window
from tests import test_indicator_follow_signal_validation_v4_marking as marking
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.test_indicator_follow_signal_validation_v5_baseline import case


def _manual_keys(window):
    canvas = window.canvas
    price = next(
        descriptor
        for descriptor in window.visualization_descriptors
        if descriptor.axis == v5_window.PRICE_AXIS
    )
    lower = next(
        descriptor
        for descriptor in window.visualization_descriptors
        if descriptor.axis == v5_window.LOWER_AXIS
    )
    canvas._toggle_price_legend_identities(price.sides[0], (price.identity,))
    canvas._toggle_price_legend_identities(lower.sides[0], (lower.identity,))
    return (
        set(canvas.highlighted_price_filter_identities),
        set(canvas.highlighted_lower_filter_identities),
    )


def test_valid_toggle_preserves_v5_manual_price_and_lower_selection(case, monkeypatch):
    monkeypatch.setattr(marking, "wm", v5_window)
    monkeypatch.setattr(marking, "fm", v5_flow)
    monkeypatch.setattr(marking, "window_for", baseline.window_for)
    monkeypatch.setattr(marking, "install", baseline.install)
    monkeypatch.setattr(
        v5_flow,
        "IndicatorFollowSignalValidationV4Flow",
        v5_flow.IndicatorFollowSignalValidationV5Flow,
        raising=False,
    )

    marking.test_valid_display_selects_enabled_ui_without_marking_or_projection(case)


def test_zero_valid_toggle_uses_valid_then_restores_manual_selection(case):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(100))
    canvas = window.canvas
    manual_price, manual_lower = _manual_keys(window)

    with patch.object(window, "_current_valid_filter_identities", return_value=set()), patch.object(
        canvas,
        "set_visualization_projection",
        side_effect=AssertionError("valid toggle must not reload canvas"),
    ):
        window.filter_display_valid_check.setChecked(True)
        assert canvas._effective_price_filter_identities() == set()
        assert canvas._effective_lower_filter_identities() == set()
        assert set(canvas.highlighted_price_filter_identities) == manual_price
        assert set(canvas.highlighted_lower_filter_identities) == manual_lower

        window.filter_display_valid_check.setChecked(False)

    assert canvas._effective_price_filter_identities() == manual_price
    assert canvas._effective_lower_filter_identities() == manual_lower


def test_explicit_legend_reset_clears_manual_selection_while_valid_stays_on(case):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(100))
    canvas = window.canvas
    manual_price, manual_lower = _manual_keys(window)

    with patch.object(
        window,
        "_current_valid_filter_identities",
        return_value=manual_price | manual_lower,
    ):
        window.filter_display_valid_check.setChecked(True)

    canvas.reset_price_legend_visibility()

    assert window.filter_display_valid_check.isChecked()
    assert set(canvas.valid_filter_identities) == manual_price | manual_lower
    assert not canvas.highlighted_price_filter_identities
    assert not canvas.highlighted_lower_filter_identities
