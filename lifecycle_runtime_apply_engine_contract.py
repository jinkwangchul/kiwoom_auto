# -*- coding: utf-8 -*-
"""Preview-only contract builder for the future runtime apply engine.

This module converts Runtime Apply Orchestrator Preview into an engine input
contract. It never reads or writes runtime files, writes SQLite, updates GUI
state, calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_APPLY_ENGINE_CONTRACT"
STATUS_READY = "READY"
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


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _empty_payload_contract(contract_type: str, reason: str) -> dict[str, Any]:
    return {
        "contract_type": contract_type,
        "status": STATUS_BLOCKED,
        "ready": False,
        "source_stage": "",
        "payload_preview": {},
        "required": True,
        "blocked_reason": reason,
        "preview_only": True,
    }


def _payload_contract(
    *,
    contract_type: str,
    source_stage: str,
    stage_payload: dict[str, Any],
    context_payload: dict[str, Any],
) -> dict[str, Any]:
    payload_preview = deepcopy(context_payload) if context_payload else deepcopy(stage_payload)
    return {
        "contract_type": contract_type,
        "status": STATUS_READY,
        "ready": True,
        "source_stage": source_stage,
        "payload_preview": payload_preview,
        "required": True,
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
    }


def _engine_input_contract(
    *,
    status: str,
    pipeline: dict[str, Any],
    context: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    if status != STATUS_READY:
        reason = "; ".join(issues) or "engine contract is not ready"
        return {
            "runtime_payload_contract": _empty_payload_contract("RUNTIME_PAYLOAD_CONTRACT", reason),
            "position_payload_contract": _empty_payload_contract("POSITION_PAYLOAD_CONTRACT", reason),
            "balance_payload_contract": _empty_payload_contract("BALANCE_PAYLOAD_CONTRACT", reason),
            "backup_contract": _empty_payload_contract("BACKUP_CONTRACT", reason),
            "rollback_contract": _empty_payload_contract("ROLLBACK_CONTRACT", reason),
            "preview_only": True,
        }

    writer_stage = _as_dict(pipeline.get("writer_stage"))
    controller_stage = _as_dict(pipeline.get("controller_stage"))
    atomic_stage = _as_dict(pipeline.get("atomic_apply_stage"))

    return {
        "runtime_payload_contract": _payload_contract(
            contract_type="RUNTIME_PAYLOAD_CONTRACT",
            source_stage="writer_stage",
            stage_payload=writer_stage,
            context_payload=_as_dict(context.get("runtime_payload_contract")),
        ),
        "position_payload_contract": _payload_contract(
            contract_type="POSITION_PAYLOAD_CONTRACT",
            source_stage="writer_stage",
            stage_payload=writer_stage,
            context_payload=_as_dict(context.get("position_payload_contract")),
        ),
        "balance_payload_contract": _payload_contract(
            contract_type="BALANCE_PAYLOAD_CONTRACT",
            source_stage="writer_stage",
            stage_payload=writer_stage,
            context_payload=_as_dict(context.get("balance_payload_contract")),
        ),
        "backup_contract": _payload_contract(
            contract_type="BACKUP_CONTRACT",
            source_stage="controller_stage",
            stage_payload=controller_stage,
            context_payload=_as_dict(context.get("backup_contract")),
        ),
        "rollback_contract": _payload_contract(
            contract_type="ROLLBACK_CONTRACT",
            source_stage="atomic_apply_stage",
            stage_payload=atomic_stage,
            context_payload=_as_dict(context.get("rollback_contract")),
        ),
        "preview_only": True,
    }


def _apply_engine_contract(
    *,
    status: str,
    orchestrator_id: str,
    context: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    ready = status == STATUS_READY
    contract_id = _text(context.get("contract_id")) or "RUNTIME_APPLY_ENGINE_CONTRACT_{}".format(uuid4().hex)
    return {
        "contract_id": contract_id,
        "source_orchestrator_id": orchestrator_id,
        "status": status,
        "ready_for_engine": ready,
        "blocked_reason": "; ".join(issues) if status == STATUS_BLOCKED else "",
        "invalid_reason": "; ".join(issues) if status == STATUS_INVALID else "",
        "preview_only": True,
    }


def _engine_gate(status: str, context: dict[str, Any]) -> dict[str, Any]:
    ready = status == STATUS_READY
    return {
        "approval_required": True,
        "operator_review_required": True,
        "contract_token_required": True,
        "ready_to_execute": ready,
        "contract_token_preview": _text(context.get("contract_token")) or "",
        "preview_only": True,
        "engine_executed": False,
    }


def _result(
    *,
    status: str,
    apply_engine_contract: dict[str, Any],
    engine_input_contract: dict[str, Any],
    engine_gate: dict[str, Any],
    engine_validation: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "engine_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "apply_engine_contract": deepcopy(apply_engine_contract),
        "engine_input_contract": deepcopy(engine_input_contract),
        "engine_gate": deepcopy(engine_gate),
        "engine_validation": deepcopy(engine_validation),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_orchestrator_preview(orchestrator_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not orchestrator_preview:
        return STATUS_INVALID, ["orchestrator_preview must be a dict"]

    status = _text(orchestrator_preview.get("status")).upper()
    upstream_issues = list(orchestrator_preview.get("issues") or [])
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["orchestrator preview is BLOCKED"] + upstream_issues
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["orchestrator preview is INVALID"] + upstream_issues
    if status != STATUS_READY:
        return STATUS_INVALID, ["orchestrator preview status is not READY"]

    if orchestrator_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["orchestrator preview_only must be true"]
    for flag in ("runtime_write", "position_write", "balance_write"):
        if orchestrator_preview.get(flag) is not False:
            return STATUS_INVALID, ["orchestrator {} must be false".format(flag)]
    if orchestrator_preview.get("apply_executed") is not False:
        return STATUS_INVALID, ["orchestrator apply_executed must be false"]

    orchestrator = _as_dict(orchestrator_preview.get("apply_orchestrator_preview"))
    if not _text(orchestrator.get("orchestrator_id")):
        return STATUS_INVALID, ["apply_orchestrator_preview.orchestrator_id is required"]

    pipeline = _as_dict(orchestrator_preview.get("pipeline_execution_preview"))
    if not pipeline:
        return STATUS_INVALID, ["pipeline_execution_preview is required"]

    validation = _as_dict(orchestrator_preview.get("orchestrator_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["orchestrator_validation.ready must be true"]

    decision = _as_dict(orchestrator_preview.get("final_apply_decision_preview"))
    if decision.get("approved") is not True:
        return STATUS_BLOCKED, ["final_apply_decision_preview.approved must be true"]

    return STATUS_READY, []


def build_runtime_apply_engine_contract(
    orchestrator_preview: Any,
    engine_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only input contract for the future runtime apply engine."""
    orchestrator_result = deepcopy(_as_dict(orchestrator_preview))
    context = deepcopy(_as_dict(engine_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(orchestrator_result.get("warnings") or [])

    status, issues = _validate_orchestrator_preview(orchestrator_result)
    orchestrator = _as_dict(orchestrator_result.get("apply_orchestrator_preview"))
    pipeline = _as_dict(orchestrator_result.get("pipeline_execution_preview"))
    orchestrator_id = _text(orchestrator.get("orchestrator_id"))

    apply_contract = _apply_engine_contract(
        status=status,
        orchestrator_id=orchestrator_id,
        context=context,
        issues=issues,
    )
    input_contract = _engine_input_contract(
        status=status,
        pipeline=pipeline,
        context=context,
        issues=issues,
    )
    gate = _engine_gate(status, context)
    validation = _validation(status, issues, warnings)
    return _result(
        status=status,
        apply_engine_contract=apply_contract,
        engine_input_contract=input_contract,
        engine_gate=gate,
        engine_validation=validation,
        issues=issues,
        warnings=warnings,
        now=now,
    )
