"""Wheel saturation leaves the view alone while unlocking history browsing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def window_with_rows(case, count):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(count))
    window._set_time_view(0, min(count, 3500))
    assert window._historical_pool_installed
    assert len(window._candles) == count
    return window


def wheel(widget, delta):
    pos = QPoint(round(widget._LEFT + widget._plot_width() * .47),
                 round(widget.price_scale().plot_top + 30))
    event = QWheelEvent(QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                        QPoint(), QPoint(0, delta), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    event.setAccepted(False)
    QApplication.sendEvent(widget, event)
    assert event.isAccepted()


def bound_flow(window):
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(), host=contracts._Host(),
        recent_stock_store=contracts._RecentStore(),
        preference_store=SimpleNamespace(), historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_args: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False,
        },
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._request_generation[key] = 1
    flow._historical_count = 3500
    flow._history_targets[key] = 3500
    flow._last_run_requests[key] = flow_module.IndicatorFollowSignalValidationRunRequest(
        window._signal_validation_seed.settings_snapshot, 3500,
    )
    window.historical_extension_requested.connect(
        lambda: flow._request_history_extension(window)
    )
    return flow, key


class VisibilityEvents(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.Hide):
            self.events.append((watched, event.type()))
        return False


def test_cap_wheel_prefetches_once_without_changing_view_or_scrollbar(case):
    window = window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    flow, key = bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    assert window._history_extension_is_needed()
    scrollbar = window.time_navigation_scrollbar
    assert scrollbar.maximum() == 0
    assert window.isVisible()
    assert scrollbar.isVisible()
    before = (window._visible_start_index, window._visible_candle_span,
              window.current_price_bounds)
    calculation_before = [dict(row) for row in window._calculation_candles]
    range_before = (window._validation_range, window._validation_range_times)
    scrollbar_before = (scrollbar.maximum(), scrollbar.value(),
                        scrollbar.pageStep(), scrollbar.isEnabled(),
                        scrollbar.isVisible(), scrollbar.height())
    visibility = VisibilityEvents()
    for widget in (window, window.chart_stack, scrollbar):
        widget.installEventFilter(visibility)

    with patch.object(flow, "_run_validation") as run:
        for _ in range(3):
            wheel(window.canvas, -120)
            assert window._pending_wheel_zoom is None
            assert not window._wheel_zoom_frame_timer.isActive()
        QTest.qWait(400)
        assert emitted == [1]
        assert flow._history_targets[key] == 4000
        run.assert_called_once()
    assert (scrollbar.maximum(), scrollbar.value(), scrollbar.pageStep(),
            scrollbar.isEnabled(), scrollbar.isVisible(), scrollbar.height()) == scrollbar_before
    assert visibility.events == []
    assert (window._visible_start_index, window._visible_candle_span,
            window.current_price_bounds) == before
    assert window._calculation_candles == calculation_before
    assert (window._validation_range, window._validation_range_times) == range_before


def test_explicit_pan_at_zero_requests_prepend_and_opens_scrollbar(case):
    window = window_with_rows(case, 3500)
    flow, key = bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    canvas = window.canvas
    start = QPoint(round(canvas._LEFT + canvas._plot_width() * .47),
                   round(canvas.price_scale().plot_top + 30))
    end = start + QPoint(40, 0)
    anchor_time = window._candles[0]["time"]

    with patch.object(flow, "_run_validation") as run:
        for event_type, pos, button, buttons in (
            (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
            (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
            (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton),
        ):
            event = QMouseEvent(event_type, QPointF(pos),
                                QPointF(canvas.mapToGlobal(pos)),
                                button, buttons, Qt.NoModifier)
            QApplication.sendEvent(canvas, event)
            assert event.isAccepted()
        assert window._visible_start_index == 0
        QTest.qWait(400)
        assert emitted == [1]
        assert flow._history_targets[key] == 4000
        run.assert_called_once()
        assert run.call_args.kwargs == {"history_extension": True}

    rows = baseline.candles(4000)
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    assert len(window._candles) == 4000
    assert window.time_navigation_scrollbar.maximum() == 500_000
    assert window.time_navigation_scrollbar.value() == 500_000
    assert window._visible_candle_span == 3500
    assert window._candles[round(window._visible_start_index)]["time"] == anchor_time


@pytest.mark.parametrize("count", [500, 1000])
def test_loaded_data_boundary_below_cap_still_requests(case, count):
    window = window_with_rows(case, count)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel(window.canvas, -120)
    QTest.qWait(400)
    assert emitted == [1]
    assert window._visible_candle_span == count


@pytest.mark.parametrize("start", [0, 1000])
def test_cap_with_more_loaded_data_does_not_request(case, start):
    window = window_with_rows(case, 5000)
    window._set_time_view(start, 3500)
    flow, key = bound_flow(window)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    assert window._history_extension_is_needed()
    scrollbar = window.time_navigation_scrollbar
    before = (window._visible_start_index, window._visible_candle_span,
              scrollbar.maximum(), scrollbar.value(), scrollbar.pageStep(),
              scrollbar.isEnabled(), window.current_price_bounds)
    calculation_before = [dict(row) for row in window._calculation_candles]
    range_before = (window._validation_range, window._validation_range_times)
    with (patch.object(flow, "_run_validation") as run,
          patch.object(window, "_set_time_view") as set_view,
          patch.object(window.canvas, "update") as update):
        for _ in range(5):
            wheel(window.canvas, -120)
            QApplication.processEvents()
            assert window._pending_wheel_zoom is None
        QTest.qWait(700)
        assert flow._history_targets[key] == 3500
        run.assert_not_called()
        set_view.assert_not_called()
        update.assert_not_called()
    assert emitted == []
    assert (window._visible_start_index, window._visible_candle_span,
            scrollbar.maximum(), scrollbar.value(), scrollbar.pageStep(),
            scrollbar.isEnabled(), window.current_price_bounds) == before
    assert window._calculation_candles == calculation_before
    assert (window._validation_range, window._validation_range_times) == range_before


def test_opposite_wheel_direction_at_cap_shrinks_span(case):
    window = window_with_rows(case, 3500)
    wheel(window.canvas, 120)
    QApplication.processEvents()
    assert window._visible_candle_span < 3500


def test_queued_reversal_at_cap_uses_final_wheel_direction(case):
    window = window_with_rows(case, 3500)
    window.show()
    QApplication.processEvents()
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    wheel(window.canvas, 120)
    assert window._pending_wheel_zoom[2] < 3500
    wheel(window.canvas, -120)
    QApplication.processEvents()
    assert window._visible_candle_span == 3500
    assert window._pending_wheel_zoom is None
    QTest.qWait(700)
    assert emitted == [1]


def test_price_axis_wheel_at_cap_changes_only_price(case):
    window = window_with_rows(case, 3500)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    axis = window.fixed_price_axis
    scale = window.canvas.price_scale()
    pos = QPoint(axis.width() // 2,
                 round(scale.plot_top + 30 + window.chart_scroll_area.viewport().geometry().top()))
    event = QWheelEvent(QPointF(pos), QPointF(axis.mapToGlobal(pos)),
                        QPoint(), QPoint(0, 120), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    event.setAccepted(False)
    before = window.current_price_bounds
    QApplication.sendEvent(axis, event)
    assert event.isAccepted()
    QApplication.processEvents()
    assert window.current_price_bounds != before
    assert window._visible_candle_span == 3500
    QTest.qWait(400)
    assert emitted == []


def test_pending_navigation_intent_is_not_replaced_by_cap_wheel(case):
    window = window_with_rows(case, 3500)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    window._maybe_request_history_extension(source="navigation")
    generation = window._history_extension_intent_generation
    wheel(window.canvas, -120)
    assert window._history_extension_intent_generation == generation
    assert window._history_extension_intent_source == "navigation"
    QTest.qWait(400)
    assert emitted == [1]


def test_pointer_interaction_blocks_cap_wheel_history(case):
    window = window_with_rows(case, 3500)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    window.canvas._begin_pointer_interaction()
    wheel(window.canvas, -120)
    QTest.qWait(400)
    assert emitted == []
    assert window._pending_wheel_zoom is None
