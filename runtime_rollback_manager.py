# -*- coding: utf-8 -*-
"""Runtime Rollback Manager for Real Runtime Commit (M6-3).

This module builds a *preview-only* rollback plan based on a backup_plan. It does NOT
execute any rollback, does NOT restore any files, and does NOT touch protected
runtime files.

Scope boundaries (M6-3):
- ``preview_only`` is always True.
- No file copy / creation / write of any kind.
- No backup directory creation or modification.
- No modification of ``runtime/*.json`` or ``routines/*/rules.json``.
- Atomic Writer is never invoked (``atomic_writer_called`` stays False).
- Backup Manager / Runtime Commit Executor are NOT invoked here.
- No GUI / SendOrder / Chejan / Broker / SQLite / rules writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

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


def create_runtime_rollback_plan(
    commit_id: Optional[str] = None,
    backup_plan: Optional[dict[str, Any]] = None,
    failed_targets: Optional[Sequence[Any]] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Build a preview-only rollback plan based on a backup_plan.

    Args:
        commit_id: Identifier for the runtime commit this rollback belongs to.
        backup_plan: Backup plan dict from create_runtime_backup_plan.
        failed_targets: Optional iterable of failed target paths to rollback.
        runtime_snapshot: Optional runtime snapshot reference (not used).
        operator_context: Optional operator context metadata (not used).

    Returns:
        Dict with rollback_status (READY/BLOCKED/INVALID), preview_only=True,
        commit_id, rollback_targets, rollback_metadata, rollback_strategy,
        safety_flags (all False), issues, warnings.
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

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=[],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- INVALID: backup_plan.preview_only != True ----
    if not backup_plan.get("preview_only", False) is True:
        issues.append("backup_plan preview_only is not True")

    # ---- INVALID: commit_id mismatch ----
    bp_commit_id = backup_plan.get("commit_id", "")
    if str(bp_commit_id).strip() != str(commit_id).strip():
        issues.append("commit_id mismatch with backup_plan")

    # ---- INVALID: backup_plan.backup_status == INVALID ----
    backup_status = backup_plan.get("backup_status", "")
    if backup_status == STATUS_INVALID:
        issues.append("backup_plan backup_status is INVALID")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=[],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- BLOCKED: backup_plan.backup_status != READY ----
    if backup_status != STATUS_READY:
        issues.append("backup_plan backup_status is not READY")
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=[],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    backup_targets = backup_plan.get("backup_targets", [])
    if not backup_targets:
        issues.append("backup_targets is empty in backup_plan")
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=[],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- Determine rollback targets ----
    rollback_targets = _resolve_rollback_targets(
        backup_targets=backup_targets,
        failed_targets=failed_targets,
        issues=issues,
    )

    if not rollback_targets:
        issues.append("no matching failed_targets in backup_plan")
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=failed_targets if failed_targets else [],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- INVALID: rules.json in rollback targets ----
    for target in rollback_targets:
        p = Path(target.get("source", ""))
        if p and _is_protected_rules_json(p):
            issues.append(f"protected rules.json target included: {p}")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            failed_targets=failed_targets if failed_targets else [],
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
        failed_targets=failed_targets if failed_targets else [],
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
    )


def _resolve_rollback_targets(
    backup_targets: list[dict[str, Any]],
    failed_targets: Optional[Sequence[Any]],
    issues: list[str],
) -> list[dict[str, Any]]:
    """Resolve rollback targets based on failed_targets specification."""
    if failed_targets is None:
        return list(backup_targets)

    result: list[dict[str, Any]] = []
    failed_sources = set()
    for item in failed_targets:
        p = Path(str(item)) if item else None
        if p:
            failed_sources.add(str(p.resolve()))

    for target in backup_targets:
        source = target.get("source", "")
        p = Path(source) if source else None
        if p and str(p.resolve()) in failed_sources:
            result.append(target)

    return result


def _build_plan(
    *,
    status: str,
    commit_id: Optional[str],
    backup_plan: Optional[dict[str, Any]],
    failed_targets: list[Any],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    bp_targets = []
    if isinstance(backup_plan, dict):
        bp_targets = backup_plan.get("backup_targets", [])  # type: ignore[union-attr]

    rollback_targets = []
    if status == STATUS_READY:
        rollback_targets = _resolve_rollback_targets(
            backup_targets=bp_targets,
            failed_targets=failed_targets if failed_targets else None,
            issues=issues,
        )

    rollback_metadata = {
        "plan_type": "RUNTIME_ROLLBACK_PLAN",
        "commit_id": commit_str,
        "target_count": len(rollback_targets),
        "preview_only": True,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    rollback_strategy = {
        "strategy": "restore_from_backup",
        "restore_candidates": [
            {
                "source": t.get("source", ""),
                "backup_candidate": t.get("backup_candidate", ""),
                "restore_candidate": t.get("source", ""),
            }
            for t in rollback_targets
        ],
    }

    return {
        "rollback_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "rollback_targets": rollback_targets,
        "rollback_metadata": rollback_metadata,
        "rollback_strategy": rollback_strategy,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }