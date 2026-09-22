# -*- coding: utf-8 -*-
"""Validation-only regular-market candle reaggregation."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from indicator_follow_validation_timeframe import MINUTE_VALUES


REGULAR_MARKET_START_MINUTE = 9 * 60
REGULAR_CONTINUOUS_END_MINUTE = 15 * 60 + 20
REGULAR_MARKET_CLOSE_MINUTE = 15 * 60 + 30
MAXIMUM_SOURCE_CANDLES = 200_000
_SOURCE_HEADROOM_CAP = 5_000


def regular_market_source_minutes(target_minutes: int) -> int:
    if target_minutes not in MINUTE_VALUES:
        raise ValueError(f"unsupported target minute timeframe: {target_minutes}")
    if target_minutes in {1, 3}:
        return target_minutes
    return 5


def _regular_bars_per_day(minutes: int) -> int:
    if minutes not in MINUTE_VALUES:
        raise ValueError(f"unsupported minute timeframe: {minutes}")
    continuous_minutes = (
        REGULAR_CONTINUOUS_END_MINUTE - REGULAR_MARKET_START_MINUTE
    )
    continuous_bars = int(math.ceil(continuous_minutes / minutes))
    auction_bucket = (
        REGULAR_MARKET_CLOSE_MINUTE - REGULAR_MARKET_START_MINUTE
    ) // minutes
    return continuous_bars + (1 if auction_bucket >= continuous_bars else 0)


def required_regular_market_source_candles(
    target_minutes: int,
    target_count: int,
) -> int:
    if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count <= 0:
        raise ValueError("target_count must be a positive integer")
    source_minutes = regular_market_source_minutes(target_minutes)
    source_per_day = _regular_bars_per_day(source_minutes)
    target_per_day = _regular_bars_per_day(target_minutes)
    base = int(math.ceil(target_count * source_per_day / target_per_day))
    headroom = min(target_count, _SOURCE_HEADROOM_CAP)
    return min(MAXIMUM_SOURCE_CANDLES, base + headroom)


def _parse_candle_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=SEOUL_TIMEZONE)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def project_regular_market_candles(
    source_candles: list[dict[str, Any]],
    target_minutes: int,
    *,
    limit: int | None = None,
) -> list[dict[str, float | str]]:
    if target_minutes not in MINUTE_VALUES:
        raise ValueError(f"unsupported target minute timeframe: {target_minutes}")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
    ):
        raise ValueError("limit must be a positive integer")
    rows: list[tuple[datetime, dict[str, float]]] = []
    for candle in source_candles if isinstance(source_candles, list) else []:
        if not isinstance(candle, dict):
            continue
        stamp = _parse_candle_time(candle.get("time"))
        if stamp is None:
            continue
        clock_minute = stamp.hour * 60 + stamp.minute
        if not (
            REGULAR_MARKET_START_MINUTE
            <= clock_minute
            <= REGULAR_MARKET_CLOSE_MINUTE
        ):
            continue
        values = {
            key: _number(candle.get(key))
            for key in ("open", "high", "low", "close", "volume")
        }
        if any(value is None for value in values.values()):
            continue
        rows.append((stamp, {key: float(value) for key, value in values.items()}))

    buckets: dict[datetime, list[tuple[datetime, dict[str, float]]]] = {}
    for stamp, values in sorted(rows, key=lambda item: item[0]):
        elapsed = stamp.hour * 60 + stamp.minute - REGULAR_MARKET_START_MINUTE
        bucket_offset = (elapsed // target_minutes) * target_minutes
        bucket_start = stamp.replace(
            hour=9, minute=0, second=0, microsecond=0
        ) + timedelta(minutes=bucket_offset)
        buckets.setdefault(bucket_start, []).append((stamp, values))
    projected: list[dict[str, float | str]] = []
    for bucket_start in sorted(buckets):
        bucket_rows = sorted(buckets[bucket_start], key=lambda item: item[0])
        first = bucket_rows[0][1]
        last = bucket_rows[-1][1]
        projected.append({
            "time": bucket_start.strftime("%Y%m%d%H%M%S"),
            "open": first["open"],
            "high": max(item[1]["high"] for item in bucket_rows),
            "low": min(item[1]["low"] for item in bucket_rows),
            "close": last["close"],
            "volume": sum(item[1]["volume"] for item in bucket_rows),
        })
    if limit is not None:
        projected = projected[-limit:]
    return projected
