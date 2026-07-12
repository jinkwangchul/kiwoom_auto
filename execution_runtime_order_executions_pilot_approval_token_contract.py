# -*- coding: utf-8 -*-
"""Preview-only approval token contract for the order_executions pilot."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from execution_runtime_order_executions_pilot_approval_gate import (
    APPROVAL_TYPE,
    STATUS_APPROVED as GATE_STATUS_APPROVED,
    STATUS_BLOCKED as GATE_STATUS_BLOCKED,
    STATUS_INVALID as GATE_STATUS_INVALID,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    LOGICAL_TARGET,
    PILOT_TYPE,
)


TOKEN_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_TOKEN"
TOKEN_VERSION = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_TOKEN_V1"
STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


@dataclass(frozen=True)
class ExecutionRuntimeOrderExecutionsPilotApprovalToken:
    token_type: str
    token_version: str
    status: str
    approval_token: str
    approval_fingerprint: str
    logical_target: str
    runtime_target: str
    execution_mode: str
    file_exists: bool
    backup_required: bool
    pilot_snapshot_fingerprint: str
    preview_only: bool
    dry_run_only: bool
    runtime_write: bool
    actual_execution_allowed: bool
    commit_service_called: bool
    approval_gate_snapshot: dict[str, Any]
    pilot_boundary_snapshot: dict[str, Any]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_type": self.token_type,
            "token_version": self.token_version,
            "status": self.status,
            "approval_token": self.approval_token,
            "approval_fingerprint": self.approval_fingerprint,
            "logical_target": self.logical_target,
            "runtime_target": self.runtime_target,
            "execution_mode": self.execution_mode,
            "file_exists": self.file_exists,
            "backup_required": self.backup_required,
            "pilot_snapshot_fingerprint": self.pilot_snapshot_fingerprint,
            "preview_only": self.preview_only,
            "dry_run_only": self.dry_run_only,
            "runtime_write": self.runtime_write,
            "actual_execution_allowed": self.actual_execution_allowed,
            "commit_service_called": self.commit_service_called,
            "approval_gate_snapshot": deepcopy(self.approval_gate_snapshot),
            "pilot_boundary_snapshot": deepcopy(self.pilot_boundary_snapshot),
            "issues": list(self.issues),
            "warnings": list(self.warnings),
        }


def build_execution_runtime_order_executions_pilot_approval_token(
    approval_gate_result: Any,
) -> dict[str, Any]:
    """Build a preview-only approval token from a pilot approval gate result."""
    if not isinstance(approval_gate_result, dict):
        return _token_result(
            status=STATUS_INVALID,
            approval_token="",
            issues=["MALFORMED_APPROVAL_GATE_RESULT"],
        ).to_dict()

    gate = deepcopy(approval_gate_result)
    issues = _as_list(gate.get("issues"))
    warnings = _as_list(gate.get("warnings"))
    pilot_boundary = _as_dict(gate.get("pilot_boundary_snapshot"))
    approval_fingerprint = _text(gate.get("approval_fingerprint"))
    logical_target = _text(gate.get("logical_target"))
    runtime_target = _text(gate.get("runtime_target"))
    execution_mode = _text(gate.get("execution_mode"))
    file_exists = gate.get("file_exists") is True
    backup_required = gate.get("backup_required") is True
    pilot_snapshot_fingerprint = _fingerprint(_pilot_snapshot_payload(pilot_boundary))
    expected_approval_fingerprint = _fingerprint(
        {
            "pilot_boundary": pilot_boundary,
            "logical_target": logical_target,
            "runtime_target": runtime_target,
            "execution_mode": execution_mode,
            "backup_plan": _as_dict(gate.get("backup_plan")),
            "atomic_write_plan": _as_dict(gate.get("atomic_write_plan")),
            "rollback_plan": _as_dict(gate.get("rollback_plan")),
        }
    )

    if gate.get("approval_type") != APPROVAL_TYPE:
        issues.append("INVALID_APPROVAL_TYPE")
    if gate.get("status") != GATE_STATUS_APPROVED or gate.get("production_pilot_approved") is not True:
        issues.append("PILOT_APPROVAL_NOT_APPROVED")
    if gate.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if gate.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if gate.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if gate.get("actual_execution_allowed") is not False:
        issues.append("ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE")
    if gate.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")
    if pilot_boundary.get("pilot_type") != PILOT_TYPE:
        issues.append("INVALID_PILOT_TYPE")
    if logical_target != LOGICAL_TARGET:
        issues.append("PILOT_LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if execution_mode not in {EXECUTION_MODE_INIT, EXECUTION_MODE_APPEND}:
        issues.append("INVALID_EXECUTION_MODE")
    if not approval_fingerprint:
        issues.append("MISSING_APPROVAL_FINGERPRINT")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")

    if _text(pilot_boundary.get("logical_target")) != logical_target:
        issues.append("PILOT_BOUNDARY_LOGICAL_TARGET_MISMATCH")
    if _text(pilot_boundary.get("runtime_target")) != runtime_target:
        issues.append("PILOT_BOUNDARY_RUNTIME_TARGET_MISMATCH")
    if _text(pilot_boundary.get("execution_mode")) != execution_mode:
        issues.append("PILOT_BOUNDARY_EXECUTION_MODE_MISMATCH")

    if approval_fingerprint and approval_fingerprint != expected_approval_fingerprint:
        issues.append("APPROVAL_FINGERPRINT_MISMATCH")

    if pilot_boundary and _fingerprint(_pilot_snapshot_payload(deepcopy(pilot_boundary))) != pilot_snapshot_fingerprint:
        issues.append("PILOT_SNAPSHOT_FINGERPRINT_MISMATCH")

    status = STATUS_APPROVED if not issues else _status_from_issues(issues, gate.get("status"))
    approval_token = (
        _build_token_text(
            approval_fingerprint=approval_fingerprint,
            pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
            logical_target=logical_target,
            runtime_target=runtime_target,
            execution_mode=execution_mode,
        )
        if status == STATUS_APPROVED
        else ""
    )
    return _token_result(
        status=status,
        approval_token=approval_token,
        approval_fingerprint=approval_fingerprint,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        file_exists=file_exists,
        backup_required=backup_required,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
        approval_gate_snapshot=gate,
        pilot_boundary_snapshot=pilot_boundary,
        issues=_dedupe(issues),
        warnings=warnings,
    ).to_dict()


def _token_result(
    *,
    status: str,
    approval_token: str,
    approval_fingerprint: str = "",
    logical_target: str = "",
    runtime_target: str = "",
    execution_mode: str = "",
    file_exists: bool = False,
    backup_required: bool = False,
    pilot_snapshot_fingerprint: str = "",
    approval_gate_snapshot: dict[str, Any] | None = None,
    pilot_boundary_snapshot: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ExecutionRuntimeOrderExecutionsPilotApprovalToken:
    return ExecutionRuntimeOrderExecutionsPilotApprovalToken(
        token_type=TOKEN_TYPE,
        token_version=TOKEN_VERSION,
        status=status,
        approval_token=approval_token,
        approval_fingerprint=approval_fingerprint,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        file_exists=file_exists,
        backup_required=backup_required,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
        preview_only=True,
        dry_run_only=True,
        runtime_write=False,
        actual_execution_allowed=False,
        commit_service_called=False,
        approval_gate_snapshot=deepcopy(approval_gate_snapshot or {}),
        pilot_boundary_snapshot=deepcopy(pilot_boundary_snapshot or {}),
        issues=tuple(issues or ()),
        warnings=tuple(warnings or ()),
    )


def _pilot_snapshot_payload(pilot_boundary: dict[str, Any]) -> dict[str, Any]:
    return {
        "pilot_type": pilot_boundary.get("pilot_type"),
        "status": pilot_boundary.get("status"),
        "pilot_ready": pilot_boundary.get("pilot_ready"),
        "logical_target": pilot_boundary.get("logical_target"),
        "runtime_target": pilot_boundary.get("runtime_target"),
        "execution_mode": pilot_boundary.get("execution_mode"),
        "file_exists": pilot_boundary.get("file_exists"),
        "backup_required": pilot_boundary.get("backup_required"),
        "backup_plan": deepcopy(pilot_boundary.get("backup_plan")) if isinstance(pilot_boundary.get("backup_plan"), dict) else {},
        "atomic_write_plan": deepcopy(pilot_boundary.get("atomic_write_plan")) if isinstance(pilot_boundary.get("atomic_write_plan"), dict) else {},
        "rollback_plan": deepcopy(pilot_boundary.get("rollback_plan")) if isinstance(pilot_boundary.get("rollback_plan"), dict) else {},
        "preconditions": deepcopy(pilot_boundary.get("preconditions")) if isinstance(pilot_boundary.get("preconditions"), list) else [],
    }


def _build_token_text(
    *,
    approval_fingerprint: str,
    pilot_snapshot_fingerprint: str,
    logical_target: str,
    runtime_target: str,
    execution_mode: str,
) -> str:
    digest = _fingerprint(
        {
            "approval_fingerprint": approval_fingerprint,
            "pilot_snapshot_fingerprint": pilot_snapshot_fingerprint,
            "logical_target": logical_target,
            "runtime_target": runtime_target,
            "execution_mode": execution_mode,
        }
    )[:24].upper()
    return f"EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_TOKEN_{digest}"


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _status_from_issues(issues: list[str], gate_status: Any) -> str:
    if gate_status == GATE_STATUS_INVALID:
        return STATUS_INVALID
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _invalid_issue(issue: str) -> bool:
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH")
    return any(marker in issue for marker in markers)
