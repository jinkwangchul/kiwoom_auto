"""Read-only Signal Queue Gate preview builder.

This module converts a Signal Queue Candidate into the final gate preview.
It performs no side effects and has no adapter or GUI integration.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE = "SIGNAL_QUEUE_GATE"
GATE_OPEN = "OPEN"
GATE_BLOCKED = "BLOCKED"
GATE_IGNORE = "IGNORE"
REQUIRED_CANDIDATE_FIELDS = (
    "stage",
    "candidate_result",
    "signal",
)


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _gate_result_for(candidate_result: Any) -> str:
    if candidate_result == "READY":
        return GATE_OPEN
    if candidate_result == "IGNORE":
        return GATE_IGNORE
    return GATE_BLOCKED


def _base_gate(
    candidate: dict[str, Any],
    *,
    ok: bool,
    gate_result: str,
    gate_reason: str,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": STAGE,
        "gate_result": gate_result,
        "gate_reason": gate_reason,
        "candidate_result": deepcopy(candidate.get("candidate_result")),
        "signal": deepcopy(candidate.get("signal")),
        "decision": deepcopy(candidate.get("decision")),
        "policy_result": deepcopy(candidate.get("policy_result")),
        "rule_source": deepcopy(candidate.get("rule_source")),
        "matched_rule_paths": _as_list(candidate.get("matched_rule_paths")),
        "condition_summary": _as_list(candidate.get("condition_summary")),
        "applied_policies": _as_list(candidate.get("applied_policies")),
        "blocked_policy": deepcopy(candidate.get("blocked_policy")),
        "signal_index": deepcopy(candidate.get("signal_index")),
        "delay_bar": deepcopy(candidate.get("delay_bar")),
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "blocked_reasons": deepcopy(blocked_reasons or []),
        "warnings": [],
    }


def _blocked(reason: str, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    source = candidate if isinstance(candidate, dict) else {}
    return _base_gate(
        source,
        ok=False,
        gate_result=GATE_BLOCKED,
        gate_reason=reason,
        blocked_reasons=[reason],
    )


def build_signal_queue_gate(signal_queue_candidate: dict[str, Any]) -> dict[str, Any]:
    """Build the final read-only gate preview from a queue candidate."""
    if not isinstance(signal_queue_candidate, dict):
        return _blocked("signal_queue_candidate must be dict")

    missing = [
        field
        for field in REQUIRED_CANDIDATE_FIELDS
        if field not in signal_queue_candidate
    ]
    if missing:
        return _blocked(
            "missing required signal_queue_candidate fields: " + ", ".join(missing),
            signal_queue_candidate,
        )

    if signal_queue_candidate.get("stage") != "SIGNAL_QUEUE_CANDIDATE":
        return _blocked("signal_queue_candidate.stage is invalid", signal_queue_candidate)

    candidate_result = signal_queue_candidate.get("candidate_result")
    if candidate_result not in {"READY", "BLOCKED", "IGNORE"}:
        return _blocked("signal_queue_candidate.candidate_result is invalid", signal_queue_candidate)

    gate_result = _gate_result_for(candidate_result)
    if gate_result == GATE_OPEN:
        reason = "signal queue candidate is ready; gate open"
    elif gate_result == GATE_IGNORE:
        reason = "signal queue candidate is ignored; gate ignored"
    else:
        reason = "signal queue candidate is blocked; gate blocked"

    return _base_gate(
        signal_queue_candidate,
        ok=True,
        gate_result=gate_result,
        gate_reason=reason,
    )


def preview_signal_queue_gate(signal_queue_candidate: dict[str, Any]) -> dict[str, Any]:
    """Alias for call sites that prefer preview naming."""
    return build_signal_queue_gate(signal_queue_candidate)
