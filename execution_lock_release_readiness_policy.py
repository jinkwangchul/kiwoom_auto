# -*- coding: utf-8 -*-
"""Preview-only policy for opening lock release.

This policy decides whether a queue status update orchestrator result may be
handed to a future Lock Release layer. It never releases locks, writes
order_locks.json, recalls queue/runtime updates, re-runs recorders or brokers,
or touches GUI/real execution controller flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_LOCK_RELEASE_READINESS_POLICY"
STATUS_READY = "READY_TO_RELEASE_LOCK"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
QUEUE_STATUS_UPDATED = "QUEUE_STATUS_UPDATED"
NEXT_STAGE_REQUIRED = "LOCK_RELEASE_REQUIRED"


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
    lock_release_allowed: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "lock_release_allowed": lock_release_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "lock_release_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_lock_release_readiness(
    queue_status_update_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether lock release may be opened later."""
    confirmation_checks = {
        "manual_lock_release_confirmed": _as_dict(confirmations).get("manual_lock_release_confirmed") is True,
    }
    environment_checks = {
        "lock_release_enabled": _as_dict(environment_flags).get("lock_release_enabled") is True,
        "runtime_lock_state_enabled": _as_dict(environment_flags).get("runtime_lock_state_enabled") is True,
    }

    if not isinstance(queue_status_update_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_QUEUE_STATUS_UPDATE_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(queue_status_update_orchestrator_result.get("warnings"))
    queue_status = _text(queue_status_update_orchestrator_result.get("status"))
    if queue_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(queue_status_update_orchestrator_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if queue_status != QUEUE_STATUS_UPDATED:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(queue_status_update_orchestrator_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_ORCHESTRATOR_NOT_UPDATED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if queue_status_update_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("LOCK_RELEASE_NEXT_STAGE_REQUIRED")
    if queue_status_update_orchestrator_result.get("queue_status_update_called") is not True:
        issues.append("QUEUE_STATUS_UPDATE_CALLED_NOT_TRUE")
    if _as_dict(queue_status_update_orchestrator_result.get("queue_status_record")) == {}:
        issues.append("QUEUE_STATUS_RECORD_REQUIRED")
    if not confirmation_checks["manual_lock_release_confirmed"]:
        issues.append("MANUAL_LOCK_RELEASE_CONFIRMATION_REQUIRED")
    if not environment_checks["lock_release_enabled"]:
        issues.append("LOCK_RELEASE_ENVIRONMENT_DISABLED")
    if not environment_checks["runtime_lock_state_enabled"]:
        issues.append("RUNTIME_LOCK_STATE_ENVIRONMENT_DISABLED")

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
        lock_release_allowed=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
