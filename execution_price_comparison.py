# -*- coding: utf-8 -*-
"""Pure price-source and percent-comparison primitives for execution policies."""

from __future__ import annotations

from typing import Any


def positive_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve_price_source(
    source: str,
    *,
    order_price: float | None,
    current_price: float | None,
    average_price: float | None,
) -> float | None:
    normalized = str(source or "").strip().upper()
    if normalized == "ORDER_PRICE":
        return order_price
    if normalized == "CURRENT_PRICE":
        return current_price
    if normalized == "AVG_PRICE":
        return average_price
    return None


def evaluate_percent_comparison(
    *,
    left: float,
    right: float,
    direction: str,
    compare: str,
    threshold: float,
) -> tuple[bool | None, float | None]:
    """Return eligibility and the direction-normalized observed percentage."""
    if left <= 0 or right <= 0 or threshold <= 0:
        return None, None
    signed_percent = ((right - left) / left) * 100.0
    normalized_direction = str(direction or "").strip().upper()
    normalized_compare = str(compare or "").strip().upper()
    if normalized_direction == "UP":
        observed = signed_percent
        if normalized_compare == ">=":
            return observed >= threshold, observed
        if normalized_compare == "<=":
            return observed <= threshold, observed
    elif normalized_direction == "DOWN":
        observed = -signed_percent
        if normalized_compare == ">=":
            return observed >= threshold, observed
        if normalized_compare == "<=":
            return observed <= threshold, observed
    elif normalized_direction == "BOTH":
        observed = abs(signed_percent)
        if normalized_compare == "WITHIN":
            return observed <= threshold, observed
        if normalized_compare == "OUTSIDE":
            return observed > threshold, observed
    return None, None


__all__ = ["evaluate_percent_comparison", "positive_price", "resolve_price_source"]
