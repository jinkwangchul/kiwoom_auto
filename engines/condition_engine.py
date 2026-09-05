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


_EXPRESSION_OPERATORS = {"AND", "OR", "NOT"}


def parse_condition_expression(
    expression: Any,
    *,
    allowed_identifiers: set[str] | tuple[str, ...] | list[str],
    allow_duplicate_identifiers: bool = True,
    max_identifiers: int = 10,
) -> dict[str, Any]:
    """Parse the UI's bounded condition expression without using ``eval``.

    ``NOT`` is the project's binary exclusion operator: ``A NOT B`` means
    ``A AND (NOT B)``.  Unary NOT and the composite tokens ``AND NOT`` /
    ``OR NOT`` are therefore rejected.  The returned AST contains only JSON
    values so it can travel through the existing Rule approval/commit path.
    """
    text = str(expression or "").strip()
    if not text:
        return {"ok": False, "reason": "CONDITION_EXPRESSION_EMPTY"}

    allowed = {str(value).strip().upper() for value in allowed_identifiers}
    raw_tokens = text.replace("(", " ( ").replace(")", " ) ").split()
    tokens = [token.upper() for token in raw_tokens]
    if not tokens:
        return {"ok": False, "reason": "CONDITION_EXPRESSION_EMPTY"}
    for token in tokens:
        if token not in allowed and token not in _EXPRESSION_OPERATORS and token not in {"(", ")"}:
            return {"ok": False, "reason": f"CONDITION_EXPRESSION_TOKEN_UNSUPPORTED:{token}"}

    identifier_tokens = [token for token in tokens if token in allowed]
    if not identifier_tokens:
        return {"ok": False, "reason": "CONDITION_EXPRESSION_IDENTIFIER_MISSING"}
    if len(identifier_tokens) > max_identifiers:
        return {"ok": False, "reason": "CONDITION_EXPRESSION_IDENTIFIER_LIMIT_EXCEEDED"}
    if not allow_duplicate_identifiers and len(identifier_tokens) != len(set(identifier_tokens)):
        return {"ok": False, "reason": "CONDITION_EXPRESSION_DUPLICATE_IDENTIFIER"}

    position = 0

    def parse_primary() -> dict[str, Any]:
        nonlocal position
        if position >= len(tokens):
            raise ValueError("CONDITION_EXPRESSION_OPERAND_MISSING")
        token = tokens[position]
        if token == "(":
            position += 1
            node = parse_sequence()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("CONDITION_EXPRESSION_PARENTHESIS_UNBALANCED")
            position += 1
            return node
        if token in allowed:
            position += 1
            return {"type": "identifier", "name": token}
        raise ValueError(f"CONDITION_EXPRESSION_OPERAND_INVALID:{token}")

    def parse_sequence() -> dict[str, Any]:
        """Parse every binary operator with the UI's left-to-right contract."""
        nonlocal position
        node = parse_primary()
        while position < len(tokens) and tokens[position] in _EXPRESSION_OPERATORS:
            operator = tokens[position]
            position += 1
            right = parse_primary()
            node = {"type": "binary", "operator": operator, "left": node, "right": right}
        return node

    try:
        ast = parse_sequence()
        if position != len(tokens):
            raise ValueError(f"CONDITION_EXPRESSION_TRAILING_TOKEN:{tokens[position]}")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}

    normalized = " ".join(tokens).replace("( ", "(").replace(" )", ")")
    return {
        "ok": True,
        "reason": None,
        "normalized": normalized,
        "identifiers": identifier_tokens,
        "ast": ast,
    }


