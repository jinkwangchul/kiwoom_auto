"""Direct M3 calculation evidence; no broker, GUI, or runtime writes."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sor_direct_m3_signal_evaluation import audit_direct_m3
import sor_direct_m3_signal_evaluation as audit


def _fixture(tmp_path: Path, count: int = 920):
    rules = json.loads((Path(__file__).resolve().parents[1] / "routine_instances"
        / "5fb57335-5c6f-4ab6-9534-bdbfd24e1f97" / "rules.json").read_text(encoding="utf-8"))
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    begin = datetime(2026, 9, 1, 9)
    candles = [{"time": (begin + timedelta(minutes=3*i)).strftime("%Y%m%d%H%M%S"),
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0 + i % 4, "volume": 100.0} for i in range(count)]
    source = {"stock": {"code": "005930", "name": "Samsung"},
              "timeframe_key": "M3", "timeframe_minutes": 3,
              "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
              "requested_count": count, "signal_entries_by_settings_hash": {}, "candles": candles}
    cache = tmp_path / "005930_AL_3_920.json"
    cache.write_text(json.dumps(source), encoding="utf-8")
    digest = hashlib.sha256(json.dumps(candles, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    sidecar = cache.with_name(cache.name + ".market-source.json")
    sidecar.write_text(json.dumps({"canonical_stock_code": "005930",
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "candle_content_sha256": digest}), encoding="utf-8")
    return cache, rules_path, sidecar, candles


def test_small_direct_m3_uses_real_consumers_with_bounded_window(tmp_path):
    cache, rules, _, candles = _fixture(tmp_path)
    result = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2, max_direct_bars=920)
    assert result["source"]["direct_m3_rows"] == 920
    assert result["window"]["evaluated_bars"] == 2
    assert result["window"]["cutoff"] == candles[-1]["time"]
    assert set(result["consumers"]) == {"V5_RAW", "PRODUCTION_SELECTION", "MOCK_SELECTION"}
    assert all(result["consumers"][name]["evaluations"] == 2
               for name in result["consumers"])
    assert result["status"] in {"BLOCKED", "MISMATCH"}
    assert result["broker_attested"] is False


@pytest.mark.parametrize("tamper", ["source", "sha", "identity", "timeframe", "cached_entries"])
def test_wrong_direct_source_fails_before_evaluation(tmp_path, tamper):
    cache, rules, sidecar, candles = _fixture(tmp_path)
    source = json.loads(cache.read_text(encoding="utf-8"))
    marker = json.loads(sidecar.read_text(encoding="utf-8"))
    if tamper == "source":
        source["market_source"] = "KRX"
    elif tamper == "sha":
        source["candles"][0]["close"] += 1
    elif tamper == "identity":
        marker["market_data_identity"] = "005930"
    elif tamper == "timeframe":
        source["timeframe_minutes"] = 1
    else:
        source["signal_entries_by_settings_hash"] = {"fake": []}
    cache.write_text(json.dumps(source), encoding="utf-8")
    sidecar.write_text(json.dumps(marker), encoding="utf-8")
    result = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2)
    assert result["status"] == "BLOCKED"
    assert result["reason"].startswith("SOURCE_")
    assert "consumers" not in result


def test_cutoff_and_context_cap_fail_closed(tmp_path):
    cache, rules, _, candles = _fixture(tmp_path)
    too_early = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                                as_of=candles[-1]["time"], evaluation_bars=2)
    assert too_early["status"] == "BLOCKED"
    assert too_early["reason"] == "UNCLOSED_CUTOFF"
    capped = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2,
                             max_direct_bars=100)
    assert capped["status"] == "BLOCKED"
    assert capped["reason"] == "INSUFFICIENT_DIRECT_M3_CONTEXT"


def test_no_m1_file_reads_or_fallback(tmp_path, monkeypatch):
    cache, rules, _, candles = _fixture(tmp_path)
    original = Path.read_text

    def strict_read(path, *args, **kwargs):
        assert "_1_" not in path.name and "M1" not in path.name
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", strict_read)
    result = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2)
    assert "consumers" in result


def test_first_selection_mismatch_carries_actual_records(tmp_path, monkeypatch):
    cache, rules, _, candles = _fixture(tmp_path)
    import mock_validation_indicator_follow_adapter as mock_adapter

    def wrong_selection(prefix, config, context, *, evaluator):
        return {"signal": "SELL", "signal_index": len(prefix) - 1,
                "reason": "injected mismatch", "matched_groups": [], "details": []}

    monkeypatch.setattr(mock_adapter, "_pure_signal_selection", lambda: wrong_selection)
    result = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2)
    assert result["status"] == "MISMATCH"
    assert result["first_mismatch"]["records"]["MOCK_SELECTION"][0]["reason"] == "injected mismatch"
    assert result["first_mismatch"]["time"] == candles[-2]["time"]


def test_v5_scan_error_is_explicitly_blocked(tmp_path, monkeypatch):
    cache, rules, _, candles = _fixture(tmp_path)
    from routines.지표추종매매.routine_validation_replay import ValidationHistoricalReplay

    def broken_scan(*args, **kwargs):
        raise ValueError("synthetic conversion failure")

    monkeypatch.setattr(ValidationHistoricalReplay, "scan_signal_entries", broken_scan)
    result = audit_direct_m3(cache, rules, cutoff=candles[-1]["time"],
                             as_of="20260904090000", evaluation_bars=2)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "V5_SCAN_ERROR"
    assert "synthetic conversion failure" in result["error"]
    assert "consumers" not in result


def test_real_samsung_direct_m3_benchmark():
    started = __import__("time").perf_counter()
    result = audit.audit_direct_m3(audit.DEFAULT_CACHE, audit.DEFAULT_RULES,
                                   cutoff="20261023195700", evaluation_bars=12)
    assert __import__("time").perf_counter() - started < 15
    assert result["source"]["direct_m3_rows"] == 5900
    assert result["window"]["direct_context_bars"] == 912
    assert result["status"] in {"BLOCKED", "MISMATCH"}
