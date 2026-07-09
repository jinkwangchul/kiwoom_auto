# -*- coding: utf-8 -*-
"""Preview-only execution sendorder result review preview.

This module converts a SendOrder Call Preview result into the
preview-only SendOrder Result Review Preview. It is produced BEFORE any real
order result is recorded, Chejan is connected, or recorder is called.
It never records order results, connects Chejan, calls a recorder,
writes runtime files, modifies routines/*/rules.json, writes SQLite,
updates GUI state, or commits Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_SENDORDER_RESULT_REVIEW_PREVIEW"
STATUS_READY = "SENDORDER_RESULT_REVIEW_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
SENDORDER_CALL_STATUS_READY = "SENDORDER_CALL_PREVIEW_READY"


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
    "send_order_called",
    "send_order_result_recorded",
    "recorder_called",
    "chejan_called",
    "execution_completed",
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "gui_update_called",
    "backup_created",
    "rollback_executed",
)


def _build_sendorder_result_review_preview(
    sendorder_call_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only sendorder result review preview."""
    sendorder_call_preview_data = _as_dict(sendorder_call_preview.get("sendorder_call_preview"))
    sendorder_parameter_preview = _as_dict(sendorder_call_preview.get("sendorder_parameter_preview"))
    result_candidate_preview = _as_dict(sendorder_call_preview.get("sendorder_result_candidate_preview"))

    review_id = _text(context.get("review_id")) or "SENDORDER_RESULT_REVIEW_{}".format(uuid4().hex)
    call_id = _text(sendorder_call_preview_data.get("call_id")) or ""
    sendorder_id = _text(sendorder_call_preview_data.get("sendorder_id")) or ""
    account = _text(sendorder_call_preview_data.get("account")) or ""
    stock_code = _text(sendorder_call_preview_data.get("stock_code")) or ""
    order_type = _text(sendorder_call_preview_data.get("order_type")) or ""
    price = _text(sendorder_call_preview_data.get("price")) or "0"
    quantity = _text(sendorder_call_preview_data.get("quantity")) or "0"

    return {
        "review_id": review_id,
        "call_id": call_id,
        "sendorder_id": sendorder_id,
        "broker_adapter_name": _text(sendorder_call_preview_data.get("broker_adapter_name")) or "BROKER_ADAPTER",
        "broker_adapter_version": _text(sendorder_call_preview_data.get("broker_adapter_version")) or "v1",
        "account": account,
        "stock_code": stock_code,
        "order_type": order_type,
        "price": price,
        "quantity": quantity,
        "review_planned": True,
        "review_completed": False,
        "preview_only": True,
    }


