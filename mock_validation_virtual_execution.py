# -*- coding: utf-8 -*-
"""Deterministic virtual order/fill engine for the isolated Mock domain.

Only frozen Mock market evidence is consumed.  No Production queue, broker,
Chejan, position, PnL, event, or budget writer is imported or called.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any, Callable, Iterable

from mock_validation_contract import (
    ORDER_CANCELED,
    ORDER_CANCEL_PENDING,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_CLOSING,
    SESSION_RUNNING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    new_mock_identity,
    normalized_stock_code,
    payload_hash,
)
from mock_validation_market_data import (
    MockMarketSnapshot,
    MockOrderbookLevel,
    MockOrderbookSnapshot,
    MockTradeSnapshot,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService


RESULT_ACCEPTED = "ACCEPTED"
RESULT_BLOCKED = "BLOCKED"
RESULT_NOOP = "NOOP"
RESULT_PROGRESS = "PROGRESS"

FILLABLE_STATES = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
LIVE_STATES = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}


@dataclass(frozen=True)
class MockExecutionPolicy:
    """Explicit consumer-side freshness and cost policy."""

    connection_epoch: int
    login_session_id: str
    max_orderbook_age_seconds: float
    max_trade_age_seconds: float
    commission_rate: float = 0.0


def _decimal(value: Any, reason: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise MockValidationError(reason)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MockValidationError(reason) from exc
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise MockValidationError(reason)
    return number


def _number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def _aware(value: datetime | str) -> datetime | None:
    observed = value
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed)
        except ValueError:
            return None
    return observed if isinstance(observed, datetime) and observed.tzinfo is not None else None


def _fresh(
    *,
    received_at: str,
    now: datetime,
    max_age_seconds: float,
    connection_epoch: int,
    login_session_id: str,
    policy: MockExecutionPolicy,
) -> tuple[bool, str]:
    current = _aware(now)
    received = _aware(received_at)
    if current is None or received is None or max_age_seconds < 0:
        return False, "MOCK_MARKET_TIME_INVALID"
    if (
        connection_epoch != int(policy.connection_epoch)
        or login_session_id != clean_text(policy.login_session_id)
    ):
        return False, "MOCK_MARKET_SESSION_INVALID"
    age = (current - received).total_seconds()
    if age < 0 or age > max_age_seconds:
        return False, "MOCK_MARKET_STALE"
    return True, "MOCK_MARKET_FRESH"


def _valid_levels(levels: Iterable[MockOrderbookLevel]) -> Iterable[tuple[int, Decimal, int]]:
    """Yield only the contiguous, valid depth prefix in official level order."""

    for level in levels:
        if level.price is None or level.quantity is None:
            break
        price = _decimal(level.price, "MOCK_BOOK_PRICE_INVALID", positive=True)
        quantity = _decimal(level.quantity, "MOCK_BOOK_QTY_INVALID")
        whole_qty = int(quantity.to_integral_value(rounding=ROUND_FLOOR))
        if quantity != Decimal(whole_qty):
            break
        yield int(level.level), price, whole_qty


def _sweep(
    *,
    side: str,
    order_type: str,
    limit_price: Decimal | None,
    remaining_qty: int,
    orderbook: MockOrderbookSnapshot,
    available_notional: Decimal | None,
) -> list[tuple[int, Decimal, int]]:
    levels = orderbook.asks if side == "BUY" else orderbook.bids
    fills: list[tuple[int, Decimal, int]] = []
    remaining = int(remaining_qty)
    budget = available_notional
    for level, price, displayed_qty in _valid_levels(levels):
        if remaining <= 0:
            break
        if order_type == "LIMIT" and limit_price is not None:
            if side == "BUY" and price > limit_price:
                break
            if side == "SELL" and price < limit_price:
                break
        fill_qty = min(remaining, displayed_qty)
        if budget is not None:
            affordable = int((budget / price).to_integral_value(rounding=ROUND_FLOOR))
            fill_qty = min(fill_qty, affordable)
        if fill_qty <= 0:
            break
        fills.append((fill_qty, price, level))
        remaining -= fill_qty
        if budget is not None:
            budget -= price * fill_qty
    return fills


def _same_side_queue(order: dict[str, Any], orderbook: MockOrderbookSnapshot) -> int | None:
    price = _decimal(order["requested_price"], "MOCK_ORDER_REQUESTED_PRICE_INVALID", positive=True)
    levels = orderbook.bids if order["side"] == "BUY" else orderbook.asks
    for level in levels:
        if level.price is None or level.quantity is None:
            return None
        observed_price = _decimal(level.price, "MOCK_BOOK_PRICE_INVALID", positive=True)
        quantity = _decimal(level.quantity, "MOCK_BOOK_QTY_INVALID")
        if observed_price == price:
            whole_qty = int(quantity.to_integral_value(rounding=ROUND_FLOOR))
            return whole_qty if quantity == Decimal(whole_qty) else None
    return 0


def order_vwap(order: dict[str, Any], fills: Iterable[dict[str, Any]]) -> int | float | None:
    own = [item for item in fills if item.get("mock_order_id") == order.get("mock_order_id")]
    quantity = sum(int(item["qty"]) for item in own)
    if quantity <= 0:
        return None
    notional = sum(_decimal(item["price"], "MOCK_FILL_PRICE_INVALID", positive=True) * int(item["qty"]) for item in own)
    return _number(notional / quantity)


class MockVirtualExecutionEngine:
    """Atomic virtual execution against one isolated Session document."""

    def __init__(
        self,
        repository: MockValidationRepository,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._now = now_factory or (lambda: datetime.now().astimezone())

    @staticmethod
    def _instance(document: dict[str, Any], routine_instance_id: str) -> str:
        instance_id = clean_text(routine_instance_id)
        if instance_id not in document["instance_execution"]:
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
        return instance_id

    @staticmethod
    def _progression(
        document: dict[str, Any], instance_id: str, *, allow_closing: bool = False,
    ) -> None:
        allowed_states = {SESSION_RUNNING, SESSION_CLOSING} if allow_closing else {SESSION_RUNNING}
        if document["session"]["state"] not in allowed_states:
            raise MockValidationError("MOCK_SESSION_NOT_RUNNING")
        if document["review"].get("review_required") is True:
            raise MockValidationError("MOCK_SESSION_REVIEW_STOPPED")
        if (
            document["instance_execution"][instance_id].get("progression_allowed") is not True
            and not allow_closing
        ):
            raise MockValidationError("MOCK_INSTANCE_PROGRESSION_BLOCKED")

    @staticmethod
    def _position(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
        return next(item for item in document["positions"] if item["routine_instance_id"] == instance_id)

    @staticmethod
    def _pnl(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
        return next(item for item in document["pnl"] if item["routine_instance_id"] == instance_id)

    @staticmethod
    def _order(document: dict[str, Any], mock_order_id: str) -> tuple[int, dict[str, Any]]:
        for index, order in enumerate(document["orders"]):
            if order.get("mock_order_id") == mock_order_id:
                return index, order
        raise MockValidationError("MOCK_ORDER_NOT_FOUND")

    @staticmethod
    def _active_sell_reservation(document: dict[str, Any], instance_id: str) -> int:
        return sum(
            int(order["remaining_qty"])
            for order in document["orders"]
            if order.get("routine_instance_id") == instance_id
            and order.get("side") == "SELL"
            and order.get("state") in LIVE_STATES
        )

    @staticmethod
    def _active_buy_reservation(document: dict[str, Any], instance_id: str) -> Decimal:
        return sum(
            (_decimal(order.get("reserved_budget", 0), "MOCK_ORDER_RESERVED_BUDGET_INVALID") for order in document["orders"]
             if order.get("routine_instance_id") == instance_id
             and order.get("side") == "BUY"
             and order.get("state") in LIVE_STATES),
            Decimal(0),
        )

    @staticmethod
    def _command_result(document: dict[str, Any], command_id: str) -> dict[str, Any] | None:
        entry = document.get("applied_commands", {}).get(command_id)
        return deepcopy(entry) if isinstance(entry, dict) else None

    def _event(
        self,
        *,
        document: dict[str, Any],
        command_id: str,
        event_type: str,
        timestamp: str,
        instance_id: str,
        reason_code: str = "",
        payload: dict[str, Any] | None = None,
        ordinal: int = 0,
    ) -> dict[str, Any]:
        return {
            "event_id": deterministic_mock_identity("ME", document["session"]["validation_session_id"], command_id, event_type, ordinal),
            "validation_session_id": document["session"]["validation_session_id"],
            "stock_code": document["session"]["stock_code"],
            "routine_instance_id": instance_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "reason_code": reason_code,
            "payload": deepcopy(payload) if isinstance(payload, dict) else {},
        }

    def _emit(self, events: Iterable[dict[str, Any]]) -> None:
        for event in events:
            self.repository.append_event(event)

    def _blocked(
        self,
        document: dict[str, Any],
        *,
        command_id: str,
        instance_id: str,
        reason: str,
        now_text: str,
    ) -> dict[str, Any]:
        event = self._event(
            document=document,
            command_id=command_id,
            event_type="VIRTUAL_ORDER_BLOCKED",
            timestamp=now_text,
            instance_id=instance_id,
            reason_code=reason,
        )
        self._emit((event,))
        return {"status": RESULT_BLOCKED, "reason": reason, "events": [event]}

    @staticmethod
    def _book_from_market(market: MockMarketSnapshot | MockOrderbookSnapshot | None) -> MockOrderbookSnapshot | None:
        if isinstance(market, MockMarketSnapshot):
            return market.orderbook
        return market if isinstance(market, MockOrderbookSnapshot) else None

    def _book_status(
        self,
        book: MockOrderbookSnapshot | None,
        *,
        document: dict[str, Any],
        now: datetime,
        policy: MockExecutionPolicy,
    ) -> tuple[bool, str]:
        if book is None:
            return False, "MOCK_ORDERBOOK_UNAVAILABLE"
        if book.stock_code != document["session"]["stock_code"]:
            return False, "MOCK_MARKET_STOCK_MISMATCH"
        return _fresh(
            received_at=book.received_at,
            now=now,
            max_age_seconds=float(policy.max_orderbook_age_seconds),
            connection_epoch=book.connection_epoch,
            login_session_id=book.login_session_id,
            policy=policy,
        )

    def submit_order(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        side: str,
        order_type: str,
        requested_qty: int,
        limit_price: int | float | None,
        market: MockMarketSnapshot | MockOrderbookSnapshot | None,
        policy: MockExecutionPolicy,
        execution_budget: int | float | None = None,
        generation: int = 0,
        child_identity: str = "",
        command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._instance(before, routine_instance_id)
        self._progression(before, instance_id, allow_closing=allow_closing)
        previous = self._command_result(before, command)
        if previous is not None:
            self._emit(previous.get("events", ()))
            order = next((item for item in before["orders"] if item.get("mock_order_id") == previous.get("entity_id")), None)
            return {"status": RESULT_NOOP, "duplicate": True, "order": deepcopy(order), "document": before}

        now = self._now()
        timestamp = now.isoformat(timespec="microseconds")
        normalized_side = clean_text(side).upper()
        normalized_type = clean_text(order_type).upper()
        if normalized_side not in {"BUY", "SELL"}:
            return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_ORDER_SIDE_INVALID", now_text=timestamp)
        if normalized_type not in {"MARKET", "LIMIT"}:
            return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_ORDER_TYPE_INVALID", now_text=timestamp)
        try:
            quantity_value = _decimal(requested_qty, "MOCK_ORDER_REQUESTED_QTY_INVALID", positive=True)
            quantity = int(quantity_value)
        except MockValidationError:
            quantity = 0
            quantity_value = Decimal(0)
        if quantity <= 0 or quantity_value != Decimal(quantity):
            return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_ORDER_REQUESTED_QTY_INVALID", now_text=timestamp)
        try:
            generation_value = int(generation)
        except (TypeError, ValueError):
            generation_value = -1
        if isinstance(generation, bool) or generation_value < 0 or str(generation).strip() not in {str(generation_value), f"{generation_value}.0"}:
            return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_ORDER_GENERATION_INVALID", now_text=timestamp)
        requested_price = None
        if normalized_type == "LIMIT":
            try:
                requested_price = _decimal(limit_price, "MOCK_ORDER_REQUESTED_PRICE_INVALID", positive=True)
            except MockValidationError:
                return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_ORDER_REQUESTED_PRICE_INVALID", now_text=timestamp)
        elif limit_price not in (None, 0, 0.0, ""):
            return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_MARKET_ORDER_PRICE_MUST_BE_EMPTY", now_text=timestamp)

        position = self._position(before, instance_id)
        if normalized_side == "SELL":
            sellable = int(position["available_qty"])
            if quantity > sellable:
                return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_SELLABLE_QTY_EXCEEDED", now_text=timestamp)
        else:
            try:
                budget = _decimal(execution_budget, "MOCK_EXECUTION_BUDGET_UNAVAILABLE", positive=True)
            except MockValidationError:
                return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_EXECUTION_BUDGET_UNAVAILABLE", now_text=timestamp)
            invested = _decimal(position.get("realized_cost_basis", 0), "MOCK_POSITION_COST_BASIS_INVALID")
            available = budget - invested - self._active_buy_reservation(before, instance_id)
            reserve = requested_price * quantity if requested_price is not None else available
            if available < 0 or reserve > available:
                return self._blocked(before, command_id=command, instance_id=instance_id, reason="MOCK_EXECUTION_BUDGET_EXCEEDED", now_text=timestamp)

        book = self._book_from_market(market)
        book_fresh, book_reason = self._book_status(book, document=before, now=now, policy=policy)
        order_id = deterministic_mock_identity("MO", session_id, command)
        events: list[dict[str, Any]] = []
        created_event = self._event(
            document=before, command_id=command, event_type="VIRTUAL_ORDER_CREATED",
            timestamp=timestamp, instance_id=instance_id, payload={"mock_order_id": order_id}, ordinal=0,
        )
        opened_event = self._event(
            document=before, command_id=command, event_type="VIRTUAL_ORDER_OPENED",
            timestamp=timestamp, instance_id=instance_id, payload={"mock_order_id": order_id}, ordinal=1,
        )
        events.extend((created_event, opened_event))
        order = {
            "mock_order_id": order_id,
            "validation_session_id": session_id,
            "routine_instance_id": instance_id,
            "stock_code": before["session"]["stock_code"],
            "side": normalized_side,
            "order_type": normalized_type,
            "requested_qty": quantity,
            "requested_price": _number(requested_price) if requested_price is not None else None,
            "remaining_qty": quantity,
            "filled_qty": 0,
            "state": ORDER_OPEN,
            "created_at": timestamp,
            "accepted_at": timestamp,
            "mock_opened_at": timestamp,
            "updated_at": timestamp,
            "last_progress_at": timestamp,
            "canceled_at": "",
            "generation": generation_value,
            "child_identity": clean_text(child_identity),
            "market_snapshot_identity_at_creation": book.snapshot_identity if book is not None else "",
            "market_connection_epoch": book.connection_epoch if book is not None else int(policy.connection_epoch),
            "market_login_session_id": book.login_session_id if book is not None else clean_text(policy.login_session_id),
            "last_processed_orderbook_snapshot": "",
            "last_processed_trade_sequence": 0,
            "last_processed_trade_identity": "",
            "queue_ahead_qty": None,
            "resting": False,
            "matching_status": book_reason,
            "execution_budget": _number(budget) if normalized_side == "BUY" else None,
            "reserved_budget": _number(reserve) if normalized_side == "BUY" else 0,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["orders"].append(order)
            current = document["orders"][-1]
            if current["side"] == "SELL":
                self._refresh_available_qty(document, instance_id)
            if book_fresh and book is not None:
                self._apply_orderbook(document, current, book, policy, timestamp, events, command)
            document["applied_commands"][command] = {
                "operation": "VIRTUAL_ORDER_SUBMIT",
                "applied_at": timestamp,
                "entity_id": order_id,
                "events": deepcopy(events),
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        persisted = next(item for item in result["document"]["orders"] if item["mock_order_id"] == order_id)
        self._emit(events)
        return {
            "status": RESULT_ACCEPTED,
            "duplicate": False,
            "order": deepcopy(persisted),
            "fills": [deepcopy(item) for item in result["document"]["fills"] if item["mock_order_id"] == order_id],
            "vwap": order_vwap(persisted, result["document"]["fills"]),
            "document": result["document"],
            "events": events,
        }

    def _apply_orderbook(
        self,
        document: dict[str, Any],
        order: dict[str, Any],
        book: MockOrderbookSnapshot,
        policy: MockExecutionPolicy,
        timestamp: str,
        events: list[dict[str, Any]],
        command: str,
    ) -> None:
        if order["state"] not in FILLABLE_STATES:
            return
        if order.get("last_processed_orderbook_snapshot") == book.snapshot_identity:
            return
        available_notional = None
        if order["side"] == "BUY":
            available_notional = _decimal(order.get("reserved_budget", 0), "MOCK_ORDER_RESERVED_BUDGET_INVALID")
        was_resting = order.get("resting") is True
        fills = _sweep(
            side=order["side"],
            order_type=order["order_type"],
            limit_price=_decimal(order["requested_price"], "MOCK_ORDER_REQUESTED_PRICE_INVALID", positive=True) if order["order_type"] == "LIMIT" else None,
            remaining_qty=int(order["remaining_qty"]),
            orderbook=book,
            available_notional=available_notional,
        )
        order["last_processed_orderbook_snapshot"] = book.snapshot_identity
        order["matching_status"] = "MOCK_ORDERBOOK_PROCESSED"
        for qty, price, level in fills:
            self._record_fill(
                document, order, qty=qty, price=price, market_identity=book.snapshot_identity,
                source_trade_sequence=None, timestamp=timestamp, events=events, command=command,
                source={"kind": "ORDERBOOK", "level": level}, policy=policy,
            )
        if order["state"] != ORDER_FILLED and order["order_type"] == "LIMIT":
            order["resting"] = True
            if (
                not was_resting
                or bool(fills)
                or order.get("queue_ahead_qty") is None
            ):
                order["queue_ahead_qty"] = _same_side_queue(order, book)
            order["matching_status"] = (
                "QUEUE_AHEAD_READY" if order["queue_ahead_qty"] is not None else "QUEUE_PROGRESS_UNCONFIRMED"
            )
        elif order["state"] != ORDER_FILLED:
            order["matching_status"] = "INSUFFICIENT_ORDERBOOK_DEPTH"

    def _record_fill(
        self,
        document: dict[str, Any],
        order: dict[str, Any],
        *,
        qty: int,
        price: Decimal,
        market_identity: str,
        source_trade_sequence: int | None,
        timestamp: str,
        events: list[dict[str, Any]],
        command: str,
        source: dict[str, Any],
        policy: MockExecutionPolicy,
    ) -> None:
        if order["state"] not in FILLABLE_STATES:
            raise MockValidationError("MOCK_ORDER_NOT_FILLABLE")
        fill_qty = min(int(qty), int(order["remaining_qty"]))
        if fill_qty <= 0:
            return
        instance_id = order["routine_instance_id"]
        position = self._position(document, instance_id)
        pnl = self._pnl(document, instance_id)
        if order["side"] == "SELL" and fill_qty > int(position["holding_qty"]):
            raise MockValidationError("MOCK_POSITION_NEGATIVE_RISK")
        sequence = 1 + sum(1 for item in document["fills"] if item.get("mock_order_id") == order["mock_order_id"])
        fill_id = deterministic_mock_identity("MF", order["mock_order_id"], sequence, market_identity, source_trade_sequence or 0, _number(price), fill_qty)
        existing = next((item for item in document["fills"] if item.get("mock_fill_id") == fill_id), None)
        if existing is not None:
            expected = (existing.get("qty"), existing.get("price"), existing.get("mock_order_id"))
            if expected != (fill_qty, _number(price), order["mock_order_id"]):
                raise MockValidationError("MOCK_FILL_ID_CONFLICT")
            return
        gross = price * fill_qty
        commission = gross * _decimal(policy.commission_rate, "MOCK_COMMISSION_RATE_INVALID")
        if _decimal(policy.commission_rate, "MOCK_COMMISSION_RATE_INVALID") > 1:
            raise MockValidationError("MOCK_COMMISSION_RATE_INVALID")
        tax = Decimal(0)
        realized = Decimal(0)
        old_qty = int(position["holding_qty"])
        old_basis = _decimal(position.get("realized_cost_basis", 0), "MOCK_POSITION_COST_BASIS_INVALID")
        if order["side"] == "BUY":
            new_qty = old_qty + fill_qty
            new_basis = old_basis + gross
            position["holding_qty"] = new_qty
            position["average_price"] = _number(new_basis / new_qty)
            position["realized_cost_basis"] = _number(new_basis)
        else:
            average = _decimal(position.get("average_price", 0), "MOCK_POSITION_AVERAGE_PRICE_INVALID")
            removed_basis = average * fill_qty
            realized = gross - removed_basis
            new_qty = old_qty - fill_qty
            new_basis = max(Decimal(0), old_basis - removed_basis)
            position["holding_qty"] = new_qty
            position["average_price"] = 0 if new_qty == 0 else position["average_price"]
            position["realized_cost_basis"] = 0 if new_qty == 0 else _number(new_basis)
            if document["session"].get("mock_tax_enabled") is True:
                tax = gross * _decimal(document["session"].get("mock_tax_rate", 0), "MOCK_TAX_RATE_INVALID")
        order["filled_qty"] = int(order["filled_qty"]) + fill_qty
        order["remaining_qty"] = int(order["requested_qty"]) - int(order["filled_qty"])
        order["state"] = ORDER_FILLED if order["remaining_qty"] == 0 else ORDER_PARTIAL_FILL
        order["updated_at"] = timestamp
        order["last_progress_at"] = timestamp
        order["queue_ahead_qty"] = 0 if order["queue_ahead_qty"] is not None else None
        order["resting"] = bool(order["state"] != ORDER_FILLED and order["order_type"] == "LIMIT")
        if order["side"] == "BUY":
            current_reserve = _decimal(order.get("reserved_budget", 0), "MOCK_ORDER_RESERVED_BUDGET_INVALID")
            if order["order_type"] == "LIMIT":
                limit = _decimal(order["requested_price"], "MOCK_ORDER_REQUESTED_PRICE_INVALID", positive=True)
                order["reserved_budget"] = _number(limit * int(order["remaining_qty"]))
            else:
                order["reserved_budget"] = _number(max(Decimal(0), current_reserve - gross))
            if order["state"] == ORDER_FILLED:
                order["reserved_budget"] = 0
        pnl["realized_pnl"] = _number(_decimal(pnl.get("realized_pnl", 0), "MOCK_PNL_REALIZED_INVALID") + realized)
        pnl["commission"] = _number(_decimal(pnl.get("commission", 0), "MOCK_PNL_COMMISSION_INVALID") + commission)
        pnl["mock_tax"] = _number(_decimal(pnl.get("mock_tax", 0), "MOCK_PNL_TAX_INVALID") + tax)
        gross_pnl = _decimal(pnl["realized_pnl"], "MOCK_PNL_REALIZED_INVALID") + _decimal(pnl.get("unrealized_pnl", 0), "MOCK_PNL_UNREALIZED_INVALID")
        pnl["gross_pnl"] = _number(gross_pnl)
        pnl["net_pnl"] = _number(gross_pnl - _decimal(pnl["commission"], "MOCK_PNL_COMMISSION_INVALID") - _decimal(pnl["mock_tax"], "MOCK_PNL_TAX_INVALID"))
        pnl["updated_at"] = timestamp
        position["updated_at"] = timestamp
        fill = {
            "mock_fill_id": fill_id,
            "mock_order_id": order["mock_order_id"],
            "validation_session_id": document["session"]["validation_session_id"],
            "routine_instance_id": instance_id,
            "stock_code": document["session"]["stock_code"],
            "side": order["side"],
            "qty": fill_qty,
            "price": _number(price),
            "filled_at": timestamp,
            "market_snapshot_identity": market_identity,
            "source_trade_sequence": source_trade_sequence,
            "fill_sequence": sequence,
            "commission": _number(commission),
            "mock_tax": _number(tax),
            "realized_pnl": _number(realized),
            "source": source,
        }
        document["fills"].append(fill)
        self._refresh_available_qty(document, instance_id)
        event_offset = len(events)
        events.append(self._event(
            document=document, command_id=command, event_type="VIRTUAL_FILL_RECORDED",
            timestamp=timestamp, instance_id=instance_id,
            payload={"mock_order_id": order["mock_order_id"], "mock_fill_id": fill_id, "qty": fill_qty, "price": _number(price)}, ordinal=event_offset,
        ))
        state_event = "VIRTUAL_ORDER_FILLED" if order["state"] == ORDER_FILLED else "VIRTUAL_ORDER_PARTIAL_FILL"
        events.append(self._event(
            document=document, command_id=command, event_type=state_event,
            timestamp=timestamp, instance_id=instance_id,
            payload={"mock_order_id": order["mock_order_id"], "remaining_qty": order["remaining_qty"]}, ordinal=event_offset + 1,
        ))

    def _refresh_available_qty(self, document: dict[str, Any], instance_id: str) -> None:
        position = self._position(document, instance_id)
        reserved = self._active_sell_reservation(document, instance_id)
        position["available_qty"] = max(0, int(position["holding_qty"]) - reserved)

    def process_orderbook(
        self,
        session_id: str,
        mock_order_id: str,
        *,
        market: MockMarketSnapshot | MockOrderbookSnapshot | None,
        policy: MockExecutionPolicy,
        command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        _, initial = self._order(before, mock_order_id)
        instance_id = self._instance(before, initial["routine_instance_id"])
        self._progression(before, instance_id, allow_closing=allow_closing)
        previous = self._command_result(before, command)
        if previous is not None:
            self._emit(previous.get("events", ()))
            return {"status": RESULT_NOOP, "duplicate": True, "document": before}
        if initial["state"] not in FILLABLE_STATES:
            return {"status": RESULT_NOOP, "reason": "MOCK_ORDER_TERMINAL", "document": before}
        book = self._book_from_market(market)
        now = self._now()
        timestamp = now.isoformat(timespec="microseconds")
        fresh, reason = self._book_status(book, document=before, now=now, policy=policy)
        if not fresh or book is None:
            return {"status": RESULT_NOOP, "reason": reason, "document": before}
        if initial.get("last_processed_orderbook_snapshot") == book.snapshot_identity:
            return {"status": RESULT_NOOP, "reason": "MOCK_ORDERBOOK_ALREADY_PROCESSED", "document": before}
        events: list[dict[str, Any]] = []

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            _, order = self._order(document, mock_order_id)
            self._apply_orderbook(document, order, book, policy, timestamp, events, command)
            document["applied_commands"][command] = {
                "operation": "VIRTUAL_ORDERBOOK_PROCESS", "applied_at": timestamp,
                "entity_id": mock_order_id, "events": deepcopy(events),
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._emit(events)
        order = next(item for item in result["document"]["orders"] if item["mock_order_id"] == mock_order_id)
        return {"status": RESULT_PROGRESS if result.get("changed") else RESULT_NOOP, "order": deepcopy(order), "document": result["document"], "events": events}

    def process_trade(
        self,
        session_id: str,
        mock_order_id: str,
        *,
        trade: MockTradeSnapshot | None,
        policy: MockExecutionPolicy,
        command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        _, initial = self._order(before, mock_order_id)
        instance_id = self._instance(before, initial["routine_instance_id"])
        self._progression(before, instance_id, allow_closing=allow_closing)
        if initial["state"] not in FILLABLE_STATES or initial.get("order_type") != "LIMIT" or initial.get("resting") is not True:
            return {"status": RESULT_NOOP, "reason": "MOCK_ORDER_NOT_RESTING", "document": before}
        if trade is None or trade.stock_code != before["session"]["stock_code"]:
            return {"status": RESULT_NOOP, "reason": "MOCK_TRADE_UNAVAILABLE", "document": before}
        now = self._now()
        timestamp = now.isoformat(timespec="microseconds")
        fresh, reason = _fresh(
            received_at=trade.received_at, now=now, max_age_seconds=float(policy.max_trade_age_seconds),
            connection_epoch=trade.connection_epoch, login_session_id=trade.login_session_id, policy=policy,
        )
        if not fresh:
            return {"status": RESULT_NOOP, "reason": reason, "document": before}
        if int(trade.receive_sequence) <= int(initial.get("last_processed_trade_sequence", 0)):
            return {"status": RESULT_NOOP, "reason": "MOCK_TRADE_ALREADY_PROCESSED", "document": before}
        previous = self._command_result(before, command)
        if previous is not None:
            self._emit(previous.get("events", ()))
            return {"status": RESULT_NOOP, "duplicate": True, "document": before}
        expected_side = "SELL" if initial["side"] == "BUY" else "BUY"
        progression_reason = ""
        trade_qty = 0
        if trade.trade_side != expected_side:
            progression_reason = "QUEUE_PROGRESS_UNCONFIRMED"
        elif _decimal(trade.execution_price, "MOCK_TRADE_PRICE_INVALID", positive=True) != _decimal(initial["requested_price"], "MOCK_ORDER_REQUESTED_PRICE_INVALID", positive=True):
            progression_reason = "MOCK_TRADE_PRICE_UNRELATED"
        elif trade.execution_qty is None:
            progression_reason = "QUEUE_PROGRESS_UNCONFIRMED"
        else:
            quantity_value = _decimal(trade.execution_qty, "MOCK_TRADE_QTY_INVALID", positive=True)
            trade_qty = int(quantity_value)
            if quantity_value != Decimal(trade_qty):
                progression_reason = "QUEUE_PROGRESS_UNCONFIRMED"
        events: list[dict[str, Any]] = []

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            _, order = self._order(document, mock_order_id)
            queue = order.get("queue_ahead_qty")
            if progression_reason:
                order["matching_status"] = progression_reason
            elif queue is None:
                order["matching_status"] = "QUEUE_PROGRESS_UNCONFIRMED"
            else:
                consumed_ahead = min(int(queue), trade_qty)
                order["queue_ahead_qty"] = int(queue) - consumed_ahead
                own_qty = trade_qty - consumed_ahead
                if own_qty > 0:
                    self._record_fill(
                        document, order, qty=own_qty,
                        price=_decimal(trade.execution_price, "MOCK_TRADE_PRICE_INVALID", positive=True),
                        market_identity=trade.snapshot_identity,
                        source_trade_sequence=int(trade.receive_sequence), timestamp=timestamp,
                        events=events, command=command, source={"kind": "TRADE_TICK", "trade_side": trade.trade_side},
                        policy=policy,
                    )
                elif consumed_ahead > 0:
                    order["last_progress_at"] = timestamp
                    order["updated_at"] = timestamp
                order["matching_status"] = "QUEUE_TRADE_PROCESSED"
            order["last_processed_trade_sequence"] = int(trade.receive_sequence)
            order["last_processed_trade_identity"] = trade.snapshot_identity
            document["applied_commands"][command] = {
                "operation": "VIRTUAL_TRADE_PROCESS", "applied_at": timestamp,
                "entity_id": mock_order_id, "events": deepcopy(events),
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._emit(events)
        order = next(item for item in result["document"]["orders"] if item["mock_order_id"] == mock_order_id)
        return {
            "status": RESULT_NOOP if progression_reason else RESULT_PROGRESS,
            "reason": progression_reason,
            "order": deepcopy(order), "document": result["document"], "events": events,
        }

    def request_cancel(
        self, session_id: str, mock_order_id: str, *, command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        return self._cancel_transition(
            session_id, mock_order_id, target=ORDER_CANCEL_PENDING,
            command_id=command_id, allow_closing=allow_closing,
        )

    def finalize_cancel(
        self, session_id: str, mock_order_id: str, *, command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        return self._cancel_transition(
            session_id, mock_order_id, target=ORDER_CANCELED,
            command_id=command_id, allow_closing=allow_closing,
        )

    def cancel_order(
        self, session_id: str, mock_order_id: str, *, command_id: str | None = None,
        allow_closing: bool = False,
    ) -> dict[str, Any]:
        base = clean_text(command_id) or new_mock_identity("MC")
        requested = self.request_cancel(
            session_id, mock_order_id, command_id=f"{base}:PENDING",
            allow_closing=allow_closing,
        )
        if requested.get("status") == RESULT_BLOCKED:
            return requested
        return self.finalize_cancel(
            session_id, mock_order_id, command_id=f"{base}:FINAL",
            allow_closing=allow_closing,
        )

    def _cancel_transition(
        self,
        session_id: str,
        mock_order_id: str,
        *,
        target: str,
        command_id: str | None,
        allow_closing: bool,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        _, initial = self._order(before, mock_order_id)
        instance_id = self._instance(before, initial["routine_instance_id"])
        self._progression(before, instance_id, allow_closing=allow_closing)
        previous = self._command_result(before, command)
        if previous is not None:
            self._emit(previous.get("events", ()))
            current = next(item for item in before["orders"] if item["mock_order_id"] == mock_order_id)
            return {"status": RESULT_NOOP, "duplicate": True, "order": deepcopy(current), "document": before}
        allowed = (
            initial["state"] in {ORDER_OPEN, ORDER_PARTIAL_FILL} if target == ORDER_CANCEL_PENDING
            else initial["state"] == ORDER_CANCEL_PENDING
        )
        if not allowed:
            return {"status": RESULT_BLOCKED, "reason": "MOCK_CANCEL_STATE_INVALID", "document": before}
        now = self._now()
        timestamp = now.isoformat(timespec="microseconds")
        event_type = "VIRTUAL_ORDER_CANCEL_PENDING" if target == ORDER_CANCEL_PENDING else "VIRTUAL_ORDER_CANCELED"
        event = self._event(
            document=before, command_id=command, event_type=event_type, timestamp=timestamp,
            instance_id=instance_id, payload={"mock_order_id": mock_order_id},
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            _, order = self._order(document, mock_order_id)
            order["state"] = target
            order["updated_at"] = timestamp
            if target == ORDER_CANCELED:
                order["canceled_at"] = timestamp
                order["resting"] = False
                order["reserved_budget"] = 0
                self._refresh_available_qty(document, instance_id)
            document["applied_commands"][command] = {
                "operation": event_type, "applied_at": timestamp,
                "entity_id": mock_order_id, "events": [deepcopy(event)],
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._emit((event,))
        order = next(item for item in result["document"]["orders"] if item["mock_order_id"] == mock_order_id)
        return {"status": RESULT_PROGRESS, "order": deepcopy(order), "document": result["document"], "events": [event]}

    def escalate_structural_review(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        reason_code: str,
        reason: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        service = MockValidationSessionService(self.repository, now_factory=lambda: self._now().isoformat(timespec="microseconds"))
        return service.stop_for_instance_error(
            session_id, source_routine_instance_id=routine_instance_id,
            reason_code=reason_code, reason=reason, command_id=command_id,
        )


__all__ = [
    "MockExecutionPolicy",
    "MockVirtualExecutionEngine",
    "RESULT_ACCEPTED",
    "RESULT_BLOCKED",
    "RESULT_NOOP",
    "RESULT_PROGRESS",
    "order_vwap",
]
