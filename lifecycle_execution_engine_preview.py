# -*- coding: utf-8 -*-
"""Preview-only execution engine plan before real execution.

This module converts an Execution Transaction Contract into the final
preview-only Execution Engine plan. It never starts execution, writes runtime
files, connects brokers or order routers, calls SendOrder, updates GUI state,
or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_ENGINE_PREVIEW"
STATUS_READY = "READY"
STATUS_ENGINE_PREVIEW_READY = "EXECUTION_ENGINE_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

EXECUTION_TRANSACTION_CONTRACT_READY = "EXECUTION_TRANSACTION_CONTRACT_READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _is_ready(status: str) -> bool:
    return status in (STATUS_READY, STATUS_ENGINE_PREVIEW_READY, EXECUTION_TRANSACTION_CONTRACT_READY)


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": _is_ready(status),
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


SAFETY_FLAGS = (
    "execution_allowed",
    "execution_started",
    "execution_completed",
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "send_order_called",
    "chejan_called",
    "backup_created",
    "rollback_executed",
    "broker_connected",
    "order_router_connected",
    "order_routed",
)


def _build_engine_plan(contract: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    transaction = _as_dict(contract.get("execution_transaction_contract"))
    route = _as_dict(contract.get("execution_route_contract"))
    engine = _as_dict(route.get("execution_engine"))

    engine_type = _text(engine.get("name")) or _text(context.get("execution_engine")) or "LIFECYCLE_EXECUTION_ENGINE"
    execution_mode = _text(transaction.get("execution_mode")) or "PREVIEW_ONLY"

    planned_steps = [
        {"step_index": 1, "action": "OPEN_EXECUTION_GATE", "target": "execution_engine", "preview_only": True},
        {"step_index": 2, "action": "PREFLIGHT_CHECK", "target": "execution_engine", "preview_only": True},
        {"step_index": 3, "action": "PLAN_BROKER_ADAPTER", "target": "broker_adapter", "preview_only": True},
        {"step_index": 4, "action": "PLAN_ORDER_ROUTER", "target": "order_router", "preview_only": True},
        {"step_index": 5, "action": "SAFETY_REVIEW", "target": "execution_engine", "preview_only": True},
        {"step_index": 6, "action": "FINAL_DECISION", "target": "execution_engine", "preview_only": True},
    ]

    return {
        "engine_type": engine_type,
        "execution_mode": execution_mode,
        "planned_steps": planned_steps,
        "preview_only": True,
    }


def _build_preflight_preview() -> dict[str, Any]:
    items = [
        {"name": "contract_ready", "ok": True, "preview_only": True},
        {"name": "safety_flags_valid", "ok": True, "preview_only": True},
        {"name": "broker_adapter_planned", "ok": True, "preview_only": True},
        {"name": "order_router_planned", "ok": True, "preview_only": True},
    ]
    return {
        "preflight_required": True,
        "preflight_executed": False,
        "preflight_items": items,
        "preview_only": True,
    }


def _build_broker_adapter_preview(route: dict[str, Any]) -> dict[str, Any]:
    broker = _as_dict(route.get("broker_adapter"))
    name = _text(broker.get("name")) or "BROKER_ADAPTER"
    return {
        "broker_adapter_name": name,
        "broker_adapter_planned": True,
        "broker_connected": False,
        "send_order_available": False,
        "preview_only": True,
    }


def _build_order_router_preview(route: dict[str, Any]) -> dict[str, Any]:
    router = _as_dict(route.get("order_router"))
    name = _text(router.get("name")) or "ORDER_ROUTER"
    return {
        "order_router_name": name,
        "order_router_planned": True,
        "order_router_connected": False,
        "order_routed": False,
        "preview_only": True,
    }


def _build_safety_review(contract: dict[str, Any], status: str, warnings: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    for flag in SAFETY_FLAGS:
        if contract.get(flag) is True:
            issues.append("contract {} must be false".format(flag))

    final_contract = _as_dict(contract.get("final_execution_contract"))
    if final_contract.get("approved") is not True:
        issues.append("final_execution_contract.approved must be true")

    ready = status == STATUS_ENGINE_PREVIEW_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_decision(safety_review: dict[str, Any], status: str) -> dict[str, Any]:
    approved = safety_review.get("ready") is True and status == STATUS_ENGINE_PREVIEW_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_review.get("issues") or []) if not approved else "",
        "approval_reason": "execution safety review ready" if approved else "",
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "preview_only": True,
    }


def _validate_execution_contract(contract: dict[str, Any]) -> tuple[str, list[str]]:
    if not contract:
        return STATUS_INVALID, ["execution_contract must be a dict"]

    status = _text(contract.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["execution contract is BLOCKED"] + list(contract.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["execution contract is INVALID"] + list(contract.get("issues") or [])
    if status not in (STATUS_READY, STATUS_ENGINE_PREVIEW_READY, EXECUTION_TRANSACTION_CONTRACT_READY):
        return STATUS_INVALID, ["execution contract status is not EXECUTION_TRANSACTION_CONTRACT_READY"]

    if contract.get("preview_only") is not True:
        return STATUS_INVALID, ["execution contract preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if contract.get(flag) is True:
            return STATUS_INVALID, ["execution contract {} must be false".format(flag)]

    transaction = _as_dict(contract.get("execution_transaction_contract"))
    if transaction.get("status") != EXECUTION_TRANSACTION_CONTRACT_READY:
        return STATUS_INVALID, ["execution_transaction_contract.status must be EXECUTION_TRANSACTION_CONTRACT_READY"]

    route = _as_dict(contract.get("execution_route_contract"))
    if not route:
        return STATUS_INVALID, ["execution_route_contract is required"]

    return STATUS_ENGINE_PREVIEW_READY, []


def _result(
    *,
    status: str,
    execution_engine_plan: dict[str, Any] | None = None,
    execution_preflight_preview: dict[str, Any] | None = None,
    broker_adapter_preview: dict[str, Any] | None = None,
    order_router_preview: dict[str, Any] | None = None,
    execution_safety_review: dict[str, Any] | None = None,
    final_engine_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "broker_connected": False,
        "order_router_connected": False,
        "order_routed": False,
        "send_order_called": False,
        "chejan_called": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "execution_engine_plan": deepcopy(execution_engine_plan or {}),
        "execution_preflight_preview": deepcopy(execution_preflight_preview or {}),
        "broker_adapter_preview": deepcopy(broker_adapter_preview or {}),
        "order_router_preview": deepcopy(order_router_preview or {}),
        "execution_safety_review": deepcopy(execution_safety_review or {}),
        "final_engine_decision": deepcopy(final_engine_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_engine_preview(
    execution_contract: Any,
    engine_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only execution engine plan from an execution contract."""
    contract = _as_dict(execution_contract)
    context = deepcopy(_as_dict(engine_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(contract.get("warnings") or [])

    status, issues = _validate_execution_contract(contract)

    if status != STATUS_ENGINE_PREVIEW_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            execution_safety_review=validation,
            final_engine_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    engine_plan = _build_engine_plan(contract, context)
    preflight = _build_preflight_preview()
    route = _as_dict(contract.get("execution_route_contract"))
    broker_adapter = _build_broker_adapter_preview(route)
    order_router = _build_order_router_preview(route)
    safety_review = _build_safety_review(contract, STATUS_ENGINE_PREVIEW_READY, warnings)
    decision = _build_final_decision(safety_review, STATUS_ENGINE_PREVIEW_READY)

    return _result(
        status=STATUS_ENGINE_PREVIEW_READY,
        execution_engine_plan=engine_plan,
        execution_preflight_preview=preflight,
        broker_adapter_preview=broker_adapter,
        order_router_preview=order_router,
        execution_safety_review=safety_review,
        final_engine_decision=decision,
        issues=safety_review["issues"],
        warnings=warnings,
        now=now,
    )