"""Read-only one-shot routine signal probe.

The probe accepts in-memory rules and a market snapshot, evaluates the routine,
and returns the existing RoutineSignalPreview wrapper. It never writes project
rule files, runtime files, queues, execution services, order adapters, or GUI.
"""

from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

from engines.condition_engine import validate_market_snapshot
from routine_signal_preview_service import build_routine_signal_preview


def _load_evaluator():
    project_root = Path(__file__).resolve().parent
    engine_path = next((project_root / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_signal_probe_macd_engine", engine_path)
    module = module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"cannot load routine evaluator from {engine_path}")
    spec.loader.exec_module(module)
    return module.evaluate_indicator_follow_routine


def _condition_summary(preview: dict[str, Any]) -> list[str]:
    details = preview.get("details")
    return deepcopy(details) if isinstance(details, list) else []


def _matched_rule_paths(signal: str | None) -> list[str]:
    if signal == "BUY":
        return ["buy.groups"]
    if signal == "SELL":
        return ["sell.signals"]
    return []


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": "ROUTINE_SIGNAL_PROBE_BLOCKED",
        "preview_type": "routine_signal_preview",
        "blocked_reasons": [reason],
        "warnings": [],
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


def run_routine_signal_probe(
    rules: dict[str, Any],
    market_snapshot: dict[str, Any],
    *,
    rule_source: str = "evaluate_indicator_follow_routine",
) -> dict[str, Any]:
    """Evaluate a rules/snapshot pair and return a RoutineSignalPreview dict."""
    if not isinstance(rules, dict):
        return _blocked("rules must be dict")

    snapshot_validation = validate_market_snapshot(market_snapshot)
    if not snapshot_validation["ok"]:
        return _blocked(str(snapshot_validation["reason"]))

    rules_copy = deepcopy(rules)
    snapshot_copy = snapshot_validation["snapshot"]
    candles = deepcopy(snapshot_copy["candles"])
    evaluate_indicator_follow_routine = _load_evaluator()
    routine_signal = evaluate_indicator_follow_routine(candles, rules_copy, snapshot_copy)
    preview = build_routine_signal_preview(
        routine_signal,
        {
            "rule_source": rule_source,
            "matched_rule_paths": _matched_rule_paths(routine_signal.signal),
        },
    )
    preview["stage"] = "ROUTINE_SIGNAL_PROBE"
    preview["condition_summary"] = _condition_summary(preview)
    return preview
