"""Pure input provenance gate for a future V5 SOR calculation adapter.

The caller must supply a frozen session and independently checked merge evidence.
Evidence identifiers are assertions, not broker verification performed here. This
module reads no live state, merges no ticks, and does not calculate OHLCV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from indicator_follow_validation_v5_sor_tail import SourceIdentity


_KST = timedelta(hours=9)
_ROLES = {"KRX": "KRX_PRODUCTION", "NXT": "NXT_DISPLAY_ONLY",
          "INTEGRATED": "INTEGRATED_CALCULATION", "SOR": "INTEGRATED_CALCULATION"}


@dataclass(frozen=True, slots=True)
class FrozenSession:
    connection_epoch: int
    login_session_id: str
    stock_code: str
    window_start: datetime
    window_end: datetime
    as_of: datetime


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    identity: SourceIdentity
    role: str
    connection_epoch: int
    login_session_id: str
    stock_code: str
    coverage_start: datetime
    coverage_end: datetime
    coverage_evidence: str
    data_quality: str


@dataclass(frozen=True, slots=True)
class SourceEvent:
    identity: SourceIdentity
    connection_epoch: int
    login_session_id: str
    stock_code: str
    occurred_at: datetime
    sequence: int


@dataclass(frozen=True, slots=True)
class VerifiedSorMerge:
    output_source: SourceIdentity
    contributors: tuple[SourceIdentity, SourceIdentity]
    connection_epoch: int
    login_session_id: str
    stock_code: str
    verified_by: str
    verification_id: str
    dedup_evidence: str
    sequence_evidence: str
    coverage_evidence: str
    output_sequences: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SourceGateOutcome:
    classification: str
    eligible_for_integrated_calculation: bool
    source_identity: SourceIdentity | None
    reason: str


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity(value: object) -> bool:
    return isinstance(value, SourceIdentity) and all(_text(part) for part in (
        value.provider, value.market, value.instrument))


def _time(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == _KST


def _session_match(row: object, session: FrozenSession) -> bool:
    return (row.connection_epoch == session.connection_epoch
            and row.login_session_id == session.login_session_id
            and row.stock_code == session.stock_code)


def _reject(reason: str) -> SourceGateOutcome:
    return SourceGateOutcome("REJECTED", False, None, reason)


def validate_v5_sor_source_batch(
    session: FrozenSession,
    sources: tuple[SourceMetadata, ...],
    events: tuple[SourceEvent, ...],
    merge: VerifiedSorMerge | None = None,
) -> SourceGateOutcome:
    """Classify a bounded frozen batch; never infer SOR from KRX/NXT labels.

    A verified integrated batch contains integrated output events plus covered
    KRX and NXT contributor metadata. Raw mixed ticks remain unmerged inputs.
    The independent verifier is responsible for source coverage and de-dup truth.
    """
    if (not isinstance(session, FrozenSession)
            or type(session.connection_epoch) is not int or session.connection_epoch < 0
            or not _text(session.login_session_id) or not _text(session.stock_code)
            or not all(_time(value) for value in (
                session.window_start, session.window_end, session.as_of))
            or not session.window_start < session.window_end <= session.as_of):
        return _reject("invalid frozen session")
    if (type(sources) is not tuple or type(events) is not tuple or not sources
            or not events or len(sources) > 3 or len(events) > 10000):
        return _reject("missing or unbounded source/event batch")

    by_identity: dict[SourceIdentity, SourceMetadata] = {}
    for row in sources:
        if (not isinstance(row, SourceMetadata)
                or not _identity(row.identity)
                or row.identity in by_identity
                or row.role != _ROLES.get(row.identity.market)
                or not _session_match(row, session)
                or not _time(row.coverage_start) or not _time(row.coverage_end)
                or row.coverage_start > session.window_start
                or row.coverage_end < session.window_end
                or row.coverage_end > session.as_of
                or not _text(row.coverage_evidence)
                or row.data_quality != "NORMAL"):
            return _reject("invalid source, coverage, session, or data quality")
        expected_instrument = (f"{session.stock_code}_AL"
                               if row.role == "INTEGRATED_CALCULATION"
                               else session.stock_code)
        if row.identity.instrument != expected_instrument:
            return _reject("source instrument does not match its market role")
        by_identity[row.identity] = row

    previous_time: datetime | None = None
    previous_sequence: dict[SourceIdentity, int] = {}
    for tick in events:
        if (not isinstance(tick, SourceEvent)
                or not _identity(tick.identity)
                or tick.identity not in by_identity
                or not _session_match(tick, session)
                or not _time(tick.occurred_at)
                or not session.window_start <= tick.occurred_at < session.window_end
                or tick.occurred_at > session.as_of
                or type(tick.sequence) is not int or tick.sequence <= 0):
            return _reject("event source, session, time, or sequence invalid")
        if ((previous_time is not None and tick.occurred_at < previous_time)
                or tick.sequence <= previous_sequence.get(tick.identity, 0)):
            return _reject("duplicate or out-of-order event")
        previous_time = tick.occurred_at
        previous_sequence[tick.identity] = tick.sequence

    event_sources = {tick.identity for tick in events}
    if len(event_sources) != 1:
        return _reject("raw mixed sources are not an integrated output")
    output = by_identity[next(iter(event_sources))]
    if output.role != "INTEGRATED_CALCULATION":
        if merge is not None or len(sources) != 1:
            return _reject("unrelated source or merge proof for raw stream")
        return SourceGateOutcome(output.role, False, output.identity,
                                 "raw stream retains its original role")

    raw = {row.identity.market: row for row in sources
           if row.role in ("KRX_PRODUCTION", "NXT_DISPLAY_ONLY")}
    if (not isinstance(merge, VerifiedSorMerge)
            or len(sources) != 3 or set(raw) != {"KRX", "NXT"}
            or merge.output_source != output.identity
            or type(merge.contributors) is not tuple
            or len(merge.contributors) != 2
            or not all(_identity(value) for value in merge.contributors)
            or set(merge.contributors) != {row.identity for row in raw.values()}
            or not _session_match(merge, session)
            or not all(_text(value) for value in (
                merge.verified_by, merge.verification_id, merge.dedup_evidence,
                merge.sequence_evidence, merge.coverage_evidence))
            or merge.verified_by.strip().casefold() in {
                row.identity.provider.strip().casefold() for row in sources}
            or type(merge.output_sequences) is not tuple
            or merge.output_sequences != tuple(tick.sequence for tick in events)):
        return _reject("independent SOR merge provenance incomplete or mismatched")
    return SourceGateOutcome("INTEGRATED_CALCULATION", True, output.identity,
                             "caller supplied independently verified merge evidence")
