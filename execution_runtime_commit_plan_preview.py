# -*- coding: utf-8 -*-
"""Preview-only execution runtime commit plan.

This module combines a runtime write preview and commit readiness gate into an
in-memory plan. It never creates runtime files, writes files, creates
directories, performs atomic writes, commits queues, or calls execution/order
components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PLAN_TYPE = "EXECUTION_RUNTIME_COMMIT_PLAN_PREVIEW"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _result(
    *,
    status: str,
    commit_ready: bool,
    planned_targets: dict[str, Any] | None = None,
    execution_record: dict[str, Any] | None = None,
    lock_record: dict[str, Any] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "status": status,
        "commit_ready": commit_ready,
        "preview_only": True,
        "runtime_write": False,
        "planned_targets": deepcopy(planned_targets or {}),
        "planned_records": {
            "execution": deepcopy(execution_record),
            "lock": deepcopy(lock_record),
        },
        "required_confirmations": deepcopy(required_confirmations or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def build_execution_runtime_commit_plan_preview(
    write_preview_orchestrator_result: Any,
    commit_readiness_gate_result: Any,
) -> dict[str, Any]:
    """Build an in-memory commit plan preview without side effects."""
    if not isinstance(write_preview_orchestrator_result, dict):
        return _result(
            status="INVALID",
            commit_ready=False,
            issues=["MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT"],
        )
    if not isinstance(commit_readiness_gate_result, dict):
        return _result(
            status="INVALID",
            commit_ready=False,
            issues=["MALFORMED_COMMIT_READINESS_GATE_RESULT"],
        )

    orchestrator = _as_dict(write_preview_orchestrator_result)
    gate = _as_dict(commit_readiness_gate_result)
    write_preview = _as_dict(orchestrator.get("write_preview"))
    issues = _unique(_as_list(orchestrator.get("issues")) + _as_list(gate.get("issues")))
    warnings = _unique(_as_list(orchestrator.get("warnings")) + _as_list(gate.get("warnings")))
    required_confirmations = _as_dict(gate.get("required_confirmations"))
    planned_targets = _as_dict(write_preview.get("would_write_targets"))

    gate_status = gate.get("status")
    if gate_status == "INVALID":
        return _result(
            status="INVALID",
            commit_ready=False,
            planned_targets=planned_targets,
            required_confirmations=required_confirmations,
            issues=issues or ["COMMIT_READINESS_GATE_INVALID"],
            warnings=warnings,
        )
    if gate_status == "BLOCKED":
        return _result(
            status="BLOCKED",
            commit_ready=False,
            planned_targets=planned_targets,
            required_confirmations=required_confirmations,
            issues=issues or ["COMMIT_READINESS_GATE_BLOCKED"],
            warnings=warnings,
        )
    if gate.get("commit_ready") is not True:
        return _result(
            status="BLOCKED",
            commit_ready=False,
            planned_targets=planned_targets,
            required_confirmations=required_confirmations,
            issues=issues or ["COMMIT_READY_IS_NOT_TRUE"],
            warnings=warnings,
        )

    if orchestrator.get("status") != "READY" or write_preview.get("status") != "READY":
        return _result(
            status="BLOCKED" if orchestrator.get("status") == "BLOCKED" else "INVALID",
            commit_ready=False,
            planned_targets=planned_targets,
            required_confirmations=required_confirmations,
            issues=issues or ["WRITE_PREVIEW_NOT_READY"],
            warnings=warnings,
        )

    execution_record = _as_dict(write_preview.get("execution_record_preview"))
    lock_record = _as_dict(write_preview.get("lock_record_preview"))
    missing_records: list[str] = []
    if not execution_record:
        missing_records.append("MISSING_PLANNED_EXECUTION_RECORD")
    if not lock_record:
        missing_records.append("MISSING_PLANNED_LOCK_RECORD")
    if missing_records:
        return _result(
            status="INVALID",
            commit_ready=False,
            planned_targets=planned_targets,
            required_confirmations=required_confirmations,
            issues=_unique(issues + missing_records),
            warnings=warnings,
        )

    return _result(
        status="READY",
        commit_ready=True,
        planned_targets=planned_targets,
        execution_record=execution_record,
        lock_record=lock_record,
        required_confirmations=required_confirmations,
        issues=issues,
        warnings=warnings,
    )
