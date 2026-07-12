# -*- coding: utf-8 -*-
"""Preview-only route validation for order_executions pilot approval tokens."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_order_executions_pilot_approval_token_contract import (
    STATUS_APPROVED as TOKEN_STATUS_APPROVED,
    TOKEN_TYPE,
    TOKEN_VERSION,
    build_execution_runtime_order_executions_pilot_approval_token,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    LOGICAL_TARGET,
)


ROUTE_PREVIEW_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_TOKEN_ROUTE_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def validate_order_executions_pilot_token_route_preview(token_contract: Any) -> dict[str, Any]:
    """Validate a pilot approval token before handing it toward execution routing.

    This adapter does not store, read, consume, or revoke tokens. It rebuilds the
    preview token from the embedded approval gate snapshot and compares the core
    route-binding fields.
    """
    if not isinstance(token_contract, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_TOKEN_CONTRACT"])

    token = deepcopy(token_contract)
    issues = _as_list(token.get("issues"))
    warnings = _as_list(token.get("warnings"))
    approval_gate_snapshot = _as_dict(token.get("approval_gate_snapshot"))
    pilot_snapshot = _as_dict(token.get("pilot_boundary_snapshot"))

    approval_token = _text(token.get("approval_token"))
    approval_fingerprint = _text(token.get("approval_fingerprint"))
    logical_target = _text(token.get("logical_target"))
    runtime_target = _text(token.get("runtime_target"))
    execution_mode = _text(token.get("execution_mode"))
    pilot_snapshot_fingerprint = _text(token.get("pilot_snapshot_fingerprint"))

    if token.get("token_type") != TOKEN_TYPE:
        issues.append("INVALID_TOKEN_TYPE")
    if token.get("token_version") != TOKEN_VERSION:
        issues.append("INVALID_TOKEN_VERSION")
    if token.get("status") != TOKEN_STATUS_APPROVED:
        issues.append("TOKEN_CONTRACT_NOT_APPROVED")
    if not approval_token:
        issues.append("MISSING_APPROVAL_TOKEN")
    if not approval_fingerprint:
        issues.append("MISSING_APPROVAL_FINGERPRINT")
    if logical_target != LOGICAL_TARGET:
        issues.append("LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")
    if execution_mode not in {EXECUTION_MODE_INIT, EXECUTION_MODE_APPEND}:
        issues.append("INVALID_EXECUTION_MODE")
    if not pilot_snapshot_fingerprint:
        issues.append("MISSING_PILOT_SNAPSHOT_FINGERPRINT")

    if token.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if token.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if token.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if token.get("actual_execution_allowed") is not False:
        issues.append("ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE")
    if token.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")

    if _text(pilot_snapshot.get("logical_target")) != logical_target:
        issues.append("PILOT_SNAPSHOT_LOGICAL_TARGET_MISMATCH")
    if _text(pilot_snapshot.get("runtime_target")) != runtime_target:
        issues.append("PILOT_SNAPSHOT_RUNTIME_TARGET_MISMATCH")
    if _text(pilot_snapshot.get("execution_mode")) != execution_mode:
        issues.append("PILOT_SNAPSHOT_EXECUTION_MODE_MISMATCH")

    rebuilt = (
        build_execution_runtime_order_executions_pilot_approval_token(approval_gate_snapshot)
        if approval_gate_snapshot
        else {}
    )
    if not rebuilt:
        issues.append("MISSING_APPROVAL_GATE_SNAPSHOT")
    elif rebuilt.get("status") != TOKEN_STATUS_APPROVED:
        issues.append("REBUILT_TOKEN_CONTRACT_NOT_APPROVED")
    else:
        for field in (
            "approval_token",
            "approval_fingerprint",
            "logical_target",
            "runtime_target",
            "execution_mode",
            "pilot_snapshot_fingerprint",
        ):
            if _text(rebuilt.get(field)) != _text(token.get(field)):
                issues.append(f"{field.upper()}_MISMATCH")

    status = STATUS_READY if not issues else _status_from_issues(issues)
    return _result(
        status=status,
        token_valid=status == STATUS_READY,
        execution_route_ready=status == STATUS_READY,
        approval_token=approval_token,
        approval_fingerprint=approval_fingerprint,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
        approval_gate_snapshot=approval_gate_snapshot,
        pilot_boundary_snapshot=pilot_snapshot,
        issues=_dedupe(issues),
        warnings=_dedupe(warnings),
    )


def _result(
    *,
    status: str,
    token_valid: bool = False,
    execution_route_ready: bool = False,
    approval_token: str = "",
    approval_fingerprint: str = "",
    logical_target: str = "",
    runtime_target: str = "",
    execution_mode: str = "",
    pilot_snapshot_fingerprint: str = "",
    approval_gate_snapshot: dict[str, Any] | None = None,
    pilot_boundary_snapshot: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "route_preview_type": ROUTE_PREVIEW_TYPE,
        "status": status,
        "token_valid": token_valid,
        "execution_route_ready": execution_route_ready,
        "approval_token": approval_token,
        "approval_fingerprint": approval_fingerprint,
        "logical_target": logical_target,
        "runtime_target": runtime_target,
        "execution_mode": execution_mode,
        "pilot_snapshot_fingerprint": pilot_snapshot_fingerprint,
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "actual_execution_allowed": False,
        "commit_service_called": False,
        "token_stored": False,
        "token_consumed": False,
        "token_deleted": False,
        "backup_executed": False,
        "rollback_executed": False,
        "approval_gate_snapshot": deepcopy(approval_gate_snapshot or {}),
        "pilot_boundary_snapshot": deepcopy(pilot_boundary_snapshot or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _status_from_issues(issues: list[Any]) -> str:
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _invalid_issue(issue: Any) -> bool:
    text = str(issue)
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH", "ALREADY_CALLED")
    return any(marker in text for marker in markers)


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
