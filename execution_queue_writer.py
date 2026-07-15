# -*- coding: utf-8 -*-
"""Dry-run execution queue writer preview.

This module only builds an ORDER_QUEUED record preview in memory. It never reads
or writes runtime/order_queue.json, never persists ORDER_QUEUED, and never calls
SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_QUEUE_WRITE_REQUIRED = "QUEUE_WRITE_REQUIRED"
NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED = "QUEUE_COMMITTED_REVIEW_REQUIRED"
NEXT_STAGE_DISPATCH_CLAIM_REVIEW_REQUIRED = "DISPATCH_CLAIM_REVIEW_REQUIRED"

_QUEUE_THREAD_LOCK = threading.RLock()
_DEFAULT_LOCK_TIMEOUT_SEC = 5.0
_LOCK_POLL_SEC = 0.025
_DEFAULT_DISPATCH_CLAIM_TTL_SEC = 60
_MAX_DISPATCH_CLAIM_TTL_SEC = 300


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "write_preview": False,
        "write_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [reason],
        "order_queued_record_preview": None,
    }


def _commit_blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "committed": False,
        "write_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _post_write_failed_result(stage: str, reason: str, *, order_queue_path: str, backup_path: str | None = None) -> dict[str, Any]:
    return {
        "committed": True,
        "write_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": True,
        "order_queue_path": order_queue_path,
        "backup_path": backup_path,
        "file_write": True,
        "queue_write": True,
        "queue_committed": True,
        "post_write_verified": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _queue_metadata(
    *,
    revision_before: int | None = None,
    revision_after: int | None = None,
    expected_revision: int | None = None,
    cas_checked: bool = False,
    lock_acquired: bool = False,
    lock_wait_ms: int = 0,
) -> dict[str, Any]:
    return {
        "committed": False,
        "changed": False,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "expected_revision": expected_revision,
        "cas_checked": cas_checked,
        "lock_acquired": lock_acquired,
        "lock_wait_ms": lock_wait_ms,
        "file_write": False,
        "queue_write": False,
        "queue_committed": False,
        "post_write_verified": False,
    }


def _with_queue_metadata(result: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    updated = _queue_metadata(**metadata)
    updated.update(result)
    return updated


_QUEUE_MUTATION_RESULT_FIELDS = (
    "committed",
    "changed",
    "file_write",
    "queue_write",
    "queue_committed",
    "post_write_verified",
    "revision_before",
    "revision_after",
    "lock_acquired",
    "cas_checked",
)


def preserve_queue_mutation_result(result: dict[str, Any], mutation_result: Any) -> dict[str, Any]:
    """Preserve canonical mutation facts when an adapter adds business status."""
    merged = deepcopy(result)
    canonical = _as_dict(mutation_result)
    for field in _QUEUE_MUTATION_RESULT_FIELDS:
        merged[field] = deepcopy(canonical.get(field))
    return merged


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _time_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _parse_time_text(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(_clean_text(value).encode("utf-8")).hexdigest()


def _order_queued_id(order_id: str, request_hash: str) -> str:
    source = order_id or request_hash
    return f"ORDER_QUEUED_{source}"


def _existing_duplicate_reason(
    existing_orders: Any,
    *,
    request_hash: str,
    lock_id: str,
    order_id: str,
) -> str | None:
    for order in _as_list(existing_orders):
        item = _as_dict(order)
        if _clean_text(item.get("request_hash")) == request_hash:
            return "duplicate request_hash"

    for order in _as_list(existing_orders):
        item = _as_dict(order)
        if _clean_text(item.get("lock_id")) == lock_id:
            return "duplicate lock_id"

    for order in _as_list(existing_orders):
        item = _as_dict(order)
        if _clean_text(item.get("order_id")) == order_id:
            return "duplicate order_id"

    return None


def _manual_write_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_queue_write_confirmed") is True
        or ctx.get("operator_confirmed_for_queue_write") is True
    )


def _lock_timeout_sec(context: Any) -> float:
    ctx = _as_dict(context)
    value = ctx.get("queue_lock_timeout_sec", ctx.get("lock_timeout_sec", _DEFAULT_LOCK_TIMEOUT_SEC))
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_LOCK_TIMEOUT_SEC
    return max(0.0, timeout)


def _normalize_revision(data: dict[str, Any]) -> int:
    revision = data.get("revision", 0)
    if isinstance(revision, bool):
        revision = 0
    if not isinstance(revision, int):
        revision = 0
    data["revision"] = revision
    return revision


def _cas_blocked(current_revision: int, expected_revision: int | None) -> dict[str, Any] | None:
    if expected_revision is None:
        return None
    if current_revision != expected_revision:
        return _commit_blocked("revision_cas", "queue revision changed after preview; rerun queue preview")
    return None


class _QueueFileLock:
    def __init__(self, queue_path: Path, timeout_sec: float) -> None:
        self.lock_path = queue_path.with_name(f"{queue_path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_QueueFileLock":
        start = time.monotonic()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - start) * 1000)
                return self
            except OSError:
                if time.monotonic() - start >= self.timeout_sec:
                    self.wait_ms = int((time.monotonic() - start) * 1000)
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("queue lock timeout")
                time.sleep(_LOCK_POLL_SEC)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


def _validate_write_preview(queue_write_preview_result: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    preview = _as_dict(queue_write_preview_result)
    if preview.get("write_preview") is not True:
        return preview, {}, _commit_blocked("write_preview", "queue_write_preview_result.write_preview is not true")

    if preview.get("write_stage") != "order_queued_record_preview_created":
        return preview, {}, _commit_blocked(
            "write_preview",
            "queue_write_preview_result.write_stage is not order_queued_record_preview_created",
        )

    if preview.get("next_stage") != NEXT_STAGE_QUEUE_WRITE_REQUIRED:
        return preview, {}, _commit_blocked(
            "write_preview",
            "queue_write_preview_result.next_stage is not QUEUE_WRITE_REQUIRED",
        )

    if preview.get("preview_only") is not True:
        return preview, {}, _commit_blocked("write_preview", "queue_write_preview_result.preview_only is not true")

    if preview.get("no_write") is not True:
        return preview, {}, _commit_blocked("write_preview", "queue_write_preview_result.no_write is not true")

    record = _as_dict(preview.get("order_queued_record_preview"))
    if not record:
        return preview, {}, _commit_blocked("write_preview", "order_queued_record_preview is required")

    if record.get("status") != "ORDER_QUEUED":
        return preview, record, _commit_blocked("write_preview", "record.status is not ORDER_QUEUED")

    if record.get("send_order_called") is not False:
        return preview, record, _commit_blocked("write_preview", "record.send_order_called is not false")

    if record.get("execution_enabled") is not False:
        return preview, record, _commit_blocked("write_preview", "record.execution_enabled is not false")

    required_fields = [
        "id",
        "source",
        "source_signal_id",
        "order_id",
        "candidate_id",
        "queue_pending_id",
        "request_hash",
        "lock_id",
        "execution_id",
        "execution_request",
        "queue_contract_version",
    ]
    for field in required_fields:
        value = record.get(field)
        if field == "execution_request":
            if not isinstance(value, dict) or not value:
                return preview, record, _commit_blocked("write_preview", "record.execution_request is required")
            continue
        if not _clean_text(value):
            return preview, record, _commit_blocked("write_preview", f"record.{field} is required")

    return preview, record, None


def _read_queue_file(queue_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not queue_path.exists():
        return {}, _commit_blocked("read_queue", "queue file does not exist")

    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _commit_blocked("read_queue", f"failed to read order_queue json: {exc}")

    if not isinstance(data, dict):
        return {}, _commit_blocked("read_queue", "order_queue root must be an object")

    version = data.get("version", 1)
    if not isinstance(version, int):
        return {}, _commit_blocked("read_queue", "order_queue version must be an integer")
    _normalize_revision(data)

    orders = data.get("orders")
    if not isinstance(orders, list):
        return {}, _commit_blocked("read_queue", "order_queue orders must be a list")

    for item in orders:
        if not isinstance(item, dict):
            return {}, _commit_blocked("read_queue", "order_queue orders must contain only objects")

    return data, None


def _write_json_atomic(queue_path: Path, data: dict[str, Any]) -> str:
    tmp_path = queue_path.with_name(f".{queue_path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, queue_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return str(tmp_path)


def mutate_order_queue(
    queue_path: str | Path,
    mutator: Any,
    *,
    operation_name: str,
    success_stage: str,
    next_stage: str,
    backup: bool = True,
    context: Any = None,
    expected_revision: int | None = None,
    verify: Any = None,
    default_queue: Any = None,
) -> dict[str, Any]:
    """Run a canonical locked queue mutation.

    ``mutator`` is called while both the in-process and process file locks are
    held. It receives a normalized queue dict and must return either
    ``{"data": updated_queue, "result": {...}}`` or ``{"blocked": {...}}``.
    """
    target_path = Path(queue_path)
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                data, read_blocked = _read_queue_file(target_path)
                if read_blocked is not None and not target_path.exists() and isinstance(default_queue, dict):
                    data = deepcopy(default_queue)
                    if not isinstance(data.get("orders"), list):
                        data["orders"] = []
                    data["version"] = data.get("version", 1)
                    data["updated_at"] = data.get("updated_at", "")
                    _normalize_revision(data)
                    read_blocked = None
                if read_blocked is not None:
                    return _with_queue_metadata(
                        read_blocked,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                revision_before = _normalize_revision(data)
                cas_blocked = _cas_blocked(revision_before, expected_revision)
                if cas_blocked is not None:
                    return _with_queue_metadata(
                        cas_blocked,
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=True,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                mutation = mutator(deepcopy(data))
                if not isinstance(mutation, dict):
                    return _with_queue_metadata(
                        _commit_blocked(operation_name, "queue mutation result must be a dict"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                blocked = mutation.get("blocked")
                if isinstance(blocked, dict):
                    return _with_queue_metadata(
                        blocked,
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                updated_data = mutation.get("data")
                if not isinstance(updated_data, dict):
                    return _with_queue_metadata(
                        _commit_blocked(operation_name, "queue mutation data must be a dict"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                orders = updated_data.get("orders")
                if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
                    return _with_queue_metadata(
                        _commit_blocked(operation_name, "mutated order_queue orders must contain only objects"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                backup_path = None
                if backup and target_path.exists():
                    backup_path = str(target_path) + ".bak"
                    try:
                        shutil.copy2(target_path, backup_path)
                    except Exception as exc:
                        return _with_queue_metadata(
                            _commit_blocked("backup", f"failed to create backup: {exc}"),
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                revision_after = revision_before + 1
                updated_data["version"] = updated_data.get("version", 1)
                updated_data["revision"] = revision_after
                updated_data["updated_at"] = _now_text()

                try:
                    temp_path = _write_json_atomic(target_path, updated_data)
                except Exception as exc:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("write_queue", f"failed to write order_queue json: {exc}"),
                            "order_queue_path": str(target_path),
                            "backup_path": backup_path,
                            "file_write": False,
                            "post_write_verified": False,
                            "queue_write": False,
                            "queue_committed": False,
                        },
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_data, after_blocked = _read_queue_file(target_path)
                if after_blocked is not None:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                after_blocked.get("write_stage", "post_write_verify"),
                                after_blocked.get("blocked_reasons", ["post-write queue read failed"])[0],
                                order_queue_path=str(target_path),
                                backup_path=backup_path,
                            ),
                            "operation_name": operation_name,
                            "temp_path": temp_path,
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if _normalize_revision(after_data) != revision_after:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                "post_write_verify",
                                "order_queue revision did not advance as expected",
                                order_queue_path=str(target_path),
                                backup_path=backup_path,
                            ),
                            "operation_name": operation_name,
                            "temp_path": temp_path,
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                if callable(verify):
                    verify_blocked = verify(after_data, mutation)
                    if isinstance(verify_blocked, dict):
                        return _with_queue_metadata(
                            {
                                **_post_write_failed_result(
                                    verify_blocked.get("write_stage", "post_write_verify"),
                                    verify_blocked.get("blocked_reasons", ["post-write queue verification failed"])[0],
                                    order_queue_path=str(target_path),
                                    backup_path=backup_path,
                                ),
                                "operation_name": operation_name,
                                "temp_path": temp_path,
                            },
                            revision_before=revision_before,
                            revision_after=revision_after,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                result = {
                    "committed": True,
                    "operation_name": operation_name,
                    "write_stage": success_stage,
                    "next_stage": next_stage,
                    "changed": True,
                    "order_queue_path": str(target_path),
                    "backup_path": backup_path,
                    "temp_path": temp_path,
                    "file_write": True,
                    "queue_write": True,
                    "queue_committed": True,
                    "post_write_verified": True,
                    "blocked_reasons": [],
                    "warnings": [],
                }
                extra = mutation.get("result")
                if isinstance(extra, dict):
                    result.update(deepcopy(extra))
                return _with_queue_metadata(
                    result,
                    revision_before=revision_before,
                    revision_after=revision_after,
                    expected_revision=expected_revision,
                    cas_checked=expected_revision is not None,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(
            _commit_blocked("queue_lock", "queue lock timeout"),
            expected_revision=expected_revision,
            cas_checked=expected_revision is not None,
            lock_acquired=False,
        )

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"), expected_revision=expected_revision)


def preview_execution_queue_write(
    queue_pending_result: Any,
    existing_orders: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Build an ORDER_QUEUED record preview without reading or writing files."""
    pending = _as_dict(queue_pending_result)

    if pending.get("queue_pending") is not True:
        return _blocked("queue_pending", "queue_pending_result.queue_pending is not true")

    if pending.get("queue_pending_stage") != "queue_pending_created":
        return _blocked("queue_pending", "queue_pending_stage is not queue_pending_created")

    if pending.get("next_stage") != "QUEUE_WRITER_REQUIRED":
        return _blocked("queue_pending", "queue_pending_result.next_stage is not QUEUE_WRITER_REQUIRED")

    if pending.get("preview_only") is not True:
        return _blocked("queue_pending", "queue_pending_result.preview_only is not true")

    if pending.get("no_write") is not True:
        return _blocked("queue_pending", "queue_pending_result.no_write is not true")

    queue_pending_id = _clean_text(pending.get("queue_pending_id"))
    candidate_id = _clean_text(pending.get("created_from_candidate_id"))
    order_id = _clean_text(pending.get("order_id"))
    source_signal_id = _clean_text(pending.get("source_signal_id"))
    request_hash_preview = _clean_text(pending.get("request_hash_preview"))
    lock_preview = _as_dict(pending.get("lock_preview"))
    execution_request_preview = _as_dict(pending.get("execution_request_preview"))
    execution_request = _as_dict(execution_request_preview.get("execution_request"))

    if not queue_pending_id:
        return _blocked("queue_pending", "queue_pending_id is required")

    if not candidate_id:
        return _blocked("queue_pending", "created_from_candidate_id is required")

    if not order_id:
        return _blocked("queue_pending", "order_id is required")

    if not source_signal_id:
        return _blocked("queue_pending", "source_signal_id is required")

    if not request_hash_preview:
        return _blocked("queue_pending", "request_hash_preview is required")

    lock_id = _clean_text(lock_preview.get("lock_id"))
    if not lock_id:
        return _blocked("queue_pending", "lock_preview.lock_id is required")

    if not execution_request:
        return _blocked("queue_pending", "execution_request_preview.execution_request is required")

    execution_id = _clean_text(execution_request.get("execution_id"))
    if not execution_id:
        return _blocked("queue_pending", "execution_request.execution_id is required")

    request_hash = _clean_text(execution_request.get("request_hash"))
    if not request_hash:
        return _blocked("queue_pending", "execution_request.request_hash is required")

    execution_lock_id = _clean_text(execution_request.get("lock_id"))
    if not execution_lock_id:
        return _blocked("queue_pending", "execution_request.lock_id is required")

    duplicate_reason = _existing_duplicate_reason(
        existing_orders,
        request_hash=request_hash,
        lock_id=lock_id,
        order_id=order_id,
    )
    if duplicate_reason:
        return _blocked("duplicate", duplicate_reason)

    return {
        "write_preview": True,
        "write_stage": "order_queued_record_preview_created",
        "next_stage": NEXT_STAGE_QUEUE_WRITE_REQUIRED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "order_queued_record_preview": {
            "id": _order_queued_id(order_id, request_hash),
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": source_signal_id,
            "order_id": order_id,
            "candidate_id": candidate_id,
            "queue_pending_id": queue_pending_id,
            "request_hash": request_hash,
            "lock_id": execution_lock_id,
            "execution_id": execution_id,
            "execution_request": deepcopy(execution_request),
            "queue_contract_version": _clean_text(pending.get("queue_contract_version")) or "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        },
    }


def commit_execution_queue_write(
    queue_write_preview_result: Any,
    queue_path: str | Path,
    backup: bool = True,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Commit an ORDER_QUEUED record preview to the explicit queue_path only."""
    if not _manual_write_confirmed(context):
        return _with_queue_metadata(
            _commit_blocked("manual_confirm", "manual queue write confirmation is required"),
            expected_revision=expected_revision,
        )

    preview, record, blocked = _validate_write_preview(queue_write_preview_result)
    if blocked is not None:
        return _with_queue_metadata(blocked, expected_revision=expected_revision)

    target_path = Path(queue_path)
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                data, read_blocked = _read_queue_file(target_path)
                if read_blocked is not None:
                    return _with_queue_metadata(
                        read_blocked,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                revision_before = _normalize_revision(data)
                cas_blocked = _cas_blocked(revision_before, expected_revision)
                if cas_blocked is not None:
                    return _with_queue_metadata(
                        cas_blocked,
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=True,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                orders = data["orders"]
                duplicate_reason = _existing_duplicate_reason(
                    orders,
                    request_hash=_clean_text(record.get("request_hash")),
                    lock_id=_clean_text(record.get("lock_id")),
                    order_id=_clean_text(record.get("order_id")),
                )
                if duplicate_reason:
                    return _with_queue_metadata(
                        _commit_blocked("duplicate", duplicate_reason),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                backup_path = None
                if backup:
                    backup_path = str(target_path) + ".bak"
                    try:
                        shutil.copy2(target_path, backup_path)
                    except Exception as exc:
                        return _with_queue_metadata(
                            _commit_blocked("backup", f"failed to create backup: {exc}"),
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                revision_after = revision_before + 1
                updated_data = deepcopy(data)
                updated_data["version"] = updated_data.get("version", 1)
                updated_data["revision"] = revision_after
                updated_data["updated_at"] = _now_text()
                updated_data["orders"].append(deepcopy(record))

                try:
                    _write_json_atomic(target_path, updated_data)
                except Exception as exc:
                    return _with_queue_metadata(
                        _commit_blocked("write_queue", f"failed to write order_queue json: {exc}"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_data, after_blocked = _read_queue_file(target_path)
                if after_blocked is not None:
                    return _with_queue_metadata(
                        _post_write_failed_result(
                            after_blocked.get("write_stage", "post_write_verify"),
                            after_blocked.get("blocked_reasons", ["post-write queue read failed"])[0],
                            order_queue_path=str(target_path),
                            backup_path=backup_path,
                        ),
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if _normalize_revision(after_data) != revision_after:
                    return _with_queue_metadata(
                        _post_write_failed_result(
                            "post_write_verify",
                            "order_queue revision did not advance as expected",
                            order_queue_path=str(target_path),
                            backup_path=backup_path,
                        ),
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                return _with_queue_metadata(
                    {
                        "committed": True,
                        "write_stage": "order_queued_record_committed",
                        "next_stage": NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED,
                        "changed": True,
                        "order_queue_path": str(target_path),
                        "backup_path": backup_path,
                        "order_id": record.get("order_id"),
                        "order_queued_id": record.get("id"),
                        "request_hash": record.get("request_hash"),
                        "lock_id": record.get("lock_id"),
                        "status": record.get("status"),
                        "send_order_called": False,
                        "execution_enabled": False,
                        "file_write": True,
                        "queue_write": True,
                        "queue_committed": True,
                        "post_write_verified": True,
                        "blocked_reasons": [],
                        "warnings": [],
                    },
                    revision_before=revision_before,
                    revision_after=revision_after,
                    expected_revision=expected_revision,
                    cas_checked=expected_revision is not None,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(
            _commit_blocked("queue_lock", "queue lock timeout"),
            expected_revision=expected_revision,
            cas_checked=expected_revision is not None,
            lock_acquired=False,
        )

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"), expected_revision=expected_revision)


def commit_execution_queue_write_batch(
    queue_write_preview_results: Any,
    queue_path: str | Path,
    *,
    backup: bool = True,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically commit multiple ORDER_QUEUED record previews to one queue file."""
    if not _manual_write_confirmed(context):
        return _with_queue_metadata(
            _commit_blocked("manual_confirm", "manual queue write confirmation is required"),
            expected_revision=expected_revision,
        )

    if not isinstance(queue_write_preview_results, list) or not queue_write_preview_results:
        return _with_queue_metadata(
            _commit_blocked("write_preview", "queue_write_preview_results must be a non-empty list"),
            expected_revision=expected_revision,
        )

    previews: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for item in queue_write_preview_results:
        preview, record, blocked = _validate_write_preview(item)
        if blocked is not None:
            return _with_queue_metadata(blocked, expected_revision=expected_revision)
        previews.append(preview)
        records.append(deepcopy(record))

    duplicate_reason = _duplicate_record_reason(records)
    if duplicate_reason:
        return _with_queue_metadata(_commit_blocked("duplicate", duplicate_reason), expected_revision=expected_revision)

    target_path = Path(queue_path)
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                data, read_blocked = _read_queue_file(target_path)
                if read_blocked is not None:
                    return _with_queue_metadata(
                        read_blocked,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                revision_before = _normalize_revision(data)
                cas_blocked = _cas_blocked(revision_before, expected_revision)
                if cas_blocked is not None:
                    return _with_queue_metadata(
                        cas_blocked,
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=True,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                for record in records:
                    existing_duplicate_reason = _existing_batch_duplicate_reason(data["orders"], record)
                    if existing_duplicate_reason:
                        return _with_queue_metadata(
                            _commit_blocked("duplicate", existing_duplicate_reason),
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                backup_path = None
                if backup:
                    backup_path = str(target_path) + ".bak"
                    try:
                        shutil.copy2(target_path, backup_path)
                    except Exception as exc:
                        return _with_queue_metadata(
                            _commit_blocked("backup", f"failed to create backup: {exc}"),
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                revision_after = revision_before + 1
                updated_data = deepcopy(data)
                updated_data["version"] = updated_data.get("version", 1)
                updated_data["revision"] = revision_after
                updated_data["updated_at"] = _now_text()
                updated_data["orders"].extend(deepcopy(records))

                try:
                    temp_path = _write_json_atomic(target_path, updated_data)
                except Exception as exc:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("write_queue", f"failed to write order_queue json: {exc}"),
                            "order_queue_path": str(target_path),
                            "backup_path": backup_path,
                            "file_write": False,
                            "post_write_verified": False,
                            "queue_write": False,
                            "queue_committed": False,
                            "committed_count": 0,
                        },
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_data, after_blocked = _read_queue_file(target_path)
                if after_blocked is not None:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                after_blocked.get("write_stage", "post_write_verify"),
                                after_blocked.get("blocked_reasons", ["post-write queue read failed"])[0],
                                order_queue_path=str(target_path),
                                backup_path=backup_path,
                            ),
                            "committed_count": len(records),
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if _normalize_revision(after_data) != revision_after:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                "post_write_verify",
                                "order_queue revision did not advance as expected",
                                order_queue_path=str(target_path),
                                backup_path=backup_path,
                            ),
                            "committed_count": len(records),
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                return _with_queue_metadata(
                    {
                        "committed": True,
                        "committed_count": len(records),
                        "write_stage": "order_queued_records_committed",
                        "next_stage": NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED,
                        "changed": True,
                        "order_queue_path": str(target_path),
                        "backup_path": backup_path,
                        "temp_path": temp_path,
                        "committed_records": deepcopy(records),
                        "order_ids": [record.get("order_id") for record in records],
                        "order_queued_ids": [record.get("id") for record in records],
                        "request_hashes": [record.get("request_hash") for record in records],
                        "lock_ids": [record.get("lock_id") for record in records],
                        "execution_ids": [record.get("execution_id") for record in records],
                        "send_order_called": False,
                        "execution_enabled": False,
                        "file_write": True,
                        "queue_write": True,
                        "queue_committed": True,
                        "post_write_verified": True,
                        "blocked_reasons": [],
                        "warnings": [],
                    },
                    revision_before=revision_before,
                    revision_after=revision_after,
                    expected_revision=expected_revision,
                    cas_checked=expected_revision is not None,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(
            _commit_blocked("queue_lock", "queue lock timeout"),
            expected_revision=expected_revision,
            cas_checked=expected_revision is not None,
            lock_acquired=False,
        )

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"), expected_revision=expected_revision)


def commit_legacy_order_queued_record(
    record: Any,
    queue_path: str | Path,
    *,
    backup: bool = True,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Commit a legacy ORDER_QUEUED record through the canonical lock/CAS path."""
    item = _as_dict(record)
    if not item:
        return _with_queue_metadata(_commit_blocked("legacy_record", "record must be a dict"), expected_revision=expected_revision)
    if item.get("status") != "ORDER_QUEUED":
        return _with_queue_metadata(_commit_blocked("legacy_record", "record.status is not ORDER_QUEUED"), expected_revision=expected_revision)
    if item.get("send_order_called") is not False:
        return _with_queue_metadata(_commit_blocked("legacy_record", "record.send_order_called is not false"), expected_revision=expected_revision)
    if item.get("execution_enabled") is not False:
        return _with_queue_metadata(_commit_blocked("legacy_record", "record.execution_enabled is not false"), expected_revision=expected_revision)
    if not _clean_text(item.get("order_id")):
        return _with_queue_metadata(_commit_blocked("legacy_record", "record.order_id is required"), expected_revision=expected_revision)

    canonical_record = deepcopy(item)

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        duplicate_reason = _existing_duplicate_reason(
            data.get("orders", []),
            request_hash=_clean_text(canonical_record.get("request_hash")),
            lock_id=_clean_text(canonical_record.get("lock_id")),
            order_id=_clean_text(canonical_record.get("order_id")),
        )
        if duplicate_reason:
            return {"blocked": _commit_blocked("duplicate", duplicate_reason)}
        updated_data = deepcopy(data)
        updated_data["orders"].append(deepcopy(canonical_record))
        return {"data": updated_data}

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="legacy_order_queued_commit",
        success_stage="order_queued_record_committed",
        next_stage=NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED,
        backup=backup,
        context=context,
        expected_revision=expected_revision,
    )
    if result.get("committed") is True:
        result.setdefault("order_id", canonical_record.get("order_id"))
        result.setdefault("order_queued_id", canonical_record.get("id"))
        result.setdefault("request_hash", canonical_record.get("request_hash"))
        result.setdefault("lock_id", canonical_record.get("lock_id"))
        result.setdefault("status", canonical_record.get("status"))
        result.setdefault("send_order_called", False)
        result.setdefault("execution_enabled", False)
    return result


_DISPATCH_CLAIM_IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
    "source_signal_id",
)


_DISPATCH_CLAIM_BLOCKED_STATUSES = {
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_CALL_REJECTED",
    "SEND_UNCERTAIN",
    "BROKER_ACCEPTED",
    "BROKER_REJECTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "BLOCKED",
    "INVALID",
}


def _dispatch_identity(identity: Any) -> tuple[dict[str, str], dict[str, Any] | None]:
    source = _as_dict(identity)
    if not source:
        return {}, _commit_blocked("dispatch_identity", "dispatch identity must be a dict")

    normalized = {field: _clean_text(source.get(field)) for field in _DISPATCH_CLAIM_IDENTITY_FIELDS}
    order_queued_id = _clean_text(source.get("order_queued_id") or source.get("id"))
    if not order_queued_id:
        return {}, _commit_blocked("dispatch_identity", "dispatch identity order_queued_id is required")
    normalized["order_queued_id"] = order_queued_id

    missing = [field for field, value in normalized.items() if not value]
    if missing:
        return normalized, _commit_blocked("dispatch_identity", f"dispatch identity {missing[0]} is required")
    return normalized, None


def _dispatch_identity_matches(record: dict[str, Any], identity: dict[str, str]) -> bool:
    if _clean_text(record.get("id") or record.get("order_queued_id")) != identity.get("order_queued_id"):
        return False
    return all(_clean_text(record.get(field)) == identity.get(field) for field in _DISPATCH_CLAIM_IDENTITY_FIELDS)


def _dispatch_identity_matches_without_queued_id(record: dict[str, Any], identity: dict[str, str]) -> bool:
    return all(_clean_text(record.get(field)) == identity.get(field) for field in _DISPATCH_CLAIM_IDENTITY_FIELDS)


def _dispatch_matching_records(queue_data: dict[str, Any], identity: dict[str, str]) -> list[dict[str, Any]]:
    return [
        item
        for item in _as_list(queue_data.get("orders"))
        if isinstance(item, dict) and _dispatch_identity_matches(item, identity)
    ]


def _dispatch_claim_ttl(context: Any) -> tuple[int, dict[str, Any] | None]:
    ctx = _as_dict(context)
    raw_value = ctx.get("dispatch_claim_ttl_sec", ctx.get("claim_ttl_sec", _DEFAULT_DISPATCH_CLAIM_TTL_SEC))
    try:
        ttl = int(raw_value)
    except (TypeError, ValueError):
        return 0, _commit_blocked("dispatch_claim_ttl", "dispatch claim ttl must be an integer")
    if ttl <= 0:
        return ttl, _commit_blocked("dispatch_claim_ttl", "dispatch claim ttl must be greater than zero")
    if ttl > _MAX_DISPATCH_CLAIM_TTL_SEC:
        return ttl, _commit_blocked("dispatch_claim_ttl", "dispatch claim ttl exceeds maximum")
    return ttl, None


def _dispatch_claim_source(context: Any) -> str:
    ctx = _as_dict(context)
    return _clean_text(ctx.get("dispatch_claim_source") or ctx.get("claim_source") or "final_guard")


def _dispatch_final_guard_ready(
    final_guard_result: Any,
    identity: dict[str, str],
    context: Any,
    expected_revision: int,
) -> dict[str, Any] | None:
    guard = _as_dict(final_guard_result)
    ctx = _as_dict(context)
    if not guard:
        return _commit_blocked("final_guard", "final guard result is required")
    if guard.get("guard_type") != "SELL_DISPATCH_FINAL_EXECUTION_GUARD":
        return _commit_blocked("final_guard", "final guard type mismatch")
    if guard.get("status") != "READY" or guard.get("final_guard_ready") is not True:
        return _commit_blocked("final_guard", "final guard must be READY")

    token_hash = _clean_text(guard.get("approval_token_hash"))
    approval_token = _clean_text(_as_dict(context).get("approval_token") or _as_dict(context).get("dispatch_approval_token"))
    if token_hash:
        if not approval_token:
            return _commit_blocked("approval_token", "approval token is required for dispatch claim")
        if _sha256_text(approval_token) != token_hash:
            return _commit_blocked("approval_token", "approval token hash mismatch")

    queue_path = _clean_text(_as_dict(context).get("queue_path"))
    if queue_path and _clean_text(guard.get("queue_path")) and queue_path != _clean_text(guard.get("queue_path")):
        return _commit_blocked("final_guard", "final guard queue_path mismatch")

    guard_revision = guard.get("queue_revision", guard.get("source_queue_revision"))
    if guard_revision is None:
        guard_revision = _as_dict(guard.get("summary")).get("queue_revision")
    if guard_revision is None:
        guard_revision = ctx.get("final_guard_queue_revision")
    if guard_revision is not None:
        try:
            normalized_guard_revision = int(guard_revision)
        except (TypeError, ValueError):
            return _commit_blocked("final_guard", "final guard queue_revision must be an integer")
        if normalized_guard_revision != expected_revision:
            return _commit_blocked("final_guard", "final guard queue revision is stale")

    guard_snapshot_hash = _clean_text(
        guard.get("queue_snapshot_hash")
        or guard.get("source_queue_snapshot_hash")
        or _as_dict(guard.get("summary")).get("queue_snapshot_hash")
    )
    context_snapshot_hash = _clean_text(ctx.get("queue_snapshot_hash") or ctx.get("final_guard_queue_snapshot_hash"))
    if guard_snapshot_hash and context_snapshot_hash and guard_snapshot_hash != context_snapshot_hash:
        return _commit_blocked("final_guard", "final guard queue snapshot hash mismatch")

    guarded = _as_list(guard.get("guarded_candidates"))
    if guarded:
        for item in guarded:
            candidate = _as_dict(_as_dict(item).get("candidate"))
            queue_record = _as_dict(_as_dict(item).get("queue_record"))
            if _dispatch_identity_matches_without_queued_id(candidate, identity) or _dispatch_identity_matches(queue_record, identity):
                return None
        return _commit_blocked("final_guard", "final guard does not contain target identity")
    return None


def _dispatch_claim_record_blocked(record: dict[str, Any], *, allow_release: bool = False) -> dict[str, Any] | None:
    status = _clean_text(record.get("status"))
    if allow_release:
        if status != "DISPATCH_CLAIMED":
            return _commit_blocked("dispatch_claim", "target record is not DISPATCH_CLAIMED")
    elif status != "ORDER_QUEUED":
        stage = "stale_dispatch_claim" if status == "DISPATCH_CLAIMED" else "dispatch_claim"
        return _commit_blocked(stage, f"target record status is {status or 'missing'}")

    if not allow_release:
        if record.get("execution_enabled") is not True:
            return _commit_blocked("dispatch_claim", "target record execution_enabled is not true")
        if record.get("send_order_called") is not False:
            return _commit_blocked("dispatch_claim", "target record send_order_called is not false")
        if _clean_text(record.get("broker_order_no")):
            return _commit_blocked("dispatch_claim", "target record already has broker_order_no")
        if record.get("dispatch_claimed") is True or _clean_text(record.get("dispatch_claim_id")):
            return _commit_blocked("stale_dispatch_claim", "target record already has dispatch claim")
    else:
        if record.get("send_order_called") is not False:
            return _commit_blocked("dispatch_claim_release", "claimed record send_order_called is not false")
        if _clean_text(record.get("broker_order_no")):
            return _commit_blocked("dispatch_claim_release", "claimed record already has broker_order_no")
    if status in _DISPATCH_CLAIM_BLOCKED_STATUSES and not (allow_release and status == "DISPATCH_CLAIMED"):
        return _commit_blocked("dispatch_claim", f"target record status cannot be claimed: {status}")
    return None


def _manual_release_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return ctx.get("manual_dispatch_claim_release_confirmed") is True or ctx.get("manual_release_confirmed") is True


def claim_order_for_dispatch(
    queue_path: str | Path,
    identity: Any,
    final_guard_result: Any,
    *,
    claim_token: str | None = None,
    claim_owner: str | None = None,
    claim_source: str | None = None,
    context: Any = None,
    expected_revision: int | None = None,
    claim_id: str | None = None,
) -> dict[str, Any]:
    """Atomically claim one ORDER_QUEUED record for dispatch without calling SendOrder."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked, expected_revision=expected_revision)
    if expected_revision is None:
        return _with_queue_metadata(_commit_blocked("revision_cas", "expected_revision is required for dispatch claim"))

    token = _clean_text(claim_token or _as_dict(context).get("claim_token") or _as_dict(context).get("dispatch_claim_token"))
    if not token:
        return _with_queue_metadata(_commit_blocked("dispatch_claim_token", "dispatch claim token is required"), expected_revision=expected_revision)
    owner = _clean_text(claim_owner or _as_dict(context).get("claim_owner") or _as_dict(context).get("dispatch_claim_owner"))
    if not owner:
        return _with_queue_metadata(_commit_blocked("dispatch_claim_owner", "dispatch claim owner is required"), expected_revision=expected_revision)
    source = _clean_text(claim_source) or _dispatch_claim_source(context)
    if not source:
        return _with_queue_metadata(_commit_blocked("dispatch_claim_source", "dispatch claim source is required"), expected_revision=expected_revision)

    ttl, ttl_blocked = _dispatch_claim_ttl(context)
    if ttl_blocked is not None:
        return _with_queue_metadata(ttl_blocked, expected_revision=expected_revision)

    guard_blocked = _dispatch_final_guard_ready(final_guard_result, normalized_identity, context, expected_revision)
    if guard_blocked is not None:
        return _with_queue_metadata(guard_blocked, expected_revision=expected_revision)

    dispatch_claim_id = _clean_text(claim_id) or f"DISPATCH_CLAIM_{uuid4().hex}"
    token_hash = _sha256_text(token)
    claimed_at = datetime.now()
    expires_at = claimed_at + timedelta(seconds=ttl)

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = _dispatch_matching_records(data, normalized_identity)
        if len(matches) != 1:
            return {"blocked": _commit_blocked("dispatch_identity", f"dispatch target matching record count is {len(matches)}")}
        target = matches[0]
        record_blocked = _dispatch_claim_record_blocked(target)
        if record_blocked is not None:
            return {"blocked": record_blocked}

        updated_data = deepcopy(data)
        for index, item in enumerate(updated_data["orders"]):
            if _dispatch_identity_matches(_as_dict(item), normalized_identity):
                updated_record = deepcopy(item)
                dispatch_generation = int(updated_record.get("dispatch_generation") or 0) + 1
                updated_record.update(
                    {
                        "status": "DISPATCH_CLAIMED",
                        "dispatch_claimed": True,
                        "dispatch_claim_id": dispatch_claim_id,
                        "dispatch_claim_token_hash": token_hash,
                        "dispatch_claim_owner": owner,
                        "dispatch_claimed_at": _time_text(claimed_at),
                        "dispatch_claim_expires_at": _time_text(expires_at),
                        "dispatch_claim_source": source,
                        "dispatch_claim_revision": _normalize_revision(data),
                        "dispatch_claim_attempt": int(updated_record.get("dispatch_claim_attempt") or 0) + 1,
                        "dispatch_generation": dispatch_generation,
                        "dispatch_claim_previous_status": "ORDER_QUEUED",
                        "updated_at": _time_text(claimed_at),
                    }
                )
                updated_data["orders"][index] = updated_record
                return {
                    "data": updated_data,
                    "result": {
                        "claimed": True,
                        "status": "DISPATCH_CLAIMED",
                        "dispatch_claim_id": dispatch_claim_id,
                        "dispatch_claim_token_hash": token_hash,
                        "dispatch_claim_owner": owner,
                        "dispatch_claimed_at": _time_text(claimed_at),
                        "dispatch_claim_expires_at": _time_text(expires_at),
                        "dispatch_claim_source": source,
                        "dispatch_claim_attempt": updated_record["dispatch_claim_attempt"],
                        "dispatch_generation": dispatch_generation,
                        "claimed_identity": deepcopy(normalized_identity),
                        "send_order_called": False,
                        "actual_order_sent": False,
                        "broker_api_called": False,
                    },
                }
        return {"blocked": _commit_blocked("dispatch_identity", "dispatch target disappeared before mutation")}

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = _dispatch_matching_records(after_data, normalized_identity)
        if len(matches) != 1:
            return _commit_blocked("post_dispatch_claim_verify", f"claimed record count is {len(matches)}")
        claimed = matches[0]
        if claimed.get("status") != "DISPATCH_CLAIMED" or claimed.get("dispatch_claimed") is not True:
            return _commit_blocked("post_dispatch_claim_verify", "claimed record status was not persisted")
        if _clean_text(claimed.get("dispatch_claim_id")) != dispatch_claim_id:
            return _commit_blocked("post_dispatch_claim_verify", "dispatch claim id mismatch after write")
        if _clean_text(claimed.get("dispatch_claim_token_hash")) != token_hash:
            return _commit_blocked("post_dispatch_claim_verify", "dispatch claim token hash mismatch after write")
        if claimed.get("send_order_called") is not False:
            return _commit_blocked("post_dispatch_claim_verify", "claimed record send_order_called changed")
        return None

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="dispatch_claim",
        success_stage="dispatch_claim_committed",
        next_stage=NEXT_STAGE_DISPATCH_CLAIM_REVIEW_REQUIRED,
        backup=True,
        context=context,
        expected_revision=expected_revision,
        verify=verify,
    )
    if result.get("committed") is True:
        result.setdefault("claimed", True)
        result.setdefault("status", "DISPATCH_CLAIMED")
        result.setdefault("send_order_called", False)
        result.setdefault("actual_order_sent", False)
        result.setdefault("broker_api_called", False)
    else:
        result.setdefault("claimed", False)
    return result


def inspect_dispatch_claim(queue_path: str | Path, identity: Any, *, context: Any = None) -> dict[str, Any]:
    """Read the current dispatch claim state for one queue record."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked)
    target_path = Path(queue_path)
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                data, read_blocked = _read_queue_file(target_path)
                if read_blocked is not None:
                    return _with_queue_metadata(read_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)
                revision = _normalize_revision(data)
                matches = _dispatch_matching_records(data, normalized_identity)
                if len(matches) != 1:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("dispatch_identity", f"dispatch target matching record count is {len(matches)}"),
                            "claimed": False,
                        },
                        revision_before=revision,
                        revision_after=revision,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                record = matches[0]
                return _with_queue_metadata(
                    {
                        "committed": False,
                        "changed": False,
                        "write_stage": "dispatch_claim_inspected",
                        "next_stage": NEXT_STAGE_BLOCKED,
                        "claimed": record.get("status") == "DISPATCH_CLAIMED" and record.get("dispatch_claimed") is True,
                        "status": record.get("status"),
                        "dispatch_claim_id": record.get("dispatch_claim_id"),
                        "dispatch_claim_token_hash": record.get("dispatch_claim_token_hash"),
                        "dispatch_claim_owner": record.get("dispatch_claim_owner"),
                        "dispatch_claimed_at": record.get("dispatch_claimed_at"),
                        "dispatch_claim_expires_at": record.get("dispatch_claim_expires_at"),
                        "claimed_identity": deepcopy(normalized_identity),
                        "blocked_reasons": [],
                        "warnings": [],
                    },
                    revision_before=revision,
                    revision_after=revision,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock timeout"), lock_acquired=False)

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"))


def release_dispatch_claim(
    queue_path: str | Path,
    identity: Any,
    *,
    claim_id: str,
    claim_token: str,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Manually release a dispatch claim back to ORDER_QUEUED without SendOrder."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked, expected_revision=expected_revision)
    if expected_revision is None:
        return _with_queue_metadata(_commit_blocked("revision_cas", "expected_revision is required for dispatch claim release"))
    if not _manual_release_confirmed(context):
        return _with_queue_metadata(_commit_blocked("manual_confirm", "manual dispatch claim release confirmation is required"), expected_revision=expected_revision)
    release_claim_id = _clean_text(claim_id)
    release_token_hash = _sha256_text(claim_token)
    if not release_claim_id:
        return _with_queue_metadata(_commit_blocked("dispatch_claim_release", "dispatch claim id is required"), expected_revision=expected_revision)
    if not _clean_text(claim_token):
        return _with_queue_metadata(_commit_blocked("dispatch_claim_release", "dispatch claim token is required"), expected_revision=expected_revision)

    released_at = datetime.now()
    release_reason = _clean_text(_as_dict(context).get("dispatch_release_reason") or _as_dict(context).get("release_reason") or "manual_release")
    released_by = _clean_text(_as_dict(context).get("dispatch_released_by") or _as_dict(context).get("released_by") or "manual")

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = _dispatch_matching_records(data, normalized_identity)
        if len(matches) != 1:
            return {"blocked": _commit_blocked("dispatch_identity", f"dispatch target matching record count is {len(matches)}")}
        target = matches[0]
        record_blocked = _dispatch_claim_record_blocked(target, allow_release=True)
        if record_blocked is not None:
            return {"blocked": record_blocked}
        if _clean_text(target.get("dispatch_claim_id")) != release_claim_id:
            return {"blocked": _commit_blocked("dispatch_claim_release", "dispatch claim id mismatch")}
        if _clean_text(target.get("dispatch_claim_token_hash")) != release_token_hash:
            return {"blocked": _commit_blocked("dispatch_claim_release", "dispatch claim token hash mismatch")}

        updated_data = deepcopy(data)
        for index, item in enumerate(updated_data["orders"]):
            if _dispatch_identity_matches(_as_dict(item), normalized_identity):
                updated_record = deepcopy(item)
                previous_claim_id = _clean_text(updated_record.get("dispatch_claim_id"))
                dispatch_generation = int(updated_record.get("dispatch_generation") or 0) + 1
                updated_record.update(
                    {
                        "status": "ORDER_QUEUED",
                        "dispatch_claimed": False,
                        "dispatch_release_reason": release_reason,
                        "dispatch_released_at": _time_text(released_at),
                        "dispatch_released_by": released_by,
                        "previous_dispatch_claim_id": previous_claim_id,
                        "dispatch_claim_released_at": _time_text(released_at),
                        "dispatch_claim_release_source": _clean_text(_as_dict(context).get("release_source") or "manual"),
                        "dispatch_generation": dispatch_generation,
                        "updated_at": _time_text(released_at),
                    }
                )
                updated_data["orders"][index] = updated_record
                return {
                    "data": updated_data,
                    "result": {
                        "released": True,
                        "status": "ORDER_QUEUED",
                        "dispatch_claim_id": release_claim_id,
                        "dispatch_release_reason": release_reason,
                        "dispatch_released_at": _time_text(released_at),
                        "dispatch_released_by": released_by,
                        "previous_dispatch_claim_id": previous_claim_id,
                        "dispatch_generation": dispatch_generation,
                        "claimed_identity": deepcopy(normalized_identity),
                        "send_order_called": False,
                        "actual_order_sent": False,
                        "broker_api_called": False,
                    },
                }
        return {"blocked": _commit_blocked("dispatch_identity", "dispatch target disappeared before release")}

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = _dispatch_matching_records(after_data, normalized_identity)
        if len(matches) != 1:
            return _commit_blocked("post_dispatch_claim_release_verify", f"released record count is {len(matches)}")
        record = matches[0]
        if record.get("status") != "ORDER_QUEUED" or record.get("dispatch_claimed") is not False:
            return _commit_blocked("post_dispatch_claim_release_verify", "released record status was not persisted")
        if record.get("send_order_called") is not False:
            return _commit_blocked("post_dispatch_claim_release_verify", "released record send_order_called changed")
        return None

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="dispatch_claim_release",
        success_stage="dispatch_claim_released",
        next_stage=NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED,
        backup=True,
        context=context,
        expected_revision=expected_revision,
        verify=verify,
    )
    if result.get("committed") is True:
        result.setdefault("released", True)
        result.setdefault("status", "ORDER_QUEUED")
    else:
        result.setdefault("released", False)
    return result


def _send_attempt_record_blocked(
    record: dict[str, Any],
    *,
    dispatch_claim_id: str,
    claim_token_hash: str,
    claim_owner: str,
) -> dict[str, Any] | None:
    if record.get("status") != "DISPATCH_CLAIMED":
        return _commit_blocked("send_order_attempt", "target record status is not DISPATCH_CLAIMED")
    if record.get("dispatch_claimed") is not True:
        return _commit_blocked("send_order_attempt", "target record dispatch_claimed is not true")
    if _clean_text(record.get("dispatch_claim_id")) != dispatch_claim_id:
        return _commit_blocked("send_order_attempt", "dispatch claim id mismatch")
    if _clean_text(record.get("dispatch_claim_token_hash")) != claim_token_hash:
        return _commit_blocked("send_order_attempt", "dispatch claim token hash mismatch")
    if _clean_text(record.get("dispatch_claim_owner")) != claim_owner:
        return _commit_blocked("send_order_attempt", "dispatch claim owner mismatch")
    expires_at = _parse_time_text(record.get("dispatch_claim_expires_at"))
    if expires_at is None:
        return _commit_blocked("send_order_attempt", "dispatch claim expiration is required")
    if datetime.now() >= expires_at:
        return _commit_blocked("stale_dispatch_claim", "dispatch claim expired; manual reconciliation required")
    if record.get("send_order_called") is not False:
        return _commit_blocked("send_order_attempt", "target record send_order_called is not false")
    if _clean_text(record.get("broker_order_no")):
        return _commit_blocked("send_order_attempt", "target record already has broker_order_no")
    if _clean_text(record.get("send_order_attempt_id")):
        return _commit_blocked("send_order_attempt", "send order attempt already recorded")
    return None


def mark_send_order_attempted(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    claim_token: str,
    claim_owner: str,
    attempt_owner: str | None = None,
    attempt_source: str | None = None,
    context: Any = None,
    expected_revision: int | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Record a durable SEND_ATTEMPTED state without calling SendOrder."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked, expected_revision=expected_revision)
    if expected_revision is None:
        return _with_queue_metadata(_commit_blocked("revision_cas", "expected_revision is required for send order attempt"))

    normalized_claim_id = _clean_text(dispatch_claim_id)
    normalized_claim_token_hash = _sha256_text(claim_token)
    normalized_claim_owner = _clean_text(claim_owner)
    if not normalized_claim_id:
        return _with_queue_metadata(_commit_blocked("send_order_attempt", "dispatch claim id is required"), expected_revision=expected_revision)
    if not _clean_text(claim_token):
        return _with_queue_metadata(_commit_blocked("send_order_attempt", "dispatch claim token is required"), expected_revision=expected_revision)
    if not normalized_claim_owner:
        return _with_queue_metadata(_commit_blocked("send_order_attempt", "dispatch claim owner is required"), expected_revision=expected_revision)

    owner = _clean_text(attempt_owner or _as_dict(context).get("send_order_attempt_owner") or normalized_claim_owner)
    source = _clean_text(attempt_source or _as_dict(context).get("send_order_attempt_source") or "dispatch_claim")
    if not owner:
        return _with_queue_metadata(_commit_blocked("send_order_attempt", "send order attempt owner is required"), expected_revision=expected_revision)
    if not source:
        return _with_queue_metadata(_commit_blocked("send_order_attempt", "send order attempt source is required"), expected_revision=expected_revision)

    normalized_attempt_id = _clean_text(attempt_id) or f"SEND_ATTEMPT_{uuid4().hex}"
    attempted_at = datetime.now()

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = _dispatch_matching_records(data, normalized_identity)
        if len(matches) != 1:
            return {"blocked": _commit_blocked("send_order_attempt", f"send order target matching record count is {len(matches)}")}
        target = matches[0]
        record_blocked = _send_attempt_record_blocked(
            target,
            dispatch_claim_id=normalized_claim_id,
            claim_token_hash=normalized_claim_token_hash,
            claim_owner=normalized_claim_owner,
        )
        if record_blocked is not None:
            return {"blocked": record_blocked}

        updated_data = deepcopy(data)
        for index, item in enumerate(updated_data["orders"]):
            if _dispatch_identity_matches(_as_dict(item), normalized_identity):
                updated_record = deepcopy(item)
                updated_record.update(
                    {
                        "status": "SEND_ATTEMPTED",
                        "send_order_called": False,
                        "send_order_attempted": True,
                        "send_order_attempt_recorded": True,
                        "send_order_attempt_id": normalized_attempt_id,
                        "send_order_attempted_at": _time_text(attempted_at),
                        "send_order_attempt_owner": owner,
                        "send_order_attempt_source": source,
                        "send_order_attempt_revision": _normalize_revision(data),
                        "send_order_attempt_count": int(updated_record.get("send_order_attempt_count") or 0) + 1,
                        "broker_call_executed": False,
                        "actual_order_sent": False,
                        "broker_api_called": False,
                        "send_call_result_known": False,
                        "broker_result_known": False,
                        "broker_accepted": False,
                        "broker_rejected": False,
                        "automatic_retry_allowed": False,
                        "updated_at": _time_text(attempted_at),
                    }
                )
                updated_data["orders"][index] = updated_record
                return {
                    "data": updated_data,
                    "result": {
                        "attempt_recorded": True,
                        "status": "SEND_ATTEMPTED",
                        "send_order_attempt_id": normalized_attempt_id,
                        "send_order_attempted_at": _time_text(attempted_at),
                        "send_order_attempt_owner": owner,
                        "send_order_attempt_source": source,
                        "send_order_attempt_count": updated_record["send_order_attempt_count"],
                        "dispatch_claim_id": normalized_claim_id,
                        "dispatch_generation": updated_record.get("dispatch_generation"),
                        "claimed_identity": deepcopy(normalized_identity),
                        "send_order_called": False,
                        "send_order_attempt_recorded": True,
                        "broker_call_executed": False,
                        "actual_order_sent": False,
                        "broker_api_called": False,
                        "send_call_result_known": False,
                        "broker_result_known": False,
                        "broker_accepted": False,
                        "broker_rejected": False,
                        "automatic_retry_allowed": False,
                    },
                }
        return {"blocked": _commit_blocked("send_order_attempt", "send order target disappeared before mutation")}

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = _dispatch_matching_records(after_data, normalized_identity)
        if len(matches) != 1:
            return _commit_blocked("post_send_order_attempt_verify", f"attempted record count is {len(matches)}")
        record = matches[0]
        if record.get("status") != "SEND_ATTEMPTED":
            return _commit_blocked("post_send_order_attempt_verify", "send order attempt status was not persisted")
        if _clean_text(record.get("send_order_attempt_id")) != normalized_attempt_id:
            return _commit_blocked("post_send_order_attempt_verify", "send order attempt id mismatch after write")
        if record.get("broker_call_executed") is not False or record.get("broker_api_called") is not False:
            return _commit_blocked("post_send_order_attempt_verify", "send order attempt executed broker call")
        return None

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="send_order_attempt",
        success_stage="send_order_attempt_recorded",
        next_stage="SEND_ORDER_RESULT_RECORD_REQUIRED",
        backup=True,
        context=context,
        expected_revision=expected_revision,
        verify=verify,
    )
    if result.get("committed") is True:
        result.setdefault("attempt_recorded", True)
        result.setdefault("status", "SEND_ATTEMPTED")
    else:
        result.setdefault("attempt_recorded", False)
    return result


def _broker_result_record_blocked(
    record: dict[str, Any],
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
) -> dict[str, Any] | None:
    if record.get("status") != "SEND_CALL_IN_PROGRESS":
        return _commit_blocked("broker_send_result", "target record status is not SEND_CALL_IN_PROGRESS")
    if _clean_text(record.get("dispatch_claim_id")) != dispatch_claim_id:
        return _commit_blocked("broker_send_result", "dispatch claim id mismatch")
    if _clean_text(record.get("send_order_attempt_id")) != send_order_attempt_id:
        return _commit_blocked("broker_send_result", "send order attempt id mismatch")
    if record.get("send_call_result_known") is True or record.get("send_call_accepted") is True or record.get("send_call_rejected") is True or record.get("send_uncertain") is True:
        return _commit_blocked("broker_send_result", "send order attempt already has broker result")
    if record.get("broker_result_known") is True or record.get("broker_accepted") is True or record.get("broker_rejected") is True:
        return _commit_blocked("broker_send_result", "send order attempt already has broker lifecycle result")
    return None


def _send_call_start_blocked(
    record: dict[str, Any],
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
) -> dict[str, Any] | None:
    if record.get("status") != "SEND_ATTEMPTED":
        return _commit_blocked("send_call_start", "target record status is not SEND_ATTEMPTED")
    if _clean_text(record.get("dispatch_claim_id")) != dispatch_claim_id:
        return _commit_blocked("send_call_start", "dispatch claim id mismatch")
    if _clean_text(record.get("send_order_attempt_id")) != send_order_attempt_id:
        return _commit_blocked("send_call_start", "send order attempt id mismatch")
    if record.get("send_order_called") is not False:
        return _commit_blocked("send_call_start", "target record send_order_called is not false")
    if record.get("broker_call_executed") is not False or record.get("broker_api_called") is not False:
        return _commit_blocked("send_call_start", "target record broker call already executed")
    if record.get("send_call_result_known") is True or record.get("send_uncertain") is True:
        return _commit_blocked("send_call_start", "target record already has send call result")
    return None


def mark_send_order_call_in_progress(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Record that the real SendOrder callable boundary has been entered."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked, expected_revision=expected_revision)
    if expected_revision is None:
        return _with_queue_metadata(_commit_blocked("revision_cas", "expected_revision is required for send call start"))

    normalized_claim_id = _clean_text(dispatch_claim_id)
    normalized_attempt_id = _clean_text(send_order_attempt_id)
    if not normalized_claim_id:
        return _with_queue_metadata(_commit_blocked("send_call_start", "dispatch claim id is required"), expected_revision=expected_revision)
    if not normalized_attempt_id:
        return _with_queue_metadata(_commit_blocked("send_call_start", "send order attempt id is required"), expected_revision=expected_revision)

    started_at = datetime.now()

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = _dispatch_matching_records(data, normalized_identity)
        if len(matches) != 1:
            return {"blocked": _commit_blocked("send_call_start", f"send call target matching record count is {len(matches)}")}
        target = matches[0]
        record_blocked = _send_call_start_blocked(
            target,
            dispatch_claim_id=normalized_claim_id,
            send_order_attempt_id=normalized_attempt_id,
        )
        if record_blocked is not None:
            return {"blocked": record_blocked}

        updated_data = deepcopy(data)
        for index, item in enumerate(updated_data["orders"]):
            if _dispatch_identity_matches(_as_dict(item), normalized_identity):
                updated_record = deepcopy(item)
                updated_record.update(
                    {
                        "status": "SEND_CALL_IN_PROGRESS",
                        "send_order_called": False,
                        "send_order_call_started": True,
                        "send_order_call_started_at": _time_text(started_at),
                        "send_order_call_revision": _normalize_revision(data),
                        "broker_call_executed": False,
                        "broker_api_called": False,
                        "actual_order_sent": False,
                        "call_execution_uncertain": True,
                        "send_call_result_known": False,
                        "send_call_accepted": False,
                        "send_call_rejected": False,
                        "send_uncertain": False,
                        "automatic_retry_allowed": False,
                        "manual_reconciliation_required": True,
                        "updated_at": _time_text(started_at),
                    }
                )
                updated_data["orders"][index] = updated_record
                return {
                    "data": updated_data,
                    "result": {
                        "send_call_started": True,
                        "status": "SEND_CALL_IN_PROGRESS",
                        "dispatch_claim_id": normalized_claim_id,
                        "send_order_attempt_id": normalized_attempt_id,
                        "send_order_call_started_at": _time_text(started_at),
                        "claimed_identity": deepcopy(normalized_identity),
                        "send_order_called": False,
                        "broker_call_executed": False,
                        "broker_api_called": False,
                        "actual_order_sent": False,
                        "call_execution_uncertain": True,
                        "send_call_result_known": False,
                        "automatic_retry_allowed": False,
                        "manual_reconciliation_required": True,
                    },
                }
        return {"blocked": _commit_blocked("send_call_start", "send call target disappeared before mutation")}

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = _dispatch_matching_records(after_data, normalized_identity)
        if len(matches) != 1:
            return _commit_blocked("post_send_call_start_verify", f"send call started record count is {len(matches)}")
        record = matches[0]
        if record.get("status") != "SEND_CALL_IN_PROGRESS":
            return _commit_blocked("post_send_call_start_verify", "send call in-progress status was not persisted")
        if _clean_text(record.get("send_order_attempt_id")) != normalized_attempt_id:
            return _commit_blocked("post_send_call_start_verify", "send order attempt id mismatch after call start")
        if record.get("send_order_called") is not False or record.get("broker_api_called") is not False:
            return _commit_blocked("post_send_call_start_verify", "send call execution flags were set before callable execution")
        if record.get("call_execution_uncertain") is not True:
            return _commit_blocked("post_send_call_start_verify", "call execution uncertainty marker was not persisted")
        return None

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="send_call_start",
        success_stage="send_call_in_progress_recorded",
        next_stage="SEND_CALL_RESULT_RECORD_REQUIRED",
        backup=True,
        context=context,
        expected_revision=expected_revision,
        verify=verify,
    )
    if result.get("committed") is True:
        result.setdefault("send_call_started", True)
        result.setdefault("status", "SEND_CALL_IN_PROGRESS")
    else:
        result.setdefault("send_call_started", False)
    return result


def _record_broker_send_result(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
    result_status: str,
    context: Any = None,
    expected_revision: int | None = None,
    broker_return_code: Any = None,
    broker_error_code: Any = None,
    broker_error_message: Any = None,
    broker_order_no: Any = None,
    uncertain_reason: Any = None,
) -> dict[str, Any]:
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked, expected_revision=expected_revision)
    if expected_revision is None:
        return _with_queue_metadata(_commit_blocked("revision_cas", "expected_revision is required for broker send result"))

    normalized_claim_id = _clean_text(dispatch_claim_id)
    normalized_attempt_id = _clean_text(send_order_attempt_id)
    if not normalized_claim_id:
        return _with_queue_metadata(_commit_blocked("broker_send_result", "dispatch claim id is required"), expected_revision=expected_revision)
    if not normalized_attempt_id:
        return _with_queue_metadata(_commit_blocked("broker_send_result", "send order attempt id is required"), expected_revision=expected_revision)
    if result_status not in {"SEND_CALL_ACCEPTED", "SEND_CALL_REJECTED", "SEND_UNCERTAIN"}:
        return _with_queue_metadata(_commit_blocked("broker_send_result", "broker send result status is invalid"), expected_revision=expected_revision)

    recorded_at = datetime.now()

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = _dispatch_matching_records(data, normalized_identity)
        if len(matches) != 1:
            return {"blocked": _commit_blocked("broker_send_result", f"broker result target matching record count is {len(matches)}")}
        target = matches[0]
        record_blocked = _broker_result_record_blocked(
            target,
            dispatch_claim_id=normalized_claim_id,
            send_order_attempt_id=normalized_attempt_id,
        )
        if record_blocked is not None:
            return {"blocked": record_blocked}

        updated_data = deepcopy(data)
        for index, item in enumerate(updated_data["orders"]):
            if _dispatch_identity_matches(_as_dict(item), normalized_identity):
                updated_record = deepcopy(item)
                common = {
                    "status": result_status,
                    "dispatch_claim_id": normalized_claim_id,
                    "send_order_attempt_id": normalized_attempt_id,
                    "send_call_result_recorded_at": _time_text(recorded_at),
                    "send_order_called": True,
                    "broker_call_executed": True,
                    "broker_api_called": True,
                    "call_execution_uncertain": False,
                    "broker_return_code": broker_return_code,
                    "broker_result_known": False,
                    "broker_accepted": False,
                    "broker_rejected": False,
                    "updated_at": _time_text(recorded_at),
                }
                if result_status == "SEND_CALL_ACCEPTED":
                    common.update(
                        {
                            "send_call_result_known": True,
                            "send_call_accepted": True,
                            "send_call_rejected": False,
                            "send_uncertain": False,
                            "broker_error_code": "",
                            "broker_error_message": "",
                            "actual_order_sent": False,
                            "automatic_retry_allowed": False,
                        }
                    )
                    if _clean_text(broker_order_no):
                        common["broker_order_no"] = _clean_text(broker_order_no)
                elif result_status == "SEND_CALL_REJECTED":
                    common.update(
                        {
                            "send_call_result_known": True,
                            "send_call_accepted": False,
                            "send_call_rejected": True,
                            "send_uncertain": False,
                            "broker_error_code": _clean_text(broker_error_code),
                            "broker_error_message": _clean_text(broker_error_message),
                            "actual_order_sent": False,
                            "automatic_retry_allowed": False,
                        }
                    )
                else:
                    common.update(
                        {
                            "send_call_result_known": False,
                            "send_call_accepted": False,
                            "send_call_rejected": False,
                            "send_uncertain": True,
                            "uncertain_reason": _clean_text(uncertain_reason) or "send order result is uncertain",
                            "uncertain_recorded_at": _time_text(recorded_at),
                            "automatic_retry_allowed": False,
                            "manual_reconciliation_required": True,
                            "actual_order_sent": False,
                        }
                    )
                updated_record.update(common)
                updated_data["orders"][index] = updated_record
                return {
                    "data": updated_data,
                    "result": {
                        "send_call_result_recorded": True,
                        "broker_result_recorded": False,
                        "status": result_status,
                        "dispatch_claim_id": normalized_claim_id,
                        "send_order_attempt_id": normalized_attempt_id,
                        "send_call_result_recorded_at": _time_text(recorded_at),
                        "send_order_called": True,
                        "broker_call_executed": True,
                        "broker_api_called": True,
                        "call_execution_uncertain": False,
                        "send_call_result_known": common.get("send_call_result_known"),
                        "send_call_accepted": common.get("send_call_accepted"),
                        "send_call_rejected": common.get("send_call_rejected"),
                        "broker_result_known": False,
                        "broker_accepted": False,
                        "broker_rejected": False,
                        "send_uncertain": common.get("send_uncertain"),
                        "automatic_retry_allowed": common.get("automatic_retry_allowed"),
                        "manual_reconciliation_required": common.get("manual_reconciliation_required", False),
                        "claimed_identity": deepcopy(normalized_identity),
                    },
                }
        return {"blocked": _commit_blocked("broker_send_result", "broker result target disappeared before mutation")}

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = _dispatch_matching_records(after_data, normalized_identity)
        if len(matches) != 1:
            return _commit_blocked("post_broker_send_result_verify", f"broker result record count is {len(matches)}")
        record = matches[0]
        if record.get("status") != result_status:
            return _commit_blocked("post_broker_send_result_verify", "broker send result status was not persisted")
        if _clean_text(record.get("send_order_attempt_id")) != normalized_attempt_id:
            return _commit_blocked("post_broker_send_result_verify", "send order attempt id mismatch after result write")
        return None

    result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="broker_send_result",
        success_stage="broker_send_result_recorded",
        next_stage="BROKER_SEND_RESULT_REVIEW_REQUIRED",
        backup=True,
        context=context,
        expected_revision=expected_revision,
        verify=verify,
    )
    if result.get("committed") is True:
        result.setdefault("send_call_result_recorded", True)
        result.setdefault("broker_result_recorded", False)
        result.setdefault("status", result_status)
    else:
        result.setdefault("send_call_result_recorded", False)
        result.setdefault("broker_result_recorded", False)
    return result


def record_broker_send_accepted(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
    broker_return_code: Any = 0,
    broker_order_no: Any = None,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _record_broker_send_result(
        queue_path,
        identity,
        dispatch_claim_id=dispatch_claim_id,
        send_order_attempt_id=send_order_attempt_id,
        result_status="SEND_CALL_ACCEPTED",
        broker_return_code=broker_return_code,
        broker_order_no=broker_order_no,
        context=context,
        expected_revision=expected_revision,
    )


def record_broker_send_rejected(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
    broker_return_code: Any = None,
    broker_error_code: Any = None,
    broker_error_message: Any = None,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _record_broker_send_result(
        queue_path,
        identity,
        dispatch_claim_id=dispatch_claim_id,
        send_order_attempt_id=send_order_attempt_id,
        result_status="SEND_CALL_REJECTED",
        broker_return_code=broker_return_code,
        broker_error_code=broker_error_code,
        broker_error_message=broker_error_message,
        context=context,
        expected_revision=expected_revision,
    )


def record_broker_send_uncertain(
    queue_path: str | Path,
    identity: Any,
    *,
    dispatch_claim_id: str,
    send_order_attempt_id: str,
    uncertain_reason: Any,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _record_broker_send_result(
        queue_path,
        identity,
        dispatch_claim_id=dispatch_claim_id,
        send_order_attempt_id=send_order_attempt_id,
        result_status="SEND_UNCERTAIN",
        uncertain_reason=uncertain_reason,
        context=context,
        expected_revision=expected_revision,
    )


def inspect_send_order_lifecycle(queue_path: str | Path, identity: Any, *, context: Any = None) -> dict[str, Any]:
    """Read the current SendOrder lifecycle state without mutating the queue."""
    normalized_identity, identity_blocked = _dispatch_identity(identity)
    if identity_blocked is not None:
        return _with_queue_metadata(identity_blocked)
    target_path = Path(queue_path)
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                data, read_blocked = _read_queue_file(target_path)
                if read_blocked is not None:
                    return _with_queue_metadata(read_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)
                revision = _normalize_revision(data)
                matches = _dispatch_matching_records(data, normalized_identity)
                if len(matches) != 1:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("send_order_lifecycle", f"send order lifecycle matching record count is {len(matches)}"),
                            "lifecycle_inspected": False,
                        },
                        revision_before=revision,
                        revision_after=revision,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                record = matches[0]
                expires_at = _parse_time_text(record.get("dispatch_claim_expires_at"))
                return _with_queue_metadata(
                    {
                        "committed": False,
                        "changed": False,
                        "write_stage": "send_order_lifecycle_inspected",
                        "next_stage": NEXT_STAGE_BLOCKED,
                        "lifecycle_inspected": True,
                        "status": record.get("status"),
                        "dispatch_claim_id": record.get("dispatch_claim_id"),
                        "dispatch_generation": record.get("dispatch_generation"),
                        "send_order_attempt_id": record.get("send_order_attempt_id"),
                        "send_order_attempt_count": record.get("send_order_attempt_count", 0),
                        "send_order_call_started": record.get("send_order_call_started", False),
                        "send_order_call_started_at": record.get("send_order_call_started_at"),
                        "call_execution_uncertain": record.get("call_execution_uncertain", False),
                        "send_call_result_known": record.get("send_call_result_known", False),
                        "send_call_accepted": record.get("send_call_accepted", False),
                        "send_call_rejected": record.get("send_call_rejected", False),
                        "broker_result_known": record.get("broker_result_known", False),
                        "broker_accepted": record.get("broker_accepted", False),
                        "broker_rejected": record.get("broker_rejected", False),
                        "send_uncertain": record.get("send_uncertain", False),
                        "automatic_retry_allowed": record.get("automatic_retry_allowed", False),
                        "manual_reconciliation_required": record.get("manual_reconciliation_required", False),
                        "dispatch_claim_expired": expires_at is not None and datetime.now() >= expires_at,
                        "claimed_identity": deepcopy(normalized_identity),
                        "blocked_reasons": [],
                        "warnings": [],
                    },
                    revision_before=revision,
                    revision_after=revision,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock timeout"), lock_acquired=False)

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"))


_RECOVERY_IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "request_hash",
    "lock_id",
    "execution_id",
)


def _matching_identity_records(queue_data: dict[str, Any], identity: dict[str, Any]) -> list[dict[str, Any]]:
    fields = [field for field in _RECOVERY_IDENTITY_FIELDS if _clean_text(identity.get(field))]
    return [
        item
        for item in _as_list(queue_data.get("orders"))
        if isinstance(item, dict) and all(_clean_text(item.get(field)) == _clean_text(identity.get(field)) for field in fields)
    ]


def _recovery_diff(queue_data: dict[str, Any], backup_data: dict[str, Any], identities: list[dict[str, Any]]) -> dict[str, Any]:
    queue_orders = [item for item in _as_list(queue_data.get("orders")) if isinstance(item, dict)]
    backup_orders = [item for item in _as_list(backup_data.get("orders")) if isinstance(item, dict)]
    queue_counts = [len(_matching_identity_records(queue_data, identity)) for identity in identities]
    backup_counts = [len(_matching_identity_records(backup_data, identity)) for identity in identities]
    return {
        "queue_order_count": len(queue_orders),
        "backup_order_count": len(backup_orders),
        "queue_matching_record_count": sum(queue_counts),
        "backup_matching_record_count": sum(backup_counts),
        "queue_matching_counts": queue_counts,
        "backup_matching_counts": backup_counts,
        "queue_backup_changed": queue_orders != backup_orders,
        "target_record_changed": queue_counts != backup_counts,
    }


def restore_order_queue_from_approved_backup(
    queue_path: str | Path,
    backup_path: str | Path,
    target_identities: Any,
    *,
    expected_diff: Any = None,
    context: Any = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Restore queue orders from an approved backup through the canonical lock."""
    target_path = Path(queue_path)
    source_backup_path = Path(backup_path)
    identities = [item for item in _as_list(target_identities) if isinstance(item, dict)]
    if not identities:
        return _with_queue_metadata(_commit_blocked("recovery_identity", "target_identities must not be empty"), expected_revision=expected_revision)

    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(target_path, _lock_timeout_sec(context)) as lock:
                queue_data, queue_blocked = _read_queue_file(target_path)
                if queue_blocked is not None:
                    return _with_queue_metadata(
                        queue_blocked,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                backup_data, backup_blocked = _read_queue_file(source_backup_path)
                if backup_blocked is not None:
                    return _with_queue_metadata(
                        {
                            **backup_blocked,
                            "write_stage": "backup_read",
                        },
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                revision_before = _normalize_revision(queue_data)
                cas_blocked = _cas_blocked(revision_before, expected_revision)
                if cas_blocked is not None:
                    return _with_queue_metadata(
                        cas_blocked,
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=True,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                current_diff = _recovery_diff(queue_data, backup_data, identities)
                planned = _as_dict(expected_diff)
                if planned:
                    keys = (
                        "queue_order_count",
                        "backup_order_count",
                        "queue_matching_record_count",
                        "backup_matching_record_count",
                        "queue_backup_changed",
                        "target_record_changed",
                    )
                    if any(current_diff.get(key) != planned.get(key) for key in keys):
                        return _with_queue_metadata(
                            _commit_blocked("recovery_preflight", "current queue/backup state differs from approval action"),
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            cas_checked=expected_revision is not None,
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )
                if current_diff.get("queue_backup_changed") is not True or current_diff.get("target_record_changed") is not True:
                    return _with_queue_metadata(
                        _commit_blocked("recovery_preflight", "queue and backup must differ before recovery"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if current_diff.get("queue_matching_record_count") != len(identities) or any(count != 1 for count in current_diff.get("queue_matching_counts", [])):
                    return _with_queue_metadata(
                        _commit_blocked("recovery_preflight", "current queue must contain exactly one record for each target identity"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if current_diff.get("backup_matching_record_count") != 0:
                    return _with_queue_metadata(
                        _commit_blocked("recovery_preflight", "backup must not contain the target record"),
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                safety_backup_path = target_path.with_name(f"{target_path.name}.recovery_safety.{uuid4().hex}.bak")
                temp_restore_path = target_path.with_name(f"{target_path.name}.recovery_restore.{uuid4().hex}.tmp")
                shutil.copy2(target_path, safety_backup_path)

                revision_after = revision_before + 1
                restore_data = deepcopy(backup_data)
                restore_data["version"] = restore_data.get("version", 1)
                restore_data["revision"] = revision_after
                restore_data["updated_at"] = _now_text()

                try:
                    with temp_restore_path.open("w", encoding="utf-8") as handle:
                        json.dump(restore_data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception as exc:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("temp_restore", f"failed to write recovery temp json: {exc}"),
                            "order_queue_path": str(target_path),
                            "backup_path": str(source_backup_path),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": False,
                            "file_write": True,
                            "queue_write": False,
                            "queue_committed": False,
                            "post_write_verified": False,
                        },
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                temp_data, temp_blocked = _read_queue_file(temp_restore_path)
                if temp_blocked is not None or temp_data != restore_data:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("temp_restore", "temp restore json does not match backup data"),
                            "order_queue_path": str(target_path),
                            "backup_path": str(source_backup_path),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": True,
                            "file_write": True,
                            "queue_write": False,
                            "queue_committed": False,
                            "post_write_verified": False,
                        },
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                try:
                    os.replace(temp_restore_path, target_path)
                except Exception as exc:
                    return _with_queue_metadata(
                        {
                            **_commit_blocked("restore_replace", f"failed to replace queue from recovery temp: {exc}"),
                            "order_queue_path": str(target_path),
                            "backup_path": str(source_backup_path),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": True,
                            "file_write": True,
                            "queue_write": False,
                            "queue_committed": False,
                            "post_write_verified": False,
                        },
                        revision_before=revision_before,
                        revision_after=revision_before,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                post_data, post_blocked = _read_queue_file(target_path)
                if post_blocked is not None:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                post_blocked.get("write_stage", "post_restore_verify"),
                                post_blocked.get("blocked_reasons", ["restored queue json invalid"])[0],
                                order_queue_path=str(target_path),
                                backup_path=str(source_backup_path),
                            ),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": True,
                            "restore_executed": True,
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if post_data != restore_data:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                "post_restore_verify",
                                "restored queue json does not match expected recovery data",
                                order_queue_path=str(target_path),
                                backup_path=str(source_backup_path),
                            ),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": True,
                            "restore_executed": True,
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                post_diff = _recovery_diff(post_data, restore_data, identities)
                if post_diff.get("queue_matching_record_count") != 0:
                    return _with_queue_metadata(
                        {
                            **_post_write_failed_result(
                                "post_restore_verify",
                                "target identity still exists after restore",
                                order_queue_path=str(target_path),
                                backup_path=str(source_backup_path),
                            ),
                            "safety_backup_path": str(safety_backup_path),
                            "temp_restore_path": str(temp_restore_path),
                            "safety_backup_created": True,
                            "temp_restore_written": True,
                            "restore_executed": True,
                        },
                        revision_before=revision_before,
                        revision_after=revision_after,
                        expected_revision=expected_revision,
                        cas_checked=expected_revision is not None,
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                return _with_queue_metadata(
                    {
                        "committed": True,
                        "operation_name": "approved_backup_restore",
                        "write_stage": "approved_backup_restored",
                        "next_stage": NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED,
                        "changed": True,
                        "order_queue_path": str(target_path),
                        "backup_path": str(source_backup_path),
                        "safety_backup_path": str(safety_backup_path),
                        "temp_restore_path": str(temp_restore_path),
                        "safety_backup_created": True,
                        "temp_restore_written": True,
                        "restore_executed": True,
                        "file_write": True,
                        "queue_write": True,
                        "queue_committed": True,
                        "post_write_verified": True,
                        "blocked_reasons": [],
                        "warnings": [],
                    },
                    revision_before=revision_before,
                    revision_after=revision_after,
                    expected_revision=expected_revision,
                    cas_checked=expected_revision is not None,
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_queue_metadata(
            _commit_blocked("queue_lock", "queue lock timeout"),
            expected_revision=expected_revision,
            cas_checked=expected_revision is not None,
            lock_acquired=False,
        )
    except Exception as exc:
        return _with_queue_metadata(
            _commit_blocked("approved_backup_restore", f"failed to restore approved backup: {exc}"),
            expected_revision=expected_revision,
            cas_checked=expected_revision is not None,
        )

    return _with_queue_metadata(_commit_blocked("queue_lock", "queue lock failed"), expected_revision=expected_revision)


def _duplicate_record_reason(records: list[dict[str, Any]]) -> str | None:
    checks = (
        ("order_id", "duplicate order_id"),
        ("candidate_id", "duplicate candidate_id"),
        ("queue_pending_id", "duplicate queue_pending_id"),
        ("execution_id", "duplicate execution_id"),
        ("request_hash", "duplicate request_hash"),
        ("lock_id", "duplicate lock_id"),
    )
    for field, reason in checks:
        seen: set[str] = set()
        for record in records:
            value = _clean_text(record.get(field))
            if value in seen:
                return reason
            seen.add(value)
    return None


def _existing_batch_duplicate_reason(existing_orders: Any, record: dict[str, Any]) -> str | None:
    checks = (
        ("order_id", "duplicate order_id"),
        ("candidate_id", "duplicate candidate_id"),
        ("queue_pending_id", "duplicate queue_pending_id"),
        ("execution_id", "duplicate execution_id"),
        ("request_hash", "duplicate request_hash"),
        ("lock_id", "duplicate lock_id"),
    )
    for field, reason in checks:
        value = _clean_text(record.get(field))
        for order in _as_list(existing_orders):
            if _clean_text(_as_dict(order).get(field)) == value:
                return reason
    return None
