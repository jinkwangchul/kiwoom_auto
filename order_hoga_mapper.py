# -*- coding: utf-8 -*-
"""Preview-only order hoga mapper.

This module intentionally does not know Kiwoom OpenAPI hoga codes and does not
promote an order into an executable state. It only normalizes human/UI intent
from order_intent into this project's internal preview hoga values.
"""

from __future__ import annotations

from typing import Any


HOGA_MARKET = "MARKET"
HOGA_LIMIT = "LIMIT"

_INTENT_KEYS = (
    "hoga",
    "hoga_intent",
    "hoga_type",
    "hoga_name",
    "order_hoga",
    "order_method",
    "order_price_type",
    "price_type",
    "method",
    "ui_hoga",
)

_MARKET_TERMS = {
    "MARKET",
    "MARKET_ORDER",
    "MKT",
    "\uc2dc\uc7a5\uac00",
}

_LIMIT_TERMS = {
    "LIMIT",
    "LIMIT_ORDER",
    "LMT",
    "CURRENT_PRICE",
    "\uc9c0\uc815\uac00",
    "\ud604\uc7ac\uac00",
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


def _extract_hoga_intent(order_intent: dict[str, Any]) -> tuple[Any, str | None]:
    for key in _INTENT_KEYS:
        if key not in order_intent:
            continue

        value = order_intent.get(key)
        if isinstance(value, dict):
            nested_value, nested_key = _extract_hoga_intent(value)
            if nested_key is not None:
                return nested_value, f"{key}.{nested_key}"
            continue

        return value, key

    return None, None


def _unresolved_result(warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "hoga": None,
        "source": "order_intent",
        "unresolved": True,
        "warnings": warnings,
    }


def map_order_hoga_preview(order: Any) -> dict[str, Any]:
    """Map order_intent hoga wording to an internal preview hoga value.

    Supported phase-1 mappings:
    - 시장가 / MARKET -> MARKET
    - 현재가 / 지정가 / LIMIT -> LIMIT
    - empty, undecided, unknown, or unsupported values stay unresolved
    """
    warnings: list[str] = []

    order_intent = _extract_order_intent(order)
    if not order_intent:
        warnings.append("order_intent missing or empty; hoga unresolved")
        return _unresolved_result(warnings)

    raw_value, source_key = _extract_hoga_intent(order_intent)
    normalized = _normalize(raw_value)
    display_value = _clean_text(raw_value)

    if source_key is None or normalized in _UNRESOLVED_TERMS:
        warnings.append("order_intent hoga is unresolved")
        return _unresolved_result(warnings)

    if normalized in _MARKET_TERMS:
        return {
            "ok": True,
            "hoga": HOGA_MARKET,
            "source": "order_intent",
            "unresolved": False,
            "warnings": warnings,
        }

    if normalized in _LIMIT_TERMS:
        return {
            "ok": True,
            "hoga": HOGA_LIMIT,
            "source": "order_intent",
            "unresolved": False,
            "warnings": warnings,
        }

    warnings.append(f"unsupported order_intent hoga value: {display_value}")
    return _unresolved_result(warnings)
