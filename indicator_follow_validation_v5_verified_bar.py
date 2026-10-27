"""V5-only read-only boundary for a caller-verified forming SOR bar.

This does not acquire ticks or verify the independent merge evidence. The
caller must provide real coverage and merge proof; no GUI or signal path calls
this boundary until that proof and an authoritative bar producer exist.
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
from indicator_follow_validation_v5_sor_tail import SourceIdentity


_KST = timedelta(hours=9)
_MAX_AGE = timedelta(seconds=30)
_TIMEFRAMES = {"M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240",
               "D1", "W1", "MO1", "Y1"}


@dataclass(frozen=True, slots=True)
class CandidateCurrentBar:
    timeframe_key: str
    start: datetime
    end: datetime
    source_identity: SourceIdentity
    connection_epoch: int
    login_session_id: str
    stock_code: str
    observed_at: datetime
    sequence: int
    open: Real
    high: Real
    low: Real
    close: Real
    volume: Real


def _kst(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == _KST


def _ohlcv_valid(bar: CandidateCurrentBar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    try:
        return (all(not isinstance(value, bool) and isinstance(value, Real)
                    and math.isfinite(value) for value in values)
                and bar.low > 0 and bar.low <= bar.open <= bar.high
                and bar.low <= bar.close <= bar.high and bar.volume >= 0)
    except (OverflowError, TypeError, ValueError):
        return False


def publish_verified_current_bar(
    candidate: CandidateCurrentBar,
    previous: CandidateCurrentBar | None,
    session: FrozenSession,
    sources: tuple[SourceMetadata, ...],
    events: tuple[SourceEvent, ...],
    merge: VerifiedSorMerge | None,
    *,
    now: datetime,
) -> CandidateCurrentBar | None:
    """Return one publishable bar or None; never promote a raw market stream."""
    outcome = validate_v5_sor_source_batch(session, sources, events, merge)
    if not outcome.eligible_for_integrated_calculation:
        return None
    if not isinstance(candidate, CandidateCurrentBar) or not _kst(now):
        return None
    latest = events[-1]  # An eligible gate outcome requires a nonempty event tuple.
    if (not isinstance(candidate.timeframe_key, str)
            or candidate.timeframe_key not in _TIMEFRAMES
            or not all(_kst(value) for value in (
                candidate.start, candidate.end, candidate.observed_at))
            or not candidate.start <= candidate.observed_at <= now < candidate.end
            or now - candidate.observed_at > _MAX_AGE
            or candidate.observed_at != latest.occurred_at
            or type(candidate.sequence) is not int
            or candidate.sequence != latest.sequence
            or candidate.source_identity != outcome.source_identity
            or candidate.connection_epoch != session.connection_epoch
            or candidate.login_session_id != session.login_session_id
            or candidate.stock_code != session.stock_code
            or not _ohlcv_valid(candidate)):
        return None
    if previous is not None:
        if (not isinstance(previous, CandidateCurrentBar)
                or (previous.timeframe_key, previous.source_identity,
                    previous.connection_epoch, previous.login_session_id,
                    previous.stock_code) !=
                   (candidate.timeframe_key, candidate.source_identity,
                    candidate.connection_epoch, candidate.login_session_id,
                    candidate.stock_code)
                or candidate.sequence <= previous.sequence
                or candidate.observed_at <= previous.observed_at
                or candidate.start < previous.start
                or (candidate.start == previous.start
                    and candidate.end != previous.end)
                or (candidate.start > previous.start
                    and candidate.start < previous.end)):
            return None
    return candidate
