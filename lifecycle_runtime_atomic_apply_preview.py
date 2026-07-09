# -*- coding: utf-8 -*-
"""Preview-only atomic apply planning after runtime commit executor preview.

The module only builds an in-memory preview of the final atomic apply boundary.
It never writes runtime files, writes SQLite, updates GUI state, calls
SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_ATOMIC_APPLY_PREVIEW"
STATUS_READY = "ATOMIC_APPLY_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


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


def _result(
    *,
    status: str,
    apply_batch: dict[str, Any] | None = None,
    atomic_boundary_validation: dict[str, Any] | None = None,
    pre_apply_validation: dict[str, Any] | None = None,
    post_apply_verification_preview: dict[str, Any] | None = None,
    rollback_trigger_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "atomic_apply_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "apply_batch": deepcopy(apply_batch or {}),
        "atomic_boundary_validation": deepcopy(atomic_boundary_validation or {}),
        "pre_apply_validation": deepcopy(pre_apply_validation or {}),
        "post_apply_verification_preview": deepcopy(post_apply_verification_preview or {}),
        "rollback_trigger_preview": deepcopy(rollback_trigger_preview or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _validation(ready: bool, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": ready,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_apply_batch(atomic_plan: dict[str, Any], executor_payload: dict[str, Any], now: str) -> dict[str, Any]:
    groups = _as_list(atomic_plan.get("transaction_groups"))
    sequence = _as_list(atomic_plan.get("commit_sequence"))
    return {
        "batch_id": "ATOMIC_APPLY_BATCH_{}".format(uuid4().hex),
        "batch_type": "RUNTIME_POSITION_BALANCE_ATOMIC_APPLY_PREVIEW",
        "transaction_groups": deepcopy(groups),
        "commit_sequence": deepcopy(sequence),
        "executor_preview": deepcopy(_as_dict(executor_payload.get("executor_preview"))),
        "preview_only": True,
        "atomic_apply_executed": False,
        "created_at": now,
    }


def _validate_boundary(atomic_plan: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    boundary = _as_dict(atomic_plan.get("atomic_boundary"))
    groups = _as_list(atomic_plan.get("transaction_groups"))
    issues: list[str] = []
    if not boundary:
        issues.append("atomic_boundary is required")
    if not groups:
        issues.append("transaction_groups are required")
    if boundary.get("boundary_type") != "ALL_OR_NOTHING":
        issues.append("atomic_boundary.boundary_type must be ALL_OR_NOTHING")
    if boundary.get("preview_only") is not True:
        issues.append("atomic_boundary.preview_only must be true")
    if boundary.get("requires_backup_before_apply") is not True:
        issues.append("atomic boundary must require backup before apply")
    if boundary.get("requires_rollback_on_failure") is not True:
        issues.append("atomic boundary must require rollback on failure")
    for group in groups:
        payload = _as_dict(group)
        if payload.get("atomic") is not True:
            issues.append("transaction group {} is not atomic".format(_text(payload.get("group_id"))))
    return {
        "validation_type": "ATOMIC_BOUNDARY_VALIDATION",
        "ready": not issues,
        "boundary": deepcopy(boundary),
        "group_count": len(groups),
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _pre_apply_validation(executor_payload: dict[str, Any], atomic_plan: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    validation = _as_dict(executor_payload.get("execution_validation"))
    if validation.get("ready") is not True:
        issues.append("executor execution_validation.ready must be true")
    if executor_payload.get("preview_only") is not True:
        issues.append("executor preview_only must be true")
    if executor_payload.get("runtime_write") is not False:
        issues.append("executor runtime_write must be false")
    if not _as_list(atomic_plan.get("commit_sequence")):
        issues.append("atomic commit_sequence is required")
    return {
        "validation_type": "PRE_ATOMIC_APPLY_VALIDATION",
        "ready": not issues,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _post_apply_verification(apply_batch: dict[str, Any], now: str) -> dict[str, Any]:
    groups = _as_list(apply_batch.get("transaction_groups"))
    return {
        "verification_type": "POST_ATOMIC_APPLY_VERIFICATION_PREVIEW",
        "preview_only": True,
        "atomic_apply_executed": False,
        "expected_group_count": len(groups),
        "checks": [
            "verify runtime state after apply",
            "verify position state after apply",
            "verify balance state after apply",
            "verify all-or-nothing boundary",
        ],
        "verified": False,
        "planned_at": now,
    }


def _rollback_trigger(apply_batch: dict[str, Any], atomic_plan: dict[str, Any], now: str) -> dict[str, Any]:
    boundary = _as_dict(atomic_plan.get("atomic_boundary"))
    return {
        "trigger_type": "ATOMIC_APPLY_ROLLBACK_TRIGGER_PREVIEW",
        "preview_only": True,
        "rollback_executed": False,
        "trigger_on_failure": boundary.get("requires_rollback_on_failure") is True,
        "batch_id": apply_batch.get("batch_id", ""),
        "rollback_reasons": [
            "runtime apply failure",
            "position apply failure",
            "balance apply failure",
            "post-apply verification failure",
        ],
        "planned_at": now,
    }


def build_runtime_atomic_apply_preview(
    executor_preview: Any,
    apply_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only final atomic apply plan from executor preview."""
    executor_payload = _as_dict(executor_preview)
    context = deepcopy(_as_dict(apply_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(executor_payload.get("warnings") or [])

    if not executor_payload:
        issues = ["executor_preview must be a dict"]
        validation = _validation(False, issues, warnings)
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, now=now, pre_apply_validation=validation)

    status = _text(executor_payload.get("status")).upper()
    if status == "BLOCKED":
        issues = ["executor preview is BLOCKED"] + list(executor_payload.get("issues") or [])
        validation = _validation(False, issues, warnings)
        return _result(status=STATUS_BLOCKED, issues=issues, warnings=warnings, now=now, pre_apply_validation=validation)
    if status == "INVALID":
        issues = ["executor preview is INVALID"] + list(executor_payload.get("issues") or [])
        validation = _validation(False, issues, warnings)
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, now=now, pre_apply_validation=validation)
    if status != "READY":
        issues = ["executor preview status is not READY"]
        validation = _validation(False, issues, warnings)
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, now=now, pre_apply_validation=validation)

    atomic_plan = _as_dict(executor_payload.get("atomic_execution_plan"))
    if not atomic_plan:
        issues = ["atomic_execution_plan is required"]
        validation = _validation(False, issues, warnings)
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, now=now, pre_apply_validation=validation)

    boundary_validation = _validate_boundary(atomic_plan, warnings)
    pre_validation = _pre_apply_validation(executor_payload, atomic_plan, warnings)
    validation_issues = list(boundary_validation.get("issues") or []) + list(pre_validation.get("issues") or [])
    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            atomic_boundary_validation=boundary_validation,
            pre_apply_validation=pre_validation,
            issues=validation_issues,
            warnings=warnings,
            now=now,
        )

    apply_batch = _build_apply_batch(atomic_plan, executor_payload, now)
    post_verification = _post_apply_verification(apply_batch, now)
    rollback_preview = _rollback_trigger(apply_batch, atomic_plan, now)
    return _result(
        status=STATUS_READY,
        apply_batch=apply_batch,
        atomic_boundary_validation=boundary_validation,
        pre_apply_validation=pre_validation,
        post_apply_verification_preview=post_verification,
        rollback_trigger_preview=rollback_preview,
        issues=[],
        warnings=warnings,
        now=now,
    )

