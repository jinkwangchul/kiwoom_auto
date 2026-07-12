# -*- coding: utf-8 -*-
"""공통 조건 평가 엔진.

역할:
- RSI / 이평선 / MACD / OSC / 가격 / 거래량 등 공통 조건 평가.
- 조건그룹 내부는 AND.
- 조건그룹 간은 OR.
- 각 조건은 NOT 반전 가능.

주의:
- 주문, 예산, 체결, 청산, 검토관리는 처리하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any


SeriesMap = dict[str, list[float | None]]
SNAPSHOT_CANDLE_KEYS = ("candles", "bars", "ohlcv")
SNAPSHOT_INDICATOR_KEYS = ("rsi", "macd", "signal", "ma", "bollinger")


@dataclass(frozen=True)
class ConditionResult:
    passed: bool
    detail: str


@dataclass(frozen=True)
class GroupResult:
    passed: bool
    group_name: str
    details: list[str]


def normalize_market_snapshot(market_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the read-only standard market snapshot shape used by probes."""
    candles_source = None
    for key in SNAPSHOT_CANDLE_KEYS:
        value = market_snapshot.get(key)
        if isinstance(value, list):
            candles_source = value
            break

    indicators = market_snapshot.get("indicators")
    if not isinstance(indicators, dict):
        indicators = {}

    return {
        "symbol": deepcopy(market_snapshot.get("symbol")),
        "timeframe": deepcopy(market_snapshot.get("timeframe")),
        "candles": [deepcopy(item) for item in candles_source or [] if isinstance(item, dict)],
        "current_price": deepcopy(market_snapshot.get("current_price")),
        "indicators": {
            key: deepcopy(indicators.get(key))
            for key in SNAPSHOT_INDICATOR_KEYS
        },
    }


def validate_market_snapshot(market_snapshot: Any) -> dict[str, Any]:
    """Validate and normalize the official probe market_snapshot contract."""
    if not isinstance(market_snapshot, dict):
        return {"ok": False, "reason": "market_snapshot must be dict", "snapshot": None}

    missing: list[str] = []
    for key in ("symbol", "timeframe", "current_price", "indicators"):
        if key not in market_snapshot:
            missing.append(key)

    candle_key_present = any(key in market_snapshot for key in SNAPSHOT_CANDLE_KEYS)
    if not candle_key_present:
        missing.append("candles")

    indicators = market_snapshot.get("indicators")
    if isinstance(indicators, dict):
        for key in SNAPSHOT_INDICATOR_KEYS:
            if key not in indicators:
                missing.append(f"indicators.{key}")
    elif "indicators" in market_snapshot:
        missing.append("indicators must be dict")

    if missing:
        return {
            "ok": False,
            "reason": "missing required market_snapshot fields: " + ", ".join(missing),
            "snapshot": None,
        }

    snapshot = normalize_market_snapshot(market_snapshot)
    if not isinstance(snapshot["candles"], list) or not snapshot["candles"]:
        return {"ok": False, "reason": "market_snapshot.candles must be non-empty list", "snapshot": None}

    return {"ok": True, "reason": None, "snapshot": snapshot}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _series_key(condition: dict[str, Any], target_key: str = "target") -> str:
    target = _norm(condition.get(target_key))
    if target == "MA":
        period_key = "compare_period" if target_key == "compare_target" else "period"
        period = int(_safe_float(condition.get(period_key)) or 0)
        if period <= 0 and target_key == "compare_target":
            period = int(_safe_float(condition.get("period")) or 0)
        return f"MA{period}" if period > 0 else "MA"
    return target


def _value_at(series: list[float | None] | None, index: int) -> float | None:
    if not series:
        return None
    if index < 0:
        index = len(series) + index
    if index < 0 or index >= len(series):
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
    if operator in {"=", "==", "EQ"}:
        return left == right
    return False


def _logic(value: Any, default: str = "AND") -> str:
    text = _norm(value)
    return text if text in {"AND", "OR"} else default


def _apply_percent_offset(base_value: float | None, operator: str, offset_percent: float | None) -> float | None:
    if base_value is None or offset_percent is None:
        return base_value
    ratio = abs(offset_percent) / 100
    if operator in {">", ">=", "GT", "GTE", "ABOVE"}:
        return base_value * (1 + ratio)
    if operator in {"<", "<=", "LT", "LTE", "BELOW"}:
        return base_value * (1 - ratio)
    return base_value


