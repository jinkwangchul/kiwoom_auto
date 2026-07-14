# -*- coding: utf-8 -*-
"""Record a reviewed normalized Chejan event to an explicit queue file.

This module appends Chejan event history and reduces broker lifecycle state on
one existing queue record. It does not update fill ledgers, position ledgers,
GUI flows, timers, or broker order APIs.
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
_BROKER_ACCEPT_EVENT_TYPES = {"ORDER_ACCEPTED", "ORDER_OPEN"}
_SEND_RESULT_STATES = {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN"}
_BROKER_ACTIVE_STATES = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_TERMINAL_STATES = {"FILLED", "CANCELLED", "PARTIAL_CANCELLED", "BROKER_REJECTED"}


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


def _validate_target_record(record: dict[str, Any], review_result: dict[str, Any], event_type: str) -> dict[str, Any] | None:
    for field in ("order_id", "request_hash", "lock_id", "execution_id"):
        if _clean_text(record.get(field)) != _clean_text(review_result.get(field)):
            return _blocked("record_consistency", f"target record.{field} does not match chejan_review_result.{field}")

    status = _clean_text(record.get("status"))
    if event_type in _BROKER_ACCEPT_EVENT_TYPES:
        if status not in _SEND_RESULT_STATES and status != "ORDER_QUEUED":
            return _blocked("record", f"target record.status cannot accept broker acceptance event: {status or 'missing'}")
    elif event_type == "ORDER_REJECTED":
        if status in {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "PARTIAL_CANCELLED"}:
            return _blocked("record", f"target record.status cannot transition to BROKER_REJECTED: {status}")
        if status not in _SEND_RESULT_STATES and status != "BROKER_ACCEPTED" and status != "ORDER_QUEUED":
            return _blocked("record", f"target record.status cannot accept broker rejection event: {status or 'missing'}")
    elif event_type in _FILL_RECORD_TYPES:
        if status in {"BROKER_REJECTED", "CANCELLED", "PARTIAL_CANCELLED"}:
            return _blocked("record", f"target record.status cannot accept fill event: {status}")
        if status not in _SEND_RESULT_STATES and status not in _BROKER_ACTIVE_STATES and status != "FILLED" and status != "ORDER_QUEUED":
            return _blocked("record", f"target record.status cannot accept fill event: {status or 'missing'}")
    elif event_type == "ORDER_CANCELED":
        if status in {"FILLED", "BROKER_REJECTED", "CANCELLED", "PARTIAL_CANCELLED"}:
            return _blocked("record", f"target record.status cannot accept cancel event: {status}")
        if status not in _SEND_RESULT_STATES and status not in _BROKER_ACTIVE_STATES and status != "ORDER_QUEUED":
            return _blocked("record", f"target record.status cannot accept cancel event: {status or 'missing'}")

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


def _broker_order_conflict(
    orders: list[Any],
    target_index: int,
    broker_order_no: str,
) -> dict[str, Any] | None:
    if not broker_order_no:
        return None
    for index, order in enumerate(orders):
        if index == target_index:
            continue
        item = _as_dict(order)
        if _clean_text(item.get("broker_order_no")) == broker_order_no:
            return _blocked("broker_order_no", "broker_order_no already belongs to another queue record")
    return None


def _next_stage_for_event(event_type: str) -> str:
    if event_type in _FILL_RECORD_TYPES:
        return NEXT_STAGE_FILL_RECORD_REQUIRED
    return NEXT_STAGE_CHEJAN_EVENT_RECORDED


def _event_received_at(event: dict[str, Any], now: str) -> str:
    raw_event = _as_dict(event.get("raw_event"))
    return _clean_text(event.get("received_at")) or _clean_text(raw_event.get("received_at")) or now


_BROKER_EVENT_ID_FIELDS = (
    "event_id",
    "chejan_event_id",
    "execution_no",
    "fill_no",
    "trade_no",
)


def _broker_event_identity_value(event: dict[str, Any]) -> str:
    raw_event = _as_dict(event.get("raw_event"))
    fid_values = _as_dict(raw_event.get("fid_values"))
    for source in (event, raw_event):
        for field in _BROKER_EVENT_ID_FIELDS:
            value = _clean_text(source.get(field))
            if value:
                return value
    return _clean_text(fid_values.get("909"))


def _event_identity(event: dict[str, Any], event_type: str, broker_order_no: str) -> tuple[str, str]:
    broker_event_id = _broker_event_identity_value(event)
    if broker_event_id:
        identity_source = "broker_event_id"
        identity_payload: dict[str, Any] = {
            "broker_order_no": broker_order_no,
            "event_type": event_type,
            "broker_event_id": broker_event_id,
        }
    else:
        identity_source = "canonical_event_hash"
        identity_payload = {
            "broker": _clean_text(event.get("broker")),
            "broker_order_no": broker_order_no,
            "event_type": event_type,
            "gubun": _clean_text(event.get("gubun")),
            "account_no": _clean_text(event.get("account_no")),
            "code": _clean_text(event.get("code")),
            "side": _clean_text(event.get("side")),
            "order_status": _clean_text(event.get("order_status")),
            "order_quantity": event.get("order_quantity"),
            "filled_quantity": event.get("filled_quantity"),
            "remaining_quantity": event.get("remaining_quantity"),
            "order_price": event.get("order_price"),
            "filled_price": event.get("filled_price"),
        }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper(), identity_source


def _stored_event_identity(stored_event: Any) -> str:
    stored = _as_dict(stored_event)
    identity = _clean_text(stored.get("event_identity")).upper()
    if identity:
        return identity
    normalized = _as_dict(stored.get("normalized_event"))
    if not normalized:
        return ""
    derived, _ = _event_identity(
        normalized,
        _clean_text(stored.get("event_type") or normalized.get("event_type")),
        _clean_text(stored.get("broker_order_no") or normalized.get("broker_order_no")),
    )
    return derived


def _event_id(event_identity: str) -> str:
    return f"CHEJAN_EVENT_{event_identity}"


def _event_record(
    *,
    event: dict[str, Any],
    event_type: str,
    broker_order_no: str,
    event_identity: str,
    event_identity_source: str,
    now: str,
) -> dict[str, Any]:
    return {
        "event_id": _event_id(event_identity),
        "event_identity": event_identity,
        "event_identity_source": event_identity_source,
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


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = _clean_text(value).replace(",", "")
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


def _price_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = _clean_text(value).replace(",", "")
        if not text:
            return None
        numeric = float(text)
        return int(numeric) if numeric.is_integer() else numeric
    except (TypeError, ValueError):
        return None


def _fill_blocked(record: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    order_quantity = _int_or_none(event.get("order_quantity") or record.get("original_order_quantity") or record.get("quantity"))
    filled_quantity = _int_or_none(event.get("filled_quantity"))
    remaining_quantity = _int_or_none(event.get("remaining_quantity"))
    previous_filled = _int_or_none(record.get("cumulative_filled_quantity") or record.get("total_filled_quantity")) or 0

    if filled_quantity is None or filled_quantity < 0:
        return _blocked("fill_quantity", "filled_quantity must be a non-negative integer")
    if remaining_quantity is not None and remaining_quantity < 0:
        return _blocked("fill_quantity", "remaining_quantity must be a non-negative integer")
    if order_quantity is not None and order_quantity < 0:
        return _blocked("fill_quantity", "order_quantity must be a non-negative integer")
    if filled_quantity < previous_filled and _clean_text(record.get("status")) != "FILLED":
        return _blocked("fill_quantity", "filled_quantity cannot decrease")
    if order_quantity is not None and filled_quantity > order_quantity:
        return _blocked("fill_quantity", "filled_quantity cannot exceed order_quantity")
    if order_quantity is not None and remaining_quantity is not None and filled_quantity + remaining_quantity != order_quantity:
        return _blocked("fill_quantity", "filled_quantity plus remaining_quantity must equal order_quantity")
    return None


def _apply_acceptance(
    record: dict[str, Any],
    *,
    broker_order_no: str,
    event_identity: str,
    received_at: str,
) -> None:
    record.update(
        {
            "status": "BROKER_ACCEPTED",
            "broker_order_no": broker_order_no,
            "broker_result_known": True,
            "broker_accepted": True,
            "broker_rejected": False,
            "broker_accepted_at": received_at,
            "broker_accept_event_id": _event_id(event_identity),
            "send_uncertain": False,
            "manual_reconciliation_required": False,
            "automatic_retry_allowed": False,
            "actual_order_sent": True,
        }
    )


def _apply_rejection(
    record: dict[str, Any],
    event: dict[str, Any],
    *,
    broker_order_no: str,
    event_identity: str,
    received_at: str,
) -> None:
    record.update(
        {
            "status": "BROKER_REJECTED",
            "broker_order_no": broker_order_no,
            "broker_result_known": True,
            "broker_accepted": False,
            "broker_rejected": True,
            "broker_rejected_at": received_at,
            "broker_reject_event_id": _event_id(event_identity),
            "broker_reject_reason": _clean_text(event.get("order_status")) or "broker rejected",
            "broker_error_code": _clean_text(event.get("broker_error_code")),
            "actual_order_sent": False,
            "manual_reconciliation_required": False,
            "automatic_retry_allowed": False,
        }
    )


def _apply_fill(
    record: dict[str, Any],
    event: dict[str, Any],
    *,
    event_type: str,
    broker_order_no: str,
    event_identity: str,
    received_at: str,
) -> None:
    previous_status = _clean_text(record.get("status"))
    previous_filled = _int_or_none(record.get("cumulative_filled_quantity") or record.get("total_filled_quantity")) or 0
    order_quantity = _int_or_none(event.get("order_quantity") or record.get("original_order_quantity") or record.get("quantity"))
    filled_quantity = _int_or_none(event.get("filled_quantity")) or 0
    remaining_quantity = _int_or_none(event.get("remaining_quantity"))
    filled_price = _price_or_none(event.get("filled_price"))
    fill_delta = max(filled_quantity - previous_filled, 0)

    if previous_status == "FILLED" and event_type == "PARTIAL_FILL":
        record.update(
            {
                "out_of_order_detected": True,
                "last_out_of_order_event_id": _event_id(event_identity),
                "last_out_of_order_event_type": event_type,
            }
        )
        return

    final_fill = event_type == "FULL_FILL" or (order_quantity is not None and filled_quantity == order_quantity) or remaining_quantity == 0
    record.update(
        {
            "status": "FILLED" if final_fill else "PARTIALLY_FILLED",
            "broker_order_no": broker_order_no,
            "broker_result_known": True,
            "broker_accepted": True,
            "broker_rejected": False,
            "actual_order_sent": True,
            "original_order_quantity": order_quantity,
            "cumulative_filled_quantity": filled_quantity,
            "total_filled_quantity": filled_quantity,
            "remaining_quantity": 0 if final_fill else remaining_quantity,
            "last_fill_quantity": fill_delta,
            "last_fill_price": filled_price,
            "last_fill_event_id": _event_id(event_identity),
            "last_fill_at": received_at,
            "fill_count": int(record.get("fill_count") or 0) + 1,
            "manual_reconciliation_required": False,
            "automatic_retry_allowed": False,
        }
    )
    if filled_price is not None:
        record["average_fill_price"] = filled_price
    if final_fill:
        record["filled_at"] = received_at
        record["final_fill_event_id"] = _event_id(event_identity)


def _apply_cancel(
    record: dict[str, Any],
    event: dict[str, Any],
    *,
    broker_order_no: str,
    event_identity: str,
    received_at: str,
) -> None:
    cumulative = _int_or_none(record.get("cumulative_filled_quantity") or record.get("total_filled_quantity")) or 0
    status = "PARTIAL_CANCELLED" if cumulative > 0 else "CANCELLED"
    record.update(
        {
            "status": status,
            "broker_order_no": broker_order_no,
            "cancelled_at": received_at,
            "cancellation_event_id": _event_id(event_identity),
            "cancellation_reason": _clean_text(event.get("order_status")) or "broker cancellation",
            "final_filled_quantity": cumulative,
            "remaining_quantity": 0,
            "manual_reconciliation_required": False,
            "automatic_retry_allowed": False,
        }
    )


def _apply_lifecycle_transition(
    record: dict[str, Any],
    event: dict[str, Any],
    *,
    event_type: str,
    broker_order_no: str,
    event_identity: str,
    received_at: str,
) -> dict[str, Any] | None:
    if event_type in _BROKER_ACCEPT_EVENT_TYPES:
        _apply_acceptance(record, broker_order_no=broker_order_no, event_identity=event_identity, received_at=received_at)
        return None
    if event_type == "ORDER_REJECTED":
        _apply_rejection(record, event, broker_order_no=broker_order_no, event_identity=event_identity, received_at=received_at)
        return None
    if event_type in _FILL_RECORD_TYPES:
        fill_blocked = _fill_blocked(record, event)
        if fill_blocked is not None:
            return fill_blocked
        _apply_fill(
            record,
            event,
            event_type=event_type,
            broker_order_no=broker_order_no,
            event_identity=event_identity,
            received_at=received_at,
        )
        return None
    if event_type == "ORDER_CANCELED":
        _apply_cancel(record, event, broker_order_no=broker_order_no, event_identity=event_identity, received_at=received_at)
        return None
    return _blocked("lifecycle", f"unsupported lifecycle event_type: {event_type}")


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

        record_blocked = _validate_target_record(target_record, review_result, event_type)
        if record_blocked is not None:
            return {"blocked": record_blocked}

        broker_order_no, broker_order_no_enriched, broker_blocked = _broker_order_policy(target_record, event)
        if broker_blocked is not None:
            return {"blocked": broker_blocked}
        broker_conflict = _broker_order_conflict(orders, target_index, broker_order_no)
        if broker_conflict is not None:
            return {"blocked": broker_conflict}

        now = _now_text()
        updated_data = deepcopy(data)
        updated_record = deepcopy(updated_data["orders"][target_index])
        existing_events = updated_record.get("chejan_events")
        if not isinstance(existing_events, list):
            existing_events = []

        order_queued_id = _clean_text(updated_record.get("id"))
        event_identity, event_identity_source = _event_identity(event, event_type, broker_order_no)
        if any(_stored_event_identity(existing_event) == event_identity for existing_event in existing_events):
            duplicate = _blocked("duplicate_event", "duplicate Chejan event identity")
            duplicate.update(
                {
                    "duplicate": True,
                    "idempotent": True,
                    "event_identity": event_identity,
                    "event_identity_source": event_identity_source,
                }
            )
            return {"blocked": duplicate}
        appended_event = _event_record(
            event=event,
            event_type=event_type,
            broker_order_no=broker_order_no,
            event_identity=event_identity,
            event_identity_source=event_identity_source,
            now=now,
        )
        lifecycle_blocked = _apply_lifecycle_transition(
            updated_record,
            event,
            event_type=event_type,
            broker_order_no=broker_order_no,
            event_identity=event_identity,
            received_at=appended_event["received_at"],
        )
        if lifecycle_blocked is not None:
            return {"blocked": lifecycle_blocked}

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
                "event_identity": event_identity,
                "event_identity_source": event_identity_source,
                "lifecycle_status": _clean_text(updated_record.get("status")),
                "lifecycle_updated": True,
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
        return preserve_queue_mutation_result(blocked, mutation_result)

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
        "event_identity": mutation_state["event_identity"],
        "event_identity_source": mutation_state["event_identity_source"],
        "lifecycle_status": mutation_state["lifecycle_status"],
        "lifecycle_updated": mutation_state["lifecycle_updated"],
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update({key: value for key, value in mutation_result.items() if key not in result})
    return preserve_queue_mutation_result(result, mutation_result)


def inspect_broker_chejan_lifecycle(
    queue_path: str | Path,
    identity: Any,
) -> dict[str, Any]:
    """Read one queue record's broker/Chejan lifecycle without mutating files."""
    target_path = Path(queue_path)
    data, read_blocked = _read_queue(target_path)
    if read_blocked is not None:
        result = {"inspection_ok": False, "inspection_type": "BROKER_CHEJAN_LIFECYCLE_INSPECTION"}
        result.update(read_blocked)
        return result

    review_like = _as_dict(identity)
    record, index = _find_target_order(data["orders"], review_like)
    if record is None or index < 0:
        return {
            "inspection_ok": False,
            "inspection_type": "BROKER_CHEJAN_LIFECYCLE_INSPECTION",
            "record_stage": "record",
            "blocked_reasons": ["target queue record not found"],
            "warnings": [],
        }

    events = record.get("chejan_events")
    if not isinstance(events, list):
        events = []
    identities = [_stored_event_identity(event) for event in events]
    return {
        "inspection_ok": True,
        "inspection_type": "BROKER_CHEJAN_LIFECYCLE_INSPECTION",
        "queue_path": str(target_path),
        "order_queued_id": _clean_text(record.get("id")),
        "order_id": _clean_text(record.get("order_id")),
        "request_hash": _clean_text(record.get("request_hash")),
        "lock_id": _clean_text(record.get("lock_id")),
        "execution_id": _clean_text(record.get("execution_id")),
        "status": _clean_text(record.get("status")),
        "broker_order_no": _clean_text(record.get("broker_order_no")),
        "broker_accepted": record.get("broker_accepted") is True,
        "broker_rejected": record.get("broker_rejected") is True,
        "original_order_quantity": record.get("original_order_quantity"),
        "cumulative_filled_quantity": record.get("cumulative_filled_quantity"),
        "remaining_quantity": record.get("remaining_quantity"),
        "fill_count": record.get("fill_count", 0),
        "chejan_event_count": len(events),
        "duplicate_event_count": len(identities) - len(set(identities)),
        "last_chejan_event_type": _clean_text(record.get("last_chejan_event_type")),
        "out_of_order_detected": record.get("out_of_order_detected") is True,
        "manual_reconciliation_required": record.get("manual_reconciliation_required") is True,
        "automatic_retry_allowed": record.get("automatic_retry_allowed") is True,
        "final_state": _clean_text(record.get("status")) in _TERMINAL_STATES,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order_called": False,
        "broker_api_called": False,
    }
