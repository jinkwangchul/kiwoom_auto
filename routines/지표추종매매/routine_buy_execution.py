# -*- coding: utf-8 -*-
"""Indicator-follow BUY intent construction from approved rules and fills."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from buy_order_candidate_preview_service import build_buy_order_candidate_preview
from execution_price_comparison import evaluate_percent_comparison
from math import isfinite
from krx_tick_price import move_krx_price_by_ticks
try:
    from .routine_sell_execution import _time_offsets_milliseconds, _time_unit_milliseconds
    from .routine_cycle_projection import project_indicator_follow_cycle
except ImportError:
    from routine_sell_execution import _time_offsets_milliseconds, _time_unit_milliseconds
    from routine_cycle_projection import project_indicator_follow_cycle


STATUS_READY = "READY"


def inspect_buy_execution_support(*, subject: dict[str, Any], rules: dict[str, Any], _planning: bool = False) -> str:
    """Reject unsupported BUY policies without promoting saved UI into rules.

    Check both current rules and frozen intent options: old downgraded signals
    must not become executable after restart or a later settings change.
    Missing point/execution mode retains the existing legacy SINGLE encoding.
    """
    def nonnegative_number(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            and float(value) >= 0
        )

    execution = _as_dict(_as_dict(rules.get("buy")).get("execution"))
    additional = _as_dict(execution.get("additional"))
    if additional:
        if additional.get("execution_connected") is not True:
            return "MAPPED_BUT_EXECUTION_NOT_CONNECTED"
        price_skip = _as_dict(additional.get("previous_round_price_skip"))
        last_plus_one = _as_dict(additional.get("last_plus_one"))
        active_condition = _as_dict(last_plus_one.get("active_condition"))
        if (
            not price_skip
            or not last_plus_one
            or not isinstance(price_skip.get("enabled"), bool)
            or price_skip.get("reference_source") != "PREVIOUS_CONFIRMED_BUY_ORDER_PRICE"
            or price_skip.get("current_source") != "ACTIONABLE_ORDER_PRICE"
            or price_skip.get("action") != "SKIP_CURRENT_GENERATION"
            or price_skip.get("skipped_round_increment") is not False
            or price_skip.get("direction") not in {"UP", "DOWN", "BOTH"}
            or price_skip.get("comparator") not in {">=", "<=", "WITHIN", "OUTSIDE"}
            or not nonnegative_number(price_skip.get("ratio_percent"))
            or not isinstance(last_plus_one.get("enabled"), bool)
            or last_plus_one.get("generation_kind") != "LAST_PLUS_ONE"
            or last_plus_one.get("trigger") != "AFTER_NORMAL_MAX_ROUND_COMPLETED"
            or last_plus_one.get("max_occurrences") != 1
            or last_plus_one.get("method") not in {"MARKET", "CURRENT_PRICE", "ACTIVE"}
            or last_plus_one.get("budget_basis") != "LAST_NORMAL_ROUND_APPROVED_BUDGET"
            or last_plus_one.get("terminal_after_completed_fill") is not True
            or active_condition.get("direction") not in {"UP", "DOWN", "BOTH"}
            or active_condition.get("comparator") not in {">=", "<=", "WITHIN", "OUTSIDE"}
            or not nonnegative_number(active_condition.get("ratio_percent"))
        ):
            return "BUY_ADDITIONAL_POLICY_INVALID"
    cycle_policy = _as_dict(execution.get("cycle"))
    if cycle_policy and cycle_policy.get("execution_connected") is not True:
        return str(cycle_policy.get("execution_lock_reason") or "CYCLE_OPTION_EXECUTION_NOT_CONNECTED")
    intents = list(subject.get("execution_intents") or [])
    if isinstance(subject.get("execution_intent"), dict):
        intents.append(subject["execution_intent"])
    for intent in intents or [subject]:
        intent = _as_dict(intent)
        side = str(intent.get("side") or subject.get("side") or subject.get("signal") or "").upper()
        action = str(subject.get("order_action") or intent.get("order_action") or "NEW").upper()
        if side != "BUY" or action == "CANCEL":
            continue
        for policy in (_as_dict(execution.get("base")), intent,
                       _as_dict(intent.get("approved_execution_options"))):
            point = policy.get("point_mode")
            mode = policy.get("execution_mode")
            if point == "MULTI_RATIO" or mode == "MULTI_RATIO" or policy.get("child_kind") == "RATIO_SLICE":
                if not _planning and _buy_ratio_intent_issue(intent):
                    return "BUY_MULTI_RATIO_PLAN_INVALID"
            if point == "ACTIVE_BUY" or mode == "ACTIVE_BUY":
                return "ACTIVE_BUY_NOT_IMPLEMENTED"
            if point not in (None, "NONE", "MULTI_TIME", "MULTI_RATIO"):
                return "BUY_POINT_MODE_NOT_SUPPORTED"
            if mode not in (None, "SINGLE", "MULTI_HOGA", "MULTI_TIME", "MULTI_RATIO"):
                return "BUY_EXECUTION_MODE_NOT_SUPPORTED"
            if "hoga_mode" in policy and policy["hoga_mode"] not in ("SINGLE", "MULTI"):
                return "BUY_HOGA_MODE_NOT_SUPPORTED"
        active = _as_dict(_as_dict(execution.get("base")).get("last_round_active_buy"))
        if active.get("enabled") is True:
            point = _as_dict(execution.get("base")).get("point_mode")
            if point not in {"MULTI_TIME", "MULTI_RATIO"}:
                return "BUY_LAST_ROUND_ACTIVE_REQUIRES_MULTI_POINT"
            if (
                active.get("applies_to") != "LAST_MULTI_POINT_CHILD"
                or active.get("budget_policy_override") != "NONE"
                or active.get("purpose") != "BUY_METHOD_SPECIAL_ACTION"
                or active.get("subject") != "AVERAGE_PRICE"
                or active.get("reference") != "MULTI_POINT_SET_PRICE"
                or active.get("direction") not in {"UP", "DOWN", "BOTH"}
                or active.get("comparator") not in {">=", "<=", "WITHIN", "OUTSIDE"}
                or not nonnegative_number(active.get("ratio_percent"))
            ):
                return "BUY_LAST_ROUND_ACTIVE_POLICY_INVALID"
        if (intent.get("buy_phase") == "REPEAT"
                or (_positive_int(intent.get("buy_round")) or 0) > 1):
            repeat = _as_dict(execution.get("repeat"))
            if repeat.get("detail_mode") == "ACTIVE_BUY":
                return "ACTIVE_BUY_NOT_IMPLEMENTED"
            if "detail_mode" in repeat and repeat["detail_mode"] not in ("ROUND", "BUDGET"):
                return "INVALID_REPEAT_DETAIL_MODE"
    return ""


def inspect_buy_time_slice_continuation(*, subject: dict[str, Any], rules: dict[str, Any], project_root: Path) -> str:
    """Recheck this routine's cycle and current stock limit at its two gates.

    Generic dispatch owns cash/account/round consumption. This routine alone
    interprets buy_round, cycle and maximum-round settings. Reads only existing
    canonical ledgers and the same running config projection as signal probing.
    """
    intent = _as_dict(subject.get("execution_intent"))
    if intent.get("side") != "BUY" or intent.get("execution_mode") not in {"MULTI_TIME", "MULTI_RATIO"}:
        return ""
    from stock_repository import StockRepository
    from running_budget_adjustment import project_running_budget_adjustment_config
    def read(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value

    try:
        code = str(subject.get("code") or "").strip()
        instance = str(intent.get("routine_instance_id") or "").strip()
        if not code or not instance:
            return "BUY_TIME_SLICE_CYCLE_IDENTITY_MISSING"
        stock_dir = StockRepository(project_root=project_root).resolve_stock_dir(code)
        config = read(stock_dir / "config.json")
        state = read(stock_dir / "state.json")
        if config.get("assigned_routine_instance_id") != instance:
            return "BUY_TIME_SLICE_ASSIGNMENT_CHANGED"
        config, _ = project_running_budget_adjustment_config(config, state)
        queue = read(project_root / "runtime" / "order_queue.json")
        fills = read(project_root / "runtime" / "fills.json")
        cycle = project_indicator_follow_cycle(
            code=code, routine_instance_id=instance, order_queue=queue, fills=fills,
            positions=read(project_root / "runtime" / "positions.json"),
        )
        if cycle.get("status") != "resolved":
            return "BUY_TIME_SLICE_CYCLE_UNRESOLVED"
        planned_round = _positive_int(intent.get("buy_round"))
        confirmed = cycle.get("confirmed_buy_round")
        if planned_round is None or confirmed not in {planned_round - 1, planned_round}:
            return "BUY_TIME_SLICE_ROUND_CHANGED"
        if cycle.get("active") and cycle.get("cycle_identity") != intent.get("cycle_identity"):
            return "BUY_TIME_SLICE_CYCLE_CHANGED"
        current_plan_has_fill = any(
            f.get("execution_process_id") == intent.get("execution_process_id") and f.get("side") == "BUY"
            for f in fills.get("fills", []) if isinstance(f, dict)
        )
        if ((cycle.get("cycle_ended") and current_plan_has_fill)
                or any(r != planned_round for r in cycle.get("pending_buy_rounds", []))):
            return "BUY_TIME_SLICE_CYCLE_ENDED_OR_OTHER_ROUND_PENDING"
        maximum_rounds = _maximum_rounds(config, rules)
        if maximum_rounds is not None and planned_round > maximum_rounds:
            return "BUY_TIME_SLICE_MAXIMUM_ROUND_EXCEEDED"
        if config.get("buy_limit_enabled") is True:
            limit = _positive_float(config.get("buy_limit_amount"))
            amount = _positive_float(intent.get("budget"))
            used = cycle.get("cumulative_filled_buy_amount")
            if limit is None or amount is None or not isinstance(used, (int, float)):
                return "BUY_TIME_SLICE_STOCK_LIMIT_UNAVAILABLE"
            if used + amount > limit:
                return "BUY_TIME_SLICE_STOCK_LIMIT_EXCEEDED"
        return ""
    except (OSError, ValueError, TypeError, KeyError):
        return "BUY_TIME_SLICE_CURRENT_CONTEXT_UNAVAILABLE"


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


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if str(value).strip() not in {str(number), f"{number}.0"}:
        try:
            if float(value) != number:
                return None
        except (TypeError, ValueError):
            return None
    return number if number >= 0 else None


def _blocked(reason: str, *, preview: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "reason": reason,
        "execution_intent": None,
        "execution_intents": [],
    }
    if isinstance(preview, dict):
        result["preview"] = preview
    return result


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


def _multi_hoga_execution_intents(
    *,
    execution_intent: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if str(execution_intent.get("price_basis") or "").strip().upper() != "ORDER_PRICE":
        return _blocked("BUY_MULTI_HOGA_ORDER_PRICE_REQUIRED")

    up_count = _nonnegative_int(execution_intent.get("hoga_up"))
    down_count = _nonnegative_int(execution_intent.get("hoga_down"))
    if up_count is None or down_count is None or up_count + down_count <= 0:
        return _blocked("BUY_MULTI_HOGA_RANGE_INVALID")

    total_quantity = _positive_int(execution_intent.get("quantity"))
    approved_round_budget = _positive_float(execution_intent.get("budget"))
    base_price = _positive_float(execution_intent.get("price"))
    if total_quantity is None or approved_round_budget is None or base_price is None:
        return _blocked("BUY_MULTI_HOGA_PLAN_INPUT_INVALID")

    offsets = _hoga_offsets(up_count, down_count)
    if total_quantity < len(offsets):
        return _blocked("BUY_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT")

    instrument_type = (
        context.get("instrument_classification")
        or context.get("instrument_type")
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
        return _blocked(str(exc) or "BUY_MULTI_HOGA_PRICE_INVALID")

    quantities = _split_quantity(total_quantity, len(offsets))
    child_budgets = [price * quantity for price, quantity in zip(prices, quantities)]
    planned_total_budget = sum(child_budgets)
    if planned_total_budget > approved_round_budget:
        return _blocked("BUY_MULTI_HOGA_ROUND_BUDGET_EXCEEDED")

    total = len(offsets)
    plan = {
        "base_price": base_price,
        "hoga_offsets": list(offsets),
        "configured_child_count": total,
        "planned_child_count": total,
        "planned_total_quantity": total_quantity,
        "approved_round_budget": approved_round_budget,
        "planned_total_budget": planned_total_budget,
        "instrument_type": str(instrument_type or "STOCK"),
        "buy_round": execution_intent.get("buy_round"),
    }
    intents: list[dict[str, Any]] = []
    for index, (offset, quantity, price, child_budget) in enumerate(
        zip(offsets, quantities, prices, child_budgets),
        start=1,
    ):
        child_plan = {
            "planned_quantity": quantity,
            "planned_price": price,
            "planned_budget": child_budget,
            "hoga_offset_ticks": offset,
        }
        intents.append(
            {
                **deepcopy(execution_intent),
                "budget": child_budget,
                "quantity": quantity,
                "planned_total_quantity": total_quantity,
                "price_basis": "ORDER_PRICE",
                "price": price,
                "hoga": "LIMIT",
                "hoga_mode": "MULTI",
                "execution_mode": "MULTI_HOGA",
                "execution_process_owner_required": True,
                "plan_generation": 0,
                "child_sequence_index": index,
                "child_sequence_total": total,
                "child_kind": "HOGA_LEVEL",
                "child_plan": child_plan,
                "multi_hoga_plan": deepcopy(plan),
            }
        )
    return {
        "status": STATUS_READY,
        "reason": "",
        "execution_intent": intents[0],
        "execution_intents": intents,
    }


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


def _execution_rules(rules: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(rules.get("buy")).get("execution"))


def _evaluate_canonical_comparison(
    *,
    left: float,
    right: float,
    direction: Any,
    comparator: Any,
    threshold: Any,
) -> tuple[bool | None, float | None]:
    """Bridge the canonical zero-inclusive ratio contract to the common helper."""
    if not isfinite(left) or not isfinite(right) or left <= 0 or right <= 0:
        return None, None
    if isinstance(threshold, bool):
        return None, None
    try:
        ratio = float(threshold)
    except (TypeError, ValueError):
        return None, None
    if not isfinite(ratio) or ratio < 0:
        return None, None
    if ratio > 0:
        return evaluate_percent_comparison(
            left=left,
            right=right,
            direction=str(direction or ""),
            compare=str(comparator or ""),
            threshold=ratio,
        )
    signed = ((right - left) / left) * 100.0
    normalized_direction = str(direction or "").strip().upper()
    normalized_compare = str(comparator or "").strip().upper()
    if normalized_direction == "UP":
        return ({">=": signed >= 0, "<=": signed <= 0}.get(normalized_compare), signed)
    if normalized_direction == "DOWN":
        observed = -signed
        return ({">=": observed >= 0, "<=": observed <= 0}.get(normalized_compare), observed)
    if normalized_direction == "BOTH":
        observed = abs(signed)
        return ({"WITHIN": observed <= 0, "OUTSIDE": observed > 0}.get(normalized_compare), observed)
    return None, None


def _previous_price_skip_result(
    *,
    policy: dict[str, Any],
    cycle: dict[str, Any],
    actionable_order_price: float | None,
) -> dict[str, Any] | None:
    if policy.get("enabled") is not True or cycle.get("confirmed_buy_round") == 0:
        return None
    previous = _positive_float(cycle.get("last_confirmed_buy_order_price"))
    if previous is None:
        return _blocked("PREVIOUS_ROUND_PRICE_UNAVAILABLE")
    if actionable_order_price is None:
        return _blocked("PRICE_EVIDENCE_STALE")
    matched, observed = _evaluate_canonical_comparison(
        left=previous,
        right=actionable_order_price,
        direction=policy.get("direction"),
        comparator=policy.get("comparator"),
        threshold=policy.get("ratio_percent"),
    )
    if matched is None:
        return _blocked("PREVIOUS_ROUND_PRICE_POLICY_INVALID")
    if not matched:
        return None
    result = _blocked("BUY_GENERATION_SKIPPED_BY_PREVIOUS_ROUND_PRICE")
    result["decision"] = {
        "action": "SKIP_CURRENT_GENERATION",
        "previous_confirmed_order_price": previous,
        "actionable_order_price": actionable_order_price,
        "observed_percent": observed,
        "round_increment": False,
    }
    return result


def _cycle_planning_rules(rules: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    execution = _execution_rules(rules)
    cycle_policy = _as_dict(execution.get("cycle"))
    if not cycle_policy:
        return deepcopy(rules), None, ""
    if (
        cycle_policy.get("scope") != "SIGNAL_SCOPED_BUY_CYCLE"
        or cycle_policy.get("requires_source_signal") is not True
        or cycle_policy.get("autonomous_scheduler") is not False
        or cycle_policy.get("after_cycle_completion") != "REQUIRE_NEW_BUY_SIGNAL"
        or cycle_policy.get("execution_connected") is not True
    ):
        return deepcopy(rules), cycle_policy, "CYCLE_OPTION_EXECUTION_NOT_CONNECTED"
    order = _as_dict(cycle_policy.get("order_policy"))
    point = _as_dict(cycle_policy.get("point_policy"))
    situation = _as_dict(cycle_policy.get("situation_response"))
    if situation.get("mode") == "PRICE_COMPARE" and situation.get("action") == "CANCEL_BATCH":
        return deepcopy(rules), cycle_policy, "CYCLE_OPTION_EXECUTION_NOT_CONNECTED"

    planned = deepcopy(rules)
    planned_execution = planned.setdefault("buy", {}).setdefault("execution", {})
    base = planned_execution.setdefault("base", {})
    base.update({
        "hoga_mode": order.get("hoga_mode"),
        "order_price_basis": order.get("order_price_basis"),
        "hoga_up": order.get("hoga_up"),
        "hoga_down": order.get("hoga_down"),
        "point_mode": point.get("mode"),
    })
    if point.get("mode") == "MULTI_TIME":
        base.update({
            "point_value": point.get("value"),
            "point_unit": point.get("unit"),
            "point_range": point.get("range"),
            "point_count": point.get("count"),
            "time_order_price_basis": point.get("order_price_basis"),
        })
    elif point.get("mode") == "MULTI_RATIO":
        base.update({
            "ratio_left": point.get("left_source"),
            "ratio_right": point.get("right_source"),
            "ratio_direction": point.get("direction"),
            "ratio_value": point.get("ratio_percent"),
            "ratio_compare": point.get("comparator"),
            "ratio_count": point.get("count"),
        })
    if situation.get("mode") == "UNFILLED":
        base["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER",
            "enabled": True,
            "action": "CANCEL",
            "scope": situation.get("scope"),
            "configured_value": situation.get("configured_value"),
            "configured_unit": situation.get("configured_unit"),
        }
    elif situation.get("mode") == "PRICE_COMPARE" and situation.get("action") == "RESET":
        base["buy_price_reset_policy"] = {
            "policy": "BUY_PRICE_CHANGE_RESET",
            "enabled": True,
            "action": "RESET",
            "left_source": situation.get("left_source"),
            "right_source": situation.get("right_source"),
            "direction": situation.get("direction"),
            "threshold_percent": situation.get("ratio_percent"),
            "compare": situation.get("comparator"),
        }
    else:
        return deepcopy(rules), cycle_policy, "CYCLE_OPTION_EXECUTION_NOT_CONNECTED"
    return planned, cycle_policy, ""


def _last_plus_one_planning_rules(
    *,
    rules: dict[str, Any],
    method: str,
) -> dict[str, Any]:
    planned = deepcopy(rules)
    execution = planned.setdefault("buy", {}).setdefault("execution", {})
    base = execution.setdefault("base", {})
    base.update({
        "hoga_mode": "SINGLE",
        "hoga_up": 0,
        "hoga_down": 0,
        "point_mode": "NONE",
        "order_price_basis": "MARKET" if method == "MARKET" else "CURRENT_PRICE",
    })
    if isinstance(base.get("last_round_active_buy"), dict):
        base["last_round_active_buy"] = {
            **deepcopy(base["last_round_active_buy"]),
            "enabled": False,
        }
    repeat = execution.setdefault("repeat", {})
    repeat.update({
        "detail_mode": "BUDGET",
        "budget_ratio": 1,
        "round_operator": "ADD",
        "round_budget_value": 1,
    })
    return planned


def _apply_last_round_active_buy(
    *,
    result: dict[str, Any],
    policy: dict[str, Any],
    average_price: float | None,
) -> dict[str, Any]:
    if policy.get("enabled") is not True:
        return result
    intents = [deepcopy(item) for item in result.get("execution_intents", []) if isinstance(item, dict)]
    if not intents and isinstance(result.get("execution_intent"), dict):
        intents = [deepcopy(result["execution_intent"])]
    if not intents or intents[0].get("execution_mode") not in {"MULTI_TIME", "MULTI_RATIO"}:
        return _blocked("BUY_LAST_ROUND_ACTIVE_REQUIRES_MULTI_POINT")
    if average_price is None:
        return _blocked("BUY_LAST_ROUND_ACTIVE_AVERAGE_PRICE_UNAVAILABLE")
    last = intents[-1]
    set_price = _positive_float(last.get("price"))
    if set_price is None:
        return _blocked("BUY_LAST_ROUND_ACTIVE_SET_PRICE_UNAVAILABLE")
    matched, observed = _evaluate_canonical_comparison(
        left=set_price,
        right=average_price,
        direction=policy.get("direction"),
        comparator=policy.get("comparator"),
        threshold=policy.get("ratio_percent"),
    )
    if matched is None:
        return _blocked("BUY_LAST_ROUND_ACTIVE_POLICY_INVALID")
    decision = {
        "policy": "LAST_MULTI_POINT_CHILD",
        "matched": matched,
        "set_price": set_price,
        "average_price": average_price,
        "observed_percent": observed,
        "budget_policy_override": "NONE",
    }
    if matched:
        intents[-1]["last_round_active_decision"] = decision
    else:
        intents = intents[:-1]
        if not intents:
            blocked = _blocked("BUY_LAST_ROUND_ACTIVE_CHILD_SKIPPED")
            blocked["decision"] = decision
            return blocked
        effective_count = len(intents)
        total_quantity = sum(int(item.get("quantity") or 0) for item in intents)
        total_budget = sum(float(item.get("budget") or 0) for item in intents)
        for index, item in enumerate(intents, 1):
            item["child_sequence_index"] = index
            item["child_sequence_total"] = effective_count
            item["planned_total_quantity"] = total_quantity
            item["last_round_active_decision"] = decision
            options = _as_dict(item.get("approved_execution_options"))
            if item.get("execution_mode") == "MULTI_RATIO":
                options["ratio_count"] = effective_count
                item["approved_execution_options"] = options
            plan_key = "multi_time_plan" if item.get("execution_mode") == "MULTI_TIME" else "multi_ratio_plan"
            plan = _as_dict(item.get(plan_key))
            plan.update({
                "configured_child_count": effective_count,
                "planned_child_count": effective_count,
                "planned_total_quantity": total_quantity,
                "planned_total_budget": total_budget,
                "last_round_active_original_child_count": effective_count + 1,
                "last_round_active_terminal_skip": True,
            })
            if plan_key == "multi_time_plan" and isinstance(plan.get("scheduled_offsets_ms"), list):
                plan["scheduled_offsets_ms"] = plan["scheduled_offsets_ms"][:effective_count]
            item[plan_key] = plan
    result = deepcopy(result)
    result["execution_intents"] = intents
    result["execution_intent"] = intents[0]
    result["last_round_active_decision"] = decision
    return result


def _multi_time_execution_intents(intent: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    base = _as_dict(_as_dict(_as_dict(context.get("rules")).get("buy")).get("execution")).get("base", {})
    if intent.get("hoga_mode") != "SINGLE":
        return _blocked("BUY_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED")
    count = _positive_int(base.get("point_count"))
    value = _positive_int(base.get("point_value"))
    quantity = _positive_int(intent.get("quantity"))
    if count is None or value is None or quantity is None:
        return _blocked("BUY_MULTI_TIME_PLAN_INVALID")
    if quantity < count:
        return _blocked("BUY_MULTI_TIME_QUANTITY_BELOW_CHILD_COUNT")
    unit_ms = _time_unit_milliseconds(base.get("point_unit"), context)
    if unit_ms is None:
        return _blocked("BUY_MULTI_TIME_UNIT_UNRESOLVED")
    offsets = _time_offsets_milliseconds(count=count, value=value, unit_milliseconds=unit_ms, range_mode=base.get("point_range"))
    if offsets is None:
        return _blocked("BUY_MULTI_TIME_RANGE_INVALID")
    price_basis = base.get("time_order_price_basis")
    if price_basis not in {"ORDER_PRICE", "CURRENT_PRICE"}:
        return _blocked("BUY_MULTI_TIME_PRICE_POLICY_MISSING")
    price = _positive_float(context.get("actionable_current_price") if price_basis == "CURRENT_PRICE" else context.get("reference_price"))
    if price is None:
        return _blocked("CURRENT_PRICE_VALUE_MISSING" if price_basis == "CURRENT_PRICE" else "REFERENCE_PRICE_VALUE_MISSING")
    budget = _positive_float(intent.get("budget"))
    if budget is None or quantity * price > budget:
        return _blocked("BUY_MULTI_TIME_ROUND_BUDGET_EXCEEDED")
    plan = {
        "configured_child_count": count, "planned_child_count": count,
        "planned_total_quantity": quantity, "approved_round_budget": budget,
        "planned_total_budget": quantity * price, "scheduled_offsets_ms": offsets,
        "time_value": value, "time_unit": base.get("point_unit"),
        "time_range": base.get("point_range"), "price_basis": price_basis,
        "buy_round": intent.get("buy_round"),
    }
    children = []
    for index, (qty, offset) in enumerate(zip(_split_quantity(quantity, count), offsets), 1):
        children.append({
            **deepcopy(intent), "quantity": qty, "budget": qty * price,
            "price": price, "price_basis": price_basis, "hoga": "LIMIT",
            "execution_mode": "MULTI_TIME", "execution_process_owner_required": True,
            "plan_generation": 0, "child_kind": "TIME_SLICE",
            "child_sequence_index": index, "child_sequence_total": count,
            "planned_total_quantity": quantity, "multi_time_plan": deepcopy(plan),
            "child_plan": {"planned_quantity": qty, "planned_price": price,
                           "planned_budget": qty * price, "scheduled_offset_ms": offset},
        })
    return {"status": STATUS_READY, "reason": "", "execution_intent": children[0], "execution_intents": children}


def _buy_ratio_intent_issue(intent: dict[str, Any]) -> bool:
    """A legacy SINGLE with ratio options is never a valid deferred child."""
    plan = _as_dict(intent.get("multi_ratio_plan"))
    options = _as_dict(intent.get("approved_execution_options"))
    count = _positive_int(plan.get("planned_child_count"))
    quantity = _positive_int(plan.get("planned_total_quantity"))
    index = _positive_int(intent.get("child_sequence_index"))
    price = _positive_float(intent.get("price"))
    budget = _positive_float(plan.get("approved_round_budget"))
    order_price = _positive_float(plan.get("order_price"))
    threshold = _positive_float(plan.get("ratio_value"))
    if (intent.get("execution_mode") != "MULTI_RATIO" or intent.get("child_kind") != "RATIO_SLICE"
            or intent.get("hoga_mode") != "SINGLE" or intent.get("hoga") != "LIMIT"
            or options.get("point_mode") != "MULTI_RATIO"
            or count is None or quantity is None or quantity < count or index is None or index > count
            or price is None or not isfinite(price) or budget is None or not isfinite(budget)
            or order_price is None or not isfinite(order_price) or threshold is None or not isfinite(threshold)):
        return True
    if (intent.get("child_sequence_total") != count or options.get("ratio_count") != count
            or plan.get("configured_child_count") != count
            or intent.get("planned_total_quantity") != quantity
            or _positive_int(intent.get("buy_round")) is None or plan.get("buy_round") != intent.get("buy_round")
            or intent.get("quantity") != _split_quantity(quantity, count)[index - 1]
            or intent.get("budget") != intent["quantity"] * price
            or plan.get("planned_total_budget") != quantity * order_price
            or quantity * order_price > budget
            or intent.get("price_basis") not in {"ORDER_PRICE", "CURRENT_PRICE"}
            or plan.get("price_basis") != intent.get("price_basis")
            or (intent.get("price_basis") == "ORDER_PRICE" and price != order_price)):
        return True
    for key in ("ratio_left", "ratio_right", "ratio_direction", "ratio_value", "ratio_compare"):
        if plan.get(key) != options.get(key):
            return True
    if not {plan.get("ratio_left"), plan.get("ratio_right")} <= {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}:
        return True
    return evaluate_percent_comparison(left=100, right=100, direction=plan.get("ratio_direction"),
        compare=plan.get("ratio_compare"), threshold=threshold)[0] is None


def _buy_unfilled_timeout_policy(base: dict[str, Any], context: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    raw = base.get("unfilled_timeout_policy")
    if raw is None or (isinstance(raw, dict) and raw.get("enabled") is False):
        return None, ""
    policy = _as_dict(raw)
    value = policy.get("configured_value")
    if (policy.get("policy") != "CANCEL_PENDING_ORDER" or policy.get("enabled") is not True
            or policy.get("action") != "CANCEL" or policy.get("scope") not in {"EACH", "BATCH"}
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < 0):
        return None, "BUY_UNFILLED_TIMEOUT_POLICY_INVALID"
    unit_ms = _time_unit_milliseconds(policy.get("configured_unit"), context)
    if unit_ms is None:
        return None, "BUY_UNFILLED_TIMEOUT_UNIT_UNRESOLVED"
    timeout_ms = value * unit_ms
    if not isfinite(timeout_ms) or not float(timeout_ms).is_integer():
        return None, "BUY_UNFILLED_TIMEOUT_VALUE_INVALID"
    result = {**deepcopy(policy), "timeout_ms": int(timeout_ms), "anchor": "BROKER_ACCEPTED_AT"}
    if policy.get("configured_unit") == "BAR":
        result["timeframe_minutes"] = unit_ms // 60000
    return result, ""


def _multi_ratio_execution_intents(intent: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Split one approved BUY round; eligibility, not planning, tests prices."""
    base = _as_dict(_as_dict(_as_dict(_as_dict(context.get("rules")).get("buy")).get("execution")).get("base"))
    if intent.get("hoga_mode") != "SINGLE":
        return _blocked("BUY_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED")
    count = _positive_int(base.get("ratio_count"))
    quantity = _positive_int(intent.get("quantity"))
    if count is None or quantity is None:
        return _blocked("BUY_MULTI_RATIO_PLAN_INVALID")
    if quantity < count:
        return _blocked("BUY_MULTI_RATIO_QUANTITY_BELOW_CHILD_COUNT")
    price_basis = intent.get("price_basis")
    if price_basis not in {"ORDER_PRICE", "CURRENT_PRICE"}:
        return _blocked("BUY_MULTI_RATIO_PRICE_POLICY_UNSUPPORTED")
    price = _positive_float(intent.get("price"))
    budget = _positive_float(intent.get("budget"))
    threshold = _positive_float(base.get("ratio_value"))
    sources = {base.get("ratio_left"), base.get("ratio_right")}
    if (not sources <= {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or threshold is None or not isfinite(threshold)):
        return _blocked("BUY_MULTI_RATIO_TRIGGER_INVALID")
    eligible, _ = evaluate_percent_comparison(left=100, right=100,
        direction=base.get("ratio_direction"), compare=base.get("ratio_compare"), threshold=threshold)
    if eligible is None:
        return _blocked("BUY_MULTI_RATIO_TRIGGER_INVALID")
    if price is None or not isfinite(price) or budget is None or not isfinite(budget) or quantity * price > budget:
        return _blocked("BUY_MULTI_RATIO_ROUND_BUDGET_EXCEEDED")
    plan = {
        "configured_child_count": count, "planned_child_count": count,
        "planned_total_quantity": quantity, "approved_round_budget": budget,
        "planned_total_budget": quantity * price, "price_basis": price_basis,
        "buy_round": intent.get("buy_round"), "order_price": price,
        **{key: base[key] for key in ("ratio_left", "ratio_right", "ratio_direction", "ratio_compare")},
        "ratio_value": threshold,
    }
    children = [{
        **deepcopy(intent), "quantity": qty, "budget": qty * price, "hoga": "LIMIT",
        "execution_mode": "MULTI_RATIO", "execution_process_owner_required": True,
        "plan_generation": 0, "child_kind": "RATIO_SLICE",
        "child_sequence_index": index, "child_sequence_total": count,
        "planned_total_quantity": quantity, "multi_ratio_plan": deepcopy(plan),
        "child_plan": {"planned_quantity": qty, "planned_price": price, "planned_budget": qty * price},
    } for index, qty in enumerate(_split_quantity(quantity, count), 1)]
    return {"status": STATUS_READY, "reason": "", "execution_intent": children[0], "execution_intents": children}


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
    sizing_reference_price: float,
) -> dict[str, Any]:
    mode = str(stock_config.get("trade_amount_type") or "QUANTITY").strip().upper()
    budget: dict[str, Any] = {
        "starting_budget_type": mode,
        "sizing_reference_price": sizing_reference_price,
        "base_buy_budget": _positive_float(cycle.get("base_filled_buy_amount")),
        "previous_buy_budget": _positive_float(cycle.get("last_filled_buy_amount")),
        "max_buy_rounds": _maximum_rounds(stock_config, rules),
    }
    account_budget = _as_dict(cycle.get("account_budget"))
    if account_budget:
        budget.update(
            {
                "system_total_budget_gate_required": True,
                "system_total_budget": account_budget.get("system_total_budget"),
                "account_consumed_amount": account_budget.get("account_consumed_amount"),
                "account_no": account_budget.get("account_no"),
            }
        )
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


