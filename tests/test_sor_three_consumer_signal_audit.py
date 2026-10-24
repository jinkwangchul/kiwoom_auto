"""Contract tests for the detached, read-only three-consumer signal audit."""

from copy import deepcopy
import json

import pytest

from sor_three_consumer_signal_audit import compare_three_consumer_signals, main


def evidence(consumer, events=None):
    return {
        "consumer": consumer,
        "origin_proof": f"independent-{consumer.lower()}-capture",
        "stock": "005930",
        "timeframe": "M1",
        "rules_hash": "a" * 64,
        "cutoff": "20261024150000",
        "market_data_identity": "005930_AL",
        "events": [] if events is None else events,
    }


def three(events=None):
    return [evidence(name, deepcopy(events)) for name in ("V5", "PRODUCTION", "MOCK")]


def event(time, side):
    return {"time": time, "side": side}


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
