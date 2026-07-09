# -*- coding: utf-8 -*-
"""Preview-only execution final approval preview.

This module converts a SendOrder Result Review Preview into the
preview-only Execution Final Approval Preview. It is produced BEFORE any real
approval processing, Execution is allowed, SendOrder is called, order results
are recorded, recorder is called, or Chejan is connected.

It never performs real approval, allows Execution, calls SendOrder, records
order results, calls a recorder, connects Chejan, writes runtime files,
modifies routines/*/rules.json, writes SQLite, updates GUI state, or commits
Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_FINAL_APPROVAL_PREVIEW"
STATUS_READY = "EXECUTION_FINAL_APPROVAL_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
RESULT_REVIEW_STATUS_READY = "SENDORDER_RESULT_REVIEW_PREVIEW_READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _is_ready(status: str) -> bool:
    return status == STATUS_READY


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


SAFETY_FLAGS = (
    "approval_granted",
    "execution_allowed",
    "execution_started",
    "execution_completed",
    "send_order_called",
    "send_order_result_recorded",
    "recorder_called",
    "chejan_called",
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "gui_update_called",
    "backup_created",
    "rollback_executed",
)


def _build_approval_requirement_preview(
    result_review_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only approval requirement preview."""
    approval_source = _text(context.get("approval_source")) or "PREVIEW_ONLY_APPROVAL_SOURCE"

    return {
        "approval_required": True,
        "approval_granted": False,
        "approval_source": approval_source,
        "approval_reason": "preview-only final approval requirement",
        "preview_only": True,
    }


