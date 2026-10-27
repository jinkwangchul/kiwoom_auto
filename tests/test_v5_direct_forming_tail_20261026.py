"""Samsung's direct AL M3 response contains an unfinished selected-period tail."""

from datetime import datetime, timedelta
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import pytest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from gui_indicator_follow_signal_validation_v5_flow import (
    IndicatorFollowSignalValidationV5Flow,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from indicator_follow_signal_validation_historical_cache import ValidationHistoricalCacheEntry
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
from tests.test_indicator_follow_v2_display_only_integrated_flow import (
    ValidationRequest, ValidationSession, ValidationSettingsSnapshot, ValidationStockRef,
)


AS_OF = datetime(2026, 10, 26, 16, 21, 47, tzinfo=ZoneInfo("Asia/Seoul"))
ROWS = [
    {"time": "20261023153900", "open": 215500.0, "high": 215500.0,
     "low": 215000.0, "close": 215500.0, "volume": 57745.0},
    {"time": "20261026161800", "open": 213000.0, "high": 213500.0,
     "low": 213000.0, "close": 213000.0, "volume": 7204.0},
    {"time": "20261026162100", "open": 213500.0, "high": 213500.0,
     "low": 213000.0, "close": 213000.0, "volume": 1066.0},
]


def _session():
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
    })
    return ValidationSession(ValidationRequest(stock, settings, 3),
                             operation_active_reader=lambda: False)


def _result(session, at, rows=ROWS, identity="005930_AL",
            request_id="opt10080_005930_direct_m3"):
    snapshot = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=3, timeframe_key="M3",
        requested_count=len(rows), request_id=request_id,
        rows=IndicatorFollowSignalValidationV5Flow._historical_rows_from_candles(rows),
        market_data_identity=identity, market_source="INTEGRATED",
        request_as_of=at.isoformat(),
    )
    return ValidationHistoricalResult(True, snapshot)


def test_premarket_direct_d1_placeholder_does_not_block_normal_base(monkeypatch, tmp_path):
    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1}, "validation_timeframe": {"key": "D1"},
    })
    session = ValidationSession(ValidationRequest(stock, settings, 1),
                                operation_active_reader=lambda: False)
    now = datetime(2026, 10, 27, 7, 20, 42, tzinfo=ZoneInfo("Asia/Seoul"))
    prior = {"time": "20261026000000", "open": 211000, "high": 214000,
             "low": 210000, "close": 213000, "volume": 120000}
    placeholder = {"time": "20261027000000", "open": 213000,
                   "high": 213000, "low": 213000, "close": 213000, "volume": 0}
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key="D1",
        requested_count=2, request_id="opt10081_005930_direct_d1",
        rows=flow._historical_rows_from_candles([prior, placeholder]),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    flow._validation_source_by_code[stock.code] = True
    flow._now_factory = lambda: now
    installed = []
    monkeypatch.setattr(flow, "_persist_history", lambda *args, **kwargs: True)
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda *args, **kwargs:
                        installed.append(args[2]) or True)

    assert flow._handle_historical_result(
        SimpleNamespace(), session, ValidationHistoricalResult(True, snapshot),
        evaluation_count=2, cache_requested_count=2,
    )
    pool = installed[0]
    view, _ = flow._normal_base_history_view(session, pool, 2)
    calculated = flow._sor_m1_calculation_view(session, pool, view, None,
                                               normal_base_only=True)
    assert calculated["candles"][-1] == prior
    assert all(row["time"] != placeholder["time"] for row in calculated["candles"])

    traded = dict(placeholder, open=214000, high=215000, close=215000, volume=100)
    traded_snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key="D1",
        requested_count=2, request_id=snapshot.request_id,
        rows=flow._historical_rows_from_candles([prior, traded]),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    admitted, _, _ = flow._direct_selected_history_parts(traded_snapshot)
    assert admitted[-1] == traded

    from indicator_follow_signal_validation_historical_cache import (
        IndicatorFollowSignalValidationHistoricalCache,
    )
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    assert type(flow)._persist_history(
        flow, stock, 1, "D1", 2, [prior, placeholder],
        market_data_identity="005930_AL", market_source="INTEGRATED",
    )
    restored = flow._cached_history(stock, 1, "D1", 2)
    assert restored is not None
    assert list(restored.candles) == [prior]


