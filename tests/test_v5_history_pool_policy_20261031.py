"""Golden history-pool decisions, also run against the pre-extraction flow."""

from copy import deepcopy
from datetime import datetime
import importlib
import importlib.util

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow


POLICY = "indicator_follow_validation_v5_history_pool_policy"
NOW = datetime.fromisoformat("2026-10-30T10:20:45+09:00")
STOCK = v5.ValidationStockRef("005930", "Samsung")


def _policy():
    assert importlib.util.find_spec(POLICY) is not None, "pure history pool policy not extracted"
    return importlib.import_module(POLICY)


@pytest.fixture
def flow():
    result = _flow()
    result._now_factory = lambda: NOW
    result._validation_source_by_code[STOCK.code] = True
    return result


def _pool(**changes):
    return {
        "stock": STOCK, "timeframe_key": "M3",
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "requested_count": 5900,
        "candles": [{"time": "20261030101800", "open": 100, "high": 102,
                     "low": 99, "close": 101, "volume": 10}],
        "source_verified_at": "2026-10-30T10:20:00+09:00", **changes,
    }


@pytest.mark.parametrize("backend", ["flow", "policy"])
@pytest.mark.parametrize("verified,current,same_date,expected", [
    ("2026-10-30T10:20:00+09:00", "2026-10-30T10:20:59+09:00", False, True),
    ("2026-10-30T10:20:59+09:00", "2026-10-30T10:21:00+09:00", False, False),
    ("2026-10-30T10:20:46+09:00", "2026-10-30T10:20:45+09:00", False, False),
    ("2026-10-31T00:00:00+09:00", "2026-10-31T23:59:59+09:00", True, True),
    ("2026-10-31T23:59:59+09:00", "2026-11-01T23:59:59+09:00", False, True),
    ("2026-10-31T23:59:59+09:00", "2026-11-01T00:00:00+09:00", True, False),
    ("2026-10-30T23:59:59+09:00", "2026-10-31T00:00:00+09:00", False, False),
    ("2026-11-01T23:59:59+09:00", "2026-11-02T00:00:00+09:00", False, False),
    ("2026-10-24T00:00:00+09:00", "2026-10-31T00:00:00+09:00", False, False),
    (None, "2026-10-30T10:20:45+09:00", False, False),
    ("2026-10-30T10:20:00+09:00", None, False, False),
])
def test_freshness_golden(flow, backend, verified, current, same_date, expected):
    if backend == "flow":
        flow._now_factory = lambda: current
        actual = flow._source_snapshot_reusable(verified, require_same_date=same_date)
    else:
        actual = _policy().source_snapshot_reusable(
            datetime.fromisoformat(verified) if verified else None,
            datetime.fromisoformat(current) if current else None,
            require_same_date=same_date,
        )
    assert actual is expected


@pytest.mark.parametrize("backend", ["flow", "policy"])
@pytest.mark.parametrize("changes,reason", [
    ({}, None),
    ({"stock": None}, "stock"),
    ({"timeframe_key": "M5"}, "timeframe"),
    ({"market_session_scope": "REGULAR"}, "market_session_scope"),
    ({"market_data_identity": "005930_NX"}, "market_identity"),
    ({"market_source": "NXT"}, "market_source"),
    ({"requested_count": 5899}, "requested_count"),
    ({"requested_count": "5900"}, "requested_count"),
    ({"requested_count": 5900.0}, "requested_count"),
    ({"candles": []}, "candles"),
    ({"candles": ({"time": "20261030101800"},)}, "candles"),
    ({"source_verified_at": "2026-10-30T10:19:59+09:00"}, "freshness"),
    ({"source_verified_at": "bad"}, "freshness"),
])
def test_pool_decisions_and_exact_reasons(flow, backend, changes, reason):
    pool = _pool(**changes)
    before = deepcopy(pool)
    if backend == "flow":
        matched = flow._pool_matches(pool, STOCK, "M3", 5900)
    else:
        matched = _policy().pool_matches(
            pool, STOCK, "M3", 5900, expected_identity="005930_AL",
            expected_source="INTEGRATED", source_snapshot_reusable=flow._source_snapshot_reusable,
        )
        # The diagnostic prefix stays in flow to preserve lazy source selection.
        if reason not in {"stock", "timeframe", "market_session_scope"}:
            assert _policy().pool_source_match_failure_reason(
                pool, 5900, expected_identity="005930_AL", expected_source="INTEGRATED",
                source_snapshot_reusable=flow._source_snapshot_reusable,
            ) == reason
    assert matched is (reason is None)
    assert flow._pool_match_failure_reason(pool, STOCK, "M3", 5900) == reason
    assert pool == before


