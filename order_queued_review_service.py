# -*- coding: utf-8 -*-
"""Preview-only ORDER_QUEUED record review gate.

This module validates an ORDER_QUEUED record before it can move toward a
send-order request adapter preview. It never reads or writes runtime files and
does not call adapter preview or send-order code.
"""

from __future__ import annotations

from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_SEND_ORDER_REQUEST_PREVIEW_REQUIRED = "SEND_ORDER_REQUEST_PREVIEW_REQUIRED"

_REQUIRED_RECORD_FIELDS = (
    "id",
    "status",
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
    "send_order_called",
    "execution_enabled",
)

_REQUIRED_EXECUTION_REQUEST_FIELDS = (
    "execution_id",
    "order_id",
    "source_signal_id",
    "lock_id",
    "request_hash",
    "guard_snapshot",
    "request_preview",
)

_MATCH_FIELDS = (
    "request_hash",
    "lock_id",
    "execution_id",
    "order_id",
    "source_signal_id",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "review_ok": False,
        "review_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def review_order_queued_record(record: Any, context: Any = None) -> dict[str, Any]:
    """Review one ORDER_QUEUED record without mutating it."""
    del context

    if not isinstance(record, dict):
        return _blocked("record_validation", "record must be a dict")

    if record.get("status") != "ORDER_QUEUED":
        return _blocked("record_validation", "record.status is not ORDER_QUEUED")

    if "send_order_called" not in record or record.get("send_order_called") is None:
        return _blocked("record_validation", "record.send_order_called is required")

    if record.get("send_order_called") is not False:
        return _blocked("record_validation", "record.send_order_called is not false")

    if "execution_enabled" not in record or record.get("execution_enabled") is None:
        return _blocked("record_validation", "record.execution_enabled is required")

    if record.get("execution_enabled") is not False:
        return _blocked("record_validation", "record.execution_enabled is not false")

    if record.get("source") != "execution_queue_pending":
        return _blocked("record_validation", "record.source is not execution_queue_pending")

    blocked_reasons = record.get("blocked_reasons")
    if isinstance(blocked_reasons, list) and blocked_reasons:
        return _blocked("record_validation", "record.blocked_reasons is not empty")
    if blocked_reasons not in (None, []) and not isinstance(blocked_reasons, list):
        return _blocked("record_validation", "record.blocked_reasons must be a list")

    for field in _REQUIRED_RECORD_FIELDS:
        if field == "execution_request":
            if not isinstance(record.get(field), dict):
                return _blocked("record_validation", "record.execution_request must be a dict")
            continue
        if _missing(record.get(field)):
            return _blocked("record_validation", f"record.{field} is required")

    execution_request = _as_dict(record.get("execution_request"))
    for field in _REQUIRED_EXECUTION_REQUEST_FIELDS:
        if field in {"guard_snapshot", "request_preview"}:
            if not isinstance(execution_request.get(field), dict):
                return _blocked("execution_request_validation", f"execution_request.{field} must be a dict")
            continue
        if _missing(execution_request.get(field)):
            return _blocked("execution_request_validation", f"execution_request.{field} is required")

    for field in _MATCH_FIELDS:
        if _clean_text(record.get(field)) != _clean_text(execution_request.get(field)):
            return _blocked("record_consistency", f"record.{field} does not match execution_request.{field}")

    return {
        "review_ok": True,
        "review_stage": "order_queued_record_reviewed",
        "next_stage": NEXT_STAGE_SEND_ORDER_REQUEST_PREVIEW_REQUIRED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "order_queued_id": _clean_text(record.get("id")),
        "order_id": _clean_text(record.get("order_id")),
        "request_hash": _clean_text(record.get("request_hash")),
        "lock_id": _clean_text(record.get("lock_id")),
        "execution_id": _clean_text(record.get("execution_id")),
        "blocked_reasons": [],
        "warnings": [],
    }
