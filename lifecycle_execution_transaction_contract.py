# -*- coding: utf-8 -*-
"""Preview-only execution transaction contract.

This module converts Runtime Execution Readiness Gate Preview into the first
Execution Layer input contract. It never starts execution, writes runtime
files, connects brokers or order routers, calls SendOrder, updates GUI state,
or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_TRANSACTION_CONTRACT"
STATUS_READY = "EXECUTION_TRANSACTION_CONTRACT_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

EXECUTION_READINESS_GATE_READY = "EXECUTION_READINESS_GATE_READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "validation_ready": status == STATUS_READY,
        "validation_items": [
            {
                "name": "readiness_gate_preview",
                "ok": status == STATUS_READY,
                "preview_only": True,
            }
        ],
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _execution_transaction_contract(status: str, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_id": _text(context.get("contract_id")) or "EXECUTION_TRANSACTION_CONTRACT_{}".format(uuid4().hex),
        "contract_version": _text(context.get("contract_version")) or "v1",
        "transaction_type": _text(context.get("transaction_type")) or "RUNTIME_TO_EXECUTION_TRANSACTION",
        "execution_mode": _text(context.get("execution_mode")) or "PREVIEW_ONLY",
        "status": status,
        "preview_only": True,
    }


def _payload_contract(name: str, gate: dict[str, Any], context_payload: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(context_payload) if context_payload else {
        "payload_type": name,
        "source_readiness_gate": deepcopy(_as_dict(gate.get("readiness_check_preview"))),
        "preview_only": True,
    }
    payload.setdefault("preview_only", True)
    return payload


def _execution_input_contract(gate: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_payload": _payload_contract("runtime_payload", gate, _as_dict(context.get("runtime_payload"))),
        "position_payload": _payload_contract("position_payload", gate, _as_dict(context.get("position_payload"))),
        "balance_payload": _payload_contract("balance_payload", gate, _as_dict(context.get("balance_payload"))),
        "audit_payload": _payload_contract("audit_payload", gate, _as_dict(context.get("audit_payload"))),
        "preview_only": True,
    }


def _execution_gate_contract() -> dict[str, Any]:
    return {
        "execution_gate_open": False,
        "operator_review_required": True,
        "execution_token_required": True,
        "approval_required": True,
        "preview_only": True,
    }


def _execution_route_contract(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_engine": {
            "name": _text(context.get("execution_engine")) or "LIFECYCLE_EXECUTION_ENGINE",
            "planned": True,
            "connected": False,
            "preview_only": True,
        },
        "broker_adapter": {
            "name": _text(context.get("broker_adapter")) or "BROKER_ADAPTER",
            "planned": True,
            "connected": False,
            "preview_only": True,
        },
        "order_router": {
            "name": _text(context.get("order_router")) or "ORDER_ROUTER",
            "planned": True,
            "connected": False,
            "preview_only": True,
        },
        "preview_only": True,
    }


def _final_execution_contract(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "approval_reason": "readiness gate is ready and execution transaction contract is valid" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    execution_transaction_contract: dict[str, Any],
    execution_input_contract: dict[str, Any],
    execution_gate_contract: dict[str, Any],
    execution_validation_contract: dict[str, Any],
    execution_route_contract: dict[str, Any],
    final_execution_contract: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "broker_connected": False,
        "order_router_connected": False,
        "backup_created": False,
        "rollback_executed": False,
        "execution_transaction_contract": deepcopy(execution_transaction_contract),
        "execution_input_contract": deepcopy(execution_input_contract),
        "execution_gate_contract": deepcopy(execution_gate_contract),
        "execution_validation_contract": deepcopy(execution_validation_contract),
        "execution_route_contract": deepcopy(execution_route_contract),
        "final_execution_contract": deepcopy(final_execution_contract),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_readiness_gate_preview(readiness_gate: dict[str, Any]) -> tuple[str, list[str]]:
    if not readiness_gate:
        return STATUS_INVALID, ["readiness_gate_preview must be a dict"]

    status = _text(readiness_gate.get("status")).upper()
    upstream_issues = list(readiness_gate.get("issues") or [])
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["readiness gate preview is BLOCKED"] + upstream_issues
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["readiness gate preview is INVALID"] + upstream_issues
    if status != EXECUTION_READINESS_GATE_READY:
        return STATUS_INVALID, ["readiness gate preview status is not EXECUTION_READINESS_GATE_READY"]

    if readiness_gate.get("preview_only") is not True:
        return STATUS_INVALID, ["readiness gate preview_only must be true"]
    for flag in (
        "execution_allowed",
        "execution_started",
        "runtime_write",
        "position_write",
        "balance_write",
        "audit_write",
        "file_write_called",
        "send_order_called",
        "chejan_called",
        "backup_created",
        "rollback_executed",
    ):
        if readiness_gate.get(flag) is not False:
            return STATUS_INVALID, ["readiness gate {} must be false".format(flag)]

    if not _as_dict(readiness_gate.get("readiness_check_preview")):
        return STATUS_INVALID, ["readiness_check_preview is required"]
    if not _as_dict(readiness_gate.get("execution_gate_preview")):
        return STATUS_INVALID, ["execution_gate_preview is required"]

    decision = _as_dict(readiness_gate.get("final_readiness_decision"))
    if decision.get("approved") is not True:
        return STATUS_BLOCKED, ["final_readiness_decision.approved must be true"]
    if decision.get("execution_allowed") is not False:
        return STATUS_INVALID, ["final_readiness_decision.execution_allowed must be false"]

    return STATUS_READY, []


def build_execution_transaction_contract(
    readiness_gate_preview: Any,
    execution_context: Any = None,
) -> dict[str, Any]:
    """Build the first preview-only Execution Layer transaction contract."""
    readiness_gate = deepcopy(_as_dict(readiness_gate_preview))
    context = deepcopy(_as_dict(execution_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(readiness_gate.get("warnings") or [])

    status, issues = _validate_readiness_gate_preview(readiness_gate)
    transaction = _execution_transaction_contract(status, context)
    input_contract = _execution_input_contract(readiness_gate, context)
    gate_contract = _execution_gate_contract()
    validation_contract = _validation(status, issues, warnings)
    route_contract = _execution_route_contract(context)
    final_contract = _final_execution_contract(status, issues)

    return _result(
        status=status,
        execution_transaction_contract=transaction,
        execution_input_contract=input_contract,
        execution_gate_contract=gate_contract,
        execution_validation_contract=validation_contract,
        execution_route_contract=route_contract,
        final_execution_contract=final_contract,
        issues=issues,
        warnings=warnings,
        now=now,
    )