@pytest.mark.parametrize("pool,reason", [(None, "missing"), ([], "not_dict")])
def test_invalid_pool_keeps_diagnostic_source_selection_lazy(flow, monkeypatch, pool, reason):
    def unexpected(*args):
        pytest.fail("diagnostic must return before selecting a source")
    monkeypatch.setattr(flow, "_validation_market_data_identity", unexpected)
    assert flow._pool_match_failure_reason(pool, STOCK, "M3", 1) == reason
    assert _policy().pool_matches(
        pool, STOCK, "M3", 1, expected_identity="005930_AL", expected_source="INTEGRATED",
        source_snapshot_reusable=unexpected,
    ) is False


@pytest.mark.parametrize("key", ["M3", "D1", "W1", "MO1", "Y1"])
@pytest.mark.parametrize("scope", ["FULL", "REGULAR"])
@pytest.mark.parametrize("integrated", [False, True])
def test_flow_preserves_period_source_selection(flow, monkeypatch, key, scope, integrated):
    flow._validation_source_by_code[STOCK.code] = integrated
    monkeypatch.setattr(flow, "_validation_market_data_identity", lambda stock: "005930_NX")
    monkeypatch.setattr(flow, "_validation_market_source", lambda stock: "NXT")
    if scope == "REGULAR" and key != "M3":
        identity, source = ("005930_AL", "INTEGRATED") if integrated else ("005930", "KRX")
    else:
        identity, source = "005930_NX", "NXT"
    pool = _pool(timeframe_key=key, market_session_scope=scope,
                 market_data_identity=identity, market_source=source)
    assert flow._pool_matches(pool, STOCK, key, 5900, market_session_scope=scope) is True
    assert flow._pool_match_failure_reason(pool, STOCK, key, 5900,
                                          market_session_scope=scope) is None


def test_routing_keeps_validator_override_independent_of_diagnostics(flow, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(flow, "_source_snapshot_reusable", lambda value: sentinel)
    monkeypatch.setattr(flow, "_pool_match_failure_reason", lambda *a, **k: "freshness")
    assert flow._pool_matches(_pool(), STOCK, "M3", 5900) is sentinel
    assert _policy().pool_matches(
        _pool(), STOCK, "M3", 5900, expected_identity="005930_AL",
        expected_source="INTEGRATED", source_snapshot_reusable=lambda value: sentinel,
    ) is sentinel


def test_routing_int_conversion_is_independent_of_diagnostic_comparison(flow):
    class Count(int):
        def __int__(self):
            return 5900
    pool = _pool(requested_count=Count(1))
    assert flow._pool_matches(pool, STOCK, "M3", 5900) is True
    assert flow._pool_match_failure_reason(pool, STOCK, "M3", 5900) == "requested_count"
    assert _policy().pool_matches(
        pool, STOCK, "M3", 5900, expected_identity="005930_AL", expected_source="INTEGRATED",
        source_snapshot_reusable=flow._source_snapshot_reusable,
    ) is True


def test_flow_keeps_parser_override_and_clock_exception_boundary(flow, monkeypatch):
    seen = []
    def parse(value):
        seen.append(value)
        return NOW
    monkeypatch.setattr(flow, "_source_snapshot_datetime", parse)
    assert flow._source_snapshot_reusable("override") is True
    assert seen == ["override", NOW]
    def broken_clock():
        raise RuntimeError("clock unavailable")
    monkeypatch.setattr(flow, "_now_factory", broken_clock)
    assert flow._source_snapshot_reusable("override") is False


def test_existing_pool_builder_remains_usable_through_flow(flow):
    settings = v5.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
    })
    session = v5.ValidationSession(v5.ValidationRequest(STOCK, settings, 3),
                                   operation_active_reader=lambda: False)
    pool = flow._pool_from_candles(session, _pool()["candles"], requested_count=5900,
                                   request_id="POLICY-OFFLINE",
                                   market_data_identity="005930_AL", market_source="INTEGRATED")
    assert flow._pool_matches(pool, STOCK, "M3", 5900) is True
    assert flow._pool_match_failure_reason(pool, STOCK, "M3", 5900) is None

@pytest.mark.parametrize(
    "timeframe,receipt_changes,integrated,identity,source,expected,uses_fallback",
    [
        ("D1", {}, True, "005930_AL", "integrated", True, False),
        ("W1", {}, False, "005930", "KRX", True, False),
        ("MO1", {}, None, "005930", "KRX", True, False),
        ("Y1", {}, True, "005930_AL", "KRX", False, False),
        ("M5", {}, True, "005930_NX", "NXT", True, True),
        ("D1", {"timeframe_key": "W1"}, True, "005930_NX", "NXT", True, True),
        ("D1", {"stock_code": "000660"}, True, "005930_AL", "INTEGRATED", False, True),
        ("D1", {"market_session_scope": "FULL"}, True, "005930_NX", "NXT", True, True),
        ("D1", None, True, "005930_NX", "NXT", True, True),
    ],
)
def test_historical_result_source_policy_preserves_receipt_and_lazy_fallback(
    timeframe, receipt_changes, integrated, identity, source, expected, uses_fallback,
):
    receipt = None if receipt_changes is None else {
        "market_session_scope": "REGULAR", "stock_code": STOCK.code,
        "timeframe_key": timeframe, **receipt_changes,
    }
    calls = []

    def fallback():
        calls.append(True)
        return "005930_NX", "NXT"

    actual = _policy().historical_result_source_matches(
        stock_code=STOCK.code, timeframe_key=timeframe,
        response_identity=identity, response_source=source,
        integrated_source_available=integrated, regular_period_receipt=receipt,
        fallback_expected_source=fallback,
    )
    assert actual is expected
    assert bool(calls) is uses_fallback


