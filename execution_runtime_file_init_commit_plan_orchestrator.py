# -*- coding: utf-8 -*-
"""Orchestrator for execution runtime file-init commit plan validation.

This layer only chains init commit plan preview construction and validation.
It does not create runtime files, write files, create directories, perform
atomic writes, commit queues, call SendOrder, or connect to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_file_init_commit_plan_preview import (
    build_execution_runtime_file_init_commit_plan_preview,
)
from execution_runtime_file_init_commit_plan_validator import (
    validate_execution_runtime_file_init_commit_plan_preview,
)


ORCHESTRATOR_TYPE = "EXECUTION_RUNTIME_FILE_INIT_COMMIT_PLAN_ORCHESTRATOR"


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _final_status(commit_plan: dict[str, Any], validation: dict[str, Any]) -> str:
    plan_status = commit_plan.get("status")
    if plan_status == "INVALID":
        return "INVALID"
    if validation.get("valid") is not True:
        return "INVALID"
    if plan_status == "BLOCKED":
        return "BLOCKED"
    if plan_status == "SKIPPED":
        return "SKIPPED"
    if plan_status == "READY":
        return "READY"
    return "INVALID"


def run_execution_runtime_file_init_commit_plan_orchestrator(
    file_init_preview_result: Any,
    file_init_approval_gate_result: Any,
) -> dict[str, Any]:
    """Build and validate a runtime file-init commit plan preview."""
    commit_plan = build_execution_runtime_file_init_commit_plan_preview(
        file_init_preview_result,
        file_init_approval_gate_result,
    )
    validation = validate_execution_runtime_file_init_commit_plan_preview(commit_plan)
    status = _final_status(commit_plan, validation)
    issues = _unique(list(commit_plan.get("issues") or []) + list(validation.get("issues") or []))
    warnings = _unique(list(commit_plan.get("warnings") or []) + list(validation.get("warnings") or []))

    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "init_commit_ready": bool(commit_plan.get("init_commit_ready")) and status == "READY",
        "preview_only": True,
        "runtime_write": False,
        "commit_plan": deepcopy(commit_plan),
        "validation": deepcopy(validation),
        "issues": issues,
        "warnings": warnings,
    }
