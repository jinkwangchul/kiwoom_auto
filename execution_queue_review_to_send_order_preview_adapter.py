# -*- coding: utf-8 -*-
"""Adapt queue committed review results to SendOrder request previews.

This layer only bridges an already reviewed ORDER_QUEUED record into the
existing Kiwoom SendOrder request preview builder. It never calls Final Send
Gate, SendOrder, queue commit services, runtime writers, GUI, or real execution
components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from kiwoom_send_order_preview_service import preview_kiwoom_send_order_request


ADAPTER_TYPE = "EXECUTION_QUEUE_REVIEW_TO_SEND_ORDER_PREVIEW_ADAPTER"
STATUS_READY = "READY_FOR_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
QUEUE_REVIEW_READY = "READY_FOR_FINAL_SEND_GATE"
QUEUE_REVIEW_NEXT_STAGE_REQUIRED = "FINAL_SEND_GATE_REQUIRED"
SEND_ORDER_PREVIEW_NEXT_STAGE = "SEND_ORDER_REQUEST_PREVIEW_REQUIRED"

_IDENTITY_FIELDS = ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    adapter_preview_result: dict[str, Any] | None = None,
    order_queued_record: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "send_order_called": False,
        "final_send_gate_called": False,
        "adapter_preview_result": deepcopy(adapter_preview_result) if isinstance(adapter_preview_result, dict) else None,
        "order_queued_record": deepcopy(order_queued_record) if isinstance(order_queued_record, dict) else None,
        "identity": deepcopy(identity or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _missing_identity(identity: dict[str, Any]) -> list[str]:
    return [field for field in _IDENTITY_FIELDS if not _text(identity.get(field))]


def _record_review_result(queue_review_result: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    record = _as_dict(queue_review_result.get("order_queued_record"))
    return {
        "review_ok": True,
        "review_stage": "queue_committed_review_adapted",
        "next_stage": SEND_ORDER_PREVIEW_NEXT_STAGE,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "order_queued_id": _text(record.get("id")),
        "order_id": _text(identity.get("order_id")),
        "source_signal_id": _text(identity.get("source_signal_id")),
        "execution_id": _text(identity.get("execution_id")),
        "request_hash": _text(identity.get("request_hash")),
        "lock_id": _text(identity.get("lock_id")),
        "blocked_reasons": [],
        "warnings": [],
    }


def _identity_issues(queue_identity: dict[str, Any], adapter_preview_result: dict[str, Any]) -> list[str]:
    request_preview = _as_dict(adapter_preview_result.get("send_order_request_preview"))
    issues: list[str] = []
    for field in _IDENTITY_FIELDS:
        queue_value = _text(queue_identity.get(field))
        preview_value = _text(request_preview.get(field))
        if not preview_value:
            issues.append(f"MISSING_ADAPTER_PREVIEW_{field.upper()}")
        elif queue_value != preview_value:
            issues.append(f"IDENTITY_MISMATCH_{field.upper()}")
    return issues


def adapt_queue_review_to_send_order_preview(
    queue_committed_review_result: Any,
    current_guard: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Build a SendOrder request preview from a queue committed review result."""
    del current_guard

    if not isinstance(queue_committed_review_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_QUEUE_COMMITTED_REVIEW_RESULT"])

    warnings = _as_list(queue_committed_review_result.get("warnings"))
    queue_status = _text(queue_committed_review_result.get("status"))
    if queue_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(queue_committed_review_result.get("issues")) or ["QUEUE_COMMITTED_REVIEW_INVALID"],
            warnings=warnings,
        )
    if queue_status != QUEUE_REVIEW_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(queue_committed_review_result.get("issues")) or ["QUEUE_COMMITTED_REVIEW_NOT_READY"],
            warnings=warnings,
        )

    if queue_committed_review_result.get("next_stage") != QUEUE_REVIEW_NEXT_STAGE_REQUIRED:
        return _result(status=STATUS_BLOCKED, issues=["QUEUE_REVIEW_NEXT_STAGE_NOT_FINAL_SEND_GATE_REQUIRED"], warnings=warnings)

    order_queued_record = _as_dict(queue_committed_review_result.get("order_queued_record"))
    if not order_queued_record:
        return _result(status=STATUS_BLOCKED, issues=["ORDER_QUEUED_RECORD_REQUIRED"], warnings=warnings)

    identity = _as_dict(queue_committed_review_result.get("identity"))
    if not identity:
        return _result(status=STATUS_BLOCKED, order_queued_record=order_queued_record, issues=["IDENTITY_REQUIRED"], warnings=warnings)

    missing = _missing_identity(identity)
    if missing:
        return _result(
            status=STATUS_BLOCKED,
            order_queued_record=order_queued_record,
            identity=identity,
            issues=[f"MISSING_{field.upper()}" for field in missing],
            warnings=warnings,
        )

    record_review_result = _record_review_result(queue_committed_review_result, identity)
    adapter_preview_result = preview_kiwoom_send_order_request(
        record_review_result,
        deepcopy(order_queued_record),
        context=context,
    )
    if not isinstance(adapter_preview_result, dict):
        return _result(
            status=STATUS_INVALID,
            order_queued_record=order_queued_record,
            identity=identity,
            issues=["MALFORMED_ADAPTER_PREVIEW_RESULT"],
            warnings=warnings,
        )

    combined_warnings = warnings + _as_list(adapter_preview_result.get("warnings"))
    if adapter_preview_result.get("adapter_preview_ok") is not True:
        return _result(
            status=STATUS_BLOCKED,
            adapter_preview_result=adapter_preview_result,
            order_queued_record=order_queued_record,
            identity=identity,
            issues=_as_list(adapter_preview_result.get("blocked_reasons")) or ["ADAPTER_PREVIEW_NOT_READY"],
            warnings=combined_warnings,
        )

    identity_issues = _identity_issues(identity, adapter_preview_result)
    if identity_issues:
        return _result(
            status=STATUS_BLOCKED,
            adapter_preview_result=adapter_preview_result,
            order_queued_record=order_queued_record,
            identity=identity,
            issues=identity_issues,
            warnings=combined_warnings,
        )

    return _result(
        status=STATUS_READY,
        adapter_preview_result=adapter_preview_result,
        order_queued_record=order_queued_record,
        identity=identity,
        warnings=combined_warnings,
    )
