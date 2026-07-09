# -*- coding: utf-8 -*-
"""Preview-only execution sendorder call preview.

This module converts a SendOrder Contract Preview result into the
preview-only SendOrder Call Preview. It is produced BEFORE any real
SendOrder is called. It never calls SendOrder, connects a broker,
starts execution, writes runtime files, modifies routines/*/rules.json,
writes SQLite, updates GUI state, or connects Chejan.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_SENDORDER_CALL_PREVIEW"
STATUS_READY = "SENDORDER_CALL_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
SENDORDER_CONTRACT_STATUS_READY = "SENDORDER_CONTRACT_PREVIEW_READY"


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
    return status == STATUS_READY


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
    "send_order_called",
    "send_order_available",
    "broker_connected",
    "broker_api_called",
    "order_router_connected",
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


def _build_sendorder_call_preview(
    sendorder_contract_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only sendorder call preview."""
    sendorder_contract = _as_dict(sendorder_contract_preview.get("sendorder_contract"))
    sendorder_payload_preview = _as_dict(sendorder_contract_preview.get("sendorder_payload_preview"))
    broker_api_preview = _as_dict(sendorder_contract_preview.get("broker_api_preview"))

    call_id = _text(context.get("call_id")) or "SENDORDER_CALL_{}".format(uuid4().hex)
    account = _text(sendorder_payload_preview.get("account")) or _text(context.get("account")) or "PREVIEW_ACCOUNT"
    stock_code = _text(sendorder_payload_preview.get("stock_code")) or _text(context.get("stock_code")) or "PREVIEW_STOCK"
    order_type = _text(sendorder_payload_preview.get("order_type")) or _text(context.get("order_type")) or "PREVIEW_ORDER_TYPE"
    price = _text(sendorder_payload_preview.get("price")) or _text(context.get("price")) or "0"
    quantity = _text(sendorder_payload_preview.get("quantity")) or _text(context.get("quantity")) or "0"

    return {
        "call_id": call_id,
        "sendorder_id": _text(sendorder_contract.get("sendorder_id")) or "",
        "broker_adapter_name": _text(sendorder_contract.get("broker_adapter_name")) or "BROKER_ADAPTER",
        "broker_adapter_version": _text(sendorder_contract.get("broker_adapter_version")) or "v1",
        "account": account,
        "stock_code": stock_code,
        "order_type": order_type,
        "price": price,
        "quantity": quantity,
        "call_planned": True,
        "call_executed": False,
        "preview_only": True,
    }


