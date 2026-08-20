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
            previous = reservations.get(source_signal_id)
            if previous is None or rank > previous[0]:
                reservations[source_signal_id] = (rank, status, order)

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
