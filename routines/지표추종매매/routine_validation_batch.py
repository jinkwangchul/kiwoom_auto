# -*- coding: utf-8 -*-
"""Validation-only compiled batch scan for indicator-follow rules.

The fast path predicts signal indexes from whole-history value/boolean series.
It invokes the authoritative routine evaluator only for predicted emissions so
the canonical RoutineSignal and decision trace remain the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterator, Mapping

from engines.condition_engine import _series_key
from engines.indicator_engine import close_prices, rsi
from engines.signal_result import RoutineSignal
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
    validation_virtual_fill_price,
)

from .routine_macd_engine import (
    BUY_FILTER_ORDER,
    _buy_composite_filter_config,
    _buy_filter_config_map,
    _buy_ocr_filter_config,
    _condition_sell_signals,
    _enrich_price_compare_series,
    _evaluate_profit_rate_sell,
    _is_buy_expression_mode,
    _logic,
    _macd_sell_section,
    _normalize_rsi_operator,
    _profit_rate_sell_section,
    build_indicator_follow_base_series,
    evaluate_indicator_follow_routine,
)
_SUPPORTED_CONDITION_OPERATORS = {
    "TURN_UP", "TURN_DOWN", "TREND_UP", "TREND_DOWN",
    "CROSS_UP", "CROSS_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN",
    "PERCENT_GAP", ">", ">=", "<", "<=", "=", "==",
    "GT", "GTE", "LT", "LTE", "EQ", "ABOVE", "BELOW",
}
_DYNAMIC_SERIES = {"AVG_PRICE", "ORDER_PRICE"}


@dataclass(frozen=True, slots=True)
class ValidationBatchSignalRecord:
    evaluation_side: str
    evaluation_index: int
    evaluation_time: str
    routine_signal: RoutineSignal
    trace: dict[str, Any]
    context: dict[str, Any]

    @property
    def signal(self) -> str | None:
        return self.routine_signal.signal


@dataclass(frozen=True, slots=True)
class ValidationBatchScanResult:
    supported: bool
    records: tuple[ValidationBatchSignalRecord, ...] = ()
    fallback_reason: str | None = None


class _Unsupported(Exception):
    pass


class _DirectTraceObserver:
    """Collect authoritative callbacks once; evaluator payloads are detached."""

    def __init__(self) -> None:
        self.conditions: list[Any] = []
        self.groups: list[Any] = []
        self.aggregations: list[dict[str, Any]] = []

    def observe_condition(self, payload: Any) -> None:
        self.conditions.append(payload)

    def observe_group(self, payload: Any) -> None:
        self.groups.append(payload)

    def observe_aggregation(self, side: Any, payload: Any) -> None:
        self.aggregations.append({"side": side, "payload": payload})

    def snapshot(self) -> dict[str, list[Any]]:
        return {
            "conditions": self.conditions,
            "groups": self.groups,
            "aggregations": self.aggregations,
        }


class _CandlePrefixView(list):
    """List-compatible immutable prefix without copying candle references."""

    def __init__(self, source: list[dict[str, Any]], length: int) -> None:
        super().__init__()
        self._source = source
        self._length = max(0, min(int(length), len(source)))

    def __len__(self) -> int:
        return self._length

    def __bool__(self) -> bool:
        return self._length > 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(self._length):
            yield self._source[index]

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self._source[position] for position in range(start, stop, step)]
        position = int(index)
        if position < 0:
            position += self._length
        if not 0 <= position < self._length:
            raise IndexError(position)
        return self._source[position]


class _PriceBoxPrefixSeries(list):
    def __init__(self, middle: list[float | None], offset: float | None, length: int) -> None:
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
        middle = self._middle[position]
        return None if middle is None or self._offset is None else float(middle) + self._offset


class _IncrementalAverageContext:
    """O(N) equivalent of build_validation_average_price_context."""

    validation_read_only_fast_path = True

    def __init__(self) -> None:
        self._processed_through = -1
        self._indexed_entry_count = 0
        self._signals_by_index: dict[int, set[str]] = {}
        self._fill_prices: list[float] = []
        self._fill_price_sum = 0.0
        self._buy_indexes: list[int] = []
        self._average_series: list[float | None] = []

    def _index_entries(self, candles: list[dict[str, Any]], entries: list[Any]) -> None:
        for entry in entries[min(self._indexed_entry_count, len(entries)):]:
            index = getattr(entry, "evaluation_index", None)
            signal = str(getattr(entry, "signal", "") or "").upper()
            if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candles) and signal in {"BUY", "SELL"}:
                self._signals_by_index.setdefault(index, set()).add(signal)
        self._indexed_entry_count = len(entries)

    def context_for_fast(self, evaluation_index: int, side: str, candles: list[dict[str, Any]], prior_entries: list[Any]) -> dict[str, Any]:
        self._index_entries(candles, prior_entries)
        while self._processed_through < evaluation_index - 1:
            index = self._processed_through + 1
            if len(self._average_series) <= index:
                self._average_series.append(
                    self._fill_price_sum / len(self._fill_prices)
                    if self._fill_prices
                    else None
                )
            signals = self._signals_by_index.get(index, set())
            if "SELL" in signals:
                self._fill_prices.clear()
                self._fill_price_sum = 0.0
                self._buy_indexes.clear()
            elif "BUY" in signals:
                fill_price = validation_virtual_fill_price(candles[index])
                if fill_price is not None:
                    self._fill_prices.append(fill_price)
                    self._fill_price_sum += fill_price
                    self._buy_indexes.append(index)
            self._processed_through = index
        if len(self._average_series) <= evaluation_index:
            self._average_series.append(
                self._fill_price_sum / len(self._fill_prices) if self._fill_prices else None
            )
        average = self._average_series[evaluation_index]
        return {
            "average_price_series": self._average_series,
            "average_price": average,
            "validation_trace_context": {
                "side": str(side or "").upper(),
                "evaluation_index": evaluation_index,
                "estimated_average_price": average,
                "contributing_buy_indexes": list(self._buy_indexes),
                "average_source": "VALIDATION_BUY_OHLC4_VIRTUAL_FILL_SEGMENT",
            },
            "_indicator_follow_average_price_series_normalized": True,
        }


@dataclass(frozen=True, slots=True)
class _CompiledCondition:
    config: dict[str, Any]
    values: tuple[bool, ...] | None


@dataclass(frozen=True, slots=True)
class _CompiledGroup:
    enabled: bool
    logic: str
    expression: Any
    conditions: tuple[_CompiledCondition, ...]


@dataclass(frozen=True, slots=True)
class _CompiledGroups:
    delay: int
    groups: tuple[_CompiledGroup, ...]
    values: tuple[bool, ...] | None


@dataclass(frozen=True, slots=True)
class _CompiledSellSignal:
    name: str
    enabled: bool
    groups: _CompiledGroups
    expression: Any


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return abs(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None


def _price_box_prefix_offsets(series_map: Mapping[str, list[float | None]]) -> tuple[list[float | None], list[float | None]]:
    closes = series_map.get("CLOSE")
    middle = series_map.get("PRICE_BOX_MIDDLE")
    if not isinstance(closes, list) or not isinstance(middle, list) or len(closes) != len(middle):
        return [], []
    positive_count = negative_count = 0
    positive_sum = positive_sum_sq = negative_sum = negative_sum_sq = 0.0
    lower: list[float | None] = []
    upper: list[float | None] = []
    for close_value, middle_value in zip(closes, middle):
        close_number, middle_number = _number(close_value), _number(middle_value)
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
            upper.append(mean + 2.0 * math.sqrt(max(0.0, positive_sum_sq / positive_count - mean * mean)))
        else:
            upper.append(None)
        if negative_count:
            mean = negative_sum / negative_count
            lower.append(mean - 2.0 * math.sqrt(max(0.0, negative_sum_sq / negative_count - mean * mean)))
        else:
            lower.append(None)
    return lower, upper


def _evaluation_series_maps(base_series: dict[str, list[float | None]]) -> tuple[dict[str, list[float | None]], ...]:
    length = len(base_series.get("CLOSE", []))
    lower, upper = _price_box_prefix_offsets(base_series)
    middle = base_series.get("PRICE_BOX_MIDDLE")
    result: list[dict[str, list[float | None]]] = []
    for index in range(length):
        if isinstance(middle, list) and index < len(lower) and index < len(upper):
            current = dict(base_series)
            current["PRICE_BOX_LOWER"] = _PriceBoxPrefixSeries(middle, lower[index], index + 1)
            current["PRICE_BOX_UPPER"] = _PriceBoxPrefixSeries(middle, upper[index], index + 1)
            result.append(current)
        else:
            result.append(base_series)
    return tuple(result)


def _condition_is_dynamic(condition: dict[str, Any]) -> bool:
    return _series_key(condition) in _DYNAMIC_SERIES or _series_key(condition, "compare_target") in _DYNAMIC_SERIES


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _at(series: Any, index: int) -> float | None:
    if not series or index < 0 or index >= len(series):
        return None
    return series[index]


def _compare(left: float, operator: str, right: float) -> bool:
    if operator in {">", "GT", "ABOVE"}:
        return left > right
    if operator in {">=", "GTE"}:
        return left >= right
    if operator in {"<", "LT", "BELOW"}:
        return left < right
    if operator in {"<=", "LTE"}:
        return left <= right
    return left == right if operator in {"=", "==", "EQ"} else False


def _condition_bool(condition: dict[str, Any], series_map: dict[str, list[float | None]], index: int) -> bool:
    """Allocation-free equivalent of the canonical condition decision."""
    if not condition.get("enabled", True):
        return True
    try:
        bar_offset = int(condition.get("bar_offset", 0))
    except (TypeError, ValueError):
        return False
    if bar_offset < 0:
        return False
    target = _series_key(condition)
    operator = str(condition.get("operator") or "").strip().upper()
    series = series_map.get(target)
    base_index = len(series) + index if series and index < 0 else index
    effective = base_index - bar_offset
    current, previous, previous2 = _at(series, effective), _at(series, effective - 1), _at(series, effective - 2)
    if operator == "TURN_UP":
        passed = previous2 is not None and previous is not None and current is not None and previous2 > previous and current > previous
    elif operator == "TURN_DOWN":
        passed = previous2 is not None and previous is not None and current is not None and previous2 < previous and current < previous
    elif operator == "TREND_UP":
        passed = previous is not None and current is not None and current > previous
    elif operator == "TREND_DOWN":
        passed = previous is not None and current is not None and current < previous
    elif operator in {"CROSS_UP", "CROSS_DOWN"}:
        compare = series_map.get(_series_key(condition, "compare_target"))
        compare_current, compare_previous = _at(compare, effective), _at(compare, effective - 1)
        passed = False
        if previous is not None and current is not None and compare_previous is not None and compare_current is not None:
            passed = previous <= compare_previous and current > compare_current if operator == "CROSS_UP" else previous >= compare_previous and current < compare_current
    elif operator in {"ZERO_CROSS_UP", "ZERO_CROSS_DOWN"}:
        passed = False if previous is None or current is None else (
            previous <= 0 and current > 0 if operator == "ZERO_CROSS_UP" else previous >= 0 and current < 0
        )
    elif operator == "PERCENT_GAP":
        compare = _at(series_map.get(_series_key(condition, "compare_target")), effective)
        percent = _float(condition.get("value"))
        direction = str(condition.get("direction") or "").strip().upper()
        mode = str(condition.get("compare_mode") or "").strip().upper()
        passed = False
        if current is not None and compare is not None and compare > 0 and percent is not None and percent >= 0:
            lower, upper = compare * (1 - percent / 100.0), compare * (1 + percent / 100.0)
            if direction == "UP":
                passed = current >= upper if mode == "GTE" else current <= upper if mode == "LTE" else False
            elif direction == "DOWN":
                passed = current >= lower if mode == "GTE" else current <= lower if mode == "LTE" else False
            elif direction == "BOTH":
                passed = lower <= current <= upper if mode == "WITHIN" else (current < lower or current > upper) if mode == "OUTSIDE" else False
    else:
        right = _float(condition.get("value"))
        if condition.get("compare_target"):
            right = _at(series_map.get(_series_key(condition, "compare_target")), effective)
            offset = _float(condition.get("value"))
            if right is not None and offset is not None:
                if condition.get("signed_percent_offset") is True:
                    right *= 1 + offset / 100.0
                else:
                    ratio = abs(offset) / 100.0
                    if operator in {">", ">=", "GT", "GTE", "ABOVE"}:
                        right *= 1 + ratio
                    elif operator in {"<", "<=", "LT", "LTE", "BELOW"}:
                        right *= 1 - ratio
        passed = current is not None and right is not None and _compare(current, operator, right)
    return not passed if bool(condition.get("not", False)) else passed


def _expression_bool(node: Any, values: dict[str, bool]) -> bool:
    if not isinstance(node, dict):
        raise _Unsupported("BATCH_EXPRESSION_UNSUPPORTED")
    if str(node.get("type") or "").strip().lower() == "identifier":
        name = str(node.get("name") or "").strip().upper()
        if name not in values:
            raise _Unsupported("BATCH_EXPRESSION_UNSUPPORTED")
        return values[name]
    if str(node.get("type") or "").strip().lower() != "binary":
        raise _Unsupported("BATCH_EXPRESSION_UNSUPPORTED")
    operator = str(node.get("operator") or "").strip().upper()
    left = _expression_bool(node.get("left"), values)
    right = _expression_bool(node.get("right"), values)
    if operator == "AND":
        return left and right
    if operator == "OR":
        return left or right
    if operator == "NOT":
        return left and not right
    raise _Unsupported("BATCH_EXPRESSION_UNSUPPORTED")


def _validate_condition(condition: dict[str, Any], base_series: dict[str, list[float | None]]) -> None:
    if not bool(condition.get("enabled", True)):
        return
    operator = str(condition.get("operator") or "").strip().upper()
    if operator not in _SUPPORTED_CONDITION_OPERATORS:
        raise _Unsupported("BATCH_CONDITION_UNSUPPORTED")
    target = _series_key(condition)
    if target not in base_series and target not in _DYNAMIC_SERIES:
        raise _Unsupported("BATCH_CONDITION_UNSUPPORTED")
    if operator in {"CROSS_UP", "CROSS_DOWN", "PERCENT_GAP"} or condition.get("compare_target"):
        compare = _series_key(condition, "compare_target")
        if compare not in base_series and compare not in _DYNAMIC_SERIES:
            raise _Unsupported("BATCH_CONDITION_UNSUPPORTED")


def _compile_groups(groups: Any, delay: int, evaluation_maps: tuple[dict[str, list[float | None]], ...], base_series: dict[str, list[float | None]]) -> _CompiledGroups:
    if not isinstance(groups, list):
        groups = []
    compiled_groups: list[_CompiledGroup] = []
    any_dynamic = False
    for group in groups:
        if not isinstance(group, dict):
            raise _Unsupported("BATCH_GROUP_UNSUPPORTED")
        enabled = bool(group.get("enabled", True))
        raw_conditions = group.get("conditions", [])
        if not isinstance(raw_conditions, list):
            raw_conditions = []
        conditions: list[_CompiledCondition] = []
        seen_ids: set[str] = set()
        for condition_index, condition in enumerate(raw_conditions):
            if not isinstance(condition, dict):
                raise _Unsupported("BATCH_CONDITION_UNSUPPORTED")
            _validate_condition(condition, base_series)
            expression_id = str(condition.get("expression_id") or f"C{condition_index}").strip().upper()
            if expression_id in seen_ids:
                raise _Unsupported("BATCH_EXPRESSION_UNSUPPORTED")
            seen_ids.add(expression_id)
            if _condition_is_dynamic(condition):
                values = None
                any_dynamic = True
            else:
                values = tuple(
                    _condition_bool(condition, evaluation_maps[evaluation_index], evaluation_index - delay)
                    for evaluation_index in range(len(evaluation_maps))
                )
            conditions.append(_CompiledCondition(condition, values))
        compiled_groups.append(_CompiledGroup(
            enabled,
            str(group.get("conditions_logic", group.get("logic", "AND")) or "").strip().upper(),
            group.get("condition_expression"),
            tuple(conditions),
        ))
    compiled = _CompiledGroups(delay, tuple(compiled_groups), None)
    if not any_dynamic:
        compiled = _CompiledGroups(
            delay,
            tuple(compiled_groups),
            tuple(_evaluate_compiled_groups(compiled, index, evaluation_maps[index]) for index in range(len(evaluation_maps))),
        )
    return compiled


def _evaluate_compiled_groups(compiled: _CompiledGroups, evaluation_index: int, series_map: dict[str, list[float | None]]) -> bool:
    if compiled.values is not None:
        return compiled.values[evaluation_index]
    signal_index = evaluation_index - compiled.delay
    for group in compiled.groups:
        if not group.enabled or not group.conditions:
            continue
        condition_values: list[bool] = []
        expression_values: dict[str, bool] = {}
        for condition_index, condition in enumerate(group.conditions):
            passed = (
                condition.values[evaluation_index]
                if condition.values is not None
                else _condition_bool(condition.config, series_map, signal_index)
            )
            condition_values.append(passed)
            expression_id = str(condition.config.get("expression_id") or f"C{condition_index}").strip().upper()
            expression_values[expression_id] = passed
        if group.expression is not None:
            group_passed = _expression_bool(group.expression, expression_values)
        else:
            group_passed = any(condition_values) if group.logic == "OR" else all(condition_values)
        if group_passed:
            return True
    return False


def _buy_rsi_period(rules: Mapping[str, Any]) -> int | None:
    buy = rules.get("buy")
    filters = buy.get("filters") if isinstance(buy, dict) else None
    cfg = filters.get("rsi") if isinstance(filters, dict) else None
    if not isinstance(cfg, dict):
        return None
    conditions = cfg.get("conditions")
    condition = next((item for item in conditions if isinstance(item, dict)), cfg) if isinstance(conditions, list) else cfg
    indicators = rules.get("indicators")
    indicator_rsi = indicators.get("rsi") if isinstance(indicators, dict) else None
    try:
        period = int(condition.get("period", indicator_rsi.get("period", 14) if isinstance(indicator_rsi, dict) else 14))
    except (TypeError, ValueError):
        return None
    return period if period > 0 else None


def _price_compare_bool(cfg: dict[str, Any], series_map: dict[str, list[float | None]], index: int) -> bool:
    if not cfg or not bool(cfg.get("enabled", True)):
        return True
    conditions = cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False
    values: list[bool] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        if condition.get("target") not in _DYNAMIC_SERIES | {"CLOSE"} or condition.get("compare_target") not in _DYNAMIC_SERIES | {"CLOSE"}:
            return False
        if _at(series_map.get(str(condition.get("target"))), index) is None or _at(series_map.get(str(condition.get("compare_target"))), index) is None:
            return False
        values.append(_condition_bool(condition, series_map, index))
    if not values:
        return False
    return all(values) if _logic(cfg.get("conditions_logic", cfg.get("logic", "AND")), "AND") == "AND" else any(values)


def _bollinger_bool(cfg: dict[str, Any], series_map: dict[str, list[float | None]], index: int) -> bool:
    if not cfg:
        return True
    conditions = cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions or not isinstance(conditions[0], dict):
        return False
    if not bool(cfg.get("enabled", True)):
        return True
    condition = conditions[0]
    raw_period = condition.get("period")
    if raw_period is not None:
        try:
            if int(raw_period) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    operator = str(condition.get("operator") or "").strip().upper()
    compare_target = str(condition.get("compare_target") or "").strip().upper()
    if compare_target == "BOLLINGER":
        compare_target = "BOLLINGER_LOWER"
    if operator not in {">", ">=", "<", "<="} or compare_target not in {"BOLLINGER_LOWER", "BOLLINGER_UPPER"}:
        return False
    raw_value = condition.get("value")
    value = _float(raw_value)
    if raw_value is not None and value is None:
        return False
    close_value, band = _at(series_map.get("CLOSE"), index), _at(series_map.get(compare_target), index)
    if close_value is None or band is None:
        return False
    threshold = round(band * (1.0 + (value or 0.0) / 100.0), 8)
    return _compare(close_value, operator, threshold)


def _ocr_bool(cfg: dict[str, Any], series_map: dict[str, list[float | None]], index: int) -> bool:
    if not cfg or not bool(cfg.get("enabled", True)):
        return True
    conditions = cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False
    values: list[bool] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            return False
        runtime = dict(condition)
        runtime.setdefault("target", "OSC")
        values.append(_condition_bool(runtime, series_map, index))
    return all(values) if _logic(cfg.get("conditions_logic", cfg.get("logic", "AND")), "AND") == "AND" else any(values)


def _validate_filters(rules: dict[str, Any], buy_cfg: dict[str, Any], base_series: dict[str, list[float | None]], *, expression_mode: bool) -> None:
    configs = _buy_filter_config_map(rules, buy_cfg)
    rsi_cfg = configs["rsi"]
    if rsi_cfg and bool(rsi_cfg.get("enabled", True)):
        conditions = rsi_cfg.get("conditions")
        condition = next((item for item in conditions if isinstance(item, dict)), rsi_cfg) if isinstance(conditions, list) else rsi_cfg
        if _buy_rsi_period(rules) is None or _normalize_rsi_operator(condition.get("operator", condition.get("compare_operator", condition.get("compare")))) is None or _float(condition.get("threshold", condition.get("value"))) is None:
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
    ma_cfg = configs["moving_average"]
    if ma_cfg and bool(ma_cfg.get("enabled", True)):
        conditions = ma_cfg.get("conditions")
        condition = next((item for item in conditions if isinstance(item, dict)), ma_cfg) if isinstance(conditions, list) else ma_cfg
        runtime = dict(condition)
        runtime.setdefault("target", "CLOSE")
        runtime.setdefault("compare_target", "MA")
        runtime.setdefault("operator", ma_cfg.get("operator", "CROSS_UP"))
        _validate_condition(runtime, base_series)
    price_cfg = configs["price_compare"]
    if not expression_mode and price_cfg and bool(price_cfg.get("enabled", True)):
        conditions = price_cfg.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
            _validate_condition(condition, base_series)
    bollinger_cfg = configs["bollinger"]
    if bollinger_cfg and bool(bollinger_cfg.get("enabled", True)):
        conditions = bollinger_cfg.get("conditions")
        if not isinstance(conditions, list) or not conditions or not isinstance(conditions[0], dict):
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
        condition = conditions[0]
        compare = str(condition.get("compare_target") or "").strip().upper()
        if compare == "BOLLINGER":
            compare = "BOLLINGER_LOWER"
        if str(condition.get("operator") or "").strip().upper() not in {">", ">=", "<", "<="} or compare not in {"BOLLINGER_LOWER", "BOLLINGER_UPPER"}:
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
    ocr_cfg = configs["ocr"]
    if ocr_cfg and bool(ocr_cfg.get("enabled", True)):
        conditions = ocr_cfg.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
            runtime = dict(condition)
            runtime.setdefault("target", "OSC")
            _validate_condition(runtime, base_series)


def _compile_filter_values(name: str, candles: list[dict[str, Any]], rules: dict[str, Any], buy_cfg: dict[str, Any], delay: int, evaluation_maps: tuple[dict[str, list[float | None]], ...]) -> tuple[bool, ...] | None:
    cfg = _buy_filter_config_map(rules, buy_cfg)[name]
    if name == "price_compare" and isinstance(cfg, dict):
        conditions = cfg.get("conditions")
        if isinstance(conditions, list) and any(isinstance(item, dict) and _condition_is_dynamic(item) for item in conditions):
            return None
    values: list[bool] = []
    for evaluation_index, series_map in enumerate(evaluation_maps):
        signal_index = evaluation_index - delay
        if name == "rsi":
            if not cfg:
                passed = True
            elif not bool(cfg.get("enabled", True)):
                passed = True
            else:
                conditions = cfg.get("conditions")
                condition = next((item for item in conditions if isinstance(item, dict)), cfg) if isinstance(conditions, list) else cfg
                period = _buy_rsi_period(rules)
                threshold = _float(condition.get("threshold", condition.get("value")))
                operator = _normalize_rsi_operator(
                    condition.get("operator", condition.get("compare_operator", condition.get("compare")))
                )
                rsi_values = series_map.get(f"_VALIDATION_RSI_{period}") if period is not None else None
                current = _at(rsi_values, signal_index)
                if operator == "<=":
                    passed = current is not None and threshold is not None and current <= threshold
                elif operator == ">=":
                    passed = current is not None and threshold is not None and current >= threshold
                else:
                    passed = False
        elif name == "moving_average":
            if not cfg or not bool(cfg.get("enabled", True)):
                passed = True
            else:
                conditions = cfg.get("conditions")
                condition = next((item for item in conditions if isinstance(item, dict)), cfg) if isinstance(conditions, list) else cfg
                try:
                    period = int(condition.get("period", cfg.get("period", 60)))
                except (TypeError, ValueError):
                    period = 0
                passed = period > 0 and condition.get("target", "CLOSE") == "CLOSE" and condition.get("compare_target", "MA") == "MA" and _condition_bool({
                    "enabled": True,
                    "not": bool(condition.get("not", False)),
                    "target": "CLOSE",
                    "operator": str(condition.get("operator", cfg.get("operator", "CROSS_UP")) or "").strip().upper(),
                    "compare_target": "MA",
                    "period": period,
                }, series_map, signal_index)
        elif name == "price_compare":
            passed = _price_compare_bool(cfg, series_map, signal_index)
        elif name == "bollinger":
            passed = _bollinger_bool(cfg, series_map, signal_index)
        else:
            passed = _ocr_bool(cfg, series_map, signal_index)
        values.append(passed)
    return tuple(values)


def _filter_results_at(evaluation_index: int, buy_index: int, rules: dict[str, Any], buy_cfg: dict[str, Any], series_map: dict[str, list[float | None]], compiled_filters: dict[str, tuple[bool, ...] | None]) -> dict[str, dict[str, Any]]:
    configs = _buy_filter_config_map(rules, buy_cfg)
    result: dict[str, dict[str, Any]] = {}
    for name in BUY_FILTER_ORDER:
        values = compiled_filters[name]
        if values is not None:
            passed = values[evaluation_index]
        elif name == "price_compare":
            passed = _price_compare_bool(configs[name], series_map, buy_index)
        else:
            raise _Unsupported("BATCH_FILTER_UNSUPPORTED")
        cfg = configs[name]
        result[name] = {
            "passed": passed,
            "detail": None,
            "configured": bool(cfg),
            "enabled": bool(cfg.get("enabled", True)) if cfg else False,
        }
    return result


def _composite_bool(cfg: dict[str, Any], results: dict[str, dict[str, Any]]) -> bool:
    if not cfg or not bool(cfg.get("enabled", False)):
        return True
    if cfg.get("_invalid_config"):
        return False
    expression = cfg.get("expression")
    if isinstance(expression, dict):
        identifiers, identifier_map = expression.get("identifiers"), expression.get("identifier_map")
        if not isinstance(identifiers, list) or not isinstance(identifier_map, dict):
            return False
        values: dict[str, bool] = {}
        for raw_identifier in identifiers:
            identifier = str(raw_identifier or "").strip().upper()
            name = str(identifier_map.get(identifier) or "").strip().lower()
            result = results.get(name)
            if not name or not isinstance(result, dict) or not result.get("configured", False) or not result.get("enabled", False):
                return False
            values[identifier] = bool(result.get("passed", False))
        try:
            return _expression_bool(expression.get("ast"), values)
        except _Unsupported:
            return False
    top_logic = str(cfg.get("logic", "AND") or "").strip().upper()
    if top_logic not in {"AND", "OR"} or str(cfg.get("include_unreferenced_active_filters", "AND_REQUIRED") or "").strip().upper() != "AND_REQUIRED":
        return False
    groups = cfg.get("groups")
    if not isinstance(groups, list) or not groups:
        return False
    referenced: list[str] = []
    group_values: list[bool] = []
    active_seen = False
    for group in groups:
        if not isinstance(group, dict):
            return False
        if not bool(group.get("enabled", True)):
            continue
        active_seen = True
        logic = str(group.get("logic", "AND") or "").strip().upper()
        names = group.get("filters")
        if logic not in {"AND", "OR"} or not isinstance(names, list) or not names:
            return False
        seen: set[str] = set()
        active_values: list[bool] = []
        for raw_name in names:
            name = str(raw_name or "").strip().lower()
            if name not in BUY_FILTER_ORDER or name == "composite" or name in seen:
                return False
            seen.add(name)
            if name not in referenced:
                referenced.append(name)
            result = results.get(name)
            if not isinstance(result, dict) or not result.get("configured", False):
                return False
            if result.get("enabled", True):
                active_values.append(bool(result.get("passed", False)))
        if not active_values:
            return False
        group_values.append(all(active_values) if logic == "AND" else any(active_values))
    if not active_seen:
        return False
    composite = all(group_values) if top_logic == "AND" else any(group_values)
    unreferenced = [
        name for name in BUY_FILTER_ORDER
        if name not in referenced and results[name]["configured"] and results[name]["enabled"]
    ]
    return composite and all(bool(results[name]["passed"]) for name in unreferenced)


def _context_for(context_provider: Any, evaluation_index: int, side: str, prefix: list[dict[str, Any]], entries: list[Any]) -> dict[str, Any]:
    context: dict[str, Any] = {"_indicator_follow_evaluate_side": side}
    if context_provider is None:
        return context
    supplied = context_provider.context_for_fast(evaluation_index, side, prefix, entries)
    if not isinstance(supplied, dict):
        raise _Unsupported("BATCH_CONTEXT_RESULT_UNSUPPORTED")
    supplied.pop("decision_trace_observer", None)
    supplied.pop("_indicator_follow_evaluate_side", None)
    context.update(supplied)
    return context


def _standard_context_provider(value: Any) -> bool:
    return isinstance(value, _IncrementalAverageContext) or (
        value.__class__.__module__ == "indicator_follow_signal_validation_execution"
        and value.__class__.__name__ == "ValidationVirtualPositionTracker"
    )


def _buy_context_from_sell(context: dict[str, Any]) -> dict[str, Any]:
    result = dict(context)
    result["_indicator_follow_evaluate_side"] = "BUY"
    trace_context = context.get("validation_trace_context")
    if isinstance(trace_context, dict):
        result["validation_trace_context"] = dict(trace_context)
        result["validation_trace_context"]["side"] = "BUY"
    return result


def _fast_sell(evaluation_index: int, prefix: list[dict[str, Any]], rules: dict[str, Any], sell_cfg: dict[str, Any], signals: tuple[_CompiledSellSignal, ...], profit_cfg: dict[str, Any], sell_delay: int, series_map: dict[str, list[float | None]], context: dict[str, Any]) -> tuple[bool, int, int]:
    passed_map: dict[str, bool] = {}
    index_map: dict[str, int] = {}
    for signal in signals:
        index_map[signal.name] = evaluation_index - signal.groups.delay
        passed_map[signal.name] = signal.enabled and _evaluate_compiled_groups(signal.groups, evaluation_index, series_map)
    sell_index = evaluation_index - sell_delay
    profit_passed, _, _ = _evaluate_profit_rate_sell(profit_cfg, prefix, sell_index, context)
    active = [signal.name for signal in signals if signal.enabled]
    if isinstance(profit_cfg, dict) and profit_cfg.get("enabled", False):
        active.append("profit_rate_sell")

    ui_names = {"ui_condition_a", "ui_condition_b", "ui_condition_c"}
    active_ui = [name for name in active if name in ui_names]
    independent = [name for name in active if name not in ui_names]
    aggregate = list(independent)
    causal = [name for name in independent if name != "profit_rate_sell"]
    ui_expression_passed: bool | None = None
    if active_ui:
        contracts = [signal.expression for signal in signals if signal.name in active_ui]
        if contracts and len(contracts) == len(active_ui) and all(isinstance(value, dict) for value in contracts) and all(value == contracts[0] for value in contracts[1:]):
            contract = contracts[0]
            identifier_map, identifiers, ast = contract.get("identifier_map"), contract.get("identifiers"), contract.get("ast")
            if isinstance(identifier_map, dict) and isinstance(identifiers, list) and identifiers and isinstance(ast, dict):
                values: dict[str, bool] = {}
                referenced: list[str] = []
                for raw_identifier in identifiers:
                    identifier = str(raw_identifier or "").strip().upper()
                    name = str(identifier_map.get(identifier) or "").strip()
                    if name not in passed_map or name not in ui_names:
                        values = {}
                        break
                    values[identifier] = bool(passed_map[name])
                    if name not in referenced:
                        referenced.append(name)
                if values:
                    aggregate = [name for name in aggregate if name != "macd_sell"]
                    causal = [name for name in causal if name != "macd_sell"]
                    try:
                        ui_expression_passed = _expression_bool(ast, values)
                    except _Unsupported:
                        ui_expression_passed = False
                    aggregate.append("ui_signal_expression")
                    if ui_expression_passed:
                        causal.extend(referenced)

    signal_pass_map = dict(passed_map)
    signal_pass_map["profit_rate_sell"] = profit_passed
    if ui_expression_passed is not None:
        signal_pass_map["ui_signal_expression"] = ui_expression_passed
    sell_logic = _logic(sell_cfg.get("signal_logic", "OR"), "OR")
    if not aggregate:
        passed = False
    elif sell_logic == "AND":
        passed = all(signal_pass_map.get(name, False) for name in aggregate)
    else:
        passed = any(signal_pass_map.get(name, False) for name in aggregate) or profit_passed
    matched_indexes = [index_map[name] for name in causal if passed_map.get(name) and name in index_map]
    selected_index = max(matched_indexes, default=sell_index)
    return passed, selected_index, max(evaluation_index - selected_index, 0)


def _fast_buy(evaluation_index: int, rules: dict[str, Any], buy_cfg: dict[str, Any], groups: _CompiledGroups, expression_mode: bool, composite_cfg: dict[str, Any], compiled_filters: dict[str, tuple[bool, ...] | None], series_map: dict[str, list[float | None]]) -> tuple[bool, int, int]:
    buy_index = evaluation_index - groups.delay
    filter_results = _filter_results_at(evaluation_index, buy_index, rules, buy_cfg, series_map, compiled_filters)
    if expression_mode:
        return _composite_bool(composite_cfg, filter_results), buy_index, groups.delay
    if not _evaluate_compiled_groups(groups, evaluation_index, series_map):
        return False, buy_index, groups.delay
    if composite_cfg and bool(composite_cfg.get("enabled", False)):
        return _composite_bool(composite_cfg, filter_results), buy_index, groups.delay
    return all(
        not filter_results[name]["configured"]
        or not filter_results[name]["enabled"]
        or filter_results[name]["passed"]
        for name in BUY_FILTER_ORDER
    ), buy_index, groups.delay


def _authoritative_record(side: str, evaluation_index: int, expected_index: int, expected_delay: int, candles: list[dict[str, Any]], prefix: list[dict[str, Any]], rules: dict[str, Any], context: dict[str, Any], series_map: dict[str, list[float | None]]) -> ValidationBatchSignalRecord | None:
    observer = _DirectTraceObserver()
    traced_context = dict(context)
    traced_context["decision_trace_observer"] = observer
    signal = evaluate_indicator_follow_routine(prefix, rules, traced_context, _base_series_map=series_map)
    if signal.signal != side or signal.signal_index != expected_index or signal.delay_bar != expected_delay:
        return None
    return ValidationBatchSignalRecord(
        side,
        evaluation_index,
        str(candles[evaluation_index].get("time") or ""),
        signal,
        observer.snapshot(),
        context,
    )


def scan_indicator_follow_validation_batch(candles: list[dict[str, Any]], rules: dict[str, Any], *, start_index: int = 0, end_index: int | None = None, context_provider: Callable[..., dict[str, Any]] | None = None) -> ValidationBatchScanResult:
    """Return a detached prototype scan, or unsupported for legacy fallback."""
    if not isinstance(candles, list) or not candles:
        return ValidationBatchScanResult(False, fallback_reason="BATCH_CANDLES_UNSUPPORTED")
    if not isinstance(rules, dict):
        return ValidationBatchScanResult(False, fallback_reason="BATCH_RULES_UNSUPPORTED")
    if context_provider is build_validation_average_price_context:
        context_provider = _IncrementalAverageContext()
    if context_provider is not None and (
        not _standard_context_provider(context_provider)
        or getattr(context_provider, "validation_read_only_fast_path", False) is not True
        or not callable(getattr(context_provider, "context_for_fast", None))
    ):
        return ValidationBatchScanResult(False, fallback_reason="BATCH_CONTEXT_PROVIDER_UNSUPPORTED")
    final_index = len(candles) - 1 if end_index is None else end_index
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (start_index, final_index)) or start_index < 0 or final_index < start_index or final_index >= len(candles):
        return ValidationBatchScanResult(False, fallback_reason="BATCH_RANGE_UNSUPPORTED")

    try:
        base_series = build_indicator_follow_base_series(candles, rules)
        rsi_period = _buy_rsi_period(rules)
        if rsi_period is not None:
            base_series[f"_VALIDATION_RSI_{rsi_period}"] = rsi(close_prices(candles), rsi_period)
        evaluation_maps = _evaluation_series_maps(base_series)
        buy_cfg = rules.get("buy") if isinstance(rules.get("buy"), dict) else {}
        sell_cfg = rules.get("sell") if isinstance(rules.get("sell"), dict) else {}
        buy_ocr_cfg = _buy_ocr_filter_config(rules, buy_cfg)
        buy_delay = int(buy_ocr_cfg.get("order_delay_bars", buy_cfg.get("delay_bar", 1)) or 0)
        macd_sell_cfg = _macd_sell_section(sell_cfg)
        sell_delay = int(macd_sell_cfg.get("delay_bar", sell_cfg.get("delay_bar", 1)) or 0)
        if buy_delay < 0 or sell_delay < 0:
            raise _Unsupported("BATCH_DELAY_UNSUPPORTED")
        buy_groups = _compile_groups(buy_cfg.get("groups", []), buy_delay, evaluation_maps, base_series)
        condition_signals = _condition_sell_signals(sell_cfg)
        sell_signals: list[_CompiledSellSignal] = []
        for name, cfg in condition_signals.items():
            delay = int(cfg.get("order_delay_bars", sell_delay) or 0)
            if delay < 0:
                raise _Unsupported("BATCH_DELAY_UNSUPPORTED")
            sell_signals.append(_CompiledSellSignal(
                name,
                bool(cfg.get("enabled", True)),
                _compile_groups(cfg.get("groups", []), delay, evaluation_maps, base_series),
                cfg.get("signal_expression"),
            ))
        composite_cfg = _buy_composite_filter_config(rules, buy_cfg)
        expression_mode = _is_buy_expression_mode(composite_cfg)
        _validate_filters(
            rules,
            buy_cfg,
            base_series,
            expression_mode=expression_mode,
        )
        compiled_filters = {
            name: _compile_filter_values(name, candles, rules, buy_cfg, buy_delay, evaluation_maps)
            for name in BUY_FILTER_ORDER
        }
        profit_cfg = _profit_rate_sell_section(sell_cfg)
    except _Unsupported as exc:
        return ValidationBatchScanResult(False, fallback_reason=str(exc))
    except (TypeError, ValueError, OverflowError, KeyError):
        return ValidationBatchScanResult(False, fallback_reason="BATCH_CONFIGURATION_UNSUPPORTED")

    records: list[ValidationBatchSignalRecord] = []
    try:
        for evaluation_index in range(start_index, final_index + 1):
            prefix = _CandlePrefixView(candles, evaluation_index + 1)
            sell_context = _context_for(
                context_provider, evaluation_index, "SELL", prefix, records
            )
            for side in ("SELL", "BUY"):
                context = (
                    sell_context
                    if side == "SELL"
                    else _buy_context_from_sell(sell_context)
                )
                series_map = dict(evaluation_maps[evaluation_index])
                _enrich_price_compare_series(series_map, context)
                if evaluation_index < 2 or not rules.get("enabled", True):
                    predicted, signal_index, delay = False, -1, 0
                elif side == "SELL":
                    predicted, signal_index, delay = _fast_sell(
                        evaluation_index, prefix, rules, sell_cfg, tuple(sell_signals),
                        profit_cfg, sell_delay, series_map, context,
                    )
                else:
                    predicted, signal_index, delay = _fast_buy(
                        evaluation_index, rules, buy_cfg, buy_groups, expression_mode,
                        composite_cfg, compiled_filters, series_map,
                    )
                if not predicted:
                    continue
                record = _authoritative_record(
                    side, evaluation_index, signal_index, delay, candles, prefix,
                    rules, context, series_map,
                )
                if record is None:
                    return ValidationBatchScanResult(False, fallback_reason="BATCH_AUTHORITATIVE_REPLAY_MISMATCH")
                records.append(record)
    except _Unsupported as exc:
        return ValidationBatchScanResult(False, fallback_reason=str(exc))
    except Exception:
        return ValidationBatchScanResult(False, fallback_reason="BATCH_FAST_SCAN_ERROR")
    return ValidationBatchScanResult(True, tuple(records))
