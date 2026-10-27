"""Read-only confirmed-M1 input for a common SOR calculation source.

The caller supplies independently verified merge and active-time evidence.
Historical display caches cannot supply that evidence. Nothing here acquires
data, infers SOR from KRX/NXT bars, or publishes to production paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from numbers import Real

from indicator_follow_validation_v5_sor_source_gate import (
    FrozenSession, SourceEvent, SourceMetadata, VerifiedSorMerge,
    validate_v5_sor_source_batch,
)
from indicator_follow_validation_v5_sor_tail import (
    ConfirmedComponent, CoverageInterval, SourceIdentity,
)


_MINUTE = timedelta(minutes=1)
_MAX_MINUTES = 1440


@dataclass(frozen=True, slots=True)
class ConfirmedSorM1:
    component: ConfirmedComponent
    connection_epoch: int
    login_session_id: str
    stock_code: str
    output_sequence: int
    confirmed: bool
    confirmation_evidence: str


@dataclass(frozen=True, slots=True)
class SorM1Supply:
    session: FrozenSession
    source_identity: SourceIdentity
    coverage: tuple[CoverageInterval, ...]
    components: tuple[ConfirmedComponent, ...]
    merge_verification_id: str


def supply_confirmed_sor_m1(
    session: FrozenSession,
    sources: tuple[SourceMetadata, ...],
    events: tuple[SourceEvent, ...],
    merge: VerifiedSorMerge | None,
    coverage: tuple[CoverageInterval, ...],
    bars: tuple[ConfirmedSorM1, ...],
) -> SorM1Supply | None:
    """Return a source-scoped batch only when every asserted active minute is proved."""
    outcome = validate_v5_sor_source_batch(session, sources, events, merge)
    if (not outcome.eligible_for_integrated_calculation
            or not isinstance(merge, VerifiedSorMerge)
            or type(merge.connection_epoch) is not int
            or any(type(row.connection_epoch) is not int for row in (*sources, *events))
            or type(coverage) is not tuple or type(bars) is not tuple
            or not coverage or not bars
            or len(coverage) > _MAX_MINUTES or len(bars) > _MAX_MINUTES):
        return None

    source = outcome.source_identity
    active: set[datetime] = set()
    for span in coverage:
        if (not isinstance(span, CoverageInterval)
                or span.source_identity != source
                or not isinstance(span.start, datetime)
                or not isinstance(span.end, datetime)
                or span.start.tzinfo is None or span.end.tzinfo is None
                or span.start.utcoffset() != timedelta(hours=9)
                or span.end.utcoffset() != timedelta(hours=9)
                or span.start.second or span.start.microsecond
                or span.end.second or span.end.microsecond
                or not session.window_start <= span.start < span.end <= session.window_end
                or span.end > session.as_of.replace(second=0, microsecond=0)):
            return None
        minutes = (span.end - span.start) / _MINUTE
        if not minutes.is_integer() or len(active) + minutes > _MAX_MINUTES:
            return None
        for offset in range(int(minutes)):
            minute = span.start + offset * _MINUTE
            if minute in active:
                return None
            active.add(minute)

    by_minute: dict[datetime, list[SourceEvent]] = {}
    for event in events:
        minute = event.occurred_at.replace(second=0, microsecond=0)
        if minute not in active:
            return None
        by_minute.setdefault(minute, []).append(event)

    components: list[ConfirmedComponent] = []
    seen: set[datetime] = set()
    for bar in bars:
        if not isinstance(bar, ConfirmedSorM1) or not isinstance(bar.component, ConfirmedComponent):
            return None
        row = bar.component
        values = (row.open, row.high, row.low, row.close, row.volume)
        try:
            valid_ohlcv = (all(not isinstance(value, bool) and isinstance(value, Real)
                               and math.isfinite(value) for value in values)
                           and row.low > 0 and row.low <= row.open <= row.high
                           and row.low <= row.close <= row.high and row.volume >= 0)
        except (OverflowError, TypeError, ValueError):
            return None
        if (row.source_identity != source or row.granularity != "M1"
                or not isinstance(row.start, datetime)
                or not isinstance(row.end, datetime)
                or row.start not in active or row.start in seen
                or row.end != row.start + _MINUTE
                or type(bar.connection_epoch) is not int
                or bar.connection_epoch != session.connection_epoch
                or bar.login_session_id != session.login_session_id
                or bar.stock_code != session.stock_code
                or bar.confirmed is not True
                or not isinstance(bar.confirmation_evidence, str)
                or not bar.confirmation_evidence.strip()
                or type(bar.output_sequence) is not int
                or not by_minute.get(row.start)
                or bar.output_sequence != by_minute[row.start][-1].sequence
                or not valid_ohlcv):
            return None
        seen.add(row.start)
        components.append(row)
    if seen != active:
        return None
    return SorM1Supply(session, source, coverage, tuple(sorted(components, key=lambda row: row.start)),
                       merge.verification_id)
