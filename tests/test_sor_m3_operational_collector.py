import json
from copy import deepcopy

from sor_m3_operational_evidence import build_v5_records, compare_records, main
from tests.test_sor_m3_operational_evidence import CANDLE, RULES, SOURCE
from sor_routine_signal_evidence import build_source_marker


def _records(side="BUY"):
    marker = build_source_marker(stock="005930", rules=RULES,
                                 candles=[CANDLE], source=SOURCE, side=side)
    v5 = {**marker, "origin": "V5", "source_verified": True,
          "verification_kind": "CACHE_SIDECAR_CONTENT_MATCH_ONLY",
          "window_key": 1,
          "generation": 1, "request_generation": 1,
          "historical_request_id": "AL-M3-1", "activation_time": marker["evaluation_time"]}
    production = {"code": "005930", "signal": "SKIP",
                  "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
                  "execution_enabled": False, "queue_connected": False,
                  "send_order_connected": False, "sor_preview_signal": side,
                  "sor_preview_evidence": deepcopy(marker),
                  "sor_preview_routine_instance_id": "RI-1",
                  "sor_preview_observed_at": "2026-10-24T09:03:00+09:00",
                  "sor_preview_cycle_hash": "c" * 64}
    mock = {"schema_version": "mock_validation_event_v1",
            "validation_session_id": "MV-1", "events": [{
                "event_id": "ME-1", "validation_session_id": "MV-1",
                "stock_code": "005930", "event_type": "ROUTINE_EVALUATED",
                "timestamp": "2026-10-24T09:03:00+09:00",
                "payload": {"signal": side, "rules_hash": marker["rules_hash"],
                            "evaluation_cycle_id": "MC-1",
                            "operation_identity": "C-1", "sor_source_evidence": deepcopy(marker)}}]}
    return [v5], [production], mock


def test_collector_compares_independent_decisions_and_none():
    for side in ("BUY", "SELL", "NONE"):
        v5, production, mock = _records(side)
        result = compare_records(v5, production, mock,
                                 cutoff="20261024090000")
        assert result["comparison"] == "MATCH"
        assert result["matched_bars"] == 1


def test_collector_reports_first_difference_and_blocks_mixed_context():
    v5, production, mock = _records()
    mock["events"][0]["payload"]["signal"] = "SELL"
    mock["events"][0]["payload"]["sor_source_evidence"]["side"] = "SELL"
    result = compare_records(v5, production, mock, cutoff="20261024090000")
    assert result["comparison"] == "MISMATCH"
    assert result["first_difference"]["time"] == "20261024090000"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["input_hash"] = "b" * 64
    assert compare_records(v5, production, mock,
                           cutoff="20261024090000")["status"] == "BLOCKED"
    v5, production, mock = _records()
    mock["events"][0]["payload"]["rules_hash"] = "b" * 64
    assert compare_records(v5, production, mock,
                           cutoff="20261024090000")["status"] == "BLOCKED"
    v5, production, mock = _records()
    mock["events"][0]["payload"]["sor_source_evidence"]["source_session_sha256"] = "d" * 64
    assert compare_records(v5, production, mock,
                           cutoff="20261024090000")["reason"] == "PRODUCTION_MOCK_SOURCE_RECEIPT_MISMATCH"


