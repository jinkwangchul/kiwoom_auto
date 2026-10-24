"""The calculation-only sink cannot persist trading context or block a probe."""

import json

from sor_m3_production_capture import enqueue_preview, _QUEUE
from tests.test_sor_m3_operational_collector import _records


def test_capture_sanitizes_and_deduplicates(tmp_path, monkeypatch):
    import sor_m3_production_capture as capture

    capture._QUEUE.join()
    monkeypatch.setattr(capture, "_PATH", tmp_path / "production.jsonl")
    capture._SEEN.clear()
    _v5, production, _mock = _records()
    output = dict(production[0], account_number="SECRET", positions=[{"secret": 1}],
                  order_queue=[{"secret": 2}])
    output["sor_preview_evidence"]["account_number"] = "SECRET"
    assert enqueue_preview(output) is True
    assert enqueue_preview(output) is False
    _QUEUE.join()
    lines = (tmp_path / "production.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "SECRET" not in lines[0]
    assert "positions" not in lines[0]
    saved = json.loads(lines[0])
    assert saved["sor_preview_evidence"]["timeframe"] == "M3"
    assert "sor_preview_routine_instance_id" not in saved
    assert len(saved["sor_preview_routine_instance_sha256"]) == 64


def test_capture_rejects_fake_source_and_file_cap(tmp_path, monkeypatch):
    import sor_m3_production_capture as capture

    capture._QUEUE.join()
    monkeypatch.setattr(capture, "_PATH", tmp_path / "production.jsonl")
    capture._SEEN.clear()
    _v5, production, _mock = _records()
    invalid = dict(production[0], queue_connected=True)
    assert enqueue_preview(invalid) is False
    invalid = dict(production[0], sor_preview_evidence=dict(
        production[0]["sor_preview_evidence"], timeframe="M1"))
    assert enqueue_preview(invalid) is False
    capture._PATH.write_bytes(b"x" * capture._MAX_BYTES)
    assert enqueue_preview(production[0]) is True
    _QUEUE.join()
    assert capture._PATH.stat().st_size == capture._MAX_BYTES
