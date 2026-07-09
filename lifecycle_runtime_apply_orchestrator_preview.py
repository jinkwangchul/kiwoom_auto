# -*- coding: utf-8 -*-
"""Preview-only orchestrator before final runtime apply.

This module consumes Runtime State Validator Preview and builds the final
end-to-end apply orchestration preview. It never reads or writes runtime files,
writes SQLite, updates GUI state, calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_APPLY_ORCHESTRATOR_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

PIPELINE_STAGE_ORDER = [
    "projection_stage",
    "persistence_stage",
    "recovery_stage",
    "reconciliation_stage",
    "commit_executor_stage",
    "atomic_apply_stage",
    "controller_stage",
    "writer_stage",
    "validator_stage",
]


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


def _result(
    *,
    status: str,
    apply_orchestrator_preview: dict[str, Any] | None = None,
    pipeline_execution_preview: dict[str, Any] | None = None,
    orchestrator_validation: dict[str, Any] | None = None,
    final_apply_decision_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "apply_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "apply_orchestrator_preview": deepcopy(apply_orchestrator_preview or {}),
        "pipeline_execution_preview": deepcopy(pipeline_execution_preview or {}),
        "orchestrator_validation": deepcopy(orchestrator_validation or {}),
        "final_apply_decision_preview": deepcopy(final_apply_decision_preview or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _stage(name: str, status: str, *, source: str = "", ready: bool = False) -> dict[str, Any]:
    return {
        "stage_name": name,
        "status": status,
        "ready": ready,
        "source": source,
        "preview_only": True,
        "executed": False,
    }


def _pipeline_preview(validator_preview: dict[str, Any], status: str) -> dict[str, Any]:
    validator_result = _as_dict(validator_preview.get("validator_result"))
    ready = status == STATUS_READY
    return {
        "projection_stage": _stage("projection", "EXPECTED_READY", source="upstream", ready=ready),
        "persistence_stage": _stage("persistence", "EXPECTED_READY", source="upstream", ready=ready),
        "recovery_stage": _stage("recovery", "EXPECTED_READY", source="upstream", ready=ready),
        "reconciliation_stage": _stage("reconciliation", "EXPECTED_READY", source="upstream", ready=ready),
        "commit_executor_stage": _stage("commit_executor", "EXPECTED_READY", source="upstream", ready=ready),
        "atomic_apply_stage": _stage("atomic_apply", "EXPECTED_READY", source="upstream", ready=ready),
        "controller_stage": _stage("controller", "EXPECTED_READY", source="upstream", ready=ready),
        "writer_stage": _stage("writer", "EXPECTED_READY", source="upstream", ready=ready),
        "validator_stage": {
            "stage_name": "validator",
            "status": validator_preview.get("status", ""),
            "ready": validator_result.get("ready") is True,
            "source": "validator_preview",
            "preview_only": True,
            "executed": False,
            "runtime_validation_preview": deepcopy(_as_dict(validator_preview.get("runtime_validation_preview"))),
            "position_validation_preview": deepcopy(_as_dict(validator_preview.get("position_validation_preview"))),
            "balance_validation_preview": deepcopy(_as_dict(validator_preview.get("balance_validation_preview"))),
            "sequence_validation_preview": deepcopy(_as_dict(validator_preview.get("sequence_validation_preview"))),
        },
    }


def _orchestrator_preview(
    *,
    context: dict[str, Any],
    pipeline: dict[str, Any],
    status: str,
    issues: list[str],
    now: str,
) -> dict[str, Any]:
    orchestrator_id = _text(context.get("orchestrator_id")) or "RUNTIME_APPLY_ORCHESTRATOR_{}".format(uuid4().hex)
    ready_count = len([stage for stage in pipeline.values() if _as_dict(stage).get("ready") is True])
    return {
        "orchestrator_id": orchestrator_id,
        "execution_pipeline": list(PIPELINE_STAGE_ORDER),
        "pipeline_summary": {
            "status": status,
            "stage_count": len(PIPELINE_STAGE_ORDER),
            "ready_stage_count": ready_count,
            "blocked": status == STATUS_BLOCKED,
            "invalid": status == STATUS_INVALID,
            "issues": list(issues),
            "preview_only": True,
            "generated_at": now,
        },
    }


def _decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "approval_reason": "validator preview ready and full apply pipeline preview is ready" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "preview_only": True,
        "apply_executed": False,
    }


def _validate_validator_preview(validator_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not validator_preview:
        return STATUS_INVALID, ["validator_preview must be a dict"]
    status = _text(validator_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["validator preview is BLOCKED"] + list(validator_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["validator preview is INVALID"] + list(validator_preview.get("issues") or [])
    if status != STATUS_READY:
        return STATUS_INVALID, ["validator preview status is not READY"]
    if validator_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["validator preview_only must be true"]
    for flag in ("runtime_write", "position_write", "balance_write"):
        if validator_preview.get(flag) is not False:
            return STATUS_INVALID, ["validator {} must be false".format(flag)]
    validator_result = _as_dict(validator_preview.get("validator_result"))
    if validator_result.get("ready") is not True:
        return STATUS_BLOCKED, ["validator_result.ready must be true"]
    return STATUS_READY, []


def build_runtime_apply_orchestrator_preview(
    validator_preview: Any,
    orchestrator_context: Any = None,
) -> dict[str, Any]:
    """Build the final preview-only runtime apply orchestration payload."""
    validator = _as_dict(validator_preview)
    context = deepcopy(_as_dict(orchestrator_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(validator.get("warnings") or [])

    status, issues = _validate_validator_preview(validator)
    pipeline = _pipeline_preview(validator, status)
    orchestrator = _orchestrator_preview(
        context=context,
        pipeline=pipeline,
        status=status,
        issues=issues,
        now=now,
    )
    validation = _validation(status, issues, warnings)
    decision = _decision(status, issues)
    return _result(
        status=status,
        apply_orchestrator_preview=orchestrator,
        pipeline_execution_preview=pipeline,
        orchestrator_validation=validation,
        final_apply_decision_preview=decision,
        issues=issues,
        warnings=warnings,
        now=now,
    )