def _premarket_m1_selected_case():
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _registered_m1_calculation_case,
    )

    flow, session, _old_pool, market, _ = _registered_m1_calculation_case()
    now = datetime(2026, 10, 27, 7, 55, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    flow._now_factory = lambda: now
    market._display_timeframe_candles.pop(("005930", 1), None)
    market._display_timeframe_session_identity.pop(("005930", 1), None)
    rows = [
        {"time": "20261026195800", "open": 100, "high": 101,
         "low": 99, "close": 100, "volume": 3},
        {"time": "20261026195900", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 4},
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=2, request_id="selected-al-m1-20261027",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    completed, forming, query = flow._direct_selected_history_parts(snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=2, request_id=snapshot.request_id,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["availability_state"] == "SOR_M1_SOURCE_UNAVAILABLE"
    view = flow._validation_pool_for_session(session, pool, target_count=2)
    return flow, session, pool, view, frozen, query, snapshot


def test_premarket_m1_uses_fresh_selected_completed_history_when_host_m1_is_empty():
    flow, session, pool, view, frozen, query, snapshot = _premarket_m1_selected_case()
    assert flow._sor_m1_calculation_view(session, pool, view, frozen) is view
    assert query["request_id"] == snapshot.request_id


def test_premarket_samsung_m1_requires_fresh_selected_result_over_cache(monkeypatch):
    flow, session, _pool, _view, _frozen, _query, _snapshot = _premarket_m1_selected_case()
    cached = object()
    flow._historical_cache = SimpleNamespace(load=lambda *_args, **_kwargs: cached)
    monkeypatch.setattr(flow, "_cached_history_for_source", lambda *_args, **_kwargs: cached)
    assert flow._cached_history(session.request.stock, 1, "M1", 5900) is None
    flow._now_factory = lambda: datetime(
        2026, 10, 27, 9, 5, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert flow._cached_history(session.request.stock, 1, "M1", 5900) is cached


@pytest.mark.parametrize("invalid", [
    "active_session", "active_forming", "invalid_hash", "legacy_cache",
])
def test_missing_host_m1_still_blocks_active_or_unproven_direct_history(invalid):
    flow, session, pool, view, _frozen, _query, _snapshot = _premarket_m1_selected_case()
    if invalid == "active_session":
        flow._now_factory = lambda: datetime(
            2026, 10, 27, 9, 5, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    elif invalid == "active_forming":
        now = datetime(2026, 10, 27, 9, 5, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        flow._now_factory = lambda: now
        rows = [pool["candles"][-1], {
            "time": "20261027090500", "open": 102, "high": 103,
            "low": 101, "close": 102, "volume": 1,
        }]
        snapshot = ValidationHistoricalSnapshot(
            stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
            requested_count=2, request_id="selected-active-al-m1",
            rows=flow._historical_rows_from_candles(rows),
            market_data_identity="005930_AL", market_source="INTEGRATED",
            request_as_of=now.isoformat(),
        )
        completed, forming, query = flow._direct_selected_history_parts(snapshot)
        assert rows[-1] not in completed
        pool = flow._pool_from_candles(
            session, completed, requested_count=2, request_id=snapshot.request_id,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            direct_selected_query=query, selected_direct_forming_snapshot=forming,
        )
        view = flow._validation_pool_for_session(session, pool, target_count=1)
    elif invalid == "invalid_hash":
        pool["direct_selected_query"]["source_content_sha256"] = "0" * 64
    else:
        pool.pop("direct_selected_query")
        pool["request_id"] = "VALIDATION_CACHE_RESTORE"
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, pool, view, frozen)


def test_full_m5_source_with_closing_display_rows_starts_normal_base_without_refetch():
    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    flow._validation_source_by_code[stock.code] = True
    settings = ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}})
    session = ValidationSession(ValidationRequest(stock, settings, 5),
                                operation_active_reader=lambda: False)
    rows = []
    for index in range(5899):
        day = datetime(2026, 7, 1) + timedelta(days=index // 75)
        stamp = (day.replace(hour=15, minute=35) if index % 75 == 74
                 else day.replace(hour=8) + timedelta(minutes=5 * (index % 75)))
        rows.append({"time": stamp.strftime("%Y%m%d%H%M%S"), "open": 100,
                     "high": 100, "low": 100, "close": 100, "volume": 1})
    rows.append(dict(rows[-1], time="20261026195500"))
    query = {
        "stock_code": stock.code, "timeframe_key": "M5", "timeframe_minutes": 5,
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "request_id": "direct-m5", "request_as_of": "2026-10-26T20:00:00+09:00",
        "last_completed_time": rows[-1]["time"],
    }
    pool = flow._pool_from_candles(
        session, rows, requested_count=5900, request_id="direct-m5",
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query,
    )
    view, deficit = flow._normal_base_history_view(session, pool, 5000)
    assert deficit > 0
    assert any(row.get("display_only_special") for row in view["display_candles"])

    class Window:
        pass

    window = Window()
    window._normal_base_requested = True
    assert flow._ensure_normal_base_history(window, session, pool, 5000) is False


def test_completed_direct_m3_marking_replays_selected_al_rows(monkeypatch):
    import gui_indicator_follow_signal_validation_v5_flow as flow_module

    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    flow._now_factory = lambda: AS_OF.replace(minute=24, second=0)
    pool = flow._pool_from_candles(
        session, deepcopy(ROWS), requested_count=5900,
        request_id="opt10080_005930_direct_m3",
        market_data_identity="005930_AL", market_source="INTEGRATED")
    flow._pool_dispatch_snapshot(pool)
    tooltips = []
    window = SimpleNamespace(
        stock=session.request.stock, _marking_generation=1, _marking_key="mark",
        _disable_marking=lambda **_kwargs: None,
        marking_display_check=SimpleNamespace(setToolTip=tooltips.append))
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._request_generation[key] = 1
    observed = []
    payloads = []

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            observed.append(snapshot.to_rows())
            raise RuntimeError("STOP_AFTER_DIRECT_M3_INPUT")

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    flow._replay_factory = lambda _session: StopReplay()
    monkeypatch.setattr(flow_module, "Thread", InlineThread)
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)
    request = dict(
        settings=session.request.settings_snapshot, calculation=deepcopy(ROWS),
        display=deepcopy(ROWS[-2:]), generation=1, key="mark", range=(0, 1),
        times=(ROWS[-2]["time"], ROWS[-1]["time"]), descriptors=())

    flow._start_marking(window, request)

    assert observed == [flow._historical_rows_from_candles(ROWS)], (
        payloads[-1]["error"] if payloads else tooltips)
    assert payloads[-1]["error"] == "STOP_AFTER_DIRECT_M3_INPUT"


def test_direct_m3_acquisition_keeps_current_response_separate_from_completed_view(monkeypatch):
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    # A delayed callback cannot make the query-time partial row complete.
    flow._now_factory = lambda: AS_OF.replace(minute=30)
    installed = []
    persisted = []
    monkeypatch.setattr(flow, "_persist_history", lambda *args, **kwargs:
                        persisted.append(args[4]) or True)
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda *args, **kwargs:
                        installed.append(args[2]) or True)

    assert flow._handle_historical_result(
        SimpleNamespace(), session, _result(session, AS_OF),
        evaluation_count=3, cache_requested_count=5900,
    )
    pool = installed[0]
    assert pool["candles"] == ROWS[:-1]
    assert persisted[0] == ROWS[:-1]
    assert pool["selected_direct_forming_snapshot"]["candle"] == ROWS[-1]
    assert pool["selected_direct_forming_snapshot"]["request_as_of"] == AS_OF.isoformat()
    flow._now_factory = lambda: AS_OF
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5900)
    calculated = flow._sor_m1_calculation_view(session, pool, view, frozen)
    assert calculated["candles"] == ROWS
    assert calculated["display_candles"].count(ROWS[-1]) == 1


def test_direct_current_stays_usable_in_same_bucket_and_never_ages_into_history():
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    completed, forming, query = flow._direct_selected_history_parts(_result(session, AS_OF).snapshot)
    pool = flow._pool_from_candles(session, completed, requested_count=5900,
        request_id="opt10080_005930_direct_m3", market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        selected_direct_forming_snapshot=forming)
    for minute in (21, 22):
        flow._now_factory = lambda minute=minute: AS_OF.replace(minute=minute)
        frozen = flow._sor_m1_calculation_snapshot(session, pool)
        view = flow._validation_pool_for_session(session, pool, target_count=5900)
        assert flow._sor_m1_calculation_view(session, pool, view, frozen)["candles"][-1] == ROWS[-1]
    flow._now_factory = lambda: AS_OF.replace(minute=30)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5900)
    with pytest.raises(ValueError, match="SOR_DIRECT_HISTORY_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, pool, view, frozen)
    assert pool["candles"] == ROWS[:-1]


@pytest.mark.parametrize("changed", [
    None, "display_corrupt", "forged_query", "source_flip", "broken_basis",
    "stale_generation", "stale_session", "stale_source", "stale_view",
    "stale_broken_basis", "native_same_bar",
])
def test_expired_direct_m3_range_replay_uses_its_published_verified_view(monkeypatch, tmp_path, changed):
    import gui_indicator_follow_signal_validation_v5_flow as flow_module

    at = datetime(2026, 10, 26, 19, 59, 54, 424226,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    rows = [ROWS[0],
            {"time": "20261026195400", "open": 213500.0, "high": 214500.0,
             "low": 213500.0, "close": 214000.0, "volume": 46239.0},
            {"time": "20261026195700", "open": 214000.0, "high": 214500.0,
             "low": 213500.0, "close": 214000.0, "volume": 59278.0}]
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    completed, forming, query = flow._direct_selected_history_parts(
        _result(session, at, rows, request_id="opt10080_005930_195954425227").snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=5900,
        request_id="opt10080_005930_195954425227",
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming)
    calculation_at = at.replace(second=58, microsecond=0)
    flow._now_factory = lambda: calculation_at
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[1] == calculation_at
    assert forming["request_as_of"] == at.isoformat()
    direct = flow._sor_m1_calculation_view(
        session, pool, flow._validation_pool_for_session(
            session, pool, target_count=5900), frozen)
    published, _ = flow._normal_base_history_view(session, pool, 3, direct)
    window = SimpleNamespace(
        canvas=None, _marking_times=None, _normal_base_requested=True,
        _calculation_candles=[], _candles=[], validation_range=(0, 2),
        _validation_range_times=(rows[0]["time"], rows[-1]["time"]),
    )
    window.set_historical_candle_pool = lambda candles, **_kw: (
        setattr(window, "_calculation_candles", deepcopy(candles)))
    window.set_historical_display_candles = lambda candles, **_kw: (
        setattr(window, "_candles", deepcopy(candles)))
    window.install_normal_base = lambda *_args, **_kw: setattr(
        window, "_normal_base_ready", True)
    key = id(window)
    flow._open_windows[key] = window
    flow._historical_pools[key] = pool
    flow._validation_sessions[key] = session
    flow._request_generation[key] = 1
    flow._range_replay_generation[key] = 1
    flow._history_targets[key] = 3
    flow._pool_dispatch_snapshot(pool)
    monkeypatch.setattr(flow, "_ensure_integrated_display_sidecar", lambda *_a: None)
    assert flow._apply_pool_to_window(
        window, session, pool, evaluation_count=1, signal_entries=None,
        prepared_view_pool=published, normal_base_presentation=((), {}))
    assert window._calculation_candles == rows

    if changed == "native_same_bar":
        from stock_market_source_plan import initialize_stock_market_source_plan
        initialize_stock_market_source_plan(
            tmp_path, "005930", "Samsung", trading_day=at.date(),
            decided_at=at.replace(hour=8, minute=0), nxt_available=True)
        monkeypatch.setattr(flow_module, "PROJECT_ROOT", tmp_path)
        flow._broker = SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=1, login_session_id="offline"))
        native_at = at.replace(second=59, microsecond=0)
        started = dict(ok=True, code="005930", rqname="native-m3-same-bar",
                       market_data_identity="005930_AL", market_source="INTEGRATED",
                       display_only=True, connection_epoch=1, login_session_id="offline",
                       request_as_of=native_at.isoformat(), started_at=native_at.isoformat())
        response = dict(started, interval=3, request_id="native-m3-same-bar",
                        rows=[dict(rows[-1])], received_at=native_at.isoformat())
        proof = dict(request_receipt=(started, response), market_source="INTEGRATED",
                     market_data_identity="005930_AL", timeframe_minutes=3,
                     session_identity=(1, "offline"), stock_code="005930",
                     request_id="native-m3-same-bar",
                     forming_candle=dict(rows[-1], is_complete=False,
                                         native_bar_end=at.replace(hour=20, minute=0,
                                                                   second=0).isoformat()),
                     completed_candles=())
        replay_at = native_at.replace(microsecond=100000)
        flow._now_factory = lambda: replay_at
        assert flow_module._v5_host_native_proof_is_current(
            session.request.stock, proof, flow._broker, replay_at)
        native_pool = dict(pool, native_live_snapshot=proof, native_live_completed=())
        native_pool.pop("_dispatch_snapshot", None)
        native_view = flow._validation_pool_for_session(
            session, native_pool, target_count=5900)
        native_view, _ = flow._normal_base_history_view(
            session, native_pool, 3, native_view)
        flow._historical_pools[key] = native_pool
        flow._pool_dispatch_snapshot(native_pool)
        assert flow._apply_pool_to_window(
            window, session, native_pool, evaluation_count=1, signal_entries=None,
            prepared_view_pool=native_view, normal_base_presentation=((), {}))
        assert window._v5_direct_current_replay_basis is None
        assert window._calculation_candles[-1]["time"] == rows[-1]["time"]
        pool = native_pool
    else:
        flow._now_factory = lambda: at.replace(hour=20, minute=3)
    current_frozen = flow._sor_m1_calculation_snapshot(session, pool)
    current_view = flow._sor_m1_calculation_view(
        session, pool, flow._validation_pool_for_session(
            session, pool, target_count=5900), current_frozen)
    if changed != "native_same_bar":
        assert current_frozen[1] == at.replace(hour=20, minute=3)
        assert current_view["candles"] == completed  # Replay must not revive live current.
    if changed == "display_corrupt":
        window._calculation_candles[-1]["close"] += 1
    elif changed == "forged_query":
        pool["direct_selected_query"]["request_id"] = "forged"
    elif changed == "source_flip":
        flow._validation_source_by_code["005930"] = False
    elif changed == "broken_basis":
        window._v5_direct_current_replay_basis = (
            *window._v5_direct_current_replay_basis[:4], object())
    observed = []
    replay_frozen = []
    original_calculation_view = flow._sor_m1_calculation_view

    def record_calculation_view(*args, **kwargs):
        replay_frozen.append(args[3])
        return original_calculation_view(*args, **kwargs)

    monkeypatch.setattr(flow, "_sor_m1_calculation_view", record_calculation_view)
    payloads = []

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            observed.append(snapshot.to_rows()[-1])
            raise RuntimeError("STOP_AFTER_VERIFIED_REPLAY_INPUT")

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    flow._replay_factory = lambda _session: StopReplay()
    monkeypatch.setattr(flow_module, "Thread", InlineThread)
    monkeypatch.setattr(flow, "_emit_range_replay_completed", payloads.append)
    request = {
        "window": window, "window_key": key, "pool": pool,
        "session": session, "source_session": session,
        "generation": 1, "request_generation": 1,
        "range": (0, 2), "range_times": window._validation_range_times,
        "range_uses_timestamps": True, "history_target": 3,
        "token": object(),
    }
    flow._start_range_replay(request)
    if changed in {"display_corrupt", "forged_query", "source_flip", "broken_basis"}:
        assert observed == []
        assert payloads[-1]["error"] == "SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED"
        return
    if changed != "native_same_bar":
        assert replay_frozen and replay_frozen[-1][1] == calculation_at
        assert forming["request_as_of"] == at.isoformat()
        assert observed == [flow._historical_rows_from_candles(rows)[-1]], payloads[-1]["error"]
    else:
        assert observed == [flow._historical_rows_from_candles(
            [window._calculation_candles[-1]])[0]], payloads[-1]["error"]
    assert payloads[-1]["error"] == "STOP_AFTER_VERIFIED_REPLAY_INPUT"
    failures = []
    monkeypatch.setattr(flow, "_window_accepts_results", lambda _window: True)
    monkeypatch.setattr(flow, "_fail_window", lambda _window, reason: failures.append(reason))
    if changed in {None, "native_same_bar"}:
        flow._on_range_replay_completed(payloads[-1])
        assert failures == ["REPLAY_ERROR: STOP_AFTER_VERIFIED_REPLAY_INPUT"]
        if changed == "native_same_bar":
            proof["request_id"] = "forged"
            flow._start_range_replay(dict(request, token=object()))
            assert payloads[-1]["error"] == "NATIVE_LIVE_RECEIPT_UNAVAILABLE"
            assert len(observed) == 1
        return
    if changed == "stale_generation":
        flow._request_generation[key] += 1
    elif changed == "stale_session":
        flow._validation_sessions[key] = object()
    elif changed == "stale_source":
        pool["candles"][-1]["close"] += 1
    elif changed == "stale_view":
        window._calculation_candles[-1]["close"] += 1
    elif changed == "stale_broken_basis":
        window._v5_direct_current_replay_basis = (
            *window._v5_direct_current_replay_basis[:4], object())
    flow._on_range_replay_completed(payloads[-1])
    assert failures == []
    assert key not in flow._range_replay_active


def test_direct_m3_period_replay_reconstructs_full_published_warmup():
    at = datetime(2026, 10, 26, 19, 59, 54, 424226,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    prior = []
    day = datetime(2026, 10, 1)
    while day.date() <= datetime(2026, 10, 23).date():
        if day.weekday() < 5:
            for minute in range(15 * 60 + 39, 20 * 60, 3):
                prior.append(dict(
                    ROWS[0], time=day.strftime("%Y%m%d")
                    + f"{minute // 60:02d}{minute % 60:02d}00"))
        day += timedelta(days=1)
    today = [dict(ROWS[0], time="20261026"
                  + f"{minute // 60:02d}{minute % 60:02d}00")
             for minute in range(15 * 60 + 39, 19 * 60 + 55, 3)]
    forming = {"time": "20261026195700", "open": 214000.0,
               "high": 214500.0, "low": 213500.0, "close": 214000.0,
               "volume": 59278.0}
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    completed, receipt, query = flow._direct_selected_history_parts(
        _result(session, at, [*prior[-920:], *today, forming]).snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=5900, request_id=query["request_id"],
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=receipt)
    flow._now_factory = lambda: at
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    published = flow._sor_m1_calculation_view(
        session, pool, flow._validation_pool_for_session(
            session, pool, target_count=5900), frozen)
    assert len(published["candles"]) == 1007
    assert len(flow._range_replay_history_view(session, pool, 100)["candles"]) == 1000
    observed = []

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            observed.append(snapshot.to_rows()[-1])
            raise RuntimeError("STOP_AFTER_FULL_PUBLISHED_INPUT")

    flow._replay_factory = lambda _session: StopReplay()
    with pytest.raises(RuntimeError, match="STOP_AFTER_FULL_PUBLISHED_INPUT"):
        flow._prepare_replay_result_from_pool(
            session, pool, history_target=100, chart_range=(98, 99),
            sor_m1_calculation_snapshot=frozen,
            expected_sor_m1_calculation=published["candles"])
    assert observed == [flow._historical_rows_from_candles([forming])[0]]
    corrupted = deepcopy(published["candles"])
    corrupted[-2]["close"] += 1
    with pytest.raises(ValueError, match="DISPLAYED_CALCULATION_CHANGED"):
        flow._prepare_replay_result_from_pool(
            session, pool, history_target=100, chart_range=(98, 99),
            sor_m1_calculation_snapshot=frozen,
            expected_sor_m1_calculation=corrupted)


def test_direct_current_normal_base_keeps_calculation_and_rejects_tampered_receipt():
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    completed, forming, query = flow._direct_selected_history_parts(_result(session, AS_OF).snapshot)
    pool = flow._pool_from_candles(session, completed, requested_count=5900,
        request_id="opt10080_005930_direct_m3", market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        selected_direct_forming_snapshot=forming)
    flow._now_factory = lambda: AS_OF.replace(minute=22)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5900)
    direct = flow._sor_m1_calculation_view(session, pool, view, frozen)
    normal, _ = flow._normal_base_history_view(session, pool, 5000, direct)
    assert normal["candles"][-1] == ROWS[-1]
    assert normal["display_candles"].count(ROWS[-1]) == 1
    for field, invalid in (("stock_code", "000660"),
                           ("timeframe_key", "M5"),
                           ("request_id", "other"),
                           ("request_as_of", AS_OF.replace(minute=24).isoformat())):
        bad = deepcopy(pool)
        bad["selected_direct_forming_snapshot"][field] = invalid
        bad_frozen = flow._sor_m1_calculation_snapshot(session, bad)
        bad_view = flow._validation_pool_for_session(session, bad, target_count=5900)
        with pytest.raises(ValueError, match="SOR_DIRECT_CURRENT_PROVENANCE_INVALID"):
            flow._sor_m1_calculation_view(session, bad, bad_view, bad_frozen)


