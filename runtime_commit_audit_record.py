# -*- coding: utf-8 -*-
"""Runtime Commit Audit Record for Real Runtime Commit (M6-5).

This module builds a *preview-only* audit record for a runtime commit. It does NOT
write any files, does NOT read actual runtime files, and does NOT touch protected
runtime files.

Scope boundaries (M6-5):
- ``preview_only`` is always True.
- No file read / write of any kind.
- No actual audit file creation or modification.
- No modification of ``runtime/*.json`` or ``routines/*/rules.json``.
- Atomic Writer is never invoked (``atomic_writer_called`` stays False).
- No GUI / SendOrder / Chejan / Broker / SQLite / rules writes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
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


def create_runtime_commit_audit_record(
    commit_id: Optional[str] = None,
    backup_plan: Optional[dict[str, Any]] = None,
    rollback_plan: Optional[dict[str, Any]] = None,
    verification_result: Optional[dict[str, Any]] = None,
    audit_records: Optional[Sequence[Any]] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Build a preview-only audit record for a runtime commit.

    Args:
        commit_id: Identifier for the runtime commit.
        backup_plan: Backup plan dict from create_runtime_backup_plan.
        rollback_plan: Rollback plan dict from create_runtime_rollback_plan.
        verification_result: Verification result dict from verify_runtime_commit.
        audit_records: Optional sequence of audit records to include.
        runtime_snapshot: Optional runtime snapshot reference (not used).
        operator_context: Optional operator context metadata (not used).

    Returns:
        Dict with audit_status (READY/BLOCKED/INVALID), preview_only,
        commit_id, audit_records_preview, audit_metadata, safety_flags (all False),
        issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ---- INVALID: commit_id missing ----
    if not commit_id or not str(commit_id).strip():
        issues.append("commit_id is missing or empty")

    # ---- INVALID: backup_plan not dict ----
    if backup_plan is not None and not isinstance(backup_plan, dict):
        issues.append("backup_plan is not a dict")

    # ---- INVALID: rollback_plan not dict ----
    if rollback_plan is not None and not isinstance(rollback_plan, dict):
        issues.append("rollback_plan is not a dict")

    # ---- INVALID: verification_result not dict ----
    if verification_result is not None and not isinstance(verification_result, dict):
        issues.append("verification_result is not a dict")

    if issues:
        return _build_audit_record(
            status=STATUS_INVALID,
            commit_id=commit_id,
            audit_records=audit_records if audit_records else [],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- INVALID: rules.json in backup_targets ----
    if backup_plan and backup_plan.get("backup_targets"):
        for t in backup_plan.get("backup_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in backup_targets: {p}")

    # ---- INVALID: backup_plan status is INVALID ----
    if backup_plan and backup_plan.get("backup_status") == STATUS_INVALID:
        issues.append("backup_plan status is INVALID")

    # ---- INVALID: rules.json in rollback_targets ----
    if rollback_plan and rollback_plan.get("rollback_targets"):
        for t in rollback_plan.get("rollback_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in rollback_targets: {p}")

    if issues:
        return _build_audit_record(
            status=STATUS_INVALID,
            commit_id=commit_id,
            audit_records=audit_records if audit_records else [],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- BLOCKED: verification rollback_required ----
    if verification_result and verification_result.get("rollback_required", False):
        issues.append("verification indicates rollback required")
        status_to_return = STATUS_BLOCKED
    else:
        status_to_return = STATUS_READY

    # ---- READY or BLOCKED ----
    return _build_audit_record(
        status=status_to_return,
        commit_id=commit_id,
        backup_plan=backup_plan,
        rollback_plan=rollback_plan,
        verification_result=verification_result,
        audit_records=audit_records if audit_records else [],
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
    )


def _build_audit_record(
    *,
    status: str,
    commit_id: Optional[str],
    backup_plan: Optional[dict[str, Any]] = None,
    rollback_plan: Optional[dict[str, Any]] = None,
    verification_result: Optional[dict[str, Any]] = None,
    audit_records: list[Any],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    # Build audit records preview without writing
    records_preview = []
    for i, record in enumerate(audit_records):
        if isinstance(record, dict):
            records_preview.append({
                "record_index": i,
                "target": record.get("target", ""),
                "status": record.get("status", ""),
                "hash": _compute_hash(record) if record else None,
            })
        elif isinstance(record, str):
            records_preview.append({
                "record_index": i,
                "data": record,
                "hash": _compute_hash({"data": record}),
            })
        else:
            records_preview.append({
                "record_index": i,
                "data": str(record),
                "hash": _compute_hash({"data": str(record)}),
            })

    audit_metadata = {
        "plan_type": "RUNTIME_COMMIT_AUDIT_RECORD",
        "commit_id": commit_str,
        "record_count": len(records_preview),
        "backup_target_count": len(backup_plan.get("backup_targets", [])) if backup_plan else 0,
        "rollback_target_count": len(rollback_plan.get("rollback_targets", [])) if rollback_plan else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "preview_only": True,
        "has_backup_plan": backup_plan is not None,
        "has_rollback_plan": rollback_plan is not None,
        "has_verification_result": verification_result is not None,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    return {
        "audit_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "audit_records_preview": records_preview,
        "audit_metadata": audit_metadata,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }


def _compute_hash(data: Any) -> str:
    """Compute a hash of the data without side effects."""
    try:
        data_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(data_str.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""