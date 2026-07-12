# -*- coding: utf-8 -*-
"""Final preview authorization before an order_executions pilot execution gate call."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_order_executions_pilot_boundary import LOGICAL_TARGET
from execution_runtime_order_executions_pilot_execution_gate_dry_run_adapter import (
    ADAPTER_TYPE as DRY_RUN_ADAPTER_TYPE,
    STATUS_READY as DRY_RUN_STATUS_READY,
)
from runtime_commit_execution_gate import (
    _contains_protected_rules_target,
    _dedupe,
    _find_safety_violations,
    _text,
    build_execution_plan_hash,
)


FINAL_AUTHORIZATION_TYPE = "EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_FINAL_AUTHORIZATION_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def build_order_executions_pilot_final_authorization_preview(dry_run_adapter_result: Any) -> dict[str, Any]:
    """Build the final read-only authorization preview for a future gate call."""
    if not isinstance(dry_run_adapter_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_DRY_RUN_ADAPTER_RESULT"])

    dry_run = deepcopy(dry_run_adapter_result)
    issues = _as_list(dry_run.get("issues"))
    warnings = _as_list(dry_run.get("warnings"))
    commit_id = _text(dry_run.get("commit_id"))
    plan_hash = _text(dry_run.get("plan_hash"))
    execution_token = _as_dict(dry_run.get("execution_token"))
    approval_context = _as_dict(dry_run.get("approval_context"))
    execution_plan = _as_dict(dry_run.get("execution_plan"))
    nested_plan = _as_dict(execution_plan.get("execution_plan"))
    source_statuses = _as_dict(execution_plan.get("source_statuses"))
    logical_target = _text(nested_plan.get("logical_target") or source_statuses.get("logical_target"))
    runtime_target = _text(nested_plan.get("runtime_target") or source_statuses.get("runtime_target"))
    execution_mode = _text(nested_plan.get("pilot_execution_mode") or source_statuses.get("execution_mode"))

    if dry_run.get("adapter_type") != DRY_RUN_ADAPTER_TYPE:
        issues.append("INVALID_DRY_RUN_ADAPTER_TYPE")
    if dry_run.get("status") != DRY_RUN_STATUS_READY or dry_run.get("execution_gate_dry_run_ready") is not True:
        issues.append("DRY_RUN_ADAPTER_NOT_READY")
    if dry_run.get("status") == STATUS_INVALID:
        issues.append("DRY_RUN_ADAPTER_INVALID")
    if not commit_id:
        issues.append("MISSING_COMMIT_ID")
    if not plan_hash:
        issues.append("MISSING_PLAN_HASH")
    if not execution_token:
        issues.append("MISSING_EXECUTION_TOKEN")
    if not approval_context:
        issues.append("MISSING_APPROVAL_CONTEXT")
    if not execution_plan:
        issues.append("MISSING_EXECUTION_PLAN")
    if logical_target != LOGICAL_TARGET:
        issues.append("LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")
    if not execution_mode:
        issues.append("MISSING_EXECUTION_MODE")

    if dry_run.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if dry_run.get("dry_run_only") is not True:
        issues.append("DRY_RUN_ONLY_REQUIRED")
    if dry_run.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if dry_run.get("actual_execution_allowed") is not False:
        issues.append("ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE")
    if dry_run.get("execution_gate_called") is True:
        issues.append("EXECUTION_GATE_ALREADY_CALLED")
    if dry_run.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")
    if dry_run.get("token_stored") is True:
        issues.append("TOKEN_ALREADY_STORED")
    if dry_run.get("token_consumed") is True:
        issues.append("TOKEN_ALREADY_CONSUMED")

    recomputed_plan_hash = build_execution_plan_hash(execution_plan) if execution_plan else ""
    if plan_hash and recomputed_plan_hash and plan_hash != recomputed_plan_hash:
        issues.append("PLAN_HASH_MISMATCH")
    if _text(execution_plan.get("commit_id")) != commit_id:
        issues.append("EXECUTION_PLAN_COMMIT_ID_MISMATCH")
    if _text(execution_token.get("commit_id")) != commit_id:
        issues.append("EXECUTION_TOKEN_COMMIT_ID_MISMATCH")
    if _text(execution_token.get("plan_hash")) != plan_hash:
        issues.append("EXECUTION_TOKEN_PLAN_HASH_MISMATCH")
    if _text(approval_context.get("approved_commit_id")) != commit_id:
        issues.append("APPROVAL_CONTEXT_COMMIT_ID_MISMATCH")
    if _text(approval_context.get("approved_plan_hash")) != plan_hash:
        issues.append("APPROVAL_CONTEXT_PLAN_HASH_MISMATCH")
    if approval_context.get("approved") is not True:
        issues.append("APPROVAL_CONTEXT_NOT_APPROVED")
    if _text(execution_token.get("scope")) != "RUNTIME_COMMIT_EXECUTION":
        issues.append("EXECUTION_TOKEN_SCOPE_INVALID")
    if execution_token.get("single_use") is not True:
        issues.append("EXECUTION_TOKEN_SINGLE_USE_REQUIRED")
    if execution_token.get("consumed") is True:
        issues.append("EXECUTION_TOKEN_ALREADY_CONSUMED")

    for source_name, value in (
        ("execution_plan", execution_plan),
        ("approval_context", approval_context),
        ("execution_token", execution_token),
    ):
        issues.extend(_find_safety_violations(value, source_name))
        if _contains_protected_rules_target(value):
            issues.append(f"{source_name}: protected routines rules.json target included")

    issues = _dedupe(issues)
    warnings = _dedupe(warnings)
    status = STATUS_READY if not issues else _status_from_issues(issues)
    return _result(
        status=status,
        final_authorization_ready=status == STATUS_READY,
        commit_id=commit_id,
        plan_hash=plan_hash,
        execution_token=execution_token,
        approval_context=approval_context,
        execution_plan=execution_plan,
        logical_target=logical_target,
        runtime_target=runtime_target,
        execution_mode=execution_mode,
        dry_run_adapter_snapshot=dry_run,
        issues=issues,
        warnings=warnings,
    )


def _result(
    *,
    status: str,
    final_authorization_ready: bool = False,
    commit_id: str = "",
    plan_hash: str = "",
    execution_token: dict[str, Any] | None = None,
    approval_context: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    logical_target: str = "",
    runtime_target: str = "",
    execution_mode: str = "",
    dry_run_adapter_snapshot: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "final_authorization_type": FINAL_AUTHORIZATION_TYPE,
        "status": status,
        "final_authorization_ready": final_authorization_ready,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "execution_token": deepcopy(execution_token or {}),
        "approval_context": deepcopy(approval_context or {}),
        "execution_plan": deepcopy(execution_plan or {}),
        "logical_target": logical_target,
        "runtime_target": runtime_target,
        "execution_mode": execution_mode,
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "actual_execution_allowed": False,
        "execution_gate_called": False,
        "commit_service_called": False,
        "token_stored": False,
        "token_consumed": False,
        "backup_executed": False,
        "rollback_executed": False,
        "dry_run_adapter_snapshot": deepcopy(dry_run_adapter_snapshot or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _status_from_issues(issues: list[Any]) -> str:
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _invalid_issue(issue: Any) -> bool:
    text = str(issue)
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH", "ALREADY", "safety flag")
    return any(marker in text for marker in markers)


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []
