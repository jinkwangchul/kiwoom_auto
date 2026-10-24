# -*- coding: utf-8 -*-
"""Read-only current forming projection from Kiwoom one-minute candles.

The raw ``candles.json`` timestamp is the start time of the one-minute bar.
Buckets use the session clock, or its explicit broker bucket anchor; they are
never anchored to the first row in the available rolling window.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any


SEOUL_TIMEZONE = timezone(timedelta(hours=9), name="Asia/Seoul")
MARKET_BUCKET_ANCHOR = time(9, 0)
SUPPORTED_BAR_MINUTES = frozenset({1, 3, 5, 10, 15, 30, 60, 120, 240})
WARMUP_MARGIN_RATIO = 0.10
MAXIMUM_MINUTE_CANDLES = 200_000
TIMESTAMP_ALIASES = ("timestamp", "datetime", "time", "date", "bar_time")
SUPPORTED_MARKET_BAR_PROJECTIONS = frozenset({
    "COMPLETED_TIMEFRAME",
    "FORMING_BASE_BAR",
})


def validate_market_bar_projection_request(
    projection_request: object,
    *,
    require_warmup: bool,
) -> dict[str, Any]:
    """Validate one routine-declared projection without coercing its schema."""

    if not isinstance(projection_request, dict):
        raise ValueError("projection_request must be a mapping")
    projection = str(projection_request.get("projection") or "").strip().upper()
    if projection not in SUPPORTED_MARKET_BAR_PROJECTIONS:
        raise ValueError("projection_request.projection is unsupported")
    normalized = dict(projection_request)
    normalized["projection"] = projection

    warmup_bars: int | None = None
    if "warmup_bars" in projection_request:
        raw_warmup = projection_request.get("warmup_bars")
        if isinstance(raw_warmup, bool) or not isinstance(raw_warmup, int):
            raise ValueError("projection_request.warmup_bars must be a positive integer")
        if raw_warmup <= 0:
            raise ValueError("projection_request.warmup_bars must be a positive integer")
        warmup_bars = raw_warmup
        normalized["warmup_bars"] = warmup_bars
    elif require_warmup:
        raise ValueError("projection_request.warmup_bars is required")

    if "history_target_bars" in projection_request:
        raw_target = projection_request.get("history_target_bars")
        if isinstance(raw_target, bool) or not isinstance(raw_target, int):
            raise ValueError("projection_request.history_target_bars must be a positive integer")
        if raw_target <= 0:
            raise ValueError("projection_request.history_target_bars must be a positive integer")
        if warmup_bars is not None and raw_target < warmup_bars:
            raise ValueError("projection_request.history_target_bars must be >= warmup_bars")
        normalized["history_target_bars"] = raw_target
    return normalized


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
    """Project only today's current forming higher-timeframe bucket from M1.

    Completed higher-timeframe bars must come from independent direct SOR _AL
    receipts. An old incomplete bucket is not a current forming bar.
    """
    if timeframe_minutes not in SUPPORTED_BAR_MINUTES:
        raise ValueError(f"unsupported timeframe_minutes: {timeframe_minutes}")
    current_time = now or datetime.now(SEOUL_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        current_time = current_time.astimezone(SEOUL_TIMEZONE)

    sessions = normalize_candle_sessions(session_windows)
    bucket_anchors: dict[tuple[str, int, int], int] = {}
    for index, item in enumerate(session_windows if isinstance(session_windows, (list, tuple)) else ()):
        if not isinstance(item, dict) or "bucket_anchor_time" not in item:
            continue
        session = (str(item.get("name") or item.get("key") or f"session{index}"),
                   _clock_minutes(item.get("start_time")), _clock_minutes(item.get("end_time")))
        if session not in sessions:
            raise ValueError("bucket_anchor_time needs a valid session")
        anchor = _clock_minutes(item["bucket_anchor_time"])
        if (anchor is None or anchor > session[1]
                or session[1] - anchor >= timeframe_minutes
                or any(other[2] > anchor and other[1] < session[1]
                       for other in sessions if other != session)):
            raise ValueError("bucket_anchor_time crosses or misses its session")
        bucket_anchors[session] = anchor
    raw_by_minute = _normalized_raw_minutes(raw_candles)
    buckets: dict[tuple[str, datetime, int, int], list[tuple[datetime, dict[str, Any]]]] = {}
    for bar_time, candle in raw_by_minute.items():
        session = _session_for_bar(bar_time, sessions)
        if session is None:
            continue
        session_name, session_start, session_end = session
        start = _bucket_start(bar_time, timeframe_minutes,
                              bucket_anchors.get(session, session_start))
        buckets.setdefault((session_name, start, session_start, session_end), []).append((bar_time, candle))

    projected: list[dict[str, Any]] = []
    for session_name, start, session_start, session_end in sorted(buckets, key=lambda item: item[1]):
        rows = sorted(buckets[(session_name, start, session_start, session_end)], key=lambda item: item[0])
        row_times = {bar_time for bar_time, _candle in rows}
        session_end_time = datetime.combine(
            start.date(), time(session_end // 60, session_end % 60), tzinfo=SEOUL_TIMEZONE
        ) if session_end < 24 * 60 else datetime.combine(
            start.date() + timedelta(days=1), time(0, 0), tzinfo=SEOUL_TIMEZONE
        )
        bucket_end = min(start + timedelta(minutes=timeframe_minutes), session_end_time)
        expected_start = max(start, datetime.combine(
            start.date(), time(session_start // 60, session_start % 60), tzinfo=SEOUL_TIMEZONE
        ))
        expected_times = {
            expected_start + timedelta(minutes=offset)
            for offset in range(int((bucket_end - expected_start).total_seconds() // 60))
        }
        is_complete = row_times == expected_times and current_time >= bucket_end
        first = rows[0][1]
        last = rows[-1][1]
        if timeframe_minutes > 1 and (is_complete or not start <= current_time < bucket_end
                                      or start.date() != current_time.date()):
            continue
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
    """Return completed M1 only; higher completed bars require direct SOR."""
    timeframe_minutes = read_canonical_bar_minutes(rules)
    if timeframe_minutes > 1:
        return []
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
    """Apply the Production-owned 10% margin and 200,000-minute ceiling."""
    import math

    raw_required = max(int(timeframe_minutes), 1) * max(int(warmup_bars), 1)
    required = raw_required + int(math.ceil(raw_required * WARMUP_MARGIN_RATIO))
    return {
        "required_minute_candles": required,
        "maximum_minute_candles": MAXIMUM_MINUTE_CANDLES,
        "within_limit": required <= MAXIMUM_MINUTE_CANDLES,
        "reason": "" if required <= MAXIMUM_MINUTE_CANDLES else "CANDLE_WARMUP_LIMIT_EXCEEDED",
    }


def effective_history_target_minute_candles(
    timeframe_minutes: int,
    history_target_bars: int,
) -> dict[str, Any]:
    """Cap desired retained history without weakening the minimum warmup gate."""
    requested = required_minute_candles(timeframe_minutes, history_target_bars)
    requested_count = int(requested["required_minute_candles"])
    effective_count = min(requested_count, MAXIMUM_MINUTE_CANDLES)
    return {
        "required_minute_candles": effective_count,
        "requested_minute_candles": requested_count,
        "maximum_minute_candles": MAXIMUM_MINUTE_CANDLES,
        "capped": effective_count < requested_count,
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
    direct_timeframe_candles: Any = None,
) -> dict[str, Any]:
    """Project one applied Routine request through the common Main contract."""
    interval = read_canonical_bar_minutes(rules)
    try:
        request = validate_market_bar_projection_request(
            projection_request,
            require_warmup=False,
        )
    except ValueError as exc:
        return {
            "available": False,
            "candles": [],
            "timeframe_minutes": interval,
            "projection": "",
            "availability_state": "PROJECTION_INVALID",
            "reason": str(exc),
        }
    projection = request["projection"]
    warmup_declared = "warmup_bars" in request
    history_target_declared = "history_target_bars" in request
    warmup_bars = int(request.get("warmup_bars", 1))
    history_target_bars = int(
        request.get("history_target_bars", warmup_bars)
    )
    warmup = required_minute_candles(interval, warmup_bars)
    history_target = effective_history_target_minute_candles(
        interval,
        history_target_bars,
    )
    if interval > 1:
        warmup = {**warmup, "required_minute_candles": 0,
                  "within_limit": True, "reason": ""}
        history_target = {**history_target, "required_minute_candles": 0,
                          "requested_minute_candles": 0, "capped": False}
    if not warmup_declared:
        warmup = {**warmup, "required_minute_candles": 0}
    base = {
        "timeframe_minutes": interval,
        "projection": projection,
        "history_target_bars": (
            history_target_bars if history_target_declared else 0
        ),
        "history_target_minute_candles": (
            int(history_target["required_minute_candles"])
            if history_target_declared
            else 0
        ),
        "history_target_requested_minute_candles": (
            int(history_target["requested_minute_candles"])
            if history_target_declared
            else 0
        ),
        "history_target_capped": (
            bool(history_target["capped"])
            if history_target_declared
            else False
        ),
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
    if interval > 1:
        direct = direct_timeframe_candles
        if (not isinstance(direct, list)
                or (not direct and projection != "FORMING_BASE_BAR")
                or any(not isinstance(row, dict)
                       or row.get("is_complete") is not True
                       or row.get("timeframe_minutes") != interval
                       or row.get("market_source") != "INTEGRATED"
                       or not str(row.get("market_data_identity") or "").endswith("_AL")
                       or row.get("display_only") is not True
                       for row in direct)):
            return {**base, "available": False, "candles": [],
                    "availability_state": "DIRECT_TIMEFRAME_SOURCE_UNAVAILABLE",
                    "reason": "DIRECT_TIMEFRAME_SOURCE_UNAVAILABLE"}
        candles = [dict(row) for row in direct]
        forming_included = False
        if projection == "FORMING_BASE_BAR":
            forming = forming_minute if isinstance(forming_minute, dict) else embedded_forming
            if isinstance(forming, dict):
                current = now or datetime.now(SEOUL_TIMEZONE)
                # Project only the active bucket. Older M1 must never create a
                # completed higher-timeframe candle, including transiently.
                marker = aggregate_minute_candles(
                    [forming], interval, now=current,
                    session_windows=session_windows,
                )
                bucket_start = candle_market_datetime(marker[-1]) if marker else None
                today = [row for row in raw if bucket_start is not None
                         and (stamp := candle_market_datetime(row)) is not None
                         and bucket_start <= stamp < current]
                projected = aggregate_minute_candles(
                    [*today, forming], interval, now=current,
                    session_windows=session_windows,
                )
                if projected and (not candles or str(projected[-1]["bar_time"])
                                  > str(candles[-1]["bar_time"])):
                    candles.append(projected[-1])
                    forming_included = True
            if not forming_included:
                return {**base, "available": False, "candles": [],
                        "availability_state": "FORMING_SOURCE_UNAVAILABLE",
                        "reason": "FORMING_SOURCE_UNAVAILABLE"}
        if history_target_declared:
            candles = candles[-history_target_bars:]
        if ((warmup_declared and len(candles) < warmup_bars)
                or (history_target_declared and len(candles) < history_target_bars)):
            return {**base, "available": False, "candles": [],
                    "availability_state": "HISTORY_INSUFFICIENT",
                    "reason": "HISTORY_INSUFFICIENT"}
        return {**base, "available": True, "availability_state": "AVAILABLE",
                "candles": candles, "complete": not forming_included,
                "forming_included": forming_included,
                "completeness": {"completed_only": not forming_included,
                                 "forming_included": forming_included},
                "freshness": {"as_of": now.isoformat(timespec="seconds")
                              if isinstance(now, datetime) else ""},
                "available_minute_candles": len(raw),
                "available_timeframe_bars": len(candles),
                "reason": ""}
    history_insufficient = bool(
        warmup_declared and len(raw) < int(warmup["required_minute_candles"])
    )
    source_rows = list(raw)
    forming_included = False
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
        forming_bucket_identity = None
        if isinstance(forming_minute, dict):
            source_rows.append(dict(forming_minute))
            forming_projection = aggregate_minute_candles(
                [forming_minute],
                interval,
                now=now,
                session_windows=session_windows,
            )
            if forming_projection:
                marker = forming_projection[-1]
                forming_bucket_identity = (
                    marker.get("bar_time"),
                    marker.get("trade_date"),
                    marker.get("session"),
                )
            elif warmup_declared:
                return {
                    **base,
                    "available": False,
                    "candles": [],
                    "available_minute_candles": len(raw),
                    "availability_state": "FORMING_SOURCE_UNAVAILABLE",
                    "reason": "봉데이터 부족",
                }
        aggregated = aggregate_minute_candles(
            source_rows,
            interval,
            now=now,
            session_windows=session_windows,
        )
        candles = [item for item in aggregated if item.get("is_complete") is True]
        if forming_bucket_identity is not None:
            forming_bucket = next(
                (
                    item
                    for item in aggregated
                    if (
                        item.get("bar_time"),
                        item.get("trade_date"),
                        item.get("session"),
                    )
                    == forming_bucket_identity
                ),
                None,
            )
            if (
                isinstance(forming_bucket, dict)
                and forming_bucket.get("is_complete") is not True
                and (
                    not candles
                    or str(forming_bucket.get("bar_time") or "")
                    >= str(candles[-1].get("bar_time") or "")
                )
            ):
                candles.append(forming_bucket)
                forming_included = True
    elif projection == "COMPLETED_TIMEFRAME":
        projector = completed_projector if callable(completed_projector) else completed_timeframe_candles
        try:
            candles = projector(source_rows, rules, now=now, session_windows=session_windows)
        except TypeError:
            candles = projector(source_rows, rules, now=now)
    else:
        return {**base, "available": False, "candles": [], "availability_state": "PROJECTION_INVALID", "reason": "ROUTINE_MARKET_PROJECTION_REQUEST_INVALID"}

    if history_target_declared and len(candles) > history_target_bars:
        candles = candles[-history_target_bars:]

    projected_history_insufficient = bool(
        warmup_declared and len(candles) < int(request["warmup_bars"])
    )
    if history_insufficient or projected_history_insufficient:
        return {
            **base,
            "available": False,
            "candles": [],
            "available_minute_candles": len(raw),
            "available_timeframe_bars": len(candles),
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
        "forming_included": forming_included,
        "completeness": {
            "completed_only": projection == "COMPLETED_TIMEFRAME",
            "forming_included": forming_included,
        },
        "freshness": {
            "latest_completed_minute": latest_completed.isoformat(timespec="seconds")
            if latest_completed is not None
            else "",
            "as_of": now.isoformat(timespec="seconds") if isinstance(now, datetime) else "",
        },
        "available_minute_candles": len(raw),
        "available_timeframe_bars": len(candles),
        "reason": "" if candles or not warmup_declared else "봉데이터 부족",
    }
