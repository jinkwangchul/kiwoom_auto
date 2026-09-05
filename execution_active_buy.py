# -*- coding: utf-8 -*-
"""Repeat ACTIVE_BUY reconciliation over existing Production authorities.

The inspector is read-only.  Its commit helper terminates only undispatched
queue records through the canonical queue mutator; replans re-enter the
existing signal consumer/approval pipeline.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any
from routine_main_facts import records_from_routine_main_facts

from buy_execution_policy import calculate_active_buy_requirement
from execution_price_reset import build_buy_generation_intents
from execution_provenance_contract import plan_generation, stable_hash
from execution_queue_writer import mutate_order_queue


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"
SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"

_PRE_DISPATCH = {"APPROVED", "EXECUTABLE", "ORDER_QUEUED"}
_IN_FLIGHT_UNCERTAIN = {
    "DISPATCH_CLAIMED", "SEND_ATTEMPTED", "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED", "SEND_UNCERTAIN",
}
_OPEN = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_TERMINAL = {
    "FILLED", "CANCELLED", "CANCELED", "PARTIAL_CANCELLED",
    "BROKER_REJECTED", "SEND_CALL_REJECTED", "REJECTED", "BLOCKED", "INVALID",
}
_CANCEL_ACTIVE = _PRE_DISPATCH | _IN_FLIGHT_UNCERTAIN | _OPEN


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    number = int(numeric)
    return number if number > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    number = int(numeric)
    return number if number >= 0 else None


def _intent(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("execution_intent")
    if isinstance(value, dict):
        return value
    return record


def _preview(record: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(record.get("execution_request")).get("request_preview"))


def _action(record: dict[str, Any]) -> str:
    return _text(record.get("order_action") or _preview(record).get("order_action") or "NEW").upper()


def _read(path: str | Path, field: str, *, optional: bool = False) -> tuple[list[dict[str, Any]], str]:
    target = Path(path)
    if optional and not target.exists():
        return [], ""
    try:
        root = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"{target.name}:{exc}"
    values = root.get(field) if isinstance(root, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        return [], f"{target.name}:{field} invalid"
    return values, ""


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return records[-1] if records else {}


def _active_policy(intent: dict[str, Any]) -> dict[str, Any]:
    policy = _as_dict(intent.get("active_buy_policy"))
    return policy if policy.get("policy") == "REPEAT_ACTIVE_BUY" else {}


def _cancel_evidence(record: dict[str, Any]) -> dict[str, Any]:
    for source in (record, _preview(record), _as_dict(record.get("execution_request"))):
        value = source.get("cancel_evidence")
        if isinstance(value, dict):
            return value
    return {}


def _snapshot(
    *, process_id: str, generation: int, calculation: dict[str, Any],
    open_remaining: int, planned_remaining: int,
) -> dict[str, Any]:
    value = {
        "policy": "REPEAT_ACTIVE_BUY",
        "execution_process_id": process_id,
        "source_plan_generation": generation,
        "calculation": deepcopy(calculation),
        "broker_open_remaining_quantity": open_remaining,
        "pre_dispatch_remaining_quantity": planned_remaining,
    }
    value["snapshot_hash"] = stable_hash(value)
    return value


def _generation_execution_snapshot(
    *, template: dict[str, Any], calculation: dict[str, Any],
    generation: int, source_snapshot_hash: str,
) -> dict[str, str]:
    prior = _as_dict(template.get("execution_snapshot"))
    approved_rule_hash = _text(prior.get("approved_rule_hash"))
    if not approved_rule_hash:
        raise ValueError("ACTIVE_BUY_APPROVED_RULE_HASH_MISSING")
    runtime_state_hash = stable_hash({
        "position_quantity": calculation.get("quantity"),
        "confirmed_average_buy_price": calculation.get("average_price"),
    })
    calculation_hash = stable_hash({
        "policy": "REPEAT_ACTIVE_BUY",
        "plan_generation": generation,
        "source_snapshot_hash": source_snapshot_hash,
        "calculation": calculation,
    })
    policy_hash = stable_hash({
        "policy_type": "BUY_EXECUTION_POLICY",
        "approved_rule_hash": approved_rule_hash,
        "runtime_state_hash": runtime_state_hash,
        "calculation_hash": calculation_hash,
    })
    return {
        "approved_rule_hash": approved_rule_hash,
        "runtime_state_hash": runtime_state_hash,
        "calculation_hash": calculation_hash,
        "policy_hash": policy_hash,
    }


def build_active_buy_generation_intents(
    *, template: dict[str, Any], source_signal_id: str, process_id: str,
    option_snapshot_hash: str, generation: int, buy_round: int,
    calculation: dict[str, Any], source_snapshot_hash: str,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    quantity = _positive_int(calculation.get("required_quantity"))
    price = calculation.get("actionable_price")
    round_number = _positive_int(buy_round)
    next_generation = _nonnegative_int(generation)
    if (
        quantity is None
        or not isinstance(price, (int, float))
        or isinstance(price, bool)
        or not math.isfinite(float(price))
        or price <= 0
        or round_number is None
        or next_generation is None
        or not _text(source_signal_id)
        or not _text(process_id)
        or not _text(option_snapshot_hash)
        or not _text(source_snapshot_hash)
    ):
        raise ValueError("ACTIVE_BUY_REPLAN_CALCULATION_INVALID")
    execution_snapshot = _generation_execution_snapshot(
        template=template,
        calculation=calculation,
        generation=next_generation,
        source_snapshot_hash=source_snapshot_hash,
    )
    intents = build_buy_generation_intents(
        template=template,
        source_signal_id=source_signal_id,
        process_id=process_id,
        option_snapshot_hash=option_snapshot_hash,
        generation=next_generation,
        buy_round=round_number,
        remaining_budget=quantity * float(price),
        current_price=float(price),
        source_snapshot_hash=source_snapshot_hash,
        generated_at=generated_at,
    )
    for intent in intents:
        intent.pop("buy_price_reset_source_snapshot_hash", None)
        child = _as_dict(intent.get("child_plan"))
        child.pop("buy_price_reset_source_snapshot_hash", None)
        child["active_buy_source_snapshot_hash"] = source_snapshot_hash
        intent["child_plan"] = child
        intent["active_buy_source_snapshot_hash"] = source_snapshot_hash
        intent["active_buy_calculation"] = deepcopy(calculation)
        intent["active_buy_required_quantity"] = quantity
        intent["budget_reference"] = "ACTIVE_BUY_REQUIRED_QUANTITY"
        intent["execution_snapshot"] = deepcopy(execution_snapshot)
    return intents


def inspect_active_buy_lifecycle(
    *,
    selected_account_no: str,
    allowed_stock_codes: Any = None,
    actionable_prices_by_code: Any = None,
    blocked_execution_process_ids: Any = None,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
    now: datetime | None = None,
    main_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project supersede/cancel/replan actions without mutating Production data."""
    result: dict[str, Any] = {
        "ok": True, "supersede_proposals": [], "cancel_proposals": [],
        "replan_proposals": [], "waiting": [], "reviews": [], "errors": [],
        "blocked_execution_process_ids": [],
    }
    loaded: dict[str, list[dict[str, Any]]] = {}
    for key, path, field, optional in (
        ("orders", order_queue_path, "orders", False),
        ("fills", fills_path, "fills", False),
        ("positions", positions_path, "positions", False),
        ("holdings", holdings_path, "holdings", False),
        ("signals", signals_path, "signals", False),
    ):
        loaded[key], issue = (
            records_from_routine_main_facts(main_facts, field, optional=optional)
            if main_facts is not None
            else _read(path, field, optional=optional)
        )
        if issue:
            result["errors"].append(issue)
    if result["errors"]:
        result["ok"] = False
        return result

    account = _text(selected_account_no)
    allowed = None if allowed_stock_codes is None else {
        _text(value) for value in allowed_stock_codes if _text(value)
    }
    blocked = {_text(value) for value in blocked_execution_process_ids or [] if _text(value)}
    prices = _as_dict(actionable_prices_by_code)
    current_at = now or datetime.now()

    for signal in loaded["signals"]:
        intents = [deepcopy(value) for value in signal.get("execution_intents", []) if isinstance(value, dict)]
        if not intents and isinstance(signal.get("execution_intent"), dict):
            intents = [deepcopy(signal["execution_intent"])]
        active_intents = [item for item in intents if _active_policy(item)]
        if not active_intents:
            continue
        process_ids = {_text(item.get("execution_process_id")) for item in active_intents}
        process_ids.discard("")
        if len(process_ids) != 1:
            result["reviews"].append({"source_signal_id": signal.get("id"), "review_reasons": ["ACTIVE_BUY_PROCESS_IDENTITY_INVALID"]})
            continue
        process_id = next(iter(process_ids))
        if process_id in blocked:
            continue
        source_signal_id = _text(signal.get("id"))
        code = _text(signal.get("code") or active_intents[0].get("code"))
        if allowed is not None and code not in allowed:
            continue
        source_ids = {_text(item.get("source_signal_id")) for item in active_intents}
        source_ids.discard("")
        policy_hashes = {stable_hash(_active_policy(item)) for item in active_intents}
        round_values = {_positive_int(item.get("buy_round")) for item in active_intents}
        round_values.discard(None)
        intent_codes = {_text(item.get("code")) for item in active_intents if _text(item.get("code"))}
        identity_reasons: list[str] = []
        if not source_signal_id or source_ids != {source_signal_id}:
            identity_reasons.append("ACTIVE_BUY_SOURCE_SIGNAL_IDENTITY_INVALID")
        if len(policy_hashes) != 1:
            identity_reasons.append("ACTIVE_BUY_POLICY_IDENTITY_INVALID")
        if len(round_values) != 1:
            identity_reasons.append("ACTIVE_BUY_ROUND_IDENTITY_INVALID")
        if intent_codes and intent_codes != {code}:
            identity_reasons.append("ACTIVE_BUY_STOCK_IDENTITY_INVALID")
        if identity_reasons:
            result["reviews"].append({
                "execution_process_id": process_id,
                "source_signal_id": source_signal_id,
                "code": code,
                "review_reasons": identity_reasons,
            })
            blocked.add(process_id)
            continue
        generation = max(plan_generation(item.get("plan_generation")) for item in active_intents)
        generation_intents = [item for item in active_intents if plan_generation(item.get("plan_generation")) == generation]
        template = generation_intents[0]
        policy = _active_policy(template)
        process_orders = [
            item for item in loaded["orders"]
            if _action(item) == "NEW"
            and _text(item.get("execution_process_id") or _intent(item).get("execution_process_id")) == process_id
        ]
        current_orders = [
            item for item in process_orders
            if plan_generation(item.get("plan_generation", _intent(item).get("plan_generation"))) == generation
        ]
        if any(
            _text(item.get("code") or _intent(item).get("code")) not in {"", code}
            or _text(item.get("account_no")) not in {"", account}
            for item in current_orders
        ):
            result["reviews"].append({
                "execution_process_id": process_id,
                "source_signal_id": source_signal_id,
                "code": code,
                "review_reasons": ["ACTIVE_BUY_ORDER_IDENTITY_INVALID"],
            })
            blocked.add(process_id)
            continue
        by_execution: dict[str, list[dict[str, Any]]] = {}
        for item in current_orders:
            execution_id = _text(item.get("execution_id") or _intent(item).get("execution_id"))
            if execution_id:
                by_execution.setdefault(execution_id, []).append(item)
        latest_orders = [_latest(values) for values in by_execution.values()]
        position_matches = [
            item for item in loaded["positions"]
            if _text(item.get("code")) == code
            and _text(item.get("account_no")) in {"", account}
        ]
        if len(position_matches) != 1:
            result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": ["ACTIVE_BUY_POSITION_MATCH_INVALID"]})
            blocked.add(process_id)
            continue
        position = position_matches[0]
        quantity = _positive_int(position.get("quantity"))
        average = position.get("average_price")
        holding_matches = [
            item for item in loaded["holdings"]
            if _text(item.get("code") or item.get("stock_code")) == code
            and _text(item.get("account_no")) in {"", account}
        ]
        holding_quantity = (
            _nonnegative_int(holding_matches[0].get("holding_quantity"))
            if len(holding_matches) == 1
            else None
        )
        if (
            len(holding_matches) != 1
            or holding_quantity is None
            or quantity != holding_quantity
            or holding_matches[0].get("manual_reconciliation_required") is True
            or _text(holding_matches[0].get("reconciliation_status")).upper()
            not in {"", "CONSISTENT"}
        ):
            result["waiting"].append({
                "execution_process_id": process_id,
                "code": code,
                "reason": "ACTIVE_BUY_POSITION_RECONCILIATION_PENDING",
            })
            blocked.add(process_id)
            continue
        price = prices.get(code)
        if price in (None, ""):
            result["waiting"].append({
                "execution_process_id": process_id,
                "code": code,
                "reason": "ACTIVE_BUY_PRICE_UNAVAILABLE",
            })
            blocked.add(process_id)
            continue
        calculation = calculate_active_buy_requirement(
            quantity=quantity,
            average_price=average,
            reference_price=policy.get("reference_price"),
            actionable_price=price,
            direction=policy.get("direction"),
            ratio_percent=policy.get("ratio_percent"),
            comparator=policy.get("comparator"),
        )
        if calculation.get("status") == "INVALID":
            result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": [calculation.get("reason")]})
            blocked.add(process_id)
            continue

        unsafe = [item for item in latest_orders if _text(item.get("status")).upper() in _IN_FLIGHT_UNCERTAIN or item.get("manual_reconciliation_required") is True]
        pre_dispatch = [item for item in latest_orders if _text(item.get("status")).upper() in _PRE_DISPATCH]
        open_orders = [item for item in latest_orders if _text(item.get("status")).upper() in _OPEN]
        unresolved = [item for item in latest_orders if _text(item.get("status")).upper() not in _PRE_DISPATCH | _IN_FLIGHT_UNCERTAIN | _OPEN | _TERMINAL]
        if unsafe or unresolved:
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "ACTIVE_BUY_RECONCILIATION_PENDING"})
            blocked.add(process_id)
            continue

        process_cancels = [
            item for item in loaded["orders"]
            if _action(item) == "CANCEL"
            and _text(item.get("execution_process_id") or _as_dict(item.get("execution_request")).get("execution_process_id")) == process_id
            and _text(_cancel_evidence(item).get("trigger")).upper() == "ACTIVE_BUY_RECONCILIATION"
        ]
        active_cancels = [item for item in process_cancels if _text(item.get("status")).upper() in _CANCEL_ACTIVE and item.get("original_order_effect_confirmed") is not True]
        if active_cancels:
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "ACTIVE_BUY_CANCEL_EFFECT_PENDING"})
            blocked.add(process_id)
            continue

        open_remaining = sum(_positive_int(item.get("remaining_quantity")) or 0 for item in open_orders)
        planned_remaining = sum(_positive_int(item.get("quantity") or _intent(item).get("quantity")) or 0 for item in pre_dispatch)
        snapshot = _snapshot(
            process_id=process_id, generation=generation, calculation=calculation,
            open_remaining=open_remaining, planned_remaining=planned_remaining,
        )
        if open_orders:
            required = _nonnegative_int(calculation.get("required_quantity")) if calculation.get("status") in {"READY", "NO_BUY"} else None
            outstanding = open_remaining + planned_remaining
            if pre_dispatch and (
                calculation.get("status") != "READY"
                or required != outstanding
            ):
                result["supersede_proposals"].append({
                    "execution_process_id": process_id,
                    "source_signal_id": source_signal_id,
                    "code": code,
                    "plan_generation": generation,
                    "order_ids": [_text(item.get("id")) for item in pre_dispatch],
                    "trigger_snapshot": deepcopy(snapshot),
                })
            if required is not None and open_remaining > required:
                for order in open_orders:
                    broker_no = _text(order.get("broker_order_no"))
                    remaining = _positive_int(order.get("remaining_quantity"))
                    if not broker_no or remaining is None:
                        result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": ["ACTIVE_BUY_OPEN_ORDER_IDENTITY_INVALID"]})
                        continue
                    result["cancel_proposals"].append({
                        "order_queued_id": _text(order.get("id")), "account_no": account,
                        "code": code, "side": "BUY", "broker_order_no": broker_no,
                        "remaining_quantity": remaining, "execution_process_id": process_id,
                        "source_signal_id": source_signal_id, "source_plan_generation": generation,
                        "trigger_snapshot": deepcopy(snapshot),
                    })
                result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "ACTIVE_BUY_CANCEL_REQUIRED"})
            else:
                result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "ACTIVE_BUY_OPEN_OBLIGATION_PENDING"})
            blocked.add(process_id)
            continue

        if pre_dispatch:
            required = _positive_int(calculation.get("required_quantity")) if calculation.get("status") == "READY" else None
            unchanged = required == planned_remaining
            if unchanged:
                continue
            proposal = {
                "execution_process_id": process_id, "source_signal_id": source_signal_id,
                "code": code, "plan_generation": generation,
                "order_ids": [_text(item.get("id")) for item in pre_dispatch],
                "trigger_snapshot": deepcopy(snapshot),
            }
            result["supersede_proposals"].append(proposal)
            blocked.add(process_id)
            if calculation.get("status") == "READY":
                option_hashes = {_text(item.get("option_snapshot_hash") or _intent(item).get("option_snapshot_hash")) for item in pre_dispatch}
                option_hashes.discard("")
                if len(option_hashes) != 1:
                    result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": ["ACTIVE_BUY_OPTION_SNAPSHOT_HASH_INVALID"]})
                    continue
                try:
                    next_intents = build_active_buy_generation_intents(
                        template=template, source_signal_id=source_signal_id,
                        process_id=process_id, option_snapshot_hash=next(iter(option_hashes)),
                        generation=generation + 1, buy_round=int(template.get("buy_round") or 0),
                        calculation=calculation, source_snapshot_hash=snapshot["snapshot_hash"],
                        generated_at=current_at,
                    )
                except ValueError as exc:
                    result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": [str(exc)]})
                    continue
                signal_proposal = deepcopy(signal)
                signal_proposal.update({"status": "PENDING", "execution_intent": next_intents[0], "execution_intents": next_intents})
                result["replan_proposals"].append({
                    **proposal, "plan_generation": generation + 1,
                    "signal": signal_proposal, "execution_intents": next_intents,
                })
            continue

        # All prior obligations are terminal. Confirmed fills/Position now own
        # Q/A; only a still-required quantity creates the next generation.
        if latest_orders and calculation.get("status") == "READY":
            option_hashes = {_text(item.get("option_snapshot_hash") or _intent(item).get("option_snapshot_hash")) for item in latest_orders}
            option_hashes.discard("")
            if len(option_hashes) != 1:
                result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": ["ACTIVE_BUY_OPTION_SNAPSHOT_HASH_INVALID"]})
                blocked.add(process_id)
                continue
            try:
                next_intents = build_active_buy_generation_intents(
                    template=template, source_signal_id=source_signal_id,
                    process_id=process_id, option_snapshot_hash=next(iter(option_hashes)),
                    generation=generation + 1, buy_round=template.get("buy_round"),
                    calculation=calculation, source_snapshot_hash=snapshot["snapshot_hash"],
                    generated_at=current_at,
                )
            except ValueError as exc:
                result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": [str(exc)]})
                blocked.add(process_id)
                continue
            signal_proposal = deepcopy(signal)
            signal_proposal.update({"status": "PENDING", "execution_intent": next_intents[0], "execution_intents": next_intents})
            result["replan_proposals"].append({
                "execution_process_id": process_id, "source_signal_id": source_signal_id,
                "code": code, "plan_generation": generation + 1,
                "signal": signal_proposal, "execution_intents": next_intents,
                "trigger_snapshot": deepcopy(snapshot),
            })
            blocked.add(process_id)

    result["blocked_execution_process_ids"] = sorted(blocked)
    return result


