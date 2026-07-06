# -*- coding: utf-8 -*-
"""Preview-only initializer plan for execution runtime files.

This module only builds an in-memory preview of runtime file initialization.
It never creates files, creates directories, writes runtime data, commits
queues, calls SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)


PREVIEW_TYPE = "EXECUTION_RUNTIME_FILE_INIT_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_SKIPPED = "SKIPPED"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _project_runtime_root() -> Path:
    return (Path(__file__).resolve().parent / "runtime").resolve(strict=False)


def _under_project_runtime(path: Path) -> bool:
    target = path.resolve(strict=False)
    try:
        target.relative_to(_project_runtime_root())
    except ValueError:
        return False
    return True


def _base_result(
    *,
    status: str,
    order_executions_path: Any,
    order_locks_path: Any,
    would_create: list[str] | None = None,
    existing: list[str] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "mkdir_required": False,
        "targets": {
            "order_executions": _text(order_executions_path),
            "order_locks": _text(order_locks_path),
        },
        "schemas": {
            "order_executions": default_order_executions_data(),
            "order_locks": default_order_locks_data(),
        },
        "would_create": list(would_create or []),
        "existing": list(existing or []),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_runtime_file_init_preview(
    order_executions_path: Any,
    order_locks_path: Any,
    *,
    allow_project_runtime_path: bool = False,
) -> dict[str, Any]:
    """Build an initialization preview for execution runtime files."""
    order_executions_text = _text(order_executions_path)
    order_locks_text = _text(order_locks_path)
    if not order_executions_text:
        return _base_result(
            status=STATUS_INVALID,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            issues=["MISSING_ORDER_EXECUTIONS_PATH"],
        )
    if not order_locks_text:
        return _base_result(
            status=STATUS_INVALID,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            issues=["MISSING_ORDER_LOCKS_PATH"],
        )

    try:
        order_executions_target = Path(order_executions_text)
        order_locks_target = Path(order_locks_text)
    except Exception as exc:
        return _base_result(
            status=STATUS_INVALID,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            issues=[f"MALFORMED_PATH: {exc}"],
        )

    if (
        _under_project_runtime(order_executions_target)
        or _under_project_runtime(order_locks_target)
    ) and allow_project_runtime_path is not True:
        return _base_result(
            status=STATUS_BLOCKED,
            order_executions_path=order_executions_target,
            order_locks_path=order_locks_target,
            issues=["PROJECT_RUNTIME_PATH_NOT_ALLOWED"],
        )

    if (
        not order_executions_target.parent.exists()
        or not order_locks_target.parent.exists()
    ):
        return _base_result(
            status=STATUS_BLOCKED,
            order_executions_path=order_executions_target,
            order_locks_path=order_locks_target,
            issues=["PARENT_DIRECTORY_MISSING"],
        )

    order_executions_exists = order_executions_target.exists()
    order_locks_exists = order_locks_target.exists()
    existing: list[str] = []
    would_create: list[str] = []
    if order_executions_exists:
        existing.append("order_executions")
    else:
        would_create.append("order_executions")
    if order_locks_exists:
        existing.append("order_locks")
    else:
        would_create.append("order_locks")

    if order_executions_exists and order_locks_exists:
        return _base_result(
            status=STATUS_SKIPPED,
            order_executions_path=order_executions_target,
            order_locks_path=order_locks_target,
            existing=existing,
            warnings=["RUNTIME_FILES_ALREADY_EXIST"],
        )

    if order_executions_exists != order_locks_exists:
        return _base_result(
            status=STATUS_BLOCKED,
            order_executions_path=order_executions_target,
            order_locks_path=order_locks_target,
            would_create=would_create,
            existing=existing,
            issues=["PARTIAL_RUNTIME_FILES_EXIST"],
        )

    return _base_result(
        status=STATUS_READY,
        order_executions_path=order_executions_target,
        order_locks_path=order_locks_target,
        would_create=would_create,
    )


def copy_preview(preview: Any) -> Any:
    """Return a deep copy for callers that need an immutable-style payload."""
    return deepcopy(preview)
