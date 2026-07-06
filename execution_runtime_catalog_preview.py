# -*- coding: utf-8 -*-
"""Execution Runtime Catalog preview.

This module only validates and groups already-built preview-only execution
objects into an in-memory Runtime Catalog Preview. It never reads or writes
runtime files, creates directories, enqueues orders, commits queue records, or
calls SendOrder/execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
CATALOG_TYPE = "EXECUTION_RUNTIME_CATALOG_PREVIEW"
RUNTIME_TARGETS = {
    "order_executions": "runtime/order_executions.json",
    "order_locks": "runtime/order_locks.json",
}
BASE_WARNINGS = [
    "Preview mode",
    "Runtime write disabled",
    "Runtime catalog preview only",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _flag_unresolved(value: dict[str, Any]) -> bool:
    return value.get("unresolved") is True or value.get("ok") is False


def _queue_write_preview(queue_write_preview_result: dict[str, Any]) -> bool:
    return (
        queue_write_preview_result.get("write_preview") is True
        and queue_write_preview_result.get("preview_only") is True
        and queue_write_preview_result.get("no_write") is True
    )


def _execution_request(execution_request_preview: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(execution_request_preview.get("execution_request"))


def _value(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _checks(
    *,
    execution_request_ok: bool,
    lock_ok: bool,
    hash_ok: bool,
    queue_ok: bool,
    order_ok: bool,
) -> dict[str, str]:
    return {
        "ExecutionRequest": "PASS" if execution_request_ok else "FAIL",
        "LockPreview": "PASS" if lock_ok else "FAIL",
        "RequestHashPreview": "PASS" if hash_ok else "FAIL",
        "QueuePreview": "PASS" if queue_ok else "FAIL",
        "OrderCandidate": "PASS" if order_ok else "FAIL",
        "RuntimeWriteDisabled": "PASS",
    }


def _result(
    *,
    status: str,
    execution_id: str = "",
    order_id: str = "",
    request_hash: str = "",
    lock_id: str = "",
    checks: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "catalog_type": CATALOG_TYPE,
        "execution_id": execution_id or None,
        "order_id": order_id or None,
        "request_hash": request_hash or None,
        "lock_id": lock_id or None,
        "runtime_targets": deepcopy(RUNTIME_TARGETS),
        "checks": deepcopy(checks or {}),
        "warnings": list(warnings or BASE_WARNINGS),
        "issues": list(issues or []),
    }


def build_execution_runtime_catalog_preview(
    *,
    execution_request_preview: Any = None,
    lock_preview: Any = None,
    request_hash_preview: Any = None,
    queue_write_preview_result: Any = None,
    order_candidate: Any = None,
) -> dict[str, Any]:
    """Build a Runtime Catalog Preview without filesystem side effects."""
    execution_preview = _as_dict(execution_request_preview)
    lock = _as_dict(lock_preview)
    request_hash_result = _as_dict(request_hash_preview)
    queue_preview = _as_dict(queue_write_preview_result)
    order = _as_dict(order_candidate)

    if not all(
        isinstance(value, dict)
        for value in (
            execution_request_preview,
            lock_preview,
            request_hash_preview,
            queue_write_preview_result,
            order_candidate,
        )
    ):
        return _result(
            status=STATUS_INVALID,
            checks=_checks(
                execution_request_ok=bool(execution_preview),
                lock_ok=bool(lock),
                hash_ok=bool(request_hash_result),
                queue_ok=bool(queue_preview),
                order_ok=bool(order),
            ),
            issues=["MALFORMED_INPUT"],
        )

    execution_request = _execution_request(execution_preview)
    execution_id = _value(execution_request.get("execution_id"), execution_preview.get("execution_id"))
    order_id = _value(
        execution_request.get("order_id"),
        execution_preview.get("order_id"),
        order.get("id"),
        order.get("order_id"),
    )
    request_hash = _value(
        execution_request.get("request_hash"),
        execution_preview.get("request_hash"),
        request_hash_result.get("request_hash"),
    )
    lock_id = _value(
        execution_request.get("lock_id"),
        execution_preview.get("lock_id"),
        lock.get("lock_id"),
    )

    lock_unresolved = _flag_unresolved(lock)
    hash_unresolved = _flag_unresolved(request_hash_result)
    execution_unresolved = _flag_unresolved(execution_preview)
    queue_ok = _queue_write_preview(queue_preview)
    order_ok = bool(order)
    execution_request_ok = bool(execution_request) and not execution_unresolved
    lock_ok = bool(lock_id) and not lock_unresolved
    hash_ok = bool(request_hash) and not hash_unresolved

    checks = _checks(
        execution_request_ok=execution_request_ok,
        lock_ok=lock_ok,
        hash_ok=hash_ok,
        queue_ok=queue_ok,
        order_ok=order_ok,
    )

    missing_issues: list[str] = []
    if not execution_id:
        missing_issues.append("MISSING_EXECUTION_ID")
    if not order_id:
        missing_issues.append("MISSING_ORDER_ID")
    if not request_hash:
        missing_issues.append("MISSING_REQUEST_HASH")
    if not lock_id:
        missing_issues.append("MISSING_LOCK_ID")
    if missing_issues and not (lock_unresolved or hash_unresolved or execution_unresolved):
        return _result(
            status=STATUS_INVALID,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            checks=checks,
            issues=missing_issues,
        )

    blocked_issues: list[str] = []
    if execution_unresolved:
        blocked_issues.append("EXECUTION_REQUEST_UNRESOLVED")
    if hash_unresolved:
        blocked_issues.append("REQUEST_HASH_UNRESOLVED")
    if lock_unresolved:
        blocked_issues.append("LOCK_UNRESOLVED")
    if not queue_ok:
        blocked_issues.append("QUEUE_PREVIEW_UNAVAILABLE")
    if blocked_issues:
        return _result(
            status=STATUS_BLOCKED,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            checks=checks,
            issues=blocked_issues,
        )

    if not order_ok:
        return _result(
            status=STATUS_INVALID,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            checks=checks,
            issues=["MALFORMED_INPUT"],
        )

    return _result(
        status=STATUS_READY,
        execution_id=execution_id,
        order_id=order_id,
        request_hash=request_hash,
        lock_id=lock_id,
        checks=checks,
    )
