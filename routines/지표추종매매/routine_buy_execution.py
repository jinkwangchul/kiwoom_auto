# -*- coding: utf-8 -*-
"""Indicator-follow BUY intent construction from approved rules and fills."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from buy_order_candidate_preview_service import build_buy_order_candidate_preview


STATUS_READY = "READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: Any) -> int | None:
    number = _positive_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _official_execution_rules(rules: dict[str, Any]) -> dict[str, Any]:
    buy = _as_dict(rules.get("buy"))
    execution = _as_dict(buy.get("execution"))
    return {
        "buy": {
            "execution": {
                "base": deepcopy(_as_dict(execution.get("base"))),
                "repeat": deepcopy(_as_dict(execution.get("repeat"))),
            }
        }
    }


def _maximum_rounds(config: dict[str, Any], rules: dict[str, Any]) -> int | None:
    execution = _as_dict(_as_dict(rules.get("buy")).get("execution"))
    for source in (config, _as_dict(execution.get("repeat")), _as_dict(execution.get("base"))):
        value = _positive_int(source.get("max_buy_rounds"))
        if value is not None:
            return value
    return None


def _budget_context(
    *,
    stock_config: dict[str, Any],
    rules: dict[str, Any],
    cycle: dict[str, Any],
    current_price: float,
) -> dict[str, Any]:
    mode = str(stock_config.get("trade_amount_type") or "QUANTITY").strip().upper()
    budget: dict[str, Any] = {
        "starting_budget_type": mode,
        "current_price": current_price,
        "base_buy_budget": _positive_float(cycle.get("base_filled_buy_amount")),
        "previous_buy_budget": _positive_float(cycle.get("last_filled_buy_amount")),
        "max_buy_rounds": _maximum_rounds(stock_config, rules),
    }
    if mode == "QUANTITY":
        budget["starting_quantity"] = _positive_int(stock_config.get("buy_qty"))
    elif mode == "AMOUNT":
        budget["starting_amount"] = _positive_float(stock_config.get("buy_amount"))

    limit_enabled = stock_config.get("buy_limit_enabled") is True
    limit_amount = _positive_float(stock_config.get("buy_limit_amount"))
    if limit_enabled and limit_amount is not None:
        cumulative = _positive_float(cycle.get("cumulative_filled_buy_amount")) or 0.0
        budget["total_budget"] = limit_amount
        budget["remaining_budget"] = limit_amount - cumulative
    return budget


def build_indicator_follow_buy_intent(
    *,
    buy_signal_result: Any,
    context: Any,
) -> dict[str, Any]:
    """Return a routine-owned intent or a fail-closed block result."""
    signal = deepcopy(_as_dict(buy_signal_result))
    runtime_context = _as_dict(context)
    cycle = _as_dict(runtime_context.get("cycle"))
    if cycle.get("status") != "resolved":
        return {
            "status": "BLOCKED",
            "reason": str(cycle.get("unresolved_reason") or "CYCLE_PROJECTION_UNRESOLVED"),
            "execution_intent": None,
        }

    confirmed_round = cycle.get("confirmed_buy_round")
    if not isinstance(confirmed_round, int) or isinstance(confirmed_round, bool) or confirmed_round < 0:
        return {"status": "BLOCKED", "reason": "CONFIRMED_BUY_ROUND_INVALID", "execution_intent": None}
    next_round = confirmed_round + 1
    pending_rounds = cycle.get("pending_buy_rounds")
    if isinstance(pending_rounds, list) and next_round in pending_rounds:
        return {"status": "BLOCKED", "reason": "BUY_ROUND_ALREADY_PENDING", "execution_intent": None}
    if isinstance(pending_rounds, list) and pending_rounds:
        return {"status": "BLOCKED", "reason": "BUY_ORDER_STILL_PENDING", "execution_intent": None}

    current_price = _positive_float(runtime_context.get("current_price"))
    if current_price is None:
        return {"status": "BLOCKED", "reason": "CURRENT_PRICE_VALUE_MISSING", "execution_intent": None}

    stock_config = _as_dict(runtime_context.get("stock_config"))
    rules = _as_dict(runtime_context.get("rules"))
    signal.update({
        "side": "BUY",
        "current_price": current_price,
        "order_price": current_price,
        "market_price": current_price,
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": runtime_context.get("routine_instance_id"),
        "cycle_identity": cycle.get("cycle_identity"),
        "confirmed_previous_round": confirmed_round,
    })
    preview = build_buy_order_candidate_preview(
        buy_signal_result=signal,
        approved_rules=_official_execution_rules(rules),
        runtime_state_snapshot={
            "confirmed_current_buy_round": confirmed_round,
            "confirmed_cumulative_buy_budget": cycle.get("cumulative_filled_buy_amount"),
        },
        budget_context=_budget_context(
            stock_config=stock_config,
            rules=rules,
            cycle=cycle,
            current_price=current_price,
        ),
    )
    if preview.get("status") != STATUS_READY:
        issues = _as_dict(preview.get("execution_policy_result")).get("issues")
        reason = issues[0] if isinstance(issues, list) and issues else "BUY_EXECUTION_POLICY_BLOCKED"
        return {"status": "BLOCKED", "reason": reason, "execution_intent": None, "preview": preview}

    intent = deepcopy(_as_dict(preview.get("execution_intent")))
    intent["confirmed_previous_round"] = confirmed_round
    return {"status": STATUS_READY, "reason": "", "execution_intent": intent, "preview": preview}
