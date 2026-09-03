# -*- coding: utf-8 -*-
"""Stock-scoped operation-day lifecycle for the isolated Mock domain.

An operation day controls every Routine Instance of one stock together while
positions and trading cycles remain instance-owned.  The module writes only
the Mock repository and delegates matching/cancel transitions to the Phase-3
virtual engine with its explicit closing-only capability.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Callable

from mock_validation_contract import (
    ORDER_CANCEL_PENDING,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_CLOSING,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
)
from mock_validation_market_data import MockMarketSnapshot
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import (
    MockExecutionPolicy,
    MockVirtualExecutionEngine,
    RESULT_BLOCKED,
)


OPERATION_RUNNING = "RUNNING"
OPERATION_CLOSING = "CLOSING"
OPERATION_ENDED = "ENDED"
OPERATION_REVIEW_STOPPED = "REVIEW_STOPPED"

OUTCOME_DONE = "DONE"
OUTCOME_CARRYOVER_DONE = "CARRYOVER_DONE"
OUTCOME_NOT_READY = "NOT_READY"
OUTCOME_REVIEW_REQUIRED = "REVIEW_REQUIRED"

CLOSE_MARKET = "MARKET"
CLOSE_CURRENT_PRICE = "CURRENT_PRICE"
CLOSE_CARRYOVER = "CARRYOVER"

_LIVE = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
_REQUEST_EVENTS = {
    "NORMAL": "NORMAL_CLOSE_REQUESTED",
    "AUTO": "AUTO_CLOSE_REQUESTED",
    "EARLY": "EARLY_CLOSE_REQUESTED",
}


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    try:
        parsed = datetime.fromisoformat(clean_text(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _date_text(value: date | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = clean_text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise MockValidationError("MOCK_OPERATION_TRADING_DATE_INVALID") from exc


def normalize_close_method(value: Any) -> str:
    text = clean_text(value).upper().replace(" ", "_")
    aliases = {
        "MARKET": CLOSE_MARKET,
        "MARKET_ORDER": CLOSE_MARKET,
        "시장가": CLOSE_MARKET,
        "CURRENT_PRICE": CLOSE_CURRENT_PRICE,
        "현재가": CLOSE_CURRENT_PRICE,
        "현재가즉시": CLOSE_CURRENT_PRICE,
        "CARRYOVER": CLOSE_CARRYOVER,
        "LONG_HOLD": CLOSE_CARRYOVER,
        "이월": CLOSE_CARRYOVER,
        "장기보유": CLOSE_CARRYOVER,
    }
    result = aliases.get(text)
    if result is None:
        raise MockValidationError("MOCK_OPERATION_CLOSE_METHOD_INVALID")
    return result


def _root(document: dict[str, Any]) -> dict[str, Any]:
    root = document.setdefault(
        "mock_operation_lifecycle",
        {"version": 1, "current": None, "history": [], "commands": {}},
    )
    if (
        not isinstance(root, dict)
        or not isinstance(root.get("history"), list)
        or not isinstance(root.get("commands"), dict)
    ):
        raise MockValidationError("MOCK_OPERATION_STATE_INVALID")
    return root


def _positions(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        clean_text(item.get("routine_instance_id")): item
        for item in document.get("positions", ())
        if isinstance(item, dict)
    }
    if set(result) != set(document.get("instance_execution", {})):
        raise MockValidationError("MOCK_OPERATION_POSITION_SET_MISMATCH")
    return result


def _validate_operation_integrity(document: dict[str, Any]) -> None:
    root = _root(document)
    history_ids = [
        clean_text(item.get("operation_session_id"))
        for item in root["history"]
        if isinstance(item, dict)
    ]
    if len(history_ids) != len(root["history"]) or any(not value for value in history_ids):
        raise MockValidationError("MOCK_OPERATION_HISTORY_INVALID")
    if len(history_ids) != len(set(history_ids)):
        raise MockValidationError("MOCK_OPERATION_HISTORY_IDENTITY_CONFLICT")
    current = root.get("current")
    if current is None:
        return
    if not isinstance(current, dict):
        raise MockValidationError("MOCK_OPERATION_CURRENT_INVALID")
    if not clean_text(current.get("operation_session_id")).startswith("MS-"):
        raise MockValidationError("MOCK_OPERATION_IDENTITY_INVALID")
    if current.get("state") not in {
        OPERATION_RUNNING, OPERATION_CLOSING, OPERATION_ENDED, OPERATION_REVIEW_STOPPED,
    }:
        raise MockValidationError("MOCK_OPERATION_STATE_INVALID")
    _date_text(current.get("trading_date"))
    if not isinstance(current.get("processed_cycles"), dict):
        raise MockValidationError("MOCK_OPERATION_CYCLE_LEDGER_INVALID")
    if not isinstance(current.get("immediate_commands"), dict):
        raise MockValidationError("MOCK_IMMEDIATE_COMMAND_LEDGER_INVALID")


def _live_orders(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in document.get("orders", ()) if item.get("state") in _LIVE]


def evaluate_mock_operation_completion(
    document: dict[str, Any], *, final_close_boundary: bool = False,
) -> dict[str, Any]:
    """Pure stock-level DONE/CARRYOVER/REVIEW classification."""
    root = document.get("mock_operation_lifecycle")
    operation = root.get("current") if isinstance(root, dict) else None
    if not isinstance(operation, dict):
        return {"outcome": OUTCOME_NOT_READY, "reason": "MOCK_OPERATION_NOT_STARTED"}
    if document.get("review", {}).get("review_required") is True:
        return {
            "outcome": OUTCOME_REVIEW_REQUIRED,
            "reason": "MOCK_OPERATION_REVIEW_REQUIRED",
            "source_routine_instance_id": clean_text(
                document["review"].get("source_routine_instance_id")
            ),
        }
    positions = _positions(document)
    live = _live_orders(document)
    if live:
        return {
            "outcome": OUTCOME_NOT_READY,
            "reason": "MOCK_OPERATION_ACTIVE_EXECUTION",
            "active_order_ids": [item["mock_order_id"] for item in live],
        }
    method = normalize_close_method(operation.get("close_method"))
    remaining = {
        instance_id: int(item.get("holding_qty", 0) or 0)
        for instance_id, item in positions.items()
        if int(item.get("holding_qty", 0) or 0) > 0
    }
    if method == CLOSE_CARRYOVER:
        if operation.get("long_hold_enabled") is not True:
            return {
                "outcome": OUTCOME_REVIEW_REQUIRED if final_close_boundary else OUTCOME_NOT_READY,
                "reason": "MOCK_LONG_HOLD_NOT_ENABLED",
            }
        return {
            "outcome": OUTCOME_CARRYOVER_DONE,
            "reason": "MOCK_CARRYOVER_QUALIFIED",
            "remaining_by_instance": remaining,
        }
    if not remaining:
        return {"outcome": OUTCOME_DONE, "reason": "MOCK_LIQUIDATION_COMPLETE"}
    culprit = sorted(remaining)[0]
    return {
        "outcome": OUTCOME_REVIEW_REQUIRED if final_close_boundary else OUTCOME_NOT_READY,
        "reason": "MOCK_CLOSE_RESIDUAL" if final_close_boundary else "MOCK_LIQUIDATION_REMAINING",
        "source_routine_instance_id": culprit,
        "remaining_by_instance": remaining,
    }


def mock_validation_end_eligibility(document: dict[str, Any]) -> dict[str, Any]:
    """Read-only eligibility for leaving the Validation domain."""
    if document.get("review", {}).get("review_required") is True:
        return {"eligible": False, "reason": "MOCK_REVIEW_UNRESOLVED"}
    if _live_orders(document):
        return {"eligible": False, "reason": "MOCK_ACTIVE_EXECUTION"}
    if any(int(item.get("holding_qty", 0) or 0) > 0 for item in document.get("positions", ())):
        return {"eligible": False, "reason": "MOCK_POSITION_REMAINS"}
    root = document.get("mock_operation_lifecycle")
    current = root.get("current") if isinstance(root, dict) else None
    if isinstance(current, dict) and current.get("state") in {OPERATION_RUNNING, OPERATION_CLOSING}:
        return {"eligible": False, "reason": "MOCK_OPERATION_ACTIVE"}
    return {"eligible": True, "reason": ""}


class MockOperationLifecycleCoordinator:
    """Deterministic stock-level operation start/close/recovery coordinator."""

    def __init__(
        self,
        repository: MockValidationRepository,
        engine: MockVirtualExecutionEngine,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self._now = now_factory or (lambda: datetime.now().astimezone())

    def _event(
        self,
        document: dict[str, Any],
        *,
        event_type: str,
        identity: str,
        timestamp: str,
        instance_id: str = "",
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        operation = _root(document).get("current")
        common = {
            "operation_session_id": operation.get("operation_session_id", "") if isinstance(operation, dict) else "",
            "trading_date": operation.get("trading_date", "") if isinstance(operation, dict) else "",
            "operation_state": operation.get("state", "") if isinstance(operation, dict) else "",
            "close_reason": operation.get("close_reason", "") if isinstance(operation, dict) else "",
            "close_method": operation.get("close_method", "") if isinstance(operation, dict) else "",
        }
        common.update(deepcopy(payload) if isinstance(payload, dict) else {})
        self.repository.append_event({
            "event_id": deterministic_mock_identity(
                "ME", document["session"]["validation_session_id"], identity, event_type
            ),
            "validation_session_id": document["session"]["validation_session_id"],
            "stock_code": document["session"]["stock_code"],
            "routine_instance_id": clean_text(instance_id),
            "event_type": event_type,
            "timestamp": timestamp,
            "reason_code": clean_text(reason),
            "payload": common,
        })

    def start_stock_operation(
        self,
        session_id: str,
        *,
        trading_date: date | str,
        as_of: datetime,
        operation_policy_snapshot: dict[str, Any] | None = None,
        command_id: str,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        date_text = _date_text(trading_date)
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        current = root.get("current")
        if isinstance(current, dict) and current.get("state") in {OPERATION_RUNNING, OPERATION_CLOSING}:
            if current.get("trading_date") == date_text:
                return {"status": "NOOP", "duplicate": True, "document": before}
            raise MockValidationError("MOCK_OPERATION_ALREADY_ACTIVE")
        if before["session"]["state"] != SESSION_WAITING:
            raise MockValidationError("MOCK_OPERATION_START_STATE_INVALID")
        if before["review"].get("review_required") is True:
            raise MockValidationError("MOCK_OPERATION_REVIEW_UNRESOLVED")
        if any(item.get("trading_date") == date_text for item in root["history"]):
            raise MockValidationError("MOCK_OPERATION_TRADING_DATE_ALREADY_USED")
        timestamp = as_of.isoformat(timespec="microseconds")
        operation_id = deterministic_mock_identity("MS", session_id, date_text, command)
        policy_snapshot = deepcopy(operation_policy_snapshot or {})
        operation = {
            "operation_session_id": operation_id,
            "trading_date": date_text,
            "state": OPERATION_RUNNING,
            "started_at": timestamp,
            "closing_requested_at": "",
            "ended_at": "",
            "close_source": "",
            "close_reason": "",
            "close_method": "",
            "outcome": "",
            "operation_policy_snapshot": policy_snapshot,
            "close_policy_snapshot": None,
            "long_hold_enabled": bool(policy_snapshot.get("long_hold_enabled", False)),
            "immediate_commands": {},
            "liquidation_by_instance": {},
            "processed_cycles": {},
            "pnl_finalization": "OPEN",
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            state["current"] = deepcopy(operation)
            state["commands"][command] = {
                "operation": "START_OPERATION", "applied_at": timestamp,
                "entity_id": operation_id,
            }
            document["session"].update({
                "state": SESSION_RUNNING,
                "started_at": timestamp,
                "ended_at": "",
                "start_identity": operation_id,
            })
            for item in document["instance_execution"].values():
                item.update({
                    "state": SESSION_RUNNING,
                    "started_at": timestamp,
                    "progression_allowed": True,
                    "operation_session_id": operation_id,
                    "operation_started_at": timestamp,
                })
            return document

        result = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )
        document = result["document"]
        self._event(document, event_type="OPERATION_SESSION_CREATED", identity=operation_id, timestamp=timestamp)
        self._event(document, event_type="OPERATION_STARTED", identity=command, timestamp=timestamp)
        return {"status": "STARTED", "duplicate": False, "operation": deepcopy(operation), "document": document}

    def request_normal_close(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_close(session_id, source="NORMAL", **kwargs)

    def request_auto_close(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_close(session_id, source="AUTO", **kwargs)

    def request_early_close(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_close(session_id, source="EARLY", **kwargs)

    def _request_close(
        self,
        session_id: str,
        *,
        source: str,
        method: str,
        reason: str,
        as_of: datetime,
        command_id: str,
        long_hold_enabled: bool | None = None,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        close_method = normalize_close_method(method)
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root.get("current")
        if not isinstance(operation, dict) or operation.get("state") != OPERATION_RUNNING:
            raise MockValidationError("MOCK_OPERATION_CLOSE_STATE_INVALID")
        carry_enabled = operation.get("long_hold_enabled") if long_hold_enabled is None else bool(long_hold_enabled)
        if close_method == CLOSE_CARRYOVER and carry_enabled is not True:
            raise MockValidationError("MOCK_LONG_HOLD_NOT_ENABLED")
        timestamp = as_of.isoformat(timespec="microseconds")

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["current"]
            current.update({
                "state": OPERATION_CLOSING,
                "closing_requested_at": timestamp,
                "close_source": source,
                "close_reason": clean_text(reason),
                "close_method": close_method,
                "long_hold_enabled": carry_enabled,
                "close_policy_snapshot": {
                    "source": source,
                    "method": close_method,
                    "reason": clean_text(reason),
                    "long_hold_enabled": carry_enabled,
                    "captured_at": timestamp,
                },
            })
            state["commands"][command] = {
                "operation": f"{source}_CLOSE", "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            document["session"]["state"] = SESSION_CLOSING
            for item in document["instance_execution"].values():
                item.update({"state": SESSION_CLOSING, "progression_allowed": False})
            return document

        result = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )
        self._event(
            result["document"], event_type=_REQUEST_EVENTS[source],
            identity=command, timestamp=timestamp, reason=reason,
        )
        return {"status": "CLOSING", "duplicate": False, "document": result["document"]}

    def request_immediate_liquidation(
        self,
        session_id: str,
        *,
        as_of: datetime,
        command_id: str,
        source: str = "USER",
        method: str = CLOSE_MARKET,
        reason: str = "",
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        close_method = normalize_close_method(method)
        if close_method == CLOSE_CARRYOVER:
            raise MockValidationError("MOCK_IMMEDIATE_LIQUIDATION_METHOD_INVALID")
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            current = root.get("current") or {}
            saved = current.get("immediate_commands", {}).get(command)
            return {"status": "NOOP", "duplicate": True, "command": deepcopy(saved), "document": before}
        operation = root.get("current")
        if not isinstance(operation, dict) or operation.get("state") not in {OPERATION_RUNNING, OPERATION_CLOSING}:
            raise MockValidationError("MOCK_IMMEDIATE_LIQUIDATION_STATE_INVALID")
        timestamp = as_of.isoformat(timespec="microseconds")
        request = {
            "command_id": command,
            "operation_session_id": operation["operation_session_id"],
            "requested_at": timestamp,
            "source": clean_text(source),
            "target_stock_code": before["session"]["stock_code"],
            "method": close_method,
            "status": "REQUESTED",
            "reason": clean_text(reason),
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["current"]
            current["immediate_commands"][command] = deepcopy(request)
            current.update({
                "state": OPERATION_CLOSING,
                "closing_requested_at": current.get("closing_requested_at") or timestamp,
                "close_source": "IMMEDIATE",
                "close_reason": clean_text(reason),
                "close_method": close_method,
                "close_policy_snapshot": {
                    "source": "IMMEDIATE",
                    "method": close_method,
                    "reason": clean_text(reason),
                    "captured_at": timestamp,
                },
            })
            state["commands"][command] = {
                "operation": "IMMEDIATE_LIQUIDATION", "applied_at": timestamp,
                "entity_id": command,
            }
            document["session"]["state"] = SESSION_CLOSING
            for item in document["instance_execution"].values():
                item.update({"state": SESSION_CLOSING, "progression_allowed": False})
            return document

        result = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )
        self._event(
            result["document"], event_type="IMMEDIATE_LIQUIDATION_REQUESTED",
            identity=command, timestamp=timestamp, reason=reason,
            payload={"command_id": command, "source": source, "method": close_method},
        )
        return {"status": "REQUESTED", "duplicate": False, "command": request, "document": result["document"]}

    def resume_stock_operation(
        self,
        session_id: str,
        *,
        as_of: datetime,
        command_id: str,
        resolution: str,
    ) -> dict[str, Any]:
        """Resume a reviewed stock as one unit and continue its close intent."""
        if as_of.tzinfo is None or not clean_text(resolution):
            raise MockValidationError("MOCK_OPERATION_RESUME_INPUT_INVALID")
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root.get("current")
        if (
            not isinstance(operation, dict)
            or operation.get("state") != OPERATION_REVIEW_STOPPED
            or before["session"].get("state") != SESSION_REVIEW_STOPPED
            or before["review"].get("review_required") is not True
        ):
            raise MockValidationError("MOCK_OPERATION_RESUME_STATE_INVALID")
        if not clean_text(operation.get("close_method")):
            raise MockValidationError("MOCK_OPERATION_RESUME_CLOSE_INTENT_MISSING")
        timestamp = as_of.isoformat(timespec="microseconds")

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["current"]
            current.update({
                "state": OPERATION_CLOSING,
                "outcome": "",
                "resumed_at": timestamp,
                "resume_resolution": clean_text(resolution),
            })
            state["commands"][command] = {
                "operation": "RESUME_OPERATION",
                "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            document["session"]["state"] = SESSION_CLOSING
            document["review"].update({
                "review_required": False,
                "resolved_at": timestamp,
                "resolution": clean_text(resolution),
            })
            for item in document["instance_execution"].values():
                item.update({"state": SESSION_CLOSING, "progression_allowed": False})
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document, event_type="OPERATION_RESUMED", identity=command,
            timestamp=timestamp, payload={"resolution": clean_text(resolution)},
        )
        return {"status": "CLOSING", "duplicate": False, "document": document}

    @staticmethod
    def _market_ready(
        market: MockMarketSnapshot | None,
        policy: MockExecutionPolicy,
        as_of: datetime,
        *,
        require_trade: bool,
    ) -> tuple[bool, str, float | None]:
        if market is None or market.orderbook is None:
            return False, "MOCK_ORDERBOOK_UNAVAILABLE", None
        book = market.orderbook
        received = _aware(book.received_at)
        if (
            received is None
            or book.connection_epoch != policy.connection_epoch
            or book.login_session_id != policy.login_session_id
        ):
            return False, "MOCK_MARKET_SESSION_INVALID", None
        if (as_of - received).total_seconds() > float(policy.max_orderbook_age_seconds):
            return False, "MOCK_ORDERBOOK_STALE", None
        if not require_trade:
            return True, "", None
        trade = market.trade
        trade_received = _aware(trade.received_at) if trade is not None else None
        if trade is None or trade_received is None:
            return False, "MOCK_CURRENT_PRICE_UNAVAILABLE", None
        if (
            trade.connection_epoch != policy.connection_epoch
            or trade.login_session_id != policy.login_session_id
            or (as_of - trade_received).total_seconds() > float(policy.max_trade_age_seconds)
        ):
            return False, "MOCK_CURRENT_PRICE_STALE", None
        try:
            price = float(trade.current_price)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            return False, "MOCK_CURRENT_PRICE_UNAVAILABLE", None
        return True, "", int(price) if price.is_integer() else price

    def process_mock_operation_cycle(
        self,
        session_id: str,
        *,
        lifecycle_cycle_id: str,
        as_of: datetime,
        market: MockMarketSnapshot | None,
        policy: MockExecutionPolicy,
        final_close_boundary: bool = False,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        cycle_id = clean_text(lifecycle_cycle_id)
        before = self.repository.read_session(session_id)
        try:
            _validate_operation_integrity(before)
        except MockValidationError as exc:
            return self._review_structural(session_id, cycle_id, as_of, exc)
        root = _root(before)
        operation = root.get("current")
        if not isinstance(operation, dict):
            return {"status": "NOOP", "reason": "MOCK_OPERATION_NOT_STARTED", "document": before}
        if cycle_id in operation["processed_cycles"]:
            return {"status": "NOOP", "reason": "MOCK_OPERATION_CYCLE_ALREADY_PROCESSED", "document": before}
        if operation.get("state") == OPERATION_ENDED:
            return {"status": "NOOP", "reason": "MOCK_OPERATION_ALREADY_ENDED", "document": before}
        if operation.get("state") == OPERATION_REVIEW_STOPPED or before["review"].get("review_required") is True:
            return {"status": "REVIEW_STOPPED", "reason": "MOCK_OPERATION_REVIEW_STOPPED", "document": before}
        if operation.get("state") != OPERATION_CLOSING:
            return {"status": "WAIT", "reason": "MOCK_OPERATION_NOT_CLOSING", "document": before}
        timestamp = as_of.isoformat(timespec="microseconds")
        live = _live_orders(before)
        pending = [item for item in live if item.get("state") == ORDER_CANCEL_PENDING]
        if pending:
            order = pending[0]
            result = self.engine.finalize_cancel(
                session_id, order["mock_order_id"],
                command_id=deterministic_mock_identity("MC", session_id, cycle_id, order["mock_order_id"], "CANCEL_EFFECT"),
                allow_closing=True,
            )
            return self._finish_cycle(session_id, cycle_id, timestamp, "CANCEL_EFFECT", result)

        close_prefix = f"MOCK_CLOSE:{operation['operation_session_id']}:"
        close_orders = [item for item in live if clean_text(item.get("child_identity")).startswith(close_prefix)]
        other_orders = [item for item in live if item not in close_orders]
        if other_orders:
            order = other_orders[0]
            result = self.engine.request_cancel(
                session_id, order["mock_order_id"],
                command_id=deterministic_mock_identity("MC", session_id, cycle_id, order["mock_order_id"], "CLOSE_CANCEL"),
                allow_closing=True,
            )
            return self._finish_cycle(session_id, cycle_id, timestamp, "CANCEL_REQUEST", result)

        if close_orders:
            if final_close_boundary:
                return self._review_residual(session_id, operation, close_orders[0], cycle_id, as_of, market)
            ready, reason, _ = self._market_ready(
                market, policy, as_of,
                require_trade=operation.get("close_method") == CLOSE_CURRENT_PRICE,
            )
            if not ready:
                return self._finish_cycle(session_id, cycle_id, timestamp, "WAIT", {"status": "WAIT", "reason": reason})
            order = close_orders[0]
            result = self.engine.process_orderbook(
                session_id, order["mock_order_id"], market=market, policy=policy,
                command_id=deterministic_mock_identity("MC", session_id, cycle_id, order["mock_order_id"], "LIQUIDATION_PROGRESS"),
                allow_closing=True,
            )
            current = result.get("order") if isinstance(result.get("order"), dict) else order
            document = self._finish_cycle(session_id, cycle_id, timestamp, "LIQUIDATION_PROGRESS", result)["document"]
            self._event(
                document, event_type="LIQUIDATION_PROGRESS", identity=cycle_id,
                timestamp=timestamp, instance_id=current.get("routine_instance_id", ""),
                payload={
                    "mock_order_id": current.get("mock_order_id"),
                    "filled_qty": current.get("filled_qty"),
                    "residual_qty": current.get("remaining_qty"),
                    "market_snapshot_identity": market.snapshot_identity if market else "",
                },
            )
            return {"status": "PROGRESSED", "action": "LIQUIDATION_PROGRESS", "document": document, "order": current}

        completion = evaluate_mock_operation_completion(before, final_close_boundary=final_close_boundary)
        if completion["outcome"] in {OUTCOME_DONE, OUTCOME_CARRYOVER_DONE}:
            return self._complete(session_id, completion, cycle_id, as_of)
        if completion["outcome"] == OUTCOME_REVIEW_REQUIRED:
            culprit = completion.get("source_routine_instance_id") or sorted(before["instance_execution"])[0]
            synthetic = {
                "routine_instance_id": culprit,
                "requested_qty": completion.get("remaining_by_instance", {}).get(culprit, 0),
                "filled_qty": 0,
                "remaining_qty": completion.get("remaining_by_instance", {}).get(culprit, 0),
                "mock_order_id": "",
            }
            return self._review_residual(session_id, operation, synthetic, cycle_id, as_of, market)

        ready, reason, current_price = self._market_ready(
            market, policy, as_of,
            require_trade=operation.get("close_method") == CLOSE_CURRENT_PRICE,
        )
        if not ready:
            return self._finish_cycle(session_id, cycle_id, timestamp, "WAIT", {"status": "WAIT", "reason": reason})
        positions = _positions(before)
        created: list[dict[str, Any]] = []
        for instance_id in sorted(positions):
            qty = int(positions[instance_id].get("available_qty", 0) or 0)
            if qty <= 0:
                continue
            order_type = "MARKET" if operation.get("close_method") == CLOSE_MARKET else "LIMIT"
            result = self.engine.submit_order(
                session_id,
                routine_instance_id=instance_id,
                side="SELL",
                order_type=order_type,
                requested_qty=qty,
                limit_price=None if order_type == "MARKET" else current_price,
                market=market,
                policy=policy,
                generation=0,
                child_identity=f"{close_prefix}{instance_id}",
                command_id=deterministic_mock_identity("MC", session_id, cycle_id, instance_id, "LIQUIDATE"),
                allow_closing=True,
            )
            if result.get("status") == RESULT_BLOCKED:
                raise MockValidationError(clean_text(result.get("reason")) or "MOCK_LIQUIDATION_BLOCKED")
            if isinstance(result.get("order"), dict):
                created.append(deepcopy(result["order"]))
        document = self._finish_cycle(
            session_id, cycle_id, timestamp, "LIQUIDATION_STARTED",
            {"status": "PROGRESSED", "orders": created},
        )["document"]
        for order in created:
            self._event(
                document, event_type="LIQUIDATION_STARTED",
                identity=order["mock_order_id"], timestamp=timestamp,
                instance_id=order["routine_instance_id"],
                payload={
                    "intended_liquidation_qty": order["requested_qty"],
                    "filled_qty": order["filled_qty"],
                    "residual_qty": order["remaining_qty"],
                    "mock_order_id": order["mock_order_id"],
                    "market_snapshot_identity": market.snapshot_identity if market else "",
                },
            )
        return {"status": "PROGRESSED", "action": "LIQUIDATION_STARTED", "orders": created, "document": document}

    def _review_structural(
        self, session_id: str, cycle_id: str, as_of: datetime,
        error: MockValidationError,
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)
        instance_id = sorted(before.get("instance_execution", {}))[0]
        reason = clean_text(error) or "MOCK_OPERATION_INTEGRITY_FAILURE"

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            root = document.get("mock_operation_lifecycle")
            current = root.get("current") if isinstance(root, dict) else None
            if isinstance(current, dict):
                current.update({
                    "state": OPERATION_REVIEW_STOPPED,
                    "outcome": OUTCOME_REVIEW_REQUIRED,
                    "integrity_error": reason,
                })
                cycles = current.get("processed_cycles")
                if isinstance(cycles, dict):
                    cycles[cycle_id] = {
                        "action": OUTCOME_REVIEW_REQUIRED,
                        "recorded_at": timestamp,
                        "reason": reason,
                    }
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        service = MockValidationSessionService(self.repository, now_factory=lambda: timestamp)
        stopped = service.stop_for_instance_error(
            session_id,
            source_routine_instance_id=instance_id,
            reason_code="MOCK_OPERATION_INTEGRITY_FAILURE",
            reason=reason,
            command_id=deterministic_mock_identity("MC", session_id, cycle_id, "OPERATION_INTEGRITY"),
        )
        self._event(
            stopped["document"], event_type="OPERATION_REVIEW_STOPPED",
            identity=cycle_id, timestamp=timestamp, instance_id=instance_id,
            reason="MOCK_OPERATION_INTEGRITY_FAILURE",
            payload={"error": reason},
        )
        return {
            "status": OUTCOME_REVIEW_REQUIRED,
            "action": OUTCOME_REVIEW_REQUIRED,
            "reason": reason,
            "document": stopped["document"],
        }

    def _finish_cycle(
        self, session_id: str, cycle_id: str, timestamp: str,
        action: str, result: dict[str, Any],
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            operation = _root(document)["current"]
            operation["processed_cycles"][cycle_id] = {
                "action": action,
                "recorded_at": timestamp,
                "reason": clean_text(result.get("reason")),
            }
            return document

        saved = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        return {
            "status": clean_text(result.get("status")) or "PROGRESSED",
            "reason": clean_text(result.get("reason")),
            "action": action,
            "document": saved,
        }

    def _complete(
        self,
        session_id: str,
        completion: dict[str, Any],
        cycle_id: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        outcome = completion["outcome"]
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            root = _root(document)
            operation = root["current"]
            operation.update({
                "state": OPERATION_ENDED,
                "ended_at": timestamp,
                "outcome": outcome,
                "pnl_finalization": "FINAL" if outcome == OUTCOME_DONE else "DEFERRED_CARRYOVER",
            })
            operation["processed_cycles"][cycle_id] = {
                "action": outcome, "recorded_at": timestamp, "reason": completion["reason"],
            }
            for command in operation.get("immediate_commands", {}).values():
                if command.get("status") != "COMPLETED":
                    command.update({"status": "COMPLETED", "completed_at": timestamp})
            if not any(item.get("operation_session_id") == operation["operation_session_id"] for item in root["history"]):
                root["history"].append(deepcopy(operation))
            document["session"].update({"state": SESSION_WAITING, "started_at": "", "start_identity": ""})
            for instance_id, item in document["instance_execution"].items():
                item.update({
                    "state": SESSION_WAITING,
                    "progression_allowed": False,
                    "last_operation_session_id": operation["operation_session_id"],
                    "operation_session_id": "",
                })
                if outcome == OUTCOME_DONE:
                    cycle = document["cycle_state_by_instance"].get(instance_id)
                    if isinstance(cycle, dict) and cycle.get("active") is True:
                        cycle.update({"active": False, "completed_at": timestamp})
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        operation = _root(document)["current"]
        if outcome == OUTCOME_CARRYOVER_DONE:
            self._event(document, event_type="LONG_HOLD_SELECTED", identity=cycle_id, timestamp=timestamp)
            self._event(document, event_type="CARRYOVER_CONFIRMED", identity=cycle_id, timestamp=timestamp)
            result_event = "OPERATION_CARRYOVER_DONE"
        else:
            self._event(document, event_type="LIQUIDATION_COMPLETED", identity=cycle_id, timestamp=timestamp)
            result_event = "OPERATION_DONE"
        self._event(document, event_type=result_event, identity=operation["operation_session_id"], timestamp=timestamp)
        self._event(document, event_type="OPERATION_SESSION_ENDED", identity=cycle_id, timestamp=timestamp)
        return {"status": outcome, "action": outcome, "completion": completion, "document": document}

    def _review_residual(
        self,
        session_id: str,
        operation: dict[str, Any],
        order: dict[str, Any],
        cycle_id: str,
        as_of: datetime,
        market: MockMarketSnapshot | None,
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        instance_id = clean_text(order.get("routine_instance_id"))
        before = self.repository.read_session(session_id)
        position = _positions(before)[instance_id]
        residual = int(position.get("holding_qty", 0) or 0)
        evidence = {
            "operation_session_id": operation["operation_session_id"],
            "routine_instance_id": instance_id,
            "intended_close_method": operation.get("close_method"),
            "intended_liquidation_qty": int(order.get("requested_qty", 0) or 0),
            "filled_qty": int(order.get("filled_qty", 0) or 0),
            "residual_qty": residual,
            "mock_order_id": clean_text(order.get("mock_order_id")),
            "market_snapshot_identity": market.snapshot_identity if market else "",
            "reason": "MOCK_CLOSE_RESIDUAL",
            "occurred_at": timestamp,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = _root(document)["current"]
            current.update({"state": OPERATION_REVIEW_STOPPED, "outcome": OUTCOME_REVIEW_REQUIRED})
            current["processed_cycles"][cycle_id] = {
                "action": OUTCOME_REVIEW_REQUIRED, "recorded_at": timestamp,
                "reason": "MOCK_CLOSE_RESIDUAL",
            }
            current.setdefault("residual_reviews", []).append(deepcopy(evidence))
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document, event_type="CLOSE_RESIDUAL_DETECTED", identity=cycle_id,
            timestamp=timestamp, instance_id=instance_id,
            reason="MOCK_CLOSE_RESIDUAL", payload=evidence,
        )
        service = MockValidationSessionService(
            self.repository, now_factory=lambda: timestamp
        )
        stopped = service.stop_for_instance_error(
            session_id,
            source_routine_instance_id=instance_id,
            reason_code="MOCK_CLOSE_RESIDUAL",
            reason="Mock liquidation left an unresolved residual position",
            command_id=deterministic_mock_identity("MC", session_id, cycle_id, "RESIDUAL_REVIEW"),
        )
        self._event(
            stopped["document"], event_type="OPERATION_REVIEW_STOPPED",
            identity=cycle_id, timestamp=timestamp, instance_id=instance_id,
            reason="MOCK_CLOSE_RESIDUAL", payload=evidence,
        )
        return {
            "status": OUTCOME_REVIEW_REQUIRED,
            "action": OUTCOME_REVIEW_REQUIRED,
            "reason": "MOCK_CLOSE_RESIDUAL",
            "evidence": evidence,
            "document": stopped["document"],
        }


__all__ = [
    "CLOSE_CARRYOVER",
    "CLOSE_CURRENT_PRICE",
    "CLOSE_MARKET",
    "MockOperationLifecycleCoordinator",
    "OPERATION_CLOSING",
    "OPERATION_ENDED",
    "OPERATION_REVIEW_STOPPED",
    "OPERATION_RUNNING",
    "OUTCOME_CARRYOVER_DONE",
    "OUTCOME_DONE",
    "OUTCOME_NOT_READY",
    "OUTCOME_REVIEW_REQUIRED",
    "evaluate_mock_operation_completion",
    "mock_validation_end_eligibility",
    "normalize_close_method",
]
