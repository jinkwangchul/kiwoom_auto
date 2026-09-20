# -*- coding: utf-8 -*-
"""Validation-only historical candle projection and routine signal replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from typing import Any, Callable

from candle_timeframe_aggregation import MARKET_BUCKET_ANCHOR, SEOUL_TIMEZONE
from engines.signal_result import RoutineSignal
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
)

from .routine_macd_engine import (
    build_indicator_follow_base_series,
    evaluate_indicator_follow_routine,
)
from .routine_validation_contract import ValidationStockRef
from .routine_validation_historical import ValidationHistoricalSnapshot
from .routine_validation_session import ValidationSession
from .routine_validation_trace import ValidationTraceObserver


REASON_INVALID_HISTORICAL_SNAPSHOT = "INVALID_HISTORICAL_SNAPSHOT"
REASON_HISTORICAL_IDENTITY_MISMATCH = "HISTORICAL_IDENTITY_MISMATCH"
REASON_NO_VALID_CANDLES = "NO_VALID_CANDLES"
REASON_INVALID_EVALUATION_RANGE = "INVALID_EVALUATION_RANGE"
REASON_EVALUATOR_ERROR = "EVALUATOR_ERROR"
REASON_INVALID_ROUTINE_SIGNAL = "INVALID_ROUTINE_SIGNAL"


class _PriceBoxPrefixSeries(list):
    """List-compatible Price Box view for one historical prefix."""

    def __init__(
        self,
        middle: list[float | None],
        offset: float | None,
        length: int,
    ) -> None:
        super().__init__()
        self._middle = middle
        self._offset = offset
        self._length = max(0, min(int(length), len(middle)))

    def __len__(self) -> int:
        return self._length

    def __bool__(self) -> bool:
        return self._length > 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self[position] for position in range(start, stop, step)]
        position = int(index)
        if position < 0:
            position += self._length
        if not 0 <= position < self._length:
            raise IndexError(position)
        middle_value = self._middle[position]
        if middle_value is None or self._offset is None:
            return None
        return float(middle_value) + self._offset


def _price_box_prefix_offsets(
    series_map: dict[str, list[float | None]],
) -> tuple[list[float | None], list[float | None]]:
    """Return prefix-equivalent lower/upper offsets for each evaluation index."""
    closes = series_map.get("CLOSE")
    middle = series_map.get("PRICE_BOX_MIDDLE")
    if not isinstance(closes, list) or not isinstance(middle, list):
        return [], []
    if len(closes) != len(middle):
        return [], []

    positive_count = 0
    positive_sum = 0.0
    positive_sum_sq = 0.0
    negative_count = 0
    negative_sum = 0.0
    negative_sum_sq = 0.0
    lower_offsets: list[float | None] = []
    upper_offsets: list[float | None] = []

    for close_value, middle_value in zip(closes, middle):
        close_number = _normalized_number(close_value)
        middle_number = _normalized_number(middle_value)
        if close_number is not None and middle_number is not None:
            deviation = close_number - middle_number
            if deviation > 0:
                positive_count += 1
                positive_sum += deviation
                positive_sum_sq += deviation * deviation
            elif deviation < 0:
                negative_count += 1
                negative_sum += deviation
                negative_sum_sq += deviation * deviation

        if positive_count:
            mean = positive_sum / positive_count
            variance = max(
                0.0,
                positive_sum_sq / positive_count - mean * mean,
            )
            upper_offsets.append(mean + 2.0 * math.sqrt(variance))
        else:
            upper_offsets.append(None)

        if negative_count:
            mean = negative_sum / negative_count
            variance = max(
                0.0,
                negative_sum_sq / negative_count - mean * mean,
            )
            lower_offsets.append(mean - 2.0 * math.sqrt(variance))
        else:
            lower_offsets.append(None)

    return lower_offsets, upper_offsets


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fresh_json(value_json: str) -> Any:
    return json.loads(value_json)


def _normalized_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text[0] in {"+", "-"}:
        text = text[1:].strip()
    if not text:
        return None
    try:
        number = abs(float(text))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return text


_VALIDATION_REGULAR_SESSION_END_MINUTE = 15 * 60 + 30


def _latest_candle_is_forming(
    candle_time: str,
    timeframe_minutes: int,
    *,
    as_of: datetime | None = None,
) -> bool:
    """Return True only when the latest row is the current Seoul-time bucket."""
    try:
        candle_start = datetime.strptime(candle_time, "%Y%m%d%H%M%S").replace(
            tzinfo=SEOUL_TIMEZONE
        )
    except ValueError:
        return False
    current = as_of or datetime.now(SEOUL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        current = current.astimezone(SEOUL_TIMEZONE)
    if candle_start.date() != current.date():
        return False

    current_minute = current.hour * 60 + current.minute
    anchor_minute = MARKET_BUCKET_ANCHOR.hour * 60 + MARKET_BUCKET_ANCHOR.minute
    if current_minute < anchor_minute or current_minute >= _VALIDATION_REGULAR_SESSION_END_MINUTE:
        return False

    elapsed = current_minute - anchor_minute
    bucket_offset = (elapsed // timeframe_minutes) * timeframe_minutes
    bucket_start = current.replace(
        hour=MARKET_BUCKET_ANCHOR.hour,
        minute=MARKET_BUCKET_ANCHOR.minute,
        second=0,
        microsecond=0,
    ) + timedelta(minutes=bucket_offset)
    bucket_end = bucket_start + timedelta(minutes=timeframe_minutes)
    return candle_start == bucket_start and current < bucket_end


def project_validation_candles(
    historical_snapshot: ValidationHistoricalSnapshot,
    *,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Project detached OPT10080 rows into closed chronological Validation candles."""
    if not isinstance(historical_snapshot, ValidationHistoricalSnapshot):
        raise TypeError("historical_snapshot must be ValidationHistoricalSnapshot")

    raw_rows = historical_snapshot.to_rows()
    candles_by_time: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        candle_time = _normalized_time(row.get("체결시간"))
        close = _normalized_number(row.get("현재가"))
        if candle_time is None or close is None:
            continue
        candles_by_time[candle_time] = {
            "time": candle_time,
            "open": _normalized_number(row.get("시가")),
            "high": _normalized_number(row.get("고가")),
            "low": _normalized_number(row.get("저가")),
            "close": close,
            "volume": _normalized_number(row.get("거래량")),
        }

    candles = [candles_by_time[key] for key in sorted(candles_by_time)]
    if candles and _latest_candle_is_forming(
        str(candles[-1].get("time") or ""),
        historical_snapshot.timeframe_minutes,
        as_of=as_of,
    ):
        candles.pop()
    detached = _fresh_json(_canonical_json(candles))
    return detached, len(raw_rows) - len(detached)


