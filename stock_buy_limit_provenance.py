# -*- coding: utf-8 -*-
"""Canonical provenance contract for persisted Stock buy limits."""

from __future__ import annotations

from typing import Mapping


BUY_LIMIT_SOURCE_RECOMMENDED = "RECOMMENDED"
BUY_LIMIT_SOURCE_MANUAL = "MANUAL"
BUY_LIMIT_SOURCE_UNKNOWN = "UNKNOWN"
BUY_LIMIT_SOURCES = frozenset(
    {
        BUY_LIMIT_SOURCE_RECOMMENDED,
        BUY_LIMIT_SOURCE_MANUAL,
        BUY_LIMIT_SOURCE_UNKNOWN,
    }
)


def _positive_amount(value: object) -> int | None:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def normalized_stock_buy_limit_source(config: Mapping[str, object]) -> str | None:
    """Resolve persisted and legacy Stock limit provenance without guessing."""

    if not bool(config.get("buy_limit_enabled", False)):
        return None
    if _positive_amount(config.get("buy_limit_amount")) is None:
        return BUY_LIMIT_SOURCE_RECOMMENDED
    source = str(config.get("buy_limit_source") or "").strip().upper()
    return source if source in BUY_LIMIT_SOURCES else BUY_LIMIT_SOURCE_UNKNOWN


def canonical_stock_buy_limit_values(
    *,
    enabled: bool,
    amount: object = None,
    source: object = None,
) -> tuple[bool, int | None, str | None]:
    """Return the only valid persisted enabled/amount/source combinations."""

    if not enabled:
        return False, None, None
    normalized_amount = _positive_amount(amount)
    if normalized_amount is None:
        return True, None, BUY_LIMIT_SOURCE_RECOMMENDED
    normalized_source = str(source or "").strip().upper()
    if normalized_source not in BUY_LIMIT_SOURCES:
        normalized_source = BUY_LIMIT_SOURCE_UNKNOWN
    return True, normalized_amount, normalized_source
