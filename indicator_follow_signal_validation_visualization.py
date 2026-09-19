# -*- coding: utf-8 -*-
"""Pure visualization data projection for Indicator-Follow Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from engines.indicator_engine import (
    bollinger_band,
    close_prices,
    macd_series,
    price_box,
    rsi,
    simple_ma,
)
from indicator_follow_signal_validation_execution import (
    ValidationVirtualPositionTracker,
    validation_virtual_fill_price,
)
from indicator_follow_signal_validation_presentation import (
    canonical_validation_condition_key,
    signal_evidence_records_for_entry,
)


PRICE_AXIS = "PRICE"
LOWER_AXIS = "LOWER"

FAMILY_MOVING_AVERAGE = "MOVING_AVERAGE"
FAMILY_MA_ARRANGEMENT = "MA_ARRANGEMENT"
FAMILY_BOLLINGER = "BOLLINGER"
FAMILY_PRICE_BOX = "PRICE_BOX"
FAMILY_PRICE_COMPARISON = "PRICE_COMPARISON"
FAMILY_RSI = "RSI"
FAMILY_MACD_SIGNAL = "MACD_SIGNAL"
FAMILY_OCR_OSC = "OCR_OSC"

_FAMILY_LABELS = {
    FAMILY_MOVING_AVERAGE: "이동평균",
    FAMILY_MA_ARRANGEMENT: "이평배열",
    FAMILY_BOLLINGER: "Bollinger",
    FAMILY_PRICE_BOX: "Price Box",
    FAMILY_PRICE_COMPARISON: "가격비교",
    FAMILY_RSI: "RSI",
    FAMILY_MACD_SIGNAL: "MACD / Signal",
    FAMILY_OCR_OSC: "OCR / OSC",
}

_LOWER_FAMILIES = {
    FAMILY_RSI,
    FAMILY_MACD_SIGNAL,
    FAMILY_OCR_OSC,
}


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _series_target(condition: Mapping[str, Any], field: str, period_field: str) -> str:
    target = str(condition.get(field) or "").strip().upper()
    if target == "MA":
        period = _positive_int(condition.get(period_field))
        return f"MA{period}" if period is not None else "MA"
    return target


def _condition_series_keys(
    family: str,
    condition: Mapping[str, Any],
) -> tuple[str, ...]:
    target = _series_target(condition, "target", "period")
    compare = _series_target(condition, "compare_target", "compare_period")
    if family == FAMILY_BOLLINGER:
        return ("BOLLINGER_LOWER", "BOLLINGER_MIDDLE", "BOLLINGER_UPPER")
    if family == FAMILY_PRICE_BOX:
        return ("PRICE_BOX_LOWER", "PRICE_BOX_MIDDLE", "PRICE_BOX_UPPER")
    if family == FAMILY_MACD_SIGNAL:
        return ("MACD", "SIGNAL")
    if family == FAMILY_OCR_OSC:
        return ("OSC",)
    if family == FAMILY_RSI:
        return ("RSI",)
    if family in {FAMILY_MOVING_AVERAGE, FAMILY_MA_ARRANGEMENT}:
        values = [
            value
            for value in (target, compare)
            if value.startswith("MA") and value[2:].isdigit()
        ]
        return tuple(dict.fromkeys(values))
    if family == FAMILY_PRICE_COMPARISON:
        values = []
        for value in (target, compare):
            if value in {"CLOSE", "AVG_PRICE"}:
                values.append(value)
            elif value == "ORDER_PRICE":
                values.append("VIRTUAL_FILL_PRICE")
        return tuple(dict.fromkeys(values)) or ("CLOSE", "AVG_PRICE")
    return ()


def _family_for_condition(
    condition: Mapping[str, Any],
    *,
    hint: str = "",
) -> str | None:
    expression_id = str(condition.get("expression_id") or "").strip().upper()
    condition_type = str(condition.get("type") or "").strip().upper()
    target = _series_target(condition, "target", "period")
    compare = _series_target(condition, "compare_target", "compare_period")
    operator = str(condition.get("operator") or "").strip().upper()
    hint = str(hint or "").strip().lower()

    if expression_id.startswith("ARRAY_") or (
        target.startswith("MA")
        and compare.startswith("MA")
        and target != compare
    ):
        return FAMILY_MA_ARRANGEMENT
    if "PRICE_BOX" in target or "PRICE_BOX" in compare or "price_box" in hint:
        return FAMILY_PRICE_BOX
    if "BOLLINGER" in target or "BOLLINGER" in compare or "bollinger" in hint:
        return FAMILY_BOLLINGER
    if operator == "PERCENT_GAP" or "price_compare" in hint or expression_id.startswith("GAP_"):
        return FAMILY_PRICE_COMPARISON
    if target == "RSI" or compare == "RSI" or hint == "rsi":
        return FAMILY_RSI
    if target in {"MACD", "SIGNAL"} or compare in {"MACD", "SIGNAL"} or hint == "macd":
        return FAMILY_MACD_SIGNAL
    if target in {"OSC", "OCR"} or compare in {"OSC", "OCR"} or hint in {"ocr", "osc"}:
        return FAMILY_OCR_OSC
    if (
        target.startswith("MA")
        or compare.startswith("MA")
        or condition_type in {"MA", "MOVING_AVERAGE"}
        or hint == "moving_average"
    ):
        return FAMILY_MOVING_AVERAGE
    return None


def _global_parameters(
    rules: Mapping[str, Any],
    family: str,
    condition: Mapping[str, Any],
) -> dict[str, Any]:
    indicators = _mapping(rules.get("indicators"))
    if family == FAMILY_RSI:
        config = _mapping(indicators.get("rsi"))
        return {"period": _positive_int(condition.get("period")) or _positive_int(config.get("period")) or 14}
    if family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
        config = _mapping(indicators.get("macd"))
        return {
            "fast": _positive_int(config.get("fast")) or 12,
            "slow": _positive_int(config.get("slow")) or 26,
            "signal": _positive_int(config.get("signal")) or 9,
        }
    if family == FAMILY_BOLLINGER:
        config = _mapping(indicators.get("bollinger"))
        return {
            "period": _positive_int(config.get("period")) or 20,
            "std": float(config.get("std") or 2.0),
        }
    if family == FAMILY_PRICE_BOX:
        config = _mapping(indicators.get("price_box"))
        return {"period": _positive_int(config.get("period")) or 24}
    if family in {FAMILY_MOVING_AVERAGE, FAMILY_MA_ARRANGEMENT}:
        periods = []
        for key in ("period", "compare_period"):
            value = _positive_int(condition.get(key))
            if value is not None:
                periods.append(value)
        for key in _condition_series_keys(family, condition):
            if key.startswith("MA") and key[2:].isdigit():
                periods.append(int(key[2:]))
        return {"periods": tuple(sorted(set(periods)))}
    if family == FAMILY_PRICE_COMPARISON:
        return {
            key: condition.get(key)
            for key in (
                "target",
                "compare_target",
                "operator",
                "direction",
                "compare_mode",
                "value",
            )
            if condition.get(key) not in (None, "")
        }
    return {}


def _descriptor_identity(
    family: str,
    parameters: Mapping[str, Any],
    series_keys: tuple[str, ...],
) -> str:
    payload = _json({
        "family": family,
        "parameters": dict(parameters),
        "series_keys": list(series_keys),
    })
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{family}:{digest}"


@dataclass(frozen=True, slots=True)
class ValidationFilterDescriptor:
    identity: str
    family: str
    label: str
    axis: str
    sides: tuple[str, ...]
    series_keys: tuple[str, ...]
    parameter_json: str
    evidence_keys: tuple[str, ...]
    supported_sides: tuple[str, ...] = ()
    unsupported_sides: tuple[str, ...] = ()
    supported: bool = True
    unavailable_reason: str = ""

    @property
    def parameters(self) -> dict[str, Any]:
        value = json.loads(self.parameter_json)
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class ValidationIndicatorSeriesCache:
    candle_count: int
    series: tuple[tuple[str, str, tuple[float | None, ...]], ...]

    def values_for(
        self,
        descriptor_identity: str,
        channel: str,
    ) -> tuple[float | None, ...]:
        expected_identity = str(descriptor_identity or "").strip()
        expected_channel = str(channel or "").strip().upper()
        for identity, name, values in self.series:
            if identity == expected_identity and name == expected_channel:
                return values
        return ()

    def channels_for(self, descriptor_identity: str) -> tuple[str, ...]:
        expected = str(descriptor_identity or "").strip()
        return tuple(
            name
            for identity, name, _values in self.series
            if identity == expected
        )


def _actual_evidence_keys(rules: Mapping[str, Any], side: str) -> set[str]:
    side_rules = _mapping(rules.get(side.lower()))
    keys: set[str] = set()
    filters = _mapping(side_rules.get("filters"))
    for filter_name, config in filters.items():
        if filter_name == "composite" or not isinstance(config, Mapping):
            continue
        for condition in config.get("conditions", []):
            key = canonical_validation_condition_key(condition)
            if key:
                keys.add(key)
        if config.get("enabled") is not False and not config.get("conditions"):
            keys.add(f"{side.lower()}-filter:{filter_name}")
    signals = _mapping(side_rules.get("signals"))
    for signal in signals.values():
        if not isinstance(signal, Mapping) or signal.get("enabled") is False:
            continue
        for group in signal.get("groups", []):
            if not isinstance(group, Mapping) or group.get("enabled") is False:
                continue
            for condition in group.get("conditions", []):
                key = canonical_validation_condition_key(condition)
                if key:
                    keys.add(key)
    return keys


def _visualization_source(rules: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    root = _mapping(rules.get("validation_visualization_rules"))
    side_source = _mapping(root.get(side.lower()))
    return side_source or _mapping(rules.get(side.lower()))


def build_validation_filter_universe(
    rules: Mapping[str, Any],
) -> tuple[ValidationFilterDescriptor, ...]:
    """Return the BUY∪SELL configured visualization universe."""
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    actual_by_side = {
        "BUY": _actual_evidence_keys(rules, "BUY"),
        "SELL": _actual_evidence_keys(rules, "SELL"),
    }
    grouped: dict[str, dict[str, Any]] = {}

    def add_condition(side: str, condition: Mapping[str, Any], hint: str = "") -> None:
        if condition.get("enabled") is False:
            return
        family = _family_for_condition(condition, hint=hint)
        if family is None:
            return
        series_keys = _condition_series_keys(family, condition)
        parameters = _global_parameters(rules, family, condition)
        identity = _descriptor_identity(family, parameters, series_keys)
        evidence_key = canonical_validation_condition_key(condition)
        supported = bool(evidence_key and evidence_key in actual_by_side[side])
        reason = "" if supported else "VALIDATION_FILTER_NOT_EVALUATED"
        current = grouped.get(identity)
        if current is None:
            current = {
                "family": family,
                "axis": LOWER_AXIS if family in _LOWER_FAMILIES else PRICE_AXIS,
                "sides": set(),
                "series_keys": series_keys,
                "parameters": parameters,
                "evidence_keys": set(),
                "supported_sides": set(),
                "unsupported_sides": set(),
                "supported": False,
                "reasons": set(),
            }
            grouped[identity] = current
        current["sides"].add(side)
        if evidence_key:
            current["evidence_keys"].add(evidence_key)
        if supported:
            current["supported_sides"].add(side)
        else:
            current["unsupported_sides"].add(side)
        current["supported"] = current["supported"] or supported
        if reason:
            current["reasons"].add(reason)


    def add_arrangement(
        side: str,
        conditions: list[Mapping[str, Any]],
    ) -> None:
        if not conditions:
            return
        periods: set[int] = set()
        evidence_keys: set[str] = set()
        for condition in conditions:
            for field in ("period", "compare_period"):
                value = _positive_int(condition.get(field))
                if value is not None:
                    periods.add(value)
            for key in _condition_series_keys(FAMILY_MA_ARRANGEMENT, condition):
                if key.startswith("MA") and key[2:].isdigit():
                    periods.add(int(key[2:]))
            evidence_key = canonical_validation_condition_key(condition)
            if evidence_key:
                evidence_keys.add(evidence_key)
        series_keys = tuple(f"MA{period}" for period in sorted(periods))
        parameters = {"periods": tuple(sorted(periods))}
        identity = _descriptor_identity(
            FAMILY_MA_ARRANGEMENT,
            parameters,
            series_keys,
        )
        supported = bool(evidence_keys) and evidence_keys.issubset(
            actual_by_side[side]
        )
        current = grouped.get(identity)
        if current is None:
            current = {
                "family": FAMILY_MA_ARRANGEMENT,
                "axis": PRICE_AXIS,
                "sides": set(),
                "series_keys": series_keys,
                "parameters": parameters,
                "evidence_keys": set(),
                "supported_sides": set(),
                "unsupported_sides": set(),
                "supported": False,
                "reasons": set(),
            }
            grouped[identity] = current
        current["sides"].add(side)
        current["evidence_keys"].update(evidence_keys)
        if supported:
            current["supported_sides"].add(side)
        else:
            current["unsupported_sides"].add(side)
            current["reasons"].add("VALIDATION_FILTER_NOT_EVALUATED")
        current["supported"] = current["supported"] or supported


    def add_conditions(
        side: str,
        conditions: list[Mapping[str, Any]],
        *,
        hint: str = "",
    ) -> None:
        array_conditions = [
            condition
            for condition in conditions
            if str(condition.get("expression_id") or "").strip().upper().startswith("ARRAY_")
        ]
        if array_conditions:
            add_arrangement(side, array_conditions)
        for condition in conditions:
            if condition in array_conditions:
                continue
            add_condition(side, condition, hint=hint)

    for side in ("BUY", "SELL"):
        source = _visualization_source(rules, side)
        filters = _mapping(source.get("filters"))
        for filter_name, config in filters.items():
            if filter_name == "composite" or not isinstance(config, Mapping):
                continue
            if config.get("enabled") is False:
                continue
            conditions = [
                item for item in config.get("conditions", [])
                if isinstance(item, Mapping)
            ]
            add_conditions(side, conditions, hint=str(filter_name))

        groups = source.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, Mapping) or group.get("enabled") is False:
                    continue
                conditions = [
                    condition
                    for condition in group.get("conditions", [])
                    if isinstance(condition, Mapping)
                ]
                add_conditions(side, conditions)

        signals = _mapping(source.get("signals"))
        for signal in signals.values():
            if not isinstance(signal, Mapping) or signal.get("enabled") is False:
                continue
            for group in signal.get("groups", []):
                if not isinstance(group, Mapping) or group.get("enabled") is False:
                    continue
                conditions = [
                    condition
                    for condition in group.get("conditions", [])
                    if isinstance(condition, Mapping)
                ]
                add_conditions(side, conditions)

    return tuple(
        ValidationFilterDescriptor(
            identity=identity,
            family=value["family"],
            label=_FAMILY_LABELS[value["family"]],
            axis=value["axis"],
            sides=tuple(sorted(value["sides"])),
            series_keys=tuple(value["series_keys"]),
            parameter_json=_json(value["parameters"]),
            evidence_keys=tuple(sorted(value["evidence_keys"])),
            supported_sides=tuple(sorted(value["supported_sides"])),
            unsupported_sides=tuple(sorted(value["unsupported_sides"])),
            supported=bool(value["supported"]),
            unavailable_reason=(
                ""
                if value["supported"]
                else ",".join(sorted(value["reasons"])) or "VALIDATION_FILTER_NOT_EVALUATED"
            ),
        )
        for identity, value in sorted(grouped.items())
    )


def _price_box_prefix_series(
    candles: list[dict[str, Any]],
    period: int,
) -> dict[str, tuple[float | None, ...]]:
    closes = close_prices(candles)
    lower_values: list[float | None] = []
    middle_values: list[float | None] = []
    upper_values: list[float | None] = []
    for end in range(1, len(closes) + 1):
        lower, middle, upper = price_box(closes[:end], period)
        lower_values.append(lower[-1] if lower else None)
        middle_values.append(middle[-1] if middle else None)
        upper_values.append(upper[-1] if upper else None)
    return {
        "PRICE_BOX_LOWER": tuple(lower_values),
        "PRICE_BOX_MIDDLE": tuple(middle_values),
        "PRICE_BOX_UPPER": tuple(upper_values),
    }


def build_validation_indicator_cache(
    candles: list[dict[str, Any]],
    rules: Mapping[str, Any],
    descriptors: tuple[ValidationFilterDescriptor, ...],
    *,
    entries: list[Any] | None = None,
) -> ValidationIndicatorSeriesCache:
    """Build immutable descriptor-scoped series without re-evaluating signals."""
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    closes = close_prices(candles)
    source_entries = list(entries or [])

    def normalized_values(values: Any) -> tuple[float | None, ...]:
        if not isinstance(values, (list, tuple)) or len(values) != len(candles):
            return ()
        result: list[float | None] = []
        for value in values:
            if value is None:
                result.append(None)
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                result.append(None)
                continue
            result.append(number if math.isfinite(number) else None)
        return tuple(result)

    ma_cache: dict[int, tuple[float | None, ...]] = {}
    rsi_cache: dict[int, tuple[float | None, ...]] = {}
    macd_cache: dict[tuple[int, int, int], dict[str, tuple[float | None, ...]]] = {}
    bollinger_cache: dict[tuple[int, float], dict[str, tuple[float | None, ...]]] = {}
    price_box_cache: dict[int, dict[str, tuple[float | None, ...]]] = {}

    average_values: tuple[float | None, ...] = ()
    if any("AVG_PRICE" in descriptor.series_keys for descriptor in descriptors):
        tracker = ValidationVirtualPositionTracker(
            _mapping(rules.get("validation_execution"))
        )
        projected: list[float | None] = []
        for index in range(len(candles)):
            context = tracker(
                index,
                "SELL",
                candles[: index + 1],
                source_entries,
            )
            projected.append(context.get("average_price"))
        average_values = tuple(projected)

    fill_values = tuple(
        validation_virtual_fill_price(candle)
        for candle in candles
    )
    close_values = normalized_values(closes)

    projected_series: list[
        tuple[str, str, tuple[float | None, ...]]
    ] = []

    for descriptor in descriptors:
        parameters = descriptor.parameters
        channels: dict[str, tuple[float | None, ...]] = {}

        if descriptor.family in {
            FAMILY_MOVING_AVERAGE,
            FAMILY_MA_ARRANGEMENT,
        }:
            for channel in descriptor.series_keys:
                if not channel.startswith("MA") or not channel[2:].isdigit():
                    continue
                period = int(channel[2:])
                if period not in ma_cache:
                    ma_cache[period] = normalized_values(
                        simple_ma(closes, period)
                    )
                channels[channel] = ma_cache[period]

        elif descriptor.family == FAMILY_RSI:
            period = _positive_int(parameters.get("period")) or 14
            if period not in rsi_cache:
                rsi_cache[period] = normalized_values(rsi(closes, period))
            channels["RSI"] = rsi_cache[period]

        elif descriptor.family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
            macd_key = (
                _positive_int(parameters.get("fast")) or 12,
                _positive_int(parameters.get("slow")) or 26,
                _positive_int(parameters.get("signal")) or 9,
            )
            if macd_key not in macd_cache:
                macd_values, signal_values, osc_values = macd_series(
                    closes,
                    macd_key[0],
                    macd_key[1],
                    macd_key[2],
                )
                macd_cache[macd_key] = {
                    "MACD": normalized_values(macd_values),
                    "SIGNAL": normalized_values(signal_values),
                    "OSC": normalized_values(osc_values),
                }
            for channel in descriptor.series_keys:
                if channel in macd_cache[macd_key]:
                    channels[channel] = macd_cache[macd_key][channel]

        elif descriptor.family == FAMILY_BOLLINGER:
            period = _positive_int(parameters.get("period")) or 20
            try:
                std_value = float(parameters.get("std", 2.0))
            except (TypeError, ValueError):
                std_value = 2.0
            bollinger_key = (period, std_value)
            if bollinger_key not in bollinger_cache:
                lower, middle, upper = bollinger_band(
                    closes,
                    period,
                    std_value,
                )
                bollinger_cache[bollinger_key] = {
                    "BOLLINGER_LOWER": normalized_values(lower),
                    "BOLLINGER_MIDDLE": normalized_values(middle),
                    "BOLLINGER_UPPER": normalized_values(upper),
                }
            channels.update(bollinger_cache[bollinger_key])

        elif descriptor.family == FAMILY_PRICE_BOX:
            period = _positive_int(parameters.get("period")) or 24
            if period not in price_box_cache:
                price_box_cache[period] = _price_box_prefix_series(
                    candles,
                    period,
                )
            channels.update(price_box_cache[period])

        elif descriptor.family == FAMILY_PRICE_COMPARISON:
            for channel in descriptor.series_keys:
                if channel == "CLOSE":
                    channels[channel] = close_values
                elif channel == "AVG_PRICE":
                    channels[channel] = average_values
                elif channel == "VIRTUAL_FILL_PRICE":
                    channels[channel] = normalized_values(fill_values)

        for channel in descriptor.series_keys:
            values = channels.get(channel, ())
            if len(values) == len(candles):
                projected_series.append(
                    (descriptor.identity, channel, values)
                )

    return ValidationIndicatorSeriesCache(
        len(candles),
        tuple(projected_series),
    )


def active_filter_identities_for_entry(
    entry: Any,
    rules: Mapping[str, Any],
    descriptors: tuple[ValidationFilterDescriptor, ...],
) -> tuple[str, ...]:
    records = signal_evidence_records_for_entry(entry, rules)
    evidence_keys = {
        key
        for record in records
        for key in record.condition_keys
    }
    return tuple(
        descriptor.identity
        for descriptor in descriptors
        if descriptor.supported
        and evidence_keys.intersection(descriptor.evidence_keys)
    )
