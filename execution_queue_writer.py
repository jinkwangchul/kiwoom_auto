# -*- coding: utf-8 -*-
"""Dry-run execution queue writer preview.

This module only builds an ORDER_QUEUED record preview in memory. It never reads
or writes runtime/order_queue.json, never persists ORDER_QUEUED, and never calls
SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_QUEUE_WRITE_REQUIRED = "QUEUE_WRITE_REQUIRED"
NEXT_STAGE_QUEUE_COMMITTED_REVIEW_REQUIRED = "QUEUE_COMMITTED_REVIEW_REQUIRED"


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


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
) -> dict[str, Any]:
    """Commit an ORDER_QUEUED record preview to the explicit queue_path only."""
    if not _manual_write_confirmed(context):
        return _commit_blocked("manual_confirm", "manual queue write confirmation is required")

    preview, record, blocked = _validate_write_preview(queue_write_preview_result)
    if blocked is not None:
        return blocked

    target_path = Path(queue_path)
    data, read_blocked = _read_queue_file(target_path)
    if read_blocked is not None:
        return read_blocked

    orders = data["orders"]
    duplicate_reason = _existing_duplicate_reason(
        orders,
        request_hash=_clean_text(record.get("request_hash")),
        lock_id=_clean_text(record.get("lock_id")),
        order_id=_clean_text(record.get("order_id")),
    )
    if duplicate_reason:
        return _commit_blocked("duplicate", duplicate_reason)

    backup_path = None
    if backup:
        backup_path = str(target_path) + ".bak"
        try:
            shutil.copy2(target_path, backup_path)
        except Exception as exc:
            return _commit_blocked("backup", f"failed to create backup: {exc}")

    updated_data = deepcopy(data)
    updated_data["version"] = updated_data.get("version", 1)
    updated_data["updated_at"] = _now_text()
    updated_data["orders"].append(deepcopy(record))

    try:
        _write_json_atomic(target_path, updated_data)
    except Exception as exc:
        return _commit_blocked("write_queue", f"failed to write order_queue json: {exc}")

    return {
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
        "blocked_reasons": [],
        "warnings": [],
    }