def supersede_active_buy_pre_dispatch_generation(
    proposal: Any,
    *,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
) -> dict[str, Any]:
    """Terminalize exactly one undispatched generation without changing approval data."""
    item = deepcopy(proposal) if isinstance(proposal, dict) else {}
    process_id = _text(item.get("execution_process_id"))
    generation = plan_generation(item.get("plan_generation"))
    expected_ids = {_text(value) for value in item.get("order_ids", []) if _text(value)}
    snapshot = _as_dict(item.get("trigger_snapshot"))
    snapshot_hash = _text(snapshot.get("snapshot_hash"))
    if not process_id or not expected_ids or not snapshot_hash:
        return {"committed": False, "blocked_reasons": ["ACTIVE_BUY_SUPERSEDE_PROPOSAL_INVALID"]}

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        matches = [order for order in data.get("orders", []) if _text(order.get("id")) in expected_ids]
        if len(matches) != len(expected_ids):
            return {"blocked": {"committed": False, "write_stage": "active_buy_supersede", "blocked_reasons": ["ACTIVE_BUY_SUPERSEDE_ORDER_SET_CHANGED"]}}
        for order in matches:
            intent = _intent(order)
            status = _text(order.get("status")).upper()
            if (
                _text(order.get("execution_process_id") or intent.get("execution_process_id")) != process_id
                or plan_generation(order.get("plan_generation", intent.get("plan_generation"))) != generation
                or status not in _PRE_DISPATCH
            ):
                return {"blocked": {"committed": False, "write_stage": "active_buy_supersede", "blocked_reasons": ["ACTIVE_BUY_SUPERSEDE_PRECONDITION_CHANGED"]}}
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        for order in matches:
            order["status"] = "BLOCKED"
            order["blocked_reason"] = "ACTIVE_BUY_PLAN_SUPERSEDED"
            order["active_buy_superseded_at"] = timestamp
            order["active_buy_source_snapshot_hash"] = snapshot_hash
        return {"data": data, "result": {"superseded_order_ids": sorted(expected_ids), "source_snapshot_hash": snapshot_hash}}

    def verify(data: dict[str, Any], _mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = [order for order in data.get("orders", []) if _text(order.get("id")) in expected_ids]
        if len(matches) != len(expected_ids) or any(_text(order.get("status")).upper() != "BLOCKED" for order in matches):
            return {"write_stage": "active_buy_supersede_verify", "blocked_reasons": ["ACTIVE_BUY_SUPERSEDE_VERIFY_FAILED"]}
        return None

    return mutate_order_queue(
        order_queue_path, mutation,
        operation_name="active_buy_supersede",
        success_stage="active_buy_generation_superseded",
        next_stage="ACTIVE_BUY_REPLAN_APPROVAL",
        verify=verify,
    )


__all__ = [
    "build_active_buy_generation_intents",
    "calculate_active_buy_requirement",
    "inspect_active_buy_lifecycle",
    "supersede_active_buy_pre_dispatch_generation",
]
