# -*- coding: utf-8 -*-
"""Preview-only controller payload before runtime state apply.

This layer consumes Atomic Apply Preview output and builds the final controller
preview required before any real runtime/position/balance apply. It never
reads or writes runtime files, writes SQLite, updates GUI state, calls
SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_STATE_APPLY_CONTROLLER_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
ATOMIC_READY = "ATOMIC_APPLY_PREVIEW_READY"


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
    }


def _result(
    *,
    status: str,
    apply_controller_preview: dict[str, Any] | None = None,
    apply_gate_preview: dict[str, Any] | None = None,
    apply_lock_preview: dict[str, Any] | None = None,
    apply_execution_order_preview: dict[str, Any] | None = None,
    controller_validation: dict[str, Any] | None = None,
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
        "lock_acquired": False,
        "apply_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "apply_controller_preview": deepcopy(apply_controller_preview or {}),
        "apply_gate_preview": deepcopy(apply_gate_preview or {}),
        "apply_lock_preview": deepcopy(apply_lock_preview or {}),
        "apply_execution_order_preview": deepcopy(apply_execution_order_preview or {}),
        "controller_validation": deepcopy(controller_validation or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _controller_id(context: dict[str, Any]) -> str:
    return _text(context.get("controller_id")) or "RUNTIME_STATE_APPLY_CONTROLLER_{}".format(uuid4().hex)


def _source_atomic_apply_id(atomic_preview: dict[str, Any]) -> str:
    apply_batch = _as_dict(atomic_preview.get("apply_batch"))
    return _text(apply_batch.get("batch_id")) or _text(atomic_preview.get("atomic_apply_id"))


def _blocked_preview(
    *,
    status: str,
    atomic_preview: dict[str, Any],
    context: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    controller_status = STATUS_BLOCKED if status == STATUS_BLOCKED else STATUS_INVALID
    controller = _build_controller_preview(
        atomic_preview=atomic_preview,
        context=context,
        status=controller_status,
        issues=issues,
    )
    gate = _build_gate_preview(context, ready=False)
    lock = _build_lock_preview(atomic_preview, context)
    order = _build_execution_order_preview(atomic_preview)
    validation = _validation(controller_status, issues, warnings)
    return _result(
        status=controller_status,
        apply_controller_preview=controller,
        apply_gate_preview=gate,
        apply_lock_preview=lock,
        apply_execution_order_preview=order,
        controller_validation=validation,
        issues=issues,
        warnings=warnings,
        now=now,
    )


def _build_controller_preview(
    *,
    atomic_preview: dict[str, Any],
    context: dict[str, Any],
    status: str,
    issues: list[str],
) -> dict[str, Any]:
    ready = status == STATUS_READY
    return {
        "controller_id": _controller_id(context),
        "source_atomic_apply_id": _source_atomic_apply_id(atomic_preview),
        "status": status,
        "ready_to_apply": ready,
        "blocked_reason": "; ".join(issues) if status == STATUS_BLOCKED else "",
        "invalid_reason": "; ".join(issues) if status == STATUS_INVALID else "",
        "preview_only": True,
        "apply_executed": False,
    }


def _build_gate_preview(context: dict[str, Any], *, ready: bool) -> dict[str, Any]:
    return {
        "gate_status": "READY_FOR_OPERATOR_APPROVAL" if ready else "NOT_READY",
        "approval_required": True,
        "approval_token_required": True,
        "operator_review_required": True,
        "approval_token": _text(context.get("approval_token_preview")),
        "preview_only": True,
    }


def _build_lock_preview(atomic_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    lock_key = _text(context.get("lock_key"))
    if not lock_key:
        source_id = _source_atomic_apply_id(atomic_preview) or "UNKNOWN"
        lock_key = "runtime_state_apply:{}".format(source_id)
    return {
        "lock_required": True,
        "lock_key": lock_key,
        "lock_acquired": False,
        "preview_only": True,
    }


def _sequence_actions(atomic_preview: dict[str, Any]) -> list[str]:
    apply_batch = _as_dict(atomic_preview.get("apply_batch"))
    sequence = _as_list(apply_batch.get("commit_sequence"))
    actions = [_text(_as_dict(item).get("action")) for item in sequence]
    return [action for action in actions if action]


def _build_execution_order_preview(atomic_preview: dict[str, Any]) -> dict[str, Any]:
    actions = _sequence_actions(atomic_preview)
    return {
        "backup": [action for action in actions if "BACKUP" in action] or ["BACKUP_RUNTIME_STATE"],
        "runtime_apply": [action for action in actions if "RUNTIME" in action and "APPLY" in action] or ["APPLY_RUNTIME_STATE"],
        "position_apply": [action for action in actions if "POSITION" in action and "APPLY" in action] or ["APPLY_POSITION_STATE"],
        "balance_apply": [action for action in actions if "BALANCE" in action and "APPLY" in action] or ["APPLY_BALANCE_STATE"],
        "verify": [action for action in actions if "VERIFY" in action] or ["VERIFY_ATOMIC_COMMIT"],
        "rollback_on_failure": True,
        "preview_only": True,
        "apply_executed": False,
    }


def _validate_atomic_preview(atomic_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not atomic_preview:
        return STATUS_INVALID, ["atomic_apply_preview must be a dict"]
    status = _text(atomic_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["atomic apply preview is BLOCKED"] + list(atomic_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["atomic apply preview is INVALID"] + list(atomic_preview.get("issues") or [])
    if status != ATOMIC_READY:
        return STATUS_INVALID, ["atomic apply preview status is not {}".format(ATOMIC_READY)]
    if atomic_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["atomic apply preview_only must be true"]
    if atomic_preview.get("atomic_apply_executed") is not False:
        return STATUS_INVALID, ["atomic_apply_executed must be false"]
    if atomic_preview.get("runtime_write") is not False:
        return STATUS_INVALID, ["atomic apply runtime_write must be false"]
    if not _as_dict(atomic_preview.get("apply_batch")):
        return STATUS_INVALID, ["apply_batch is required"]
    if not _as_dict(atomic_preview.get("atomic_boundary_validation")).get("ready"):
        return STATUS_BLOCKED, ["atomic boundary validation is not ready"]
    if not _as_dict(atomic_preview.get("pre_apply_validation")).get("ready"):
        return STATUS_BLOCKED, ["pre-apply validation is not ready"]
    if not _as_dict(atomic_preview.get("post_apply_verification_preview")):
        return STATUS_INVALID, ["post_apply_verification_preview is required"]
    if not _as_dict(atomic_preview.get("rollback_trigger_preview")):
        return STATUS_INVALID, ["rollback_trigger_preview is required"]
    return STATUS_READY, []


def build_runtime_state_apply_controller_preview(
    atomic_apply_preview: Any,
    controller_context: Any = None,
) -> dict[str, Any]:
    """Build final preview-only controller payload before runtime state apply."""
    atomic_preview = _as_dict(atomic_apply_preview)
    context = deepcopy(_as_dict(controller_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(atomic_preview.get("warnings") or [])

    status, issues = _validate_atomic_preview(atomic_preview)
    if status != STATUS_READY:
        return _blocked_preview(
            status=status,
            atomic_preview=atomic_preview,
            context=context,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    controller = _build_controller_preview(
        atomic_preview=atomic_preview,
        context=context,
        status=STATUS_READY,
        issues=[],
    )
    gate = _build_gate_preview(context, ready=True)
    lock = _build_lock_preview(atomic_preview, context)
    order = _build_execution_order_preview(atomic_preview)
    validation = _validation(STATUS_READY, [], warnings)
    return _result(
        status=STATUS_READY,
        apply_controller_preview=controller,
        apply_gate_preview=gate,
        apply_lock_preview=lock,
        apply_execution_order_preview=order,
        controller_validation=validation,
        issues=[],
        warnings=warnings,
        now=now,
    )

