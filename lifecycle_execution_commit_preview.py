# -*- coding: utf-8 -*-
"""Preview-only execution commit preview.

This module converts an Execution Dispatcher Preview into the preview-only
Execution Commit Preview. It is produced BEFORE any real commit is executed,
dispatch is executed, Execution is allowed, SendOrder is called, order results
are recorded, recorder is called, or Chejan is connected.

It never performs real commit, real dispatch, allows Execution, calls SendOrder,
records order results, calls a recorder, connects Chejan, writes runtime files,
modifies routines/*/rules.json, writes SQLite, updates GUI state, or commits
Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_COMMIT_PREVIEW"
STATUS_READY = "EXECUTION_COMMIT_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
DISPATCHER_STATUS_READY = "EXECUTION_DISPATCHER_PREVIEW_READY"


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
    "execution_commit_allowed",
    "execution_commit_started",
    "execution_commit_completed",
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


def _build_execution_commit_candidate_preview(
    dispatcher_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only execution commit candidate preview."""
    final_dispatch_decision = _as_dict(dispatcher_preview.get("final_dispatch_decision"))

    candidates = _as_list(context.get("execution_commit_candidates")) or [
        {
            "candidate_index": 1,
            "candidate_id": "EXECUTION_COMMIT_CANDIDATE_{}".format(uuid4().hex),
            "candidate_source": "EXECUTION_DISPATCHER_PREVIEW",
            "candidate_ready": final_dispatch_decision.get("dispatched") is True,
            "candidate_blocked": final_dispatch_decision.get("dispatched") is not True,
            "preview_only": True,
        }
    ]

    ready = final_dispatch_decision.get("dispatched") is True

    return {
        "candidates": list(candidates),
        "total_candidates": len(candidates),
        "execution_commit_candidate_ready": ready,
        "execution_commit_candidate_blocked": not ready,
        "dispatch_reference": final_dispatch_decision.get("dispatch_reason") or "",
        "preview_only": True,
    }


