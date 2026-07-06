"""Read-only Signal Queue Candidate builder.

This module converts a Signal Policy Orchestrator preview into the final
read-only candidate object. It performs no side effects and has no adapter or
GUI integration.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE = "SIGNAL_QUEUE_CANDIDATE"
CANDIDATE_TYPE = "QUEUE_SIGNAL"
CANDIDATE_READY = "READY"
CANDIDATE_BLOCKED = "BLOCKED"
CANDIDATE_IGNORE = "IGNORE"
REQUIRED_ORCHESTRATOR_FIELDS = (
    "stage",
    "decision",
    "policy_result",
    "policy_orchestrator",
    "policy_orchestrator_result",
    "signal",
    "rule_source",
    "matched_rule_paths",
    "condition_summary",
    "applied_policies",
    "blocked_policy",
)


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _candidate_result_for(orchestrator_result: Any) -> str:
    if orchestrator_result == "PASS":
        return CANDIDATE_READY
    if orchestrator_result == "IGNORE":
        return CANDIDATE_IGNORE
    return CANDIDATE_BLOCKED


def _base_candidate(
    preview: dict[str, Any],
    *,
    ok: bool,
    candidate_result: str,
    candidate_reason: str,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": STAGE,
        "candidate_type": CANDIDATE_TYPE,
        "candidate_result": candidate_result,
        "signal": deepcopy(preview.get("signal")),
        "decision": deepcopy(preview.get("decision")),
        "policy_result": deepcopy(preview.get("policy_orchestrator_result", preview.get("policy_result"))),
        "candidate_reason": candidate_reason,
        "rule_source": deepcopy(preview.get("rule_source")),
        "matched_rule_paths": _as_list(preview.get("matched_rule_paths")),
        "condition_summary": _as_list(preview.get("condition_summary")),
        "applied_policies": _as_list(preview.get("applied_policies")),
        "blocked_policy": deepcopy(preview.get("blocked_policy")),
        "signal_index": deepcopy(preview.get("signal_index")),
        "delay_bar": deepcopy(preview.get("delay_bar")),
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "blocked_reasons": deepcopy(blocked_reasons or []),
        "warnings": [],
    }


def _blocked(reason: str, preview: dict[str, Any] | None = None) -> dict[str, Any]:
    source = preview if isinstance(preview, dict) else {}
    return _base_candidate(
        source,
        ok=False,
        candidate_result=CANDIDATE_BLOCKED,
        candidate_reason=reason,
        blocked_reasons=[reason],
    )


def build_signal_queue_candidate(policy_orchestrator_preview: dict[str, Any]) -> dict[str, Any]:
    """Build the final read-only queue candidate from an orchestrator preview."""
    if not isinstance(policy_orchestrator_preview, dict):
        return _blocked("policy_orchestrator_preview must be dict")

    missing = [
        field
        for field in REQUIRED_ORCHESTRATOR_FIELDS
        if field not in policy_orchestrator_preview
    ]
    if missing:
        return _blocked(
            "missing required policy_orchestrator_preview fields: " + ", ".join(missing),
            policy_orchestrator_preview,
        )

    if policy_orchestrator_preview.get("stage") != "SIGNAL_POLICY_PREVIEW":
        return _blocked("policy_orchestrator_preview.stage is invalid", policy_orchestrator_preview)
    if policy_orchestrator_preview.get("policy_orchestrator") != "SIGNAL_POLICY_ORCHESTRATOR":
        return _blocked("policy_orchestrator_preview.policy_orchestrator is invalid", policy_orchestrator_preview)

    orchestrator_result = policy_orchestrator_preview.get("policy_orchestrator_result")
    if orchestrator_result not in {"PASS", "REJECT", "IGNORE"}:
        return _blocked("policy_orchestrator_preview.policy_orchestrator_result is invalid", policy_orchestrator_preview)

    candidate_result = _candidate_result_for(orchestrator_result)
    if candidate_result == CANDIDATE_READY:
        reason = "signal policy orchestrator passed; queue candidate is ready"
    elif candidate_result == CANDIDATE_IGNORE:
        reason = "signal policy orchestrator ignored signal; queue candidate ignored"
    else:
        reason = "signal policy orchestrator rejected signal; queue candidate blocked"

    return _base_candidate(
        policy_orchestrator_preview,
        ok=True,
        candidate_result=candidate_result,
        candidate_reason=reason,
    )


def create_signal_queue_candidate(policy_orchestrator_preview: dict[str, Any]) -> dict[str, Any]:
    """Alias for call sites that prefer verb-first naming."""
    return build_signal_queue_candidate(policy_orchestrator_preview)
