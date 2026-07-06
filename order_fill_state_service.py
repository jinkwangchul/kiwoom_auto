# -*- coding: utf-8 -*-
"""Review order fill-state candidates after fill and position updates.

This module only returns an in-memory review dictionary. It never writes
order_queue, fills, positions, GUI state, timers, Chejan handlers, or broker
order APIs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_COMMIT_REQUIRED = "ORDER_FILL_STATE_COMMIT_REQUIRED"
NEXT_STAGE_LIFECYCLE_REVIEW_REQUIRED = "ORDER_LIFECYCLE_REVIEW_REQUIRED"
POSITION_NEXT_STAGE_REQUIRED = "ORDER_FILL_STATE_REVIEW_REQUIRED"
FILL_NEXT_STAGE_REQUIRED = "POSITION_UPDATE_REQUIRED"
_FILL_EVENT_TYPES = {"PARTIAL_FILL", "FULL_FILL"}
_ORDER_STATUSES = {"ORDER_QUEUED", "PARTIALLY_FILLED"}
_ALLOWED_TRANSITIONS = {
    ("ORDER_QUEUED", "PARTIALLY_FILLED"),
    ("ORDER_QUEUED", "FILLED"),
    ("PARTIALLY_FILLED", "FILLED"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "order_fill_state_review_ok": False,
        "fill_state_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _commit_blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "order_fill_state_committed": False,
        "fill_state_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _read_blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": "ORDER_FILL_STATE_READ",
        "read_stage": stage,
        "order": None,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _commit_confirmed(context: Any) -> bool:
    return _as_dict(context).get("manual_order_fill_state_commit_confirmed") is True


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _read_queue(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {}, _commit_blocked("read_queue", "queue file does not exist")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def _validate_position_update_result(result_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(result_value)
    if not isinstance(result_value, dict):
        return result, _blocked("position_update", "position_update_result must be a dict")

    if result.get("position_updated") is not True:
        return result, _blocked("position_update", "position_update_result.position_updated is not true")

    if result.get("next_stage") != POSITION_NEXT_STAGE_REQUIRED:
        return result, _blocked(
            "position_update",
            "position_update_result.next_stage is not ORDER_FILL_STATE_REVIEW_REQUIRED",
        )

    return result, None


def _validate_fill_record_result(result_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(result_value)
    if not isinstance(result_value, dict):
        return result, _blocked("fill_record_result", "fill_record_result must be a dict")

    if result.get("fill_recorded") is not True:
        return result, _blocked("fill_record_result", "fill_record_result.fill_recorded is not true")

    if result.get("next_stage") != FILL_NEXT_STAGE_REQUIRED:
        return result, _blocked(
            "fill_record_result",
            "fill_record_result.next_stage is not POSITION_UPDATE_REQUIRED",
        )

    return result, None


def _validate_fill_record(fill_value: Any) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    fill = _as_dict(fill_value)
    if not isinstance(fill_value, dict):
        return fill, "", _blocked("fill_record", "fill_record must be a dict")

    event_type = _clean_text(fill.get("event_type"))
    if event_type not in _FILL_EVENT_TYPES:
        return fill, event_type, _blocked("event_type", "fill_record.event_type is not fill state reviewable")

    filled_quantity = fill.get("filled_quantity")
    if not isinstance(filled_quantity, int) or filled_quantity <= 0:
        return fill, event_type, _blocked("quantity", "fill_record.filled_quantity must be greater than 0")

    remaining_quantity = fill.get("remaining_quantity")
    if not isinstance(remaining_quantity, int):
        return fill, event_type, _blocked("remaining_quantity", "fill_record.remaining_quantity is required")

    if event_type == "PARTIAL_FILL" and remaining_quantity <= 0:
        return fill, event_type, _blocked("remaining_quantity", "PARTIAL_FILL remaining_quantity must be greater than 0")

    if event_type == "FULL_FILL" and remaining_quantity != 0:
        return fill, event_type, _blocked("remaining_quantity", "FULL_FILL remaining_quantity must be 0")

    return fill, event_type, None


def _validate_order_record(order_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    order = _as_dict(order_value)
    if not isinstance(order_value, dict):
        return order, _blocked("order_record", "order_record must be a dict")

    if _clean_text(order.get("status")) not in _ORDER_STATUSES:
        return order, _blocked("order_record", "order_record.status is not fill state reviewable")

    return order, None


def _identity_mismatch(
    position_update_result: dict[str, Any],
    fill_record_result: dict[str, Any],
    fill_record: dict[str, Any],
    order_record: dict[str, Any],
) -> str | None:
    for field in ("fill_id",):
        position_value = _clean_text(position_update_result.get(field))
        fill_value = _clean_text(fill_record.get(field))
        if not position_value or not fill_value:
            return f"{field} is required"
        if position_value != fill_value:
            return f"position_update_result.{field} does not match fill_record.{field}"

    for field in ("order_id", "order_queued_id", "request_hash", "lock_id", "execution_id"):
        fill_result_value = _clean_text(fill_record_result.get(field))
        fill_value = _clean_text(fill_record.get(field))
        order_field = "id" if field == "order_queued_id" else field
        order_value = _clean_text(order_record.get(order_field))
        if not fill_result_value or not fill_value or not order_value:
            return f"{field} is required"
        if fill_result_value != fill_value:
            return f"fill_record_result.{field} does not match fill_record.{field}"
        if fill_value != order_value:
            return f"fill_record.{field} does not match order_record.{order_field}"

    return None


def _status_candidate(event_type: str) -> str:
    if event_type == "FULL_FILL":
        return "FILLED"
    return "PARTIALLY_FILLED"


def _validate_commit_review_result(review_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(review_value)
    if not isinstance(review_value, dict):
        return result, _commit_blocked("review_result", "order_fill_state_review_result must be a dict")

    if result.get("order_fill_state_review_ok") is not True:
        return result, _commit_blocked("review_result", "order_fill_state_review_result.order_fill_state_review_ok is not true")

    if result.get("next_stage") != NEXT_STAGE_COMMIT_REQUIRED:
        return result, _commit_blocked(
            "review_result",
            "order_fill_state_review_result.next_stage is not ORDER_FILL_STATE_COMMIT_REQUIRED",
        )

    status_candidate = _clean_text(result.get("status_candidate"))
    if status_candidate not in {"PARTIALLY_FILLED", "FILLED"}:
        return result, _commit_blocked("review_result", "status_candidate is not fill state committable")

    event_type = _clean_text(result.get("event_type"))
    if event_type not in _FILL_EVENT_TYPES:
        return result, _commit_blocked("review_result", "event_type is not fill state committable")

    for field in ("order_id", "order_queued_id", "fill_id"):
        if not _clean_text(result.get(field)):
            return result, _commit_blocked("review_result", f"{field} is required")

    total = result.get("total_filled_quantity_candidate")
    if not isinstance(total, int) or total <= 0:
        return result, _commit_blocked("review_result", "total_filled_quantity_candidate must be greater than 0")

    remaining = result.get("remaining_quantity_candidate")
    if not isinstance(remaining, int) or remaining < 0:
        return result, _commit_blocked("review_result", "remaining_quantity_candidate is invalid")

    if status_candidate == "PARTIALLY_FILLED" and remaining <= 0:
        return result, _commit_blocked("review_result", "PARTIALLY_FILLED requires remaining quantity")

    if status_candidate == "FILLED" and remaining != 0:
        return result, _commit_blocked("review_result", "FILLED requires zero remaining quantity")

    return result, None


def _find_order(orders: list[Any], review_result: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    order_queued_id = _clean_text(review_result.get("order_queued_id"))
    order_id = _clean_text(review_result.get("order_id"))
    for index, order in enumerate(orders):
        item = _as_dict(order)
        if order_queued_id and _clean_text(item.get("id")) == order_queued_id:
            return item, index

    for index, order in enumerate(orders):
        item = _as_dict(order)
        if order_id and _clean_text(item.get("order_id")) == order_id:
            return item, index

    return None, -1


def _validate_transition(current_status: str, next_status: str) -> dict[str, Any] | None:
    if (current_status, next_status) not in _ALLOWED_TRANSITIONS:
        return _commit_blocked("transition", f"transition {current_status} -> {next_status} is not allowed")
    return None


def _read_queue_for_fill_state(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {}, _read_blocked("read_queue", "queue file does not exist")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _read_blocked("read_queue", f"failed to read order_queue json: {exc}")

    if not isinstance(data, dict):
        return {}, _read_blocked("read_queue", "order_queue root must be an object")

    orders = data.get("orders")
    if not isinstance(orders, list):
        return {}, _read_blocked("read_queue", "order_queue orders must be a list")

    for item in orders:
        if not isinstance(item, dict):
            return {}, _read_blocked("read_queue", "order_queue orders must contain only objects")

    return data, None


def get_order_fill_state(order_id: Any, queue_path: str | Path | None) -> dict[str, Any]:
    """Read a single order's committed fill-state fields from an explicit queue path."""
    target_order_id = _clean_text(order_id)
    if not target_order_id:
        return _read_blocked("order_id", "order_id is required")

    if queue_path is None or not str(queue_path).strip():
        return _read_blocked("queue_path", "queue_path is required")

    data, read_blocked = _read_queue_for_fill_state(Path(queue_path))
    if read_blocked is not None:
        return read_blocked

    for order in data["orders"]:
        if _clean_text(order.get("order_id")) == target_order_id or _clean_text(order.get("id")) == target_order_id:
            order_state = {
                "order_id": _clean_text(order.get("order_id")),
                "order_queued_id": _clean_text(order.get("id")),
                "status": _clean_text(order.get("status")),
                "fill_state": order.get("fill_state"),
                "total_filled_quantity": order.get("total_filled_quantity"),
                "remaining_quantity": order.get("remaining_quantity"),
                "last_fill_id": order.get("last_fill_id"),
                "updated_at": order.get("updated_at"),
                "filled_at": order.get("filled_at"),
            }
            return {
                "ok": True,
                "stage": "ORDER_FILL_STATE_READ",
                "read_stage": "order_fill_state_read",
                "order": order_state,
                **order_state,
                "blocked_reasons": [],
                "warnings": [],
            }

    return _read_blocked("not_found", "order_id was not found")


