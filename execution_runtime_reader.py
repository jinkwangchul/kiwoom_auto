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

from execution_provenance_contract import validate_process_record

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
    result = _read_runtime_json(path, list_field="executions")
    if result.get("ok") is not True:
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    executions = data.get("executions") or []
    if "processes" not in data:
        result["provenance_status"] = "LEGACY_MISSING"
        return result
    processes = data.get("processes")
    if not isinstance(processes, list) or any(not isinstance(item, dict) for item in processes):
        return _result(
            ok=False,
            status=STATUS_INVALID,
            path=Path(path),
            data=data,
            issues=["processes field must be a list of objects"],
        )

    issues: list[str] = []
    process_by_id: dict[str, dict[str, Any]] = {}
    child_counts: dict[str, int] = {}
    for process in processes:
        process_issues = validate_process_record(process)
        process_id = str(process.get("execution_process_id") or "").strip()
        issues.extend(f"{process_id or 'unknown process'}: {issue}" for issue in process_issues)
        if process_id in process_by_id:
            issues.append(f"duplicate execution_process_id: {process_id}")
        elif process_id:
            process_by_id[process_id] = process

    for execution in executions:
        if not isinstance(execution, dict):
            continue
        process_id = str(execution.get("execution_process_id") or "").strip()
        if not process_id:
            continue
        child_counts[process_id] = child_counts.get(process_id, 0) + 1
        owner = process_by_id.get(process_id)
        if owner is None:
            issues.append(
                f"{execution.get('execution_id') or 'unknown execution'}: execution process owner missing"
            )
            continue
        child_hash = str(execution.get("option_snapshot_hash") or "").strip()
        owner_hash = str(owner.get("option_snapshot_hash") or "").strip()
        if not child_hash or child_hash != owner_hash:
            issues.append(
                f"{execution.get('execution_id') or 'unknown execution'}: option snapshot hash mismatch"
            )
    for process_id in process_by_id:
        if child_counts.get(process_id, 0) == 0:
            issues.append(f"{process_id}: execution process has no child execution")

    if issues:
        return _result(
            ok=False,
            status=STATUS_INVALID,
            path=Path(path),
            data=data,
            issues=issues,
        )
    has_legacy_execution = any(
        isinstance(item, dict)
        and not str(item.get("execution_process_id") or "").strip()
        for item in executions
    )
    result["provenance_status"] = (
        "PARTIAL"
        if processes and has_legacy_execution
        else "COMPLETE"
        if processes
        else "LEGACY_MISSING"
    )
    return result


def read_order_locks(path: str | Path) -> dict[str, Any]:
    """Read order_locks.json from an explicit path without mutation."""
    return _read_runtime_json(path, list_field="locks")
