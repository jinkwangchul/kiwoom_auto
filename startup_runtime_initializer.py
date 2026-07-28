# -*- coding: utf-8 -*-
"""Safe initialization for a completely pristine startup Runtime.

The initializer owns no trading behavior. It creates the canonical empty
evidence set only when every required file is absent, validates the written
schemas, and leaves partial or damaged Runtime evidence untouched.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from execution_runtime_file_schema import (
    default_broker_holdings_data,
    default_fills_data,
    default_order_executions_data,
    default_order_locks_data,
    default_order_queue_data,
    default_positions_data,
    default_routine_signals_data,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "runtime"

STATUS_INITIALIZED = "INITIALIZED"
STATUS_READY_EXISTING = "READY_EXISTING"
STATUS_BLOCKED_PARTIAL = "BLOCKED_PARTIAL"
STATUS_BLOCKED_INVALID = "BLOCKED_INVALID"
STATUS_ERROR = "ERROR"

_RUNTIME_SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "order_queue.json": (
        "orders",
        default_order_queue_data(),
    ),
    "fills.json": (
        "fills",
        default_fills_data(),
    ),
    "positions.json": (
        "positions",
        default_positions_data(),
    ),
    "broker_holdings.json": (
        "holdings",
        default_broker_holdings_data(),
    ),
    "order_executions.json": (
        "executions",
        default_order_executions_data(),
    ),
    "order_locks.json": (
        "locks",
        default_order_locks_data(),
    ),
    "routine_signals.json": (
        "signals",
        default_routine_signals_data(),
    ),
}


def startup_runtime_paths(runtime_dir: str | Path = DEFAULT_RUNTIME_DIR) -> dict[str, Path]:
    root = Path(runtime_dir)
    return {filename: root / filename for filename in _RUNTIME_SCHEMAS}


def _result(
    status: str,
    *,
    initialized: bool = False,
    created_files: list[str] | None = None,
    read_back_verified: bool = False,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "initializer_type": "PRISTINE_STARTUP_RUNTIME_INITIALIZER",
        "status": status,
        "initialized": initialized,
        "runtime_write": initialized,
        "created_files": list(created_files or []),
        "read_back_verified": read_back_verified,
        "issues": list(issues or []),
    }


def _validate_file(path: Path, list_field: str) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"{path.name} read failed: {exc}"
    if not isinstance(data, dict):
        return f"{path.name} root must be an object"
    items = data.get(list_field)
    if not isinstance(items, list):
        return f"{path.name}.{list_field} must be a list"
    if any(not isinstance(item, dict) for item in items):
        return f"{path.name}.{list_field} entries must be objects"
    return ""


def _write_new_json(path: Path, data: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags)
    try:
        payload = (
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def initialize_pristine_startup_runtime(
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
) -> dict[str, Any]:
    """Create the complete empty evidence set only for a pristine Runtime."""
    root = Path(runtime_dir)
    paths = startup_runtime_paths(root)
    existing = [path for path in paths.values() if path.exists()]

    if existing and len(existing) != len(paths):
        return _result(
            STATUS_BLOCKED_PARTIAL,
            issues=[
                "PARTIAL_RUNTIME_EVIDENCE",
                *[f"existing: {path.name}" for path in existing],
                *[f"missing: {path.name}" for path in paths.values() if not path.exists()],
            ],
        )

    if len(existing) == len(paths):
        issues = [
            issue
            for filename, (list_field, _schema) in _RUNTIME_SCHEMAS.items()
            if (issue := _validate_file(paths[filename], list_field))
        ]
        return _result(
            STATUS_BLOCKED_INVALID if issues else STATUS_READY_EXISTING,
            read_back_verified=not issues,
            issues=issues,
        )

    created_root = False
    created_files: list[Path] = []
    try:
        if not root.exists():
            root.mkdir(parents=True, exist_ok=False)
            created_root = True
        if not root.is_dir():
            return _result(
                STATUS_BLOCKED_INVALID,
                issues=[f"runtime path is not a directory: {root}"],
            )

        for filename, (_list_field, schema) in _RUNTIME_SCHEMAS.items():
            path = paths[filename]
            _write_new_json(path, deepcopy(schema))
            created_files.append(path)

        issues = [
            issue
            for filename, (list_field, _schema) in _RUNTIME_SCHEMAS.items()
            if (issue := _validate_file(paths[filename], list_field))
        ]
        if issues:
            raise RuntimeError("; ".join(issues))
    except Exception as exc:
        for path in reversed(created_files):
            try:
                path.unlink()
            except OSError:
                pass
        if created_root:
            try:
                root.rmdir()
            except OSError:
                pass
        return _result(
            STATUS_ERROR,
            created_files=[str(path) for path in created_files],
            issues=[f"STARTUP_RUNTIME_INITIALIZATION_FAILED: {exc}"],
        )

    return _result(
        STATUS_INITIALIZED,
        initialized=True,
        created_files=[str(path) for path in created_files],
        read_back_verified=True,
    )
