# -*- coding: utf-8 -*-
"""Pure helpers for converting Kiwoom opt10080 rows into candles.json data.

This module does not create a QAxWidget, call Kiwoom OpenAPI, register realtime
feeds, or send orders. It only normalizes already-received TR rows and stores
them through the existing candle_manager helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from candle_manager import (
    DEFAULT_CANDLES_MAX_COUNT,
    candle_commit_lock,
    commit_candles,
    load_candles,
)
from candle_timeframe_aggregation import candle_market_datetime
from stock_repository import StockRepository


BAR_COMMITTED = "BAR_COMMITTED"
OPT10080_SOURCE = "opt10080"
MINUTE_TIMEFRAME = 1


@dataclass(frozen=True)
class BarCommittedNotification:
    event_type: str
    stock_code: str
    stock_name: str
    timeframe_minutes: int
    trade_date: str
    bar_time: str
    bar_key: str
    bar_identity: str
    commit_identity: str
    canonical_content_hash: str
    canonical_path: str
    saved_count: int
    source: str
    rqname: str = ""
    trcode: str = ""
    connection_epoch: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class CanonicalMinuteCandleCommitResult:
    ok: bool
    changed: bool
    readback_verified: bool
    path: str
    saved_count: int
    canonical_content_hash: str
    commit_identity: str = ""
    bar_key: str = ""
    bar_identity: str = ""
    bar_time: str = ""
    trade_date: str = ""
    error_kind: str = ""
    error: str = ""
    notification: BarCommittedNotification | None = None


def _identity_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bar_commit_identity(
    stock_code: str,
    candle: dict[str, Any],
    canonical_content_hash: str,
) -> tuple[str, str, str, str, str]:
    parsed_time = candle_market_datetime(candle)
    if parsed_time is None:
        raise ValueError("latest canonical candle time is invalid")
    bar_time = parsed_time.isoformat(timespec="seconds")
    bar_key = f"{stock_code}:{MINUTE_TIMEFRAME}:{bar_time}"
    bar_identity = _identity_hash(
        {
            "stock_code": stock_code,
            "timeframe_minutes": MINUTE_TIMEFRAME,
            "bar_time": bar_time,
            "open": candle.get("open"),
            "high": candle.get("high"),
            "low": candle.get("low"),
            "close": candle.get("close"),
            "volume": candle.get("volume"),
        }
    )
    commit_identity = _identity_hash(
        {
            "stock_code": stock_code,
            "timeframe_minutes": MINUTE_TIMEFRAME,
            "canonical_content_hash": canonical_content_hash,
        }
    )
    return (
        parsed_time.date().isoformat(),
        bar_time,
        bar_key,
        bar_identity,
        commit_identity,
    )


def normalize_kiwoom_price(value: Any) -> float | None:
    """Normalize Kiwoom numeric text such as ' -1,234 ' into a positive float."""
    if value is None:
        return None

    text = str(value).strip().replace(",", "")
    if not text:
        return None

    if text[0] in {"+", "-"}:
        text = text[1:].strip()
    if not text:
        return None

    try:
        return abs(float(text))
    except (TypeError, ValueError):
        return None


def normalize_opt10080_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one opt10080 row to the candle_manager candle shape.

    Raises:
        ValueError: when row is not a dict or close/current price is missing.
    """
    if not isinstance(row, dict):
        raise ValueError("opt10080 row must be a dict")

    close = normalize_kiwoom_price(row.get("현재가"))
    if close is None:
        raise ValueError("opt10080 row missing close/current price")

    return {
        "time": str(row.get("체결시간", "") or "").strip(),
        "open": normalize_kiwoom_price(row.get("시가")),
        "high": normalize_kiwoom_price(row.get("고가")),
        "low": normalize_kiwoom_price(row.get("저가")),
        "close": close,
        "volume": normalize_kiwoom_price(row.get("거래량")),
    }


