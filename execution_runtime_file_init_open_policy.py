# -*- coding: utf-8 -*-
"""Preview-only policy for opening project runtime file initialization.

This module decides whether creating project runtime init files may be opened.
It never creates files, creates directories, calls commit services, commits
queues, calls SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


POLICY_TYPE = "EXECUTION_RUNTIME_FILE_INIT_OPEN_POLICY"
STATUS_READY = "READY_TO_OPEN_FILE_INIT"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_SKIPPED = "SKIPPED"


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


def _targets_from_orchestrator(orchestrator: dict[str, Any]) -> dict[str, Any]:
    commit_plan = _as_dict(orchestrator.get("commit_plan"))
    return _as_dict(commit_plan.get("planned_targets"))


def _result(
    *,
    status: str,
    file_init_allowed: bool,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "file_init_allowed": file_init_allowed,
        "preview_only": True,
        "runtime_write": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_runtime_file_init_open_policy(
    *,
    file_init_commit_plan_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether project runtime file initialization may be opened."""
    if not isinstance(file_init_commit_plan_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            file_init_allowed=False,
            issues=["MALFORMED_FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_RESULT"],
        )

    orchestrator = file_init_commit_plan_orchestrator_result
    confirmation_flags = _as_dict(confirmations)
    env_flags = _as_dict(environment_flags)
    targets = _targets_from_orchestrator(orchestrator)
    order_executions_is_project_runtime = _under_project_runtime(targets.get("order_executions"))
    order_locks_is_project_runtime = _under_project_runtime(targets.get("order_locks"))

    required_confirmations = {
        "manual_runtime_file_init_commit_confirmed": confirmation_flags.get(
            "manual_runtime_file_init_commit_confirmed"
        )
        is True,
        "manual_project_runtime_path_confirmed": confirmation_flags.get(
            "manual_project_runtime_path_confirmed"
        )
        is True,
    }
    environment_checks = {
        "real_runtime_file_init_enabled": env_flags.get("real_runtime_file_init_enabled") is True,
        "allow_project_runtime_file_init": env_flags.get("allow_project_runtime_file_init") is True,
        "order_executions_is_project_runtime": order_executions_is_project_runtime,
        "order_locks_is_project_runtime": order_locks_is_project_runtime,
    }

    status = orchestrator.get("status")
    warnings = list(orchestrator.get("warnings") or [])

    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            file_init_allowed=False,
            issues=list(orchestrator.get("issues") or []) or ["FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_INVALID"],
            warnings=warnings,
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
        )
    if status == "SKIPPED":
        return _result(
            status=STATUS_SKIPPED,
            file_init_allowed=False,
            warnings=warnings or ["FILE_INIT_ALREADY_SKIPPED"],
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
        )
    if status != "READY":
        return _result(
            status=STATUS_BLOCKED,
            file_init_allowed=False,
            issues=list(orchestrator.get("issues") or []) or ["FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_NOT_READY"],
            warnings=warnings,
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
        )

    issues: list[str] = []
    if orchestrator.get("init_commit_ready") is not True:
        issues.append("INIT_COMMIT_READY_IS_NOT_TRUE")
    if not required_confirmations["manual_runtime_file_init_commit_confirmed"]:
        issues.append("MANUAL_RUNTIME_FILE_INIT_COMMIT_CONFIRMATION_REQUIRED")
    if not required_confirmations["manual_project_runtime_path_confirmed"]:
        issues.append("MANUAL_PROJECT_RUNTIME_PATH_CONFIRMATION_REQUIRED")
    if not environment_checks["real_runtime_file_init_enabled"]:
        issues.append("REAL_RUNTIME_FILE_INIT_DISABLED")
    if not environment_checks["allow_project_runtime_file_init"]:
        issues.append("PROJECT_RUNTIME_FILE_INIT_NOT_ALLOWED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            file_init_allowed=False,
            issues=issues,
            warnings=warnings,
            required_confirmations=required_confirmations,
            environment_checks=environment_checks,
        )

    return _result(
        status=STATUS_READY,
        file_init_allowed=True,
        warnings=warnings,
        required_confirmations=required_confirmations,
        environment_checks=environment_checks,
    )
