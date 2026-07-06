# -*- coding: utf-8 -*-
"""Read-only inspector for execution candidates just before execution preview.

This module only diagnoses already-built gate, candidate, and queue preview
results. It never writes runtime files, enqueues orders, calls SendOrder, or
invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


STAGE = "EXECUTION_CANDIDATE_INSPECTION"
STATUS_READY = "READY"
STATUS_NOT_READY = "NOT_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper().replace("-", "_").replace(" ", "_")


def _number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _order_intent(order: dict[str, Any]) -> dict[str, Any]:
    intent = order.get("order_intent")
    return intent if isinstance(intent, dict) else {}


def _order_status(order: dict[str, Any], queue_preview: dict[str, Any]) -> str:
    return _norm(
        order.get("status")
        or order.get("order_status")
        or queue_preview.get("order_status")
        or queue_preview.get("candidate_result")
    )


def _quantity(order: dict[str, Any]) -> Decimal | None:
    return _number(order.get("quantity", order.get("qty")))


def _price(order: dict[str, Any]) -> Decimal | None:
    return _number(order.get("price"))


def _order_type(order: dict[str, Any]) -> str:
    intent = _order_intent(order)
    value = (
        order.get("order_type")
        or order.get("side")
        or intent.get("order_type")
        or intent.get("side")
        or intent.get("signal")
    )
    return _norm(value)


def _hoga(order: dict[str, Any]) -> str:
    intent = _order_intent(order)
    value = (
        order.get("hoga")
        or order.get("order_hoga")
        or intent.get("hoga")
        or intent.get("order_hoga")
        or intent.get("order_method")
    )
    return _norm(value)


def _queue_write_preview(queue_preview: dict[str, Any]) -> dict[str, Any]:
    nested = _as_dict(queue_preview.get("queue_write_preview_result"))
    return nested if nested else queue_preview


def _preview_connected(queue_preview: dict[str, Any]) -> bool:
    if queue_preview.get("queue_writer_preview_connected") is True:
        return True
    return _queue_write_preview(queue_preview).get("write_preview") is True


def _runtime_write(queue_preview: dict[str, Any]) -> bool:
    if queue_preview.get("runtime_write") is True:
        return True
    return _queue_write_preview(queue_preview).get("no_write") is False


def _flag(queue_preview: dict[str, Any], name: str) -> bool:
    return queue_preview.get(name) is True


def _candidate_issues(order: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not order:
        return ["NO_ORDER_CANDIDATE"]

    if _price(order) is None:
        issues.append("MISSING_ORDER_PRICE")

    qty = _quantity(order)
    if qty is None or qty <= 0:
        issues.append("MISSING_ORDER_QTY")

    if _order_type(order) not in {"BUY", "SELL"}:
        issues.append("INVALID_ORDER_TYPE")

    if _hoga(order) not in {"MARKET", "MARKET_ORDER", "MKT", "시장가", "LIMIT", "LIMIT_ORDER", "LMT", "CURRENT_PRICE", "지정가", "현재가"}:
        issues.append("INVALID_HOGA")

    return issues


def _safety_warnings(
    *,
    runtime_write: bool,
    execution_connected: bool,
    send_order_connected: bool,
) -> list[str]:
    warnings = ["Preview mode"]
    if not runtime_write:
        warnings.append("Runtime write disabled")
    if not execution_connected:
        warnings.append("Execution disabled")
    if not send_order_connected:
        warnings.append("SendOrder disabled")
    return warnings


def inspect_execution_candidate(
    gate_result: Any,
    order_candidate: Any,
    queue_preview_result: Any,
) -> dict[str, Any]:
    """Inspect an execution candidate without side effects."""
    gate = _as_dict(gate_result)
    order = _as_dict(order_candidate)
    queue_preview = _as_dict(queue_preview_result)

    gate_value = _clean_text(gate.get("gate_result") or queue_preview.get("gate_result"))
    order_status = _order_status(order, queue_preview)
    preview_connected = _preview_connected(queue_preview)
    runtime_write = _runtime_write(queue_preview)
    execution_connected = _flag(queue_preview, "execution_connected")
    send_order_connected = _flag(queue_preview, "send_order_connected")

    issues: list[str] = []
    candidate_issues = _candidate_issues(order)

    if gate_value != "OPEN":
        status = STATUS_BLOCKED
        eligible = False
        gate_issues = [str(item) for item in _as_list(gate.get("blocked_reasons"))]
        issues.extend(gate_issues or [gate_value or "SIGNAL_NOT_READY"])
        summary = "BLOCKED_BY_POLICY"
    elif candidate_issues:
        status = STATUS_INVALID
        eligible = False
        issues.extend(candidate_issues)
        summary = "INVALID_EXECUTION_CANDIDATE"
    elif order_status != "REAL_READY":
        status = STATUS_NOT_READY
        eligible = False
        issues.append("SIGNAL_NOT_READY")
        summary = "SIGNAL_NOT_READY"
    elif not preview_connected:
        status = STATUS_NOT_READY
        eligible = False
        issues.append("QUEUE_PREVIEW_FAILED")
        summary = "QUEUE_PREVIEW_FAILED"
    elif runtime_write or execution_connected or send_order_connected:
        status = STATUS_NOT_READY
        eligible = False
        if runtime_write:
            issues.append("RUNTIME_WRITE_ENABLED")
        if execution_connected:
            issues.append("EXECUTION_CONNECTED")
        if send_order_connected:
            issues.append("SEND_ORDER_CONNECTED")
        summary = "PREVIEW_SAFETY_FLAGS_NOT_READY"
    else:
        status = STATUS_READY
        eligible = True
        summary = "READY_FOR_EXECUTION_PREVIEW"

    return {
        "ok": True,
        "stage": STAGE,
        "status": status,
        "eligible": eligible,
        "issues": issues,
        "warnings": _safety_warnings(
            runtime_write=runtime_write,
            execution_connected=execution_connected,
            send_order_connected=send_order_connected,
        ),
        "summary": summary,
        "gate": gate_value or None,
        "candidate_status": order_status or None,
        "preview_connected": preview_connected,
        "runtime_write": runtime_write,
        "execution_connected": execution_connected,
        "send_order_connected": send_order_connected,
        "gate_result": deepcopy(gate),
        "order_candidate": deepcopy(order),
        "queue_preview_result": deepcopy(queue_preview),
    }
