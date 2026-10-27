"""Pure V5 calculation tail from one authoritative, confirmed source.

Coverage intervals state exactly which source-time spans were active. Omitted
spans are trusted session gaps; every stated active span must be covered by
confirmed components. This module acquires no data and changes no history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from numbers import Real
from typing import Sequence


_MINUTES = {1, 3, 5, 10, 15, 30, 60, 120, 240}
_ALLOWED_SOURCE = {
    "D1": {"M1", "D1"},
    "W1": {"D1", "M1"},
    "MO1": {"D1", "MO1", "M1"},
    "Y1": {"D1", "MO1", "M1"},
}
_KST_OFFSET = timedelta(hours=9)
_MAX_SEGMENTS = 1440


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    provider: str
    market: str
    instrument: str


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    start: datetime
    end: datetime
    source_identity: SourceIdentity


@dataclass(frozen=True, slots=True)
class ConfirmedComponent:
    start: datetime
    end: datetime
    granularity: str
    source_identity: SourceIdentity
    open: Real
    high: Real
    low: Real
    close: Real
    volume: Real


@dataclass(frozen=True, slots=True)
class CalculationTailBar:
    timeframe_key: str
    start: datetime
    end: datetime
    source_identity: SourceIdentity
    open: Real
    high: Real
    low: Real
    close: Real
    volume: Real
    calculation_complete: bool
    raw_complete: bool


def _kst_time(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware KST datetime")
    if value.utcoffset() != _KST_OFFSET:
        raise ValueError(f"{name} must use KST (+09:00)")
    return value


def _interval(start: object, end: object, name: str) -> None:
    start = _kst_time(start, f"{name}.start")
    end = _kst_time(end, f"{name}.end")
    if start.second or start.microsecond or end.second or end.microsecond or start >= end:
        raise ValueError(f"{name} requires ordered whole-minute bounds")


def _next_month(start: datetime) -> datetime:
    return start.replace(year=start.year + (start.month == 12),
                         month=start.month % 12 + 1)


def _bucket_end(key: str, start: datetime, anchor: datetime) -> datetime:
    if key.startswith("M") and key != "MO1":
        try:
            minutes = int(key[1:])
        except ValueError as exc:
            raise ValueError("unsupported timeframe key") from exc
        if minutes not in _MINUTES or key != f"M{minutes}":
            raise ValueError("unsupported timeframe key")
        offset = (start - anchor).total_seconds() / 60
        if offset < 0 or offset % minutes or start.second or start.microsecond:
            raise ValueError("noncanonical minute bucket start")
        return start + timedelta(minutes=minutes)
    if key not in _ALLOWED_SOURCE:
        raise ValueError("unsupported timeframe key")
    if start.hour or start.minute or start.second or start.microsecond:
        raise ValueError("noncanonical calendar bucket start")
    if key == "D1":
        return start + timedelta(days=1)
    if key == "W1":
        if start.weekday() != 0:
            raise ValueError("noncanonical week bucket start")
        return start + timedelta(days=7)
    if key == "MO1":
        if start.day != 1:
            raise ValueError("noncanonical month bucket start")
        return _next_month(start)
    if start.month != 1 or start.day != 1:
        raise ValueError("noncanonical year bucket start")
    return start.replace(year=start.year + 1)


def _current_and_previous_start(
        key: str, as_of: datetime, anchor: datetime) -> tuple[datetime, datetime]:
    if key.startswith("M") and key != "MO1":
        minutes = int(key[1:])
        elapsed = (as_of - anchor) // timedelta(minutes=minutes)
        current = anchor + elapsed * timedelta(minutes=minutes)
        return current, current - timedelta(minutes=minutes)
    midnight = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "D1":
        return midnight, midnight - timedelta(days=1)
    if key == "W1":
        current = midnight - timedelta(days=midnight.weekday())
        return current, current - timedelta(days=7)
    if key == "MO1":
        current = midnight.replace(day=1)
        return current, (current - timedelta(days=1)).replace(day=1)
    current = midnight.replace(month=1, day=1)
    return current, current.replace(year=current.year - 1)


def _source_end(start: datetime, granularity: str) -> datetime:
    if granularity == "M1":
        return start + timedelta(minutes=1)
    if granularity == "D1" and not any((start.hour, start.minute, start.second,
                                          start.microsecond)):
        return start + timedelta(days=1)
    if granularity == "MO1" and start.day == 1 and not any((
            start.hour, start.minute, start.second, start.microsecond)):
        return _next_month(start)
    raise ValueError("invalid source granularity or bounds")


def _merged(spans: Sequence[tuple[datetime, datetime]], name: str) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            raise ValueError(f"{name} overlap or duplicate")
        if merged and start == merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _check_ohlcv(row: ConfirmedComponent) -> None:
    values = (row.open, row.high, row.low, row.close, row.volume)
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("malformed OHLCV values")
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("malformed OHLCV values") from exc
        if not finite:
            raise ValueError("malformed OHLCV values")
    if (row.low <= 0 or row.high < row.low or
            not row.low <= row.open <= row.high or
            not row.low <= row.close <= row.high or row.volume < 0):
        raise ValueError("malformed OHLCV ordering or magnitude")


def build_v5_sor_calculation_tail(
    timeframe_key: str,
    as_of: datetime,
    bucket_start: datetime,
    source_identity: SourceIdentity,
    coverage: Sequence[CoverageInterval],
    components: Sequence[ConfirmedComponent],
    *,
    raw_complete: bool,
    bucket_anchor: datetime | None = None,
) -> CalculationTailBar:
    """Return one latest bar; reject every unproved active span.

    ``as_of`` is frozen before the call. A minute beginning in its current
    minute cannot be confirmed. Coverage carries known closures by omission;
    no outside broker comparison or raw-completion inference occurs here.
    Minute keys use the supplied trusted bucket anchor, defaulting to 09:00.
    """
    as_of = _kst_time(as_of, "as_of")
    bucket_start = _kst_time(bucket_start, "bucket_start")
    if not isinstance(source_identity, SourceIdentity) or any(
            not isinstance(value, str) or not value.strip()
            for value in (source_identity.provider, source_identity.market,
                          source_identity.instrument)):
        raise ValueError("invalid source identity")
    if type(raw_complete) is not bool:
        raise ValueError("raw_complete must be a bool")
    if timeframe_key.startswith("M") and timeframe_key != "MO1":
        if bucket_anchor is None:
            bucket_anchor = as_of.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            bucket_anchor = _kst_time(bucket_anchor, "bucket_anchor")
            if (bucket_anchor.date() != as_of.date() or bucket_anchor.second or
                    bucket_anchor.microsecond or bucket_anchor > as_of):
                raise ValueError("invalid minute bucket anchor")
    elif bucket_anchor is not None:
        raise ValueError("bucket anchor is only for minute keys")
    bucket_end = _bucket_end(timeframe_key, bucket_start, bucket_anchor)
    current_start, previous_start = _current_and_previous_start(
        timeframe_key, as_of, bucket_anchor)
    if bucket_start not in (current_start, previous_start):
        raise ValueError("target bucket is not current or latest")
    if (not isinstance(coverage, (list, tuple)) or
            not isinstance(components, (list, tuple)) or
            not coverage or not components or
            len(coverage) > _MAX_SEGMENTS or len(components) > _MAX_SEGMENTS):
        raise ValueError("coverage and components must be bounded nonempty sequences")

    cutoff = as_of.replace(second=0, microsecond=0)
    allowed = {"M1"} if timeframe_key.startswith("M") and timeframe_key != "MO1" else _ALLOWED_SOURCE[timeframe_key]
    component_spans: list[tuple[datetime, datetime]] = []
    for row in components:
        if not isinstance(row, ConfirmedComponent):
            raise ValueError("invalid confirmed component")
        if row.source_identity != source_identity:
            raise ValueError("component source identity mismatch")
        _interval(row.start, row.end, "component")
        if row.granularity not in allowed or row.end != _source_end(row.start, row.granularity):
            raise ValueError("invalid source granularity or bounds")
        if timeframe_key in ("W1", "MO1", "Y1") and row.granularity == "M1" and row.start.date() != as_of.date():
            raise ValueError("calendar tail M1 must be from today")
        if row.start < bucket_start or row.end > bucket_end:
            raise ValueError("component outside target bucket")
        if row.end > cutoff:
            raise ValueError("forming component at as_of minute")
        _check_ohlcv(row)
        component_spans.append((row.start, row.end))
    combined_components = _merged(component_spans, "component")

    coverage_spans: list[tuple[datetime, datetime]] = []
    for interval in coverage:
        if not isinstance(interval, CoverageInterval):
            raise ValueError("invalid coverage interval")
        if interval.source_identity != source_identity:
            raise ValueError("coverage source identity mismatch")
        _interval(interval.start, interval.end, "coverage")
        if interval.start < bucket_start or interval.end > bucket_end:
            raise ValueError("coverage outside target bucket")
        if interval.end > cutoff:
            raise ValueError("coverage extends into forming as_of minute")
        coverage_spans.append((interval.start, interval.end))
    if combined_components != _merged(coverage_spans, "coverage"):
        raise ValueError("unproven gap or mismatch in active coverage")

    ordered = sorted(components, key=lambda row: row.start)
    return CalculationTailBar(
        timeframe_key, bucket_start, ordered[-1].end, source_identity,
        ordered[0].open, max(row.high for row in ordered),
        min(row.low for row in ordered), ordered[-1].close,
        sum(row.volume for row in ordered), True, raw_complete,
    )
