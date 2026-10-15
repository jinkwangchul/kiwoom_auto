"""A capped visible span can fetch data to unlock history navigation."""
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


def _install(window, count):
    rows = baseline.candles(count)
    window.set_historical_candle_pool(rows, chart_candle_count=count)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")


def test_cap_wheel_then_navigation_reaches_5000(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    flow, key = wheel_cases.bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    before = (window._visible_start_index, window._visible_candle_span,
              window.current_price_bounds)
    with patch.object(flow, "_run_validation") as run:
        for _ in range(3):
            wheel_cases.wheel(window.canvas, -120)
        QApplication.processEvents()
        assert (window._visible_start_index, window._visible_candle_span,
                window.current_price_bounds) == before
        assert emitted == []
        QTest.qWait(400)
        assert emitted == [1]
        assert flow._history_targets[key] == 4000
        run.assert_called_once()
        _install(window, 4000)
        flow._history_extension_inflight.pop(key, None)
        assert window._visible_candle_span == 3500
        assert window.time_navigation_scrollbar.maximum() == 500_000
        assert window.time_navigation_scrollbar.isEnabled()
        assert window.time_navigation_scrollbar.value() == 500_000
        wheel_cases.wheel(window.canvas, -120)
        QTest.qWait(700)
        assert emitted == [1, 1]
        assert flow._history_targets[key] == 4500
        _install(window, 4500)
        flow._history_extension_inflight.pop(key, None)
        assert window.time_navigation_scrollbar.maximum() == 1_000_000
        window.time_navigation_scrollbar.setValue(0)
        QTest.qWait(700)
        assert flow._history_targets[key] == 5000
        _install(window, 5000)
        assert window.time_navigation_scrollbar.maximum() == 1_500_000
        window.time_navigation_scrollbar.setValue(500_000)
        QApplication.processEvents()
        assert window._visible_start_index == 500
        window.time_navigation_scrollbar.setValue(0)
        QApplication.processEvents()
        assert window._visible_start_index == 0
        QTest.qWait(400)
        prior = len(emitted)
        wheel_cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        assert len(emitted) == prior
        assert flow._history_targets[key] == 5000
        assert window._visible_candle_span == 3500


def test_reversing_cap_wheel_before_idle_cancels_prefetch(case):
    window = wheel_cases.window_with_rows(case, 3500)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel_cases.wheel(window.canvas, -120)
    wheel_cases.wheel(window.canvas, 120)
    QApplication.processEvents()
    QTest.qWait(400)
    assert emitted == []
    assert window._visible_candle_span < 3500


def test_reaching_cap_from_below_requests_without_extra_wheel(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window._set_time_view(0, 3400)
    window.show()
    QApplication.processEvents()
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window._visible_candle_span == 3500
    assert window._history_extension_intent_source == "wheel"
    assert emitted == []
    QTest.qWait(700)
    assert emitted == [1], (window._history_extension_intent_source,
                            window._history_extension_retry_generation,
                            window.is_chart_view_update_busy(),
                            window._history_extension_intent_generation)