def evaluate_condition(
    condition: dict[str, Any],
    series_map: SeriesMap,
    index: int = -1,
) -> ConditionResult:
    """조건 1개를 평가한다."""
    enabled = condition.get("enabled", True)
    if not enabled:
        return ConditionResult(True, "비활성 조건 통과 처리")

    target_key = _series_key(condition)
    operator = _norm(condition.get("operator"))
    use_not = bool(condition.get("not", False))
    series = series_map.get(target_key)

    current = _value_at(series, index)
    prev = _value_at(series, index - 1)
    prev2 = _value_at(series, index - 2)

    passed = False
    detail = f"{target_key} {operator}"

    if operator == "TURN_UP":
        passed = (
            prev2 is not None
            and prev is not None
            and current is not None
            and prev2 > prev
            and current > prev
        )
    elif operator == "TURN_DOWN":
        passed = (
            prev2 is not None
            and prev is not None
            and current is not None
            and prev2 < prev
            and current < prev
        )
    elif operator == "TREND_UP":
        passed = prev is not None and current is not None and current > prev
    elif operator == "TREND_DOWN":
        passed = prev is not None and current is not None and current < prev
    elif operator in {"CROSS_UP", "CROSS_DOWN"}:
        compare_target = _series_key(condition, "compare_target")
        compare_series = series_map.get(compare_target)
        compare_current = _value_at(compare_series, index)
        compare_prev = _value_at(compare_series, index - 1)
        if prev is not None and current is not None and compare_prev is not None and compare_current is not None:
            if operator == "CROSS_UP":
                passed = prev <= compare_prev and current > compare_current
            else:
                passed = prev >= compare_prev and current < compare_current
        detail = f"{target_key} {operator} {compare_target}"
    elif operator in {"ZERO_CROSS_UP", "ZERO_CROSS_DOWN"}:
        if prev is not None and current is not None:
            if operator == "ZERO_CROSS_UP":
                passed = prev <= 0 and current > 0
            else:
                passed = prev >= 0 and current < 0
    elif operator in {">", ">=", "<", "<=", "=", "==", "GT", "GTE", "LT", "LTE", "EQ", "ABOVE", "BELOW"}:
        right_value = _safe_float(condition.get("value"))
        compare_target = condition.get("compare_target")
        if compare_target:
            compare_key = _series_key(condition, "compare_target")
            right_value = _value_at(series_map.get(compare_key), index)
            offset_percent = _safe_float(condition.get("value"))
            right_value = _apply_percent_offset(right_value, operator, offset_percent)
            detail = (
                f"{target_key} {operator} {compare_key}"
                if offset_percent is None
                else f"{target_key} {operator} {compare_key} offset {offset_percent}%"
            )
        else:
            detail = f"{target_key} {operator} {right_value}"
        if current is not None and right_value is not None:
            passed = _compare(current, operator, right_value)
    else:
        return ConditionResult(False, f"지원하지 않는 조건: {target_key} {operator}")

    if use_not:
        passed = not passed
        detail = "NOT " + detail

    return ConditionResult(passed, detail)


def evaluate_group(
    group: dict[str, Any],
    series_map: SeriesMap,
    index: int = -1,
) -> GroupResult:
    """조건그룹 1개를 AND 기준으로 평가한다."""
    group_name = str(group.get("name", "조건")).strip() or "조건"
    if not group.get("enabled", True):
        return GroupResult(False, group_name, ["그룹 비활성"])

    conditions = group.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        return GroupResult(False, group_name, ["조건 없음"])

    details: list[str] = []
    logic = _logic(group.get("conditions_logic", group.get("logic", "AND")), "AND")
    all_passed = True
    any_passed = False
    for condition in conditions:
        if not isinstance(condition, dict):
            all_passed = False
            details.append("잘못된 조건 형식")
            continue
        result = evaluate_condition(condition, series_map, index)
        details.append(("PASS " if result.passed else "FAIL ") + result.detail)
        if not result.passed:
            all_passed = False
        else:
            any_passed = True

    return GroupResult(any_passed if logic == "OR" else all_passed, group_name, details)


def evaluate_groups_or(
    groups: list[dict[str, Any]],
    series_map: SeriesMap,
    index: int = -1,
) -> tuple[bool, list[GroupResult]]:
    """조건그룹 목록을 OR 기준으로 평가한다."""
    results: list[GroupResult] = []
    any_passed = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        result = evaluate_group(group, series_map, index)
        results.append(result)
        if result.passed:
            any_passed = True
    return any_passed, results
