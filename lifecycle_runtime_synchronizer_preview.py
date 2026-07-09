# -*- coding: utf-8 -*-
"""Preview-only runtime synchronizer before real runtime state synchronization.

This module consumes Runtime State Commit Preview and builds synchronization
targets, consistency checks, sync plans, sync sequence, validation, and final
decision previews. It never executes sync, commits, writes files, creates
backups, rolls back, writes SQLite, updates GUI, calls SendOrder, or connects
Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_SYNCHRONIZER_PREVIEW"
STATUS_READY = "SYNCHRONIZER_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

STATE_COMMIT_PREVIEW_READY = "STATE_COMMIT_PREVIEW_READY"


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


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _empty_sync_target_preview() -> dict[str, Any]:
    return {
        "runtime_sync_targets": [],
        "position_sync_targets": [],
        "balance_sync_targets": [],
        "audit_sync_targets": [],
        "preview_only": True,
    }


def _sync_target_preview(state_commit: dict[str, Any]) -> dict[str, Any]:
    verification = _as_dict(state_commit.get("post_commit_verification_preview"))
    targets = _as_dict(verification.get("verification_targets"))
    return {
        "runtime_sync_targets": deepcopy(_as_list(targets.get("runtime_targets"))),
        "position_sync_targets": deepcopy(_as_list(targets.get("position_targets"))),
        "balance_sync_targets": deepcopy(_as_list(targets.get("balance_targets"))),
        "audit_sync_targets": deepcopy(_as_list(targets.get("audit_targets"))),
        "preview_only": True,
    }


def _consistency_check_preview(sync_targets: dict[str, Any]) -> dict[str, Any]:
    check_targets = {
        "runtime": deepcopy(_as_list(sync_targets.get("runtime_sync_targets"))),
        "position": deepcopy(_as_list(sync_targets.get("position_sync_targets"))),
        "balance": deepcopy(_as_list(sync_targets.get("balance_sync_targets"))),
        "audit": deepcopy(_as_list(sync_targets.get("audit_sync_targets"))),
    }
    return {
        "consistency_check_required": True,
        "consistency_check_executed": False,
        "check_targets": check_targets,
        "expected_consistency_state": {
            "runtime_position_consistent": True,
            "runtime_balance_consistent": True,
            "audit_matches_runtime": True,
            "preview_only": True,
        },
        "preview_only": True,
    }


def _plan(target_name: str, targets: list[Any]) -> dict[str, Any]:
    return {
        "target": target_name,
        "targets": deepcopy(targets),
        "sync_required": True,
        "sync_executed": False,
        "preview_only": True,
    }


def _sync_plan_preview(sync_targets: dict[str, Any]) -> dict[str, Any]:
    runtime = _as_list(sync_targets.get("runtime_sync_targets"))
    position = _as_list(sync_targets.get("position_sync_targets"))
    balance = _as_list(sync_targets.get("balance_sync_targets"))
    audit = _as_list(sync_targets.get("audit_sync_targets"))
    return {
        "runtime_sync_plan": _plan("runtime", runtime),
        "position_sync_plan": _plan("position", position),
        "balance_sync_plan": _plan("balance", balance),
        "audit_sync_plan": _plan("audit", audit),
        "plan_count": 4,
        "preview_only": True,
    }


def _sync_sequence_preview() -> dict[str, Any]:
    return {
        "sequence_type": "RUNTIME_SYNCHRONIZER_PREVIEW_SEQUENCE",
        "ordered_steps": [
            {"step_index": 1, "action": "VERIFY_STATE_COMMIT_PREVIEW", "preview_only": True},
            {"step_index": 2, "action": "CHECK_RUNTIME_CONSISTENCY", "preview_only": True},
            {"step_index": 3, "action": "PLAN_RUNTIME_SYNC", "preview_only": True},
            {"step_index": 4, "action": "PLAN_POSITION_SYNC", "preview_only": True},
            {"step_index": 5, "action": "PLAN_BALANCE_SYNC", "preview_only": True},
            {"step_index": 6, "action": "PLAN_AUDIT_SYNC", "preview_only": True},
            {"step_index": 7, "action": "FINAL_SYNC_REVIEW", "preview_only": True},
        ],
        "sync_executed": False,
        "preview_only": True,
    }


def _final_sync_decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "sync_allowed": False,
        "sync_executed": False,
        "approval_reason": "state commit preview is ready and sync preflight validation passed" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    sync_target_preview: dict[str, Any],
    consistency_check_preview: dict[str, Any],
    sync_plan_preview: dict[str, Any],
    sync_sequence_preview: dict[str, Any],
    sync_preflight_validation: dict[str, Any],
    final_sync_decision: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "sync_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "commit_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "sync_target_preview": deepcopy(sync_target_preview),
        "consistency_check_preview": deepcopy(consistency_check_preview),
        "sync_plan_preview": deepcopy(sync_plan_preview),
        "sync_sequence_preview": deepcopy(sync_sequence_preview),
        "sync_preflight_validation": deepcopy(sync_preflight_validation),
        "final_sync_decision": deepcopy(final_sync_decision),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_state_commit_preview(state_commit: dict[str, Any]) -> tuple[str, list[str]]:
    if not state_commit:
        return STATUS_INVALID, ["state_commit_preview must be a dict"]

    status = _text(state_commit.get("status")).upper()
    upstream_issues = list(state_commit.get("issues") or [])
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["state commit preview is BLOCKED"] + upstream_issues
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["state commit preview is INVALID"] + upstream_issues
    if status != STATE_COMMIT_PREVIEW_READY:
        return STATUS_INVALID, ["state commit preview status is not STATE_COMMIT_PREVIEW_READY"]

    if state_commit.get("preview_only") is not True:
        return STATUS_INVALID, ["state commit preview_only must be true"]
    for flag in (
        "sync_executed",
        "runtime_write",
        "position_write",
        "balance_write",
        "audit_write",
        "file_write_called",
        "commit_executed",
        "backup_created",
        "rollback_executed",
        "gui_update_called",
        "send_order_called",
        "chejan_called",
    ):
        if flag in state_commit and state_commit.get(flag) is not False:
            return STATUS_INVALID, ["state commit {} must be false".format(flag)]

    if not _as_dict(state_commit.get("commit_candidate_preview")):
        return STATUS_INVALID, ["commit_candidate_preview is required"]
    if not _as_dict(state_commit.get("post_commit_verification_preview")):
        return STATUS_INVALID, ["post_commit_verification_preview is required"]

    validation = _as_dict(state_commit.get("commit_preflight_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["commit_preflight_validation.ready must be true"]

    decision = _as_dict(state_commit.get("final_commit_decision"))
    if decision.get("approved") is not True:
        return STATUS_BLOCKED, ["final_commit_decision.approved must be true"]
    if decision.get("commit_allowed") is not False:
        return STATUS_INVALID, ["final_commit_decision.commit_allowed must be false"]

    return STATUS_READY, []


def build_runtime_synchronizer_preview(
    state_commit_preview: Any,
    sync_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only runtime synchronizer payload from state commit preview."""
    state_commit = deepcopy(_as_dict(state_commit_preview))
    context = deepcopy(_as_dict(sync_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(state_commit.get("warnings") or [])

    status, issues = _validate_state_commit_preview(state_commit)
    targets = _sync_target_preview(state_commit) if status == STATUS_READY else _empty_sync_target_preview()
    consistency = _consistency_check_preview(targets)
    plan = _sync_plan_preview(targets)
    sequence = _sync_sequence_preview()
    validation = _validation(status, issues, warnings)
    decision = _final_sync_decision(status, issues)

    return _result(
        status=status,
        sync_target_preview=targets,
        consistency_check_preview=consistency,
        sync_plan_preview=plan,
        sync_sequence_preview=sequence,
        sync_preflight_validation=validation,
        final_sync_decision=decision,
        issues=issues,
        warnings=warnings,
        now=now,
    )
