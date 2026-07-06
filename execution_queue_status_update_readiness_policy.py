# -*- coding: utf-8 -*-
"""Preview-only policy for opening queue status updates.

This policy decides whether a runtime status update orchestrator result may be
handed to a future Queue Status Update layer. It never updates queue files,
releases locks, recalls runtime status updates, result recorders, brokers, GUI,
or real execution controller flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_QUEUE_STATUS_UPDATE_READINESS_POLICY"
STATUS_READY = "READY_TO_UPDATE_QUEUE_STATUS"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
RUNTIME_STATUS_UPDATED = "RUNTIME_STATUS_UPDATED"
NEXT_STAGE_REQUIRED = "QUEUE_STATUS_UPDATE_REQUIRED"


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
    queue_status_update_allowed: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "queue_status_update_allowed": queue_status_update_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_status_update_called": False,
        "lock_release_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_queue_status_update_readiness(
    runtime_status_update_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether queue status update may be opened later."""
    confirmation_checks = {
        "manual_queue_status_update_confirmed": _as_dict(confirmations).get(
            "manual_queue_status_update_confirmed"
        )
        is True,
    }
    environment_checks = {
        "queue_status_update_enabled": _as_dict(environment_flags).get("queue_status_update_enabled") is True,
        "queue_execution_state_enabled": _as_dict(environment_flags).get("queue_execution_state_enabled") is True,
    }

    if not isinstance(runtime_status_update_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_RUNTIME_STATUS_UPDATE_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(runtime_status_update_orchestrator_result.get("warnings"))
    runtime_status = _text(runtime_status_update_orchestrator_result.get("status"))
    if runtime_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(runtime_status_update_orchestrator_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if runtime_status != RUNTIME_STATUS_UPDATED:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(runtime_status_update_orchestrator_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_ORCHESTRATOR_NOT_UPDATED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if runtime_status_update_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("QUEUE_STATUS_UPDATE_NEXT_STAGE_REQUIRED")
    if runtime_status_update_orchestrator_result.get("runtime_status_update_called") is not True:
        issues.append("RUNTIME_STATUS_UPDATE_CALLED_NOT_TRUE")
    if _as_dict(runtime_status_update_orchestrator_result.get("runtime_status_record")) == {}:
        issues.append("RUNTIME_STATUS_RECORD_REQUIRED")
    if not confirmation_checks["manual_queue_status_update_confirmed"]:
        issues.append("MANUAL_QUEUE_STATUS_UPDATE_CONFIRMATION_REQUIRED")
    if not environment_checks["queue_status_update_enabled"]:
        issues.append("QUEUE_STATUS_UPDATE_ENVIRONMENT_DISABLED")
    if not environment_checks["queue_execution_state_enabled"]:
        issues.append("QUEUE_EXECUTION_STATE_ENVIRONMENT_DISABLED")

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
        queue_status_update_allowed=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