@pytest.mark.parametrize("forming_time,minute", [
    ("20261026162000", 21), ("20261026162200", 22),
])
def test_direct_current_rejects_off_grid_selected_period_row(forming_time, minute):
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    at = AS_OF.replace(minute=minute)
    completed, forming, query = flow._direct_selected_history_parts(_result(session, at).snapshot)
    forming["candle"] = dict(forming["candle"], time=forming_time)
    pool = flow._pool_from_candles(session, completed, requested_count=5900,
        request_id="opt10080_005930_direct_m3", market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        selected_direct_forming_snapshot=forming)
    flow._now_factory = lambda: at
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5900)
    with pytest.raises(ValueError, match="SOR_DIRECT_CURRENT_PROVENANCE_INVALID"):
        flow._sor_m1_calculation_view(session, pool, view, frozen)


def test_normal_base_enrichment_preserves_direct_query_and_current_receipt():
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    completed, forming, query = flow._direct_selected_history_parts(_result(session, AS_OF).snapshot)
    pool = flow._pool_from_candles(session, completed, requested_count=5900,
        request_id="opt10080_005930_direct_m3", market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        selected_direct_forming_snapshot=forming)
    flow._now_factory = lambda: AS_OF.replace(minute=22)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._sor_m1_calculation_view(
        session, pool, flow._validation_pool_for_session(session, pool, target_count=5900), frozen)
    window = SimpleNamespace()
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    flow._historical_provider_factory = lambda *_args, **_kwargs: None
    ready = []
    assert flow._ensure_normal_base_history(
        window, session, pool, 3, display_view=view,
        on_ready=lambda enriched, enriched_view, deficit:
            ready.append((enriched, enriched_view, deficit)))
    enriched, enriched_view, _ = ready[0]
    assert enriched["direct_selected_query"] == query
    assert enriched["selected_direct_forming_snapshot"] == forming
    assert enriched["candles"] == ROWS[:-1]
    assert enriched_view["candles"] == ROWS
    assert enriched_view["snapshot"].to_rows() == flow._historical_rows_from_candles(ROWS)


