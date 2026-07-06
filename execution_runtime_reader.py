# -*- coding: utf-8 -*-
"""Read-only helpers for future execution runtime files.

All functions require an explicit path and return structured validation results.
They never create directories, write files, commit data, or call execution/order
components.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

STATUS_READY = "READY"
STATUS_MISSING = "MISSING"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"


def _result(
    *,
    ok: bool,
    status: str,
    path: Path,
    data: dict[str, Any] | None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "path": str(path),
        "data": deepcopy(data) if isinstance(data, dict) else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _read_runtime_json(path: str | Path, *, list_field: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return _result(
            ok=False,
            status=STATUS_MISSING,
            path=target,
            data=None,
            issues=[f"{target.name} file not found"],
        )

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return _result(
            ok=False,
            status=STATUS_ERROR,
            path=target,
            data=None,
            issues=[f"failed to read {target.name} json: {exc}"],
        )

    if not isinstance(data, dict):
        return _result(
            ok=False,
            status=STATUS_INVALID,
            path=target,
            data=None,
            issues=[f"{target.name} root must be an object"],
        )

    if list_field not in data:
        return _result(
            ok=False,
            status=STATUS_INVALID,
            path=target,
            data=deepcopy(data),
            issues=[f"{list_field} field is required"],
        )

    if not isinstance(data.get(list_field), list):
        return _result(
            ok=False,
            status=STATUS_INVALID,
            path=target,
            data=deepcopy(data),
            issues=[f"{list_field} field must be a list"],
        )

    return _result(
        ok=True,
        status=STATUS_READY,
        path=target,
        data=deepcopy(data),
    )


def read_order_executions(path: str | Path) -> dict[str, Any]:
    """Read order_executions.json from an explicit path without mutation."""
    return _read_runtime_json(path, list_field="executions")


def read_order_locks(path: str | Path) -> dict[str, Any]:
    """Read order_locks.json from an explicit path without mutation."""
    return _read_runtime_json(path, list_field="locks")
