"""Read-only final signal decision preview.

This module decides whether a RoutineSignalPreview is an executable signal.
It never writes runtime files, never enqueues signals, and never calls
execution or order adapters.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DECISION_ACCEPT = "ACCEPT"
DECISION_REJECT = "REJECT"
DECISION_IGNORE = "IGNORE"
STAGE = "SIGNAL_DECISION_PREVIEW"
VALID_SIGNALS = {"BUY", "SELL", None}
REQUIRED_PREVIEW_FIELDS = (
    "ok",
    "preview_type",
    "signal",
    "reason",
    "rule_source",
    "matched_rule_paths",
    "condition_summary",
)


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _base_result(
    *,
    ok: bool,
    decision: str,
    signal: str | None,
    reason: Any,
    decision_reason: str,
    rule_source: Any = None,
    matched_rule_paths: Any = None,
    condition_summary: Any = None,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": STAGE,
        "decision": decision,
        "signal": signal,
        "reason": deepcopy(reason),
        "decision_reason": decision_reason,
        "rule_source": deepcopy(rule_source),
        "matched_rule_paths": _as_list(matched_rule_paths),
        "condition_summary": _as_list(condition_summary),
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "blocked_reasons": deepcopy(blocked_reasons or []),
        "warnings": [],
    }


def _reject(reason: str, preview: dict[str, Any] | None = None) -> dict[str, Any]:
    source = preview if isinstance(preview, dict) else {}
    signal = source.get("signal")
    if signal not in VALID_SIGNALS:
        signal = None
    return _base_result(
        ok=False,
        decision=DECISION_REJECT,
        signal=signal,
        reason=source.get("reason"),
        decision_reason=reason,
        rule_source=source.get("rule_source"),
        matched_rule_paths=source.get("matched_rule_paths"),
        condition_summary=source.get("condition_summary"),
        blocked_reasons=[reason],
    )


def build_signal_decision_preview(routine_signal_preview: dict[str, Any]) -> dict[str, Any]:
    """Return ACCEPT, IGNORE, or REJECT for a RoutineSignalPreview."""
    if not isinstance(routine_signal_preview, dict):
        return _reject("routine_signal_preview must be dict")

    missing = [
        field
        for field in REQUIRED_PREVIEW_FIELDS
        if field not in routine_signal_preview
    ]
    if missing:
        return _reject(
            "missing required routine_signal_preview fields: " + ", ".join(missing),
            routine_signal_preview,
        )

    if routine_signal_preview.get("ok") is not True:
        return _reject("routine_signal_preview.ok is not true", routine_signal_preview)

    if routine_signal_preview.get("preview_type") != "routine_signal_preview":
        return _reject("routine_signal_preview.preview_type is invalid", routine_signal_preview)

    signal = routine_signal_preview.get("signal")
    if signal not in VALID_SIGNALS:
        return _reject("routine_signal_preview.signal is invalid", routine_signal_preview)

    if signal in {"BUY", "SELL"}:
        return _base_result(
            ok=True,
            decision=DECISION_ACCEPT,
            signal=signal,
            reason=routine_signal_preview.get("reason"),
            decision_reason=f"{signal} signal accepted by current signal decision rules",
            rule_source=routine_signal_preview.get("rule_source"),
            matched_rule_paths=routine_signal_preview.get("matched_rule_paths"),
            condition_summary=routine_signal_preview.get("condition_summary"),
        )

    return _base_result(
        ok=True,
        decision=DECISION_IGNORE,
        signal=None,
        reason=routine_signal_preview.get("reason"),
        decision_reason="no executable signal in routine signal preview",
        rule_source=routine_signal_preview.get("rule_source"),
        matched_rule_paths=routine_signal_preview.get("matched_rule_paths"),
        condition_summary=routine_signal_preview.get("condition_summary"),
    )


def decide_signal(routine_signal_preview: dict[str, Any]) -> dict[str, Any]:
    """Alias for call sites that prefer verb-first naming."""
    return build_signal_decision_preview(routine_signal_preview)