def _build_execution_commit_route_preview(
    dispatcher_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only execution commit route preview."""
    route_target = _text(context.get("execution_commit_route_target")) or "PREVIEW_ONLY_COMMIT_TARGET"

    return {
        "route_ready": True,
        "route_target": route_target,
        "route_strategy": _text(context.get("execution_commit_route_strategy"))
        or "PREVIEW_ONLY_COMMIT_STRATEGY",
        "route_blocked": False,
        "route_reason": "preview-only execution commit route",
        "preview_only": True,
    }


def _build_execution_commit_queue_preview(
    dispatcher_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only execution commit queue preview."""
    queue_name = _text(context.get("execution_commit_queue_name")) or "PREVIEW_ONLY_COMMIT_QUEUE"
    queue_position = context.get("execution_commit_queue_position")
    if not isinstance(queue_position, int):
        queue_position = 0

    return {
        "queue_ready": True,
        "queue_name": queue_name,
        "queue_position": queue_position,
        "queue_size": context.get("execution_commit_queue_size")
        if isinstance(context.get("execution_commit_queue_size"), int)
        else 0,
        "queue_enqueued": False,
        "queue_started": False,
        "queue_reason": "preview-only execution commit queue",
        "preview_only": True,
    }


def _build_post_commit_verification_preview(
    dispatcher_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only post-commit verification plan."""
    verification_items = _as_list(context.get("post_commit_verification_items")) or [
        {
            "verification_index": 1,
            "verification_name": "dispatch_preview_ready",
            "verification_description": "Confirm dispatcher preview is ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
        {
            "verification_index": 2,
            "verification_name": "commit_safety_validation_ready",
            "verification_description": "Confirm commit safety validation is ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
        {
            "verification_index": 3,
            "verification_name": "execution_blocking_ready",
            "verification_description": "Confirm execution blocking preview is ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
    ]

    return {
        "post_commit_verification_required": True,
        "post_commit_verification_completed": False,
        "verification_items": list(verification_items),
        "total_items": len(verification_items),
        "verification_reason": "preview-only post-commit verification plan",
        "preview_only": True,
    }


def _build_commit_safety_validation(
    dispatcher_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate commit safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if dispatcher_preview.get(flag) is True:
            issues.append("dispatcher preview {} must be false".format(flag))

    if dispatcher_preview.get("preview_only") is not True:
        issues.append("dispatcher preview preview_only must be true")

    if not _as_dict(dispatcher_preview.get("final_dispatch_decision")):
        issues.append("dispatcher preview final_dispatch_decision is required")

    final_dispatch_decision = _as_dict(dispatcher_preview.get("final_dispatch_decision"))
    if final_dispatch_decision.get("dispatched") is not True:
        issues.append("dispatcher preview final_dispatch_decision.dispatched must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_commit_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final commit decision."""
    committed = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "committed": committed,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not committed
        else "",
        "commit_reason": "commit safety validation ready" if committed else "",
        "execution_commit_allowed": False,
        "execution_commit_started": False,
        "execution_commit_completed": False,
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


def _validate_dispatcher_preview(
    dispatcher_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the dispatcher preview."""
    if not dispatcher_preview:
        return STATUS_INVALID, ["dispatcher_preview must be a dict"]

    status = _text(dispatcher_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["dispatcher preview is BLOCKED"] + list(
            dispatcher_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["dispatcher preview is INVALID"] + list(
            dispatcher_preview.get("issues") or []
        )
    if status != DISPATCHER_STATUS_READY:
        return STATUS_INVALID, [
            "dispatcher preview status is not EXECUTION_DISPATCHER_PREVIEW_READY"
        ]

    if dispatcher_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["dispatcher preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if dispatcher_preview.get(flag) is True:
            return STATUS_INVALID, ["dispatcher preview {} must be false".format(flag)]

    if not _as_dict(dispatcher_preview.get("final_dispatch_decision")):
        return STATUS_INVALID, ["dispatcher preview final_dispatch_decision is required"]

    final_dispatch_decision = _as_dict(dispatcher_preview.get("final_dispatch_decision"))
    if final_dispatch_decision.get("dispatched") is not True:
        return STATUS_INVALID, [
            "dispatcher preview final_dispatch_decision.dispatched must be true"
        ]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    execution_commit_candidate_preview: dict[str, Any] | None = None,
    execution_commit_route_preview: dict[str, Any] | None = None,
    execution_commit_queue_preview: dict[str, Any] | None = None,
    post_commit_verification_preview: dict[str, Any] | None = None,
    commit_safety_validation: dict[str, Any] | None = None,
    final_commit_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "execution_commit_allowed": False,
        "execution_commit_started": False,
        "execution_commit_completed": False,
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
        "execution_commit_candidate_preview": deepcopy(execution_commit_candidate_preview or {}),
        "execution_commit_route_preview": deepcopy(execution_commit_route_preview or {}),
        "execution_commit_queue_preview": deepcopy(execution_commit_queue_preview or {}),
        "post_commit_verification_preview": deepcopy(post_commit_verification_preview or {}),
        "commit_safety_validation": deepcopy(commit_safety_validation or {}),
        "final_commit_decision": deepcopy(final_commit_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_commit_preview(
    dispatcher_preview: Any,
    commit_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only Execution Commit Preview from a dispatcher preview.

    The pipeline is:
    1. EXECUTION_DISPATCHER_PREVIEW_READY (from dispatcher preview)
    2. EXECUTION_COMMIT_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(dispatcher_preview))
    context = deepcopy(_as_dict(commit_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the dispatcher preview
    status, issues = _validate_dispatcher_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_commit_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            commit_safety_validation=validation,
            final_commit_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    commit_candidate_preview = _build_execution_commit_candidate_preview(preview, context)
    commit_route_preview = _build_execution_commit_route_preview(preview, context)
    commit_queue_preview = _build_execution_commit_queue_preview(preview, context)
    post_commit_verification_preview = _build_post_commit_verification_preview(preview, context)
    safety_validation = _build_commit_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_commit_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        execution_commit_candidate_preview=commit_candidate_preview,
        execution_commit_route_preview=commit_route_preview,
        execution_commit_queue_preview=commit_queue_preview,
        post_commit_verification_preview=post_commit_verification_preview,
        commit_safety_validation=safety_validation,
        final_commit_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