def test_historical_result_flow_keeps_frozen_krx_gate_and_period_evidence():
    from types import SimpleNamespace

    def forbidden_default(_stock):
        raise AssertionError("verified regular-period source must stay independent")

    receipt_id = "POLICY-FROZEN-KRX"
    snapshot = v5.ValidationHistoricalSnapshot(
        stock=STOCK, timeframe_minutes=1, timeframe_key="D1",
        requested_count=3, request_id=receipt_id, rows=[],
        market_data_identity=STOCK.code, market_source="KRX",
    )
    result = v5.ValidationHistoricalResult(ok=True, snapshot=snapshot)
    flow = SimpleNamespace(
        _validation_source_by_code={STOCK.code: False},
        _validation_frozen_krx_codes={STOCK.code},
        _verified_al_period_receipts={
            receipt_id: {
                "market_session_scope": "REGULAR", "stock_code": STOCK.code,
                "timeframe_key": "D1",
            },
        },
        _validation_market_data_identity=forbidden_default,
        _validation_market_source=forbidden_default,
    )
    matcher = v5.IndicatorFollowSignalValidationV5Flow._historical_result_matches_validation_source
    assert matcher(flow, result, STOCK) is True
    flow._validation_frozen_krx_codes.clear()
    assert matcher(flow, result, STOCK) is False


def test_historical_source_mismatch_still_normalizes_response_source():
    normalized = []

    class ResponseSource:
        def __str__(self):
            normalized.append("source")
            return "KRX"

    actual = _policy().historical_result_source_matches(
        stock_code=STOCK.code, timeframe_key="M5",
        response_identity="WRONG", response_source=ResponseSource(),
        integrated_source_available=True, regular_period_receipt=None,
        fallback_expected_source=lambda: (STOCK.code, "KRX"),
    )
    assert actual is False
    assert normalized == ["source"]


@pytest.mark.parametrize("key,minutes", [("M1", 1), ("M3", 3), ("M5", 5),
                                        ("M10", 10), ("M15", 15), ("M30", 30),
                                        ("M60", 60), ("M120", 120), ("M240", 240)])
@pytest.mark.parametrize("regular_only", [False, True])
def test_display_toggle_reuses_only_unchanged_published_native_pool(
        flow, monkeypatch, key, minutes, regular_only):
    from types import SimpleNamespace

    rules = {"bar": {"bar_minutes": minutes}, "validation_timeframe": {"key": key},
             "validation_market_scope": {"regular_market_only": not regular_only}}
    previous = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), minutes),
        operation_active_reader=lambda: False)
    rules["validation_market_scope"]["regular_market_only"] = regular_only
    current = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), minutes),
        operation_active_reader=lambda: False)
    window = SimpleNamespace(stock=STOCK,
        _session_display_republish_pending=current.request.settings_snapshot)
    broker_state = SimpleNamespace(connected=True, connection_epoch=7, login_session_id="offline")
    flow._broker = SimpleNamespace(broker_session_snapshot=lambda: broker_state)
    for session, generation in ((previous, 4), (current, 5)):
        session.source_selection = v5._select_v5_source(
            STOCK, key, True, False, NOW, window_key=id(window), generation=generation)
    rows = [{"time": f"202610{day:02d}{hour:02d}0000", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 10}
            for day in range(20, 30) for hour in (8, 9)]
    query = dict(stock_code=STOCK.code, timeframe_key=key, timeframe_minutes=minutes,
                 market_data_identity="005930_AL", market_source="INTEGRATED",
                 request_id="PUBLISHED-NATIVE", request_as_of="2026-10-29T20:01:00+09:00",
                 last_completed_time=rows[-1]["time"])
    pool = flow._pool_from_candles(previous, rows, requested_count=len(rows),
        request_id=query["request_id"], market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=query["request_as_of"])
    flow._open_windows[id(window)] = window
    flow._historical_pools[id(window)] = pool
    flow._validation_sessions[id(window)] = previous
    flow._request_generation[id(window)] = 4
    flow._pool_dispatch_snapshot(pool)
    binding = (pool, previous, 4, pool["_dispatch_snapshot"])
    window._v5_marking_display_publication = binding
    window._v5_session_display_basis = (binding, (7, "offline"))
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    reuse = flow._session_display_pool_reusable
    assert not flow._pool_matches(pool, STOCK, key, 5), "ordinary freshness stays strict"
    assert reuse(window, current, pool, 3)
    projected, deficit = flow._normal_base_history_view(current, pool, 3, sidecar_override=None)
    assert not deficit
    assert any(row["time"].endswith("080000") for row in projected["candles"]) is not regular_only
    assert not reuse(window, current, pool, len(rows)), "missing warmup must request history"
    window._session_display_republish_pending = None
    assert not reuse(window, current, pool, 3), "ordinary validation cannot use this route"
    window._session_display_republish_pending = current.request.settings_snapshot
    broker_state.connection_epoch = 8
    assert not reuse(window, current, pool, 3), "reconnected session cannot reuse"
    broker_state.connection_epoch = 7
    flow._request_generation[id(window)] = 5
    assert not reuse(window, current, pool, 3), "stale generation cannot reuse"
    flow._request_generation[id(window)] = 4
    for rebound_stock, rebound_key, rebound_minutes in (
            (v5.ValidationStockRef("000660", "Other"), key, minutes),
            (STOCK, "M5" if key != "M5" else "M3", 5 if key != "M5" else 3)):
        flow._validation_sessions[id(window)] = v5.ValidationSession(
            v5.ValidationRequest(rebound_stock, v5.ValidationSettingsSnapshot({
                "bar": {"bar_minutes": rebound_minutes},
                "validation_timeframe": {"key": rebound_key}}), rebound_minutes),
            operation_active_reader=lambda: False)
        assert not reuse(window, current, pool, 3), "rebinding must invalidate the toggle basis"
    flow._validation_sessions[id(window)] = previous
    pool["candles"][0]["close"] = 100
    assert not reuse(window, current, pool, 3), "changed original cannot reuse"
    pool["candles"][0]["close"] = 101
    pool["direct_selected_query"]["last_completed_time"] = "invalid"
    assert not reuse(window, current, pool, 3), "changed receipt cannot reuse"
    pool["direct_selected_query"]["last_completed_time"] = rows[-1]["time"]

    # Exercise the real router, stopping at its existing complete-calculation entry.
    from pathlib import Path
    monkeypatch.setattr(v5, "StockRepository", lambda _: SimpleNamespace(
        resolve_stock_dir=lambda *_: Path(".")))
    monkeypatch.setattr(v5, "read_json_dict", lambda _: {"calculation_market_source": "SOR"})
    monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _: True)
    monkeypatch.setattr(flow, "_sync_v5_observation_lease", lambda _: None)
    monkeypatch.setattr(flow, "_cached_history", lambda *_a, **_kw: pytest.fail("unexpected cache lookup"))
    flow._historical_provider_factory = lambda *_a, **_kw: pytest.fail("unexpected server request")
    flow._operation_active_reader = lambda: False
    flow._history_targets[id(window)] = 3
    flow._last_run_requests[id(window)] = v5.IndicatorFollowSignalValidationRunRequest(
        previous.request.settings_snapshot, 3)
    installed = []
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda *a, **kw: installed.append((a, kw)))
    flow._run_validation(window, v5.IndicatorFollowSignalValidationRunRequest(
        current.request.settings_snapshot, 3))
    assert len(installed) == 1
    args, kwargs = installed[0]
    assert args[0] is window and args[2] is pool
    assert args[1].request.settings_snapshot is current.request.settings_snapshot
    assert kwargs["history_browse"] is False
    republished_session = args[1]
    frozen = flow._sor_m1_calculation_snapshot(republished_session, pool)
    if minutes > 1:
        assert frozen[1].isoformat() == query["request_as_of"]
    monkeypatch.setattr(flow, "_verified_nxt_display_stock", lambda _: True)
    flow._broker.request_minute_candles_display_only_integrated = (
        lambda *_a, **_kw: pytest.fail("display toggle must not fetch a sidecar"))
    flow._ensure_integrated_display_sidecar(window, republished_session, 3)
    flow._validation_sessions[id(window)] = republished_session
    assert flow._source_selection_is_current(republished_session.source_selection)
    broker_state.connection_epoch = 8
    assert not flow._source_selection_is_current(republished_session.source_selection)


