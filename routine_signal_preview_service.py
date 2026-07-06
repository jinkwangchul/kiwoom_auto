"""Read-only preview wrapper for routine evaluation signals.

This module never writes runtime files, never enqueues signals, and never calls
execution or order adapters. It only converts an already evaluated RoutineSignal
into an operator-review payload.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from engines.signal_result import RoutineSignal, signal_to_dict


PREVIEW_TYPE = "routine_signal_preview"
DEFAULT_ENGINE_VERSION = "routine_signal_preview_v1"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _context_dict(context: Any) -> dict[str, Any]:
    return deepcopy(context) if isinstance(context, dict) else {}


def build_routine_signal_preview(
    routine_signal: RoutineSignal,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an operator-facing preview for a RoutineSignal."""
    if not isinstance(routine_signal, RoutineSignal):
        return {
            "ok": False,
            "stage": "ROUTINE_SIGNAL_PREVIEW_BLOCKED",
            "preview_type": PREVIEW_TYPE,
            "blocked_reasons": ["routine_signal must be RoutineSignal"],
            "warnings": [],
        }

    ctx = _context_dict(context)
    signal_payload = signal_to_dict(routine_signal)
    rule_source = ctx.get("rule_source") or "evaluate_indicator_follow_routine"
    preview_time = ctx.get("preview_time") or _now_iso()
    engine_version = ctx.get("engine_version") or DEFAULT_ENGINE_VERSION
    matched_rule_paths = _as_list(ctx.get("matched_rule_paths"))
    condition_summary = _as_list(ctx.get("condition_summary"))

    return {
        "ok": True,
        "stage": "ROUTINE_SIGNAL_PREVIEW",
        "preview_type": PREVIEW_TYPE,
        "signal": signal_payload.get("signal"),
        "reason": signal_payload.get("reason"),
        "matched_groups": signal_payload.get("matched_groups", []),
        "details": signal_payload.get("details", []),
        "signal_index": signal_payload.get("signal_index"),
        "delay_bar": signal_payload.get("delay_bar"),
        "rule_source": rule_source,
        "matched_rule_paths": matched_rule_paths,
        "preview_time": preview_time,
        "condition_summary": condition_summary,
        "engine_version": engine_version,
        "routine_signal": signal_payload,
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "blocked_reasons": [],
        "warnings": [],
    }


def preview_routine_signal(
    routine_signal: RoutineSignal,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Alias kept explicit for call sites that prefer verb-first naming."""
    return build_routine_signal_preview(routine_signal, context)
