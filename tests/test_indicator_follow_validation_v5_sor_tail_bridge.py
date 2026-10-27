"""Offline V5 verified-tail bridge contract; no broker or GUI startup."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from indicator_follow_validation_v5_sor_m1_supply import ConfirmedSorM1
from indicator_follow_validation_v5_sor_source_gate import (
    FrozenSession, SourceEvent, SourceMetadata, VerifiedSorMerge,
)
from indicator_follow_validation_v5_sor_tail import (
    ConfirmedComponent, CoverageInterval, SourceIdentity,
)
from indicator_follow_validation_v5_sor_tail_bridge import (
    SorTailBatch, apply_verified_sor_tail,
)


KST = timezone(timedelta(hours=9))
KRX = SourceIdentity("KIWOOM", "KRX", "005930")
NXT = SourceIdentity("KIWOOM", "NXT", "005930")
SOR = SourceIdentity("KIWOOM", "INTEGRATED", "005930_AL")
SHA = "a" * 64
KEYS = ("M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240",
        "D1", "W1", "MO1", "Y1")


def at(day=20, hour=9, minute=0, *, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=KST)


def evidence(key):
    as_of = at(minute=2).replace(second=30)
    bucket = {
        "M1": at(minute=1), "D1": at(hour=0),
        "W1": at(day=19, hour=0), "MO1": at(day=1, hour=0),
        "Y1": at(day=1, hour=0, month=1),
    }.get(key, at())
    minutes = (1,) if key == "M1" else (0, 1)
    start = at(minute=minutes[0])
    end = at(minute=2)
    session = FrozenSession(7, "login-1", "005930", start, end, as_of)
    sources = tuple(SourceMetadata(identity, role, 7, "login-1", "005930",
                                   start, end, "independent-coverage", "NORMAL")
                    for identity, role in ((KRX, "KRX_PRODUCTION"),
                                           (NXT, "NXT_DISPLAY_ONLY"),
                                           (SOR, "INTEGRATED_CALCULATION")))
    events = tuple(SourceEvent(SOR, 7, "login-1", "005930",
                               at(minute=minute).replace(second=10), 11 + i)
                   for i, minute in enumerate(minutes))
    merge = VerifiedSorMerge(SOR, (KRX, NXT), 7, "login-1", "005930",
                             "independent-auditor", "merge-id", "dedup-id",
                             "sequence-id", "coverage-id",
                             tuple(event.sequence for event in events))
    coverage = tuple(CoverageInterval(at(minute=minute),
                                      at(minute=minute + 1), SOR)
                     for minute in minutes)
    bars = tuple(ConfirmedSorM1(
        ConfirmedComponent(span.start, span.end, "M1", SOR,
                           100 + i, 102 + i, 99 + i, 101 + i, 10),
        7, "login-1", "005930", 11 + i, True, "confirmed-record")
        for i, span in enumerate(coverage))
    extra = ()
    if key == "W1":
        extra = (ConfirmedComponent(at(day=19, hour=0), at(day=20, hour=0),
                                    "D1", SOR, 90, 95, 88, 94, 50),)
    elif key == "MO1":
        extra = (ConfirmedComponent(at(day=19, hour=0), at(day=20, hour=0),
                                    "D1", SOR, 90, 95, 88, 94, 50),)
    elif key == "Y1":
        extra = (ConfirmedComponent(at(day=1, hour=0, month=1),
                                    at(day=1, hour=0, month=2),
                                    "MO1", SOR, 80, 95, 78, 94, 500),)
    extra_coverage = tuple(CoverageInterval(row.start, row.end, SOR)
                           for row in extra)
    batch = SorTailBatch(
        session, sources, events, merge, coverage, bars, bucket,
        at() if key.startswith("M") and key != "MO1" else None,
        SHA, "independent-history-auditor", "history-id",
        "independent-calendar-auditor", "calendar-id",
        extra_coverage, extra,
    )
    return as_of, batch


def view(batch, key):
    old = {"time": "20261016150000", "open": 70, "high": 71,
           "low": 69, "close": 70, "volume": 5}
    current = {"time": batch.bucket_start.strftime("%Y%m%d%H%M%S"),
               "open": 999, "high": 1000, "low": 998, "close": 999,
               "volume": 99}
    return {"market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "timeframe_key": key, "timeframe_minutes": 1 if key == "M1" else
            int(key[1:]) if key.startswith("M") and key != "MO1" else 1440,
            "candles": [old, current], "display_candles": [dict(old), dict(current)]}


def apply(key, batch=None, pool=None, as_of=None, sha=SHA):
    expected_as_of, expected_batch = evidence(key)
    batch = expected_batch if batch is None else batch
    pool = view(batch, key) if pool is None else pool
    return apply_verified_sor_tail(
        pool, stock_code="005930", timeframe_key=key,
        timeframe_minutes=pool["timeframe_minutes"],
        as_of=expected_as_of if as_of is None else as_of,
        history_sha256=sha, batch=batch,
    )


@pytest.mark.parametrize("key", KEYS)
def test_all_thirteen_keys_replace_only_latest_and_share_display(key):
    _, batch = evidence(key)
    raw = view(batch, key)
    original = [dict(row) for row in raw["candles"]]
    result = apply(key, batch, raw)
    assert raw["candles"] == original
    assert raw["display_candles"][-1]["close"] == 999
    assert result["candles"][0] == original[0]
    assert result["display_candles"][0] == original[0]
    assert result["candles"][-1] == result["display_candles"][-1]
    assert result["candles"][-1]["close"] == (101 if key == "M1" else 102)
    assert result["candles"][-1]["volume"] >= 10
    assert result["sor_tail_calculation_complete"] is True


@pytest.mark.parametrize("change,reason", [
    ({"history_sha256": "b" * 64}, "SESSION_OR_ATTESTATION"),
    ({"history_verified_by": "KIWOOM"}, "SOURCE_OR_TARGET"),
    ({"calendar_verification_id": ""}, "SESSION_OR_ATTESTATION"),
    ({"session": FrozenSession(8, "login-1", "005930", at(), at(minute=2),
                               at(minute=2).replace(second=30))}, "M1_SUPPLY"),
])
def test_missing_or_stale_independent_proof_blocks(change, reason):
    _, batch = evidence("M5")
    with pytest.raises(ValueError, match=reason):
        apply("M5", replace(batch, **change))


def test_wrong_stock_market_time_and_target_alignment_block():
    as_of, batch = evidence("M5")
    raw = view(batch, "M5")
    for changed in (
        dict(raw, market_source="KRX"),
        dict(raw, market_data_identity="005930"),
        dict(raw, timeframe_minutes=3),
        dict(raw, candles=[*raw["candles"][:-1],
                           dict(raw["candles"][-1], time="20261020090500")]),
        dict(raw, display_candles=[*raw["display_candles"][:-1],
                                   dict(raw["display_candles"][-1], time="20261020090500")]),
    ):
        with pytest.raises(ValueError, match="SOR_VERIFIED_BLOCKED"):
            apply("M5", batch, changed)
    with pytest.raises(ValueError, match="SESSION_OR_ATTESTATION"):
        apply("M5", batch, raw, as_of=as_of - timedelta(minutes=1))
    with pytest.raises(ValueError, match="SESSION_OR_ATTESTATION"):
        apply("M5", batch, raw, as_of=as_of.replace(tzinfo=None))


def test_absent_provider_batch_blocks_and_tail_changes_signal_cache_identity():
    import gui_indicator_follow_signal_validation_v5_flow as flow_module

    as_of, batch = evidence("M5")
    raw = view(batch, "M5")
    with pytest.raises(ValueError, match="SOR_VERIFIED_BLOCKED:SESSION_OR_ATTESTATION"):
        apply_verified_sor_tail(
            raw, stock_code="005930", timeframe_key="M5", timeframe_minutes=5,
            as_of=as_of, history_sha256=SHA, batch=None,
        )
    source_identity = ("M5", 2, "request", "005930_AL", "INTEGRATED",
                       2, raw["candles"][0]["time"], raw["candles"][-1]["time"], SHA)
    first = apply("M5", batch, raw)
    refreshed_proof = apply("M5", replace(batch, merge=replace(
        batch.merge, verification_id="fresh-merge")), raw)
    changed_bar = replace(batch.m1_bars[-1], component=replace(
        batch.m1_bars[-1].component, close=101))
    second = apply("M5", replace(batch, m1_bars=(*batch.m1_bars[:-1], changed_bar)), raw)
    signature = flow_module.IndicatorFollowSignalValidationV5Flow._signal_view_signature
    assert signature(raw, source_identity=source_identity) != signature(
        first, source_identity=source_identity)
    assert signature(first, source_identity=source_identity) == signature(
        refreshed_proof, source_identity=source_identity)
    assert signature(first, source_identity=source_identity) != signature(
        second, source_identity=source_identity)


def test_forming_m1_missing_minute_overlap_and_cross_market_block():
    _, batch = evidence("M5")
    forming = ConfirmedComponent(at(minute=2), at(minute=3), "M1", SOR,
                                 103, 104, 102, 103, 5)
    for changed in (
        replace(batch, m1_bars=batch.m1_bars[:-1]),
        replace(batch, m1_coverage=batch.m1_coverage + (
            CoverageInterval(forming.start, forming.end, SOR),)),
        replace(batch, extra_components=(batch.m1_bars[0].component,),
                extra_coverage=(batch.m1_coverage[0],)),
        replace(batch, m1_bars=(replace(batch.m1_bars[0], component=replace(
            batch.m1_bars[0].component, source_identity=KRX)), *batch.m1_bars[1:])),
    ):
        with pytest.raises(ValueError, match="SOR_VERIFIED_BLOCKED"):
            apply("M5", changed)


def test_calendar_history_requires_independent_coarse_components():
    _, batch = evidence("W1")
    with pytest.raises(ValueError, match="EXTRA_COMPONENTS"):
        apply("W1", replace(batch, extra_coverage=(), extra_components=()))
    with pytest.raises(ValueError, match="EXTRA_COMPONENTS"):
        apply("W1", replace(
            batch, extra_coverage=batch.m1_coverage,
            extra_components=tuple(bar.component for bar in batch.m1_bars),
        ))


def test_injected_provider_reaches_worker_scan_and_replay_with_one_latest_view():
    import gui_indicator_follow_signal_validation_v5_flow as flow_module
    from routines.지표추종매매.routine_validation_contract import (
        ValidationRequest, ValidationSettingsSnapshot, ValidationStockRef,
    )
    from routines.지표추종매매.routine_validation_session import ValidationSession
    from indicator_follow_validation_history_contract import (
        required_validation_history_context_bars,
    )
    from tests.v5_validation_test_support import _FakeHost, _MemoryRecentStockStore, v5_rules
    from tests.qt_test_support import ensure_qapplication

    ensure_qapplication()
    rules = v5_rules()
    rules["bar"]["bar_minutes"] = 5
    rules["validation_timeframe"] = {
        "key": "M5", "kind": "MINUTE", "minutes": 5, "label": "M5",
    }
    assert flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT == 5_000
    assert required_validation_history_context_bars(rules) == 900
    assert flow_module.DEFAULT_CANDLES_MAX_COUNT == 200_000
    stock = ValidationStockRef("005930", "Samsung")
    session = ValidationSession(
        ValidationRequest(stock, ValidationSettingsSnapshot(rules), 5),
        operation_active_reader=lambda: False,
    )
    now, batch = evidence("M5")
    observed = []

    def provider(received_session, history_sha, frozen_as_of):
        assert received_session is session
        assert frozen_as_of == now
        observed.append("provider")
        return replace(batch, history_sha256=history_sha)

    class Replay:
        def scan_signal_entries(self, snapshot, *, context_provider):
            candles, _ = flow_module.project_validation_candles(snapshot)
            observed.append(("scan", candles[-1]["close"]))
            return ()

    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(), host=_FakeHost(stock), historical_count=2,
        recent_stock_store=_MemoryRecentStockStore(),
        replay_factory=lambda _session: Replay(),
        verified_sor_tail_provider=provider,
    )
    flow._validation_source_by_code[stock.code] = True
    flow._now_factory = lambda: now
    window = SimpleNamespace(_normal_base_requested=False)
    key = id(window)
    raw = view(batch, "M5")
    pool = dict(raw, stock=stock, request_id="raw-tr", requested_count=2)
    original = deepcopy(pool["candles"])
    flow._open_windows[key] = window
    flow._request_generation[key] = 1
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._history_targets[key] = 2
    source_identity = flow._pool_computation_source_identity(pool)
    request = {
        "window": window, "session": session, "pool": pool,
        "evaluation_count": 1, "generation": 1, "token": object(),
        "dispatch_source_identity": source_identity,
        "dispatch_snapshot": flow._pool_dispatch_snapshot(
            pool, source_identity=source_identity),
    }

    def inspect_replay(_session, _pool, *, prepared_view_pool, **_kwargs):
        candles = prepared_view_pool["candles"]
        display = prepared_view_pool["display_candles"]
        snapshot_candles, _ = flow_module.project_validation_candles(
            prepared_view_pool["snapshot"])
        assert candles[-1] == display[-1] == snapshot_candles[-1]
        observed.append(("replay", candles[-1]["close"]))
        raise RuntimeError("STOP_AFTER_VERIFIED_REPLAY_INPUT")

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    payloads = []
    with patch.object(flow_module, "Thread", InlineThread), \
            patch.object(flow, "_emit_signal_scan_completed", payloads.append), \
            patch.object(flow, "_prepare_replay_result_from_pool", inspect_replay):
        flow._start_pool_prepare(request)

    assert observed == ["provider", ("scan", 102), ("replay", 102)]
    assert "STOP_AFTER_VERIFIED_REPLAY_INPUT" in payloads[0]["error"]
    assert pool["candles"] == original
