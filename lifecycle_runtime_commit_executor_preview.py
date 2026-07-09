# -*- coding: utf-8 -*-
"""Preview-only runtime commit executor.

This module builds an in-memory preview of how a runtime commit executor would
apply reconciliation results to runtime/position/balance state. It never writes
``runtime/*.json`` files, writes SQLite, updates the GUI, calls SendOrder,
calls Chejan, or applies runtime state. Every side-effecting flag is pinned to
``False`` so the payload is safe to inspect before any real commit.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_COMMIT_EXECUTOR_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

RECONCILIATION_READY = "RECONCILIATION_PREVIEW_READY"

POSITION_FIELDS = ("quantity", "filled_quantity", "remaining_quantity", "price")
BALANCE_FIELDS = ("balance", "cash", "available_cash", "available_balance")


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


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _result(
    *,
    status: str,
    commit_execution_plan: dict[str, Any] | None = None,
    executor_preview: dict[str, Any] | None = None,
    atomic_execution_plan: dict[str, Any] | None = None,
    execution_validation: dict[str, Any] | None = None,
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
        "backup_created": False,
        "rollback_executed": False,
        "runtime_apply_called": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "commit_execution_plan": deepcopy(commit_execution_plan or {}),
        "executor_preview": deepcopy(executor_preview or {}),
        "atomic_execution_plan": deepcopy(atomic_execution_plan or {}),
        "execution_validation": deepcopy(execution_validation or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "status": status,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _build_commit_execution_plan(reconciliation: dict[str, Any], now: str) -> dict[str, Any]:
    actions = _as_list(reconciliation.get("reconciliation_actions"))
    mismatches = _as_list(reconciliation.get("mismatch_candidates"))

    runtime_apply_order: list[str] = []
    for action in actions:
        order_id = _text(_as_dict(action).get("order_id"))
        if order_id and order_id not in runtime_apply_order:
            runtime_apply_order.append(order_id)

    position_apply_order: list[str] = []
    balance_apply_order: list[str] = []
    for mismatch in mismatches:
        m = _as_dict(mismatch)
        order_id = _text(m.get("order_id"))
        field = _text(m.get("field"))
        if not order_id:
            continue
        if field in POSITION_FIELDS and order_id not in position_apply_order:
            position_apply_order.append(order_id)
        elif field in BALANCE_FIELDS and order_id not in balance_apply_order:
            balance_apply_order.append(order_id)

    backup_sequence = [
        {
            "step": "BACKUP_RUNTIME_STATE",
            "target": "runtime",
            "preview_only": True,
            "backup_created": False,
        },
        {
            "step": "BACKUP_POSITION_STATE",
            "target": "position",
            "preview_only": True,
            "backup_created": False,
        },
        {
            "step": "BACKUP_BALANCE_STATE",
            "target": "balance",
            "preview_only": True,
            "backup_created": False,
        },
    ]

    rollback_sequence = [
        {
            "step": "ROLLBACK_RUNTIME_STATE",
            "target": "runtime",
            "preview_only": True,
            "rollback_executed": False,
        },
        {
            "step": "ROLLBACK_POSITION_STATE",
            "target": "position",
            "preview_only": True,
            "rollback_executed": False,
        },
        {
            "step": "ROLLBACK_BALANCE_STATE",
            "target": "balance",
            "preview_only": True,
            "rollback_executed": False,
        },
    ]

    return {
        "runtime_apply_order": runtime_apply_order,
        "position_apply_order": position_apply_order,
        "balance_apply_order": balance_apply_order,
        "backup_sequence": backup_sequence,
        "rollback_sequence": rollback_sequence,
    }


def _build_executor_preview(
    commit_plan: dict[str, Any],
    reconciliation: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    runtime_apply_order = _as_list(commit_plan.get("runtime_apply_order"))
    position_apply_order = _as_list(commit_plan.get("position_apply_order"))
    balance_apply_order = _as_list(commit_plan.get("balance_apply_order"))
    backup_sequence = _as_list(commit_plan.get("backup_sequence"))
    rollback_sequence = _as_list(commit_plan.get("rollback_sequence"))

    execution_steps: list[dict[str, Any]] = []
    step_index = 1

    for backup in backup_sequence:
        execution_steps.append({
            "step_index": step_index,
            "step_type": "BACKUP",
            "step_name": _text(backup.get("step")),
            "target": _text(backup.get("target")),
            "would_execute": True,
            "executed": False,
            "preview_only": True,
        })
        step_index += 1

    for order_id in runtime_apply_order:
        execution_steps.append({
            "step_index": step_index,
            "step_type": "RUNTIME_APPLY",
            "step_name": "APPLY_RUNTIME_{}".format(order_id),
            "target": "runtime",
            "order_id": order_id,
            "would_execute": True,
            "executed": False,
            "preview_only": True,
        })
        step_index += 1

    for order_id in position_apply_order:
        execution_steps.append({
            "step_index": step_index,
            "step_type": "POSITION_APPLY",
            "step_name": "APPLY_POSITION_{}".format(order_id),
            "target": "position",
            "order_id": order_id,
            "would_execute": True,
            "executed": False,
            "preview_only": True,
        })
        step_index += 1

    for order_id in balance_apply_order:
        execution_steps.append({
            "step_index": step_index,
            "step_type": "BALANCE_APPLY",
            "step_name": "APPLY_BALANCE_{}".format(order_id),
            "target": "balance",
            "order_id": order_id,
            "would_execute": True,
            "executed": False,
            "preview_only": True,
        })
        step_index += 1

    for rollback in rollback_sequence:
        execution_steps.append({
            "step_index": step_index,
            "step_type": "ROLLBACK",
            "step_name": _text(rollback.get("step")),
            "target": _text(rollback.get("target")),
            "would_execute": True,
            "executed": False,
            "preview_only": True,
        })
        step_index += 1

    execution_summary = {
        "total_steps": len(execution_steps),
        "backup_steps": len(backup_sequence),
        "runtime_apply_steps": len(runtime_apply_order),
        "position_apply_steps": len(position_apply_order),
        "balance_apply_steps": len(balance_apply_order),
        "rollback_steps": len(rollback_sequence),
        "executed_steps": 0,
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "backup_created": False,
        "rollback_executed": False,
        "generated_at": now,
    }

    return {
        "execution_steps": execution_steps,
        "execution_summary": execution_summary,
    }


def _build_atomic_execution_plan(
    commit_plan: dict[str, Any],
    reconciliation: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    runtime_apply_order = _as_list(commit_plan.get("runtime_apply_order"))
    position_apply_order = _as_list(commit_plan.get("position_apply_order"))
    balance_apply_order = _as_list(commit_plan.get("balance_apply_order"))

    transaction_groups = [
        {
            "group_id": "RUNTIME_APPLY_GROUP",
            "group_type": "RUNTIME",
            "order_ids": list(runtime_apply_order),
            "atomic": True,
        },
        {
            "group_id": "POSITION_APPLY_GROUP",
            "group_type": "POSITION",
            "order_ids": list(position_apply_order),
            "atomic": True,
        },
        {
            "group_id": "BALANCE_APPLY_GROUP",
            "group_type": "BALANCE",
            "order_ids": list(balance_apply_order),
            "atomic": True,
        },
    ]

    atomic_boundary = {
        "boundary_type": "ALL_OR_NOTHING",
        "groups": [group["group_id"] for group in transaction_groups],
        "requires_backup_before_apply": True,
        "requires_rollback_on_failure": True,
        "preview_only": True,
    }

    commit_sequence = [
        {"sequence_index": 1, "phase": "BACKUP", "group_id": "RUNTIME_APPLY_GROUP", "action": "BACKUP_RUNTIME_STATE"},
        {"sequence_index": 2, "phase": "APPLY", "group_id": "RUNTIME_APPLY_GROUP", "action": "APPLY_RUNTIME_STATE"},
        {"sequence_index": 3, "phase": "APPLY", "group_id": "POSITION_APPLY_GROUP", "action": "APPLY_POSITION_STATE"},
        {"sequence_index": 4, "phase": "APPLY", "group_id": "BALANCE_APPLY_GROUP", "action": "APPLY_BALANCE_STATE"},
        {"sequence_index": 5, "phase": "VERIFY", "group_id": "ALL", "action": "VERIFY_ATOMIC_COMMIT"},
    ]

    return {
        "transaction_groups": transaction_groups,
        "atomic_boundary": atomic_boundary,
        "commit_sequence": commit_sequence,
    }


def build_runtime_commit_executor_preview(
    reconciliation_preview: Any,
    executor_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only runtime commit executor payload.

    The payload describes how reconciliation results would be committed to
    runtime/position/balance state without performing any side effects.
    """
    reconciliation = _as_dict(reconciliation_preview)
    context = deepcopy(_as_dict(executor_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(reconciliation.get("warnings") or [])

    if not reconciliation:
        issues = ["reconciliation_preview must be a dict"]
        return _result(
            status=STATUS_INVALID,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_INVALID, issues, warnings),
        )

    status = _text(reconciliation.get("status")).upper()
    if status == STATUS_INVALID:
        issues = ["reconciliation preview is INVALID"] + list(reconciliation.get("issues") or [])
        return _result(
            status=STATUS_INVALID,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_INVALID, issues, warnings),
        )
    if status == STATUS_BLOCKED:
        issues = ["reconciliation preview is BLOCKED"] + list(reconciliation.get("issues") or [])
        return _result(
            status=STATUS_BLOCKED,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_BLOCKED, issues, warnings),
        )
    if status != RECONCILIATION_READY:
        issues = ["reconciliation preview status is not {}".format(RECONCILIATION_READY)]
        return _result(
            status=STATUS_INVALID,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_INVALID, issues, warnings),
        )
    if reconciliation.get("preview_only") is not True:
        issues = ["reconciliation preview_only must be true"]
        return _result(
            status=STATUS_INVALID,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_INVALID, issues, warnings),
        )

    review_required = _as_list(reconciliation.get("review_required_items"))
    if review_required:
        issues = ["reconciliation has review_required_items that must be resolved before commit"]
        return _result(
            status=STATUS_BLOCKED,
            issues=issues,
            warnings=warnings,
            now=now,
            execution_validation=_validation(STATUS_BLOCKED, issues, warnings),
        )

    commit_plan = _build_commit_execution_plan(reconciliation, now)
    executor_preview = _build_executor_preview(commit_plan, reconciliation, now)
    atomic_plan = _build_atomic_execution_plan(commit_plan, reconciliation, now)
    validation = _validation(STATUS_READY, [], warnings)

    return _result(
        status=STATUS_READY,
        commit_execution_plan=commit_plan,
        executor_preview=executor_preview,
        atomic_execution_plan=atomic_plan,
        execution_validation=validation,
        issues=[],
        warnings=warnings,
        now=now,
    )