@pytest.mark.parametrize("complete_response", [True, False])
def test_normal_base_enrichment_uses_only_fresh_complete_native_m3_source(
        tmp_path, monkeypatch, complete_response):
    import gui_indicator_follow_signal_validation_v5_flow as flow_module
    from indicator_follow_signal_validation_historical_cache import (
        IndicatorFollowSignalValidationHistoricalCache)

    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    source_rows = [dict(ROWS[0], time=(datetime(2026, 10, 26, 15, 0)
                   + timedelta(minutes=3 * index)).strftime("%Y%m%d%H%M%S"),
                   close=float(200000 + index)) for index in range(11)]
    saved = source_rows[-8:]
    _, _, saved_query = flow._direct_selected_history_parts(
        _result(session, AS_OF, saved, request_id="saved-m3").snapshot)
    assert flow._persist_history(session.request.stock, 3, "M3", 8, saved,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=saved_query)
    marker_path = flow._validation_cache_source_marker_path(
        session.request.stock, 3, "M3", 8)
    marker_before = marker_path.read_bytes()
    cache_before = flow._historical_cache.path_for(
        "005930", 3, 8, "M3", market_data_identity="005930_AL").read_bytes()
    pool = flow._pool_from_candles(session, saved, requested_count=8,
        request_id="VALIDATION_CACHE_RESTORE", market_data_identity="005930_AL",
        market_source="INTEGRATED")
    window = SimpleNamespace()
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    response_rows = source_rows if complete_response else source_rows[:3]
    response = _result(session, AS_OF, response_rows, request_id="fresh-m3")
    _, _, fresh_query = flow._direct_selected_history_parts(response.snapshot)
    calls = []
    class Provider:
        def request_latest(self, count, callback):
            calls.append(count)
            callback(response)
    monkeypatch.setattr(flow_module, "_V5SelectedSourceRequester",
                        lambda *_a, **_k: SimpleNamespace())
    flow._historical_provider_factory = lambda *_a, **_k: Provider()
    monkeypatch.setattr(flow_module, "required_validation_history_context_bars",
                        lambda *_a: 3)
    monkeypatch.setattr(flow, "_normal_base_history_view", lambda *_a, **_k:
                        (dict(pool, candles=saved, display_candles=saved), 3))
    used = []
    monkeypatch.setattr(flow, "_use_pool_for_window", lambda *_a, **kwargs:
                        used.append((_a[2], kwargs["_normal_view"])))
    assert flow._ensure_normal_base_history(window, session, pool, 8)
    assert calls == [11]
    assert len(used) == 1
    used_pool, used_view = used[0]
    if complete_response:
        assert used_pool["candles"] == source_rows
        assert used_pool["request_id"] == "fresh-m3"
        assert used_pool["direct_selected_query"] == fresh_query
        assert used_view["candles"] == source_rows
        assert used_view["snapshot"].request_id == "fresh-m3"
    else:
        assert used_pool is pool
        assert used_view["candles"] == saved
    assert marker_path.read_bytes() == marker_before
    assert flow._historical_cache.path_for(
        "005930", 3, 8, "M3", market_data_identity="005930_AL").read_bytes() == cache_before
    assert not flow._validation_cache_source_marker_path(
        session.request.stock, 3, "M3", 11).exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_direct_cache_marker_raises_controlled_error(tmp_path, corrupt):
    from indicator_follow_signal_validation_historical_cache import (
        IndicatorFollowSignalValidationHistoricalCache)
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    if corrupt:
        flow._validation_cache_source_marker_path(
            session.request.stock, 3, "M3", 11).write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="SOR_DIRECT_CACHE_COMPLETION_UNVERIFIED"):
        flow._pool_from_candles(session, ROWS[:-1], requested_count=11,
            request_id="VALIDATION_CACHE_RESTORE", market_data_identity="005930_AL",
            market_source="INTEGRATED")


