# -*- coding: utf-8 -*-
"""Preview-only SELL exit policy evaluator.

This module evaluates ``exit_*`` fields from a single SELL method preview. It
does not build liquidation orders and does not connect runtime, queue, execution,
or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "SELL_EXIT_POLICY_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
LOGIC = "OR"
SAFETY_FLAGS = ("execution_connected", "runtime_write", "send_order", "queue_write")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).upper()


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "TRUE", "YES", "Y", "ON", "CHECKED"}


def _method_preview(source: Any) -> dict[str, Any]:
    data = _as_dict(source)
    if "method_snapshot" in data:
        return data
    previews = _as_list(data.get("method_previews"))
    if len(previews) == 1 and isinstance(previews[0], dict):
        return previews[0]
    return data


def _method_snapshot(preview: dict[str, Any]) -> Any:
    if "method_snapshot" in preview:
        return preview.get("method_snapshot")
    return preview


def _method_set(preview: dict[str, Any]) -> str | None:
    value = _text(preview.get("method_set"))
    return value or None


def _status(*, active_count: int, blocked: list[str], invalid: list[str]) -> str:
    if invalid:
        return STATUS_INVALID
    if active_count == 0:
        return STATUS_NOT_APPLICABLE
    if blocked:
        return STATUS_BLOCKED
    return STATUS_READY


def _target_value(target: Any, market: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    token = _norm(target)
    mapping = {
        "현재가": ("CURRENT_PRICE", ("current_price", "price", "latest_price", "close")),
        "CURRENT_PRICE": ("CURRENT_PRICE", ("current_price", "price", "latest_price", "close")),
        "CLOSE": ("CURRENT_PRICE", ("current_price", "price", "latest_price", "close")),
        "평단가": ("AVG_PRICE", ("average_price", "avg_price", "avg_buy_price")),
        "AVG_PRICE": ("AVG_PRICE", ("average_price", "avg_price", "avg_buy_price")),
        "AVERAGE_PRICE": ("AVG_PRICE", ("average_price", "avg_price", "avg_buy_price")),
        "주문가": ("ORDER_PRICE", ("order_price",)),
        "ORDER_PRICE": ("ORDER_PRICE", ("order_price",)),
    }
    item = mapping.get(token)
    if item is None:
        return None, None, f"unsupported price target: {_text(target) or '<empty>'}"
    canonical, keys = item
    for key in keys:
        value = _number(market.get(key))
        if value is not None:
            return canonical, value, None
    return canonical, None, f"{canonical} is required"


def _operator(value: Any) -> str | None:
    token = _norm(value)
    return {
        "이상": ">=",
        ">=": ">=",
        "GTE": ">=",
        "이하": "<=",
        "<=": "<=",
        "LTE": "<=",
    }.get(token)


def _direction(value: Any) -> str | None:
    token = _norm(value)
    return {
        "상향": "UP",
        "UP": "UP",
        "하향": "DOWN",
        "DOWN": "DOWN",
        "상하": "BOTH",
        "BOTH": "BOTH",
    }.get(token)


def _unsupported_range_compare(direction: str | None, compare: Any) -> str | None:
    compare_text = _norm(compare)
    if direction == "BOTH" or compare_text in {"이내", "이탈", "WITHIN", "OUTSIDE"}:
        return "range exit price compare is unsupported in preview"
    return None


def _threshold(base: float, direction: str, offset_percent: float) -> float:
    ratio = abs(offset_percent) / 100
    if direction == "DOWN":
        return base * (1 - ratio)
    return base * (1 + ratio)


def _compare(left: float, operator: str, right: float) -> bool:
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    return False


def _price_condition(setting: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    condition = {
        "condition_type": "PRICE",
        "enabled": True,
        "matched": False,
        "status": STATUS_READY,
        "reason": "not_matched",
    }
    left_name, left, left_error = _target_value(setting.get("exit_price_left"), market)
    right_name, right, right_error = _target_value(setting.get("exit_price_right"), market)
    direction = _direction(setting.get("exit_price_direction"))
    operator = _operator(setting.get("exit_price_compare"))
    offset = _number(setting.get("exit_price_value"))

    condition.update({
        "left": left_name,
        "right": right_name,
        "direction": direction,
        "operator": operator,
        "offset_percent": offset,
    })

    unsupported = _unsupported_range_compare(direction, setting.get("exit_price_compare"))
    if unsupported:
        condition.update({"status": STATUS_BLOCKED, "reason": unsupported})
        return condition
    if left_error or right_error:
        condition.update({"status": STATUS_BLOCKED, "reason": left_error or right_error})
        return condition
    if direction not in {"UP", "DOWN"}:
        condition.update({"status": STATUS_INVALID, "reason": "exit_price_direction is invalid"})
        return condition
    if operator is None:
        condition.update({"status": STATUS_INVALID, "reason": "exit_price_compare is invalid"})
        return condition
    if offset is None:
        condition.update({"status": STATUS_INVALID, "reason": "exit_price_value is invalid"})
        return condition

    assert left is not None and right is not None
    threshold = _threshold(right, direction, offset)
    matched = _compare(left, operator, threshold)
    condition.update({
        "left_value": left,
        "right_value": right,
        "threshold": threshold,
        "matched": matched,
        "reason": "matched" if matched else "not_matched",
    })
    return condition


def _count_condition(setting: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    condition = {
        "condition_type": "COUNT",
        "enabled": True,
        "matched": False,
        "status": STATUS_READY,
        "reason": "not_matched",
    }
    target_count = _number(setting.get("exit_count_line"))
    execution_count = _number(runtime.get("execution_count"))
    condition.update({"target_count": target_count, "execution_count": execution_count})
    if target_count is None:
        condition.update({"status": STATUS_INVALID, "reason": "exit_count_line is invalid"})
        return condition
    if execution_count is None:
        condition.update({"status": STATUS_BLOCKED, "reason": "execution_count is required"})
        return condition
    matched = execution_count >= target_count
    condition.update({"matched": matched, "reason": "matched" if matched else "not_matched"})
    return condition


def _time_unit_seconds(value: Any) -> tuple[float | None, str | None]:
    token = _norm(value)
    if token in {"초", "SECOND", "SECONDS", "SEC"}:
        return 1.0, None
    if token in {"분", "MINUTE", "MINUTES", "MIN"}:
        return 60.0, None
    if token in {"봉", "BAR", "BARS"}:
        return None, "BAR unit requires elapsed_bars and is not supported by elapsed_time"
    return None, "exit_time_unit is invalid"


def _time_condition(setting: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    condition = {
        "condition_type": "TIME",
        "enabled": True,
        "matched": False,
        "status": STATUS_READY,
        "reason": "not_matched",
    }
    target_value = _number(setting.get("exit_time_line"))
    multiplier, unit_error = _time_unit_seconds(setting.get("exit_time_unit"))
    elapsed_time = _number(runtime.get("elapsed_time"))
    condition.update({
        "target_value": target_value,
        "unit": _text(setting.get("exit_time_unit")),
        "elapsed_time": elapsed_time,
    })
    if target_value is None:
        condition.update({"status": STATUS_INVALID, "reason": "exit_time_line is invalid"})
        return condition
    if unit_error:
        condition.update({"status": STATUS_BLOCKED if "BAR" in unit_error else STATUS_INVALID, "reason": unit_error})
        return condition
    if elapsed_time is None:
        if runtime.get("entry_time") not in (None, ""):
            condition.update({"status": STATUS_BLOCKED, "reason": "entry_time calculation is not supported in preview"})
        else:
            condition.update({"status": STATUS_BLOCKED, "reason": "elapsed_time is required"})
        return condition

    assert multiplier is not None
    threshold_seconds = target_value * multiplier
    matched = elapsed_time >= threshold_seconds
    condition.update({
        "threshold_seconds": threshold_seconds,
        "matched": matched,
        "reason": "matched" if matched else "not_matched",
    })
    return condition


def _active_conditions(setting: dict[str, Any], market: dict[str, Any], runtime: dict[str, Any]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    if _truthy(setting.get("exit_price_check")):
        conditions.append(_price_condition(setting, market))
    if _truthy(setting.get("exit_count_check")):
        conditions.append(_count_condition(setting, runtime))
    if _truthy(setting.get("exit_time_check")):
        conditions.append(_time_condition(setting, runtime))
    return conditions


def build_sell_exit_policy_preview(
    method_preview: Any,
    market_context: Any = None,
    runtime_context: Any = None,
) -> dict[str, Any]:
    """Evaluate one method set's exit policy without creating orders."""
    preview = deepcopy(_method_preview(method_preview))
    market = deepcopy(_as_dict(market_context))
    runtime = deepcopy(_as_dict(runtime_context))
    snapshot = _method_snapshot(preview)

    reasons: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []
    conditions: list[dict[str, Any]] = []

    if not isinstance(snapshot, dict):
        invalid.append("method_snapshot must be a dict")
        snapshot_copy = None
    else:
        snapshot_copy = deepcopy(snapshot)
        for flag in SAFETY_FLAGS:
            if snapshot.get(flag) is True or preview.get(flag) is True:
                invalid.append(f"safety flag must be false: {flag}")
        conditions = _active_conditions(snapshot, market, runtime)
        for condition in conditions:
            status = condition.get("status")
            reason = str(condition.get("reason") or "")
            if status == STATUS_BLOCKED:
                reasons.append(reason)
            elif status == STATUS_INVALID:
                invalid.append(reason)

    matched_conditions = [
        deepcopy(condition)
        for condition in conditions
        if condition.get("matched") is True and condition.get("status") == STATUS_READY
    ]
    status = _status(active_count=len(conditions), blocked=reasons, invalid=invalid)

    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "send_order": False,
        "queue_write": False,
        "status": status,
        "method_set": _method_set(preview),
        "logic": LOGIC,
        "conditions": deepcopy(conditions),
        "matched_conditions": matched_conditions,
        "method_snapshot": snapshot_copy,
        "market_context_snapshot": deepcopy(market),
        "runtime_context_snapshot": deepcopy(runtime),
        "quantity": None,
        "price": None,
        "hoga": None,
        "order_type": None,
        "reasons": list(reasons + invalid),
        "warnings": warnings,
    }
