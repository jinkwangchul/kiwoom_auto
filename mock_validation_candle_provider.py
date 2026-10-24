# -*- coding: utf-8 -*-
"""Read-only Candle projection owned by the Mock Validation domain."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Callable

from candle_manager import DEFAULT_CANDLES_MAX_COUNT, canonical_candle_content_hash
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    candle_market_datetime,
    normalize_candle_sessions,
    read_canonical_bar_minutes,
    validate_market_bar_projection_request,
)
from kiwoom_candle_adapter import normalize_opt10080_rows


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
    cache: dict[tuple[str, int], dict[str, Any]] | None = None,
    pending: set[tuple[str, int]] | None = None,
    identity_reader: Callable[[str], tuple[str, int, str, str]] | None = None,
    latest_trade: Any = None,
) -> dict[str, Any]:
    """Read legacy KRX bars for explicit historical diagnostics only."""

    del project_root
    code = str(stock_code or "").strip()
    if consumer_scope != "HISTORICAL_DIAGNOSTIC":
        return {"available": False, "stock_code": code, "candles": [],
                "availability_state": "SOURCE_INVALID"}
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
    try:
        interval = read_canonical_bar_minutes(rules)
    except (TypeError, ValueError) as exc:
        return {
            "available": False,
            "stock_code": code,
            "candles": [],
            "availability_state": "INTERVAL_UNSUPPORTED",
            "reason": str(exc),
        }

    base: dict[str, Any] = {
        "stock_code": code,
        "timeframe_minutes": interval,
        "projection": request["projection"],
        "direct_timeframe_source": True,
    }
    windows = normalize_candle_sessions(session_windows)
    if isinstance(session_windows, (list, tuple)) and session_windows and len(windows) != len(session_windows):
        return {**base, "available": False, "candles": [], "availability_state": "SESSION_INVALID"}
    reader = getattr(api, "request_minute_candles_read_only", None)
    if not callable(reader) or not callable(identity_reader):
        return {
            **base,
            "available": False,
            "candles": [],
            "availability_state": "DIRECT_SOURCE_UNAVAILABLE",
        }
    cache = cache if cache is not None else {}
    pending = pending if pending is not None else set()
    key = (code, interval)
    now = as_of or datetime.now(SEOUL_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SEOUL_TIMEZONE)
    now = now.astimezone(SEOUL_TIMEZONE)
    try:
        identity = identity_reader(code)
    except Exception:
        identity = ("", 0, "", "")
    entry = cache.get(key, {})
    if entry and entry.get("identity") != identity:
        cache.pop(key, None)
        pending.discard(key)
        entry = {}
    minute_key = now.strftime("%Y%m%d%H%M")
    if key not in pending and entry.get("request_minute") != minute_key:
        if identity[0] and identity[2]:
            pending.add(key)
            cache[key] = {**entry, "identity": identity, "request_minute": minute_key}

            def received(result: dict[str, Any]) -> None:
                try:
                    current_identity = identity_reader(code)
                except Exception:
                    return
                if (current_identity != identity
                    or cache.get(key, {}).get("request_minute") != minute_key):
                    return
                pending.discard(key)
                if (
                    not isinstance(result, dict)
                    or result.get("ok") is not True
                    or result.get("code") != code
                    or result.get("market_data_identity", code) != code
                    or result.get("interval") != interval
                    or result.get("market_source") != "KRX"
                ):
                    return
                cache[key] = {
                    "identity": identity,
                    "request_minute": minute_key,
                    "rows": normalize_opt10080_rows(result.get("rows")),
                }

            try:
                response = reader(
                    code, interval=interval,
                    count=min(max(int(request.get("history_target_bars", request.get("warmup_bars", 1))) + 2, 3), DEFAULT_CANDLES_MAX_COUNT),
                    callback=received,
                )
                if isinstance(response, dict) and response.get("ok") is False:
                    pending.discard(key)
            except Exception:
                pending.discard(key)
    raw = cache.get(key, {}).get("rows", ())
    completed: list[dict[str, Any]] = []
    forming: dict[str, Any] | None = None
    for row in raw:
        bar_time = candle_market_datetime(row)
        if bar_time is None:
            continue
        minute_of_day = bar_time.hour * 60 + bar_time.minute
        session = next(
            (name for name, start, end in windows if start <= minute_of_day < end),
            "" if windows else "regular",
        )
        if not session:
            continue
        bar = {
            "bar_time": bar_time.isoformat(timespec="seconds"),
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume"), "timeframe_minutes": interval,
            "trade_date": bar_time.date().isoformat(), "session": session,
        }
        if now >= bar_time + timedelta(minutes=interval) or (
            now.date() == bar_time.date()
            and now.hour * 60 + now.minute >= 930
            and 540 <= bar_time.hour * 60 + bar_time.minute < 930
        ):
            completed.append({**bar, "is_complete": True})
        elif bar_time <= now:
            forming = {**bar, "is_complete": False}
    target_count = int(request.get("history_target_bars", 0) or 0)
    if target_count:
        completed = completed[-target_count:]
    if forming is not None and latest_trade is not None:
        trade_time = candle_market_datetime({"time": getattr(latest_trade, "market_datetime", "")})
        price = getattr(latest_trade, "current_price", None)
        start = candle_market_datetime(forming)
        if (start is not None and trade_time is not None
            and start <= trade_time < start + timedelta(minutes=interval)
            and isinstance(price, (int, float))
            and isinstance(forming["high"], (int, float))
            and isinstance(forming["low"], (int, float))):
            forming["close"] = price
            forming["high"] = max(forming["high"], price)
            forming["low"] = min(forming["low"], price)
    candles = completed + ([forming] if request["projection"] == "FORMING_BASE_BAR" and forming else [])
    warmup = int(request.get("warmup_bars", 0) or 0)
    forming_required = request["projection"] == "FORMING_BASE_BAR" and warmup > 0
    available = len(completed) >= warmup and bool(candles) and (not forming_required or forming is not None)
    return {
        **base, "available": available,
        "availability_state": (
            "AVAILABLE" if available else
            "FORMING_SOURCE_UNAVAILABLE" if forming_required and forming is None else
            "DIRECT_TIMEFRAME_HISTORY_INSUFFICIENT"
        ),
        "candles": candles if available else [],
        "available_timeframe_bars": len(completed),
        "source_identity": canonical_candle_content_hash(candles) if candles else "",
    }


def project_mock_sor_calculation_snapshot(
    value: Any, *, stock_code: str, rules: dict[str, Any],
    projection_request: dict[str, Any], as_of: datetime,
) -> dict[str, Any]:
    """Admit detached broker _AL calculation values without a Mock TR or execution link."""
    blocked = {"available": False, "candles": [], "availability_state": "SOR_VALUE_UNAVAILABLE",
               "calculation_only": True, "execution_enabled": False,
               "queue_connected": False, "send_order_connected": False}
    try:
        request = validate_market_bar_projection_request(projection_request, require_warmup=False)
        interval = read_canonical_bar_minutes(rules)
        if (not isinstance(as_of, datetime) or as_of.tzinfo is None
                or as_of.utcoffset() != timedelta(hours=9)):
            return blocked
        if (not isinstance(value, dict)
                or value.get("calculation_as_of") != as_of.isoformat()):
            return blocked
        if (not isinstance(value, dict) or value.get("available") is not True
                or value.get("stock_code") != stock_code
                or value.get("timeframe_minutes") != interval
                or value.get("projection") != request["projection"]
                or value.get("market_source") != "INTEGRATED"
                or value.get("market_data_identity") != f"{stock_code}_AL"
                or value.get("calculation_source") != "BROKER_AL_CONFIRMED_M1"
                or value.get("source_proof") != "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
                or value.get("calculation_only") is not True
                or value.get("execution_enabled") is not False
                or value.get("queue_connected") is not False
                or value.get("send_order_connected") is not False):
            return blocked
        rows = value.get("candles")
        if not isinstance(rows, list) or not 0 < len(rows) <= 10000:
            return blocked
        previous = None
        completed = 0
        detached = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("timeframe_minutes") != interval:
                return blocked
            stamp = datetime.fromisoformat(row["bar_time"])
            if (stamp.utcoffset() != timedelta(hours=9)
                    or (previous is not None and stamp <= previous)
                    or stamp > as_of or stamp.date() > as_of.date()
                    or row.get("trade_date") != stamp.date().isoformat()
                    or not isinstance(row.get("session"), str)
                    or not row["session"]):
                return blocked
            previous = stamp
            nums = tuple(row[k] for k in ("open", "high", "low", "close", "volume"))
            if (any(type(n) not in (int, float) or not isfinite(n) for n in nums)
                    or nums[2] <= 0 or nums[4] < 0
                    or not nums[2] <= nums[0] <= nums[1]
                    or not nums[2] <= nums[3] <= nums[1]):
                return blocked
            complete = row.get("is_complete")
            if complete is True:
                if stamp + timedelta(minutes=interval) > as_of:
                    return blocked
                completed += 1
            elif (complete is not False or request["projection"] != "FORMING_BASE_BAR"
                  or index != len(rows) - 1 or stamp.date() != as_of.date()):
                return blocked
            detached.append({key: row[key] for key in (
                "bar_time", "open", "high", "low", "close", "volume",
                "timeframe_minutes", "is_complete", "trade_date", "session",
            )})
        if (previous.date() != as_of.date()
                or as_of - previous > timedelta(minutes=interval + 1)
                or completed < request.get("warmup_bars", 0)
                or (request["projection"] == "FORMING_BASE_BAR"
                    and rows[-1].get("is_complete") is not False)):
            return blocked
        if request["projection"] == "FORMING_BASE_BAR":
            received = datetime.fromisoformat(value["forming_snapshot_received_at"])
            if (value.get("forming_snapshot_source") != "BROKER_AL_TR_FORMING_M1"
                    or received.utcoffset() != timedelta(hours=9)
                    or received.replace(second=0, microsecond=0)
                       != as_of.replace(second=0, microsecond=0)
                    or received > datetime.now(SEOUL_TIMEZONE)):
                return blocked
        receipt_present = "sor_source_receipt" in value
        receipt = value.get("sor_source_receipt")
        if receipt_present:
            if (request["projection"] != "FORMING_BASE_BAR"
                    or not isinstance(receipt, dict)
                    or set(receipt) != {
                        "stock_code", "timeframe_key", "source_base_timeframe_key",
                        "source_snapshot_key", "connection_epoch", "session_sha256",
                        "source_as_of", "received_at", "source_proof",
                        "independent_merge_verified",
                    }
                    or receipt.get("stock_code") != stock_code
                    or receipt.get("timeframe_key") != f"M{interval}"
                    or receipt.get("source_base_timeframe_key") != "M1"
                    or receipt.get("source_as_of") != as_of.isoformat()
                    or receipt.get("received_at") != value.get("forming_snapshot_received_at")
                    or receipt.get("source_proof")
                    != "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
                    or receipt.get("independent_merge_verified") is not False
                    or type(receipt.get("connection_epoch")) is not int
                    or receipt["connection_epoch"] <= 0
                    or any(not isinstance(receipt.get(key), str)
                           or len(receipt[key]) != 64
                           or any(ch not in "0123456789abcdef" for ch in receipt[key])
                           for key in ("source_snapshot_key", "session_sha256"))):
                return blocked
        result = {"available": True, "candles": deepcopy(detached),
                "stock_code": stock_code, "timeframe_minutes": interval,
                "projection": request["projection"], "market_source": "INTEGRATED",
                "market_data_identity": f"{stock_code}_AL",
                "calculation_source": "BROKER_AL_CONFIRMED_M1",
                "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                "calculation_only": True, "execution_enabled": False,
                "queue_connected": False, "send_order_connected": False}
        if receipt_present:
            result["sor_source_receipt"] = deepcopy(receipt)
        return result
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return blocked


__all__ = [
    "is_production_market_data_callback",
    "project_mock_routine_candles",
    "project_mock_sor_calculation_snapshot",
]
