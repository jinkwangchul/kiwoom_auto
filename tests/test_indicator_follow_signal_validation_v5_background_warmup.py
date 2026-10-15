"""V5 one-shot background history warmup gates."""
from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts


class _Settings:
    rules_hash = "background-warmup-test"

    def to_dict(self):
        return {"bar": {"bar_minutes": 1}}


def _flow():
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(),
        host=contracts._Host(),
        recent_stock_store=contracts._RecentStore(),
        preference_store=SimpleNamespace(),
        historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_args: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False,
        },
    )
    flow._historical_count = flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
    flow._open_windows = {}
    flow._request_generation = {}
    flow._last_run_requests = {}
    flow._history_targets = {}
    flow._history_extension_inflight = {}
    flow._history_exhaustion = {}
    flow._prepare_active = {}
    flow._range_replay_active = {}
    flow._active_providers = {}
    flow._history_extension_pending = {}
    return flow


class _Window:
    def __init__(self):
        self.stock = SimpleNamespace(code="005930")


def _window():
    return _Window()


def _request():
    request = object.__new__(flow_module.IndicatorFollowSignalValidationRunRequest)
    object.__setattr__(request, "settings_snapshot", _Settings())
    object.__setattr__(request, "candle_count", 250)
    object.__setattr__(request, "force_historical_refresh", False)
    return request


def test_background_warmup_uses_one_maximum_request_after_initial_500_commit():
    """A regression to 500-step chaining must not produce this 5000 request."""
    flow = _flow()
    window = _window()
    key = id(window)
    request = _request()
    flow._open_windows[key] = window
    flow._request_generation[key] = 7
    flow._last_run_requests[key] = request
    flow._history_targets[key] = flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT

    with patch.object(flow, "_run_validation") as run:
        flow._start_background_history_warmup(window)

    assert flow._history_targets[key] == flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT
    assert run.call_args.args == (window, request)
    assert run.call_args.kwargs == {"history_extension": True}
    state = flow._history_extension_inflight[key]
    assert state["previous_target"] == flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
    assert state["requested_target"] == flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT
    assert state["background_warmup"] is True

    flow._start_background_history_warmup(window)
    assert run.call_count == 1


def test_user_history_navigation_cancels_background_warmup_and_uses_existing_step():
    """A stale warmup cannot block an explicit navigation request."""
    flow = _flow()
    window = _window()
    key = id(window)
    request = _request()
    cancelled = Event()
    flow._open_windows[key] = window
    flow._request_generation[key] = 3
    flow._last_run_requests[key] = request
    flow._history_targets[key] = flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT
    flow._history_extension_inflight[key] = {
        "previous_target": flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT,
        "requested_target": flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT,
        "background_warmup": True,
        "cancel_event": cancelled,
    }

    with (
        patch.object(flow, "_run_validation") as run,
        patch.object(
            flow_module.IndicatorFollowSignalValidationRunRequest,
            "__post_init__",
            return_value=None,
        ),
    ):
        flow._request_history_extension(window)

    assert cancelled.is_set()
    assert flow._history_targets[key] == (
        flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
        + flow_module._HISTORY_EXTENSION_STEP
    )
    assert run.call_args.args == (window, request)
    assert run.call_args.kwargs == {"history_extension": True}
    assert flow._history_extension_inflight[key].get("background_warmup") is not True


def _normal_base_payload(flow, window, *, generation=11):
    """Build the same completion shape emitted by the normal-base worker."""
    key = id(window)
    token = object()
    session = object.__new__(flow_module.ValidationSession)
    pool = {}
    flow._open_windows[key] = window
    flow._request_generation[key] = generation
    flow._prepare_active[key] = token
    flow._historical_pools[key] = pool
    flow._validation_sessions[key] = session
    flow._history_targets[key] = flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT
    flow._last_run_requests[key] = _request()
    return {
        "window_key": key,
        "generation": generation,
        "prepare_token": token,
        "session": session,
        "pool": pool,
        "evaluation_count": 250,
        "prepared_view_pool": {},
        "normal_base_presentation": object(),
        "normal_base": True,
    }


def test_normal_base_completion_queues_warmup_only_after_first_install():
    """Removing the normal-base enqueue must leave the initial 500 view unwarmed."""
    flow = _flow()
    window = _window()
    window.installed = False
    payload = _normal_base_payload(flow, window)
    queued = []

    def apply(*_args, **_kwargs):
        window.installed = True
        return True

    with (
        patch.object(flow_module.QTimer, "singleShot", side_effect=lambda delay, callback: queued.append((delay, callback))),
        patch.object(flow, "_apply_pool_to_window", side_effect=apply),
        patch.object(flow, "_start_background_history_warmup") as start,
        patch.object(flow, "_start_pending_window_work"),
    ):
        flow._on_signal_scan_completed(payload)

        assert window.installed is True
        assert start.call_count == 0
        assert len(queued) == 1
        queued[0][1]()

    assert queued[0][0] >= 0
    assert start.call_args.args == (window,)


def test_normal_base_completion_does_not_queue_warmup_after_failed_apply():
    """A rejected initial installation must not fetch hidden history."""
    flow = _flow()
    window = _window()
    payload = _normal_base_payload(flow, window)

    with (
        patch.object(flow_module.QTimer, "singleShot") as schedule,
        patch.object(flow, "_apply_pool_to_window", return_value=False),
        patch.object(flow, "_start_background_history_warmup") as start,
        patch.object(flow, "_start_pending_window_work"),
    ):
        flow._on_signal_scan_completed(payload)

    assert schedule.call_count == 0
    assert start.call_count == 0


def test_stale_normal_base_completion_does_not_queue_warmup():
    """A superseded normal-base worker cannot enqueue a cross-source warmup."""
    flow = _flow()
    window = _window()
    payload = _normal_base_payload(flow, window, generation=11)
    flow._request_generation[id(window)] = 12

    with (
        patch.object(flow_module.QTimer, "singleShot") as schedule,
        patch.object(flow, "_apply_pool_to_window") as apply,
        patch.object(flow, "_start_background_history_warmup") as start,
    ):
        flow._on_signal_scan_completed(payload)

    assert apply.call_count == 0
    assert schedule.call_count == 0
    assert start.call_count == 0
