# -*- coding: utf-8 -*-
"""Approval gate for Chejan-to-lifecycle transition previews."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_APPROVED = "LIFECYCLE_APPROVED"
STATUS_DENIED = "DENIED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    approval: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "approval": deepcopy(approval) if isinstance(approval, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "lifecycle_write_allowed": status == STATUS_APPROVED,
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _policy_decision(policy: dict[str, Any]) -> tuple[bool | None, str | None]:
    if not policy:
        return None, "lifecycle_approval_policy must be a non-empty dict"
    if "lifecycle_approval_enabled" in policy and policy.get("lifecycle_approval_enabled") is not True:
        return False, "lifecycle approval disabled"
    for key in ("approved", "approval_allowed", "allow", "lifecycle_write_allowed"):
        if policy.get(key) is True:
            return True, None
        if policy.get(key) is False:
            return False, "lifecycle approval policy rejected"
    status = _text(policy.get("status") or policy.get("decision") or policy.get("approval_status")).upper()
    if status in {"APPROVED", "ALLOW", "ALLOWED", "PASS", "PASSED"}:
        return True, None
    if status in {"DENIED", "REJECTED", "REJECT", "BLOCKED"}:
        return False, "lifecycle approval policy rejected"
    if status == "INVALID":
        return None, "lifecycle_approval_policy status is INVALID"
    return None, "lifecycle_approval_policy approval decision is missing"


def _operator_approved(operator_context: dict[str, Any]) -> bool:
    return (
        operator_context.get("operator_confirmed") is True
        or operator_context.get("operator_lifecycle_approved") is True
        or operator_context.get("manual_lifecycle_transition_confirmed") is True
    )


def _validate_transition_payload(transition_result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    preview = _as_dict(transition_result.get("transition_preview"))
    lifecycle_event = _text(transition_result.get("candidate_lifecycle_event"))
    if not preview:
        issues.append("transition_preview is required")
    if not lifecycle_event:
        issues.append("candidate_lifecycle_event is required")
    elif preview and _text(preview.get("candidate_lifecycle_event")) != lifecycle_event:
        issues.append("candidate_lifecycle_event does not match transition_preview")

    identity = _as_dict(preview.get("identity")) if preview else {}
    missing_identity = [
        field
        for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id")
        if not _text(identity.get(field))
    ]
    if missing_identity:
        issues.append("identity missing fields: " + ", ".join(missing_identity))
    if preview and preview.get("lifecycle_created") is not False:
        issues.append("transition_preview.lifecycle_created must be false")
    if preview and (preview.get("runtime_write") is not False or preview.get("queue_write") is not False):
        issues.append("transition_preview must not write runtime or queue")
    return preview, issues


def evaluate_lifecycle_transition_approval(
    transition_preview_result: Any,
    lifecycle_approval_policy: Any,
    operator_context: Any,
) -> dict[str, Any]:
    """Decide whether a lifecycle transition preview may proceed to a future write layer."""
    transition = _as_dict(transition_preview_result)
    if not transition:
        return _result(status=STATUS_INVALID, issues=["transition_preview_result must be a dict"])

    status = _text(transition.get("status")).upper()
    warnings = list(transition.get("warnings") or [])
    if status == "BLOCKED":
        return _result(
            status=STATUS_DENIED,
            issues=["transition_preview_result.status is BLOCKED"] + list(transition.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["transition_preview_result.status is INVALID"] + list(transition.get("issues") or []),
            warnings=warnings,
        )
    if status != "TRANSITION_READY":
        return _result(status=STATUS_INVALID, issues=["transition_preview_result.status is not supported"], warnings=warnings)

    policy = _as_dict(lifecycle_approval_policy)
    operator = _as_dict(operator_context)
    if not operator:
        return _result(status=STATUS_INVALID, issues=["operator_context must be a non-empty dict"], warnings=warnings)

    preview, transition_issues = _validate_transition_payload(transition)
    if transition_issues:
        return _result(status=STATUS_INVALID, issues=transition_issues, warnings=warnings)

    policy_allowed, policy_issue = _policy_decision(policy)
    if policy_allowed is None:
        return _result(status=STATUS_INVALID, issues=[policy_issue or "lifecycle_approval_policy is malformed"], warnings=warnings)

    approval = {
        "approval_type": "LIFECYCLE_TRANSITION_APPROVAL_GATE",
        "approved": False,
        "candidate_lifecycle_event": transition.get("candidate_lifecycle_event"),
        "transition_preview": deepcopy(preview),
        "policy": deepcopy(policy),
        "operator_context": deepcopy(operator),
        "identity": deepcopy(_as_dict(preview.get("identity"))),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }

    issues: list[str] = []
    if policy_allowed is not True:
        issues.append(policy_issue or "lifecycle approval policy rejected")
    if not _operator_approved(operator):
        issues.append("operator lifecycle approval is missing")
    if operator.get("emergency_stop") is True:
        issues.append("emergency stop is active")
    if _text(transition.get("candidate_lifecycle_event")) in {_text(item) for item in _as_list(policy.get("blocked_lifecycle_events"))}:
        issues.append("candidate lifecycle event is blocked by policy")

    if issues:
        return _result(status=STATUS_DENIED, approval=approval, issues=issues, warnings=warnings)

    approval["approved"] = True
    approval["lifecycle_write_allowed"] = True
    approval["next_stage"] = "ORDER_LIFECYCLE_WRITE_REQUIRED"
    return _result(status=STATUS_APPROVED, approval=approval, issues=[], warnings=warnings)
