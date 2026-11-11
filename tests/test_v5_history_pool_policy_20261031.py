"""Golden history-pool decisions, also run against the pre-extraction flow."""

from copy import deepcopy
from datetime import datetime
import importlib
import importlib.util

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
from tests.test_v5_wm_direct_current_20261102 import wm


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
    from pathlib import Path
    from types import SimpleNamespace

    rules = {"bar": {"bar_minutes": minutes}, "validation_timeframe": {"key": key},
             "validation_market_scope": {"regular_market_only": regular_only}}
    settings = v5.ValidationSettingsSnapshot(rules)
    session = v5.ValidationSession(
        v5.ValidationRequest(STOCK, settings, minutes),
        operation_active_reader=lambda: False)
    window = SimpleNamespace(stock=STOCK)
    session.source_selection = v5._select_v5_source(
        STOCK, key, True, regular_only, NOW, window_key=id(window), generation=4)
    rows = [{"time": f"202610{day:02d}{hour:02d}0000", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 10}
            for day in range(20, 30) for hour in (8, 9)]
    query = dict(stock_code=STOCK.code, timeframe_key=key, timeframe_minutes=minutes,
                 market_data_identity="005930_AL", market_source="INTEGRATED",
                 request_id="PUBLISHED-NATIVE", request_as_of=NOW.isoformat(),
                 last_completed_time=rows[-1]["time"])
    pool = flow._pool_from_candles(session, rows, requested_count=len(rows),
        request_id=query["request_id"], market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=NOW.isoformat())
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    projected, deficit = flow._normal_base_history_view(session, pool, 3, sidecar_override=None)
    assert deficit == 0
    assert projected["candles"] == rows[-5:]
    assert projected["display_candles"] == rows[-3:]
    assert pool["candles"] == rows and pool["direct_selected_query"] == query
    assert session.source_selection.market_session_scope == "FULL"
    # The ordinary FULL admission gate keeps M5's additional native callback requirement.
    assert flow._pool_matches(pool, STOCK, key, 5) is (key != "M5")
    stale = dict(pool, source_verified_at="2026-10-29T20:01:00+09:00")
    assert not flow._pool_matches(stale, STOCK, key, 5)
    old_projection = dict(pool, _regular_minute_source_view=pool)
    assert not flow._pool_matches(old_projection, STOCK, key, 5)
    with pytest.raises(ValueError, match="REGULAR_MINUTE_SOURCE_UNVERIFIED"):
        flow._validation_pool_for_session(session, old_projection, target_count=3)

    if key == "M1":
        assert not flow._pool_matches(pool, STOCK, key, 5 + v5._V5_M1_REQUEST_BUFFER_BARS)
    if key not in {"M1", "M5"}:
        flow._open_windows[id(window)] = window
        flow._historical_pools[id(window)] = pool
        flow._validation_sessions[id(window)] = session
        flow._request_generation[id(window)] = 4
        flow._history_targets[id(window)] = 3
        flow._operation_active_reader = lambda: False
        monkeypatch.setattr(v5, "StockRepository", lambda _: SimpleNamespace(
            resolve_stock_dir=lambda *_: Path(".")))
        monkeypatch.setattr(v5, "read_json_dict", lambda _: {})
        monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _: True)
        monkeypatch.setattr(flow, "_sync_v5_observation_lease", lambda _: None)
        monkeypatch.setattr(flow, "_cached_history", lambda *_a, **_kw: pytest.fail("unexpected cache lookup"))
        flow._historical_provider_factory = lambda *_a, **_kw: pytest.fail("unexpected native request")
        installed = []
        monkeypatch.setattr(flow, "_install_acquired_pool", lambda *a, **kw: installed.append((a, kw)))
        flow._run_validation(window, v5.IndicatorFollowSignalValidationRunRequest(settings, 3))
        assert len(installed) == 1
        assert installed[0][0][2] is pool
        effective = installed[0][0][1].request.settings_snapshot.to_dict()
        assert effective["validation_market_scope"] == {"regular_market_only": False}
        assert settings.to_dict() == rules


@pytest.mark.parametrize("history_target", [10, 5000])
@pytest.mark.parametrize("invalidate_before_run", [False, True])
def test_verified_native_warmup_retoggle_needs_no_second_handoff(
        flow, monkeypatch, history_target, invalidate_before_run):
    from pathlib import Path
    from types import SimpleNamespace

    rules = {"bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
             "validation_market_scope": {"regular_market_only": False}}
    session = v5.ValidationSession(
        v5.ValidationRequest(STOCK, v5.ValidationSettingsSnapshot(rules), 3),
        operation_active_reader=lambda: False)
    window = SimpleNamespace(stock=STOCK, _normal_base_requested=True,
        install_normal_base=lambda *_a, **_kw: None, _candle_preview_token=None,
        clear_candle_preview=lambda *_: None)
    session.source_selection = v5._select_v5_source(
        STOCK, "M3", True, False, NOW, window_key=id(window), generation=3)
    base = [dict(time=f"202610{day:02d}{minute // 60:02d}{minute % 60:02d}00",
                 open=100, high=102, low=99, close=101, volume=10)
            for day in range(4, 29) for minute in range(8 * 60, 20 * 60, 3)
            if not (8 * 60 + 50 <= minute < 9 * 60
                    or 15 * 60 + 30 <= minute < 15 * 60 + 40)
            and not (invalidate_before_run and day == 4 and 9 * 60 <= minute < 13 * 60)]
    if history_target == 10:
        base = base[-10:]
    rows = [dict(base[0], time=stamp) for stamp in
            ("20261003150000", "20261003150300")] + base
    query = dict(stock_code=STOCK.code, timeframe_key="M3", timeframe_minutes=3,
                 market_data_identity="005930_AL", market_source="INTEGRATED",
                 request_id="ACCEPTED-WARMUP", request_as_of=NOW.isoformat(),
                 last_completed_time=rows[-1]["time"])
    pool = flow._pool_from_candles(session, rows, requested_count=len(rows),
        request_id=query["request_id"], market_data_identity="005930_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=NOW.isoformat())
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    monkeypatch.setattr(flow, "_ensure_integrated_display_sidecar", lambda *_: None)
    monkeypatch.setattr(v5, "StockRepository", lambda _: SimpleNamespace(
        resolve_stock_dir=lambda *_: Path(".")))
    monkeypatch.setattr(v5, "read_json_dict", lambda _: {})
    monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _: True)
    monkeypatch.setattr(flow, "_sync_v5_observation_lease", lambda _: None)
    monkeypatch.setattr(flow, "_cached_history", lambda *_a, **_kw: pytest.fail("unexpected cache lookup"))
    flow._historical_provider_factory = lambda *_a, **_kw: pytest.fail("accepted FULL warmup must not request again")
    flow._operation_active_reader = lambda: False
    key = id(window)
    flow._open_windows[key] = window
    flow._historical_pools[key] = pool
    flow._validation_sessions[key] = session
    flow._request_generation[key] = 3
    flow._history_targets[key] = history_target
    view, deficit = flow._normal_base_history_view(session, pool, history_target)
    assert deficit == 0
    assert len(view["display_candles"]) == history_target
    assert len(view["candles"]) == history_target + 2
    assert view["display_candles"] == base[-history_target:]
    assert not flow._ensure_normal_base_history(window, session, pool, history_target)
    prepared = []
    monkeypatch.setattr(flow, "_start_pool_prepare", prepared.append)
    original = deepcopy(pool["candles"]), deepcopy(pool["direct_selected_query"])
    for turn, regular in enumerate((False, True, False, True), 1):
        old_generation = flow._request_generation[key]
        old_session = flow._validation_sessions[key]
        old_job = dict(window=window, session=old_session, pool=pool, generation=old_generation)
        assert flow._prepare_request_is_current(old_job)
        if invalidate_before_run:
            flow._invalidate_window_input(window)
            assert not flow._prepare_request_is_current(old_job)
        rules["validation_market_scope"]["regular_market_only"] = regular
        incoming = v5.ValidationSettingsSnapshot(rules)
        flow._run_validation(window, v5.IndicatorFollowSignalValidationRunRequest(incoming, history_target))
        assert len(prepared) == turn
        request = prepared[-1]
        assert flow._request_generation[key] == old_generation + (2 if invalidate_before_run else 1)
        assert not flow._prepare_request_is_current(old_job)
        assert request["pool"] is pool
        assert request["session"].request.settings_snapshot.to_dict()["validation_market_scope"] == {
            "regular_market_only": False}
        assert incoming.to_dict() == rules
        current, missing = flow._normal_base_history_view(
            request["session"], pool, history_target, request.get("normal_base_view"))
        assert missing == 0 and current["candles"] == view["candles"]
        assert current["display_candles"] == view["display_candles"]
        assert (pool["candles"], pool["direct_selected_query"]) == original


@pytest.fixture
def full_native_publication(wm, monkeypatch):
    """Reuse the existing FULL requester/provider/callback receipt, without forging proof."""
    c = wm.acquire("W1", False)
    flow, session, pool = c.flow, c.session, c.pool
    key = session.source_selection.window_key
    c.window = flow._open_windows[key]
    c.window.install_normal_base = lambda *_a, **_kw: None
    c.window._marking_current_settings = lambda: session.request.settings_snapshot
    c.window._validation_range_times = (c.rows[-2]["time"], c.rows[-1]["time"])
    c.window.validation_range = (0, 1)
    c.window._marking_source_key = lambda settings, times: (
        v5.IndicatorFollowSignalValidationV5Window._marking_source_key_for_candles(
            c.window.stock.code, settings, times, regular_only=False,
            calculation_candles=c.view["candles"], display_candles=c.view["display_candles"]))
    flow._history_targets[key] = 2
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    assert session.source_selection.market_session_scope == "FULL"
    assert flow._sor_m1_calculation_view(session, pool, c.view, None) is c.view
    assert flow._apply_pool_to_window(c.window, session, pool, evaluation_count=2,
        signal_entries=None, prepared_view_pool=c.view, normal_base_presentation=((), {}))
    c.binding = c.window._v5_marking_display_publication
    assert flow._selected_range_binding_identity(c.window) is not None
    return c


@pytest.mark.parametrize("changed", [
    "content", "receipt", "source_identity", "source", "pool_timeframe", "selection_timeframe",
    "stock", "epoch", "login", "disconnected", "session", "publication",
])
def test_full_native_source_mutations_reach_their_own_guards(full_native_publication, monkeypatch, changed):
    from dataclasses import replace

    c = full_native_publication
    flow, session, pool, window = c.flow, c.session, c.pool, c.window
    key = id(window)
    publication = c.binding[3]
    frozen = deepcopy(publication[2])
    assert "_regular_minute_source_view" not in pool
    assert flow._source_selection_is_current(session.source_selection)
    assert flow._marking_publication_is_current(window, c.binding)
    assert flow._selected_range_binding_identity(window) is not None
    require_results, receipt_results, owner_results = [], [], []
    require = flow._require_selected_timeframe_pool
    fresh = flow._fresh_selected_wm_view
    owned = flow._marking_publication_is_current

    def observe_require(*args):
        try:
            result = require(*args)
        except ValueError as exc:
            require_results.append(str(exc))
            raise
        require_results.append(None)
        return result

    def observe_receipt(*args, **kwargs):
        result = fresh(*args, **kwargs)
        receipt_results.append(result)
        return result

    def observe_owner(*args):
        result = owned(*args)
        owner_results.append(result)
        return result

    monkeypatch.setattr(flow, "_require_selected_timeframe_pool", observe_require)
    monkeypatch.setattr(flow, "_fresh_selected_wm_view", observe_receipt)
    monkeypatch.setattr(flow, "_marking_publication_is_current", observe_owner)
    assert flow._validation_pool_for_session(session, pool, target_count=2)["candles"] == c.rows[-2:]
    assert flow._sor_m1_calculation_view(session, pool, c.view, None) is c.view
    assert flow._selected_range_binding_identity(window) is not None
    assert require_results and all(result is None for result in require_results)
    assert receipt_results == [True] and owner_results == [True]
    require_results.clear()
    owner_results.clear()

    if changed == "content":
        pool["candles"][0]["close"] = 100.5  # Still valid OHLCV; the original content proof must reject it.
    elif changed == "receipt":
        pool["direct_selected_query"]["last_completed_time"] = "invalid"
    elif changed == "source_identity":
        pool["market_data_identity"] = "005930_NX"
    elif changed == "source":
        pool["market_source"] = "NXT"
    elif changed == "pool_timeframe":
        pool["timeframe_key"] = "M1"
    elif changed == "selection_timeframe":
        session.source_selection = replace(session.source_selection, timeframe_key="MO1")
    elif changed == "stock":
        window.stock = v5.ValidationStockRef("000660", "Other")
    elif changed == "epoch":
        c.broker_session.connection_epoch += 1
    elif changed == "login":
        c.broker_session.login_session_id = "other"
    elif changed == "disconnected":
        c.broker_session.connected = False
    elif changed == "session":
        flow._validation_sessions[key] = v5.ValidationSession(
            session.request, operation_active_reader=lambda: False)
    elif changed == "publication":
        pool["_dispatch_snapshot"] = None

    assert flow._request_generation[key] == c.binding[2]
    if changed in {"stock", "session", "publication"}:
        assert flow._selected_range_binding_identity(window) is None
        assert owner_results == [False]
        assert require_results == [] and receipt_results == [True]
    else:
        assert flow._source_selection_is_current(session.source_selection)
        assert owned(window, c.binding)
        if changed in {"epoch", "login", "disconnected"}:
            assert pool["candles"] == frozen["candles"]
            assert pool["direct_selected_query"] == frozen["direct_selected_query"]
            with pytest.raises(ValueError, match="^SOR_M1_BLOCKED:SOURCE_UNAVAILABLE$"):
                flow._sor_m1_calculation_view(session, pool, c.view, None)
            assert receipt_results == [True, False]
        else:
            reason = {
                "content": "SOR_DIRECT_PERIOD_SOURCE_UNVERIFIED",
                "receipt": "SOR_DIRECT_PERIOD_SOURCE_UNVERIFIED",
                "source_identity": "VALIDATION_POOL_MARKET_IDENTITY_MISMATCH",
                "source": "VALIDATION_POOL_MARKET_IDENTITY_MISMATCH",
                "pool_timeframe": "VALIDATION_POOL_TIMEFRAME_MISMATCH",
                "selection_timeframe": "VALIDATION_SOURCE_SELECTION_MISMATCH",
            }[changed]
            with pytest.raises(ValueError, match="^" + reason + "$"):
                flow._validation_pool_for_session(session, pool, target_count=2)
            assert require_results == [reason]
            assert receipt_results == [True]
    assert publication[2] == frozen


@pytest.mark.parametrize("changed", ["second_invalidation", "extra_generation"])
def test_full_native_publication_stale_controls(full_native_publication, changed):
    c = full_native_publication
    flow, session, pool, window = c.flow, c.session, c.pool, c.window
    generation = flow._request_generation[id(window)]
    before = deepcopy((pool["candles"], pool["direct_selected_query"]))
    assert flow._source_selection_is_current(session.source_selection)
    assert flow._marking_publication_is_current(window, c.binding)
    if changed == "second_invalidation":
        flow._invalidate_window_input(window)
        assert flow._request_generation[id(window)] == generation + 1
        flow._invalidate_window_input(window)
        assert flow._request_generation[id(window)] == generation + 2
    else:
        flow._request_generation[id(window)] += 1
    assert not flow._source_selection_is_current(session.source_selection)
    assert not flow._apply_pool_to_window(
        window, session, pool, evaluation_count=2, signal_entries=None)
    assert not flow._marking_publication_is_current(window, c.binding)
    assert (pool["candles"], pool["direct_selected_query"]) == before
    assert len(c.calls) == 1


