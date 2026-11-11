"""V5 replay keeps acquired native FULL input and displayed bounds.

Legacy regular-only settings cannot exclude acquired off-session bars.
No live feed, broker, or order is involved.
"""
import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_indicator_follow_signal_validation_v5_baseline import case

from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import (
    _flow, _session,
)


TIMES = (
    "20251027080000", "20251027090000", "20251027090500", "20251027154000",
    "20251028080000", "20251028090000", "20251028090500", "20251028154000",
    "20251029080000", "20251029090000", "20251029090500", "20251029154000",
)


def _history():
    return [
        {"time": stamp, "open": 100 + i, "high": 101 + i,
         "low": 99 + i, "close": 100 + i, "volume": 10}
        for i, stamp in enumerate(TIMES)
    ]


def _scoped_source(regular_only, key="M1"):
    stock = _session().request.stock
    minutes = int(key[1:])
    settings = v5.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": minutes},
        "validation_timeframe": {"key": key},
        "validation_market_scope": {"regular_market_only": regular_only},
    })
    session = v5.ValidationSession(
        v5.ValidationRequest(stock, settings, minutes),
        operation_active_reader=lambda: False,
    )
    return session, dict(
        stock=stock, candles=_history(), market_source="INTEGRATED",
        market_data_identity="005930_AL", timeframe_key=key,
        timeframe_minutes=minutes, requested_count=12,
        request_id="OFFLINE_SCOPE_REGRESSION",
    )


def _scoped_flow():
    flow = _flow()
    flow._validation_source_by_code["005930"] = True
    return flow


@pytest.mark.parametrize("key", ["M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240"])
@pytest.mark.parametrize("regular_only", [False, True])
def test_scope_projects_native_history_before_tail(key, regular_only):
    flow = _scoped_flow()
    session, pool = _scoped_source(regular_only, key)
    original = _history()
    view = flow._validation_pool_for_session(session, pool, target_count=4)
    indexes = (8, 9, 10, 11)
    assert view["candles"] == [original[i] for i in indexes]
    assert view["display_candles"] == view["candles"]
    assert view["snapshot"].rows_count == 4
    assert [row["체결시간"] for row in view["snapshot"].to_rows()] == [TIMES[i] for i in indexes]
    assert pool["candles"] == original


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("ranged", [False, True])
def test_legacy_scope_replay_keeps_full_interior_and_hidden_prefix(prepared, ranged):
    flow = _scoped_flow()
    session, pool = _scoped_source(True)
    pool["display_candles"] = [pool["candles"][i] for i in (8, 9, 10, 11)]
    result = flow._prepare_replay_result_from_pool(
        session, pool, history_target=4, evaluation_count=4,
        chart_range=(0, 3) if ranged else None,
        prepared_view_pool=pool if prepared else None,
    )
    assert result.ok and result.snapshot is not None
    snapshot = result.snapshot
    rows = snapshot.to_candles()
    assert rows == _history()
    assert rows[snapshot.evaluated_start_index:snapshot.evaluated_end_index + 1] == [
        _history()[i] for i in (8, 9, 10, 11)
    ]


def test_legacy_scope_normal_base_keeps_full_warmup(monkeypatch):
    flow = _scoped_flow()
    session, pool = _scoped_source(True)
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    display_view = flow._validation_pool_for_session(session, pool, target_count=4)
    for prepared in (None, display_view):
        view, deficit = flow._normal_base_history_view(session, pool, 2, prepared)
        assert deficit == 0
        assert view["candles"] == [_history()[i] for i in (8, 9, 10, 11)]
        assert view["display_candles"] == [_history()[i] for i in (10, 11)]
        assert view["snapshot"].rows_count == 4


@pytest.mark.parametrize("key", ["M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240"])
def test_legacy_scope_native_history_growth_counts_full_warmup(key):
    flow = _scoped_flow()
    session, pool = _scoped_source(True, key)
    calculated = flow._normal_base_native_m5_calculation_rows(session, pool, pool["candles"])
    assert calculated == _history()


