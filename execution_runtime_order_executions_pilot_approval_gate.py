# -*- coding: utf-8 -*-
"""Preview-only approval gate for the order_executions production pilot."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    LOGICAL_TARGET,
    PILOT_TYPE,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
)


APPROVAL_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_GATE"
STATUS_APPROVED = "APPROVED"


def evaluate_order_executions_pilot_approval(pilot_boundary_result: Any) -> dict[str, Any]:
    """Approve a READY pilot boundary without enabling actual execution."""
    if not isinstance(pilot_boundary_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_PILOT_BOUNDARY_RESULT"])

    pilot = deepcopy(pilot_boundary_result)
    issues = _as_list(pilot.get("issues"))
    warnings = _as_list(pilot.get("warnings"))
    logical_target = _text(pilot.get("logical_target"))
    runtime_target = _text(pilot.get("runtime_target"))
    execution_mode = _text(pilot.get("execution_mode"))
    backup_plan = _as_dict(pilot.get("backup_plan"))
    atomic_write_plan = _as_dict(pilot.get("atomic_write_plan"))
    rollback_plan = _as_dict(pilot.get("rollback_plan"))

    if pilot.get("pilot_type") != PILOT_TYPE:
        issues.append("INVALID_PILOT_TYPE")
    if pilot.get("status") != STATUS_READY or pilot.get("pilot_ready") is not True:
        issues.append("PILOT_BOUNDARY_NOT_READY")
    if logical_target != LOGICAL_TARGET:
        issues.append("PILOT_LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if execution_mode not in {EXECUTION_MODE_INIT, EXECUTION_MODE_APPEND}:
        issues.append("INVALID_EXECUTION_MODE")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")
    if pilot.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if pilot.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if pilot.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if pilot.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")

    if pilot.get("file_exists") is True and backup_plan.get("backup_required") is not True:
        issues.append("BACKUP_REQUIRED_FOR_EXISTING_FILE")
    if pilot.get("file_exists") is False and backup_plan.get("backup_required") is True:
        issues.append("BACKUP_NOT_REQUIRED_FOR_MISSING_FILE")

    if not backup_plan:
        issues.append("BACKUP_PLAN_MISSING")
    if not atomic_write_plan:
        issues.append("ATOMIC_WRITE_PLAN_MISSING")
    if not rollback_plan:
        issues.append("ROLLBACK_PLAN_MISSING")

    if backup_plan:
        if _text(backup_plan.get("target")) != runtime_target:
            issues.append("BACKUP_TARGET_MISMATCH")
        if backup_plan.get("preview_only") is not True:
            issues.append("BACKUP_PLAN_PREVIEW_ONLY_REQUIRED")
        if backup_plan.get("runtime_write") is not False:
            issues.append("BACKUP_PLAN_RUNTIME_WRITE_MUST_BE_FALSE")
    if atomic_write_plan:
        if _text(atomic_write_plan.get("target")) != runtime_target:
            issues.append("ATOMIC_WRITE_TARGET_MISMATCH")
        if atomic_write_plan.get("preview_only") is not True:
            issues.append("ATOMIC_WRITE_PREVIEW_ONLY_REQUIRED")
        if atomic_write_plan.get("runtime_write") is not False:
            issues.append("ATOMIC_WRITE_RUNTIME_WRITE_MUST_BE_FALSE")
    if rollback_plan:
        if _text(rollback_plan.get("target")) != runtime_target:
            issues.append("ROLLBACK_TARGET_MISMATCH")
        if rollback_plan.get("preview_only") is not True:
            issues.append("ROLLBACK_PREVIEW_ONLY_REQUIRED")
        if rollback_plan.get("runtime_write") is not False:
            issues.append("ROLLBACK_RUNTIME_WRITE_MUST_BE_FALSE")

    approval_fingerprint = _fingerprint(
        {
            "pilot_boundary": pilot,
            "logical_target": logical_target,
            "runtime_target": runtime_target,
            "execution_mode": execution_mode,
            "backup_plan": backup_plan,
            "atomic_write_plan": atomic_write_plan,
            "rollback_plan": rollback_plan,
        }
    )

    status = STATUS_APPROVED if not issues else _status_from_issues(issues, pilot.get("status"))
    return _result(
        status=status,
        approval_fingerprint=approval_fingerprint,
        pilot_boundary_snapshot=pilot,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        file_exists=pilot.get("file_exists") is True,
        backup_required=pilot.get("backup_required") is True,
        backup_plan=backup_plan,
        atomic_write_plan=atomic_write_plan,
        rollback_plan=rollback_plan,
        issues=_dedupe(issues),
        warnings=warnings,
    )


def _result(
    *,
    status: str,
    approval_fingerprint: str = "",
    pilot_boundary_snapshot: dict[str, Any] | None = None,
    logical_target: str = "",
    runtime_target: str = "",
    execution_mode: str = "",
    file_exists: bool = False,
    backup_required: bool = False,
    backup_plan: dict[str, Any] | None = None,
    atomic_write_plan: dict[str, Any] | None = None,
    rollback_plan: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "approval_type": APPROVAL_TYPE,
        "status": status,
        "production_pilot_approved": status == STATUS_APPROVED,
        "approval_fingerprint": approval_fingerprint,
        "pilot_boundary_snapshot": deepcopy(pilot_boundary_snapshot or {}),
        "logical_target": logical_target,
        "runtime_target": runtime_target,
        "execution_mode": execution_mode,
        "file_exists": file_exists,
        "backup_required": backup_required,
        "backup_plan": deepcopy(backup_plan or {}),
        "atomic_write_plan": deepcopy(atomic_write_plan or {}),
        "rollback_plan": deepcopy(rollback_plan or {}),
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "actual_execution_allowed": False,
        "commit_service_called": False,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _status_from_issues(issues: list[Any], pilot_status: Any) -> str:
    if pilot_status == STATUS_INVALID:
        return STATUS_INVALID
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _invalid_issue(issue: Any) -> bool:
    text = str(issue)
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH")
    return any(marker in text for marker in markers)
