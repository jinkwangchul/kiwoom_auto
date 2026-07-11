# -*- coding: utf-8 -*-
"""BUY order candidate draft preview bridge.

This module converts a READY BUY execution policy result into an in-memory
order candidate draft view model. It does not write queue/runtime files, call
SendOrder, touch GUI state, or connect to order management.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable

from buy_execution_policy import evaluate_buy_execution_policy


SERVICE_TYPE = "BUY_ORDER_CANDIDATE_PREVIEW_SERVICE"
CANDIDATE_VERSION = "BUY_ORDER_CANDIDATE_DRAFT_V1"
POLICY_VERSION = "BUY_EXECUTION_POLICY_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _signal_side(signal: dict[str, Any]) -> str:
    for key in ("side", "signal", "signal_type", "action", "decision"):
        value = str(signal.get(key) or "").strip().upper()
        if value:
            return value
    if signal.get("buy_signal") is True or signal.get("is_buy") is True:
        return "BUY"
    return ""


def _approved_execution_rules(approved_rules: Any) -> dict[str, Any]:
    rules = _as_dict(approved_rules)
    if "base" in rules or "repeat" in rules:
        return {
            "buy": {
                "execution": {
                    "base": deepcopy(_as_dict(rules.get("base"))),
                    "repeat": deepcopy(_as_dict(rules.get("repeat"))),
                }
            }
        }
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


def _symbol(signal: dict[str, Any]) -> str:
    for key in ("symbol", "code", "stock_code", "ticker"):
        value = str(signal.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_signal_id(signal: dict[str, Any]) -> str | None:
    for key in ("signal_id", "source_signal_id", "id"):
        value = str(signal.get(key) or "").strip()
        if value:
            return value
    return None


def _candidate_id_payload(signal: dict[str, Any], policy_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_version": CANDIDATE_VERSION,
        "symbol": _symbol(signal),
        "side": "BUY",
        "source_signal_id": _source_signal_id(signal),
        "next_buy_round": policy_result.get("next_buy_round"),
        "order_price_basis": policy_result.get("order_price_basis"),
        "order_price": None if policy_result.get("order_price_basis") == "MARKET" else policy_result.get("order_price"),
        "round_budget": policy_result.get("round_budget"),
        "execution_snapshot": deepcopy(_as_dict(policy_result.get("execution_snapshot"))),
    }


def _candidate_id(signal: dict[str, Any], policy_result: dict[str, Any]) -> str:
    return "BUY_ORDER_CANDIDATE_" + _stable_hash(_candidate_id_payload(signal, policy_result))[:24].upper()


def _draft(signal: dict[str, Any], policy_result: dict[str, Any]) -> dict[str, Any]:
    order_price_basis = str(policy_result.get("order_price_basis") or "").strip().upper()
    order_type = "MARKET" if order_price_basis == "MARKET" else "LIMIT"
    price = None if order_type == "MARKET" else policy_result.get("order_price")
    return {
        "candidate_version": CANDIDATE_VERSION,
        "candidate_id": _candidate_id(signal, policy_result),
        "symbol": _symbol(signal),
        "side": "BUY",
        "order_type": order_type,
        "price": price,
        "budget": policy_result.get("round_budget"),
        "quantity_policy": "BUDGET_BASED",
        "next_buy_round": policy_result.get("next_buy_round"),
        "is_last_round": policy_result.get("is_last_round"),
        "hoga_mode": policy_result.get("hoga_mode"),
        "hoga_up": policy_result.get("hoga_up"),
        "hoga_down": policy_result.get("hoga_down"),
        "source_signal_id": _source_signal_id(signal),
        "policy_version": POLICY_VERSION,
        "execution_snapshot": deepcopy(_as_dict(policy_result.get("execution_snapshot"))),
    }


def _diagnostics(stage: str, ok: bool, reason: str, policy_result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [{
        "stage": stage,
        "ok": ok,
        "reason": reason,
        "policy_status": _as_dict(policy_result).get("status") if isinstance(policy_result, dict) else None,
    }]


def _result(
    *,
    status: str,
    order_candidate_draft: dict[str, Any] | None,
    execution_policy_result: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    policy_result = _as_dict(execution_policy_result)
    return {
        "service_type": SERVICE_TYPE,
        "status": status,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "order_management_connected": False,
        "send_order_called": False,
        "gui_updated": False,
        "order_candidate_draft": deepcopy(order_candidate_draft),
        "execution_policy_result": deepcopy(policy_result),
        "execution_snapshot": deepcopy(_as_dict(policy_result.get("execution_snapshot"))),
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics),
    }


def build_buy_order_candidate_preview(
    *,
    buy_signal_result: Any,
    approved_rules: Any,
    runtime_state_snapshot: Any,
    budget_context: Any,
    evaluator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a preview-only BUY order candidate draft from approved execution rules."""
    signal = deepcopy(_as_dict(buy_signal_result))
    approved_execution = _approved_execution_rules(deepcopy(approved_rules))
    runtime_state = deepcopy(_as_dict(runtime_state_snapshot))
    budget = deepcopy(_as_dict(budget_context))

    if _signal_side(signal) != "BUY":
        return _result(
            status=STATUS_BLOCKED,
            order_candidate_draft=None,
            execution_policy_result={},
            evidence={"signal_side": _signal_side(signal)},
            diagnostics=_diagnostics("signal", False, "BUY signal is required"),
        )

    policy_evaluator = evaluator or evaluate_buy_execution_policy
    policy_result = policy_evaluator(
        signal_context=signal,
        approved_rules=approved_execution,
        runtime_state_snapshot=runtime_state,
        budget_context=budget,
    )
    if not isinstance(policy_result, dict):
        return _result(
            status=STATUS_INVALID,
            order_candidate_draft=None,
            execution_policy_result={},
            evidence={},
            diagnostics=_diagnostics("evaluator", False, "evaluator result must be dict"),
        )

    policy_status = policy_result.get("status")
    evidence = _as_dict(policy_result.get("evidence"))
    if policy_status == STATUS_READY:
        draft = _draft(signal, policy_result)
        return _result(
            status=STATUS_READY,
            order_candidate_draft=draft,
            execution_policy_result=policy_result,
            evidence=evidence,
            diagnostics=_diagnostics("candidate_draft", True, "candidate draft ready", policy_result),
        )

    if policy_status == STATUS_BLOCKED:
        return _result(
            status=STATUS_BLOCKED,
            order_candidate_draft=None,
            execution_policy_result=policy_result,
            evidence=evidence,
            diagnostics=_diagnostics("execution_policy", False, "execution policy blocked", policy_result),
        )

    return _result(
        status=STATUS_INVALID,
        order_candidate_draft=None,
        execution_policy_result=policy_result,
        evidence=evidence,
        diagnostics=_diagnostics("evaluator", False, "evaluator status is invalid", policy_result),
    )