def _build_sendorder_parameter_preview(
    sendorder_contract_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only sendorder parameter preview."""
    sendorder_payload_preview = _as_dict(sendorder_contract_preview.get("sendorder_payload_preview"))
    broker_api_preview = _as_dict(sendorder_contract_preview.get("broker_api_preview"))

    parameters: dict[str, Any] = {
        "account": _text(sendorder_payload_preview.get("account")) or _text(context.get("account")) or "PREVIEW_ACCOUNT",
        "stock_code": _text(sendorder_payload_preview.get("stock_code")) or _text(context.get("stock_code")) or "PREVIEW_STOCK",
        "order_type": _text(sendorder_payload_preview.get("order_type")) or _text(context.get("order_type")) or "PREVIEW_ORDER_TYPE",
        "price": _text(sendorder_payload_preview.get("price")) or _text(context.get("price")) or "0",
        "quantity": _text(sendorder_payload_preview.get("quantity")) or _text(context.get("quantity")) or "0",
        "original_order_id": _text(context.get("original_order_id")) or "",
        "send_order_available": False,
        "send_order_called": False,
        "broker_api_called": False,
        "broker_connected": False,
        "preview_only": True,
    }

    broker_api_parameters: dict[str, Any] = {
        "api_id": _text(broker_api_preview.get("api_id")) or "BROKER_API_{}".format(uuid4().hex),
        "broker_adapter": _text(broker_api_preview.get("broker_adapter")) or "BROKER_ADAPTER",
        "api_version": _text(broker_api_preview.get("api_version")) or "v1",
        "parameters": deepcopy(parameters),
        "parameter_valid": True,
        "preview_only": True,
    }

    return {
        "parameter_set_id": _text(context.get("parameter_set_id")) or "PARAMETER_SET_{}".format(uuid4().hex),
        "parameters": deepcopy(parameters),
        "broker_api_parameters": deepcopy(broker_api_parameters),
        "parameter_validation": {
            "valid": True,
            "issues": [],
            "warnings": [],
            "preview_only": True,
        },
        "preview_only": True,
    }


def _build_sendorder_call_sequence_preview(
    sendorder_contract_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only sendorder call sequence preview."""
    sequence_id = _text(context.get("sequence_id")) or "CALL_SEQUENCE_{}".format(uuid4().hex)

    steps = [
        {
            "step_index": 1,
            "step_name": "validate_call_preview",
            "step_description": "Validate sendorder call preview before calling SendOrder",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
        {
            "step_index": 2,
            "step_name": "prepare_sendorder_parameters",
            "step_description": "Prepare SendOrder parameters from sendorder parameter preview",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
        {
            "step_index": 3,
            "step_name": "call_send_order",
            "step_description": "Call Kiwoom SendOrder API (preview only, never executed)",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
        {
            "step_index": 4,
            "step_name": "verify_send_order_result",
            "step_description": "Verify SendOrder result (preview only)",
            "step_required": True,
            "step_completed": False,
            "preview_only": True,
        },
    ]

    return {
        "sequence_id": sequence_id,
        "sequence_name": "SENDORDER_CALL_SEQUENCE",
        "sequence_planned": True,
        "sequence_executed": False,
        "steps": steps,
        "current_step": 0,
        "total_steps": len(steps),
        "sequence_completed": False,
        "preview_only": True,
    }


def _build_sendorder_result_candidate_preview(
    sendorder_contract_preview: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the preview-only sendorder result candidate preview."""
    candidates = [
        {
            "candidate_index": 1,
            "result_name": "SENDORDER_SUCCESS",
            "result_description": "SendOrder call succeeds (preview only)",
            "result_status": "SENDORDER_CALL_SUCCESS",
            "order_accepted": True,
            "order_rejected": False,
            "error_code": "",
            "error_message": "",
            "preview_only": True,
        },
        {
            "candidate_index": 2,
            "result_name": "SENDORDER_REJECTED",
            "result_description": "SendOrder call rejected by broker (preview only)",
            "result_status": "SENDORDER_CALL_REJECTED",
            "order_accepted": False,
            "order_rejected": True,
            "error_code": "REJECTED",
            "error_message": "Order rejected by broker",
            "preview_only": True,
        },
        {
            "candidate_index": 3,
            "result_name": "SENDORDER_FAILED",
            "result_description": "SendOrder call failed (preview only)",
            "result_status": "SENDORDER_CALL_FAILED",
            "order_accepted": False,
            "order_rejected": True,
            "error_code": "FAILED",
            "error_message": "SendOrder call failed",
            "preview_only": True,
        },
    ]

    return {
        "result_candidate_set_id": _text(context.get("result_candidate_set_id")) or "RESULT_CANDIDATE_SET_{}".format(uuid4().hex),
        "candidates": candidates,
        "selected_candidate_index": 0,
        "result_selected": False,
        "preview_only": True,
    }


def _build_call_safety_validation(
    sendorder_contract_preview: dict[str, Any],
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Validate SendOrder call safety requirements."""
    issues: list[str] = []

    for flag in SAFETY_FLAGS:
        if sendorder_contract_preview.get(flag) is True:
            issues.append("sendorder contract preview {} must be false".format(flag))

    if sendorder_contract_preview.get("preview_only") is not True:
        issues.append("sendorder contract preview preview_only must be true")

    final_sendorder_decision = _as_dict(sendorder_contract_preview.get("final_sendorder_decision"))
    if final_sendorder_decision.get("approved") is not True:
        issues.append("sendorder contract preview final_sendorder_decision.approved must be true")

    if not _as_dict(sendorder_contract_preview.get("sendorder_contract")):
        issues.append("sendorder contract preview sendorder_contract is required")

    if not _as_dict(sendorder_contract_preview.get("sendorder_payload_preview")):
        issues.append("sendorder contract preview sendorder_payload_preview is required")

    ready = status == STATUS_READY and not issues
    return {
        "ready": ready,
        "issues": issues,
        "warnings": list(warnings),
        "preview_only": True,
    }


def _build_final_call_decision(
    safety_validation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the final call decision."""
    approved = safety_validation.get("ready") is True and status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "rejection_reason": "; ".join(safety_validation.get("issues") or [])
        if not approved
        else "",
        "approval_reason": "sendorder call safety validation ready" if approved else "",
        "send_order_allowed": False,
        "send_order_available": False,
        "send_order_called": False,
        "broker_connected": False,
        "broker_api_called": False,
        "order_router_connected": False,
        "order_routed": False,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "preview_only": True,
    }


def _validate_sendorder_contract_preview(
    sendorder_contract_preview: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate the sendorder contract preview."""
    if not sendorder_contract_preview:
        return STATUS_INVALID, ["sendorder_contract_preview must be a dict"]

    status = _text(sendorder_contract_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["sendorder contract preview is BLOCKED"] + list(
            sendorder_contract_preview.get("issues") or []
        )
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["sendorder contract preview is INVALID"] + list(
            sendorder_contract_preview.get("issues") or []
        )
    if status != SENDORDER_CONTRACT_STATUS_READY:
        return STATUS_INVALID, [
            "sendorder contract preview status is not SENDORDER_CONTRACT_PREVIEW_READY"
        ]

    if sendorder_contract_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["sendorder contract preview preview_only must be true"]

    for flag in SAFETY_FLAGS:
        if sendorder_contract_preview.get(flag) is True:
            return STATUS_INVALID, ["sendorder contract preview {} must be false".format(flag)]

    final_sendorder_decision = _as_dict(sendorder_contract_preview.get("final_sendorder_decision"))
    if final_sendorder_decision.get("approved") is not True:
        return STATUS_INVALID, ["sendorder contract preview final_sendorder_decision.approved must be true"]

    if not _as_dict(sendorder_contract_preview.get("sendorder_contract")):
        return STATUS_INVALID, ["sendorder contract preview sendorder_contract is required"]

    if not _as_dict(sendorder_contract_preview.get("sendorder_payload_preview")):
        return STATUS_INVALID, ["sendorder contract preview sendorder_payload_preview is required"]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    sendorder_call_preview: dict[str, Any] | None = None,
    sendorder_parameter_preview: dict[str, Any] | None = None,
    sendorder_call_sequence_preview: dict[str, Any] | None = None,
    sendorder_result_candidate_preview: dict[str, Any] | None = None,
    call_safety_validation: dict[str, Any] | None = None,
    final_call_decision: dict[str, Any] | None = None,
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
        "broker_api_called": False,
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
        "sendorder_call_preview": deepcopy(sendorder_call_preview or {}),
        "sendorder_parameter_preview": deepcopy(sendorder_parameter_preview or {}),
        "sendorder_call_sequence_preview": deepcopy(sendorder_call_sequence_preview or {}),
        "sendorder_result_candidate_preview": deepcopy(sendorder_result_candidate_preview or {}),
        "call_safety_validation": deepcopy(call_safety_validation or {}),
        "final_call_decision": deepcopy(final_call_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_sendorder_call_preview(
    sendorder_contract_preview: Any,
    call_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only SendOrder call preview from a sendorder contract preview.

    The pipeline is:
    1. SENDORDER_CONTRACT_PREVIEW_READY (from sendorder contract preview)
    2. SENDORDER_CALL_PREVIEW_READY (this function)
    3. BLOCKED / INVALID states propagate

    All safety flags are enforced to be False and preview_only is enforced to True.
    """
    preview = deepcopy(_as_dict(sendorder_contract_preview))
    context = deepcopy(_as_dict(call_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(preview.get("warnings") or [])

    # Validate the sendorder contract preview
    status, issues = _validate_sendorder_contract_preview(preview)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _build_final_call_decision({"ready": False, "issues": issues}, status)
        return _result(
            status=status,
            call_safety_validation=validation,
            final_call_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    # Build all preview components
    sendorder_call_preview = _build_sendorder_call_preview(preview, context)
    sendorder_parameter_preview = _build_sendorder_parameter_preview(preview, context)
    sendorder_call_sequence_preview = _build_sendorder_call_sequence_preview(preview, context)
    sendorder_result_candidate_preview = _build_sendorder_result_candidate_preview(preview, context)
    safety_validation = _build_call_safety_validation(preview, STATUS_READY, warnings)
    decision = _build_final_call_decision(safety_validation, STATUS_READY)

    return _result(
        status=STATUS_READY,
        sendorder_call_preview=sendorder_call_preview,
        sendorder_parameter_preview=sendorder_parameter_preview,
        sendorder_call_sequence_preview=sendorder_call_sequence_preview,
        sendorder_result_candidate_preview=sendorder_result_candidate_preview,
        call_safety_validation=safety_validation,
        final_call_decision=decision,
        issues=safety_validation["issues"],
        warnings=warnings,
        now=now,
    )
