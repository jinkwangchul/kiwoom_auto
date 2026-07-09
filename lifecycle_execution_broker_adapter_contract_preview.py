# -*- coding: utf-8 -*-
"""Preview-only execution broker adapter contract preview.

This module converts an Execution Engine Preview result into the preview-only
Broker Adapter input contract. It is produced BEFORE any real Broker Adapter is
connected. It never connects a real broker, calls SendOrder, connects an order
router, starts execution, writes runtime files, modifies routines/*/rules.json,
writes SQLite, updates GUI state, or connects Chejan.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_BROKER_ADAPTER_CONTRACT_PREVIEW"
STATUS_READY = "BROKER_ADAPTER_CONTRACT_PREVIEW_READY"
STATUS_ENGINE_PREVIEW_READY = "EXECUTION_ENGINE_PREVIEW_READY"
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
    return status in (STATUS_READY, STATUS_ENGINE_PREVIEW_READY)


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
    "broker_connected",
    "broker_adapter_called",
    "send_order_available",
    "send_order_called",
    "order_routed",
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


def _build_broker_adapter_contract(engine_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    broker_adapter_preview = _as_dict(engine_preview.get("broker_adapter_preview"))
    name = _text(broker_adapter_preview.get("broker_adapter_name")) or _text(context.get("broker_adapter")) or "BROKER_ADAPTER"
    return {
        "adapter_id": _text(context.get("adapter_id")) or "BROKER_ADAPTER_CONTRACT_{}".format(uuid4().hex),
        "adapter_name": name,
        "adapter_version": _text(context.get("adapter_version")) or "v1",
        "adapter_planned": True,
        "adapter_called": False,
        "preview_only": True,
    }


def _build_broker_connection_preview(engine_preview: dict[str, Any]) -> dict[str, Any]:
    broker_adapter_preview = _as_dict(engine_preview.get("broker_adapter_preview"))
    return {
        "broker_name": _text(broker_adapter_preview.get("broker_adapter_name")) or "BROKER_ADAPTER",
        "connection_planned": True,
        "broker_connected": False,
        "connection_established": False,
        "preview_only": True,
    }


def _build_send_order_contract_preview(engine_preview: dict[str, Any]) -> dict[str, Any]:
    broker_adapter_preview = _as_dict(engine_preview.get("broker_adapter_preview"))
    return {
        "send_order_planned": True,
        "send_order_available": False,
        "send_order_called": False,
        "send_order_blocked": True,
        "broker_connected": False,
        "preview_only": True,
    }


def _build_order_route_candidate_preview(engine_preview: dict[str, Any]) -> dict[str, Any]:
    order_router_preview = _as_dict(engine_preview.get("order_router_preview"))
    router_name = _text(order_router_preview.get("order_router_name")) or "ORDER_ROUTER"
    candidates = [
        {
            "candidate_index": 1,
            "order_router_name": router_name,
            "route_selected": False,
            "order_routed": False,
            "preview_only": True,
        }
    ]
    return {
        "order_router_name": router_name,
        "order_route_candidates": candidates,
        "order_route_selected": False,
        "order_routed": False,
        "preview_only": True,
    }


def _build_adapter_safety_validation(engine_preview: dict[str, Any], status: str, warnings: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    for flag in SAFETY_FLAGS:
        if engine_preview.get(flag) is True:
            issues.append("engine preview {} must be false".format(flag))

    if engine_preview.get("preview_only") is not True:
        issues.append("engine preview preview_only must be true")

    final_engine_decision = _as_dict(engine_preview.get("final_engine_decision"))
    if final_engine_decision.get("approved") is not True:
        issues.append("engine preview final_engine_decision.approved must be true")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_adapter_decision(safety_validation: dict[str, Any], status: str) -> dict[str, Any]:
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or []) if not approved else "",
        "approval_reason": "broker adapter safety validation ready" if approved else "",
        "broker_connected": False,
        "broker_adapter_called": False,
        "send_order_available": False,
        "send_order_called": False,
        "order_routed": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "preview_only": True,
    }


def _validate_engine_preview(engine_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not engine_preview:
        return STATUS_INVALID, ["engine_preview must be a dict"]

    status = _text(engine_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["engine preview is BLOCKED"] + list(engine_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["engine preview is INVALID"] + list(engine_preview.get("issues") or [])
    if status != STATUS_ENGINE_PREVIEW_READY:
        return STATUS_INVALID, ["engine preview status is not EXECUTION_ENGINE_PREVIEW_READY"]

    if engine_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["engine preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if engine_preview.get(flag) is True:
            return STATUS_INVALID, ["engine preview {} must be false".format(flag)]

    final_engine_decision = _as_dict(engine_preview.get("final_engine_decision"))
    if final_engine_decision.get("approved") is not True:
        return STATUS_INVALID, ["engine preview final_engine_decision.approved must be true"]

    if not _as_dict(engine_preview.get("broker_adapter_preview")):
        return STATUS_INVALID, ["engine preview broker_adapter_preview is required"]
    if not _as_dict(engine_preview.get("order_router_preview")):
        return STATUS_INVALID, ["engine preview order_router_preview is required"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    broker_adapter_contract: dict[str, Any] | None = None,
    broker_connection_preview: dict[str, Any] | None = None,
    send_order_contract_preview: dict[str, Any] | None = None,
    order_route_candidate_preview: dict[str, Any] | None = None,
    adapter_safety_validation: dict[str, Any] | None = None,
    final_adapter_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "broker_connected": False,
        "broker_adapter_called": False,
        "send_order_available": False,
        "send_order_called": False,
        "order_routed": False,
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
        "broker_adapter_contract": deepcopy(broker_adapter_contract or {}),
        "broker_connection_preview": deepcopy(broker_connection_preview or {}),
        "send_order_contract_preview": deepcopy(send_order_contract_preview or {}),
        "order_route_candidate_preview": deepcopy(order_route_candidate_preview or {}),
        "adapter_safety_validation": deepcopy(adapter_safety_validation or {}),
        "final_adapter_decision": deepcopy(final_adapter_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_broker_adapter_contract_preview(
    engine_preview: Any,
    adapter_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only broker adapter contract from an execution engine preview."""
    preview = deepcopy(_as_dict(engine_preview))
    context = deepcopy(_as_dict(adapter_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    status, issues = _validate_engine_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_adapter_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            adapter_safety_validation=validation,
            final_adapter_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    broker_adapter_contract = _build_broker_adapter_contract(preview, context)
    broker_connection_preview = _build_broker_connection_preview(preview)
    send_order_contract_preview = _build_send_order_contract_preview(preview)
    order_route_candidate_preview = _build_order_route_candidate_preview(preview)
    safety_validation = _build_adapter_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_adapter_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        broker_adapter_contract=broker_adapter_contract,
        broker_connection_preview=broker_connection_preview,
        send_order_contract_preview=send_order_contract_preview,
        order_route_candidate_preview=order_route_candidate_preview,
        adapter_safety_validation=safety_validation,
        final_adapter_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