def test_missing_or_stale_v5_and_bounded_cli_block(tmp_path, capsys):
    v5, production, mock = _records()
    assert compare_records([], production, mock,
                           cutoff="20261024090000")["reason"] == "V5_ACCEPTED_M3_MISSING"
    v5[0]["source_verified"] = False
    assert compare_records(v5, production, mock,
                           cutoff="20261024090000")["status"] == "BLOCKED"
    v5, production, mock = _records()
    old = dict(v5[0], generation=0, side="SELL")
    (tmp_path / "v5.jsonl").write_text(
        json.dumps(old) + "\n" + json.dumps(v5[0]) + "\n", encoding="utf-8")
    (tmp_path / "production.json").write_text(json.dumps(production), encoding="utf-8")
    (tmp_path / "mock.json").write_text(json.dumps(mock), encoding="utf-8")
    assert main(["--v5", str(tmp_path / "v5.jsonl"), "--production",
                 str(tmp_path / "production.json"), "--mock", str(tmp_path / "mock.json"),
                 "--cutoff", "20261024090000"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert result["comparison"] == "MATCH"
    assert main(["--v5", str(tmp_path / "absent.jsonl"), "--production",
                 str(tmp_path / "production.json"), "--mock", str(tmp_path / "mock.json"),
                 "--cutoff", "20261024090000"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "V5_ACCEPTED_M3_FILE_UNAVAILABLE"


def test_cli_reads_production_jsonl_and_rejects_duplicate_cutoff(tmp_path, capsys):
    v5, production, mock = _records()
    v5_path = tmp_path / "v5.jsonl"
    production_path = tmp_path / "production.jsonl"
    mock_path = tmp_path / "mock.json"
    v5_path.write_text(json.dumps(v5[0]) + "\n", encoding="utf-8")
    production_path.write_text(json.dumps(production[0]) + "\n", encoding="utf-8")
    mock_path.write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(v5_path), "--production", str(production_path),
            "--mock", str(mock_path), "--cutoff", "20261024090000"]
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"
    with production_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(production[0]) + "\n")
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "PRODUCTION_DUPLICATE_EVALUATION"


def test_v5_record_requires_verified_cache_and_evaluated_snapshot():
    from hashlib import sha256
    from types import SimpleNamespace
    row = {"time": "20261024090000", "open": 100, "high": 101,
           "low": 99, "close": 100, "volume": 10}
    pool = {"stock": SimpleNamespace(code="005930"), "timeframe_key": "M3",
            "timeframe_minutes": 3, "market_source": "INTEGRATED",
            "market_data_identity": "005930_AL", "candles": [row]}
    marker = {"canonical_stock_code": "005930", "market_source": "INTEGRATED",
              "market_data_identity": "005930_AL",
              "candle_content_sha256": sha256(json.dumps([row], ensure_ascii=False,
                  sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
    snapshot = SimpleNamespace(stock=pool["stock"], timeframe_key="M3",
        timeframe_minutes=3, settings_hash="a" * 64,
        historical_request_id="AL-M3-1", evaluated_start_index=0,
        evaluated_end_index=0)
    records = build_v5_records(pool, marker, snapshot, [row], [],
                               generation=1, request_generation=1)
    assert len(records) == 1
    assert records[0]["side"] == "NONE"
    assert records[0]["receipt_hash"]
    assert records[0]["input_hash"] == sha256(json.dumps([row], ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert build_v5_records(pool, dict(marker, market_source="KRX"), snapshot,
                            [row], [], generation=1, request_generation=1) == []
    assert build_v5_records(pool, marker, snapshot, [dict(row, close=99)], [],
                            generation=1, request_generation=1) == []
    assert build_v5_records(dict(pool, request_id="ANOTHER"), marker, snapshot,
                            [row], [], generation=1, request_generation=1) == []


def test_v5_publish_queues_once_and_stale_publish_is_ignored(tmp_path, monkeypatch):
    import gui_indicator_follow_signal_validation_v5_flow as module
    from types import SimpleNamespace
    path = tmp_path / "v5_sor_m3_operational.jsonl"
    monkeypatch.setattr(module, "_V5_M3_OPERATIONAL_PATH", path)
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", tmp_path / "marking.jsonl")
    window = SimpleNamespace(_marking_generation=1, _marking_key="mark",
                             apply_marking_snapshot=lambda *_args: True)
    flow = SimpleNamespace(_open_windows={id(window): window},
                           _request_generation={id(window): 1},
                           _sor_m3_marking_published={})
    payload = {"window_key": id(window), "generation": 1, "key": "mark",
               "request_generation": 1, "snapshot": object(), "comparison": object(),
               "error": "", "sor_m3_evidence_lines": '{"origin":"V5"}\n'}
    module.IndicatorFollowSignalValidationV5Flow._on_marking_completed(flow, payload)
    module.IndicatorFollowSignalValidationV5Flow._on_marking_completed(flow, payload)
    window._marking_generation = 2
    module.IndicatorFollowSignalValidationV5Flow._on_marking_completed(flow, payload)
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    assert path.read_text(encoding="utf-8").splitlines() == ['{"origin":"V5"}']


def test_normal_replay_publish_queues_once_after_current_accepted_result(monkeypatch):
    from types import SimpleNamespace
    import gui_indicator_follow_signal_validation_v5_flow as module

    queued = []
    monkeypatch.setattr(module, "_queue_v5_m3_evidence",
                        lambda line: queued.append(line) or True)
    flow = SimpleNamespace(_request_generation={7: 4}, _prepare_pending={},
                           _sor_m3_replay_published={})
    payload = {"window_key": 7, "generation": 4,
               "replay_snapshot": SimpleNamespace(historical_request_id="AL-M3-4"),
               "sor_m3_evidence_lines": '{"origin":"V5"}\n'}
    publish = module.IndicatorFollowSignalValidationV5Flow._append_accepted_m3_replay_evidence
    publish(flow, payload)
    publish(flow, payload)
    assert queued == [payload["sor_m3_evidence_lines"]]
    flow._prepare_pending[7] = object()
    publish(flow, dict(payload, generation=5))
    assert len(queued) == 1
    flow._prepare_pending.clear()
    publish(flow, dict(payload, generation=3))
    publish(flow, dict(payload, normal_base=True))
    assert len(queued) == 1
