# -*- coding: utf-8 -*-
"""Preview-only execution runtime apply preview.

This module converts an Execution Commit Preview into the preview-only
Execution Runtime Apply Preview. It is produced BEFORE any real runtime apply
is executed, runtime files are modified, position/balance/audit are written,
commit/dispatch/Execution is executed, SendOrder is called, recorder is called,
Chejan is connected, GUI is updated, routines/*/rules.json is modified, SQLite
is written, or Git is committed/pushed.

It never performs real runtime apply, modifies runtime files, writes
position/balance/audit, executes commit/dispatch/Execution, calls SendOrder,
calls a recorder, connects Chejan, updates GUI state, modifies
routines/*/rules.json, writes SQLite, or commits Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_RUNTIME_APPLY_PREVIEW"
STATUS_READY = "EXECUTION_RUNTIME_APPLY_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
COMMIT_STATUS_READY = "EXECUTION_COMMIT_PREVIEW_READY"


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
    "runtime_apply_allowed",
    "runtime_apply_started",
    "runtime_apply_completed",
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


def _build_runtime_apply_candidate_preview(
    execution_commit_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only runtime apply candidate preview."""
    final_commit_decision = _as_dict(execution_commit_preview.get("final_commit_decision"))

    candidates = _as_list(context.get("runtime_apply_candidates")) or [
        {
            "candidate_index": 1,
            "candidate_id": "RUNTIME_APPLY_CANDIDATE_{}".format(uuid4().hex),
            "candidate_source": "EXECUTION_COMMIT_PREVIEW",
            "candidate_ready": final_commit_decision.get("committed") is True,
            "candidate_blocked": final_commit_decision.get("committed") is not True,
            "preview_only": True,
        }
    ]

    ready = final_commit_decision.get("committed") is True

    return {
        "candidates": list(candidates),
        "total_candidates": len(candidates),
        "runtime_apply_candidate_ready": ready,
        "runtime_apply_candidate_blocked": not ready,
        "commit_reference": final_commit_decision.get("commit_reason") or "",
        "preview_only": True,
    }


