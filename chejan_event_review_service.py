# -*- coding: utf-8 -*-
"""Review whether a normalized Chejan event can be linked to an order record.

This module only reviews in-memory dictionaries. It never writes runtime files,
creates fills or positions, connects Chejan handlers, or calls SendOrder.
"""

from __future__ import annotations

from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_MANUAL_REVIEW = "MANUAL_CHEJAN_REVIEW_REQUIRED"
NEXT_STAGE_EVENT_RECORD_REQUIRED = "CHEJAN_EVENT_RECORD_REQUIRED"
NEXT_STAGE_FILL_RECORD_REQUIRED = "FILL_RECORD_REQUIRED"
RESULT_STATUS_CALLED = "SEND_ORDER_CALLED"

_EVENT_RECORD_TYPES = {
    "ORDER_ACCEPTED",
    "ORDER_OPEN",
    "ORDER_REJECTED",
    "ORDER_CANCELED",
}
_FILL_RECORD_TYPES = {"PARTIAL_FILL", "FULL_FILL"}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str, event_type: str = "ORDER_UNKNOWN") -> dict[str, Any]:
    next_stage = NEXT_STAGE_MANUAL_REVIEW if event_type == "ORDER_UNKNOWN" else NEXT_STAGE_BLOCKED
    return {
        "chejan_review_ok": False,
        "review_stage": stage,
        "next_stage": next_stage,
        "event_type": event_type,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _event_type(normalized_event: dict[str, Any]) -> str:
    return _clean_text(normalized_event.get("event_type")) or "ORDER_UNKNOWN"


def _identity_value(record: dict[str, Any], key: str) -> str:
    return _clean_text(record.get(key))


def _validate_result_review(result_review: Any, record: dict[str, Any]) -> dict[str, Any] | None:
    if result_review is None:
        return None

    if not isinstance(result_review, dict):
        return _blocked("send_order_result_review", "send_order_result_review_result must be a dict")

    if result_review.get("result_review_ok") is not True:
        return _blocked("send_order_result_review", "send_order_result_review_result.result_review_ok is not true")

    if result_review.get("next_stage") != "CHEJAN_OR_EXECUTION_EVENT_REQUIRED":
        return _blocked(
            "send_order_result_review",
            "send_order_result_review_result.next_stage is not CHEJAN_OR_EXECUTION_EVENT_REQUIRED",
        )

    for key in ("order_id", "request_hash", "lock_id", "execution_id"):
        result_value = _clean_text(result_review.get(key))
        record_value = _identity_value(record, key)
        if not result_value or not record_value:
            return _blocked("send_order_result_review", f"{key} is required")
        if result_value != record_value:
            return _blocked(
                "send_order_result_review",
                f"send_order_result_review_result.{key} does not match order_record.{key}",
            )

    return None


def _next_stage_for_event(event_type: str) -> str:
    if event_type in _FILL_RECORD_TYPES:
        return NEXT_STAGE_FILL_RECORD_REQUIRED
    return NEXT_STAGE_EVENT_RECORD_REQUIRED


def _validate_fill_event(event: dict[str, Any], event_type: str) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    filled_quantity = event.get("filled_quantity")
    remaining_quantity = event.get("remaining_quantity")

    if not isinstance(filled_quantity, int) or filled_quantity <= 0:
        return _blocked("fill_validation", "filled_quantity must be greater than 0", event_type), warnings

    if event_type == "PARTIAL_FILL":
        if not isinstance(remaining_quantity, int) or remaining_quantity <= 0:
            return _blocked("fill_validation", "PARTIAL_FILL remaining_quantity must be greater than 0", event_type), warnings

    if event_type == "FULL_FILL":
        if remaining_quantity != 0:
            return _blocked("fill_validation", "FULL_FILL remaining_quantity must be 0", event_type), warnings

    if event.get("filled_price") is None:
        warnings.append("filled_price is missing; fill recorder should verify price before recording")

    return None, warnings


def _broker_order_match(event: dict[str, Any], record: dict[str, Any]) -> tuple[str | None, list[str], dict[str, Any] | None]:
    warnings: list[str] = []
    event_broker_order_no = _clean_text(event.get("broker_order_no"))
    record_broker_order_no = _clean_text(record.get("broker_order_no"))

    if event_broker_order_no and record_broker_order_no:
        if event_broker_order_no != record_broker_order_no:
            return None, warnings, _blocked("event_link", "broker_order_no does not match")
        return "broker_order_no", warnings, None

    if event_broker_order_no and not record_broker_order_no:
        warnings.append("order record broker_order_no is missing; Chejan recorder may enrich it")
        return "event_broker_order_no", warnings, None

    if record_broker_order_no and not event_broker_order_no:
        return None, warnings, _blocked("event_link", "normalized_event.broker_order_no is required")

    return None, warnings, _blocked("event_link", "broker_order_no is required to link Chejan event in phase 1")


def review_chejan_event(
    normalized_event: Any,
    order_record: Any = None,
    send_order_result_review_result: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Review normalized Chejan event linkage without side effects."""
    del context

    if not isinstance(normalized_event, dict):
        return _blocked("normalized_event", "normalized_event must be a dict")

    event_type = _event_type(normalized_event)

    if normalized_event.get("normalized") is not True:
        return _blocked("normalized_event", "normalized_event.normalized is not true", event_type)

    if normalized_event.get("unresolved") is not False:
        return _blocked("normalized_event", "normalized_event.unresolved is not false", event_type)

    if normalized_event.get("broker") != "KIWOOM":
        return _blocked("normalized_event", "normalized_event.broker is not KIWOOM", event_type)

    if event_type == "ORDER_UNKNOWN":
        return _blocked("normalized_event", "ORDER_UNKNOWN requires manual Chejan review", event_type)

    if event_type not in _EVENT_RECORD_TYPES and event_type not in _FILL_RECORD_TYPES:
        return _blocked("normalized_event", f"unsupported event_type: {event_type}", event_type)

    if not isinstance(order_record, dict):
        return _blocked("order_record", "order_record must be a dict", event_type)

    if order_record.get("send_order_called") is not True:
        return _blocked("order_record", "order_record.send_order_called is not true", event_type)

    if order_record.get("send_order_result_status") != RESULT_STATUS_CALLED:
        return _blocked("order_record", "order_record.send_order_result_status is not SEND_ORDER_CALLED", event_type)

    for key in ("account_no", "code", "side"):
        event_value = _clean_text(normalized_event.get(key))
        record_value = _clean_text(order_record.get(key))
        if not event_value or not record_value:
            return _blocked("event_link", f"{key} is required", event_type)
        if event_value != record_value:
            return _blocked("event_link", f"normalized_event.{key} does not match order_record.{key}", event_type)

    result_blocked = _validate_result_review(send_order_result_review_result, order_record)
    if result_blocked is not None:
        result_blocked["event_type"] = event_type
        return result_blocked

    matched_by, link_warnings, link_blocked = _broker_order_match(normalized_event, order_record)
    if link_blocked is not None:
        link_blocked["event_type"] = event_type
        return link_blocked

    fill_blocked, fill_warnings = (None, [])
    if event_type in _FILL_RECORD_TYPES:
        fill_blocked, fill_warnings = _validate_fill_event(normalized_event, event_type)
        if fill_blocked is not None:
            return fill_blocked

    warnings = link_warnings + fill_warnings
    return {
        "chejan_review_ok": True,
        "review_stage": "chejan_event_reviewed",
        "next_stage": _next_stage_for_event(event_type),
        "event_type": event_type,
        "order_id": _clean_text(order_record.get("order_id")),
        "order_queued_id": _clean_text(order_record.get("id")),
        "broker_order_no": _clean_text(normalized_event.get("broker_order_no")),
        "request_hash": _clean_text(order_record.get("request_hash")),
        "lock_id": _clean_text(order_record.get("lock_id")),
        "execution_id": _clean_text(order_record.get("execution_id")),
        "matched_by": matched_by,
        "blocked_reasons": [],
        "warnings": warnings,
    }
