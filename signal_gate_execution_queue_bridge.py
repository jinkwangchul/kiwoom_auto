# -*- coding: utf-8 -*-
"""Dry-run bridge from Signal Queue Gate to Execution Queue Writer preview.

This module only connects an OPEN gate and a REAL_READY order to the existing
preview-only execution queue writer path. It never enqueues, never writes
runtime/order_queue.json, and never calls SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from execution_preview_service import preview_execution_for_order
from execution_queue_writer import preview_execution_queue_write


STAGE = "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE"
BRIDGE_CONNECTED = "QUEUE_WRITER_PREVIEW_READY"
BRIDGE_BLOCKED = "BLOCKED"
BRIDGE_IGNORE = "IGNORE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _base_result(
    gate_preview: Any,
    order: Any,
    *,
    ok: bool,
    bridge_result: str,
    bridge_reason: str,
    queue_write_preview_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = _as_dict(gate_preview)
    order_dict = _as_dict(order)
    return {
        "ok": ok,
        "stage": STAGE,
        "bridge_result": bridge_result,
        "bridge_reason": bridge_reason,
        "gate_result": deepcopy(gate.get("gate_result")),
        "gate_stage": deepcopy(gate.get("stage")),
        "candidate_result": deepcopy(gate.get("candidate_result")),
        "signal": deepcopy(gate.get("signal")),
        "decision": deepcopy(gate.get("decision")),
        "policy_result": deepcopy(gate.get("policy_result")),
        "rule_source": deepcopy(gate.get("rule_source")),
        "matched_rule_paths": deepcopy(gate.get("matched_rule_paths", []))
        if isinstance(gate.get("matched_rule_paths", []), list)
        else [],
        "condition_summary": deepcopy(gate.get("condition_summary", []))
        if isinstance(gate.get("condition_summary", []), list)
        else [],
        "applied_policies": deepcopy(gate.get("applied_policies", []))
        if isinstance(gate.get("applied_policies", []), list)
        else [],
        "blocked_policy": deepcopy(gate.get("blocked_policy")),
        "signal_index": deepcopy(gate.get("signal_index")),
        "delay_bar": deepcopy(gate.get("delay_bar")),
        "order_id": deepcopy(order_dict.get("id") or order_dict.get("order_id")),
        "order_status": deepcopy(order_dict.get("status")),
        "queue_writer_preview_connected": queue_write_preview_result is not None,
        "queue_write_preview_result": deepcopy(queue_write_preview_result),
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


def _blocked(gate_preview: Any, order: Any, reason: str) -> dict[str, Any]:
    return _base_result(
        gate_preview,
        order,
        ok=False,
        bridge_result=BRIDGE_BLOCKED,
        bridge_reason=reason,
    )


def build_signal_gate_execution_queue_bridge(
    gate_preview: Any,
    real_ready_order: Any,
    *,
    guard: Any = None,
    existing_orders: Any = None,
    execution_preview_builder: Callable[[Any, Any], dict[str, Any]] = preview_execution_for_order,
) -> dict[str, Any]:
    """Build a dry-run bridge result into the existing queue writer preview."""
    gate = _as_dict(gate_preview)
    order = _as_dict(real_ready_order)

    if not gate:
        return _blocked(gate_preview, real_ready_order, "gate_preview must be dict")

    if gate.get("stage") != "SIGNAL_QUEUE_GATE":
        return _blocked(gate_preview, real_ready_order, "gate_preview.stage is invalid")

    gate_result = gate.get("gate_result")
    if gate_result == "IGNORE":
        return _base_result(
            gate_preview,
            real_ready_order,
            ok=True,
            bridge_result=BRIDGE_IGNORE,
            bridge_reason="gate result is IGNORE; queue writer preview not connected",
        )

    if gate_result != "OPEN":
        return _blocked(
            gate_preview,
            real_ready_order,
            f"gate_result is not OPEN: {_clean_text(gate_result) or 'EMPTY'}",
        )

    if not order:
        return _blocked(gate_preview, real_ready_order, "real_ready_order must be dict")

    if _norm(order.get("status")) != "REAL_READY":
        return _blocked(
            gate_preview,
            real_ready_order,
            "order.status is not REAL_READY",
        )

    execution_preview_result = execution_preview_builder(deepcopy(order), deepcopy(guard))
    if existing_orders is None:
        queue_write_preview_result = _as_dict(execution_preview_result.get("queue_write_preview_result"))
    else:
        queue_write_preview_result = preview_execution_queue_write(
            _as_dict(execution_preview_result.get("queue_pending_result")),
            deepcopy(existing_orders),
        )

    if queue_write_preview_result.get("write_preview") is not True:
        reasons = queue_write_preview_result.get("blocked_reasons")
        if isinstance(reasons, list) and reasons:
            reason = str(reasons[0])
        else:
            reason = "execution queue writer preview was not created"
        return _base_result(
            gate_preview,
            real_ready_order,
            ok=False,
            bridge_result=BRIDGE_BLOCKED,
            bridge_reason=reason,
            queue_write_preview_result=queue_write_preview_result,
        )

    return _base_result(
        gate_preview,
        real_ready_order,
        ok=True,
        bridge_result=BRIDGE_CONNECTED,
        bridge_reason="gate OPEN and REAL_READY order connected to queue writer preview",
        queue_write_preview_result=queue_write_preview_result,
    )


def preview_signal_gate_execution_queue_bridge(
    gate_preview: Any,
    real_ready_order: Any,
    *,
    guard: Any = None,
    existing_orders: Any = None,
) -> dict[str, Any]:
    """Alias for preview naming at call sites."""
    return build_signal_gate_execution_queue_bridge(
        gate_preview,
        real_ready_order,
        guard=guard,
        existing_orders=existing_orders,
    )
