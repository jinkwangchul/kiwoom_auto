# -*- coding: utf-8 -*-
"""Single dry-run broker-order entrypoint.

This module validates the final gate result, request candidate, and queue
record, then calls only the supplied test broker adapter. It does not import
broker-specific adapters, touch runtime files, mutate records, or connect GUI
or timer flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_RESULT_REVIEW_REQUIRED = "SEND_ORDER_RESULT_REVIEW_REQUIRED"
NEXT_STAGE_UNCERTAIN_REVIEW_REQUIRED = "BROKER_CALL_UNCERTAIN_REVIEW_REQUIRED"
FINAL_GATE_NEXT_STAGE_REQUIRED = "SEND_ORDER_ENTRYPOINT_REQUIRED"

_REQUIRED_REQUEST_FIELDS = (
    "order_id",
    "source_signal_id",
    "execution_id",
    "request_hash",
    "lock_id",
    "account_no",
    "side",
    "code",
    "quantity",
    "price",
    "hoga",
)

_MATCH_FIELDS = (
    "order_id",
    "request_hash",
    "lock_id",
    "execution_id",
    "source_signal_id",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "send_order_executed": False,
        "entrypoint_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "send_order_called": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _uncertain(reason: str) -> dict[str, Any]:
    return {
        "send_order_executed": False,
        "entrypoint_stage": "broker_adapter",
        "next_stage": NEXT_STAGE_UNCERTAIN_REVIEW_REQUIRED,
        "send_order_called": False,
        "blocked_reasons": [reason],
        "warnings": ["broker call may be uncertain; manual review required"],
    }


def _manual_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_send_order_entrypoint_confirmed") is True
        or ctx.get("operator_confirmed_for_send_order_entrypoint") is True
    )


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _identity_mismatch(
    final_gate: dict[str, Any],
    adapter_request: dict[str, Any],
    record: dict[str, Any],
    field: str,
) -> str | None:
    gate_value = _clean_text(final_gate.get(field))
    request_value = _clean_text(adapter_request.get(field))
    record_value = _clean_text(record.get(field))
    if not gate_value or not request_value or not record_value:
        return f"{field} is required"
    if gate_value != request_value:
        return f"final_send_gate_result.{field} does not match adapter_request.{field}"
    if record_value != request_value:
        return f"order_queued_record.{field} does not match adapter_request.{field}"
    return None


def _validate_guard(current_guard: Any, account_no: str) -> dict[str, Any] | None:
    guard = _as_dict(current_guard)
    if not guard:
        return _blocked("guard", "current_guard is required")

    if not _truthy(guard.get("real_trade_enabled")):
        return _blocked("guard", "current_guard.real_trade_enabled is not true")

    if not _truthy(guard.get("kiwoom_logged_in")):
        return _blocked("guard", "current_guard.kiwoom_logged_in is not true")

    if not _truthy(guard.get("account_selected")):
        return _blocked("guard", "current_guard.account_selected is not true")

    guard_account_no = _clean_text(guard.get("account_no"))
    if not guard_account_no:
        return _blocked("guard", "current_guard.account_no is required")

    if guard_account_no != account_no:
        return _blocked("guard", "current_guard.account_no does not match adapter_request.account_no")

    if not _truthy(guard.get("operator_confirmed")):
        return _blocked("guard", "current_guard.operator_confirmed is not true")

    return None


def _broker_name(broker_adapter: Any) -> str:
    return (
        _clean_text(getattr(broker_adapter, "broker_name", ""))
        or _clean_text(getattr(broker_adapter, "name", ""))
        or broker_adapter.__class__.__name__
    )


def execute_send_order(
    final_send_gate_result: Any,
    adapter_request: Any,
    order_queued_record: Any,
    broker_adapter: Any,
    queue_path: Any = None,
    queue_snapshot: Any = None,
    current_queue_snapshot: Any = None,
    current_guard: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Validate inputs and call the supplied mock broker adapter only."""
    del queue_path

    if not isinstance(final_send_gate_result, dict):
        return _blocked("final_gate", "final_send_gate_result must be a dict")

    if final_send_gate_result.get("final_send_gate_ok") is not True:
        return _blocked("final_gate", "final_send_gate_result.final_send_gate_ok is not true")

    if final_send_gate_result.get("next_stage") != FINAL_GATE_NEXT_STAGE_REQUIRED:
        return _blocked(
            "final_gate",
            "final_send_gate_result.next_stage is not SEND_ORDER_ENTRYPOINT_REQUIRED",
        )

    if final_send_gate_result.get("send_order_called") is not False:
        return _blocked("final_gate", "final_send_gate_result.send_order_called is not false")

    if not isinstance(adapter_request, dict):
        return _blocked("adapter_request", "adapter_request must be a dict")

    for field in _REQUIRED_REQUEST_FIELDS:
        value = adapter_request.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return _blocked("adapter_request", f"adapter_request.{field} is required")

    if not isinstance(order_queued_record, dict):
        return _blocked("record", "order_queued_record must be a dict")

    if order_queued_record.get("status") != "ORDER_QUEUED":
        return _blocked("record", "order_queued_record.status is not ORDER_QUEUED")

    if order_queued_record.get("send_order_called") is not False:
        return _blocked("record", "order_queued_record.send_order_called is not false")

    if order_queued_record.get("execution_enabled") is not False:
        return _blocked("record", "order_queued_record.execution_enabled is not false")

    for field in _MATCH_FIELDS:
        reason = _identity_mismatch(final_send_gate_result, adapter_request, order_queued_record, field)
        if reason:
            return _blocked("record_consistency", reason)

    guard_blocked = _validate_guard(current_guard, _clean_text(adapter_request.get("account_no")))
    if guard_blocked is not None:
        return guard_blocked

    if not _manual_confirmed(context):
        return _blocked("operator_confirmation", "manual send order entrypoint confirmation is required")

    if isinstance(queue_snapshot, dict) and isinstance(current_queue_snapshot, dict):
        before_sha = _snapshot_sha256(queue_snapshot)
        current_sha = _snapshot_sha256(current_queue_snapshot)
        if not before_sha or not current_sha:
            return _blocked("stale_queue", "queue snapshot sha256 is required")
        if before_sha != current_sha:
            return _blocked(
                "stale_queue",
                "queue file changed after final send gate; rerun final send gate",
            )

    if broker_adapter is None:
        return _blocked("broker_adapter", "broker_adapter is required")

    send_callable = getattr(broker_adapter, "send_order", None)
    if not callable(send_callable):
        return _blocked("broker_adapter", "broker_adapter.send_order must be callable")

    request_for_adapter = deepcopy(adapter_request)
    try:
        broker_result = send_callable(request_for_adapter)
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _uncertain(f"broker adapter raised exception: {exc}")

    return {
        "send_order_executed": True,
        "entrypoint_stage": "send_order_called_mock",
        "next_stage": NEXT_STAGE_RESULT_REVIEW_REQUIRED,
        "broker": _broker_name(broker_adapter),
        "order_id": _clean_text(adapter_request.get("order_id")),
        "order_queued_id": _clean_text(order_queued_record.get("id")),
        "request_hash": _clean_text(adapter_request.get("request_hash")),
        "lock_id": _clean_text(adapter_request.get("lock_id")),
        "execution_id": _clean_text(adapter_request.get("execution_id")),
        "broker_result": broker_result if isinstance(broker_result, dict) else {"raw_result": broker_result},
        "runtime_write_required": True,
        "send_order_called": True,
        "blocked_reasons": [],
        "warnings": [],
    }
