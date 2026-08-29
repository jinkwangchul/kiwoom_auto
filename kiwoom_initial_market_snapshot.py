# -*- coding: utf-8 -*-
"""Normalization for the official OPTKWFID login-time market snapshot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

from stock_code_contract import normalize_broker_stock_code


OPTKWFID_MARKET_FIELDS = (
    "종목코드",
    "현재가",
    "등락율",
    "거래량",
    "거래대금",
    "시가총액",
    "체결강도",
    "전일거래량대비",
    "시가",
    "고가",
    "저가",
)


@dataclass(frozen=True)
class NormalizedInitialMarketSnapshot:
    stock_code: str
    current_price: int | float | None
    open_price: int | float | None
    high_price: int | float | None
    low_price: int | float | None
    change_rate: int | float | None
    previous_day_volume_rate: int | float | None
    execution_strength: int | float | None
    cumulative_volume: int | float | None
    cumulative_trading_value: int | float | None
    market_capitalization: int | float | None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: object) -> int | float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _price(value: object) -> int | float | None:
    number = _number(value)
    if number is None:
        return None
    normalized = abs(number)
    return int(normalized) if float(normalized).is_integer() else normalized


def normalize_optkwfid_market_row(
    row: object,
) -> NormalizedInitialMarketSnapshot | None:
    if not isinstance(row, dict):
        return None
    code = normalize_broker_stock_code(row.get("종목코드"))
    if not code:
        return None
    return NormalizedInitialMarketSnapshot(
        stock_code=code,
        current_price=_price(row.get("현재가")),
        open_price=_price(row.get("시가")),
        high_price=_price(row.get("고가")),
        low_price=_price(row.get("저가")),
        change_rate=_number(row.get("등락율")),
        previous_day_volume_rate=_number(row.get("전일거래량대비")),
        execution_strength=_number(row.get("체결강도")),
        cumulative_volume=_number(row.get("거래량")),
        cumulative_trading_value=_number(row.get("거래대금")),
        market_capitalization=_number(row.get("시가총액")),
    )
