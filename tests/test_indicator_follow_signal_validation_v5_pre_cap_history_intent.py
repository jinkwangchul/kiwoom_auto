"""Wheel history intent expires when zoom reaches the 3,500-bar cap."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_signal_validation_v5_wheel_history_boundary as wheel_cases


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


@pytest.mark.parametrize("start", [3250, 0])
def test_pre_cap_wheel_burst_prefetches_once_at_saturation(case, start):
    window = wheel_cases.window_with_rows(case, 3500)
    window._set_time_view(start, 250)
    window.show()
    QApplication.processEvents()
    flow, key = wheel_cases.bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    calculation_count = len(window._calculation_candles)

    with patch.object(flow, "_run_validation") as run:
        for _ in range(36):
            wheel_cases.wheel(window.canvas, -120)
            QApplication.processEvents()
        assert window._visible_start_index == 0
        assert window._visible_candle_span == 3500
        assert window.time_navigation_scrollbar.maximum() == 0
        at_cap = (window._visible_start_index, window._visible_candle_span,
                  window.time_navigation_scrollbar.maximum(),
                  window.time_navigation_scrollbar.value(),
                  window.time_navigation_scrollbar.isEnabled())
        QTest.qWait(700)
        assert emitted == [1]
        assert flow._history_targets[key] == 4000
        run.assert_called_once()
        assert window._history_extension_intent_source is None
        assert window._history_extension_retry_generation is None
        assert (window._visible_start_index, window._visible_candle_span,
                window.time_navigation_scrollbar.maximum(),
                window.time_navigation_scrollbar.value(),
                window.time_navigation_scrollbar.isEnabled()) == at_cap

    assert window._visible_candle_span == 3500
    assert window.time_navigation_scrollbar.maximum() == 0
    assert len(window._calculation_candles) == calculation_count


def test_busy_pre_cap_wheel_retry_is_superseded_at_saturation(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window._set_time_view(0, 250)
    window.show()
    QApplication.processEvents()
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    generation = window._history_extension_intent_generation
    assert window._history_extension_intent_source == "wheel"

    with patch.object(window, "is_chart_view_update_busy", return_value=True):
        window._emit_history_extension_if_idle(generation)
    assert window._history_extension_retry_generation == generation

    for _ in range(36):
        wheel_cases.wheel(window.canvas, -120)
        QApplication.processEvents()
    assert window._visible_candle_span == 3500
    assert window._history_extension_intent_source == "wheel"
    QTest.qWait(700)
    assert emitted == [1]


def test_pre_cap_wheel_intent_cancels_after_user_browses_away(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window._set_time_view(140, 250)
    window.show()
    QApplication.processEvents()
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window._history_extension_intent_source == "wheel"
    assert window._history_extension_is_needed()

    window.time_navigation_scrollbar.setValue(3_000_000)
    QTest.qWait(100)
    assert not window._history_extension_is_needed()
    QTest.qWait(700)
    assert emitted == []


def test_reverse_wheel_leaving_oldest_boundary_cancels_intent(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window._set_time_view(140, 250)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window._history_extension_intent_source == "wheel"
    assert window._history_extension_is_needed()

    wheel_cases.wheel(window.canvas, 120)
    QApplication.processEvents()
    assert not window._history_extension_is_needed()
    QTest.qWait(700)
    assert emitted == []
