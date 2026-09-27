# -*- coding: utf-8 -*-
"""Read compatibility for Indicator Follow strategy-price comparisons."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


BUY_STRATEGY_PRICE_AXES = frozenset({
    "CURRENT_PRICE",
    "SIGNAL_PRICE",
    "AVG_PRICE",
})

_BUY_LEGACY_PRICE_AXIS_MAP = {
    "ORDER_PRICE": "SIGNAL_PRICE",
    "CLOSE": "CURRENT_PRICE",
}


def canonicalize_buy_strategy_price_compare_filter(value: Any) -> Any:
    """Return a detached BUY price-compare filter with legacy axes normalized."""
    normalized = deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    conditions = normalized.get("conditions")
    if not isinstance(conditions, list):
        return normalized
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        for field in ("target", "compare_target"):
            token = condition.get(field)
            replacement = _BUY_LEGACY_PRICE_AXIS_MAP.get(token)
            if replacement is not None:
                condition[field] = replacement
    return normalized


def canonicalize_buy_strategy_price_comparison_rules(value: Any) -> Any:
    """Return detached rules, normalizing only buy.filters.price_compare."""
    normalized = deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    buy = normalized.get("buy")
    filters = buy.get("filters") if isinstance(buy, dict) else None
    if not isinstance(filters, dict) or "price_compare" not in filters:
        return normalized
    filters["price_compare"] = canonicalize_buy_strategy_price_compare_filter(
        filters["price_compare"]
    )
    return normalized
