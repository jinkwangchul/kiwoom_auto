# -*- coding: utf-8 -*-
"""Preview-only safety gate before a Kiwoom SendOrder call."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_SAFE = "SEND_ORDER_SAFE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

VALID_ORDER_TYPES = {1, 2, 3, 4, 5, 6}
CANCEL_ORDER_TYPES = {3, 4}
MODIFY_ORDER_TYPES = {5, 6}
VALID_HOGAS = {"00", "03"}


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
    return _text(value).upper() in {"TRUE", "YES", "Y", "1", "ON"}


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


def _result(
    *,
    status: str,
    safety: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    send_order_allowed: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "safety": deepcopy(safety) if isinstance(safety, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "send_order_allowed": bool(send_order_allowed),
        "send_order_called": False,
        "broker_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _contains_identity(items: Any, *, dispatch_id: str, order_id: str, account_no: str) -> bool:
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        item_dispatch_id = _text(item.get("dispatch_id"))
        item_order_id = _text(item.get("order_id"))
        item_account_no = _text(item.get("account_no"))
        if dispatch_id and item_dispatch_id == dispatch_id:
            return True
        if order_id and item_order_id == order_id and (not account_no or item_account_no in {"", account_no}):
            return True
    return False


def _has_runtime_lock(snapshot: dict[str, Any], *, order_id: str, dispatch_id: str, account_no: str) -> bool:
    if snapshot.get("runtime_lock") is True or snapshot.get("locked") is True:
        return True
    return _contains_identity(snapshot.get("locks"), dispatch_id=dispatch_id, order_id=order_id, account_no=account_no)


def _has_duplicate_dispatch(snapshot: dict[str, Any], *, dispatch_id: str, order_id: str, account_no: str) -> bool:
    for key in ("dispatches", "existing_dispatches", "sent_dispatches", "send_order_dispatches"):
        if _contains_identity(snapshot.get(key), dispatch_id=dispatch_id, order_id=order_id, account_no=account_no):
            return True
    return False


def _validate_screen_no(screen_no: str) -> bool:
    return len(screen_no) == 4 and screen_no.isdigit()


def _validate_params(params: dict[str, Any]) -> str | None:
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

    if params.get("order_type") not in VALID_ORDER_TYPES:
        return "send_order_params.order_type is invalid"
    if _text(params.get("hoga")) not in VALID_HOGAS:
        return "send_order_params.hoga is invalid"
    if not _positive_number(params.get("quantity")):
        return "send_order_params.quantity must be greater than 0"
    order_type = params.get("order_type")
    if order_type in CANCEL_ORDER_TYPES | MODIFY_ORDER_TYPES and not _text(params.get("original_order_no")):
        return "send_order_params.original_order_no is required for cancel/modify"
    if order_type in CANCEL_ORDER_TYPES and not _zero_or_positive_number(params.get("price")):
        return "send_order_params cancel price must be zero or greater"
    if order_type not in CANCEL_ORDER_TYPES and _text(params.get("hoga")) == "00" and not _positive_number(params.get("price")):
        return "send_order_params LIMIT price must be greater than 0"
    if order_type not in CANCEL_ORDER_TYPES and _text(params.get("hoga")) == "03" and not _zero_or_positive_number(params.get("price")):
        return "send_order_params MARKET price must be zero or greater"
    if not _validate_screen_no(_text(params.get("screen_no"))):
        return "send_order_params.screen_no is invalid"
    return None


def evaluate_kiwoom_send_order_safety(
    send_order_adapter_contract_result: Any,
    runtime_snapshot: Any,
    kiwoom_connection_state: Any,
    operator_context: Any,
) -> dict[str, Any]:
    """Evaluate final safety conditions without calling SendOrder."""
    contract_result = _as_dict(send_order_adapter_contract_result)
    snapshot = _as_dict(runtime_snapshot)
    connection = _as_dict(kiwoom_connection_state)
    operator = _as_dict(operator_context)

    if not contract_result:
        return _result(status=STATUS_INVALID, issues=["send_order_adapter_contract_result must be a dict"])
    if not isinstance(runtime_snapshot, dict):
        return _result(status=STATUS_INVALID, issues=["runtime_snapshot must be a dict"])
    if not isinstance(kiwoom_connection_state, dict):
        return _result(status=STATUS_INVALID, issues=["kiwoom_connection_state must be a dict"])
    if not isinstance(operator_context, dict):
        return _result(status=STATUS_INVALID, issues=["operator_context must be a dict"])

    contract_status = _text(contract_result.get("status")).upper()
    if contract_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["send_order_adapter_contract_result.status is INVALID"] + list(contract_result.get("issues") or []),
        )
    if contract_status != "SEND_ORDER_CONTRACT_READY":
        return _result(
            status=STATUS_BLOCKED,
            issues=["send_order_adapter_contract_result.status is not SEND_ORDER_CONTRACT_READY"]
            + list(contract_result.get("issues") or []),
        )
    if contract_result.get("send_order_called") is not False or contract_result.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["contract already called send order or broker"])

    adapter_contract = _as_dict(contract_result.get("send_order_adapter_contract"))
    params = _as_dict(contract_result.get("send_order_params") or adapter_contract.get("send_order_params"))
    if not adapter_contract:
        return _result(status=STATUS_INVALID, issues=["send_order_adapter_contract is required"])
    if not params:
        return _result(status=STATUS_INVALID, issues=["send_order_params is required"])

    dispatch_id = _text(adapter_contract.get("dispatch_id"))
    order_id = _text(adapter_contract.get("order_id"))
    account_no = _text(adapter_contract.get("account_no"))
    screen_no = _text(adapter_contract.get("screen_no"))
    missing = [
        field
        for field, value in {
            "dispatch_id": dispatch_id,
            "order_id": order_id,
            "account_no": account_no,
            "screen_no": screen_no,
        }.items()
        if not value
    ]
    if missing:
        return _result(status=STATUS_INVALID, issues=[f"{field} is required" for field in missing])

    params_issue = _validate_params(params)
    if params_issue:
        return _result(status=STATUS_INVALID, issues=[params_issue])

    if not _validate_screen_no(screen_no):
        return _result(status=STATUS_BLOCKED, issues=["screen_no is invalid"])

    if not (_truthy(connection.get("connected")) or _truthy(connection.get("kiwoom_connected"))):
        return _result(status=STATUS_BLOCKED, issues=["kiwoom is not connected"])

    connection_account = _text(connection.get("account_no") or connection.get("selected_account_no"))
    if not connection_account:
        return _result(status=STATUS_BLOCKED, issues=["kiwoom_connection_state.account_no is required"])
    if connection_account != account_no:
        return _result(status=STATUS_BLOCKED, issues=["account_no does not match kiwoom connection state"])

    if not (
        operator.get("operator_final_send_confirmed") is True
        or operator.get("manual_send_order_confirmed") is True
        or operator.get("manual_kiwoom_send_order_confirmed") is True
    ):
        return _result(status=STATUS_BLOCKED, issues=["operator final send confirmation is required"])

    if (
        snapshot.get("emergency_stop") is True
        or operator.get("emergency_stop") is True
        or connection.get("emergency_stop") is True
    ):
        return _result(status=STATUS_BLOCKED, issues=["emergency stop is active"])

    if _has_runtime_lock(snapshot, order_id=order_id, dispatch_id=dispatch_id, account_no=account_no):
        return _result(status=STATUS_BLOCKED, issues=["runtime lock exists"])

    if _has_duplicate_dispatch(snapshot, order_id=order_id, dispatch_id=dispatch_id, account_no=account_no):
        return _result(status=STATUS_BLOCKED, issues=["duplicate dispatch exists"])

    safety = {
        "contract_ready": True,
        "kiwoom_connected": True,
        "account_matched": True,
        "operator_final_confirmed": True,
        "runtime_lock_absent": True,
        "emergency_stop_absent": True,
        "duplicate_dispatch_absent": True,
        "screen_no_valid": True,
        "order_params_valid": True,
        "dispatch_id": dispatch_id,
        "order_id": order_id,
        "account_no": account_no,
        "screen_no": screen_no,
    }
    return _result(
        status=STATUS_SAFE,
        safety=safety,
        issues=[],
        warnings=list(contract_result.get("warnings") or []),
        send_order_allowed=True,
    )
