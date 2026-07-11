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
        return RoutineSignal("BUY", "매수조건 충족", matched, details, buy_index, buy_delay)

    sell_results = [
        result
        for results in condition_sell_results.values()
        for result in results
    ]
    details = [detail for result in sell_results + buy_results for detail in result.details]
    return RoutineSignal(None, "조건 미충족", [], details, len(candles) - 1, 0)


evaluate_macd_routine = evaluate_indicator_follow_routine
