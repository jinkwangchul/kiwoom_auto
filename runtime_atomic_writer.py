# -*- coding: utf-8 -*-
"""Atomic runtime JSON writer utility for Real Runtime Commit (M6-1).

This module provides a single pure utility for atomic JSON file replacement
used by the Real Runtime Commit architecture. It writes the payload to a temp
file in the same directory, flushes + fsyncs, then atomically replaces the
target via ``os.replace``.

Design boundaries (M6-1 scope):
- It never creates directories. The target parent directory must already exist.
- It never creates backups. Backup creation belongs to Runtime Backup Manager.
- It never rolls back. Rollback belongs to Runtime Rollback Manager.
- It never verifies content after write. Verification belongs to Runtime
  Commit Verifier.
- It never connects to execution/GUI/SendOrder/Chejan/Broker components.
- It must only be exercised against temp/test paths in tests. It must not write
  to protected runtime files (``runtime/*.json``, ``routines/*/rules.json``)
  outside an explicit, gated commit flow owned by Runtime Commit Executor.

The atomic write sequence mirrors the established project pattern
(``execution_runtime_commit_service._write_json_atomic``): temp file with a
unique name, json dump with ``ensure_ascii=False`` + indent, ``os.fsync``,
``os.replace``, and guaranteed temp cleanup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

WRITER_TYPE = "RUNTIME_ATOMIC_WRITER"
STATUS_OK = "OK"
STATUS_ERROR = "ERROR"


def _as_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


def _result(
    *,
    status: str,
    target_path: Path,
    written: bool = False,
    bytes_written: int = 0,
    temp_path: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "writer_type": WRITER_TYPE,
        "status": status,
        "target_path": str(target_path),
        "written": written,
        "bytes_written": bytes_written,
        "temp_path": temp_path,
        "error": error,
    }


def write_json_atomic(path: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    """Atomically replace ``path`` with ``data`` as JSON.

    The payload is serialized to a uniquely named temp file in the same
    directory as ``path``, flushed and fsync'd, then moved into place with
    ``os.replace`` (atomic on the same filesystem). The temp file is always
    removed, even on failure.

    Args:
        path: Target JSON file path (str or Path). Parent must exist.
        data: JSON-serializable dict to write.

    Returns:
        Result dict with keys: writer_type, status (OK/ERROR), target_path,
        written, bytes_written, temp_path, error.
    """
    target = _as_path(path)
    tmp_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    payload_bytes = 0

    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            # Recompute exact bytes written for the result contract.
            try:
                payload_bytes = tmp_path.stat().st_size
            except OSError:
                payload_bytes = 0
        os.replace(tmp_path, target)
        return _result(
            status=STATUS_OK,
            target_path=target,
            written=True,
            bytes_written=payload_bytes,
            temp_path=None,
        )
    except Exception as exc:  # noqa: BLE001 - surface as ERROR result, never raise
        return _result(
            status=STATUS_ERROR,
            target_path=target,
            written=False,
            bytes_written=0,
            temp_path=str(tmp_path),
            error=f"ATOMIC_WRITE_FAILED: {exc}",
        )
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
