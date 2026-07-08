# -*- coding: utf-8 -*-
"""Create the final preview-only Kiwoom SendOrder call payload."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "SEND_ORDER_CALL_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    send_order_call_preview: dict[str, Any] | None = None,
    send_order_args: list[Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "send_order_call_preview": (
            deepcopy(send_order_call_preview)
            if isinstance(send_order_call_preview, dict)
            else {}
        ),
        "send_order_args": deepcopy(send_order_args) if isinstance(send_order_args, list) else [],
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "send_order_called": False,
        "broker_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _validate_send_order_params(params: dict[str, Any]) -> str | None:
    required = (
        "screen_no",
        "order_name",
        "account_no",
        "order_type",
        "code",
        "quantity",
        "price",
        "hoga",
        "original_order_no",
    )
    for field in required:
        if field == "original_order_no":
            if field not in params:
                return "send_order_params.original_order_no is required"
            continue
        value = params.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"send_order_params.{field} is required"
    return None


def _build_send_order_args(params: dict[str, Any]) -> list[Any]:
    return [
        params.get("screen_no"),
        params.get("order_name"),
        params.get("account_no"),
        params.get("order_type"),
        params.get("code"),
        params.get("quantity"),
        params.get("price"),
        params.get("hoga"),
        params.get("original_order_no"),
    ]


def preview_kiwoom_send_order_call(
    safety_gate_result: Any,
    adapter_contract_result: Any,
    call_context: Any,
) -> dict[str, Any]:
    """Build final SendOrder call arguments without calling SendOrder."""
    safety = _as_dict(safety_gate_result)
    contract_result = _as_dict(adapter_contract_result)
    context = _as_dict(call_context)

    if not safety:
        return _result(status=STATUS_INVALID, issues=["safety_gate_result must be a dict"])
    if not contract_result:
        return _result(status=STATUS_INVALID, issues=["adapter_contract_result must be a dict"])
    if not isinstance(call_context, dict):
        return _result(status=STATUS_INVALID, issues=["call_context must be a dict"])

    safety_status = _text(safety.get("status")).upper()
    if safety_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["safety_gate_result.status is INVALID"] + list(safety.get("issues") or []),
        )
    if safety_status != "SEND_ORDER_SAFE":
        return _result(
            status=STATUS_BLOCKED,
            issues=["safety_gate_result.status is not SEND_ORDER_SAFE"] + list(safety.get("issues") or []),
        )
    if safety.get("send_order_allowed") is not True:
        return _result(status=STATUS_BLOCKED, issues=["safety_gate_result.send_order_allowed is not true"])
    if safety.get("send_order_called") is not False or safety.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["safety gate already called send order or broker"])

    contract_status = _text(contract_result.get("status")).upper()
    if contract_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["adapter_contract_result.status is INVALID"] + list(contract_result.get("issues") or []),
        )
    if contract_status != "SEND_ORDER_CONTRACT_READY":
        return _result(
            status=STATUS_BLOCKED,
            issues=["adapter_contract_result.status is not SEND_ORDER_CONTRACT_READY"]
            + list(contract_result.get("issues") or []),
        )
    if contract_result.get("send_order_called") is not False or contract_result.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["adapter contract already called send order or broker"])

    final_call_token = _text(context.get("final_call_token"))
    if not final_call_token:
        return _result(status=STATUS_BLOCKED, issues=["call_context.final_call_token is required"])

    params = _as_dict(contract_result.get("send_order_params"))
    if not params:
        adapter_contract = _as_dict(contract_result.get("send_order_adapter_contract"))
        params = _as_dict(adapter_contract.get("send_order_params"))
    if not params:
        return _result(status=STATUS_INVALID, issues=["send_order_params is required"])

    params_issue = _validate_send_order_params(params)
    if params_issue:
        return _result(status=STATUS_INVALID, issues=[params_issue])

    adapter_contract = _as_dict(contract_result.get("send_order_adapter_contract"))
    send_order_args = _build_send_order_args(params)
    preview = {
        "preview_type": "KIWOOM_SEND_ORDER_CALL_PREVIEW",
        "final_call_token": final_call_token,
        "dispatch_id": _text(adapter_contract.get("dispatch_id")),
        "order_id": _text(adapter_contract.get("order_id")),
        "account_no": _text(params.get("account_no")),
        "screen_no": _text(params.get("screen_no")),
        "send_order_params": deepcopy(params),
        "send_order_args_ready": True,
        "send_order_arg_order": [
            "screen_no",
            "order_name",
            "account_no",
            "order_type",
            "code",
            "quantity",
            "price",
            "hoga",
            "original_order_no",
        ],
    }
    return _result(
        status=STATUS_READY,
        send_order_call_preview=preview,
        send_order_args=send_order_args,
        issues=[],
        warnings=list(safety.get("warnings") or []) + list(contract_result.get("warnings") or []),
    )
