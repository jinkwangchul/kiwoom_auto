# -*- coding: utf-8 -*-
"""Read-only Candle projection owned by the Mock Validation domain."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
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
from stock_market_source_plan import (native_bar_end, read_stock_market_source_plan, source_for_time,
                                      split_native_closing_price_candles)
from sor_native_selected_current_join import join_native_selected_completed


_DIRECT_SESSION_MINUTES = {
    "NXT_PRE": (8 * 60, 8 * 60 + 50),
    "KRX": (9 * 60, 15 * 60 + 20),
    "KRX_CLOSING_AUCTION": (15 * 60 + 20, 15 * 60 + 30),
    "AL_TRANSITION": (15 * 60 + 30, 15 * 60 + 40),
    "NXT_AFTER": (15 * 60 + 40, 20 * 60),
}


def _verified_sparse_m1_receipt(value: dict[str, Any], rows: list[dict[str, Any]],
                                stock_code: str, as_of: datetime) -> bool:
    """Bind an idle auction's last real M1 bar to the broker _AL receipt."""
    receipt = value.get("direct_source_receipt")
    if (not isinstance(receipt, dict)
            or set(receipt) != {"stock_code", "timeframe_key", "source_content_sha256",
                                    "connection_epoch", "session_sha256", "source_as_of"}
            or receipt.get("stock_code") != stock_code
            or receipt.get("timeframe_key") != "M1"
            or receipt.get("source_as_of") != as_of.isoformat()
            or type(receipt.get("connection_epoch")) is not int
            or receipt["connection_epoch"] <= 0
            or not isinstance(receipt.get("session_sha256"), str)
            or len(receipt["session_sha256"]) != 64
            or any(ch not in "0123456789abcdef" for ch in receipt["session_sha256"])):
        return False
    for row in rows:
        stamp = datetime.fromisoformat(row["bar_time"])
        minute = stamp.hour * 60 + stamp.minute
        native_session = next((name for name, (start, end) in _DIRECT_SESSION_MINUTES.items()
                               if start <= minute < end), None)
        if native_session is None or row["session"] not in {native_session, "regular"}:
            return False
    original = [
        {"time": datetime.fromisoformat(row["bar_time"]).strftime("%Y%m%d%H%M%S"),
         **{key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}}
        for row in rows
    ]
    return receipt.get("source_content_sha256") == canonical_candle_content_hash(original)


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
    """Project native KRX or premarket NXT bars from the frozen source plan."""

    code = str(stock_code or "").strip()
    now = as_of or datetime.now(SEOUL_TIMEZONE)
    if now.tzinfo is None:
        return {"available": False, "stock_code": code, "candles": [],
                "availability_state": "SOURCE_INVALID"}
    now = now.astimezone(SEOUL_TIMEZONE)
    try:
        plan = read_stock_market_source_plan(Path(project_root), code, now.date())
        active = source_for_time(plan, now) if plan is not None else None
    except ValueError:
        active = None
    if (consumer_scope not in {"MOCK", "HISTORICAL_DIAGNOSTIC", "KRX_CALCULATION_ONLY", "NXT_CALCULATION_ONLY"}
            or active is None or active["source"] not in {"KRX", "NXT"}
            or (consumer_scope.endswith("_CALCULATION_ONLY")
                and consumer_scope != f"{active['source']}_CALCULATION_ONLY")):
        return {"available": False, "stock_code": code, "candles": [],
                "availability_state": "SOURCE_INVALID"}
    source = active["source"]
    broker_identity = active["market_data_identity"]
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
    strict_receipt = (consumer_scope.endswith("_CALCULATION_ONLY") or source == "NXT"
                      or request["projection"] == "FORMING_BASE_BAR")
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
    windows = normalize_candle_sessions(
        [item for item in plan["sessions"] if item["source"] == source]
        if consumer_scope != "HISTORICAL_DIAGNOSTIC" else session_windows
    )
    if (consumer_scope == "HISTORICAL_DIAGNOSTIC" and isinstance(session_windows, (list, tuple))
            and session_windows and len(windows) != len(session_windows)):
        return {**base, "available": False, "candles": [], "availability_state": "SESSION_INVALID"}
    reader = getattr(api, "request_minute_candles_display_only_nxt" if source == "NXT"
                     else "request_minute_candles_display_only_krx", None)
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
    try:
        identity = identity_reader(code)
    except Exception:
        identity = ("", 0, "", "")
    entry = cache.get(key, {})
    if entry and (entry.get("identity") != identity
                  or entry.get("market_data_identity") != broker_identity):
        cache.pop(key, None)
        pending.discard(key)
        entry = {}
    minute_key = now.strftime("%Y%m%d%H%M")
    if key not in pending and entry.get("request_minute") != minute_key:
        if identity[0] and identity[2]:
            pending.add(key)
            cache[key] = {"identity": identity, "request_minute": minute_key,
                          "market_data_identity": broker_identity, "rows": ()}

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
                    or result.get("market_data_identity") != broker_identity
                    or result.get("interval") != interval
                    or result.get("market_source") != source
                    or result.get("display_only") is not True
                ):
                    return
                capture = {}
                if strict_receipt:
                    try:
                        requested = datetime.fromisoformat(result["request_as_of"])
                        received_at = datetime.fromisoformat(result["received_at"])
                        receipt_valid = (
                            result.get("type") == "minute_candles"
                            and result.get("trcode") == "opt10080"
                            and isinstance(result.get("rqname"), str)
                            and result["rqname"].startswith(f"opt10080_{code}_")
                            and result.get("request_id") == result["rqname"]
                            and result.get("connection_epoch") == identity[1]
                            and result.get("login_session_id") == identity[2]
                            and isinstance(result.get("rows"), list)
                            and result.get("rows_count") == len(result["rows"])
                            and requested.tzinfo is not None
                            and received_at.tzinfo is not None
                            and requested <= received_at
                            and requested.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M") == minute_key
                            and received_at.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M") == minute_key
                        )
                    except (KeyError, TypeError, ValueError):
                        receipt_valid = False
                    if not receipt_valid:
                        return
                    capture = {"request_id": result["request_id"],
                               "request_as_of": requested.isoformat(),
                               "received_at": received_at.isoformat()}
                cache[key] = {
                    "identity": identity,
                    "market_data_identity": broker_identity,
                    "request_minute": minute_key,
                    "rows": normalize_opt10080_rows(result.get("rows")),
                    "capture": capture,
                }

            try:
                response = reader(
                    code, interval=interval,
                    count=min(max(int(request.get("history_target_bars", request.get("warmup_bars", 1))) + 2, 3), DEFAULT_CANDLES_MAX_COUNT),
                    callback=received,
                    **({"nxt_available": True} if source == "NXT" else {}),
                )
                if isinstance(response, dict) and response.get("ok") is False:
                    pending.discard(key)
            except Exception:
                pending.discard(key)
    cached = cache.get(key, {})
    raw = cached.get("rows", ())
    capture = cached.get("capture", {})
    snapshot_at = datetime.fromisoformat(capture["request_as_of"]) if capture else now
    cutoff = min(now, snapshot_at)
    completed: list[dict[str, Any]] = []
    forming: dict[str, Any] | None = None
    for row in raw:
        stamp = str(row.get("time") or "")
        if len(stamp) != 14 or not stamp.isdigit() or stamp[-2:] != "00":
            continue
        bar_time = candle_market_datetime(row)
        if bar_time is None:
            continue
        prices = tuple(row.get(field) for field in
                       ("open", "high", "low", "close", "volume"))
        if (any(type(value) not in (int, float) or not isfinite(value)
                for value in prices)
                or prices[2] <= 0 or prices[4] < 0
                or not prices[2] <= prices[0] <= prices[1]
                or not prices[2] <= prices[3] <= prices[1]):
            continue
        minute_of_day = bar_time.hour * 60 + bar_time.minute
        if consumer_scope != "HISTORICAL_DIAGNOSTIC":
            clock = bar_time.strftime("%H:%M:%S")
            session = next((item["name"] for item in plan["sessions"]
                            if item["source"] == source
                            and item["start_time"] <= clock < item["end_time"]), "")
        else:
            session = next(
                (name for name, start, end in windows if start <= minute_of_day < end),
                "" if windows else "regular",
            )
        if not session or bar_time > cutoff:
            continue
        bar = {
            "bar_time": bar_time.isoformat(timespec="seconds"),
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume"), "timeframe_minutes": interval,
            "trade_date": bar_time.date().isoformat(), "session": session,
            "market_source": source, "market_data_identity": broker_identity,
            "display_only": True,
        }
        session_end = bar_time + timedelta(minutes=interval)
        if consumer_scope != "HISTORICAL_DIAGNOSTIC":
            session_end = native_bar_end(plan, bar_time, interval)
        if (source == "KRX" and bar_time.strftime("%H:%M:%S") == "15:30:00") or cutoff >= session_end:
            completed.append({**bar, "is_complete": True})
        elif (bar_time.date() == now.date()
              and bar_time <= now < session_end and capture
              and snapshot_at.strftime("%Y%m%d%H%M") == minute_key):
            forming = {**bar, "is_complete": False}
    target_count = int(request.get("history_target_bars", 0) or 0)
    if target_count:
        completed = completed[-target_count:]
    if (consumer_scope == "HISTORICAL_DIAGNOSTIC" and forming is not None
            and latest_trade is not None):
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
    forming_included = bool(request["projection"] == "FORMING_BASE_BAR" and forming)
    warmup = int(request.get("warmup_bars", 0) or 0)
    forming_required = request["projection"] == "FORMING_BASE_BAR" and warmup > 0
    available = len(completed) >= warmup and bool(candles) and (not forming_required or forming is not None)
    source_identity = canonical_candle_content_hash(candles) if candles else ""
    native_receipt = {
        "stock_code": code, "timeframe_key": f"M{interval}",
        "source_content_sha256": source_identity,
        "source_as_of": now.isoformat(),
        "connection_epoch": identity[1],
        "session_sha256": sha256(str(identity[2]).encode("utf-8")).hexdigest(),
        **capture,
    } if (isinstance(identity, tuple) and len(identity) >= 3
          and type(identity[1]) is int and identity[1] > 0
          and isinstance(identity[2], str) and identity[2]) else {}
    return {
        **base, "available": available,
        "market_source": source, "market_data_identity": broker_identity,
        "calculation_only": True, "execution_enabled": False,
        "queue_connected": False, "send_order_connected": False,
        "availability_state": (
            "AVAILABLE" if available else
            "FORMING_SOURCE_UNAVAILABLE" if forming_required and forming is None else
            "DIRECT_TIMEFRAME_HISTORY_INSUFFICIENT"
        ),
        "candles": candles if available else [],
        "available_timeframe_bars": len(completed),
        "source_identity": source_identity,
        "source_proof": f"BROKER_{source}_NATIVE_TIMEFRAME_RECEIPT",
        "calculation_source": f"BROKER_{source}_CONFIRMED_DIRECT",
        "direct_history_source": f"BROKER_{source}_CONFIRMED_DIRECT",
        "direct_source_receipt": native_receipt,
        "complete": not forming_included,
        "forming_included": forming_included,
        "forming_snapshot_source": (
            f"BROKER_{source}_NATIVE_CURRENT_TIMEFRAME" if forming_included else ""
        ),
    }


