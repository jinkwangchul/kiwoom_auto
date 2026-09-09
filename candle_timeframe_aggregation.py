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
WARMUP_MARGIN_RATIO = 0.20
MAXIMUM_MINUTE_CANDLES = 200_000
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


def _clock_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def normalize_candle_sessions(value: Any) -> tuple[tuple[str, int, int], ...]:
    """Normalize already-selected Production session windows for projection."""
    if value is None:
        return ()
    result: list[tuple[str, int, int]] = []
    for index, item in enumerate(value if isinstance(value, (list, tuple)) else ()):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("key") or f"session{index}")
            start = _clock_minutes(item.get("start_time"))
            end = _clock_minutes(item.get("end_time"))
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            name = str(item[0] or f"session{index}")
            start, end = _clock_minutes(item[1]), _clock_minutes(item[2])
        else:
            continue
        if start is None or end is None or start >= end:
            continue
        result.append((name, start, end))
    return tuple(sorted(result, key=lambda item: (item[1], item[2], item[0])))


def candle_session_windows(
    operation_policy: dict[str, Any] | None,
    selected_ats_sessions: Any,
) -> tuple[dict[str, Any], ...]:
    """Project regular plus explicitly selected ATS windows without joining gaps."""
    policy = operation_policy if isinstance(operation_policy, dict) else {}
    sessions: list[dict[str, Any]] = []
    regular = policy.get("regular_market")
    if isinstance(regular, dict):
        sessions.append(
            {
                "name": "regular",
                "start_time": regular.get("start_time", "09:00:00"),
                "end_time": regular.get("end_time", "15:20:00"),
            }
        )
    selected = {str(value or "").strip() for value in (selected_ats_sessions or ())}
    extra = policy.get("extra_sessions")
    if isinstance(extra, list):
        for index, session in enumerate(extra):
            key = f"extra{index + 1}"
            if key not in selected or not isinstance(session, dict) or session.get("enabled", True) is False:
                continue
            sessions.append(
                {
                    "name": key,
                    "start_time": session.get("start_time"),
                    "end_time": session.get("end_time"),
                }
            )
    return tuple(sessions)


def _session_for_bar(
    bar_time: datetime,
    sessions: tuple[tuple[str, int, int], ...],
) -> tuple[str, int, int] | None:
    minute = bar_time.hour * 60 + bar_time.minute
    if not sessions:
        return ("regular", MARKET_BUCKET_ANCHOR.hour * 60, 24 * 60)
    return next((item for item in sessions if item[1] <= minute < item[2]), None)


