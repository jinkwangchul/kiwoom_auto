# -*- coding: utf-8 -*-
"""Record a send-order entrypoint result to an explicit queue file.

This module only records an already-returned entrypoint result. It never calls
SendOrder, never calls send_order_entrypoint, never uses a default runtime
queue path, and never connects GUI or timer flows.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from execution_queue_writer import mutate_order_queue, preserve_queue_mutation_result


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_RESULT_REVIEW_REQUIRED = "SEND_ORDER_RESULT_REVIEW_REQUIRED"
NEXT_STAGE_UNCERTAIN_REVIEW_REQUIRED = "BROKER_CALL_UNCERTAIN_REVIEW_REQUIRED"
RESULT_STATUS_CALLED = "SEND_ORDER_CALLED"


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
        "recorded": False,
        "record_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "send_order_called": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _confirmed(context: Any) -> bool:
    return _as_dict(context).get("manual_send_order_result_record_confirmed") is True


def _expected_revision(context: Any) -> int | None:
    value = _as_dict(context).get("expected_revision")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _read_queue(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {}, _blocked("read_queue", "queue file does not exist")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _blocked("read_queue", f"failed to read order_queue json: {exc}")

    if not isinstance(data, dict):
        return {}, _blocked("read_queue", "order_queue root must be an object")

    orders = data.get("orders")
    if not isinstance(orders, list):
        return {}, _blocked("read_queue", "order_queue orders must be a list")

    for item in orders:
        if not isinstance(item, dict):
            return {}, _blocked("read_queue", "order_queue orders must contain only objects")

    return data, None


def _validate_entrypoint_result(entrypoint_result: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(entrypoint_result)
    if not isinstance(entrypoint_result, dict):
        return result, _blocked("entrypoint_result", "entrypoint_result must be a dict")

    if result.get("next_stage") == NEXT_STAGE_UNCERTAIN_REVIEW_REQUIRED:
        return result, _blocked("entrypoint_result", "uncertain broker call results are not recorded by this recorder")

    if result.get("send_order_executed") is not True:
        return result, _blocked("entrypoint_result", "entrypoint_result.send_order_executed is not true")

    if result.get("send_order_called") is not True:
        return result, _blocked("entrypoint_result", "entrypoint_result.send_order_called is not true")

    if result.get("runtime_write_required") is not True:
        return result, _blocked("entrypoint_result", "entrypoint_result.runtime_write_required is not true")

    if result.get("next_stage") != NEXT_STAGE_RESULT_REVIEW_REQUIRED:
        return result, _blocked(
            "entrypoint_result",
            "entrypoint_result.next_stage is not SEND_ORDER_RESULT_REVIEW_REQUIRED",
        )

    return result, None


def _find_target_order(orders: list[Any], entrypoint_result: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    order_queued_id = _clean_text(entrypoint_result.get("order_queued_id"))
    order_id = _clean_text(entrypoint_result.get("order_id"))
    request_hash = _clean_text(entrypoint_result.get("request_hash"))
    lock_id = _clean_text(entrypoint_result.get("lock_id"))
    execution_id = _clean_text(entrypoint_result.get("execution_id"))

    for index, order in enumerate(orders):
        item = _as_dict(order)
        if order_queued_id and _clean_text(item.get("id")) == order_queued_id:
            return item, index

    for index, order in enumerate(orders):
        item = _as_dict(order)
        if (
            _clean_text(item.get("order_id")) == order_id
            and _clean_text(item.get("request_hash")) == request_hash
            and _clean_text(item.get("lock_id")) == lock_id
            and _clean_text(item.get("execution_id")) == execution_id
        ):
            return item, index

    return None, -1


def _validate_target_record(record: dict[str, Any], entrypoint_result: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("status") != "ORDER_QUEUED":
        return _blocked("record", "target record.status is not ORDER_QUEUED")

    if record.get("send_order_called") is not False:
        return _blocked("record", "target record.send_order_called is not false")

    if record.get("execution_enabled") is not False:
        return _blocked("record", "target record.execution_enabled is not false")

    for field in ("order_id", "request_hash", "lock_id", "execution_id"):
        if _clean_text(record.get(field)) != _clean_text(entrypoint_result.get(field)):
            return _blocked("record_consistency", f"target record.{field} does not match entrypoint_result.{field}")

    return None


def _broker_order_no(entrypoint_result: dict[str, Any]) -> str | None:
    broker_result = _as_dict(entrypoint_result.get("broker_result"))
    for key in ("broker_order_no", "order_no", "order_number"):
        value = _clean_text(broker_result.get(key))
        if value:
            return value
    return None


def record_send_order_result(
    entrypoint_result: Any,
    queue_path: str | Path | None,
    queue_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Record a successful entrypoint result to an explicit queue file."""
    result, blocked = _validate_entrypoint_result(entrypoint_result)
    if blocked is not None:
        return blocked

    if queue_path is None or not str(queue_path).strip():
        return _blocked("queue_path", "queue_path is required")

    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual send order result record confirmation is required")

    target_path = Path(queue_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(queue_snapshot)
    if snapshot_sha256:
        if before_sha256 != snapshot_sha256:
            return _blocked(
                "stale_queue",
                "queue file changed after send order entrypoint; manual review required",
            )

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        if snapshot_sha256 and _sha256_file(target_path) != snapshot_sha256:
            return {
                "blocked": _blocked(
                    "stale_queue",
                    "queue file changed after send order entrypoint; manual review required",
                )
            }

        orders = data["orders"]
        target_record, target_index = _find_target_order(orders, result)
        if target_record is None or target_index < 0:
            return {"blocked": _blocked("record", "target record not found")}

        record_blocked = _validate_target_record(target_record, result)
        if record_blocked is not None:
            return {"blocked": record_blocked}

        now = _now_text()
        updated_data = deepcopy(data)
        updated_record = deepcopy(updated_data["orders"][target_index])
        updated_record["send_order_called"] = True
        updated_record["send_order_called_at"] = now
        updated_record["send_order_entrypoint_stage"] = _clean_text(result.get("entrypoint_stage"))
        updated_record["send_order_result_status"] = RESULT_STATUS_CALLED
        updated_record["send_order_result_recorded_at"] = now
        updated_record["broker"] = _clean_text(result.get("broker"))
        updated_record["broker_result"] = deepcopy(_as_dict(result.get("broker_result")))
        updated_record["broker_order_no"] = _broker_order_no(result)
        updated_record["send_order_record_source"] = "send_order_entrypoint"
        updated_record["updated_at"] = now
        updated_data["orders"][target_index] = updated_record
        return {"data": updated_data}

    mutation_result = mutate_order_queue(
        target_path,
        mutate,
        operation_name="target_record_send_result_update",
        success_stage="send_order_result_recorded",
        next_stage=NEXT_STAGE_RESULT_REVIEW_REQUIRED,
        backup=backup,
        context=context,
        expected_revision=_expected_revision(context),
    )
    if mutation_result.get("committed") is not True or mutation_result.get("post_write_verified") is not True:
        stage = _clean_text(mutation_result.get("record_stage") or mutation_result.get("write_stage")) or "write_queue"
        reasons = mutation_result.get("blocked_reasons") if isinstance(mutation_result.get("blocked_reasons"), list) else []
        blocked = _blocked(stage, reasons[0] if reasons else "queue mutation failed")
        blocked.update({key: value for key, value in mutation_result.items() if key not in blocked})
        return preserve_queue_mutation_result(blocked, mutation_result)

    after_sha256 = _sha256_file(target_path)
    response = {
        "recorded": True,
        "record_stage": "send_order_result_recorded",
        "next_stage": NEXT_STAGE_RESULT_REVIEW_REQUIRED,
        "changed": True,
        "order_queue_path": str(target_path),
        "backup_path": mutation_result.get("backup_path"),
        "order_id": _clean_text(result.get("order_id")),
        "order_queued_id": _clean_text(result.get("order_queued_id")),
        "request_hash": _clean_text(result.get("request_hash")),
        "lock_id": _clean_text(result.get("lock_id")),
        "execution_id": _clean_text(result.get("execution_id")),
        "send_order_called": True,
        "send_order_result_status": RESULT_STATUS_CALLED,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
    response.update({key: value for key, value in mutation_result.items() if key not in response})
    return preserve_queue_mutation_result(response, mutation_result)