def evaluate_condition_expression(
    expression_ast: Any,
    values: dict[str, Any],
    *,
    current_identity: Any = None,
) -> dict[str, Any]:
    """Evaluate a canonical expression as matched-result identity sets.

    ``NOT`` is binary set subtraction.  Boolean callers remain compatible:
    ``True`` represents the supplied current identity (or a local sentinel).
    """
    normalized_values = {str(key).strip().upper(): value for key, value in values.items()}
    fallback_identity = "__CURRENT__"

    def normalize_result(value: Any, name: str) -> set[Any]:
        if isinstance(value, bool):
            return {(current_identity if current_identity is not None else fallback_identity)} if value else set()
        if isinstance(value, (set, frozenset, list, tuple)) and not isinstance(value, (str, bytes)):
            try:
                return set(value)
            except TypeError as exc:
                raise ValueError(f"CONDITION_EXPRESSION_VALUE_INVALID:{name}") from exc
        raise ValueError(f"CONDITION_EXPRESSION_VALUE_INVALID:{name}")

    def evaluate_node(node: Any) -> set[Any]:
        if not isinstance(node, dict):
            raise ValueError("CONDITION_EXPRESSION_AST_INVALID")
        node_type = str(node.get("type") or "").strip().lower()
        if node_type == "identifier":
            name = str(node.get("name") or "").strip().upper()
            if name not in normalized_values:
                raise ValueError(f"CONDITION_EXPRESSION_VALUE_MISSING:{name}")
            return normalize_result(normalized_values[name], name)
        if node_type != "binary":
            raise ValueError("CONDITION_EXPRESSION_AST_NODE_UNSUPPORTED")
        operator = str(node.get("operator") or "").strip().upper()
        if operator not in _EXPRESSION_OPERATORS:
            raise ValueError(f"CONDITION_EXPRESSION_OPERATOR_UNSUPPORTED:{operator}")
        left = evaluate_node(node.get("left"))
        right = evaluate_node(node.get("right"))
        if operator == "AND":
            return left & right
        if operator == "OR":
            return left | right
        return left - right

    try:
        matched = evaluate_node(expression_ast)
        passed = (
            current_identity in matched
            if current_identity is not None
            else bool(matched)
        )
        return {
            "ok": True,
            "passed": passed,
            "matched_identities": sorted(matched, key=lambda value: (type(value).__name__, repr(value))),
            "reason": None,
        }
    except ValueError as exc:
        return {"ok": False, "passed": False, "matched_identities": [], "reason": str(exc)}


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


def _notify_observer(observer: Any, method_name: str, payload: dict[str, Any]) -> None:
    if observer is None:
        return
    callback = getattr(observer, method_name, None)
    if not callable(callback):
        return
    try:
        callback(payload)
    except Exception:
        # Diagnostic observation must never alter a Production decision.
        return


def _operand(source: str, key: str, index: int | None, value: Any, reason: str = "") -> dict[str, Any]:
    result = {
        "source": source,
        "key": key,
        "index": index,
        "value": value,
    }
    if value is None:
        result["reason"] = reason or "value unavailable"
    return result


def _series_period(condition: dict[str, Any], key: str, *, compare: bool = False) -> int | None:
    field = "compare_period" if compare else "period"
    value = condition.get(field)
    try:
        if value not in (None, ""):
            return int(value)
    except (TypeError, ValueError):
        pass
    suffix = "".join(character for character in str(key) if character.isdigit())
    return int(suffix) if suffix else None


def _indicator_snapshot(
    key: str,
    index: int,
    series: list[float | None] | None,
    period: int | None = None,
) -> dict[str, Any]:
    current = _value_at(series, index)
    result = {
        "indicator": key,
        "index": index,
        "current": current,
        "previous": _value_at(series, index - 1),
        "previous2": _value_at(series, index - 2),
    }
    if period is not None:
        result["period"] = period
    if current is None:
        result["reason"] = "indicator value unavailable"
    return result


