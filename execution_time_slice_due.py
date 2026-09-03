# -*- coding: utf-8 -*-
"""Read-only due selection for durable TIME_SLICE execution plans."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from math import ceil
from pathlib import Path
from typing import Any

from execution_provenance_contract import plan_generation, validate_child_set, validate_process_record
from account_auto_trade_budget_consumption import (
    project_time_slice_buy_budget, _order_code, _order_side, _record_account,
)
from execution_unfilled_cancel_eligibility import cancel_effect_state


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"
ORDERS_PATH = RUNTIME_DIR / "order_queue.json"
EXECUTIONS_PATH = RUNTIME_DIR / "order_executions.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"

_PRE_DISPATCH = {
    "PENDING",
    "APPROVED",
    "EXECUTABLE",
    "EXECUTION_ENABLED",
    "REAL_READY",
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
}
_SAFE_POST_DISPATCH = {
    "BROKER_ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "SEND_CALL_REJECTED",
    "BROKER_REJECTED",
    "REJECTED",
    "CANCELLED",
    "PARTIAL_CANCELLED",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _positive_int(value: Any) -> int | None:
    number = _nonnegative_int(value)
    return number if number is not None and number > 0 else None


def _time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _read(path: str | Path, field: str, *, optional: bool = False) -> tuple[list[dict[str, Any]], str]:
    try:
        root = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{field.upper()}_READ_FAILED:{exc}"
    if optional and isinstance(root, dict) and field not in root:
        return [], ""
    if not isinstance(root, dict) or not isinstance(root.get(field), list):
        return [], f"{field.upper()}_SCHEMA_INVALID"
    if any(not isinstance(item, dict) for item in root[field]):
        return [], f"{field.upper()}_ITEM_INVALID"
    return [deepcopy(item) for item in root[field]], ""


def _intent_generation(intent: dict[str, Any]) -> int | None:
    try:
        return plan_generation(intent.get("plan_generation"))
    except ValueError:
        return None


def _logical_identity(value: dict[str, Any]) -> tuple[str, int, int] | None:
    intent = _as_dict(value.get("execution_intent"))
    process_id = _text(value.get("execution_process_id") or intent.get("execution_process_id"))
    generation = _intent_generation(value if value.get("plan_generation") not in (None, "") else intent)
    index = value.get("child_sequence_index", intent.get("child_sequence_index"))
    if (
        not process_id
        or generation is None
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index <= 0
    ):
        return None
    return process_id, generation, index


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        records,
        key=lambda item: (
            _time(
                item.get("updated_at")
                or item.get("send_call_result_recorded_at")
                or item.get("created_at")
            )
            or datetime.min,
            _text(item.get("id")),
        ),
    )


def _evidence_time(record: dict[str, Any]) -> datetime:
    return _time(
        record.get("updated_at")
        or record.get("recorded_at")
        or record.get("received_at")
        or record.get("send_call_result_recorded_at")
        or record.get("created_at")
    ) or datetime.min


def inspect_buy_slice_funding(
    *, selected, signal, account_no, code, process_id, signal_id, latest_orders,
    processes, orders, fills, positions, holdings, actionable_prices_by_code,
    current_orderable_cash, current, missing, due_count=None,
):
    """Shared read-only funding/holding checks for one deferred BUY child."""
    proposals, reviews, waiting, reasons = [], [], [], []
    selected = deepcopy(selected)
    meta = {"source_signal_id": signal_id, "execution_process_id": process_id,
            "code": code, "name": _text(signal.get("name"))}
    if latest_orders:
        owner = next((p for p in processes if p.get("execution_process_id") == process_id), {})
        if validate_process_record(owner) or owner.get("source_signal_id") != signal_id:
            reviews.append({**meta, "review_reasons": ["TIME_SLICE_BUY_PROCESS_OWNER_INVALID"], "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
            return proposals, reviews, waiting
        # Subsequent children reference the first immutable owner;
        # their own approval time is not a new process approval.
        selected["execution_process_owner_required"] = False
        selected["option_snapshot_hash"] = owner["option_snapshot_hash"]
    quantity = _positive_int(selected.get("quantity"))
    price = _positive_int(
        _as_dict(actionable_prices_by_code).get(code)
        if selected.get("price_basis") == "CURRENT_PRICE" else selected.get("price")
    )
    if quantity is None:
        reasons.append("TIME_SLICE_BUY_PLANNED_QUANTITY_INVALID")
    if price is None:
        waiting.append({**meta, "reason": "TIME_SLICE_BUY_PRICE_UNAVAILABLE"})
        return proposals, reviews, waiting
    if reasons:
        reviews.append({**meta, "review_reasons": reasons, "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
        return proposals, reviews, waiting
    amount = quantity * price
    selected["price"] = price
    selected["budget"] = amount
    selected_signal = deepcopy(signal)
    selected_signal["execution_intent"] = selected
    selected_signal["execution_intents"] = [selected]
    budget = project_time_slice_buy_budget(
        order=selected_signal, order_records=orders, fill_records=fills,
        candidate_amount=amount,
    )
    if budget.get("available") is not True:
        reviews.append({**meta, "review_reasons": [budget["reason"]], "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
        return proposals, reviews, waiting
    if budget.get("admitted") is not True:
        waiting.append({**meta, "reason": budget["reason"], "budget_evidence": budget})
        return proposals, reviews, waiting
    # No implicit parallel BUY: another open execution for this stock
    # also holds its budget until a definitive lifecycle is recorded.
    # Queue keeps both the source candidate and its dispatch record.
    # A stale EXECUTABLE source is not another live broker order.
    stock_orders: dict[str, list[dict[str, Any]]] = {}
    try:
        for item in orders:
            if _order_code(item) != code or _order_side(item) != "BUY" or _record_account(item) != account_no:
                continue
            key = _text(item.get("execution_id") or item.get("id"))
            stock_orders.setdefault(key, []).append(item)
    except ValueError:
        reviews.append({**meta, "review_reasons": ["TIME_SLICE_BUY_ACCOUNT_MISMATCH"], "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
        return proposals, reviews, waiting
    other_open = [latest for items in stock_orders.values()
                  if _text((latest := _latest(items)).get("status"))
                  in _PRE_DISPATCH | {"BROKER_ACCEPTED", "PARTIALLY_FILLED", "SEND_UNCERTAIN"}]
    if other_open:
        if any(_text(item.get("status")) == "SEND_UNCERTAIN" for item in other_open):
            reviews.append({**meta, "review_reasons": ["TIME_SLICE_BUY_SEND_UNCERTAIN"], "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
        else:
            waiting.append({**meta, "reason": "TIME_SLICE_BUY_OPEN_ORDER_PENDING"})
        return proposals, reviews, waiting
    pos = [p for p in positions if _text(p.get("account_no")) == account_no and _text(p.get("code")) == code]
    held = [h for h in holdings if _text(h.get("account_no")) == account_no and _text(h.get("code")) == code]
    pos_qty = _nonnegative_int(pos[0].get("quantity")) if len(pos) == 1 else 0
    held_qty = _nonnegative_int(held[0].get("holding_quantity")) if len(held) == 1 else 0
    if (len(pos) > 1 or len(held) > 1 or pos_qty is None or pos_qty != held_qty
            or (held and (held[0].get("manual_reconciliation_required") is True
                or _text(held[0].get("reconciliation_status")) not in {"", "CONSISTENT"}))):
        reviews.append({**meta, "review_reasons": ["TIME_SLICE_POSITION_HOLDING_MISMATCH"], "review_location": "MULTI_TIME_DUE_RECONCILIATION"})
        return proposals, reviews, waiting
    if latest_orders and held:
        latest = max([_evidence_time(o) for o in latest_orders.values()]
                     + [_evidence_time(p) for p in pos]
                     + [_evidence_time(f) for f in fills if f.get("execution_process_id") == process_id])
        if _evidence_time(held[0]) < latest:
            waiting.append({**meta, "reason": "TIME_SLICE_HOLDING_EVIDENCE_PENDING"})
            return proposals, reviews, waiting
    cash = _nonnegative_int(current_orderable_cash)
    if cash is None or amount > cash:
        waiting.append({**meta, "reason": "TIME_SLICE_BUY_CASH_UNAVAILABLE" if cash is None else "TIME_SLICE_BUY_CASH_EXCEEDED"})
        return proposals, reviews, waiting
    child_plan = deepcopy(_as_dict(selected.get("child_plan")))
    child_plan.update({"planned_price": price, "planned_budget": amount,
                       "due_selected_at": current.isoformat(timespec="milliseconds"),
                       "remaining_round_budget_at_due": budget["remaining_round_budget"]})
    selected["child_plan"] = child_plan
    proposals.append({**meta, "child_sequence_index": selected["child_sequence_index"],
                      "scheduled_at": child_plan.get("scheduled_at"), "safe_quantity": quantity,
                      "overdue_child_count": len(missing) if due_count is None else due_count,
                      "complete_after_enqueue": len(missing) == 1,
                      "signal": selected_signal, "execution_intents": [selected], "budget_evidence": budget})
    return proposals, reviews, waiting



def inspect_due_time_slices(
    *,
    selected_account_no: str,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    actionable_prices_by_code: dict[str, Any] | None = None,
    current_orderable_cash: Any = None,
    now: datetime | None = None,
    signals_path: str | Path = SIGNALS_PATH,
    orders_path: str | Path = ORDERS_PATH,
    executions_path: str | Path = EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
) -> dict[str, Any]:
    """Select at most one safe due child per signal without mutating Runtime."""
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

    current = now or datetime.now()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)

    orders = loaded["orders"]
    executions = loaded["executions"]
    processes = loaded["processes"]
    fills = loaded["fills"]
    positions = loaded["positions"]
    holdings = loaded["holdings"]
    proposals: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []

    for signal in loaded["signals"]:
        if _text(signal.get("status")).upper() != "PENDING":
            continue
        intents = signal.get("execution_intents")
        if not isinstance(intents, list) or not intents:
            continue
        clean_intents = [deepcopy(item) for item in intents if isinstance(item, dict)]
        if len(clean_intents) != len(intents) or {
            _text(item.get("execution_mode")).upper() for item in clean_intents
        } != {"MULTI_TIME"}:
            continue
        code = _text(signal.get("code"))
        if allowed is not None and code not in allowed:
            continue
        signal_id = _text(signal.get("id"))
        process_ids = {_text(item.get("execution_process_id")) for item in clean_intents}
        source_ids = {_text(item.get("source_signal_id")) for item in clean_intents}
        reasons = [
            f"TIME_SLICE_CHILD_SET_INVALID:{issue}"
            for issue in validate_child_set(clean_intents)
        ]
        if len(process_ids) != 1 or "" in process_ids:
            reasons.append("TIME_SLICE_PROCESS_ID_INVALID")
        if source_ids != {signal_id}:
            reasons.append("TIME_SLICE_SOURCE_SIGNAL_ID_MISMATCH")
        process_id = next(iter(process_ids), "")
        sides = {_text(item.get("side")).upper() for item in clean_intents}
        if len(sides) != 1 or not sides <= {"BUY", "SELL"}:
            reasons.append("TIME_SLICE_SIDE_INVALID")
        side = next(iter(sides), "")
        if side == "BUY" and {_text(item.get("account_no")) for item in clean_intents} != {account_no}:
            reasons.append("TIME_SLICE_BUY_ACCOUNT_MISMATCH")
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

        process_orders = [
            item
            for item in orders
            if _text(item.get("execution_process_id")) == process_id
        ]
        grouped_orders: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for order in process_orders:
            action = _text(order.get("order_action") or _as_dict(order.get("execution_request")).get("request_preview", {}).get("order_action")).upper()
            if action == "CANCEL":
                effect = cancel_effect_state(order)
                if effect == "PENDING":
                    evidence_pending = True
                elif effect == "UNCERTAIN":
                    reasons.append("TIME_SLICE_CANCEL_EFFECT_UNCERTAIN")
                continue
            identity = _logical_identity(order)
            if identity is None:
                reasons.append("TIME_SLICE_QUEUE_IDENTITY_INVALID")
                continue
            grouped_orders.setdefault(identity, []).append(order)
        latest_orders = {identity: _latest(records) for identity, records in grouped_orders.items()}
        existing_identities = set(latest_orders)

        evidence_pending = False
        runtime_by_execution = {
            _text(item.get("execution_id")): item
            for item in executions
            if _text(item.get("execution_id"))
        }
        for identity, order in latest_orders.items():
            status = _text(order.get("status")).upper()
            execution_id = _text(order.get("execution_id"))
            if (
                status == "SEND_UNCERTAIN"
                or order.get("send_uncertain") is True
                or order.get("call_execution_uncertain") is True
                or order.get("manual_reconciliation_required") is True
            ):
                reasons.append(f"TIME_SLICE_UNSAFE_CHILD:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _PRE_DISPATCH:
                evidence_pending = True
            elif status not in _SAFE_POST_DISPATCH:
                reasons.append(f"TIME_SLICE_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")
            if status in _SAFE_POST_DISPATCH:
                runtime = runtime_by_execution.get(execution_id)
                if runtime is None:
                    reasons.append(f"TIME_SLICE_RUNTIME_EXECUTION_MISSING:{execution_id}")
                elif _logical_identity(runtime) != identity:
                    reasons.append(f"TIME_SLICE_RUNTIME_IDENTITY_MISMATCH:{execution_id}")
        if latest_orders:
            owners = [
                item for item in processes if _text(item.get("execution_process_id")) == process_id
            ]
            if len(owners) != 1 and not evidence_pending:
                reasons.append("TIME_SLICE_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")

        if reasons:
            reviews.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "name": _text(signal.get("name")),
                    "review_reasons": sorted(set(reasons)),
                    "review_location": "MULTI_TIME_DUE_RECONCILIATION",
                }
            )
            continue
        if evidence_pending:
            waiting.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "PREVIOUS_TIME_SLICE_EVIDENCE_PENDING",
                }
            )
            continue

        missing = [
            item
            for item in sorted(clean_intents, key=lambda value: value["child_sequence_index"])
            if (
                _text(item.get("execution_process_id")),
                plan_generation(item.get("plan_generation")),
                item.get("child_sequence_index"),
            )
            not in existing_identities
        ]
        due = [
            item
            for item in missing
            if (_time(_as_dict(item.get("child_plan")).get("scheduled_at")) or datetime.max)
            <= current
        ]
        if not due:
            waiting.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "TIME_SLICE_NOT_DUE" if missing else "TIME_SLICE_PLAN_COMPLETE",
                }
            )
            continue

        if side == "BUY":
            buy_proposals, buy_reviews, buy_waiting = inspect_buy_slice_funding(
                selected=due[0], signal=signal, account_no=account_no, code=code,
                process_id=process_id, signal_id=signal_id, latest_orders=latest_orders,
                processes=processes, orders=orders, fills=fills, positions=positions, holdings=holdings,
                actionable_prices_by_code=actionable_prices_by_code, current_orderable_cash=current_orderable_cash,
                current=current, missing=missing, due_count=len(due),
            )
            proposals.extend(buy_proposals)
            reviews.extend(buy_reviews)
            waiting.extend(buy_waiting)
            continue
        holding_matches = [
            item
            for item in holdings
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        position_matches = [
            item
            for item in positions
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        if len(holding_matches) != 1:
            reasons.append("TIME_SLICE_BROKER_HOLDING_MATCH_INVALID")
            holding = {}
        else:
            holding = holding_matches[0]
        if len(position_matches) > 1:
            reasons.append("TIME_SLICE_POSITION_MATCH_AMBIGUOUS")
            position = {}
        else:
            position = position_matches[0] if position_matches else {}
        holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
        available_quantity = _nonnegative_int(holding.get("available_quantity"))
        position_quantity = _nonnegative_int(position.get("quantity")) if position else 0
        if (
            holding_quantity is None
            or available_quantity is None
            or available_quantity > holding_quantity
        ):
            reasons.append("TIME_SLICE_BROKER_HOLDING_QUANTITY_INVALID")
        if position_quantity is None or position_quantity != holding_quantity:
            reasons.append("TIME_SLICE_POSITION_HOLDING_MISMATCH")
        if (
            holding.get("manual_reconciliation_required") is True
            or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}
        ):
            reasons.append("TIME_SLICE_HOLDING_RECONCILIATION_REQUIRED")

        process_execution_ids = {
            _text(order.get("execution_id")) for order in latest_orders.values()
        }
        process_fills = [
            fill for fill in fills if _text(fill.get("execution_id")) in process_execution_ids
        ]
        latest_evidence = max(
            [_evidence_time(item) for item in latest_orders.values()]
            + [_evidence_time(item) for item in process_fills]
            + [_evidence_time(position)],
            default=datetime.min,
        )
        holding_time = _time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_evidence:
            if reasons:
                reasons.append("TIME_SLICE_HOLDING_EVIDENCE_STALE")
            else:
                waiting.append(
                    {
                        "source_signal_id": signal_id,
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "TIME_SLICE_HOLDING_EVIDENCE_PENDING",
                    }
                )
                continue
        if reasons:
            reviews.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "name": _text(signal.get("name")),
                    "review_reasons": sorted(set(reasons)),
                    "review_location": "MULTI_TIME_DUE_RECONCILIATION",
                }
            )
            continue
        assert available_quantity is not None
        if available_quantity <= 0:
            waiting.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "TIME_SLICE_NO_SELLABLE_QUANTITY",
                }
            )
            continue

        selected = deepcopy(due[0])
        planned_quantity = _positive_int(selected.get("quantity"))
        if planned_quantity is None:
            reviews.append(
                {
                    "source_signal_id": signal_id,
                    "execution_process_id": process_id,
                    "code": code,
                    "name": _text(signal.get("name")),
                    "review_reasons": ["TIME_SLICE_PLANNED_QUANTITY_INVALID"],
                    "review_location": "MULTI_TIME_DUE_RECONCILIATION",
                }
            )
            continue
        safe_quantity = min(planned_quantity, ceil(available_quantity / len(missing)))
        if safe_quantity <= 0:
            continue
        child_plan = deepcopy(_as_dict(selected.get("child_plan")))
        child_plan["scheduled_planned_quantity"] = planned_quantity
        child_plan["planned_quantity"] = safe_quantity
        child_plan["due_selected_at"] = current.isoformat(timespec="milliseconds")
        child_plan["available_quantity_at_due"] = available_quantity
        child_plan["remaining_unqueued_child_count"] = len(missing)
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
                "scheduled_at": child_plan.get("scheduled_at"),
                "planned_quantity": planned_quantity,
                "safe_quantity": safe_quantity,
                "available_quantity": available_quantity,
                "overdue_child_count": len(due),
                "complete_after_enqueue": len(missing) == 1,
                "signal": selected_signal,
                "execution_intents": [selected],
            }
        )

    return {"ok": True, "proposals": proposals, "reviews": reviews, "waiting": waiting, "errors": []}
