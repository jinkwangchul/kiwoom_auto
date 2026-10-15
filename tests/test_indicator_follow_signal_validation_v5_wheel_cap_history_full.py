"""Each genuine capped wheel intent can extend history through 5,000 bars."""
from __future__ import annotations

from unittest.mock import patch
import time

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_signal_validation_v5_wheel_history_boundary as wheel_cases


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _install(window, count):
    rows = baseline.candles(count)
    window.set_historical_candle_pool(rows, chart_candle_count=count)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")


def test_each_cap_wheel_fetches_one_chunk_to_5000_without_changing_view(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    flow, key = wheel_cases.bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    anchor_time = window._candles[0]["time"]
    previous_visible_time = anchor_time
    price_bounds = window.current_price_bounds

    with patch.object(flow, "_run_validation") as run:
        for count, maximum in ((4000, 500_000), (4500, 1_000_000),
                               (5000, 1_500_000)):
            wheel_cases.wheel(window.canvas, -120)
            QTest.qWait(700)
            assert len(emitted) == (count - 3500) // 500
            assert flow._history_targets[key] == count
            assert run.call_count == len(emitted)
            _install(window, count)
            flow._history_extension_inflight.pop(key, None)
            assert window._visible_candle_span == 3500
            visible_time = window._candles[round(window._visible_start_index)]["time"]
            if count == 4000:
                assert visible_time == anchor_time
            else:
                assert visible_time < previous_visible_time
                assert window.time_navigation_scrollbar.value() < maximum
            previous_visible_time = visible_time
            assert window.time_navigation_scrollbar.maximum() == maximum
            assert window.time_navigation_scrollbar.isEnabled()
            assert window.current_price_bounds == price_bounds

        wheel_cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        assert len(emitted) == 3
        assert flow._history_targets[key] == 5000
        assert run.call_count == 3


def _wait_target(flow, key, target, timeout_seconds=2.5):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and flow._history_targets[key] != target:
        QTest.qWait(75)
        QApplication.processEvents()
    assert flow._history_targets[key] == target


def test_wheel_then_scrollbar_then_wheel_reaches_cap(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    flow, key = wheel_cases.bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))

    with patch.object(flow, "_run_validation") as run:
        wheel_cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        assert flow._history_targets[key] == 4000
        _install(window, 4000)
        flow._history_extension_inflight.pop(key, None)

        window.time_navigation_scrollbar.setValue(0)
        _wait_target(flow, key, 4500)
        _install(window, 4500)
        flow._history_extension_inflight.pop(key, None)

        wheel_cases.wheel(window.canvas, -120)
        _wait_target(flow, key, 5000)
        assert emitted == [1, 1, 1]
        assert run.call_count == 3


def test_hidden_calculation_warmup_does_not_block_wheel_history(case):
    """Only chart/history targets count toward the 5,000-bar loading cap."""
    window = baseline.window_for(case)
    window.set_historical_candle_pool(baseline.candles(5000), chart_candle_count=4000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    window._set_time_view(0, 3500)
    window.show()
    QApplication.processEvents()
    assert len(window._candles) == 4000
    assert len(window._calculation_candles) == 5000
    assert window.historical_candle_count == 4000
    flow, key = wheel_cases.bound_flow(window)
    flow._history_targets[key] = 4000
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    with patch.object(flow, "_run_validation") as run:
        wheel_cases.wheel(window.canvas, -120)
        QTest.qWait(450)
        QApplication.processEvents()
        assert emitted == [1]
        assert flow._history_targets[key] == 4500
        run.assert_called_once()
        assert window._visible_candle_span == 3500
        assert window.time_navigation_scrollbar.maximum() == 500_000

def test_hidden_calculation_warmup_stops_at_display_target_5000(case):
    window = baseline.window_for(case)
    window.set_historical_candle_pool(baseline.candles(5900), chart_candle_count=5000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    window._set_time_view(0, 3500)
    window.show()
    QApplication.processEvents()
    assert window.historical_candle_count == 5000
    assert len(window._candles) == 5000
    events = []
    window.historical_extension_requested.connect(lambda: events.append(1))
    for _ in range(3):
        wheel_cases.wheel(window.canvas, -120)
    QTest.qWait(450)
    assert not events
    assert window._visible_candle_span == 3500
    assert window.time_navigation_scrollbar.maximum() == 1_500_000