@pytest.mark.parametrize("history_target", [10, 5000])
@pytest.mark.parametrize("invalidate_before_run", [False, True])
def test_verified_native_warmup_retoggle_needs_no_second_handoff(
        flow, monkeypatch, history_target, invalidate_before_run):
    from pathlib import Path
    from types import SimpleNamespace
    from PyQt5.QtCore import QObject, pyqtSignal

    class InputSignals(QObject):
        chart_input_invalidated = pyqtSignal()

    class Window(SimpleNamespace):
        pass

    rules = {"bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
             "validation_market_scope": {"regular_market_only": True}}
    previous = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    window = Window(stock=STOCK, _normal_base_requested=True,
                    install_normal_base=lambda *_a, **_kw: None)
    signals = InputSignals()
    window.chart_input_invalidated = signals.chart_input_invalidated
    window._candle_preview_token = None
    window.clear_candle_preview = lambda *_: None
    window._cancel_visualization_projection_work = lambda: None
    signals.chart_input_invalidated.connect(lambda: flow._invalidate_window_input(window))
    flow._broker = SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
        connected=True, connection_epoch=7, login_session_id="offline"))
    previous.source_selection = v5._select_v5_source(
        STOCK, "M3", True, True, NOW, window_key=id(window), generation=3)
    base = [{"time": f"202610{day:02d}{hour:02d}0000", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 10}
            for day in range(24, 29) for hour in (8, 9)]
    if history_target == 5000:
        base = [dict(base[0], time=f"202610{day:02d}{minute // 60:02d}{minute % 60:02d}00")
                for day in range(4, 29) for minute in range(8 * 60, 20 * 60, 3)
                if not (8 * 60 + 50 <= minute < 9 * 60
                        or 15 * 60 + 30 <= minute < 15 * 60 + 40)
                and not (invalidate_before_run and day == 4 and 9 * 60 <= minute < 13 * 60)]
    expanded = [dict(base[0], time=stamp) for stamp in
                (("20261003150000", "20261003150300") if history_target == 5000
                 else ("20261023150000", "20261023150300"))] + base
    query = dict(stock_code=STOCK.code, timeframe_key="M3", timeframe_minutes=3,
                 market_data_identity="005930_AL", market_source="INTEGRATED",
                 request_id="BEFORE-WARMUP", request_as_of="2026-10-29T20:01:00+09:00",
                 last_completed_time=base[-1]["time"])
    pool = flow._pool_from_candles(previous, base, requested_count=len(base),
        request_id=query["request_id"], market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=query["request_as_of"])
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 3
    flow._history_targets[id(window)] = history_target
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    monkeypatch.setattr(v5, "_V5SelectedSourceRequester", lambda *_a, **_kw: object())
    monkeypatch.setattr(flow, "_cached_history", lambda *_a, **_kw: None)
    calls = []

    def request_latest(count, callback):
        calls.append(count)
        assert len(calls) == 1, "verified warmup must not trigger a second native TR"
        snapshot = v5.ValidationHistoricalSnapshot(
            stock=STOCK, timeframe_minutes=3, timeframe_key="M3",
            requested_count=count, request_id="ACCEPTED-WARMUP",
            rows=flow._historical_rows_from_candles(expanded),
            market_data_identity="005930_AL", market_source="INTEGRATED",
            request_as_of=query["request_as_of"])
        callback(v5.ValidationHistoricalResult(True, snapshot))

    flow._historical_provider_factory = lambda *_a, **_kw: SimpleNamespace(
        request_latest=request_latest)
    ready = []
    assert flow._ensure_normal_base_history(
        window, previous, pool, history_target, on_ready=lambda *args: ready.append(args))
    assert calls == [len(expanded)] and len(ready) == 1
    verified_pool, verified_view, deficit = ready[0]
    assert deficit == 0
    assert len(verified_view["display_candles"]) == (
        (3170 if invalidate_before_run else 3250) if history_target == 5000 else 5)
    if history_target == 5000:
        assert len(expanded) == (5747 if invalidate_before_run else 5827)
        assert verified_view["display_candles"][0]["time"] == (
            "20261004130000" if invalidate_before_run else "20261004090000")
        assert base[-5000]["time"] > verified_view["display_candles"][0]["time"]

    monkeypatch.setattr(flow, "_ensure_integrated_display_sidecar", lambda *_a: None)

    def publish(session, view):
        flow._historical_pools[id(window)] = verified_pool
        flow._validation_sessions[id(window)] = session
        flow._pool_dispatch_snapshot(verified_pool)
        assert flow._apply_pool_to_window(
            window, session, verified_pool, evaluation_count=history_target, signal_entries=None,
            prepared_view_pool=view, normal_base_presentation=((), {}))

    publish(previous, verified_view)
    monkeypatch.setattr(v5, "StockRepository", lambda _: SimpleNamespace(
        resolve_stock_dir=lambda *_: Path(".")))
    monkeypatch.setattr(v5, "read_json_dict", lambda _: {"calculation_market_source": "SOR"})
    monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _: True)
    monkeypatch.setattr(flow, "_sync_v5_observation_lease", lambda _: None)
    flow._operation_active_reader = lambda: False
    flow._last_run_requests[id(window)] = v5.IndicatorFollowSignalValidationRunRequest(
        previous.request.settings_snapshot, history_target)
    prepared = []
    monkeypatch.setattr(flow, "_start_pool_prepare", prepared.append)

    for turn, regular in enumerate((False, True, False, True), 1):
        rules["validation_market_scope"]["regular_market_only"] = regular
        settings = v5.ValidationSettingsSnapshot(rules)
        old_binding = window._v5_marking_display_publication
        old_generation = flow._request_generation[id(window)]
        if invalidate_before_run:
            assert flow._marking_publication_is_current(window, old_binding)
            if prepared:
                assert flow._prepare_request_is_current(prepared[-1])
            # Run the real window invalidator and its connected production receiver.
            v5.IndicatorFollowSignalValidationV5Window._invalidate_user_background_results(window)
            assert flow._request_generation[id(window)] == old_generation + 1
            assert not flow._marking_publication_is_current(window, old_binding)
            assert not flow._source_selection_is_current(old_binding[1].source_selection)
            if prepared:
                assert not flow._prepare_request_is_current(prepared[-1])
        window._session_display_republish_pending = settings
        flow._run_validation(window, v5.IndicatorFollowSignalValidationRunRequest(settings, history_target))
        assert calls == [len(expanded)], "retoggle must retain the accepted native receipt"
        assert len(prepared) == turn
        request = prepared[-1]
        assert request["session"] is not old_binding[1]
        assert flow._request_generation[id(window)] == old_generation + (2 if invalidate_before_run else 1)
        assert not flow._marking_publication_is_current(window, old_binding)
        assert request["pool"] is verified_pool
        view, missing = flow._normal_base_history_view(
            request["session"], verified_pool, history_target, request["normal_base_view"])
        assert missing == 0
        if regular:
            assert request["normal_base_view"] is not None
            assert view["display_candles"] == verified_view["display_candles"]
            assert view["candles"] == verified_view["candles"]
        else:
            assert view["display_candles"] == base[-history_target:]
        publish(request["session"], view)
    assert calls == [len(expanded)]