def _build_runtime_apply_target_preview(
    execution_commit_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only runtime apply target preview."""
    targets = _as_list(context.get("runtime_apply_targets")) or [
        "runtime/order_queue.json",
        "runtime/order_executions.json",
        "runtime/order_locks.json",
    ]

    return {
        "targets": list(targets),
        "total_targets": len(targets),
        "target_ready": True,
        "target_written": False,
        "target_reason": "preview-only runtime apply targets",
        "preview_only": True,
    }


def _build_runtime_apply_sequence_preview(
    execution_commit_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only runtime apply sequence preview."""
    sequence = _as_list(context.get("runtime_apply_sequence")) or [
        {
            "step_index": 1,
            "step_name": "lock_runtime",
            "step_description": "Preview-only lock runtime before apply",
            "step_executed": False,
            "preview_only": True,
        },
        {
            "step_index": 2,
            "step_name": "apply_order_queue",
            "step_description": "Preview-only apply order queue",
            "step_executed": False,
            "preview_only": True,
        },
        {
            "step_index": 3,
            "step_name": "apply_order_executions",
            "step_description": "Preview-only apply order executions",
            "step_executed": False,
            "preview_only": True,
        },
        {
            "step_index": 4,
            "step_name": "unlock_runtime",
            "step_description": "Preview-only unlock runtime after apply",
            "step_executed": False,
            "preview_only": True,
        },
    ]

    return {
        "sequence_ready": True,
        "steps": list(sequence),
        "total_steps": len(sequence),
        "sequence_executed": False,
        "sequence_reason": "preview-only runtime apply sequence",
        "preview_only": True,
    }


def _build_runtime_apply_verification_preview(
    execution_commit_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only runtime apply verification plan."""
    verification_items = _as_list(context.get("runtime_apply_verification_items")) or [
        {
            "verification_index": 1,
            "verification_name": "commit_preview_ready",
            "verification_description": "Confirm commit preview is ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
        {
            "verification_index": 2,
            "verification_name": "runtime_apply_safety_validation_ready",
            "verification_description": "Confirm runtime apply safety validation is ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
        {
            "verification_index": 3,
            "verification_name": "apply_targets_ready",
            "verification_description": "Confirm apply targets are ready",
            "verification_required": True,
            "verification_completed": False,
            "preview_only": True,
        },
    ]

    return {
        "runtime_apply_verification_required": True,
        "runtime_apply_verification_completed": False,
        "verification_items": list(verification_items),
        "total_items": len(verification_items),
        "verification_reason": "preview-only runtime apply verification plan",
        "preview_only": True,
    }


def _build_runtime_apply_safety_validation(
    execution_commit_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate runtime apply safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if execution_commit_preview.get(flag) is True:
            issues.append("execution commit preview {} must be false".format(flag))

    if execution_commit_preview.get("preview_only") is not True:
        issues.append("execution commit preview preview_only must be true")

    if not _as_dict(execution_commit_preview.get("final_commit_decision")):
        issues.append("execution commit preview final_commit_decision is required")

    final_commit_decision = _as_dict(execution_commit_preview.get("final_commit_decision"))
    if final_commit_decision.get("committed") is not True:
        issues.append("execution commit preview final_commit_decision.committed must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_runtime_apply_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final runtime apply decision."""
    applied = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "applied": applied,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not applied
        else "",
        "apply_reason": "runtime apply safety validation ready" if applied else "",
        "runtime_apply_allowed": False,
        "runtime_apply_started": False,
        "runtime_apply_completed": False,
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


def _validate_execution_commit_preview(
    execution_commit_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the execution commit preview."""
    if not execution_commit_preview:
        return STATUS_INVALID, ["execution_commit_preview must be a dict"]

    status = _text(execution_commit_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["execution commit preview is BLOCKED"] + list(
            execution_commit_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["execution commit preview is INVALID"] + list(
            execution_commit_preview.get("issues") or []
        )
    if status != COMMIT_STATUS_READY:
        return STATUS_INVALID, [
            "execution commit preview status is not EXECUTION_COMMIT_PREVIEW_READY"
        ]

    if execution_commit_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["execution commit preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if execution_commit_preview.get(flag) is True:
            return STATUS_INVALID, ["execution commit preview {} must be false".format(flag)]

    if not _as_dict(execution_commit_preview.get("final_commit_decision")):
        return STATUS_INVALID, ["execution commit preview final_commit_decision is required"]

    final_commit_decision = _as_dict(execution_commit_preview.get("final_commit_decision"))
    if final_commit_decision.get("committed") is not True:
        return STATUS_INVALID, [
            "execution commit preview final_commit_decision.committed must be true"
        ]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    runtime_apply_candidate_preview: dict[str, Any] | None = None,
    runtime_apply_target_preview: dict[str, Any] | None = None,
    runtime_apply_sequence_preview: dict[str, Any] | None = None,
    runtime_apply_verification_preview: dict[str, Any] | None = None,
    runtime_apply_safety_validation: dict[str, Any] | None = None,
    final_runtime_apply_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_apply_allowed": False,
        "runtime_apply_started": False,
        "runtime_apply_completed": False,
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
        "runtime_apply_candidate_preview": deepcopy(runtime_apply_candidate_preview or {}),
        "runtime_apply_target_preview": deepcopy(runtime_apply_target_preview or {}),
        "runtime_apply_sequence_preview": deepcopy(runtime_apply_sequence_preview or {}),
        "runtime_apply_verification_preview": deepcopy(runtime_apply_verification_preview or {}),
        "runtime_apply_safety_validation": deepcopy(runtime_apply_safety_validation or {}),
        "final_runtime_apply_decision": deepcopy(final_runtime_apply_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_runtime_apply_preview(
    execution_commit_preview: Any,
    apply_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only Execution Runtime Apply Preview from a commit preview.

    The pipeline is:
    1. EXECUTION_COMMIT_PREVIEW_READY (from execution commit preview)
    2. EXECUTION_RUNTIME_APPLY_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(execution_commit_preview))
    context = deepcopy(_as_dict(apply_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the execution commit preview
    status, issues = _validate_execution_commit_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_runtime_apply_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            runtime_apply_safety_validation=validation,
            final_runtime_apply_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    apply_candidate_preview = _build_runtime_apply_candidate_preview(preview, context)
    apply_target_preview = _build_runtime_apply_target_preview(preview, context)
    apply_sequence_preview = _build_runtime_apply_sequence_preview(preview, context)
    apply_verification_preview = _build_runtime_apply_verification_preview(preview, context)
    safety_validation = _build_runtime_apply_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_runtime_apply_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        runtime_apply_candidate_preview=apply_candidate_preview,
        runtime_apply_target_preview=apply_target_preview,
        runtime_apply_sequence_preview=apply_sequence_preview,
        runtime_apply_verification_preview=apply_verification_preview,
        runtime_apply_safety_validation=safety_validation,
        final_runtime_apply_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