def test_cached_same_completed_refresh_rebinds_or_clears_direct_current(tmp_path, monkeypatch):
    from indicator_follow_signal_validation_historical_cache import (
        IndicatorFollowSignalValidationHistoricalCache)
    from indicator_follow_signal_validation_projection import (
        IndicatorFollowSignalValidationRunRequest)
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Window
    import gui_indicator_follow_signal_validation_v5_flow as flow_module

    flow = _flow()
    session = _session()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._validation_source_by_code["005930"] = True
    old_completed, old_forming, old_query = flow._direct_selected_history_parts(
        _result(session, AS_OF, request_id="direct-old").snapshot)
    assert flow._persist_history(session.request.stock, 3, "M3", 5900, old_completed,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=old_query)
    responses = [
        _result(session, AS_OF.replace(minute=22, second=30),
                request_id="direct-new-current"),
        _result(session, AS_OF.replace(minute=22, second=50), ROWS[:-1],
                request_id="direct-new-completed-only"),
    ]
    calls = []

    class Provider:
        def __init__(self, fetch_session, requester):
            assert fetch_session.request.timeframe_key == "M3"

        def request_latest(self, count, callback):
            calls.append(count)
            callback(responses.pop(0))

    steps = []
    def make_provider(fetch_session, requester, **kwargs):
        steps.append("provider")
        return Provider(fetch_session, requester)
    flow._historical_provider_factory = make_provider
    original_pool_from_candles = flow._pool_from_candles

    def restored_with_old_current(*args, **kwargs):
        pool = original_pool_from_candles(*args, **kwargs)
        if kwargs.get("request_id") == "VALIDATION_CACHE_RESTORE":
            pool["selected_direct_forming_snapshot"] = deepcopy(old_forming)
        return pool

    monkeypatch.setattr(flow, "_pool_from_candles", restored_with_old_current)
    installed = []
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda _w, _s, pool, **_k:
                        installed.append(pool) or True)
    errors = []
    monkeypatch.setattr(flow, "_fail_window", lambda _w, reason: errors.append(reason))
    monkeypatch.setattr(flow_module, "load_stock_library_snapshot", lambda *_a, **_k:
        SimpleNamespace(state=flow_module.STOCK_LIBRARY_READY,
                        records=[{"code": "005930", "nxt_available": True}]))
    monkeypatch.setattr(flow_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _stock: True)
    monkeypatch.setattr(flow, "_sync_v5_observation_lease", lambda _window: None)
    monkeypatch.setattr(flow, "_broker_al_m1_provider_options", lambda _session:
                        steps.append("options") or {})
    monkeypatch.setattr(flow_module, "_V5SelectedSourceRequester", lambda *_a, **_k:
                        steps.append("requester") or SimpleNamespace())
    window = _Window(session.request.stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    request = IndicatorFollowSignalValidationRunRequest(session.request.settings_snapshot, 3)
    flow._now_factory = lambda: AS_OF.replace(minute=22, second=40)
    flow._run_validation(window, request)
    assert installed and not errors, (errors, steps)
    assert installed[-1]["candles"] == old_completed
    assert installed[-1]["direct_selected_query"]["request_id"] == "direct-new-current"
    assert installed[-1]["selected_direct_forming_snapshot"]["request_id"] == "direct-new-current"
    flow._now_factory = lambda: AS_OF.replace(minute=22, second=55)
    flow._run_validation(window, request)
    assert len(installed) == 2 and not errors, errors
    assert installed[-1]["direct_selected_query"]["request_id"] == "direct-new-completed-only"
    assert "selected_direct_forming_snapshot" not in installed[-1]
    assert len(calls) == 2 and all(0 < count < 5900 for count in calls)


def test_direct_m3_later_response_admits_completed_tail_without_changing_identity(monkeypatch):
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    flow._now_factory = lambda: AS_OF.replace(minute=24)
    installed = []
    errors = []
    monkeypatch.setattr(flow, "_persist_history", lambda *args, **kwargs: True)
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda *args, **kwargs:
                        installed.append(args[2]) or True)
    monkeypatch.setattr(flow, "_fail_window", lambda _window, reason: errors.append(reason))
    assert flow._handle_historical_result(
        SimpleNamespace(), session, _result(session, AS_OF.replace(minute=24)),
        evaluation_count=3, cache_requested_count=5900,
    )
    assert installed[0]["candles"] == ROWS
    assert "selected_direct_forming_snapshot" not in installed[0]
    assert not flow._handle_historical_result(
        SimpleNamespace(), session, _result(session, AS_OF, identity="005930"),
        evaluation_count=3, cache_requested_count=5900,
    )
    assert errors == ["HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID"]
    assert len(installed) == 1


