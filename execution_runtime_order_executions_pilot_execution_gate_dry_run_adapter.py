# -*- coding: utf-8 -*-
"""Dry-run adapter for order_executions pilot execution gate bridge previews."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_order_executions_pilot_execution_gate_bridge_preview import (
    BRIDGE_TYPE,
    STATUS_READY as BRIDGE_STATUS_READY,
)
from runtime_commit_execution_gate import (
    STATUS_APPROVED as GATE_STATUS_APPROVED,
    STATUS_BLOCKED as GATE_STATUS_BLOCKED,
    STATUS_INVALID as GATE_STATUS_INVALID,
    _contains_protected_rules_target,
    _dedupe,
    _find_safety_violations,
    _messages,
    _status_from_plan,
    _text,
    _validate_steps,
    build_execution_plan_hash,
)


ADAPTER_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_EXECUTION_GATE_DRY_RUN_ADAPTER"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def evaluate_order_executions_pilot_execution_gate_dry_run(bridge_preview: Any) -> dict[str, Any]:
    """Reproduce runtime execution gate validation without calling the gate."""
    if not isinstance(bridge_preview, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_BRIDGE_PREVIEW"])

    bridge = deepcopy(bridge_preview)
    issues = _as_list(bridge.get("issues"))
    warnings = _as_list(bridge.get("warnings"))

    if bridge.get("bridge_type") != BRIDGE_TYPE:
        issues.append("INVALID_BRIDGE_TYPE")
    if bridge.get("status") != BRIDGE_STATUS_READY:
        issues.append("BRIDGE_PREVIEW_NOT_READY")
    if bridge.get("status") == STATUS_INVALID:
        issues.append("BRIDGE_PREVIEW_INVALID")
    if bridge.get("execution_gate_input_ready") is not True:
        issues.append("BRIDGE_EXECUTION_GATE_INPUT_NOT_READY")
    if bridge.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if bridge.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if bridge.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if bridge.get("actual_execution_allowed") is not False:
        issues.append("ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE")
    if bridge.get("execution_gate_called") is True:
        issues.append("EXECUTION_GATE_ALREADY_CALLED")
    if bridge.get("token_stored") is True:
        issues.append("TOKEN_ALREADY_STORED")
    if bridge.get("token_consumed") is True:
        issues.append("TOKEN_ALREADY_CONSUMED")
    if bridge.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")

    gate_input = _as_dict(bridge.get("execution_gate_input_preview"))
    commit_id = _text(gate_input.get("commit_id") or bridge.get("commit_id_preview"))
    execution_plan = _as_dict(gate_input.get("execution_plan"))
    approval_context = _as_dict(gate_input.get("approval_context"))
    execution_token = _as_dict(gate_input.get("execution_token"))
    expected_plan_hash = _text(gate_input.get("expected_plan_hash") or bridge.get("plan_hash_preview"))

    if not gate_input:
        issues.append("MISSING_EXECUTION_GATE_INPUT_PREVIEW")
    if not commit_id:
        issues.append("commit_id is missing or empty")
    if not execution_plan:
        issues.append("execution_plan must be a dict")
    if not approval_context:
        issues.append("approval_context is missing")
    if not execution_token:
        issues.append("execution_token is missing")

    plan_hash = build_execution_plan_hash(execution_plan) if execution_plan else ""
    if _text(bridge.get("commit_id_preview")) != commit_id:
        issues.append("bridge commit_id_preview mismatch")
    if _text(bridge.get("plan_hash_preview")) != expected_plan_hash:
        issues.append("bridge plan_hash_preview mismatch")
    if expected_plan_hash and plan_hash and expected_plan_hash != plan_hash:
        issues.append("expected_plan_hash mismatch")

    if execution_plan:
        issues.extend(_validate_execution_plan(execution_plan, commit_id, plan_hash, expected_plan_hash))
        warnings.extend(_messages(execution_plan, "warnings", "execution_plan"))
    if approval_context:
        issues.extend(_validate_approval_context(approval_context, commit_id, plan_hash))
    if execution_token:
        issues.extend(_validate_execution_token(execution_token, commit_id, plan_hash))

    for source_name, value in (
        ("execution_plan", execution_plan),
        ("approval_context", approval_context),
        ("execution_token", execution_token),
        ("operator_context", gate_input.get("operator_context")),
    ):
        if value is None:
            continue
        issues.extend(_find_safety_violations(value, source_name))
        if _contains_protected_rules_target(value):
            issues.append(f"{source_name}: protected routines rules.json target included")

    issues = _dedupe(issues)
    warnings = _dedupe(warnings)
    gate_status = _gate_status_from_issues(issues)
    status = STATUS_READY if gate_status == GATE_STATUS_APPROVED else gate_status
    return _result(
        status=status,
        execution_gate_dry_run_ready=status == STATUS_READY,
        commit_id=commit_id,
        plan_hash=plan_hash,
        execution_token=execution_token,
        approval_context=approval_context,
        execution_plan=execution_plan,
        bridge_preview_snapshot=bridge,
        issues=issues,
        warnings=warnings,
    )


def _validate_execution_plan(
    execution_plan: dict[str, Any],
    commit_id: str,
    plan_hash: str,
    expected_plan_hash: str,
) -> list[str]:
    issues: list[str] = []
    if _text(execution_plan.get("commit_id")) != commit_id:
        issues.append("execution_plan commit_id mismatch")

    plan_status = _status_from_plan(execution_plan)
    if plan_status == GATE_STATUS_INVALID:
        issues.append("execution_plan status is INVALID")
    elif plan_status == GATE_STATUS_BLOCKED:
        issues.append("execution_plan status is not READY")

    nested_plan = execution_plan.get("execution_plan") if isinstance(execution_plan.get("execution_plan"), dict) else {}
    state_machine = execution_plan.get("state_machine") if isinstance(execution_plan.get("state_machine"), dict) else {}
    final_state = state_machine.get("terminal_state") or state_machine.get("current_state")
    if final_state != "READY_TO_EXECUTE":
        issues.append("state_machine final state is not READY_TO_EXECUTE")
    if nested_plan.get("rollback_required") is True:
        issues.append("execution_plan rollback_required is true")

    step_issues, _step_warnings = _validate_steps(execution_plan)
    issues.extend(step_issues)
    issues.extend(_messages(execution_plan, "issues", "execution_plan"))
    if not expected_plan_hash:
        issues.append("expected_plan_hash is missing")
    elif expected_plan_hash != plan_hash:
        issues.append("expected_plan_hash mismatch")
    return issues


def _validate_approval_context(approval_context: dict[str, Any], commit_id: str, plan_hash: str) -> list[str]:
    issues: list[str] = []
    if approval_context.get("approved") is not True:
        issues.append("approval_context approved must be true")
    if _text(approval_context.get("approved_commit_id")) != commit_id:
        issues.append("approval_context approved_commit_id mismatch")
    if _text(approval_context.get("approval_scope")) != "RUNTIME_COMMIT":
        issues.append("approval_context approval_scope is invalid")
    if _text(approval_context.get("approved_plan_hash")) != plan_hash:
        issues.append("approval_context approved_plan_hash mismatch")
    if not _text(approval_context.get("approved_by")):
        issues.append("approval_context approved_by is missing")
    if not _text(approval_context.get("approval_reason")):
        issues.append("approval_context approval_reason is missing")
    return issues


def _validate_execution_token(execution_token: dict[str, Any], commit_id: str, plan_hash: str) -> list[str]:
    issues: list[str] = []
    if _text(execution_token.get("commit_id")) != commit_id:
        issues.append("execution_token commit_id mismatch")
    if _text(execution_token.get("plan_hash")) != plan_hash:
        issues.append("execution_token plan_hash mismatch")
    if _text(execution_token.get("scope")) != "RUNTIME_COMMIT_EXECUTION":
        issues.append("execution_token scope is invalid")
    if execution_token.get("single_use") is not True:
        issues.append("execution_token single_use must be true")
    if execution_token.get("consumed") is True:
        issues.append("execution_token consumed must be false")
    return issues


def _result(
    *,
    status: str,
    execution_gate_dry_run_ready: bool = False,
    commit_id: str = "",
    plan_hash: str = "",
    execution_token: dict[str, Any] | None = None,
    approval_context: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    bridge_preview_snapshot: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "execution_gate_dry_run_ready": execution_gate_dry_run_ready,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "execution_token": deepcopy(execution_token or {}),
        "approval_context": deepcopy(approval_context or {}),
        "execution_plan": deepcopy(execution_plan or {}),
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "actual_execution_allowed": False,
        "execution_gate_called": False,
        "token_stored": False,
        "token_consumed": False,
        "commit_service_called": False,
        "backup_executed": False,
        "rollback_executed": False,
        "bridge_preview_snapshot": deepcopy(bridge_preview_snapshot or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _gate_status_from_issues(issues: list[Any]) -> str:
    invalid_markers = (
        "mismatch",
        "scope is invalid",
        "status is INVALID",
        "order is invalid",
        "duplicate",
        "protected routines rules.json",
        "must be a dict",
        "safety flag",
        "MALFORMED",
        "INVALID",
        "MISSING",
        "REQUIRED",
        "ALREADY",
    )
    if any(marker in str(issue) for issue in issues for marker in invalid_markers):
        return STATUS_INVALID
    if issues:
        return STATUS_BLOCKED
    return GATE_STATUS_APPROVED


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []
