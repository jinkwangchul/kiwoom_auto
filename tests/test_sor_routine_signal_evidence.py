"""Fail-closed extraction of actual Production and Mock routine outputs."""

from copy import deepcopy
import json

from sor_routine_signal_evidence import (
    build_source_marker, extract_mock_evidence, extract_production_evidence, main,
)


STAMP = "2026-10-24T09:01:00+09:00"
CUTOFF = "20261024090100"
RULES = {"bar": {"bar_minutes": 1}, "buy": {"groups": []}}
CANDLES = [{"bar_time": STAMP, "timeframe_minutes": 1, "trade_date": "2026-10-24",
            "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}]
SOURCE = {"available": True, "stock_code": "005930", "candles": CANDLES,
          "timeframe_minutes": 1, "market_source": "INTEGRATED",
          "market_data_identity": "005930_AL", "calculation_source": "BROKER_AL_CONFIRMED_M1",
          "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
          "calculation_only": True, "execution_enabled": False,
          "queue_connected": False, "send_order_connected": False}


def marker(side="BUY"):
    return build_source_marker(stock="005930", rules=RULES, candles=CANDLES,
                               source=SOURCE, side=side)


def test_production_actual_preview_output_extracts_without_broker_claim():
    captured = {"code": "005930", "signal": "SKIP",
                "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
                "execution_enabled": False, "queue_connected": False,
                "send_order_connected": False,
                "sor_preview_signal": "BUY", "sor_preview_evidence": marker()}
    result = extract_production_evidence([captured], cutoff=CUTOFF)
    assert result["consumer"] == "PRODUCTION"
    assert result["events"] == [{"time": CUTOFF, "side": "BUY"}]
    assert result["market_data_identity"] == "005930_AL"
    assert result["origin_proof"].startswith("PRODUCTION_ROUTINE_OUTPUT:")


def test_mock_actual_journal_event_extracts_same_context():
    source = marker("SELL")
    event = {"event_id": "ME-abc", "validation_session_id": "MV-abc",
             "stock_code": "005930", "event_type": "ROUTINE_EVALUATED",
             "timestamp": STAMP, "payload": {"signal": "SELL",
             "rules_hash": source["rules_hash"], "evaluation_cycle_id": "MC-abc",
             "sor_source_evidence": source}}
    journal = {"schema_version": "mock_validation_event_v1",
               "validation_session_id": "MV-abc", "events": [event]}
    result = extract_mock_evidence(journal, cutoff=CUTOFF)
    assert result["consumer"] == "MOCK"
    assert result["events"] == [{"time": CUTOFF, "side": "SELL"}]
    assert result["origin_proof"].startswith("MOCK_ROUTINE_EVALUATED:")


def test_missing_or_false_source_provenance_blocks():
    for field, value in (("market_data_identity", "005930"),
                         ("source_proof", "BROKER_ATTESTED"),
                         ("candles", []), ("timeframe_minutes", 3),
                         ("execution_enabled", True)):
        altered = deepcopy(SOURCE)
        altered[field] = value
        assert build_source_marker(stock="005930", rules=RULES,
                                   candles=CANDLES, source=altered, side="BUY") is None
    assert build_source_marker(stock="005930", rules={}, candles=CANDLES,
                               source=SOURCE, side="BUY") is None
    assert build_source_marker(stock="005930", rules={"buy": {}}, candles=CANDLES,
                               source=SOURCE, side="BUY") is None


def test_missing_hash_cutoff_or_event_binding_blocks():
    good = {"code": "005930", "signal": "SKIP",
            "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
            "execution_enabled": False, "queue_connected": False,
            "send_order_connected": False, "sor_preview_signal": "BUY",
            "sor_preview_evidence": marker()}
    assert extract_production_evidence([good], cutoff="20261024090200") is None
    assert extract_production_evidence([{**good, "sor_preview_signal": "SELL"}],
                                       cutoff=CUTOFF) is None
    broken = deepcopy(good)
    broken["sor_preview_evidence"].pop("rules_hash")
    assert extract_production_evidence([broken], cutoff=CUTOFF) is None
    assert extract_production_evidence([], cutoff=CUTOFF) is None
    source = marker()
    event = {"event_id": "ME-abc", "validation_session_id": "MV-abc",
             "stock_code": "005930", "event_type": "ROUTINE_EVALUATED",
             "timestamp": STAMP, "payload": {"signal": "BUY",
             "rules_hash": source["rules_hash"], "evaluation_cycle_id": "MC-abc",
             "sor_source_evidence": source}}
    journal = {"schema_version": "mock_validation_event_v1",
               "validation_session_id": "MV-abc", "events": [event]}
    assert extract_mock_evidence(journal, cutoff="20261024090200") is None
    event["payload"]["rules_hash"] = "0" * 64
    assert extract_mock_evidence(journal, cutoff=CUTOFF) is None


def test_actual_production_probe_result_carries_source_marker(tmp_path):
    from tests.test_sor_core_m1_input import _installed_host, _probe_sor, _project

    output, _selected = _probe_sor(tmp_path, _project(_installed_host(), 1))
    source = output["sor_preview_evidence"]
    assert source is not None
    assert source["side"] == "BUY"
    assert extract_production_evidence(
        [output], cutoff=source["evaluation_time"]
    )["events"] == [{"time": source["evaluation_time"], "side": "BUY"}]


def test_actual_mock_adapter_writes_marker_to_existing_event_journal():
    from mock_validation_virtual_execution import MockExecutionPolicy
    from tests.test_mock_indicator_follow_adapter import (
        MockIndicatorFollowAdapterTest, NOW, SESSION_ID, _buy_rules, _market,
    )

    case = MockIndicatorFollowAdapterTest("runTest")
    try:
        rules = _buy_rules()
        rules["bar"] = {"bar_minutes": 1}
        repository, _service, _engine, adapter, _selected = case.build({"A": rules})
        candle = dict(CANDLES[0])
        candle["bar_time"] = NOW.isoformat()
        candle["trade_date"] = NOW.date().isoformat()
        source = dict(SOURCE, candles=[candle])
        adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id="A", candles=[candle],
            market=_market(), policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id="MC-evidence-cycle", evaluated_at=NOW,
            candle_availability=source,
        )
        journal = {"schema_version": "mock_validation_event_v1",
                   "validation_session_id": SESSION_ID,
                   "events": repository.read_events(SESSION_ID)}
        result = extract_mock_evidence(journal, cutoff=NOW.strftime("%Y%m%d%H%M%S"))
        assert result is not None
        assert result["events"] == [{"time": NOW.strftime("%Y%m%d%H%M%S"),
                                     "side": "BUY"}]
    finally:
        case.doCleanups()


def test_mock_non_sor_event_payload_keeps_existing_shape():
    from tests.test_mock_indicator_follow_adapter import (
        MockIndicatorFollowAdapterTest, _buy_rules, SESSION_ID,
    )

    case = MockIndicatorFollowAdapterTest("runTest")
    try:
        repository, _service, _engine, adapter, _selected = case.build(
            {"A": _buy_rules()}
        )
        case.evaluate(adapter)
        event = next(event for event in repository.read_events(SESSION_ID)
                     if event["event_type"] == "ROUTINE_EVALUATED")
        assert "sor_source_evidence" not in event["payload"]
    finally:
        case.doCleanups()


def test_read_only_cli_outputs_audit_input_and_blocks_invalid_file(tmp_path, capsys):
    captured = [{"code": "005930", "signal": "SKIP",
                 "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
                 "execution_enabled": False, "queue_connected": False,
                 "send_order_connected": False, "sor_preview_signal": "BUY",
                 "sor_preview_evidence": marker()}]
    path = tmp_path / "captured.json"
    path.write_text(json.dumps(captured), encoding="utf-8")
    assert main(["PRODUCTION", str(path), CUTOFF]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == [
        {"time": CUTOFF, "side": "BUY"}]
    path.write_text('[{"code":"005930","code":"005930"}]', encoding="utf-8")
    assert main(["PRODUCTION", str(path), CUTOFF]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
