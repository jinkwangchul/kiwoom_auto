"""One explicit older-history request survives a busy completion boundary."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
import gui_indicator_follow_signal_validation_v5_window as window_module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _window(case):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(3500))
    window._set_time_view(0, 3500)
    assert window.time_navigation_scrollbar.maximum() == 0
    assert window._history_extension_is_needed()
    return window


def _flow(window):
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(),
        host=contracts._Host(),
        recent_stock_store=contracts._RecentStore(),
        preference_store=SimpleNamespace(),
        historical_cache=SimpleNamespace(),
        historical_count=3500,
        production_session_contract_reader=lambda *_args: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False,
        },
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._request_generation[key] = 1
    flow._last_run_requests[key] = flow_module.IndicatorFollowSignalValidationRunRequest(
        window._signal_validation_seed.settings_snapshot, 3500,
    )
    return flow, key


def test_window_busy_frame_release_rechecks_explicit_navigation_once(case):
    window = _window(case)
    emitted = []
    callbacks = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    with patch.object(window_module.QTimer, "singleShot", side_effect=lambda _ms, callback: callbacks.append(callback)):
        window._maybe_request_history_extension(source="navigation")
        assert len(callbacks) == 1
        window._chart_input_frame_active = True
        window._chart_input_frame_canvas = window.canvas
        window._chart_input_frame_release_scheduled = True
        callbacks.pop()()
        assert emitted == []
        window._release_chart_input_frame(window._chart_input_frame_generation, window.canvas)
        assert len(callbacks) == 1
        callbacks.pop()()
        assert emitted == [1]
        assert callbacks == []


def test_window_busy_request_does_not_emit_after_moving_away(case):
    window = _window(case)
    emitted = []
    callbacks = []
    window.historical_extension_requested.connect(lambda: emitted.append(1))
    with patch.object(window_module.QTimer, "singleShot", side_effect=lambda _ms, callback: callbacks.append(callback)):
        window._maybe_request_history_extension(source="navigation")
        window._chart_input_frame_active = True
        window._chart_input_frame_canvas = window.canvas
        window._chart_input_frame_release_scheduled = True
        callbacks.pop()()
        window._set_time_view(1000, 250)
        window._release_chart_input_frame(window._chart_input_frame_generation, window.canvas)
        assert callbacks == []
        assert emitted == []


@pytest.mark.parametrize("busy_gate", ["prepare", "range", "provider"])
def test_flow_busy_work_drains_one_explicit_request_at_idle(case, busy_gate):
    window = _window(case)
    flow, key = _flow(window)
    if busy_gate == "prepare":
        active = flow._prepare_active
        active_key = key
    elif busy_gate == "range":
        active = flow._range_replay_active
        active_key = key
    else:
        active = flow._active_providers
        active_key = (key, 1)
    active[active_key] = object()
    started = []
    with patch.object(flow, "_run_validation", side_effect=lambda *args, **kwargs: started.append((args, kwargs))):
        flow._request_history_extension(window)
        flow._request_history_extension(window)
        assert started == []
        active.pop(active_key)
        flow._start_pending_window_work(key)
        assert len(started) == 1
        assert started[0][1] == {"history_extension": True}
        flow._start_pending_window_work(key)
        assert len(started) == 1


def test_flow_busy_request_cancels_if_navigation_moves_away(case):
    window = _window(case)
    flow, key = _flow(window)
    flow._prepare_active[key] = object()
    with patch.object(flow, "_run_validation") as run:
        flow._request_history_extension(window)
        window._set_time_view(1000, 250)
        flow._prepare_active.pop(key)
        flow._start_pending_window_work(key)
    run.assert_not_called()


def test_flow_busy_request_cancels_if_run_identity_changes(case):
    window = _window(case)
    flow, key = _flow(window)
    flow._prepare_active[key] = object()
    with patch.object(flow, "_run_validation") as run:
        flow._request_history_extension(window)
        previous = flow._last_run_requests[key]
        flow._last_run_requests[key] = flow_module.IndicatorFollowSignalValidationRunRequest(
            previous.settings_snapshot, previous.candle_count,
        )
        flow._prepare_active.pop(key)
        flow._start_pending_window_work(key)
    run.assert_not_called()


def test_flow_busy_request_respects_existing_history_ceiling(case):
    window = _window(case)
    flow, key = _flow(window)
    flow._prepare_active[key] = object()
    flow._history_targets[key] = flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT
    with patch.object(flow, "_run_validation") as run:
        flow._request_history_extension(window)
        flow._prepare_active.pop(key)
        flow._start_pending_window_work(key)
    run.assert_not_called()
