# -*- coding: utf-8 -*-
"""MACD 전용 신호발생 엔진.

역할:
- MACD 오실레이터 기반 BUY / SELL 판단.
- 공통 조건엔진과 공통 지표엔진을 사용한다.

주의:
- 이 파일은 신호발생부다.
- 주문, 예산, 체결, 청산, 검토관리 이동은 처리하지 않는다.
"""

from __future__ import annotations

from typing import Any

from engines.condition_engine import evaluate_condition, evaluate_groups_or
from engines.indicator_engine import build_indicator_series, close_prices, rsi
from engines.signal_result import RoutineSignal, signal_to_dict


DEFAULT_INDICATOR_FOLLOW_CONFIG: dict[str, Any] = {
    "routine_type": "MACD_OSC",
    "enabled": True,
    "bar_minutes": 1,
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "rsi": {"period": 14},
    "moving_averages": [5, 20, 60],
    "buy": {
        "delay_bar": 1,
        "groups": [
            {
                "enabled": True,
                "name": "매수조건1",
                "conditions": [
                    {"enabled": True, "not": False, "target": "OSC", "operator": "TURN_UP"},
                ],
            },
            {"enabled": False, "name": "매수조건2", "conditions": []},
            {"enabled": False, "name": "매수조건3", "conditions": []},
            {"enabled": False, "name": "매수조건4", "conditions": []},
            {"enabled": False, "name": "매수조건5", "conditions": []},
        ],
    },
    "sell": {
        "delay_bar": 1,
        "groups": [
            {
                "enabled": True,
                "name": "매도조건1",
                "conditions": [
                    {"enabled": True, "not": False, "target": "OSC", "operator": "TURN_DOWN"},
                ],
            },
            {"enabled": False, "name": "매도조건2", "conditions": []},
            {"enabled": False, "name": "매도조건3", "conditions": []},
            {"enabled": False, "name": "매도조건4", "conditions": []},
            {"enabled": False, "name": "매도조건5", "conditions": []},
        ],
    },
}

DEFAULT_MACD_ROUTINE_CONFIG = DEFAULT_INDICATOR_FOLLOW_CONFIG


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def _delay_index(candles: list[dict[str, Any]], delay_bar: int) -> int:
    # delay_bar=0: 마지막 봉이 0봉.
    # delay_bar=1: 마지막 봉에서 1봉 전이 신호봉이고, 현재 봉에서 신호를 발생시킨다.
    return len(candles) - 1 - max(int(delay_bar or 0), 0)


def _macd_sell_section(sell_cfg: dict[str, Any]) -> dict[str, Any]:
    """SELL 설정에서 MACD SELL 섹션을 추출한다.

    우선순위:
    1. 신규 구조: sell.signals.macd_sell
    2. 구 구조: sell
    """
    signals = sell_cfg.get("signals")
    if isinstance(signals, dict):
        macd_sell = signals.get("macd_sell")
        if isinstance(macd_sell, dict):
            return macd_sell
    return sell_cfg




def _profit_rate_sell_section(sell_cfg: dict[str, Any]) -> dict[str, Any]:
    """SELL 설정에서 수익률 SELL 섹션을 추출한다.

    우선순위:
    1. 신규 구조: sell.signals.profit_rate_sell
    2. 없으면 빈 설정
    """
    signals = sell_cfg.get("signals")
    if isinstance(signals, dict):
        profit_rate_sell = signals.get("profit_rate_sell")
        if isinstance(profit_rate_sell, dict):
            return profit_rate_sell
    return {}


