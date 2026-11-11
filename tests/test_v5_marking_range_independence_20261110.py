"""Post-worker UI boundary only; recorded candles are not new broker evidence."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QSignalBlocker

from tests.test_v5_async_marking_range_interleaving_20261031 import no_external_effects
from tests.test_v5_marking_bind_stage2_20261030 import NATIVE_ROWS
from tests.test_indicator_follow_signal_validation_v5_baseline import case, window_for
from tests.test_indicator_follow_signal_validation_v5_history_hash_ownership import _entry, _snapshot
from tests.test_v5_direct_forming_tail_20261026 import _flow
from tests.v5_validation_test_support import _FakeWindow
import gui_indicator_follow_signal_validation_v5_flow as v5


@pytest.fixture
def view(case):
    window = window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("1")
    rows = NATIVE_ROWS["M1"]
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    settings = window._marking_current_settings()
    window.install_normal_base(settings, "M1")
    flow = _flow()
    flow._operation_active_reader = lambda: False
    now = datetime.fromisoformat("2026-10-30T11:45:00+09:00")
    flow._now_factory = lambda: now
    key = id(window)
    session = v5.ValidationSession(v5.ValidationRequest(window.stock, settings, 1),
        operation_active_reader=lambda: False)
    session.source_selection = v5._V5SourceSelection(
        window.stock, "M1", key, 7, "FULL", window.stock.code + "_AL",
        "INTEGRATED", True, now)
    pool = {"candles": rows}
    flow._open_windows[key] = window
    flow._request_generation[key] = 7
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    assert flow._source_selection_is_current(session.source_selection)
    requests = []
    window.marking_evaluation_requested.connect(requests.append)

    def payload(entries=()):
        token = object()
        flow._range_replay_generation[key] = 1
        flow._range_replay_active[key] = token
        return dict(window_key=key, generation=1, request_generation=7,
            range_token=token, pool=pool, session=session, source_session=session,
            range=window.validation_range, range_times=window._validation_range_times,
            snapshot=v5.ValidationReplaySnapshot(stock=window.stock,
                timeframe_minutes=1, timeframe_key="M1", settings_hash=settings.rules_hash,
                historical_request_id="UI-only-range", evaluated_start_index=1,
                evaluated_end_index=3, dropped_raw_rows_count=0,
                candles=rows, entries=list(entries)),
            error="")

    return SimpleNamespace(window=window, rows=rows, settings=settings,
        flow=flow, key=key, session=session, pool=pool, payload=payload, requests=requests)


@pytest.mark.parametrize("selected", ["pending", "completed", "empty", "failed"])
def test_range_never_suppresses_frozen_markers_and_mark_off_keeps_range(view, selected):
    r = view
    window = r.window
    window.marking_display_check.click()
    request = r.requests.pop()
    frozen = _snapshot(window, r.rows, r.settings, "UI-only-mark", [
        _entry(r.rows, 1, "BUY"), _entry(r.rows, 4, "SELL")])
    assert window.apply_marking_snapshot(request["generation"], request["key"], frozen, None)
    window._set_validation_range(1, 3)
    if selected != "pending":
        replay = r.payload([_entry(r.rows, 1, "BUY"), _entry(r.rows, 2, "SELL")]
                           if selected == "completed" else [])
        if selected == "failed":
            replay["error"] = "offline selected replay failure"
            r.flow._on_range_replay_completed(replay)
        else:
            window.apply_range_replay_snapshot(replay["snapshot"], settings_snapshot=r.settings)

    def markers():
        return {(m["evaluation_index"], m["side"], m["provenance"])
                for m in window._chart_marker_records()}

    manual = {(1, "BUY", "manual"), (2, "SELL", "manual")} if selected == "completed" else set()
    marked = {(1, "BUY", "marking"), (4, "SELL", "marking")}
    assert markers() == manual | marked
    assert window._marking_snapshot is frozen
    before = (window.validation_range, window._validation_range_times,
              list(window._range_replay_entries), window._range_replay_entries_available,
              window.result_summary_label.text(), window.estimated_return_label.text(),
              window.completed_cycles)
    window.marking_display_check.click()
    assert markers() == manual
    assert (window.validation_range, window._validation_range_times,
            list(window._range_replay_entries), window._range_replay_entries_available,
            window.result_summary_label.text(), window.estimated_return_label.text(),
            window.completed_cycles) == before
    window.marking_display_check.click()
    assert window.marking_display_check.isChecked()
    assert markers() == manual | marked


@pytest.mark.parametrize("failure", ["worker", "snapshot", "api", "apply"])
def test_current_range_failure_preserves_pending_marking_and_allows_manual_retry(
        view, monkeypatch, failure):
    r = view
    window = r.window
    window._set_validation_range(1, 3)
    payload = r.payload()
    pending = (window.stock.code, "M1", r.settings.rules_hash,
               window.validation_regular_market_only_check.isChecked())
    window._marking_timeframe_pending = pending
    window._pending_result_settings_snapshot = r.settings
    window.marking_display_check.click()
    assert window.marking_display_check.isChecked()
    generation = window._marking_generation
    errors = []
    r.flow.validation_failed.connect(errors.append)
    if failure == "worker":
        payload["error"] = "offline range worker failure"
    elif failure == "snapshot":
        payload["snapshot"] = None
    elif failure == "api":
        monkeypatch.setattr(window, "apply_range_replay_snapshot_async", None)
        monkeypatch.setattr(window, "apply_range_replay_snapshot", None)
    else:
        def fail_apply(*_args, **_kwargs):
            raise ValueError("offline range view failure")
        monkeypatch.setattr(window, "apply_range_replay_snapshot_async", fail_apply)
    r.flow._on_range_replay_completed(payload)
    assert errors and errors[-1] in window.validation_status_label.text()
    assert not window._marking_timeframe_failed
    assert window._marking_timeframe_pending == pending
    assert window._pending_result_settings_snapshot is r.settings
    assert window._marking_generation == generation
    assert window.marking_display_check.isChecked()
    assert r.key not in r.flow._range_replay_active
    assert not window._range_replay_entries_available
    window._normal_base_requested = True
    window._resume_published_timeframe_marking(pending)
    assert window._marking_timeframe_pending is None
    request = r.requests.pop()
    mark = _snapshot(window, r.rows, r.settings, "UI-only-retry", [])
    assert window.apply_marking_snapshot(request["generation"], request["key"], mark, None)
    window.marking_display_check.click()
    window.marking_display_check.click()
    assert window.marking_display_check.isChecked()
    assert window._marking_snapshot is mark


@pytest.mark.parametrize("error", ["offline range failure", "TR_BUDGET_EXCEEDED"])
def test_range_failure_drains_pending_work_without_releasing_transition_lock(
        view, monkeypatch, error):
    r = view
    r.window._set_validation_range(1, 3)
    payload = r.payload()
    payload["error"] = error
    request = v5.IndicatorFollowSignalValidationRunRequest(r.settings, 5)
    r.window._begin_timeframe_transition_lock(request)
    target = r.window._timeframe_transition_target
    drained = []
    drain = r.flow._start_pending_window_work
    def observe(key):
        drained.append(key)
        drain(key)
    monkeypatch.setattr(r.flow, "_start_pending_window_work", observe)
    r.flow._on_range_replay_completed(payload)
    assert r.window._timeframe_transition_target == target
    assert not r.window._timeframe_transition_overlay.isHidden()
    assert drained == [r.key]
    assert error in r.window.validation_status_label.text()
    assert r.window._validation_budget_blocked == (error == "TR_BUDGET_EXCEEDED")
    assert (r.key in r.flow._v5_manual_retry_required) == (error == "TR_BUDGET_EXCEEDED")


@pytest.mark.parametrize("stale", ["token", "range_generation", "request_generation",
                                    "pool", "session", "source_selection"])
def test_obsolete_range_failure_cannot_mutate_current_marking(view, stale):
    r = view
    r.window._set_validation_range(1, 3)
    payload = r.payload()
    payload["error"] = "obsolete range failure"
    r.window.marking_display_check.click()
    before = (r.window._marking_generation, r.window._marking_key,
              r.window._marking_times, r.window.validation_status_label.text())
    if stale == "token":
        payload["range_token"] = object()
    elif stale == "range_generation":
        payload["generation"] = 0
    elif stale == "request_generation":
        payload["request_generation"] = 6
    elif stale == "pool":
        payload["pool"] = dict(r.pool)
    elif stale == "session":
        payload["source_session"] = object()
    else:
        r.session.source_selection = v5._V5SourceSelection(
            r.window.stock, "M1", r.key, 6, "FULL", r.window.stock.code + "_AL",
            "INTEGRATED", True, r.flow._now_factory())
    r.flow._on_range_replay_completed(payload)
    assert r.window.marking_display_check.isChecked()
    assert not r.window._marking_timeframe_failed
    assert (r.window._marking_generation, r.window._marking_key,
            r.window._marking_times, r.window.validation_status_label.text()) == before
    assert not r.window._range_replay_entries_available


def test_ordinary_range_error_does_not_clear_existing_budget_block(view):
    r = view
    r.window._set_validation_range(1, 3)
    payload = r.payload()
    payload["error"] = "offline range failure"
    r.window._validation_budget_blocked = True
    r.flow._on_range_replay_completed(payload)
    assert r.window._validation_budget_blocked


def test_range_failure_supports_existing_message_only_window_callback(view, case):
    r = view
    r.window._set_validation_range(1, 3)
    payload = r.payload()
    payload["error"] = "offline range failure"
    fake = _FakeWindow(r.window.stock, r.window._signal_validation_seed)
    case.widgets.append(fake)
    fake.validation_range = r.window.validation_range
    fake._validation_range_times = r.window._validation_range_times
    r.flow._open_windows[r.key] = fake
    errors = []
    r.flow.validation_failed.connect(errors.append)
    r.flow._on_range_replay_completed(payload)
    assert fake.errors == errors == ["REPLAY_ERROR: offline range failure"]
    assert r.key not in r.flow._range_replay_active