def review_order_fill_state(
    position_update_result: Any,
    fill_record_result: Any,
    fill_record: Any,
    order_record: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Review an order fill-state candidate without mutating any inputs."""
    del context

    position_result, position_blocked = _validate_position_update_result(position_update_result)
    if position_blocked is not None:
        return position_blocked

    fill_result, fill_result_blocked = _validate_fill_record_result(fill_record_result)
    if fill_result_blocked is not None:
        return fill_result_blocked

    fill, event_type, fill_blocked = _validate_fill_record(fill_record)
    if fill_blocked is not None:
        return fill_blocked

    order, order_blocked = _validate_order_record(order_record)
    if order_blocked is not None:
        return order_blocked

    mismatch = _identity_mismatch(position_result, fill_result, fill, order)
    if mismatch:
        return _blocked("identity", mismatch)

    previous_total = order.get("total_filled_quantity", 0)
    if not isinstance(previous_total, int):
        previous_total = 0
    total_filled_quantity_candidate = previous_total + fill["filled_quantity"]

    return {
        "order_fill_state_review_ok": True,
        "fill_state_stage": "order_fill_state_reviewed",
        "next_stage": NEXT_STAGE_COMMIT_REQUIRED,
        "status_candidate": _status_candidate(event_type),
        "order_id": _clean_text(fill.get("order_id")),
        "order_queued_id": _clean_text(fill.get("order_queued_id")),
        "fill_id": _clean_text(fill.get("fill_id")),
        "event_type": event_type,
        "total_filled_quantity_candidate": total_filled_quantity_candidate,
        "remaining_quantity_candidate": fill["remaining_quantity"],
        "blocked_reasons": [],
        "warnings": [],
    }


def commit_order_fill_state(
    order_fill_state_review_result: Any,
    queue_path: str | Path | None,
    queue_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Commit a reviewed fill-state candidate to the explicit queue_path only."""
    review_result, review_blocked = _validate_commit_review_result(order_fill_state_review_result)
    if review_blocked is not None:
        return review_blocked

    if queue_path is None or not str(queue_path).strip():
        return _commit_blocked("queue_path", "queue_path is required")

    if not _commit_confirmed(context):
        return _commit_blocked("operator_confirmation", "manual order fill state commit confirmation is required")

    target_path = Path(queue_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(queue_snapshot)
    if snapshot_sha256 and before_sha256 != snapshot_sha256:
        return _commit_blocked(
            "stale_queue",
            "order_queue file changed after order fill state review; manual review required",
        )

    data, read_blocked = _read_queue(target_path)
    if read_blocked is not None:
        return read_blocked

    orders = data["orders"]
    target_order, target_index = _find_order(orders, review_result)
    if target_order is None or target_index < 0:
        return _commit_blocked("order_record", "target order record not found")

    if _clean_text(target_order.get("id")) != _clean_text(review_result.get("order_queued_id")):
        return _commit_blocked("identity", "target order id does not match review order_queued_id")

    if _clean_text(target_order.get("order_id")) != _clean_text(review_result.get("order_id")):
        return _commit_blocked("identity", "target order_id does not match review order_id")

    before_status = _clean_text(target_order.get("status"))
    after_status = _clean_text(review_result.get("status_candidate"))
    transition_blocked = _validate_transition(before_status, after_status)
    if transition_blocked is not None:
        return transition_blocked

    backup_path = None
    if backup:
        backup_path = str(target_path) + ".bak"
        try:
            shutil.copy2(target_path, backup_path)
        except Exception as exc:
            return _commit_blocked("backup", f"failed to create backup: {exc}")

    now = _now_text()
    updated_data = deepcopy(data)
    updated_order = deepcopy(updated_data["orders"][target_index])
    updated_order["status"] = after_status
    updated_order["fill_state"] = _clean_text(review_result.get("event_type"))
    updated_order["last_fill_id"] = _clean_text(review_result.get("fill_id"))
    updated_order["total_filled_quantity"] = review_result.get("total_filled_quantity_candidate")
    updated_order["remaining_quantity"] = review_result.get("remaining_quantity_candidate")
    updated_order["order_fill_state_updated_at"] = now
    updated_order["updated_at"] = now
    if after_status == "FILLED":
        updated_order["filled_at"] = now

    updated_data["orders"][target_index] = updated_order
    updated_data["version"] = updated_data.get("version", 1)
    updated_data["updated_at"] = now

    try:
        _write_json_atomic(target_path, updated_data)
    except Exception as exc:
        return _commit_blocked("write_queue", f"failed to write order_queue json: {exc}")

    after_sha256 = _sha256_file(target_path)
    return {
        "order_fill_state_committed": True,
        "fill_state_stage": "order_fill_state_committed",
        "next_stage": NEXT_STAGE_LIFECYCLE_REVIEW_REQUIRED,
        "changed": True,
        "queue_path": str(target_path),
        "backup_path": backup_path,
        "order_id": _clean_text(review_result.get("order_id")),
        "order_queued_id": _clean_text(review_result.get("order_queued_id")),
        "before_status": before_status,
        "after_status": after_status,
        "fill_id": _clean_text(review_result.get("fill_id")),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