def _build_operator_review_preview(
    result_review_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only operator review preview."""
    review_items = _as_list(context.get("review_items")) or [
        {
            "item_index": 1,
            "item_name": "result_review_preview_ready",
            "item_description": "Confirm sendorder result review preview is ready",
            "item_required": True,
            "item_completed": False,
            "preview_only": True,
        },
        {
            "item_index": 2,
            "item_name": "approval_requirement_ready",
            "item_description": "Confirm approval requirement preview is ready",
            "item_required": True,
            "item_completed": False,
            "preview_only": True,
        },
        {
            "item_index": 3,
            "item_name": "execution_blocking_ready",
            "item_description": "Confirm execution blocking preview is ready",
            "item_required": True,
            "item_completed": False,
            "preview_only": True,
        },
    ]

    return {
        "operator_review_required": True,
        "operator_review_completed": False,
        "review_items": list(review_items),
        "total_items": len(review_items),
        "review_reason": "preview-only operator review requirement",
        "preview_only": True,
    }


def _build_execution_blocking_preview(
    result_review_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only execution blocking preview."""
    blocking_reasons = _as_list(context.get("blocking_reasons")) or [
        "preview-only execution is blocked until real approval is granted",
    ]

    return {
        "blocking_reasons": list(blocking_reasons),
        "execution_blocked": True,
        "block_reason": "preview-only execution blocked",
        "preview_only": True,
    }


def _build_approval_token_preview(
    result_review_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only approval token preview."""
    token_id = _text(context.get("token_id")) or "APPROVAL_TOKEN_{}".format(uuid4().hex)

    return {
        "token_id": token_id,
        "token_required": True,
        "token_issued": False,
        "token_consumed": False,
        "token_reason": "preview-only approval token requirement",
        "preview_only": True,
    }


def _build_approval_safety_validation(
    result_review_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate final approval safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if result_review_preview.get(flag) is True:
            issues.append("result review preview {} must be false".format(flag))

    if result_review_preview.get("preview_only") is not True:
        issues.append("result review preview preview_only must be true")

    if not _as_dict(result_review_preview.get("sendorder_result_review_preview")):
        issues.append("result review preview sendorder_result_review_preview is required")

    if not _as_dict(result_review_preview.get("result_safety_validation")):
        issues.append("result review preview result_safety_validation is required")

    if not _as_dict(result_review_preview.get("final_result_review_decision")):
        issues.append("result review preview final_result_review_decision is required")

    result_safety_validation = _as_dict(result_review_preview.get("result_safety_validation"))
    if result_safety_validation.get("ready") is not True:
        issues.append("result review preview result_safety_validation.ready must be true")

    final_result_review_decision = _as_dict(result_review_preview.get("final_result_review_decision"))
    if final_result_review_decision.get("approved") is not True:
        issues.append("result review preview final_result_review_decision.approved must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_approval_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final approval decision."""
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not approved
        else "",
        "approval_reason": "final approval safety validation ready" if approved else "",
        "approval_granted": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "send_order_called": False,
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "gui_update_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "preview_only": True,
    }


def _validate_result_review_preview(
    result_review_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the result review preview."""
    if not result_review_preview:
        return STATUS_INVALID, ["result_review_preview must be a dict"]

    status = _text(result_review_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["result review preview is BLOCKED"] + list(
            result_review_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["result review preview is INVALID"] + list(
            result_review_preview.get("issues") or []
        )
    if status != RESULT_REVIEW_STATUS_READY:
        return STATUS_INVALID, [
            "result review preview status is not SENDORDER_RESULT_REVIEW_PREVIEW_READY"
        ]

    if result_review_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["result review preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if result_review_preview.get(flag) is True:
            return STATUS_INVALID, ["result review preview {} must be false".format(flag)]

    if not _as_dict(result_review_preview.get("sendorder_result_review_preview")):
        return STATUS_INVALID, ["result review preview sendorder_result_review_preview is required"]

    if not _as_dict(result_review_preview.get("result_safety_validation")):
        return STATUS_INVALID, ["result review preview result_safety_validation is required"]

    if not _as_dict(result_review_preview.get("final_result_review_decision")):
        return STATUS_INVALID, ["result review preview final_result_review_decision is required"]

    result_safety_validation = _as_dict(result_review_preview.get("result_safety_validation"))
    if result_safety_validation.get("ready") is not True:
        return STATUS_INVALID, ["result review preview result_safety_validation.ready must be true"]

    final_result_review_decision = _as_dict(result_review_preview.get("final_result_review_decision"))
    if final_result_review_decision.get("approved") is not True:
        return STATUS_INVALID, ["result review preview final_result_review_decision.approved must be true"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    approval_requirement_preview: dict[str, Any] | None = None,
    operator_review_preview: dict[str, Any] | None = None,
    execution_blocking_preview: dict[str, Any] | None = None,
    approval_token_preview: dict[str, Any] | None = None,
    approval_safety_validation: dict[str, Any] | None = None,
    final_approval_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "approval_granted": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "send_order_called": False,
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "gui_update_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "approval_requirement_preview": deepcopy(approval_requirement_preview or {}),
        "operator_review_preview": deepcopy(operator_review_preview or {}),
        "execution_blocking_preview": deepcopy(execution_blocking_preview or {}),
        "approval_token_preview": deepcopy(approval_token_preview or {}),
        "approval_safety_validation": deepcopy(approval_safety_validation or {}),
        "final_approval_decision": deepcopy(final_approval_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_final_approval_preview(
    result_review_preview: Any,
    approval_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only Execution Final Approval Preview from a result review preview.

    The pipeline is:
    1. SENDORDER_RESULT_REVIEW_PREVIEW_READY (from result review preview)
    2. EXECUTION_FINAL_APPROVAL_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(result_review_preview))
    context = deepcopy(_as_dict(approval_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the result review preview
    status, issues = _validate_result_review_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_approval_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            approval_safety_validation=validation,
            final_approval_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    approval_requirement_preview = _build_approval_requirement_preview(preview, context)
    operator_review_preview = _build_operator_review_preview(preview, context)
    execution_blocking_preview = _build_execution_blocking_preview(preview, context)
    approval_token_preview = _build_approval_token_preview(preview, context)
    safety_validation = _build_approval_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_approval_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        approval_requirement_preview=approval_requirement_preview,
        operator_review_preview=operator_review_preview,
        execution_blocking_preview=execution_blocking_preview,
        approval_token_preview=approval_token_preview,
        approval_safety_validation=safety_validation,
        final_approval_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
