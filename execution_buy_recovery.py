# -*- coding: utf-8 -*-
"""BUY residual Recovery inspection over confirmed cancel effects.

The inspector is read-only.  It never creates a new BUY round or budget and
returns same-signal/same-process generation proposals for the existing
Candidate/Approval/Queue pipeline.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
from typing import Any
from routine_main_facts import records_from_routine_main_facts

from execution_price_reset import build_buy_generation_intents
from execution_provenance_contract import plan_generation, stable_hash
from execution_unfilled_cancel_eligibility import cancel_effect_state


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"
SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"

_RECOVERY_TRIGGERS = {"UNFILLED_TIMEOUT", "BUY_PRICE_CHANGE_CANCEL_BATCH"}
_TERMINAL_SIGNALS = {"DONE", "CANCELLED", "EXPIRED", "ERROR", "BLOCKED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 and number.is_integer() else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number >= 0 and number.is_integer() else None


def _read(path: str | Path, field: str) -> tuple[list[dict[str, Any]], str]:
    try:
        root = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{field.upper()}_READ_FAILED:{exc}"
    values = root.get(field) if isinstance(root, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        return [], f"{field.upper()}_SCHEMA_INVALID"
    return values, ""


def _intent(record: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(record.get("execution_intent"))
    return direct or _dict(_dict(record.get("execution_request")).get("execution_intent"))


def _preview(record: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(record.get("execution_request")).get("request_preview"))


def _action(record: dict[str, Any]) -> str:
    return _text(record.get("order_action") or _preview(record).get("order_action") or "NEW").upper()


def _cancel_evidence(record: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(record.get("cancel_evidence"))
    if direct:
        return direct
    child = _dict(record.get("child_plan")) or _dict(_dict(record.get("execution_request")).get("child_plan"))
    return _dict(child.get("cancel_evidence"))


def _original_no(record: dict[str, Any]) -> str:
    return _text(record.get("original_order_no") or _preview(record).get("original_order_no"))


def _broker_no(record: dict[str, Any]) -> str:
    return _text(record.get("broker_order_no"))


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(records, key=lambda item: (_text(item.get("updated_at") or item.get("created_at")), _text(item.get("id"))))


def _recovery_template(template: dict[str, Any], quantity: int, signal: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(template.get("buy_recovery_cycle_policy"))
    if policy.get("scope") != "SIGNAL_SCOPED_BUY_RECOVERY" or policy.get("residual_only") is not True:
        raise ValueError("BUY_RECOVERY_POLICY_INVALID")
    order = _dict(policy.get("order_policy"))
    point = _dict(policy.get("point_policy"))
    unfilled = _dict(policy.get("unfilled_timeout_policy"))
    price_responses = policy.get("buy_price_response_policies")
    if not isinstance(price_responses, list) or any(not isinstance(item, dict) for item in price_responses):
        raise ValueError("BUY_RECOVERY_PRICE_RESPONSE_POLICY_INVALID")
    value = deepcopy(template)
    value["hoga_mode"] = order.get("hoga_mode")
    value["price_basis"] = order.get("order_price_basis")
    value["order_price_basis"] = order.get("order_price_basis")
    # Situation Response owns both the base flow and every Recovery generation.
    # Recovery only redistributes the confirmed residual; it preserves the
    # independently enabled timeout and price-response policies.
    if unfilled.get("enabled") is True:
        configured_value = unfilled.get("configured_value")
        configured_unit = _text(unfilled.get("configured_unit")).upper()
        if (
            unfilled.get("policy") != "CANCEL_PENDING_ORDER"
            or _text(unfilled.get("scope")).upper() not in {"EACH", "BATCH"}
            or isinstance(configured_value, bool)
            or not isinstance(configured_value, (int, float))
            or not isfinite(configured_value)
            or configured_value < 0
            or configured_unit not in {"SECOND", "MINUTE", "BAR"}
        ):
            raise ValueError("BUY_RECOVERY_UNFILLED_POLICY_INVALID")
        if configured_unit == "SECOND":
            unit_ms = 1_000
        elif configured_unit == "MINUTE":
            unit_ms = 60_000
        else:
            timeframe = _positive_int(signal.get("signal_timeframe_minutes"))
            if timeframe is None:
                raise ValueError("BUY_RECOVERY_UNFILLED_TIMEFRAME_UNAVAILABLE")
            unit_ms = timeframe * 60_000
        timeout_ms = configured_value * unit_ms
        if not isfinite(timeout_ms) or not float(timeout_ms).is_integer():
            raise ValueError("BUY_RECOVERY_UNFILLED_POLICY_INVALID")
        value["unfilled_timeout_policy"] = {
            **deepcopy(unfilled),
            "timeout_ms": int(timeout_ms),
            "anchor": "BROKER_ACCEPTED_AT",
        }
        if configured_unit == "BAR":
            value["unfilled_timeout_policy"]["timeframe_minutes"] = unit_ms // 60_000
    else:
        value["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER",
            "enabled": False,
        }
    for response in price_responses:
        if (
            response.get("enabled") is not True
            or _text(response.get("left_source")).upper() not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or _text(response.get("right_source")).upper() not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or _text(response.get("direction")).upper() not in {"UP", "DOWN", "BOTH"}
            or _text(response.get("action")).upper() not in {"RESET", "CANCEL_BATCH"}
        ):
            raise ValueError("BUY_RECOVERY_PRICE_RESPONSE_POLICY_INVALID")
    value["buy_price_response_policies"] = deepcopy(price_responses)
    reset_responses = [item for item in price_responses if _text(item.get("action")).upper() == "RESET"]
    value["buy_price_reset_policy"] = (
        {"policy": "BUY_PRICE_CHANGE_RESET", **deepcopy(reset_responses[0])}
        if len(reset_responses) == 1
        else {"policy": "BUY_PRICE_CHANGE_RESET", "enabled": False}
    )
    point_mode = _text(point.get("mode")).upper()
    if point_mode == "MULTI_TIME":
        count = min(_positive_int(point.get("count")) or 0, quantity)
        unit = _text(point.get("unit")).upper()
        step = float(point.get("value") or 0)
        if count <= 0 or step <= 0:
            raise ValueError("BUY_RECOVERY_TIME_POLICY_INVALID")
        if unit == "SECOND":
            unit_ms = 1000
        elif unit == "MINUTE":
            unit_ms = 60_000
        elif unit == "BAR":
            timeframe = _positive_int(signal.get("signal_timeframe_minutes"))
            if timeframe is None:
                raise ValueError("BUY_RECOVERY_TIMEFRAME_UNAVAILABLE")
            unit_ms = timeframe * 60_000
        else:
            raise ValueError("BUY_RECOVERY_TIME_UNIT_INVALID")
        if _text(point.get("range")).upper() == "WITHIN":
            offsets = [int(round((step * unit_ms * index) / max(count - 1, 1))) for index in range(count)]
        else:
            offsets = [int(step * unit_ms * index) for index in range(count)]
        value["execution_mode"] = "MULTI_TIME"
        value["multi_time_plan"] = {
            "configured_child_count": count,
            "planned_child_count": count,
            "scheduled_offsets_ms": offsets,
            "price_basis": point.get("order_price_basis"),
        }
        value["price_basis"] = point.get("order_price_basis")
    elif point_mode == "MULTI_RATIO":
        count = min(_positive_int(point.get("count")) or 0, quantity)
        if count <= 0:
            raise ValueError("BUY_RECOVERY_RATIO_POLICY_INVALID")
        value["execution_mode"] = "MULTI_RATIO"
        value["multi_ratio_plan"] = {
            "configured_child_count": count,
            "planned_child_count": count,
            "ratio_left": point.get("left_source"),
            "ratio_right": point.get("right_source"),
            "ratio_direction": point.get("direction"),
            "ratio_value": point.get("ratio_percent"),
            "ratio_compare": point.get("comparator"),
        }
    elif order.get("hoga_mode") == "MULTI":
        up = _nonnegative_int(order.get("hoga_up"))
        down = _nonnegative_int(order.get("hoga_down"))
        if up is None or down is None:
            raise ValueError("BUY_RECOVERY_HOGA_POLICY_INVALID")
        offsets = [0]
        for distance in range(1, max(up, down) + 1):
            if distance <= up:
                offsets.append(distance)
            if distance <= down:
                offsets.append(-distance)
        offsets = offsets[:quantity]
        value["execution_mode"] = "MULTI_HOGA" if len(offsets) > 1 else "SINGLE_ORDER"
        value["multi_hoga_plan"] = {
            "hoga_offsets": offsets,
            "configured_child_count": len(offsets),
            "planned_child_count": len(offsets),
        }
    else:
        value["execution_mode"] = "SINGLE_ORDER"
    return value


def inspect_buy_recovery_generations(
    *,
    selected_account_no: str,
    actionable_prices_by_code: dict[str, Any],
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    now: datetime | None = None,
    proposal_limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
    main_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path, field in ((order_queue_path, "orders"), (positions_path, "positions"), (holdings_path, "holdings"), (signals_path, "signals")):
        loaded[field], error = (
            records_from_routine_main_facts(main_facts, field)
            if main_facts is not None
            else _read(path, field)
        )
        if error:
            errors.append(error)
    result = {"ok": not errors, "proposals": [], "completion_proposals": [], "reviews": [], "waiting": [], "errors": errors, "blocked_execution_process_ids": []}
    if errors:
        return result
    account = _text(selected_account_no)
    allowed = {_text(value) for value in allowed_stock_codes or [] if _text(value)} if allowed_stock_codes is not None else None
    externally_blocked = {_text(value) for value in blocked_execution_process_ids or [] if _text(value)}
    prices: dict[str, float] = {}
    for raw_code, raw_value in actionable_prices_by_code.items():
        code = _text(raw_code)
        if not code or raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices[code] = value
    orders = loaded["orders"]
    originals_by_broker: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        if _action(order) in {"NEW", "MODIFY"} and _broker_no(order):
            originals_by_broker.setdefault(_broker_no(order), []).append(order)
    candidates: dict[tuple[str, int, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for cancel in orders:
        if _action(cancel) != "CANCEL":
            continue
        evidence = _cancel_evidence(cancel)
        trigger = _text(evidence.get("trigger")).upper()
        if trigger not in _RECOVERY_TRIGGERS:
            continue
        cancel_side = _text(cancel.get("side") or _preview(cancel).get("side")).upper()
        if cancel_side and cancel_side != "BUY":
            continue
        process_id = _text(cancel.get("execution_process_id") or evidence.get("execution_process_id"))
        generation = plan_generation(evidence.get("source_plan_generation"))
        if process_id and process_id not in externally_blocked:
            scope = _text(evidence.get("scope")).upper()
            batch = trigger == "BUY_PRICE_CHANGE_CANCEL_BATCH" or scope == "BATCH"
            bucket = "BATCH" if batch else "EACH"
            candidates.setdefault((process_id, generation, bucket), []).append((cancel, evidence))
    generated_at = now or datetime.now()
    for (process_id, source_generation, recovery_bucket), cancel_items in sorted(candidates.items()):
        if len(result["proposals"]) >= max(0, int(proposal_limit or 0)):
            break
        claimed_original_nos = {
            _text(original_no)
            for order in orders
            if _text(order.get("execution_process_id") or _intent(order).get("execution_process_id")) == process_id
            for original_no in (_intent(order).get("recovery_source_original_order_nos") or [])
            if _text(original_no)
        }
        cancel_items = [
            (cancel, evidence)
            for cancel, evidence in cancel_items
            if _original_no(cancel) not in claimed_original_nos
        ]
        if not cancel_items:
            continue
        latest_cancels: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for cancel, evidence in cancel_items:
            identity = _original_no(cancel) or _text(cancel.get("id"))
            prior = latest_cancels.get(identity)
            if prior is None or _latest([prior[0], cancel]) is cancel:
                latest_cancels[identity] = (cancel, evidence)
        cancel_items = list(latest_cancels.values())
        states = [cancel_effect_state(cancel) for cancel, _ in cancel_items]
        if "UNCERTAIN" in states or "REJECTED" in states:
            result["reviews"].append({"execution_process_id": process_id, "review_reasons": ["BUY_RECOVERY_CANCEL_EFFECT_UNCERTAIN"]})
            result["blocked_execution_process_ids"].append(process_id)
            continue
        if any(state != "CONFIRMED" for state in states):
            result["waiting"].append({"execution_process_id": process_id, "reason": "BUY_RECOVERY_CANCEL_EFFECT_PENDING"})
            result["blocked_execution_process_ids"].append(process_id)
            continue
        originals: list[dict[str, Any]] = []
        for cancel, _ in cancel_items:
            matches = originals_by_broker.get(_original_no(cancel), [])
            if matches:
                originals.append(_latest(matches))
        if len(originals) != len(cancel_items):
            result["reviews"].append({"execution_process_id": process_id, "review_reasons": ["BUY_RECOVERY_ORIGINAL_ORDER_MISSING"]})
            result["blocked_execution_process_ids"].append(process_id)
            continue
        if recovery_bucket == "BATCH":
            required_broker_nos = {
                broker_no
                for broker_no, rows in originals_by_broker.items()
                for original in [_latest(rows)]
                if _text(original.get("execution_process_id") or _intent(original).get("execution_process_id")) == process_id
                and plan_generation(_intent(original).get("plan_generation", original.get("plan_generation"))) == source_generation
                and (_nonnegative_int(original.get("remaining_quantity")) or 0) > 0
            }
            confirmed_broker_nos = {
                _original_no(cancel)
                for cancel, _ in cancel_items
                if cancel_effect_state(cancel) == "CONFIRMED"
            }
            if required_broker_nos - confirmed_broker_nos:
                result["waiting"].append({
                    "execution_process_id": process_id,
                    "reason": "BUY_RECOVERY_BATCH_CANCEL_EFFECT_PENDING",
                    "pending_original_order_nos": sorted(required_broker_nos - confirmed_broker_nos),
                })
                result["blocked_execution_process_ids"].append(process_id)
                continue
        representative = originals[0]
        template = _intent(representative)
        sides = {
            _text(order.get("side") or _preview(order).get("side") or _intent(order).get("side")).upper()
            for order in originals
        }
        if sides != {"BUY"}:
            result["reviews"].append({
                "execution_process_id": process_id,
                "review_reasons": ["BUY_RECOVERY_SIDE_IDENTITY_INVALID"],
            })
            result["blocked_execution_process_ids"].append(process_id)
            continue
        signal_id = _text(representative.get("source_signal_id") or template.get("source_signal_id"))
        signal = next((row for row in loaded["signals"] if _text(row.get("id")) == signal_id), {})
        if not signal or _text(signal.get("status")).upper() in _TERMINAL_SIGNALS:
            continue
        code = _text(representative.get("code") or _preview(representative).get("code"))
        order_account = _text(representative.get("account_no") or _preview(representative).get("account_no"))
        if order_account != account or (allowed is not None and code not in allowed):
            continue
        position = [row for row in loaded["positions"] if _text(row.get("account_no")) == account and _text(row.get("code")) == code]
        holding = [row for row in loaded["holdings"] if _text(row.get("account_no")) == account and _text(row.get("code")) == code]
        if len(position) > 1 or len(holding) > 1:
            result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": ["BUY_RECOVERY_POSITION_IDENTITY_INVALID"]})
            continue
        position_qty = _nonnegative_int(position[0].get("quantity")) if position else 0
        holding_qty = _nonnegative_int(holding[0].get("holding_quantity")) if holding else 0
        if position_qty is None or holding_qty is None or position_qty != holding_qty:
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "BUY_RECOVERY_POSITION_BROKER_MISMATCH"})
            continue
        residual = sum(_nonnegative_int(order.get("remaining_quantity")) or 0 for order in originals)
        if residual <= 0:
            completion_payload = {
                "execution_process_id": process_id,
                "source_signal_id": signal_id,
                "code": code,
                "source_plan_generation": source_generation,
                "cancel_ids": sorted(_text(cancel.get("id")) for cancel, _ in cancel_items),
                "confirmed_residual_quantity": 0,
                "position_quantity": position_qty,
                "reason": "BUY_RECOVERY_RESIDUAL_ZERO",
            }
            completion_payload["snapshot_hash"] = stable_hash(completion_payload)
            result["completion_proposals"].append(completion_payload)
            result["blocked_execution_process_ids"].append(process_id)
            continue
        snapshot_payload = {
            "execution_process_id": process_id,
            "source_signal_id": signal_id,
            "source_plan_generation": source_generation,
            "cancel_ids": sorted(_text(cancel.get("id")) for cancel, _ in cancel_items),
            "original_order_nos": sorted(_broker_no(order) for order in originals),
            "confirmed_residual_quantity": residual,
            "position_quantity": position_qty,
        }
        snapshot_hash = stable_hash(snapshot_payload)
        if any(
            _text(_intent(order).get("recovery_source_snapshot_hash")) == snapshot_hash
            for order in orders
        ):
            continue
        next_generation = max(
            [plan_generation(_intent(order).get("plan_generation", order.get("plan_generation"))) for order in orders if _text(order.get("execution_process_id") or _intent(order).get("execution_process_id")) == process_id]
            or [source_generation]
        ) + 1
        try:
            recovery_template = _recovery_template(template, residual, signal)
            current_price = prices.get(code)
            if current_price is None or current_price <= 0:
                raise ValueError("BUY_RECOVERY_CURRENT_PRICE_UNAVAILABLE")
            buy_round = _positive_int(template.get("buy_round"))
            option_hash = _text(template.get("option_snapshot_hash"))
            if buy_round is None or not option_hash:
                raise ValueError("BUY_RECOVERY_IDENTITY_INVALID")
            intents = build_buy_generation_intents(
                template=recovery_template,
                source_signal_id=signal_id,
                process_id=process_id,
                option_snapshot_hash=option_hash,
                generation=next_generation,
                buy_round=buy_round,
                remaining_budget=residual * current_price,
                current_price=current_price,
                source_snapshot_hash=snapshot_hash,
                generated_at=generated_at,
                quantity_override=residual,
            )
        except (TypeError, ValueError) as exc:
            result["reviews"].append({"execution_process_id": process_id, "code": code, "review_reasons": [str(exc)]})
            continue
        recovery_started_at = _text(template.get("recovery_started_at")) or generated_at.isoformat(timespec="milliseconds")
        for intent in intents:
            intent["recovery_cycle"] = True
            intent["recovery_generation"] = next_generation
            intent["recovery_started_at"] = recovery_started_at
            intent["recovery_source_snapshot_hash"] = snapshot_hash
            intent["confirmed_residual_quantity"] = residual
            intent["recovery_source_original_order_nos"] = sorted(
                {_broker_no(order) for order in originals if _broker_no(order)}
            )
            intent["recovery_source_cancel_ids"] = sorted(
                {_text(cancel.get("id")) for cancel, _ in cancel_items if _text(cancel.get("id"))}
            )
            intent.pop("buy_price_reset_source_snapshot_hash", None)
        proposal_signal = deepcopy(signal)
        proposal_signal.update({"status": "PENDING", "execution_intent": intents[0], "execution_intents": intents})
        result["proposals"].append({
            "execution_process_id": process_id,
            "source_signal_id": signal_id,
            "code": code,
            "plan_generation": next_generation,
            "buy_round": buy_round,
            "confirmed_residual_quantity": residual,
            "trigger_snapshot": {**snapshot_payload, "snapshot_hash": snapshot_hash},
            "signal": proposal_signal,
            "execution_intents": intents,
        })
        result["blocked_execution_process_ids"].append(process_id)
    result["blocked_execution_process_ids"] = sorted(set(result["blocked_execution_process_ids"]))
    result["ok"] = not result["errors"] and not result["reviews"]
    return result


__all__ = ["inspect_buy_recovery_generations"]
