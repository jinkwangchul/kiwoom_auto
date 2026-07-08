# -*- coding: utf-8 -*-
"""Review Chejan event evidence without creating lifecycle state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_OK = "EVIDENCE_REVIEW_OK"
STATUS_BLOCKED = "EVIDENCE_REVIEW_BLOCKED"
STATUS_INVALID = "INVALID"

VALID_CANDIDATES = {
    "ORDER_RECEIVED_CANDIDATE",
    "ORDER_REJECTED_CANDIDATE",
    "ORDER_CANCELLED_CANDIDATE",
    "PARTIAL_FILL_CANDIDATE",
    "FULL_FILL_CANDIDATE",
    "UNKNOWN_CANDIDATE",
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
    review: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    lifecycle_ready: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "review": deepcopy(review) if isinstance(review, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "lifecycle_ready": bool(lifecycle_ready),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _context_valid(context: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ctx = _as_dict(context)
    if not ctx:
        return ctx, _result(status=STATUS_INVALID, issues=["review_context must be a non-empty dict"])
    if ctx.get("lifecycle_review_enabled") is not True:
        return ctx, _result(status=STATUS_BLOCKED, issues=["review_context.lifecycle_review_enabled is not true"])
    return ctx, None


def _identity_mismatch(identity: dict[str, Any], expected: dict[str, Any]) -> str | None:
    for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id"):
        value = _text(identity.get(field))
        if not value:
            return f"identity.{field} is required"
        expected_value = _text(expected.get(field))
        if expected_value and expected_value != value:
            return f"identity.{field} does not match review_context"
    return None


def review_chejan_event_evidence(
    evidence_result: Any,
    review_context: Any,
) -> dict[str, Any]:
    """Review an evidence contract as a lifecycle candidate only."""
    evidence_payload = _as_dict(evidence_result)
    if not evidence_payload:
        return _result(status=STATUS_INVALID, issues=["evidence_result must be a dict"])

    context, context_blocked = _context_valid(review_context)
    status = _text(evidence_payload.get("status")).upper()
    warnings = list(evidence_payload.get("warnings") or [])

    if status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["evidence_result.status is BLOCKED"] + list(evidence_payload.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["evidence_result.status is INVALID"] + list(evidence_payload.get("issues") or []),
            warnings=warnings,
        )
    if status != "EVIDENCE_READY":
        return _result(status=STATUS_INVALID, issues=["evidence_result.status is not supported"], warnings=warnings)

    if context_blocked is not None:
        return context_blocked

    evidence = _as_dict(evidence_payload.get("evidence"))
    if not evidence:
        return _result(status=STATUS_INVALID, issues=["evidence is required"], warnings=warnings)

    evidence_id = _text(evidence.get("evidence_id"))
    if not evidence_id:
        return _result(status=STATUS_INVALID, issues=["evidence.evidence_id is required"], warnings=warnings)
    candidate = _text(evidence.get("candidate_event_type"))
    if not candidate:
        return _result(status=STATUS_INVALID, issues=["evidence.candidate_event_type is required"], warnings=warnings)
    if candidate not in VALID_CANDIDATES:
        return _result(status=STATUS_INVALID, issues=["evidence.candidate_event_type is not supported"], warnings=warnings)
    if evidence.get("lifecycle_created") is not False or evidence.get("final_state_confirmed") is not False:
        return _result(status=STATUS_INVALID, issues=["evidence must not confirm lifecycle state"], warnings=warnings)

    identity = _as_dict(evidence.get("identity"))
    if not identity:
        return _result(status=STATUS_INVALID, issues=["evidence.identity is required"], warnings=warnings)
    mismatch = _identity_mismatch(identity, _as_dict(context.get("expected_identity")))
    if mismatch is not None:
        return _result(status=STATUS_INVALID, issues=[mismatch], warnings=warnings)

    review = {
        "review_type": "CHEJAN_EVENT_EVIDENCE_REVIEW",
        "review_stage": "chejan_event_evidence_reviewed",
        "evidence_id": evidence_id,
        "candidate_event_type": candidate,
        "confidence": evidence.get("confidence"),
        "identity": deepcopy(identity),
        "raw_fields": deepcopy(_as_dict(evidence.get("raw_fields"))),
        "unknown_candidate": candidate == "UNKNOWN_CANDIDATE",
        "final_state_confirmed": False,
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
        "next_stage": "ORDER_LIFECYCLE_CANDIDATE_REVIEW_REQUIRED",
    }
    return _result(status=STATUS_OK, review=review, issues=[], warnings=warnings, lifecycle_ready=True)
