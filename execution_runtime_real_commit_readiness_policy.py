# -*- coding: utf-8 -*-
"""Preview-only policy for opening real execution runtime commits.

This module only decides whether a real project-runtime commit may be opened.
It never writes runtime files, creates directories, calls commit services,
commits queues, calls SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from execution_runtime_allowlist import OPERATION_PREVIEW, validate_runtime_target


POLICY_TYPE = "EXECUTION_RUNTIME_REAL_COMMIT_READINESS_POLICY"
STATUS_READY = "READY_TO_OPEN_RUNTIME_COMMIT"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _project_runtime_root() -> Path:
    return (Path(__file__).resolve().parent / "runtime").resolve(strict=False)


def _under_project_runtime(path_value: Any) -> bool:
    path_text = _text(path_value)
    if not path_text:
        return False
    target = Path(path_text).resolve(strict=False)
    try:
        target.relative_to(_project_runtime_root())
    except ValueError:
        return False
    return True


def _result(
    *,
    status: str,
    runtime_commit_allowed: bool,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    allowlist_decisions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "runtime_commit_allowed": runtime_commit_allowed,
        "runtime_write": False,
        "preview_only": True,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "allowlist_decisions": deepcopy(allowlist_decisions or {}),
    }


def evaluate_execution_runtime_real_commit_readiness(
    *,
    runtime_api_result: Any,
    commit_plan_orchestrator_result: Any,
    order_executions_path: Any,
    order_locks_path: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
    logical_target: Any = "order_executions",
    allowlist_operation: Any = OPERATION_PREVIEW,
) -> dict[str, Any]:
    """Evaluate whether real project-runtime commit may be opened."""
    if not isinstance(runtime_api_result, dict):
        return _result(
            status=STATUS_INVALID,
            runtime_commit_allowed=False,
            issues=["MALFORMED_RUNTIME_API_RESULT"],
        )
    if not isinstance(commit_plan_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            runtime_commit_allowed=False,
            issues=["MALFORMED_COMMIT_PLAN_ORCHESTRATOR_RESULT"],
        )

    order_executions_text = _text(order_executions_path)
    order_locks_text = _text(order_locks_path)
    if not order_executions_text:
        return _result(
            status=STATUS_INVALID,
            runtime_commit_allowed=False,
            issues=["MISSING_ORDER_EXECUTIONS_PATH"],
        )
    if not order_locks_text:
        return _result(
            status=STATUS_INVALID,
            runtime_commit_allowed=False,
            issues=["MISSING_ORDER_LOCKS_PATH"],
        )

    confirmation_flags = _as_dict(confirmations)
    env_flags = _as_dict(environment_flags)
    order_executions_is_project_runtime = _under_project_runtime(order_executions_text)
    order_locks_is_project_runtime = _under_project_runtime(order_locks_text)
    project_runtime_target = order_executions_is_project_runtime or order_locks_is_project_runtime
    runtime_root = _project_runtime_root()
    allowlist_decision = validate_runtime_target(
        logical_target,
        runtime_root=runtime_root,
        operation=allowlist_operation,
    ).to_dict()
    allowlist_decisions = {"runtime_target": allowlist_decision}

    required_confirmations = {
        "manual_execution_runtime_commit_confirmed": confirmation_flags.get(
            "manual_execution_runtime_commit_confirmed"
        )
        is True,
        "manual_runtime_file_write_confirmed": confirmation_flags.get(
            "manual_runtime_file_write_confirmed"
        )
        is True,
    }
    environment_checks = {
        "real_runtime_commit_enabled": env_flags.get("real_runtime_commit_enabled") is True,
        "allow_project_runtime_commit": env_flags.get("allow_project_runtime_commit") is True,
        "order_executions_is_project_runtime": order_executions_is_project_runtime,
        "order_locks_is_project_runtime": order_locks_is_project_runtime,
        "runtime_allowlist_valid": allowlist_decision.get("allowed") is True,
    }

    issues: list[str] = []
    runtime_status = runtime_api_result.get("status")
    commit_status = commit_plan_orchestrator_result.get("status")

    if runtime_status != "READY":
        issues.append("RUNTIME_API_RESULT_NOT_READY")

    if commit_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            runtime_commit_allowed=False,
            issues=["COMMIT_PLAN_ORCHESTRATOR_INVALID"],
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
            allowlist_decisions=allowlist_decisions,
        )
    if commit_status != "READY":
        issues.append("COMMIT_PLAN_ORCHESTRATOR_NOT_READY")

    if commit_plan_orchestrator_result.get("commit_ready") is not True:
        issues.append("COMMIT_READY_IS_NOT_TRUE")

    if allowlist_decision.get("allowed") is not True:
        reason = (
            allowlist_decision.get("blocked_reason")
            or allowlist_decision.get("reason")
            or allowlist_decision.get("status")
        )
        issues.append(f"RUNTIME_ALLOWLIST_BLOCKED: {reason}")

    if project_runtime_target and not environment_checks["allow_project_runtime_commit"]:
        issues.append("PROJECT_RUNTIME_COMMIT_NOT_ALLOWED")

    if not required_confirmations["manual_execution_runtime_commit_confirmed"]:
        issues.append("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED")

    if not required_confirmations["manual_runtime_file_write_confirmed"]:
        issues.append("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED")

    if not environment_checks["real_runtime_commit_enabled"]:
        issues.append("REAL_RUNTIME_COMMIT_DISABLED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            runtime_commit_allowed=False,
            issues=issues,
            warnings=list(runtime_api_result.get("warnings") or [])
            + list(commit_plan_orchestrator_result.get("warnings") or []),
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
            allowlist_decisions=allowlist_decisions,
        )

    return _result(
        status=STATUS_READY,
        runtime_commit_allowed=True,
        warnings=list(runtime_api_result.get("warnings") or [])
        + list(commit_plan_orchestrator_result.get("warnings") or []),
        required_confirmations=required_confirmations,
        environment_checks=environment_checks,
        allowlist_decisions=allowlist_decisions,
    )