def test_legacy_scope_fallback_replay_keeps_full_input(monkeypatch):
    from types import SimpleNamespace

    flow = _scoped_flow()
    session, pool = _scoped_source(True)
    received, errors = [], []
    window = SimpleNamespace(set_replay_snapshot=received.append)
    monkeypatch.setattr(flow, "_fail_window", lambda _w, why: errors.append(why))
    flow._history_targets[id(window)] = 4
    flow._replay_from_pool(window, session, pool, evaluation_count=4)
    assert not errors
    assert len(received) == 1
    snapshot = received[0]
    assert snapshot.to_candles() == _history()
    assert snapshot.to_candles()[snapshot.evaluated_start_index:snapshot.evaluated_end_index + 1] == [
        _history()[i] for i in (8, 9, 10, 11)]


@pytest.mark.parametrize("mutation", ["ohlcv", "display", "identity", "request"])
def test_full_display_axis_mutations_reach_their_own_guards(monkeypatch, mutation):
    from copy import deepcopy
    from datetime import datetime
    from types import SimpleNamespace

    flow = _scoped_flow()
    session, pool = _scoped_source(False)
    view = deepcopy(flow._validation_pool_for_session(session, pool, target_count=4))
    view["display_candles"] = deepcopy(view["display_candles"])
    assert view["candles"] == [_history()[i] for i in (8, 9, 10, 11)]
    now = datetime.fromisoformat("2026-10-30T18:00:00+09:00")
    flow._now_factory = lambda: now
    window = SimpleNamespace(stock=session.request.stock,
        validation_range=(0, 3), _validation_range_times=(TIMES[8], TIMES[11]),
        _marking_current_settings=lambda: session.request.settings_snapshot)
    session.source_selection = v5._select_v5_source(
        window.stock, "M1", True, False, now, window_key=id(window), generation=3)
    flow._open_windows[id(window)] = window
    flow._historical_pools[id(window)] = view
    flow._validation_sessions[id(window)] = session
    flow._request_generation[id(window)] = 3
    flow._pool_dispatch_snapshot(view)
    publication = view["_dispatch_snapshot"]
    window._v5_marking_display_publication = (view, session, 3, publication)
    key_calls, identity_calls = [], []
    def marking_key(settings, times):
        key_calls.append(times)
        return v5.IndicatorFollowSignalValidationV5Window._marking_source_key_for_candles(
            window.stock.code, settings, times, regular_only=False,
            calculation_candles=view["candles"], display_candles=view["display_candles"])
    window._marking_source_key = marking_key
    source_identity = flow._pool_computation_source_identity
    def observe_identity(candidate):
        identity = source_identity(candidate)
        identity_calls.append((candidate, identity))
        return identity
    monkeypatch.setattr(flow, "_pool_computation_source_identity", observe_identity)
    assert flow._selected_range_binding_identity(window) is not None
    assert len(key_calls) == len(identity_calls) == 1
    key_calls.clear()
    identity_calls.clear()

    if mutation == "ohlcv":
        view["candles"][0]["close"] += 1
    elif mutation == "display":
        view["display_candles"] = [view["display_candles"][0]] * 4
    elif mutation == "identity":
        view["market_data_identity"] = "005930_NX"
    else:
        view["request_id"] = "ANOTHER_NATIVE_REQUEST"
    assert "_regular_minute_source_view" not in view
    assert flow._source_selection_is_current(session.source_selection)
    assert flow._marking_publication_is_current(window, window._v5_marking_display_publication)
    assert flow._selected_range_binding_identity(window) is None
    assert len(identity_calls) == 1 and identity_calls[0][0] is view
    if mutation == "display":
        assert identity_calls[0][1] == publication[3]
        assert key_calls == [(TIMES[8], TIMES[11])]
    else:
        assert identity_calls[0][1] != publication[3]
        assert key_calls == []
    assert publication[2]["candles"] == [_history()[i] for i in (8, 9, 10, 11)]
    assert publication[2]["display_candles"] == [_history()[i] for i in (8, 9, 10, 11)]


