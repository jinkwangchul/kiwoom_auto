from __future__ import annotations

from unittest.mock import patch
import time

from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtTest import QTest

import gui_indicator_follow_signal_validation_v5_flow as v5_flow
import gui_indicator_follow_signal_validation_v5_window as v5_window
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


def _wait_for(case, predicate):
    deadline = time.monotonic() + 30
    while not predicate() and time.monotonic() < deadline:
        case.app.processEvents()
        QTest.qWait(10)
    assert predicate()


def _connected(case):
    window = baseline.window_for(case)
    rows = baseline.candles(100)
    baseline.install(window, rows)
    settings = window._marking_current_settings()
    window.install_normal_base(settings, "M1")
    legacy = baseline.legacy
    session = legacy.ValidationSession(legacy.ValidationRequest(
        case.stock, settings, settings.to_dict()["bar"]["bar_minutes"],
    ), operation_active_reader=lambda: False)
    flow = v5_flow.IndicatorFollowSignalValidationV5Flow(
        object(), host=legacy._FakeHost(case.stock),
        historical_cache=case.default_history_cache, historical_count=100,
    )
    flow._validation_source_by_code[case.stock.code] = False
    flow._validation_frozen_krx_codes.add(case.stock.code)
    key = id(window)
    flow._open_windows[key] = window
    flow._request_generation[key] = 0
    flow._history_targets[key] = 100
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = flow._pool_from_candles(
        session, rows, requested_count=100, request_id="VALID_LEGEND",
        market_data_identity=case.stock.code, market_source="KRX",
    )
    window.marking_evaluation_requested.connect(lambda r: flow._start_marking(window, r))
    window.validation_range_evaluation_requested.connect(lambda a, b: flow._run_cached_range_replay(window, a, b))
    window._set_time_view(40, 30)
    return window, flow


def test_valid_toggle_preserves_v5_manual_price_and_lower_selection(case):
    """Valid is a settings-only overlay and never starts replay or full projection."""
    window, flow = _connected(case)
    blockers = [
        QSignalBlocker(window.buy_signal_expr_line),
        QSignalBlocker(window.sell_signal_expr_line),
    ]
    window.buy_signal_expr_line.setText("A")
    window.sell_signal_expr_line.setText("A AND B")
    del blockers
    window._on_signal_validation_ui_changed()
    _wait_for(case, lambda: window._visualization_projection_active is None)
    canvas = window.canvas
    descriptors = window.visualization_descriptors
    cache = window.visualization_cache
    expected = {
        (side, descriptor.identity)
        for descriptor in descriptors
        for side in descriptor.sides
        if (
            side == "BUY"
            and descriptor.family == v5_window.FAMILY_OCR_OSC
        ) or (
            side == "SELL"
            and descriptor.family in {
                v5_window.FAMILY_OCR_OSC,
                v5_window.FAMILY_RSI,
                v5_window.FAMILY_PRICE_COMPARISON,
                v5_window.FAMILY_PRICE_BOX,
                v5_window.FAMILY_BOLLINGER,
            }
        )
    }
    assert len(expected) == 8
    manual_price = next(
        descriptor for descriptor in descriptors
        if descriptor.axis == v5_window.PRICE_AXIS
    )
    manual_lower = next(
        descriptor for descriptor in descriptors
        if descriptor.axis == v5_window.LOWER_AXIS
    )
    canvas._toggle_price_legend_identities(
        manual_price.sides[0], (manual_price.identity,),
    )
    canvas._toggle_price_legend_identities(
        manual_lower.sides[0], (manual_lower.identity,),
    )
    manual_price_keys = canvas.highlighted_price_filter_identities
    manual_lower_keys = canvas.highlighted_lower_filter_identities

    with patch.object(
        flow,
        "_prepare_replay_result_from_pool",
        side_effect=AssertionError("valid display must not replay"),
    ), patch.object(
        window,
        "_refresh_filter_display_projection",
        side_effect=AssertionError("valid display must not project"),
    ), patch.object(
        canvas,
        "set_visualization_projection",
        side_effect=AssertionError("valid display must not reload canvas"),
    ):
        window.filter_display_valid_check.setChecked(True)

    assert window.filter_display_valid_check.isChecked()
    assert not window.marking_display_check.isChecked()
    assert window.validation_range is None and window.replay_snapshot is None
    assert set(canvas.valid_filter_identities) == expected
    assert canvas._valid_filter_index_ranges == {}
    assert canvas._valid_filter_delay_ranges == {}
    assert window.visualization_descriptors == descriptors
    assert window.visualization_cache is cache
    assert canvas.highlighted_price_filter_identities == manual_price_keys
    assert canvas.highlighted_lower_filter_identities == manual_lower_keys

    transition = next(
        descriptor for descriptor in descriptors
        if descriptor.family in {v5_window.FAMILY_OCR_OSC, v5_window.FAMILY_MACD_SIGNAL}
        and descriptor.parameters.get("condition", {}).get("operator") in {
            "TURN_UP", "TURN_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN",
        }
    )
    assert all(
        canvas._histogram_valid_signal_color(index, (transition,)) is None
        for index in range(len(canvas._candles))
    )

    window.filter_display_valid_check.setChecked(False)
    assert canvas.valid_filter_identities == ()
    assert canvas.highlighted_price_filter_identities == manual_price_keys
    assert canvas.highlighted_lower_filter_identities == manual_lower_keys
    window.filter_display_valid_check.setChecked(True)
    window.filter_display_check.setChecked(False)
    assert canvas.valid_filter_identities == ()
    assert window.filter_display_valid_check.isChecked()


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
