# -*- coding: utf-8 -*-
"""Preview-only execution readiness gate after runtime synchronizer preview.

This module fixes final readiness, approval requirements, and blocking reasons
before handing runtime state toward an execution layer. It never starts
execution, writes runtime files, commits, syncs, creates backups, rolls back,
updates GUI state, calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_EXECUTION_READINESS_GATE_PREVIEW"
STATUS_READY = "EXECUTION_READINESS_GATE_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

SYNCHRONIZER_PREVIEW_READY = "SYNCHRONIZER_PREVIEW_READY"


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
        "preview_only": True,
    }


def _readiness_check_preview(status: str, synchronizer: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "readiness_required": True,
        "readiness_checked": False,
        "synchronizer_status": _text(synchronizer.get("status")),
        "sync_preflight_ready": _as_dict(synchronizer.get("sync_preflight_validation")).get("ready") is True,
        "final_sync_approved": _as_dict(synchronizer.get("final_sync_decision")).get("approved") is True,
        "ready_for_execution_layer": status == STATUS_READY,
        "issues": list(issues),
        "preview_only": True,
    }


def _execution_gate_preview(status: str) -> dict[str, Any]:
    return {
        "gate_type": "RUNTIME_EXECUTION_READINESS_GATE",
        "gate_status": status,
        "execution_allowed": False,
        "execution_started": False,
        "manual_execution_approval_required": True,
        "runtime_state_ready": status == STATUS_READY,
        "preview_only": True,
    }


def _approval_requirement_preview(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "operator_approval_required": True,
        "runtime_review_required": True,
        "execution_token_required": True,
        "approval_token_issued": False,
        "approval_token_consumed": False,
        "approval_policy": deepcopy(_as_dict(context.get("approval_policy"))),
        "preview_only": True,
    }


def _blocking_reason_preview(status: str, issues: list[str]) -> dict[str, Any]:
    return {
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "blocking_reasons": list(issues) if status == STATUS_BLOCKED else [],
        "invalid_reasons": list(issues) if status == STATUS_INVALID else [],
        "preview_only": True,
    }


def _final_readiness_decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "execution_allowed": False,
        "execution_started": False,
        "approval_reason": "synchronizer preview is ready and readiness gate validation passed" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    readiness_check_preview: dict[str, Any],
    execution_gate_preview: dict[str, Any],
    approval_requirement_preview: dict[str, Any],
    blocking_reason_preview: dict[str, Any],
    final_readiness_decision: dict[str, Any],
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
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "commit_executed": False,
        "sync_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "readiness_check_preview": deepcopy(readiness_check_preview),
        "execution_gate_preview": deepcopy(execution_gate_preview),
        "approval_requirement_preview": deepcopy(approval_requirement_preview),
        "blocking_reason_preview": deepcopy(blocking_reason_preview),
        "final_readiness_decision": deepcopy(final_readiness_decision),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_synchronizer_preview(synchronizer: dict[str, Any]) -> tuple[str, list[str]]:
    if not synchronizer:
        return STATUS_INVALID, ["synchronizer_preview must be a dict"]

    status = _text(synchronizer.get("status")).upper()
    upstream_issues = list(synchronizer.get("issues") or [])
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["synchronizer preview is BLOCKED"] + upstream_issues
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["synchronizer preview is INVALID"] + upstream_issues
    if status != SYNCHRONIZER_PREVIEW_READY:
        return STATUS_INVALID, ["synchronizer preview status is not SYNCHRONIZER_PREVIEW_READY"]

    if synchronizer.get("preview_only") is not True:
        return STATUS_INVALID, ["synchronizer preview_only must be true"]
    for flag in (
        "execution_allowed",
        "execution_started",
        "runtime_write",
        "position_write",
        "balance_write",
        "audit_write",
        "file_write_called",
        "commit_executed",
        "sync_executed",
        "backup_created",
        "rollback_executed",
        "gui_update_called",
        "send_order_called",
        "chejan_called",
    ):
        if flag in synchronizer and synchronizer.get(flag) is not False:
            return STATUS_INVALID, ["synchronizer {} must be false".format(flag)]

    if not _as_dict(synchronizer.get("sync_target_preview")):
        return STATUS_INVALID, ["sync_target_preview is required"]
    if not _as_dict(synchronizer.get("consistency_check_preview")):
        return STATUS_INVALID, ["consistency_check_preview is required"]

    validation = _as_dict(synchronizer.get("sync_preflight_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["sync_preflight_validation.ready must be true"]

    decision = _as_dict(synchronizer.get("final_sync_decision"))
    if decision.get("approved") is not True:
        return STATUS_BLOCKED, ["final_sync_decision.approved must be true"]
    if decision.get("sync_allowed") is not False:
        return STATUS_INVALID, ["final_sync_decision.sync_allowed must be false"]

    return STATUS_READY, []


def build_runtime_execution_readiness_gate_preview(
    synchronizer_preview: Any,
    gate_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only execution readiness gate payload from synchronizer preview."""
    synchronizer = deepcopy(_as_dict(synchronizer_preview))
    context = deepcopy(_as_dict(gate_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(synchronizer.get("warnings") or [])

    status, issues = _validate_synchronizer_preview(synchronizer)
    readiness = _readiness_check_preview(status, synchronizer, issues)
    gate = _execution_gate_preview(status)
    approval = _approval_requirement_preview(context)
    blocking = _blocking_reason_preview(status, issues)
    decision = _final_readiness_decision(status, issues)

    return _result(
        status=status,
        readiness_check_preview=readiness,
        execution_gate_preview=gate,
        approval_requirement_preview=approval,
        blocking_reason_preview=blocking,
        final_readiness_decision=decision,
        issues=issues,
        warnings=warnings,
        now=now,
    )