def _configured_order_price_basis(rules: dict[str, Any]) -> str:
    execution = _as_dict(_as_dict(rules.get("buy")).get("execution"))
    base = _as_dict(execution.get("base"))
    return str(base.get("order_price_basis") or "").strip().upper()


def _buy_price_compare_branch_planning_rules(
    *,
    rules: dict[str, Any],
    confirmed_round: int,
    average_price: Any,
    actionable_order_price: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Apply an approved price-compare branch to the existing repeat budget policy."""
    if confirmed_round <= 0:
        return rules, None, ""
    price_compare = _as_dict(_as_dict(_as_dict(rules.get("buy")).get("filters")).get("price_compare"))
    conditions = price_compare.get("conditions")
    branch_conditions = [
        condition for condition in conditions or []
        if isinstance(condition, dict) and isinstance(condition.get("branch_policy"), dict)
    ] if isinstance(conditions, list) else []
    if not branch_conditions:
        return rules, None, ""
    boundary = {
        str(condition.get("branch_id") or ""): str(condition.get("operator") or "")
        for condition in branch_conditions
    }
    if boundary != {"BELOW_OR_EQUAL": "<=", "ABOVE": ">"}:
        return rules, None, "BUY_PRICE_COMPARE_BRANCH_BOUNDARY_INVALID"

    average = _positive_float(average_price)
    order_price = _positive_float(actionable_order_price)
    if average is None or order_price is None:
        return rules, None, "BUY_PRICE_COMPARE_BRANCH_EVIDENCE_UNAVAILABLE"

    def matches(operator: str) -> bool:
        return {"<=": average <= order_price, ">": average > order_price}.get(operator, False)

    matched = [condition for condition in branch_conditions if matches(str(condition.get("operator") or ""))]
    if len(matched) != 1:
        return rules, None, "BUY_PRICE_COMPARE_BRANCH_NOT_DETERMINISTIC"
    selected = matched[0]
    policy = _as_dict(selected.get("branch_policy"))
    detail_mode = str(policy.get("detail_mode") or "").strip().upper()
    if detail_mode == "ROUND":
        if (
            policy.get("round_operator") not in {"ADD", "MULTIPLY"}
            or _positive_float(policy.get("round_budget_value")) is None
        ):
            return rules, None, "BUY_PRICE_COMPARE_BRANCH_POLICY_INVALID"
    elif detail_mode == "BUDGET":
        if _positive_float(policy.get("budget_ratio")) is None:
            return rules, None, "BUY_PRICE_COMPARE_BRANCH_POLICY_INVALID"
    else:
        return rules, None, "BUY_PRICE_COMPARE_BRANCH_POLICY_UNSUPPORTED"

    planned = deepcopy(rules)
    repeat = planned.setdefault("buy", {}).setdefault("execution", {}).setdefault("repeat", {})
    repeat.update(deepcopy(policy))
    evidence = {
        "branch_id": selected.get("branch_id"),
        "average_price": average,
        "actionable_order_price": order_price,
        "operator": selected.get("operator"),
        "policy": deepcopy(policy),
    }
    return planned, evidence, ""


def build_indicator_follow_buy_intent(
    *,
    buy_signal_result: Any,
    context: Any,
) -> dict[str, Any]:
    """Return a routine-owned intent or a fail-closed block result."""
    signal = deepcopy(_as_dict(buy_signal_result))
    if not any(
        str(signal.get(key) or "").strip().upper() == "BUY"
        for key in ("signal", "side", "signal_type", "action", "decision")
    ) and signal.get("buy_signal") is not True and signal.get("is_buy") is not True:
        return _blocked("BUY_SOURCE_SIGNAL_REQUIRED")
    runtime_context = _as_dict(context)
    cycle = _as_dict(runtime_context.get("cycle"))
    if isinstance(runtime_context.get("account_budget"), dict):
        cycle["account_budget"] = deepcopy(runtime_context["account_budget"])
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

    stock_config = _as_dict(runtime_context.get("stock_config"))
    rules = _as_dict(runtime_context.get("rules"))
    execution_rules = _execution_rules(rules)
    additional = _as_dict(execution_rules.get("additional"))
    previous_price_policy = _as_dict(additional.get("previous_round_price_skip"))
    last_plus_one_policy = _as_dict(additional.get("last_plus_one"))
    maximum_rounds = _maximum_rounds(stock_config, rules)
    if last_plus_one_policy.get("enabled") is True and maximum_rounds is None:
        return _blocked("LAST_PLUS_ONE_MAX_ROUND_UNAVAILABLE")
    if cycle.get("last_plus_one_completed") is True:
        return _blocked("BUY_ADDITIONAL_PROGRESS_COMPLETED")
    last_plus_one = bool(
        last_plus_one_policy.get("enabled") is True
        and maximum_rounds is not None
        and confirmed_round == maximum_rounds
    )
    if last_plus_one and cycle.get("last_plus_one_pending") is True:
        return _blocked("LAST_PLUS_ONE_ALREADY_PENDING")

    if last_plus_one:
        planning_rules = _last_plus_one_planning_rules(
            rules=rules,
            method=str(last_plus_one_policy.get("method") or ""),
        )
        cycle_policy = None
        cycle_reason = ""
    else:
        planning_rules, cycle_policy, cycle_reason = _cycle_planning_rules(rules)
    if cycle_reason:
        return _blocked(cycle_reason)
    price_basis = _configured_order_price_basis(planning_rules)
    reference_price = _positive_float(
        runtime_context.get("reference_price", runtime_context.get("current_price"))
    )
    actionable_price = _positive_float(runtime_context.get("actionable_current_price"))
    sizing_reference_price = (
        actionable_price if price_basis == "CURRENT_PRICE" else reference_price
    )
    if sizing_reference_price is None:
        reason = (
            "CURRENT_PRICE_VALUE_MISSING"
            if price_basis == "CURRENT_PRICE"
            else "REFERENCE_PRICE_VALUE_MISSING"
        )
        return {"status": "BLOCKED", "reason": reason, "execution_intent": None}

    actionable_order_price = (
        actionable_price if price_basis in {"CURRENT_PRICE", "MARKET"} else reference_price
    )
    branch_evidence = None
    if not last_plus_one:
        planning_rules, branch_evidence, branch_reason = _buy_price_compare_branch_planning_rules(
            rules=planning_rules,
            confirmed_round=confirmed_round,
            average_price=cycle.get("avg_price"),
            actionable_order_price=actionable_order_price,
        )
        if branch_reason:
            return _blocked(branch_reason)
    support_reason = inspect_buy_execution_support(
        subject={"side": "BUY", "buy_round": next_round}, rules=planning_rules, _planning=True,
    )
    if support_reason:
        return _blocked(support_reason)
    price_skip = _previous_price_skip_result(
        policy=previous_price_policy,
        cycle=cycle,
        actionable_order_price=actionable_order_price,
    )
    if price_skip is not None:
        return price_skip

    if last_plus_one and last_plus_one_policy.get("method") == "ACTIVE":
        average_price = _positive_float(cycle.get("avg_price"))
        if actionable_price is None:
            return _blocked("PRICE_EVIDENCE_STALE")
        if average_price is None:
            return _blocked("LAST_PLUS_ONE_AVERAGE_PRICE_UNAVAILABLE")
        matched, observed = _evaluate_canonical_comparison(
            left=actionable_price,
            right=average_price,
            direction=_as_dict(last_plus_one_policy.get("active_condition")).get("direction"),
            comparator=_as_dict(last_plus_one_policy.get("active_condition")).get("comparator"),
            threshold=_as_dict(last_plus_one_policy.get("active_condition")).get("ratio_percent"),
        )
        if matched is None:
            return _blocked("LAST_PLUS_ONE_ACTIVE_CONDITION_INVALID")
        if not matched:
            blocked = _blocked("LAST_PLUS_ONE_ACTIVE_CONDITION_NOT_MET")
            blocked["decision"] = {"matched": False, "observed_percent": observed, "occurrence_consumed": False}
            return blocked

    signal.update({
        "side": "BUY",
        "sizing_reference_price": sizing_reference_price,
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": runtime_context.get("routine_instance_id"),
        "cycle_identity": cycle.get("cycle_identity"),
        "confirmed_previous_round": confirmed_round,
    })
    if price_basis == "CURRENT_PRICE":
        signal["current_price"] = actionable_price
    elif price_basis == "ORDER_PRICE":
        signal["order_price"] = reference_price
    budget_context = _budget_context(
        stock_config=stock_config,
        rules=planning_rules,
        cycle=cycle,
        sizing_reference_price=sizing_reference_price,
    )
    if last_plus_one:
        approved_budget = _positive_float(cycle.get("last_normal_round_approved_budget"))
        if approved_budget is None:
            return _blocked("LAST_NORMAL_ROUND_APPROVED_BUDGET_UNAVAILABLE")
        budget_context["previous_buy_budget"] = approved_budget
        budget_context["max_buy_rounds"] = int(maximum_rounds or 0) + 1
    preview = build_buy_order_candidate_preview(
        buy_signal_result=signal,
        approved_rules=_official_execution_rules(planning_rules),
        runtime_state_snapshot={
            "confirmed_current_buy_round": confirmed_round,
            "confirmed_cumulative_buy_budget": cycle.get("cumulative_filled_buy_amount"),
        },
        budget_context=budget_context,
    )
    if preview.get("status") != STATUS_READY:
        issues = _as_dict(preview.get("execution_policy_result")).get("issues")
        reason = issues[0] if isinstance(issues, list) and issues else "BUY_EXECUTION_POLICY_BLOCKED"
        return {"status": "BLOCKED", "reason": reason, "execution_intent": None, "preview": preview}

    intent = deepcopy(_as_dict(preview.get("execution_intent")))
    intent["confirmed_previous_round"] = confirmed_round
    intent["actionable_order_price"] = actionable_order_price
    if branch_evidence is not None:
        intent["buy_price_compare_branch"] = branch_evidence
    if last_plus_one:
        intent.update({
            "generation_kind": "LAST_PLUS_ONE",
            "last_plus_one_occurrence": 1,
            "budget_reference": "LAST_NORMAL_ROUND_APPROVED_BUDGET",
            "normal_max_buy_round": maximum_rounds,
        })
    if next_round > 1:
        repeat_started_at = runtime_context.get("buy_repeat_started_at") or cycle.get("buy_repeat_started_at")
        if next_round == 2 and not repeat_started_at:
            repeat_started_at = runtime_context.get("now") or datetime.now().isoformat(timespec="milliseconds")
        if repeat_started_at:
            intent["buy_repeat_started_at"] = str(repeat_started_at)
    base = _as_dict(_as_dict(_as_dict(planning_rules.get("buy")).get("execution")).get("base"))
    timeout_policy, timeout_reason = _buy_unfilled_timeout_policy(base, runtime_context)
    if timeout_reason:
        return _blocked(timeout_reason)
    if timeout_policy is not None:
        intent["unfilled_timeout_policy"] = timeout_policy
    exit_policy = _as_dict(base.get("buy_exit_policy"))
    if exit_policy:
        if (
            exit_policy.get("policy") != "BUY_REPEAT_EXIT"
            or exit_policy.get("enabled") is not True
            or str(exit_policy.get("logic") or "").upper() != "OR"
            or not isinstance(exit_policy.get("conditions"), list)
            or not exit_policy.get("conditions")
        ):
            return _blocked("BUY_EXIT_POLICY_INVALID")
        intent["buy_exit_policy"] = deepcopy(exit_policy)
    reset_policy = _as_dict(base.get("buy_price_reset_policy"))
    if reset_policy.get("enabled") is True:
        if (reset_policy.get("policy") != "BUY_PRICE_CHANGE_RESET"
                or reset_policy.get("action") != "RESET"
                or reset_policy.get("left_source") not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
                or reset_policy.get("right_source") not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
                or reset_policy.get("direction") not in {"UP", "DOWN", "BOTH"}
                or reset_policy.get("compare") not in {">=", "<=", "WITHIN", "OUTSIDE"}
                or not isinstance(reset_policy.get("threshold_percent"), (int, float))
                or isinstance(reset_policy.get("threshold_percent"), bool)
                or not isfinite(float(reset_policy.get("threshold_percent")))
                or float(reset_policy.get("threshold_percent")) <= 0):
            return _blocked("BUY_PRICE_RESET_POLICY_INVALID")
        intent["buy_price_reset_policy"] = deepcopy(reset_policy)
    planning_context = deepcopy(runtime_context)
    planning_context["rules"] = planning_rules
    if base.get("point_mode") == "MULTI_RATIO":
        result = _multi_ratio_execution_intents(intent, planning_context)
    elif base.get("point_mode") == "MULTI_TIME":
        result = _multi_time_execution_intents(intent, planning_context)
    elif str(intent.get("hoga_mode") or "").strip().upper() == "MULTI":
        result = _multi_hoga_execution_intents(
            execution_intent=intent,
            context=planning_context,
        )
    else:
        result = {"status": STATUS_READY, "reason": "", "execution_intent": intent, "execution_intents": [intent]}
    if result.get("status") != STATUS_READY:
        result["preview"] = preview
        return result

    if cycle_policy is not None:
        cycle_snapshot = {
            "scope": "SIGNAL_SCOPED_BUY_CYCLE",
            "requires_source_signal": True,
            "autonomous_scheduler": False,
            "after_cycle_completion": "REQUIRE_NEW_BUY_SIGNAL",
        }
        intents = [deepcopy(item) for item in result.get("execution_intents", []) if isinstance(item, dict)]
        for item in intents:
            item["cycle_scope"] = "SIGNAL_SCOPED_BUY_CYCLE"
            item["signal_scoped_cycle"] = deepcopy(cycle_snapshot)
        result["execution_intents"] = intents
        result["execution_intent"] = intents[0]

    original_base = _as_dict(execution_rules.get("base"))
    if not last_plus_one:
        result = _apply_last_round_active_buy(
            result=result,
            policy=_as_dict(original_base.get("last_round_active_buy")),
            average_price=_positive_float(cycle.get("avg_price")),
        )
    result["preview"] = preview
    return result
