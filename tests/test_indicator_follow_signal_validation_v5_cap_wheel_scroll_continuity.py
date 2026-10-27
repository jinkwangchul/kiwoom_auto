"""At the 3,500-bar cap, extra history-wheel input must navigate smoothly."""

import pytest
from PyQt5.QtCore import QObject, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_signal_validation_v5_wheel_history_boundary as wheel_cases


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


class ScrollbarEvents(QObject):
    def __init__(self):
        super().__init__()
        self.hidden = 0

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Hide:
            self.hidden += 1
        return False


def test_wheel_cap_transitions_into_scrollbar_time_navigation(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    window.set_historical_candle_pool(baseline.candles(4000), chart_candle_count=4000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    QApplication.processEvents()
    scrollbar = window.time_navigation_scrollbar
    assert window.visible_candle_span == 3500
    assert window.visible_start_index == 500
    assert scrollbar.maximum() == 500_000
    assert scrollbar.isEnabled() and scrollbar.isVisible()
    events = ScrollbarEvents()
    scrollbar.installEventFilter(events)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window.visible_candle_span == 3500
    assert 0 <= window.visible_start_index < 500
    assert scrollbar.value() < 500_000
    assert scrollbar.isVisible()
    assert events.hidden == 0
    QTest.qWait(900)
    QApplication.processEvents()
    assert requests == [1]


def test_cap_navigation_after_replay_prepend_preserves_bar_visibility(case):
    window = wheel_cases.window_with_rows(case, 4000)
    window._set_time_view(500, 3500)
    window.show()
    QApplication.processEvents()
    start_time = window._candles[500]["time"]
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window.visible_candle_span == 3500
    assert window._candles[round(window.visible_start_index)]["time"] < start_time


def test_wheel_cap_at_oldest_before_extension_keeps_view_and_fetches(case):
    window = wheel_cases.window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    events = []
    window.historical_extension_requested.connect(lambda: events.append(1))
    wheel_cases.wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window.visible_start_index == 0
    assert window.visible_candle_span == 3500
    QTest.qWait(450)
    assert events == [1]


@pytest.mark.parametrize("delta, expected_start", [(-120, 860), (-240, 720)])
def test_full_5000_cap_wheel_step_is_proportional_without_extension(
    case, delta, expected_start,
):
    window = wheel_cases.window_with_rows(case, 5000)
    window._set_time_view(1000, 3500)
    window.show()
    QApplication.processEvents()
    scrollbar = window.time_navigation_scrollbar
    visibility = ScrollbarEvents()
    scrollbar.installEventFilter(visibility)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))

    wheel_cases.wheel(window.canvas, delta)
    QApplication.processEvents()
    QTest.qWait(400)
    assert window.visible_start_index == pytest.approx(expected_start)
    assert window.visible_candle_span == 3500
    assert scrollbar.value() == pytest.approx(expected_start * 1000, abs=1)
    assert scrollbar.isVisible() and scrollbar.isEnabled()
    assert visibility.hidden == 0
    assert requests == []


def test_full_5000_cap_slider_drag_keeps_thumb_and_range(case):
    window = wheel_cases.window_with_rows(case, 5000)
    window._set_time_view(1000, 3500)
    window.show()
    QApplication.processEvents()
    scrollbar = window.time_navigation_scrollbar
    visibility = ScrollbarEvents()
    scrollbar.installEventFilter(visibility)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))

    scrollbar.setSliderDown(True)
    for value in (800_000, 600_000, 400_000):
        scrollbar.setSliderPosition(value)
        QApplication.processEvents()
        assert scrollbar.isSliderDown()
        assert scrollbar.isVisible() and scrollbar.isEnabled()
        assert scrollbar.maximum() == 1_500_000
    scrollbar.setSliderDown(False)
    QTest.qWait(400)
    assert window.visible_start_index == pytest.approx(400)
    assert scrollbar.value() == 400_000
    assert visibility.hidden == 0
    assert requests == []
