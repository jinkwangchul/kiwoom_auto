# -*- coding: utf-8 -*-
"""Read-only account-wide auto-trade budget-consumption projection.

The projection combines only canonical Runtime facts that already exist:
current position cost basis and the unfilled reservation of broker-accepted
BUY orders.  It never writes Runtime, persists a new SoT, or calls a broker.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable


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
_UNSENT_STATUSES = {
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
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


def project_account_auto_trade_budget_consumption(
    *,
    account_no: object,
    positions_path: str | Path,
    order_queue_path: str | Path,
    recovery_complete: bool,
    reconciled_stock_codes: Iterable[object] = (),
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
        orders = _read_list(Path(order_queue_path), "orders")
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

        open_buy_reservation = 0
        open_buy_order_count = 0
        broker_order_numbers: set[str] = set()
        for order in orders:
            status = _text(order.get("status")).upper()
            side = _order_side(order)
            if side != "BUY":
                continue
            account_identity = _record_account(order)
            if status in _OPEN_BROKER_STATUSES | _AMBIGUOUS_SEND_STATUSES:
                if not account_identity:
                    raise ValueError("active BUY order account_no is required")
                if account_identity != account:
                    continue
            elif account_identity and account_identity != account:
                continue

            if status in _AMBIGUOUS_SEND_STATUSES:
                raise ValueError(f"BUY order lifecycle is unresolved: {status}")
            if status in _TERMINAL_STATUSES | _UNSENT_STATUSES:
                continue
            if status not in _OPEN_BROKER_STATUSES:
                if (
                    order.get("actual_order_sent") is True
                    or order.get("send_order_called") is True
                    or _text(order.get("broker_order_no"))
                ):
                    raise ValueError(f"BUY order status is unsupported: {status or 'missing'}")
                continue
            if _order_action(order) != "NEW":
                raise ValueError("active MODIFY/CANCEL BUY order cannot be projected safely")
            if not _text(order.get("source_signal_id")):
                raise ValueError("active BUY source_signal_id is required")
            code = _order_code(order)
            if not code:
                raise ValueError("active BUY stock code is required")
            if code not in reconciled:
                raise ValueError("active BUY order is outside reconciled stock scope")
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
