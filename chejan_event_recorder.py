# -*- coding: utf-8 -*-
"""Record a reviewed normalized Chejan event to an explicit queue file.

This module only appends event history to an existing ORDER_QUEUED record. It
does not update fill ledgers, position ledgers, status transitions, GUI flows,
timers, or broker order APIs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from execution_queue_writer import mutate_order_queue


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_CHEJAN_EVENT_RECORDED = "CHEJAN_EVENT_RECORDED"
NEXT_STAGE_FILL_RECORD_REQUIRED = "FILL_RECORD_REQUIRED"

_REVIEW_NEXT_STAGES = {
    "CHEJAN_EVENT_RECORD_REQUIRED",
    "FILL_RECORD_REQUIRED",
}
_EVENT_RECORD_TYPES = {
    "ORDER_ACCEPTED",
    "ORDER_OPEN",
    "ORDER_REJECTED",
    "ORDER_CANCELED",
}
_FILL_RECORD_TYPES = {"PARTIAL_FILL", "FULL_FILL"}


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
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _confirmed(context: Any) -> bool:
    return _as_dict(context).get("manual_chejan_event_record_confirmed") is True


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


def _validate_review_result(review_result: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(review_result)
    if not isinstance(review_result, dict):
        return result, _blocked("chejan_review", "chejan_review_result must be a dict")

    if result.get("chejan_review_ok") is not True:
        return result, _blocked("chejan_review", "chejan_review_result.chejan_review_ok is not true")

    if result.get("next_stage") not in _REVIEW_NEXT_STAGES:
        return result, _blocked(
            "chejan_review",
            "chejan_review_result.next_stage is not recordable",
        )

    return result, None


def _validate_normalized_event(normalized_event: Any, review_result: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    event = _as_dict(normalized_event)
    if not isinstance(normalized_event, dict):
        return event, "", _blocked("normalized_event", "normalized_event must be a dict")

    event_type = _clean_text(event.get("event_type"))
    if not event_type:
        return event, "", _blocked("normalized_event", "normalized_event.event_type is required")

    if _clean_text(review_result.get("event_type")) and _clean_text(review_result.get("event_type")) != event_type:
        return event, event_type, _blocked(
            "normalized_event",
            "normalized_event.event_type does not match chejan_review_result.event_type",
        )

    if event_type not in _EVENT_RECORD_TYPES and event_type not in _FILL_RECORD_TYPES:
        return event, event_type, _blocked("normalized_event", f"unsupported event_type: {event_type}")

    return event, event_type, None


def _find_target_order(orders: list[Any], review_result: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    order_queued_id = _clean_text(review_result.get("order_queued_id"))
    order_id = _clean_text(review_result.get("order_id"))
    request_hash = _clean_text(review_result.get("request_hash"))
    lock_id = _clean_text(review_result.get("lock_id"))
    execution_id = _clean_text(review_result.get("execution_id"))

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


def _validate_target_record(record: dict[str, Any], review_result: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("status") != "ORDER_QUEUED":
        return _blocked("record", "target record.status is not ORDER_QUEUED")

    for field in ("order_id", "request_hash", "lock_id", "execution_id"):
        if _clean_text(record.get(field)) != _clean_text(review_result.get(field)):
            return _blocked("record_consistency", f"target record.{field} does not match chejan_review_result.{field}")

    return None


def _broker_order_policy(record: dict[str, Any], event: dict[str, Any]) -> tuple[str, bool, dict[str, Any] | None]:
    record_broker_order_no = _clean_text(record.get("broker_order_no"))
    event_broker_order_no = _clean_text(event.get("broker_order_no"))

    if record_broker_order_no and event_broker_order_no:
        if record_broker_order_no != event_broker_order_no:
            return "", False, _blocked("broker_order_no", "broker_order_no does not match")
        return record_broker_order_no, False, None

    if event_broker_order_no and not record_broker_order_no:
        return event_broker_order_no, True, None

    if record_broker_order_no and not event_broker_order_no:
        return "", False, _blocked("broker_order_no", "normalized_event.broker_order_no is required")

    return "", False, _blocked("broker_order_no", "broker_order_no is required to record Chejan event")


def _next_stage_for_event(event_type: str) -> str:
    if event_type in _FILL_RECORD_TYPES:
        return NEXT_STAGE_FILL_RECORD_REQUIRED
    return NEXT_STAGE_CHEJAN_EVENT_RECORDED


def _event_received_at(event: dict[str, Any], now: str) -> str:
    raw_event = _as_dict(event.get("raw_event"))
    return _clean_text(event.get("received_at")) or _clean_text(raw_event.get("received_at")) or now


def _event_id(order_queued_id: str, event_type: str, broker_order_no: str, sequence: int) -> str:
    source = broker_order_no or order_queued_id
    return f"CHEJAN_EVENT_{source}_{event_type}_{sequence}"


def _event_record(
    *,
    event: dict[str, Any],
    event_type: str,
    broker_order_no: str,
    order_queued_id: str,
    sequence: int,
    now: str,
) -> dict[str, Any]:
    return {
        "event_id": _event_id(order_queued_id, event_type, broker_order_no, sequence),
        "event_type": event_type,
        "broker": _clean_text(event.get("broker")),
        "broker_order_no": broker_order_no,
        "account_no": _clean_text(event.get("account_no")),
        "code": _clean_text(event.get("code")),
        "side": _clean_text(event.get("side")),
        "order_status": _clean_text(event.get("order_status")),
        "order_quantity": event.get("order_quantity"),
        "filled_quantity": event.get("filled_quantity"),
        "remaining_quantity": event.get("remaining_quantity"),
        "order_price": event.get("order_price"),
        "filled_price": event.get("filled_price"),
        "received_at": _event_received_at(event, now),
        "normalized_event": deepcopy(event),
    }


def record_chejan_event(
    chejan_review_result: Any,
    normalized_event: Any,
    queue_path: str | Path | None,
    queue_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Append a reviewed Chejan event to one explicit queue file."""
    review_result, review_blocked = _validate_review_result(chejan_review_result)
    if review_blocked is not None:
        return review_blocked

    event, event_type, event_blocked = _validate_normalized_event(normalized_event, review_result)
    if event_blocked is not None:
        return event_blocked

    if queue_path is None or not str(queue_path).strip():
        return _blocked("queue_path", "queue_path is required")

    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual Chejan event record confirmation is required")

    target_path = Path(queue_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(queue_snapshot)
    if snapshot_sha256 and before_sha256 != snapshot_sha256:
        return _blocked(
            "stale_queue",
            "queue file changed after Chejan event review; manual review required",
        )

    mutation_state: dict[str, Any] = {}

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        if snapshot_sha256 and _sha256_file(target_path) != snapshot_sha256:
            return {
                "blocked": _blocked(
                    "stale_queue",
                    "queue file changed after Chejan event review; manual review required",
                )
            }

        orders = data["orders"]
        target_record, target_index = _find_target_order(orders, review_result)
        if target_record is None or target_index < 0:
            return {"blocked": _blocked("record", "target ORDER_QUEUED record not found")}

        record_blocked = _validate_target_record(target_record, review_result)
        if record_blocked is not None:
            return {"blocked": record_blocked}

        broker_order_no, broker_order_no_enriched, broker_blocked = _broker_order_policy(target_record, event)
        if broker_blocked is not None:
            return {"blocked": broker_blocked}

        now = _now_text()
        updated_data = deepcopy(data)
        updated_record = deepcopy(updated_data["orders"][target_index])
        existing_events = updated_record.get("chejan_events")
        if not isinstance(existing_events, list):
            existing_events = []

        order_queued_id = _clean_text(updated_record.get("id"))
        appended_event = _event_record(
            event=event,
            event_type=event_type,
            broker_order_no=broker_order_no,
            order_queued_id=order_queued_id,
            sequence=len(existing_events) + 1,
            now=now,
        )

        updated_record["chejan_event_recorded"] = True
        updated_record["chejan_event_recorded_at"] = now
        updated_record["chejan_event_record_source"] = "chejan_event_review"
        updated_record["last_chejan_event_type"] = event_type
        updated_record["last_chejan_event_at"] = appended_event["received_at"]
        updated_record["last_chejan_review_stage"] = _clean_text(review_result.get("review_stage"))
        updated_record["broker_order_no"] = broker_order_no
        updated_record["updated_at"] = now
        updated_record["chejan_events"] = existing_events + [appended_event]

        updated_data["orders"][target_index] = updated_record
        mutation_state.update(
            {
                "order_queued_id": order_queued_id,
                "broker_order_no": broker_order_no,
                "broker_order_no_enriched": broker_order_no_enriched,
            }
        )
        return {"data": updated_data}

    mutation_result = mutate_order_queue(
        target_path,
        mutate,
        operation_name="target_record_event_append",
        success_stage="chejan_event_recorded",
        next_stage=_next_stage_for_event(event_type),
        backup=backup,
        context=context,
        expected_revision=_expected_revision(context),
    )
    if mutation_result.get("committed") is not True or mutation_result.get("post_write_verified") is not True:
        stage = _clean_text(mutation_result.get("record_stage") or mutation_result.get("write_stage")) or "write_queue"
        reasons = mutation_result.get("blocked_reasons") if isinstance(mutation_result.get("blocked_reasons"), list) else []
        blocked = _blocked(stage, reasons[0] if reasons else "queue mutation failed")
        blocked.update({key: value for key, value in mutation_result.items() if key not in blocked})
        return blocked

    after_sha256 = _sha256_file(target_path)
    result = {
        "recorded": True,
        "record_stage": "chejan_event_recorded",
        "next_stage": _next_stage_for_event(event_type),
        "changed": True,
        "order_queue_path": str(target_path),
        "backup_path": mutation_result.get("backup_path"),
        "order_id": _clean_text(review_result.get("order_id")),
        "order_queued_id": mutation_state["order_queued_id"],
        "broker_order_no": mutation_state["broker_order_no"],
        "event_type": event_type,
        "matched_by": _clean_text(review_result.get("matched_by")),
        "broker_order_no_enriched": mutation_state["broker_order_no_enriched"],
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update({key: value for key, value in mutation_result.items() if key not in result})
    return result
