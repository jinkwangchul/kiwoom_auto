# -*- coding: utf-8 -*-
"""Validator for execution runtime file-init commit plan previews.

This module only validates in-memory initialization commit plan dictionaries.
It never creates runtime files, writes files, creates directories, performs
atomic writes, commits queues, calls SendOrder, or connects to GUI/real
execution.
"""

from __future__ import annotations

from typing import Any

from execution_runtime_file_init_commit_plan_preview import PLAN_TYPE


ALLOWED_STATUSES = {"READY", "BLOCKED", "INVALID", "SKIPPED"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    valid: bool,
    status: str,
    issues: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "valid": valid,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "issues": list(issues),
        "warnings": list(warnings or []),
    }


def validate_execution_runtime_file_init_commit_plan_preview(plan_preview: Any) -> dict[str, Any]:
    """Validate an Execution Runtime File Init Commit Plan Preview."""
    if not isinstance(plan_preview, dict):
        return _result(
            valid=False,
            status="INVALID",
            issues=["MALFORMED_FILE_INIT_COMMIT_PLAN_PREVIEW"],
        )

    issues: list[str] = []
    warnings: list[str] = []
    status = plan_preview.get("status")
    plan_issues = plan_preview.get("issues")
    plan_warnings = plan_preview.get("warnings")
    planned_targets = plan_preview.get("planned_targets")
    planned_schemas = plan_preview.get("planned_schemas")

    if plan_preview.get("plan_type") != PLAN_TYPE:
        issues.append("INVALID_PLAN_TYPE")
    if status not in ALLOWED_STATUSES:
        issues.append("INVALID_STATUS")
    if plan_preview.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if plan_preview.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if not isinstance(plan_preview.get("init_commit_ready"), bool):
        issues.append("INIT_COMMIT_READY_MUST_BE_BOOL")
    if not isinstance(plan_preview.get("required_confirmations"), dict):
        issues.append("REQUIRED_CONFIRMATIONS_MUST_BE_DICT")
    if not isinstance(plan_issues, list):
        issues.append("ISSUES_MUST_BE_LIST")
        plan_issues = []
    if not isinstance(plan_warnings, list):
        issues.append("WARNINGS_MUST_BE_LIST")
        plan_warnings = []
    warnings.extend(plan_warnings)

    if not isinstance(planned_targets, dict):
        issues.append("PLANNED_TARGETS_MUST_BE_DICT")
        planned_targets = {}
    if not isinstance(planned_schemas, dict):
        issues.append("PLANNED_SCHEMAS_MUST_BE_DICT")
        planned_schemas = {}

    if status == "READY":
        if plan_preview.get("init_commit_ready") is not True:
            issues.append("READY_REQUIRES_INIT_COMMIT_READY_TRUE")
        if not _text(planned_targets.get("order_executions")):
            issues.append("MISSING_TARGET_ORDER_EXECUTIONS")
        if not _text(planned_targets.get("order_locks")):
            issues.append("MISSING_TARGET_ORDER_LOCKS")
        if not isinstance(planned_schemas.get("order_executions"), dict) or not planned_schemas.get(
            "order_executions"
        ):
            issues.append("MISSING_SCHEMA_ORDER_EXECUTIONS")
        if not isinstance(planned_schemas.get("order_locks"), dict) or not planned_schemas.get(
            "order_locks"
        ):
            issues.append("MISSING_SCHEMA_ORDER_LOCKS")
        if plan_issues:
            issues.append("READY_WITH_ISSUES")

    if status == "INVALID" and not plan_issues:
        issues.append("INVALID_WITHOUT_ISSUES")

    if status == "BLOCKED" and not plan_issues and not plan_warnings:
        issues.append("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS")

    if status == "SKIPPED" and plan_preview.get("init_commit_ready") is not False:
        issues.append("SKIPPED_REQUIRES_INIT_COMMIT_READY_FALSE")

    return _result(
        valid=not issues,
        status=status if status in ALLOWED_STATUSES else "INVALID",
        issues=issues,
        warnings=warnings,
    )