def verified_native_calculation_supply(
    value: dict[str, Any], *, stock_code: str, interval: int,
    as_of: datetime, plan: dict[str, Any],
) -> bool:
    """Shared calculation/chart admission for direct KRX and NXT snapshots."""
    if isinstance(value, dict) and "native_current_base" in value:
        base, proof = value.get("native_current_base"), value.get("native_current_proof")
        if not isinstance(base, dict) or not isinstance(proof, dict):
            return False
        if not verified_native_calculation_supply(
                base, stock_code=stock_code, interval=interval, as_of=as_of, plan=plan):
            return False
        projected = project_mock_sor_native_selected_current(
            base, proof, stock_code=stock_code,
            rules={"bar": {"bar_minutes": interval}}, as_of=as_of,
            project_root=Path(__file__).resolve().parent)
        return (projected.get("available") is True
                and projected.get("candles") == value.get("candles")
                and projected.get("source_identity") == value.get("source_identity")
                and projected.get("native_current_tick_revision") ==
                value.get("native_current_tick_revision"))
    try:
        active = source_for_time(plan, as_of)
        if active is None or active["source"] not in {"KRX", "NXT"}:
            return False
        source, identity = active["source"], active["market_data_identity"]
        rows, receipt = value.get("candles"), value.get("direct_source_receipt")
        if not isinstance(rows, list) or not rows or not isinstance(receipt, dict):
            return False
        digest = canonical_candle_content_hash(rows)
        if not (value.get("available") is True and value.get("stock_code") == stock_code
                and value.get("timeframe_minutes") == interval
                and value.get("projection") in {"COMPLETED_TIMEFRAME", "FORMING_BASE_BAR"}
                and value.get("market_source") == source
                and value.get("market_data_identity") == identity
                and value.get("calculation_source") == f"BROKER_{source}_CONFIRMED_DIRECT"
                and value.get("direct_history_source") == f"BROKER_{source}_CONFIRMED_DIRECT"
                and value.get("direct_timeframe_source") is True
                and value.get("source_proof") == f"BROKER_{source}_NATIVE_TIMEFRAME_RECEIPT"
                and value.get("calculation_only") is True
                and all(value.get(key) is False for key in
                        ("execution_enabled", "queue_connected", "send_order_connected"))
                and value.get("source_identity") == digest
                and receipt.get("source_content_sha256") == digest
                and receipt.get("stock_code") == stock_code
                and receipt.get("timeframe_key") == f"M{interval}"
                and receipt.get("source_as_of") == as_of.isoformat()
                and type(receipt.get("connection_epoch")) is int and receipt["connection_epoch"] > 0
                and isinstance(receipt.get("session_sha256"), str)
                and len(receipt["session_sha256"]) == 64
                and all(ch in "0123456789abcdef" for ch in receipt["session_sha256"])):
            return False
        requested = datetime.fromisoformat(receipt["request_as_of"]) if receipt.get("request_as_of") else None
        received = datetime.fromisoformat(receipt["received_at"]) if receipt.get("received_at") else None
        previous = None
        forming_count = 0
        for row in rows:
            stamp = candle_market_datetime(row)
            prices = tuple(row.get(field) for field in ("open", "high", "low", "close", "volume"))
            if (any(type(number) not in (int, float) or not isfinite(number) for number in prices)
                    or prices[2] <= 0 or prices[4] < 0
                    or not prices[2] <= prices[0] <= prices[1]
                    or not prices[2] <= prices[3] <= prices[1]):
                return False
            if (stamp is None or stamp > as_of or (previous and stamp <= previous)
                    or stamp.second != 0 or stamp.microsecond != 0
                    or row.get("display_only_special")
                    or row.get("timeframe_minutes") != interval
                    or row.get("market_source") != source or row.get("market_data_identity") != identity):
                return False
            session = next((item for item in plan["sessions"] if item["source"] == source
                            and item["start_time"] <= stamp.strftime("%H:%M:%S") < item["end_time"]), None)
            if session is None or row.get("session") != session["name"]:
                return False
            end = native_bar_end(plan, stamp, interval)
            if row.get("is_complete") is False:
                forming_count += 1
                if not (row is rows[-1] and forming_count == 1
                        and value.get("projection") == "FORMING_BASE_BAR"
                        and value.get("forming_included") is True and value.get("complete") is False
                        and value.get("forming_snapshot_source") == f"BROKER_{source}_NATIVE_CURRENT_TIMEFRAME"
                        and requested is not None and received is not None
                        and requested.tzinfo is not None and received.tzinfo is not None
                        and requested <= received and stamp <= requested < end and as_of < end
                        and requested.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M")
                        == as_of.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M")
                        and received.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M")
                        == as_of.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d%H%M")
                        and str(receipt.get("request_id", "")).startswith(f"opt10080_{stock_code}_")):
                    return False
            elif row.get("is_complete") is not True:
                return False
            elif not (source == "KRX" and stamp.strftime("%H:%M:%S") == "15:30:00"):
                cutoff = min(as_of, requested) if requested is not None else as_of
                if end > cutoff:
                    return False
            previous = stamp
        return forming_count == int(value.get("forming_included") is True)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def project_mock_sor_calculation_snapshot(
    value: Any, *, stock_code: str, rules: dict[str, Any],
    projection_request: dict[str, Any], as_of: datetime,
    native_selected_current: bool = False,
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
        direct_completed = interval > 1 and request["projection"] == "COMPLETED_TIMEFRAME"
        native_m1_completed = (interval == 1 and native_selected_current
                               and request["projection"] == "COMPLETED_TIMEFRAME")
        direct_forming = interval > 1 and request["projection"] == "FORMING_BASE_BAR"
        native_m3_forming = (
            direct_forming and interval == 3
            and value.get("calculation_source") == "BROKER_AL_DIRECT_M3_WITH_FORMING_M1"
        ) if isinstance(value, dict) else False
        if (not isinstance(value, dict) or value.get("available") is not True
                or value.get("stock_code") != stock_code
                or value.get("timeframe_minutes") != interval
                or value.get("projection") != request["projection"]
                or value.get("market_source") != "INTEGRATED"
                or value.get("market_data_identity") != f"{stock_code}_AL"
                or value.get("calculation_source") != (
                    "BROKER_AL_CONFIRMED_DIRECT" if direct_completed
                    else "BROKER_AL_DIRECT_M3_WITH_FORMING_M1" if native_m3_forming
                    else "BROKER_AL_CONFIRMED_M1")
                or (direct_forming and interval == 3
                    and isinstance(value.get("forming_base_bar"), dict)
                    and not native_m3_forming)
                or value.get("source_proof") != "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
                or value.get("calculation_only") is not True
                or value.get("execution_enabled") is not False
                or value.get("queue_connected") is not False
                or value.get("send_order_connected") is not False):
            return blocked
        rows = value.get("candles")
        if not isinstance(rows, list) or not 0 < len(rows) <= 10000:
            return blocked
        direct = direct_completed or direct_forming
        if direct:
            if (value.get("direct_timeframe_source") is not True
                    or value.get("available_timeframe_bars") != len(rows)
                    or value.get("direct_history_source") != "BROKER_AL_CONFIRMED_DIRECT"
                    or value.get("complete") is not (not direct_forming)
                    or value.get("forming_included") is not direct_forming):
                return blocked
            receipt = value.get("direct_source_receipt")
            source_identity = (value.get("source_identity") if direct_completed
                               else value.get("direct_source_identity"))
            if (not isinstance(receipt, dict)
                    or set(receipt) != {"stock_code", "timeframe_key",
                                            "source_content_sha256", "connection_epoch",
                                            "session_sha256", "source_as_of"}
                    or receipt.get("stock_code") != stock_code
                    or receipt.get("timeframe_key") != f"M{interval}"
                    or receipt.get("source_as_of") != as_of.isoformat()
                    or type(receipt.get("connection_epoch")) is not int
                    or receipt["connection_epoch"] <= 0
                    or not isinstance(source_identity, str)
                    or receipt.get("source_content_sha256") != source_identity
                    or (direct_forming and (
                        not isinstance(value.get("source_identity"), str)
                        or len(value["source_identity"]) != 64
                        or any(ch not in "0123456789abcdef"
                               for ch in value["source_identity"])))
                    or any(not isinstance(receipt.get(key), str)
                           or len(receipt[key]) != 64
                           or any(ch not in "0123456789abcdef" for ch in receipt[key])
                           for key in ("source_content_sha256", "session_sha256"))):
                return blocked
        elif interval == 1 and value.get("direct_history_source") == "BROKER_AL_CONFIRMED_DIRECT":
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
                if not direct and stamp + timedelta(minutes=interval) > as_of:
                    return blocked
                if direct:
                    window = _DIRECT_SESSION_MINUTES.get(row["session"])
                    minute = stamp.hour * 60 + stamp.minute
                    if (window is None or not window[0] <= minute < window[1]
                            or min(stamp + timedelta(minutes=interval),
                                   stamp.replace(hour=window[1] // 60,
                                                 minute=window[1] % 60,
                                                 second=0, microsecond=0))
                            > as_of.replace(second=0, microsecond=0)):
                        return blocked
                completed += 1
            elif (complete is not False or request["projection"] != "FORMING_BASE_BAR"
                  or index != len(rows) - 1 or stamp.date() != as_of.date()):
                return blocked
            if direct and complete is True and (
                row.get("market_source") != "INTEGRATED"
                or row.get("market_data_identity") != f"{stock_code}_AL"
                or row.get("display_only") is not True
            ):
                return blocked
            detached.append(deepcopy(row) if direct else {key: row[key] for key in (
                "bar_time", "open", "high", "low", "close", "volume",
                "timeframe_minutes", "is_complete", "trade_date", "session",
            )})
        if direct and (completed == 0 or canonical_candle_content_hash(
            detached if direct_completed else detached[:-1]
        ) != source_identity):
            return blocked
        sparse_direct = (
            direct_completed
            and 15 * 60 + 20 <= as_of.hour * 60 + as_of.minute < 15 * 60 + 40
            and previous + timedelta(minutes=interval) >= previous.replace(
                hour=15, minute=20, second=0, microsecond=0)
        )
        sparse_m1 = (
            interval == 1 and request["projection"] == "COMPLETED_TIMEFRAME"
            and 15 * 60 + 20 <= as_of.hour * 60 + as_of.minute < 15 * 60 + 40
            and previous.hour == 15 and previous.minute >= 19
            and _verified_sparse_m1_receipt(value, detached, stock_code, as_of)
        )
        if native_m1_completed and not _verified_sparse_m1_receipt(
                value, detached, stock_code, as_of):
            return blocked
        if (previous.date() != as_of.date()
                or (not (sparse_direct or sparse_m1)
                    and as_of - previous > timedelta(minutes=(
                         3 * interval if direct_completed and native_selected_current
                        else interval + 1)))
                or completed < request.get("warmup_bars", 0)
                or ("history_target_bars" in request
                    and len(rows) < request["history_target_bars"])
                or (request["projection"] == "FORMING_BASE_BAR"
                    and rows[-1].get("is_complete") is not False)):
            return blocked
        if request["projection"] == "FORMING_BASE_BAR":
            received = datetime.fromisoformat(value["forming_snapshot_received_at"])
            if (value.get("forming_snapshot_source") not in (
                    {"BROKER_AL_TR_FORMING_M1", "BROKER_AL_PARITY_NATIVE_FORMING_M1"}
                    if direct_forming else {"BROKER_AL_TR_FORMING_M1"})
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
        if native_m3_forming:
            base = value.get("forming_base_bar")
            if not receipt_present or not isinstance(base, dict):
                return blocked
            base_time = datetime.fromisoformat(base["bar_time"])
            request_time = datetime.fromisoformat(base["snapshot_request_as_of"])
            forming = detached[-1]
            forming_time = datetime.fromisoformat(forming["bar_time"])
            direct_receipt = value["direct_source_receipt"]
            base_values = tuple(base[key] for key in ("open", "high", "low", "close", "volume"))
            if (base_time.utcoffset() != timedelta(hours=9)
                    or request_time.utcoffset() != timedelta(hours=9)
                    or base_time != as_of.replace(second=0, microsecond=0)
                    or not forming_time <= base_time < forming_time + timedelta(minutes=3)
                    or not request_time <= received
                    or received - request_time >= timedelta(minutes=1)
                    or request_time.replace(second=0, microsecond=0) != base_time
                    or base.get("snapshot_received_at") != received.isoformat()
                    or base.get("snapshot_source") != value.get("forming_snapshot_source")
                    or base.get("timeframe_minutes") != 1
                    or base.get("is_complete") is not False
                    or base.get("trade_date") != base_time.date().isoformat()
                    or base.get("session") != forming.get("session")
                    or base.get("market_source") != "INTEGRATED"
                    or base.get("market_data_identity") != f"{stock_code}_AL"
                    or base.get("display_only") is not True
                    or any(type(number) not in (int, float) or not isfinite(number)
                           for number in base_values)
                    or base_values[2] <= 0 or base_values[4] < 0
                    or not base_values[2] <= base_values[0] <= base_values[1]
                    or not base_values[2] <= base_values[3] <= base_values[1]
                    or forming.get("market_source") != "INTEGRATED"
                    or forming.get("market_data_identity") != f"{stock_code}_AL"
                    or forming.get("display_only") is not True
                    or forming["close"] != base["close"]
                    or forming["high"] < base["high"]
                    or forming["low"] > base["low"]
                    or forming["volume"] < base["volume"]
                    or receipt["connection_epoch"] != direct_receipt["connection_epoch"]
                    or receipt["session_sha256"] != direct_receipt["session_sha256"]):
                return blocked
            key_parts = (
                stock_code, value["source_identity"], base["bar_time"],
                {key: base[key] for key in ("open", "high", "low", "close", "volume")},
                received.isoformat(), receipt["connection_epoch"],
                receipt["session_sha256"],
            )
            snapshot_key = sha256(json.dumps(
                key_parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            if snapshot_key != receipt["source_snapshot_key"]:
                return blocked
        try:
            signal_rows, display_rows = split_native_closing_price_candles(
                rows, timeframe_minutes=interval,
                market_data_identity=f"{stock_code}_AL",
                market_source="INTEGRATED",
            )
            source_display = value.get("display_candles")
            if source_display is not None:
                if not isinstance(source_display, list):
                    return blocked
                display_signal, display_rows = split_native_closing_price_candles(
                    source_display, timeframe_minutes=interval,
                    market_data_identity=f"{stock_code}_AL",
                    market_source="INTEGRATED",
                )
                if display_signal != signal_rows:
                    return blocked
        except ValueError:
            return {**blocked, "availability_state": "SOR_SPECIAL_BAR_AMBIGUOUS"}
        accepted_times = {row["bar_time"] for row in signal_rows}
        detached = [row for row in detached if row["bar_time"] in accepted_times]
        if (not detached
                or sum(row["is_complete"] is True for row in detached)
                   < request.get("warmup_bars", 0)
                or (len(signal_rows) != len(rows)
                    and as_of - datetime.fromisoformat(detached[-1]["bar_time"])
                        > timedelta(minutes=interval + 1)
                    and not (15 * 60 + 20 <= as_of.hour * 60 + as_of.minute
                             < 15 * 60 + 40))):
            return blocked
        result = {"available": True, "candles": deepcopy(detached),
                "stock_code": stock_code, "timeframe_minutes": interval,
                "projection": request["projection"], "market_source": "INTEGRATED",
                "market_data_identity": f"{stock_code}_AL",
                "calculation_source": value["calculation_source"],
                "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                "calculation_only": True, "execution_enabled": False,
                "queue_connected": False, "send_order_connected": False}
        if len(signal_rows) != len(display_rows):
            result["display_candles"] = deepcopy(display_rows)
        if receipt_present:
            result["sor_source_receipt"] = deepcopy(receipt)
        if direct:
            result.update(
                direct_history_source="BROKER_AL_CONFIRMED_DIRECT",
                direct_source_receipt=deepcopy(value["direct_source_receipt"]),
                source_identity=value["source_identity"],
                complete=value["complete"],
                forming_included=value["forming_included"],
            )
            if direct_forming:
                result["direct_source_identity"] = value["direct_source_identity"]
                if native_m3_forming:
                    result["forming_base_bar"] = deepcopy(base)
                    result["forming_snapshot_source"] = value["forming_snapshot_source"]
                    result["forming_snapshot_received_at"] = value["forming_snapshot_received_at"]
        elif native_m1_completed:
            result.update(direct_history_source="BROKER_AL_CONFIRMED_M1",
                          direct_source_receipt=deepcopy(value["direct_source_receipt"]),
                          complete=True, forming_included=False)
        return result
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return blocked


def project_mock_sor_native_selected_current(
    completed: Any, proof: Any, *, stock_code: str, rules: dict[str, Any],
    as_of: datetime, project_root: Path,
) -> dict[str, Any]:
    """Join validated direct completed bars with the host's admitted current bar."""
    source = proof.get("market_source") if isinstance(proof, dict) else None
    if source not in {"INTEGRATED", "NXT", "KRX"}:
        source = "INTEGRATED"
    prefix = "AL" if source == "INTEGRATED" else source
    identity = (f"{stock_code}_AL" if source == "INTEGRATED" else
                f"{stock_code}_NX" if source == "NXT" else stock_code)
    plan_source = "SOR" if source == "INTEGRATED" else source
    blocked = {"available": False, "candles": [],
               "availability_state": "SOR_NATIVE_CURRENT_UNAVAILABLE",
               "calculation_only": True, "execution_enabled": False,
               "queue_connected": False, "send_order_connected": False}
    try:
        interval = read_canonical_bar_minutes(rules)
        rows = completed["candles"]
        receipt = completed["direct_source_receipt"]
        forming = proof["forming_candle"]
        started, response = proof["request_receipt"]
        session = proof["session_identity"]
        stamp = candle_market_datetime(forming)
        end = datetime.fromisoformat(forming["native_bar_end"])
        received = datetime.fromisoformat(forming.get(
            "tick_received_at", forming["snapshot_received_at"]))
        plan = read_stock_market_source_plan(project_root, stock_code, as_of.date())
        active = source_for_time(plan, as_of) if plan else None
        seen = source_for_time(plan, received) if plan else None
        if (completed.get("available") is not True
                or completed.get("projection") != "COMPLETED_TIMEFRAME"
                 or completed.get("direct_history_source") != (
                     "BROKER_AL_CONFIRMED_M1" if interval == 1 and source == "INTEGRATED"
                     else f"BROKER_{prefix}_CONFIRMED_DIRECT")
                or completed.get("complete") is not True
                or completed.get("forming_included") is not False
                or not isinstance(rows, list) or not rows
                or not isinstance(receipt, dict) or not isinstance(forming, dict)
                or not isinstance(started, dict) or not isinstance(response, dict)
                or not isinstance(session, tuple) or len(session) != 2
                or receipt.get("connection_epoch") != session[0]
                or receipt.get("session_sha256") != sha256(session[1].encode()).hexdigest()
                or proof.get("stock_code") != stock_code
                or proof.get("timeframe_minutes") != interval
                or proof.get("market_source") != source
                or proof.get("market_data_identity") != identity
                or forming.get("market_source") != source
                or forming.get("market_data_identity") != identity
                or forming.get("is_complete") is not False
                or proof.get("request_id") != started.get("rqname")
                or proof.get("request_id") != response.get("rqname")
                or response.get("market_data_identity") != identity
                or (response.get("connection_epoch"), response.get("login_session_id")) != session
                or type(proof.get("tick_revision")) is not int
                or (proof["tick_revision"] > 0 and forming.get("snapshot_source")
                    != f"BROKER_{prefix}_TR_PLUS_CONTIGUOUS_TICKS")
                or stamp is None or not stamp <= received <= as_of < end
                or as_of - received >= timedelta(minutes=1)
                or not isinstance(active, dict) or not isinstance(seen, dict)
                or any(item.get("source") != plan_source
                       or item.get("market_data_identity") != identity
                       or item.get("name") != active.get("name")
                       for item in (active, seen))
                 or datetime.fromisoformat(rows[-1]["bar_time"]) >= stamp):
            return blocked
        joined, native_tail_identity = join_native_selected_completed(
            completed, proof, stock_code=stock_code, interval=interval, as_of=as_of)
        numbers = tuple(forming.get(key) for key in ("open", "high", "low", "close", "volume"))
        if (any(type(number) not in (int, float) or not isfinite(number)
                for number in numbers) or numbers[2] <= 0 or numbers[4] < 0
                or not numbers[2] <= numbers[0] <= numbers[1]
                or not numbers[2] <= numbers[3] <= numbers[1]):
            return blocked
        session_name = next((name for name, (start, finish) in _DIRECT_SESSION_MINUTES.items()
                             if start <= stamp.hour * 60 + stamp.minute < finish), None)
        if session_name is None:
            return blocked
        current = deepcopy(forming)
        current.update(bar_time=stamp.isoformat(), timeframe_minutes=interval,
                       trade_date=stamp.date().isoformat(), session=session_name,
                       display_only=True)
        candles = [*joined, current]
        return {**completed, "candles": candles, "projection": "FORMING_BASE_BAR",
                "complete": False, "forming_included": True,
                 "calculation_source": f"BROKER_{prefix}_NATIVE_SELECTED_CURRENT",
                 "forming_snapshot_source": f"BROKER_{source}_NATIVE_CURRENT_TIMEFRAME",
                 "direct_source_identity": receipt["source_content_sha256"],
                "source_identity": canonical_candle_content_hash(candles),
                 "native_completed_source_identity": native_tail_identity,
                "native_current_request_id": proof["request_id"],
                 "native_current_tick_revision": proof["tick_revision"],
                 "native_current_base": deepcopy(completed),
                 "native_current_proof": deepcopy(proof)}
    except (TypeError, ValueError, KeyError, AttributeError, OSError):
        return blocked


__all__ = [
    "is_production_market_data_callback",
    "project_mock_routine_candles",
    "project_mock_sor_calculation_snapshot",
]
