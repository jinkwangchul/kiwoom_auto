# -*- coding: utf-8 -*-
"""Preview bridge from order_executions pilot token routing to execution gate input."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_order_executions_pilot_approval_token_contract import (
    build_execution_runtime_order_executions_pilot_approval_token,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    LOGICAL_TARGET,
)
from execution_runtime_order_executions_pilot_token_route_preview import (
    ROUTE_PREVIEW_TYPE,
    STATUS_READY as ROUTE_STATUS_READY,
)
from runtime_commit_approval_token_store import TOKEN_SCOPE
from runtime_commit_execution_gate import build_execution_plan_hash


BRIDGE_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_EXECUTION_GATE_BRIDGE_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def build_order_executions_pilot_execution_gate_bridge_preview(route_preview: Any) -> dict[str, Any]:
    """Build deterministic execution gate input candidates without calling the gate."""
    if not isinstance(route_preview, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_ROUTE_PREVIEW"])

    route = deepcopy(route_preview)
    issues = _as_list(route.get("issues"))
    warnings = _as_list(route.get("warnings"))
    approval_gate_snapshot = _as_dict(route.get("approval_gate_snapshot"))
    source_token = (
        build_execution_runtime_order_executions_pilot_approval_token(approval_gate_snapshot)
        if approval_gate_snapshot
        else {}
    )

    approval_token = _text(route.get("approval_token"))
    approval_fingerprint = _text(route.get("approval_fingerprint"))
    logical_target = _text(route.get("logical_target"))
    runtime_target = _text(route.get("runtime_target"))
    execution_mode = _text(route.get("execution_mode"))
    pilot_snapshot_fingerprint = _text(route.get("pilot_snapshot_fingerprint"))

    if route.get("route_preview_type") != ROUTE_PREVIEW_TYPE:
        issues.append("INVALID_ROUTE_PREVIEW_TYPE")
    if route.get("status") != ROUTE_STATUS_READY:
        issues.append("ROUTE_PREVIEW_NOT_READY")
    if route.get("status") == STATUS_INVALID:
        issues.append("ROUTE_PREVIEW_INVALID")
    if route.get("token_valid") is not True:
        issues.append("ROUTE_TOKEN_NOT_VALID")
    if route.get("execution_route_ready") is not True:
        issues.append("ROUTE_EXECUTION_NOT_READY")
    if logical_target != LOGICAL_TARGET:
        issues.append("LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")
    if execution_mode not in {EXECUTION_MODE_INIT, EXECUTION_MODE_APPEND}:
        issues.append("INVALID_EXECUTION_MODE")
    if not approval_token:
        issues.append("MISSING_APPROVAL_TOKEN")
    if not approval_fingerprint:
        issues.append("MISSING_APPROVAL_FINGERPRINT")
    if not pilot_snapshot_fingerprint:
        issues.append("MISSING_PILOT_SNAPSHOT_FINGERPRINT")

    if route.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if route.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if route.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if route.get("actual_execution_allowed") is not False:
        issues.append("ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE")
    if route.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")
    if route.get("token_stored") is True:
        issues.append("TOKEN_ALREADY_STORED")
    if route.get("token_consumed") is True:
        issues.append("TOKEN_ALREADY_CONSUMED")

    if not source_token:
        issues.append("MISSING_APPROVAL_GATE_SNAPSHOT")
    elif source_token.get("status") != "APPROVED":
        issues.append("SOURCE_APPROVAL_TOKEN_NOT_APPROVED")
    else:
        for field in (
            "approval_token",
            "approval_fingerprint",
            "logical_target",
            "runtime_target",
            "execution_mode",
            "pilot_snapshot_fingerprint",
        ):
            if _text(source_token.get(field)) != _text(route.get(field)):
                issues.append(f"{field.upper()}_MISMATCH")

    commit_id_preview = _commit_id_preview(
        approval_token=approval_token,
        approval_fingerprint=approval_fingerprint,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
    )
    execution_plan_preview = _execution_plan_preview(
        commit_id=commit_id_preview,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        approval_fingerprint=approval_fingerprint,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
    )
    plan_hash_preview = build_execution_plan_hash(execution_plan_preview) if commit_id_preview else ""
    execution_token_preview = _execution_token_preview(
        token_id=approval_token,
        commit_id=commit_id_preview,
        plan_hash=plan_hash_preview,
    )
    approval_context_preview = _approval_context_preview(
        commit_id=commit_id_preview,
        plan_hash=plan_hash_preview,
        approval_fingerprint=approval_fingerprint,
    )
    execution_gate_input_preview = {
        "commit_id": commit_id_preview,
        "execution_plan": deepcopy(execution_plan_preview),
        "approval_context": deepcopy(approval_context_preview),
        "execution_token": deepcopy(execution_token_preview),
        "expected_plan_hash": plan_hash_preview,
        "operator_context": None,
    }

    status = STATUS_READY if not issues else _status_from_issues(issues)
    return _result(
        status=status,
        execution_gate_input_ready=status == STATUS_READY,
        commit_id_preview=commit_id_preview,
        plan_hash_preview=plan_hash_preview,
        execution_token_preview=execution_token_preview,
        approval_context_preview=approval_context_preview,
        execution_plan_preview=execution_plan_preview,
        execution_gate_input_preview=execution_gate_input_preview,
        source_approval_token=approval_token,
        approval_fingerprint=approval_fingerprint,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        pilot_snapshot_fingerprint=pilot_snapshot_fingerprint,
        route_preview_snapshot=route,
        issues=_dedupe(issues),
        warnings=_dedupe(warnings),
    )


def _result(
    *,
    status: str,
    execution_gate_input_ready: bool = False,
    commit_id_preview: str = "",
    plan_hash_preview: str = "",
    execution_token_preview: dict[str, Any] | None = None,
    approval_context_preview: dict[str, Any] | None = None,
    execution_plan_preview: dict[str, Any] | None = None,
    execution_gate_input_preview: dict[str, Any] | None = None,
    source_approval_token: str = "",
    approval_fingerprint: str = "",
    logical_target: str = "",
    runtime_target: str = "",
    execution_mode: str = "",
    pilot_snapshot_fingerprint: str = "",
    route_preview_snapshot: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "bridge_type": BRIDGE_TYPE,
        "status": status,
        "execution_gate_input_ready": execution_gate_input_ready,
        "commit_id_preview": commit_id_preview,
        "plan_hash_preview": plan_hash_preview,
        "execution_token_preview": deepcopy(execution_token_preview or {}),
        "approval_context_preview": deepcopy(approval_context_preview or {}),
        "execution_plan_preview": deepcopy(execution_plan_preview or {}),
        "execution_gate_input_preview": deepcopy(execution_gate_input_preview or {}),
        "source_approval_token": source_approval_token,
        "approval_fingerprint": approval_fingerprint,
        "logical_target": logical_target,
        "runtime_target": runtime_target,
        "execution_mode": execution_mode,
        "pilot_snapshot_fingerprint": pilot_snapshot_fingerprint,
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "actual_execution_allowed": False,
        "token_stored": False,
        "token_consumed": False,
        "execution_gate_called": False,
        "commit_service_called": False,
        "backup_executed": False,
        "rollback_executed": False,
        "route_preview_snapshot": deepcopy(route_preview_snapshot or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _execution_plan_preview(
    *,
    commit_id: str,
    logical_target: str,
    runtime_target: str,
    execution_mode: str,
    approval_fingerprint: str,
    pilot_snapshot_fingerprint: str,
) -> dict[str, Any]:
    return {
        "executor_status": "READY",
        "preview_only": True,
        "runtime_write": False,
        "actual_execution": False,
        "commit_id": commit_id,
        "execution_plan": {
            "commit_id": commit_id,
            "execution_mode": "PREVIEW",
            "pilot_execution_mode": execution_mode,
            "logical_target": logical_target,
            "runtime_target": runtime_target,
            "executable": True,
            "rollback_required": False,
            "protected_target_violation": False,
            "actual_execution_performed": False,
        },
        "execution_steps": [
            {
                "step_index": index,
                "step_name": name,
                "component": component,
                "required": required,
                "callable_invoked": False,
                "execution_performed": False,
                "issues": [],
                "warnings": [],
            }
            for index, (name, component, required) in enumerate(
                [
                    ("VALIDATE_BOUNDARY", "order_executions_pilot_token_route_preview", True),
                    ("PREPARE_BACKUP", "order_executions_pilot_boundary_backup_plan", True),
                    ("PREPARE_ATOMIC_WRITE", "order_executions_pilot_boundary_atomic_write_plan", True),
                    ("VERIFY_COMMIT", "order_executions_pilot_validation_preview", True),
                    ("EVALUATE_ROLLBACK", "order_executions_pilot_boundary_rollback_plan", False),
                    ("BUILD_AUDIT_RECORD", "order_executions_pilot_audit_preview", True),
                    ("COMPLETE", "order_executions_pilot_bridge_preview", True),
                ],
                start=1,
            )
        ],
        "state_machine": {"current_state": "READY_TO_EXECUTE", "terminal_state": "READY_TO_EXECUTE"},
        "source_statuses": {
            "pilot_token_route_preview": "READY",
            "logical_target": logical_target,
            "runtime_target": runtime_target,
            "execution_mode": execution_mode,
            "approval_fingerprint": approval_fingerprint,
            "pilot_snapshot_fingerprint": pilot_snapshot_fingerprint,
        },
        "issues": [],
        "warnings": [],
        "safety_flags": {},
    }


def _approval_context_preview(*, commit_id: str, plan_hash: str, approval_fingerprint: str) -> dict[str, Any]:
    return {
        "approved": True,
        "approved_commit_id": commit_id,
        "approved_plan_hash": plan_hash,
        "approved_by": "order_executions_pilot_approval_token",
        "approval_reason": f"preview-only pilot approval token fingerprint {approval_fingerprint}",
        "approval_scope": "RUNTIME_COMMIT",
        "single_use": True,
        "preview_only": True,
    }


def _execution_token_preview(*, token_id: str, commit_id: str, plan_hash: str) -> dict[str, Any]:
    return {
        "token_id": token_id,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "scope": TOKEN_SCOPE,
        "issued_for": "order_executions_pilot_execution_gate_preview",
        "single_use": True,
        "consumed": False,
        "preview_only": True,
    }


def _commit_id_preview(
    *,
    approval_token: str,
    approval_fingerprint: str,
    pilot_snapshot_fingerprint: str,
) -> str:
    if not approval_token or not approval_fingerprint or not pilot_snapshot_fingerprint:
        return ""
    return "order-executions-pilot-" + _text(approval_token).rsplit("_", 1)[-1].lower()


def _status_from_issues(issues: list[Any]) -> str:
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _invalid_issue(issue: Any) -> bool:
    text = str(issue)
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH", "ALREADY")
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
