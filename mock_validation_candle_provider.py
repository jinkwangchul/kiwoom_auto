# -*- coding: utf-8 -*-
"""Read-only Candle projection owned by the Mock Validation domain."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from candle_manager import canonical_candle_content_hash, load_candles
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    project_candle_supply,
    read_canonical_bar_minutes,
    required_minute_candles,
    validate_market_bar_projection_request,
)
from stock_repository import StockRepository


def is_production_market_data_callback(callback: Callable[..., Any] | None) -> bool:
    """Return True for callbacks owned by Production MarketDataHost."""

    if callback is None:
        return False
    owner = getattr(callback, "__self__", None)
    if owner is None:
        return getattr(callback, "__module__", "") == "gui_market_data_host"
    return any(
        getattr(base, "__module__", "") == "gui_market_data_host"
        and getattr(base, "__name__", "") == "MarketDataHost"
        for base in type(owner).__mro__
    )


def project_mock_routine_candles(
    *,
    project_root: str | Path,
    api: Any,
    stock_code: object,
    rules: dict[str, Any] | None,
    projection_request: dict[str, Any] | None,
    as_of: datetime | None = None,
    session_windows: object = None,
    consumer_scope: str = "MOCK",
) -> dict[str, Any]:
    """Project Mock candles without mutating Production market-data state."""

    del consumer_scope
    code = str(stock_code or "").strip()
    try:
        request = validate_market_bar_projection_request(
            projection_request,
            require_warmup=False,
        )
    except (TypeError, ValueError) as exc:
        return {
            "available": False,
            "stock_code": code,
            "candles": [],
            "availability_state": "PROJECTION_REQUEST_INVALID",
            "reason": str(exc),
        }
    projection = request["projection"]
    try:
        interval = read_canonical_bar_minutes(rules)
        warmup = required_minute_candles(
            interval,
            request.get("warmup_bars", 1),
        )
    except (TypeError, ValueError) as exc:
        return {
            "available": False,
            "stock_code": code,
            "candles": [],
            "availability_state": "INTERVAL_UNSUPPORTED",
            "reason": str(exc),
        }

    base = {
        "stock_code": code,
        "timeframe_minutes": interval,
        "projection": projection,
        **warmup,
    }
    if not warmup["within_limit"]:
        return {
            **base,
            "available": False,
            "candles": [],
            "availability_state": "WARMUP_LIMIT_EXCEEDED",
        }

    repository = StockRepository(Path(project_root).resolve())
    raw = load_candles(repository.resolve_stock_dir(code))
    source_hash = canonical_candle_content_hash(raw) if raw else ""
    forming = None
    if projection == "FORMING_BASE_BAR":
        current_reader = getattr(api, "current_realtime_shadow_bar", None)
        forming = current_reader(code) if callable(current_reader) else None

    now = as_of or datetime.now(SEOUL_TIMEZONE)
    result = project_candle_supply(
        raw,
        rules,
        request,
        now=now,
        session_windows=session_windows,
        forming_minute=forming if isinstance(forming, dict) else None,
    )
    source_rows = list(raw)
    if isinstance(forming, dict):
        source_rows.append(dict(forming))
    return {
        **result,
        "stock_code": code,
        "source_identity": (
            canonical_candle_content_hash(source_rows)
            if source_rows
            else source_hash
        ),
    }


__all__ = [
    "is_production_market_data_callback",
    "project_mock_routine_candles",
]