def test_legacy_projected_minute_marker_is_rejected():
    flow = _scoped_flow()
    session, pool = _scoped_source(False)
    view = flow._validation_pool_for_session(session, pool, target_count=4)
    assert view["candles"] == [_history()[i] for i in (8, 9, 10, 11)]
    legacy = dict(view, _regular_minute_source_view=pool)
    with pytest.raises(ValueError, match="REGULAR_MINUTE_SOURCE_UNVERIFIED"):
        flow._validation_pool_for_session(session, legacy, target_count=4)


def test_legacy_scope_direct_current_receipt_survives_range_revalidation():
    from datetime import datetime
    from tests.test_v5_direct_forming_tail_20261026 import _result, ROWS

    flow = _scoped_flow()
    session, _ = _scoped_source(True, "M3")
    at = datetime.fromisoformat("2026-10-26T11:21:47+09:00")
    stamps = ("20261023080000", "20261023090000", "20261026111800", "20261026112100")
    rows = [dict(ROWS[min(i, 2)], time=stamp) for i, stamp in enumerate(stamps)]
    completed, forming, query = flow._direct_selected_history_parts(_result(session, at, rows).snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=5900, request_id=query["request_id"],
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    flow._now_factory = lambda: at
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5900)
    current = flow._sor_m1_calculation_view(session, pool, view, frozen)
    expected = rows
    assert current["candles"] == expected
    result = flow._prepare_replay_result_from_pool(
        session, pool, history_target=4, chart_range=(0, 3),
        prepared_view_pool=current, sor_m1_calculation_snapshot=frozen,
        expected_sor_m1_calculation=expected,
    )
    assert result.ok and result.snapshot.to_candles() == expected
    assert pool["candles"] == completed


@pytest.mark.parametrize("regular_only", [True, False])
def test_minute_legacy_scope_reentry_requests_full(case, regular_only):
    from tests.test_indicator_follow_signal_validation_v5_baseline import window_for
    from tests.test_indicator_follow_signal_validation_v5_history_hash_ownership import _snapshot

    window = window_for(case)
    window._restore_validation_preferences({"regular_market_only": regular_only})
    rows = _history()
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    settings = window._signal_validation_seed.settings_snapshot
    window.install_normal_base(settings, "M1")
    old = _snapshot(window, rows, settings, "previous-full-scope", [])
    window.set_replay_snapshot(old)
    requests = []
    window.validation_run_requested.connect(requests.append)
    window._request_validation(normal_base=True)
    assert len(requests) == 1
    assert requests[0].settings_snapshot.to_dict()["validation_market_scope"] == {
        "regular_market_only": False,
    }
    assert window.validation_range is None
    assert window._marking_times is None
    assert window._replay_snapshot is old
    assert window._pending_result_settings_snapshot is requests[0].settings_snapshot
    assert window.canvas._candles == window._candles


def test_minute_legacy_on_reentry_replaces_inflight_before_candles_arrive(case):
    from tests.test_indicator_follow_signal_validation_v5_baseline import window_for

    window = window_for(case)
    previous = window._request_validation(normal_base=True)
    assert previous is not None
    requests = []
    window.validation_run_requested.connect(requests.append)
    window._restore_validation_preferences({"regular_market_only": True})
    window._request_validation(normal_base=True)
    assert len(requests) == 1
    assert requests[0].settings_snapshot.rules_hash == previous.settings_snapshot.rules_hash
    assert requests[0].settings_snapshot.to_dict()["validation_market_scope"]["regular_market_only"] is False


