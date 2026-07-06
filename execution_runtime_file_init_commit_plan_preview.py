# -*- coding: utf-8 -*-
"""Preview-only commit plan for execution runtime file initialization.

This module combines file-init preview and approval-gate results into an
in-memory initialization commit plan. It never creates files, creates
directories, writes runtime data, calls commit services, commits queues, calls
SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_file_init_approval_gate import GATE_TYPE
from execution_runtime_file_init_preview import PREVIEW_TYPE


PLAN_TYPE = "EXECUTION_RUNTIME_FILE_INIT_COMMIT_PLAN_PREVIEW"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _result(
    *,
    status: str,
    init_commit_ready: bool,
    planned_targets: dict[str, Any] | None = None,
    planned_schemas: dict[str, Any] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "status": status,
        "init_commit_ready": init_commit_ready,
        "preview_only": True,
        "runtime_write": False,
        "planned_targets": deepcopy(planned_targets or {}),
        "planned_schemas": deepcopy(planned_schemas or {}),
        "required_confirmations": deepcopy(required_confirmations or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _base_validation(preview: dict[str, Any], approval: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if preview.get("preview_type") != PREVIEW_TYPE:
        issues.append("INVALID_FILE_INIT_PREVIEW_TYPE")
    if approval.get("gate_type") != GATE_TYPE:
        issues.append("INVALID_FILE_INIT_APPROVAL_GATE_TYPE")
    if preview.get("preview_only") is not True or approval.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if preview.get("runtime_write") is not False or approval.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    return issues


def _status_conflict(preview_status: Any, approval_status: Any) -> bool:
    expected_by_approval = {
        "APPROVED": "READY",
        "SKIPPED": "SKIPPED",
        "BLOCKED": "BLOCKED",
        "INVALID": "INVALID",
    }
    expected_preview = expected_by_approval.get(approval_status)
    return expected_preview is None or preview_status != expected_preview


def build_execution_runtime_file_init_commit_plan_preview(
    file_init_preview_result: Any,
    file_init_approval_gate_result: Any,
) -> dict[str, Any]:
    """Build a file initialization commit plan preview without side effects."""
    if not isinstance(file_init_preview_result, dict):
        return _result(
            status="INVALID",
            init_commit_ready=False,
            issues=["MALFORMED_FILE_INIT_PREVIEW_RESULT"],
        )
    if not isinstance(file_init_approval_gate_result, dict):
        return _result(
            status="INVALID",
            init_commit_ready=False,
            issues=["MALFORMED_FILE_INIT_APPROVAL_GATE_RESULT"],
        )

    preview = _as_dict(file_init_preview_result)
    approval = _as_dict(file_init_approval_gate_result)
    structural_issues = _base_validation(preview, approval)
    issues = _unique(
        _as_list(preview.get("issues"))
        + _as_list(approval.get("issues"))
        + structural_issues
    )
    warnings = _unique(_as_list(preview.get("warnings")) + _as_list(approval.get("warnings")))
    required_confirmations = _as_dict(approval.get("required_confirmations"))
    planned_targets = _as_dict(preview.get("targets"))
    planned_schemas = _as_dict(preview.get("schemas"))

    preview_status = preview.get("status")
    approval_status = approval.get("status")
    if _status_conflict(preview_status, approval_status):
        return _result(
            status="INVALID",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=_unique(issues + ["FILE_INIT_PREVIEW_APPROVAL_STATUS_CONFLICT"]),
            warnings=warnings,
        )

    if structural_issues:
        return _result(
            status="INVALID",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=structural_issues,
            warnings=warnings,
        )

    if approval_status == "SKIPPED":
        return _result(
            status="SKIPPED",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            warnings=warnings,
        )

    if approval_status == "BLOCKED":
        return _result(
            status="BLOCKED",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=_as_list(approval.get("issues")) or ["FILE_INIT_APPROVAL_BLOCKED"],
            warnings=warnings,
        )

    if approval_status == "INVALID":
        return _result(
            status="INVALID",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=_as_list(approval.get("issues")) or ["FILE_INIT_APPROVAL_INVALID"],
            warnings=warnings,
        )

    if approval_status != "APPROVED" or approval.get("init_commit_allowed") is not True:
        return _result(
            status="INVALID",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=["INIT_COMMIT_APPROVAL_REQUIRED"],
            warnings=warnings,
        )

    missing_ready_fields: list[str] = []
    if not planned_targets.get("order_executions") or not planned_targets.get("order_locks"):
        missing_ready_fields.append("MISSING_PLANNED_TARGETS")
    if not planned_schemas.get("order_executions") or not planned_schemas.get("order_locks"):
        missing_ready_fields.append("MISSING_PLANNED_SCHEMAS")
    if missing_ready_fields:
        return _result(
            status="INVALID",
            init_commit_ready=False,
            planned_targets=planned_targets,
            planned_schemas=planned_schemas,
            required_confirmations=required_confirmations,
            issues=missing_ready_fields,
            warnings=warnings,
        )

    return _result(
        status="READY",
        init_commit_ready=True,
        planned_targets=planned_targets,
        planned_schemas=planned_schemas,
        required_confirmations=required_confirmations,
        warnings=warnings,
    )
