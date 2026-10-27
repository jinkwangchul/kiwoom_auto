# -*- coding: utf-8 -*-
"""Pure visualization data projection for Indicator-Follow Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Callable, Mapping

from engines.indicator_engine import (
    bollinger_band,
    indicator_history_target_bars_from_rules,
    price_box,
    close_prices,
    macd_series_causal_history,
    rsi_causal_history,
    simple_ma,
)
from indicator_follow_signal_validation_execution import (
    ValidationVirtualPositionTracker,
    validation_virtual_fill_price,
)
from indicator_follow_signal_validation_presentation import (
    canonical_validation_condition_key,
    chart_compare_mode_label,
    chart_operand_label,
    chart_operator_label,
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
    FAMILY_BOLLINGER: "볼린저밴드",
    FAMILY_PRICE_BOX: "가격박스",
    FAMILY_PRICE_COMPARISON: "가격비교",
    FAMILY_RSI: "RSI",
    FAMILY_MACD_SIGNAL: "MACD / 시그널선",
    FAMILY_OCR_OSC: "OCR",
}

_LOWER_FAMILIES = {
    FAMILY_RSI,
    FAMILY_MACD_SIGNAL,
    FAMILY_OCR_OSC,
}


def _optional_numpy() -> Any | None:
    try:
        import numpy
    except (ImportError, OSError):
        return None
    return numpy


class ValidationProjectionCancelled(RuntimeError):
    """Raised when an opt-in detached projection is no longer current."""


def _check_projection_cancelled(
    cancel_predicate: Callable[[], bool] | None,
) -> None:
    if cancel_predicate is not None and cancel_predicate():
        raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")


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


def _safe_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series_target(condition: Mapping[str, Any], field: str, period_field: str) -> str:
    target = str(condition.get(field) or "").strip().upper()
    if target == "MA":
        period = _positive_int(condition.get(period_field))
        if period is None and field == "compare_target":
            period = _positive_int(condition.get("period"))
        return f"MA{period}" if period is not None else "MA"
    return target


def _condition_threshold(condition: Mapping[str, Any]) -> float | None:
    return _safe_float(
        condition.get("threshold", condition.get("value"))
    )


def _condition_contract(
    family: str,
    condition: Mapping[str, Any],
) -> dict[str, Any]:
    target = _series_target(condition, "target", "period")
    compare = _series_target(
        condition,
        "compare_target",
        "compare_period",
    )
    if family == FAMILY_RSI and not target:
        target = "RSI"
    if family == FAMILY_OCR_OSC and not target:
        target = "OSC"
    return {
        "target": target,
        "compare_target": compare,
        "operator": str(condition.get("operator") or "").strip().upper(),
        "direction": str(condition.get("direction") or "").strip().upper(),
        "compare_mode": str(condition.get("compare_mode") or "").strip().upper(),
        "threshold": _condition_threshold(condition),
        "value": _safe_float(condition.get("value")),
        "signed_percent_offset": condition.get("signed_percent_offset") is True,
        "period": _positive_int(condition.get("period")),
        "compare_period": _positive_int(condition.get("compare_period")),
    }


def _effective_offset_percent(contract: Mapping[str, Any]) -> float | None:
    value = _safe_float(contract.get("value"))
    if value is None:
        return None
    if contract.get("signed_percent_offset") is True:
        return value
    operator = str(contract.get("operator") or "").strip().upper()
    if operator in {">", ">=", "GT", "GTE", "ABOVE"}:
        return abs(value)
    if operator in {"<", "<=", "LT", "LTE", "BELOW"}:
        return -abs(value)
    return 0.0


def _format_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    if number == 0.0:
        return "0"
    return f"{number:g}"


def _compact_chart_operand_label(
    value: Any,
    *,
    omit_current: bool = False,
) -> str:
    token = str(value or "").strip().upper()
    if omit_current and token in {"CLOSE", "CURRENT_PRICE"}:
        return ""
    if token == "AVG_PRICE":
        return "평단"
    if token == "MACD":
        return "MACD"
    if token == "SIGNAL":
        return "시그널"
    return chart_operand_label(token) if token else ""


def _criterion_label(
    family: str,
    parameters: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str:
    operator_token = str(contract.get("operator") or "").strip().upper()
    operator = chart_operator_label(operator_token)
    threshold = _safe_float(contract.get("threshold"))
    compare = str(contract.get("compare_target") or "").strip().upper()
    target = str(contract.get("target") or "").strip().upper()
    target_label = _compact_chart_operand_label(target, omit_current=True)
    compare_label = _compact_chart_operand_label(compare)

    if family == FAMILY_RSI:
        period = _positive_int(parameters.get("period")) or 14
        return (
            f"RSI({period}) {_format_number(threshold)} {operator}".strip()
            if threshold is not None
            else f"RSI({period})"
        )
    if family == FAMILY_BOLLINGER:
        offset = _effective_offset_percent(contract)
        suffix = "" if offset in (None, 0.0) else f" {offset:+g}%"
        basis = compare_label or "볼린저밴드"
        prefix = f"{target_label} {basis}".strip() if target_label else basis
        return f"{prefix}{suffix} {operator}".strip()
    if family == FAMILY_PRICE_BOX:
        offset = _effective_offset_percent(contract)
        suffix = "" if offset in (None, 0.0) else f" {offset:+g}%"
        basis = compare_label or "가격박스"
        prefix = f"{target_label} {basis}".strip() if target_label else basis
        return f"{prefix}{suffix} {operator}".strip()
    if family == FAMILY_OCR_OSC:
        if threshold is not None and operator_token not in {"TURN_UP", "TURN_DOWN"}:
            return f"OCR {_format_number(threshold)} {operator}".strip()
        return f"OCR {operator}".strip()
    if family == FAMILY_MACD_SIGNAL:
        left = target_label or "MACD"
        if compare:
            return f"{left} {compare_label} {operator}".strip()
        if threshold is not None:
            return f"{left} {_format_number(threshold)} {operator}".strip()
        return left
    if family == FAMILY_PRICE_COMPARISON:
        direction = str(contract.get("direction") or "").strip().upper()
        percent = _safe_float(contract.get("value"))
        left = target_label
        right = compare_label
        operands = f"{left} {right}".strip() if left else right
        if operator_token == "PERCENT_GAP":
            signed = ""
            if percent is not None:
                if direction == "UP":
                    signed = f"+{_format_number(abs(percent))}%"
                elif direction == "DOWN":
                    signed = f"-{_format_number(abs(percent))}%"
                elif direction == "BOTH":
                    signed = f"±{_format_number(abs(percent))}%"
                else:
                    signed = f"{_format_number(percent)}%"
            compare_mode = chart_compare_mode_label(contract.get("compare_mode"))
            return f"{operands} 대비 {signed} {compare_mode}".strip()
        offset = _effective_offset_percent(contract)
        suffix = "" if offset in (None, 0.0) else f" {offset:+g}%"
        return f"{operands}{suffix} {operator}".strip()
    if family == FAMILY_MOVING_AVERAGE:
        right = compare_label or compare
        operands = f"{target_label} {right}".strip() if target_label else right
        return f"{operands} {operator}".strip()
    return _FAMILY_LABELS.get(family, family)


def _condition_series_keys(
    family: str,
    condition: Mapping[str, Any],
) -> tuple[str, ...]:
    contract = _condition_contract(family, condition)
    target = str(contract.get("target") or "")
    compare = str(contract.get("compare_target") or "")
    threshold = _safe_float(contract.get("threshold"))
    operator = str(contract.get("operator") or "").upper()

    if family in {FAMILY_BOLLINGER, FAMILY_PRICE_BOX}:
        return ("CRITERION",) if compare else ()
    if family == FAMILY_RSI:
        return (
            ("RSI", "CRITERION")
            if threshold is not None
            else ("RSI",)
        )
    if family == FAMILY_MACD_SIGNAL:
        values = ["MACD", "SIGNAL"]
        if not compare and threshold is not None:
            values.append("CRITERION")
        return tuple(values)
    if family == FAMILY_OCR_OSC:
        return (
            ("OSC", "CRITERION")
            if threshold is not None and operator not in {"TURN_UP", "TURN_DOWN"}
            else ("OSC",)
        )
    if family in {FAMILY_MOVING_AVERAGE, FAMILY_MA_ARRANGEMENT}:
        values = [
            value
            for value in (target, compare)
            if value.startswith("MA") and value[2:].isdigit()
        ]
        return tuple(dict.fromkeys(values))
    if family == FAMILY_PRICE_COMPARISON:
        values: list[str] = []
        if target == "AVG_PRICE":
            values.append("AVG_PRICE")
        elif target in {"SIGNAL_PRICE", "ORDER_PRICE"}:
            values.append("SIGNAL_PRICE")
        if compare:
            if operator == "PERCENT_GAP" and str(
                contract.get("direction") or ""
            ).upper() == "BOTH":
                values.extend(("CRITERION_LOWER", "CRITERION_UPPER"))
            else:
                values.append("CRITERION")
        return tuple(dict.fromkeys(values))
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
    parameters: dict[str, Any] = {}

    if family == FAMILY_RSI:
        config = _mapping(indicators.get("rsi"))
        parameters["period"] = (
            _positive_int(condition.get("period"))
            or _positive_int(config.get("period"))
            or 14
        )
    elif family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
        config = _mapping(indicators.get("macd"))
        parameters.update({
            "fast": _positive_int(config.get("fast")) or 12,
            "slow": _positive_int(config.get("slow")) or 26,
            "signal": _positive_int(config.get("signal")) or 9,
        })
    elif family == FAMILY_BOLLINGER:
        config = _mapping(indicators.get("bollinger"))
        parameters.update({
            "period": _positive_int(config.get("period")) or 20,
            "std": float(config.get("std") or 2.0),
        })
    elif family == FAMILY_PRICE_BOX:
        config = _mapping(indicators.get("price_box"))
        parameters["period"] = (
            _positive_int(config.get("period")) or 24
        )
    elif family in {FAMILY_MOVING_AVERAGE, FAMILY_MA_ARRANGEMENT}:
        periods = []
        for key in ("period", "compare_period"):
            value = _positive_int(condition.get(key))
            if value is not None:
                periods.append(value)
        for key in _condition_series_keys(family, condition):
            if key.startswith("MA") and key[2:].isdigit():
                periods.append(int(key[2:]))
        parameters["periods"] = tuple(sorted(set(periods)))

    contract = _condition_contract(family, condition)
    if family == FAMILY_RSI and contract.get("period") is None:
        contract["period"] = parameters.get("period")
    parameters["condition"] = contract
    parameters["criterion_label"] = _criterion_label(
        family,
        parameters,
        contract,
    )
    return parameters


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
class ValidationLowerFilterPresentation:
    key: str
    side: str
    source_path: str
    filter_token: str
    pane_family: str
    display_order: tuple[int, ...]
    descriptor_identities: tuple[str, ...]
    component_descriptor_identities: tuple[str, ...]
    component_labels: tuple[str, ...]


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
    groups = side_rules.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping) or group.get("enabled") is False:
                continue
            for condition in group.get("conditions", []):
                key = canonical_validation_condition_key(condition)
                if key:
                    keys.add(key)
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


_SELL_PRESENTATION_PREFIX_FAMILIES = {
    "OCR": FAMILY_OCR_OSC,
    "RSI": FAMILY_RSI,
    "MACD": FAMILY_MACD_SIGNAL,
}


def _sell_presentation_prefix(
    condition: Mapping[str, Any],
) -> tuple[str, str] | None:
    expression_id = str(condition.get("expression_id") or "").strip()
    prefix, separator, suffix = expression_id.rpartition("_")
    normalized = prefix.strip().upper()
    family = _SELL_PRESENTATION_PREFIX_FAMILIES.get(normalized)
    if not separator or not suffix.isdigit() or not normalized or family is None:
        return None
    if _family_for_condition(condition) != family:
        return None
    return normalized, family


def build_validation_lower_filter_presentations(
    rules: Mapping[str, Any],
    descriptors: tuple[ValidationFilterDescriptor, ...],
) -> tuple[ValidationLowerFilterPresentation, ...]:
    """Project lower-pane descriptors onto stable user-visible filter boundaries."""
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")

    lower_descriptors = tuple(
        descriptor
        for descriptor in descriptors
        if descriptor.axis == LOWER_AXIS
    )
    descriptor_by_identity = {
        descriptor.identity: descriptor
        for descriptor in lower_descriptors
    }
    evidence_index: dict[tuple[str, str], tuple[str, ...]] = {}
    for side in ("BUY", "SELL"):
        evidence_to_identities: dict[str, set[str]] = {}
        for descriptor in lower_descriptors:
            if side not in {
                str(value or "").strip().upper()
                for value in descriptor.sides
            }:
                continue
            for evidence_key in descriptor.evidence_keys:
                evidence_to_identities.setdefault(evidence_key, set()).add(
                    descriptor.identity
                )
        for evidence_key, identities in evidence_to_identities.items():
            evidence_index[(side, evidence_key)] = tuple(sorted(identities))

    def component_matches(
        side: str,
        condition: Mapping[str, Any],
        *,
        expected_family: str | None = None,
    ) -> tuple[str, ...]:
        evidence_key = canonical_validation_condition_key(condition)
        if not evidence_key:
            return ()
        identities = evidence_index.get((side, evidence_key), ())
        if expected_family is None:
            return identities
        return tuple(
            identity
            for identity in identities
            if descriptor_by_identity[identity].family == expected_family
        )

    def component_label(
        condition: Mapping[str, Any],
        identity: str,
    ) -> str:
        expression_id = str(condition.get("expression_id") or "").strip()
        if expression_id:
            return expression_id
        parameters = descriptor_by_identity[identity].parameters
        label = str(parameters.get("criterion_label") or "").strip()
        return label or descriptor_by_identity[identity].label

    def make_record(
        *,
        side: str,
        source_path: str,
        filter_token: str,
        display_order: tuple[int, ...],
        components: list[tuple[Mapping[str, Any], tuple[str, ...]]],
        key_suffix: str = "",
    ) -> ValidationLowerFilterPresentation | None:
        component_identities: list[str] = []
        component_labels: list[str] = []
        owned_identities: list[str] = []
        for condition, identities in components:
            for identity in identities:
                component_identities.append(identity)
                component_labels.append(component_label(condition, identity))
                if identity not in owned_identities:
                    owned_identities.append(identity)
        if not owned_identities:
            return None
        families = {
            descriptor_by_identity[identity].family
            for identity in owned_identities
        }
        pane_family = next(iter(families)) if len(families) == 1 else "MIXED"
        key = f"{side}:{source_path}:{filter_token}"
        if key_suffix:
            key = f"{key}:{key_suffix}"
        return ValidationLowerFilterPresentation(
            key=key,
            side=side,
            source_path=source_path,
            filter_token=filter_token,
            pane_family=pane_family,
            display_order=display_order,
            descriptor_identities=tuple(owned_identities),
            component_descriptor_identities=tuple(component_identities),
            component_labels=tuple(component_labels),
        )

    records: list[ValidationLowerFilterPresentation] = []
    represented_identities: set[tuple[str, str]] = set()
    explicit_fallbacks: dict[
        tuple[str, str],
        tuple[str, str, tuple[int, ...], Mapping[str, Any]],
    ] = {}

    buy_source = _visualization_source(rules, "BUY")
    buy_filters = _mapping(buy_source.get("filters"))
    formal_buy_condition_keys: set[str] = set()
    for filter_index, (filter_name, config) in enumerate(buy_filters.items()):
        if filter_name == "composite" or not isinstance(config, Mapping):
            continue
        components = []
        for condition in config.get("conditions", []):
            if not isinstance(condition, Mapping):
                continue
            condition_key = canonical_validation_condition_key(condition)
            if condition_key:
                formal_buy_condition_keys.add(condition_key)
            identities = component_matches("BUY", condition)
            if identities:
                components.append((condition, identities))
        record = make_record(
            side="BUY",
            source_path=f"buy.filters.{filter_name}",
            filter_token=str(filter_name),
            display_order=(0, filter_index),
            components=components,
        )
        if record is not None:
            records.append(record)
            represented_identities.update(
                (record.side, identity)
                for identity in record.descriptor_identities
            )

    buy_groups = buy_source.get("groups")
    if isinstance(buy_groups, list):
        for group_index, group in enumerate(buy_groups):
            if not isinstance(group, Mapping):
                continue
            group_path = f"buy.groups[{group_index}]"
            partitions: dict[
                str,
                list[tuple[Mapping[str, Any], tuple[str, ...]]],
            ] = {}
            partition_order: dict[str, int] = {}
            for condition_index, condition in enumerate(group.get("conditions", [])):
                if not isinstance(condition, Mapping):
                    continue
                condition_key = canonical_validation_condition_key(condition)
                if condition_key and condition_key in formal_buy_condition_keys:
                    continue
                identities_by_family: dict[str, list[str]] = {}
                for identity in component_matches("BUY", condition):
                    family = descriptor_by_identity[identity].family
                    identities_by_family.setdefault(family, []).append(identity)
                for family, identities in identities_by_family.items():
                    partition_order.setdefault(family, condition_index)
                    partitions.setdefault(family, []).append(
                        (condition, tuple(identities))
                    )
            for family in sorted(partitions, key=partition_order.__getitem__):
                record = make_record(
                    side="BUY",
                    source_path=group_path,
                    filter_token=family,
                    display_order=(
                        0,
                        len(buy_filters) + group_index,
                        partition_order[family],
                    ),
                    components=partitions[family],
                )
                if record is not None:
                    records.append(record)
                    represented_identities.update(
                        (record.side, identity)
                        for identity in record.descriptor_identities
                    )

    sell_source = _visualization_source(rules, "SELL")
    sell_signals = _mapping(sell_source.get("signals"))
    signal_rank = {
        "ui_condition_a": 0,
        "ui_condition_b": 1,
        "ui_condition_c": 2,
    }
    ordered_signals = sorted(
        enumerate(sell_signals.items()),
        key=lambda item: (
            signal_rank.get(str(item[1][0]), 3),
            item[0] if str(item[1][0]) not in signal_rank else 0,
        ),
    )
    for signal_order, (_source_index, (signal_name, signal)) in enumerate(
        ordered_signals
    ):
        if not isinstance(signal, Mapping):
            continue
        groups = signal.get("groups")
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                continue
            group_path = f"sell.signals.{signal_name}.groups[{group_index}]"
            partitions: dict[
                str,
                list[tuple[Mapping[str, Any], tuple[str, ...]]],
            ] = {}
            partition_order: dict[str, int] = {}
            for condition_index, condition in enumerate(group.get("conditions", [])):
                if not isinstance(condition, Mapping):
                    continue
                prefix_contract = _sell_presentation_prefix(condition)
                if prefix_contract is None:
                    for identity in component_matches("SELL", condition):
                        explicit_fallbacks.setdefault(
                            ("SELL", identity),
                            (
                                "SELL",
                                f"{group_path}.conditions[{condition_index}]",
                                (1, signal_order, group_index, condition_index),
                                condition,
                            ),
                        )
                    continue
                prefix, family = prefix_contract
                identities = component_matches(
                    "SELL",
                    condition,
                    expected_family=family,
                )
                if not identities:
                    continue
                partition_order.setdefault(prefix, condition_index)
                partitions.setdefault(prefix, []).append((condition, identities))
            for prefix in sorted(partitions, key=partition_order.__getitem__):
                record = make_record(
                    side="SELL",
                    source_path=group_path,
                    filter_token=prefix,
                    display_order=(
                        1,
                        signal_order,
                        group_index,
                        partition_order[prefix],
                    ),
                    components=partitions[prefix],
                )
                if record is not None:
                    records.append(record)
                    represented_identities.update(
                        (record.side, identity)
                        for identity in record.descriptor_identities
                    )

    fallback_candidates = dict(explicit_fallbacks)
    for descriptor in lower_descriptors:
        normalized_sides = tuple(
            str(side or "").strip().upper()
            for side in descriptor.sides
            if str(side or "").strip()
        )
        for side in normalized_sides or ("UNKNOWN",):
            key = (side, descriptor.identity)
            if key in represented_identities or key in fallback_candidates:
                continue
            fallback_candidates[key] = (
                side,
                f"descriptor.{descriptor.identity}",
                (2, 0 if side == "BUY" else 1, 0, 0),
                {},
            )

    for fallback_index, ((coverage_side, identity), candidate) in enumerate(
        sorted(
            fallback_candidates.items(),
            key=lambda item: (item[1][2], item[0]),
        )
    ):
        if (coverage_side, identity) in represented_identities:
            continue
        side, source_path, order, condition = candidate
        descriptor = descriptor_by_identity[identity]
        label = (
            component_label(condition, identity)
            if condition
            else descriptor.label
        )
        records.append(ValidationLowerFilterPresentation(
            key=f"{side}:{source_path}:descriptor:{identity}",
            side=side,
            source_path=source_path,
            filter_token=descriptor.family,
            pane_family=descriptor.family,
            display_order=order + (fallback_index,),
            descriptor_identities=(identity,),
            component_descriptor_identities=(identity,),
            component_labels=(label,),
        ))

    return tuple(sorted(
        records,
        key=lambda record: (record.display_order, record.key),
    ))


def build_validation_filter_universe(
    rules: Mapping[str, Any],
    *,
    include_disabled: bool = False,
) -> tuple[ValidationFilterDescriptor, ...]:
    """Return the BUY∪SELL visualization universe.

    Disabled filters remain excluded by default. include_disabled is
    presentation-only and must not change signal evaluation semantics.
    """
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    actual_by_side = {
        "BUY": _actual_evidence_keys(rules, "BUY"),
        "SELL": _actual_evidence_keys(rules, "SELL"),
    }
    grouped: dict[str, dict[str, Any]] = {}

    def add_condition(side: str, condition: Mapping[str, Any], hint: str = "") -> None:
        if not include_disabled and condition.get("enabled") is False:
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
            if not include_disabled and config.get("enabled") is False:
                continue
            conditions = [
                item for item in config.get("conditions", [])
                if isinstance(item, Mapping)
            ]
            add_conditions(side, conditions, hint=str(filter_name))

        groups = source.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if (
                    not isinstance(group, Mapping)
                    or (not include_disabled and group.get("enabled") is False)
                ):
                    continue
                conditions = [
                    condition
                    for condition in group.get("conditions", [])
                    if isinstance(condition, Mapping)
                ]
                add_conditions(side, conditions)

        signals = _mapping(source.get("signals"))
        for signal in signals.values():
            if (
                not isinstance(signal, Mapping)
                or (not include_disabled and signal.get("enabled") is False)
            ):
                continue
            for group in signal.get("groups", []):
                if (
                    not isinstance(group, Mapping)
                    or (not include_disabled and group.get("enabled") is False)
                ):
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


def _constant_series(
    value: float | None,
    count: int,
) -> tuple[float | None, ...]:
    return tuple(value for _ in range(count))


def _offset_series(
    values: tuple[float | None, ...],
    offset_percent: float | None,
) -> tuple[float | None, ...]:
    if offset_percent is None:
        return tuple(values)
    ratio = 1.0 + offset_percent / 100.0
    return tuple(
        None if value is None else value * ratio
        for value in values
    )


def required_validation_warmup_bars(
    rules: Mapping[str, Any],
) -> int:
    """Return the largest configured indicator lookback used by Validation V2."""
    descriptors = build_validation_filter_universe(rules)
    required = 0
    for descriptor in descriptors:
        parameters = descriptor.parameters
        if descriptor.family in {FAMILY_MOVING_AVERAGE, FAMILY_MA_ARRANGEMENT}:
            periods = parameters.get("periods")
            if isinstance(periods, (list, tuple)):
                for value in periods:
                    period = _positive_int(value)
                    if period is not None:
                        required = max(required, period)
        elif descriptor.family in {FAMILY_RSI, FAMILY_BOLLINGER, FAMILY_PRICE_BOX}:
            period = _positive_int(parameters.get("period"))
            if period is not None:
                required = max(required, period)
        elif descriptor.family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
            slow = _positive_int(parameters.get("slow")) or 26
            signal = _positive_int(parameters.get("signal")) or 9
            required = max(required, slow + signal)
    return required


def indicator_transition_indexes(
    values: tuple[float | None, ...] | list[float | None],
    operator: str,
) -> tuple[int, ...]:
    """Return chart indexes for confirmed turn/zero-cross visualization.

    TURN_* conditions are displayed on their first confirmed bar. ZERO_CROSS_*
    belongs to the bar that actually crosses zero.
    """
    normalized = str(operator or "").strip().upper()
    if normalized not in {
        "TURN_UP",
        "TURN_DOWN",
        "ZERO_CROSS_UP",
        "ZERO_CROSS_DOWN",
    }:
        return ()

    result: list[int] = []
    if normalized in {"TURN_UP", "TURN_DOWN"}:
        for index in range(2, len(values)):
            prev2 = _safe_float(values[index - 2])
            prev = _safe_float(values[index - 1])
            current = _safe_float(values[index])
            if prev2 is None or prev is None or current is None:
                continue
            if normalized == "TURN_UP":
                matched = prev2 > prev and current > prev
            else:
                matched = prev2 < prev and current < prev
            if matched:
                result.append(index)
        return tuple(result)

    for index in range(1, len(values)):
        prev = _safe_float(values[index - 1])
        current = _safe_float(values[index])
        if prev is None or current is None:
            continue
        if normalized == "ZERO_CROSS_UP":
            matched = prev <= 0 and current > 0
        else:
            matched = prev >= 0 and current < 0
        if matched:
            result.append(index)
    return tuple(result)

def _display_rsi_causal_history(
    closes: list[float | None],
    period: int,
    history_window: int,
) -> list[float | None]:
    """Exact per-window RSI tails with precomputed price changes for chart display."""
    if history_window <= 0 or period <= 0:
        return rsi_causal_history(closes, period, history_window)
    window = max(int(history_window), int(period) + 1)
    if (len(closes) <= window
            or any(value is None or not math.isfinite(value) for value in closes)):
        return rsi_causal_history(closes, period, history_window)

    numpy = _optional_numpy()
    if numpy is not None and all(type(value) is float for value in closes):
        values = rsi_causal_history(closes[:window], period, window)
        gains = [0.0] * len(closes)
        losses = [0.0] * len(closes)
        for index in range(1, len(closes)):
            change = closes[index] - closes[index - 1]
            gains[index] = max(change, 0.0)
            losses[index] = max(-change, 0.0)
        gains_array = numpy.asarray(gains, dtype=numpy.float64)
        losses_array = numpy.asarray(losses, dtype=numpy.float64)
        starts = numpy.arange(1, len(closes) - window + 1, dtype=numpy.int32)
        avg_gain = numpy.asarray([
            sum(gains[start + 1:start + period + 1]) / period
            for start in starts
        ], dtype=numpy.float64)
        avg_loss = numpy.asarray([
            sum(losses[start + 1:start + period + 1]) / period
            for start in starts
        ], dtype=numpy.float64)
        for index in range(period + 1, window):
            avg_gain = ((avg_gain * (period - 1)) + gains_array[starts + index]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses_array[starts + index]) / period
        for gain, loss in zip(avg_gain, avg_loss):
            gain = float(gain)
            loss = float(loss)
            if gain == 0 and loss == 0:
                values.append(0.0)
            elif loss == 0:
                values.append(100.0)
            else:
                values.append(100 - (100 / (1 + gain / loss)))
        return values

    # Keep the canonical prefix and Wilder recurrence, including both zero cases.
    values = rsi_causal_history(closes[:window], period, window)
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    for index in range(window, len(closes)):
        start = index - window + 1
        avg_gain = sum(gains[start + 1:start + period + 1]) / period
        avg_loss = sum(losses[start + 1:start + period + 1]) / period
        for gain, loss in zip(gains[start + period + 1:index + 1], losses[start + period + 1:index + 1]):
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_gain == 0 and avg_loss == 0:
            values.append(0.0)
        elif avg_loss == 0:
            values.append(100.0)
        else:
            values.append(100 - (100 / (1 + avg_gain / avg_loss)))
    return values


def _display_macd_causal_history(
    closes: list[float | None],
    fast: int,
    slow: int,
    signal_period: int,
    history_window: int,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Exact MACD window tails for dense chart data; keep canonical engine untouched."""
    window = max(int(history_window), int(slow) + int(signal_period))
    seed = max(int(fast), int(slow))
    if (
        min(fast, slow, signal_period) <= 0
        or len(closes) <= window + 16
        or window < max(seed, int(signal_period))
        or any(value is None or not math.isfinite(value) for value in closes)
    ):
        return macd_series_causal_history(
            closes, fast, slow, signal_period, history_window,
        )

    numpy = _optional_numpy()
    if numpy is not None and all(type(value) is float for value in closes):
        macd_values, signal_values, osc_values = macd_series_causal_history(
            closes[:window], fast, slow, signal_period, window,
        )
        count = len(closes) - window
        close_values = numpy.asarray(closes, dtype=numpy.float64)
        fast_state = close_values[1:1 + count].copy()
        slow_state = fast_state.copy()
        fast_prefix = [fast_state.copy()]
        slow_prefix = [slow_state.copy()]
        fast_multiplier = 2 / (fast + 1)
        slow_multiplier = 2 / (slow + 1)
        signal_multiplier = 2 / (signal_period + 1)
        for index in range(1, seed):
            value = close_values[1 + index:1 + index + count]
            fast_state = (value - fast_state) * fast_multiplier + fast_state
            slow_state = (value - slow_state) * slow_multiplier + slow_state
            fast_prefix.append(fast_state)
            slow_prefix.append(slow_state)
        fast_seed = fast_prefix[fast - 1]
        slow_seed = slow_prefix[slow - 1]
        signal_state = None
        for position in range(seed):
            macd_value = (
                (fast_prefix[position] if position >= fast else fast_seed)
                - (slow_prefix[position] if position >= slow else slow_seed)
            )
            signal_state = (
                macd_value if signal_state is None
                else (macd_value - signal_state) * signal_multiplier + signal_state
            )
        for index in range(seed, window):
            value = close_values[1 + index:1 + index + count]
            fast_state = (value - fast_state) * fast_multiplier + fast_state
            slow_state = (value - slow_state) * slow_multiplier + slow_state
            macd_value = fast_state - slow_state
            signal_state = (macd_value - signal_state) * signal_multiplier + signal_state
        osc_values.extend(map(float, macd_value - signal_state))
        macd_values.extend(map(float, macd_value))
        signal_values.extend(map(float, signal_state))
        return macd_values, signal_values, osc_values

    # Reuse the canonical prefix (including Hero4 EAVG backfill semantics).
    macd_values, signal_values, osc_values = macd_series_causal_history(
        closes[:window], fast, slow, signal_period, window,
    )
    fast_multiplier = 2 / (fast + 1)
    slow_multiplier = 2 / (slow + 1)
    signal_multiplier = 2 / (signal_period + 1)

    for index in range(window, len(closes)):
        local = closes[index - window + 1:index + 1]
        fast_state = float(local[0])
        slow_state = float(local[0])
        fast_prefix = [fast_state]
        slow_prefix = [slow_state]
        for close in local[1:seed]:
            value = float(close)
            fast_state = (value - fast_state) * fast_multiplier + fast_state
            slow_state = (value - slow_state) * slow_multiplier + slow_state
            fast_prefix.append(fast_state)
            slow_prefix.append(slow_state)

        fast_seed = fast_prefix[fast - 1]
        slow_seed = slow_prefix[slow - 1]
        signal_state = None
        for position in range(seed):
            macd_value = (
                (fast_prefix[position] if position >= fast else fast_seed)
                - (slow_prefix[position] if position >= slow else slow_seed)
            )
            signal_state = (
                macd_value if signal_state is None
                else (macd_value - signal_state) * signal_multiplier + signal_state
            )
        for close in local[seed:]:
            value = float(close)
            fast_state = (value - fast_state) * fast_multiplier + fast_state
            slow_state = (value - slow_state) * slow_multiplier + slow_state
            macd_value = fast_state - slow_state
            signal_state = (
                (macd_value - signal_state) * signal_multiplier + signal_state
            )

        macd_values.append(macd_value)
        signal_values.append(signal_state)
        osc_values.append(macd_value - signal_state)
    return macd_values, signal_values, osc_values