@pytest.mark.parametrize(
    "display_indexes,evaluation_count,expected_source_bounds",
    [
        ((1, 2, 5, 6, 9, 10), 6, (1, 10)),
        ((1, 2, 5, 6, 9, 10), 2, (9, 10)),
        (tuple(range(12)), 6, (6, 11)),
    ],
)
def test_replay_without_range_uses_display_axis_bounds(
    display_indexes, evaluation_count, expected_source_bounds,
):
    flow = _flow()
    session = _session()
    candles = _history()
    pool = {
        "stock": session.request.stock,
        "candles": candles,
        "display_candles": [candles[index] for index in display_indexes],
        "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL",
        "timeframe_key": "M1",
        "timeframe_minutes": 1,
        "requested_count": len(candles),
        "request_id": "OFFLINE_VERIFIED_AXIS",
    }
    result = flow._prepare_replay_result_from_pool(
        session, pool, history_target=len(candles),
        evaluation_count=evaluation_count, prepared_view_pool=pool,
    )
    assert result.ok and result.snapshot is not None
    snapshot = result.snapshot
    evaluated = snapshot.to_candles()[
        snapshot.evaluated_start_index:snapshot.evaluated_end_index + 1
    ]
    assert (evaluated[0]["time"], evaluated[-1]["time"]) == tuple(
        TIMES[index] for index in expected_source_bounds
    )


def test_fallback_replay_uses_same_regular_display_bounds(monkeypatch):
    """The re-entry replay path must not silently select the FULL tail."""
    from types import SimpleNamespace

    flow = _flow()
    session = _session()
    candles = _history()
    visible = [candles[index] for index in (1, 2, 5, 6, 9, 10)]
    pool = {
        "stock": session.request.stock,
        "candles": candles,
        "display_candles": visible,
        "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL",
        "timeframe_key": "M1",
        "timeframe_minutes": 1,
        "requested_count": len(candles),
        "request_id": "OFFLINE_VERIFIED_AXIS",
    }
    # Projection is independent of the replay indexing under test.
    monkeypatch.setattr(
        flow, "_validation_pool_for_session", lambda *_args, **_kwargs: pool,
    )
    received = []
    errors = []
    window = SimpleNamespace(set_replay_snapshot=received.append)
    monkeypatch.setattr(flow, "_fail_window", lambda _window, why: errors.append(why))
    flow._history_targets[id(window)] = len(candles)

    flow._replay_from_pool(window, session, pool, evaluation_count=len(visible))

    assert not errors
    assert len(received) == 1
    snapshot = received[0]
    evaluated = snapshot.to_candles()[
        snapshot.evaluated_start_index:snapshot.evaluated_end_index + 1
    ]
    assert (evaluated[0]["time"], evaluated[-1]["time"]) == (
        TIMES[1], TIMES[10],
    )


def test_m5_closing_price_display_only_bar_never_enters_replay_calculation():
    """The 15:35 native display row has no matching calculation candle."""
    import gui_indicator_follow_signal_validation_v5_flow as v5

    flow = _flow()
    stock = _session().request.stock
    settings = v5.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 5},
        "validation_timeframe": {"key": "M5"},
    })
    session = v5.ValidationSession(
        v5.ValidationRequest(stock, settings, 5),
        operation_active_reader=lambda: False,
    )
    times = ("20251027090000", "20251027090500", "20251027091000")
    candles = [
        {"time": stamp, "open": 100 + i, "high": 101 + i,
         "low": 99 + i, "close": 100 + i, "volume": 10}
        for i, stamp in enumerate(times)
    ]
    special = dict(
        candles[-1], time="20251027153500",
        display_only_special=v5.NATIVE_CLOSING_PRICE_SPECIAL,
    )
    pool = {
        "stock": stock, "candles": candles,
        "display_candles": [*candles, special],
        "display_only_integrated": True,
        "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL",
        "timeframe_key": "M5", "timeframe_minutes": 5,
        "requested_count": len(candles),
        "request_id": "OFFLINE_NATIVE_M5",
    }
    result = flow._prepare_replay_result_from_pool(
        session, pool, history_target=4, evaluation_count=2,
        prepared_view_pool=pool,
    )
    assert result.ok and result.snapshot is not None
    snapshot = result.snapshot
    evaluated = snapshot.to_candles()[
        snapshot.evaluated_start_index:snapshot.evaluated_end_index + 1
    ]
    assert [row["time"] for row in evaluated] == ["20251027091000"]

    with pytest.raises(ValueError, match="NO_CALCULATION_CANDLES_IN_RANGE"):
        flow._prepare_replay_result_from_pool(
            session, pool, history_target=4, evaluation_count=1,
            prepared_view_pool=pool,
        )
