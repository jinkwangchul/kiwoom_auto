# -*- coding: utf-8 -*-
"""Build evidence for a Chejan event candidate without creating lifecycle state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


STATUS_READY = "EVIDENCE_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _result(
    *,
    status: str,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "evidence": deepcopy(evidence) if isinstance(evidence, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _context_valid(context: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ctx = _as_dict(context)
    if not ctx:
        return ctx, _result(status=STATUS_INVALID, issues=["evidence_context must be a non-empty dict"])
    if ctx.get("evidence_enabled") is not True:
        return ctx, _result(status=STATUS_BLOCKED, issues=["evidence_context.evidence_enabled is not true"])
    return ctx, None


def build_chejan_event_evidence(
    classification_preview_result: Any,
    evidence_context: Any,
) -> dict[str, Any]:
    """Build an evidence contract for a candidate Chejan event only."""
    classification_result = _as_dict(classification_preview_result)
    if not classification_result:
        return _result(status=STATUS_INVALID, issues=["classification_preview_result must be a dict"])

    context, context_blocked = _context_valid(evidence_context)
    status = _text(classification_result.get("status")).upper()
    warnings = list(classification_result.get("warnings") or [])

    if status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["classification_preview_result.status is BLOCKED"] + list(classification_result.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["classification_preview_result.status is INVALID"] + list(classification_result.get("issues") or []),
            warnings=warnings,
        )
    if status != "CLASSIFICATION_READY":
        return _result(status=STATUS_INVALID, issues=["classification_preview_result.status is not supported"], warnings=warnings)

    if context_blocked is not None:
        return context_blocked

    candidate = _text(classification_result.get("candidate_event_type"))
    if not candidate:
        return _result(status=STATUS_INVALID, issues=["candidate_event_type is required"], warnings=warnings)

    preview = _as_dict(classification_result.get("classification_preview"))
    if not preview:
        return _result(status=STATUS_INVALID, issues=["classification_preview is required"], warnings=warnings)

    identity = _as_dict(preview.get("identity"))
    if not identity:
        return _result(status=STATUS_INVALID, issues=["classification_preview.identity is required"], warnings=warnings)
    missing_identity = [field for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id") if not _text(identity.get(field))]
    if missing_identity:
        return _result(status=STATUS_INVALID, issues=["identity missing fields: " + ", ".join(missing_identity)], warnings=warnings)

    evidence_id = _text(context.get("evidence_id")) or f"CHEJAN_EVIDENCE_{uuid4().hex}"
    raw_fields = {
        "source_event_type": preview.get("source_event_type"),
        "raw_order_status": preview.get("raw_order_status"),
        "raw_filled_quantity": preview.get("raw_filled_quantity"),
        "raw_remaining_quantity": preview.get("raw_remaining_quantity"),
    }

    evidence = {
        "evidence_type": "CHEJAN_EVENT_EVIDENCE",
        "evidence_id": evidence_id,
        "candidate_event_type": candidate,
        "confidence": _text(classification_result.get("confidence")) or _text(preview.get("confidence")),
        "identity": deepcopy(identity),
        "raw_fields": deepcopy(raw_fields),
        "classification_preview": deepcopy(preview),
        "evidence_context": deepcopy(context),
        "created_at": _now_text(),
        "final_state_confirmed": False,
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
        "position_update_called": False,
        "balance_update_called": False,
        "auto_retry_called": False,
        "next_stage": "CHEJAN_EVENT_EVIDENCE_REVIEW_REQUIRED",
    }
    return _result(status=STATUS_READY, evidence=evidence, issues=[], warnings=warnings)
