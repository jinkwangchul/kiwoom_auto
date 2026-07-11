# -*- coding: utf-8 -*-
"""Pure BUY execution policy evaluator.

This module reads approved ``buy.execution.base`` and ``buy.execution.repeat``
rules and returns a BUY order candidate draft. It never writes runtime files,
queues orders, calls a broker, or reads pending approval namespaces.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


POLICY_TYPE = "BUY_EXECUTION_POLICY"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"

BASE_PATH = "buy.execution.base"
REPEAT_PATH = "buy.execution.repeat"
SUPPORTED_HOGA_MODES = {"SINGLE", "MULTI"}
SUPPORTED_PRICE_BASIS = {"ORDER_PRICE", "CURRENT_PRICE", "MARKET"}
SUPPORTED_DETAIL_MODES = {"ROUND", "BUDGET", "ACTIVE_BUY"}
SUPPORTED_ROUND_OPERATORS = {"ADD", "MULTIPLY"}


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
    if number is None:
        return None
    return int(number)


def _positive_float(value: Any) -> float | None:
    number = _safe_float(value)
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


def _price_for_basis(order_price_basis: str, signal_context: dict[str, Any], budget_context: dict[str, Any]) -> tuple[float | None, str]:
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


def _current_round(runtime_state: dict[str, Any]) -> int:
    for key in ("current_buy_round", "buy_round", "last_buy_round", "completed_buy_rounds"):
        value = _safe_int(runtime_state.get(key))
        if value is not None and value >= 0:
            return value
    return 0


def _used_budget(runtime_state: dict[str, Any]) -> float:
    for key in ("used_budget", "accumulated_budget", "total_buy_amount", "consumed_budget"):
        value = _safe_float(runtime_state.get(key))
        if value is not None and value >= 0:
            return value
    return 0.0


def _max_rounds(base_rule: dict[str, Any], budget_context: dict[str, Any]) -> int | None:
    for value in (
        budget_context.get("max_buy_rounds"),
        budget_context.get("round_count"),
        base_rule.get("point_count"),
        base_rule.get("ratio_count"),
    ):
        count = _safe_int(value)
        if count is not None and count > 0:
            return count
    return None


def _round_budget(
    repeat_rule: dict[str, Any],
    budget_context: dict[str, Any],
    next_buy_round: int,
) -> tuple[float | None, dict[str, Any], list[str]]:
    evidence: dict[str, Any] = {
        "detail_mode": repeat_rule.get("detail_mode"),
        "round_operator": repeat_rule.get("round_operator"),
    }
    issues: list[str] = []
    detail_mode = str(repeat_rule.get("detail_mode") or "ROUND").strip().upper()
    if detail_mode not in SUPPORTED_DETAIL_MODES:
        return None, evidence, ["INVALID_REPEAT_DETAIL_MODE"]

    if detail_mode == "BUDGET":
        total_budget = _positive_float(budget_context.get("total_budget"))
        ratio = _positive_float(repeat_rule.get("budget_ratio"))
        evidence.update({"total_budget": total_budget, "budget_ratio": ratio})
        if total_budget is None or ratio is None:
            return None, evidence, ["MISSING_BUDGET_RATIO_CONTEXT"]
        return total_budget * ratio / 100.0, evidence, issues

    base_budget = _positive_float(repeat_rule.get("round_budget_value"))
    if base_budget is None:
        base_budget = _positive_float(budget_context.get("base_round_budget"))
    evidence["base_round_budget"] = base_budget
    if base_budget is None:
        return None, evidence, ["ROUND_BUDGET_NOT_POSITIVE"]

    if detail_mode == "ACTIVE_BUY":
        evidence["active_buy"] = {
            "direction": repeat_rule.get("active_direction"),
            "ratio": repeat_rule.get("active_ratio"),
            "compare": repeat_rule.get("active_compare"),
            "price_adjusted": False,
        }
        return base_budget, evidence, issues

    operator = str(repeat_rule.get("round_operator") or "ADD").strip().upper()
    step = _safe_float(repeat_rule.get("budget_ratio"))
    evidence["budget_ratio"] = step
    if operator not in SUPPORTED_ROUND_OPERATORS:
        return None, evidence, ["INVALID_ROUND_OPERATOR"]
    if step is None:
        step = 0.0 if operator == "ADD" else 1.0

    if operator == "ADD":
        return base_budget + max(0, next_buy_round - 1) * step, evidence, issues
    return base_budget * (step ** max(0, next_buy_round - 1)), evidence, issues


def _result(
    *,
    status: str,
    issues: list[str],
    signal_context: dict[str, Any],
    approved_base: dict[str, Any],
    approved_repeat: dict[str, Any],
    runtime_state: dict[str, Any],
    budget_context: dict[str, Any],
    next_buy_round: int | None = None,
    order_price_basis: str | None = None,
    order_price: float | None = None,
    hoga_mode: str | None = None,
    hoga_up: int | None = None,
    hoga_down: int | None = None,
    round_budget: float | None = None,
    is_last_round: bool | None = None,
    remaining_budget_after_candidate: float | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved_payload = {
        "base": deepcopy(approved_base),
        "repeat": deepcopy(approved_repeat),
    }
    runtime_payload = deepcopy(runtime_state)
    calculation_payload = {
        "status": status,
        "issues": list(issues),
        "next_buy_round": next_buy_round,
        "order_price_basis": order_price_basis,
        "order_price": order_price,
        "hoga_mode": hoga_mode,
        "hoga_up": hoga_up,
        "hoga_down": hoga_down,
        "round_budget": round_budget,
        "is_last_round": is_last_round,
        "remaining_budget_after_candidate": remaining_budget_after_candidate,
    }
    approved_rule_hash = _stable_hash(approved_payload)
    runtime_state_hash = _stable_hash(runtime_payload)
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
        "next_buy_round": next_buy_round,
        "order_price_basis": order_price_basis,
        "order_price": order_price,
        "hoga_mode": hoga_mode,
        "hoga_up": hoga_up,
        "hoga_down": hoga_down,
        "round_budget": round_budget,
        "is_last_round": is_last_round,
        "remaining_budget_after_candidate": remaining_budget_after_candidate,
        "evidence": deepcopy(evidence or {}),
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
    """Evaluate approved BUY execution policy without mutating inputs."""
    signal = deepcopy(_as_dict(signal_context))
    runtime_state = deepcopy(_as_dict(runtime_state_snapshot))
    budget = deepcopy(_as_dict(budget_context))
    base_rule, repeat_rule = _approved_execution_rules(deepcopy(approved_rules))

    issues: list[str] = []
    evidence: dict[str, Any] = {
        "pending_namespace_read": False,
        "canonical_paths": [BASE_PATH, REPEAT_PATH],
    }

    if not _signal_is_buy(signal):
        issues.append("NOT_BUY_SIGNAL")
    if not base_rule or not repeat_rule:
        issues.append("APPROVED_EXECUTION_RULE_MISSING")

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

    next_buy_round = _current_round(runtime_state) + 1
    max_rounds = _max_rounds(base_rule, budget)
    is_last_round = bool(max_rounds is not None and next_buy_round == max_rounds)
    if max_rounds is not None and next_buy_round > max_rounds:
        issues.append("BUY_ROUND_COUNT_EXCEEDED")

    order_price = None
    price_source = None
    if order_price_basis in SUPPORTED_PRICE_BASIS:
        order_price, price_source = _price_for_basis(order_price_basis, signal, budget)
        if order_price_basis != "MARKET" and order_price is None:
            issues.append("ORDER_PRICE_VALUE_MISSING")

    round_budget, round_evidence, budget_issues = _round_budget(repeat_rule, budget, next_buy_round)
    issues.extend(budget_issues)
    if round_budget is not None and round_budget <= 0:
        issues.append("ROUND_BUDGET_NOT_POSITIVE")

    total_budget = _positive_float(budget.get("total_budget"))
    remaining_budget_raw = _safe_float(budget.get("remaining_budget"))
    remaining_budget = remaining_budget_raw if remaining_budget_raw is not None and remaining_budget_raw > 0 else None
    used_budget = _used_budget(runtime_state)
    if total_budget is None:
        total_budget = remaining_budget + used_budget if remaining_budget is not None else None
    if remaining_budget_raw is None and remaining_budget is None and total_budget is not None:
        remaining_budget = total_budget - used_budget
    if remaining_budget is None or remaining_budget <= 0:
        issues.append("REMAINING_BUDGET_NOT_POSITIVE")
    remaining_after = None
    if remaining_budget is not None and round_budget is not None:
        remaining_after = remaining_budget - round_budget
        if round_budget > remaining_budget:
            issues.append("ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET")
    if total_budget is not None and round_budget is not None and used_budget + round_budget > total_budget:
        issues.append("TOTAL_BUDGET_EXCEEDED")

    evidence.update({
        "price_source": price_source,
        "max_rounds": max_rounds,
        "used_budget": used_budget,
        "total_budget": total_budget,
        "remaining_budget_before_candidate": remaining_budget,
        "round_budget_calculation": round_evidence,
    })

    provisional = _result(
        status=STATUS_BLOCKED if issues else STATUS_READY,
        issues=issues,
        signal_context=signal,
        approved_base=base_rule,
        approved_repeat=repeat_rule,
        runtime_state=runtime_state,
        budget_context=budget,
        next_buy_round=next_buy_round,
        order_price_basis=order_price_basis,
        order_price=order_price,
        hoga_mode=hoga_mode,
        hoga_up=hoga_up,
        hoga_down=hoga_down,
        round_budget=round_budget,
        is_last_round=is_last_round,
        remaining_budget_after_candidate=remaining_after,
        evidence=evidence,
    )
    expected_hash_text = str(expected_policy_hash or "").strip()
    if expected_hash_text and expected_hash_text != provisional["execution_snapshot"]["policy_hash"]:
        issues = list(issues) + ["POLICY_HASH_MISMATCH"]

    return _result(
        status=STATUS_BLOCKED if issues else STATUS_READY,
        issues=issues,
        signal_context=signal,
        approved_base=base_rule,
        approved_repeat=repeat_rule,
        runtime_state=runtime_state,
        budget_context=budget,
        next_buy_round=next_buy_round,
        order_price_basis=order_price_basis,
        order_price=order_price,
        hoga_mode=hoga_mode,
        hoga_up=hoga_up,
        hoga_down=hoga_down,
        round_budget=round_budget,
        is_last_round=is_last_round,
        remaining_budget_after_candidate=remaining_after,
        evidence=evidence,
    )
