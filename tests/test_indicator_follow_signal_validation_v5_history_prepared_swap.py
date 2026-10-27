"""The old V5 chart stays attached while a replacement is prepared."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_window as module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.qt_test_support import ensure_qapplication


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _shown_window(case, count):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(count))
    window.resize(1400, 800)
    window.show()
    ensure_qapplication().processEvents()
    return window


def test_constructor_failure_keeps_attached_canvas(case):
    window = _shown_window(case, 500)
    old = window.canvas
    with patch.object(module, "IndicatorFollowSignalValidationChartCanvas",
                      side_effect=RuntimeError("candidate failed")):
        with pytest.raises(RuntimeError, match="candidate failed"):
            window._install_normal_chart_canvas("M1", [])
    assert window.canvas is old
    assert window.chart_scroll_area.widget() is old
    assert not old.isHidden()


@pytest.mark.parametrize("count", (500, 3500))
def test_extension_prepares_while_old_canvas_is_attached(case, count):
    window = _shown_window(case, count)
    old = window.canvas
    old_time = window._candles[-250]["time"]
    window.set_historical_candle_pool(baseline.candles(count + 500),
                                      chart_candle_count=count + 500)
    canvas_class = module.IndicatorFollowSignalValidationChartCanvas
    original_init = canvas_class.__init__
    started = {}
    def prepare(candidate, *args, **kwargs):
        started["begin"] = time.perf_counter()
        assert window.chart_scroll_area.widget() is old
        assert not old.isHidden()
        original_init(candidate, *args, **kwargs)
        started["end"] = time.perf_counter()
        assert window.chart_scroll_area.widget() is old
    take = window.chart_scroll_area.takeWidget
    set_widget = window.chart_scroll_area.setWidget
    def detach():
        started["detach"] = time.perf_counter()
        return take()
    def attach(widget):
        started["attach"] = time.perf_counter()
        return set_widget(widget)
    with patch.object(canvas_class, "__init__", prepare), \
         patch.object(window.chart_scroll_area, "takeWidget", side_effect=detach), \
         patch.object(window.chart_scroll_area, "setWidget", side_effect=attach):
        window._install_normal_chart_canvas("M1", [])
    ensure_qapplication().processEvents()
    assert window.chart_scroll_area.widget() is window.canvas
    assert window.canvas is not old
    assert window._candles[-250]["time"] == old_time
    assert started["end"] <= started["detach"] <= started["attach"]
    assert window.visible_candle_span == 250
    assert window.time_navigation_scrollbar.maximum() >= 0
    print(f"prepared {count}->{count + 500}: constructor="
          f"{(started['end'] - started['begin']) * 1000:.2f} ms, "
          f"detached gap={(started['attach'] - started['detach']) * 1000:.2f} ms")


def test_same_data_replacement_keeps_full_view_raster(case):
    window = _shown_window(case, 500)
    app = ensure_qapplication()
    lower = next(descriptor for descriptor in window._visualization_descriptors
                 if descriptor.axis == module.LOWER_AXIS)
    window._not_filter_identities = {("BUY", lower.identity)}
    window._install_normal_chart_canvas("M1", [])
    app.processEvents()
    assert window.canvas._lower_families
    assert ("BUY", lower.identity) in window.canvas._not_filter_identities
    before_time = window._candles[int(window.visible_start_index)]["time"]
    before_scroll = (window.time_navigation_scrollbar.minimum(),
                     window.time_navigation_scrollbar.maximum(),
                     window.time_navigation_scrollbar.pageStep(),
                     window.time_navigation_scrollbar.value())
    before = window.chart_view.grab().toImage()
    window._install_normal_chart_canvas("M1", [])
    app.processEvents()
    after = window.chart_view.grab().toImage()
    assert before == after
    assert window._candles[int(window.visible_start_index)]["time"] == before_time
    assert (window.time_navigation_scrollbar.minimum(),
            window.time_navigation_scrollbar.maximum(),
            window.time_navigation_scrollbar.pageStep(),
            window.time_navigation_scrollbar.value()) == before_scroll
    assert window.canvas._lower_families
    assert ("BUY", lower.identity) in window.canvas._not_filter_identities
