# -*- coding: utf-8 -*-
"""Record PARTIAL_FILL/FULL_FILL events to an explicit fills file.

This module only appends fill ledger records to the provided fill_path. It does
not update positions, order_queue status, GUI flows, timers, Chejan handlers, or
broker order APIs.
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
NEXT_STAGE_POSITION_UPDATE_REQUIRED = "POSITION_UPDATE_REQUIRED"
CHEJAN_EVENT_NEXT_STAGE_REQUIRED = "FILL_RECORD_REQUIRED"
_FILL_EVENT_TYPES = {"PARTIAL_FILL", "FULL_FILL"}


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
        "fill_recorded": False,
        "fill_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _confirmed(context: Any) -> bool:
    return _as_dict(context).get("manual_fill_record_confirmed") is True


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


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


def _read_fills(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {"version": 1, "updated_at": None, "fills": []}, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _blocked("read_fills", f"failed to read fills json: {exc}")

    if not isinstance(data, dict):
        return {}, _blocked("read_fills", "fills root must be an object")

    fills = data.get("fills")
    if not isinstance(fills, list):
        return {}, _blocked("read_fills", "fills must be a list")

    for item in fills:
        if not isinstance(item, dict):
            return {}, _blocked("read_fills", "fills must contain only objects")

    return data, None


def _validate_event_record_result(result_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(result_value)
    if not isinstance(result_value, dict):
        return result, _blocked("chejan_event_record_result", "chejan_event_record_result must be a dict")

    if result.get("recorded") is not True:
        return result, _blocked("chejan_event_record_result", "chejan_event_record_result.recorded is not true")

    if result.get("next_stage") != CHEJAN_EVENT_NEXT_STAGE_REQUIRED:
        return result, _blocked(
            "chejan_event_record_result",
            "chejan_event_record_result.next_stage is not FILL_RECORD_REQUIRED",
        )

    return result, None


def _received_at(event: dict[str, Any]) -> str:
    raw_event = _as_dict(event.get("raw_event"))
    return _clean_text(event.get("received_at")) or _clean_text(raw_event.get("received_at"))


def _required_text(event: dict[str, Any], field: str) -> str | None:
    value = _clean_text(event.get(field))
    if not value:
        return f"normalized_event.{field} is required"
    return None


def _required_int(event: dict[str, Any], field: str) -> str | None:
    value = event.get(field)
    if not isinstance(value, int):
        return f"normalized_event.{field} is required"
    return None


def _validate_normalized_event(event_value: Any) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    event = _as_dict(event_value)
    if not isinstance(event_value, dict):
        return event, "", _blocked("normalized_event", "normalized_event must be a dict")

    event_type = _clean_text(event.get("event_type"))
    if event_type not in _FILL_EVENT_TYPES:
        return event, event_type, _blocked("event_type", "normalized_event.event_type is not fill recordable")

    for field in ("broker_order_no", "account_no", "code", "side"):
        reason = _required_text(event, field)
        if reason:
            return event, event_type, _blocked("normalized_event", reason)

    for field in ("filled_quantity", "filled_price", "remaining_quantity", "order_quantity"):
        reason = _required_int(event, field)
        if reason:
            return event, event_type, _blocked("normalized_event", reason)

    if not _received_at(event):
        return event, event_type, _blocked("normalized_event", "normalized_event.received_at is required")

    if event["filled_quantity"] <= 0:
        return event, event_type, _blocked("quantity", "filled_quantity must be greater than 0")

    if event["filled_price"] <= 0:
        return event, event_type, _blocked("price", "filled_price must be greater than 0")

    if event_type == "PARTIAL_FILL" and event["remaining_quantity"] <= 0:
        return event, event_type, _blocked("remaining_quantity", "PARTIAL_FILL remaining_quantity must be greater than 0")

    if event_type == "FULL_FILL" and event["remaining_quantity"] != 0:
        return event, event_type, _blocked("remaining_quantity", "FULL_FILL remaining_quantity must be 0")

    return event, event_type, None


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _fill_id(event: dict[str, Any], event_type: str, received_at: str) -> str:
    received_hash = hashlib.sha256(received_at.encode("utf-8")).hexdigest().upper()[:12]
    parts = [
        "FILL",
        _clean_text(event.get("broker_order_no")),
        event_type,
        str(event.get("filled_quantity")),
        str(event.get("remaining_quantity")),
        str(event.get("filled_price")),
        received_hash,
    ]
    return "_".join(parts)


def _composite_key(record: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        _clean_text(record.get("broker_order_no")),
        _clean_text(record.get("event_type")),
        str(record.get("filled_quantity")),
        str(record.get("remaining_quantity")),
        str(record.get("filled_price")),
        _clean_text(record.get("received_at")),
    )


def _duplicate_reason(fills: list[Any], candidate: dict[str, Any]) -> str | None:
    candidate_fill_id = _clean_text(candidate.get("fill_id"))
    candidate_key = _composite_key(candidate)
    candidate_event_hash = _stable_hash(candidate.get("normalized_event"))

    for fill in fills:
        item = _as_dict(fill)
        if _clean_text(item.get("fill_id")) == candidate_fill_id:
            return "duplicate fill_id"

    for fill in fills:
        item = _as_dict(fill)
        if _composite_key(item) == candidate_key:
            return "duplicate fill composite key"

    for fill in fills:
        item = _as_dict(fill)
        if _stable_hash(item.get("normalized_event")) == candidate_event_hash:
            return "duplicate normalized_event"

    return None


def _fill_record(
    *,
    result: dict[str, Any],
    event: dict[str, Any],
    event_type: str,
    received_at: str,
    recorded_at: str,
) -> dict[str, Any]:
    fill_id = _fill_id(event, event_type, received_at)
    return {
        "fill_id": fill_id,
        "fill_source": "chejan_event",
        "event_type": event_type,
        "broker": _clean_text(event.get("broker")),
        "broker_order_no": _clean_text(event.get("broker_order_no")),
        "order_id": _clean_text(result.get("order_id")),
        "order_queued_id": _clean_text(result.get("order_queued_id")),
        "execution_id": _clean_text(result.get("execution_id")),
        "request_hash": _clean_text(result.get("request_hash")),
        "lock_id": _clean_text(result.get("lock_id")),
        "account_no": _clean_text(event.get("account_no")),
        "code": _clean_text(event.get("code")),
        "side": _clean_text(event.get("side")),
        "filled_quantity": event.get("filled_quantity"),
        "filled_price": event.get("filled_price"),
        "remaining_quantity": event.get("remaining_quantity"),
        "order_quantity": event.get("order_quantity"),
        "order_price": event.get("order_price"),
        "received_at": received_at,
        "recorded_at": recorded_at,
        "normalized_event": deepcopy(event),
    }


def record_execution_fill(
    chejan_event_record_result: Any,
    normalized_event: Any,
    fill_path: str | Path | None,
    fill_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Append a fill ledger record to the explicit fill_path only."""
    result, result_blocked = _validate_event_record_result(chejan_event_record_result)
    if result_blocked is not None:
        return result_blocked

    event, event_type, event_blocked = _validate_normalized_event(normalized_event)
    if event_blocked is not None:
        return event_blocked

    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual fill record confirmation is required")

    if fill_path is None or not str(fill_path).strip():
        return _blocked("fill_path", "fill_path is required")

    target_path = Path(fill_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(fill_snapshot)
    if snapshot_sha256 and before_sha256 != snapshot_sha256:
        return _blocked(
            "stale_fills",
            "fills file changed after Chejan event record; manual review required",
        )

    data, read_blocked = _read_fills(target_path)
    if read_blocked is not None:
        return read_blocked

    now = _now_text()
    received_at = _received_at(event)
    fill_record = _fill_record(
        result=result,
        event=event,
        event_type=event_type,
        received_at=received_at,
        recorded_at=now,
    )

    duplicate_reason = _duplicate_reason(data["fills"], fill_record)
    if duplicate_reason:
        return _blocked("duplicate", duplicate_reason)

    backup_path = None
    if backup and target_path.exists():
        backup_path = str(target_path) + ".bak"
        try:
            shutil.copy2(target_path, backup_path)
        except Exception as exc:
            return _blocked("backup", f"failed to create backup: {exc}")

    updated_data = deepcopy(data)
    updated_data["version"] = updated_data.get("version", 1)
    updated_data["updated_at"] = now
    updated_data["fills"].append(fill_record)

    try:
        _write_json_atomic(target_path, updated_data)
    except Exception as exc:
        return _blocked("write_fills", f"failed to write fills json: {exc}")

    after_sha256 = _sha256_file(target_path)
    return {
        "fill_recorded": True,
        "fill_stage": "execution_fill_recorded",
        "next_stage": NEXT_STAGE_POSITION_UPDATE_REQUIRED,
        "changed": True,
        "fill_path": str(target_path),
        "backup_path": backup_path,
        "fill_id": fill_record["fill_id"],
        "event_type": event_type,
        "order_id": fill_record["order_id"],
        "order_queued_id": fill_record["order_queued_id"],
        "broker_order_no": fill_record["broker_order_no"],
        "request_hash": fill_record["request_hash"],
        "lock_id": fill_record["lock_id"],
        "execution_id": fill_record["execution_id"],
        "filled_quantity": fill_record["filled_quantity"],
        "filled_price": fill_record["filled_price"],
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