def _build_result_classification_preview(
    sendorder_call_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only result classification preview."""
    result_candidate_preview = _as_dict(sendorder_call_preview.get("sendorder_result_candidate_preview"))
    candidates = _as_list(result_candidate_preview.get("candidates"))

    classification_id = _text(context.get("classification_id")) or "RESULT_CLASSIFICATION_{}".format(uuid4().hex)

    classified_candidates = []
    for candidate in candidates:
        classified_candidates.append({
            "candidate_index": candidate.get("candidate_index"),
            "result_name": _text(candidate.get("result_name")),
            "result_status": _text(candidate.get("result_status")),
            "order_accepted": candidate.get("order_accepted", False),
            "order_rejected": candidate.get("order_rejected", False),
            "error_code": _text(candidate.get("error_code")),
            "error_message": _text(candidate.get("error_message")),
            "classification": "SUCCESS" if candidate.get("order_accepted") else "FAILURE",
            "preview_only": True,
        })

    return {
        "classification_id": classification_id,
        "classifications": classified_candidates,
        "selected_classification": "",
        "classification_selected": False,
        "preview_only": True,
    }


def _build_recorder_handoff_preview(
    sendorder_call_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only recorder handoff preview."""
    final_call_decision = _as_dict(sendorder_call_preview.get("final_call_decision"))
    result_classification_preview = _as_dict(context.get("result_classification_preview"))

    handoff_id = _text(context.get("handoff_id")) or "RECORDER_HANDOFF_{}".format(uuid4().hex)

    return {
        "handoff_id": handoff_id,
        "handoff_required": True,
        "handoff_completed": False,
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "recorder_handoff_reason": "preview-only result review handoff",
        "preview_only": True,
    }


def _build_failure_handling_preview(
    sendorder_call_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only failure handling preview."""
    result_candidate_preview = _as_dict(sendorder_call_preview.get("sendorder_result_candidate_preview"))
    candidates = _as_list(result_candidate_preview.get("candidates"))

    failure_candidates = [
        candidate for candidate in candidates
        if candidate.get("order_rejected") or candidate.get("error_code")
    ]

    handling_id = _text(context.get("handling_id")) or "FAILURE_HANDLING_{}".format(uuid4().hex)

    steps = [
        {
            "step_index": 1,
            "step_name": "detect_failure",
            "step_description": "Detect order failure from result candidates",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
        {
            "step_index": 2,
            "step_name": "classify_failure",
            "step_description": "Classify failure type (rejected/failed/timeout)",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
        {
            "step_index": 3,
            "step_name": "prepare_retry_or_rollback",
            "step_description": "Prepare retry or rollback plan (preview only)",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
    ]

    return {
        "handling_id": handling_id,
        "failure_candidates": failure_candidates,
        "handling_steps": steps,
        "total_steps": len(steps),
        "handling_required": len(failure_candidates) > 0,
        "handling_completed": False,
        "retry_planned": False,
        "rollback_planned": False,
        "preview_only": True,
    }


def _build_result_safety_validation(
    sendorder_call_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate SendOrder result review safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if sendorder_call_preview.get(flag) is True:
            issues.append("sendorder call preview {} must be false".format(flag))

    if sendorder_call_preview.get("preview_only") is not True:
        issues.append("sendorder call preview preview_only must be true")

    final_call_decision = _as_dict(sendorder_call_preview.get("final_call_decision"))
    if final_call_decision.get("approved") is not True:
        issues.append("sendorder call preview final_call_decision.approved must be true")

    if not _as_dict(sendorder_call_preview.get("sendorder_call_preview")):
        issues.append("sendorder call preview sendorder_call_preview is required")

    if not _as_dict(sendorder_call_preview.get("sendorder_parameter_preview")):
        issues.append("sendorder call preview sendorder_parameter_preview is required")

    if not _as_dict(sendorder_call_preview.get("sendorder_result_candidate_preview")):
        issues.append("sendorder call preview sendorder_result_candidate_preview is required")

    if not _as_dict(sendorder_call_preview.get("sendorder_call_sequence_preview")):
        issues.append("sendorder call preview sendorder_call_sequence_preview is required")

    call_safety_validation = _as_dict(sendorder_call_preview.get("call_safety_validation"))
    if call_safety_validation.get("ready") is not True:
        issues.append("sendorder call preview call_safety_validation.ready must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_result_review_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final result review decision."""
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not approved
        else "",
        "approval_reason": "sendorder result review safety validation ready" if approved else "",
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "execution_completed": False,
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


def _validate_sendorder_call_preview(
    sendorder_call_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the sendorder call preview."""
    if not sendorder_call_preview:
        return STATUS_INVALID, ["sendorder_call_preview must be a dict"]

    status = _text(sendorder_call_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["sendorder call preview is BLOCKED"] + list(
            sendorder_call_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["sendorder call preview is INVALID"] + list(
            sendorder_call_preview.get("issues") or []
        )
    if status != SENDORDER_CALL_STATUS_READY:
        return STATUS_INVALID, [
            "sendorder call preview status is not SENDORDER_CALL_PREVIEW_READY"
        ]

    if sendorder_call_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["sendorder call preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if sendorder_call_preview.get(flag) is True:
            return STATUS_INVALID, ["sendorder call preview {} must be false".format(flag)]

    final_call_decision = _as_dict(sendorder_call_preview.get("final_call_decision"))
    if final_call_decision.get("approved") is not True:
        return STATUS_INVALID, ["sendorder call preview final_call_decision.approved must be true"]

    if not _as_dict(sendorder_call_preview.get("sendorder_call_preview")):
        return STATUS_INVALID, ["sendorder call preview sendorder_call_preview is required"]

    if not _as_dict(sendorder_call_preview.get("sendorder_parameter_preview")):
        return STATUS_INVALID, ["sendorder call preview sendorder_parameter_preview is required"]

    if not _as_dict(sendorder_call_preview.get("sendorder_result_candidate_preview")):
        return STATUS_INVALID, ["sendorder call preview sendorder_result_candidate_preview is required"]

    if not _as_dict(sendorder_call_preview.get("sendorder_call_sequence_preview")):
        return STATUS_INVALID, ["sendorder call preview sendorder_call_sequence_preview is required"]

    call_safety_validation = _as_dict(sendorder_call_preview.get("call_safety_validation"))
    if call_safety_validation.get("ready") is not True:
        return STATUS_INVALID, ["sendorder call preview call_safety_validation.ready must be true"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    sendorder_result_review_preview: dict[str, Any] | None = None,
    result_classification_preview: dict[str, Any] | None = None,
    recorder_handoff_preview: dict[str, Any] | None = None,
    failure_handling_preview: dict[str, Any] | None = None,
    result_safety_validation: dict[str, Any] | None = None,
    final_result_review_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "send_order_called": False,
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "execution_completed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "gui_update_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "sendorder_result_review_preview": deepcopy(sendorder_result_review_preview or {}),
        "result_classification_preview": deepcopy(result_classification_preview or {}),
        "recorder_handoff_preview": deepcopy(recorder_handoff_preview or {}),
        "failure_handling_preview": deepcopy(failure_handling_preview or {}),
        "result_safety_validation": deepcopy(result_safety_validation or {}),
        "final_result_review_decision": deepcopy(final_result_review_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_sendorder_result_review_preview(
    sendorder_call_preview: Any,
    review_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only SendOrder result review from a sendorder call preview.

    The pipeline is:
    1. SENDORDER_CALL_PREVIEW_READY (from sendorder call preview)
    2. SENDORDER_RESULT_REVIEW_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(sendorder_call_preview))
    context = deepcopy(_as_dict(review_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the sendorder call preview
    status, issues = _validate_sendorder_call_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_result_review_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            result_safety_validation=validation,
            final_result_review_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    sendorder_result_review_preview = _build_sendorder_result_review_preview(preview, context)
    result_classification_preview = _build_result_classification_preview(preview, context)
    recorder_handoff_preview = _build_recorder_handoff_preview(preview, context)
    failure_handling_preview = _build_failure_handling_preview(preview, context)
    safety_validation = _build_result_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_result_review_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        sendorder_result_review_preview=sendorder_result_review_preview,
        result_classification_preview=result_classification_preview,
        recorder_handoff_preview=recorder_handoff_preview,
        failure_handling_preview=failure_handling_preview,
        result_safety_validation=safety_validation,
        final_result_review_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
