"""Focused, offline evidence for the V5 marking request lifecycle."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import gui_indicator_follow_signal_validation_v5_flow as module
from tests.test_v5_sor_marking_source import InlineThread, marking_case


def events(path):
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_marking_worker_logs_request_and_calculation_counts(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)
    snapshot = SimpleNamespace(
        candle_count=3, _entries=(object(), object()),
        to_candles=lambda: request["calculation"], to_entries=lambda: [],
    )
    monkeypatch.setattr(
        flow, "_prepare_replay_result_from_pool",
        lambda *_args, **_kwargs: SimpleNamespace(snapshot=snapshot),
    )
    monkeypatch.setattr(module, "_build_validation_projection_data", lambda *_args, **_kwargs: ({}, None))

    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    assert payloads[0]["snapshot"] is snapshot
    records = events(path)
    assert [record["event"] for record in records] == ["marking_requested", "marking_calculated"]
    assert records[1]["calculation_count"] == 3
    assert records[1]["display_count"] == 3
    assert records[1]["entry_count"] == 2
    assert all("settings" not in record and "candles" not in record and "key" not in record for record in records)


def test_marking_failure_uses_safe_reason_and_rejection_is_distinct(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    request["settings"] = SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": -1}})
    flow._start_marking(window, request)
    records = events(path)
    assert [record["event"] for record in records] == ["marking_requested", "marking_failed"]
    assert records[-1]["reason"] == "MARKING_TIMEFRAME_MISMATCH"


def test_marking_completion_logs_published_or_stale(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    window.apply_marking_snapshot = lambda *_args: True
    payload = dict(window_key=id(window), generation=1, key="mark", request_generation=1,
                   snapshot=object(), comparison=object(), error="")

    flow._on_marking_completed(payload)
    window.apply_marking_snapshot = lambda *_args: False
    flow._on_marking_completed(payload)
    window._marking_generation = 2
    flow._on_marking_completed(payload)

    assert [record["event"] for record in events(path)] == [
        "marking_published", "marking_stale", "marking_stale",
    ]


def test_marking_worker_failure_never_logs_exception_text(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)

    def fail(*_args, **_kwargs):
        raise ValueError("credential=secret; candle rows=private")

    monkeypatch.setattr(flow, "_prepare_replay_result_from_pool", fail)
    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    assert payloads[0]["error"].startswith("credential=secret")
    assert [record["event"] for record in events(path)] == [
        "marking_requested", "marking_failed",
    ]
    assert "secret" not in path.read_text(encoding="utf-8")
    assert events(path)[-1]["reason"] == "UNCLASSIFIED"


def test_marking_logger_is_bounded_append_only_and_sanitized(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    module._trace_v5_marking("marking_failed", 12, 1, 2, reason="password=secret\ncandles=[]")
    module._trace_v5_marking("marking_requested", 12, 1, 2, calculation_count=999999999)
    records = events(path)
    assert records[0]["reason"] == "UNCLASSIFIED"
    assert "calculation_count" not in records[1]
    assert "secret" not in path.read_text(encoding="utf-8")
    assert all(len(line) < 512 for line in path.read_text(encoding="utf-8").splitlines())

    prior = path.read_bytes()
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_MAX_BYTES", len(prior))
    module._trace_v5_marking("marking_requested", 12, 1, 2)
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    assert path.read_bytes() == prior
