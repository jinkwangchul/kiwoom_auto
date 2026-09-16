"""Pure legacy compatibility rules for indicator-follow persisted UI state."""

from __future__ import annotations

from typing import Any


def legacy_buy_bollinger_sign(signal_filter: dict[str, Any]) -> str | None:
    """Return the historical implicit BUY Bollinger sign, if it is knowable."""
    direction = str(signal_filter.get("buy_bollinger_direction_combo") or "").strip()
    return {
        "하향": "-",
        "상향": "+",
    }.get(direction)


def legacy_sell_signed_percent_sign(
    condition: dict[str, Any],
    *,
    compare_field: str,
) -> str | None:
    """Return the historical implicit SELL offset sign, if it is knowable."""
    compare = str(condition.get(compare_field) or "").strip().upper()
    return {
        "이상": "+",
        ">=": "+",
        "GTE": "+",
        "이하": "-",
        "<=": "-",
        "LTE": "-",
    }.get(compare)
