"""Verified-only V5 worker view bridge; the caller owns independent evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from indicator_follow_validation_v5_sor_m1_supply import (
    ConfirmedSorM1, supply_confirmed_sor_m1,
)
from indicator_follow_validation_v5_sor_source_gate import (
    FrozenSession, SourceEvent, SourceMetadata, VerifiedSorMerge,
)
from indicator_follow_validation_v5_sor_tail import (
    ConfirmedComponent, CoverageInterval, SourceIdentity,
    build_v5_sor_calculation_tail,
)


_MINUTE_KEYS = {f"M{minutes}": minutes for minutes in (1, 3, 5, 10, 15, 30, 60, 120, 240)}
_CALENDAR_KEYS = {"D1", "W1", "MO1", "Y1"}


@dataclass(frozen=True, slots=True)
class SorTailBatch:
    session: FrozenSession
    sources: tuple[SourceMetadata, ...]
    events: tuple[SourceEvent, ...]
    merge: VerifiedSorMerge
    m1_coverage: tuple[CoverageInterval, ...]
    m1_bars: tuple[ConfirmedSorM1, ...]
    bucket_start: datetime
    bucket_anchor: datetime | None
    history_sha256: str
    history_verified_by: str
    history_verification_id: str
    calendar_verified_by: str
    calendar_verification_id: str
    extra_coverage: tuple[CoverageInterval, ...] = ()
    extra_components: tuple[ConfirmedComponent, ...] = ()


def apply_verified_sor_tail(
    view: dict[str, object], *, stock_code: str, timeframe_key: str,
    timeframe_minutes: int, as_of: datetime, history_sha256: str,
    batch: SorTailBatch,
) -> dict[str, object]:
    """Replace only the last target bar in a detached view or block explicitly.

    History and calendar attestations must come from the injected independent
    producer. An _AL broker label and its target-timeframe TR prove neither.
    """
    def block(reason: str) -> None:
        raise ValueError(f"SOR_VERIFIED_BLOCKED:{reason}")

    if (not isinstance(batch, SorTailBatch)
            or not isinstance(batch.session, FrozenSession)
            or not isinstance(as_of, datetime) or as_of.tzinfo is None
            or as_of.utcoffset() != timedelta(hours=9)
            or batch.session.as_of != as_of
            or batch.session.stock_code != stock_code
            or not isinstance(history_sha256, str) or len(history_sha256) != 64
            or batch.history_sha256 != history_sha256
            or not all(isinstance(value, str) and value.strip() for value in (
                batch.history_verified_by, batch.history_verification_id,
                batch.calendar_verified_by, batch.calendar_verification_id,
            ))):
        block("SESSION_OR_ATTESTATION")
    if (not isinstance(timeframe_key, str)
            or type(timeframe_minutes) is not int or timeframe_minutes <= 0
            or (timeframe_key not in _MINUTE_KEYS and timeframe_key not in _CALENDAR_KEYS)
            or (timeframe_key in _MINUTE_KEYS
                and timeframe_minutes != _MINUTE_KEYS[timeframe_key])):
        block("SOURCE_OR_TARGET")
    source = batch.merge.output_source if isinstance(batch.merge, VerifiedSorMerge) else None
    if (not isinstance(source, SourceIdentity)
            or source.market not in ("INTEGRATED", "SOR")
            or batch.history_verified_by.strip().casefold() == source.provider.strip().casefold()
            or batch.calendar_verified_by.strip().casefold() == source.provider.strip().casefold()
            or not isinstance(view, dict)
            or view.get("market_source") != "INTEGRATED"
            or view.get("market_data_identity") != f"{stock_code}_AL"
            or view.get("timeframe_key") != timeframe_key
            or view.get("timeframe_minutes") != timeframe_minutes
            or source.instrument != f"{stock_code}_AL"):
        block("SOURCE_OR_TARGET")
    supply = supply_confirmed_sor_m1(
        batch.session, batch.sources, batch.events, batch.merge,
        batch.m1_coverage, batch.m1_bars,
    )
    if supply is None or supply.source_identity != source:
        block("M1_SUPPLY")
    if (not isinstance(batch.bucket_start, datetime)
            or type(batch.extra_coverage) is not tuple
            or type(batch.extra_components) is not tuple
            or len(batch.extra_coverage) > 1440
            or len(batch.extra_components) > 1440
            or any(not isinstance(row, ConfirmedComponent)
                   or row.granularity not in {"D1", "MO1"}
                   for row in batch.extra_components)
            or (timeframe_key in {"W1", "MO1", "Y1"}
                and batch.bucket_start.date() < as_of.date()
                and not batch.extra_components)):
        block("EXTRA_COMPONENTS")
    try:
        tail = build_v5_sor_calculation_tail(
            timeframe_key, as_of, batch.bucket_start, source,
            (*batch.extra_coverage, *supply.coverage),
            (*batch.extra_components, *supply.components),
            raw_complete=False, bucket_anchor=batch.bucket_anchor,
        )
    except (TypeError, ValueError) as exc:
        block(f"TAIL:{exc}")
    candles = view.get("candles")
    display = view.get("display_candles", candles)
    stamp = tail.start.strftime("%Y%m%d%H%M%S")
    if (not isinstance(candles, list) or not candles
            or not isinstance(display, list) or not display
            or not isinstance(candles[-1], dict)
            or not isinstance(display[-1], dict)
            or candles[-1].get("time") != stamp
            or display[-1].get("time") != stamp):
        block("LATEST_TARGET_ALIGNMENT")
    replacement = {
        "time": stamp, "open": tail.open, "high": tail.high,
        "low": tail.low, "close": tail.close, "volume": tail.volume,
    }
    result = dict(view)
    result["candles"] = [*candles[:-1], replacement]
    result["display_candles"] = [*display[:-1], dict(replacement)]
    result["sor_tail_signature"] = (
        history_sha256, source, tuple(replacement.values()),
    )
    result["sor_tail_calculation_complete"] = tail.calculation_complete
    return result
