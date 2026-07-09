# -*- coding: utf-8 -*-
"""Preview-safe Runtime Apply Engine entry layer.

This module consumes Runtime Apply Engine Contract and builds the execution
plans that a future real engine would use. It never writes runtime files,
writes SQLite, updates GUI state, calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_APPLY_ENGINE"
STATUS_ENGINE_READY = "ENGINE_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _engine_validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_ENGINE_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _empty_plan(plan_type: str, reason: str) -> dict[str, Any]:
    return {
        "plan_type": plan_type,
        "status": STATUS_BLOCKED,
        "ready": False,
        "blocked_reason": reason,
        "preview_only": True,
        "executed": False,
    }


def _engine_execution_plan(
    *,
    status: str,
    contract: dict[str, Any],
    engine_input: dict[str, Any],
    context: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    if status != STATUS_ENGINE_READY:
        return _empty_plan("ENGINE_EXECUTION_PLAN", "; ".join(issues) or "engine is not ready")

    return {
        "plan_type": "ENGINE_EXECUTION_PLAN",
        "status": STATUS_ENGINE_READY,
        "ready": True,
        "engine_run_id": _text(context.get("engine_run_id")) or "RUNTIME_APPLY_ENGINE_RUN_{}".format(uuid4().hex),
        "source_contract_id": _text(contract.get("contract_id")),
        "source_orchestrator_id": _text(contract.get("source_orchestrator_id")),
        "execution_steps": [
            "validate_contract",
            "prepare_transaction",
            "prepare_file_write_preview",
            "verify_preview_only_boundary",
            "await_real_engine_implementation",
        ],
        "engine_input_contract": deepcopy(engine_input),
        "preview_only": True,
        "engine_executed": False,
    }


def _transaction_plan_preview(status: str, execution_plan: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    if status != STATUS_ENGINE_READY:
        return _empty_plan("TRANSACTION_PLAN_PREVIEW", "; ".join(issues) or "engine is not ready")

    return {
        "plan_type": "TRANSACTION_PLAN_PREVIEW",
        "status": STATUS_ENGINE_READY,
        "ready": True,
        "source_engine_run_id": execution_plan.get("engine_run_id", ""),
        "transaction_boundary": {
            "atomic_required": True,
            "backup_before_write": True,
            "rollback_on_failure": True,
            "runtime_write_allowed": False,
            "position_write_allowed": False,
            "balance_write_allowed": False,
        },
        "preview_only": True,
        "transaction_opened": False,
        "sqlite_write": False,
    }


def _file_write_plan_preview(status: str, engine_input: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    if status != STATUS_ENGINE_READY:
        return _empty_plan("FILE_WRITE_PLAN_PREVIEW", "; ".join(issues) or "engine is not ready")

    return {
        "plan_type": "FILE_WRITE_PLAN_PREVIEW",
        "status": STATUS_ENGINE_READY,
        "ready": True,
        "runtime_file_write_preview": deepcopy(_as_dict(engine_input.get("runtime_payload_contract"))),
        "position_file_write_preview": deepcopy(_as_dict(engine_input.get("position_payload_contract"))),
        "balance_file_write_preview": deepcopy(_as_dict(engine_input.get("balance_payload_contract"))),
        "backup_preview": deepcopy(_as_dict(engine_input.get("backup_contract"))),
        "rollback_preview": deepcopy(_as_dict(engine_input.get("rollback_contract"))),
        "preview_only": True,
        "file_write_called": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
    }


def _final_engine_decision(status: str, issues: list[str]) -> dict[str, Any]:
    ready = status == STATUS_ENGINE_READY
    return {
        "approved_for_future_engine": ready,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "decision": status,
        "approval_reason": "engine contract is ready and preview-only execution plans are built" if ready else "",
        "rejection_reason": "; ".join(issues) if not ready else "",
        "preview_only": True,
        "engine_executed": False,
    }


def _result(
    *,
    status: str,
    engine_execution_plan: dict[str, Any],
    transaction_plan_preview: dict[str, Any],
    file_write_plan_preview: dict[str, Any],
    final_engine_decision: dict[str, Any],
    engine_validation: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "engine_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "engine_execution_plan": deepcopy(engine_execution_plan),
        "transaction_plan_preview": deepcopy(transaction_plan_preview),
        "file_write_plan_preview": deepcopy(file_write_plan_preview),
        "final_engine_decision": deepcopy(final_engine_decision),
        "engine_validation": deepcopy(engine_validation),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_engine_contract(engine_contract_result: dict[str, Any]) -> tuple[str, list[str]]:
    if not engine_contract_result:
        return STATUS_INVALID, ["engine_contract must be a dict"]

    source_status = _text(engine_contract_result.get("status")).upper()
    upstream_issues = list(engine_contract_result.get("issues") or [])
    if source_status == "BLOCKED":
        return STATUS_BLOCKED, ["engine contract is BLOCKED"] + upstream_issues
    if source_status == "INVALID":
        return STATUS_INVALID, ["engine contract is INVALID"] + upstream_issues
    if source_status != "READY":
        return STATUS_INVALID, ["engine contract status is not READY"]

    if engine_contract_result.get("preview_only") is not True:
        return STATUS_INVALID, ["engine contract preview_only must be true"]
    for flag in ("runtime_write", "position_write", "balance_write"):
        if engine_contract_result.get(flag) is not False:
            return STATUS_INVALID, ["engine contract {} must be false".format(flag)]
    if engine_contract_result.get("engine_executed") is not False:
        return STATUS_INVALID, ["engine contract engine_executed must be false"]

    apply_contract = _as_dict(engine_contract_result.get("apply_engine_contract"))
    if not apply_contract:
        return STATUS_INVALID, ["apply_engine_contract is required"]
    if apply_contract.get("ready_for_engine") is not True:
        return STATUS_BLOCKED, ["apply_engine_contract.ready_for_engine must be true"]
    if not _text(apply_contract.get("contract_id")):
        return STATUS_INVALID, ["apply_engine_contract.contract_id is required"]

    engine_input = _as_dict(engine_contract_result.get("engine_input_contract"))
    if not engine_input:
        return STATUS_INVALID, ["engine_input_contract is required"]
    for key in (
        "runtime_payload_contract",
        "position_payload_contract",
        "balance_payload_contract",
        "backup_contract",
        "rollback_contract",
    ):
        if not _as_dict(engine_input.get(key)):
            return STATUS_INVALID, ["engine_input_contract.{} is required".format(key)]

    gate = _as_dict(engine_contract_result.get("engine_gate"))
    if gate.get("ready_to_execute") is not True:
        return STATUS_BLOCKED, ["engine_gate.ready_to_execute must be true"]

    validation = _as_dict(engine_contract_result.get("engine_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["engine_validation.ready must be true"]

    return STATUS_ENGINE_READY, []


def build_runtime_apply_engine_result(
    engine_contract: Any,
    engine_context: Any = None,
) -> dict[str, Any]:
    """Build preview-safe Runtime Apply Engine result from an engine contract."""
    contract_result = deepcopy(_as_dict(engine_contract))
    context = deepcopy(_as_dict(engine_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(contract_result.get("warnings") or [])

    status, issues = _validate_engine_contract(contract_result)
    apply_contract = _as_dict(contract_result.get("apply_engine_contract"))
    engine_input = _as_dict(contract_result.get("engine_input_contract"))

    execution_plan = _engine_execution_plan(
        status=status,
        contract=apply_contract,
        engine_input=engine_input,
        context=context,
        issues=issues,
    )
    transaction_preview = _transaction_plan_preview(status, execution_plan, issues)
    file_write_preview = _file_write_plan_preview(status, engine_input, issues)
    decision = _final_engine_decision(status, issues)
    validation = _engine_validation(status, issues, warnings)

    return _result(
        status=status,
        engine_execution_plan=execution_plan,
        transaction_plan_preview=transaction_preview,
        file_write_plan_preview=file_write_preview,
        final_engine_decision=decision,
        engine_validation=validation,
        issues=issues,
        warnings=warnings,
        now=now,
    )
