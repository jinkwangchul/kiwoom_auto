# -*- coding: utf-8 -*-
"""Validator for execution runtime commit plan previews.

This module only validates in-memory commit plan preview dictionaries. It never
creates runtime files, writes files, creates directories, performs atomic
writes, commits queues, or calls execution/order components.
"""

from __future__ import annotations

from typing import Any

from execution_runtime_commit_plan_preview import PLAN_TYPE


ALLOWED_STATUSES = {"READY", "BLOCKED", "INVALID"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(*, valid: bool, status: str, issues: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "valid": valid,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "issues": list(issues),
        "warnings": list(warnings or []),
    }


def _record_issues(record: Any, *, missing_issue: str, required_fields: dict[str, str]) -> list[str]:
    if not isinstance(record, dict) or not record:
        return [missing_issue]
    issues: list[str] = []
    for field, issue in required_fields.items():
        if not _text(record.get(field)):
            issues.append(issue)
    return issues


def validate_execution_runtime_commit_plan_preview(plan_preview: Any) -> dict[str, Any]:
    """Validate an Execution Runtime Commit Plan Preview without side effects."""
    if not isinstance(plan_preview, dict):
        return _result(
            valid=False,
            status="INVALID",
            issues=["MALFORMED_COMMIT_PLAN_PREVIEW"],
        )

    issues: list[str] = []
    warnings: list[str] = []
    status = plan_preview.get("status")
    plan_issues = plan_preview.get("issues")
    plan_warnings = plan_preview.get("warnings")
    planned_targets = plan_preview.get("planned_targets")
    planned_records = plan_preview.get("planned_records")

    if plan_preview.get("plan_type") != PLAN_TYPE:
        issues.append("INVALID_PLAN_TYPE")
    if plan_preview.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if plan_preview.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if status not in ALLOWED_STATUSES:
        issues.append("INVALID_STATUS")
    if not isinstance(plan_preview.get("commit_ready"), bool):
        issues.append("COMMIT_READY_MUST_BE_BOOL")

    if not isinstance(planned_targets, dict):
        issues.append("PLANNED_TARGETS_MUST_BE_DICT")
    else:
        if not _text(planned_targets.get("order_executions")):
            issues.append("MISSING_TARGET_ORDER_EXECUTIONS")
        if not _text(planned_targets.get("order_locks")):
            issues.append("MISSING_TARGET_ORDER_LOCKS")

    if not isinstance(planned_records, dict):
        issues.append("PLANNED_RECORDS_MUST_BE_DICT")
        planned_records = {}
    if not isinstance(plan_preview.get("required_confirmations"), dict):
        issues.append("REQUIRED_CONFIRMATIONS_MUST_BE_DICT")
    if not isinstance(plan_issues, list):
        issues.append("ISSUES_MUST_BE_LIST")
        plan_issues = []
    if not isinstance(plan_warnings, list):
        issues.append("WARNINGS_MUST_BE_LIST")
        plan_warnings = []
    warnings.extend(plan_warnings)

    if status == "READY":
        if plan_preview.get("commit_ready") is not True:
            issues.append("READY_REQUIRES_COMMIT_READY_TRUE")
        issues.extend(
            _record_issues(
                planned_records.get("execution"),
                missing_issue="MISSING_PLANNED_EXECUTION_RECORD",
                required_fields={
                    "execution_id": "MISSING_EXECUTION_RECORD_EXECUTION_ID",
                    "order_id": "MISSING_EXECUTION_RECORD_ORDER_ID",
                    "request_hash": "MISSING_EXECUTION_RECORD_REQUEST_HASH",
                },
            )
        )
        issues.extend(
            _record_issues(
                planned_records.get("lock"),
                missing_issue="MISSING_PLANNED_LOCK_RECORD",
                required_fields={
                    "lock_id": "MISSING_LOCK_RECORD_LOCK_ID",
                    "order_id": "MISSING_LOCK_RECORD_ORDER_ID",
                    "request_hash": "MISSING_LOCK_RECORD_REQUEST_HASH",
                },
            )
        )
        if plan_issues:
            issues.append("READY_WITH_ISSUES")

    if status == "INVALID" and not plan_issues:
        issues.append("INVALID_WITHOUT_ISSUES")

    if status == "BLOCKED" and not plan_issues and not plan_warnings:
        issues.append("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS")

    return _result(
        valid=not issues,
        status=status if status in ALLOWED_STATUSES else "INVALID",
        issues=issues,
        warnings=warnings,
    )