def test_full_native_warmup_control_reports_missing_prefix(full_native_publication):
    c = full_native_publication
    view, missing = c.flow._normal_base_history_view(c.session, c.pool, 2)
    assert missing == 0
    assert view["candles"] == c.rows and view["display_candles"] == c.rows[-2:]
    view, missing = c.flow._normal_base_history_view(c.session, c.pool, 4)
    assert missing == 2
    assert view["candles"] == view["display_candles"] == c.rows
    assert c.flow._source_selection_is_current(c.session.source_selection)
    assert c.flow._selected_range_binding_identity(c.window) is not None


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
        _validation_range_times=("20261029090000", "20261029092700"), validation_range=(0, 9),
        _marking_current_settings=lambda: current.request.settings_snapshot,
        _marking_source_key=lambda settings, times: (settings.rules_hash, times))
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
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _: 2)
    assert flow._selected_range_binding_identity(window) is not None
    original = deepcopy(pool["native_minute_receipt"])
    if change == "replacement":
        replacement = deepcopy(pool["native_minute_receipt"])
        replacement[1]["received_at"] = NOW.isoformat()
        pool["native_minute_receipt"] = replacement
    else:
        response["rows"][0]["close"] = 999
    # Both changes still satisfy the real metadata/session validator.
    flow._require_selected_timeframe_pool(current, pool)
    assert flow._selected_range_binding_identity(window) is None
    flow._invalidate_window_input(window)
    assert not flow._apply_pool_to_window(
        window, previous, pool, evaluation_count=3, signal_entries=None)
    assert not flow._marking_publication_is_current(window, binding)
    assert binding[3][2]["native_minute_receipt"] == original


@pytest.fixture
def real_window_case():
    from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
    yield from baseline.case.__wrapped__()


def test_real_v5_checkbox_retoggle_keeps_native_original_and_stale_jobs_blocked(real_window_case, monkeypatch):
    from datetime import timedelta
    from time import monotonic, sleep
    from types import SimpleNamespace
    import weakref
    from PyQt5.QtCore import QSignalBlocker
    from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
    import gui_indicator_follow_signal_validation_v5_window as window_module

    case = real_window_case
    window = baseline.window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo):
        combo = window.basic_signal_interval_combo
        combo.setCurrentIndex(next(i for i in range(combo.count())
            if window_module.normalize_validation_timeframe(combo.itemText(i))["key"] == "M3"))
    flow = _flow()
    flow._operation_active_reader = lambda: False
    projection_inputs = []
    build_projection = v5._build_validation_projection_data
    def capture_projection(calculation, *args, **kwargs):
        result = build_projection(calculation, *args, **kwargs)
        projection_inputs.append(deepcopy(calculation))
        return result
    monkeypatch.setattr(v5, "_build_validation_projection_data", capture_projection)
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
        assert len(calls) <= 2, "legacy preference must not repeat the accepted FULL request"
        rows = expanded[-kwargs["count"]:]
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
    assert calls == [5000 + warmup, 5000]  # Native FULL calculation and display sidecar.
    original_calls = list(calls)
    original_display = deepcopy(window._candles)
    original_calculation = deepcopy(window._calculation_candles)
    assert len(original_display) == 5000
    assert len(original_calculation) == 5000 + warmup
    assert projection_inputs and projection_inputs[-1] == original_calculation
    assert window.visualization_descriptors and window.visualization_cache.series
    assert any(row["time"].endswith("080000") for row in original_calculation)
    assert any(row["time"].endswith("154200") for row in original_calculation)
    original_pool = binding[0]
    for regular in (False, True):
        old = binding
        old_job = dict(window=window, session=old[1], pool=old[0], generation=old[2],
                       dispatch_publication=old[3])
        assert flow._prepare_request_is_current(old_job)
        window._restore_validation_preferences({"regular_market_only": regular})
        window._on_validation_context_changed(normal_base=True, preserve_chart=True)
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
        assert window._candles == original_display
        assert window._calculation_candles == original_calculation
        assert projection_inputs[-1] == original_calculation
        assert binding[1].request.settings_snapshot.to_dict()["validation_market_scope"] == {
            "regular_market_only": False}
