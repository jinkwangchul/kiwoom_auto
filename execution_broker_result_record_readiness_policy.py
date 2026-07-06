# -*- coding: utf-8 -*-
"""Preview-only policy for opening broker result recording.

This policy decides whether a reviewed broker result may be handed to a future
Execution Result Recorder layer. It never records results, writes runtime or
queue files, releases locks, recalls brokers, or touches GUI/real controller
flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_BROKER_RESULT_RECORD_READINESS_POLICY"
STATUS_READY = "READY_TO_RECORD_BROKER_RESULT"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
REVIEW_STATUS_READY = "READY_FOR_RESULT_RECORD"
NEXT_STAGE_REQUIRED = "BROKER_RESULT_RECORD_REQUIRED"


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
    result_record_allowed: bool = False,
    broker_called: bool = True,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "result_record_allowed": result_record_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "broker_called": bool(broker_called),
        "result_record_called": False,
        "lock_release_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_broker_result_record_readiness(
    broker_result_review_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether broker result recording may be opened later."""
    confirmation_checks = {
        "manual_result_record_confirmed": _as_dict(confirmations).get("manual_result_record_confirmed") is True,
    }
    environment_checks = {
        "result_record_enabled": _as_dict(environment_flags).get("result_record_enabled") is True,
        "runtime_recording_enabled": _as_dict(environment_flags).get("runtime_recording_enabled") is True,
    }

    if not isinstance(broker_result_review_result, dict):
        return _result(
            status=STATUS_INVALID,
            broker_called=False,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_BROKER_RESULT_REVIEW_RESULT"],
        )

    warnings = _as_list(broker_result_review_result.get("warnings"))
    broker_called = broker_result_review_result.get("broker_called") is True
    review_status = _text(broker_result_review_result.get("status"))
    if review_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            broker_called=broker_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(broker_result_review_result.get("issues")) or ["BROKER_RESULT_REVIEW_INVALID"],
            warnings=warnings,
        )
    if review_status != REVIEW_STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(broker_result_review_result.get("issues")) or ["BROKER_RESULT_REVIEW_NOT_READY"],
            warnings=warnings,
        )

    issues: list[str] = []
    if broker_result_review_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("BROKER_RESULT_RECORD_NEXT_STAGE_REQUIRED")
    if not broker_called:
        issues.append("BROKER_CALLED_NOT_TRUE")
    if broker_result_review_result.get("preview_only") is not True:
        issues.append("BROKER_RESULT_REVIEW_PREVIEW_ONLY_NOT_TRUE")
    if not _as_dict(broker_result_review_result.get("broker_result")):
        issues.append("BROKER_RESULT_REQUIRED")
    if not confirmation_checks["manual_result_record_confirmed"]:
        issues.append("MANUAL_RESULT_RECORD_CONFIRMATION_REQUIRED")
    if not environment_checks["result_record_enabled"]:
        issues.append("RESULT_RECORD_ENVIRONMENT_DISABLED")
    if not environment_checks["runtime_recording_enabled"]:
        issues.append("RUNTIME_RECORDING_ENVIRONMENT_DISABLED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=issues,
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        result_record_allowed=True,
        broker_called=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
