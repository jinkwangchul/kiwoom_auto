# -*- coding: utf-8 -*-
"""Read-only account-wide auto-trade budget-consumption projection.

The projection combines only canonical Runtime facts that already exist:
current position cost basis and one reservation per admitted BUY lifecycle.
It never writes Runtime, persists a new SoT, or calls a broker.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


_OPEN_BROKER_STATUSES = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_AMBIGUOUS_SEND_STATUSES = {
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
}
_TERMINAL_STATUSES = {
    "FILLED",
    "CANCELLED",
    "PARTIAL_CANCELLED",
    "BROKER_REJECTED",
    "SEND_CALL_REJECTED",
}
_RESERVED_UNSENT_STATUSES = {
    "EXECUTABLE",
    "REAL_READY",
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
}
_IGNORED_PRE_ADMISSION_STATUSES = {
    "PENDING",
    "APPROVED",
    "BLOCKED",
    "BLOCKED_POLICY",
}
_LIFECYCLE_RANK = {
    **{status: 0 for status in _IGNORED_PRE_ADMISSION_STATUSES},
    "EXECUTABLE": 10,
    "REAL_READY": 20,
    "ORDER_QUEUED": 30,
    "DISPATCH_CLAIMED": 40,
    "SEND_ATTEMPTED": 50,
    "SEND_CALL_IN_PROGRESS": 60,
    "SEND_CALL_ACCEPTED": 70,
    "SEND_UNCERTAIN": 70,
    "BROKER_ACCEPTED": 80,
    "PARTIALLY_FILLED": 90,
    **{status: 100 for status in _TERMINAL_STATUSES},
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_code(value: Any) -> str:
    text = _text(value).upper()
    return text[1:] if text.startswith("A") else text


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    text = _text(value).replace(",", "")
    if not text:
        raise ValueError(f"{field} is required")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"{field} must be an integer")
    result = int(number)
    if result < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return result


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "holding_cost": None,
        "open_buy_reservation": None,
        "consumed_amount": None,
        "position_count": 0,
        "open_buy_order_count": 0,
        "reason": str(reason or "budget consumption unavailable"),
    }


def _read_list(path: Path, field: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} root must be an object")
    records = data.get(field)
    if not isinstance(records, list):
        raise ValueError(f"{path.name}.{field} must be a list")
    if any(not isinstance(item, dict) for item in records):
        raise ValueError(f"{path.name}.{field} must contain only objects")
    return [dict(item) for item in records]


def _record_account(record: dict[str, Any]) -> str:
    execution_request = _as_dict(record.get("execution_request"))
    request_preview = _as_dict(execution_request.get("request_preview"))
    guard_snapshot = _as_dict(execution_request.get("guard_snapshot"))
    candidates = {
        _text(value)
        for value in (
            record.get("account_no"),
            request_preview.get("account_no"),
            guard_snapshot.get("account_no"),
        )
        if _text(value)
    }
    if len(candidates) > 1:
        raise ValueError("order account identity is inconsistent")
    return next(iter(candidates), "")


def _request_preview(record: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(record.get("execution_request")).get("request_preview"))


def _order_side(record: dict[str, Any]) -> str:
    request = _request_preview(record)
    return _text(record.get("side") or request.get("side")).upper()


def _order_action(record: dict[str, Any]) -> str:
    request = _request_preview(record)
    return _text(record.get("order_action") or request.get("order_action") or "NEW").upper()


def _order_code(record: dict[str, Any]) -> str:
    request = _request_preview(record)
    return _normalize_code(record.get("code") or record.get("stock_code") or request.get("code"))


def _order_quantity(record: dict[str, Any], *, status: str) -> int:
    request = _request_preview(record)
    original = record.get("original_order_quantity")
    if original in (None, ""):
        original = record.get("quantity")
    if original in (None, ""):
        original = request.get("quantity")
    quantity = _integer(original, field="order quantity", minimum=1)
    remaining = record.get("remaining_quantity")
    if status == "PARTIALLY_FILLED":
        remaining_quantity = _integer(
            remaining,
            field="remaining_quantity",
            minimum=1,
        )
    elif remaining in (None, ""):
        remaining_quantity = quantity
    else:
        remaining_quantity = _integer(
            remaining,
            field="remaining_quantity",
            minimum=1,
        )
    if remaining_quantity > quantity:
        raise ValueError("remaining_quantity exceeds order quantity")
    return remaining_quantity


def _order_price(record: dict[str, Any]) -> int:
    request = _request_preview(record)
    value = record.get("order_price")
    if value in (None, ""):
        value = record.get("price")
    if value in (None, ""):
        value = request.get("price")
    return _integer(value, field="open BUY order price", minimum=1)


def canonical_buy_candidate_amount(record: Mapping[str, Any]) -> int:
    """Return the already-computed BUY request budget without repricing it."""
    order = dict(record) if isinstance(record, Mapping) else {}
    intent = _as_dict(order.get("execution_intent"))
    values = []
    for value in (order.get("amount"), intent.get("budget")):
        if value not in (None, ""):
            values.append(_integer(value, field="candidate BUY amount", minimum=1))
    if not values:
        raise ValueError("candidate BUY amount is required")
    if len(set(values)) != 1:
        raise ValueError("candidate BUY amount is inconsistent")
    return values[0]


def project_system_total_budget_buy_admission(
    *,
    total_budget: object,
    account_consumed_amount: object,
    candidate_buy_amount: object,
) -> dict[str, object]:
    """Project the strict system-total ceiling for one deterministic BUY."""
    try:
        total = _integer(total_budget, field="system total budget")
        consumed = _integer(account_consumed_amount, field="account consumed amount")
        candidate = _integer(candidate_buy_amount, field="candidate BUY amount", minimum=1)
    except ValueError as exc:
        return {
            "available": False,
            "admitted": False,
            "reason": str(exc),
            "reason_code": "SYSTEM_TOTAL_BUDGET_EVIDENCE_UNAVAILABLE",
            "system_total_budget": None,
            "account_consumed_amount": None,
            "candidate_buy_amount": None,
            "projected_account_consumption": None,
            "system_total_budget_exceeded": None,
        }
    projected = consumed + candidate
    exceeded = projected > total
    return {
        "available": True,
        "admitted": not exceeded,
        "reason": "SYSTEM_TOTAL_BUDGET_EXCEEDED" if exceeded else "",
        "reason_code": "SYSTEM_TOTAL_BUDGET_EXCEEDED" if exceeded else "",
        "system_total_budget": total,
        "account_consumed_amount": consumed,
        "candidate_buy_amount": candidate,
        "projected_account_consumption": projected,
        "system_total_budget_exceeded": exceeded,
    }


def _validated_order_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = list(records)
    if any(not isinstance(item, Mapping) for item in result):
        raise ValueError("order_queue.json.orders must contain only objects")
    return [dict(item) for item in result]


def project_account_auto_trade_budget_consumption(
    *,
    account_no: object,
    positions_path: str | Path,
    order_queue_path: str | Path,
    recovery_complete: bool,
    reconciled_stock_codes: Iterable[object] = (),
    order_records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, object]:
    """Project occupied capital for one reconciled account without mutation."""
    account = _text(account_no)
    if not account:
        return _unavailable("selected account is required")
    if recovery_complete is not True:
        return _unavailable("Production Recovery is not complete")
    reconciled = {
        code for code in (_normalize_code(item) for item in reconciled_stock_codes) if code
    }

    try:
        positions = _read_list(Path(positions_path), "positions")
        orders = (
            _read_list(Path(order_queue_path), "orders")
            if order_records is None
            else _validated_order_records(order_records)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _unavailable(str(exc))

    holding_cost = 0
    position_count = 0
    position_codes: set[str] = set()
    try:
        for position in positions:
            quantity_value = position.get("quantity", 0)
            quantity = _integer(quantity_value, field="position.quantity")
            position_account = _text(position.get("account_no"))
            if quantity > 0 and not position_account:
                raise ValueError("open position account_no is required")
            if position_account != account:
                continue
            code = _normalize_code(position.get("code") or position.get("stock_code"))
            if not code:
                raise ValueError("position stock code is required")
            if code in position_codes:
                raise ValueError("duplicate account position stock code")
            position_codes.add(code)
            status = _text(position.get("position_status")).upper()
            if quantity == 0:
                if status and status != "CLOSED":
                    raise ValueError("zero position is not CLOSED")
                continue
            if status != "OPEN":
                raise ValueError("positive position is not OPEN")
            if code not in reconciled:
                raise ValueError("open position is outside reconciled stock scope")
            cost_basis = _integer(
                position.get("cost_basis"),
                field="position.cost_basis",
                minimum=1,
            )
            holding_cost += cost_basis
            position_count += 1

        reservations: dict[str, tuple[int, str, dict[str, Any]]] = {}
        for order in orders:
            if _order_action(order) == "CANCEL":
                # Control orders do not consume BUY budget; the original
                # broker-open order remains the reservation until its effect
                # evidence updates that order's lifecycle.
                continue
            status = _text(order.get("status")).upper()
            side = _order_side(order)
            if side != "BUY":
                continue
            account_identity = _record_account(order)
            active_statuses = _OPEN_BROKER_STATUSES | _AMBIGUOUS_SEND_STATUSES | _RESERVED_UNSENT_STATUSES
            if status in active_statuses:
                if not account_identity:
                    raise ValueError("active BUY order account_no is required")
                if account_identity != account:
                    continue
            elif account_identity and account_identity != account:
                continue

            if status in _AMBIGUOUS_SEND_STATUSES:
                raise ValueError(f"BUY order lifecycle is unresolved: {status}")
            if status not in _LIFECYCLE_RANK:
                if (
                    order.get("actual_order_sent") is True
                    or order.get("send_order_called") is True
                    or _text(order.get("broker_order_no"))
                ):
                    raise ValueError(f"BUY order status is unsupported: {status or 'missing'}")
                continue
            source_signal_id = _text(order.get("source_signal_id"))
            if status in active_statuses and not source_signal_id:
                raise ValueError("active BUY source_signal_id is required")
            if not source_signal_id:
                continue
            rank = _LIFECYCLE_RANK[status]
            # A signal can own several independent child reservations. Only
            # projections of the SAME execution may collapse to one lifecycle.
            intent = _as_dict(order.get("execution_intent"))
            execution_id = _text(order.get("execution_id") or intent.get("execution_id"))
            reservation_key = f"execution:{execution_id}" if execution_id else f"signal:{source_signal_id}"
            previous = reservations.get(reservation_key)
            if previous is None or rank > previous[0]:
                reservations[reservation_key] = (rank, status, order)

        open_buy_reservation = 0
        open_buy_order_count = 0
        broker_order_numbers: set[str] = set()
        for _rank, status, order in reservations.values():
            if status in _TERMINAL_STATUSES | _IGNORED_PRE_ADMISSION_STATUSES:
                continue
            if _order_action(order) != "NEW":
                raise ValueError("active MODIFY/CANCEL BUY order cannot be projected safely")
            code = _order_code(order)
            if not code:
                raise ValueError("active BUY stock code is required")
            if code not in reconciled:
                raise ValueError("active BUY order is outside reconciled stock scope")
            if status in _RESERVED_UNSENT_STATUSES:
                open_buy_reservation += canonical_buy_candidate_amount(order)
                open_buy_order_count += 1
                continue
            broker_order_no = _text(order.get("broker_order_no"))
            if not broker_order_no:
                raise ValueError("active BUY broker_order_no is required")
            if broker_order_no in broker_order_numbers:
                raise ValueError("duplicate active BUY broker_order_no")
            broker_order_numbers.add(broker_order_no)
            remaining_quantity = _order_quantity(order, status=status)
            order_price = _order_price(order)
            open_buy_reservation += remaining_quantity * order_price
            open_buy_order_count += 1
    except ValueError as exc:
        return _unavailable(str(exc))

    return {
        "available": True,
        "holding_cost": holding_cost,
        "open_buy_reservation": open_buy_reservation,
        "consumed_amount": holding_cost + open_buy_reservation,
        "position_count": position_count,
        "open_buy_order_count": open_buy_order_count,
        "reason": "",
    }


def project_time_slice_buy_budget(
    *,
    order: Mapping[str, Any],
    order_records: list[dict[str, Any]],
    fill_records: list[dict[str, Any]],
    candidate_amount: object,
) -> dict[str, Any]:
    """Recheck an immutable deferred BUY round ceiling using Queue/Fill facts.

    Open predecessor orders wait (including their reserved unfilled amount).
    Terminal partial cancellation consumes only the confirmed cumulative Fill
    cost, never the original requested quantity. No writes or quantity scaling.
    """
    from chejan_event_recorder import _fill_ledger_summary

    intent = _as_dict(order.get("execution_intent")) or dict(order)
    if intent.get("side") != "BUY" or intent.get("execution_mode") not in {"MULTI_TIME", "MULTI_RATIO"}:
        return {"available": True, "admitted": True, "reason": "NOT_APPLICABLE"}
    try:
        plan_key = "multi_ratio_plan" if intent.get("execution_mode") == "MULTI_RATIO" else "multi_time_plan"
        plan = _as_dict(intent.get(plan_key))
        budget = _integer(plan.get("approved_round_budget"), field="approved round budget", minimum=1)
        amount = _integer(candidate_amount, field="due BUY amount", minimum=1)
        process_id = _text(intent.get("execution_process_id"))
        signal_id = _text(intent.get("source_signal_id"))
        own_id = _text(intent.get("execution_id"))
        if not process_id or not signal_id or not own_id:
            raise ValueError("TIME_SLICE_BUY_IDENTITY_MISSING")
        consumed = Decimal(0)
        reserved = 0
        grouped: dict[str, dict[str, Any]] = {}
        for record in order_records:
            if _order_action(record) == "CANCEL":
                continue
            other = _as_dict(record.get("execution_intent"))
            pid = _text(record.get("execution_process_id") or other.get("execution_process_id"))
            if pid != process_id:
                continue
            eid = _text(record.get("execution_id") or other.get("execution_id"))
            if not eid:
                raise ValueError("TIME_SLICE_BUY_QUEUE_IDENTITY_MISSING")
            if eid == own_id:
                continue
            if (
                _text(record.get("source_signal_id") or other.get("source_signal_id")) != signal_id
                or other.get("buy_round") != intent.get("buy_round")
                or other.get(plan_key) != plan
                or _order_side(record) != "BUY"
                or _record_account(record) != _text(intent.get("account_no"))
                or _order_code(record) != _order_code(dict(order))
            ):
                raise ValueError("TIME_SLICE_BUY_ROUND_IDENTITY_MISMATCH")
            status = _text(record.get("status")).upper()
            if (status in _AMBIGUOUS_SEND_STATUSES or record.get("manual_reconciliation_required") is True
                    or record.get("send_uncertain") is True):
                raise ValueError("TIME_SLICE_BUY_SEND_UNCERTAIN")
            previous = grouped.get(eid)
            if previous is None or _LIFECYCLE_RANK.get(status, -1) > _LIFECYCLE_RANK.get(_text(previous.get("status")), -1):
                grouped[eid] = record
        for record in grouped.values():
            status = _text(record.get("status")).upper()
            if status not in _LIFECYCLE_RANK:
                raise ValueError("TIME_SLICE_BUY_LIFECYCLE_UNKNOWN")
            summary = _fill_ledger_summary(fill_records, record)
            if summary["duplicate_execution_identities"] or summary["out_of_order_fill_identities"] or summary["broker_order_mismatches"]:
                raise ValueError("TIME_SLICE_BUY_FILL_IDENTITY_MISMATCH")
            cumulative = _integer(record.get("cumulative_filled_quantity", 0), field="cumulative fill quantity")
            if cumulative != summary["fills_summed_quantity"]:
                raise ValueError("TIME_SLICE_BUY_QUEUE_FILL_MISMATCH")
            original_quantity = record.get("original_order_quantity") or record.get("quantity") or _request_preview(record).get("quantity")
            if status == "FILLED" and cumulative != _integer(original_quantity, field="filled order quantity", minimum=1):
                raise ValueError("TIME_SLICE_BUY_FILLED_QUANTITY_MISMATCH")
            if cumulative:
                average = summary["fills_weighted_average_price"]
                if average is None or average <= 0:
                    raise ValueError("TIME_SLICE_BUY_FILL_PRICE_MISSING")
                consumed += Decimal(str(average)) * cumulative
            if status in _OPEN_BROKER_STATUSES:
                reserved += _order_quantity(record, status=status) * _order_price(record)
            elif status not in _TERMINAL_STATUSES | {"BLOCKED", "BLOCKED_POLICY"}:
                reserved += canonical_buy_candidate_amount(record)
        # Round consumption is historical BUY fill cost, not current holdings:
        # selling some holdings must not reopen this round's spending ceiling.
        remaining = Decimal(budget) - consumed - reserved
        return {
            "available": True, "admitted": reserved == 0 and Decimal(amount) <= remaining,
            "waiting": reserved > 0,
            "reason": "TIME_SLICE_BUY_OPEN_ORDER_PENDING" if reserved else (
                "TIME_SLICE_BUY_ROUND_BUDGET_EXCEEDED" if Decimal(amount) > remaining else ""),
            "approved_round_budget": budget, "consumed_amount": float(consumed),
            "open_buy_reservation": reserved, "remaining_round_budget": float(remaining),
            "candidate_buy_amount": amount,
        }
    except (ValueError, TypeError, KeyError) as exc:
        return {"available": False, "admitted": False, "reason": str(exc)}
