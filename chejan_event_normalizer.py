# -*- coding: utf-8 -*-
"""Pure Kiwoom Chejan event normalizer.

This module converts an in-memory raw Chejan event dictionary into an internal
standard event dictionary. It never reads/writes runtime files, creates fills
or positions, connects Kiwoom event handlers, or calls SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import re
from typing import Any
from zoneinfo import ZoneInfo


STAGE_NORMALIZED = "chejan_event_normalized"
STAGE_BLOCKED = "chejan_event_blocked"

_BUY_TOKENS = ("BUY", "매수")
_SELL_TOKENS = ("SELL", "매도")
_REJECT_TOKENS = ("거부", "오류", "실패", "REJECT", "ERROR", "FAILED")
_CANCEL_TOKENS = ("취소", "CANCEL")
_ACCEPT_TOKENS = ("접수", "확인", "ACCEPT", "CONFIRM", "OPEN")
_FILL_TOKENS = ("체결", "FILLED", "FILL")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _upper(value: Any) -> str:
    return _clean_text(value).upper()


def _contains_any(value: Any, tokens: tuple[str, ...]) -> bool:
    text = _clean_text(value)
    upper_text = text.upper()
    return any(token in text or token in upper_text for token in tokens)


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "normalized": False,
        "event_stage": STAGE_BLOCKED,
        "event_type": "ORDER_UNKNOWN",
        "broker": "KIWOOM",
        "unresolved": True,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _fid(fid_values: dict[str, Any], key: str) -> Any:
    return fid_values.get(key)


def _normalize_code(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text.upper().startswith("A"):
        return text[1:].strip()
    return text


def _parse_int(value: Any, field_name: str, warnings: list[str]) -> int | None:
    text = _clean_text(value)
    if text in {"", "-", "--"}:
        return None
    try:
        return int(float(text.replace(",", "")))
    except (TypeError, ValueError):
        warnings.append(f"{field_name} could not be parsed as int")
        return None


def _side(value: Any, warnings: list[str]) -> tuple[str | None, bool]:
    text = _clean_text(value)
    if text == "2" or _contains_any(text, _BUY_TOKENS):
        return "BUY", False
    if text == "1" or _contains_any(text, _SELL_TOKENS):
        return "SELL", False
    warnings.append("side is unclear")
    return None, True


def _event_type(order_status: str, filled_quantity: int | None, remaining_quantity: int | None) -> tuple[str, bool]:
    filled = filled_quantity if filled_quantity is not None else 0
    remaining = remaining_quantity if remaining_quantity is not None else 0

    if _contains_any(order_status, _REJECT_TOKENS):
        return "ORDER_REJECTED", False
    if _contains_any(order_status, _CANCEL_TOKENS):
        return "ORDER_CANCELED", False
    if filled > 0 and remaining > 0:
        return "PARTIAL_FILL", False
    if filled > 0 and remaining == 0:
        return "FULL_FILL", False
    if _contains_any(order_status, _ACCEPT_TOKENS) and filled == 0 and remaining > 0:
        return "ORDER_OPEN", False
    if _contains_any(order_status, _FILL_TOKENS) and filled == 0 and remaining > 0:
        return "ORDER_OPEN", False
    return "ORDER_UNKNOWN", True


def _seoul_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed.astimezone(ZoneInfo("Asia/Seoul"))


def _trade_date(value: Any) -> date | None:
    text = _clean_text(value)
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def resolve_execution_time(
    *,
    event_type: Any,
    broker_execution_time_raw: Any,
    received_at: Any,
    execution_trade_date: Any = None,
) -> dict[str, Any]:
    """Resolve Broker time only with an explicit, non-contradictory trade date."""
    raw = _clean_text(broker_execution_time_raw)
    received = _seoul_datetime(received_at)
    trade_day = _trade_date(execution_trade_date)
    fill_event = _clean_text(event_type) in {"PARTIAL_FILL", "FULL_FILL"}
    broker_clock_valid = bool(re.fullmatch(r"\d{6}(?:\d{2})?", raw))
    hour = minute = second = -1
    if broker_clock_valid:
        hour, minute, second = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
        broker_clock_valid = hour < 24 and minute < 60 and second < 60

    if (
        fill_event
        and broker_clock_valid
        and trade_day is not None
        and (received is None or received.date() == trade_day)
    ):
        broker_datetime = datetime(
            trade_day.year,
            trade_day.month,
            trade_day.day,
            hour,
            minute,
            second,
            tzinfo=ZoneInfo("Asia/Seoul"),
        )
        return {
            "broker_execution_time_raw": raw,
            "broker_execution_datetime": broker_datetime.isoformat(),
            "execution_time_source": "BROKER_FID_908",
            "execution_time_quality": "EXACT",
        }
    if received is not None:
        return {
            "broker_execution_time_raw": raw,
            "broker_execution_datetime": None,
            "execution_time_source": "LOCAL_RECEIVED_AT",
            "execution_time_quality": "APPROXIMATE",
        }
    return {
        "broker_execution_time_raw": raw,
        "broker_execution_datetime": None,
        "execution_time_source": "NONE",
        "execution_time_quality": "UNAVAILABLE",
    }


def normalize_kiwoom_chejan_event(raw_event: Any, context: Any = None) -> dict[str, Any]:
    """Normalize one raw Chejan event into an internal standard event dict."""
    if not isinstance(raw_event, dict):
        return _blocked("raw_event must be a dict")

    source = _clean_text(raw_event.get("source"))
    if not source:
        return _blocked("raw_event.source is required")

    gubun = _clean_text(raw_event.get("gubun"))
    if gubun != "0":
        return _blocked("raw_event.gubun must be 0 for an order Chejan event")

    received_at = _clean_text(raw_event.get("received_at"))
    if not received_at:
        return _blocked("raw_event.received_at is required")

    fid_values = raw_event.get("fid_values")
    if not isinstance(fid_values, dict):
        return _blocked("raw_event.fid_values must be a dict")

    warnings: list[str] = []
    account_no = _clean_text(_fid(fid_values, "9201")) or None
    broker_order_no = _clean_text(_fid(fid_values, "9203")) or None
    original_order_no = _clean_text(_fid(fid_values, "904")) or None
    code = _normalize_code(_fid(fid_values, "9001"))
    name = _clean_text(_fid(fid_values, "302")) or None
    order_status = _clean_text(_fid(fid_values, "913")) or None
    side, side_unresolved = _side(_fid(fid_values, "907"), warnings)

    order_quantity = _parse_int(_fid(fid_values, "900"), "order_quantity", warnings)
    filled_quantity = _parse_int(_fid(fid_values, "911"), "filled_quantity", warnings)
    remaining_quantity = _parse_int(_fid(fid_values, "902"), "remaining_quantity", warnings)
    filled_price = _parse_int(_fid(fid_values, "910"), "filled_price", warnings)
    order_price = _parse_int(_fid(fid_values, "901"), "order_price", warnings)

    event_type, event_unresolved = _event_type(order_status or "", filled_quantity, remaining_quantity)
    fid_raw_values = raw_event.get("fid_raw_values")
    if not isinstance(fid_raw_values, dict):
        fid_raw_values = fid_values
    broker_execution_time_raw = _clean_text(_fid(fid_raw_values, "908"))
    broker_execution_no = _clean_text(_fid(fid_values, "909")) or None
    ctx = context if isinstance(context, dict) else {}
    time_evidence = resolve_execution_time(
        event_type=event_type,
        broker_execution_time_raw=broker_execution_time_raw,
        received_at=received_at,
        execution_trade_date=ctx.get("execution_trade_date"),
    )
    if broker_execution_time_raw and not re.fullmatch(r"\d{6}(?:\d{2})?", broker_execution_time_raw):
        warnings.append("broker_execution_time_raw could not be parsed")
    parse_unresolved = any("could not be parsed" in warning for warning in warnings)
    unresolved = bool(side_unresolved or event_unresolved or parse_unresolved)

    return {
        "normalized": True,
        "event_stage": STAGE_NORMALIZED,
        "event_type": event_type,
        "broker": "KIWOOM",
        "source": source,
        "gubun": gubun,
        "received_at": received_at,
        "broker_order_no": broker_order_no,
        "original_order_no": original_order_no,
        "account_no": account_no,
        "code": code,
        "name": name,
        "side": side,
        "order_status": order_status,
        "order_quantity": order_quantity,
        "filled_quantity": filled_quantity,
        "remaining_quantity": remaining_quantity,
        "order_price": order_price,
        "filled_price": filled_price,
        "broker_execution_no": broker_execution_no,
        **time_evidence,
        "request_hash": None,
        "lock_id": None,
        "execution_id": None,
        "unresolved": unresolved,
        "blocked_reasons": [],
        "warnings": warnings,
        "raw_event": deepcopy(raw_event),
    }
