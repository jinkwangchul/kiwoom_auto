# -*- coding: utf-8 -*-
"""Preview-only order type mapper.

This module intentionally does not know Kiwoom OpenAPI order type codes and
does not promote an order into an executable state. It only normalizes
human/UI intent from order_intent into this project's internal preview
order_type values.
"""

from __future__ import annotations

from typing import Any


ORDER_TYPE_BUY = "BUY"
ORDER_TYPE_SELL = "SELL"

_INTENT_KEYS = (
    "side",
    "order_side",
    "order_type",
    "type",
    "action",
    "intent",
    "signal",
    "trade_side",
)

_BUY_TERMS = {
    "BUY",
    "BUY_ORDER",
    "\ub9e4\uc218",
}

_SELL_TERMS = {
    "SELL",
    "SELL_ORDER",
    "\ub9e4\ub3c4",
}

_UNRESOLVED_TERMS = {
    "",
    "UNKNOWN",
    "UNDECIDED",
    "UNRESOLVED",
    "NONE",
    "NULL",
    "N/A",
    "NA",
    "\ubbf8\ud655\uc815",
    "\uc54c \uc218 \uc5c6\uc74c",
    "\uc54c\uc218\uc5c6\uc74c",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize(value: Any) -> str:
    return _clean_text(value).upper().replace("-", "_").replace(" ", "_")


def _extract_order_intent(order: Any) -> dict[str, Any]:
    if not isinstance(order, dict):
        return {}

    intent = order.get("order_intent")
    if isinstance(intent, dict):
        return intent

    return order


def _extract_order_type_intent(order_intent: dict[str, Any]) -> tuple[Any, str | None]:
    for key in _INTENT_KEYS:
        if key not in order_intent:
            continue

        value = order_intent.get(key)
        if isinstance(value, dict):
            nested_value, nested_key = _extract_order_type_intent(value)
            if nested_key is not None:
                return nested_value, f"{key}.{nested_key}"
            continue

        return value, key

    return None, None


def _unresolved_result(warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "order_type": None,
        "source": "order_intent",
        "unresolved": True,
        "warnings": warnings,
    }


def map_order_type_preview(order: Any) -> dict[str, Any]:
    """Map order_intent side wording to an internal preview order_type value.

    Supported phase-1 mappings:
    - BUY / 매수 -> BUY
    - SELL / 매도 -> SELL
    - empty, undecided, unknown, or unsupported values stay unresolved
    """
    warnings: list[str] = []

    order_intent = _extract_order_intent(order)
    if not order_intent:
        warnings.append("order_intent missing or empty; order_type unresolved")
        return _unresolved_result(warnings)

    raw_value, source_key = _extract_order_type_intent(order_intent)
    normalized = _normalize(raw_value)
    display_value = _clean_text(raw_value)

    if source_key is None or normalized in _UNRESOLVED_TERMS:
        warnings.append("order_intent order_type is unresolved")
        return _unresolved_result(warnings)

    if normalized in _BUY_TERMS:
        return {
            "ok": True,
            "order_type": ORDER_TYPE_BUY,
            "source": "order_intent",
            "unresolved": False,
            "warnings": warnings,
        }

    if normalized in _SELL_TERMS:
        return {
            "ok": True,
            "order_type": ORDER_TYPE_SELL,
            "source": "order_intent",
            "unresolved": False,
            "warnings": warnings,
        }

    warnings.append(f"unsupported order_intent order_type value: {display_value}")
    return _unresolved_result(warnings)
