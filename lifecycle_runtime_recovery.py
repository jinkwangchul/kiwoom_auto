# -*- coding: utf-8 -*-
"""Preview-only recovery planning for lifecycle runtime persistence plans.

This module never reads or writes runtime files. It only turns a persistence
plan into recovery candidates, validation details, reconciliation preview, and
a recovery summary that can be reviewed by a later layer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


RECOVERY_TYPE = "LIFECYCLE_RUNTIME_RECOVERY_PREVIEW"
STATUS_READY = "RECOVERY_PREVIEW_READY"
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


def _result(
    *,
    status: str,
    recovery_candidates: list[dict[str, Any]] | None = None,
    recovery_steps: list[dict[str, Any]] | None = None,
    reconciliation_preview: dict[str, Any] | None = None,
    recovery_summary: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "recovery_type": RECOVERY_TYPE,
        "status": status,
        "preview_only": True,
        "recovery_executed": False,
        "runtime_restored": False,
        "snapshot_loaded": False,
        "reconciliation_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
        "recovery_candidates": deepcopy(recovery_candidates or []),
        "recovery_steps": deepcopy(recovery_steps or []),
        "reconciliation_preview": deepcopy(reconciliation_preview or {}),
        "recovery_summary": deepcopy(recovery_summary or {}),
        "validation_result": deepcopy(validation_result or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": status == STATUS_READY,
        "status": status,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _candidate_from_target(target_key: str, target_path: str, backup_path: str, now: str) -> dict[str, Any]:
    return {
        "candidate_id": "RECOVERY_CANDIDATE_{}".format(uuid4().hex),
        "target_key": target_key,
        "target_path": target_path,
        "backup_path_preview": backup_path,
        "candidate_type": "RESTORE_FROM_BACKUP_PREVIEW",
        "snapshot_loaded": False,
        "runtime_restored": False,
        "created_at": now,
    }


def _build_candidates(plan: dict[str, Any], now: str) -> list[dict[str, Any]]:
    rollback_targets = _as_dict(plan.get("rollback_targets"))
    backup_targets = _as_dict(plan.get("backup_targets"))
    keys = sorted(set(rollback_targets) | set(backup_targets))
    candidates: list[dict[str, Any]] = []
    for key in keys:
        target_path = _text(rollback_targets.get(key))
        backup_path = _text(backup_targets.get(key))
        candidates.append(_candidate_from_target(key, target_path, backup_path, now))
    return candidates


def _build_steps(candidates: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        steps.append(
            {
                "step_index": index,
                "step_type": "RESTORE_TARGET_PREVIEW",
                "candidate_id": candidate.get("candidate_id", ""),
                "target_key": candidate.get("target_key", ""),
                "target_path": candidate.get("target_path", ""),
                "backup_path_preview": candidate.get("backup_path_preview", ""),
                "recovery_executed": False,
                "runtime_restored": False,
                "planned_at": now,
            }
        )
    return steps


def _build_reconciliation_preview(plan: dict[str, Any], candidates: list[dict[str, Any]], now: str) -> dict[str, Any]:
    summary = _as_dict(plan.get("persistence_summary"))
    return {
        "preview_type": "LIFECYCLE_RUNTIME_RECONCILIATION_PREVIEW",
        "preview_only": True,
        "reconciliation_executed": False,
        "persistence_id": summary.get("persistence_id", ""),
        "lifecycle_event": summary.get("lifecycle_event", ""),
        "order_id": summary.get("order_id", ""),
        "candidate_count": len(candidates),
        "checks": [
            "verify target file state before restore",
            "verify backup availability before restore",
            "verify projected runtime snapshot after restore",
            "manual review before any real runtime write",
        ],
        "planned_at": now,
    }


def _build_summary(plan: dict[str, Any], candidates: list[dict[str, Any]], steps: list[dict[str, Any]], now: str) -> dict[str, Any]:
    summary = _as_dict(plan.get("persistence_summary"))
    return {
        "status": STATUS_READY,
        "persistence_id": summary.get("persistence_id", ""),
        "lifecycle_event": summary.get("lifecycle_event", ""),
        "order_id": summary.get("order_id", ""),
        "recovery_candidate_count": len(candidates),
        "recovery_step_count": len(steps),
        "preview_only": True,
        "recovery_executed": False,
        "runtime_restored": False,
        "planned_at": now,
    }


def build_runtime_recovery_preview(
    persistence_plan: Any,
    recovery_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only runtime recovery data from a persistence plan."""
    plan = _as_dict(persistence_plan)
    context = deepcopy(_as_dict(recovery_context))
    now = _text(context.get("planned_at")) or _now_text()
    warnings = list(plan.get("warnings") or [])

    if not plan:
        issues = ["persistence_plan must be a dict"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    status = _text(plan.get("status") or _as_dict(plan.get("validation_result")).get("status")).upper()
    if status in {"BLOCKED"}:
        issues = ["persistence plan is BLOCKED"] + list(plan.get("issues") or [])
        return _result(status=STATUS_BLOCKED, issues=issues, warnings=warnings, validation_result=_validation(STATUS_BLOCKED, issues, warnings))
    if status in {"INVALID"}:
        issues = ["persistence plan is INVALID"] + list(plan.get("issues") or [])
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if status and status != "PERSISTENCE_PREVIEW_READY":
        issues = ["persistence plan status is not supported"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    if plan.get("preview_only") is not True:
        issues = ["persistence plan preview_only must be true"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if plan.get("runtime_write") is not False:
        issues = ["persistence plan runtime_write must be false"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    backup_targets = _as_dict(plan.get("backup_targets"))
    rollback_targets = _as_dict(plan.get("rollback_targets"))
    if not backup_targets:
        issues = ["backup_targets are required"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if not rollback_targets:
        issues = ["rollback_targets are required"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    candidates = _build_candidates(plan, now)
    incomplete = [
        candidate.get("target_key", "")
        for candidate in candidates
        if not _text(candidate.get("target_path")) or not _text(candidate.get("backup_path_preview"))
    ]
    if incomplete:
        issues = ["recovery candidate missing target or backup: " + ", ".join(str(item) for item in incomplete)]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    steps = _build_steps(candidates, now)
    reconciliation = _build_reconciliation_preview(plan, candidates, now)
    summary = _build_summary(plan, candidates, steps, now)
    validation = _validation(STATUS_READY, [], warnings)
    return _result(
        status=STATUS_READY,
        recovery_candidates=candidates,
        recovery_steps=steps,
        reconciliation_preview=reconciliation,
        recovery_summary=summary,
        validation_result=validation,
        issues=[],
        warnings=warnings,
    )

