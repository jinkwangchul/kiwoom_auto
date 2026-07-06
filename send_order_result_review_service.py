# -*- coding: utf-8 -*-
"""Review recorded broker-order call result integrity.

This module only reviews in-memory dictionaries produced after a recorded
broker-order call result. It does not read or write runtime files, call broker
APIs, connect event handlers, or mutate input records.
"""

from __future__ import annotations

from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_EVENT_REQUIRED = "CHEJAN_OR_EXECUTION_EVENT_REQUIRED"
RECORDER_NEXT_STAGE_REQUIRED = "SEND_ORDER_RESULT_REVIEW_REQUIRED"
RESULT_STATUS_CALLED = "SEND_ORDER_CALLED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "result_review_ok": False,
        "review_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
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


def _broker_order_no(record: dict[str, Any], broker_result: dict[str, Any]) -> str:
    for value in (
        record.get("broker_order_no"),
        broker_result.get("broker_order_no"),
        broker_result.get("order_no"),
        broker_result.get("order_number"),
    ):
        text = _clean_text(value)
        if text:
            return text
    return ""


def _compare_identity(
    recorder_result: dict[str, Any],
    record: dict[str, Any],
    recorder_field: str,
    record_field: str | None = None,
) -> str | None:
    target_field = recorder_field if record_field is None else record_field
    recorder_value = _clean_text(recorder_result.get(recorder_field))
    record_value = _clean_text(record.get(target_field))
    if not recorder_value or not record_value:
        return f"{recorder_field} is required"
    if recorder_value != record_value:
        return f"recorder_result.{recorder_field} does not match updated_order_record.{target_field}"
    return None


def _broker_results_match(record_broker_result: dict[str, Any], broker_result: dict[str, Any]) -> bool:
    if record_broker_result == broker_result:
        return True

    keys = ("broker_status", "request_hash", "broker_order_no", "order_no", "order_number")
    comparable_keys = [
        key
        for key in keys
        if key in record_broker_result and key in broker_result
    ]
    if not comparable_keys:
        return False

    return all(
        _clean_text(record_broker_result.get(key)) == _clean_text(broker_result.get(key))
        for key in comparable_keys
    )


def review_send_order_result(
    recorder_result: Any,
    updated_order_record: Any,
    broker_result: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Review recorded result consistency without side effects."""
    del context

    if not isinstance(recorder_result, dict):
        return _blocked("recorder_result", "recorder_result must be a dict")

    if recorder_result.get("recorded") is not True:
        return _blocked("recorder_result", "recorder_result.recorded is not true")

    if recorder_result.get("next_stage") != RECORDER_NEXT_STAGE_REQUIRED:
        return _blocked(
            "recorder_result",
            "recorder_result.next_stage is not SEND_ORDER_RESULT_REVIEW_REQUIRED",
        )

    if recorder_result.get("send_order_called") is not True:
        return _blocked("recorder_result", "recorder_result.send_order_called is not true")

    if recorder_result.get("send_order_result_status") != RESULT_STATUS_CALLED:
        return _blocked("recorder_result", "recorder_result.send_order_result_status is not SEND_ORDER_CALLED")

    if not isinstance(updated_order_record, dict):
        return _blocked("updated_order_record", "updated_order_record must be a dict")

    if updated_order_record.get("status") != "ORDER_QUEUED":
        return _blocked("updated_order_record", "updated_order_record.status is not ORDER_QUEUED")

    if updated_order_record.get("send_order_called") is not True:
        return _blocked("updated_order_record", "updated_order_record.send_order_called is not true")

    if updated_order_record.get("send_order_result_status") != RESULT_STATUS_CALLED:
        return _blocked(
            "updated_order_record",
            "updated_order_record.send_order_result_status is not SEND_ORDER_CALLED",
        )

    for field in (
        "send_order_entrypoint_stage",
        "send_order_called_at",
        "send_order_result_recorded_at",
        "broker",
    ):
        if _missing(updated_order_record.get(field)):
            return _blocked("updated_order_record", f"updated_order_record.{field} is required")

    record_broker_result = _as_dict(updated_order_record.get("broker_result"))
    broker_result_dict = record_broker_result if broker_result is None else _as_dict(broker_result)
    if not broker_result_dict:
        return _blocked("broker_result", "broker_result must be a dict")

    for field in ("order_id", "request_hash", "lock_id", "execution_id"):
        reason = _compare_identity(recorder_result, updated_order_record, field)
        if reason:
            return _blocked("record_consistency", reason)

    reason = _compare_identity(recorder_result, updated_order_record, "order_queued_id", "id")
    if reason:
        return _blocked("record_consistency", reason)

    if record_broker_result and not _broker_results_match(record_broker_result, broker_result_dict):
        return _blocked("broker_result", "updated_order_record.broker_result does not match broker_result")

    broker_order_no = _broker_order_no(updated_order_record, broker_result_dict)
    warnings: list[str] = []
    if not broker_order_no:
        warnings.append("broker_order_no is missing; wait for broker/Chejan confirmation")

    return {
        "result_review_ok": True,
        "review_stage": "send_order_result_reviewed",
        "next_stage": NEXT_STAGE_EVENT_REQUIRED,
        "send_order_called": True,
        "send_order_result_status": RESULT_STATUS_CALLED,
        "order_id": _clean_text(recorder_result.get("order_id")),
        "order_queued_id": _clean_text(recorder_result.get("order_queued_id")),
        "request_hash": _clean_text(recorder_result.get("request_hash")),
        "lock_id": _clean_text(recorder_result.get("lock_id")),
        "execution_id": _clean_text(recorder_result.get("execution_id")),
        "broker": _clean_text(updated_order_record.get("broker")),
        "broker_order_no": broker_order_no,
        "blocked_reasons": [],
        "warnings": warnings,
    }
