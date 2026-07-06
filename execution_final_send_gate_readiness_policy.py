# -*- coding: utf-8 -*-
"""Preview-only readiness policy before Final Send Gate.

This module checks whether the SendOrder request preview produced from a queue
committed review is ready to be passed to a future Final Send Gate call. It does
not call Final Send Gate, SendOrder, queue commit services, runtime writers, GUI,
or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "EXECUTION_FINAL_SEND_GATE_READINESS_POLICY"
STATUS_READY = "READY_FOR_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
FINAL_SEND_GATE_NEXT_STAGE_REQUIRED = "FINAL_SEND_GATE_REQUIRED"

_IDENTITY_FIELDS = ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _manual_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_final_send_confirmed") is True
        or ctx.get("operator_confirmed_for_final_send") is True
    )


def _result(
    *,
    status: str,
    final_send_gate_allowed: bool = False,
    adapter_preview_result: dict[str, Any] | None = None,
    order_queued_record: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    identity_checks: dict[str, Any] | None = None,
    guard_checks: dict[str, Any] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "final_send_gate_allowed": final_send_gate_allowed,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "send_order_called": False,
        "final_send_gate_called": False,
        "adapter_preview_result": deepcopy(adapter_preview_result) if isinstance(adapter_preview_result, dict) else None,
        "order_queued_record": deepcopy(order_queued_record) if isinstance(order_queued_record, dict) else None,
        "identity": deepcopy(identity or {}),
        "identity_checks": deepcopy(identity_checks or {}),
        "guard_checks": deepcopy(guard_checks or {}),
        "required_confirmations": deepcopy(required_confirmations or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _identity_checks(identity: dict[str, Any], request_preview: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    checks: dict[str, Any] = {}
    issues: list[str] = []
    for field in _IDENTITY_FIELDS:
        identity_value = _text(identity.get(field))
        preview_value = _text(request_preview.get(field))
        record_value = _text(record.get(field))
        passed = bool(identity_value and preview_value and record_value and identity_value == preview_value == record_value)
        checks[field] = {
            "result": "PASS" if passed else "FAIL",
            "identity": identity_value,
            "send_order_request_preview": preview_value,
            "order_queued_record": record_value,
        }
        if not identity_value:
            issues.append(f"MISSING_IDENTITY_{field.upper()}")
        elif not preview_value:
            issues.append(f"MISSING_REQUEST_PREVIEW_{field.upper()}")
        elif not record_value:
            issues.append(f"MISSING_ORDER_QUEUED_RECORD_{field.upper()}")
        elif identity_value != preview_value or identity_value != record_value:
            issues.append(f"IDENTITY_MISMATCH_{field.upper()}")
    return checks, issues


def _guard_checks(current_guard: Any) -> tuple[dict[str, Any], list[str]]:
    guard = _as_dict(current_guard)
    checks = {
        "real_trade_enabled": _truthy(guard.get("real_trade_enabled")),
        "kiwoom_logged_in": _truthy(guard.get("kiwoom_logged_in")),
        "account_selected": _truthy(guard.get("account_selected")),
        "account_no": bool(_text(guard.get("account_no"))),
        "operator_confirmed": _truthy(guard.get("operator_confirmed")),
    }
    issues: list[str] = []
    if not guard:
        issues.append("CURRENT_GUARD_REQUIRED")
    if not checks["real_trade_enabled"]:
        issues.append("GUARD_REAL_TRADE_ENABLED_NOT_TRUE")
    if not checks["kiwoom_logged_in"]:
        issues.append("GUARD_KIWOOM_LOGGED_IN_NOT_TRUE")
    if not checks["account_selected"]:
        issues.append("GUARD_ACCOUNT_SELECTED_NOT_TRUE")
    if not checks["account_no"]:
        issues.append("GUARD_ACCOUNT_NO_REQUIRED")
    if not checks["operator_confirmed"]:
        issues.append("GUARD_OPERATOR_CONFIRMED_NOT_TRUE")
    return checks, issues


def evaluate_execution_final_send_gate_readiness(
    send_order_preview_adapter_result: Any,
    current_guard: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Evaluate readiness for a future Final Send Gate call without calling it."""
    confirmation = {
        "manual_final_send_confirmed": _as_dict(context).get("manual_final_send_confirmed") is True,
        "operator_confirmed_for_final_send": _as_dict(context).get("operator_confirmed_for_final_send") is True,
    }

    if not isinstance(send_order_preview_adapter_result, dict):
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation,
            issues=["MALFORMED_SEND_ORDER_PREVIEW_ADAPTER_RESULT"],
        )

    warnings = _as_list(send_order_preview_adapter_result.get("warnings"))
    adapter_status = _text(send_order_preview_adapter_result.get("status"))
    if adapter_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            required_confirmations=confirmation,
            issues=_as_list(send_order_preview_adapter_result.get("issues")) or ["SEND_ORDER_PREVIEW_ADAPTER_INVALID"],
            warnings=warnings,
        )
    if adapter_status != STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            required_confirmations=confirmation,
            issues=_as_list(send_order_preview_adapter_result.get("issues")) or ["SEND_ORDER_PREVIEW_ADAPTER_NOT_READY"],
            warnings=warnings,
        )

    adapter_preview = _as_dict(send_order_preview_adapter_result.get("adapter_preview_result"))
    if not adapter_preview:
        return _result(status=STATUS_BLOCKED, required_confirmations=confirmation, issues=["ADAPTER_PREVIEW_RESULT_REQUIRED"], warnings=warnings)

    adapter_issues: list[str] = []
    if adapter_preview.get("adapter_preview_ok") is not True:
        adapter_issues.append("ADAPTER_PREVIEW_OK_NOT_TRUE")
    if adapter_preview.get("next_stage") != FINAL_SEND_GATE_NEXT_STAGE_REQUIRED:
        adapter_issues.append("ADAPTER_PREVIEW_NEXT_STAGE_NOT_FINAL_SEND_GATE_REQUIRED")
    if adapter_preview.get("no_send") is not True:
        adapter_issues.append("ADAPTER_PREVIEW_NO_SEND_NOT_TRUE")
    if adapter_preview.get("send_order_called") is not False:
        adapter_issues.append("ADAPTER_PREVIEW_SEND_ORDER_CALLED_NOT_FALSE")
    blocked_reasons = adapter_preview.get("blocked_reasons")
    if isinstance(blocked_reasons, list) and blocked_reasons:
        adapter_issues.append("ADAPTER_PREVIEW_BLOCKED_REASONS_NOT_EMPTY")
    elif blocked_reasons not in (None, []) and not isinstance(blocked_reasons, list):
        adapter_issues.append("ADAPTER_PREVIEW_BLOCKED_REASONS_MALFORMED")

    request_preview = _as_dict(adapter_preview.get("send_order_request_preview"))
    if not request_preview:
        adapter_issues.append("SEND_ORDER_REQUEST_PREVIEW_REQUIRED")

    record = _as_dict(send_order_preview_adapter_result.get("order_queued_record"))
    if not record:
        adapter_issues.append("ORDER_QUEUED_RECORD_REQUIRED")

    identity = _as_dict(send_order_preview_adapter_result.get("identity"))
    if not identity:
        adapter_issues.append("IDENTITY_REQUIRED")

    identity_checks, identity_issues = _identity_checks(identity, request_preview, record)
    guard_checks, guard_issues = _guard_checks(current_guard)
    confirmation_ok = _manual_confirmed(context)
    if not confirmation_ok:
        adapter_issues.append("FINAL_SEND_CONFIRMATION_REQUIRED")

    issues = adapter_issues + identity_issues + guard_issues
    if issues:
        return _result(
            status=STATUS_BLOCKED,
            adapter_preview_result=adapter_preview,
            order_queued_record=record,
            identity=identity,
            identity_checks=identity_checks,
            guard_checks=guard_checks,
            required_confirmations=confirmation,
            issues=issues,
            warnings=warnings + _as_list(adapter_preview.get("warnings")),
        )

    return _result(
        status=STATUS_READY,
        final_send_gate_allowed=True,
        adapter_preview_result=adapter_preview,
        order_queued_record=record,
        identity=identity,
        identity_checks=identity_checks,
        guard_checks=guard_checks,
        required_confirmations=confirmation,
        warnings=warnings + _as_list(adapter_preview.get("warnings")),
    )
