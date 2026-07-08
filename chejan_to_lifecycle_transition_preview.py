# -*- coding: utf-8 -*-
"""Preview Chejan evidence review transition candidates for lifecycle handling."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "TRANSITION_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

CANDIDATE_TO_LIFECYCLE_EVENT = {
    "ORDER_RECEIVED_CANDIDATE": "ORDER_RECEIVED",
    "ORDER_REJECTED_CANDIDATE": "ORDER_REJECTED",
    "ORDER_CANCELLED_CANDIDATE": "ORDER_CANCELLED",
    "PARTIAL_FILL_CANDIDATE": "PARTIAL_FILL",
    "FULL_FILL_CANDIDATE": "FULL_FILL",
    "UNKNOWN_CANDIDATE": "UNKNOWN_EVENT",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    transition_preview: dict[str, Any] | None = None,
    candidate_lifecycle_event: str = "",
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "transition_preview": deepcopy(transition_preview) if isinstance(transition_preview, dict) else {},
        "candidate_lifecycle_event": candidate_lifecycle_event,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _validate_policy(policy: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = _as_dict(policy)
    if not payload:
        return payload, _result(status=STATUS_INVALID, issues=["lifecycle_policy must be a non-empty dict"])
    if "lifecycle_transition_enabled" not in payload:
        return payload, _result(status=STATUS_INVALID, issues=["lifecycle_policy.lifecycle_transition_enabled is required"])
    if payload.get("lifecycle_transition_enabled") is not True:
        return payload, _result(status=STATUS_BLOCKED, issues=["lifecycle transition disabled"])
    allowed = payload.get("allowed_lifecycle_events")
    if allowed is not None and not isinstance(allowed, (list, tuple, set)):
        return payload, _result(status=STATUS_INVALID, issues=["lifecycle_policy.allowed_lifecycle_events must be a sequence"])
    return payload, None


def _validate_snapshot(snapshot: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = _as_dict(snapshot)
    if not payload:
        return payload, _result(status=STATUS_INVALID, issues=["current_lifecycle_snapshot must be a non-empty dict"])
    if payload.get("snapshot_valid") is not True:
        return payload, _result(status=STATUS_INVALID, issues=["current_lifecycle_snapshot.snapshot_valid is not true"])
    existing_events = payload.get("existing_events", [])
    if not isinstance(existing_events, list):
        return payload, _result(status=STATUS_INVALID, issues=["current_lifecycle_snapshot.existing_events must be a list"])
    return payload, None


def _required_identity_missing(identity: dict[str, Any]) -> list[str]:
    return [
        field
        for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id")
        if not _text(identity.get(field))
    ]


def preview_chejan_lifecycle_transition(
    evidence_review_result: Any,
    lifecycle_policy: Any,
    current_lifecycle_snapshot: Any,
) -> dict[str, Any]:
    """Build a preview-only lifecycle transition candidate from evidence review."""
    review_result = _as_dict(evidence_review_result)
    if not review_result:
        return _result(status=STATUS_INVALID, issues=["evidence_review_result must be a dict"])

    status = _text(review_result.get("status")).upper()
    warnings = list(review_result.get("warnings") or [])
    if status == "EVIDENCE_REVIEW_BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["evidence_review_result.status is EVIDENCE_REVIEW_BLOCKED"] + list(review_result.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["evidence_review_result.status is INVALID"] + list(review_result.get("issues") or []),
            warnings=warnings,
        )
    if status != "EVIDENCE_REVIEW_OK":
        return _result(status=STATUS_INVALID, issues=["evidence_review_result.status is not supported"], warnings=warnings)
    if review_result.get("lifecycle_ready") is not True:
        return _result(status=STATUS_BLOCKED, issues=["evidence_review_result.lifecycle_ready is not true"], warnings=warnings)

    policy, policy_failure = _validate_policy(lifecycle_policy)
    if policy_failure is not None:
        policy_failure["warnings"] = warnings + list(policy_failure.get("warnings", []))
        return policy_failure

    snapshot, snapshot_failure = _validate_snapshot(current_lifecycle_snapshot)
    if snapshot_failure is not None:
        snapshot_failure["warnings"] = warnings + list(snapshot_failure.get("warnings", []))
        return snapshot_failure

    review = _as_dict(review_result.get("review"))
    if not review:
        return _result(status=STATUS_INVALID, issues=["evidence review payload is required"], warnings=warnings)

    candidate = _text(review.get("candidate_event_type"))
    if not candidate:
        return _result(status=STATUS_INVALID, issues=["review.candidate_event_type is required"], warnings=warnings)

    lifecycle_event = CANDIDATE_TO_LIFECYCLE_EVENT.get(candidate)
    if not lifecycle_event:
        return _result(status=STATUS_INVALID, issues=["candidate_event_type cannot be mapped"], warnings=warnings)

    allowed_events = policy.get("allowed_lifecycle_events")
    if allowed_events is not None and lifecycle_event not in set(str(item) for item in allowed_events):
        return _result(status=STATUS_BLOCKED, issues=["candidate lifecycle event is not allowed"], warnings=warnings)

    evidence_id = _text(review.get("evidence_id"))
    if not evidence_id:
        return _result(status=STATUS_INVALID, issues=["review.evidence_id is required"], warnings=warnings)

    identity = _as_dict(review.get("identity"))
    missing_identity = _required_identity_missing(identity)
    if missing_identity:
        return _result(status=STATUS_INVALID, issues=["identity missing fields: " + ", ".join(missing_identity)], warnings=warnings)

    transition_preview = {
        "preview_type": "CHEJAN_TO_LIFECYCLE_TRANSITION_PREVIEW",
        "evidence_id": evidence_id,
        "candidate_event_type": candidate,
        "candidate_lifecycle_event": lifecycle_event,
        "identity": deepcopy(identity),
        "confidence": review.get("confidence"),
        "unknown_event": lifecycle_event == "UNKNOWN_EVENT",
        "raw_fields": deepcopy(_as_dict(review.get("raw_fields"))),
        "lifecycle_policy": deepcopy(policy),
        "current_lifecycle_snapshot": deepcopy(snapshot),
        "lifecycle_created": False,
        "final_state_confirmed": False,
        "runtime_write": False,
        "queue_write": False,
        "position_update_called": False,
        "balance_update_called": False,
        "auto_retry_called": False,
        "next_stage": "ORDER_LIFECYCLE_TRANSITION_REVIEW_REQUIRED",
    }
    return _result(
        status=STATUS_READY,
        transition_preview=transition_preview,
        candidate_lifecycle_event=lifecycle_event,
        issues=[],
        warnings=warnings,
    )