def _condition_sell_signals(sell_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signals = sell_cfg.get("signals")
    if isinstance(signals, dict):
        result: dict[str, dict[str, Any]] = {}
        for name, signal_cfg in signals.items():
            if name == "profit_rate_sell" or not isinstance(signal_cfg, dict):
                continue
            if isinstance(signal_cfg.get("groups"), list):
                result[str(name)] = signal_cfg
        return result
    if isinstance(sell_cfg.get("groups"), list):
        return {"sell": sell_cfg}
    return {}


def _logic(value: Any, default: str = "OR") -> str:
    logic = str(value or default).strip().upper()
    return logic if logic in {"AND", "OR"} else default


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_get(mapping: dict[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _indicator_rsi_config(config: dict[str, Any]) -> dict[str, Any]:
    indicators = config.get("indicators")
    if isinstance(indicators, dict) and isinstance(indicators.get("rsi"), dict):
        return indicators["rsi"]
    value = config.get("rsi")
    return value if isinstance(value, dict) else {}


def _buy_rsi_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("rsi"), dict):
        return filters["rsi"]

    value = buy_cfg.get("rsi")
    if isinstance(value, dict):
        return value
    return {}


def _first_rsi_condition(filter_cfg: dict[str, Any]) -> dict[str, Any]:
    conditions = filter_cfg.get("conditions")
    if isinstance(conditions, list):
        for condition in conditions:
            if isinstance(condition, dict):
                merged = dict(filter_cfg)
                merged.update(condition)
                return merged
    return filter_cfg


def _normalize_rsi_operator(value: Any) -> str | None:
    raw_value = str(value).strip()
    operator = raw_value.upper()
    if operator in {"<=", "LTE", "LE", "BELOW_OR_EQUAL"} or raw_value == "이하":
        return "<="
    if operator in {">=", "GTE", "GE", "ABOVE_OR_EQUAL"} or raw_value == "이상":
        return ">="
    return None


def _rsi_detail(
    *,
    enabled: bool,
    period: Any,
    operator: Any,
    threshold: Any,
    evaluated_value: Any,
    passed: bool,
    reason: str,
    evaluation_index: int,
) -> str:
    return (
        "filter_type=RSI "
        f"enabled={enabled} "
        f"period={period} "
        f"operator={operator} "
        f"threshold={threshold} "
        f"evaluated_value={evaluated_value} "
        f"passed={passed} "
        f"reason={reason} "
        f"evaluation_index={evaluation_index}"
    )


def _evaluate_buy_rsi_filter(
    config: dict[str, Any],
    buy_cfg: dict[str, Any],
    candles: list[dict[str, Any]],
    evaluation_index: int,
) -> tuple[bool, str | None]:
    filter_cfg = _buy_rsi_filter_config(config, buy_cfg)
    if not filter_cfg:
        return True, None

    indicator_cfg = _indicator_rsi_config(config)
    condition = _first_rsi_condition(filter_cfg)
    enabled = bool(filter_cfg.get("enabled", True))
    raw_period = condition.get("period", indicator_cfg.get("period", 14))
    raw_operator = condition.get("operator", condition.get("compare_operator", condition.get("compare")))
    raw_threshold = condition.get("threshold", condition.get("value"))

    if not enabled:
        return True, _rsi_detail(
            enabled=False,
            period=raw_period,
            operator=raw_operator,
            threshold=raw_threshold,
            evaluated_value=None,
            passed=True,
            reason="disabled",
            evaluation_index=evaluation_index,
        )

    period = _safe_int(raw_period)
    operator = _normalize_rsi_operator(raw_operator)
    threshold = _safe_float(raw_threshold)

    if period is None or period <= 0:
        return False, _rsi_detail(
            enabled=True,
            period=raw_period,
            operator=raw_operator,
            threshold=raw_threshold,
            evaluated_value=None,
            passed=False,
            reason="invalid_period",
            evaluation_index=evaluation_index,
        )
    if threshold is None:
        return False, _rsi_detail(
            enabled=True,
            period=period,
            operator=raw_operator,
            threshold=raw_threshold,
            evaluated_value=None,
            passed=False,
            reason="invalid_threshold",
            evaluation_index=evaluation_index,
        )
    if operator is None:
        return False, _rsi_detail(
            enabled=True,
            period=period,
            operator=raw_operator,
            threshold=threshold,
            evaluated_value=None,
            passed=False,
            reason="unsupported_operator",
            evaluation_index=evaluation_index,
        )

    rsi_values = rsi(close_prices(candles), period)
    evaluated_value = rsi_values[evaluation_index] if 0 <= evaluation_index < len(rsi_values) else None
    if evaluated_value is None:
        return False, _rsi_detail(
            enabled=True,
            period=period,
            operator=operator,
            threshold=threshold,
            evaluated_value=None,
            passed=False,
            reason="insufficient_data",
            evaluation_index=evaluation_index,
        )

    passed = evaluated_value <= threshold if operator == "<=" else evaluated_value >= threshold
    return passed, _rsi_detail(
        enabled=True,
        period=period,
        operator=operator,
        threshold=threshold,
        evaluated_value=round(evaluated_value, 6),
        passed=passed,
        reason="matched" if passed else "not_matched",
        evaluation_index=evaluation_index,
    )


def _buy_moving_average_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("moving_average"), dict):
        return filters["moving_average"]
    return {}


def _first_moving_average_condition(filter_cfg: dict[str, Any]) -> dict[str, Any]:
    conditions = filter_cfg.get("conditions")
    if isinstance(conditions, list):
        for condition in conditions:
            if isinstance(condition, dict):
                merged = dict(filter_cfg)
                merged.update(condition)
                return merged
    return filter_cfg


def _moving_average_detail(
    *,
    enabled: bool,
    period: Any,
    operator: Any,
    current_value: Any,
    ma_value: Any,
    passed: bool,
    reason: str,
    evaluation_index: int,
) -> str:
    return (
        "filter_type=MOVING_AVERAGE "
        f"enabled={enabled} "
        "target=CLOSE "
        "compare_target=MA "
        f"period={period} "
        f"operator={operator} "
        f"current_value={current_value} "
        f"ma_value={ma_value} "
        f"passed={passed} "
        f"reason={reason} "
        f"evaluation_index={evaluation_index}"
    )


def _evaluate_buy_moving_average_filter(
    config: dict[str, Any],
    buy_cfg: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> tuple[bool, str | None]:
    filter_cfg = _buy_moving_average_filter_config(config, buy_cfg)
    if not filter_cfg:
        return True, None

    condition = _first_moving_average_condition(filter_cfg)
    enabled = bool(filter_cfg.get("enabled", True))
    raw_period = condition.get("period", filter_cfg.get("period", 60))
    operator = str(condition.get("operator", filter_cfg.get("operator", "CROSS_UP")) or "").strip().upper()
    period = _safe_int(raw_period)

    if not enabled:
        return True, _moving_average_detail(
            enabled=False,
            period=raw_period,
            operator=operator,
            current_value=None,
            ma_value=None,
            passed=True,
            reason="disabled",
            evaluation_index=evaluation_index,
        )
    if period is None or period <= 0:
        return False, _moving_average_detail(
            enabled=True,
            period=raw_period,
            operator=operator,
            current_value=None,
            ma_value=None,
            passed=False,
            reason="invalid_period",
            evaluation_index=evaluation_index,
        )
    if condition.get("target", "CLOSE") != "CLOSE" or condition.get("compare_target", "MA") != "MA":
        return False, _moving_average_detail(
            enabled=True,
            period=period,
            operator=operator,
            current_value=None,
            ma_value=None,
            passed=False,
            reason="unsupported_target",
            evaluation_index=evaluation_index,
        )

    ma_key = f"MA{period}"
    close_series = series_map.get("CLOSE")
    ma_series = series_map.get(ma_key)
    current_value = close_series[evaluation_index] if isinstance(close_series, list) and 0 <= evaluation_index < len(close_series) else None
    ma_value = ma_series[evaluation_index] if isinstance(ma_series, list) and 0 <= evaluation_index < len(ma_series) else None
    if current_value is None or ma_value is None:
        return False, _moving_average_detail(
            enabled=True,
            period=period,
            operator=operator,
            current_value=current_value,
            ma_value=ma_value,
            passed=False,
            reason="insufficient_data",
            evaluation_index=evaluation_index,
        )

    runtime_condition = {
        "enabled": True,
        "not": bool(condition.get("not", False)),
        "target": "CLOSE",
        "operator": operator,
        "compare_target": "MA",
        "period": period,
    }
    result = evaluate_condition(runtime_condition, series_map, evaluation_index)
    return result.passed, _moving_average_detail(
        enabled=True,
        period=period,
        operator=operator,
        current_value=round(current_value, 8),
        ma_value=round(ma_value, 8),
        passed=result.passed,
        reason="matched" if result.passed else "not_matched",
        evaluation_index=evaluation_index,
    )


def _buy_price_compare_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("price_compare"), dict):
        return filters["price_compare"]
    return {}


def _price_compare_detail(
    *,
    enabled: bool,
    target: Any,
    operator: Any,
    compare_target: Any,
    value: Any,
    passed: bool,
    reason: str,
    evaluation_index: int,
) -> str:
    return (
        "filter_type=PRICE_COMPARE "
        f"enabled={enabled} "
        f"target={target} "
        f"operator={operator} "
        f"compare_target={compare_target} "
        f"value={value} "
        f"passed={passed} "
        f"reason={reason} "
        f"evaluation_index={evaluation_index}"
    )


def _series_value_at(series_map: dict[str, list[float | None]], key: Any, index: int) -> float | None:
    series = series_map.get(str(key or "").strip().upper())
    if not isinstance(series, list):
        return None
    if index < 0:
        index = len(series) + index
    if index < 0 or index >= len(series):
        return None
    return series[index]


def _evaluate_buy_price_compare_filter(
    config: dict[str, Any],
    buy_cfg: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> tuple[bool, str | None]:
    filter_cfg = _buy_price_compare_filter_config(config, buy_cfg)
    if not filter_cfg:
        return True, None

    enabled = bool(filter_cfg.get("enabled", True))
    if not enabled:
        return True, _price_compare_detail(
            enabled=False,
            target=None,
            operator=None,
            compare_target=None,
            value=None,
            passed=True,
            reason="disabled",
            evaluation_index=evaluation_index,
        )

    conditions = filter_cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False, _price_compare_detail(
            enabled=True,
            target=None,
            operator=None,
            compare_target=None,
            value=None,
            passed=False,
            reason="missing_conditions",
            evaluation_index=evaluation_index,
        )

    logic = _logic(filter_cfg.get("conditions_logic", filter_cfg.get("logic", "AND")), "AND")
    condition_results = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        target = condition.get("target")
        compare_target = condition.get("compare_target")
        operator = condition.get("operator")
        if target not in {"CLOSE", "ORDER_PRICE", "AVG_PRICE"} or compare_target not in {"CLOSE", "ORDER_PRICE", "AVG_PRICE"}:
            return False, _price_compare_detail(
                enabled=True,
                target=target,
                operator=operator,
                compare_target=compare_target,
                value=condition.get("value"),
                passed=False,
                reason="unsupported_target",
                evaluation_index=evaluation_index,
            )
        if _series_value_at(series_map, target, evaluation_index) is None or _series_value_at(series_map, compare_target, evaluation_index) is None:
            return False, _price_compare_detail(
                enabled=True,
                target=target,
                operator=operator,
                compare_target=compare_target,
                value=condition.get("value"),
                passed=False,
                reason="insufficient_data",
                evaluation_index=evaluation_index,
            )
        result = evaluate_condition(condition, series_map, evaluation_index)
        condition_results.append((condition, result))

    if not condition_results:
        return False, _price_compare_detail(
            enabled=True,
            target=None,
            operator=None,
            compare_target=None,
            value=None,
            passed=False,
            reason="missing_conditions",
            evaluation_index=evaluation_index,
        )

    passed = all(result.passed for _, result in condition_results) if logic == "AND" else any(
        result.passed for _, result in condition_results
    )
    first_condition = condition_results[0][0]
    return passed, _price_compare_detail(
        enabled=True,
        target=first_condition.get("target"),
        operator=first_condition.get("operator"),
        compare_target=first_condition.get("compare_target"),
        value=first_condition.get("value"),
        passed=passed,
        reason="matched" if passed else "not_matched",
        evaluation_index=evaluation_index,
    )


def _buy_bollinger_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    """Get Bollinger filter configuration from buy filters."""
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("bollinger"), dict):
        return filters["bollinger"]
    return {}


def _bollinger_detail(
    *,
    enabled: bool,
    operator: Any,
    value: Any,
    close_price: Any,
    bollinger_value: Any,
    passed: bool,
    reason: str,
    evaluation_index: int,
) -> str:
    return (
        "filter_type=BOLLINGER "
        f"enabled={enabled} "
        "target=CLOSE "
        "compare_target=BOLLINGER "
        f"operator={operator} "
        f"value={value} "
        f"close_price={close_price} "
        f"bollinger_value={bollinger_value} "
        f"passed={passed} "
        f"reason={reason} "
        f"evaluation_index={evaluation_index}"
    )


def _evaluate_buy_bollinger_filter(
    config: dict[str, Any],
    buy_cfg: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> tuple[bool, str | None]:
    """Evaluate BUY Bollinger filter.

    The Bollinger filter compares the close price against the Bollinger Band value.
    - compare_target="BOLLINGER" refers to the lower Bollinger Band (for "above" conditions)
    - The value is the offset from the band (positive for above lower band, negative for below)
    """
    filter_cfg = _buy_bollinger_filter_config(config, buy_cfg)
    if not filter_cfg:
        return True, None

    conditions = filter_cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False, _bollinger_detail(
            enabled=True,
            operator=None,
            value=None,
            close_price=None,
            bollinger_value=None,
            passed=False,
            reason="missing_conditions",
            evaluation_index=evaluation_index,
        )

    # Process only the first condition (similar to other filters)
    condition = conditions[0] if conditions else {}
    if not isinstance(condition, dict):
        return False, _bollinger_detail(
            enabled=True,
            operator=None,
            value=None,
            close_price=None,
            bollinger_value=None,
            passed=False,
            reason="invalid_condition",
            evaluation_index=evaluation_index,
        )

    enabled = bool(filter_cfg.get("enabled", True))
    if not enabled:
        return True, _bollinger_detail(
            enabled=False,
            operator=None,
            value=None,
            close_price=None,
            bollinger_value=None,
            passed=True,
            reason="disabled",
            evaluation_index=evaluation_index,
        )

    operator = str(condition.get("operator", "")).strip().upper()
    raw_value = condition.get("value")
    compare_target = condition.get("compare_target")
    raw_period = condition.get("period")

    # Validate optional per-condition period override.
    # The band period is taken from config; if a condition supplies its own
    # period it must be a positive integer, otherwise the filter is blocked.
    if raw_period is not None:
        period = _safe_int(raw_period)
        if period is None or period <= 0:
            return False, _bollinger_detail(
                enabled=True,
                operator=operator,
                value=raw_value,
                close_price=None,
                bollinger_value=None,
                passed=False,
                reason="invalid_period",
                evaluation_index=evaluation_index,
            )

    # Validate operator
    if operator not in {">", ">=", "<", "<="}:
        return False, _bollinger_detail(
            enabled=True,
            operator=operator,
            value=raw_value,
            close_price=None,
            bollinger_value=None,
            passed=False,
            reason="unsupported_operator",
            evaluation_index=evaluation_index,
        )

    # Validate value (offset from the band must be numeric when supplied)
    value = _safe_float(raw_value)
    if raw_value is not None and value is None:
        return False, _bollinger_detail(
            enabled=True,
            operator=operator,
            value=raw_value,
            close_price=None,
            bollinger_value=None,
            passed=False,
            reason="invalid_value",
            evaluation_index=evaluation_index,
        )

    # Get close price
    close_series = series_map.get("CLOSE")
    close_price = close_series[evaluation_index] if isinstance(close_series, list) and 0 <= evaluation_index < len(close_series) else None

    # Get Bollinger band value
    bollinger_series = series_map.get("BOLLINGER")
    bollinger_value = bollinger_series[evaluation_index] if isinstance(bollinger_series, list) and 0 <= evaluation_index < len(bollinger_series) else None

    if close_price is None or bollinger_value is None:
        return False, _bollinger_detail(
            enabled=True,
            operator=operator,
            value=raw_value,
            close_price=close_price,
            bollinger_value=bollinger_value,
            passed=False,
            reason="insufficient_data",
            evaluation_index=evaluation_index,
        )

    # Calculate the threshold value
    # The value in the condition represents the offset from the Bollinger band
    threshold = bollinger_value + (value if value is not None else 0.0)

    # Evaluate the condition
    if operator == ">=":
        passed = close_price >= threshold
    elif operator == ">":
        passed = close_price > threshold
    elif operator == "<=":
        passed = close_price <= threshold
    elif operator == "<":
        passed = close_price < threshold
    else:
        passed = False

    return passed, _bollinger_detail(
        enabled=True,
        operator=operator,
        value=raw_value,
        close_price=round(close_price, 8),
        bollinger_value=round(bollinger_value, 8),
        passed=passed,
        reason="matched" if passed else "not_matched",
        evaluation_index=evaluation_index,
    )


def _buy_ocr_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("ocr"), dict):
        return filters["ocr"]
    return {}


def _ocr_detail(
    *,
    enabled: bool,
    logic: Any,
    passed: bool,
    reason: str,
    evaluation_index: int,
    condition_details: list[str] | None = None,
) -> str:
    detail = (
        "filter_type=OCR "
        f"enabled={enabled} "
        f"logic={logic} "
        f"passed={passed} "
        f"reason={reason} "
        f"evaluation_index={evaluation_index}"
    )
    if condition_details:
        detail += " condition_details=" + "|".join(str(item).replace(" ", "_") for item in condition_details)
    return detail


def _ocr_condition_data_available(
    condition: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> bool:
    target = str(condition.get("target", "OSC") or "OSC").strip().upper()
    series = series_map.get(target)
    if not isinstance(series, list):
        return False

    operator = str(condition.get("operator", "") or "").strip().upper()
    required_indexes = [evaluation_index]
    if operator in {"TURN_UP", "TURN_DOWN"}:
        required_indexes.extend([evaluation_index - 1, evaluation_index - 2])
    elif operator in {"TREND_UP", "TREND_DOWN", "CROSS_UP", "CROSS_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN"}:
        required_indexes.append(evaluation_index - 1)

    for index in required_indexes:
        if index < 0:
            return False
        if index >= len(series) or series[index] is None:
            return False

    compare_target = condition.get("compare_target")
    if compare_target:
        compare_condition = dict(condition)
        compare_key = "MA"
        if str(compare_target).strip().upper() == "MA":
            period = _safe_int(condition.get("period"))
            if period is None or period <= 0:
                return False
            compare_key = f"MA{period}"
        else:
            compare_key = str(compare_target).strip().upper()
        compare_series = series_map.get(compare_key)
        if not isinstance(compare_series, list):
            return False
        compare_indexes = [evaluation_index]
        if operator in {"CROSS_UP", "CROSS_DOWN"}:
            compare_indexes.append(evaluation_index - 1)
        for index in compare_indexes:
            if index < 0:
                return False
            if index >= len(compare_series) or compare_series[index] is None:
                return False
        compare_condition["compare_target"] = compare_target

    return True


def _ocr_condition_error(
    condition: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> str | None:
    target = str(condition.get("target", "OSC") or "OSC").strip().upper()
    operator = str(condition.get("operator", "") or "").strip().upper()
    supported_operators = {
        "TURN_UP", "TURN_DOWN", "TREND_UP", "TREND_DOWN",
        "CROSS_UP", "CROSS_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN",
        ">", ">=", "<", "<=", "=", "==", "GT", "GTE", "LT", "LTE", "EQ", "ABOVE", "BELOW",
    }

    if operator not in supported_operators:
        return "unsupported_operator"
    if target not in series_map:
        return "unsupported_target"
    if condition.get("compare_target") == "MA":
        period = _safe_int(condition.get("period"))
        if period is None or period <= 0:
            return "invalid_period"
    if operator in {">", ">=", "<", "<=", "=", "==", "GT", "GTE", "LT", "LTE", "EQ", "ABOVE", "BELOW"} and not condition.get("compare_target"):
        if _safe_float(condition.get("value")) is None:
            return "invalid_value"
    if not _ocr_condition_data_available(condition, series_map, evaluation_index):
        return "insufficient_data"
    return None


def _evaluate_buy_ocr_filter(
    config: dict[str, Any],
    buy_cfg: dict[str, Any],
    series_map: dict[str, list[float | None]],
    evaluation_index: int,
) -> tuple[bool, str | None]:
    filter_cfg = _buy_ocr_filter_config(config, buy_cfg)
    if not filter_cfg:
        return True, None

    enabled = bool(filter_cfg.get("enabled", True))
    logic = _logic(filter_cfg.get("conditions_logic", filter_cfg.get("logic", "AND")), "AND")
    if not enabled:
        return True, _ocr_detail(
            enabled=False,
            logic=logic,
            passed=True,
            reason="disabled",
            evaluation_index=evaluation_index,
        )

    conditions = filter_cfg.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False, _ocr_detail(
            enabled=True,
            logic=logic,
            passed=False,
            reason="missing_conditions",
            evaluation_index=evaluation_index,
        )

    condition_results = []
    condition_details: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            return False, _ocr_detail(
                enabled=True,
                logic=logic,
                passed=False,
                reason="invalid_condition",
                evaluation_index=evaluation_index,
                condition_details=condition_details,
            )
        runtime_condition = dict(condition)
        runtime_condition.setdefault("target", "OSC")
        error = _ocr_condition_error(runtime_condition, series_map, evaluation_index)
        if error is not None:
            condition_details.append(
                f"{runtime_condition.get('target')} {runtime_condition.get('operator')} reason={error}"
            )
            return False, _ocr_detail(
                enabled=True,
                logic=logic,
                passed=False,
                reason=error,
                evaluation_index=evaluation_index,
                condition_details=condition_details,
            )
        result = evaluate_condition(runtime_condition, series_map, evaluation_index)
        condition_results.append(result)
        condition_details.append(("PASS " if result.passed else "FAIL ") + result.detail)

    if not condition_results:
        return False, _ocr_detail(
            enabled=True,
            logic=logic,
            passed=False,
            reason="missing_conditions",
            evaluation_index=evaluation_index,
        )

    passed = all(result.passed for result in condition_results) if logic == "AND" else any(
        result.passed for result in condition_results
    )
    return passed, _ocr_detail(
        enabled=True,
        logic=logic,
        passed=passed,
        reason="matched" if passed else "not_matched",
        evaluation_index=evaluation_index,
        condition_details=condition_details,
    )


BUY_FILTER_ORDER = ("rsi", "moving_average", "price_compare", "bollinger", "ocr")


def _buy_composite_filter_config(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, Any]:
    filters = buy_cfg.get("filters")
    if isinstance(filters, dict) and "composite" in filters:
        composite = filters.get("composite")
        if isinstance(composite, dict):
            return composite
        return {"enabled": True, "_invalid_config": True}
    return {}


def _buy_filter_config_map(config: dict[str, Any], buy_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "rsi": _buy_rsi_filter_config(config, buy_cfg),
        "moving_average": _buy_moving_average_filter_config(config, buy_cfg),
        "price_compare": _buy_price_compare_filter_config(config, buy_cfg),
        "bollinger": _buy_bollinger_filter_config(config, buy_cfg),
        "ocr": _buy_ocr_filter_config(config, buy_cfg),
    }


def _composite_logic(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"AND", "OR"} else None


def _composite_detail(
    *,
    enabled: bool,
    logic: Any,
    passed: bool,
    reason: str,
    referenced_filters: list[str],
    unreferenced_required_filters: list[str],
    group_results: list[str],
) -> str:
    return (
        "filter_type=COMPOSITE "
        f"enabled={enabled} "
        f"logic={logic} "
        f"passed={passed} "
        f"reason={reason} "
        f"referenced_filters={','.join(referenced_filters)} "
        f"unreferenced_required_filters={','.join(unreferenced_required_filters)} "
        f"group_results={';'.join(group_results)}"
    )


def _evaluate_buy_composite_filter(
    composite_cfg: dict[str, Any],
    filter_results: dict[str, dict[str, Any]],
) -> tuple[bool, str | None]:
    if not composite_cfg or not bool(composite_cfg.get("enabled", False)):
        return True, None

    if composite_cfg.get("_invalid_config"):
        return False, _composite_detail(
            enabled=True,
            logic=composite_cfg.get("logic"),
            passed=False,
            reason="invalid_config",
            referenced_filters=[],
            unreferenced_required_filters=[],
            group_results=[],
        )

    top_logic = _composite_logic(composite_cfg.get("logic", "AND"))
    if top_logic is None:
        return False, _composite_detail(
            enabled=True,
            logic=composite_cfg.get("logic"),
            passed=False,
            reason="invalid_logic",
            referenced_filters=[],
            unreferenced_required_filters=[],
            group_results=[],
        )

    include_policy = str(composite_cfg.get("include_unreferenced_active_filters", "AND_REQUIRED") or "").strip().upper()
    if include_policy != "AND_REQUIRED":
        return False, _composite_detail(
            enabled=True,
            logic=top_logic,
            passed=False,
            reason="unsupported_unreferenced_policy",
            referenced_filters=[],
            unreferenced_required_filters=[],
            group_results=[],
        )

    groups = composite_cfg.get("groups")
    if not isinstance(groups, list) or not groups:
        return False, _composite_detail(
            enabled=True,
            logic=top_logic,
            passed=False,
            reason="missing_groups",
            referenced_filters=[],
            unreferenced_required_filters=[],
            group_results=[],
        )

    active_group_seen = False
    referenced_filters: list[str] = []
    group_passes: list[bool] = []
    group_results: list[str] = []

    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            return False, _composite_detail(
                enabled=True,
                logic=top_logic,
                passed=False,
                reason="invalid_group",
                referenced_filters=referenced_filters,
                unreferenced_required_filters=[],
                group_results=group_results,
            )
        if not bool(group.get("enabled", True)):
            continue
        active_group_seen = True
        group_logic = _composite_logic(group.get("logic", "AND"))
        if group_logic is None:
            return False, _composite_detail(
                enabled=True,
                logic=top_logic,
                passed=False,
                reason="invalid_group_logic",
                referenced_filters=referenced_filters,
                unreferenced_required_filters=[],
                group_results=group_results,
            )
        filters = group.get("filters")
        if not isinstance(filters, list) or not filters:
            return False, _composite_detail(
                enabled=True,
                logic=top_logic,
                passed=False,
                reason="missing_group_filters",
                referenced_filters=referenced_filters,
                unreferenced_required_filters=[],
                group_results=group_results,
            )

        seen_in_group: set[str] = set()
        active_filter_passes: list[bool] = []
        group_tokens: list[str] = []
        for raw_name in filters:
            name = str(raw_name or "").strip().lower()
            if name == "composite":
                return False, _composite_detail(
                    enabled=True,
                    logic=top_logic,
                    passed=False,
                    reason="self_reference",
                    referenced_filters=referenced_filters,
                    unreferenced_required_filters=[],
                    group_results=group_results,
                )
            if name not in BUY_FILTER_ORDER:
                return False, _composite_detail(
                    enabled=True,
                    logic=top_logic,
                    passed=False,
                    reason="unknown_filter",
                    referenced_filters=referenced_filters,
                    unreferenced_required_filters=[],
                    group_results=group_results,
                )
            if name in seen_in_group:
                return False, _composite_detail(
                    enabled=True,
                    logic=top_logic,
                    passed=False,
                    reason="duplicate_filter_in_group",
                    referenced_filters=referenced_filters,
                    unreferenced_required_filters=[],
                    group_results=group_results,
                )
            seen_in_group.add(name)
            if name not in referenced_filters:
                referenced_filters.append(name)
            result = filter_results.get(name)
            if not isinstance(result, dict):
                return False, _composite_detail(
                    enabled=True,
                    logic=top_logic,
                    passed=False,
                    reason="missing_filter_result",
                    referenced_filters=referenced_filters,
                    unreferenced_required_filters=[],
                    group_results=group_results,
                )
            if not result.get("configured", False):
                return False, _composite_detail(
                    enabled=True,
                    logic=top_logic,
                    passed=False,
                    reason="missing_filter_result",
                    referenced_filters=referenced_filters,
                    unreferenced_required_filters=[],
                    group_results=group_results,
                )
            if not result.get("enabled", True):
                group_tokens.append(f"{name}:disabled_ignored")
                continue
            passed = bool(result.get("passed", False))
            active_filter_passes.append(passed)
            group_tokens.append(f"{name}:{'PASS' if passed else 'FAIL'}")

        if not active_filter_passes:
            return False, _composite_detail(
                enabled=True,
                logic=top_logic,
                passed=False,
                reason="group_has_no_active_filters",
                referenced_filters=referenced_filters,
                unreferenced_required_filters=[],
                group_results=group_results + [f"group{group_index}:BLOCK:{','.join(group_tokens)}"],
            )

        group_passed = all(active_filter_passes) if group_logic == "AND" else any(active_filter_passes)
        group_passes.append(group_passed)
        group_results.append(f"group{group_index}:{group_logic}:{'PASS' if group_passed else 'FAIL'}:{','.join(group_tokens)}")

    if not active_group_seen:
        return False, _composite_detail(
            enabled=True,
            logic=top_logic,
            passed=False,
            reason="all_groups_disabled",
            referenced_filters=referenced_filters,
            unreferenced_required_filters=[],
            group_results=group_results,
        )

    composite_passed = all(group_passes) if top_logic == "AND" else any(group_passes)
    unreferenced_required = [
        name
        for name in BUY_FILTER_ORDER
        if name not in referenced_filters
        and isinstance(filter_results.get(name), dict)
        and filter_results[name].get("configured", False)
        and filter_results[name].get("enabled", True)
    ]
    unreferenced_passed = all(bool(filter_results[name].get("passed", False)) for name in unreferenced_required)
    passed = composite_passed and unreferenced_passed
    if passed:
        reason = "matched"
    elif not composite_passed:
        reason = "not_matched"
    else:
        reason = "unreferenced_required_failed"

    return passed, _composite_detail(
        enabled=True,
        logic=top_logic,
        passed=passed,
        reason=reason,
        referenced_filters=referenced_filters,
        unreferenced_required_filters=unreferenced_required,
        group_results=group_results,
    )


def _context_float(context: dict[str, Any] | None, keys: tuple[str, ...], nested: tuple[tuple[str, ...], ...] = ()) -> float | None:
    if not isinstance(context, dict):
        return None
    for key in keys:
        value = _safe_float(context.get(key))
        if value is not None:
            return value
    for path in nested:
        value = _safe_float(_nested_get(context, *path))
        if value is not None:
            return value
    return None


def _enrich_price_compare_series(series_map: dict[str, list[float | None]], context: dict[str, Any] | None) -> None:
    close_series = series_map.get("CLOSE")
    length = len(close_series) if isinstance(close_series, list) else 0
    order_price = _context_float(
        context,
        ("order_price", "buy_order_price", "planned_order_price", "mock_order_price"),
        (("order", "price"), ("planned_order", "price"), ("buy", "order_price")),
    )
    average_price = _context_float(
        context,
        ("average_price", "avg_price", "position_average_price", "mock_average_price"),
        (("position", "average_price"), ("position", "avg_price"), ("holding", "average_price"), ("holding", "avg_price")),
    )
    if order_price is not None:
        series_map["ORDER_PRICE"] = [order_price] * length
    if average_price is not None:
        series_map["AVG_PRICE"] = [average_price] * length


def _context_holding_qty(context: dict[str, Any] | None) -> float | None:
    return _context_float(
        context,
        ("holding_qty", "hold_qty", "quantity", "qty", "mock_holding_qty"),
        (("position", "quantity"), ("position", "qty"), ("holding", "quantity"), ("holding", "qty")),
    )


def _context_average_price(context: dict[str, Any] | None) -> float | None:
    return _context_float(
        context,
        ("average_price", "avg_price", "holding_average_price", "buy_average_price", "mock_average_price"),
        (("position", "average_price"), ("position", "avg_price"), ("holding", "average_price"), ("holding", "avg_price")),
    )


def _context_current_price(context: dict[str, Any] | None, candles: list[dict[str, Any]], index: int) -> float | None:
    value = _context_float(
        context,
        ("current_price",),
    )
    if value is not None:
        return value
    if candles and 0 <= index < len(candles):
        return _safe_float(candles[index].get("close"))
    if candles:
        return _safe_float(candles[-1].get("close"))
    return None


def _evaluate_profit_rate_sell(
    profit_cfg: dict[str, Any],
    candles: list[dict[str, Any]],
    signal_index: int,
    context: dict[str, Any] | None,
) -> tuple[bool, str | None, list[str]]:
    """평단 대비 수익률 SELL을 평가한다.

    원칙:
    - HOLD/CANCEL 등 신규 신호를 만들지 않는다.
    - enabled=false면 비활성으로 처리한다.
    - 보유수량 정보가 있고 0 이하이면 평가하지 않는다.
    - 평단이 없거나 0 이하이면 평가하지 않는다.
    - 현재가는 context 우선, 없으면 신호봉 close를 사용한다.
    """
    if not isinstance(profit_cfg, dict) or not profit_cfg.get("enabled", False):
        return False, None, ["profit_rate_sell 비활성"]

    target_rate = _safe_float(profit_cfg.get("profit_rate_percent"))
    if target_rate is None:
        target_rate = _safe_float(profit_cfg.get("target_profit_rate"))
    if target_rate is None:
        return False, "profit_rate_sell", ["profit_rate_sell 목표수익률 없음"]

    holding_qty = _context_holding_qty(context)
    if holding_qty is not None and holding_qty <= 0:
        return False, "profit_rate_sell", ["profit_rate_sell 보유수량 없음"]

    average_price = _context_average_price(context)
    if average_price is None or average_price <= 0:
        return False, "profit_rate_sell", ["profit_rate_sell 평단 없음"]

    current_price = _context_current_price(context, candles, signal_index)
    if current_price is None or current_price <= 0:
        return False, "profit_rate_sell", ["profit_rate_sell 현재가 없음"]

    profit_rate = ((current_price - average_price) / average_price) * 100.0
    passed = profit_rate >= target_rate
    detail = (
        f"profit_rate_sell 평단대비수익률 {profit_rate:.4f}% "
        f">= 목표 {target_rate:.4f}% "
        f"(평단={average_price}, 현재가={current_price})"
    )
    return passed, "profit_rate_sell", [("PASS " if passed else "FAIL ") + detail]


def evaluate_indicator_follow_routine(
    candles: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> RoutineSignal:
    """MACD 루틴을 평가한다.

    반환 원칙:
    - BUY / SELL 조건 충족 시에만 공식 주문신호를 반환한다.
    - 조건 미충족, 데이터 부족, 루틴 비활성은 signal=None으로 반환한다.
    - 비신호를 별도 주문신호로 승격하지 않는다.
    """
    cfg = config if isinstance(config, dict) else DEFAULT_INDICATOR_FOLLOW_CONFIG
    if isinstance(context, dict) and isinstance(context.get("candles"), list):
        candles = context["candles"]

    if not cfg.get("enabled", True):
        return RoutineSignal(None, "루틴 비활성", [], [], -1, 0)

    if len(candles) < 3:
        return RoutineSignal(None, "봉데이터 부족", [], [], -1, 0)

    series_map = build_indicator_series(candles, cfg)
    _enrich_price_compare_series(series_map, context)

    buy_cfg = _section(cfg, "buy")
    sell_cfg = _section(cfg, "sell")
    macd_sell_cfg = _macd_sell_section(sell_cfg)
    profit_rate_sell_cfg = _profit_rate_sell_section(sell_cfg)
    condition_sell_signals = _condition_sell_signals(sell_cfg)

    buy_delay = int(buy_cfg.get("delay_bar", 1) or 0)
    sell_delay = int(macd_sell_cfg.get("delay_bar", sell_cfg.get("delay_bar", 1)) or 0)

    # SELL을 먼저 평가한다.
    # 실제 주문 실행 가능 여부는 메인에서 다시 판단한다.
    # STEP23 범위: MACD SELL + 평단 대비 profit_rate_sell + sell.signal_logic(OR/AND).
    sell_index = _delay_index(candles, sell_delay)
    condition_sell_passed: dict[str, bool] = {}
    condition_sell_results = {}
    for signal_name, signal_cfg in condition_sell_signals.items():
        signal_groups = signal_cfg.get("groups", []) if isinstance(signal_cfg.get("groups"), list) else []
        if bool(signal_cfg.get("enabled", True)):
            passed, results = evaluate_groups_or(signal_groups, series_map, sell_index)
        else:
            passed, results = False, []
        condition_sell_passed[signal_name] = passed
        condition_sell_results[signal_name] = results

    profit_passed, profit_name, profit_details = _evaluate_profit_rate_sell(
        profit_rate_sell_cfg, candles, sell_index, context
    )

    active_sell_names: list[str] = []
    for signal_name, signal_cfg in condition_sell_signals.items():
        if bool(signal_cfg.get("enabled", True)):
            active_sell_names.append(signal_name)
    if isinstance(profit_rate_sell_cfg, dict) and profit_rate_sell_cfg.get("enabled", False):
        active_sell_names.append("profit_rate_sell")

    sell_logic = _logic(sell_cfg.get("signal_logic", "OR"), "OR")
    if not active_sell_names:
        sell_passed = False
    elif sell_logic == "AND":
        signal_pass_map = dict(condition_sell_passed)
        signal_pass_map["profit_rate_sell"] = profit_passed
        sell_passed = all(signal_pass_map.get(name, False) for name in active_sell_names)
    else:
        sell_passed = any(condition_sell_passed.get(name, False) for name in active_sell_names) or profit_passed

    if sell_passed:
        matched = [
            result.group_name
            for signal_name in active_sell_names
            for result in condition_sell_results.get(signal_name, [])
            if result.passed
        ]
        if profit_passed and profit_name:
            matched.append(profit_name)
        details = [
            detail
            for signal_name in active_sell_names
            for result in condition_sell_results.get(signal_name, [])
            for detail in result.details
        ] + profit_details
        return RoutineSignal("SELL", f"매도조건 충족({sell_logic})", matched, details, sell_index, sell_delay)

    buy_index = _delay_index(candles, buy_delay)
    buy_groups = buy_cfg.get("groups", []) if isinstance(buy_cfg.get("groups"), list) else []
    buy_passed, buy_results = evaluate_groups_or(buy_groups, series_map, buy_index)
    if buy_passed:
        matched = [result.group_name for result in buy_results if result.passed]
        details = [detail for result in buy_results for detail in result.details]
        composite_cfg = _buy_composite_filter_config(cfg, buy_cfg)
        if composite_cfg and bool(composite_cfg.get("enabled", False)):
            filter_cfgs = _buy_filter_config_map(cfg, buy_cfg)
            filter_results: dict[str, dict[str, Any]] = {}

            rsi_passed, rsi_detail = _evaluate_buy_rsi_filter(cfg, buy_cfg, candles, buy_index)
            if rsi_detail:
                details.append(rsi_detail)
            filter_results["rsi"] = {
                "passed": rsi_passed,
                "detail": rsi_detail,
                "configured": bool(filter_cfgs["rsi"]),
                "enabled": bool(filter_cfgs["rsi"].get("enabled", True)) if filter_cfgs["rsi"] else False,
            }

            ma_passed, ma_detail = _evaluate_buy_moving_average_filter(cfg, buy_cfg, series_map, buy_index)
            if ma_detail:
                details.append(ma_detail)
            filter_results["moving_average"] = {
                "passed": ma_passed,
                "detail": ma_detail,
                "configured": bool(filter_cfgs["moving_average"]),
                "enabled": bool(filter_cfgs["moving_average"].get("enabled", True)) if filter_cfgs["moving_average"] else False,
            }

            price_compare_passed, price_compare_detail = _evaluate_buy_price_compare_filter(cfg, buy_cfg, series_map, buy_index)
            if price_compare_detail:
                details.append(price_compare_detail)
            filter_results["price_compare"] = {
                "passed": price_compare_passed,
                "detail": price_compare_detail,
                "configured": bool(filter_cfgs["price_compare"]),
                "enabled": bool(filter_cfgs["price_compare"].get("enabled", True)) if filter_cfgs["price_compare"] else False,
            }

            bollinger_passed, bollinger_detail = _evaluate_buy_bollinger_filter(cfg, buy_cfg, series_map, buy_index)
            if bollinger_detail:
                details.append(bollinger_detail)
            filter_results["bollinger"] = {
                "passed": bollinger_passed,
                "detail": bollinger_detail,
                "configured": bool(filter_cfgs["bollinger"]),
                "enabled": bool(filter_cfgs["bollinger"].get("enabled", True)) if filter_cfgs["bollinger"] else False,
            }

            ocr_passed, ocr_detail = _evaluate_buy_ocr_filter(cfg, buy_cfg, series_map, buy_index)
            if ocr_detail:
                details.append(ocr_detail)
            filter_results["ocr"] = {
                "passed": ocr_passed,
                "detail": ocr_detail,
                "configured": bool(filter_cfgs["ocr"]),
                "enabled": bool(filter_cfgs["ocr"].get("enabled", True)) if filter_cfgs["ocr"] else False,
            }

            composite_passed, composite_detail = _evaluate_buy_composite_filter(composite_cfg, filter_results)
            if composite_detail:
                details.append(composite_detail)
            if not composite_passed:
                return RoutineSignal(None, "BUY composite filter blocked", matched, details, buy_index, buy_delay)
            return RoutineSignal("BUY", "매수조건 충족", matched, details, buy_index, buy_delay)

        rsi_passed, rsi_detail = _evaluate_buy_rsi_filter(cfg, buy_cfg, candles, buy_index)
        if rsi_detail:
            details.append(rsi_detail)
        if not rsi_passed:
            return RoutineSignal(None, "BUY RSI filter blocked", matched, details, buy_index, buy_delay)
        ma_passed, ma_detail = _evaluate_buy_moving_average_filter(cfg, buy_cfg, series_map, buy_index)
        if ma_detail:
            details.append(ma_detail)
        if not ma_passed:
            return RoutineSignal(None, "BUY moving average filter blocked", matched, details, buy_index, buy_delay)
        price_compare_passed, price_compare_detail = _evaluate_buy_price_compare_filter(cfg, buy_cfg, series_map, buy_index)
        if price_compare_detail:
            details.append(price_compare_detail)
        if not price_compare_passed:
            return RoutineSignal(None, "BUY price compare filter blocked", matched, details, buy_index, buy_delay)
        bollinger_passed, bollinger_detail = _evaluate_buy_bollinger_filter(cfg, buy_cfg, series_map, buy_index)
        if bollinger_detail:
            details.append(bollinger_detail)
        if not bollinger_passed:
            return RoutineSignal(None, "BUY bollinger filter blocked", matched, details, buy_index, buy_delay)
        ocr_passed, ocr_detail = _evaluate_buy_ocr_filter(cfg, buy_cfg, series_map, buy_index)
        if ocr_detail:
            details.append(ocr_detail)
        if not ocr_passed:
            return RoutineSignal(None, "BUY OCR filter blocked", matched, details, buy_index, buy_delay)
        return RoutineSignal("BUY", "매수조건 충족", matched, details, buy_index, buy_delay)

    sell_results = [
        result
        for results in condition_sell_results.values()
        for result in results
    ]
    details = [detail for result in sell_results + buy_results for detail in result.details]
    return RoutineSignal(None, "조건 미충족", [], details, len(candles) - 1, 0)


evaluate_macd_routine = evaluate_indicator_follow_routine