def normalize_opt10080_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize opt10080 rows, skipping invalid rows.

    Policy:
    - Invalid rows are skipped.
    - Candles are sorted by time ascending.
    - Duplicate time values keep the last valid row from the input sequence.
    """
    if not isinstance(rows, list):
        return []

    candles_by_time: dict[str, dict[str, Any]] = {}
    untimed_candles: list[dict[str, Any]] = []

    for row in rows:
        try:
            candle = normalize_opt10080_row(row)
        except ValueError:
            continue

        time_key = str(candle.get("time", "") or "").strip()
        if time_key:
            candles_by_time[time_key] = candle
        else:
            untimed_candles.append(candle)

    timed_candles = [candles_by_time[key] for key in sorted(candles_by_time)]
    return untimed_candles + timed_candles


def commit_minute_candles_for_stock(
    code: str,
    name: str,
    rows: list[dict[str, Any]],
    max_count: int = DEFAULT_CANDLES_MAX_COUNT,
    *,
    rqname: str = "",
    trcode: str = "opt10080",
    connection_epoch: int = 0,
) -> CanonicalMinuteCandleCommitResult:
    """Merge opt10080 rows and return a verified canonical commit result."""
    stock_code = str(code or "").strip()
    stock_name = str(name or "").strip()
    stock_dir = StockRepository().resolve_stock_dir(stock_code, stock_name)
    incoming = normalize_opt10080_rows(rows)
    if not incoming:
        return CanonicalMinuteCandleCommitResult(
            ok=False,
            changed=False,
            readback_verified=False,
            path=str(stock_dir / "candles.json"),
            saved_count=0,
            canonical_content_hash="",
            error_kind="NO_VALID_CANDLES",
            error="opt10080 response contains no valid candles",
        )

    incoming_times = [
        bar_time
        for candle in incoming
        if (bar_time := candle_market_datetime(candle)) is not None
    ]
    if not incoming_times:
        return CanonicalMinuteCandleCommitResult(
            ok=False,
            changed=False,
            readback_verified=False,
            path=str(stock_dir / "candles.json"),
            saved_count=0,
            canonical_content_hash="",
            error_kind="NO_VALID_CANDLE_TIME",
            error="opt10080 response contains no valid candle time",
        )
    target_date = max(incoming_times).date()

    with candle_commit_lock(stock_dir):
        merged_by_minute: dict[Any, dict[str, Any]] = {}
        for candle in load_candles(stock_dir) + incoming:
            bar_time = candle_market_datetime(candle)
            if bar_time is None or bar_time.date() != target_date:
                continue
            merged_by_minute[bar_time] = candle
        merged = [merged_by_minute[key] for key in sorted(merged_by_minute)]
        commit = commit_candles(stock_dir, merged, max_count=max_count)
        saved = load_candles(stock_dir) if commit.ok and commit.readback_verified else []

    if not commit.ok or not commit.readback_verified:
        return CanonicalMinuteCandleCommitResult(
            ok=False,
            changed=commit.changed,
            readback_verified=commit.readback_verified,
            path=commit.path,
            saved_count=commit.saved_count,
            canonical_content_hash=commit.canonical_content_hash,
            error_kind=commit.error_kind,
            error=commit.error,
        )

    try:
        trade_date, bar_time, bar_key, bar_identity, commit_identity = _bar_commit_identity(
            stock_code,
            saved[-1],
            commit.canonical_content_hash,
        )
    except Exception as exc:
        return CanonicalMinuteCandleCommitResult(
            ok=False,
            changed=commit.changed,
            readback_verified=True,
            path=commit.path,
            saved_count=commit.saved_count,
            canonical_content_hash=commit.canonical_content_hash,
            error_kind="CANDLE_IDENTITY_FAILED",
            error=str(exc),
        )

    notification = None
    if commit.changed:
        notification = BarCommittedNotification(
            event_type=BAR_COMMITTED,
            stock_code=stock_code,
            stock_name=stock_name,
            timeframe_minutes=MINUTE_TIMEFRAME,
            trade_date=trade_date,
            bar_time=bar_time,
            bar_key=bar_key,
            bar_identity=bar_identity,
            commit_identity=commit_identity,
            canonical_content_hash=commit.canonical_content_hash,
            canonical_path=commit.path,
            saved_count=commit.saved_count,
            source=OPT10080_SOURCE,
            rqname=str(rqname or ""),
            trcode=str(trcode or "opt10080"),
            connection_epoch=int(connection_epoch or 0),
        )
    return CanonicalMinuteCandleCommitResult(
        ok=True,
        changed=commit.changed,
        readback_verified=True,
        path=commit.path,
        saved_count=commit.saved_count,
        canonical_content_hash=commit.canonical_content_hash,
        commit_identity=commit_identity,
        bar_key=bar_key,
        bar_identity=bar_identity,
        bar_time=bar_time,
        trade_date=trade_date,
        notification=notification,
    )


def save_minute_candles_for_stock(
    code: str,
    name: str,
    rows: list[dict[str, Any]],
    max_count: int = DEFAULT_CANDLES_MAX_COUNT,
) -> list[dict[str, Any]]:
    """Compatibility helper preserving the existing saved-list contract."""
    stock_dir = StockRepository().resolve_stock_dir(code, name)
    result = commit_minute_candles_for_stock(
        code,
        name,
        rows,
        max_count=max_count,
    )
    if result.error_kind in {"NO_VALID_CANDLES", "NO_VALID_CANDLE_TIME"}:
        return load_candles(stock_dir)
    if not result.ok or not result.readback_verified:
        raise OSError(result.error or result.error_kind or "candle commit failed")
    return load_candles(stock_dir)
