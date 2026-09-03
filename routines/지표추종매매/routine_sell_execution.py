# -*- coding: utf-8 -*-
"""Indicator-follow SELL intent construction."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_provenance_contract import stable_hash

try:
    from krx_tick_price import move_krx_price_by_ticks
except Exception:  # pragma: no cover
    move_krx_price_by_ticks = None


STATUS_READY = "READY"
_METHOD_SET_ORDER = ("setting_a", "setting_b", "setting_c")
_SINGLE_HOGA_TERMS = {"SINGLE", "SINGLE_HOGA", "단일호가"}
_MULTI_HOGA_TERMS = {"MULTI", "MULTI_HOGA", "다중호가"}
_ORDER_PRICE_TERMS = {"ORDER_PRICE", "LIMIT", "주문가"}
_MARKET_TERMS = {"MARKET", "시장가"}
_NO_SPLIT_TERMS = {"", "NONE", "선택없음"}
_MULTI_TIME_TERMS = {"MULTI_TIME", "다중시간"}
_MULTI_RATIO_TERMS = {"MULTI_RATIO", "다중비율"}
_TIME_ORDER_PRICE_TERMS = {"ORDER_PRICE", "LIMIT", "주문가"}
_TIME_CURRENT_PRICE_TERMS = {"CURRENT_PRICE", "현재가"}
_RATIO_ORDER_PRICE_TERMS = {"ORDER_PRICE", "주문가"}
_RATIO_CURRENT_PRICE_TERMS = {"CURRENT_PRICE", "현재가"}
_RATIO_AVG_PRICE_TERMS = {"AVG_PRICE", "AVERAGE_PRICE", "평단가"}
_RATIO_UP_TERMS = {"UP", "상향"}
_RATIO_DOWN_TERMS = {"DOWN", "하향"}
_RATIO_BOTH_TERMS = {"BOTH", "상하"}
_RATIO_GTE_TERMS = {">=", "이상"}
_RATIO_LTE_TERMS = {"<=", "이하"}
_RATIO_WITHIN_TERMS = {"WITHIN", "이내"}
_RATIO_OUTSIDE_TERMS = {"OUTSIDE", "이탈"}
_PENDING_TERMS = {"PENDING", "PENDING_ORDER", "미체결"}
_PENDING_EACH_TERMS = {"EACH", "매회"}
_PENDING_BATCH_TERMS = {"BATCH", "일괄"}
_PRICE_COMPARE_TERMS = {"PRICE_COMPARE", "가격비교"}
_SELL_RESET_TERMS = {"SELL_RESET", "RESET", "매도리셋"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_term(value: Any) -> str:
    text = _text(value)
    return text if any("가" <= char <= "힣" for char in text) else text.upper()


def _checked(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalized_term(value) in {"1", "TRUE", "YES", "Y", "ON", "CHECKED"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _nonnegative_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return int(number) if number.is_integer() else number


def _ratio_price_source(value: Any) -> str | None:
    term = _normalized_term(value)
    if term in _RATIO_ORDER_PRICE_TERMS:
        return "ORDER_PRICE"
    if term in _RATIO_CURRENT_PRICE_TERMS:
        return "CURRENT_PRICE"
    if term in _RATIO_AVG_PRICE_TERMS:
        return "AVG_PRICE"
    return None


def _ratio_direction(value: Any) -> str | None:
    term = _normalized_term(value)
    if term in _RATIO_UP_TERMS:
        return "UP"
    if term in _RATIO_DOWN_TERMS:
        return "DOWN"
    if term in _RATIO_BOTH_TERMS:
        return "BOTH"
    return None


def _ratio_compare(value: Any, direction: str) -> str | None:
    term = _normalized_term(value)
    if direction in {"UP", "DOWN"}:
        if term in _RATIO_GTE_TERMS:
            return ">="
        if term in _RATIO_LTE_TERMS:
            return "<="
        return None
    if direction == "BOTH":
        if term in _RATIO_WITHIN_TERMS:
            return "WITHIN"
        if term in _RATIO_OUTSIDE_TERMS:
            return "OUTSIDE"
    return None


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "execution_intent": None,
        "execution_intents": [],
    }


def _hoga_offsets(up_count: int, down_count: int) -> list[int]:
    offsets = [0]
    for distance in range(1, max(up_count, down_count) + 1):
        if distance <= up_count:
            offsets.append(distance)
        if distance <= down_count:
            offsets.append(-distance)
    return offsets


def _split_quantity(total_quantity: int, child_count: int) -> list[int]:
    quotient, remainder = divmod(total_quantity, child_count)
    return [quotient + (1 if index < remainder else 0) for index in range(child_count)]


def _time_unit_milliseconds(value: Any, context: dict[str, Any]) -> int | None:
    unit = _normalized_term(value)
    if unit in {"SECOND", "SECONDS", "초"}:
        return 1_000
    if unit in {"MINUTE", "MINUTES", "분"}:
        return 60_000
    if unit not in {"BAR", "BARS", "봉"}:
        return None
    candles = context.get("candles")
    if not isinstance(candles, list):
        candles = context.get("bars")
    if not isinstance(candles, list):
        candles = context.get("ohlcv")
    if not isinstance(candles, list):
        return None
    timeframe = next(
        (
            _positive_int(item.get("timeframe_minutes"))
            for item in reversed(candles)
            if isinstance(item, dict) and item.get("timeframe_minutes") not in (None, "")
        ),
        None,
    )
    return timeframe * 60_000 if timeframe is not None else None


def _unfilled_timeout_policy(
    setting: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    mode = _normalized_term(setting.get("perform3_title_combo"))
    if mode not in _PENDING_TERMS:
        return None, ""
    scope_term = _normalized_term(setting.get("perform3_pending_scope"))
    if scope_term in _PENDING_EACH_TERMS:
        scope = "EACH"
    elif scope_term in _PENDING_BATCH_TERMS:
        scope = "BATCH"
    else:
        return None, "SELL_UNFILLED_TIMEOUT_SCOPE_INVALID"
    value = _nonnegative_number(setting.get("perform3_pending_value"))
    unit_ms = _time_unit_milliseconds(
        setting.get("perform3_pending_unit"),
        context,
    )
    if value is None:
        return None, "SELL_UNFILLED_TIMEOUT_VALUE_INVALID"
    if unit_ms is None:
        return None, "SELL_UNFILLED_TIMEOUT_UNIT_UNRESOLVED"
    timeout_ms = value * unit_ms
    if not float(timeout_ms).is_integer():
        return None, "SELL_UNFILLED_TIMEOUT_VALUE_INVALID"
    return {
        "policy": "CANCEL_PENDING_ORDER",
        "scope": scope,
        "timeout_ms": int(timeout_ms),
        "configured_value": value,
        "configured_unit": _text(setting.get("perform3_pending_unit")),
        "anchor": "BROKER_ACCEPTED_AT",
    }, ""


def _price_reset_policy(
    setting: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    mode = _normalized_term(setting.get("perform3_title_combo"))
    if mode not in _PRICE_COMPARE_TERMS:
        return None, ""
    action = _normalized_term(setting.get("perform3_price_action"))
    if action not in _SELL_RESET_TERMS:
        return None, ""
    left = _ratio_price_source(setting.get("perform3_price_left"))
    right = _ratio_price_source(setting.get("perform3_price_right"))
    direction = _ratio_direction(setting.get("perform3_price_direction"))
    compare = _ratio_compare(setting.get("perform3_price_compare"), direction or "")
    threshold = _positive_number(setting.get("perform3_price_value"))
    order_price = _positive_number(
        context.get("reference_price", context.get("current_price"))
    )
    if (
        left is None
        or right is None
        or direction is None
        or compare is None
        or threshold is None
    ):
        return None, "SELL_PRICE_RESET_POLICY_INVALID"
    if "ORDER_PRICE" in {left, right} and order_price is None:
        return None, "SELL_PRICE_RESET_ORDER_PRICE_MISSING"
    return {
        "policy": "SELL_PRICE_CHANGE_RESET",
        "action": "RESET",
        "left_source": left,
        "right_source": right,
        "direction": direction,
        "compare": compare,
        "threshold_percent": threshold,
        "order_price": order_price,
    }, ""


def _repeat_execution_policy(
    setting: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    repeat_keys = {
        _text(key)[len("repeat_"):]: deepcopy(value)
        for key, value in setting.items()
        if _text(key).startswith("repeat_perform")
    }
    if not repeat_keys:
        return None, ""

    hoga_mode = _normalized_term(repeat_keys.get("perform1_title_combo"))
    split_mode = _normalized_term(repeat_keys.get("perform2_title_combo"))
    template: dict[str, Any]
    if split_mode in _MULTI_TIME_TERMS:
        if hoga_mode in _MULTI_HOGA_TERMS:
            return None, "SELL_REPEAT_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED"
        if hoga_mode not in _SINGLE_HOGA_TERMS:
            return None, "SELL_REPEAT_MULTI_TIME_PERFORM1_INVALID"
        configured_count = _positive_int(repeat_keys.get("perform2_time_count"))
        time_value = _positive_int(repeat_keys.get("perform2_time_value"))
        unit_milliseconds = _time_unit_milliseconds(
            repeat_keys.get("perform2_time_unit"),
            context,
        )
        range_mode = _normalized_term(repeat_keys.get("perform2_time_range"))
        time_order = _normalized_term(repeat_keys.get("perform2_time_order"))
        if configured_count is None or time_value is None or unit_milliseconds is None:
            return None, "SELL_REPEAT_MULTI_TIME_PLAN_INVALID"
        if range_mode not in {"WITHIN", "이내", "INTERVAL", "간격"}:
            return None, "SELL_REPEAT_MULTI_TIME_RANGE_INVALID"
        if time_order in _TIME_ORDER_PRICE_TERMS:
            price_basis = "ORDER_PRICE"
        elif time_order in _TIME_CURRENT_PRICE_TERMS:
            price_basis = "CURRENT_PRICE"
        else:
            return None, "SELL_REPEAT_MULTI_TIME_PRICE_POLICY_INVALID"
        template = {
            "execution_mode": "MULTI_TIME",
            "hoga": "LIMIT",
            "price_basis": price_basis,
            "configured_child_count": configured_count,
            "time_value": time_value,
            "time_unit_milliseconds": unit_milliseconds,
            "time_unit": _text(repeat_keys.get("perform2_time_unit")),
            "time_range": range_mode,
            "sell_perform1": {"hoga_mode": "SINGLE", "price_basis": price_basis},
            "sell_perform2": {"split_mode": "MULTI_TIME", "price_basis": price_basis},
        }
    elif split_mode in _MULTI_RATIO_TERMS:
        if hoga_mode in _MULTI_HOGA_TERMS:
            return None, "SELL_REPEAT_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED"
        if hoga_mode not in _SINGLE_HOGA_TERMS:
            return None, "SELL_REPEAT_MULTI_RATIO_PERFORM1_INVALID"
        order_method = _normalized_term(repeat_keys.get("perform1_single_combo"))
        if order_method in _ORDER_PRICE_TERMS:
            hoga = "LIMIT"
            price_basis = "ORDER_PRICE"
        elif order_method in _MARKET_TERMS:
            hoga = "MARKET"
            price_basis = "MARKET"
        else:
            return None, "SELL_REPEAT_SINGLE_HOGA_PRICE_POLICY_INVALID"
        configured_count = _positive_int(repeat_keys.get("perform2_ratio_count"))
        ratio_value = _positive_number(repeat_keys.get("perform2_ratio_value"))
        ratio_left = _ratio_price_source(repeat_keys.get("perform2_ratio_left"))
        ratio_right = _ratio_price_source(repeat_keys.get("perform2_ratio_right"))
        direction = _ratio_direction(repeat_keys.get("perform2_ratio_direction"))
        compare = _ratio_compare(repeat_keys.get("perform2_ratio_compare"), direction or "")
        if (
            configured_count is None
            or ratio_value is None
            or ratio_left is None
            or ratio_right is None
            or direction is None
            or compare is None
        ):
            return None, "SELL_REPEAT_MULTI_RATIO_PLAN_INVALID"
        template = {
            "execution_mode": "MULTI_RATIO",
            "hoga": hoga,
            "price_basis": price_basis,
            "configured_child_count": configured_count,
            "ratio_left": ratio_left,
            "ratio_right": ratio_right,
            "ratio_direction": direction,
            "ratio_value": ratio_value,
            "ratio_compare": compare,
            "sell_perform1": {"hoga_mode": "SINGLE", "price_basis": price_basis},
            "sell_perform2": {"split_mode": "MULTI_RATIO"},
        }
    elif split_mode in _NO_SPLIT_TERMS:
        if hoga_mode in _MULTI_HOGA_TERMS:
            if not callable(move_krx_price_by_ticks):
                return None, "SELL_TICK_PRICE_PRIMITIVE_UNAVAILABLE"
            up_count = _nonnegative_int(repeat_keys.get("perform1_multi_up_line"))
            down_count = _nonnegative_int(repeat_keys.get("perform1_multi_down_line"))
            if up_count is None or down_count is None:
                return None, "SELL_REPEAT_MULTI_HOGA_RANGE_INVALID"
            template = {
                "execution_mode": "MULTI_HOGA",
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
                "hoga_offsets": _hoga_offsets(up_count, down_count),
                "hoga_up": up_count,
                "hoga_down": down_count,
                "instrument_type": str(
                    context.get("instrument_classification")
                    or context.get("instrument_type")
                    or "STOCK"
                ),
                "sell_perform1": {"hoga_mode": "MULTI", "price_basis": "ORDER_PRICE"},
            }
        elif hoga_mode in _SINGLE_HOGA_TERMS:
            order_method = _normalized_term(repeat_keys.get("perform1_single_combo"))
            if order_method in _ORDER_PRICE_TERMS:
                hoga = "LIMIT"
                price_basis = "ORDER_PRICE"
            elif order_method in _MARKET_TERMS:
                hoga = "MARKET"
                price_basis = "MARKET"
            else:
                return None, "SELL_REPEAT_SINGLE_HOGA_PRICE_POLICY_INVALID"
            template = {
                "execution_mode": "SINGLE_ORDER",
                "hoga": hoga,
                "price_basis": price_basis,
                "sell_perform1": {"hoga_mode": "SINGLE", "price_basis": price_basis},
            }
        else:
            return None, "SELL_REPEAT_PERFORM1_POLICY_INVALID"
    else:
        return None, "SELL_REPEAT_PERFORM2_POLICY_INVALID"

    unfilled_policy, unfilled_reason = _unfilled_timeout_policy(repeat_keys, context)
    if unfilled_reason:
        return None, unfilled_reason.replace("SELL_", "SELL_REPEAT_", 1)
    reset_policy, reset_reason = _price_reset_policy(repeat_keys, context)
    if reset_reason:
        return None, reset_reason.replace("SELL_", "SELL_REPEAT_", 1)
    if reset_policy is not None:
        reset_policy = deepcopy(reset_policy)
        reset_policy["order_price"] = None

    exit_snapshot = {
        key: deepcopy(value)
        for key, value in setting.items()
        if _text(key).startswith("exit_")
        or _text(key).startswith("complete_policy_")
    }
    exit_conditions: list[dict[str, Any]] = []
    if _checked(setting.get("exit_count_check")):
        target_count = _positive_int(setting.get("exit_count_line"))
        if target_count is None:
            return None, "SELL_REPEAT_EXIT_COUNT_INVALID"
        exit_conditions.append(
            {
                "condition_type": "COUNT",
                "target_repeat_generations": target_count,
                "initial_generation_included": False,
            }
        )
    if _checked(setting.get("exit_time_check")):
        time_value = _positive_number(setting.get("exit_time_line"))
        unit_ms = _time_unit_milliseconds(setting.get("exit_time_unit"), context)
        if time_value is None:
            return None, "SELL_REPEAT_EXIT_TIME_VALUE_INVALID"
        if unit_ms is None:
            return None, "SELL_REPEAT_EXIT_TIME_UNIT_UNRESOLVED"
        duration_ms = time_value * unit_ms
        if not float(duration_ms).is_integer():
            return None, "SELL_REPEAT_EXIT_TIME_VALUE_INVALID"
        exit_conditions.append(
            {
                "condition_type": "TIME",
                "duration_ms": int(duration_ms),
                "configured_value": time_value,
                "configured_unit": _text(setting.get("exit_time_unit")),
                "anchor": "FIRST_REPEAT_GENERATION_AT",
            }
        )
    if _checked(setting.get("exit_price_check")):
        left_source = _ratio_price_source(setting.get("exit_price_left"))
        right_source = _ratio_price_source(setting.get("exit_price_right"))
        direction = _ratio_direction(setting.get("exit_price_direction"))
        compare = _ratio_compare(setting.get("exit_price_compare"), direction or "")
        threshold = _positive_number(setting.get("exit_price_value"))
        if (
            left_source is None
            or right_source is None
            or direction is None
            or compare is None
            or threshold is None
        ):
            return None, "SELL_REPEAT_EXIT_PRICE_POLICY_INVALID"
        exit_conditions.append(
            {
                "condition_type": "PRICE",
                "left_source": left_source,
                "right_source": right_source,
                "direction": direction,
                "compare": compare,
                "threshold_percent": threshold,
                "orientation": "LEFT_VALUE_RELATIVE_TO_RIGHT_BASE",
            }
        )
    exit_policy = {
        "policy": "SELL_REPEAT_EXIT",
        "logic": "OR",
        "conditions": exit_conditions,
    }
    exit_policy["snapshot_hash"] = stable_hash(exit_policy)
    snapshot = {
        "policy": "SELL_FOLLOW_UP_REPEAT",
        "enabled": True,
        "execution_template": template,
        "unfilled_timeout_policy": unfilled_policy,
        "sell_price_reset_policy": reset_policy,
        "exit_policy_snapshot": exit_snapshot,
        "exit_policy": exit_policy,
    }
    snapshot["plan_snapshot_hash"] = stable_hash(snapshot)
    return snapshot, ""


def _time_offsets_milliseconds(
    *,
    count: int,
    value: int,
    unit_milliseconds: int,
    range_mode: Any,
) -> list[int] | None:
    duration = value * unit_milliseconds
    mode = _normalized_term(range_mode)
    if mode in {"WITHIN", "이내"}:
        if count == 1:
            return [0]
        return [round(index * duration / (count - 1)) for index in range(count)]
    if mode in {"INTERVAL", "간격"}:
        return [index * duration for index in range(count)]
    return None


def _effective_sell_method(rules: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str]:
    method = _as_dict(_as_dict(rules.get("sell")).get("method"))
    selected = method.get("selected_sets")
    if not isinstance(selected, list):
        return None, None, "SELL_SELECTED_SET_INVALID"
    selected_sets = [
        _text(value)
        for value in selected
        if _text(value)
    ]
    if len(selected_sets) != 1:
        return None, None, "SELL_SELECTED_SET_COUNT_INVALID"
    method_set = selected_sets[0]
    if method_set not in _METHOD_SET_ORDER:
        return None, None, "SELL_SELECTED_SET_INVALID"
    setting = method.get(method_set)
    if not isinstance(setting, dict):
        return None, None, "SELL_SELECTED_SETTING_MISSING"
    return method_set, deepcopy(setting), ""


def build_indicator_follow_sell_intent(
    *,
    sell_signal_result: Any,
    context: Any,
) -> dict[str, Any]:
    """Return one Routine-owned SELL intent or a fail-closed block result."""
    signal = deepcopy(_as_dict(sell_signal_result))
    runtime_context = _as_dict(context)
    cycle = _as_dict(runtime_context.get("cycle"))

    if _text(cycle.get("status")).lower() != "resolved":
        return _blocked(_text(cycle.get("unresolved_reason")) or "CYCLE_PROJECTION_UNRESOLVED")

    holding_qty = _positive_int(cycle.get("holding_qty"))
    if holding_qty is None:
        return _blocked("SELL_HOLDING_QUANTITY_INVALID")

    routine_instance_id = _text(
        runtime_context.get("routine_instance_id") or signal.get("routine_instance_id")
    )
    if not routine_instance_id:
        return _blocked("SELL_ROUTINE_INSTANCE_ID_MISSING")

    rules = _as_dict(runtime_context.get("rules"))
    if not rules:
        rules = _as_dict(runtime_context.get("routine_config"))
    method_set, setting, method_reason = _effective_sell_method(rules)
    if method_reason:
        return _blocked(method_reason)
    assert method_set is not None and setting is not None

    unfilled_timeout_policy, timeout_policy_reason = _unfilled_timeout_policy(
        setting,
        runtime_context,
    )
    if timeout_policy_reason:
        return _blocked(timeout_policy_reason)
    price_reset_policy, price_reset_reason = _price_reset_policy(
        setting,
        runtime_context,
    )
    if price_reset_reason:
        return _blocked(price_reset_reason)
    repeat_policy, repeat_policy_reason = _repeat_execution_policy(
        setting,
        runtime_context,
    )
    if repeat_policy_reason:
        return _blocked(repeat_policy_reason)

    source_signal_id = _text(
        signal.get("source_signal_id")
        or signal.get("signal_id")
        or signal.get("id")
        or runtime_context.get("source_signal_id")
    )
    common_intent = {
        "side": "SELL",
        "budget": None,
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": routine_instance_id,
        "source_signal_id": source_signal_id or None,
        "cycle_identity": cycle.get("cycle_identity"),
        "sell_method_set": method_set,
    }
    if unfilled_timeout_policy is not None:
        common_intent["unfilled_timeout_policy"] = unfilled_timeout_policy
    if price_reset_policy is not None:
        common_intent["sell_price_reset_policy"] = price_reset_policy
    if repeat_policy is not None:
        common_intent["sell_repeat_policy"] = repeat_policy

    hoga_mode = _normalized_term(setting.get("perform1_title_combo"))
    split_mode = _normalized_term(setting.get("perform2_title_combo"))
    if split_mode not in (_NO_SPLIT_TERMS | _MULTI_TIME_TERMS):
        if split_mode not in _MULTI_RATIO_TERMS:
            return _blocked("SELL_PERFORM2_POLICY_INVALID")
    if split_mode in _MULTI_RATIO_TERMS:
        if hoga_mode in _MULTI_HOGA_TERMS:
            return _blocked("SELL_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED")
        if hoga_mode not in _SINGLE_HOGA_TERMS:
            return _blocked("SELL_MULTI_RATIO_PERFORM1_INVALID")
        order_method = _normalized_term(setting.get("perform1_single_combo"))
        if order_method in _ORDER_PRICE_TERMS:
            order_price = _positive_number(
                runtime_context.get("reference_price", runtime_context.get("current_price"))
            )
            if order_price is None:
                return _blocked("SELL_ORDER_PRICE_MISSING")
            hoga = "LIMIT"
            price_basis = "ORDER_PRICE"
        elif order_method in _MARKET_TERMS:
            order_price = None
            hoga = "MARKET"
            price_basis = "MARKET"
        else:
            return _blocked("SELL_SINGLE_HOGA_PRICE_POLICY_INVALID")
        configured_count = _positive_int(setting.get("perform2_ratio_count"))
        ratio_value = _positive_number(setting.get("perform2_ratio_value"))
        ratio_left = _ratio_price_source(setting.get("perform2_ratio_left"))
        ratio_right = _ratio_price_source(setting.get("perform2_ratio_right"))
        direction = _ratio_direction(setting.get("perform2_ratio_direction"))
        compare = _ratio_compare(setting.get("perform2_ratio_compare"), direction or "")
        if (
            configured_count is None
            or ratio_value is None
            or ratio_left is None
            or ratio_right is None
            or direction is None
            or compare is None
        ):
            return _blocked("SELL_MULTI_RATIO_PLAN_INVALID")
        reference_price = _positive_number(
            runtime_context.get("reference_price", runtime_context.get("current_price"))
        )
        if "ORDER_PRICE" in {ratio_left, ratio_right} and reference_price is None:
            return _blocked("SELL_MULTI_RATIO_ORDER_PRICE_MISSING")
        child_count = min(configured_count, holding_qty)
        quantities = _split_quantity(holding_qty, child_count)
        plan = {
            "planned_child_count": child_count,
            "configured_child_count": configured_count,
            "planned_total_quantity": holding_qty,
            "ratio_left": ratio_left,
            "ratio_right": ratio_right,
            "ratio_direction": direction,
            "ratio_value": ratio_value,
            "ratio_compare": compare,
            "ratio_unit": "PERCENT",
            "order_price": reference_price,
        }
        intents: list[dict[str, Any]] = []
        for index, quantity in enumerate(quantities, start=1):
            intents.append(
                {
                    **deepcopy(common_intent),
                    "quantity": quantity,
                    "planned_total_quantity": holding_qty,
                    "price_basis": price_basis,
                    "price": order_price,
                    "hoga": hoga,
                    "hoga_mode": "SINGLE",
                    "execution_mode": "MULTI_RATIO",
                    "execution_process_owner_required": True,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": child_count,
                    "child_kind": "RATIO_SLICE",
                    "child_plan": {
                        "planned_quantity": quantity,
                        "planned_price": order_price,
                        "ratio_step_index": index,
                    },
                    "multi_ratio_plan": deepcopy(plan),
                    "ratio_left": ratio_left,
                    "ratio_right": ratio_right,
                    "ratio_direction": direction,
                    "ratio_value": ratio_value,
                    "ratio_compare": compare,
                    "ratio_count": child_count,
                    "final_current_price_evidence_required": (
                        "CURRENT_PRICE" in {ratio_left, ratio_right}
                    ),
                    "sell_perform1": {
                        "hoga_mode": "SINGLE",
                        "price_basis": price_basis,
                    },
                    "sell_perform2": {"split_mode": "MULTI_RATIO"},
                }
            )
        return {
            "status": STATUS_READY,
            "reason": "",
            "execution_intent": intents[0],
            "execution_intents": intents,
            "selected_method_set": method_set,
        }
    if split_mode in _MULTI_TIME_TERMS:
        if hoga_mode in _MULTI_HOGA_TERMS:
            return _blocked("SELL_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED")
        if hoga_mode not in _SINGLE_HOGA_TERMS:
            return _blocked("SELL_MULTI_TIME_PERFORM1_INVALID")
        if _normalized_term(setting.get("perform1_single_combo")) not in (
            _ORDER_PRICE_TERMS | _MARKET_TERMS
        ):
            return _blocked("SELL_SINGLE_HOGA_PRICE_POLICY_INVALID")
        configured_count = _positive_int(setting.get("perform2_time_count"))
        time_value = _positive_int(setting.get("perform2_time_value"))
        if configured_count is None or time_value is None:
            return _blocked("SELL_MULTI_TIME_PLAN_INVALID")
        child_count = min(configured_count, holding_qty)
        unit_milliseconds = _time_unit_milliseconds(
            setting.get("perform2_time_unit"),
            runtime_context,
        )
        if unit_milliseconds is None:
            return _blocked("SELL_MULTI_TIME_UNIT_UNRESOLVED")
        offsets = _time_offsets_milliseconds(
            count=child_count,
            value=time_value,
            unit_milliseconds=unit_milliseconds,
            range_mode=setting.get("perform2_time_range"),
        )
        if offsets is None:
            return _blocked("SELL_MULTI_TIME_RANGE_INVALID")
        time_order = _normalized_term(setting.get("perform2_time_order"))
        if time_order in _TIME_ORDER_PRICE_TERMS:
            price = _positive_number(
                runtime_context.get("reference_price", runtime_context.get("current_price"))
            )
            price_basis = "ORDER_PRICE"
        elif time_order in _TIME_CURRENT_PRICE_TERMS:
            price = _positive_number(runtime_context.get("actionable_current_price"))
            price_basis = "CURRENT_PRICE"
        else:
            return _blocked("SELL_MULTI_TIME_PRICE_POLICY_INVALID")
        if price is None:
            return _blocked(
                "CURRENT_PRICE_VALUE_MISSING"
                if price_basis == "CURRENT_PRICE"
                else "SELL_ORDER_PRICE_MISSING"
            )
        quantities = _split_quantity(holding_qty, child_count)
        plan = {
            "planned_child_count": child_count,
            "configured_child_count": configured_count,
            "planned_total_quantity": holding_qty,
            "scheduled_offsets_ms": list(offsets),
            "time_value": time_value,
            "time_unit": _text(setting.get("perform2_time_unit")),
            "time_range": _text(setting.get("perform2_time_range")),
            "price_basis": price_basis,
        }
        intents: list[dict[str, Any]] = []
        for index, (quantity, offset) in enumerate(zip(quantities, offsets), start=1):
            child_plan = {
                "planned_quantity": quantity,
                "planned_price": price,
                "scheduled_offset_ms": offset,
            }
            intents.append(
                {
                    **deepcopy(common_intent),
                    "quantity": quantity,
                    "planned_total_quantity": holding_qty,
                    "price_basis": price_basis,
                    "price": price,
                    "hoga": "LIMIT",
                    "hoga_mode": "SINGLE",
                    "execution_mode": "MULTI_TIME",
                    "execution_process_owner_required": True,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": child_count,
                    "child_kind": "TIME_SLICE",
                    "child_plan": child_plan,
                    "multi_time_plan": deepcopy(plan),
                    "sell_perform1": {
                        "hoga_mode": "SINGLE",
                        "price_basis": price_basis,
                    },
                    "sell_perform2": {
                        "split_mode": "MULTI_TIME",
                        "price_basis": price_basis,
                    },
                }
            )
        return {
            "status": STATUS_READY,
            "reason": "",
            "execution_intent": intents[0],
            "execution_intents": intents,
            "selected_method_set": method_set,
        }
    if hoga_mode in _MULTI_HOGA_TERMS:
        if not callable(move_krx_price_by_ticks):
            return _blocked("SELL_TICK_PRICE_PRIMITIVE_UNAVAILABLE")
        up_count = _nonnegative_int(setting.get("perform1_multi_up_line"))
        down_count = _nonnegative_int(setting.get("perform1_multi_down_line"))
        if up_count is None or down_count is None:
            return _blocked("SELL_MULTI_HOGA_RANGE_INVALID")
        base_price = _positive_number(
            runtime_context.get("reference_price", runtime_context.get("current_price"))
        )
        if base_price is None:
            return _blocked("SELL_ORDER_PRICE_MISSING")
        offsets = _hoga_offsets(up_count, down_count)
        if holding_qty < len(offsets):
            offsets = [0]
        quantities = _split_quantity(holding_qty, len(offsets))
        instrument_type = (
            runtime_context.get("instrument_classification")
            or runtime_context.get("instrument_type")
            or "STOCK"
        )
        try:
            prices = [
                move_krx_price_by_ticks(
                    base_price,
                    offset,
                    instrument_type=instrument_type,
                )
                for offset in offsets
            ]
        except ValueError as exc:
            return _blocked(str(exc) or "SELL_MULTI_HOGA_PRICE_INVALID")

        total = len(offsets)
        plan = {
            "base_price": base_price,
            "hoga_offsets": list(offsets),
            "planned_child_count": total,
            "planned_total_quantity": holding_qty,
            "instrument_type": str(instrument_type or "STOCK"),
        }
        intents: list[dict[str, Any]] = []
        for index, (offset, quantity, price) in enumerate(
            zip(offsets, quantities, prices),
            start=1,
        ):
            child_plan = {
                "planned_quantity": quantity,
                "planned_price": price,
                "hoga_offset_ticks": offset,
            }
            intents.append(
                {
                    **deepcopy(common_intent),
                    "quantity": quantity,
                    "planned_total_quantity": holding_qty,
                    "price_basis": "ORDER_PRICE",
                    "price": price,
                    "hoga": "LIMIT",
                    "hoga_mode": "MULTI",
                    "hoga_up": up_count,
                    "hoga_down": down_count,
                    "execution_mode": "MULTI_HOGA",
                    "execution_process_owner_required": True,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": total,
                    "child_kind": "HOGA_LEVEL",
                    "child_plan": child_plan,
                    "multi_hoga_plan": deepcopy(plan),
                    "sell_perform1": {
                        "hoga_mode": "MULTI",
                        "price_basis": "ORDER_PRICE",
                    },
                }
            )
        return {
            "status": STATUS_READY,
            "reason": "",
            "execution_intent": intents[0],
            "execution_intents": intents,
            "selected_method_set": method_set,
        }
    if hoga_mode not in _SINGLE_HOGA_TERMS:
        return _blocked("SELL_SINGLE_HOGA_POLICY_INVALID")

    single_method = _normalized_term(setting.get("perform1_single_combo"))
    if single_method in _ORDER_PRICE_TERMS:
        price = _positive_number(
            runtime_context.get("reference_price", runtime_context.get("current_price"))
        )
        if price is None:
            return _blocked("SELL_ORDER_PRICE_MISSING")
        price_basis = "ORDER_PRICE"
        hoga = "LIMIT"
    elif single_method in _MARKET_TERMS:
        price = None
        price_basis = "MARKET"
        hoga = "MARKET"
    else:
        return _blocked("SELL_SINGLE_HOGA_PRICE_POLICY_INVALID")

    intent = {
        **common_intent,
        "quantity": holding_qty,
        "price_basis": price_basis,
        "price": price,
        "hoga": hoga,
        "hoga_mode": "SINGLE",
        "sell_perform1": {
            "hoga_mode": "SINGLE",
            "price_basis": price_basis,
        },
    }
    return {
        "status": STATUS_READY,
        "reason": "",
        "execution_intent": intent,
        "execution_intents": [intent],
        "selected_method_set": method_set,
    }
