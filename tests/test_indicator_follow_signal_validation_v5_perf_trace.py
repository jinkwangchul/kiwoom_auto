"""The shared probe keeps its API while V5 owns opt-in telemetry."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "indicator_follow_signal_validation_perf_trace.py"


def _load_probe():
    spec = spec_from_file_location("isolated_v5_perf_trace", MODULE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v5_switch_selects_v5_output_and_writer_without_changing_probe_api(monkeypatch):
    monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_V5_PERF", "1")
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_V4_PERF", raising=False)
    with patch("threading.Thread") as writer:
        probe = _load_probe()

    assert probe.ENABLED
    assert probe._collector.path.name == "kiwoom_v5_perf_trace.jsonl"
    assert writer.call_args.kwargs["name"] == "kiwoom-v5-perf-writer"
    assert callable(probe.emit)
    assert callable(probe.phase)
    assert callable(probe.stage)


def test_retired_v4_switch_does_not_enable_shared_probe(monkeypatch):
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_V5_PERF", raising=False)
    monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_V4_PERF", "1")
    with patch("threading.Thread") as writer:
        probe = _load_probe()

    assert not probe.ENABLED
    assert probe.phase() is None
    writer.assert_not_called()
