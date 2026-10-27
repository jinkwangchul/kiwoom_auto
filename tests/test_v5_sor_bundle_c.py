"""Offline Bundle C handoffs for marking, period sources, and range replay."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as module
from tests.test_indicator_follow_validation_v5_sor_tail_bridge import (
    KEYS, evidence, view,
)
from tests import test_indicator_follow_signal_validation_v2 as legacy
from tests.qt_test_support import ensure_qapplication


def prepared(key="M5"):
    ensure_qapplication()
    legacy.IndicatorFollowSignalValidationV2Test.setUpClass()
    rules = deepcopy(legacy.IndicatorFollowSignalValidationV2Test.rules)
    minutes = view(evidence(key)[1], key)["timeframe_minutes"]
    rules["bar"]["bar_minutes"] = minutes
    rules["validation_timeframe"] = {
        "key": key,
        "kind": "MINUTE" if key.startswith("M") and key != "MO1" else "PERIOD",
        "minutes": minutes,
        "label": key,
    }
    stock = module.ValidationStockRef("005930", "Samsung")
    settings = module.ValidationSettingsSnapshot(rules)
    session = module.ValidationSession(
        module.ValidationRequest(stock, settings, minutes),
        operation_active_reader=lambda: False,
    )
    now, batch = evidence(key)
    raw = dict(view(batch, key), stock=stock, request_id="raw-tr",
               requested_count=2)
    calls = []

    def provider(received, sha, as_of):
        assert received.request.stock == session.request.stock
        assert received.request.timeframe_key == key and as_of == now
        calls.append(sha)
        return replace(batch, history_sha256=sha)

    flow = module.IndicatorFollowSignalValidationV5Flow(
        object(), host=legacy._FakeHost(stock), historical_count=2,
        recent_stock_store=legacy._MemoryRecentStockStore(),
        verified_sor_tail_provider=provider,
    )
    flow._now_factory = lambda: now
    identity = flow._pool_computation_source_identity(raw)
    raw["_dispatch_snapshot"] = (
        raw["candles"], raw["display_candles"], {}, identity,
    )
    return flow, session, raw, identity, calls


@pytest.mark.parametrize("key", KEYS)
def test_period_and_minute_views_require_the_same_verified_source(key):
    flow, session, raw, identity, calls = prepared(key)
    original = deepcopy(raw["candles"])
    result = flow._verified_sor_view_for_session(
        session, raw, dict(raw), flow._now_factory(), identity,
    )
    assert result["candles"][-1] == result["display_candles"][-1]
    assert result["snapshot"].to_rows()[-1]
    assert result["sor_tail_calculation_complete"] is True
    assert raw["candles"] == original
    assert calls == [identity[-1]]


def test_range_replay_reverifies_raw_pool_and_rejects_changed_source():
    flow, session, raw, identity, calls = prepared()
    observed = []

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            observed.append(snapshot.to_rows()[-1])
            raise RuntimeError("STOP_AFTER_VERIFIED_INPUT")

    flow._replay_factory = lambda _session: StopReplay()
    with patch.object(flow, "_range_replay_history_view", return_value=dict(raw)):
        with pytest.raises(RuntimeError, match="STOP_AFTER_VERIFIED_INPUT"):
            flow._prepare_replay_result_from_pool(
                session, raw, history_target=2, chart_range=(0, 1),
                verified_source_identity=identity,
            )
    assert observed and calls == [identity[-1]]
    assert raw["candles"][-1]["close"] == 999

    raw["candles"][-1]["close"] = 998
    with patch.object(flow, "_range_replay_history_view", return_value=dict(raw)):
        with pytest.raises(ValueError, match="SOURCE_CHANGED_AFTER_DISPATCH"):
            flow._prepare_replay_result_from_pool(
                session, raw, history_target=2, chart_range=(0, 1),
                verified_source_identity=identity,
            )
    assert len(calls) == 1


def test_normal_base_replay_keeps_its_verified_view():
    flow, session, raw, identity, _calls = prepared()
    verified = flow._verified_sor_view_for_session(
        session, raw, dict(raw), flow._now_factory(), identity,
    )
    observed = []
    failures = []

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            observed.append(snapshot.to_rows()[-1])
            raise RuntimeError("STOP_NORMAL_REPLAY")

    flow._replay_factory = lambda _session: StopReplay()
    window = SimpleNamespace()
    with patch.object(flow, "_validation_pool_for_session",
                      side_effect=AssertionError("RAW_REPROJECTED")), patch.object(
        flow, "_fail_window", lambda _window, reason: failures.append(reason),
    ):
        flow._replay_from_pool(window, session, verified)
        flow._replay_from_pool(window, session, raw)
    assert observed and "STOP_NORMAL_REPLAY" in failures[0]
    assert failures[1] == "SOR_VERIFIED_BLOCKED:REPLAY_VIEW_UNVERIFIED"


def test_5000_display_plus_900_warmup_keeps_prefix_and_invalidates_signal_key():
    flow, session, raw, _identity, _calls = prepared()
    first = datetime(2026, 10, 1, 0, 0)
    prefix = [
        dict(raw["candles"][0], time=(first + timedelta(minutes=i)).strftime(
            "%Y%m%d%H%M%S"))
        for i in range(5898)
    ]
    raw["candles"] = [*prefix, *raw["candles"]]
    raw["display_candles"] = deepcopy(raw["candles"][-5000:])
    raw["requested_count"] = 5900
    identity = flow._pool_computation_source_identity(raw)
    unverified_key = flow._signal_view_signature(raw, source_identity=identity)
    verified = flow._verified_sor_view_for_session(
        session, raw, dict(raw), flow._now_factory(), identity,
    )
    assert len(verified["candles"]) == 5900
    assert len(verified["display_candles"]) == 5000
    assert verified["candles"][:-1] == raw["candles"][:-1]
    assert verified["candles"][-1] == verified["display_candles"][-1]
    assert flow._signal_view_signature(
        verified, source_identity=identity,
    ) != unverified_key


def test_marking_uses_verified_window_rows_and_blocks_raw_rows():
    flow, session, raw, identity, _calls = prepared()
    verified = flow._verified_sor_view_for_session(
        session, raw, dict(raw), flow._now_factory(), identity,
    )
    window = SimpleNamespace(
        stock=session.request.stock, _marking_generation=1, _marking_key="mark",
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = raw
    flow._request_generation[key] = 1
    seen = []
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    def inspect(_session, _pool, *, prepared_view_pool, **_kwargs):
        seen.append(prepared_view_pool["candles"][-1]["close"])
        raise RuntimeError("STOP_MARKING_REPLAY")

    request = {
        "settings": session.request.settings_snapshot, "calculation": verified["candles"],
        "display": verified["display_candles"], "mixed": False,
        "generation": 1, "key": "mark", "range": (0, 1),
        "times": (verified["candles"][0]["time"], verified["candles"][-1]["time"]),
        "descriptors": (),
    }
    with patch.object(module, "Thread", InlineThread), patch.object(
        flow, "_prepare_replay_result_from_pool", inspect,
    ):
        flow._start_marking(window, request)
        assert seen == [102], payloads[-1]["error"]
        assert "STOP_MARKING_REPLAY" in payloads[-1]["error"]
        request["calculation"] = raw["candles"]
        request["display"] = raw["display_candles"]
        flow._start_marking(window, request)
    assert "MARKING_SOURCE_CHANGED" in payloads[-1]["error"]
    assert seen == [102]


def test_history_browse_verifies_candidate_before_overlap_or_presentation():
    flow, session, old_pool, identity, calls = prepared()
    verified = flow._verified_sor_view_for_session(
        session, old_pool, dict(old_pool), flow._now_factory(), identity,
    )
    snapshot = module.ValidationReplaySnapshot(
        stock=session.request.stock, timeframe_minutes=5, timeframe_key="M5",
        settings_hash=session.request.settings_snapshot.rules_hash,
        historical_request_id="old", evaluated_start_index=0,
        evaluated_end_index=1, dropped_raw_rows_count=0,
        candles=verified["candles"], entries=[],
    )
    captured = {
        "replay_snapshot": snapshot,
        "source_candles": verified["candles"],
        "signal_entries": (),
        "signal_marker_coverage_times": (
            verified["candles"][0]["time"], verified["candles"][-1]["time"],
        ),
    }
    window = SimpleNamespace(
        _normal_base_requested=False, replay_snapshot=snapshot,
        _replay_snapshot=snapshot,
        capture_history_browse_state=lambda _settings: captured,
        apply_history_browse_presentation=lambda *_args, **_kwargs: None,
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._historical_pools[key] = old_pool
    flow._validation_sessions[key] = session
    flow._request_generation[key] = 1
    flow._history_targets[key] = 2
    candidate = deepcopy(old_pool)
    observed = []
    payloads = []
    flow.history_browse_completed.disconnect()
    flow.history_browse_completed.connect(payloads.append)

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    def inspect(_old, new):
        observed.append(new[-1]["close"])
        raise RuntimeError("STOP_AFTER_VERIFIED_BROWSE")

    with patch.object(module, "Thread", InlineThread), patch.object(
        flow, "_validation_pool_for_session", return_value=dict(candidate),
    ), patch.object(flow, "_history_browse_candles_extend_source", inspect), patch.object(
        flow._historical_cache, "capture_store_lease", return_value=None,
    ):
        flow._use_pool_for_history_browse(
            window, session, candidate, evaluation_count=1,
        )
    assert observed == [102]
    assert "STOP_AFTER_VERIFIED_BROWSE" in payloads[-1]["error"]
    assert candidate["candles"][-1]["close"] == 999
    assert len(calls) == 2
