# -*- coding: utf-8 -*-
"""Pure KRX quotation-price tick helpers.

The helpers are deterministic and side-effect free.  They do not read market
data or call a broker; callers must supply an already resolved base price and
the instrument classification owned by the existing stock library.
"""

from __future__ import annotations

from typing import Any


_FUND_TYPES = {"ETF", "ETN"}
_STOCK_TYPES = {
    "",
    "-",
    "STOCK",
    "EQUITY",
    "GENERAL",
    "일반종목",
    "SPAC",
    "REIT",
    "기타",
}


def _positive_price(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("KRX_TICK_PRICE_INVALID")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("KRX_TICK_PRICE_INVALID") from exc
    if number <= 0 or not number.is_integer():
        raise ValueError("KRX_TICK_PRICE_INVALID")
    return int(number)


def _instrument_type(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    if upper in _FUND_TYPES or upper == "ELW":
        return upper
    if text in _STOCK_TYPES or upper in _STOCK_TYPES:
        return "STOCK"
    raise ValueError("KRX_TICK_INSTRUMENT_UNSUPPORTED")


def krx_tick_size(price: Any, *, instrument_type: Any = "STOCK") -> int:
    """Return the valid quotation unit for one KRX price level."""
    value = _positive_price(price)
    kind = _instrument_type(instrument_type)
    if kind == "ELW":
        return 5
    if kind in _FUND_TYPES:
        return 1 if value < 2_000 else 5
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def move_krx_price_by_ticks(
    base_price: Any,
    offset_ticks: Any,
    *,
    instrument_type: Any = "STOCK",
) -> int:
    """Move by signed ticks while re-evaluating the unit at every boundary."""
    price = _positive_price(base_price)
    if isinstance(offset_ticks, bool) or not isinstance(offset_ticks, int):
        raise ValueError("KRX_TICK_OFFSET_INVALID")
    if price % krx_tick_size(price, instrument_type=instrument_type) != 0:
        raise ValueError("KRX_BASE_PRICE_NOT_ON_TICK")

    direction = 1 if offset_ticks >= 0 else -1
    for _ in range(abs(offset_ticks)):
        if direction > 0:
            price += krx_tick_size(price, instrument_type=instrument_type)
        else:
            if price <= 1:
                raise ValueError("KRX_TICK_PRICE_UNDERFLOW")
            price -= krx_tick_size(price - 1, instrument_type=instrument_type)
        if price <= 0:
            raise ValueError("KRX_TICK_PRICE_UNDERFLOW")
    return price
