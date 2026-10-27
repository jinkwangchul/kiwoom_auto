"""Fresh selected _AL rows can supply V5 when the M1 host has no projection."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import tests.test_indicator_follow_signal_validation_v5_m240_underfilled_refresh as refresh_fixture
from gui_indicator_follow_signal_validation_v5_flow import (
    IndicatorFollowSignalValidationV5Flow, ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from tests.test_indicator_follow_signal_validation_v5_m240_underfilled_refresh import (
    _candles, _run_refresh,
)
from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
    _registered_m1_calculation_case,
)
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
from tests.test_indicator_follow_v2_display_only_integrated_flow import (
    ValidationRequest, ValidationSession, ValidationSettingsSnapshot, ValidationStockRef,
)
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
import gui_indicator_follow_signal_validation_v5_flow as flow_module


SEOUL = ZoneInfo("Asia/Seoul")


def _run_selected_refresh(tmp_path, monkeypatch, **options):
    failed = options.pop("failed", False)
    persist = IndicatorFollowSignalValidationV5Flow._persist_history
    seeded = False

    def persist_with_valid_seed(self, stock, minutes, key, count, candles, **kwargs):
        nonlocal seeded
        if not seeded:
            seeded = True
            kwargs["direct_selected_query"] = {
                "stock_code": stock.code, "timeframe_key": key,
                "timeframe_minutes": minutes,
                "market_data_identity": f"{stock.code}_AL",
                "market_source": "INTEGRATED", "request_id": "CACHED-SEED",
                "request_as_of": "2026-10-20T16:00:00+09:00",
                "last_completed_time": candles[-1]["time"],
            }
        return persist(self, stock, minutes, key, count, candles, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(IndicatorFollowSignalValidationV5Flow,
                        "_persist_history", persist_with_valid_seed)
        patcher.setattr(IndicatorFollowSignalValidationV5Flow,
                        "_verified_nxt_validation_stock", staticmethod(lambda _stock: True))
        patcher.setattr(IndicatorFollowSignalValidationV5Flow,
                        "_sync_v5_observation_lease", lambda self, _window: None)
        if failed:
            patcher.setattr(refresh_fixture, "_result", lambda *_args, **_kwargs:
                            ValidationHistoricalResult(False, reason="FAILED"))
        return _run_refresh(tmp_path, **options)


@pytest.mark.parametrize(("key", "minutes"), [("M60", 60), ("M120", 120)])
def test_underfilled_native_selected_refresh_keeps_overlapping_new_rows(
    tmp_path, monkeypatch, key, minutes,
):
    latest = datetime(2026, 10, 19, 8)
    cached = _candles(744, minutes, latest)
    incoming = [dict(row) for row in cached[-299:]]
    incoming[-1] = dict(incoming[-1], close=incoming[-1]["close"] + 1)
    incoming.extend(_candles(1, minutes, latest + timedelta(minutes=minutes)))

    requested, original, calls, installs, errors, saved = _run_selected_refresh(
        tmp_path, monkeypatch, key=key, minutes=minutes, incoming=incoming,
    )

    assert requested == 5900 and calls == [300] and errors == []
    assert original == cached
    assert saved is not None and len(saved.candles) == 745
    assert list(saved.candles)[:445] == cached[:445]
    assert list(saved.candles)[-2:] == incoming[-2:]
    assert [reason for _pool, reason in installs] == [
        "persistent_restore", "cache_refresh_merge",
    ]
    pool = installs[-1][0]
    assert pool["candles"] == list(saved.candles)
    assert pool["direct_selected_query"]["request_id"] == "OFFLINE-REFRESH"
    assert pool["direct_selected_query"]["last_completed_time"] == incoming[-1]["time"]


@pytest.mark.parametrize(("key", "minutes"), [("M60", 60), ("M120", 120)])
def test_underfilled_native_selected_refresh_rejects_disjoint_rows(
    tmp_path, monkeypatch, key, minutes,
):
    incoming = _candles(8, minutes, datetime(2026, 10, 20, 12))
    _requested, cached, calls, installs, errors, saved = _run_selected_refresh(
        tmp_path, monkeypatch, key=key, minutes=minutes, incoming=incoming,
    )
    assert calls == [300, 600, 1200, 2400, 4800, 5900]
    assert errors == []
    assert saved is not None and list(saved.candles) == cached
    assert [reason for _pool, reason in installs] == [
        "persistent_restore", "persistent_restore",
    ]
    assert all(pool["candles"] == cached for pool, _reason in installs)


@pytest.mark.parametrize(("key", "minutes"), [("M60", 60), ("M120", 120)])
def test_underfilled_native_selected_refresh_rejects_wrong_source(
    tmp_path, monkeypatch, key, minutes,
):
    _requested, cached, calls, installs, errors, saved = _run_selected_refresh(
        tmp_path, monkeypatch, key=key, minutes=minutes,
        response_identity="000660", response_source="KRX",
    )
    assert calls == [300]
    assert errors == ["HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID"]
    assert len(installs) == 1 and installs[0][1] == "persistent_restore"
    assert saved is not None and list(saved.candles) == cached


@pytest.mark.parametrize(("key", "minutes"), [("M60", 60), ("M120", 120)])
def test_underfilled_native_selected_refresh_rejects_failed_response(
    tmp_path, monkeypatch, key, minutes,
):
    _requested, cached, calls, installs, errors, saved = _run_selected_refresh(
        tmp_path, monkeypatch, key=key, minutes=minutes, failed=True,
    )
    assert calls == [300]
    assert errors == ["HISTORICAL: FAILED"]
    assert len(installs) == 1 and installs[0][1] == "persistent_restore"
    assert saved is not None and list(saved.candles) == cached


def test_identical_native_d1_today_refresh_keeps_fresh_receipt_binding(
    tmp_path, monkeypatch,
):
    cached = _candles(744, 1440, datetime(2026, 10, 20))
    incoming = [dict(row) for row in cached[-300:]]
    with monkeypatch.context() as patcher:
        patcher.setattr(refresh_fixture, "_candles", lambda count, minutes, latest:
                        cached if count == 744 else _candles(count, minutes, latest))
        requested, original, calls, installs, errors, saved = _run_selected_refresh(
            tmp_path, monkeypatch, key="D1", minutes=5, incoming=incoming,
        )

    assert requested == 5600 and calls == [300] and errors == []
    assert original == cached
    assert saved is not None and list(saved.candles) == cached
    assert len(installs) == 1 and installs[0][1] == "cache_refresh_merge"
    pool = installs[0][0]
    assert pool["candles"] == cached
    assert pool["direct_selected_query"]["request_id"] == "OFFLINE-REFRESH"
    assert pool["direct_selected_query"]["selected_row_sha256"]
    assert pool["request_id"] == "OFFLINE-REFRESH:CACHE_MERGE"


def test_active_m1_uses_fresh_selected_completed_and_current_rows():
    flow, session, _old, market, _cache = _registered_m1_calculation_case()
    now = datetime(2026, 10, 27, 9, 5, 30, tzinfo=SEOUL)
    flow._now_factory = lambda: now
    market._display_timeframe_candles.pop(("005930", 1), None)
    market._display_timeframe_session_identity.pop(("005930", 1), None)
    rows = [
        {"time": f"20261027090{minute}00", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 10}
        for minute in (3, 4, 5)
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=len(rows), request_id="selected-al-m1-active",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    completed, forming, query = flow._direct_selected_history_parts(snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=len(rows), request_id=snapshot.request_id,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["availability_state"] == "SOR_M1_SOURCE_UNAVAILABLE"
    view = flow._validation_pool_for_session(session, pool, target_count=len(rows))

    prepared = flow._sor_m1_calculation_view(session, pool, view, frozen)
    assert prepared["candles"] == rows
    assert prepared["display_candles"][-1] == rows[-1]

    replay_inputs = []

    class CaptureReplay:
        def evaluate_with_context(self, _snapshot, **kwargs):
            replay_inputs.append(kwargs.get("projected_candles_override"))
            raise RuntimeError("direct M1 replay captured")

    flow._replay_factory = lambda _session: CaptureReplay()
    with pytest.raises(RuntimeError, match="direct M1 replay captured"):
        flow._prepare_replay_result_from_pool(
            session, pool, history_target=3, evaluation_count=3,
            prepared_view_pool=prepared, sor_m1_calculation_snapshot=frozen,
        )
    assert replay_inputs == [rows]

    flow._now_factory = lambda: now + timedelta(minutes=2)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, pool, view, frozen)
    flow._now_factory = lambda: now

    pool["selected_direct_forming_snapshot"]["request_id"] = "other"
    changed = flow._sor_m1_calculation_snapshot(session, pool)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, pool, view, changed)


def test_active_m1_special_row_receipt_binds_accepted_pool(tmp_path, monkeypatch):
    flow, session, _old, market, _cache = _registered_m1_calculation_case()
    now = datetime(2026, 10, 27, 9, 5, 30, tzinfo=SEOUL)
    flow._now_factory = lambda: now
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    monkeypatch.setattr(flow_module, "_SOR_AL_M1_DIAGNOSTIC_PATH",
                        tmp_path / "v5_sor_al_m1.jsonl")
    market._display_timeframe_candles.pop(("005930", 1), None)
    market._display_timeframe_session_identity.pop(("005930", 1), None)
    rows = [
        {"time": stamp, "open": price, "high": price + 2,
         "low": price - 2, "close": price + 1, "volume": 10}
        for stamp, price in (
            ("20261026153400", 100), ("20261026153500", 101),
            ("20261027090300", 102), ("20261027090400", 103),
            ("20261027090500", 104),
        )
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=len(rows), request_id="opt10080_005930_special_m1",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    started = {"rqname": snapshot.request_id, "request_as_of": now.isoformat(),
               "connection_epoch": 7, "login_session_id": "test-session"}
    received = {"rows": snapshot.to_rows(), "request_id": snapshot.request_id,
                "trcode": "opt10080", "code": "005930", "connection_epoch": 7,
                "login_session_id": "test-session", "received_at": now.isoformat()}
    installed = []
    monkeypatch.setattr(flow, "_install_acquired_pool", lambda _window, _session, pool,
                        **_kwargs: installed.append(pool) or True)

    assert flow._handle_historical_result(
        SimpleNamespace(), session, ValidationHistoricalResult(
            True, snapshot, broker_al_request_receipt=(started, received)),
        evaluation_count=3,
    )
    pool = installed[0]
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["availability_state"] == "SOR_M1_SOURCE_UNAVAILABLE"
    view = flow._validation_pool_for_session(session, pool, target_count=len(rows))
    prepared = flow._sor_m1_calculation_view(session, pool, view, frozen)
    assert prepared["candles"] == [rows[0], *rows[2:]]
    assert pool["candles"] == [rows[0], *rows[2:4]]
    digest = hashlib.sha256(json.dumps(
        pool["candles"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    marker_path = flow._validation_cache_source_marker_path(
        session.request.stock, 1, "M1", len(rows))
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert pool["direct_selected_query"]["source_content_sha256"] == digest
    assert marker["direct_selected_query"]["source_content_sha256"] == digest
    assert marker["direct_m1_receipt"]["source_content_sha256"] == digest
    assert marker["candle_content_sha256"] == digest
    assert marker["direct_selected_query"]["request_as_of"] == now.isoformat()
    assert marker["direct_selected_query"]["selected_forming_sha256"]

    altered = deepcopy(pool)
    altered["direct_selected_query"]["source_content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, altered, view, frozen)


def test_active_d1_uses_fresh_selected_native_today_row_only_with_receipt():
    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1}, "validation_timeframe": {"key": "D1"},
    })
    session = ValidationSession(ValidationRequest(stock, settings, 1),
                                operation_active_reader=lambda: False)
    flow._validation_source_by_code[stock.code] = True
    now = datetime(2026, 10, 27, 9, 18, 32, tzinfo=SEOUL)
    flow._now_factory = lambda: now
    rows = [
        {"time": "20261026000000", "open": 100, "high": 105,
         "low": 99, "close": 103, "volume": 1000},
        {"time": "20261027000000", "open": 103, "high": 108,
         "low": 102, "close": 107, "volume": 1200},
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key="D1",
        requested_count=len(rows), request_id="selected-al-d1-active",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    selected, forming, query = flow._direct_selected_history_parts(snapshot)
    assert selected == rows and forming is None
    pool = flow._pool_from_candles(
        session, selected, requested_count=len(rows), request_id=snapshot.request_id,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query,
    )
    view = flow._validation_pool_for_session(session, pool, target_count=len(rows))
    assert flow._sor_m1_calculation_snapshot(session, pool) is None

    assert flow._sor_m1_calculation_view(session, pool, view, None)["candles"] == rows
    without_receipt = dict(pool, direct_selected_query=None)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, without_receipt, view, None)
    changed_hash = dict(pool, direct_selected_query=dict(
        pool["direct_selected_query"], selected_row_sha256="0" * 64))
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, changed_hash, view, None)
    flow._now_factory = lambda: now + timedelta(minutes=2)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, pool, view, None)
