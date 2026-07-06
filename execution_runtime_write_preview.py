# -*- coding: utf-8 -*-
"""Preview-only execution runtime write candidates.

This module builds in-memory record previews for future order_executions.json
and order_locks.json writes. It never creates runtime files, writes files,
creates directories, performs atomic replacement, commits queues, or calls
execution/order components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_catalog_preview import RUNTIME_TARGETS


WRITE_PREVIEW_TYPE = "EXECUTION_RUNTIME_WRITE_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _catalog_from_input(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict):
        return {}, False
    if isinstance(value.get("catalog_preview"), dict):
        return deepcopy(value["catalog_preview"]), True
    return deepcopy(value), True


def _base_result(
    *,
    status: str,
    catalog: dict[str, Any] | None = None,
    execution_record_preview: dict[str, Any] | None = None,
    lock_record_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    source = _as_dict(catalog)
    duplicate_checks = {
        "execution_id": source.get("execution_id"),
        "request_hash": source.get("request_hash"),
        "order_id": source.get("order_id"),
        "lock_id": source.get("lock_id"),
    }
    runtime_targets = source.get("runtime_targets")
    return {
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "write_preview_type": WRITE_PREVIEW_TYPE,
        "execution_record_preview": deepcopy(execution_record_preview),
        "lock_record_preview": deepcopy(lock_record_preview),
        "duplicate_checks": deepcopy(duplicate_checks),
        "would_write_targets": deepcopy(runtime_targets if isinstance(runtime_targets, dict) else RUNTIME_TARGETS),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _existing_items(data: Any, field: str) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(data, dict):
        return [], "MALFORMED_EXISTING_DATA"
    items = data.get(field)
    if not isinstance(items, list):
        return [], f"MALFORMED_{field.upper()}_FIELD"
    return [_as_dict(item) for item in items if isinstance(item, dict)], None


def _duplicate_issue(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    source: dict[str, Any],
) -> str | None:
    for field in fields:
        target = _clean_text(source.get(field))
        if not target:
            continue
        for item in items:
            if _clean_text(item.get(field)) == target:
                return f"DUPLICATE_{field.upper()}"
    return None


def _execution_record(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": catalog.get("execution_id"),
        "order_id": catalog.get("order_id"),
        "request_hash": catalog.get("request_hash"),
        "lock_id": catalog.get("lock_id"),
        "status": "RUNTIME_WRITE_PREVIEW",
        "source": "execution_runtime_catalog_preview",
        "preview_only": True,
        "runtime_write": False,
    }


def _lock_record(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "lock_id": catalog.get("lock_id"),
        "order_id": catalog.get("order_id"),
        "request_hash": catalog.get("request_hash"),
        "execution_id": catalog.get("execution_id"),
        "status": "RUNTIME_WRITE_PREVIEW",
        "source": "execution_runtime_catalog_preview",
        "preview_only": True,
        "runtime_write": False,
    }


def build_execution_runtime_write_preview(
    catalog_preview: Any = None,
    *,
    catalog_orchestrator_result: Any = None,
    existing_order_executions_data: Any = None,
    existing_order_locks_data: Any = None,
) -> dict[str, Any]:
    """Build order_executions/order_locks write candidates in memory only."""
    catalog_source = catalog_orchestrator_result if catalog_orchestrator_result is not None else catalog_preview
    catalog, catalog_ok = _catalog_from_input(catalog_source)
    if not catalog_ok:
        return _base_result(
            status=STATUS_INVALID,
            catalog={},
            issues=["MALFORMED_CATALOG_INPUT"],
        )

    catalog_status = catalog.get("status")
    if catalog_status == STATUS_INVALID:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=list(catalog.get("issues") or ["CATALOG_INVALID"]),
            warnings=list(catalog.get("warnings") or []),
        )
    if catalog_status != STATUS_READY:
        issue = "CATALOG_NOT_READY"
        if catalog_status == STATUS_BLOCKED:
            issue = "CATALOG_BLOCKED"
        return _base_result(
            status=STATUS_BLOCKED,
            catalog=catalog,
            issues=list(catalog.get("issues") or [issue]),
            warnings=list(catalog.get("warnings") or []),
        )

    required_fields = {
        "execution_id": "MISSING_EXECUTION_ID",
        "order_id": "MISSING_ORDER_ID",
        "request_hash": "MISSING_REQUEST_HASH",
        "lock_id": "MISSING_LOCK_ID",
    }
    missing = [
        issue
        for field, issue in required_fields.items()
        if not _clean_text(catalog.get(field))
    ]
    if missing:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=missing,
            warnings=list(catalog.get("warnings") or []),
        )

    executions, executions_error = _existing_items(existing_order_executions_data, "executions")
    locks, locks_error = _existing_items(existing_order_locks_data, "locks")
    malformed_existing = [issue for issue in (executions_error, locks_error) if issue]
    if malformed_existing:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=malformed_existing,
            warnings=list(catalog.get("warnings") or []),
        )

    execution_duplicate = _duplicate_issue(
        executions,
        fields=("execution_id", "request_hash", "order_id"),
        source=catalog,
    )
    if execution_duplicate:
        return _base_result(
            status=STATUS_BLOCKED,
            catalog=catalog,
            issues=[execution_duplicate],
            warnings=list(catalog.get("warnings") or []),
        )

    lock_duplicate = _duplicate_issue(
        locks,
        fields=("lock_id", "request_hash", "order_id"),
        source=catalog,
    )
    if lock_duplicate:
        return _base_result(
            status=STATUS_BLOCKED,
            catalog=catalog,
            issues=[lock_duplicate],
            warnings=list(catalog.get("warnings") or []),
        )

    return _base_result(
        status=STATUS_READY,
        catalog=catalog,
        execution_record_preview=_execution_record(catalog),
        lock_record_preview=_lock_record(catalog),
        issues=[],
        warnings=list(catalog.get("warnings") or []),
    )
