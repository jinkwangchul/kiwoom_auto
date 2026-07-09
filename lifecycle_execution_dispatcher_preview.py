# -*- coding: utf-8 -*-
"""Preview-only execution dispatcher preview.

This module converts an Execution Final Approval Preview into the
preview-only Execution Dispatcher Preview. It is produced BEFORE any real
dispatch is executed, dispatch is allowed, Execution is allowed, SendOrder is
called, order results are recorded, recorder is called, or Chejan is connected.

It never performs real dispatch, allows dispatch, allows Execution, calls
SendOrder, records order results, calls a recorder, connects Chejan, writes
runtime files, modifies routines/*/rules.json, writes SQLite, updates GUI
state, or commits Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_DISPATCHER_PREVIEW"
STATUS_READY = "EXECUTION_DISPATCHER_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
FINAL_APPROVAL_STATUS_READY = "EXECUTION_FINAL_APPROVAL_PREVIEW_READY"


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
    "dispatch_allowed",
    "dispatch_started",
    "dispatch_completed",
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


def _build_dispatch_candidate_preview(
    final_approval_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only dispatch candidate preview."""
    final_approval_decision = _as_dict(final_approval_preview.get("final_approval_decision"))
    final_result_review_decision = _as_dict(
        final_approval_preview.get("final_result_review_decision")
    )

    candidates = _as_list(context.get("dispatch_candidates")) or [
        {
            "candidate_index": 1,
            "candidate_id": "DISPATCH_CANDIDATE_{}".format(uuid4().hex),
            "candidate_source": "EXECUTION_FINAL_APPROVAL_PREVIEW",
            "candidate_ready": final_approval_decision.get("approved") is True,
            "candidate_blocked": final_approval_decision.get("approved") is not True,
            "preview_only": True,
        }
    ]

    approved = final_approval_decision.get("approved") is True

    return {
        "candidates": list(candidates),
        "total_candidates": len(candidates),
        "dispatch_candidate_ready": approved,
        "dispatch_candidate_blocked": not approved,
        "approval_reference": final_approval_decision.get("approval_reason") or "",
        "review_reference": final_result_review_decision.get("approval_reason") or "",
        "preview_only": True,
    }


def _build_dispatch_route_preview(
    final_approval_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only dispatch route preview."""
    route_target = _text(context.get("dispatch_route_target")) or "PREVIEW_ONLY_DISPATCH_TARGET"

    return {
        "route_ready": True,
        "route_target": route_target,
        "route_strategy": _text(context.get("dispatch_route_strategy"))
        or "PREVIEW_ONLY_ROUTE_STRATEGY",
        "route_blocked": False,
        "route_reason": "preview-only dispatch route",
        "preview_only": True,
    }


def _build_dispatch_queue_preview(
    final_approval_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only dispatch queue preview."""
    queue_name = _text(context.get("dispatch_queue_name")) or "PREVIEW_ONLY_DISPATCH_QUEUE"
    queue_position = context.get("dispatch_queue_position")
    if not isinstance(queue_position, int):
        queue_position = 0

    return {
        "queue_ready": True,
        "queue_name": queue_name,
        "queue_position": queue_position,
        "queue_size": context.get("dispatch_queue_size")
        if isinstance(context.get("dispatch_queue_size"), int)
        else 0,
        "queue_enqueued": False,
        "queue_started": False,
        "queue_reason": "preview-only dispatch queue",
        "preview_only": True,
    }


def _build_dispatch_safety_validation(
    final_approval_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate dispatch safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if final_approval_preview.get(flag) is True:
            issues.append("final approval preview {} must be false".format(flag))

    if final_approval_preview.get("preview_only") is not True:
        issues.append("final approval preview preview_only must be true")

    if not _as_dict(final_approval_preview.get("final_approval_decision")):
        issues.append("final approval preview final_approval_decision is required")

    final_approval_decision = _as_dict(final_approval_preview.get("final_approval_decision"))
    if final_approval_decision.get("approved") is not True:
        issues.append("final approval preview final_approval_decision.approved must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_dispatch_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final dispatch decision."""
    dispatched = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "dispatched": dispatched,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not dispatched
        else "",
        "dispatch_reason": "dispatch safety validation ready" if dispatched else "",
        "dispatch_allowed": False,
        "dispatch_started": False,
        "dispatch_completed": False,
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


def _validate_final_approval_preview(
    final_approval_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the final approval preview."""
    if not final_approval_preview:
        return STATUS_INVALID, ["final_approval_preview must be a dict"]

    status = _text(final_approval_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["final approval preview is BLOCKED"] + list(
            final_approval_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["final approval preview is INVALID"] + list(
            final_approval_preview.get("issues") or []
        )
    if status != FINAL_APPROVAL_STATUS_READY:
        return STATUS_INVALID, [
            "final approval preview status is not EXECUTION_FINAL_APPROVAL_PREVIEW_READY"
        ]

    if final_approval_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["final approval preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if final_approval_preview.get(flag) is True:
            return STATUS_INVALID, ["final approval preview {} must be false".format(flag)]

    if not _as_dict(final_approval_preview.get("final_approval_decision")):
        return STATUS_INVALID, ["final approval preview final_approval_decision is required"]

    final_approval_decision = _as_dict(final_approval_preview.get("final_approval_decision"))
    if final_approval_decision.get("approved") is not True:
        return STATUS_INVALID, [
            "final approval preview final_approval_decision.approved must be true"
        ]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    dispatch_candidate_preview: dict[str, Any] | None = None,
    dispatch_route_preview: dict[str, Any] | None = None,
    dispatch_queue_preview: dict[str, Any] | None = None,
    dispatch_safety_validation: dict[str, Any] | None = None,
    final_dispatch_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "dispatch_allowed": False,
        "dispatch_started": False,
        "dispatch_completed": False,
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
        "dispatch_candidate_preview": deepcopy(dispatch_candidate_preview or {}),
        "dispatch_route_preview": deepcopy(dispatch_route_preview or {}),
        "dispatch_queue_preview": deepcopy(dispatch_queue_preview or {}),
        "dispatch_safety_validation": deepcopy(dispatch_safety_validation or {}),
        "final_dispatch_decision": deepcopy(final_dispatch_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_dispatcher_preview(
    final_approval_preview: Any,
    dispatcher_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only Execution Dispatcher Preview from a final approval preview.

    The pipeline is:
    1. EXECUTION_FINAL_APPROVAL_PREVIEW_READY (from final approval preview)
    2. EXECUTION_DISPATCHER_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(final_approval_preview))
    context = deepcopy(_as_dict(dispatcher_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the final approval preview
    status, issues = _validate_final_approval_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_dispatch_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            dispatch_safety_validation=validation,
            final_dispatch_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    dispatch_candidate_preview = _build_dispatch_candidate_preview(preview, context)
    dispatch_route_preview = _build_dispatch_route_preview(preview, context)
    dispatch_queue_preview = _build_dispatch_queue_preview(preview, context)
    safety_validation = _build_dispatch_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_dispatch_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        dispatch_candidate_preview=dispatch_candidate_preview,
        dispatch_route_preview=dispatch_route_preview,
        dispatch_queue_preview=dispatch_queue_preview,
        dispatch_safety_validation=safety_validation,
        final_dispatch_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
