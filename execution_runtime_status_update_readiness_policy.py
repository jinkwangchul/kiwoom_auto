# -*- coding: utf-8 -*-
"""Preview-only policy for opening runtime status updates.

This policy decides whether a broker result recorder orchestrator result may be
handed to a future Runtime Status Update layer. It never updates runtime state,
queue records, locks, broker state, result records, GUI, or real execution
controller flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_RUNTIME_STATUS_UPDATE_READINESS_POLICY"
STATUS_READY = "READY_TO_UPDATE_RUNTIME_STATUS"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
RECORDER_STATUS_RECORDED = "BROKER_RESULT_RECORDED"
NEXT_STAGE_REQUIRED = "RUNTIME_STATUS_UPDATE_REQUIRED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    runtime_status_update_allowed: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "runtime_status_update_allowed": runtime_status_update_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "runtime_status_update_called": False,
        "queue_update_called": False,
        "lock_release_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_runtime_status_update_readiness(
    broker_result_recorder_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether runtime status update may be opened later."""
    confirmation_checks = {
        "manual_runtime_status_update_confirmed": _as_dict(confirmations).get(
            "manual_runtime_status_update_confirmed"
        )
        is True,
    }
    environment_checks = {
        "runtime_status_update_enabled": _as_dict(environment_flags).get("runtime_status_update_enabled") is True,
        "runtime_execution_state_enabled": _as_dict(environment_flags).get("runtime_execution_state_enabled")
        is True,
    }

    if not isinstance(broker_result_recorder_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_BROKER_RESULT_RECORDER_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(broker_result_recorder_orchestrator_result.get("warnings"))
    recorder_status = _text(broker_result_recorder_orchestrator_result.get("status"))
    if recorder_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(broker_result_recorder_orchestrator_result.get("issues"))
            or ["BROKER_RESULT_RECORDER_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if recorder_status != RECORDER_STATUS_RECORDED:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(broker_result_recorder_orchestrator_result.get("issues"))
            or ["BROKER_RESULT_RECORDER_ORCHESTRATOR_NOT_RECORDED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if broker_result_recorder_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("RUNTIME_STATUS_UPDATE_NEXT_STAGE_REQUIRED")
    if broker_result_recorder_orchestrator_result.get("result_record_called") is not True:
        issues.append("RESULT_RECORD_CALLED_NOT_TRUE")
    if _as_dict(broker_result_recorder_orchestrator_result.get("broker_result_record")) == {}:
        issues.append("BROKER_RESULT_RECORD_REQUIRED")
    if not confirmation_checks["manual_runtime_status_update_confirmed"]:
        issues.append("MANUAL_RUNTIME_STATUS_UPDATE_CONFIRMATION_REQUIRED")
    if not environment_checks["runtime_status_update_enabled"]:
        issues.append("RUNTIME_STATUS_UPDATE_ENVIRONMENT_DISABLED")
    if not environment_checks["runtime_execution_state_enabled"]:
        issues.append("RUNTIME_EXECUTION_STATE_ENVIRONMENT_DISABLED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=issues,
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        runtime_status_update_allowed=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