def build_validation_indicator_cache(
    candles: list[dict[str, Any]],
    rules: Mapping[str, Any],
    descriptors: tuple[ValidationFilterDescriptor, ...],
    *,
    entries: list[Any] | None = None,
    cancel_predicate: Callable[[], bool] | None = None,
) -> ValidationIndicatorSeriesCache:
    """Build immutable descriptor-scoped series without re-evaluating signals."""
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    _check_projection_cancelled(cancel_predicate)
    closes = close_prices(candles)
    _check_projection_cancelled(cancel_predicate)
    history_window = indicator_history_target_bars_from_rules(rules)
    _check_projection_cancelled(cancel_predicate)
    source_entries = list(entries or [])

    def normalized_values(values: Any) -> tuple[float | None, ...]:
        if not isinstance(values, (list, tuple)) or len(values) != len(candles):
            return ()
        result: list[float | None] = []
        for value in values:
            _check_projection_cancelled(cancel_predicate)
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
    needs_average_price = any(
        "AVG_PRICE" in descriptor.series_keys
        or str(
            _mapping(descriptor.parameters.get("condition")).get("target")
            or ""
        ).upper() == "AVG_PRICE"
        or str(
            _mapping(descriptor.parameters.get("condition")).get("compare_target")
            or ""
        ).upper() == "AVG_PRICE"
        for descriptor in descriptors
    )
    if needs_average_price:
        tracker = ValidationVirtualPositionTracker(
            _mapping(rules.get("validation_execution"))
        )
        projected: list[float | None] = []
        for index in range(len(candles)):
            _check_projection_cancelled(cancel_predicate)
            context = tracker.context_for_fast(
                index,
                "SELL",
                candles,
                source_entries,
            )
            _check_projection_cancelled(cancel_predicate)
            projected.append(context.get("average_price"))
        average_values = tuple(projected)

    fill_values_list = []
    for candle in candles:
        _check_projection_cancelled(cancel_predicate)
        fill_values_list.append(validation_virtual_fill_price(candle))
    fill_values = tuple(fill_values_list)
    close_values = normalized_values(closes)

    projected_series: list[
        tuple[str, str, tuple[float | None, ...]]
    ] = []

    for descriptor in descriptors:
        _check_projection_cancelled(cancel_predicate)
        parameters = descriptor.parameters
        condition = _mapping(parameters.get("condition"))
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
                    _check_projection_cancelled(cancel_predicate)
                    ma_cache[period] = normalized_values(
                        simple_ma(closes, period)
                    )
                    _check_projection_cancelled(cancel_predicate)
                channels[channel] = ma_cache[period]

        elif descriptor.family == FAMILY_RSI:
            period = _positive_int(parameters.get("period")) or 14
            if period not in rsi_cache:
                _check_projection_cancelled(cancel_predicate)
                rsi_cache[period] = normalized_values(
                    _display_rsi_causal_history(
                        closes,
                        period,
                        history_window,
                    )
                )
                _check_projection_cancelled(cancel_predicate)
            channels["RSI"] = rsi_cache[period]
            if "CRITERION" in descriptor.series_keys:
                channels["CRITERION"] = _constant_series(
                    _safe_float(condition.get("threshold")),
                    len(candles),
                )

        elif descriptor.family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
            macd_key = (
                _positive_int(parameters.get("fast")) or 12,
                _positive_int(parameters.get("slow")) or 26,
                _positive_int(parameters.get("signal")) or 9,
            )
            if macd_key not in macd_cache:
                _check_projection_cancelled(cancel_predicate)
                macd_values, signal_values, osc_values = _display_macd_causal_history(
                    closes,
                    macd_key[0],
                    macd_key[1],
                    macd_key[2],
                    history_window,
                )
                _check_projection_cancelled(cancel_predicate)
                macd_cache[macd_key] = {
                    "MACD": normalized_values(macd_values),
                    "SIGNAL": normalized_values(signal_values),
                    "OSC": normalized_values(osc_values),
                }
            for channel in descriptor.series_keys:
                if channel in macd_cache[macd_key]:
                    channels[channel] = macd_cache[macd_key][channel]
            if "CRITERION" in descriptor.series_keys:
                channels["CRITERION"] = _constant_series(
                    _safe_float(condition.get("threshold")),
                    len(candles),
                )

        elif descriptor.family == FAMILY_BOLLINGER:
            period = _positive_int(parameters.get("period")) or 20
            try:
                std_value = float(parameters.get("std", 2.0))
            except (TypeError, ValueError):
                std_value = 2.0
            bollinger_key = (period, std_value)
            if bollinger_key not in bollinger_cache:
                _check_projection_cancelled(cancel_predicate)
                lower, middle, upper = bollinger_band(
                    closes,
                    period,
                    std_value,
                )
                _check_projection_cancelled(cancel_predicate)
                bollinger_cache[bollinger_key] = {
                    "BOLLINGER_LOWER": normalized_values(lower),
                    "BOLLINGER_MIDDLE": normalized_values(middle),
                    "BOLLINGER_UPPER": normalized_values(upper),
                }
            compare_target = str(
                condition.get("compare_target") or ""
            ).upper()
            base_values = bollinger_cache[bollinger_key].get(
                compare_target,
                (),
            )
            if len(base_values) == len(candles):
                channels["CRITERION"] = _offset_series(
                    base_values,
                    _effective_offset_percent(condition),
                )

        elif descriptor.family == FAMILY_PRICE_BOX:
            period = _positive_int(parameters.get("period")) or 24
            if period not in price_box_cache:
                _check_projection_cancelled(cancel_predicate)
                lower, middle, upper = price_box(
                    closes,
                    period,
                    history_window=history_window,
                )
                _check_projection_cancelled(cancel_predicate)
                price_box_cache[period] = {
                    "PRICE_BOX_LOWER": normalized_values(lower),
                    "PRICE_BOX_MIDDLE": normalized_values(middle),
                    "PRICE_BOX_UPPER": normalized_values(upper),
                }
            compare_target = str(
                condition.get("compare_target") or ""
            ).upper()
            base_values = price_box_cache[period].get(
                compare_target,
                (),
            )
            if len(base_values) == len(candles):
                channels["CRITERION"] = _offset_series(
                    base_values,
                    _effective_offset_percent(condition),
                )

        elif descriptor.family == FAMILY_PRICE_COMPARISON:
            target = str(condition.get("target") or "").upper()
            compare_target = str(
                condition.get("compare_target") or ""
            ).upper()

            if target == "AVG_PRICE":
                channels["AVG_PRICE"] = average_values
            elif target in {"SIGNAL_PRICE", "ORDER_PRICE"}:
                channels["SIGNAL_PRICE"] = close_values

            if compare_target == "AVG_PRICE":
                base_values = average_values
            elif compare_target in {"SIGNAL_PRICE", "ORDER_PRICE"}:
                base_values = close_values
            elif compare_target in {"CLOSE", "CURRENT_PRICE"}:
                base_values = close_values
            else:
                base_values = ()

            if len(base_values) == len(candles):
                operator = str(
                    condition.get("operator") or ""
                ).upper()
                if operator == "PERCENT_GAP":
                    percent = _safe_float(condition.get("value"))
                    direction = str(
                        condition.get("direction") or ""
                    ).upper()
                    if percent is not None:
                        if direction == "BOTH":
                            channels["CRITERION_LOWER"] = _offset_series(
                                base_values,
                                -abs(percent),
                            )
                            channels["CRITERION_UPPER"] = _offset_series(
                                base_values,
                                abs(percent),
                            )
                        elif direction == "UP":
                            channels["CRITERION"] = _offset_series(
                                base_values,
                                abs(percent),
                            )
                        elif direction == "DOWN":
                            channels["CRITERION"] = _offset_series(
                                base_values,
                                -abs(percent),
                            )
                else:
                    channels["CRITERION"] = _offset_series(
                        base_values,
                        _effective_offset_percent(condition),
                    )

        for channel in descriptor.series_keys:
            _check_projection_cancelled(cancel_predicate)
            values = channels.get(channel, ())
            if len(values) == len(candles):
                projected_series.append(
                    (descriptor.identity, channel, values)
                )

    _check_projection_cancelled(cancel_predicate)
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
