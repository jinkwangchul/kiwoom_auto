# -*- coding: utf-8 -*-
"""Preview-only execution enable gate.

This module only evaluates whether an EXECUTABLE order is eligible for a
manual execution_enabled commit. It never mutates orders, writes runtime files,
creates REAL_READY, or connects timers.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_COMMIT_REQUIRED = "EXECUTION_ENABLE_COMMIT_REQUIRED"
NEXT_STAGE_REAL_PREFLIGHT_REQUIRED = "REAL_PREFLIGHT_REQUIRED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _quantity(value: Any) -> int | None:
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return None
    return qty


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "enable_preview": False,
        "enable_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _commit_blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "enable_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _commit_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_execution_enable_commit_confirmed") is True
        or ctx.get("operator_confirmed_for_execution_enable_commit") is True
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_queue_file(queue_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not queue_path.exists():
        return {}, _commit_blocked("read_queue", "queue file does not exist")

    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _commit_blocked("read_queue", f"failed to read order_queue json: {exc}")

    if not isinstance(data, dict):
        return {}, _commit_blocked("read_queue", "order_queue root must be an object")

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


def preview_execution_enable(order: Any, context: Any = None) -> dict[str, Any]:
    """Preview whether execution_enabled can be manually committed later."""
    order_dict = _as_dict(order)
    context_dict = _as_dict(context)

    status = _clean_text(order_dict.get("status")).upper()
    if status != "EXECUTABLE":
        return _blocked("status", "order.status must be EXECUTABLE")

    if order_dict.get("execution_enabled") is not False:
        return _blocked("execution_enabled", "order.execution_enabled must be false")

    qty = _quantity(order_dict.get("quantity"))
    if qty is None or qty <= 0:
        return _blocked("quantity", "order.quantity must be greater than 0")

    side = _clean_text(order_dict.get("side")).upper()
    if side not in {"BUY", "SELL"}:
        return _blocked("side", "order.side must be BUY or SELL")

    order_type = _clean_text(order_dict.get("order_type"))
    if not order_type:
        return _blocked("order_type", "order.order_type is required")

    code = _clean_text(order_dict.get("code"))
    if not code:
        return _blocked("code", "order.code is required")

    source_signal_id = _clean_text(order_dict.get("source_signal_id"))
    if not source_signal_id:
        return _blocked("source_signal_id", "order.source_signal_id is required")

    approval_status = _clean_text(order_dict.get("approval_status")).upper()
    if approval_status != "APPROVED":
        return _blocked("approval_status", "order.approval_status must be APPROVED")

    policy_status = _clean_text(order_dict.get("policy_status")).upper()
    if policy_status != "EXECUTABLE":
        return _blocked("policy_status", "order.policy_status must be EXECUTABLE")

    if not _truthy(context_dict.get("operator_confirmed_for_execution_enable")):
        return _blocked(
            "operator_confirmation",
            "context.operator_confirmed_for_execution_enable is not true",
        )

    return {
        "enable_preview": True,
        "enable_stage": "execution_enable_preview_created",
        "next_stage": NEXT_STAGE_COMMIT_REQUIRED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "warnings": [],
        "order_id": _clean_text(order_dict.get("id")),
        "source_signal_id": source_signal_id,
        "code": code,
        "side": side,
        "quantity": qty,
        "order_type": order_type,
    }


def commit_execution_enable(
    enable_preview_result: Any,
    queue_path: str | Path | None,
    preview_queue_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Commit execution_enabled=True to one explicit queue file."""
    preview = _as_dict(enable_preview_result)
    if preview.get("enable_preview") is not True:
        return _commit_blocked("enable_preview", "enable_preview_result.enable_preview is not true")

    if preview.get("next_stage") != NEXT_STAGE_COMMIT_REQUIRED:
        return _commit_blocked("enable_preview", "enable_preview_result.next_stage is not EXECUTION_ENABLE_COMMIT_REQUIRED")

    if queue_path is None or not str(queue_path).strip():
        return _commit_blocked("queue_path", "queue_path is required")

    if not _commit_confirmed(context):
        return _commit_blocked("operator_confirmation", "manual execution enable commit confirmation is required")

    target_path = Path(queue_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot = _as_dict(preview_queue_snapshot)
    if snapshot:
        snapshot_sha256 = _clean_text(snapshot.get("sha256")).upper()
        if not snapshot_sha256:
            return _commit_blocked("stale_preview", "preview_queue_snapshot.sha256 is required")
        if before_sha256 != snapshot_sha256:
            return _commit_blocked(
                "stale_preview",
                "queue file changed after execution enable preview; rerun preview",
            )

    data, blocked = _read_queue_file(target_path)
    if blocked is not None:
        return blocked

    order_id = _clean_text(preview.get("order_id"))
    if not order_id:
        return _commit_blocked("order_id", "enable_preview_result.order_id is required")

    orders = data["orders"]
    target_order = None
    for order in orders:
        if _clean_text(order.get("id")) == order_id:
            target_order = order
            break

    if target_order is None:
        return _commit_blocked("order", "target order not found")

    validation = preview_execution_enable(
        target_order,
        {"operator_confirmed_for_execution_enable": True},
    )
    if validation.get("enable_preview") is not True:
        reasons = validation.get("blocked_reasons") if isinstance(validation.get("blocked_reasons"), list) else []
        reason = reasons[0] if reasons else "target order is not eligible for execution enable"
        return _commit_blocked(str(validation.get("enable_stage", "order")), reason)

    updated_data = deepcopy(data)
    updated_order = None
    for order in updated_data["orders"]:
        if _clean_text(order.get("id")) == order_id:
            updated_order = order
            break

    if updated_order is None:
        return _commit_blocked("order", "target order not found")

    before_status = _clean_text(updated_order.get("status")).upper()
    before_execution_enabled = updated_order.get("execution_enabled")
    updated_order["execution_enabled"] = True

    backup_path = None
    if backup:
        backup_path = str(target_path) + ".bak"
        try:
            shutil.copy2(target_path, backup_path)
        except Exception as exc:
            return _commit_blocked("backup", f"failed to create backup: {exc}")

    try:
        _write_json_atomic(target_path, updated_data)
    except Exception as exc:
        return _commit_blocked("write_queue", f"failed to write order_queue json: {exc}")

    after_sha256 = _sha256_file(target_path)
    return {
        "enabled": True,
        "enable_stage": "execution_enabled_committed",
        "next_stage": NEXT_STAGE_REAL_PREFLIGHT_REQUIRED,
        "changed": before_sha256 != after_sha256,
        "order_queue_path": str(target_path),
        "backup_path": backup_path,
        "order_id": order_id,
        "before_status": before_status,
        "after_status": _clean_text(updated_order.get("status")).upper(),
        "before_execution_enabled": before_execution_enabled,
        "after_execution_enabled": updated_order.get("execution_enabled"),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
