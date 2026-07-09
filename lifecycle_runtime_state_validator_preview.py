# -*- coding: utf-8 -*-
"""Preview-only validation before runtime state writer execution.

This module validates Runtime State Writer Preview payloads and write sequence
without reading or writing runtime files, writing SQLite, updating GUI state,
calling SendOrder, or connecting Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_STATE_VALIDATOR_PREVIEW"
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


def _validator_result(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
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
    runtime_validation_preview: dict[str, Any] | None = None,
    position_validation_preview: dict[str, Any] | None = None,
    balance_validation_preview: dict[str, Any] | None = None,
    sequence_validation_preview: dict[str, Any] | None = None,
    validator_result: dict[str, Any] | None = None,
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
        "validation_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "runtime_validation_preview": deepcopy(runtime_validation_preview or {}),
        "position_validation_preview": deepcopy(position_validation_preview or {}),
        "balance_validation_preview": deepcopy(balance_validation_preview or {}),
        "sequence_validation_preview": deepcopy(sequence_validation_preview or {}),
        "validator_result": deepcopy(validator_result or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _item(name: str, ok: bool, reason: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "reason": reason,
        "preview_only": True,
    }


def _payload_validation(preview: dict[str, Any], payload_key: str, targets_key: str, summary_key: str, write_flag: str) -> tuple[list[dict[str, Any]], list[str]]:
    payload = _as_dict(preview.get(payload_key))
    targets = _as_dict(preview.get(targets_key))
    summary = _as_dict(preview.get(summary_key))
    items = [
        _item("{} exists".format(payload_key), bool(payload), "{} is required".format(payload_key)),
        _item("{} exists".format(targets_key), bool(targets), "{} is required".format(targets_key)),
        _item("{} exists".format(summary_key), bool(summary), "{} is required".format(summary_key)),
        _item("{} false".format(write_flag), payload.get(write_flag) is False, "{} must be false".format(write_flag)),
        _item("target_path exists", bool(_text(targets.get("target_path"))), "target_path is required"),
        _item("summary write not called", summary.get("write_called") is False, "summary.write_called must be false"),
    ]
    issues = [entry["reason"] for entry in items if not entry["ok"]]
    return items, issues


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [item for item in items if item.get("ok") is not True]
    return {
        "total": len(items),
        "passed": len(items) - len(failed),
        "failed": len(failed),
        "ready": not failed,
        "preview_only": True,
    }


def _runtime_validation(writer_preview: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    items, issues = _payload_validation(
        _as_dict(writer_preview.get("runtime_write_preview")),
        "runtime_payload",
        "runtime_targets",
        "runtime_summary",
        "runtime_write",
    )
    return {
        "runtime_validation_items": items,
        "runtime_validation_summary": _summary(items),
    }, issues


def _position_validation(writer_preview: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    items, issues = _payload_validation(
        _as_dict(writer_preview.get("position_write_preview")),
        "position_payload",
        "position_targets",
        "position_summary",
        "position_write",
    )
    return {
        "position_validation_items": items,
        "position_validation_summary": _summary(items),
    }, issues


def _balance_validation(writer_preview: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    items, issues = _payload_validation(
        _as_dict(writer_preview.get("balance_write_preview")),
        "balance_payload",
        "balance_targets",
        "balance_summary",
        "balance_write",
    )
    return {
        "balance_validation_items": items,
        "balance_validation_summary": _summary(items),
    }, issues


def _sequence_validation(writer_preview: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    sequence = _as_dict(writer_preview.get("write_sequence_preview"))
    runtime = _as_list(sequence.get("runtime"))
    position = _as_list(sequence.get("position"))
    balance = _as_list(sequence.get("balance"))
    verification = _as_list(sequence.get("verification"))
    items = [
        _item("runtime sequence exists", bool(runtime), "runtime sequence is required"),
        _item("position sequence exists", bool(position), "position sequence is required"),
        _item("balance sequence exists", bool(balance), "balance sequence is required"),
        _item("verification sequence exists", bool(verification), "verification sequence is required"),
        _item("writer not called", sequence.get("writer_called") is False, "writer_called must be false"),
        _item("sequence preview only", sequence.get("preview_only") is True, "sequence preview_only must be true"),
    ]
    dependency_validation = {
        "runtime_before_position": True,
        "position_before_balance": True,
        "balance_before_verification": True,
        "preview_only": True,
    }
    payload = {
        "execution_sequence": {
            "runtime": deepcopy(runtime),
            "position": deepcopy(position),
            "balance": deepcopy(balance),
            "verification": deepcopy(verification),
        },
        "dependency_validation": dependency_validation,
        "verification_order": deepcopy(verification),
        "sequence_validation_items": items,
        "sequence_validation_summary": _summary(items),
    }
    issues = [entry["reason"] for entry in items if not entry["ok"]]
    return payload, issues


def _validate_writer_preview(writer_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not writer_preview:
        return STATUS_INVALID, ["writer_preview must be a dict"]
    status = _text(writer_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["writer preview is BLOCKED"] + list(writer_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["writer preview is INVALID"] + list(writer_preview.get("issues") or [])
    if status != STATUS_READY:
        return STATUS_INVALID, ["writer preview status is not READY"]
    if writer_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["writer preview_only must be true"]
    for flag in ("runtime_write", "position_write", "balance_write"):
        if writer_preview.get(flag) is not False:
            return STATUS_INVALID, ["writer {} must be false".format(flag)]
    validation = _as_dict(writer_preview.get("writer_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["writer_validation.ready must be true"]
    return STATUS_READY, []


def build_runtime_state_validator_preview(
    writer_preview: Any,
    validator_context: Any = None,
) -> dict[str, Any]:
    """Validate writer preview payloads and write sequence in memory only."""
    writer = _as_dict(writer_preview)
    context = deepcopy(_as_dict(validator_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(writer.get("warnings") or [])

    status, base_issues = _validate_writer_preview(writer)
    if status != STATUS_READY:
        result = _validator_result(status, base_issues, warnings)
        return _result(status=status, validator_result=result, issues=base_issues, warnings=warnings, now=now)

    runtime_preview, runtime_issues = _runtime_validation(writer)
    position_preview, position_issues = _position_validation(writer)
    balance_preview, balance_issues = _balance_validation(writer)
    sequence_preview, sequence_issues = _sequence_validation(writer)
    issues = runtime_issues + position_issues + balance_issues + sequence_issues
    final_status = STATUS_READY if not issues else STATUS_INVALID
    result = _validator_result(final_status, issues, warnings)
    return _result(
        status=final_status,
        runtime_validation_preview=runtime_preview,
        position_validation_preview=position_preview,
        balance_validation_preview=balance_preview,
        sequence_validation_preview=sequence_preview,
        validator_result=result,
        issues=issues,
        warnings=warnings,
        now=now,
    )

