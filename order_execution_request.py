# -*- coding: utf-8 -*-
"""Preview-only execution request builder.

This module only builds an execution request candidate in memory. It never
writes runtime/order_executions.json, runtime/order_locks.json, or
order_queue.json, never creates ORDER_QUEUED records, and never calls
SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE = "EXECUTION_REQUEST_PREVIEW"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_order_id(order: dict[str, Any], request_hash_preview: dict[str, Any]) -> str:
    for key in ("id", "order_id", "source_order_id"):
        value = _clean_text(order.get(key))
        if value:
            return value

    hash_source = _as_dict(request_hash_preview.get("hash_source"))
    return _clean_text(hash_source.get("order_id"))


def _extract_source_signal_id(order: dict[str, Any], request_hash_preview: dict[str, Any]) -> str:
    value = _clean_text(order.get("source_signal_id"))
    if value:
        return value

    provenance = _as_dict(order.get("order_provenance"))
    value = _clean_text(provenance.get("source_signal_id"))
    if value:
        return value

    hash_source = _as_dict(request_hash_preview.get("hash_source"))
    return _clean_text(hash_source.get("source_signal_id"))


def _build_execution_id(order_id: str, lock_id: str, request_hash: str) -> str:
    return f"EXEC_PREVIEW_{order_id}_{lock_id}_{request_hash[:12]}"


def _routine_provenance(order: dict[str, Any]) -> dict[str, Any]:
    intent = _as_dict(order.get("execution_intent"))
    provenance = _as_dict(order.get("order_provenance"))
    result: dict[str, Any] = {}
    for field in (
        "routine_id",
        "routine_name",
        "routine_type",
        "routine_instance_id",
        "cycle_identity",
    ):
        value = intent.get(field)
        if value in (None, ""):
            value = order.get(field)
        if value in (None, ""):
            value = provenance.get(field)
        if value not in (None, ""):
            result[field] = value
    return result


def _source_identity(order: dict[str, Any], source_signal_id: str) -> tuple[str, str]:
    intent = _as_dict(order.get("execution_intent"))
    order_intent = _as_dict(order.get("order_intent"))
    source = _clean_text(order.get("source") or order_intent.get("source")).upper()
    command_id = _clean_text(
        order.get("source_command_id")
        or order.get("command_id")
        or intent.get("source_command_id")
    )
    if source == "OPERATION_COMMAND" or command_id:
        return "OPERATION_COMMAND", command_id or source_signal_id
    return "ROUTINE_SIGNAL", ""


def build_execution_request_preview(
    order: Any,
    guard: Any,
    execution_preview: Any,
    final_guard_result: Any,
    lock_preview: Any,
    request_hash_preview: Any,
) -> dict[str, Any]:
    """Build an in-memory execution request candidate."""
    order_dict = _as_dict(order)
    guard_dict = _as_dict(guard)
    execution_preview_dict = _as_dict(execution_preview)
    final_guard_dict = _as_dict(final_guard_result)
    lock_preview_dict = _as_dict(lock_preview)
    request_hash_dict = _as_dict(request_hash_preview)
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(order, dict):
        warnings.append("order must be a dict")
    if not isinstance(guard, dict):
        warnings.append("guard must be a dict")
    if not isinstance(execution_preview, dict):
        warnings.append("execution_preview must be a dict")
    if not isinstance(final_guard_result, dict):
        warnings.append("final_guard_result must be a dict")
    if not isinstance(lock_preview, dict):
        warnings.append("lock_preview must be a dict")
    if not isinstance(request_hash_preview, dict):
        warnings.append("request_hash_preview must be a dict")

    if bool(execution_preview_dict.get("unresolved", True)):
        blocked_reasons.append("execution_preview is unresolved")

    if final_guard_dict.get("ok") is not True:
        blocked_reasons.append("final_guard_result is not ok")

    if bool(lock_preview_dict.get("unresolved", True)):
        blocked_reasons.append("lock_preview is unresolved")

    if bool(request_hash_dict.get("unresolved", True)):
        blocked_reasons.append("request_hash_preview is unresolved")

    request_hash = _clean_text(request_hash_dict.get("request_hash"))
    if not request_hash:
        blocked_reasons.append("request_hash is required")

    lock_id = _clean_text(lock_preview_dict.get("lock_id"))
    order_id = _extract_order_id(order_dict, request_hash_dict)
    source_signal_id = _extract_source_signal_id(order_dict, request_hash_dict)
    source_kind, source_command_id = _source_identity(order_dict, source_signal_id)

    if not lock_id:
        blocked_reasons.append("lock_id is required")
    if not order_id:
        blocked_reasons.append("order_id is required")
    if not source_signal_id and not source_command_id:
        blocked_reasons.append("source identity is required")

    unresolved = bool(blocked_reasons)
    execution_request = None

    if not unresolved:
        adapter_preview = _as_dict(execution_preview_dict.get("adapter_request_preview"))
        request_preview = deepcopy(adapter_preview.get("request_preview"))
        if request_preview is None:
            request_preview = {}

        intent = _as_dict(order_dict.get("execution_intent"))
        planned_execution_id = _clean_text(
            order_dict.get("execution_id") or intent.get("execution_id")
        )
        execution_request = {
            "execution_id": planned_execution_id or _build_execution_id(order_id, lock_id, request_hash),
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "source_kind": source_kind,
            "lock_id": lock_id,
            "request_hash": request_hash,
            "guard_snapshot": deepcopy(guard_dict),
            "request_preview": request_preview,
        }
        if source_command_id:
            execution_request["source_command_id"] = source_command_id
        execution_intent = order_dict.get("execution_intent")
        if isinstance(execution_intent, dict):
            execution_request["execution_intent"] = deepcopy(execution_intent)
            trade_date = _clean_text(execution_intent.get("execution_trade_date"))
            if trade_date:
                execution_request["execution_trade_date"] = trade_date
        for field in (
            "execution_process_id",
            "plan_generation",
            "child_sequence_index",
            "child_sequence_total",
            "child_kind",
            "child_plan",
            "option_snapshot_hash",
        ):
            value = order_dict.get(field, intent.get(field))
            if value not in (None, ""):
                execution_request[field] = deepcopy(value)
        routine_provenance = _routine_provenance(order_dict)
        if routine_provenance:
            execution_request["routine_provenance"] = routine_provenance

    return {
        "ok": not unresolved,
        "stage": STAGE,
        "execution_request": execution_request,
        "unresolved": unresolved,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
    }