@dataclass(frozen=True, slots=True, init=False)
class ValidationReplayEntry:
    evaluation_side: str
    evaluation_index: int
    evaluation_time: str
    signal: str | None
    reason: str
    signal_index: int | None
    signal_time: str | None
    delay_bar: int
    _matched_groups_json: str
    _details_json: str
    _trace_json: str

    def __init__(
        self,
        *,
        evaluation_side: str,
        evaluation_index: int,
        evaluation_time: str,
        signal: str | None,
        reason: str,
        signal_index: int | None,
        signal_time: str | None,
        delay_bar: int,
        matched_groups: list[str],
        details: list[str],
        trace: dict[str, Any],
    ) -> None:
        side = str(evaluation_side or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("evaluation_side must be BUY or SELL")
        if isinstance(evaluation_index, bool) or not isinstance(evaluation_index, int):
            raise TypeError("evaluation_index must be an integer")
        if signal not in {None, "BUY", "SELL"}:
            raise ValueError("signal must be BUY, SELL, or None")
        if signal is not None and signal != side:
            raise ValueError("signal must match evaluation_side")
        if signal is None and (signal_index is not None or signal_time is not None):
            raise ValueError("non-signal entry cannot identify a signal bar")
        if signal is not None and (
            isinstance(signal_index, bool)
            or not isinstance(signal_index, int)
            or not str(signal_time or "").strip()
        ):
            raise ValueError("signal entry requires its signal bar")
        if isinstance(delay_bar, bool) or not isinstance(delay_bar, int) or delay_bar < 0:
            raise ValueError("delay_bar must be a non-negative integer")
        if not isinstance(matched_groups, list) or any(
            not isinstance(value, str) for value in matched_groups
        ):
            raise TypeError("matched_groups must be a list of strings")
        if not isinstance(details, list) or any(
            not isinstance(value, str) for value in details
        ):
            raise TypeError("details must be a list of strings")
        if not isinstance(trace, dict):
            raise TypeError("trace must be a mapping")

        object.__setattr__(self, "evaluation_side", side)
        object.__setattr__(self, "evaluation_index", evaluation_index)
        object.__setattr__(self, "evaluation_time", str(evaluation_time or "").strip())
        object.__setattr__(self, "signal", signal)
        object.__setattr__(self, "reason", str(reason or ""))
        object.__setattr__(self, "signal_index", signal_index)
        object.__setattr__(
            self,
            "signal_time",
            str(signal_time).strip() if signal_time is not None else None,
        )
        object.__setattr__(self, "delay_bar", delay_bar)
        object.__setattr__(self, "_matched_groups_json", _canonical_json(matched_groups))
        object.__setattr__(self, "_details_json", _canonical_json(details))
        object.__setattr__(self, "_trace_json", _canonical_json(trace))

    @property
    def matched_groups(self) -> list[str]:
        return _fresh_json(self._matched_groups_json)

    @property
    def details(self) -> list[str]:
        return _fresh_json(self._details_json)

    @property
    def trace(self) -> dict[str, Any]:
        return _fresh_json(self._trace_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_side": self.evaluation_side,
            "evaluation_index": self.evaluation_index,
            "evaluation_time": self.evaluation_time,
            "signal": self.signal,
            "reason": self.reason,
            "signal_index": self.signal_index,
            "signal_time": self.signal_time,
            "delay_bar": self.delay_bar,
            "matched_groups": self.matched_groups,
            "details": self.details,
            "trace": self.trace,
        }


@dataclass(frozen=True, slots=True, init=False)
class ValidationReplaySnapshot:
    stock: ValidationStockRef
    timeframe_minutes: int
    settings_hash: str
    historical_request_id: str
    candle_count: int
    evaluated_start_index: int
    evaluated_end_index: int
    dropped_raw_rows_count: int
    _candles_json: str
    _entries: tuple[ValidationReplayEntry, ...]

    def __init__(
        self,
        *,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        settings_hash: str,
        historical_request_id: str,
        evaluated_start_index: int,
        evaluated_end_index: int,
        dropped_raw_rows_count: int,
        candles: list[dict[str, Any]],
        entries: list[ValidationReplayEntry],
    ) -> None:
        if not isinstance(stock, ValidationStockRef):
            raise TypeError("stock must be ValidationStockRef")
        if not isinstance(candles, list) or any(
            not isinstance(candle, dict) for candle in candles
        ):
            raise TypeError("candles must be a list of mappings")
        if not isinstance(entries, list) or any(
            not isinstance(entry, ValidationReplayEntry) for entry in entries
        ):
            raise TypeError("entries must be ValidationReplayEntry values")
        if (
            isinstance(dropped_raw_rows_count, bool)
            or not isinstance(dropped_raw_rows_count, int)
            or dropped_raw_rows_count < 0
        ):
            raise ValueError("dropped_raw_rows_count must be non-negative")

        candles_json = _canonical_json(candles)
        copied_entries = tuple(_copy_entry(entry) for entry in entries)
        object.__setattr__(self, "stock", stock)
        object.__setattr__(self, "timeframe_minutes", timeframe_minutes)
        object.__setattr__(self, "settings_hash", str(settings_hash or ""))
        object.__setattr__(
            self,
            "historical_request_id",
            str(historical_request_id or "").strip(),
        )
        object.__setattr__(self, "candle_count", len(candles))
        object.__setattr__(self, "evaluated_start_index", evaluated_start_index)
        object.__setattr__(self, "evaluated_end_index", evaluated_end_index)
        object.__setattr__(self, "dropped_raw_rows_count", dropped_raw_rows_count)
        object.__setattr__(self, "_candles_json", candles_json)
        object.__setattr__(self, "_entries", copied_entries)

    def to_candles(self) -> list[dict[str, Any]]:
        return _fresh_json(self._candles_json)

    def to_entries(self) -> list[ValidationReplayEntry]:
        return [_copy_entry(entry) for entry in self._entries]

    def to_display_candles(self) -> list[dict[str, Any]]:
        candles = self.to_candles()
        start = max(0, min(self.evaluated_start_index, len(candles)))
        end = max(start, min(self.evaluated_end_index + 1, len(candles)))
        return candles[start:end]

    def to_display_entries(self) -> list[ValidationReplayEntry]:
        offset = self.evaluated_start_index
        projected: list[ValidationReplayEntry] = []
        for entry in self._entries:
            if not self.evaluated_start_index <= entry.evaluation_index <= self.evaluated_end_index:
                continue
            projected.append(ValidationReplayEntry(
                evaluation_side=entry.evaluation_side,
                evaluation_index=entry.evaluation_index - offset,
                evaluation_time=entry.evaluation_time,
                signal=entry.signal,
                reason=entry.reason,
                signal_index=(
                    None
                    if entry.signal_index is None
                    else entry.signal_index - offset
                ),
                signal_time=entry.signal_time,
                delay_bar=entry.delay_bar,
                matched_groups=entry.matched_groups,
                details=entry.details,
                trace=entry.trace,
            ))
        return projected


def _copy_entry(entry: ValidationReplayEntry) -> ValidationReplayEntry:
    return ValidationReplayEntry(
        evaluation_side=entry.evaluation_side,
        evaluation_index=entry.evaluation_index,
        evaluation_time=entry.evaluation_time,
        signal=entry.signal,
        reason=entry.reason,
        signal_index=entry.signal_index,
        signal_time=entry.signal_time,
        delay_bar=entry.delay_bar,
        matched_groups=entry.matched_groups,
        details=entry.details,
        trace=entry.trace,
    )


@dataclass(frozen=True, slots=True)
class ValidationReplayResult:
    ok: bool
    snapshot: ValidationReplaySnapshot | None = None
    reason: str | None = None
    error: str | None = None


class ValidationHistoricalReplay:
    """Replay current Validation settings against historical candle prefixes."""

    def __init__(
        self,
        session: ValidationSession,
        *,
        evaluator: Callable[
            [list[dict[str, Any]], dict[str, Any], dict[str, Any]],
            RoutineSignal,
        ] = evaluate_indicator_follow_routine,
    ) -> None:
        if not isinstance(session, ValidationSession):
            raise TypeError("session must be ValidationSession")
        if not callable(evaluator):
            raise TypeError("evaluator must be callable")
        self.session = session
        self._evaluator = evaluator

    def evaluate(
        self,
        historical_snapshot: ValidationHistoricalSnapshot,
        *,
        start_index: int = 0,
        end_index: int | None = None,
        context_provider: Callable[..., dict[str, Any]] | None = None,
        display_count: int | None = None,
    ) -> ValidationReplayResult:
        availability = self.session.readiness()
        if not availability.allowed:
            return ValidationReplayResult(False, reason=availability.reason)

        request = self.session.request
        if not isinstance(historical_snapshot, ValidationHistoricalSnapshot):
            return ValidationReplayResult(
                False,
                reason=REASON_INVALID_HISTORICAL_SNAPSHOT,
            )
        if (
            historical_snapshot.stock != request.stock
            or historical_snapshot.timeframe_minutes != request.timeframe_minutes
        ):
            return ValidationReplayResult(
                False,
                reason=REASON_HISTORICAL_IDENTITY_MISMATCH,
            )

        try:
            candles, dropped_count = project_validation_candles(historical_snapshot)
        except (TypeError, ValueError, OverflowError):
            return ValidationReplayResult(
                False,
                reason=REASON_INVALID_HISTORICAL_SNAPSHOT,
            )
        if not candles:
            return ValidationReplayResult(False, reason=REASON_NO_VALID_CANDLES)

        if display_count is not None:
            if (
                isinstance(display_count, bool)
                or not isinstance(display_count, int)
                or display_count <= 0
            ):
                return ValidationReplayResult(
                    False,
                    reason=REASON_INVALID_EVALUATION_RANGE,
                )
            start_index = max(start_index, len(candles) - display_count)

        final_index = len(candles) - 1 if end_index is None else end_index
        if not self._valid_range(start_index, final_index, len(candles)):
            return ValidationReplayResult(
                False,
                reason=REASON_INVALID_EVALUATION_RANGE,
            )

        rules = request.settings_snapshot.to_dict()
        entries: list[ValidationReplayEntry] = []
        reuse_default_base_series = self._evaluator is evaluate_indicator_follow_routine
        use_read_only_fast_path = reuse_default_base_series and (
            context_provider is None
            or context_provider is build_validation_average_price_context
            or getattr(context_provider, "validation_read_only_fast_path", False) is True
        )
        rules_json = None if use_read_only_fast_path else _canonical_json(rules)
        try:
            for evaluation_index in range(start_index, final_index + 1):
                prefix = candles[: evaluation_index + 1]
                prefix_json = (
                    None if use_read_only_fast_path else _canonical_json(prefix)
                )
                base_series_map = (
                    build_indicator_follow_base_series(
                        prefix
                        if use_read_only_fast_path
                        else _fresh_json(prefix_json),
                        rules
                        if use_read_only_fast_path
                        else _fresh_json(rules_json),
                    )
                    if reuse_default_base_series and evaluation_index >= 2
                    else None
                )
                for side in ("SELL", "BUY"):
                    observer = ValidationTraceObserver()
                    context = {
                        "decision_trace_observer": observer,
                        "_indicator_follow_evaluate_side": side,
                    }
                    if context_provider is not None:
                        fast_context = getattr(
                            context_provider,
                            "context_for_fast",
                            None,
                        )
                        supplied = (
                            fast_context(
                                evaluation_index,
                                side,
                                prefix,
                                entries,
                            )
                            if use_read_only_fast_path and callable(fast_context)
                            else context_provider(
                                evaluation_index,
                                side,
                                prefix
                                if use_read_only_fast_path
                                else _fresh_json(prefix_json),
                                entries
                                if use_read_only_fast_path
                                else list(entries),
                            )
                        )
                        if not isinstance(supplied, dict):
                            raise TypeError("context_provider must return a mapping")
                        supplied.pop("decision_trace_observer", None)
                        supplied.pop("_indicator_follow_evaluate_side", None)
                        context.update(
                            supplied
                            if use_read_only_fast_path
                            else _fresh_json(_canonical_json(supplied))
                        )
                    if base_series_map is None:
                        signal = self._evaluator(
                            prefix
                            if use_read_only_fast_path
                            else _fresh_json(prefix_json),
                            rules
                            if use_read_only_fast_path
                            else _fresh_json(rules_json),
                            context,
                        )
                    else:
                        signal = evaluate_indicator_follow_routine(
                            prefix
                            if use_read_only_fast_path
                            else _fresh_json(prefix_json),
                            rules
                            if use_read_only_fast_path
                            else _fresh_json(rules_json),
                            context,
                            _base_series_map=base_series_map,
                        )
                    entries.append(
                        self._entry_from_signal(
                            side,
                            evaluation_index,
                            candles[evaluation_index]["time"],
                            prefix
                            if use_read_only_fast_path
                            else _fresh_json(prefix_json),
                            signal,
                            self._trace_with_context(
                                observer.snapshot(),
                                context,
                                assume_detached=use_read_only_fast_path,
                            ),
                        )
                    )
        except Exception as exc:
            reason = (
                REASON_INVALID_ROUTINE_SIGNAL
                if isinstance(exc, (TypeError, ValueError))
                else REASON_EVALUATOR_ERROR
            )
            return ValidationReplayResult(False, reason=reason, error=str(exc))

        availability = self.session.readiness()
        if not availability.allowed:
            return ValidationReplayResult(False, reason=availability.reason)

        try:
            snapshot = ValidationReplaySnapshot(
                stock=request.stock,
                timeframe_minutes=request.timeframe_minutes,
                settings_hash=request.settings_snapshot.rules_hash,
                historical_request_id=historical_snapshot.request_id,
                evaluated_start_index=start_index,
                evaluated_end_index=final_index,
                dropped_raw_rows_count=dropped_count,
                candles=candles,
                entries=entries,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            return ValidationReplayResult(
                False,
                reason=REASON_INVALID_ROUTINE_SIGNAL,
                error=str(exc),
            )
        return ValidationReplayResult(True, snapshot=snapshot)

    def scan_signal_entries(
        self,
        historical_snapshot: ValidationHistoricalSnapshot,
        *,
        context_provider: Callable[..., dict[str, Any]] | None = None,
        start_index: int = 0,
        end_index: int | None = None,
    ) -> list[ValidationReplayEntry]:
        availability = self.session.readiness()
        if not availability.allowed:
            return []
        request = self.session.request
        if not isinstance(historical_snapshot, ValidationHistoricalSnapshot):
            raise TypeError("historical_snapshot must be ValidationHistoricalSnapshot")
        if (
            historical_snapshot.stock != request.stock
            or historical_snapshot.timeframe_minutes != request.timeframe_minutes
        ):
            raise ValueError("historical snapshot identity mismatch")

        candles, _dropped_count = project_validation_candles(historical_snapshot)
        if not candles:
            return []
        final_index = len(candles) - 1 if end_index is None else end_index
        if not self._valid_range(start_index, final_index, len(candles)):
            raise ValueError("signal scan range is invalid")

        rules = request.settings_snapshot.to_dict()
        reuse_default_base_series = self._evaluator is evaluate_indicator_follow_routine
        base_series_map = (
            build_indicator_follow_base_series(candles, rules)
            if reuse_default_base_series
            else None
        )
        price_box_lower_offsets, price_box_upper_offsets = (
            _price_box_prefix_offsets(base_series_map)
            if base_series_map is not None
            else ([], [])
        )
        price_box_middle = (
            base_series_map.get("PRICE_BOX_MIDDLE")
            if base_series_map is not None
            else None
        )
        entries: list[ValidationReplayEntry] = []
        for evaluation_index in range(start_index, final_index + 1):
            prefix = candles[: evaluation_index + 1]
            evaluation_series_map = base_series_map
            if (
                base_series_map is not None
                and isinstance(price_box_middle, list)
                and evaluation_index < len(price_box_lower_offsets)
                and evaluation_index < len(price_box_upper_offsets)
            ):
                evaluation_series_map = dict(base_series_map)
                evaluation_series_map["PRICE_BOX_LOWER"] = _PriceBoxPrefixSeries(
                    price_box_middle,
                    price_box_lower_offsets[evaluation_index],
                    evaluation_index + 1,
                )
                evaluation_series_map["PRICE_BOX_UPPER"] = _PriceBoxPrefixSeries(
                    price_box_middle,
                    price_box_upper_offsets[evaluation_index],
                    evaluation_index + 1,
                )
            for side in ("SELL", "BUY"):
                observer = ValidationTraceObserver()
                context = {
                    "decision_trace_observer": observer,
                    "_indicator_follow_evaluate_side": side,
                }
                if context_provider is not None:
                    fast_context = getattr(
                        context_provider,
                        "context_for_fast",
                        None,
                    )
                    supplied = (
                        fast_context(
                            evaluation_index,
                            side,
                            prefix,
                            entries,
                        )
                        if callable(fast_context)
                        else context_provider(
                            evaluation_index,
                            side,
                            prefix,
                            entries,
                        )
                    )
                    if not isinstance(supplied, dict):
                        raise TypeError("context_provider must return a mapping")
                    supplied.pop("decision_trace_observer", None)
                    supplied.pop("_indicator_follow_evaluate_side", None)
                    context.update(supplied)

                signal = (
                    evaluate_indicator_follow_routine(
                        prefix,
                        rules,
                        context,
                        _base_series_map=evaluation_series_map,
                    )
                    if evaluation_series_map is not None and evaluation_index >= 2
                    else self._evaluator(prefix, rules, context)
                )
                if signal.signal != side:
                    continue
                entries.append(
                    self._entry_from_signal(
                        side,
                        evaluation_index,
                        candles[evaluation_index]["time"],
                        prefix,
                        signal,
                        self._trace_with_context(
                            observer.snapshot(),
                            context,
                            assume_detached=True,
                        ),
                    )
                )
        return entries

    def evaluate_with_context(
        self,
        historical_snapshot: ValidationHistoricalSnapshot,
        *,
        context_provider: Callable[..., dict[str, Any]],
        start_index: int = 0,
        end_index: int | None = None,
        display_count: int | None = None,
    ) -> ValidationReplayResult:
        if not callable(context_provider):
            raise TypeError("context_provider must be callable")
        return self.evaluate(
            historical_snapshot,
            start_index=start_index,
            end_index=end_index,
            context_provider=context_provider,
            display_count=display_count,
        )

    @staticmethod
    def _trace_with_context(
        trace: dict[str, Any],
        context: dict[str, Any],
        *,
        assume_detached: bool = False,
    ) -> dict[str, Any]:
        copied = (
            dict(trace)
            if assume_detached
            else _fresh_json(_canonical_json(trace))
        )
        evidence = context.get("validation_trace_context")
        if isinstance(evidence, dict):
            copied["evaluation_context"] = (
                evidence
                if assume_detached
                else _fresh_json(_canonical_json(evidence))
            )
        return copied

    @staticmethod
    def _valid_range(start_index: Any, end_index: Any, candle_count: int) -> bool:
        return (
            isinstance(start_index, int)
            and not isinstance(start_index, bool)
            and isinstance(end_index, int)
            and not isinstance(end_index, bool)
            and 0 <= start_index <= end_index < candle_count
        )

    @staticmethod
    def _entry_from_signal(
        side: str,
        evaluation_index: int,
        evaluation_time: str,
        prefix: list[dict[str, Any]],
        signal: RoutineSignal,
        trace: dict[str, Any],
    ) -> ValidationReplayEntry:
        if not isinstance(signal, RoutineSignal):
            raise TypeError("evaluator must return RoutineSignal")
        if signal.signal not in {None, "BUY", "SELL"}:
            raise ValueError("evaluator returned an unsupported signal")
        if signal.signal is not None and signal.signal != side:
            raise ValueError("evaluator returned a signal for the opposite side")
        if (
            isinstance(signal.delay_bar, bool)
            or not isinstance(signal.delay_bar, int)
            or signal.delay_bar < 0
        ):
            raise ValueError("evaluator returned an invalid delay_bar")
        if not isinstance(signal.reason, str):
            raise TypeError("evaluator returned an invalid reason")
        if not isinstance(signal.matched_groups, list) or any(
            not isinstance(value, str) for value in signal.matched_groups
        ):
            raise TypeError("evaluator returned invalid matched_groups")
        if not isinstance(signal.details, list) or any(
            not isinstance(value, str) for value in signal.details
        ):
            raise TypeError("evaluator returned invalid details")

        signal_index: int | None = None
        signal_time: str | None = None
        if signal.signal is not None:
            if (
                isinstance(signal.signal_index, bool)
                or not isinstance(signal.signal_index, int)
                or not 0 <= signal.signal_index < len(prefix)
            ):
                raise ValueError("evaluator returned an invalid signal_index")
            signal_index = signal.signal_index
            signal_time = str(prefix[signal_index]["time"])

        return ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=evaluation_index,
            evaluation_time=evaluation_time,
            signal=signal.signal,
            reason=signal.reason,
            signal_index=signal_index,
            signal_time=signal_time,
            delay_bar=signal.delay_bar,
            matched_groups=signal.matched_groups,
            details=signal.details,
            trace=trace,
        )
