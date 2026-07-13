# -*- coding: utf-8 -*-
"""Preview-only SELL method execution structure validator.

This module reads approved ``sell.method.*`` rules and a SELL signal/market
context, then builds selected-set previews only. It never creates an executable
order request and never connects queue, runtime, execution, or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "SELL_METHOD_EXECUTION_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
METHOD_SET_ORDER = ("setting_a", "setting_b", "setting_c")
SAFETY_FLAGS = ("execution_connected", "runtime_write", "send_order", "queue_write")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _signal_side(signal: dict[str, Any]) -> str:
    for key in ("signal", "signal_type", "side"):
        value = _text(signal.get(key)).upper()
        if value:
            return value
    routine_signal = _as_dict(signal.get("routine_signal"))
    value = _text(routine_signal.get("signal")).upper()
    return value


def _signal_id(signal: dict[str, Any]) -> str | None:
    for key in ("signal_id", "source_signal_id", "id"):
        value = _text(signal.get(key))
        if value:
            return value
    return None


def _symbol(signal: dict[str, Any], market: dict[str, Any]) -> str | None:
    for source in (market, signal):
        for key in ("symbol", "code"):
            value = _text(source.get(key))
            if value:
                return value
    return None


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_price(market: dict[str, Any]) -> float | None:
    for key in ("current_price", "price", "latest_price", "close"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _holding_qty(market: dict[str, Any]) -> float | None:
    for key in ("holding_qty", "holding_quantity", "available_qty", "available_quantity", "quantity"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _average_price(market: dict[str, Any]) -> float | None:
    for key in ("average_price", "avg_price", "avg_buy_price"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _method_rules(approved_rules: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(approved_rules.get("sell")).get("method"))


def _selected_sets(method: dict[str, Any]) -> tuple[list[str], list[str]]:
    selected = method.get("selected_sets")
    if selected is None:
        return [], ["selected_sets is required"]
    if not isinstance(selected, list):
        return [], ["selected_sets must be a list"]
    if not selected:
        return [], ["selected_sets is empty"]

    selected_text = [_text(item) for item in selected if _text(item)]
    ordered = [method_set for method_set in METHOD_SET_ORDER if method_set in selected_text]
    unsupported = [method_set for method_set in selected_text if method_set not in METHOD_SET_ORDER]
    reasons = [f"unsupported method set: {method_set}" for method_set in unsupported]
    return ordered + unsupported, reasons


def _status(reasons: list[str], invalid_reasons: list[str]) -> str:
    if invalid_reasons:
        return STATUS_INVALID
    if reasons:
        return STATUS_BLOCKED
    return STATUS_READY


def _method_preview(method_set: str, method: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    reasons: list[str] = []
    invalid_reasons: list[str] = []
    snapshot: dict[str, Any] | None = None

    if method_set not in METHOD_SET_ORDER:
        reasons.append(f"unsupported method set: {method_set}")
    else:
        setting = method.get(method_set)
        if setting is None:
            reasons.append(f"method setting is required: {method_set}")
        elif not isinstance(setting, dict):
            invalid_reasons.append(f"method setting must be a dict: {method_set}")
        else:
            snapshot = deepcopy(setting)
            for flag in SAFETY_FLAGS:
                if setting.get(flag) is True:
                    invalid_reasons.append(f"method setting safety flag must be false: {method_set}.{flag}")

    method_status = _status(reasons, invalid_reasons)
    return (
        {
            "method_set": method_set,
            "status": method_status,
            "quantity": None,
            "price": None,
            "hoga": None,
            "order_type": None,
            "method_snapshot": snapshot,
            "reasons": list(reasons + invalid_reasons),
            "warnings": [],
        },
        reasons,
        invalid_reasons,
    )


def build_sell_method_execution_preview(
    sell_signal_preview: Any,
    approved_rules: Any,
    market_context: Any,
) -> dict[str, Any]:
    """Build selected SELL method previews without making an order request."""
    signal = deepcopy(_as_dict(sell_signal_preview))
    rules = deepcopy(_as_dict(approved_rules))
    market = deepcopy(_as_dict(market_context))
    method = _method_rules(rules)

    reasons: list[str] = []
    invalid_reasons: list[str] = []
    warnings: list[str] = []

    side = _signal_side(signal)
    if side != "SELL":
        reasons.append("SELL signal is required")

    symbol = _symbol(signal, market)
    if not symbol:
        reasons.append("symbol is required")

    current_price = _current_price(market)
    if current_price is None:
        reasons.append("current_price is required")

    holding_qty = _holding_qty(market)
    if holding_qty is None or holding_qty <= 0:
        reasons.append("holding_qty must be greater than 0")

    selected_sets, selected_reasons = _selected_sets(method)
    reasons.extend(selected_reasons)

    method_previews: list[dict[str, Any]] = []
    for method_set in selected_sets:
        preview, method_reasons, method_invalid_reasons = _method_preview(method_set, method)
        method_previews.append(preview)
        reasons.extend(method_reasons)
        invalid_reasons.extend(method_invalid_reasons)

    for flag in SAFETY_FLAGS:
        if signal.get(flag) is True or market.get(flag) is True:
            invalid_reasons.append(f"safety flag must be false: {flag}")

    status = _status(reasons, invalid_reasons)
    all_reasons = list(reasons + invalid_reasons)

    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "send_order": False,
        "queue_write": False,
        "status": status,
        "ready": status == STATUS_READY,
        "symbol": symbol,
        "side": "SELL" if side == "SELL" else side or None,
        "signal_id": _signal_id(signal),
        "matched_groups": deepcopy(signal.get("matched_groups", [])) if isinstance(signal.get("matched_groups"), list) else [],
        "selected_sets": deepcopy(selected_sets),
        "method_previews": method_previews,
        "quantity": None,
        "price": None,
        "hoga": None,
        "order_type": None,
        "current_price": current_price,
        "average_price": _average_price(market),
        "holding_qty": holding_qty,
        "reasons": all_reasons,
        "warnings": warnings,
    }
