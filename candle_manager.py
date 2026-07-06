# -*- coding: utf-8 -*-
"""Minimal candle file helpers for routine probe tests.

This module only reads and writes stocks/<stock>/candles.json. It does not
connect to Kiwoom, orders, rules, or the routine engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CANDLES_FILENAME = "candles.json"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_candle(candle: Any) -> bool:
    """Return True when candle is a dict with a numeric close value."""
    if not isinstance(candle, dict):
        return False
    close_value = _safe_float(candle.get("close"))
    return close_value is not None


def _normalize_candles(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("candles")
    if not isinstance(value, list):
        return []
    return [item for item in value if validate_candle(item)]


def load_candles(stock_dir: str | Path) -> list[dict[str, Any]]:
    """Load candles from stock_dir/candles.json.

    Accepted file shapes:
    - [{...}, {...}]
    - {"candles": [{...}, {...}]}
    """
    path = Path(stock_dir) / CANDLES_FILENAME
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _normalize_candles(data)


def save_candles(
    stock_dir: str | Path,
    candles: list[dict[str, Any]],
    max_count: int = 300,
) -> list[dict[str, Any]]:
    """Save validated candles as a JSON list and return the saved candles."""
    path = Path(stock_dir)
    path.mkdir(parents=True, exist_ok=True)

    clean_candles = _normalize_candles(candles)
    try:
        limit = max(int(max_count), 0)
    except (TypeError, ValueError):
        limit = 300
    if limit:
        clean_candles = clean_candles[-limit:]

    (path / CANDLES_FILENAME).write_text(
        json.dumps(clean_candles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return clean_candles


def append_candle(
    stock_dir: str | Path,
    candle: dict[str, Any],
    max_count: int = 300,
) -> list[dict[str, Any]]:
    """Append one valid candle, save, and return the saved candle list."""
    if not validate_candle(candle):
        return load_candles(stock_dir)
    candles = load_candles(stock_dir)
    candles.append(candle)
    return save_candles(stock_dir, candles, max_count=max_count)
