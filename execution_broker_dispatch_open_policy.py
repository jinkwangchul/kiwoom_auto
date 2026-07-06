# -*- coding: utf-8 -*-
"""Preview-only policy for opening real broker dispatch.

This policy decides whether a SendOrder EntryPoint result is eligible for a
later real Broker/Kiwoom OpenAPI dispatch layer. It does not call broker
adapters, Kiwoom OpenAPI, SendOrder EntryPoint, queue commit services, runtime
commit services, GUI, or real execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_BROKER_DISPATCH_OPEN_POLICY"
STATUS_READY = "READY_TO_OPEN_BROKER_DISPATCH"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
ENTRYPOINT_STATUS_PASSED = "SEND_ORDER_ENTRYPOINT_PASSED"
NEXT_STAGE_REQUIRED = "BROKER_SEND_REQUIRED"
ENTRYPOINT_NEXT_STAGE_REQUIRED = "SEND_ORDER_RESULT_REVIEW_REQUIRED"


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
    broker_dispatch_allowed: bool = False,
    entrypoint_called: bool = False,
    required_confirmations: dict[str, Any] | None = None,
    environment_checks: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "broker_dispatch_allowed": broker_dispatch_allowed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "entrypoint_called": bool(entrypoint_called),
        "send_order_called": False,
        "broker_called": False,
        "kiwoom_called": False,
        "required_confirmations": deepcopy(required_confirmations or {}),
        "environment_checks": deepcopy(environment_checks or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_broker_dispatch_open_policy(
    send_order_entrypoint_orchestrator_result: Any,
    confirmations: Any = None,
    environment_flags: Any = None,
) -> dict[str, Any]:
    """Evaluate whether real Broker/Kiwoom dispatch may be opened later."""
    confirmation_checks = {
        "manual_broker_dispatch_confirmed": _as_dict(confirmations).get("manual_broker_dispatch_confirmed") is True,
    }
    environment_checks = {
        "broker_dispatch_enabled": _as_dict(environment_flags).get("broker_dispatch_enabled") is True,
        "real_broker_dispatch_enabled": _as_dict(environment_flags).get("real_broker_dispatch_enabled") is True,
        "kiwoom_connected": _as_dict(environment_flags).get("kiwoom_connected") is True,
        "account_selected": _as_dict(environment_flags).get("account_selected") is True,
        "real_trade_enabled": _as_dict(environment_flags).get("real_trade_enabled") is True,
    }

    if not isinstance(send_order_entrypoint_orchestrator_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=["MALFORMED_SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_RESULT"],
        )

    warnings = _as_list(send_order_entrypoint_orchestrator_result.get("warnings"))
    entrypoint_called = send_order_entrypoint_orchestrator_result.get("entrypoint_called") is True
    orchestrator_status = _text(send_order_entrypoint_orchestrator_result.get("status"))
    if orchestrator_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            entrypoint_called=entrypoint_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(send_order_entrypoint_orchestrator_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if orchestrator_status != ENTRYPOINT_STATUS_PASSED:
        return _result(
            status=STATUS_BLOCKED,
            entrypoint_called=entrypoint_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=_as_list(send_order_entrypoint_orchestrator_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_NOT_PASSED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if send_order_entrypoint_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("BROKER_DISPATCH_NEXT_STAGE_REQUIRED")
    if not entrypoint_called:
        issues.append("ENTRYPOINT_CALLED_NOT_TRUE")

    entrypoint_result = _as_dict(send_order_entrypoint_orchestrator_result.get("send_order_entrypoint_result"))
    if not entrypoint_result:
        issues.append("SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED")
    else:
        if entrypoint_result.get("send_order_executed") is not True:
            issues.append("SEND_ORDER_ENTRYPOINT_RESULT_NOT_EXECUTED")
        if entrypoint_result.get("send_order_called") is not True:
            issues.append("SEND_ORDER_ENTRYPOINT_RESULT_SEND_ORDER_CALLED_NOT_TRUE")
        if entrypoint_result.get("next_stage") != ENTRYPOINT_NEXT_STAGE_REQUIRED:
            issues.append("SEND_ORDER_ENTRYPOINT_RESULT_NEXT_STAGE_REQUIRED")
        blocked_reasons = entrypoint_result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            issues.append("SEND_ORDER_ENTRYPOINT_RESULT_HAS_BLOCKED_REASONS")

    if not confirmation_checks["manual_broker_dispatch_confirmed"]:
        issues.append("MANUAL_BROKER_DISPATCH_CONFIRMATION_REQUIRED")
    if not environment_checks["broker_dispatch_enabled"]:
        issues.append("BROKER_DISPATCH_ENVIRONMENT_DISABLED")
    if not environment_checks["real_broker_dispatch_enabled"]:
        issues.append("REAL_BROKER_DISPATCH_ENVIRONMENT_DISABLED")
    if not environment_checks["kiwoom_connected"]:
        issues.append("KIWOOM_CONNECTED_NOT_TRUE")
    if not environment_checks["account_selected"]:
        issues.append("ACCOUNT_SELECTED_NOT_TRUE")
    if not environment_checks["real_trade_enabled"]:
        issues.append("REAL_TRADE_ENABLED_NOT_TRUE")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            entrypoint_called=entrypoint_called,
            required_confirmations=confirmation_checks,
            environment_checks=environment_checks,
            issues=issues,
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        broker_dispatch_allowed=True,
        entrypoint_called=True,
        required_confirmations=confirmation_checks,
        environment_checks=environment_checks,
        warnings=warnings,
    )
