# -*- coding: utf-8 -*-
"""Preview-only policy for opening SendOrder EntryPoint.

This policy decides whether a passed Final Send Gate call result may be
handed to a later SendOrder EntryPoint call layer. It does not call
send_order_entrypoint.execute_send_order, broker adapters, queue commit
services, runtime commit services, GUI, or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_SEND_ORDER_ENTRYPOINT_OPEN_POLICY"
STATUS_READY = "READY_TO_OPEN_SEND_ORDER_ENTRYPOINT"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
FINAL_GATE_STATUS_PASSED = "FINAL_SEND_GATE_PASSED"
NEXT_STAGE_REQUIRED = "SEND_ORDER_ENTRYPOINT_REQUIRED"


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
    send_order_entrypoint_allowed: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "send_order_entrypoint_allowed": send_order_entrypoint_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "entrypoint_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_send_order_entrypoint_open_policy(
    final_send_gate_call_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether SendOrder EntryPoint may be opened later."""
    confirmation_checks = {
        "manual_send_order_entrypoint_confirmed": _as_dict(confirmations).get("manual_send_order_entrypoint_confirmed")
        is True,
    }
    environment_checks = {
        "send_order_entrypoint_enabled": _as_dict(environment_flags).get("send_order_entrypoint_enabled") is True,
        "real_send_order_enabled": _as_dict(environment_flags).get("real_send_order_enabled") is True,
    }

    if not isinstance(final_send_gate_call_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_FINAL_SEND_GATE_CALL_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(final_send_gate_call_orchestrator_result.get("warnings"))
    call_status = _text(final_send_gate_call_orchestrator_result.get("status"))
    if call_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(final_send_gate_call_orchestrator_result.get("issues"))
            or ["FINAL_SEND_GATE_CALL_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if call_status != FINAL_GATE_STATUS_PASSED:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(final_send_gate_call_orchestrator_result.get("issues"))
            or ["FINAL_SEND_GATE_CALL_ORCHESTRATOR_NOT_PASSED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if final_send_gate_call_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("SEND_ORDER_ENTRYPOINT_NEXT_STAGE_REQUIRED")
    if final_send_gate_call_orchestrator_result.get("final_send_gate_called") is not True:
        issues.append("FINAL_SEND_GATE_CALLED_NOT_TRUE")
    if final_send_gate_call_orchestrator_result.get("send_order_called") is not False:
        issues.append("SEND_ORDER_ALREADY_CALLED")

    final_gate_result = _as_dict(final_send_gate_call_orchestrator_result.get("final_send_gate_result"))
    if not final_gate_result:
        issues.append("FINAL_SEND_GATE_RESULT_REQUIRED")
    else:
        if final_gate_result.get("final_send_gate_ok") is not True:
            issues.append("FINAL_SEND_GATE_RESULT_NOT_OK")
        if final_gate_result.get("next_stage") != NEXT_STAGE_REQUIRED:
            issues.append("FINAL_SEND_GATE_RESULT_NEXT_STAGE_REQUIRED")

    if not confirmation_checks["manual_send_order_entrypoint_confirmed"]:
        issues.append("MANUAL_SEND_ORDER_ENTRYPOINT_CONFIRMATION_REQUIRED")
    if not environment_checks["send_order_entrypoint_enabled"]:
        issues.append("SEND_ORDER_ENTRYPOINT_ENVIRONMENT_DISABLED")
    if not environment_checks["real_send_order_enabled"]:
        issues.append("REAL_SEND_ORDER_ENVIRONMENT_DISABLED")

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
        send_order_entrypoint_allowed=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
