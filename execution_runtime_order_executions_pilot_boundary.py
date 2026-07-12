# -*- coding: utf-8 -*-
"""Preview-only Production Pilot Boundary for order_executions.

This module evaluates whether the single ``order_executions`` runtime target is
ready for a future production pilot write. It creates only dry-run plans and
never calls commit services, atomic writers, backup, rollback, cleanup,
Order Queue, or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from execution_runtime_commit_handoff_preview import HANDOFF_TYPE, STATUS_READY
from execution_runtime_commit_request_validation_gate import STATUS_APPROVED
from execution_runtime_reader import read_order_executions


PILOT_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_BOUNDARY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
EXECUTION_MODE_INIT = "INIT"
EXECUTION_MODE_APPEND = "APPEND"
LOGICAL_TARGET = "order_executions"
RELATIVE_PATH = "order_executions.json"


def build_order_executions_pilot_boundary(
    handoff_preview: Any,
    commit_service_route_preview: Any,
    runtime_root: str | Path,
) -> dict[str, Any]:
    """Build a dry-run pilot boundary from Handoff Preview and route preview."""
    if not isinstance(handoff_preview, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_HANDOFF_PREVIEW"])
    if not isinstance(commit_service_route_preview, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_COMMIT_SERVICE_ROUTE_PREVIEW"])

    handoff = deepcopy(handoff_preview)
    route = deepcopy(commit_service_route_preview)
    commit_input = _as_dict(handoff.get("commit_service_input_preview"))
    allowlist = _as_dict(handoff.get("allowlist_decision"))
    gate_decision = _as_dict(handoff.get("gate_decision"))
    request_fingerprint = _text(handoff.get("request_fingerprint"))
    logical_target = _text(handoff.get("logical_target"))
    runtime_target = _text(handoff.get("runtime_target"))
    relative_path = _text(handoff.get("relative_path"))
    issues = _as_list(handoff.get("issues"))
    warnings = _as_list(handoff.get("warnings"))

    if handoff.get("handoff_type") != HANDOFF_TYPE:
        issues.append("INVALID_HANDOFF_TYPE")
    if handoff.get("status") != STATUS_READY or handoff.get("handoff_ready") is not True:
        issues.append("HANDOFF_NOT_READY")
    if gate_decision.get("status") != STATUS_APPROVED or gate_decision.get("commit_request_approved") is not True:
        issues.append("VALIDATION_GATE_NOT_APPROVED")
    if gate_decision.get("status") == STATUS_INVALID:
        issues.append("VALIDATION_GATE_INVALID")

    if route.get("call_allowed") is not False:
        issues.append("COMMIT_SERVICE_CALL_MUST_NOT_BE_ALLOWED")
    if route.get("dry_run_only") is not True:
        issues.append("COMMIT_SERVICE_ROUTE_DRY_RUN_ONLY_REQUIRED")
    if route.get("runtime_write") is not False:
        issues.append("COMMIT_SERVICE_ROUTE_RUNTIME_WRITE_MUST_BE_FALSE")
    if route.get("preview_only") is not True:
        issues.append("COMMIT_SERVICE_ROUTE_PREVIEW_ONLY_REQUIRED")

    if handoff.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if handoff.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if handoff.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")

    if logical_target != LOGICAL_TARGET:
        issues.append("PILOT_LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if relative_path != RELATIVE_PATH:
        issues.append("PILOT_RELATIVE_PATH_MUST_BE_ORDER_EXECUTIONS_JSON")
    if _text(commit_input.get("logical_target")) != logical_target:
        issues.append("COMMIT_INPUT_LOGICAL_TARGET_MISMATCH")
    if _text(commit_input.get("runtime_target")) != runtime_target:
        issues.append("COMMIT_INPUT_RUNTIME_TARGET_MISMATCH")
    if _text(commit_input.get("relative_path")) != relative_path:
        issues.append("COMMIT_INPUT_RELATIVE_PATH_MISMATCH")
    if _text(commit_input.get("request_fingerprint")) != request_fingerprint:
        issues.append("REQUEST_FINGERPRINT_MISMATCH")

    allowlist_target = _text(allowlist.get("resolved_path") or allowlist.get("normalized_path"))
    if allowlist.get("allowed") is not True:
        issues.append("ALLOWLIST_DECISION_NOT_ALLOWED")
    if _text(allowlist.get("logical_target")) != logical_target:
        issues.append("ALLOWLIST_LOGICAL_TARGET_MISMATCH")
    if _text(allowlist.get("relative_path")) != relative_path:
        issues.append("ALLOWLIST_RELATIVE_PATH_MISMATCH")
    if allowlist_target != runtime_target:
        issues.append("ALLOWLIST_RUNTIME_TARGET_MISMATCH")
    if not request_fingerprint:
        issues.append("MISSING_REQUEST_FINGERPRINT")

    root = Path(runtime_root).resolve(strict=False)
    target = Path(runtime_target).resolve(strict=False) if runtime_target else Path()
    if not _is_under_root(target, root):
        issues.append("RUNTIME_TARGET_OUTSIDE_RUNTIME_ROOT")
    if target.name != RELATIVE_PATH:
        issues.append("RUNTIME_TARGET_FILE_NAME_MISMATCH")

    parent_exists = bool(runtime_target) and target.parent.exists()
    file_exists = bool(runtime_target) and target.exists()
    execution_mode = EXECUTION_MODE_APPEND if file_exists else EXECUTION_MODE_INIT
    schema_ready = False
    if file_exists:
        read_result = read_order_executions(target)
        schema_ready = read_result.get("ok") is True
        if not schema_ready:
            issues.extend(_as_list(read_result.get("issues")) or ["ORDER_EXECUTIONS_SCHEMA_INVALID"])
    if not parent_exists:
        issues.append("ORDER_EXECUTIONS_PARENT_MISSING")

    backup_required = file_exists
    backup_plan = _backup_plan(target, request_fingerprint, backup_required=backup_required)
    atomic_write_plan = _atomic_write_plan(
        target,
        request_fingerprint,
        parent_exists=parent_exists,
        file_exists=file_exists,
        schema_ready=schema_ready,
        execution_mode=execution_mode,
    )
    rollback_plan = _rollback_plan(target, backup_plan, execution_mode=execution_mode)
    preconditions = _preconditions(
        handoff_ready=handoff.get("handoff_ready") is True,
        gate_approved=gate_decision.get("status") == STATUS_APPROVED,
        route_preview_only=route.get("call_allowed") is False and route.get("dry_run_only") is True,
        target_match=logical_target == LOGICAL_TARGET and relative_path == RELATIVE_PATH,
        under_runtime_root=_is_under_root(target, root),
        parent_exists=parent_exists,
        init_or_schema_ready=(execution_mode == EXECUTION_MODE_INIT or schema_ready),
    )

    status = STATUS_READY if not issues else _blocked_status(issues)
    return _result(
        status=status,
        logical_target=logical_target,
        runtime_target=str(target) if str(target) != "." else runtime_target,
        file_exists=file_exists,
        execution_mode=execution_mode,
        backup_required=backup_required,
        backup_plan=backup_plan,
        atomic_write_plan=atomic_write_plan,
        rollback_plan=rollback_plan,
        preconditions=preconditions,
        issues=_dedupe(issues),
        warnings=_dedupe(warnings),
    )


def _result(
    *,
    status: str,
    logical_target: str = "",
    runtime_target: str = "",
    file_exists: bool = False,
    execution_mode: str = "",
    backup_required: bool = False,
    backup_plan: dict[str, Any] | None = None,
    atomic_write_plan: dict[str, Any] | None = None,
    rollback_plan: dict[str, Any] | None = None,
    preconditions: list[dict[str, Any]] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "pilot_type": PILOT_TYPE,
        "status": status,
        "pilot_ready": status == STATUS_READY and not issues,
        "logical_target": logical_target,
        "runtime_target": runtime_target,
        "file_exists": file_exists,
        "execution_mode": execution_mode,
        "backup_required": backup_required,
        "backup_plan": deepcopy(backup_plan or {}),
        "atomic_write_plan": deepcopy(atomic_write_plan or {}),
        "rollback_plan": deepcopy(rollback_plan or {}),
        "preconditions": deepcopy(preconditions or []),
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "commit_service_called": False,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _backup_plan(target: Path, request_fingerprint: str, *, backup_required: bool) -> dict[str, Any]:
    return {
        "target": str(target) if str(target) != "." else "",
        "backup_required": backup_required,
        "backup_path_preview": str(target) + ".bak" if backup_required else "",
        "backup_action": "copy_existing_file" if backup_required else "none",
        "request_fingerprint": request_fingerprint,
        "backup_created": False,
        "preview_only": True,
        "runtime_write": False,
    }


def _atomic_write_plan(
    target: Path,
    request_fingerprint: str,
    *,
    parent_exists: bool,
    file_exists: bool,
    schema_ready: bool,
    execution_mode: str,
) -> dict[str, Any]:
    token = request_fingerprint[:12] if request_fingerprint else "missing"
    tmp_path = target.with_name(f".{target.name}.{token}.tmp") if str(target) else Path()
    return {
        "target": str(target) if str(target) != "." else "",
        "temp_path_preview": str(tmp_path) if str(tmp_path) != "." else "",
        "method": "_write_json_atomic",
        "execution_mode": execution_mode,
        "parent_exists": parent_exists,
        "file_exists": file_exists,
        "schema_ready": schema_ready,
        "atomic_replace_required": True,
        "can_execute_preview": parent_exists and (execution_mode == EXECUTION_MODE_INIT or schema_ready),
        "writer_called": False,
        "preview_only": True,
        "runtime_write": False,
    }


def _rollback_plan(target: Path, backup_plan: dict[str, Any], *, execution_mode: str) -> dict[str, Any]:
    if execution_mode == EXECUTION_MODE_APPEND:
        action = "restore_backup_after_write_failure"
        cleanup_required = False
    else:
        action = "remove_created_file_after_init_failure"
        cleanup_required = True
    return {
        "target": str(target) if str(target) != "." else "",
        "restore_from_backup_preview": _text(backup_plan.get("backup_path_preview")),
        "rollback_action": action,
        "rollback_required_on_write_failure": execution_mode == EXECUTION_MODE_APPEND,
        "cleanup_required_on_init_failure": cleanup_required,
        "rollback_executed": False,
        "cleanup_executed": False,
        "preview_only": True,
        "runtime_write": False,
    }


def _preconditions(**checks: bool) -> list[dict[str, Any]]:
    return [{"name": name, "ok": ok is True} for name, ok in checks.items()]


def _is_under_root(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _blocked_status(issues: list[Any]) -> str:
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


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
    if text == "ORDER_EXECUTIONS_PARENT_MISSING":
        return False
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH", "OUTSIDE")
    return any(marker in text for marker in markers)
