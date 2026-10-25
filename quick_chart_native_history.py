"""Read-only, broker-native _AL history admission for the production quickchart."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from candle_manager import DEFAULT_CANDLES_MAX_COUNT, canonical_candle_content_hash
from candle_timeframe_aggregation import SEOUL_TIMEZONE, candle_market_datetime
from gui_market_data_host import MarketDataHost, _DISPLAY_INTEGRATED_SESSIONS
from kiwoom_candle_adapter import normalize_opt10080_row
from stock_instance_day_projection import project_operation_timeline


def _seoul_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(SEOUL_TIMEZONE) if parsed.tzinfo else None


def native_history_request_count(start_at: str, through: datetime, interval: int) -> int:
    """Bound one governed continuation request generously by calendar span."""
    start = _seoul_time(start_at)
    if start is None or through.tzinfo is None or interval not in (1, 3, 5, 10, 15, 30, 60, 120, 240):
        return 0
    days = (through.astimezone(SEOUL_TIMEZONE).date() - start.date()).days + 1
    if days <= 0:
        return 0
    return min(DEFAULT_CANDLES_MAX_COUNT, days * ((1440 + interval - 1) // interval) + 1)


def validate_native_history_receipt(
    started: dict[str, Any], response: dict[str, Any],
    request_session: tuple[int, str], current_session: tuple[int, str],
    code: str, interval: int, operation_start: str, operation_end: str | None,
    *, requested_count: int | None = None,
) -> dict[str, Any] | None:
    """Accept only complete native bars from the exact governed _AL request."""
    start = _seoul_time(operation_start)
    end = _seoul_time(operation_end) if operation_end else None
    requested = _seoul_time(started.get("request_as_of")) if isinstance(started, dict) else None
    received = _seoul_time(response.get("received_at")) if isinstance(response, dict) else None
    if (start is None or (operation_end and end is None) or (end and end < start)
            or requested is None or received is None or received < requested
            or type(request_session[0]) is not int or request_session[0] <= 0
            or not request_session[1] or current_session != request_session
            or not isinstance(started, dict) or started.get("ok") is not True
            or not isinstance(response, dict) or response.get("ok") is not True):
        return None
    rqname = started.get("rqname")
    rows = response.get("rows")
    has_more = response.get("has_more")
    if (not isinstance(rqname, str) or not rqname.startswith(f"opt10080_{code}_")
            or started.get("code") != code
            or started.get("market_data_identity") != f"{code}_AL"
            or started.get("market_source") != "INTEGRATED"
            or started.get("connection_epoch") != request_session[0]
            or started.get("login_session_id") != request_session[1]
            or response.get("request_id") != rqname or response.get("rqname") != rqname
            or response.get("trcode") != "opt10080"
            or response.get("type") != "minute_candles"
            or response.get("code") != code or response.get("interval") != interval
            or response.get("market_data_identity") != f"{code}_AL"
            or response.get("market_source") != "INTEGRATED"
            or response.get("display_only") is not True
            or response.get("connection_epoch") != request_session[0]
            or response.get("login_session_id") != request_session[1]
            or response.get("request_as_of") != started.get("request_as_of")
            or not isinstance(rows, list) or not rows
            or response.get("rows_count") != len(rows)
            or type(has_more) is not bool
            or response.get("warning") != ("additional pages available" if has_more else "")):
        return None
    by_time: dict[datetime, dict[str, Any]] = {}
    oldest = None
    for raw in rows:
        try:
            normalized = normalize_opt10080_row(raw)
        except (TypeError, ValueError):
            return None
        stamp = normalized["time"]
        if not isinstance(stamp, str) or len(stamp) != 14 or not stamp.isdigit():
            return None
        bar_time = candle_market_datetime(normalized)
        values = tuple(normalized.get(key) for key in ("open", "high", "low", "close", "volume"))
        if (bar_time is None or bar_time.utcoffset() != timedelta(hours=9)
                or any(type(value) not in (int, float) or not isfinite(value) for value in values)
                or values[2] <= 0 or values[4] < 0
                or not values[2] <= values[0] <= values[1]
                or not values[2] <= values[3] <= values[1]):
            return None
        oldest = min(oldest, bar_time) if oldest else bar_time
        session = MarketDataHost._display_session_for_time(bar_time)
        if not session:
            return None
        session_end_minute = (15 * 60 + 40 if session == "AL_TRANSITION" else next(
            stop for name, _begin, stop in _DISPLAY_INTEGRATED_SESSIONS if name == session
        ))
        session_end = bar_time.replace(hour=session_end_minute // 60,
                                       minute=session_end_minute % 60, second=0, microsecond=0)
        if min(bar_time + timedelta(minutes=interval), session_end) > received:
            continue
        if bar_time < start or (end is not None and bar_time > end):
            continue
        candle = {"bar_time": bar_time.isoformat(timespec="seconds"),
                  "open": values[0], "high": values[1], "low": values[2],
                  "close": values[3], "volume": values[4],
                  "timeframe_minutes": interval, "is_complete": True,
                  "trade_date": bar_time.date().isoformat(), "session": session,
                  "display_only": True, "market_data_identity": f"{code}_AL",
                  "market_source": "INTEGRATED"}
        previous = by_time.get(bar_time)
        if previous is not None and previous != candle:
            return None
        by_time[bar_time] = candle
    candles = [by_time[key] for key in sorted(by_time)]
    return {"stock_code": code, "timeframe_minutes": interval,
            "operation_start": start.isoformat(), "operation_end": end.isoformat() if end else "",
            "connection_epoch": request_session[0], "login_session_id": request_session[1],
            "request_id": rqname, "received_at": received.isoformat(),
            "oldest_returned": oldest.isoformat() if oldest else "", "has_more": has_more,
            "request_limit_reached": (type(requested_count) is int and
                                      requested_count > 0 and len(rows) >= requested_count),
            "candles": candles, "source_content_sha256": canonical_candle_content_hash(candles)}


def apply_native_history_projection(
    base: dict[str, Any], history: dict[str, Any] | None, *, now: datetime,
) -> dict[str, Any]:
    """Add independently received native closes to the read-only chart DTO."""
    if not isinstance(history, dict) or not isinstance(base, dict) or now.tzinfo is None:
        return base
    timeline = base.get("operation_timeline")
    if (not isinstance(timeline, dict)
            or timeline.get("status") not in {"ONGOING", "ENDED", "END_UNKNOWN"}
            or base.get("nxt_available") is not True
            or base.get("stock_code") != history.get("stock_code")
            or base.get("bar_minutes") != history.get("timeframe_minutes")
            or timeline.get("start_at") != history.get("operation_start")
            or (timeline.get("end_at") or "") != history.get("operation_end")
            or not isinstance(history.get("candles"), list)
            or history.get("source_content_sha256") != canonical_candle_content_hash(history["candles"])):
        return base
    start = _seoul_time(history["operation_start"])
    end = _seoul_time(history["operation_end"]) if history["operation_end"] else now.astimezone(SEOUL_TIMEZONE)
    if start is None or end is None:
        return base
    by_time = {}
    for row in history["candles"]:
        bar_time = candle_market_datetime(row)
        if bar_time is not None and start <= bar_time <= end:
            by_time[bar_time] = dict(row)
    if (base.get("chart_candle_source") in {"CALCULATION_SOR_AL", "INTEGRATED_DISPLAY_ONLY"}
            and base.get("projection_status") == "VALID"):
        for row in base.get("candles", []):
            bar_time = candle_market_datetime(row)
            if bar_time is not None and start <= bar_time <= end:
                by_time[bar_time] = dict(row)
    if not by_time:
        return base
    candles = [by_time[key] for key in sorted(by_time)]
    updated = dict(base)
    updated["candles"] = candles
    updated["projection_status"] = "VALID"
    updated["chart_domain_label"] = "SOR _AL"
    updated["chart_candle_source"] = "BROKER_AL_NATIVE_CHART_HISTORY"
    updated["chart_history_receipt"] = {key: history[key] for key in (
        "request_id", "received_at", "oldest_returned", "has_more",
        "request_limit_reached", "source_content_sha256", "connection_epoch")}
    updated["operation_timeline"] = project_operation_timeline(
        history["operation_start"], history["operation_end"] or None,
        now=now, source_dates={day.date().isoformat() for day in by_time},
        active=timeline["status"] == "ONGOING")
    oldest = _seoul_time(history.get("oldest_returned"))
    updated["operation_timeline"]["native_history_incomplete"] = (
        (history.get("has_more") is True or history.get("request_limit_reached") is True)
        and oldest is not None and oldest > start
    )
    return updated
