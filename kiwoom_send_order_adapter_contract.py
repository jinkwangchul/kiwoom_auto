# -*- coding: utf-8 -*-
"""Build a preview-only Kiwoom SendOrder adapter contract.

This layer only standardizes the final in-memory payload needed by a Kiwoom
SendOrder adapter. It does not call SendOrder, broker adapters, GUI, queue, or
runtime writers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "SEND_ORDER_CONTRACT_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

SUPPORTED_BROKER = "KIWOOM"
ORDER_TYPE_MAP = {
    "BUY": 1,
    "SELL": 2,
}
ORDER_NAME_MAP = {
    "BUY": "BUY",
    "SELL": "SELL",
}
HOGA_MAP = {
    "LIMIT": "00",
    "LMT": "00",
    "00": "00",
    "MARKET": "03",
    "MKT": "03",
    "03": "03",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _result(
    *,
    status: str,
    send_order_adapter_contract: dict[str, Any] | None = None,
    send_order_params: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "send_order_adapter_contract": (
            deepcopy(send_order_adapter_contract)
            if isinstance(send_order_adapter_contract, dict)
            else {}
        ),
        "send_order_params": deepcopy(send_order_params) if isinstance(send_order_params, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "preview_only": True,
        "send_order_called": False,
        "broker_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _zero_or_positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def _extract_params(broker_dispatch_preview_result: dict[str, Any]) -> dict[str, Any]:
    params = _as_dict(broker_dispatch_preview_result.get("send_order_params_preview"))
    if params:
        return params
    broker_preview = _as_dict(broker_dispatch_preview_result.get("broker_dispatch_preview"))
    return _as_dict(broker_preview.get("dispatch_contract"))


def build_kiwoom_send_order_adapter_contract(
    broker_dispatch_preview_result: Any,
    kiwoom_account_context: Any,
    kiwoom_screen_context: Any,
) -> dict[str, Any]:
    """Build the final Kiwoom adapter contract without side effects."""
    preview = _as_dict(broker_dispatch_preview_result)
    account_context = _as_dict(kiwoom_account_context)
    screen_context = _as_dict(kiwoom_screen_context)

    if not preview:
        return _result(status=STATUS_INVALID, issues=["broker_dispatch_preview_result must be a dict"])
    if not isinstance(kiwoom_account_context, dict):
        return _result(status=STATUS_INVALID, issues=["kiwoom_account_context must be a dict"])
    if not isinstance(kiwoom_screen_context, dict):
        return _result(status=STATUS_INVALID, issues=["kiwoom_screen_context must be a dict"])

    preview_status = _upper(preview.get("status"))
    if preview_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["broker_dispatch_preview_result.status is INVALID"] + list(preview.get("issues") or []),
        )
    if preview_status != "BROKER_DISPATCH_READY":
        return _result(
            status=STATUS_BLOCKED,
            issues=["broker_dispatch_preview_result.status is not BROKER_DISPATCH_READY"]
            + list(preview.get("issues") or []),
        )
    if preview.get("send_order_called") is not False or preview.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["broker dispatch preview already called send order or broker"])

    params = _extract_params(preview)
    if not params:
        return _result(status=STATUS_INVALID, issues=["send_order_params_preview is required"])

    broker_type = _upper(params.get("broker_type"))
    if broker_type != SUPPORTED_BROKER:
        return _result(status=STATUS_BLOCKED, issues=["broker_type is not KIWOOM"])

    account_no = _text(account_context.get("account_no") or params.get("account_no"))
    if not account_no:
        return _result(status=STATUS_INVALID, issues=["account_no is required"])

    screen_no = _text(
        screen_context.get("screen_no")
        or screen_context.get("send_order_screen_no")
        or params.get("screen_no")
    )
    if not screen_no:
        return _result(status=STATUS_INVALID, issues=["screen_no is required"])

    side = _upper(params.get("side") or params.get("order_type"))
    if side not in ORDER_TYPE_MAP:
        return _result(status=STATUS_INVALID, issues=["order_type mapping failed"])

    hoga_key = _upper(params.get("hoga"))
    hoga_code = HOGA_MAP.get(hoga_key, "")
    if not hoga_code:
        return _result(status=STATUS_INVALID, issues=["hoga mapping failed"])

    code = _text(params.get("code"))
    quantity = params.get("quantity")
    price = params.get("price")
    if not code:
        return _result(status=STATUS_INVALID, issues=["code is required"])
    if not _positive_number(quantity):
        return _result(status=STATUS_INVALID, issues=["quantity must be greater than 0"])
    if hoga_code == "00" and not _positive_number(price):
        return _result(status=STATUS_INVALID, issues=["LIMIT price must be greater than 0"])
    if hoga_code == "03" and not _zero_or_positive_number(price):
        return _result(status=STATUS_INVALID, issues=["MARKET price must be zero or greater"])

    required_text = {
        "dispatch_id": params.get("dispatch_id"),
        "order_id": params.get("order_id"),
    }
    missing = [key for key, value in required_text.items() if not _text(value)]
    if missing:
        return _result(status=STATUS_INVALID, issues=[f"{key} is required" for key in missing])

    send_order_params = {
        "screen_no": screen_no,
        "order_name": ORDER_NAME_MAP[side],
        "account_no": account_no,
        "order_type": ORDER_TYPE_MAP[side],
        "code": code,
        "quantity": _number(quantity),
        "price": _number(price),
        "hoga": hoga_code,
        "original_order_no": _text(params.get("original_order_no") or params.get("org_order_no")),
    }
    contract = {
        "dispatch_id": _text(params.get("dispatch_id")),
        "order_id": _text(params.get("order_id")),
        "account_no": account_no,
        "screen_no": screen_no,
        "order_name": send_order_params["order_name"],
        "order_type": send_order_params["order_type"],
        "code": code,
        "quantity": send_order_params["quantity"],
        "price": send_order_params["price"],
        "hoga": hoga_code,
        "original_order_no": send_order_params["original_order_no"],
        "send_order_params": deepcopy(send_order_params),
    }

    return _result(
        status=STATUS_READY,
        send_order_adapter_contract=contract,
        send_order_params=send_order_params,
        issues=[],
        warnings=list(preview.get("warnings") or []),
    )