def evaluate_condition(
    condition: dict[str, Any],
    series_map: SeriesMap,
    index: int = -1,
    observer: Any = None,
    condition_path: str = "",
) -> ConditionResult:
    """조건 1개를 평가한다."""
    enabled = condition.get("enabled", True)
    if not enabled:
        _notify_observer(
            observer,
            "observe_condition",
            {
                "path": condition_path,
                "condition_type": str(condition.get("type") or condition.get("target") or "DISABLED"),
                "operator": str(condition.get("operator") or "DISABLED"),
                "negated": bool(condition.get("not", False)),
                "left_operand": _operand("indicator", _series_key(condition), index, None, "condition disabled"),
                "right_operand": _operand("none", "none", None, None, "condition disabled"),
                "raw_result": True,
                "final_result": True,
                "indicator_snapshots": [],
            },
        )
        return ConditionResult(True, "비활성 조건 통과 처리")

    try:
        bar_offset = int(condition.get("bar_offset", 0))
    except (TypeError, ValueError):
        bar_offset = -1
    if bar_offset < 0:
        return ConditionResult(False, "bar_offset must be a non-negative integer")
    target_key = _series_key(condition)
    operator = _norm(condition.get("operator"))
    use_not = bool(condition.get("not", False))
    series = series_map.get(target_key)
    base_index = (len(series) + index) if series and index < 0 else index
    effective_index = base_index - bar_offset

    current = _value_at(series, effective_index)
    prev = _value_at(series, effective_index - 1)
    prev2 = _value_at(series, effective_index - 2)

    passed = False
    detail = f"{target_key} {operator}"
    right_operand = _operand("none", "none", None, None, "operator has no right operand")
    snapshots = [_indicator_snapshot(target_key, effective_index, series, _series_period(condition, target_key))]

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
        compare_current = _value_at(compare_series, effective_index)
        compare_prev = _value_at(compare_series, effective_index - 1)
        right_operand = _operand("indicator", compare_target, effective_index, compare_current)
        snapshots.append(
            _indicator_snapshot(
                compare_target,
                effective_index,
                compare_series,
                _series_period(condition, compare_target, compare=True),
            )
        )
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
    elif operator == "PERCENT_GAP":
        compare_key = _series_key(condition, "compare_target")
        compare_value = _value_at(series_map.get(compare_key), effective_index)
        percent = _safe_float(condition.get("value"))
        direction = _norm(condition.get("direction"))
        compare_mode = _norm(condition.get("compare_mode"))
        right_operand = _operand("indicator", compare_key, effective_index, compare_value)
        snapshots.append(
            _indicator_snapshot(
                compare_key,
                effective_index,
                series_map.get(compare_key),
                _series_period(condition, compare_key, compare=True),
            )
        )
        if current is not None and compare_value is not None and compare_value > 0 and percent is not None and percent >= 0:
            lower = compare_value * (1 - percent / 100.0)
            upper = compare_value * (1 + percent / 100.0)
            if direction == "UP":
                passed = current >= upper if compare_mode == "GTE" else current <= upper if compare_mode == "LTE" else False
            elif direction == "DOWN":
                passed = current >= lower if compare_mode == "GTE" else current <= lower if compare_mode == "LTE" else False
            elif direction == "BOTH":
                passed = lower <= current <= upper if compare_mode == "WITHIN" else (current < lower or current > upper) if compare_mode == "OUTSIDE" else False
        detail = f"{target_key} PERCENT_GAP {compare_key} {direction} {compare_mode} {percent}%"
    elif operator in {">", ">=", "<", "<=", "=", "==", "GT", "GTE", "LT", "LTE", "EQ", "ABOVE", "BELOW"}:
        right_value = _safe_float(condition.get("value"))
        compare_target = condition.get("compare_target")
        if compare_target:
            compare_key = _series_key(condition, "compare_target")
            right_value = _value_at(series_map.get(compare_key), effective_index)
            offset_percent = _safe_float(condition.get("value"))
            right_value = _apply_percent_offset(right_value, operator, offset_percent)
            detail = (
                f"{target_key} {operator} {compare_key}"
                if offset_percent is None
                else f"{target_key} {operator} {compare_key} offset {offset_percent}%"
            )
            right_operand = _operand("indicator", compare_key, effective_index, right_value)
            snapshots.append(
                _indicator_snapshot(
                    compare_key,
                    effective_index,
                    series_map.get(compare_key),
                    _series_period(condition, compare_key, compare=True),
                )
            )
        else:
            detail = f"{target_key} {operator} {right_value}"
            right_operand = _operand("literal", "value", None, right_value)
        if current is not None and right_value is not None:
            passed = _compare(current, operator, right_value)
    else:
        detail = f"지원하지 않는 조건: {target_key} {operator}"
        _notify_observer(
            observer,
            "observe_condition",
            {
                "path": condition_path,
                "condition_type": str(condition.get("type") or target_key),
                "operator": operator or "UNSUPPORTED",
                "negated": use_not,
                "left_operand": _operand("indicator", target_key, effective_index, current),
                "right_operand": right_operand,
                "raw_result": False,
                "final_result": False,
                "indicator_snapshots": snapshots,
            },
        )
        return ConditionResult(False, detail)

    raw_passed = passed
    if use_not:
        passed = not passed
        detail = "NOT " + detail

    _notify_observer(
        observer,
        "observe_condition",
        {
            "path": condition_path,
            "condition_type": str(condition.get("type") or target_key),
            "operator": operator,
            "negated": use_not,
            "left_operand": _operand("indicator", target_key, effective_index, current),
            "right_operand": right_operand,
            "raw_result": raw_passed,
            "final_result": passed,
            "indicator_snapshots": snapshots,
        },
    )

    return ConditionResult(passed, detail)


