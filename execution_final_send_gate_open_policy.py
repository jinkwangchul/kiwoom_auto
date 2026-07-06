# -*- coding: utf-8 -*-
"""Preview-only policy for opening the Final Send Gate service call.

This policy decides whether a prepared Final Send Gate orchestrator result may
be passed to a later service-call layer. It does not call Final Send Gate,
SendOrder, queue commit services, runtime commit services, GUI, or real
execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_FINAL_SEND_GATE_OPEN_POLICY"
STATUS_READY = "READY_TO_OPEN_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
ORCHESTRATOR_READY = "READY_FOR_FINAL_SEND_GATE"
NEXT_STAGE_REQUIRED = "FINAL_SEND_GATE_SERVICE_REQUIRED"


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
    final_send_gate_call_allowed: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "final_send_gate_call_allowed": final_send_gate_call_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "final_send_gate_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_final_send_gate_open_policy(
    final_send_gate_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether Final Send Gate service call may be opened later."""
    confirmation_checks = {
        "manual_final_send_gate_call_confirmed": _as_dict(confirmations).get("manual_final_send_gate_call_confirmed") is True,
    }
    environment_checks = {
        "final_send_gate_call_enabled": _as_dict(environment_flags).get("final_send_gate_call_enabled") is True,
    }

    if not isinstance(final_send_gate_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_FINAL_SEND_GATE_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(final_send_gate_orchestrator_result.get("warnings"))
    orchestrator_status = _text(final_send_gate_orchestrator_result.get("status"))
    if orchestrator_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(final_send_gate_orchestrator_result.get("issues")) or ["FINAL_SEND_GATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if orchestrator_status != ORCHESTRATOR_READY:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(final_send_gate_orchestrator_result.get("issues")) or ["FINAL_SEND_GATE_ORCHESTRATOR_NOT_READY"],
            warnings=warnings,
        )

    issues: list[str] = []
    if final_send_gate_orchestrator_result.get("final_send_gate_ready") is not True:
        issues.append("FINAL_SEND_GATE_READY_NOT_TRUE")
    if final_send_gate_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("FINAL_SEND_GATE_NEXT_STAGE_NOT_SERVICE_REQUIRED")
    if not _as_dict(final_send_gate_orchestrator_result.get("final_send_gate_input")):
        issues.append("FINAL_SEND_GATE_INPUT_REQUIRED")
    if not confirmation_checks["manual_final_send_gate_call_confirmed"]:
        issues.append("MANUAL_FINAL_SEND_GATE_CALL_CONFIRMATION_REQUIRED")
    if not environment_checks["final_send_gate_call_enabled"]:
        issues.append("FINAL_SEND_GATE_CALL_ENVIRONMENT_DISABLED")

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
        final_send_gate_call_allowed=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
