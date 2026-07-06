# -*- coding: utf-8 -*-
"""Preview-only review of committed ORDER_QUEUED records.

This layer reviews a queue commit result before Final Send Gate input is built.
It never calls Final Send Gate, queue commit services, SendOrder, runtime
writers, GUI, or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


REVIEW_TYPE = "EXECUTION_QUEUE_COMMITTED_REVIEW"
STATUS_READY = "READY_FOR_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
NEXT_STAGE_FINAL_SEND_GATE_REQUIRED = "FINAL_SEND_GATE_REQUIRED"
QUEUE_COMMITTED_REVIEW_REQUIRED = "QUEUE_COMMITTED_REVIEW_REQUIRED"


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
    next_stage: str = "BLOCKED",
    order_queued_record: dict[str, Any] | None = None,
    identity: dict[str, str] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "review_type": REVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "send_order_called": False,
        "next_stage": next_stage,
        "order_queued_record": deepcopy(order_queued_record) if isinstance(order_queued_record, dict) else None,
        "identity": deepcopy(identity or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _extract_commit_result(queue_commit_result: dict[str, Any]) -> dict[str, Any]:
    nested = _as_dict(queue_commit_result.get("commit_result"))
    return nested if nested else queue_commit_result


def _extract_record(commit_result: dict[str, Any]) -> dict[str, Any]:
    for key in ("order_queued_record", "order_queued_record_preview", "committed_record"):
        record = _as_dict(commit_result.get(key))
        if record:
            return deepcopy(record)

    record = {
        "id": _text(commit_result.get("order_queued_id")),
        "status": _text(commit_result.get("status")),
        "order_id": _text(commit_result.get("order_id")),
        "source_signal_id": _text(commit_result.get("source_signal_id")),
        "execution_id": _text(commit_result.get("execution_id")),
        "request_hash": _text(commit_result.get("request_hash")),
        "lock_id": _text(commit_result.get("lock_id")),
        "send_order_called": commit_result.get("send_order_called"),
        "execution_enabled": commit_result.get("execution_enabled"),
    }
    return {key: value for key, value in record.items() if value not in ("", None)}


def _identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "order_id": _text(record.get("order_id")),
        "source_signal_id": _text(record.get("source_signal_id")),
        "execution_id": _text(record.get("execution_id")),
        "request_hash": _text(record.get("request_hash")),
        "lock_id": _text(record.get("lock_id")),
    }


def review_execution_queue_committed(queue_commit_result: Any) -> dict[str, Any]:
    """Review a queue commit result without side effects."""
    if not isinstance(queue_commit_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_QUEUE_COMMIT_RESULT"])

    commit_result = _extract_commit_result(queue_commit_result)
    warnings = _as_list(queue_commit_result.get("warnings")) + _as_list(commit_result.get("warnings"))

    outer_status = _text(queue_commit_result.get("status")).upper()
    if outer_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(queue_commit_result.get("blocked_reasons")) or ["QUEUE_COMMIT_RESULT_INVALID"],
            warnings=warnings,
        )

    manual_commit = queue_commit_result.get("manual_commit")
    committed = commit_result.get("committed")
    if outer_status and outer_status != "COMMITTED":
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(queue_commit_result.get("blocked_reasons")) or ["QUEUE_COMMIT_NOT_COMMITTED"],
            warnings=warnings,
        )
    if manual_commit is False or committed is not True:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(queue_commit_result.get("blocked_reasons"))
            or _as_list(commit_result.get("blocked_reasons"))
            or ["QUEUE_COMMIT_NOT_COMMITTED"],
            warnings=warnings,
        )

    next_stage = _text(queue_commit_result.get("next_stage")) or _text(commit_result.get("next_stage"))
    if next_stage != QUEUE_COMMITTED_REVIEW_REQUIRED:
        return _result(status=STATUS_BLOCKED, issues=["QUEUE_COMMIT_NEXT_STAGE_NOT_REVIEW_REQUIRED"], warnings=warnings)

    record = _extract_record(commit_result)
    if not record:
        return _result(status=STATUS_BLOCKED, issues=["ORDER_QUEUED_RECORD_REQUIRED"], warnings=warnings)

    if record.get("status") != "ORDER_QUEUED":
        return _result(status=STATUS_BLOCKED, order_queued_record=record, issues=["ORDER_QUEUED_RECORD_STATUS_INVALID"], warnings=warnings)
    if record.get("send_order_called") is not False:
        return _result(status=STATUS_BLOCKED, order_queued_record=record, issues=["ORDER_QUEUED_RECORD_SEND_ORDER_CALLED_NOT_FALSE"], warnings=warnings)
    if record.get("execution_enabled") is not False:
        return _result(status=STATUS_BLOCKED, order_queued_record=record, issues=["ORDER_QUEUED_RECORD_EXECUTION_ENABLED_NOT_FALSE"], warnings=warnings)

    identity = _identity(record)
    missing = [field for field, value in identity.items() if not value]
    if missing:
        return _result(
            status=STATUS_BLOCKED,
            order_queued_record=record,
            identity=identity,
            issues=[f"MISSING_{field.upper()}" for field in missing],
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        next_stage=NEXT_STAGE_FINAL_SEND_GATE_REQUIRED,
        order_queued_record=record,
        identity=identity,
        warnings=warnings,
    )
