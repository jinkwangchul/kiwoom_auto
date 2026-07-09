# -*- coding: utf-8 -*-
"""Runtime Backup Manager for Real Runtime Commit (M6-2).

This module builds a *preview-only* backup plan for a given ``commit_id``. It
does NOT create any backup files or directories, does NOT call the Atomic
Writer, does NOT roll back, and does NOT touch protected runtime files.

Scope boundaries (M6-2):
- ``preview_only`` is always True.
- No file copy / creation / write of any kind.
- No backup directory creation.
- No modification of ``runtime/*.json`` or ``routines/*/rules.json``.
- Atomic Writer is never invoked (``atomic_writer_called`` stays False).
- Rollback Manager / Runtime Commit Executor are NOT implemented here.
- No GUI / SendOrder / Chejan / Broker / SQLite / rules writes.

The plan is purely descriptive: it reports which files would be backed up,
where the backup would land (preview path only), and metadata. No side effects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

PLAN_TYPE = "RUNTIME_BACKUP_PLAN"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_path(value: Any) -> Optional[Path]:
    try:
        if isinstance(value, Path):
            return value
        if isinstance(value, str) and value.strip():
            return Path(value)
    except Exception:
        return None
    return None


def _is_protected_rules_json(p: Path) -> bool:
    # routines/*/rules.json and any rules.json are protected
    return p.name == "rules.json"


def _default_backup_root(commit_id: str) -> Path:
    return Path("runtime_backup_preview") / commit_id


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
    }


def create_runtime_backup_plan(
    commit_id: Optional[str] = None,
    target_files: Optional[Sequence[Any]] = None,
    backup_root: Optional[Any] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Build a preview-only runtime backup plan for a commit.

    Args:
        commit_id: Identifier for the runtime commit this backup belongs to.
        target_files: Iterable of file paths (str or Path) to back up.
        backup_root: Optional root directory for backup previews.
        runtime_snapshot: Optional runtime snapshot reference (not written).
        operator_context: Optional operator context metadata (not written).

    Returns:
        Dict with backup_status (READY/BLOCKED/INVALID), preview_only,
        commit_id, backup_root_preview, backup_targets, backup_metadata,
        safety_flags (all False), issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ---- INVALID base checks ----
    if not commit_id or not str(commit_id).strip():
        issues.append("commit_id is missing or empty")

    if target_files is None:
        issues.append("target_files is missing")
    elif isinstance(target_files, str) or not isinstance(
        target_files, (list, tuple, set, Iterable)
    ):
        issues.append("target_files has invalid type")
    else:
        target_files = list(target_files)
        if len(target_files) == 0:
            issues.append("target_files is empty")

    resolved_targets: list[Path] = []
    if not issues:
        for item in target_files:  # type: ignore[possibly-undefined]
            p = _as_path(item)
            if p is None:
                issues.append(
                    f"target_files item not interpretable as path: {item!r}"
                )
            else:
                resolved_targets.append(p.resolve())

    # protected rules.json check
    if not issues:
        for p in resolved_targets:
            if _is_protected_rules_json(p):
                issues.append(f"protected rules.json target included: {p}")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_root=backup_root,
            resolved_targets=[],
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- BLOCKED checks (no file creation) ----
    for p in resolved_targets:
        if not p.exists():
            issues.append(f"target file does not exist: {p}")

    if issues:
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_root=backup_root,
            resolved_targets=resolved_targets,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- READY ----
    return _build_plan(
        status=STATUS_READY,
        commit_id=commit_id,
        backup_root=backup_root,
        resolved_targets=resolved_targets,
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
    )


def _build_plan(
    *,
    status: str,
    commit_id: Optional[str],
    backup_root: Optional[Any],
    resolved_targets: list[Path],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    root_path = _as_path(backup_root) if backup_root is not None else None
    if root_path is not None:
        root_preview = str(root_path / commit_str)
    else:
        root_preview = str(_default_backup_root(commit_str))

    backup_targets: list[dict[str, Any]] = []
    for p in resolved_targets:
        backup_targets.append(
            {
                "source": str(p),
                "backup_candidate": str(Path(root_preview) / p.name),
                "exists": p.exists(),
            }
        )

    metadata = {
        "plan_type": PLAN_TYPE,
        "commit_id": commit_str,
        "target_count": len(resolved_targets),
        "preview_only": True,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    return {
        "backup_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "backup_root_preview": root_preview,
        "backup_targets": backup_targets,
        "backup_metadata": metadata,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }