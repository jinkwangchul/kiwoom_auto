# -*- coding: utf-8 -*-
"""Preview-only gate for execution runtime commit readiness.

This module only decides whether an execution runtime write preview may proceed
to a future commit layer. It never creates runtime files, writes files, creates
directories, performs atomic writes, commits queues, or calls execution/order
components.
"""

from __future__ import annotations

from typing import Any


GATE_TYPE = "EXECUTION_RUNTIME_COMMIT_READINESS_GATE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _result(
    *,
    status: str,
    commit_ready: bool,
    manual_execution_runtime_commit_confirmed: bool,
    manual_runtime_file_write_confirmed: bool,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "gate_type": GATE_TYPE,
        "status": status,
        "commit_ready": commit_ready,
        "preview_only": True,
        "runtime_write": False,
        "required_confirmations": {
            "manual_execution_runtime_commit_confirmed": manual_execution_runtime_commit_confirmed,
            "manual_runtime_file_write_confirmed": manual_runtime_file_write_confirmed,
        },
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_runtime_commit_readiness(
    write_preview_orchestrator_result: Any,
    *,
    manual_execution_runtime_commit_confirmed: bool = False,
    manual_runtime_file_write_confirmed: bool = False,
) -> dict[str, Any]:
    """Evaluate commit readiness without performing commit or runtime write."""
    commit_confirmed = manual_execution_runtime_commit_confirmed is True
    runtime_write_confirmed = manual_runtime_file_write_confirmed is True

    if not isinstance(write_preview_orchestrator_result, dict):
        return _result(
            status="INVALID",
            commit_ready=False,
            manual_execution_runtime_commit_confirmed=commit_confirmed,
            manual_runtime_file_write_confirmed=runtime_write_confirmed,
            issues=["MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT"],
        )

    orchestrator = _as_dict(write_preview_orchestrator_result)
    warnings = list(orchestrator.get("warnings") or [])
    orchestrator_status = orchestrator.get("status")

    if orchestrator_status == "INVALID":
        return _result(
            status="INVALID",
            commit_ready=False,
            manual_execution_runtime_commit_confirmed=commit_confirmed,
            manual_runtime_file_write_confirmed=runtime_write_confirmed,
            issues=list(orchestrator.get("issues") or ["WRITE_PREVIEW_ORCHESTRATOR_INVALID"]),
            warnings=warnings,
        )

    if orchestrator_status == "BLOCKED":
        return _result(
            status="BLOCKED",
            commit_ready=False,
            manual_execution_runtime_commit_confirmed=commit_confirmed,
            manual_runtime_file_write_confirmed=runtime_write_confirmed,
            issues=list(orchestrator.get("issues") or ["WRITE_PREVIEW_ORCHESTRATOR_BLOCKED"]),
            warnings=warnings,
        )

    if orchestrator_status != "READY":
        return _result(
            status="INVALID",
            commit_ready=False,
            manual_execution_runtime_commit_confirmed=commit_confirmed,
            manual_runtime_file_write_confirmed=runtime_write_confirmed,
            issues=["INVALID_WRITE_PREVIEW_ORCHESTRATOR_STATUS"],
            warnings=warnings,
        )

    missing_confirmations: list[str] = []
    if not commit_confirmed:
        missing_confirmations.append("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED")
    if not runtime_write_confirmed:
        missing_confirmations.append("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED")

    if missing_confirmations:
        return _result(
            status="BLOCKED",
            commit_ready=False,
            manual_execution_runtime_commit_confirmed=commit_confirmed,
            manual_runtime_file_write_confirmed=runtime_write_confirmed,
            issues=missing_confirmations,
            warnings=warnings,
        )

    return _result(
        status="READY",
        commit_ready=True,
        manual_execution_runtime_commit_confirmed=commit_confirmed,
        manual_runtime_file_write_confirmed=runtime_write_confirmed,
        issues=[],
        warnings=warnings,
    )
