# -*- coding: utf-8 -*-
"""Preview-only transaction boundary before runtime file writer.

This module consumes Runtime Apply Engine result and builds transaction
boundary, token, apply group, rollback plan, validation, and final decision
previews. It never issues real tokens, creates backups, rolls back, writes
files, writes SQLite, updates GUI state, calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_TRANSACTION_PREVIEW"
STATUS_READY = "TRANSACTION_PREVIEW_READY"
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
        "preview_only": True,
    }


def _transaction_boundary(status: str, engine_result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "boundary_id": _text(context.get("boundary_id")) or "RUNTIME_APPLY_TRANSACTION_{}".format(uuid4().hex),
        "boundary_type": "RUNTIME_APPLY_TRANSACTION",
        "source_engine_run_id": _text(_as_dict(engine_result.get("engine_execution_plan")).get("engine_run_id")),
        "status": status,
        "atomic": True,
        "all_or_nothing": True,
        "requires_backup": True,
        "requires_rollback": True,
        "preview_only": True,
        "transaction_executed": False,
    }


def _transaction_token_preview(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "token_preview_id": _text(context.get("transaction_token_preview_id"))
        or "TRANSACTION_TOKEN_PREVIEW_{}".format(uuid4().hex),
        "token_required": True,
        "token_issued": False,
        "token_consumed": False,
        "preview_only": True,
    }


def _apply_group_preview(engine_result: dict[str, Any]) -> dict[str, Any]:
    file_write = _as_dict(engine_result.get("file_write_plan_preview"))
    return {
        "runtime_group": deepcopy(_as_dict(file_write.get("runtime_file_write_preview"))),
        "position_group": deepcopy(_as_dict(file_write.get("position_file_write_preview"))),
        "balance_group": deepcopy(_as_dict(file_write.get("balance_file_write_preview"))),
        "file_write_group": {
            "file_write_plan_preview": deepcopy(file_write),
            "file_write_called": False,
            "runtime_write": False,
            "position_write": False,
            "balance_write": False,
        },
        "preview_only": True,
    }


def _rollback_plan_preview(engine_result: dict[str, Any]) -> dict[str, Any]:
    file_write = _as_dict(engine_result.get("file_write_plan_preview"))
    return {
        "rollback_required_on_failure": True,
        "rollback_executed": False,
        "rollback_source": deepcopy(_as_dict(file_write.get("rollback_preview"))),
        "backup_source": deepcopy(_as_dict(file_write.get("backup_preview"))),
        "preview_only": True,
    }


def _final_transaction_decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "approval_reason": "engine result is ENGINE_READY and transaction preview validation passed" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "transaction_executed": False,
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    transaction_boundary: dict[str, Any],
    transaction_token_preview: dict[str, Any],
    apply_group_preview: dict[str, Any],
    rollback_plan_preview: dict[str, Any],
    pre_transaction_validation: dict[str, Any],
    final_transaction_decision: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "transaction_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "transaction_boundary": deepcopy(transaction_boundary),
        "transaction_token_preview": deepcopy(transaction_token_preview),
        "apply_group_preview": deepcopy(apply_group_preview),
        "rollback_plan_preview": deepcopy(rollback_plan_preview),
        "pre_transaction_validation": deepcopy(pre_transaction_validation),
        "final_transaction_decision": deepcopy(final_transaction_decision),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_engine_result(engine_result: dict[str, Any]) -> tuple[str, list[str]]:
    if not engine_result:
        return STATUS_INVALID, ["engine_result must be a dict"]

    engine_status = _text(engine_result.get("status")).upper()
    upstream_issues = list(engine_result.get("issues") or [])
    if engine_status == "BLOCKED":
        return STATUS_BLOCKED, ["engine result is BLOCKED"] + upstream_issues
    if engine_status == "INVALID":
        return STATUS_INVALID, ["engine result is INVALID"] + upstream_issues
    if engine_status != "ENGINE_READY":
        return STATUS_INVALID, ["engine result status is not ENGINE_READY"]

    if engine_result.get("preview_only") is not True:
        return STATUS_INVALID, ["engine result preview_only must be true"]
    for flag in (
        "runtime_write",
        "position_write",
        "balance_write",
        "file_write_called",
        "backup_created",
        "rollback_executed",
        "gui_update_called",
        "send_order_called",
        "chejan_called",
    ):
        if engine_result.get(flag) is not False:
            return STATUS_INVALID, ["engine result {} must be false".format(flag)]
    if engine_result.get("engine_executed") is not False:
        return STATUS_INVALID, ["engine result engine_executed must be false"]

    execution_plan = _as_dict(engine_result.get("engine_execution_plan"))
    if not execution_plan or execution_plan.get("ready") is not True:
        return STATUS_INVALID, ["engine_execution_plan.ready must be true"]

    transaction_plan = _as_dict(engine_result.get("transaction_plan_preview"))
    if not transaction_plan or transaction_plan.get("ready") is not True:
        return STATUS_INVALID, ["transaction_plan_preview.ready must be true"]

    file_write_plan = _as_dict(engine_result.get("file_write_plan_preview"))
    if not file_write_plan or file_write_plan.get("ready") is not True:
        return STATUS_INVALID, ["file_write_plan_preview.ready must be true"]
    if file_write_plan.get("file_write_called") is not False:
        return STATUS_INVALID, ["file_write_plan_preview.file_write_called must be false"]

    final_decision = _as_dict(engine_result.get("final_engine_decision"))
    if final_decision.get("approved_for_future_engine") is not True:
        return STATUS_BLOCKED, ["final_engine_decision.approved_for_future_engine must be true"]

    engine_validation = _as_dict(engine_result.get("engine_validation"))
    if engine_validation.get("ready") is not True:
        return STATUS_BLOCKED, ["engine_validation.ready must be true"]

    return STATUS_READY, []


def build_runtime_transaction_preview(
    engine_result: Any,
    transaction_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only transaction boundary from Runtime Apply Engine result."""
    engine = deepcopy(_as_dict(engine_result))
    context = deepcopy(_as_dict(transaction_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(engine.get("warnings") or [])

    status, issues = _validate_engine_result(engine)
    boundary = _transaction_boundary(status, engine, context)
    token = _transaction_token_preview(context)
    apply_group = _apply_group_preview(engine) if status == STATUS_READY else {
        "runtime_group": {},
        "position_group": {},
        "balance_group": {},
        "file_write_group": {},
        "preview_only": True,
    }
    rollback = _rollback_plan_preview(engine)
    validation = _validation(status, issues, warnings)
    decision = _final_transaction_decision(status, issues)
    return _result(
        status=status,
        transaction_boundary=boundary,
        transaction_token_preview=token,
        apply_group_preview=apply_group,
        rollback_plan_preview=rollback,
        pre_transaction_validation=validation,
        final_transaction_decision=decision,
        issues=issues,
        warnings=warnings,
        now=now,
    )