@pytest.mark.parametrize("changed", [
    "content", "receipt", "source_identity", "source", "pool_timeframe", "selection_timeframe",
    "stock", "epoch", "login", "disconnected", "session", "publication", "warmup",
    "second_invalidation", "extra_generation",
])
def test_invalidated_native_source_proof_refuses_changed_basis(flow, monkeypatch, changed):
    from types import SimpleNamespace

    rules = {"bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
             "validation_market_scope": {"regular_market_only": True}}
    previous = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    rules["validation_market_scope"]["regular_market_only"] = False
    current = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    window = SimpleNamespace(stock=STOCK)
    broker = SimpleNamespace(connected=True, connection_epoch=7, login_session_id="offline")
    flow._broker = SimpleNamespace(broker_session_snapshot=lambda: broker)
    for session, generation in ((previous, 3), (current, 5)):
        session.source_selection = v5._select_v5_source(
            STOCK, "M3", True, False, NOW, window_key=id(window), generation=generation)
    rows = [{"time": f"202610{day:02d}{hour:02d}0000", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 10}
            for day in range(20, 30) for hour in (8, 9)]
    query = dict(stock_code=STOCK.code, timeframe_key="M3", timeframe_minutes=3,
                 market_data_identity="005930_AL", market_source="INTEGRATED",
                 request_id="NATIVE-PROOF", request_as_of="2026-10-29T20:01:00+09:00",
                 last_completed_time=rows[-1]["time"])
    pool = flow._pool_from_candles(previous, rows, requested_count=len(rows),
        request_id=query["request_id"], market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=query["request_as_of"])
    flow._open_windows[id(window)] = window
    flow._historical_pools[id(window)] = pool
    flow._validation_sessions[id(window)] = previous
    flow._request_generation[id(window)] = 3
    flow._pool_dispatch_snapshot(pool)
    binding = (pool, previous, 3, pool["_dispatch_snapshot"])
    window._v5_marking_display_publication = binding
    window._v5_session_display_basis = (binding, (7, "offline"))
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    flow._invalidate_window_input(window)
    assert window._v5_session_display_source_proof is not None
    assert not flow._marking_publication_is_current(window, binding)

    target = 3
    if changed == "content":
        pool["candles"][0]["close"] = 999
    elif changed == "receipt":
        pool["direct_selected_query"]["last_completed_time"] = "invalid"
    elif changed == "source_identity":
        pool["market_data_identity"] = "005930_NX"
    elif changed == "source":
        pool["market_source"] = "NXT"
    elif changed == "pool_timeframe":
        pool["timeframe_key"] = "M1"
        pool["timeframe_minutes"] = 1
    elif changed == "selection_timeframe":
        current.source_selection = v5._select_v5_source(
            STOCK, "M1", True, False, NOW, window_key=id(window), generation=5)
    elif changed == "stock":
        window.stock = v5.ValidationStockRef("000660", "Other")
    elif changed == "epoch":
        broker.connection_epoch = 8
    elif changed == "login":
        broker.login_session_id = "other"
    elif changed == "disconnected":
        broker.connected = False
    elif changed == "session":
        flow._validation_sessions[id(window)] = current
    elif changed == "publication":
        pool["_dispatch_snapshot"] = None
    elif changed == "warmup":
        target = len(rows)
    elif changed == "second_invalidation":
        flow._invalidate_window_input(window)
    elif changed == "extra_generation":
        flow._request_generation[id(window)] += 1
    window._session_display_republish_pending = current.request.settings_snapshot
    assert not flow._session_display_pool_reusable(window, current, pool, target)
    assert not flow._marking_publication_is_current(window, binding)


@pytest.mark.parametrize("change", ["replacement", "nested_rows"])
def test_krx_toggle_rejects_changed_published_native_receipt(flow, monkeypatch, change):
    from datetime import timedelta
    from types import SimpleNamespace
    from stock_market_source_plan import _build

    plan = _build(STOCK.code, STOCK.name, NOW.date(), NOW.isoformat(), False)
    monkeypatch.setattr(v5, "read_stock_market_source_plan", lambda *_: plan)
    flow._validation_source_by_code[STOCK.code] = False
    flow._broker = SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
        connected=True, connection_epoch=7, login_session_id="offline"))
    rules = {"bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
             "validation_market_scope": {"regular_market_only": True}}
    previous = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    rules["validation_market_scope"]["regular_market_only"] = False
    current = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    window = SimpleNamespace(stock=STOCK,
        _session_display_republish_pending=current.request.settings_snapshot)
    for session, generation in ((previous, 3), (current, 5)):
        session.source_selection = v5._select_v5_source(
            STOCK, "M3", False, False, NOW, window_key=id(window), generation=generation)
    rows = [dict(time=f"2026102909{minute:02d}00", open=100, high=102,
                 low=99, close=101, volume=10) for minute in range(0, 30, 3)]
    started = dict(code=STOCK.code, rqname="KRX-ORIGINAL", market_data_identity=STOCK.code,
                   market_source="KRX", display_only=True, connection_epoch=7,
                   login_session_id="offline", request_as_of=(NOW - timedelta(seconds=20)).isoformat(),
                   started_at=(NOW - timedelta(seconds=20)).isoformat())
    response = dict(started, request_id=started["rqname"], timeframe_key="M3", interval=3,
                    received_at=(NOW - timedelta(seconds=10)).isoformat(), rows=deepcopy(rows))
    pool = flow._pool_from_candles(previous, rows, requested_count=len(rows),
        request_id=started["rqname"], market_data_identity=STOCK.code, market_source="KRX")
    pool["native_minute_receipt"] = (started, response)
    flow._open_windows[id(window)] = window
    flow._historical_pools[id(window)] = pool
    flow._validation_sessions[id(window)] = previous
    flow._request_generation[id(window)] = 3
    flow._pool_dispatch_snapshot(pool)
    binding = (pool, previous, 3, pool["_dispatch_snapshot"])
    window._v5_marking_display_publication = binding
    window._v5_session_display_basis = (binding, (7, "offline"))
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    assert flow._session_display_pool_reusable(window, current, pool, 3)
    original = deepcopy(pool["native_minute_receipt"])
    if change == "replacement":
        replacement = deepcopy(pool["native_minute_receipt"])
        replacement[1]["received_at"] = NOW.isoformat()
        pool["native_minute_receipt"] = replacement
    else:
        response["rows"][0]["close"] = 999
    # Both changes still satisfy the real metadata/session validator.
    flow._require_selected_timeframe_pool(current, pool)
    flow._invalidate_window_input(window)
    window._session_display_republish_pending = current.request.settings_snapshot
    assert not flow._session_display_pool_reusable(window, current, pool, 3)
    assert not flow._marking_publication_is_current(window, binding)
    assert binding[3][2]["native_minute_receipt"] == original


@pytest.fixture
def real_window_case():
    from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
    yield from baseline.case.__wrapped__()


def test_real_v5_checkbox_retoggle_keeps_native_original_and_stale_jobs_blocked(real_window_case):
    from datetime import timedelta
    from time import monotonic, sleep
    from types import SimpleNamespace
    import weakref
    from PyQt5.QtCore import QSignalBlocker
    from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
    import gui_indicator_follow_signal_validation_v5_window as window_module

    case = real_window_case
    window = baseline.window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo), QSignalBlocker(
            window.validation_regular_market_only_check):
        combo = window.basic_signal_interval_combo
        combo.setCurrentIndex(next(i for i in range(combo.count())
            if window_module.normalize_validation_timeframe(combo.itemText(i))["key"] == "M3"))
        window.validation_regular_market_only_check.setChecked(True)
    flow = _flow()
    flow._operation_active_reader = lambda: False
    now = datetime.now(v5.SEOUL_TIMEZONE).replace(hour=20, minute=1, second=0, microsecond=0)
    flow._now_factory = lambda: now
    # Read the existing frozen stock plan; never initialize/change project source state.
    assert flow._verified_nxt_validation_stock(case.stock) is True
    base = [dict(time=f"202610{day:02d}{minute // 60:02d}{minute % 60:02d}00",
                 open=100, high=102, low=99, close=101, volume=10)
            for day in range(4, 29) for minute in range(8 * 60, 20 * 60, 3)
            if not (8 * 60 + 50 <= minute < 9 * 60
                    or 15 * 60 + 30 <= minute < 15 * 60 + 40)
            and not (day == 4 and 9 * 60 <= minute < 13 * 60)]
    warmup = v5.required_validation_history_context_bars(window._signal_validation_seed.settings_snapshot.to_dict())
    hidden = [dict(base[0], time=(datetime(2026, 9, 1, 9) + timedelta(
        days=i // 130, minutes=(i % 130) * 3)).strftime("%Y%m%d%H%M%S"))
        for i in range(warmup)]
    expanded = hidden + base
    calls = []

    def request_native(code, name, **kwargs):
        calls.append(kwargs["count"])
        assert kwargs["interval"] == 3 and kwargs["nxt_available"] is True
        assert len(calls) <= 3, "checkbox must not repeat the accepted native handoff"
        rows = base if len(calls) == 1 else expanded
        started = dict(ok=True, code=code, rqname=f"REAL-WINDOW-{len(calls)}",
            market_data_identity=f"{code}_AL", market_source="INTEGRATED", display_only=True,
            connection_epoch=7, login_session_id="offline", request_as_of=now.isoformat(),
            started_at=now.isoformat())
        kwargs["callback"](dict(started, request_id=started["rqname"], interval=3,
            type="minute_candles", trcode="opt10080", received_at=now.isoformat(),
            rows=flow._historical_rows_from_candles(rows), rows_count=len(rows), has_more=True))
        return started

    flow._broker = SimpleNamespace(
        request_minute_candles_display_only_integrated=request_native,
        broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline"))
    # The production opener connects both run_requested and chart_input_invalidated.
    flow._window_factory = lambda *_a, **_kw: window
    flow._last_selected_stock = None  # This fixture already owns the selected real Window.
    # No market host exists in this offline fixture; suppress its repeated M1 diagnostic.
    flow._sor_m1_diagnostic_skips[id(window)] = (case.stock.code, "LEASE_API_UNAVAILABLE")
    failures = []
    flow.validation_failed.connect(failures.append)
    assert flow._open_signal_window(window._signal_validation_seed, weakref.ref(window)) is window, failures
    flow._historical_count = 5000
    flow._history_targets[id(window)] = 5000
    window.set_historical_candle_count(5000)
    def wait_published(previous_generation=-1):
        deadline = monotonic() + 45
        while monotonic() < deadline:
            case.app.processEvents()
            assert not failures, failures
            binding = getattr(window, "_v5_marking_display_publication", None)
            if (binding is not None and binding[2] > previous_generation
                    and flow._marking_publication_is_current(window, binding)
                    and not flow._prepare_active and not flow._prepare_pending):
                return binding
            sleep(0.01)
        pytest.fail(f"real Window publication timed out: calls={calls}, failures={failures}")

    assert window.request_normal_base() is not None
    binding = wait_published()
    assert calls == [5000 + warmup, len(expanded), 5000]  # Initial query, warmup, display sidecar.
    original_calls = list(calls)
    original_display = deepcopy(window._candles)
    original_calculation = deepcopy(window._calculation_candles)
    assert len(original_display) == 3170
    assert len(original_calculation) >= 3170 + warmup
    original_pool = binding[0]
    for regular in (False, True):
        old = binding
        old_job = dict(window=window, session=old[1], pool=old[0], generation=old[2],
                       dispatch_publication=old[3])
        assert flow._prepare_request_is_current(old_job)
        window.validation_regular_market_only_check.setChecked(regular)
        assert flow._request_generation[id(window)] == old[2] + 2
        assert not flow._marking_publication_is_current(window, old)
        assert not flow._prepare_request_is_current(old_job)
        assert not flow._apply_pool_to_window(window, old[1], old[0],
            evaluation_count=5000, signal_entries=None)
        binding = wait_published(old[2])
        assert calls == original_calls
        assert binding[1] is not old[1]
        assert binding[0]["candles"] == original_pool["candles"]
        assert binding[0]["direct_selected_query"] == original_pool["direct_selected_query"]
        if regular:
            assert window._candles == original_display
            assert window._calculation_candles == original_calculation
