"""Contract tests for the detached, read-only three-consumer signal audit."""

from copy import deepcopy
import json

import pytest

import sor_three_consumer_signal_audit as audit
from sor_three_consumer_signal_audit import compare_three_consumer_signals, main
from stock_instance_day_projection import _marker_from_record
from stock_market_source_plan import _build
from datetime import date
from mock_validation_quick_chart import _signal_markers
from mock_validation_contract import payload_hash
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry, ValidationReplaySnapshot,
)


def evidence(consumer, events=None):
    return {
        "consumer": consumer,
        "origin_proof": f"independent-{consumer.lower()}-capture",
        "stock": "005930",
        "timeframe": "M1",
        "rules_hash": "a" * 64,
        "cutoff": "20261024150000",
        "market_data_identity": "005930_AL",
        "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
        "events": [] if events is None else events,
    }


def three(events=None):
    return [evidence(name, deepcopy(events)) for name in ("V5", "PRODUCTION", "MOCK")]


def event(time, side):
    return {"time": time, "side": side}


def test_quickchart_actual_record_markers_match_native_m3_without_live_claim():
    source = evidence("PRODUCTION", [event("20261024090300", "BUY")])
    source.update(stock="000660", timeframe="M3", cutoff="20261024090600",
                  market_data_identity="000660_AL")
    marker = _marker_from_record({
        "id": "recorded-1", "signal": "BUY", "signal_bar_time": "2026-10-24T09:03:00+09:00",
        "signal_bar_close": 100, "signal_timeframe_minutes": 3,
        "signal_trade_date": "2026-10-24"})
    chart = {
        "stock_code": "000660", "bar_minutes": 3, "source_plan_status": "READY",
        "source_plan": _build("000660", "Test", date(2026, 10, 24),
                              "2026-10-24T08:00:00+09:00", True),
        "candle_availability": {"available": True, "market_data_identity": "000660_AL",
                                "market_source": "INTEGRATED", "source_proof": audit.SOURCE_PROOF,
                                "timeframe_minutes": 3},
        "operation_timeline": {"status": "ONGOING"},
        "buy_signal_markers": [marker], "sell_signal_markers": [],
    }
    assert audit.compare_quickchart_marker_projection(source, chart) == {
        "status": "MATCH", "scope": "READ_ONLY_CHART_MARKER_PROJECTION_COMPARISON",
        "live_gui_parity": False, "broker_attested": False, "event_count": 1}
    assert audit.compare_quickchart_marker_projection(source, {
        **chart, "buy_signal_markers": []})["status"] == "MISMATCH"
    assert audit.compare_quickchart_marker_projection(source, {
        **chart, "buy_signal_markers": [marker, marker]})["status"] == "MISMATCH"
    assert audit.compare_quickchart_marker_projection(source, {
        **chart, "candle_availability": {**chart["candle_availability"],
                                      "market_data_identity": "000660_NX"}})["status"] == "BLOCKED"
    waiting = {**chart, "operation_timeline": {"status": "WAITING"},
               "buy_signal_markers": []}
    assert audit.compare_quickchart_marker_projection({**source, "events": []}, waiting)["status"] == "MATCH"
    assert audit.compare_quickchart_marker_projection(source, waiting)["status"] == "BLOCKED"
    assert compare_three_consumer_signals(*three())["status"] == "MATCH"


def test_quickchart_mock_record_marker_checks_rules_and_source():
    rules = {"bar": {"bar_minutes": 5}}
    source = evidence("MOCK", [event("20261024090500", "SELL")])
    source.update(stock="000660", timeframe="M5", cutoff="20261024091000",
                  market_data_identity="000660_AL", rules_hash=payload_hash(rules))
    document = {"session": {"stock_code": "000660"},
                "mock_operation_lifecycle": {"instance_operations": {
                    "A": {"operation_session_id": "operation-A"}}}}
    recorded = {"event_id": "recorded-mock-1", "event_type": "ROUTINE_EVALUATED",
                "stock_code": "000660", "routine_instance_id": "A",
                "payload": {"signal": "SELL", "signal_bar_time": "2026-10-24T09:05:00+09:00",
                            "signal_trade_date": "2026-10-24", "signal_bar_close": 100,
                            "signal_timeframe_minutes": 5,
                            "operation_identity": "operation-A", "rules_hash": payload_hash(rules)}}
    buy, sell = _signal_markers(document, instance_id="A", rules=rules,
                                trade_date="2026-10-24", events=[recorded])
    chart = {"stock_code": "000660", "bar_minutes": 5, "source_plan_status": "READY",
             "source_plan": _build("000660", "Test", date(2026, 10, 24),
                                   "2026-10-24T08:00:00+09:00", True),
             "candle_availability": {"available": True, "market_data_identity": "000660_AL",
                                     "market_source": "INTEGRATED", "source_proof": audit.SOURCE_PROOF,
                                     "timeframe_minutes": 5},
             "operation_timeline": {"status": "ONGOING"},
             "buy_signal_markers": buy, "sell_signal_markers": sell}
    assert audit.compare_quickchart_marker_projection(source, chart)["status"] == "MATCH"
    assert audit.compare_quickchart_marker_projection(source, {
        **chart, "sell_signal_markers": [{**sell[0], "rules_hash": "b" * 64}]})["status"] == "BLOCKED"


