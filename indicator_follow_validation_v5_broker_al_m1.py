"""Pure classification of broker-provided _AL M1 bars.

This is a distinct provenance type from independently tick-merged SOR M1.
The opt10080 receipt has rows but no independent proof that an absent minute
had no trades rather than a collection gap. Keep missing minutes unconfirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from indicator_follow_validation_v5_sor_source_gate import FrozenSession


_MINUTE = timedelta(minutes=1)
_KST = timedelta(hours=9)
_MAX_BARS = 1440


@dataclass(frozen=True, slots=True)
class BrokerProvidedAlM1Bar:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class BrokerAlM1Classification:
    classification: str
    reason: str
    request_id: str | None = None
    receipt_id: str | None = None
    bars: tuple[BrokerProvidedAlM1Bar, ...] = ()


@dataclass(frozen=True, slots=True)
class SegmentedBrokerAlM1Classification:
    classification: str
    reason: str
    segments: tuple[BrokerAlM1Classification, ...] = ()


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() == _KST else None


def _number(value: object, *, price: bool) -> Decimal | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", cleaned) is None:
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    # Kiwoom price signs encode direction; the existing candle projection uses magnitude.
    return abs(number) if price else number


def classify_broker_al_m1(
    started: object,
    receipt: object,
    session: FrozenSession,
) -> BrokerAlM1Classification:
    """Classify a complete broker envelope without inventing merge evidence."""
    request_id = started.get("rqname") if isinstance(started, dict) else None
    receipt_id = receipt.get("request_id") if isinstance(receipt, dict) else None

    def reject(reason: str) -> BrokerAlM1Classification:
        return BrokerAlM1Classification("REJECTED", reason, request_id, receipt_id)

    if not isinstance(started, dict) or not isinstance(receipt, dict):
        return reject("invalid request or receipt envelope")
    if not isinstance(session, FrozenSession):
        return reject("missing frozen session")
    if (type(session.connection_epoch) is not int or session.connection_epoch <= 0
            or not isinstance(session.login_session_id, str) or not session.login_session_id
            or not isinstance(session.stock_code, str) or len(session.stock_code) != 6
            or not session.stock_code.isascii() or not session.stock_code.isdigit()):
        return reject("invalid frozen session identity")
    identity = f"{session.stock_code}_AL"
    if (started.get("ok") is not True or receipt.get("ok") is not True
            or not isinstance(request_id, str) or not request_id
            or receipt_id != request_id or receipt.get("rqname") != request_id
            or re.fullmatch(rf"opt10080_{session.stock_code}_[0-9]{{12}}", request_id) is None
            or any(envelope.get("code") != session.stock_code
                   or envelope.get("market_data_identity") != identity
                   or envelope.get("market_source") != "INTEGRATED"
                   or envelope.get("display_only") is not True
                   for envelope in (started, receipt))
            or receipt.get("interval") != 1
            or receipt.get("type") != "minute_candles"
            or receipt.get("trcode") != "opt10080"):
        return reject("request or receipt _AL M1 identity mismatch")
    if (any(type(envelope.get("connection_epoch")) is not int
            or envelope.get("connection_epoch") != session.connection_epoch
            or envelope.get("login_session_id") != session.login_session_id
            for envelope in (started, receipt))):
        return reject("request or receipt epoch/login metadata missing or mismatched")

    started_at = _time(started.get("started_at"))
    as_of = _time(started.get("request_as_of"))
    received_at = _time(receipt.get("received_at"))
    if (started_at is None or as_of is None or received_at is None
            or as_of != session.as_of
            or not as_of <= started_at <= received_at
            or started_at - as_of > timedelta(seconds=30)
            or received_at - started_at > timedelta(minutes=5)):
        return reject("request/as-of/receipt timing metadata missing or invalid")
    if (not all(isinstance(value, datetime) and value.tzinfo is not None
                and value.utcoffset() == _KST
                for value in (session.window_start, session.window_end, session.as_of))
            or session.window_start.date() != session.window_end.date()
            or not session.window_start < session.window_end
            or session.window_start.hour < 8
            or session.window_end.time() > datetime.min.time().replace(hour=20)
            or any(value.second or value.microsecond
                   for value in (session.window_start, session.window_end))
            or session.window_end > as_of.replace(second=0, microsecond=0)
            or (session.window_end - session.window_start) / _MINUTE > _MAX_BARS):
        return reject("session or completed coverage bounds invalid")

    rows = receipt.get("rows")
    if (type(rows) is not list or not rows or len(rows) > _MAX_BARS
            or type(receipt.get("rows_count")) is not int
            or receipt["rows_count"] != len(rows)
            or receipt.get("has_more") is not False):
        return reject("rows missing, incomplete, or outside coverage limit")
    bars: list[BrokerProvidedAlM1Bar] = []
    seen: set[datetime] = set()
    for row in rows:
        if not isinstance(row, dict):
            return reject("malformed broker row")
        stamp = row.get("체결시간")
        if not isinstance(stamp, str) or len(stamp) != 14 or not stamp.isascii() or not stamp.isdigit():
            return reject("malformed M1 timestamp")
        try:
            start = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=session.as_of.tzinfo)
        except ValueError:
            return reject("malformed M1 timestamp")
        if start.second or start.microsecond or start in seen:
            return reject("duplicate or non-minute M1 timestamp")
        seen.add(start)
        values = tuple(_number(row.get(key), price=key != "거래량")
                       for key in ("시가", "고가", "저가", "현재가", "거래량"))
        if (any(value is None for value in values)
                or values[2] <= 0 or values[4] < 0
                or not values[2] <= values[0] <= values[1]
                or not values[2] <= values[3] <= values[1]):
            return reject("malformed OHLCV")
        bars.append(BrokerProvidedAlM1Bar(start, *values))
    bars.sort(key=lambda bar: bar.start)
    if (bars[0].start != session.window_start
            or bars[-1].start + _MINUTE != session.window_end
            or any(right.start - left.start != _MINUTE
                   for left, right in zip(bars, bars[1:]))):
        return reject("unproved gap or out-of-session bar")
    return BrokerAlM1Classification("BROKER_PROVIDED_AL_CONFIRMED_M1", "", request_id,
                                    receipt_id, tuple(bars))


def classify_broker_al_m1_segments(
    segments: object, *, expected_segment_count: object = None,
) -> SegmentedBrokerAlM1Classification:
    """Validate a planned number of receipts without inferring cross-session bars."""
    def reject(reason: str) -> SegmentedBrokerAlM1Classification:
        return SegmentedBrokerAlM1Classification("REJECTED", reason)

    if (type(expected_segment_count) is not int or not 1 <= expected_segment_count <= 5_000
            or type(segments) is not tuple or len(segments) != expected_segment_count):
        return reject("missing or unbounded segments")
    verified: list[BrokerAlM1Classification] = []
    first_session: FrozenSession | None = None
    previous_session: FrozenSession | None = None
    request_ids: set[str] = set()
    total_bars = 0
    for index, segment in enumerate(segments):
        if type(segment) is not tuple or len(segment) != 3:
            return reject(f"segment {index}: missing request, receipt, or frozen session")
        started, receipt, session = segment
        classified = classify_broker_al_m1(started, receipt, session)
        if classified.classification != "BROKER_PROVIDED_AL_CONFIRMED_M1":
            return reject(f"segment {index}: {classified.reason}")
        if first_session is None:
            first_session = session
        elif (session.connection_epoch != first_session.connection_epoch
              or session.login_session_id != first_session.login_session_id
              or session.stock_code != first_session.stock_code):
            return reject(f"segment {index}: frozen session identity changed")
        if classified.request_id in request_ids:
            return reject(f"segment {index}: duplicate request receipt")
        if previous_session is not None:
            if (session.window_start < previous_session.window_end
                    or (session.window_start.date() == previous_session.window_end.date()
                        and session.window_start != previous_session.window_end)):
                return reject(f"segment {index}: overlapping or unproved same-day coverage")
        request_ids.add(classified.request_id)
        total_bars += len(classified.bars)
        if total_bars > 5_000:
            return reject("segmented history exceeds 5000 bars")
        verified.append(classified)
        previous_session = session
    return SegmentedBrokerAlM1Classification("BROKER_PROVIDED_AL_CONFIRMED_M1_SEGMENTS", "",
                                               tuple(verified))
