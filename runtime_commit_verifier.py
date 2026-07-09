# -*- coding: utf-8 -*-
"""Runtime Commit Verifier for Real Runtime Commit (M6-4).

This module builds a *preview-only* commit verification plan based on a backup_plan
and rollback_plan. It does NOT execute any commit, does NOT modify any files,
and does NOT touch protected runtime files.

Scope boundaries (M6-4):
- ``preview_only`` is always True.
- No file copy / creation / write of any kind.
- No backup directory creation or modification.
- No modification of ``runtime/*.json`` or ``routines/*/rules.json``.
- Atomic Writer is never invoked (``atomic_writer_called`` stays False).
- No GUI / SendOrder / Chejan / Broker / SQLite / rules writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _is_protected_rules_json(p: Path) -> bool:
    return p.name == "rules.json"


def _build_safety_flags() -> dict[str, bool]:
    return {
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "rules_write": False,
        "atomic_writer_called": False,
        "backup_manager_called": False,
    }


def create_runtime_commit_verifier_plan(
    commit_id: Optional[str] = None,
    backup_plan: Optional[dict[str, Any]] = None,
    rollback_plan: Optional[dict[str, Any]] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Build a preview-only commit verification plan.

    Args:
        commit_id: Identifier for the runtime commit.
        backup_plan: Backup plan dict from create_runtime_backup_plan.
        rollback_plan: Rollback plan dict from create_runtime_rollback_plan.
        runtime_snapshot: Optional runtime snapshot reference (not used).
        operator_context: Optional operator context metadata (not used).

    Returns:
        Dict with verify_status (READY/BLOCKED/INVALID), preview_only,
        commit_id, verify_metadata, verify_strategy, safety_flags (all False),
        issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ---- INVALID: commit_id missing ----
    if not commit_id or not str(commit_id).strip():
        issues.append("commit_id is missing or empty")

    # ---- INVALID: backup_plan missing or not dict ----
    if backup_plan is None:
        issues.append("backup_plan is missing")
    elif not isinstance(backup_plan, dict):
        issues.append("backup_plan is not a dict")

    # ---- INVALID: rollback_plan missing or not dict ----
    if rollback_plan is None:
        issues.append("rollback_plan is missing")
    elif not isinstance(rollback_plan, dict):
        issues.append("rollback_plan is not a dict")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- INVALID: backup_plan.preview_only != True ----
    if backup_plan.get("preview_only", False) is not True:
        issues.append("backup_plan preview_only is not True")

    # ---- INVALID: rollback_plan.preview_only != True ----
    if rollback_plan.get("preview_only", False) is not True:
        issues.append("rollback_plan preview_only is not True")

    # ---- INVALID: commit_id mismatch ----
    bp_commit_id = backup_plan.get("commit_id", "")
    rp_commit_id = rollback_plan.get("commit_id", "")
    if str(bp_commit_id).strip() != str(commit_id).strip():
        issues.append("commit_id mismatch with backup_plan")
    if str(rp_commit_id).strip() != str(commit_id).strip():
        issues.append("commit_id mismatch with rollback_plan")

    # ---- INVALID: backup_plan.backup_status == INVALID ----
    backup_status = backup_plan.get("backup_status", "")
    if backup_status == STATUS_INVALID:
        issues.append("backup_plan backup_status is INVALID")

    # ---- INVALID: rollback_plan.rollback_status == INVALID ----
    rollback_status = rollback_plan.get("rollback_status", "")
    if rollback_status == STATUS_INVALID:
        issues.append("rollback_plan rollback_status is INVALID")

    # ---- INVALID: rules.json in backup_targets ----
    if not issues and backup_plan.get("backup_targets"):
        for t in backup_plan.get("backup_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in backup_targets: {p}")

    # ---- INVALID: rules.json in rollback_targets ----
    if not issues and rollback_plan.get("rollback_targets"):
        for t in rollback_plan.get("rollback_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in rollback_targets: {p}")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- BLOCKED: backup_plan.backup_status != READY ----
    if backup_status != STATUS_READY:
        issues.append("backup_plan backup_status is not READY")

    # ---- BLOCKED: rollback_plan.rollback_status != READY ----
    if rollback_status != STATUS_READY:
        issues.append("rollback_plan rollback_status is not READY")

    if issues:
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- READY ----
    return _build_plan(
        status=STATUS_READY,
        commit_id=commit_id,
        backup_plan=backup_plan,
        rollback_plan=rollback_plan,
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
    )


def _build_plan(
    *,
    status: str,
    commit_id: Optional[str],
    backup_plan: Optional[dict[str, Any]],
    rollback_plan: Optional[dict[str, Any]],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    bp_targets = []
    rp_targets = []
    if isinstance(backup_plan, dict):
        bp_targets = backup_plan.get("backup_targets", [])
    if isinstance(rollback_plan, dict):
        rp_targets = rollback_plan.get("rollback_targets", [])

    verify_metadata = {
        "plan_type": "RUNTIME_COMMIT_VERIFY_PLAN",
        "commit_id": commit_str,
        "backup_target_count": len(bp_targets),
        "rollback_target_count": len(rp_targets),
        "preview_only": True,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    verify_strategy = {
        "strategy": "verify_before_commit",
        "backup_verified": status == STATUS_READY,
        "rollback_ready": status == STATUS_READY,
    }

    return {
        "verify_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "verify_metadata": verify_metadata,
        "verify_strategy": verify_strategy,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }