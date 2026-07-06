# -*- coding: utf-8 -*-
"""Pure helpers for converting Kiwoom opt10080 rows into candles.json data.

This module does not create a QAxWidget, call Kiwoom OpenAPI, register realtime
feeds, or send orders. It only normalizes already-received TR rows and stores
them through the existing candle_manager helpers.
"""

from __future__ import annotations

from typing import Any

from candle_manager import save_candles
from stock_repository import StockRepository


def normalize_kiwoom_price(value: Any) -> float | None:
    """Normalize Kiwoom numeric text such as ' -1,234 ' into a positive float."""
    if value is None:
        return None

    text = str(value).strip().replace(",", "")
    if not text:
        return None

    if text[0] in {"+", "-"}:
        text = text[1:].strip()
    if not text:
        return None

    try:
        return abs(float(text))
    except (TypeError, ValueError):
        return None


def normalize_opt10080_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one opt10080 row to the candle_manager candle shape.

    Raises:
        ValueError: when row is not a dict or close/current price is missing.
    """
    if not isinstance(row, dict):
        raise ValueError("opt10080 row must be a dict")

    close = normalize_kiwoom_price(row.get("현재가"))
    if close is None:
        raise ValueError("opt10080 row missing close/current price")

    return {
        "time": str(row.get("체결시간", "") or "").strip(),
        "open": normalize_kiwoom_price(row.get("시가")),
        "high": normalize_kiwoom_price(row.get("고가")),
        "low": normalize_kiwoom_price(row.get("저가")),
        "close": close,
        "volume": normalize_kiwoom_price(row.get("거래량")),
    }


def normalize_opt10080_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize opt10080 rows, skipping invalid rows.

    Policy:
    - Invalid rows are skipped.
    - Candles are sorted by time ascending.
    - Duplicate time values keep the last valid row from the input sequence.
    """
    if not isinstance(rows, list):
        return []

    candles_by_time: dict[str, dict[str, Any]] = {}
    untimed_candles: list[dict[str, Any]] = []

    for row in rows:
        try:
            candle = normalize_opt10080_row(row)
        except ValueError:
            continue

        time_key = str(candle.get("time", "") or "").strip()
        if time_key:
            candles_by_time[time_key] = candle
        else:
            untimed_candles.append(candle)

    timed_candles = [candles_by_time[key] for key in sorted(candles_by_time)]
    return untimed_candles + timed_candles


def save_minute_candles_for_stock(
    code: str,
    name: str,
    rows: list[dict[str, Any]],
    max_count: int = 300,
) -> list[dict[str, Any]]:
    """Normalize opt10080 rows and save them to stocks/{code}_{name}/candles.json."""
    stock_dir = StockRepository().resolve_stock_dir(code, name)
    candles = normalize_opt10080_rows(rows)
    return save_candles(stock_dir, candles, max_count=max_count)
