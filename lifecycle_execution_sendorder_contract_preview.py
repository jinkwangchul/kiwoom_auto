# -*- coding: utf-8 -*-
"""Preview-only execution sendorder contract preview.

This module converts an Order Router Contract Preview result into the
preview-only SendOrder input contract. It is produced BEFORE any real
SendOrder is called. It never connects a real broker, calls SendOrder,
connects an order router, starts execution, writes runtime files,
modifies routines/*/rules.json, writes SQLite, updates GUI state, or
connects Chejan.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_SENDORDER_CONTRACT_PREVIEW"
STATUS_READY = "SENDORDER_CONTRACT_PREVIEW_READY"
STATUS_ROUTER_PREVIEW_READY = "ORDER_ROUTER_CONTRACT_PREVIEW_READY"
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
    return status in (STATUS_READY, STATUS_ROUTER_PREVIEW_READY)


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
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


def _build_sendorder_contract(
    router_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only SendOrder contract from order router preview."""
    order_router_contract = _as_dict(router_preview.get("order_router_contract"))
    name = (
        _text(context.get("broker_adapter"))
        or _text(order_router_contract.get("router_name"))
        or "BROKER_ADAPTER"
    )
    return {
        "sendorder_id": _text(context.get("sendorder_id")) or "SENDORDER_CONTRACT_{}".format(
            uuid4().hex
        ),
        "broker_adapter_name": name,
        "broker_adapter_version": _text(context.get("adapter_version")) or "v1",
        "preview_only": True,
    }


def _build_sendorder_payload_preview(
    router_preview: dict[str, Any],
    order_router_contract: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only SendOrder payload."""
    return {
        "payload_id": _text(context.get("payload_id")) or "SENDORDER_PAYLOAD_{}".format(
            uuid4().hex
        ),
        "broker_adapter": _text(order_router_contract.get("router_name")) or "BROKER_ADAPTER",
        "order_router": _text(order_router_contract.get("router_name")) or "ORDER_ROUTER",
        "order_type": _text(context.get("order_type")) or "",
        "price": _text(context.get("price")),
        "quantity": _text(context.get("quantity")),
        "account": _text(context.get("account")),
        "stock_code": _text(context.get("stock_code")),
        "preview_only": True,
    }


def _build_broker_api_preview(
    router_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only Broker API contract."""
    order_router_contract = _as_dict(router_preview.get("order_router_contract"))
    return {
        "api_id": _text(context.get("api_id")) or "BROKER_API_{}".format(uuid4().hex),
        "broker_adapter": _text(order_router_contract.get("router_name")) or "BROKER_ADAPTER",
        "api_version": _text(context.get("api_version")) or "v1",
        "preview_only": True,
    }


def _build_sendorder_safety_validation(
    router_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate SendOrder safety requirements."""
    issues: list[str] = []
    for flag in SAFETY_FLAGS:
        if router_preview.get(flag) is True:
            issues.append("router preview {} must be false".format(flag))

    if router_preview.get("preview_only") is not True:
        issues.append("router preview preview_only must be true")

    final_router_decision = _as_dict(router_preview.get("final_router_decision"))
    if final_router_decision.get("approved") is not True:
        issues.append("router preview final_router_decision.approved must be true")

    if not _as_dict(router_preview.get("order_router_contract")):
        issues.append("router preview order_router_contract is required")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_sendorder_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final SendOrder decision."""
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not approved
        else "",
        "approval_reason": "sendorder safety validation ready" if approved else "",
        "send_order_allowed": False,
        "send_order_available": False,
        "send_order_called": False,
        "broker_connected": False,
        "broker_adapter_called": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "preview_only": True,
    }


def _validate_router_preview(router_preview: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate the order router preview."""
    if not router_preview:
        return STATUS_INVALID, ["router_preview must be a dict"]

    status = _text(router_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["router preview is BLOCKED"] + list(
            router_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["router preview is INVALID"] + list(
            router_preview.get("issues") or []
        )
    if status != STATUS_ROUTER_PREVIEW_READY:
        return STATUS_INVALID, [
            "router preview status is not ORDER_ROUTER_CONTRACT_PREVIEW_READY"
        ]

    if router_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["router preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if router_preview.get(flag) is True:
            return STATUS_INVALID, ["router preview {} must be false".format(flag)]

    final_router_decision = _as_dict(router_preview.get("final_router_decision"))
    if final_router_decision.get("approved") is not True:
        return STATUS_INVALID, ["router preview final_router_decision.approved must be true"]

    if not _as_dict(router_preview.get("order_router_contract")):
        return STATUS_INVALID, ["router preview order_router_contract is required"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    sendorder_contract: dict[str, Any] | None = None,
    sendorder_payload_preview: dict[str, Any] | None = None,
    broker_api_preview: dict[str, Any] | None = None,
    sendorder_safety_validation: dict[str, Any] | None = None,
    final_sendorder_decision: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "send_order_available": False,
        "send_order_called": False,
        "broker_connected": False,
        "broker_adapter_called": False,
        "order_router_connected": False,
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
        "sendorder_contract": deepcopy(sendorder_contract or {}),
        "sendorder_payload_preview": deepcopy(sendorder_payload_preview or {}),
        "broker_api_preview": deepcopy(broker_api_preview or {}),
        "sendorder_safety_validation": deepcopy(sendorder_safety_validation or {}),
        "final_sendorder_decision": deepcopy(final_sendorder_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_sendorder_contract_preview(
    router_contract_preview: Any,
    sendorder_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only SendOrder contract from an order router contract preview.

    The pipeline is:
    1. ORDER_ROUTER_CONTRACT_PREVIEW_READY (from order router)
    2. SENDORDER_CONTRACT_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(router_contract_preview))
    context = deepcopy(_as_dict(sendorder_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the order router preview
    status, issues = _validate_router_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_sendorder_decision(
            {"ready": False, "issues": issues}, status
        )
        return _result(
            status=status,
            sendorder_safety_validation=validation,
            final_sendorder_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    order_router_contract = _as_dict(preview.get("order_router_contract"))
    sendorder_contract = _build_sendorder_contract(preview, context)
    sendorder_payload_preview = _build_sendorder_payload_preview(
        preview,
        order_router_contract,
        context,
    )
    broker_api_preview = _build_broker_api_preview(preview, context)
    safety_validation = _build_sendorder_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_sendorder_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        sendorder_contract=sendorder_contract,
        sendorder_payload_preview=sendorder_payload_preview,
        broker_api_preview=broker_api_preview,
        sendorder_safety_validation=safety_validation,
        final_sendorder_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