def test_partial_cache_cannot_age_into_completed_history_without_direct_query(tmp_path, monkeypatch):
    from indicator_follow_signal_validation_historical_cache import (
        IndicatorFollowSignalValidationHistoricalCache)
    flow = _flow()
    session = _session()
    flow._validation_source_by_code["005930"] = True
    flow._now_factory = lambda: AS_OF.replace(minute=30)
    real_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path / "isolated_cache")
    flow._historical_cache = real_cache
    writes = []
    original_store = real_cache.store
    def recording_store(*args, **kwargs):
        writes.append((args, kwargs))
        return original_store(*args, **kwargs)
    monkeypatch.setattr(real_cache, "store", recording_store)
    assert not flow._persist_history(
        session.request.stock, 3, "M3", 5900, ROWS,
        market_data_identity="005930_AL", market_source="INTEGRATED")
    _, _, partial_query = flow._direct_selected_history_parts(_result(session, AS_OF).snapshot)
    assert not flow._persist_history(
        session.request.stock, 3, "M3", 5900, ROWS,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=partial_query)
    assert writes == []
    _, _, fresh_query = flow._direct_selected_history_parts(
        _result(session, AS_OF.replace(minute=24)).snapshot)
    assert flow._persist_history(
        session.request.stock, 3, "M3", 5900, ROWS,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=fresh_query)
    assert len(writes) == 1
    assert flow._cached_history(session.request.stock, 3, "M3", 5900) is not None

    entry = ValidationHistoricalCacheEntry(
        stock_code="005930", stock_name="Samsung", timeframe_minutes=3,
        timeframe_key="M3", requested_count=5900, candles=tuple(ROWS),
        updated_at=AS_OF.replace(minute=30).isoformat(), market_data_identity="005930_AL",
        market_source="INTEGRATED",
    )
    marker_path = tmp_path / "source.json"
    flow._historical_cache = SimpleNamespace(load_covering=lambda *_args, **_kwargs: entry)
    monkeypatch.setattr(flow, "_validation_cache_source_marker_path",
                        lambda *_args, **_kwargs: marker_path)
    marker = {
        "canonical_stock_code": "005930", "market_data_identity": "005930_AL",
        "market_source": "INTEGRATED",
        "candle_content_sha256": hashlib.sha256(json.dumps(
            ROWS, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest(),
    }
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert flow._cached_history(session.request.stock, 3, "M3", 5900) is None
    with pytest.raises(ValueError, match="SOR_DIRECT_CACHE_COMPLETION_UNVERIFIED"):
        flow._pool_from_candles(session, ROWS, requested_count=5900,
            request_id="VALIDATION_CACHE_RESTORE", market_data_identity="005930_AL",
            market_source="INTEGRATED")
    display_only = flow._cached_history_for_source(
        session.request.stock, 3, "M3", 5900, "005930_AL", "INTEGRATED",
        legacy_display_only=True)
    assert list(display_only.candles) == ROWS[:-1]

    completed_at = AS_OF.replace(minute=24)
    entry = replace(entry, updated_at=completed_at.isoformat())
    marker["direct_selected_query"] = {
        "stock_code": "005930", "timeframe_key": "M3", "timeframe_minutes": 3,
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "request_id": "opt10080_005930_direct_m3",
        "request_as_of": completed_at.isoformat(),
        "last_completed_time": ROWS[-1]["time"],
    }
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert flow._cached_history(session.request.stock, 3, "M3", 5900) == entry
    marker["direct_selected_query"]["market_data_identity"] = "005930"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert flow._cached_history(session.request.stock, 3, "M3", 5900) is None
