# -*- coding: utf-8 -*-
"""Preview broker dispatch capability and SendOrder parameters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "BROKER_DISPATCH_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
PREVIEW_TYPE = "BROKER_DISPATCH_PREVIEW"
VALID_SIDES = {"BUY", "SELL"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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
    broker_dispatch_preview: dict[str, Any] | None = None,
    send_order_params_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "broker_dispatch_preview": deepcopy(broker_dispatch_preview) if isinstance(broker_dispatch_preview, dict) else {},
        "send_order_params_preview": deepcopy(send_order_params_preview) if isinstance(send_order_params_preview, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "preview_only": True,
        "broker_called": False,
        "send_order_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _normalize_hoga(value: Any) -> str:
    text = _text(value).upper()
    if text in {"MARKET", "MKT", "03"} or _text(value) == "시장가":
        return "MARKET"
    if text in {"LIMIT", "LMT", "00"} or _text(value) in {"지정가", "현재가"}:
        return "LIMIT"
    return text


def _capability_values(capabilities: dict[str, Any], key: str, fallback: set[str]) -> set[str]:
    values = capabilities.get(key)
    if values is None:
        return set(fallback)
    if not isinstance(values, list):
        return set()
    return {_text(value).upper() for value in values if _text(value)}


def _market_open(market_context: dict[str, Any]) -> bool:
    if market_context.get("market_open") is False:
        return False
    if market_context.get("session_open") is False:
        return False
    status = _text(market_context.get("status") or market_context.get("session")).upper()
    if status in {"CLOSED", "AFTER_HOURS", "HALTED"}:
        return False
    return True


def _build_params(contract: dict[str, Any], hoga: str) -> dict[str, Any]:
    return {
        "account_no": _text(contract.get("account_no")),
        "broker_type": _text(contract.get("broker_type")),
        "order_id": _text(contract.get("order_id")),
        "source_order_id": _text(contract.get("source_order_id")),
        "source_signal_id": _text(contract.get("source_signal_id")),
        "code": _text(contract.get("code")),
        "side": _text(contract.get("side")).upper(),
        "quantity": deepcopy(contract.get("quantity")),
        "price": deepcopy(contract.get("price")),
        "hoga": hoga,
        "request_hash": _text(contract.get("request_hash")),
        "dispatch_id": _text(contract.get("dispatch_id")),
    }


def preview_broker_dispatch(
    dispatch_builder_result: Any,
    broker_capabilities: Any,
    market_context: Any,
) -> dict[str, Any]:
    """Create a preview-only broker dispatch and SendOrder params plan."""
    builder = _as_dict(dispatch_builder_result)
    capabilities = _as_dict(broker_capabilities)
    market = _as_dict(market_context)

    if not builder:
        return _result(status=STATUS_INVALID, issues=["dispatch_builder_result must be a dict"])
    if not capabilities:
        return _result(status=STATUS_INVALID, issues=["broker_capabilities must be a non-empty dict"])
    if not isinstance(market_context, dict):
        return _result(status=STATUS_INVALID, issues=["market_context must be a dict"])

    builder_status = _text(builder.get("status")).upper()
    if builder_status == "INVALID":
        return _result(status=STATUS_INVALID, issues=["dispatch_builder_result.status is INVALID"] + _as_list(builder.get("issues")))
    if builder_status != "DISPATCH_READY":
        return _result(status=STATUS_BLOCKED, issues=["dispatch_builder_result.status is not DISPATCH_READY"] + _as_list(builder.get("issues")))
    if builder.get("send_order_ready") is not True:
        return _result(status=STATUS_BLOCKED, issues=["dispatch_builder_result.send_order_ready is not true"])
    if builder.get("send_order_called") is not False or builder.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["dispatch builder already called send order or broker"])

    contract = _as_dict(builder.get("dispatch_contract"))
    if not contract:
        return _result(status=STATUS_INVALID, issues=["dispatch_contract is required"])
    broker_type = _text(contract.get("broker_type")).upper()
    supported_brokers = _capability_values(capabilities, "supported_brokers", {broker_type})
    if not supported_brokers:
        return _result(status=STATUS_INVALID, issues=["broker_capabilities.supported_brokers must be a list"])
    if broker_type not in supported_brokers:
        return _result(status=STATUS_BLOCKED, issues=["broker_type is not supported"])
    if not _market_open(market):
        return _result(status=STATUS_BLOCKED, issues=["market/session is not open"])

    side = _text(contract.get("side")).upper()
    supported_sides = _capability_values(capabilities, "supported_sides", VALID_SIDES)
    if side not in VALID_SIDES or side not in supported_sides:
        return _result(status=STATUS_BLOCKED, issues=["side is not supported by broker"])

    hoga = _normalize_hoga(contract.get("hoga"))
    supported_hogas = _capability_values(capabilities, "supported_hogas", {"MARKET", "LIMIT"})
    if hoga not in supported_hogas:
        return _result(status=STATUS_BLOCKED, issues=["hoga is not supported by broker"])
    if not _positive_number(contract.get("quantity")):
        return _result(status=STATUS_BLOCKED, issues=["quantity must be greater than 0"])
    if hoga == "LIMIT" and not _positive_number(contract.get("price")):
        return _result(status=STATUS_BLOCKED, issues=["LIMIT price must be greater than 0"])
    if hoga == "MARKET" and not _zero_or_positive_number(contract.get("price")):
        return _result(status=STATUS_BLOCKED, issues=["MARKET price must be zero or greater"])
    if not _text(contract.get("account_no")):
        return _result(status=STATUS_INVALID, issues=["dispatch_contract.account_no is required"])

    params = _build_params(contract, hoga)
    missing = [key for key, value in params.items() if value in (None, "")]
    if missing:
        return _result(status=STATUS_INVALID, issues=[f"send_order_params_preview.{key} is required" for key in missing])

    broker_preview = {
        "preview_type": PREVIEW_TYPE,
        "broker_type": broker_type,
        "market_open": True,
        "supported_broker": True,
        "side_supported": True,
        "hoga_supported": True,
        "send_order_params_ready": True,
        "dispatch_contract": deepcopy(contract),
    }
    return _result(
        status=STATUS_READY,
        broker_dispatch_preview=broker_preview,
        send_order_params_preview=params,
        issues=[],
        warnings=_as_list(builder.get("warnings")),
    )
