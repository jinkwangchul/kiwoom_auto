"""Direct-source evidence is selected by exact chart frame."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from sor_m3_operational_evidence import build_v5_records, compare_records, main
from sor_m3_production_capture import _safe_record
from sor_routine_signal_evidence import build_source_marker
from tests.test_sor_m3_operational_collector import _records
from tests.test_sor_m3_operational_evidence import CANDLE, RULES, SOURCE


def _direct(frame):
    minutes = int(frame[1:])
    candle = dict(CANDLE, timeframe_minutes=minutes)
    source = deepcopy(SOURCE)
    source.update(candles=[candle], timeframe_minutes=minutes)
    source["direct_source_receipt"]["timeframe_key"] = frame
    rules = deepcopy(RULES)
    rules["bar"]["bar_minutes"] = minutes
    return candle, source, rules


@pytest.mark.parametrize("frame", ["M5", "M240"])
def test_direct_marker_and_collector_compare_only_selected_frame(frame):
    candle, source, rules = _direct(frame)
    marker = build_source_marker(stock="005930", rules=rules,
                                 candles=[candle], source=source, side="BUY")
    assert marker is not None and marker["timeframe"] == frame
    v5, production, mock = _records()
    for record in (v5[0], production[0]["sor_preview_evidence"],
                   mock["events"][0]["payload"]["sor_source_evidence"]):
        record.update(marker)
    mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    assert _safe_record(production[0]) is not None
    result = compare_records(v5, production, mock, cutoff="20261024090000",
                             timeframe=frame)
    assert result["comparison"] == "MATCH"
    assert result["status"] == "BLOCKED"
    assert result["live_parity"] is False
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    production[0]["sor_preview_evidence"]["receipt_hash"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe=frame)["reason"] == "SOURCE_RULES_OR_PREFIX_MISMATCH"


def test_v5_worker_builds_direct_frame_records_from_exact_pool():
    row = {"time": "20261024090000", "open": 100, "high": 101,
           "low": 99, "close": 100, "volume": 10}
    for frame in ("M5", "M240"):
        minutes = int(frame[1:])
        pool = {"stock": SimpleNamespace(code="005930"), "timeframe_key": frame,
                "timeframe_minutes": minutes, "market_source": "INTEGRATED",
                "market_data_identity": "005930_AL", "candles": [row]}
        marker = {"canonical_stock_code": "005930", "market_source": "INTEGRATED",
                  "market_data_identity": "005930_AL",
                  "candle_content_sha256": sha256(json.dumps([row], sort_keys=True,
                      separators=(",", ":")).encode()).hexdigest()}
        snapshot = SimpleNamespace(stock=pool["stock"], timeframe_key=frame,
            timeframe_minutes=minutes, settings_hash="a" * 64,
            historical_request_id="AL-1", evaluated_start_index=0,
            evaluated_end_index=0)
        records = build_v5_records(pool, marker, snapshot, [row], [],
                                   generation=1, request_generation=1)
        assert len(records) == 1 and records[0]["timeframe"] == frame
        assert build_v5_records(dict(pool, timeframe_key="M3"), marker,
                                snapshot, [row], [], generation=1,
                                request_generation=1) == []


def test_new_direct_frames_use_bounded_capture_routes(tmp_path, monkeypatch):
    import sor_m3_mock_capture as mock_capture
    import sor_m3_production_capture as production_capture

    monkeypatch.setattr(production_capture, "_DIRECT_PATH", tmp_path / "production.jsonl")
    monkeypatch.setattr(mock_capture, "_DIRECT_PATH", tmp_path / "mock.jsonl")
    for frame in ("M5", "M240"):
        candle, source, rules = _direct(frame)
        marker = build_source_marker(stock="005930", rules=rules,
                                     candles=[candle], source=source, side="BUY")
        v5, production, mock = _records()
        production[0]["sor_preview_evidence"] = marker
        production[0]["sor_preview_routine_instance_id"] = f"RI-{frame}"
        production[0]["sor_preview_cycle_hash"] = sha256(frame.encode()).hexdigest()
        event = mock["events"][0]
        event["event_id"] = f"ME-{frame}"
        event["routine_instance_id"] = f"RI-{frame}"
        event["payload"]["evaluation_cycle_id"] = f"MC-{frame}"
        event["payload"]["rules_hash"] = marker["rules_hash"]
        event["payload"]["sor_source_evidence"] = marker
        assert production_capture.enqueue_preview(production[0]) is True
        assert mock_capture.enqueue_event(event) is True
    production_capture._QUEUE.join()
    for name in ("production.jsonl", "mock.jsonl"):
        records = [json.loads(line) for line in (tmp_path / name).read_text(
            encoding="utf-8").splitlines()]
        assert [record["sor_preview_evidence"]["timeframe"] if name == "production.jsonl"
                else record["payload"]["sor_source_evidence"]["timeframe"]
                for record in records] == ["M5", "M240"]


def test_m1_and_period_frames_fail_closed_without_direct_receipt_contract():
    v5, production, mock = _records()
    for frame in ("M1", "D1", "W1", "MO1", "Y1"):
        result = compare_records(v5, production, mock, cutoff="20261024090000",
                                 timeframe=frame)
        assert result["status"] == "BLOCKED"
        assert result["comparison"] == "UNAVAILABLE"
    candle, source, rules = _direct("M5")
    source["direct_source_receipt"]["timeframe_key"] = "M3"
    assert build_source_marker(stock="005930", rules=rules,
                               candles=[candle], source=source, side="BUY") is None


def test_mixed_frame_hash_duplicate_and_missing_routes_block():
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["timeframe"] = "M5"
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["input_hash"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["reason"] == "SOURCE_RULES_OR_PREFIX_MISMATCH"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["source_identity"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["reason"] == "PRODUCTION_MOCK_SOURCE_RECEIPT_MISMATCH"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["source_connection_epoch"] = 0
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    v5, production, mock = _records()
    assert compare_records(v5 + v5, production, mock,
                           cutoff="20261024090000", timeframe="M3")["reason"] == "V5_DUPLICATE_EVALUATION"
    assert compare_records([], production, mock, cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"
    assert compare_records(v5, [], mock, cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"
    assert compare_records(v5, production, [], cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"


def test_explicit_frame_selects_from_shared_direct_files(tmp_path, capsys):
    selected = _records()
    other = _records()
    for records, frame in ((selected, "M5"), (other, "M240")):
        candle, source, rules = _direct(frame)
        marker = build_source_marker(stock="005930", rules=rules,
                                     candles=[candle], source=source, side="BUY")
        v5, production, mock = records
        for item in (v5[0], production[0]["sor_preview_evidence"],
                     mock["events"][0]["payload"]["sor_source_evidence"]):
            item.update(marker)
        mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    other[2]["events"][0]["event_id"] = "ME-other"
    other[2]["events"][0]["payload"]["evaluation_cycle_id"] = "MC-other"
    v5 = selected[0] + other[0]
    production = selected[1] + other[1]
    mock = {**selected[2], "events": selected[2]["events"] + other[2]["events"]}
    for frame in ("M5", "M240"):
        assert compare_records(v5, production, mock,
                               cutoff="20261024090000",
                               timeframe=frame)["comparison"] == "MATCH"
    paths = [tmp_path / name for name in ("v5.jsonl", "production.json", "mock.json")]
    paths[0].write_text("".join(json.dumps(row) + "\n" for row in v5), encoding="utf-8")
    paths[1].write_text(json.dumps(production), encoding="utf-8")
    paths[2].write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(paths[0]), "--production", str(paths[1]),
            "--mock", str(paths[2]), "--cutoff", "20261024090000"]
    for frame in ("M5", "M240"):
        assert main(argv + ["--timeframe", frame]) == 2
        assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"


def test_cli_requires_explicit_frame_for_non_m3(tmp_path, capsys):
    v5, production, mock = _records()
    for record in (v5[0], production[0]["sor_preview_evidence"],
                   mock["events"][0]["payload"]["sor_source_evidence"]):
        record["timeframe"] = "M5"
    paths = [tmp_path / name for name in ("v5.jsonl", "production.json", "mock.json")]
    paths[0].write_text(json.dumps(v5[0]) + "\n", encoding="utf-8")
    paths[1].write_text(json.dumps(production), encoding="utf-8")
    paths[2].write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(paths[0]), "--production", str(paths[1]),
            "--mock", str(paths[2]), "--cutoff", "20261024090000"]
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "UNAVAILABLE"
    assert main(argv + ["--timeframe", "M5"]) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"
