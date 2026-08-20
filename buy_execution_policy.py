# -*- coding: utf-8 -*-
"""Pure indicator-follow BUY execution policy evaluator.

``base`` means the first BUY in a new position cycle. ``repeat`` means an
additional BUY after at least one BUY round has been confirmed by fills. This
module plans the next BUY only; it never confirms rounds, writes runtime data,
queues orders, or calls a broker.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

from account_auto_trade_budget_consumption import (
    project_system_total_budget_buy_admission,
)


POLICY_TYPE = "BUY_EXECUTION_POLICY"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"

BASE_PATH = "buy.execution.base"
REPEAT_PATH = "buy.execution.repeat"
SUPPORTED_HOGA_MODES = {"SINGLE", "MULTI"}
SUPPORTED_PRICE_BASIS = {"ORDER_PRICE", "CURRENT_PRICE", "MARKET"}
SUPPORTED_DETAIL_MODES = {"ROUND", "BUDGET", "ACTIVE_BUY"}
SUPPORTED_ROUND_OPERATORS = {"ADD", "MULTIPLY"}
SUPPORTED_STARTING_BUDGET_TYPES = {"QUANTITY", "AMOUNT"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _positive_float(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None or number <= 0:
        return None
    return number


def _positive_int(value: Any) -> int | None:
    number = _safe_int(value)
    if number is None or number <= 0:
        return None
    return number


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _approved_execution_rules(approved_rules: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    rules = _as_dict(approved_rules)
    if "base" in rules or "repeat" in rules:
        return _as_dict(rules.get("base")), _as_dict(rules.get("repeat"))
    return _as_dict(_get_path(rules, BASE_PATH)), _as_dict(_get_path(rules, REPEAT_PATH))


def _signal_is_buy(signal_context: dict[str, Any]) -> bool:
    for key in ("side", "signal", "signal_type", "action", "decision"):
        value = str(signal_context.get(key) or "").strip().upper()
        if value == "BUY":
            return True
        if value in {"SELL", "CANCEL", "HOLD", "NONE"}:
            return False
    return signal_context.get("buy_signal") is True or signal_context.get("is_buy") is True


def _price_for_basis(
    order_price_basis: str,
    signal_context: dict[str, Any],
    budget_context: dict[str, Any],
) -> tuple[float | None, str]:
    source_keys = {
        "ORDER_PRICE": ("order_price", "planned_order_price", "price"),
        "CURRENT_PRICE": ("current_price", "latest_price", "close"),
        "MARKET": ("market_price", "current_price", "latest_price", "price"),
    }
    for source in (signal_context, budget_context):
        for key in source_keys.get(order_price_basis, ()):
            price = _positive_float(source.get(key))
            if price is not None:
                return price, key
    if order_price_basis == "MARKET":
        return None, "MARKET"
    return None, "MISSING"


def _current_price(signal_context: dict[str, Any], budget_context: dict[str, Any]) -> tuple[float | None, str]:
    for source in (signal_context, budget_context):
        for key in ("current_price", "latest_price", "close"):
            price = _positive_float(source.get(key))
            if price is not None:
                return price, key
    return None, "MISSING"


def _confirmed_round(runtime_state: dict[str, Any]) -> int | None:
    value = _safe_int(runtime_state.get("confirmed_current_buy_round"))
    if value is None or value < 0:
        return None
    return value


def _confirmed_cumulative_budget(runtime_state: dict[str, Any]) -> float | None:
    value = _safe_float(runtime_state.get("confirmed_cumulative_buy_budget"))
    if value is None or value < 0:
        return None
    return value


def _max_rounds(budget_context: dict[str, Any]) -> int | None:
    return _positive_int(budget_context.get("max_buy_rounds"))


def _base_budget(
    budget_context: dict[str, Any],
    current_price: float | None,
) -> tuple[float | None, int | None, str | None, dict[str, Any], list[str]]:
    budget_type = str(budget_context.get("starting_budget_type") or "").strip().upper()
    if not budget_type:
        budget_type = "QUANTITY" if _positive_int(budget_context.get("starting_quantity")) else "AMOUNT"
    evidence: dict[str, Any] = {
        "starting_budget_type": budget_type,
        "current_price": current_price,
    }
    if budget_type not in SUPPORTED_STARTING_BUDGET_TYPES:
        return None, None, None, evidence, ["INVALID_STARTING_BUDGET_TYPE"]
    if current_price is None:
        return None, None, None, evidence, ["CURRENT_PRICE_VALUE_MISSING"]

    if budget_type == "QUANTITY":
        quantity = _positive_int(budget_context.get("starting_quantity"))
        evidence["starting_quantity"] = quantity
        if quantity is None:
            return None, None, None, evidence, ["STARTING_QUANTITY_NOT_POSITIVE"]
        return current_price * quantity, quantity, "STARTING_QUANTITY", evidence, []

    amount = None
    for key in ("starting_amount", "base_buy_budget", "base_round_budget"):
        amount = _positive_float(budget_context.get(key))
        if amount is not None:
            evidence["starting_amount_source"] = key
            break
    evidence["starting_amount"] = amount
    if amount is None:
        return None, None, None, evidence, ["STARTING_AMOUNT_NOT_POSITIVE"]
    quantity = math.floor(amount / current_price)
    if quantity <= 0:
        return None, None, None, evidence, ["STARTING_AMOUNT_BELOW_ONE_SHARE"]
    effective_budget = current_price * quantity
    evidence["ignored_remainder"] = amount - effective_budget
    return effective_budget, quantity, "STARTING_AMOUNT", evidence, []


def _repeat_budget(
    repeat_rule: dict[str, Any],
    budget_context: dict[str, Any],
    current_price: float | None,
) -> tuple[float | None, int | None, str | None, dict[str, Any], list[str]]:
    detail_mode = str(repeat_rule.get("detail_mode") or "").strip().upper()
    operator = str(repeat_rule.get("round_operator") or "").strip().upper()
    base_budget = _positive_float(budget_context.get("base_buy_budget"))
    previous_budget = _positive_float(budget_context.get("previous_buy_budget"))
    evidence: dict[str, Any] = {
        "detail_mode": detail_mode,
        "round_operator": operator,
        "base_buy_budget": base_budget,
        "previous_buy_budget": previous_budget,
        "current_price": current_price,
    }
    if detail_mode not in SUPPORTED_DETAIL_MODES:
        return None, None, None, evidence, ["INVALID_REPEAT_DETAIL_MODE"]
    if detail_mode == "ACTIVE_BUY":
        evidence["required_inputs"] = [
            "current_price",
            "confirmed_average_buy_price",
            "active_direction",
            "active_ratio",
            "active_compare",
        ]
        evidence["active_buy"] = {
            "direction": repeat_rule.get("active_direction"),
            "ratio": repeat_rule.get("active_ratio"),
            "compare": repeat_rule.get("active_compare"),
            "implemented": False,
        }
        return None, None, None, evidence, ["ACTIVE_BUY_NOT_IMPLEMENTED"]
    if current_price is None:
        return None, None, None, evidence, ["CURRENT_PRICE_VALUE_MISSING"]
    if previous_budget is None:
        return None, None, None, evidence, ["PREVIOUS_BUY_BUDGET_NOT_POSITIVE"]

    budget: float | None = None
    reference: str | None = None
    if detail_mode == "ROUND":
        factor = _positive_float(repeat_rule.get("round_budget_value"))
        evidence["round_budget_factor"] = factor
        if base_budget is None:
            return None, None, None, evidence, ["BASE_BUY_BUDGET_NOT_POSITIVE"]
        if factor is None:
            return None, None, None, evidence, ["ROUND_BUDGET_FACTOR_NOT_POSITIVE"]
        if operator == "ADD":
            budget = previous_budget + (base_budget * factor)
            reference = "PREVIOUS_PLUS_BASE"
        elif operator == "MULTIPLY":
            budget = base_budget * factor
            reference = "BASE_BUDGET"
        else:
            return None, None, None, evidence, ["INVALID_ROUND_OPERATOR"]
    elif detail_mode == "BUDGET":
        factor = _positive_float(repeat_rule.get("budget_ratio"))
        evidence["previous_budget_factor"] = factor
        if factor is None:
            return None, None, None, evidence, ["BUDGET_FACTOR_NOT_POSITIVE"]
        budget = previous_budget * factor
        reference = "PREVIOUS_BUDGET"

    quantity = math.floor((budget or 0) / current_price)
    if quantity <= 0:
        return None, None, reference, evidence, ["ROUND_BUDGET_BELOW_ONE_SHARE"]
    effective_budget = current_price * quantity
    evidence["calculated_budget"] = budget
    evidence["ignored_remainder"] = (budget or 0) - effective_budget
    return effective_budget, quantity, reference, evidence, []


def _result(
    *,
    status: str,
    issues: list[str],
    approved_base: dict[str, Any],
    approved_repeat: dict[str, Any],
    runtime_state: dict[str, Any],
    buy_phase: str | None = None,
    buy_round: int | None = None,
    order_price_basis: str | None = None,
    order_price: float | None = None,
    hoga_mode: str | None = None,
    hoga_up: int | None = None,
    hoga_down: int | None = None,
    round_budget: float | None = None,
    quantity: int | None = None,
    budget_reference: str | None = None,
    is_last_round: bool | None = None,
    remaining_budget_after_candidate: float | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved_payload = {"base": deepcopy(approved_base), "repeat": deepcopy(approved_repeat)}
    calculation_payload = {
        "status": status,
        "issues": list(issues),
        "buy_phase": buy_phase,
        "buy_round": buy_round,
        "order_price_basis": order_price_basis,
        "order_price": order_price,
        "hoga_mode": hoga_mode,
        "hoga_up": hoga_up,
        "hoga_down": hoga_down,
        "round_budget": round_budget,
        "quantity": quantity,
        "budget_reference": budget_reference,
        "is_last_round": is_last_round,
        "remaining_budget_after_candidate": remaining_budget_after_candidate,
        "system_budget_admission": {
            key: (evidence or {}).get(key)
            for key in (
                "account_no",
                "system_total_budget",
                "account_consumed_amount",
                "candidate_buy_amount",
                "projected_account_consumption",
                "system_total_budget_exceeded",
            )
        },
    }
    approved_rule_hash = _stable_hash(approved_payload)
    runtime_state_hash = _stable_hash(runtime_state)
    calculation_hash = _stable_hash(calculation_payload)
    policy_hash = _stable_hash({
        "policy_type": POLICY_TYPE,
        "approved_rule_hash": approved_rule_hash,
        "runtime_state_hash": runtime_state_hash,
        "calculation_hash": calculation_hash,
    })
    snapshot = {
        "approved_rule_hash": approved_rule_hash,
        "runtime_state_hash": runtime_state_hash,
        "calculation_hash": calculation_hash,
        "policy_hash": policy_hash,
    }
    system_evidence = evidence or {}
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "ready": status == STATUS_READY,
        "blocked": status != STATUS_READY,
        "order_candidate_draft": status == STATUS_READY,
        "runtime_write": False,
        "order_write": False,
        "send_order": False,
        "issues": list(issues),
        "buy_phase": buy_phase,
        "buy_round": buy_round,
        "next_buy_round": buy_round,
        "order_price_basis": order_price_basis,
        "order_price": order_price,
        "hoga_mode": hoga_mode,
        "hoga_up": hoga_up,
        "hoga_down": hoga_down,
        "round_budget": round_budget,
        "quantity": quantity,
        "budget_reference": budget_reference,
        "is_last_round": is_last_round,
        "remaining_budget_after_candidate": remaining_budget_after_candidate,
        "evidence": deepcopy(evidence or {}),
        "system_total_budget": system_evidence.get("system_total_budget"),
        "account_consumed_amount": system_evidence.get("account_consumed_amount"),
        "candidate_buy_amount": system_evidence.get("candidate_buy_amount"),
        "projected_account_consumption": system_evidence.get("projected_account_consumption"),
        "system_total_budget_exceeded": system_evidence.get("system_total_budget_exceeded"),
        "execution_snapshot": snapshot,
    }


def evaluate_buy_execution_policy(
    *,
    signal_context: Any,
    approved_rules: Any,
    runtime_state_snapshot: Any,
    budget_context: Any,
    expected_policy_hash: Any = None,
) -> dict[str, Any]:
    """Plan the next BUY from approved rules and confirmed fill state."""
    signal = deepcopy(_as_dict(signal_context))
    runtime_state = deepcopy(_as_dict(runtime_state_snapshot))
    budget = deepcopy(_as_dict(budget_context))
    base_rule, repeat_rule = _approved_execution_rules(deepcopy(approved_rules))
    issues: list[str] = []
    evidence: dict[str, Any] = {
        "pending_namespace_read": False,
        "canonical_paths": [BASE_PATH, REPEAT_PATH],
        "round_confirmation_source": "confirmed_fills_only",
        "cycle_contract": {
            "partial_sell": "cycle_preserved",
            "full_sell": "cycle_end_requires_zero_holding_and_no_pending_orders",
            "sell_signal_alone_ends_cycle": False,
        },
    }

    if not _signal_is_buy(signal):
        issues.append("NOT_BUY_SIGNAL")
    if not base_rule:
        issues.append("APPROVED_BASE_EXECUTION_RULE_MISSING")

    confirmed_round = _confirmed_round(runtime_state)
    confirmed_cumulative = _confirmed_cumulative_budget(runtime_state)
    if confirmed_round is None:
        issues.append("CONFIRMED_BUY_ROUND_MISSING")
        confirmed_round = 0
    if confirmed_cumulative is None:
        issues.append("CONFIRMED_CUMULATIVE_BUY_BUDGET_MISSING")
        confirmed_cumulative = 0.0
    buy_round = confirmed_round + 1
    buy_phase = "BASE" if buy_round == 1 else "REPEAT"
    if buy_phase == "REPEAT" and not repeat_rule:
        issues.append("APPROVED_REPEAT_EXECUTION_RULE_MISSING")

    hoga_mode = str(base_rule.get("hoga_mode") or "").strip().upper() or None
    order_price_basis = str(base_rule.get("order_price_basis") or "").strip().upper() or None
    hoga_up = _safe_int(base_rule.get("hoga_up"))
    hoga_down = _safe_int(base_rule.get("hoga_down"))
    if hoga_mode not in SUPPORTED_HOGA_MODES:
        issues.append("INVALID_HOGA_MODE")
    if order_price_basis not in SUPPORTED_PRICE_BASIS:
        issues.append("INVALID_ORDER_PRICE_BASIS")
    if hoga_up is None or hoga_down is None or hoga_up < 0 or hoga_down < 0:
        issues.append("INVALID_HOGA_VALUE")

    max_rounds = _max_rounds(budget)
    is_last_round = bool(max_rounds is not None and buy_round == max_rounds)
    if max_rounds is not None and buy_round > max_rounds:
        issues.append("BUY_ROUND_COUNT_EXCEEDED")

    order_price = None
    price_source = None
    if order_price_basis in SUPPORTED_PRICE_BASIS:
        order_price, price_source = _price_for_basis(order_price_basis, signal, budget)
        if order_price_basis != "MARKET" and order_price is None:
            issues.append("ORDER_PRICE_VALUE_MISSING")
    current_price, current_price_source = _current_price(signal, budget)

    if buy_phase == "BASE":
        round_budget, quantity, budget_reference, budget_evidence, budget_issues = _base_budget(
            budget, current_price
        )
    else:
        round_budget, quantity, budget_reference, budget_evidence, budget_issues = _repeat_budget(
            repeat_rule, budget, current_price
        )
    issues.extend(budget_issues)

    total_budget = _positive_float(budget.get("total_budget"))
    remaining_budget = _positive_float(budget.get("remaining_budget"))
    if remaining_budget is None and total_budget is not None:
        remaining_budget = total_budget - confirmed_cumulative
    budget_limit_supplied = "remaining_budget" in budget or total_budget is not None
    if budget_limit_supplied and (remaining_budget is None or remaining_budget <= 0):
        issues.append("REMAINING_BUDGET_NOT_POSITIVE")
    remaining_after = None
    if remaining_budget is not None and round_budget is not None:
        remaining_after = remaining_budget - round_budget
        if round_budget > remaining_budget:
            issues.append("ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET")
    if total_budget is not None and round_budget is not None:
        if confirmed_cumulative + round_budget > total_budget:
            issues.append("TOTAL_BUDGET_EXCEEDED")

    system_admission: dict[str, Any] = {}
    if budget.get("system_total_budget_gate_required") is True:
        system_admission = project_system_total_budget_buy_admission(
            total_budget=budget.get("system_total_budget"),
            account_consumed_amount=budget.get("account_consumed_amount"),
            candidate_buy_amount=round_budget,
        )
        if system_admission.get("available") is not True:
            issues.append("SYSTEM_TOTAL_BUDGET_EVIDENCE_UNAVAILABLE")
        elif not str(budget.get("account_no") or "").strip():
            issues.append("ACCOUNT_IDENTITY_UNAVAILABLE")
        elif system_admission.get("admitted") is not True:
            issues.append("SYSTEM_TOTAL_BUDGET_EXCEEDED")

    evidence.update({
        "price_source": price_source,
        "current_price_source": current_price_source,
        "confirmed_current_buy_round": confirmed_round,
        "confirmed_cumulative_buy_budget": confirmed_cumulative,
        "planned_next_buy_round": buy_round,
        "max_rounds": max_rounds,
        "total_budget": total_budget,
        "remaining_budget_before_candidate": remaining_budget,
        "budget_calculation": budget_evidence,
        "account_no": budget.get("account_no"),
        **system_admission,
    })

    result_args = dict(
        issues=issues,
        approved_base=base_rule,
        approved_repeat=repeat_rule,
        runtime_state=runtime_state,
        buy_phase=buy_phase,
        buy_round=buy_round,
        order_price_basis=order_price_basis,
        order_price=order_price,
        hoga_mode=hoga_mode,
        hoga_up=hoga_up,
        hoga_down=hoga_down,
        round_budget=round_budget,
        quantity=quantity,
        budget_reference=budget_reference,
        is_last_round=is_last_round,
        remaining_budget_after_candidate=remaining_after,
        evidence=evidence,
    )
    provisional = _result(status=STATUS_BLOCKED if issues else STATUS_READY, **result_args)
    expected_hash_text = str(expected_policy_hash or "").strip()
    if expected_hash_text and expected_hash_text != provisional["execution_snapshot"]["policy_hash"]:
        result_args["issues"] = list(issues) + ["POLICY_HASH_MISMATCH"]
    return _result(
        status=STATUS_BLOCKED if result_args["issues"] else STATUS_READY,
        **result_args,
    )
