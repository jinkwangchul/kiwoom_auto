"""Read-only, broker-native _AL history admission for the production quickchart."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from candle_manager import DEFAULT_CANDLES_MAX_COUNT, canonical_candle_content_hash
from candle_timeframe_aggregation import SEOUL_TIMEZONE, candle_market_datetime
from gui_market_data_host import MarketDataHost, _DISPLAY_INTEGRATED_SESSIONS
from kiwoom_candle_adapter import normalize_opt10080_row
from stock_market_source_plan import native_bar_end, split_native_closing_price_candles, source_for_time
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


def native_history_source(plan: object, now: datetime) -> dict[str, str] | None:
    """Use a past chart's last approved session, and today's actual session."""
    if not isinstance(plan, dict) or now.tzinfo is None:
        return None
    try:
        local = now.astimezone(SEOUL_TIMEZONE)
        day = datetime.fromisoformat(plan["trading_date"]).date()
        if day < local.date():
            end = datetime.fromisoformat(f"{day.isoformat()}T{plan['sessions'][-1]['end_time']}+09:00")
            local = end - timedelta(seconds=1)
        return source_for_time(plan, local)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def native_history_completed_slot(
    plan: object, now: datetime, interval: int, source: str,
) -> datetime | None:
    """Return the last finished native slot in an active, regular source session."""
    if (not isinstance(plan, dict) or now.tzinfo is None
            or type(interval) is not int or interval <= 0):
        return None
    try:
        local = now.astimezone(SEOUL_TIMEZONE)
        if plan["trading_date"] != local.date().isoformat():
            return None
        selected = source_for_time(plan, local)
        if (selected is None or selected["source"] != source
                or selected["name"] not in {"REGULAR", "NXT_PRE", "NXT_AFTER",
                                             "KRX_AFTER_CLOSE", "KRX_SINGLE_PRICE"}):
            return None
        begin = datetime.fromisoformat(
            f"{plan['trading_date']}T{selected['start_time']}+09:00")
        elapsed = int((local - begin).total_seconds() // (interval * 60))
        return begin + timedelta(minutes=elapsed * interval) if elapsed > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def merge_native_history_increment(
    previous: dict[str, Any], incoming: dict[str, Any],
) -> dict[str, Any] | None:
    """Admit a bounded native tail only when its verified rows overlap exactly."""
    fields = ("stock_code", "timeframe_minutes", "market_data_identity",
              "market_source", "operation_start", "operation_end",
              "connection_epoch", "login_session_id")
    if (not isinstance(previous, dict) or not isinstance(incoming, dict)
            or any(previous.get(key) != incoming.get(key) for key in fields)
            or not isinstance(previous.get("candles"), list)
            or not isinstance(incoming.get("candles"), list)
            or not previous["candles"] or not 1 <= len(incoming["candles"]) <= 3
            or previous.get("source_content_sha256") != canonical_candle_content_hash(previous["candles"])
            or incoming.get("source_content_sha256") != canonical_candle_content_hash(incoming["candles"])):
        return None
    old = {row["bar_time"]: row for row in previous["candles"]}
    tail = {row["bar_time"]: row for row in incoming["candles"]}
    if len(old) != len(previous["candles"]) or len(tail) != len(incoming["candles"]):
        return None
    latest = max(old)
    overlap = set(old) & set(tail)
    if (latest not in overlap or any(old[stamp] != tail[stamp] for stamp in overlap)
            or any(stamp < latest and stamp not in old for stamp in tail)
            or len(old) + sum(stamp not in old for stamp in tail) > 10000):
        return None
    merged = dict(incoming)
    merged["candles"] = [*previous["candles"],
                         *(tail[stamp] for stamp in sorted(tail) if stamp not in old)]
    merged["source_content_sha256"] = canonical_candle_content_hash(merged["candles"])
    merged["oldest_returned"] = previous.get("oldest_returned", "")
    merged["has_more"] = previous.get("has_more", False)
    merged["request_limit_reached"] = previous.get("request_limit_reached", False)
    return merged


def validate_native_history_receipt(
    started: dict[str, Any], response: dict[str, Any],
    request_session: tuple[int, str], current_session: tuple[int, str],
    code: str, interval: int, operation_start: str, operation_end: str | None,
    *, requested_count: int | None = None, nxt_available: bool | None = True,
    selected_source: str | None = None,
    source_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Accept only complete native bars from the exact governed _AL request."""
    start = _seoul_time(operation_start)
    end = _seoul_time(operation_end) if operation_end else None
    requested = _seoul_time(started.get("request_as_of")) if isinstance(started, dict) else None
    received = _seoul_time(response.get("received_at")) if isinstance(response, dict) else None
    if (type(nxt_available) is not bool
            or start is None or (operation_end and end is None) or (end and end < start)
            or requested is None or received is None or received < requested
            or type(request_session[0]) is not int or request_session[0] <= 0
            or not request_session[1] or current_session != request_session
            or not isinstance(started, dict) or started.get("ok") is not True
            or not isinstance(response, dict) or response.get("ok") is not True):
        return None
    rqname = started.get("rqname")
    rows = response.get("rows")
    has_more = response.get("has_more")
    source = selected_source or ("INTEGRATED" if nxt_available else "KRX")
    if source not in {"INTEGRATED", "KRX", "NXT"} or (source == "NXT" and not nxt_available):
        return None
    identity = f"{code}_AL" if source == "INTEGRATED" else f"{code}_NX" if source == "NXT" else code
    if (not isinstance(rqname, str) or not rqname.startswith(f"opt10080_{code}_")
            or started.get("code") != code
            or started.get("market_data_identity") != identity
            or started.get("market_source") != source
            or started.get("connection_epoch") != request_session[0]
            or started.get("login_session_id") != request_session[1]
            or response.get("request_id") != rqname or response.get("rqname") != rqname
            or response.get("trcode") != "opt10080"
            or response.get("type") != "minute_candles"
            or response.get("code") != code or response.get("interval") != interval
            or response.get("market_data_identity") != identity
            or response.get("market_source") != source
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
        if not nxt_available and stamp[-2:] != "00":
            return None
        bar_time = candle_market_datetime(normalized)
        values = tuple(normalized.get(key) for key in ("open", "high", "low", "close", "volume"))
        if (bar_time is None or bar_time.utcoffset() != timedelta(hours=9)
                or any(type(value) not in (int, float) or not isfinite(value) for value in values)
                or values[2] <= 0 or values[4] < 0
                or not values[2] <= values[0] <= values[1]
                or not values[2] <= values[3] <= values[1]):
            return None
        if source_plan is not None:
            try:
                plan_time = datetime.fromisoformat(
                    f"{source_plan['trading_date']}T{bar_time:%H:%M:%S}+09:00")
                planned = source_for_time(source_plan, plan_time)
            except (KeyError, TypeError, ValueError):
                return None
            if (planned is None or planned.get("source") != (
                    "SOR" if source == "INTEGRATED" else source)
                    or planned.get("market_data_identity") != identity):
                return None
        oldest = min(oldest, bar_time) if oldest else bar_time
        minute = bar_time.hour * 60 + bar_time.minute
        if source == "NXT":
            session = "NXT_PRE" if 8 * 60 <= minute < 8 * 60 + 50 else ""
        elif nxt_available:
            session = MarketDataHost._display_session_for_time(bar_time)
        elif 9 * 60 <= minute < 15 * 60 + 30:
            session = "KRX"
        elif minute == 15 * 60 + 30 and bar_time.second == 0:
            session = "KRX_CLOSING"
        elif 15 * 60 + 40 <= minute < 16 * 60:
            session = "KRX_AFTER_CLOSE"
        elif 16 * 60 <= minute < 18 * 60:
            session = "KRX_SINGLE_PRICE"
        else:
            session = ""
        if not session:
            return None
        if source == "NXT":
            session_end_minute = 8 * 60 + 50
        elif not nxt_available:
            session_end_minute = {
                "KRX": 15 * 60 + 30, "KRX_CLOSING": 15 * 60 + 30,
                "KRX_AFTER_CLOSE": 16 * 60, "KRX_SINGLE_PRICE": 18 * 60,
            }[session]
        else:
            session_end_minute = (15 * 60 + 40 if session == "AL_TRANSITION" else next(
                stop for name, _begin, stop in _DISPLAY_INTEGRATED_SESSIONS if name == session
            ))
        session_end = bar_time.replace(hour=session_end_minute // 60,
                                       minute=session_end_minute % 60,
                                       second=1 if session == "KRX_CLOSING" else 0,
                                       microsecond=0)
        native_end = (native_bar_end(source_plan, bar_time, interval) if source_plan is not None
                      else min(bar_time + timedelta(minutes=interval), session_end))
        if native_end is None or native_end > requested:
            continue
        if bar_time < start or (end is not None and bar_time > end):
            continue
        candle = {"bar_time": bar_time.isoformat(timespec="seconds"),
                  "open": values[0], "high": values[1], "low": values[2],
                  "close": values[3], "volume": values[4],
                  "timeframe_minutes": interval, "is_complete": True,
                  "trade_date": bar_time.date().isoformat(), "session": session,
                  "display_only": True, "market_data_identity": identity,
                  "market_source": source}
        if source == "INTEGRATED" and interval == 1 and minute == 15 * 60 + 35:
            _, marked = split_native_closing_price_candles(
                [candle], timeframe_minutes=1,
                market_data_identity=identity, market_source=source,
            )
            candle = marked[0]
        previous = by_time.get(bar_time)
        if previous is not None and previous != candle:
            return None
        by_time[bar_time] = candle
    candles = [by_time[key] for key in sorted(by_time)]
    return {"stock_code": code, "timeframe_minutes": interval,
            "market_data_identity": identity, "market_source": source,
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
    plan = base.get("source_plan")
    selected = native_history_source(plan, now)
    expected_source = ("INTEGRATED" if selected["source"] == "SOR" else selected["source"]) if selected else (
        "INTEGRATED" if base.get("nxt_available") else "KRX")
    expected_identity = (selected["market_data_identity"] if selected else
                         f"{base.get('stock_code')}_AL" if base.get("nxt_available") else base.get("stock_code"))
    if (not isinstance(timeline, dict)
            or timeline.get("status") not in {"ONGOING", "ENDED", "END_UNKNOWN"}
            or type(base.get("nxt_available")) is not bool
            or base.get("stock_code") != history.get("stock_code")
            or history.get("market_data_identity") != expected_identity
            or history.get("market_source") != expected_source
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
    if (base.get("chart_candle_source") in {"CALCULATION_SOR_AL", "INTEGRATED_DISPLAY_ONLY",
                                           "CALCULATION_KRX_NATIVE", "CALCULATION_NXT_NATIVE"}
            and base.get("projection_status") == "VALID"):
        for row in base.get("candles", []):
            bar_time = candle_market_datetime(row)
            if (bar_time is not None and start <= bar_time <= end
                    and row.get("market_source") == expected_source
                    and row.get("market_data_identity") == expected_identity):
                by_time[bar_time] = dict(row)
    if not by_time:
        return base
    candles = [by_time[key] for key in sorted(by_time)]
    updated = dict(base)
    updated["candles"] = candles
    updated["projection_status"] = "VALID"
    updated["chart_domain_label"] = "NXT _NX" if expected_source == "NXT" else "SOR _AL" if base["nxt_available"] else "KRX"
    updated["chart_candle_source"] = (
        "BROKER_NXT_NATIVE_CHART_HISTORY" if expected_source == "NXT" else
        "BROKER_AL_NATIVE_CHART_HISTORY" if base["nxt_available"]
        else "BROKER_KRX_NATIVE_CHART_HISTORY")
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
