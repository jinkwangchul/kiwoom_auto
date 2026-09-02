# -*- coding: utf-8 -*-
"""Indicator-follow single-order SELL intent construction."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
_METHOD_SET_ORDER = ("setting_a", "setting_b", "setting_c")
_SINGLE_HOGA_TERMS = {"SINGLE", "SINGLE_HOGA", "단일호가"}
_MULTI_HOGA_TERMS = {"MULTI", "MULTI_HOGA", "다중호가"}
_ORDER_PRICE_TERMS = {"ORDER_PRICE", "LIMIT", "주문가"}
_MARKET_TERMS = {"MARKET", "시장가"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_term(value: Any) -> str:
    text = _text(value)
    return text if any("가" <= char <= "힣" for char in text) else text.upper()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _blocked(reason: str) -> dict[str, Any]:
    return {"status": "BLOCKED", "reason": reason, "execution_intent": None}


def _effective_sell_method(rules: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str]:
    method = _as_dict(_as_dict(rules.get("sell")).get("method"))
    selected = method.get("selected_sets")
    if not isinstance(selected, list):
        return None, None, "SELL_SELECTED_SET_INVALID"
    selected_sets = [
        _text(value)
        for value in selected
        if _text(value)
    ]
    if len(selected_sets) != 1:
        return None, None, "SELL_SELECTED_SET_COUNT_INVALID"
    method_set = selected_sets[0]
    if method_set not in _METHOD_SET_ORDER:
        return None, None, "SELL_SELECTED_SET_INVALID"
    setting = method.get(method_set)
    if not isinstance(setting, dict):
        return None, None, "SELL_SELECTED_SETTING_MISSING"
    return method_set, deepcopy(setting), ""


def build_indicator_follow_sell_intent(
    *,
    sell_signal_result: Any,
    context: Any,
) -> dict[str, Any]:
    """Return one Routine-owned SELL intent or a fail-closed block result."""
    signal = deepcopy(_as_dict(sell_signal_result))
    runtime_context = _as_dict(context)
    cycle = _as_dict(runtime_context.get("cycle"))

    if _text(cycle.get("status")).lower() != "resolved":
        return _blocked(_text(cycle.get("unresolved_reason")) or "CYCLE_PROJECTION_UNRESOLVED")

    holding_qty = _positive_int(cycle.get("holding_qty"))
    if holding_qty is None:
        return _blocked("SELL_HOLDING_QUANTITY_INVALID")

    routine_instance_id = _text(
        runtime_context.get("routine_instance_id") or signal.get("routine_instance_id")
    )
    if not routine_instance_id:
        return _blocked("SELL_ROUTINE_INSTANCE_ID_MISSING")

    rules = _as_dict(runtime_context.get("rules"))
    if not rules:
        rules = _as_dict(runtime_context.get("routine_config"))
    method_set, setting, method_reason = _effective_sell_method(rules)
    if method_reason:
        return _blocked(method_reason)
    assert method_set is not None and setting is not None

    hoga_mode = _normalized_term(setting.get("perform1_title_combo"))
    if hoga_mode in _MULTI_HOGA_TERMS:
        return _blocked("SELL_MULTI_HOGA_NOT_IMPLEMENTED")
    if hoga_mode not in _SINGLE_HOGA_TERMS:
        return _blocked("SELL_SINGLE_HOGA_POLICY_INVALID")

    single_method = _normalized_term(setting.get("perform1_single_combo"))
    if single_method in _ORDER_PRICE_TERMS:
        price = _positive_number(
            runtime_context.get("reference_price", runtime_context.get("current_price"))
        )
        if price is None:
            return _blocked("SELL_ORDER_PRICE_MISSING")
        price_basis = "ORDER_PRICE"
        hoga = "LIMIT"
    elif single_method in _MARKET_TERMS:
        price = None
        price_basis = "MARKET"
        hoga = "MARKET"
    else:
        return _blocked("SELL_SINGLE_HOGA_PRICE_POLICY_INVALID")

    source_signal_id = _text(
        signal.get("source_signal_id")
        or signal.get("signal_id")
        or signal.get("id")
        or runtime_context.get("source_signal_id")
    )
    intent = {
        "side": "SELL",
        "quantity": holding_qty,
        "budget": None,
        "price_basis": price_basis,
        "price": price,
        "hoga": hoga,
        "hoga_mode": "SINGLE",
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": routine_instance_id,
        # The canonical signal queue fills this from its generated record id
        # when evaluation occurs before a durable signal identity exists.
        "source_signal_id": source_signal_id or None,
        "cycle_identity": cycle.get("cycle_identity"),
        "sell_method_set": method_set,
        "sell_perform1": {
            "hoga_mode": "SINGLE",
            "price_basis": price_basis,
        },
    }
    return {
        "status": STATUS_READY,
        "reason": "",
        "execution_intent": intent,
        "selected_method_set": method_set,
    }
