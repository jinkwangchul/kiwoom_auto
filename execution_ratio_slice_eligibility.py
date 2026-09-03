# -*- coding: utf-8 -*-
"""Read-only eligibility selection for durable RATIO_SLICE plans."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any

from execution_price_comparison import (
    evaluate_percent_comparison,
    resolve_price_source,
)
from execution_provenance_contract import plan_generation, stable_hash, validate_child_set
from execution_time_slice_due import (
    EXECUTIONS_PATH,
    FILLS_PATH,
    HOLDINGS_PATH,
    ORDERS_PATH,
    POSITIONS_PATH,
    SIGNALS_PATH,
    _PRE_DISPATCH,
    _SAFE_POST_DISPATCH,
    _as_dict,
    _evidence_time,
    _latest,
    _logical_identity,
    _nonnegative_int,
    _positive_int,
    _read,
    _text,
    _time,
    inspect_buy_slice_funding,
    cancel_effect_state,
)


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _ratio_result(
    *,
    left: float,
    right: float,
    direction: str,
    compare: str,
    threshold: float,
) -> tuple[bool | None, float | None]:
    return evaluate_percent_comparison(
        left=left,
        right=right,
        direction=direction,
        compare=compare,
        threshold=threshold,
    )


def _source_price(
    source: str,
    *,
    order_price: float | None,
    current_price: float | None,
    average_price: float | None,
) -> float | None:
    return resolve_price_source(
        source,
        order_price=order_price,
        current_price=current_price,
        average_price=average_price,
    )


def _review(signal: dict[str, Any], process_id: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "source_signal_id": _text(signal.get("id")),
        "execution_process_id": process_id,
        "code": _text(signal.get("code")),
        "name": _text(signal.get("name")),
        "review_reasons": sorted(set(reasons)),
        "review_location": "MULTI_RATIO_ELIGIBILITY_RECONCILIATION",
    }


def inspect_eligible_ratio_slices(
    *,
    selected_account_no: str,
    actionable_prices_by_code: dict[str, Any] | None,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    current_orderable_cash: Any = None,
    now: datetime | None = None,
    signals_path: str | Path = SIGNALS_PATH,
    orders_path: str | Path = ORDERS_PATH,
    executions_path: str | Path = EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
) -> dict[str, Any]:
    """Return at most one safe eligible ratio child per signal without writes."""
    account_no = _text(selected_account_no)
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None
        else None
    )
    blocked_processes = {
        _text(value)
        for value in (blocked_execution_process_ids or ())
        if _text(value)
    }
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}

    sources = (
        (signals_path, "signals", False),
        (orders_path, "orders", False),
        (executions_path, "executions", False),
        (executions_path, "processes", True),
        (fills_path, "fills", False),
        (positions_path, "positions", False),
        (holdings_path, "holdings", False),
    )
    loaded: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path, field, optional in sources:
        values, error = _read(path, field, optional=optional)
        loaded[field] = values
        if error:
            errors.append(error)
    if errors:
        return {"ok": False, "proposals": [], "reviews": [], "waiting": [], "errors": errors}

    prices = {
        _text(code): _positive_number(value)
        for code, value in (actionable_prices_by_code or {}).items()
        if _text(code)
    }
    selected_at = now or datetime.now()
    orders = loaded["orders"]
    executions = loaded["executions"]
    processes = loaded["processes"]
    fills = loaded["fills"]
    positions = loaded["positions"]
    holdings = loaded["holdings"]
    proposals: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    inspected_buy_processes: set[str] = set()

    for signal in loaded["signals"]:
        if _text(signal.get("status")).upper() != "PENDING":
            continue
        intents = signal.get("execution_intents")
        if not isinstance(intents, list) or not intents:
            continue
        clean_intents = [deepcopy(item) for item in intents if isinstance(item, dict)]
        if len(clean_intents) != len(intents) or {
            _text(item.get("execution_mode")).upper() for item in clean_intents
        } != {"MULTI_RATIO"} or {
            _text(item.get("child_kind")).upper() for item in clean_intents
        } != {"RATIO_SLICE"}:
            continue
        code = _text(signal.get("code"))
        if allowed is not None and code not in allowed:
            continue
        signal_id = _text(signal.get("id"))
        process_ids = {_text(item.get("execution_process_id")) for item in clean_intents}
        source_ids = {_text(item.get("source_signal_id")) for item in clean_intents}
        plan_hashes = {stable_hash(_as_dict(item.get("multi_ratio_plan"))) for item in clean_intents}
        reasons = [f"RATIO_SLICE_CHILD_SET_INVALID:{issue}" for issue in validate_child_set(clean_intents)]
        if len(process_ids) != 1 or "" in process_ids:
            reasons.append("RATIO_SLICE_PROCESS_ID_INVALID")
        if source_ids != {signal_id}:
            reasons.append("RATIO_SLICE_SOURCE_SIGNAL_ID_MISMATCH")
        if len(plan_hashes) != 1:
            reasons.append("RATIO_SLICE_PLAN_MISMATCH")
        process_id = next(iter(process_ids), "")
        sides = {_text(item.get("side")).upper() for item in clean_intents}
        side = next(iter(sides), "")
        if len(sides) != 1 or side not in {"BUY", "SELL"}:
            reasons.append("RATIO_SLICE_SIDE_INVALID")
        if side == "BUY" and {_text(item.get("account_no")) for item in clean_intents} != {account_no}:
            reasons.append("RATIO_SLICE_BUY_ACCOUNT_MISMATCH")
        if side == "BUY":
            if process_id in inspected_buy_processes:
                continue
            inspected_buy_processes.add(process_id)
        if process_id in blocked_processes:
            waiting.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "EXECUTION_PROCESS_RESET_IN_PROGRESS",
                }
            )
            continue

        grouped_orders: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for order in orders:
            if _text(order.get("execution_process_id")) != process_id:
                continue
            action = _text(order.get("order_action") or _as_dict(order.get("execution_request")).get("request_preview", {}).get("order_action")).upper()
            if action == "CANCEL":
                effect = cancel_effect_state(order)
                if effect == "PENDING":
                    evidence_pending = True
                elif effect == "UNCERTAIN":
                    reasons.append("RATIO_SLICE_CANCEL_EFFECT_UNCERTAIN")
                continue
            identity = _logical_identity(order)
            if identity is None:
                reasons.append("RATIO_SLICE_QUEUE_IDENTITY_INVALID")
                continue
            grouped_orders.setdefault(identity, []).append(order)
        latest_orders = {identity: _latest(records) for identity, records in grouped_orders.items()}
        existing_identities = set(latest_orders)
        if side == "BUY":
            for runtime in executions:
                if _text(runtime.get("execution_process_id")) != process_id:
                    continue
                identity = _logical_identity(runtime)
                order = latest_orders.get(identity)
                if order is None or runtime.get("execution_id") != order.get("execution_id"):
                    reasons.append("RATIO_SLICE_RUNTIME_QUEUE_MISMATCH")
                if (runtime.get("status") == "SEND_UNCERTAIN"
                        or runtime.get("manual_reconciliation_required") is True):
                    reasons.append("RATIO_SLICE_RUNTIME_UNCERTAIN")
        runtime_by_execution = {
            _text(item.get("execution_id")): item
            for item in executions
            if _text(item.get("execution_id"))
        }
        evidence_pending = False
        for identity, order in latest_orders.items():
            status = _text(order.get("status")).upper()
            execution_id = _text(order.get("execution_id"))
            if (
                status == "SEND_UNCERTAIN"
                or order.get("send_uncertain") is True
                or order.get("call_execution_uncertain") is True
                or order.get("manual_reconciliation_required") is True
            ):
                reasons.append(f"RATIO_SLICE_UNSAFE_CHILD:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _PRE_DISPATCH:
                evidence_pending = True
            elif status not in _SAFE_POST_DISPATCH:
                reasons.append(f"RATIO_SLICE_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")
            if status in _SAFE_POST_DISPATCH:
                runtime = runtime_by_execution.get(execution_id)
                if runtime is None:
                    reasons.append(f"RATIO_SLICE_RUNTIME_EXECUTION_MISSING:{execution_id}")
                elif _logical_identity(runtime) != identity:
                    reasons.append(f"RATIO_SLICE_RUNTIME_IDENTITY_MISMATCH:{execution_id}")
        if latest_orders:
            owners = [
                item for item in processes if _text(item.get("execution_process_id")) == process_id
            ]
            if len(owners) != 1 and not evidence_pending:
                reasons.append("RATIO_SLICE_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")
        if reasons:
            reviews.append(_review(signal, process_id, reasons))
            continue
        if evidence_pending:
            waiting.append({"source_signal_id": signal_id, "code": code, "reason": "PREVIOUS_RATIO_SLICE_EVIDENCE_PENDING"})
            continue

        missing = [
            item
            for item in sorted(clean_intents, key=lambda value: value["child_sequence_index"])
            if (
                _text(item.get("execution_process_id")),
                plan_generation(item.get("plan_generation")),
                item.get("child_sequence_index"),
            ) not in existing_identities
        ]
        if not missing:
            waiting.append({"source_signal_id": signal_id, "code": code, "reason": "RATIO_SLICE_PLAN_COMPLETE"})
            continue

        if side == "BUY":
            position_matches = [p for p in positions
                if _text(p.get("account_no")) == account_no and _text(p.get("code")) == code]
            average_price = _positive_number(position_matches[0].get("average_price")) if len(position_matches) == 1 else None
        else:
            holding_matches = [
                item for item in holdings
                if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
            ]
            position_matches = [
                item for item in positions
                if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
            ]
            if len(holding_matches) != 1:
                reasons.append("RATIO_SLICE_BROKER_HOLDING_MATCH_INVALID")
                holding = {}
            else:
                holding = holding_matches[0]
            if len(position_matches) != 1:
                reasons.append("RATIO_SLICE_POSITION_MATCH_INVALID")
                position = {}
            else:
                position = position_matches[0]
            holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
            available_quantity = _nonnegative_int(holding.get("available_quantity"))
            position_quantity = _nonnegative_int(position.get("quantity"))
            average_price = _positive_number(position.get("average_price"))
            if holding_quantity is None or available_quantity is None or available_quantity > holding_quantity:
                reasons.append("RATIO_SLICE_BROKER_HOLDING_QUANTITY_INVALID")
            if position_quantity is None or position_quantity != holding_quantity:
                reasons.append("RATIO_SLICE_POSITION_HOLDING_MISMATCH")
            if (
                holding.get("manual_reconciliation_required") is True
                or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}
            ):
                reasons.append("RATIO_SLICE_HOLDING_RECONCILIATION_REQUIRED")

            process_execution_ids = {_text(order.get("execution_id")) for order in latest_orders.values()}
            process_fills = [fill for fill in fills if _text(fill.get("execution_id")) in process_execution_ids]
            latest_evidence = max(
                [_evidence_time(item) for item in latest_orders.values()]
                + [_evidence_time(item) for item in process_fills]
                + [_evidence_time(position)],
                default=datetime.min,
            )
            holding_time = _time(holding.get("received_at"))
            if holding_time is None or holding_time < latest_evidence:
                if reasons:
                    reasons.append("RATIO_SLICE_HOLDING_EVIDENCE_STALE")
                else:
                    waiting.append({"source_signal_id": signal_id, "code": code, "reason": "RATIO_SLICE_HOLDING_EVIDENCE_PENDING"})
                    continue
            if reasons:
                reviews.append(_review(signal, process_id, reasons))
                continue
            assert available_quantity is not None
            if available_quantity <= 0:
                waiting.append({"source_signal_id": signal_id, "code": code, "reason": "RATIO_SLICE_NO_SELLABLE_QUANTITY"})
                continue

        selected = deepcopy(missing[0])
        plan = _as_dict(selected.get("multi_ratio_plan"))
        left_source = _text(plan.get("ratio_left")).upper()
        right_source = _text(plan.get("ratio_right")).upper()
        direction = _text(plan.get("ratio_direction")).upper()
        compare = _text(plan.get("ratio_compare")).upper()
        threshold = _positive_number(plan.get("ratio_value"))
        order_price = _positive_number(plan.get("order_price"))
        current_price = prices.get(code)
        if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
            waiting.append({"source_signal_id": signal_id, "code": code, "reason": "RATIO_CURRENT_PRICE_UNAVAILABLE"})
            continue
        if "AVG_PRICE" in {left_source, right_source} and average_price is None:
            reviews.append(_review(signal, process_id, ["RATIO_AVERAGE_PRICE_UNAVAILABLE"]))
            continue
        left_price = _source_price(
            left_source,
            order_price=order_price,
            current_price=current_price,
            average_price=average_price,
        )
        right_price = _source_price(
            right_source,
            order_price=order_price,
            current_price=current_price,
            average_price=average_price,
        )
        if left_price is None or right_price is None or threshold is None:
            reviews.append(_review(signal, process_id, ["RATIO_TRIGGER_SOURCE_INVALID"]))
            continue
        eligible, observed_percent = _ratio_result(
            left=left_price,
            right=right_price,
            direction=direction,
            compare=compare,
            threshold=threshold,
        )
        if eligible is None or observed_percent is None:
            reviews.append(_review(signal, process_id, ["RATIO_TRIGGER_POLICY_INVALID"]))
            continue
        if not eligible:
            waiting.append(
                {
                    "source_signal_id": signal_id,
                    "code": code,
                    "reason": "RATIO_THRESHOLD_NOT_MET",
                    "observed_percent": observed_percent,
                    "threshold_percent": threshold,
                }
            )
            continue

        if side == "BUY":
            buy_proposals, buy_reviews, buy_waiting = inspect_buy_slice_funding(
                selected=selected, signal=signal, account_no=account_no, code=code,
                process_id=process_id, signal_id=signal_id, latest_orders=latest_orders,
                processes=processes, orders=orders, fills=fills, positions=positions, holdings=holdings,
                actionable_prices_by_code=actionable_prices_by_code, current_orderable_cash=current_orderable_cash,
                current=selected_at, missing=missing,
            )
            for proposal in buy_proposals:
                proposal["execution_intents"][0]["child_plan"]["ratio_trigger_evidence"] = {
                    "left_source": left_source, "left_price": left_price,
                    "right_source": right_source, "right_price": right_price,
                    "direction": direction, "compare": compare,
                    "threshold_percent": threshold, "observed_percent": observed_percent,
                    "evaluated_at": selected_at.isoformat(timespec="milliseconds"),
                }
            proposals.extend(buy_proposals)
            reviews.extend(buy_reviews)
            waiting.extend(buy_waiting)
            continue

        planned_quantity = _positive_int(selected.get("quantity"))
        if planned_quantity is None:
            reviews.append(_review(signal, process_id, ["RATIO_SLICE_PLANNED_QUANTITY_INVALID"]))
            continue
        safe_quantity = min(planned_quantity, ceil(available_quantity / len(missing)))
        if safe_quantity <= 0:
            continue
        child_plan = deepcopy(_as_dict(selected.get("child_plan")))
        child_plan.update(
            {
                "scheduled_planned_quantity": planned_quantity,
                "planned_quantity": safe_quantity,
                "eligible_selected_at": selected_at.isoformat(timespec="milliseconds"),
                "available_quantity_at_eligibility": available_quantity,
                "remaining_unqueued_child_count": len(missing),
                "ratio_trigger_evidence": {
                    "left_source": left_source,
                    "left_price": left_price,
                    "right_source": right_source,
                    "right_price": right_price,
                    "direction": direction,
                    "compare": compare,
                    "threshold_percent": threshold,
                    "observed_percent": observed_percent,
                },
            }
        )
        selected["quantity"] = safe_quantity
        selected["child_plan"] = child_plan
        selected_signal = deepcopy(signal)
        selected_signal["execution_intent"] = selected
        selected_signal["execution_intents"] = [selected]
        proposals.append(
            {
                "source_signal_id": signal_id,
                "execution_process_id": process_id,
                "code": code,
                "name": _text(signal.get("name")),
                "child_sequence_index": selected.get("child_sequence_index"),
                "planned_quantity": planned_quantity,
                "safe_quantity": safe_quantity,
                "available_quantity": available_quantity,
                "observed_percent": observed_percent,
                "threshold_percent": threshold,
                "complete_after_enqueue": len(missing) == 1,
                "signal": selected_signal,
                "execution_intents": [selected],
            }
        )

    return {"ok": True, "proposals": proposals, "reviews": reviews, "waiting": waiting, "errors": []}
