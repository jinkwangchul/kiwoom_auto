"""Offline native receipts and real replay; no live broker evidence."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_v5_wm_direct_current_20261102 import wm, NOW
from tests import test_v5_wm_direct_current_20261102 as wm_tests
from tests.test_display_only_integrated_history import _frozen_source_plan
from routines.지표추종매매 import routine_validation_replay as replay


def run(c, chart_range=(0, 0), times=None, expected=None):
    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        return c.flow._prepare_replay_result_from_pool(
            c.session, c.pool, history_target=2, prepared_view_pool=c.view,
            chart_range=chart_range, chart_range_times=times,
            expected_sor_m1_calculation=c.rows[-2:] if expected is None else expected)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_completed_range_keeps_prefix_and_indices_after_receipt_expires(wm, monkeypatch, key, regular):
    c = wm.acquire(key, regular)
    before = deepcopy((c.pool, c.view, c.query))
    ranges = []
    original = replay.ValidationHistoricalReplay._valid_range
    monkeypatch.setattr(replay.ValidationHistoricalReplay, "_valid_range",
        staticmethod(lambda a, b, n: ranges.append((a, b, n)) or original(a, b, n)))
    fresh = run(c)
    c.clock[0] = NOW + timedelta(seconds=120)
    stale = run(c)
    assert fresh.ok and stale.ok
    assert fresh.snapshot.to_candles() == stale.snapshot.to_candles() == c.rows[-2:-1]
    assert fresh.snapshot.to_entries() == stale.snapshot.to_entries()
    assert ranges == [(0, 0, 1), (0, 0, 1)]
    assert (c.pool, c.view, c.query) == before and len(c.calls) == 1


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
@pytest.mark.parametrize("chart_range", [(1, 1), (0, 1), None])
def test_current_spanning_and_no_range_keep_59_60_guard(wm, key, regular, chart_range):
    c = wm.acquire(key, regular)
    c.clock[0] = NOW + timedelta(seconds=59)
    assert run(c, chart_range).ok
    c.clock[0] = NOW + timedelta(seconds=60)
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        run(c, chart_range)


@pytest.mark.parametrize("defect", ["receipt", "rehash", "display", "expected", "generation", "session", "missing-time", "bounds"])
@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_stale_completed_range_proof_fails_closed(wm, key, regular, defect):
    from tests.test_v5_wm_direct_current_20261102 import _digest
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(seconds=120)
    expected = deepcopy(c.rows[-2:])
    times, bounds = None, (0, 0)
    if defect == "receipt":
        c.flow._verified_al_period_receipts.clear()
    elif defect == "rehash":
        c.pool["candles"][0]["close"] = 999
        c.query["source_content_sha256"] = _digest(c.pool["candles"])
    elif defect == "display":
        c.view["display_candles"][0] = dict(c.rows[-2], close=999)
    elif defect == "expected":
        expected[0]["close"] = 999
    elif defect == "generation":
        c.flow._request_generation[c.session.source_selection.window_key] += 1
    elif defect == "session":
        c.broker_session.login_session_id = "OTHER"
    elif defect == "missing-time":
        times = ("19990101000000", c.rows[-2]["time"])
    else:
        bounds = (0, 2)
    with pytest.raises(ValueError):
        run(c, bounds, times, expected)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_rollover_never_promotes_received_current_bar(wm, key, regular):
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(days=40)
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        run(c, (1, 1))
    assert run(c).ok


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_worker_captures_display_before_dispatch_and_keeps_hidden_prefix(wm, monkeypatch, key, regular):
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(seconds=120)
    flow = c.flow
    window = next(iter(flow._open_windows.values()))
    window._calculation_candles = deepcopy(c.rows[-2:])
    window_key = id(window)
    flow._validation_sessions[window_key] = c.session
    flow._historical_pools[window_key] = c.pool
    flow._range_replay_generation[window_key] = 1
    captured, payloads, jobs = [], [], []
    prepare = flow._prepare_replay_result_from_pool

    def observe(*args, **kwargs):
        captured.append(kwargs["expected_sor_m1_calculation"])
        return prepare(*args, **kwargs)

    monkeypatch.setattr(flow, "_prepare_replay_result_from_pool", observe)
    monkeypatch.setattr(flow, "_resolve_range_request_times", lambda _r: True)
    monkeypatch.setattr(flow, "_range_request_is_current", lambda _r: True)
    monkeypatch.setattr(flow, "_range_computation_is_current", lambda *_a: True)
    monkeypatch.setattr(flow, "_emit_range_replay_completed", payloads.append)
    monkeypatch.setattr(v5, "Thread", lambda **kw: SimpleNamespace(start=lambda: jobs.append(kw["target"])))
    request = dict(window_key=window_key, window=window, pool=c.pool,
        session=c.session, source_session=c.session, generation=1,
        request_generation=flow._request_generation[window_key], token=object(),
        history_target=2, range=(0, 0), range_times=(c.rows[-2]["time"],) * 2)
    flow._start_range_replay(request)
    assert len(jobs) == 1
    window._calculation_candles[0]["close"] = 999
    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        jobs.pop()()
    assert captured == [c.rows[-2:]]
    assert payloads[0]["error"] == "", payloads
    snapshot = payloads[0]["snapshot"]
    assert snapshot.to_candles() == c.rows[:3]
    assert (snapshot.evaluated_start_index, snapshot.evaluated_end_index) == (2, 2)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
@pytest.mark.parametrize("defect", [d for d in wm_tests.DEFECTS
                                  if d not in {"expired", "previous-date"}])
def test_existing_receipt_defects_also_block_completed_range(wm, monkeypatch, key, regular, defect):
    monkeypatch.setattr(wm_tests, "_calculate", lambda c, expected=None: run(c, expected=expected))
    wm_tests.test_unverified_current_fails_closed(wm, key, regular, defect, monkeypatch)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
@pytest.mark.parametrize("defect", ["incomplete-prefix", "duplicate", "future", "last-completed"])
def test_bound_native_prefix_still_requires_completion_and_order(wm, key, regular, defect):
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(seconds=120)
    # Model an internally hash-bound native response, including its live receipt.
    # A valid hash cannot establish chronological order or candle completion.
    if defect == "incomplete-prefix":
        c.pool["candles"][-2]["is_complete"] = False
    elif defect == "duplicate":
        c.pool["candles"][-2]["time"] = c.pool["candles"][-3]["time"]
    elif defect == "future":
        c.pool["candles"][-2]["time"] = "20270101000000"
    else:
        c.query["last_completed_time"] = c.rows[-1]["time"]
    c.query["source_content_sha256"] = wm_tests._digest(c.pool["candles"])
    c.receipt["source_content_sha256"] = c.query["source_content_sha256"]
    c.view["candles"] = deepcopy(c.pool["candles"][-2:])
    c.view["display_candles"] = deepcopy(c.view["candles"])
    with pytest.raises(ValueError):
        run(c, expected=deepcopy(c.view["candles"]))


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_missing_worker_display_snapshot_cannot_authorize_completed_range(wm, key, regular):
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(seconds=120)
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        c.flow._prepare_replay_result_from_pool(c.session, c.pool,
            history_target=2, prepared_view_pool=c.view, chart_range=(0, 0))


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True])
def test_incomplete_hidden_warmup_prefix_is_rejected(wm, key, regular):
    c = wm.acquire(key, regular)
    c.clock[0] += timedelta(seconds=120)
    c.pool["candles"][0]["is_complete"] = False
    c.query["source_content_sha256"] = wm_tests._digest(c.pool["candles"])
    c.receipt["source_content_sha256"] = c.query["source_content_sha256"]
    c.view = c.flow._range_replay_history_view(c.session, c.pool, 2)
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        run(c)
