# -*- coding: utf-8 -*-
"""Preview-only runtime state writer payload builder.

This module consumes Runtime State Apply Controller Preview and builds the
runtime/position/balance payload previews and write order. It never writes
runtime files, writes SQLite, updates GUI state, calls SendOrder, or connects
Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_STATE_WRITER_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


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
    runtime_write_preview: dict[str, Any] | None = None,
    position_write_preview: dict[str, Any] | None = None,
    balance_write_preview: dict[str, Any] | None = None,
    write_sequence_preview: dict[str, Any] | None = None,
    writer_validation: dict[str, Any] | None = None,
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
        "writer_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "runtime_write_preview": deepcopy(runtime_write_preview or {}),
        "position_write_preview": deepcopy(position_write_preview or {}),
        "balance_write_preview": deepcopy(balance_write_preview or {}),
        "write_sequence_preview": deepcopy(write_sequence_preview or {}),
        "writer_validation": deepcopy(writer_validation or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _targets(context: dict[str, Any], key: str, default_path: str) -> dict[str, str]:
    target_context = _as_dict(context.get(key))
    target_path = _text(target_context.get("target_path")) or _text(target_context.get("path")) or default_path
    return {"target_path": target_path}


def _summary(payload: dict[str, Any], targets: dict[str, str], payload_key: str) -> dict[str, Any]:
    return {
        "payload_key": payload_key,
        "target_path": targets.get("target_path", ""),
        "preview_only": True,
        "write_called": False,
        "field_count": len(payload),
    }


def _controller_payload(controller_preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "controller": deepcopy(_as_dict(controller_preview.get("apply_controller_preview"))),
        "gate": deepcopy(_as_dict(controller_preview.get("apply_gate_preview"))),
        "lock": deepcopy(_as_dict(controller_preview.get("apply_lock_preview"))),
        "execution_order": deepcopy(_as_dict(controller_preview.get("apply_execution_order_preview"))),
    }


def _runtime_preview(controller_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "payload_type": "RUNTIME_STATE_WRITE_PAYLOAD_PREVIEW",
        "writer_payload_id": "RUNTIME_PAYLOAD_{}".format(uuid4().hex),
        "source_controller_id": _text(_as_dict(controller_preview.get("apply_controller_preview")).get("controller_id")),
        "controller_payload": _controller_payload(controller_preview),
        "runtime_write": False,
    }
    targets = _targets(context, "runtime_targets", "runtime/runtime_snapshot.json")
    return {
        "runtime_payload": payload,
        "runtime_targets": targets,
        "runtime_summary": _summary(payload, targets, "runtime_payload"),
    }


def _position_preview(controller_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "payload_type": "POSITION_WRITE_PAYLOAD_PREVIEW",
        "writer_payload_id": "POSITION_PAYLOAD_{}".format(uuid4().hex),
        "source_controller_id": _text(_as_dict(controller_preview.get("apply_controller_preview")).get("controller_id")),
        "position_apply_order": deepcopy(_as_list(_as_dict(controller_preview.get("apply_execution_order_preview")).get("position_apply"))),
        "position_write": False,
    }
    targets = _targets(context, "position_targets", "runtime/position_view.json")
    return {
        "position_payload": payload,
        "position_targets": targets,
        "position_summary": _summary(payload, targets, "position_payload"),
    }


def _balance_preview(controller_preview: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "payload_type": "BALANCE_WRITE_PAYLOAD_PREVIEW",
        "writer_payload_id": "BALANCE_PAYLOAD_{}".format(uuid4().hex),
        "source_controller_id": _text(_as_dict(controller_preview.get("apply_controller_preview")).get("controller_id")),
        "balance_apply_order": deepcopy(_as_list(_as_dict(controller_preview.get("apply_execution_order_preview")).get("balance_apply"))),
        "balance_write": False,
    }
    targets = _targets(context, "balance_targets", "runtime/balance_view.json")
    return {
        "balance_payload": payload,
        "balance_targets": targets,
        "balance_summary": _summary(payload, targets, "balance_payload"),
    }


def _write_sequence(controller_preview: dict[str, Any]) -> dict[str, Any]:
    order = _as_dict(controller_preview.get("apply_execution_order_preview"))
    return {
        "runtime": deepcopy(_as_list(order.get("runtime_apply"))),
        "position": deepcopy(_as_list(order.get("position_apply"))),
        "balance": deepcopy(_as_list(order.get("balance_apply"))),
        "verification": deepcopy(_as_list(order.get("verify"))),
        "preview_only": True,
        "writer_called": False,
    }


def _validate_controller(controller_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not controller_preview:
        return STATUS_INVALID, ["controller_preview must be a dict"]
    status = _text(controller_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["controller preview is BLOCKED"] + list(controller_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["controller preview is INVALID"] + list(controller_preview.get("issues") or [])
    if status != STATUS_READY:
        return STATUS_INVALID, ["controller preview status is not READY"]
    if controller_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["controller preview_only must be true"]
    if controller_preview.get("runtime_write") is not False:
        return STATUS_INVALID, ["controller runtime_write must be false"]
    validation = _as_dict(controller_preview.get("controller_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["controller_validation.ready must be true"]
    controller = _as_dict(controller_preview.get("apply_controller_preview"))
    if controller.get("ready_to_apply") is not True:
        return STATUS_BLOCKED, ["apply_controller_preview.ready_to_apply must be true"]
    if not _as_dict(controller_preview.get("apply_execution_order_preview")):
        return STATUS_INVALID, ["apply_execution_order_preview is required"]
    return STATUS_READY, []


def build_runtime_state_writer_preview(
    controller_preview: Any,
    writer_context: Any = None,
) -> dict[str, Any]:
    """Build runtime/position/balance writer payload previews only."""
    controller = _as_dict(controller_preview)
    context = deepcopy(_as_dict(writer_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(controller.get("warnings") or [])

    status, issues = _validate_controller(controller)
    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        return _result(status=status, writer_validation=validation, issues=issues, warnings=warnings, now=now)

    runtime = _runtime_preview(controller, context)
    position = _position_preview(controller, context)
    balance = _balance_preview(controller, context)
    sequence = _write_sequence(controller)
    validation = _validation(STATUS_READY, [], warnings)
    return _result(
        status=STATUS_READY,
        runtime_write_preview=runtime,
        position_write_preview=position,
        balance_write_preview=balance,
        write_sequence_preview=sequence,
        writer_validation=validation,
        issues=[],
        warnings=warnings,
        now=now,
    )

