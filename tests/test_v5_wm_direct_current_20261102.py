"""Offline W1/MO1 current acceptance; broker doubles are never live evidence."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from routines.지표추종매매 import routine_validation_historical as historical
from tests.test_v5_pre_al_first_contract_20261101 import case as _source_case


NOW = datetime.fromisoformat("2026-11-02T16:14:00+09:00")
TR = {"D1": ("opt10081", "opt10101"), "W1": ("opt10082", "opt10102"),
      "MO1": ("opt10083", "opt10103"), "Y1": ("opt10094", "opt10104")}


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@pytest.fixture
def wm(monkeypatch):
    c = _source_case.__wrapped__(monkeypatch, Path("unused-offline-wm-current"))
    c.clock[0] = NOW

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.clock[0].astimezone(tz)

    monkeypatch.setattr(historical, "datetime", Clock)
    monkeypatch.setattr(v5, "signal_validation_trace_enabled", lambda: False)
    monkeypatch.setattr(v5, "trace_signal_validation_event", lambda *_a, **_kw: None)

    def acquire(key="W1", regular=False):
        c.start(regular_only=regular, key=key)
        assert len(c.calls) == 1 and c.calls[0]["source"] == "INTEGRATED"
        rows = [dict(time=stamp, open=100.0, high=102.0, low=99.0,
                     close=101.0, volume=10.0) for stamp in
                ("20240102000000", "20250102000000", "20261001000000",
                 "20261102000000")]
        call = c.calls[0]
        c.clock[0] += timedelta(seconds=1)
        response = dict(call["started"], type="period_candles",
            request_id=call["started"]["rqname"], timeframe_key=key,
            trcode=TR[key][int(regular)],
            rows=c.flow._historical_rows_from_candles(rows), rows_count=len(rows),
            received_at=c.clock[0].isoformat(), has_more=False, warning="")
        call["options"]["callback"](response)
        assert not c.failures and len(c.prepared) == 1, c.failures
        c.session, c.pool = c.prepared[0]["session"], c.prepared[0]["pool"]
        c.view = c.flow._validation_pool_for_session(c.session, c.pool, target_count=2)
        c.rows, c.response = rows, response
        c.query = c.pool["direct_selected_query"]
        c.receipt = c.flow._verified_al_period_receipts[c.pool["request_id"]]
        return c

    c.acquire = acquire
    return c


def _calculate(c, expected=None):
    frozen = c.flow._sor_m1_calculation_snapshot(c.session, c.pool)
    assert frozen is None  # No market host, M1 registration, or M1 projection.
    return c.flow._sor_m1_calculation_view(c.session, c.pool, c.view, frozen,
                                          expected_calculation=expected)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True], ids=["FULL", "REG"])
def test_direct_native_current_works_without_m1_and_keeps_exact_suffix(wm, key, regular):
    c = wm.acquire(key, regular)
    before = deepcopy((c.pool, c.view, c.rows, c.query))
    result = _calculate(c, c.rows[-1:])
    assert result is c.view and result["candles"] == c.rows[-2:]
    assert result["display_candles"] == c.rows[-2:]
    assert (c.pool, c.view, c.rows, c.query) == before
    assert c.query["last_completed_time"] == "20261001000000"
    assert c.query["last_completed_time"] != c.rows[-1]["time"]
    assert not any(row.get("is_complete") is True for row in result["candles"])
    assert len(c.calls) == 1  # No M1 dependency or extra native request.
    assert result["market_session_scope"] == ("REGULAR" if regular else "FULL")
    assert result["direct_selected_query"]["trcode"] == TR[key][int(regular)]
    assert not any("premarket" in k or "aftermarket" in k for k in result)


DEFECTS = ["missing-receipt", "missing-content-binding", "missing-query", "response-sha", "content-sha",
    "rehash-content", "query-stock", "query-timeframe", "query-minutes",
    "query-source", "query-identity", "query-request", "pool-request", "view-request",
    "cache-restore", "query-session", "query-epoch", "query-trcode",
    "callback-time", "callback-missing", "request-time", "expired", "future",
    "broker-disconnected", "broker-session", "broker-epoch", "broker-unavailable",
    "wrong-scope", "view-source", "view-stock", "view-timeframe", "view-content",
    "view-display", "expected-calculation", "malformed-date", "coverage", "display-only",
    "completed-current", "missing-selection", "stale-generation", "newer-selection", "previous-date"]


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True], ids=["FULL", "REG"])
@pytest.mark.parametrize("defect", DEFECTS)
def test_unverified_current_fails_closed(wm, key, regular, defect, monkeypatch):
    c = wm.acquire(key, regular)
    expected = None
    query_changes = {
        "response-sha": ("response_rows_sha256", "0" * 64),
        "content-sha": ("source_content_sha256", "0" * 64),
        "query-stock": ("stock_code", "000660"),
        "query-timeframe": ("timeframe_key", "D1"),
        "query-minutes": ("timeframe_minutes", 3),
        "query-source": ("market_source", "NXT"),
        "query-identity": ("market_data_identity", "005930_NX"),
        "query-request": ("request_id", "other-request"),
        "query-session": ("session_sha256", "0" * 64),
        "query-epoch": ("connection_epoch", 8),
        "query-trcode": ("trcode", "opt10081"),
        "callback-time": ("broker_callback_received_at", NOW.isoformat()),
        "callback-missing": ("broker_callback_received_at", None),
        "request-time": ("request_started_at", (NOW + timedelta(milliseconds=1)).isoformat()),
        "wrong-scope": ("market_session_scope", "FULL" if regular else "REGULAR"),
    }
    if defect in query_changes:
        field, value = query_changes[defect]
        c.query[field] = value
    elif defect == "missing-receipt":
        c.flow._verified_al_period_receipts.clear()
    elif defect == "missing-content-binding":
        c.receipt.pop("source_content_sha256", None)
    elif defect == "missing-query":
        c.pool.pop("direct_selected_query")
        c.view.pop("direct_selected_query")
    elif defect in {"pool-request", "cache-restore"}:
        c.pool["request_id"] = "VALIDATION_CACHE_RESTORE" if defect == "cache-restore" else "other-request"
    elif defect == "view-request":
        c.view["request_id"] = "other-request"
    elif defect in {"expired", "future"}:
        c.clock[0] = NOW + timedelta(seconds=60 if defect == "expired" else -1)
    elif defect == "previous-date":
        c.clock[0] += timedelta(days=1)
    elif defect.startswith("broker-"):
        if defect == "broker-unavailable":
            monkeypatch.setattr(c.flow._broker, "broker_session_snapshot", lambda: None)
        else:
            field, value = {"broker-disconnected": ("connected", False),
                "broker-session": ("login_session_id", "RECONNECTED"),
                "broker-epoch": ("connection_epoch", 8)}[defect]
            setattr(c.broker_session, field, value)
    elif defect.startswith("view-"):
        field, value = {"view-source": ("market_source", "NXT"),
            "view-stock": ("stock", v5.ValidationStockRef("000660", "SK")),
            "view-timeframe": ("timeframe_key", "D1"),
            "view-content": ("candles", [dict(c.rows[-1], close=999.0)]),
            "view-display": ("display_candles", [dict(c.rows[-1], close=999.0)])}[defect]
        c.view[field] = value
    elif defect == "expected-calculation":
        expected = [dict(c.rows[-1], close=999.0)]
    elif defect in {"rehash-content", "malformed-date", "completed-current"}:
        field, value = {"rehash-content": ("close", 100.5),
            "malformed-date": ("time", "20240231000000"),
            "completed-current": ("is_complete", True)}[defect]
        # Even recomputing the query hash cannot create a new live receipt.
        c.pool["candles"][0 if defect == "malformed-date" else -1][field] = value
        c.query["source_content_sha256"] = _digest(c.pool["candles"])
        c.view["candles"] = deepcopy(c.pool["candles"][-2:])
        c.view["display_candles"] = deepcopy(c.view["candles"])
    elif defect in {"coverage", "display-only"}:
        c.view["source_coverage_sufficient" if defect == "coverage" else "display_only_integrated"] = defect != "coverage"
    elif defect == "missing-selection":
        monkeypatch.setattr(c.session, "source_selection", None)
    elif defect == "stale-generation":
        selection = c.session.source_selection
        c.flow._request_generation[selection.window_key] += 1
    elif defect == "newer-selection":
        selection = c.session.source_selection
        monkeypatch.setattr(c.session, "source_selection", v5._V5SourceSelection(
            selection.stock, selection.timeframe_key, selection.window_key,
            selection.generation, selection.market_session_scope,
            selection.market_data_identity, selection.market_source,
            selection.nxt_available, c.clock[0]))
    else:
        raise AssertionError(defect)
    with pytest.raises(ValueError):
        _calculate(c, expected)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True], ids=["FULL", "REG"])
def test_current_period_stamp_before_today_still_requires_fresh_receipt(wm, key, regular):
    wm.clock[0] += timedelta(days=1)
    c = wm.acquire(key, regular)
    assert c.rows[-1]["time"][:8] < c.clock[0].strftime("%Y%m%d")
    assert _calculate(c, c.rows[-1:]) is c.view
    c.flow._verified_al_period_receipts.clear()
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
        _calculate(c)


@pytest.mark.parametrize("key", ["W1", "MO1"])
@pytest.mark.parametrize("regular", [False, True], ids=["FULL", "REG"])
@pytest.mark.parametrize("verified", [False, True])
def test_marking_cannot_bypass_receipt_or_actual_warmup(wm, key, regular, verified):
    c = wm.acquire(key, regular)
    if not verified:
        c.flow._verified_al_period_receipts.clear()
    expected_error = "MARKING_WARMUP_INSUFFICIENT" if verified else "SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"
    # Exercise the real marking replay entry before any evaluator or virtual trade.
    with pytest.raises(ValueError, match=expected_error):
        c.flow._prepare_replay_result_from_pool(c.session, c.pool,
            history_target=2, prepared_view_pool=c.view,
            expected_sor_m1_calculation=c.rows[-2:],
            chart_range=(0, 1),
            chart_range_times=tuple(row["time"] for row in c.rows[-2:]),
            require_full_warmup_for_marking=True)


@pytest.mark.parametrize("key", ["D1", "Y1"])
@pytest.mark.parametrize("regular", [False, True], ids=["FULL", "REG"])
def test_d1_y1_existing_behavior_unchanged(wm, key, regular):
    c = wm.acquire(key, regular)
    if key == "Y1" and not regular:
        with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
            _calculate(c)
    else:
        assert _calculate(c) is c.view
