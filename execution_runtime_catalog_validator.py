# -*- coding: utf-8 -*-
"""Validator for Execution Runtime Catalog Preview objects.

This module performs structure-only validation. It does not read or write
runtime files, enqueue records, connect orchestrators, or call execution/order
components.
"""

from __future__ import annotations

from typing import Any

from execution_runtime_catalog_preview import CATALOG_TYPE


ALLOWED_STATUSES = {"READY", "BLOCKED", "INVALID"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(*, valid: bool, status: str, issues: list[str]) -> dict[str, Any]:
    return {
        "valid": valid,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "issues": list(issues),
        "warnings": [],
    }


def validate_execution_runtime_catalog_preview(catalog_preview: Any) -> dict[str, Any]:
    """Validate a Runtime Catalog Preview without side effects."""
    if not isinstance(catalog_preview, dict):
        return _result(
            valid=False,
            status="INVALID",
            issues=["MALFORMED_CATALOG_PREVIEW"],
        )

    issues: list[str] = []
    status = catalog_preview.get("status")
    runtime_targets = catalog_preview.get("runtime_targets")
    checks = catalog_preview.get("checks")
    warnings = catalog_preview.get("warnings")
    catalog_issues = catalog_preview.get("issues")

    if catalog_preview.get("catalog_type") != CATALOG_TYPE:
        issues.append("INVALID_CATALOG_TYPE")
    if catalog_preview.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if catalog_preview.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if status not in ALLOWED_STATUSES:
        issues.append("INVALID_STATUS")

    if not isinstance(runtime_targets, dict):
        issues.append("MISSING_RUNTIME_TARGETS")
    else:
        if not _text(runtime_targets.get("order_executions")):
            issues.append("MISSING_RUNTIME_TARGET_ORDER_EXECUTIONS")
        if not _text(runtime_targets.get("order_locks")):
            issues.append("MISSING_RUNTIME_TARGET_ORDER_LOCKS")

    if not isinstance(checks, dict):
        issues.append("CHECKS_MUST_BE_DICT")
    if not isinstance(warnings, list):
        issues.append("WARNINGS_MUST_BE_LIST")
    if not isinstance(catalog_issues, list):
        issues.append("ISSUES_MUST_BE_LIST")

    if status == "READY" and isinstance(catalog_issues, list) and catalog_issues:
        issues.append("READY_WITH_ISSUES")
    if status == "INVALID" and isinstance(catalog_issues, list) and not catalog_issues:
        issues.append("INVALID_WITHOUT_ISSUES")

    required_fields = {
        "execution_id": "MISSING_EXECUTION_ID",
        "order_id": "MISSING_ORDER_ID",
        "request_hash": "MISSING_REQUEST_HASH",
        "lock_id": "MISSING_LOCK_ID",
    }
    for field, issue in required_fields.items():
        if not _text(catalog_preview.get(field)):
            issues.append(issue)

    return _result(
        valid=not issues,
        status=status if status in ALLOWED_STATUSES else "INVALID",
        issues=issues,
    )
