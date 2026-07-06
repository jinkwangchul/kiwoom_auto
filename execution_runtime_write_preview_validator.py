# -*- coding: utf-8 -*-
"""Validator for execution runtime write preview results.

This module only validates in-memory write preview dictionaries. It does not
create runtime files, write files, create directories, perform atomic writes,
commit queues, or call execution/order components.
"""

from __future__ import annotations

from typing import Any

from execution_runtime_write_preview import WRITE_PREVIEW_TYPE


ALLOWED_STATUSES = {"READY", "BLOCKED", "INVALID"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _check_record_fields(
    record: Any,
    *,
    missing_record_issue: str,
    required_fields: dict[str, str],
) -> list[str]:
    if not isinstance(record, dict) or not record:
        return [missing_record_issue]

    issues: list[str] = []
    for field, issue in required_fields.items():
        if not _text(record.get(field)):
            issues.append(issue)
    return issues


def validate_execution_runtime_write_preview(write_preview_result: Any) -> dict[str, Any]:
    """Validate an Execution Runtime Write Preview without side effects."""
    if not isinstance(write_preview_result, dict):
        return _result(
            valid=False,
            status="INVALID",
            issues=["MALFORMED_WRITE_PREVIEW"],
        )

    issues: list[str] = []
    warnings: list[str] = []
    status = write_preview_result.get("status")
    preview_issues = write_preview_result.get("issues")
    preview_warnings = write_preview_result.get("warnings")
    would_write_targets = write_preview_result.get("would_write_targets")

    if write_preview_result.get("write_preview_type") != WRITE_PREVIEW_TYPE:
        issues.append("INVALID_WRITE_PREVIEW_TYPE")
    if write_preview_result.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if write_preview_result.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if status not in ALLOWED_STATUSES:
        issues.append("INVALID_STATUS")

    if not isinstance(would_write_targets, dict):
        issues.append("MISSING_WOULD_WRITE_TARGETS")
    else:
        if not _text(would_write_targets.get("order_executions")):
            issues.append("MISSING_TARGET_ORDER_EXECUTIONS")
        if not _text(would_write_targets.get("order_locks")):
            issues.append("MISSING_TARGET_ORDER_LOCKS")

    if not isinstance(write_preview_result.get("duplicate_checks"), dict):
        issues.append("DUPLICATE_CHECKS_MUST_BE_DICT")
    if not isinstance(preview_issues, list):
        issues.append("ISSUES_MUST_BE_LIST")
        preview_issues = []
    if not isinstance(preview_warnings, list):
        issues.append("WARNINGS_MUST_BE_LIST")
        preview_warnings = []
    warnings.extend(preview_warnings)

    if status == "READY":
        issues.extend(
            _check_record_fields(
                write_preview_result.get("execution_record_preview"),
                missing_record_issue="MISSING_EXECUTION_RECORD_PREVIEW",
                required_fields={
                    "execution_id": "MISSING_EXECUTION_RECORD_EXECUTION_ID",
                    "order_id": "MISSING_EXECUTION_RECORD_ORDER_ID",
                    "request_hash": "MISSING_EXECUTION_RECORD_REQUEST_HASH",
                },
            )
        )
        issues.extend(
            _check_record_fields(
                write_preview_result.get("lock_record_preview"),
                missing_record_issue="MISSING_LOCK_RECORD_PREVIEW",
                required_fields={
                    "lock_id": "MISSING_LOCK_RECORD_LOCK_ID",
                    "order_id": "MISSING_LOCK_RECORD_ORDER_ID",
                    "request_hash": "MISSING_LOCK_RECORD_REQUEST_HASH",
                },
            )
        )
        if preview_issues:
            issues.append("READY_WITH_ISSUES")

    if status == "INVALID" and not preview_issues:
        issues.append("INVALID_WITHOUT_ISSUES")

    if status == "BLOCKED" and not preview_issues and not preview_warnings:
        issues.append("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS")

    return _result(
        valid=not issues,
        status=status if status in ALLOWED_STATUSES else "INVALID",
        issues=issues,
        warnings=warnings,
    )
