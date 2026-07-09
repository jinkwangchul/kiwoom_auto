# -*- coding: utf-8 -*-
"""Preview-only file writer before runtime transaction commit.

This module converts Runtime Transaction Preview results into detailed
file target, candidate, backup requirements, and write order previews for
runtime files. It never writes runtime files, writes SQLite, updates GUI,
calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_FILE_WRITER_PREVIEW"
STATUS_READY = "READY"
STATUS_FILE_WRITER_READY = "FILE_WRITER_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

TRANSACTION_PREVIEW_READY = "TRANSACTION_PREVIEW_READY"


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


def _is_ready(status: str) -> bool:
    return status in (STATUS_READY, STATUS_FILE_WRITER_READY)


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": _is_ready(status),
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = _is_ready(status)
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "rejection_reason": "; ".join(issues) if not approved else "",
        "approval_reason": "writer preflight validation ready" if approved else "",
        "file_write_allowed": False,
        "file_write_called": False,
        "preview_only": True,
    }


def _empty_file_target() -> dict[str, Any]:
    return {
        "runtime_targets": [],
        "position_targets": [],
        "balance_targets": [],
        "audit_targets": [],
        "preview_only": True,
    }


def _target(name: str, target_path: str, *candidates) -> dict[str, Any]:
    return {
        "target_name": name,
        "target_path": target_path,
        "requires_backup": True,
        "candidate_ids": list(candidates),
        "preview_only": True,
    }


def _build_file_target_preview(transaction_preview: dict[str, Any]) -> dict[str, Any]:
    transaction_targets = _as_dict(transaction_preview.get("transaction_targets"))
    writer_options = _as_dict(transaction_preview.get("writer_options"))

    targets = []

    runtime_path = writer_options.get("runtime_target_path", "runtime/runtime_snapshot.json")
    runtime_candidates = _as_list(transaction_preview.get("runtime_write_candidates"))
    targets.append(_target("runtime", runtime_path, *runtime_candidates))

    position_path = writer_options.get("position_target_path", "runtime/position_view.json")
    position_candidates = _as_list(transaction_preview.get("position_write_candidates"))
    targets.append(_target("position", position_path, *position_candidates))

    balance_path = writer_options.get("balance_target_path", "runtime/balance_view.json")
    balance_candidates = _as_list(transaction_preview.get("balance_write_candidates"))
    targets.append(_target("balance", balance_path, *balance_candidates))

    audit_path = writer_options.get("audit_target_path", "runtime/audit.log")
    audit_candidates = _as_list(transaction_preview.get("audit_write_candidates"))
    targets.append(_target("audit", audit_path, *audit_candidates))

    return {
        "file_target_preview": {
            "runtime_targets": [_target("runtime", runtime_path, *runtime_candidates)],
            "position_targets": [_target("position", position_path, *position_candidates)],
            "balance_targets": [_target("balance", balance_path, *balance_candidates)],
            "audit_targets": [_target("audit", audit_path, *audit_candidates)],
            "preview_only": True,
        },
        "write_candidates_by_target": {
            "runtime": runtime_candidates,
            "position": position_candidates,
            "balance": balance_candidates,
            "audit": audit_candidates,
        },
        "preview_only": True,
    }


def _build_write_candidate_preview(transaction_preview: dict[str, Any]) -> dict[str, Any]:
    candidates = {
        "runtime_write_candidates": _as_list(transaction_preview.get("runtime_write_candidates")),
        "position_write_candidates": _as_list(transaction_preview.get("position_write_candidates")),
        "balance_write_candidates": _as_list(transaction_preview.get("balance_write_candidates")),
        "audit_write_candidates": _as_list(transaction_preview.get("audit_write_candidates")),
    }

    return {
        "write_candidate_preview": {
            "runtime_write_candidates": candidates["runtime_write_candidates"],
            "position_write_candidates": candidates["position_write_candidates"],
            "balance_write_candidates": candidates["balance_write_candidates"],
            "audit_write_candidates": candidates["audit_write_candidates"],
            "candidate_count": len(candidates["runtime_write_candidates"]) +
                              len(candidates["position_write_candidates"]) +
                              len(candidates["balance_write_candidates"]) +
                              len(candidates["audit_write_candidates"]),
            "preview_only": True,
        },
        "candidate_summary": {
            "total_runtime_candidates": len(candidates["runtime_write_candidates"]),
            "total_position_candidates": len(candidates["position_write_candidates"]),
            "total_balance_candidates": len(candidates["balance_write_candidates"]),
            "total_audit_candidates": len(candidates["audit_write_candidates"]),
            "target_types_count": 4,
            "preview_only": True,
        },
        "preview_only": True,
    }


def _build_backup_requirement_preview(transaction_preview: dict[str, Any]) -> dict[str, Any]:
    backup_options = _as_dict(transaction_preview.get("backup_options"))

    targets = []
    if backup_options.get("backup_runtime", True):
        targets.append({"target": "runtime", "path": "runtime/runtime_snapshot.json"})
    if backup_options.get("backup_position", True):
        targets.append({"target": "position", "path": "runtime/position_view.json"})
    if backup_options.get("backup_balance", True):
        targets.append({"target": "balance", "path": "runtime/balance_view.json"})
    if backup_options.get("backup_audit", False):
        targets.append({"target": "audit", "path": "runtime/audit.log"})

    return {
        "backup_requirement_preview": {
            "backup_required": True,
            "backup_created": False,
            "backup_targets": targets,
            "backup_sequence": [
                {"step": 1, "action": "BACKUP_RUNTIME", "target": "runtime", "preview_only": True},
                {"step": 2, "action": "BACKUP_POSITION", "target": "position", "preview_only": True},
                {"step": 3, "action": "BACKUP_BALANCE", "target": "balance", "preview_only": True},
            ],
            "preview_only": True,
        },
        "backup_summary": {
            "requires_backup": True,
            "target_count": len(targets),
            "has_backup_targets": True,
            "preview_only": True,
        },
        "preview_only": True,
    }


def _build_write_sequence_preview(transaction_preview: dict[str, Any]) -> dict[str, Any]:
    steps = [
        {"step_index": 1, "action": "VERIFY_TRANSACTION", "target": "validation", "preview_only": True},
        {"step_index": 2, "action": "BACKUP_TARGETS", "target": "runtime", "preview_only": True},
        {"step_index": 3, "action": "WRITE_RUNTIME", "target": "runtime", "preview_only": True},
        {"step_index": 4, "action": "WRITE_POSITION", "target": "position", "preview_only": True},
        {"step_index": 5, "action": "WRITE_BALANCE", "target": "balance", "preview_only": True},
        {"step_index": 6, "action": "WRITE_AUDIT", "target": "audit", "preview_only": True},
        {"step_index": 7, "action": "FINAL_VERIFY", "target": "validation", "preview_only": True},
    ]

    return {
        "write_sequence_preview": {
            "sequence_type": "RUNTIME_FILE_WRITE_PREVIEW_SEQUENCE",
            "ordered_steps": steps,
            "write_executed": False,
            "preview_only": True,
        },
        "execution_summary": {
            "total_steps": len(steps),
            "target_write_steps": 4,
            "validation_steps": 2,
            "preview_only": True,
        },
        "preview_only": True,
    }


def _validate_transaction_preview(transaction_preview: dict[str, Any]) -> tuple[str, list[str]]:
    if not transaction_preview:
        return STATUS_INVALID, ["transaction_preview must be a dict"]

    status = _text(transaction_preview.get("status")).upper()
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["transaction preview is BLOCKED"] + list(transaction_preview.get("issues") or [])
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["transaction preview is INVALID"] + list(transaction_preview.get("issues") or [])
    if status not in (STATUS_READY, TRANSACTION_PREVIEW_READY, STATUS_FILE_WRITER_READY):
        return STATUS_INVALID, ["transaction preview status is not READY"]

    if transaction_preview.get("preview_only") is not True:
        return STATUS_INVALID, ["transaction preview_only must be true"]

    for flag in ("runtime_write", "position_write", "balance_write", "audit_write"):
        if transaction_preview.get(flag) is not False:
            return STATUS_INVALID, ["transaction {} must be false".format(flag)]

    return STATUS_READY, []


def _result(
    *,
    status: str,
    file_target_preview: dict[str, Any] | None = None,
    write_candidate_preview: dict[str, Any] | None = None,
    backup_requirement_preview: dict[str, Any] | None = None,
    write_sequence_preview: dict[str, Any] | None = None,
    writer_preflight_validation: dict[str, Any] | None = None,
    final_writer_decision: dict[str, Any] | None = None,
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
        "audit_write": False,
        "writer_executed": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "file_target_preview": deepcopy(file_target_preview or {}),
        "write_candidate_preview": deepcopy(write_candidate_preview or {}),
        "backup_requirement_preview": deepcopy(backup_requirement_preview or {}),
        "write_sequence_preview": deepcopy(write_sequence_preview or {}),
        "writer_preflight_validation": deepcopy(writer_preflight_validation or {}),
        "final_writer_decision": deepcopy(final_writer_decision or {}),
        "generated_at": now or _now_text(),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_runtime_file_writer_preview(
    transaction_preview: Any,
    writer_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only file writer information from transaction preview."""
    transaction = _as_dict(transaction_preview)
    context = deepcopy(_as_dict(writer_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(transaction.get("warnings") or [])

    status, issues = _validate_transaction_preview(transaction)

    if status != STATUS_READY:
        validation = _validation(status, issues, warnings)
        decision = _decision(status, issues)
        return _result(
            status=status,
            writer_preflight_validation=validation,
            final_writer_decision=decision,
            issues=issues,
            warnings=warnings,
            now=now,
        )

    file_target = _build_file_target_preview(transaction)
    write_candidate = _build_write_candidate_preview(transaction)
    backup_requirement = _build_backup_requirement_preview(transaction)
    write_sequence = _build_write_sequence_preview(transaction)
    validation = _validation(STATUS_FILE_WRITER_READY, [], warnings)
    decision = _decision(STATUS_FILE_WRITER_READY, [])

    return _result(
        status=STATUS_FILE_WRITER_READY,
        file_target_preview=file_target["file_target_preview"],
        write_candidate_preview=write_candidate["write_candidate_preview"],
        backup_requirement_preview=backup_requirement["backup_requirement_preview"],
        write_sequence_preview=write_sequence["write_sequence_preview"],
        writer_preflight_validation=validation,
        final_writer_decision=decision,
        issues=[],
        warnings=warnings,
        now=now,
    )