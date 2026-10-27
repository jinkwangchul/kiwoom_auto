"""Regressions for the Oct 23 V5 SOR chart gates."""

from copy import deepcopy
import json

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
import indicator_follow_signal_validation_runtime_trace as runtime_trace
from routines.지표추종매매.routine_validation_contract import ValidationStockRef

from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
    _registered_m1_calculation_case,
)
from tests import test_indicator_follow_signal_validation_v5_m240_underfilled_refresh as refresh_tests
from routines.지표추종매매.routine_validation_historical import ValidationHistoricalResult


def test_verified_m1_tail_preserves_earlier_direct_integrated_bars():
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    direct = deepcopy(pool["candles"])
    key = ("005930", 1)
    market._display_timeframe_candles[key] = tuple(
        row for row in market._display_timeframe_candles[key]
        if row["bar_time"] >= "2026-10-20T09:02:00+09:00"
    )

    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["available"] is True
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)

    assert view["candles"][:2] == direct[:2]
    assert [row["time"] for row in view["candles"]] == [
        row["time"] for row in direct
    ]
    assert pool["candles"] == direct


def test_failed_al_probe_reports_broker_failure_without_using_cached_history(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        refresh_tests, "_result",
        lambda *_args, **_kwargs: ValidationHistoricalResult(
            False, reason="HISTORICAL_REQUEST_FAILED", error="AL broker timed out",
        ),
    )

    _count, _cached, calls, installs, errors, _saved = refresh_tests._run_refresh(
        tmp_path,
    )

    assert calls == [300]
    assert installs == []
    assert errors == ["HISTORICAL: HISTORICAL_REQUEST_FAILED (AL broker timed out)"]


@pytest.mark.parametrize("response_change, expected_reason, expected_ok", [
    ({"market_data_identity": "000660"}, "BROKER_SOURCE_PROVENANCE_INVALID", True),
    ({"ok": False, "reason_code": "BROKER_TIMEOUT", "error": "private token=secret"},
     "BROKER_TIMEOUT", False),
])
def test_m3_broker_error_trace_records_safe_response_provenance(
    tmp_path, monkeypatch, response_change, expected_reason, expected_ok,
):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(runtime_trace, "_TRACE_PATH", trace_path)
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)

    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            response = {
                "ok": True, "type": "minute_candles", "code": code,
                "request_id": "private-session-1", "rqname": "private-session-1",
                "market_data_identity": "000660_AL", "market_source": "INTEGRATED",
                "interval": 3, "trcode": "opt10080", "rows": [], "rows_count": 0,
                "received_at": "2026-10-23T08:03:00+09:00",
                "login_session_id": "private-session-id",
            }
            response.update(response_change)
            kwargs["callback"](response)
            return {
                "ok": True, "rqname": "private-session-1",
                "request_as_of": "2026-10-23T08:02:00+09:00",
                "started_at": "2026-10-23T08:02:01+09:00",
                "login_session_id": "private-session-id",
            }

    received = []
    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("000660", "SK hynix"), "M3", True,
    )
    requester.request_minute_candles_read_only(
        "000660", "SK hynix", interval=3, count=300, callback=received.append,
    )

    assert len(received) == 1 and received[0]["ok"] is False
    if expected_ok:
        assert received[0] == {
            "ok": False, "error": "V5_BROKER_SOURCE_PROVENANCE_INVALID",
        }
    record = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "v5_sor_historical_error"
    assert record["stock"] == "000660"
    assert record["timeframe"] == "M3"
    assert record["expected_market_source"] == "INTEGRATED"
    assert record["expected_market_data_identity"] == "000660_AL"
    assert record["observed_market_source"] == "INTEGRATED"
    assert record["observed_market_data_identity"] == response_change.get(
        "market_data_identity", "000660_AL",
    )
    assert record["response_ok"] is expected_ok
    assert record["reason"] == expected_reason
    assert record["error_class"] == (
        "SOURCE_IDENTITY_MISMATCH" if expected_ok else "BROKER_ERROR_TEXT"
    )
    assert record["broker_route"] == "request_minute_candles_display_only_integrated"
    assert record["source_snapshot_present"] is False
    assert record["request_as_of"] == "2026-10-23T08:02:00+09:00"
    assert record["started_at"] == "2026-10-23T08:02:01+09:00"
    assert record["received_at"] == "2026-10-23T08:03:00+09:00"
    assert record["code_path"] == "broker_response"
    assert "error_class" in record
    raw = trace_path.read_text(encoding="utf-8")
    assert "private" not in raw and "secret" not in raw and "rows" not in raw


def test_m3_historical_result_identity_mismatch_trace_has_snapshot(tmp_path, monkeypatch):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(runtime_trace, "_TRACE_PATH", trace_path)
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)

    _count, _cached, calls, installs, errors, _saved = refresh_tests._run_refresh(
        tmp_path / "cache", key="M3", minutes=3, response_identity="000660",
        response_source="KRX",
    )

    assert calls == [300]
    assert installs == []
    assert errors == ["HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID"]
    record = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["code_path"] == "historical_result"
    assert record["reason"] == "MARKET_SOURCE_IDENTITY_INVALID"
    assert record["error_class"] == "SOURCE_IDENTITY_MISMATCH"
    assert record["response_ok"] is True
    assert record["observed_market_source"] == "KRX"
    assert record["observed_market_data_identity"] == "000660"
    assert record["source_snapshot_present"] is True
    assert record["broker_route"] == "request_minute_candles_display_only_integrated"


def test_m3_broker_exception_trace_records_class_without_message(tmp_path, monkeypatch):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(runtime_trace, "_TRACE_PATH", trace_path)
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)

    class Broker:
        def request_minute_candles_display_only_integrated(self, *_args, **_kwargs):
            raise RuntimeError("private token=secret")

    received = []
    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("000660", "SK hynix"), "M3", True,
    )
    requester.request_minute_candles_read_only(
        "000660", "SK hynix", interval=3, count=300, callback=received.append,
    )

    assert received == [{"ok": False, "error": "private token=secret"}]
    record = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["reason"] == "BROKER_REQUEST_EXCEPTION"
    assert record["error_class"] == "RuntimeError"
    assert record["response_ok"] is False
    assert record["source_snapshot_present"] is False
    assert "private" not in trace_path.read_text(encoding="utf-8")