def evaluate_group(
    group: dict[str, Any],
    series_map: SeriesMap,
    index: int = -1,
    observer: Any = None,
    group_path: str = "",
) -> GroupResult:
    """조건그룹 1개를 AND 기준으로 평가한다."""
    group_name = str(group.get("name", "조건")).strip() or "조건"
    if not group.get("enabled", True):
        _notify_observer(observer, "observe_group", {
            "path": group_path,
            "group_name": group_name,
            "enabled": False,
            "logic": _logic(group.get("conditions_logic", group.get("logic", "AND")), "AND"),
            "condition_paths": [],
            "result": False,
        })
        return GroupResult(False, group_name, ["그룹 비활성"])

    conditions = group.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        _notify_observer(observer, "observe_group", {
            "path": group_path,
            "group_name": group_name,
            "enabled": True,
            "logic": _logic(group.get("conditions_logic", group.get("logic", "AND")), "AND"),
            "condition_paths": [],
            "result": False,
        })
        return GroupResult(False, group_name, ["조건 없음"])

    details: list[str] = []
    logic = _logic(group.get("conditions_logic", group.get("logic", "AND")), "AND")
    all_passed = True
    any_passed = False
    expression_values: dict[str, bool] = {}
    condition_paths: list[str] = []
    for condition_index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            all_passed = False
            details.append("잘못된 조건 형식")
            continue
        condition_path = f"{group_path}.conditions[{condition_index}]"
        condition_paths.append(condition_path)
        result = evaluate_condition(condition, series_map, index, observer, condition_path)
        details.append(("PASS " if result.passed else "FAIL ") + result.detail)
        if not result.passed:
            all_passed = False
        else:
            any_passed = True

        expression_id = str(condition.get("expression_id") or f"C{condition_index}").strip().upper()
        if expression_id in expression_values:
            all_passed = False
            details.append(f"FAIL duplicate expression_id: {expression_id}")
        else:
            expression_values[expression_id] = result.passed

    expression_ast = group.get("condition_expression")
    if expression_ast is not None:
        representative_length = max((len(series) for series in series_map.values() if isinstance(series, list)), default=0)
        current_identity = representative_length + index if index < 0 else index
        expression_result = evaluate_condition_expression(
            expression_ast,
            expression_values,
            current_identity=current_identity,
        )
        passed = bool(expression_result.get("passed")) if expression_result.get("ok") else False
        if not expression_result.get("ok"):
            details.append(f"FAIL {expression_result.get('reason')}")
        logic = "EXPRESSION"
    else:
        passed = any_passed if logic == "OR" else all_passed
    _notify_observer(observer, "observe_group", {
        "path": group_path,
        "group_name": group_name,
        "enabled": True,
        "logic": logic,
        "condition_paths": condition_paths,
        "result": passed,
    })
    return GroupResult(passed, group_name, details)


def evaluate_groups_or(
    groups: list[dict[str, Any]],
    series_map: SeriesMap,
    index: int = -1,
    observer: Any = None,
    path_prefix: str = "groups",
) -> tuple[bool, list[GroupResult]]:
    """조건그룹 목록을 OR 기준으로 평가한다."""
    results: list[GroupResult] = []
    any_passed = False
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        result = evaluate_group(
            group,
            series_map,
            index,
            observer,
            f"{path_prefix}[{group_index}]",
        )
        results.append(result)
        if result.passed:
            any_passed = True
    return any_passed, results
