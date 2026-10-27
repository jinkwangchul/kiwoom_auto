"""A wheel zoom at the loaded edge completes after an exact history prepend."""
from __future__ import annotations

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_signal_validation_v5_history_hash_ownership as history
from tests.test_indicator_follow_signal_validation_v5_wheel_history_boundary import wheel


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def window_at_edge(case):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(500))
    window._set_time_view(0, 500)
    return window


def test_single_wheel_applies_after_prepend(case):
    window = window_at_edge(case)
    old_start_time = window._candles[0]["time"]
    wheel(window.canvas, -120)
    assert window._visible_candle_span == 500
    QTest.qWait(400)
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == pytest.approx(540)
    assert 0 < window._visible_start_index < 500
    assert window._candles[500]["time"] == old_start_time
    assert window.time_navigation_scrollbar.value() == round(
        window._visible_start_index * window._TIME_SCROLL_UNITS_PER_CANDLE
    )


def test_reverse_wheel_cancels_deferred_zoom(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    wheel(window.canvas, 120)
    QApplication.processEvents()
    assert window._deferred_wheel_zoom is None
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span < 500


def test_batched_wheels_continue_through_two_prepend_steps(case):
    window = window_at_edge(case)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    for _ in range(13):
        wheel(window.canvas, -120)
    expected = min(3500, 500 * 1.08 ** 13)
    QTest.qWait(400)
    assert requests == [1]
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == 1000
    QTest.qWait(400)
    assert requests == [1, 1]
    window.set_historical_candle_pool(baseline.candles(1500), chart_candle_count=1500)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == pytest.approx(expected)
    QTest.qWait(400)
    assert requests == [1, 1]


def test_changed_timeframe_drops_intent(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M5")
    assert window._visible_candle_span == 250
    assert window._deferred_wheel_zoom is None


def test_range_selection_drops_intent(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    window._begin_validation_range_drag(100)
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == 500


def test_scrollbar_navigation_drops_intent(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    window._begin_time_navigation()
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == 500


def test_nonprepend_drops_intent(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    rows = baseline.candles(1000)
    rows[-1] = dict(rows[-1], close=rows[-1]["close"] + 1)
    window.set_historical_candle_pool(rows, chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == 500


def test_replay_history_browse_resumes_wheel_after_anchor_restore(case):
    window = window_at_edge(case)
    settings = window._signal_validation_seed.settings_snapshot
    old_rows = baseline.candles(500)
    window.set_replay_snapshot(history._snapshot(window, old_rows, settings, "wheel-base", []))
    window._set_time_view(0, 500)
    wheel(window.canvas, -120)
    rows = baseline.candles(1000)
    prepared = history.window_module.prepare_signal_validation_presentation(
        rows, [], settings, chart_start_index=0, chart_count=len(rows),
    )
    window.apply_history_browse_presentation(
        rows, chart_candle_count=len(rows), prepared_presentation=prepared,
    )
    assert window._visible_candle_span == pytest.approx(540)


def test_price_wheel_keeps_pending_time_intent(case):
    window = window_at_edge(case)
    wheel(window.canvas, -120)
    scale = window.canvas.price_scale()
    window._queue_price_scale_wheel(scale.plot_top + 30, 120)
    QApplication.processEvents()
    window.set_historical_candle_pool(baseline.candles(1000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert window._visible_candle_span == pytest.approx(540)