def v5_replay(*, complete=True):
    candles = [
        {"time": time, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for time in ("20261023090000", "20261023090100")
    ]
    entries = [
        ValidationReplayEntry(
            evaluation_side=side, evaluation_index=index,
            evaluation_time=candles[index]["time"],
            signal=side if (index, side) in ((0, "BUY"), (1, "SELL")) else None,
            reason="", signal_index=index if (index, side) in ((0, "BUY"), (1, "SELL")) else None,
            signal_time=candles[index]["time"] if (index, side) in ((0, "BUY"), (1, "SELL")) else None,
            delay_bar=0, matched_groups=[], details=[], trace={},
        )
        for index in range(2) for side in ("SELL", "BUY")
    ]
    if not complete:
        entries.pop()
    snapshot = ValidationReplaySnapshot(
        stock=ValidationStockRef("005930", "Samsung"), timeframe_minutes=1,
        settings_hash="a" * 64, historical_request_id="v5-request-1",
        evaluated_start_index=0, evaluated_end_index=1,
        dropped_raw_rows_count=0, candles=candles, entries=entries,
    )
    view = {"market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
            "candles": candles}
    return snapshot, view


def test_v5_adapter_extracts_only_actual_replay_signals_and_audit_stays_blocked():
    snapshot, view = v5_replay()
    record = audit.extract_v5_replay_evidence(snapshot, view, cutoff="20261023090100")
    assert record["events"] == [
        event("20261023090000", "BUY"), event("20261023090100", "SELL")]
    assert record["rules_hash"] == snapshot.settings_hash
    assert record["origin_proof"].startswith("V5_REPLAY:")
    assert compare_three_consumer_signals(record, None, None) == {
        "status": "BLOCKED", "scope": "READ_ONLY_RECORDED_SIGNAL_COMPARISON",
        "broker_attested": False, "live_gui_parity": False,
        "reason": "MISSING_EVIDENCE", "consumer": "PRODUCTION",
    }


def test_v5_adapter_rejects_incomplete_or_wrong_source_context():
    snapshot, view = v5_replay(complete=False)
    assert audit.extract_v5_replay_evidence(snapshot, view, cutoff="20261023090100") is None
    snapshot, view = v5_replay()
    for altered in ({**view, "market_data_identity": "005930"},
                    {**view, "candles": view["candles"][:-1]}):
        assert audit.extract_v5_replay_evidence(snapshot, altered, cutoff="20261023090100") is None
    assert audit.extract_v5_replay_evidence(snapshot, view, cutoff="20261023090000") is None


def test_equal_buy_sell_sequences_match_without_claiming_external_parity():
    sources = three([event("20261024090100", "BUY"), event("20261024103000", "SELL")])
    before = deepcopy(sources)

    result = compare_three_consumer_signals(*sources)

    assert result == {
        "status": "MATCH",
        "scope": "READ_ONLY_RECORDED_SIGNAL_COMPARISON",
        "event_count": 2,
        "broker_attested": False,
        "live_gui_parity": False,
    }
    assert sources == before


def test_empty_but_separately_attributed_sequences_are_comparable():
    assert compare_three_consumer_signals(*three())["status"] == "MATCH"


def test_reports_first_discrepancy_with_all_three_values():
    sources = three([
        event("20261024090100", "BUY"),
        event("20261024103000", "SELL"),
        event("20261024110000", "BUY"),
    ])
    sources[1]["events"][1]["side"] = "BUY"
    sources[2]["events"][2]["time"] = "20261024110100"

    result = compare_three_consumer_signals(*sources)

    assert result["status"] == "MISMATCH"
    assert result["first_discrepancy"] == {
        "index": 1,
        "events": {
            "V5": event("20261024103000", "SELL"),
            "PRODUCTION": event("20261024103000", "BUY"),
            "MOCK": event("20261024103000", "SELL"),
        },
    }


def test_reports_missing_event_at_first_length_difference():
    sources = three([event("20261024090100", "BUY")])
    sources[2]["events"] = []

    result = compare_three_consumer_signals(*sources)

    assert result["status"] == "MISMATCH"
    assert result["first_discrepancy"] == {
        "index": 0,
        "events": {
            "V5": event("20261024090100", "BUY"),
            "PRODUCTION": event("20261024090100", "BUY"),
            "MOCK": None,
        },
    }


@pytest.mark.parametrize("missing", (0, 1, 2))
def test_missing_consumer_blocks(missing):
    sources = three()
    sources[missing] = None
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "MISSING_EVIDENCE"
    assert result["consumer"] == ("V5", "PRODUCTION", "MOCK")[missing]


def test_wrong_attribution_blocks():
    sources = three()
    sources[1]["consumer"] = "V5"
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "INVALID_EVIDENCE"


def test_same_origin_proof_blocks_even_when_events_match():
    sources = three([event("20261024090100", "BUY")])
    sources[2]["origin_proof"] = sources[0]["origin_proof"]
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "SAME_EVIDENCE"


def test_missing_required_field_blocks():
    sources = three()
    del sources[2]["rules_hash"]
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("field,value", [
    ("timeframe", "M3"),
    ("rules_hash", "b" * 64),
    ("cutoff", "20261024145900"),
])
def test_context_mismatch_blocks_before_event_comparison(field, value):
    sources = three([event("20261024090100", "BUY")])
    sources[1][field] = value
    sources[2]["events"] = []
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CONTEXT_MISMATCH"
    assert result["field"] == field


def test_source_proof_mismatch_blocks_equal_events():
    sources = three([event("20261024090100", "BUY")])
    sources[1]["source_proof"] = "KRX_RAW"
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "INVALID_EVIDENCE"
    assert result["consumer"] == "PRODUCTION"


def test_v5_adapter_requires_source_proof_from_source_view():
    snapshot, view = v5_replay()
    del view["source_proof"]
    assert audit.extract_v5_replay_evidence(snapshot, view, cutoff="20261023090100") is None


@pytest.mark.parametrize("field,value", [
    ("origin_proof", ""),
    ("stock", "000660"),
    ("rules_hash", "bad"),
    ("cutoff", "20261324150000"),
    ("market_data_identity", "005930_NX"),
    ("events", "BUY"),
    ("events", [{"time": "20261024090100", "side": "HOLD"}]),
    ("events", [{"time": "20261024150100", "side": "BUY"}]),
    ("events", [event("20261024100000", "SELL"), event("20261024090000", "BUY")]),
])
def test_malformed_evidence_blocks(field, value):
    sources = three()
    sources[0][field] = value
    result = compare_three_consumer_signals(*sources)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "INVALID_EVIDENCE"


def test_cli_reads_three_files_and_reports_mismatch(tmp_path, capsys):
    sources = three([event("20261024090100", "BUY")])
    sources[2]["events"][0]["side"] = "SELL"
    paths = []
    for source in sources:
        path = tmp_path / f'{source["consumer"].lower()}.json'
        path.write_text(json.dumps(source), encoding="utf-8")
        paths.append(str(path))

    assert main(paths) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "MISMATCH"
    assert result["first_discrepancy"]["index"] == 0


def test_cli_blocks_same_file_for_two_consumers(tmp_path, capsys):
    paths = []
    for source in three():
        path = tmp_path / f'{source["consumer"].lower()}.json'
        path.write_text(json.dumps(source), encoding="utf-8")
        paths.append(str(path))

    assert main([paths[0], paths[0], paths[2]]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "SAME_EVIDENCE"


def test_cli_blocks_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "absent.json")] * 3) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "MISSING_EVIDENCE"


def test_cli_blocks_duplicate_json_keys(tmp_path, capsys):
    paths = []
    for source in three():
        path = tmp_path / f'{source["consumer"].lower()}.json'
        path.write_text(json.dumps(source), encoding="utf-8")
        paths.append(str(path))
    first = tmp_path / "v5.json"
    first.write_text(first.read_text(encoding="utf-8").replace(
        '"consumer": "V5"', '"consumer": "V5", "consumer": "V5"'),
        encoding="utf-8")

    assert main(paths) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "INVALID_EVIDENCE"
