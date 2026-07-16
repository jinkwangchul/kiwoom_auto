# -*- coding: utf-8 -*-
"""Preview-only Kiwoom send-order request candidate builder.

This module validates a reviewed ORDER_QUEUED record and builds an in-memory
request candidate. It does not read or write runtime files, mutate order
records, call adapter code, or perform broker API calls.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_FINAL_SEND_GATE_REQUIRED = "FINAL_SEND_GATE_REQUIRED"
REVIEW_NEXT_STAGE_REQUIRED = "SEND_ORDER_REQUEST_PREVIEW_REQUIRED"

_INTERNAL_SIDES = {"BUY", "SELL"}
_INTERNAL_HOGAS = {"MARKET", "LIMIT"}
_CANCEL_ACTIONS = {"CANCEL", "CANCEL_ORDER", "ORDER_CANCEL", "CANCEL_PENDING_ORDER"}
_MODIFY_ACTIONS = {"MODIFY", "AMEND", "CORRECT", "CHANGE", "MODIFY_ORDER", "ORDER_MODIFY"}
_CANCEL_MODIFY_TOKENS = {
    "CANCEL",
    "CANCEL_ORDER",
    "MODIFY",
    "AMEND",
    "CORRECT",
    "CHANGE",
    "취소",
    "정정",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "adapter_preview_ok": False,
        "adapter_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "send_order_request_preview": None,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _positive_number(value: Any) -> bool:
    number = _decimal(value)
    return number is not None and number > 0


def _zero_or_positive_number(value: Any) -> bool:
    number = _decimal(value)
    return number is not None and number >= 0


def _number_for_preview(value: Any) -> Any:
    number = _decimal(value)
    if number is None:
        return value
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _cancel_or_modify_candidate(request_preview: dict[str, Any]) -> bool:
    original_order_no = _clean_text(
        request_preview.get("original_order_no")
        if "original_order_no" in request_preview
        else request_preview.get("org_order_no")
    )
    if original_order_no:
        return True

    for field in ("action", "order_action", "request_type", "order_kind", "trade_type", "order_category"):
        value = _norm(request_preview.get(field))
        if value in _CANCEL_MODIFY_TOKENS:
            return True

    return False


def _request_action(request_preview: dict[str, Any]) -> str:
    for field in ("action", "order_action", "request_type", "order_kind", "trade_type", "order_category"):
        value = _norm(request_preview.get(field))
        if value in _CANCEL_ACTIONS:
            return "CANCEL"
        if value in _MODIFY_ACTIONS:
            return "MODIFY"
    return ""


def _require_consistent_field(
    record: dict[str, Any],
    execution_request: dict[str, Any],
    field: str,
) -> str | None:
    record_value = _clean_text(record.get(field))
    request_value = _clean_text(execution_request.get(field))
    if not record_value and not request_value:
        return f"{field} is required"
    if record_value and request_value and record_value != request_value:
        return f"record.{field} does not match execution_request.{field}"
    return None


def preview_kiwoom_send_order_request(
    record_review_result: Any,
    order_queued_record: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Build a send-order request candidate without side effects."""
    del context

    if not isinstance(record_review_result, dict):
        return _blocked("record_review", "record_review_result must be a dict")

    if record_review_result.get("review_ok") is not True:
        return _blocked("record_review", "record_review_result.review_ok is not true")

    if record_review_result.get("next_stage") != REVIEW_NEXT_STAGE_REQUIRED:
        return _blocked(
            "record_review",
            "record_review_result.next_stage is not SEND_ORDER_REQUEST_PREVIEW_REQUIRED",
        )

    if not isinstance(order_queued_record, dict):
        return _blocked("record", "order_queued_record must be a dict")

    if order_queued_record.get("status") != "ORDER_QUEUED":
        return _blocked("record", "order_queued_record.status is not ORDER_QUEUED")

    if order_queued_record.get("send_order_called") is not False:
        return _blocked("record", "order_queued_record.send_order_called is not false")

    if order_queued_record.get("execution_enabled") is not False:
        return _blocked("record", "order_queued_record.execution_enabled is not false")

    execution_request = _as_dict(order_queued_record.get("execution_request"))
    if not execution_request:
        return _blocked("execution_request", "order_queued_record.execution_request is required")

    request_preview = _as_dict(execution_request.get("request_preview"))
    if not request_preview:
        return _blocked("request_preview", "execution_request.request_preview is required")

    for field in ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id"):
        reason = _require_consistent_field(order_queued_record, execution_request, field)
        if reason:
            return _blocked("record_consistency", reason)

    guard_snapshot = _as_dict(execution_request.get("guard_snapshot"))
    account_no = _first_text(request_preview.get("account_no"), guard_snapshot.get("account_no"))
    if not account_no:
        return _blocked("request_preview", "account_no is required")

    side = _norm(_first_text(request_preview.get("side"), request_preview.get("order_type")))
    if not side:
        return _blocked("request_preview", "side/order_type is required")
    if side not in _INTERNAL_SIDES:
        return _blocked("request_preview", "side/order_type must be BUY or SELL")

    order_action = _request_action(request_preview)
    original_order_no = _clean_text(request_preview.get("original_order_no") or request_preview.get("org_order_no"))
    if _cancel_or_modify_candidate(request_preview):
        if not order_action:
            return _blocked("request_preview", "cancel/modify action is required")
        if not original_order_no:
            return _blocked("request_preview", "original_order_no is required for cancel/modify")

    code = _clean_text(request_preview.get("code"))
    if not code:
        return _blocked("request_preview", "code is required")

    quantity = request_preview.get("quantity")
    if not _positive_number(quantity):
        return _blocked("request_preview", "quantity must be greater than 0")

    price = request_preview.get("price")
    if _missing(price):
        return _blocked("request_preview", "price is required")

    hoga = _norm(request_preview.get("hoga"))
    if not hoga:
        return _blocked("request_preview", "hoga is required")
    if hoga not in _INTERNAL_HOGAS:
        return _blocked("request_preview", "hoga must be MARKET or LIMIT")

    if order_action == "CANCEL" and not _zero_or_positive_number(price):
        return _blocked("request_preview", "cancel price must be zero or greater")

    if order_action != "CANCEL" and hoga == "LIMIT" and not _positive_number(price):
        return _blocked("request_preview", "LIMIT price must be greater than 0")

    if order_action != "CANCEL" and hoga == "MARKET" and not _zero_or_positive_number(price):
        return _blocked("request_preview", "MARKET price must be zero or greater")

    order_id = _first_text(order_queued_record.get("order_id"), execution_request.get("order_id"))
    source_signal_id = _first_text(
        order_queued_record.get("source_signal_id"),
        execution_request.get("source_signal_id"),
    )
    execution_id = _first_text(order_queued_record.get("execution_id"), execution_request.get("execution_id"))
    request_hash = _first_text(order_queued_record.get("request_hash"), execution_request.get("request_hash"))
    lock_id = _first_text(order_queued_record.get("lock_id"), execution_request.get("lock_id"))
    screen_no = _clean_text(request_preview.get("screen_no")) or "9000"
    rqname = _clean_text(request_preview.get("rqname")) or f"SEND_ORDER_PREVIEW_{order_id}"

    return {
        "adapter_preview_ok": True,
        "adapter_stage": "kiwoom_send_order_request_preview_created",
        "next_stage": NEXT_STAGE_FINAL_SEND_GATE_REQUIRED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "send_order_request_preview": {
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "execution_id": execution_id,
            "request_hash": request_hash,
            "lock_id": lock_id,
            "account_no": account_no,
            "side": side,
            "order_action": order_action or "NEW",
            "code": code,
            "quantity": _number_for_preview(quantity),
            "price": _number_for_preview(price),
            "hoga": hoga,
            "original_order_no": original_order_no,
            "screen_no": screen_no,
            "rqname": rqname,
        },
        "blocked_reasons": [],
        "warnings": [],
    }
