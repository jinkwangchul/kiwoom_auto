# -*- coding: utf-8 -*-
"""Preview-only execution order router contract preview.

This module converts a Broker Adapter Contract Preview result into the
preview-only Order Router input contract. It is produced BEFORE any real Order
Router is connected. It never connects a real order router, routes orders,
calls SendOrder, connects a broker, starts execution, writes runtime files,
modifies routines/*/rules.json, writes SQLite, updates GUI state, or connects
Chejan.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_ORDER_ROUTER_CONTRACT_PREVIEW"
STATUS_READY = "ORDER_ROUTER_CONTRACT_PREVIEW_READY"
STATUS_ADAPTER_PREVIEW_READY = "BROKER_ADAPTER_CONTRACT_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


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
    return status in (STATUS_READY, STATUS_ADAPTER_PREVIEW_READY)


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
    "order_router_connected",
    "order_routed",
    "send_order_available",
    "send_order_called",
    "broker_connected",
    "broker_adapter_called",
    "execution_allowed",
    "execution_started",
    "execution_completed",
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "chejan_called",
    "gui_update_called",
    "backup_created",
    "rollback_executed",
)


def _build_order_router_contract(adapter_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    order_route_candidate_preview = _as_dict(adapter_preview.get("order_route_candidate_preview"))
    name = _text(order_route_candidate_preview.get("order_router_name")) or _text(context.get("order_router")) or "ORDER_ROUTER"
    return {
        "router_id": _text(context.get("router_id")) or "ORDER_ROUTER_CONTRACT_{}".format(uuid4().hex),
        "router_name": name,
        "router_version": _text(context.get("router_version")) or "v1",
        "router_planned": True,
        "router_connected": False,
        "preview_only": True,
    }


def _build_routing_candidate_preview(adapter_preview: dict[str, Any]) -> dict[str, Any]:
    order_route_candidate_preview = _as_dict(adapter_preview.get("order_route_candidate_preview"))
    router_name = _text(order_route_candidate_preview.get("order_router_name")) or "ORDER_ROUTER"
    candidates = [
        {
            "candidate_index": 1,
            "order_router_name": router_name,
            "routing_selected": False,
            "order_routed": False,
            "preview_only": True,
        }
    ]
    return {
        "order_router_name": router_name,
        "routing_candidates": candidates,
        "routing_selected": False,
        "order_routed": False,
        "preview_only": True,
    }


def _build_send_order_route_preview(adapter_preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "send_order_route_planned": True,
        "send_order_available": False,
        "send_order_called": False,
        "order_router_connected": False,
        "broker_connected": False,
        "preview_only": True,
    }


def _build_route_safety_validation(adapter_preview: dict[str, Any], status: str, warnings: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    for flag in SAFETY_FLAGS:
        if adapter_preview.get(flag) is True:
            issues.append("adapter preview {} must be false".format(flag))

    if adapter_preview.get("preview_only") is not True:
        issues.append("adapter preview preview_only must be true")

    final_adapter_decision = _as_dict(adapter_preview.get("final_adapter_decision"))
    if final_adapter_decision.get("approved") is not True:
        issues.append("adapter preview final_adapter_decision.approved must be true")

    if not _as_dict(adapter_preview.get("order_route_candidate_preview")):
        issues.append("adapter preview order_route_candidate_preview is required")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_router_decision(safety_validation: dict[str, Any], status: str) -> dict[str, Any]:
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or []) if not approved else "",
        "approval_reason": "order router safety validation ready" if approved else "",
        "order_router_connected": False,
        "order_routed": False,
        "send_order_available": False,
        "send_order_called": False,
        "broker_connected": False,
        "broker_adapter_called": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "preview_only": True,
    }


def _validate_adapter_preview(adapter_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not adapter_preview:
        return STATUS_INVALID, ["adapter_preview must be a dict"]

    status = _text(adapter_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["adapter preview is BLOCKED"] + list(adapter_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["adapter preview is INVALID"] + list(adapter_preview.get("issues") or [])
    if status != STATUS_ADAPTER_PREVIEW_READY:
        return STATUS_INVALID, ["adapter preview status is not BROKER_ADAPTER_CONTRACT_PREVIEW_READY"]

    if adapter_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["adapter preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if adapter_preview.get(flag) is True:
            return STATUS_INVALID, ["adapter preview {} must be false".format(flag)]

    final_adapter_decision = _as_dict(adapter_preview.get("final_adapter_decision"))
    if final_adapter_decision.get("approved") is not True:
        return STATUS_INVALID, ["adapter preview final_adapter_decision.approved must be true"]

    if not _as_dict(adapter_preview.get("order_route_candidate_preview")):
        return STATUS_INVALID, ["adapter preview order_route_candidate_preview is required"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    order_router_contract: dict[str, Any] | None = None,
    routing_candidate_preview: dict[str, Any] | None = None,
    send_order_route_preview: dict[str, Any] | None = None,
    route_safety_validation: dict[str, Any] | None = None,
    final_router_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "order_router_connected": False,
        "order_routed": False,
        "send_order_available": False,
        "send_order_called": False,
        "broker_connected": False,
        "broker_adapter_called": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "chejan_called": False,
        "gui_update_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "order_router_contract": deepcopy(order_router_contract or {}),
        "routing_candidate_preview": deepcopy(routing_candidate_preview or {}),
        "send_order_route_preview": deepcopy(send_order_route_preview or {}),
        "route_safety_validation": deepcopy(route_safety_validation or {}),
        "final_router_decision": deepcopy(final_router_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_order_router_contract_preview(
    adapter_contract_preview: Any,
    router_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only order router contract from a broker adapter contract preview."""
    preview = deepcopy(_as_dict(adapter_contract_preview))
    context = deepcopy(_as_dict(router_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    status, issues = _validate_adapter_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_router_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            route_safety_validation=validation,
            final_router_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    order_router_contract = _build_order_router_contract(preview, context)
    routing_candidate_preview = _build_routing_candidate_preview(preview)
    send_order_route_preview = _build_send_order_route_preview(preview)
    safety_validation = _build_route_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_router_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        order_router_contract=order_router_contract,
        routing_candidate_preview=routing_candidate_preview,
        send_order_route_preview=send_order_route_preview,
        route_safety_validation=safety_validation,
        final_router_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