def _bucket_start(bar_time: datetime, timeframe_minutes: int, anchor_minutes: int) -> datetime:
    anchor = datetime.combine(bar_time.date(), time(anchor_minutes // 60, anchor_minutes % 60), tzinfo=SEOUL_TIMEZONE)
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
    session_windows: Any = None,
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

    sessions = normalize_candle_sessions(session_windows)
    raw_by_minute = _normalized_raw_minutes(raw_candles)
    buckets: dict[tuple[str, datetime, int], list[tuple[datetime, dict[str, Any]]]] = {}
    for bar_time, candle in raw_by_minute.items():
        session = _session_for_bar(bar_time, sessions)
        if session is None:
            continue
        session_name, session_start, session_end = session
        start = _bucket_start(bar_time, timeframe_minutes, session_start)
        buckets.setdefault((session_name, start, session_end), []).append((bar_time, candle))

    projected: list[dict[str, Any]] = []
    for session_name, start, session_end in sorted(buckets, key=lambda item: item[1]):
        rows = sorted(buckets[(session_name, start, session_end)], key=lambda item: item[0])
        row_times = {bar_time for bar_time, _candle in rows}
        session_end_time = datetime.combine(
            start.date(), time(session_end // 60, session_end % 60), tzinfo=SEOUL_TIMEZONE
        ) if session_end < 24 * 60 else datetime.combine(
            start.date() + timedelta(days=1), time(0, 0), tzinfo=SEOUL_TIMEZONE
        )
        bucket_end = min(start + timedelta(minutes=timeframe_minutes), session_end_time)
        expected_times = {
            start + timedelta(minutes=offset)
            for offset in range(int((bucket_end - start).total_seconds() // 60))
        }
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
                "session": session_name,
            }
        )
    return projected


def completed_timeframe_candles(
    raw_candles: Any,
    rules: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    session_windows: Any = None,
) -> list[dict[str, Any]]:
    """Return only completed bars for the canonical applied instance rule."""
    timeframe_minutes = read_canonical_bar_minutes(rules)
    return [
        candle
        for candle in aggregate_minute_candles(
            raw_candles, timeframe_minutes, now=now, session_windows=session_windows
        )
        if candle["is_complete"] is True
    ]


def required_minute_candles(
    timeframe_minutes: int,
    warmup_bars: int,
) -> dict[str, Any]:
    """Apply the user-approved 20% margin and 200,000-minute ceiling."""
    import math

    raw_required = max(int(timeframe_minutes), 1) * max(int(warmup_bars), 1)
    required = int(math.ceil(raw_required * (1.0 + WARMUP_MARGIN_RATIO)))
    return {
        "required_minute_candles": required,
        "maximum_minute_candles": MAXIMUM_MINUTE_CANDLES,
        "within_limit": required <= MAXIMUM_MINUTE_CANDLES,
        "reason": "" if required <= MAXIMUM_MINUTE_CANDLES else "CANDLE_WARMUP_LIMIT_EXCEEDED",
    }


def project_candle_supply(
    raw_candles: Any,
    rules: dict[str, Any] | None,
    projection_request: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    session_windows: Any = None,
    forming_minute: dict[str, Any] | None = None,
    completed_projector: Any = None,
) -> dict[str, Any]:
    """Project one applied Routine request through the common Main contract."""
    request = projection_request if isinstance(projection_request, dict) else {}
    projection = str(request.get("projection") or "").strip().upper()
    interval = read_canonical_bar_minutes(rules)
    warmup_declared = "warmup_bars" in request
    warmup = required_minute_candles(interval, int(request.get("warmup_bars") or 1))
    if not warmup_declared:
        warmup = {**warmup, "required_minute_candles": 0}
    base = {
        "timeframe_minutes": interval,
        "projection": projection,
        "completeness": {},
        "freshness": {},
        **warmup,
    }
    if not warmup["within_limit"]:
        return {**base, "available": False, "candles": [], "availability_state": "WARMUP_LIMIT_EXCEEDED"}
    normalized_sessions = normalize_candle_sessions(session_windows)
    supplied_session_count = len(session_windows) if isinstance(session_windows, (list, tuple)) else 0
    if supplied_session_count and len(normalized_sessions) != supplied_session_count:
        return {
            **base,
            "available": False,
            "candles": [],
            "availability_state": "SESSION_INVALID",
            "reason": "봉데이터 부족",
        }
    raw = [dict(item) for item in raw_candles if isinstance(item, dict)] if isinstance(raw_candles, list) else []
    embedded_forming = next(
        (dict(item) for item in reversed(raw) if item.get("is_complete") is False),
        None,
    )
    raw = [item for item in raw if item.get("is_complete") is not False]
    history_insufficient = bool(
        warmup_declared and len(raw) < int(warmup["required_minute_candles"])
    )
    source_rows = list(raw)
    if projection == "FORMING_BASE_BAR":
        forming_minute = forming_minute if isinstance(forming_minute, dict) else embedded_forming
        if warmup_declared and not isinstance(forming_minute, dict):
            return {
                **base,
                "available": False,
                "candles": [],
                "available_minute_candles": len(raw),
                "availability_state": "FORMING_SOURCE_UNAVAILABLE",
                "reason": "봉데이터 부족",
            }
        if isinstance(forming_minute, dict):
            source_rows.append(dict(forming_minute))
        candles = aggregate_minute_candles(source_rows, interval, now=now, session_windows=session_windows)
    elif projection == "COMPLETED_TIMEFRAME":
        projector = completed_projector if callable(completed_projector) else completed_timeframe_candles
        try:
            candles = projector(source_rows, rules, now=now, session_windows=session_windows)
        except TypeError:
            candles = projector(source_rows, rules, now=now)
    else:
        return {**base, "available": False, "candles": [], "availability_state": "PROJECTION_INVALID", "reason": "ROUTINE_MARKET_PROJECTION_REQUEST_INVALID"}
    if history_insufficient:
        return {
            **base,
            "available": False,
            "candles": [],
            "available_minute_candles": len(raw),
            "availability_state": "SOURCE_UNAVAILABLE" if not raw else "HISTORY_INSUFFICIENT",
            "reason": "봉데이터 부족",
        }
    latest_completed = max(
        (value for value in (candle_market_datetime(item) for item in raw) if value is not None),
        default=None,
    )
    return {
        **base,
        "available": bool(candles) or not warmup_declared,
        "availability_state": "AVAILABLE" if candles or not warmup_declared else "HISTORY_INSUFFICIENT",
        "candles": candles,
        "complete": bool(candles and candles[-1].get("is_complete") is True),
        "forming_included": bool(forming_minute is not None),
        "completeness": {
            "completed_only": projection == "COMPLETED_TIMEFRAME",
            "forming_included": bool(forming_minute is not None),
        },
        "freshness": {
            "latest_completed_minute": latest_completed.isoformat(timespec="seconds")
            if latest_completed is not None
            else "",
            "as_of": now.isoformat(timespec="seconds") if isinstance(now, datetime) else "",
        },
        "available_minute_candles": len(raw),
        "reason": "" if candles or not warmup_declared else "봉데이터 부족",
    }
