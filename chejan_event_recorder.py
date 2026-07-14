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
_TERMINAL_STATES = {"FILLED", "CANCELLED", "PARTIAL_CANCELLED", "BROKER_REJECTED"}
_LIFECYCLE_ALLOWED_SOURCE_STATUSES = {
    "BROKER_ACCEPT": {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN"},
    "BROKER_REJECT": {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN"},
    "FILL": {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN", "BROKER_ACCEPTED", "PARTIALLY_FILLED", "FILLED"},
    "CANCEL": {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN", "BROKER_ACCEPTED", "PARTIALLY_FILLED"},
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
    transition_key = _transition_key(event_type)
    allowed_statuses = _LIFECYCLE_ALLOWED_SOURCE_STATUSES.get(transition_key, set())
    if status not in allowed_statuses:
        return _blocked("record", f"target record.status cannot accept {transition_key} event: {status or 'missing'}")

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


def _transition_key(event_type: str) -> str:
    if event_type in _BROKER_ACCEPT_EVENT_TYPES:
        return "BROKER_ACCEPT"
    if event_type == "ORDER_REJECTED":
        return "BROKER_REJECT"
    if event_type in _FILL_RECORD_TYPES:
        return "FILL"
    if event_type == "ORDER_CANCELED":
        return "CANCEL"
    return "UNKNOWN"


def _broker_average_fill_price(event: dict[str, Any]) -> float | int | None:
    for field in (
        "average_fill_price",
        "avg_fill_price",
        "average_price",
        "cumulative_average_fill_price",
        "broker_average_fill_price",
    ):
        value = _price_or_none(event.get(field))
        if value is not None:
            return value
    raw_event = _as_dict(event.get("raw_event"))
    fid_values = _as_dict(raw_event.get("fid_values"))
    for fid in ("932", "930"):
        value = _price_or_none(fid_values.get(fid))
        if value is not None:
            return value
    return None


def _weighted_average_fill_price(
    *,
    previous_average: Any,
    previous_filled: int,
    fill_delta: int,
    last_fill_price: float | int | None,
) -> float | int | None:
    if fill_delta <= 0 or last_fill_price is None:
        return _price_or_none(previous_average)
    previous_average_value = _price_or_none(previous_average)
    if previous_average_value is None or previous_filled <= 0:
        return last_fill_price
    total_filled = previous_filled + fill_delta
    weighted = ((float(previous_average_value) * previous_filled) + (float(last_fill_price) * fill_delta)) / total_filled
    return int(weighted) if weighted.is_integer() else weighted


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
    previous_remaining = _int_or_none(record.get("remaining_quantity"))
    previous_average = _price_or_none(record.get("average_fill_price"))
    order_quantity = _int_or_none(event.get("order_quantity") or record.get("original_order_quantity") or record.get("quantity"))
    remaining_quantity = _int_or_none(event.get("remaining_quantity"))
    explicit_filled_quantity = _int_or_none(event.get("filled_quantity"))
    implied_filled_quantity = None
    if order_quantity is not None and remaining_quantity is not None:
        implied_filled_quantity = order_quantity - remaining_quantity
    filled_candidates = [value for value in (explicit_filled_quantity, implied_filled_quantity) if value is not None]
    filled_quantity = max(filled_candidates) if filled_candidates else 0
    filled_price = _price_or_none(event.get("filled_price"))
    explicit_fill_delta = max(filled_quantity - previous_filled, 0)
    remaining_fill_delta = 0
    average_previous_filled = previous_filled
    if previous_remaining is not None and remaining_quantity is not None:
        remaining_fill_delta = max(previous_remaining - remaining_quantity, 0)
        if order_quantity is not None:
            average_previous_filled = max(order_quantity - previous_remaining, 0)
    fill_delta = max(explicit_fill_delta, remaining_fill_delta)

    if previous_status == "FILLED" and event_type == "PARTIAL_FILL":
        record.update(
            {
                "out_of_order_detected": True,
                "last_out_of_order_event_id": _event_id(event_identity),
                "last_out_of_order_event_type": event_type,
            }
        )
        return

    average_fill_price = _broker_average_fill_price(event)
    if average_fill_price is None:
        if fill_delta > 0 and filled_price is not None:
            if previous_average is not None and average_previous_filled > 0:
                total_filled_for_average = average_previous_filled + fill_delta
                weighted = ((float(previous_average) * average_previous_filled) + (float(filled_price) * fill_delta)) / total_filled_for_average
                average_fill_price = int(weighted) if weighted.is_integer() else weighted
            else:
                average_fill_price = filled_price
        else:
            average_fill_price = previous_average

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
    if fill_delta > 0:
        broker_average = _broker_average_fill_price(event)
        if broker_average is not None:
            record["average_fill_price"] = broker_average
        elif filled_price is not None:
            if previous_average is not None and average_previous_filled > 0:
                total_filled_for_average = average_previous_filled + fill_delta
                weighted = ((float(previous_average) * average_previous_filled) + (float(filled_price) * fill_delta)) / total_filled_for_average
                record["average_fill_price"] = int(weighted) if weighted.is_integer() else weighted
            else:
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


_INCOMPLETE_RESTART_STATUSES = {
    "SEND_CALL_IN_PROGRESS",
    "SEND_UNCERTAIN",
    "BROKER_ACCEPTED",
    "PARTIALLY_FILLED",
}
_FILL_LEDGER_EVENT_TYPES = {"PARTIAL_FILL", "FULL_FILL"}
_CANCELLED_STATES = {"CANCELLED", "PARTIAL_CANCELLED"}
_FILL_QUANTITY_SEMANTICS = "CUMULATIVE"


def _canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _read_fills_ledger(path: Path | None) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    if path is None:
        return [], ["fills_path not provided"], []
    if not path.exists():
        return [], ["fills file does not exist"], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], [f"failed to read fills json: {exc}"], []
    if not isinstance(data, dict):
        return [], ["fills root must be an object"], []
    fills = data.get("fills")
    if not isinstance(fills, list):
        return [], ["fills must be a list"], []
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in fills:
        if isinstance(item, dict):
            records.append(item)
        else:
            warnings.append("fills must contain only objects")
    return records, warnings, []


def _identity_from_input(identity: Any) -> dict[str, Any]:
    item = _as_dict(identity)
    return {
        "order_queued_id": _clean_text(item.get("order_queued_id") or item.get("id")),
        "order_id": _clean_text(item.get("order_id")),
        "request_hash": _clean_text(item.get("request_hash")),
        "lock_id": _clean_text(item.get("lock_id")),
        "execution_id": _clean_text(item.get("execution_id")),
        "broker_order_no": _clean_text(item.get("broker_order_no")),
    }


def _record_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "order_queued_id": _clean_text(record.get("id")),
        "order_id": _clean_text(record.get("order_id")),
        "request_hash": _clean_text(record.get("request_hash")),
        "lock_id": _clean_text(record.get("lock_id")),
        "execution_id": _clean_text(record.get("execution_id")),
        "broker_order_no": _clean_text(record.get("broker_order_no")),
    }


def _fill_identity(record: dict[str, Any]) -> str:
    normalized = _as_dict(record.get("normalized_event"))
    raw_event = _as_dict(normalized.get("raw_event"))
    fid_values = _as_dict(raw_event.get("fid_values"))
    for field in ("execution_no", "broker_event_id", "event_id", "chejan_event_id", "fill_no", "trade_no"):
        value = _clean_text(record.get(field)) or _clean_text(normalized.get(field))
        if value:
            return f"{field}:{value}"
    fid_execution_no = _clean_text(fid_values.get("909"))
    if fid_execution_no:
        return f"execution_no:{fid_execution_no}"
    return f"canonical_event_hash:{_canonical_json_hash(record)}"


def _strong_identity_matches(fill: dict[str, Any], record: dict[str, Any]) -> bool:
    checks = (
        ("order_id", "order_id"),
        ("execution_id", "execution_id"),
        ("order_queued_id", "id"),
        ("request_hash", "request_hash"),
        ("lock_id", "lock_id"),
    )
    for fill_field, record_field in checks:
        fill_value = _clean_text(fill.get(fill_field))
        record_value = _clean_text(record.get(record_field))
        if fill_value and record_value and fill_value == record_value:
            return True
    return False


def _matching_fills(fills: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for fill in fills:
        if _strong_identity_matches(fill, record):
            matches.append(fill)
    return matches


def _fill_sort_key(indexed_fill: tuple[int, dict[str, Any]]) -> tuple[int, int, Any, str, str, int]:
    index, fill = indexed_fill
    normalized = _as_dict(fill.get("normalized_event"))
    raw_event = _as_dict(normalized.get("raw_event"))
    fid_values = _as_dict(raw_event.get("fid_values"))
    sequence = (
        _clean_text(fill.get("execution_no"))
        or _clean_text(fill.get("broker_event_id"))
        or _clean_text(fill.get("event_id"))
        or _clean_text(fill.get("chejan_event_id"))
        or _clean_text(fill.get("fill_no"))
        or _clean_text(fill.get("trade_no"))
        or _clean_text(normalized.get("execution_no"))
        or _clean_text(fid_values.get("909"))
    )
    if sequence:
        try:
            sequence_key: Any = int(sequence)
            sequence_kind = 0
        except ValueError:
            sequence_key = sequence
            sequence_kind = 1
        has_no_sequence = 0
    else:
        sequence_key = ""
        sequence_kind = 1
        has_no_sequence = 1
    return (
        has_no_sequence,
        sequence_kind,
        sequence_key,
        _clean_text(fill.get("received_at")),
        _clean_text(fill.get("recorded_at")),
        index,
    )


def _fill_broker_average(fill: dict[str, Any]) -> float | int | None:
    average = _broker_average_fill_price(fill)
    if average is not None:
        return average
    normalized = _as_dict(fill.get("normalized_event"))
    return _broker_average_fill_price(normalized)


def _fill_ledger_summary(fills: list[dict[str, Any]], record: dict[str, Any]) -> dict[str, Any]:
    matches = [
        fill for _, fill in sorted(
            enumerate(_matching_fills(fills, record)),
            key=_fill_sort_key,
        )
    ]
    identities = [_fill_identity(fill) for fill in matches]
    duplicate_identities = sorted({identity for identity in identities if identities.count(identity) > 1})
    previous_cumulative = 0
    max_cumulative = 0
    delta_total = 0
    effective_count = 0
    weighted_total = 0.0
    repeated_identities: list[str] = []
    out_of_order_identities: list[str] = []
    broker_order_mismatches: list[str] = []
    expected_broker_order_no = _clean_text(record.get("broker_order_no"))
    for fill in matches:
        identity = _fill_identity(fill)
        current_cumulative = _int_or_none(fill.get("filled_quantity") or fill.get("quantity")) or 0
        price = _price_or_none(fill.get("filled_price") or fill.get("price")) or 0
        fill_delta = current_cumulative - previous_cumulative
        if fill_delta < 0:
            out_of_order_identities.append(identity)
            max_cumulative = max(max_cumulative, current_cumulative)
            continue
        if fill_delta == 0:
            repeated_identities.append(identity)
            max_cumulative = max(max_cumulative, current_cumulative)
            continue
        effective_count += 1
        delta_total += fill_delta
        max_cumulative = max(max_cumulative, current_cumulative)
        previous_cumulative = current_cumulative
        weighted_total += float(fill_delta) * float(price)
        fill_broker_order_no = _clean_text(fill.get("broker_order_no"))
        if expected_broker_order_no and fill_broker_order_no and fill_broker_order_no != expected_broker_order_no:
            broker_order_mismatches.append(fill_broker_order_no)
    average = next((avg for avg in (_fill_broker_average(fill) for fill in reversed(matches)) if avg is not None), None)
    if average is None and delta_total > 0:
        value = weighted_total / delta_total
        average = int(value) if value.is_integer() else value
    return {
        "fills": matches,
        "fills_ledger_count": len(matches),
        "fills_unique_count": len(set(identities)),
        "fills_effective_count": effective_count,
        "fills_summed_quantity": max_cumulative,
        "fills_delta_quantity": delta_total,
        "fills_weighted_average_price": average,
        "duplicate_execution_identities": duplicate_identities,
        "repeated_fill_identities": repeated_identities,
        "out_of_order_fill_identities": out_of_order_identities,
        "broker_order_mismatches": broker_order_mismatches,
        "source_event_identities": identities,
    }


def _chejan_evidence(record: dict[str, Any]) -> dict[str, Any]:
    events = record.get("chejan_events")
    if not isinstance(events, list):
        events = []
    accepted = []
    rejected = []
    cancelled = []
    fills = []
    for event in events:
        item = _as_dict(event)
        event_type = _clean_text(item.get("event_type"))
        if event_type in _BROKER_ACCEPT_EVENT_TYPES:
            accepted.append(item)
        elif event_type == "ORDER_REJECTED":
            rejected.append(item)
        elif event_type == "ORDER_CANCELED":
            cancelled.append(item)
        elif event_type in _FILL_LEDGER_EVENT_TYPES:
            fills.append(item)
    return {
        "chejan_event_count": len(events),
        "accepted_event_count": len(accepted),
        "rejected_event_count": len(rejected),
        "cancel_event_count": len(cancelled),
        "fill_event_count": len(fills),
        "accepted_evidence": bool(accepted),
        "rejected_evidence": bool(rejected),
        "cancel_evidence": bool(cancelled),
        "fill_evidence": bool(fills),
    }


def _queue_fill_mismatches(record: dict[str, Any], fill_summary: dict[str, Any], evidence: dict[str, Any]) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    status = _clean_text(record.get("status"))
    original_quantity = _int_or_none(record.get("original_order_quantity") or record.get("quantity"))
    queue_cumulative = _int_or_none(record.get("cumulative_filled_quantity") or record.get("total_filled_quantity")) or 0
    queue_remaining = _int_or_none(record.get("remaining_quantity"))
    queue_fill_count = _int_or_none(record.get("fill_count")) or 0
    queue_average = _price_or_none(record.get("average_fill_price"))
    fills_count = fill_summary["fills_ledger_count"]
    fills_effective = fill_summary["fills_effective_count"]
    fills_quantity = fill_summary["fills_summed_quantity"]
    fills_average = fill_summary["fills_weighted_average_price"]

    if fill_summary["duplicate_execution_identities"]:
        reasons.append("duplicate execution identities in fills ledger")
    if fill_summary["out_of_order_fill_identities"]:
        reasons.append("out-of-order cumulative fill quantity in fills ledger")
    if fill_summary["broker_order_mismatches"]:
        reasons.append("broker_order_no mismatch between queue and fills ledger")
    if queue_cumulative != fills_quantity:
        reasons.append("queue cumulative filled quantity does not match fills ledger sum")
    if queue_fill_count != fills_effective:
        reasons.append("queue fill_count does not match effective fills ledger count")
    if queue_average is not None and fills_average is not None and float(queue_average) != float(fills_average):
        reasons.append("queue average_fill_price does not match fills weighted average")
    if original_quantity is not None and queue_remaining is not None and queue_cumulative + queue_remaining != original_quantity:
        reasons.append("queue remaining plus cumulative filled does not match original quantity")
    if fills_quantity > 0 and status == "BROKER_ACCEPTED":
        reasons.append("fills ledger has fill but queue is still BROKER_ACCEPTED")
    if status in {"PARTIALLY_FILLED", "FILLED"} and fills_count == 0:
        reasons.append("queue fill status has no fills ledger detail")
    if status == "FILLED" and (queue_remaining or 0) > 0:
        reasons.append("queue FILLED still has remaining quantity")
    if status == "PARTIALLY_FILLED" and queue_remaining == 0:
        reasons.append("queue PARTIALLY_FILLED has zero remaining quantity")
    if status == "SEND_CALL_IN_PROGRESS" and (evidence["accepted_evidence"] or evidence["fill_evidence"]):
        warnings.append("SEND_CALL_IN_PROGRESS has broker Chejan evidence")
    if status == "SEND_UNCERTAIN" and (evidence["accepted_evidence"] or evidence["rejected_evidence"] or evidence["fill_evidence"]):
        warnings.append("SEND_UNCERTAIN has clear broker Chejan evidence")
    if status in _CANCELLED_STATES and (evidence["fill_evidence"] or fills_quantity > 0):
        reasons.append("late fill evidence exists after cancelled state")
    return reasons, warnings


def _classify_reconciliation(status: str, reasons: list[str], warnings: list[str], read_warnings: list[str]) -> str:
    if any("duplicate execution identities" in reason or "broker_order_no mismatch" in reason for reason in reasons):
        return "BLOCKED"
    if read_warnings:
        return "REVIEW_REQUIRED"
    if status in _CANCELLED_STATES and reasons:
        return "BLOCKED"
    if reasons:
        return "REVIEW_REQUIRED"
    if status in _INCOMPLETE_RESTART_STATUSES and warnings:
        return "RECONCILIATION_CANDIDATE"
    if status in _INCOMPLETE_RESTART_STATUSES:
        return "REVIEW_REQUIRED"
    return "CONSISTENT"


def inspect_incomplete_order_reconciliation(
    queue_path: str | Path,
    identity: Any,
    *,
    fills_path: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect Queue/Chejan/fills evidence for restart reconciliation without writes."""
    target_path = Path(queue_path)
    before_hash = _sha256_file(target_path) if target_path.exists() else ""
    data, read_blocked = _read_queue(target_path)
    if read_blocked is not None:
        result = {
            "inspection_ok": False,
            "inspection_type": "CHEJAN_RESTART_RECONCILIATION_INSPECTION",
            "write_performed": False,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "send_order_called": False,
            "broker_api_called": False,
        }
        result.update(read_blocked)
        return result

    review_like = _identity_from_input(identity)
    record, index = _find_target_order(data["orders"], review_like)
    if record is None or index < 0:
        return {
            "inspection_ok": False,
            "inspection_type": "CHEJAN_RESTART_RECONCILIATION_INSPECTION",
            "record_stage": "record",
            "blocked_reasons": ["target queue record not found"],
            "warnings": [],
            "write_performed": False,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "send_order_called": False,
            "broker_api_called": False,
        }

    fills, fill_warnings, _ = _read_fills_ledger(Path(fills_path) if fills_path is not None else None)
    fill_summary = _fill_ledger_summary(fills, record)
    evidence = _chejan_evidence(record)
    mismatch_reasons, mismatch_warnings = _queue_fill_mismatches(record, fill_summary, evidence)
    warnings = fill_warnings + mismatch_warnings
    classification = _classify_reconciliation(_clean_text(record.get("status")), mismatch_reasons, mismatch_warnings, fill_warnings)
    original_quantity = _int_or_none(record.get("original_order_quantity") or record.get("quantity"))
    queue_cumulative = _int_or_none(record.get("cumulative_filled_quantity") or record.get("total_filled_quantity")) or 0
    queue_remaining = _int_or_none(record.get("remaining_quantity"))
    after_hash = _sha256_file(target_path) if target_path.exists() else ""
    return {
        "inspection_ok": True,
        "inspection_type": "CHEJAN_RESTART_RECONCILIATION_INSPECTION",
        "queue_path": str(target_path),
        "fills_path": str(fills_path) if fills_path is not None else None,
        "queue_revision": data.get("revision", 0),
        "queue_snapshot_hash": before_hash,
        "queue_snapshot_unchanged": before_hash == after_hash,
        "order_index": index,
        "current_queue_status": _clean_text(record.get("status")),
        "broker_order_no": _clean_text(record.get("broker_order_no")),
        "dispatch_claim_id": _clean_text(record.get("dispatch_claim_id")),
        "send_order_attempt_id": _clean_text(record.get("send_order_attempt_id")),
        "identity": _record_identity(record),
        "original_quantity": original_quantity,
        "queue_cumulative_filled": queue_cumulative,
        "queue_remaining": queue_remaining,
        "queue_fill_count": _int_or_none(record.get("fill_count")) or 0,
        "queue_average_fill_price": _price_or_none(record.get("average_fill_price")),
        "fills_ledger_count": fill_summary["fills_ledger_count"],
        "fills_effective_count": fill_summary["fills_effective_count"],
        "fill_quantity_semantics": _FILL_QUANTITY_SEMANTICS,
        "fills_summed_quantity": fill_summary["fills_summed_quantity"],
        "fills_delta_quantity": fill_summary["fills_delta_quantity"],
        "fills_weighted_average_price": fill_summary["fills_weighted_average_price"],
        "duplicate_execution_identities": fill_summary["duplicate_execution_identities"],
        "repeated_fill_identities": fill_summary["repeated_fill_identities"],
        "out_of_order_fill_identities": fill_summary["out_of_order_fill_identities"],
        "missing_identities": [
            field for field, value in _record_identity(record).items()
            if field != "broker_order_no" and not value
        ],
        "queue_fills_mismatch": bool(mismatch_reasons),
        "chejan_evidence": evidence,
        "reconciliation_candidate_status": classification,
        "manual_reconciliation_required": classification != "CONSISTENT",
        "automatic_retry_allowed": False,
        "blocked_reasons": mismatch_reasons,
        "warnings": warnings,
        "source_event_identities": fill_summary["source_event_identities"],
        "write_performed": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order_called": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
    }


def build_order_reconciliation_preview(
    queue_path: str | Path,
    identity: Any,
    *,
    fills_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a manual reconciliation proposal from read-only inspection evidence."""
    inspection = inspect_incomplete_order_reconciliation(queue_path, identity, fills_path=fills_path)
    if inspection.get("inspection_ok") is not True:
        return {
            "preview_type": "ORDER_RECONCILIATION_PREVIEW",
            "preview_ready": False,
            "status": "BLOCKED",
            "inspection": inspection,
            "approval_required": True,
            "write_performed": False,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "send_order_called": False,
            "broker_api_called": False,
            "blocked_reasons": inspection.get("blocked_reasons", []),
            "warnings": inspection.get("warnings", []),
        }

    status = _clean_text(inspection.get("current_queue_status"))
    proposed_status = status
    evidence = _as_dict(inspection.get("chejan_evidence"))
    remaining = inspection.get("queue_remaining")
    filled_quantity = inspection.get("fills_summed_quantity")
    if evidence.get("rejected_evidence"):
        proposed_status = "BROKER_REJECTED"
    elif evidence.get("cancel_evidence"):
        proposed_status = "PARTIAL_CANCELLED" if (filled_quantity or 0) > 0 else "CANCELLED"
    elif isinstance(remaining, int) and remaining == 0 and (filled_quantity or 0) > 0:
        proposed_status = "FILLED"
    elif (filled_quantity or 0) > 0:
        proposed_status = "PARTIALLY_FILLED"
    elif evidence.get("accepted_evidence"):
        proposed_status = "BROKER_ACCEPTED"

    snapshot = {
        "queue_path": inspection.get("queue_path"),
        "fills_path": inspection.get("fills_path"),
        "queue_revision": inspection.get("queue_revision"),
        "identity": inspection.get("identity"),
        "current_queue_status": status,
        "fills_summed_quantity": filled_quantity,
        "fills_weighted_average_price": inspection.get("fills_weighted_average_price"),
        "chejan_evidence": evidence,
        "blocked_reasons": inspection.get("blocked_reasons", []),
        "warnings": inspection.get("warnings", []),
    }
    preview_ready = inspection.get("reconciliation_candidate_status") in {
        "RECONCILIATION_CANDIDATE",
        "REVIEW_REQUIRED",
    }
    return {
        "preview_type": "ORDER_RECONCILIATION_PREVIEW",
        "preview_ready": preview_ready,
        "status": "READY" if preview_ready else "BLOCKED",
        "current_queue_snapshot": inspection,
        "evidence_snapshot": snapshot,
        "proposed_status": proposed_status,
        "proposed_cumulative_quantity": filled_quantity,
        "proposed_remaining_quantity": inspection.get("original_quantity") - filled_quantity
        if isinstance(inspection.get("original_quantity"), int) and isinstance(filled_quantity, int)
        else inspection.get("queue_remaining"),
        "proposed_average_fill_price": inspection.get("fills_weighted_average_price"),
        "proposed_broker_order_no": inspection.get("broker_order_no"),
        "proposed_reconciliation_reason": inspection.get("warnings") or inspection.get("blocked_reasons"),
        "source_event_identities": inspection.get("source_event_identities", []),
        "expected_revision": inspection.get("queue_revision"),
        "snapshot_hash": _canonical_json_hash(snapshot),
        "approval_required": True,
        "write_performed": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order_called": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "blocked_reasons": inspection.get("blocked_reasons", []),
        "warnings": inspection.get("warnings", []),
    }
