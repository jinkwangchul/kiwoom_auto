# -*- coding: utf-8 -*-
"""Read-only projection from raw Kiwoom one-minute candles to engine bars.

The raw ``candles.json`` timestamp is the start time of the one-minute bar.
Buckets are anchored to the 09:00 Asia/Seoul market clock for each trade date;
they are never anchored to the first row in the available rolling window.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any


SEOUL_TIMEZONE = timezone(timedelta(hours=9), name="Asia/Seoul")
MARKET_BUCKET_ANCHOR = time(9, 0)
SUPPORTED_BAR_MINUTES = frozenset({1, 3, 5, 10, 15, 30, 60, 120, 240})
TIMESTAMP_ALIASES = ("timestamp", "datetime", "time", "date", "bar_time")


def read_canonical_bar_minutes(rules: dict[str, Any] | None) -> int:
    """Read the applied engine timeframe, never the UI/pending candidate."""
    source = rules if isinstance(rules, dict) else {}
    bar = source.get("bar") if isinstance(source.get("bar"), dict) else {}
    raw_value = bar.get("bar_minutes", source.get("bar_minutes", 1))
    if isinstance(raw_value, bool):
        raise ValueError("bar.bar_minutes must be a supported integer")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("bar.bar_minutes must be a supported integer") from exc
    if value not in SUPPORTED_BAR_MINUTES or str(raw_value).strip() not in {str(value), f"{value}.0"}:
        raise ValueError(f"unsupported bar.bar_minutes: {raw_value}")
    return value


def parse_market_datetime(value: Any) -> datetime | None:
    """Parse Kiwoom compact timestamps and existing candle timestamp aliases."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = None
        compact_formats = {
            14: "%Y%m%d%H%M%S",
            12: "%Y%m%d%H%M",
        }
        compact_format = compact_formats.get(len(text)) if text.isdigit() else None
        if compact_format:
            try:
                parsed = datetime.strptime(text, compact_format)
            except ValueError:
                parsed = None
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        parsed = parsed.astimezone(SEOUL_TIMEZONE)
    return parsed.replace(second=0, microsecond=0)


def _candle_datetime(candle: dict[str, Any]) -> datetime | None:
    for key in TIMESTAMP_ALIASES:
        if candle.get(key) not in (None, ""):
            return parse_market_datetime(candle.get(key))
    return None


def candle_market_datetime(candle: Any) -> datetime | None:
    """Return one candle's normalized Asia/Seoul minute timestamp."""
    return _candle_datetime(candle) if isinstance(candle, dict) else None


def filter_candles_by_trade_date(candles: Any, trade_date: str) -> list[dict[str, Any]]:
    """Filter raw or normalized candles by timestamp-derived trade date."""
    target = str(trade_date or "").strip()
    if not isinstance(candles, list) or not target:
        return []
    filtered: list[tuple[datetime, dict[str, Any]]] = []
    for candle in candles:
        bar_time = candle_market_datetime(candle)
        if bar_time is not None and bar_time.date().isoformat() == target:
            filtered.append((bar_time, candle))
    return [candle for _bar_time, candle in sorted(filtered, key=lambda item: item[0])]


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _normalized_raw_minutes(candles: Any) -> dict[datetime, dict[str, Any]]:
    by_minute: dict[datetime, dict[str, Any]] = {}
    if not isinstance(candles, list):
        return by_minute
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        bar_time = _candle_datetime(candle)
        if bar_time is None:
            continue
        values = {field: _number(candle.get(field)) for field in ("open", "high", "low", "close", "volume")}
        if any(value is None for value in values.values()):
            continue
        by_minute[bar_time] = values
    return by_minute


def _bucket_start(bar_time: datetime, timeframe_minutes: int) -> datetime:
    anchor = datetime.combine(bar_time.date(), MARKET_BUCKET_ANCHOR, tzinfo=SEOUL_TIMEZONE)
    elapsed_minutes = int((bar_time - anchor).total_seconds() // 60)
    bucket_offset = (elapsed_minutes // timeframe_minutes) * timeframe_minutes
    return anchor + timedelta(minutes=bucket_offset)


def _trade_date_text(value: date) -> str:
    return value.isoformat()


def aggregate_minute_candles(
    raw_candles: Any,
    timeframe_minutes: int,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Aggregate raw minutes and label structural/time completion per bucket.

    A bucket is complete only when every expected one-minute slot exists and the
    bucket end has been reached. Incomplete edge buckets remain visible to
    callers with ``is_complete=False`` but must not be evaluated by the engine.
    """
    if timeframe_minutes not in SUPPORTED_BAR_MINUTES:
        raise ValueError(f"unsupported timeframe_minutes: {timeframe_minutes}")
    current_time = now or datetime.now(SEOUL_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        current_time = current_time.astimezone(SEOUL_TIMEZONE)

    raw_by_minute = _normalized_raw_minutes(raw_candles)
    buckets: dict[datetime, list[tuple[datetime, dict[str, Any]]]] = {}
    for bar_time, candle in raw_by_minute.items():
        start = _bucket_start(bar_time, timeframe_minutes)
        buckets.setdefault(start, []).append((bar_time, candle))

    projected: list[dict[str, Any]] = []
    for start in sorted(buckets):
        rows = sorted(buckets[start], key=lambda item: item[0])
        row_times = {bar_time for bar_time, _candle in rows}
        expected_times = {
            start + timedelta(minutes=offset)
            for offset in range(timeframe_minutes)
        }
        bucket_end = start + timedelta(minutes=timeframe_minutes)
        is_complete = row_times == expected_times and current_time >= bucket_end
        first = rows[0][1]
        last = rows[-1][1]
        projected.append(
            {
                "bar_time": start.isoformat(timespec="seconds"),
                "open": first["open"],
                "high": max(candle["high"] for _bar_time, candle in rows),
                "low": min(candle["low"] for _bar_time, candle in rows),
                "close": last["close"],
                "volume": sum(candle["volume"] for _bar_time, candle in rows),
                "timeframe_minutes": timeframe_minutes,
                "is_complete": is_complete,
                "trade_date": _trade_date_text(start.date()),
            }
        )
    return projected


def completed_timeframe_candles(
    raw_candles: Any,
    rules: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return only completed bars for the canonical applied instance rule."""
    timeframe_minutes = read_canonical_bar_minutes(rules)
    return [
        candle
        for candle in aggregate_minute_candles(raw_candles, timeframe_minutes, now=now)
        if candle["is_complete"] is True
    ]
