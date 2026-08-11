# -*- coding: utf-8 -*-
"""Minute-cycle coordinator for bounded opt10080 candle refreshes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PyQt5.QtCore import QTimer

from candle_manager import DEFAULT_CANDLES_MAX_COUNT, load_candles
from candle_timeframe_aggregation import (
    MARKET_BUCKET_ANCHOR,
    SEOUL_TIMEZONE,
    candle_market_datetime,
    filter_candles_by_trade_date,
    parse_market_datetime,
)
from gui_auto_trade_runtime import all_registered_stock_dirs, parse_stock_folder_name
from runtime_io import read_json_dict


BOOTSTRAP_REQUEST_COUNT = DEFAULT_CANDLES_MAX_COUNT
INCREMENTAL_REQUEST_COUNT = 3
MAX_REQUESTS_PER_CYCLE = 15
REQUEST_SPACING_MS = 1_000


RefreshCompletion = Callable[[dict[str, Any]], None]


def _is_refresh_target(stock_dir: Path) -> bool:
    state = read_json_dict(stock_dir / "state.json")
    if state.get("review_required") is True or state.get("trade_enabled") is not True:
        return False
    return str(state.get("status") or "").strip().upper() not in {
        "REVIEW_REQUIRED",
        "REVIEW",
        "EMERGENCY_STOPPED",
        "EMERGENCY_STOP",
        "EMERGENCY",
        "STOPPED",
        "UNREGISTERED",
    }


def _refresh_targets() -> list[tuple[Path, str, str]]:
    targets: list[tuple[Path, str, str]] = []
    for value in all_registered_stock_dirs():
        stock_dir = Path(value)
        if not _is_refresh_target(stock_dir):
            continue
        code, name = parse_stock_folder_name(stock_dir.name)
        if code:
            targets.append((stock_dir, code, name))
    return sorted(targets, key=lambda item: item[1])


def _api_from_host(window: Any) -> Any:
    api = getattr(window, "kiwoom_api", None)
    if api is not None:
        return api
    parent = getattr(window, "parent", None)
    owner = parent() if callable(parent) else None
    return getattr(owner, "kiwoom_api", None)


def _already_bootstrapped(stock_dir: Path, trade_date: str, as_of: datetime) -> bool:
    day_candles = filter_candles_by_trade_date(load_candles(stock_dir), trade_date)
    if not day_candles:
        return False
    times = [
        value
        for candle in day_candles
        if (value := candle_market_datetime(candle)) is not None
    ]
    if not times:
        return False
    if as_of.time() < MARKET_BUCKET_ANCHOR:
        return True
    first = min(times)
    return (first.hour, first.minute) <= (9, 1)


def _has_trade_date_candles(stock_dir: Path, trade_date: str) -> bool:
    return bool(filter_candles_by_trade_date(load_candles(stock_dir), trade_date))


def _rotated_targets(window: Any, targets: list[tuple[Path, str, str]]) -> tuple[list[tuple[Path, str, str]], int]:
    if len(targets) <= MAX_REQUESTS_PER_CYCLE:
        return targets, 0
    offset = int(getattr(window, "_candle_refresh_round_robin_offset", 0) or 0) % len(targets)
    rotated = targets[offset:] + targets[:offset]
    selected = rotated[:MAX_REQUESTS_PER_CYCLE]
    setattr(window, "_candle_refresh_round_robin_offset", (offset + len(selected)) % len(targets))
    return selected, len(targets) - len(selected)


def refresh_operation_candles(
    window: Any,
    minute_key: str,
    *,
    on_complete: RefreshCompletion | None = None,
) -> dict[str, Any]:
    """Refresh active stocks serially, then continue the minute signal cycle.

    One request is issued per second and at most fifteen per minute-cycle. This
    remains below the OpenAPI+ per-second, per-minute, and per-hour TR limits.
    """
    if getattr(window, "_automatic_candle_refresh_inflight", False):
        return {
            "accepted": False,
            "completed": False,
            "reason_code": "CANDLE_REFRESH_ALREADY_RUNNING",
        }

    as_of = parse_market_datetime(minute_key) or datetime.now(SEOUL_TIMEZONE)
    trade_date = as_of.date().isoformat()
    targets, skipped_by_limit = _rotated_targets(window, _refresh_targets())
    api = _api_from_host(window)
    request = getattr(api, "request_minute_candles", None)
    available = getattr(api, "is_available", None)
    connected = getattr(api, "is_connected", None)
    unavailable = (
        not callable(request)
        or (callable(available) and available() is not True)
        or (callable(connected) and connected() is not True)
    )

    summary: dict[str, Any] = {
        "accepted": not unavailable and bool(targets),
        "completed": unavailable or not targets,
        "reason_code": "CANDLE_REFRESH_UNAVAILABLE" if unavailable else (
            "CANDLE_REFRESH_NO_TARGETS" if not targets else "CANDLE_REFRESH_STARTED"
        ),
        "minute_key": minute_key,
        "trade_date": trade_date,
        "target_count": len(targets),
        "skipped_by_limit": skipped_by_limit,
        "requested": 0,
        "succeeded": 0,
        "failed": 0,
        "bootstrap_requested": 0,
        "incremental_requested": 0,
        "max_requests_per_cycle": MAX_REQUESTS_PER_CYCLE,
        "request_spacing_ms": REQUEST_SPACING_MS,
    }

    if unavailable or not targets:
        if callable(on_complete):
            on_complete(dict(summary))
        return summary

    tracker = getattr(window, "_candle_bootstrap_completed", None)
    if not isinstance(tracker, dict):
        tracker = {}
        setattr(window, "_candle_bootstrap_completed", tracker)
    completed_codes = tracker.setdefault(trade_date, set())
    if not isinstance(completed_codes, set):
        completed_codes = set(completed_codes)
        tracker[trade_date] = completed_codes

    setattr(window, "_automatic_candle_refresh_inflight", True)

    def finish() -> None:
        setattr(window, "_automatic_candle_refresh_inflight", False)
        summary["completed"] = True
        summary["reason_code"] = "CANDLE_REFRESH_COMPLETED"
        if callable(on_complete):
            on_complete(dict(summary))

    def request_next(index: int) -> None:
        if index >= len(targets):
            finish()
            return
        stock_dir, code, name = targets[index]
        bootstrap = code not in completed_codes and not _already_bootstrapped(stock_dir, trade_date, as_of)
        count = BOOTSTRAP_REQUEST_COUNT if bootstrap else INCREMENTAL_REQUEST_COUNT
        summary["requested"] += 1
        summary["bootstrap_requested" if bootstrap else "incremental_requested"] += 1
        callback_called = False

        def received(result: dict[str, Any]) -> None:
            nonlocal callback_called
            if callback_called:
                return
            callback_called = True
            if isinstance(result, dict) and result.get("ok") is True:
                summary["succeeded"] += 1
                if bootstrap and _has_trade_date_candles(stock_dir, trade_date):
                    completed_codes.add(code)
            else:
                summary["failed"] += 1
            QTimer.singleShot(REQUEST_SPACING_MS, lambda: request_next(index + 1))

        try:
            started = request(
                code,
                name,
                interval=1,
                count=count,
                max_count=DEFAULT_CANDLES_MAX_COUNT,
                callback=received,
            )
        except Exception as exc:
            received({"ok": False, "error": str(exc)})
            return
        if isinstance(started, dict) and started.get("ok") is not True and not callback_called:
            received(started)

    request_next(0)
    return dict(summary)
