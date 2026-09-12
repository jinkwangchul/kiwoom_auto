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

from manual_ats_runtime import PROGRAM_SESSION_ID

from mock_validation_contract import (
    INSTANCE_ERROR,
    INSTANCE_VALIDATION_STOPPED,
    ORDER_CANCEL_PENDING,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_TERMINAL_STATES,
    SESSION_CLOSING,
    SESSION_ENDED,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    instance_individual_liquidation_reservation,
    validate_instance_individual_liquidation_reservation,
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
OPERATION_VALIDATION_STOPPED = "VALIDATION_STOPPED"

OUTCOME_DONE = "DONE"
OUTCOME_CARRYOVER_DONE = "CARRYOVER_DONE"
OUTCOME_NOT_READY = "NOT_READY"
OUTCOME_REVIEW_REQUIRED = "REVIEW_REQUIRED"

CLOSE_MARKET = "MARKET"
CLOSE_CURRENT_PRICE = "CURRENT_PRICE"
CLOSE_CARRYOVER = "CARRYOVER"
CLOSE_ROUTINE = "ROUTINE"
CLOSE_PROFIT_LOSS = "PROFIT_LOSS"

_LIVE = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
_REQUEST_EVENTS = {
    "NORMAL": "NORMAL_CLOSE_REQUESTED",
    "AUTO": "AUTO_CLOSE_REQUESTED",
    "EARLY": "EARLY_CLOSE_REQUESTED",
    "LIQUIDATION": "LIQUIDATION_STARTED",
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


def _clock_seconds(value: Any, default: str = "") -> int | None:
    text = clean_text(value) or clean_text(default)
    parts = text.split(":")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3:
        return None
    try:
        hour, minute, second = (int(part) for part in parts)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 3600 + minute * 60 + second


def _mock_early_close_time_reason(
    operation: dict[str, Any],
    as_of: datetime,
) -> tuple[str, dict[str, Any]]:
    snapshot = operation.get("operation_policy_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    settings = snapshot.get("mock_instance_effective_settings")
    settings = settings if isinstance(settings, dict) else {}
    regular = snapshot.get("regular_market")
    regular = regular if isinstance(regular, dict) else {}
    liquidation = snapshot.get("liquidation")
    liquidation = liquidation if isinstance(liquidation, dict) else {}
    individual = operation.get("individual_liquidation_time_snapshot")
    individual = individual if isinstance(individual, dict) else {}
    end_seconds = _clock_seconds(regular.get("end_time"), "15:20:00")
    start_seconds = _clock_seconds(regular.get("start_time"), "09:00:00")
    try:
        minutes = int(
            individual.get("minutes_before_regular_close")
            if individual
            else liquidation.get("minutes_before_regular_close", 5)
        )
    except (TypeError, ValueError):
        minutes = 5
    liquidation_start = (
        max(0, end_seconds - max(1, minutes) * 60)
        if end_seconds is not None
        else None
    )
    current_seconds = as_of.hour * 3600 + as_of.minute * 60 + as_of.second
    evidence = {
        "operation_identity": clean_text(operation.get("operation_session_id")),
        "operation_mode": clean_text(
            snapshot.get("operation_mode") or settings.get("operation_mode")
        ).upper(),
        "regular_start_seconds": start_seconds,
        "regular_end_seconds": end_seconds,
        "liquidation_start_seconds": liquidation_start,
    }
    if start_seconds is None or liquidation_start is None:
        return "MOCK_OPERATION_TIME_POLICY_INVALID", evidence
    if current_seconds < start_seconds or current_seconds >= liquidation_start:
        return "OUTSIDE_REGULAR_MARKET", evidence
    if evidence["operation_mode"] == "SCHEDULED":
        schedule = settings.get("operation_schedule")
        schedule = schedule if isinstance(schedule, dict) else settings
        operation_start = _clock_seconds(schedule.get("start_time"), "09:00:00")
        operation_end = _clock_seconds(schedule.get("end_buy_time"), "13:30:00")
        evidence.update(
            {
                "operation_start_seconds": operation_start,
                "operation_end_seconds": operation_end,
            }
        )
        if (
            operation_start is None
            or operation_end is None
            or current_seconds < operation_start
            or current_seconds > operation_end
        ):
            return "SCHEDULED_OPERATION_WINDOW_ENDED", evidence
    return "", evidence


def normalize_close_method(value: Any) -> str:
    text = clean_text(value).upper().replace(" ", "_")
    aliases = {
        "MARKET": CLOSE_MARKET,
        "MARKET_ORDER": CLOSE_MARKET,
        "시장가": CLOSE_MARKET,
        "시장가즉시": CLOSE_MARKET,
        "CURRENT_PRICE": CLOSE_CURRENT_PRICE,
        "현재가": CLOSE_CURRENT_PRICE,
        "현재가즉시": CLOSE_CURRENT_PRICE,
        "CARRYOVER": CLOSE_CARRYOVER,
        "LONG_HOLD": CLOSE_CARRYOVER,
        "이월": CLOSE_CARRYOVER,
        "장기보유": CLOSE_CARRYOVER,
        "ROUTINE": CLOSE_ROUTINE,
        "ROUTINE_SIGNAL": CLOSE_ROUTINE,
        "루틴": CLOSE_ROUTINE,
        "루틴마감": CLOSE_ROUTINE,
        "루틴매도신호": CLOSE_ROUTINE,
        "PROFIT_LOSS": CLOSE_PROFIT_LOSS,
        "PROFIT/LOSS": CLOSE_PROFIT_LOSS,
        "손/익절": CLOSE_PROFIT_LOSS,
        "익절/손절": CLOSE_PROFIT_LOSS,
    }
    result = aliases.get(text)
    if result is None:
        raise MockValidationError("MOCK_OPERATION_CLOSE_METHOD_INVALID")
    return result


def _instance_liquidation_execution_method(operation: dict[str, Any]) -> str:
    method = clean_text(operation.get("liquidation_execution_method")).upper()
    return method or clean_text(operation.get("close_method")).upper()


def _root(document: dict[str, Any]) -> dict[str, Any]:
    root = document.setdefault(
        "mock_operation_lifecycle",
        {
            "version": 1,
            "current": None,
            "history": [],
            "commands": {},
            "instance_operations": {},
        },
    )
    if (
        not isinstance(root, dict)
        or not isinstance(root.get("history"), list)
        or not isinstance(root.get("commands"), dict)
    ):
        raise MockValidationError("MOCK_OPERATION_STATE_INVALID")
    instance_operations = root.setdefault("instance_operations", {})
    if not isinstance(instance_operations, dict):
        raise MockValidationError("MOCK_INSTANCE_OPERATION_LEDGER_INVALID")
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
    for instance_id, operation in root["instance_operations"].items():
        if instance_id not in document.get("instance_execution", {}):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        if not isinstance(operation, dict):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_INVALID")
        if clean_text(operation.get("routine_instance_id")) != instance_id:
            raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        if not clean_text(operation.get("operation_session_id")).startswith("MS-"):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_INVALID")
        if operation.get("state") not in {
            OPERATION_RUNNING,
            OPERATION_CLOSING,
            OPERATION_ENDED,
            OPERATION_REVIEW_STOPPED,
            OPERATION_VALIDATION_STOPPED,
        }:
            raise MockValidationError("MOCK_INSTANCE_OPERATION_STATE_INVALID")
        if not isinstance(operation.get("processed_cycles"), dict):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_CYCLE_LEDGER_INVALID")
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
        OPERATION_RUNNING, OPERATION_CLOSING, OPERATION_ENDED,
        OPERATION_REVIEW_STOPPED, OPERATION_VALIDATION_STOPPED,
    }:
        raise MockValidationError("MOCK_OPERATION_STATE_INVALID")
    _date_text(current.get("trading_date"))
    if not isinstance(current.get("processed_cycles"), dict):
        raise MockValidationError("MOCK_OPERATION_CYCLE_LEDGER_INVALID")
    if not isinstance(current.get("immediate_commands"), dict):
        raise MockValidationError("MOCK_IMMEDIATE_COMMAND_LEDGER_INVALID")


def _live_orders(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in document.get("orders", ()) if item.get("state") in _LIVE]


def _instance_live_orders(
    document: dict[str, Any], routine_instance_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in _live_orders(document)
        if clean_text(item.get("routine_instance_id")) == routine_instance_id
    ]


def _instance_pending_position_execution(
    document: dict[str, Any], routine_instance_id: str
) -> bool:
    instance_id = clean_text(routine_instance_id)
    if any(
        item.get("state") not in ORDER_TERMINAL_STATES
        for item in document.get("orders", ())
        if isinstance(item, dict)
        and clean_text(item.get("routine_instance_id")) == instance_id
    ):
        return True
    progression = document.get("progression_by_instance", {}).get(instance_id)
    if not isinstance(progression, dict):
        return False
    adapter = progression.get("indicator_follow_mock_adapter")
    if isinstance(adapter, dict):
        plans = adapter.get("plans")
        if isinstance(plans, list) and any(
            isinstance(item, dict)
            and clean_text(item.get("state")).upper()
            in {"ACTIVE", "PAUSED_FOR_RECOVERY"}
            for item in plans
        ):
            return True
        if isinstance(adapter.get("pending_successor"), dict):
            return True
    continuation = progression.get("indicator_follow_mock_continuation")
    if isinstance(continuation, dict):
        if isinstance(continuation.get("pending_reset"), dict):
            return True
        pending_recoveries = continuation.get("pending_recoveries")
        if isinstance(pending_recoveries, list) and pending_recoveries:
            return True
    return False


def instance_operation_state(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any] | None:
    """Return a defensive Mock-only Instance operation snapshot."""

    instance_id = clean_text(routine_instance_id)
    root = document.get("mock_operation_lifecycle")
    operations = root.get("instance_operations") if isinstance(root, dict) else None
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    return deepcopy(operation) if isinstance(operation, dict) else None


def _sync_instance_container_state(document: dict[str, Any]) -> None:
    """Keep the stock Session as a container, never as an Instance owner."""

    root = _root(document)
    current = root.get("current")
    if isinstance(current, dict) and current.get("state") in {
        OPERATION_RUNNING,
        OPERATION_CLOSING,
        OPERATION_REVIEW_STOPPED,
    }:
        return
    active = any(
        isinstance(item, dict)
        and item.get("state") in {OPERATION_RUNNING, OPERATION_CLOSING}
        for item in root["instance_operations"].values()
    )
    if document["session"].get("state") != SESSION_REVIEW_STOPPED:
        document["session"]["state"] = SESSION_RUNNING if active else SESSION_WAITING
        if not active:
            document["session"].update(
                {"started_at": "", "start_identity": ""}
            )


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
        if remaining and operation.get("long_hold_enabled") is False:
            return {
                "outcome": OUTCOME_REVIEW_REQUIRED,
                "reason": "MOCK_LONG_HOLD_DISABLED",
                "remaining_by_instance": remaining,
            }
        return {
            "outcome": OUTCOME_CARRYOVER_DONE,
            "reason": "MOCK_CARRYOVER_QUALIFIED",
            "remaining_by_instance": remaining,
        }
    if not remaining:
        return {"outcome": OUTCOME_DONE, "reason": "MOCK_LIQUIDATION_COMPLETE"}
    if final_close_boundary:
        return {
            "outcome": OUTCOME_REVIEW_REQUIRED,
            "reason": "MOCK_LIQUIDATION_RESIDUAL",
            "remaining_by_instance": remaining,
        }
    return {
        "outcome": OUTCOME_NOT_READY,
        "reason": "MOCK_LIQUIDATION_REMAINING",
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
    instance_operations = root.get("instance_operations") if isinstance(root, dict) else None
    if isinstance(instance_operations, dict) and any(
        isinstance(item, dict)
        and item.get("state") in {OPERATION_RUNNING, OPERATION_CLOSING}
        for item in instance_operations.values()
    ):
        return {"eligible": False, "reason": "MOCK_INSTANCE_OPERATION_ACTIVE"}
    return {"eligible": True, "reason": ""}


class MockOperationLifecycleCoordinator:
    """Deterministic stock-level operation start/close/recovery coordinator."""

    def __init__(
        self,
        repository: MockValidationRepository,
        engine: MockVirtualExecutionEngine,
        *,
        now_factory: Callable[[], datetime] | None = None,
        program_session_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self._now = now_factory or (lambda: datetime.now().astimezone())
        self._program_session_id = clean_text(
            program_session_id or PROGRAM_SESSION_ID
        )

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

    def record_instance_action_block(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        event_type: str,
        reason_code: str,
        as_of: datetime,
        command_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append one Mock-owned operator block without mutating lifecycle state."""

        document = self.repository.read_session(session_id)
        instance_id = clean_text(routine_instance_id)
        operation = _root(document)["instance_operations"].get(instance_id)
        operation = operation if isinstance(operation, dict) else {}
        self._event(
            document,
            event_type=event_type,
            identity=clean_text(command_id),
            timestamp=as_of.isoformat(timespec="microseconds"),
            instance_id=instance_id,
            reason=reason_code,
            payload={
                "instance_operation": True,
                "operation_identity": clean_text(
                    operation.get("operation_session_id")
                ),
                "current_lifecycle_state": clean_text(operation.get("state")),
                "result": "BLOCKED",
                **(deepcopy(payload) if isinstance(payload, dict) else {}),
            },
        )

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
            "long_hold_enabled": bool(policy_snapshot.get("long_hold_enabled", True)),
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
                if item.get("state") in {INSTANCE_ERROR, INSTANCE_VALIDATION_STOPPED}:
                    continue
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

    def start_instance_operation(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        trading_date: date | str,
        as_of: datetime,
        operation_policy_snapshot: dict[str, Any] | None = None,
        command_id: str,
    ) -> dict[str, Any]:
        """Start exactly one Mock Routine Instance inside its stock container."""

        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        date_text = _date_text(trading_date)
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        instance_id = clean_text(routine_instance_id)
        if instance_id not in before.get("instance_execution", {}):
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
        if before["session"].get("state") in {SESSION_REVIEW_STOPPED}:
            raise MockValidationError("MOCK_INSTANCE_OPERATION_START_STATE_INVALID")
        if before["session"].get("state") not in {SESSION_WAITING, SESSION_RUNNING}:
            raise MockValidationError("MOCK_INSTANCE_OPERATION_START_STATE_INVALID")
        execution = before["instance_execution"][instance_id]
        if execution.get("state") == INSTANCE_ERROR:
            raise MockValidationError("MOCK_INSTANCE_ERROR_STOPPED")
        root = _root(before)
        stock_operation = root.get("current")
        if isinstance(stock_operation, dict) and stock_operation.get("state") in {
            OPERATION_RUNNING,
            OPERATION_CLOSING,
        }:
            raise MockValidationError("MOCK_STOCK_OPERATION_ALREADY_ACTIVE")
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        previous = root["instance_operations"].get(instance_id)
        if isinstance(previous, dict):
            if previous.get("state") in {OPERATION_RUNNING, OPERATION_CLOSING}:
                return {"status": "NOOP", "duplicate": True, "document": before}
            if previous.get("state") == OPERATION_ENDED:
                raise MockValidationError("MOCK_INSTANCE_OPERATION_ENDED")
        timestamp = as_of.isoformat(timespec="microseconds")
        operation_id = deterministic_mock_identity(
            "MS", session_id, instance_id, date_text, command
        )
        pending_reservation = instance_individual_liquidation_reservation(
            before,
            instance_id,
            program_session_id=self._program_session_id,
        )
        bound_individual_snapshot = None
        if pending_reservation is not None:
            bound_individual_snapshot = {
                "operation_session_id": operation_id,
                "minutes_before_regular_close": (
                    pending_reservation["minutes_before_regular_close"]
                ),
                "method": pending_reservation["method"],
                "captured_at": timestamp,
                "reserved_at": pending_reservation["reserved_at"],
                "reservation_command_id": pending_reservation["command_id"],
            }
        operation = {
            "operation_session_id": operation_id,
            "routine_instance_id": instance_id,
            "trading_date": date_text,
            "state": OPERATION_RUNNING,
            "started_at": timestamp,
            "closing_requested_at": "",
            "ended_at": "",
            "close_source": "",
            "close_reason": "",
            "close_method": "",
            "outcome": "",
            "operation_policy_snapshot": deepcopy(operation_policy_snapshot or {}),
            "close_policy_snapshot": None,
            "close_pending": False,
            "final_sell_evidence": None,
            "early_close_cancel_evidence": None,
            "individual_liquidation_time_snapshot": deepcopy(
                bound_individual_snapshot
            ),
            "processed_cycles": {},
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            state["instance_operations"][instance_id] = deepcopy(operation)
            state["commands"][command] = {
                "operation": "START_INSTANCE_OPERATION",
                "applied_at": timestamp,
                "entity_id": operation_id,
            }
            document["session"].update(
                {
                    "state": SESSION_RUNNING,
                    "started_at": document["session"].get("started_at") or timestamp,
                    "ended_at": "",
                    "start_identity": document["session"].get("start_identity")
                    or operation_id,
                }
            )
            document["instance_execution"][instance_id].update(
                {
                    "state": SESSION_RUNNING,
                    "started_at": timestamp,
                    "progression_allowed": True,
                    "operation_session_id": operation_id,
                    "operation_started_at": timestamp,
                }
            )
            reservations = document.get(
                "individual_liquidation_reservations_by_instance"
            )
            if isinstance(reservations, dict):
                reservations.pop(instance_id, None)
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type="OPERATION_SESSION_CREATED",
            identity=operation_id,
            timestamp=timestamp,
            instance_id=instance_id,
            payload={"instance_operation": True},
        )
        self._event(
            document,
            event_type="OPERATION_STARTED",
            identity=command,
            timestamp=timestamp,
            instance_id=instance_id,
            payload={"instance_operation": True},
        )
        return {
            "status": "STARTED",
            "duplicate": False,
            "operation": deepcopy(operation),
            "document": document,
        }

    def request_instance_early_close(
        self, session_id: str, *, routine_instance_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._request_instance_close(
            session_id,
            routine_instance_id=routine_instance_id,
            source="EARLY",
            **kwargs,
        )

    def request_instance_auto_close(
        self, session_id: str, *, routine_instance_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._request_instance_close(
            session_id,
            routine_instance_id=routine_instance_id,
            source="AUTO",
            **kwargs,
        )

    @staticmethod
    def _profit_loss_snapshot(
        method: str,
        profit_percent: Any,
        loss_percent: Any,
    ) -> dict[str, float]:
        if method != CLOSE_PROFIT_LOSS:
            return {}
        result: dict[str, float] = {}
        for key, raw in (
            ("profit_percent", profit_percent),
            ("loss_percent", loss_percent),
        ):
            if raw in (None, ""):
                continue
            try:
                value = abs(float(raw))
            except (TypeError, ValueError) as exc:
                raise MockValidationError("MOCK_PROFIT_LOSS_THRESHOLD_INVALID") from exc
            if value <= 0:
                raise MockValidationError("MOCK_PROFIT_LOSS_THRESHOLD_INVALID")
            result[key] = value
        if not result:
            raise MockValidationError("MOCK_PROFIT_LOSS_THRESHOLD_REQUIRED")
        return result

    def request_instance_immediate_liquidation(
        self, session_id: str, *, routine_instance_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        method = normalize_close_method(kwargs.get("method"))
        if method == CLOSE_CARRYOVER:
            raise MockValidationError("MOCK_IMMEDIATE_LIQUIDATION_METHOD_INVALID")
        kwargs["method"] = method
        return self._request_instance_close(
            session_id,
            routine_instance_id=routine_instance_id,
            source="IMMEDIATE",
            **kwargs,
        )

    def request_instance_individual_liquidation(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        minutes_before_regular_close: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        as_of = kwargs.get("as_of")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        close_method = normalize_close_method(kwargs.get("method"))
        if close_method not in {CLOSE_MARKET, CLOSE_CURRENT_PRICE, CLOSE_CARRYOVER}:
            raise MockValidationError("MOCK_INDIVIDUAL_LIQUIDATION_METHOD_INVALID")
        if close_method == CLOSE_CARRYOVER:
            minutes: int | str = ""
        else:
            try:
                minutes = int(str(minutes_before_regular_close).strip() or "5")
            except (TypeError, ValueError) as exc:
                raise MockValidationError("MOCK_INDIVIDUAL_LIQUIDATION_TIME_INVALID") from exc
            if minutes <= 0:
                raise MockValidationError("MOCK_INDIVIDUAL_LIQUIDATION_TIME_INVALID")
        instance_id = clean_text(routine_instance_id)
        command = clean_text(kwargs.get("command_id"))
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root["instance_operations"].get(instance_id)
        active_operation = bool(
            isinstance(operation, dict) and operation.get("state") in {
            OPERATION_RUNNING,
            OPERATION_CLOSING,
            }
        )
        execution = before.get("instance_execution", {}).get(instance_id)
        stock_operation = root.get("current")
        pre_operation = bool(
            not active_operation
            and isinstance(execution, dict)
            and clean_text(execution.get("state")).upper()
            in {SESSION_WAITING, INSTANCE_VALIDATION_STOPPED}
            and clean_text(before.get("session", {}).get("state")).upper()
            in {SESSION_WAITING, SESSION_RUNNING}
            and not (
                isinstance(stock_operation, dict)
                and stock_operation.get("state")
                in {OPERATION_RUNNING, OPERATION_CLOSING}
            )
        )
        if not active_operation and not pre_operation:
            raise MockValidationError("MOCK_INSTANCE_OPERATION_CLOSE_STATE_INVALID")
        timestamp = as_of.isoformat(timespec="microseconds")
        if pre_operation:
            reservation = validate_instance_individual_liquidation_reservation(
                {
                    "version": 1,
                    "routine_instance_id": instance_id,
                    "reservation_scope": "NEXT_OPERATION",
                    "method": close_method,
                    "minutes_before_regular_close": str(minutes),
                    "reserved_at": timestamp,
                    "command_id": command,
                    "program_session_id": self._program_session_id,
                }
            )

            def reserve(document: dict[str, Any]) -> dict[str, Any]:
                state = _root(document)
                reservations = document.setdefault(
                    "individual_liquidation_reservations_by_instance", {}
                )
                reservations[instance_id] = deepcopy(reservation)
                state["commands"][command] = {
                    "operation": "RESERVE_INSTANCE_INDIVIDUAL_LIQUIDATION",
                    "applied_at": timestamp,
                    "entity_id": instance_id,
                }
                return document

            document = self.repository.mutate_session(
                session_id,
                reserve,
                expected_revision=before["revision"],
            )["document"]
            self._event(
                document,
                event_type="IMMEDIATE_LIQUIDATION_REQUESTED",
                identity=command,
                timestamp=timestamp,
                instance_id=instance_id,
                reason=clean_text(kwargs.get("reason")),
                payload={
                    "instance_operation": False,
                    "setting_only": True,
                    **reservation,
                },
            )
            return {
                "status": "REQUESTED",
                "duplicate": False,
                "setting_only": True,
                "reservation_scope": "NEXT_OPERATION",
                "document": document,
            }

        assert isinstance(operation, dict)
        policy = operation.get("operation_policy_snapshot")
        policy = policy if isinstance(policy, dict) else {}
        regular = policy.get("regular_market")
        regular = regular if isinstance(regular, dict) else {}
        end_text = clean_text(regular.get("end_time"))
        try:
            end_parts = [int(part) for part in end_text.split(":")]
            if len(end_parts) == 2:
                end_parts.append(0)
            if len(end_parts) != 3:
                raise ValueError(end_text)
            end_hour, end_minute, end_second = end_parts
            end_seconds = end_hour * 3600 + end_minute * 60 + end_second
        except (TypeError, ValueError):
            raise MockValidationError("MOCK_LIQUIDATION_END_TIME_INVALID")
        now_seconds = as_of.hour * 3600 + as_of.minute * 60 + as_of.second
        lock_minutes = (
            int(minutes)
            if close_method != CLOSE_CARRYOVER
            else int(
                str(
                    (
                        policy.get("liquidation")
                        if isinstance(policy.get("liquidation"), dict)
                        else {}
                    ).get("minutes_before_regular_close", 5)
                ).strip()
                or "5"
            )
        )
        if now_seconds >= max(0, end_seconds - lock_minutes * 60):
            self._event(
                before,
                event_type="INDIVIDUAL_LIQUIDATION_BLOCKED",
                identity=command,
                timestamp=timestamp,
                instance_id=instance_id,
                reason="LIQUIDATION_TIME_WINDOW_ENTERED",
                payload={
                    "instance_operation": True,
                    "operation_identity": clean_text(
                        operation.get("operation_session_id")
                    ),
                    "requested_policy": {
                        "minutes_before_regular_close": minutes,
                        "method": close_method,
                    },
                    "liquidation_start_seconds": max(
                        0, end_seconds - lock_minutes * 60
                    ),
                    "request_source": clean_text(kwargs.get("reason")),
                },
            )
            raise MockValidationError("LIQUIDATION_TIME_WINDOW_ENTERED")
        snapshot = {
            "operation_session_id": operation.get("operation_session_id"),
            "minutes_before_regular_close": minutes,
            "method": close_method,
            "captured_at": timestamp,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["instance_operations"][instance_id]
            current["individual_liquidation_time_snapshot"] = deepcopy(snapshot)
            state["commands"][command] = {
                "operation": "SET_INSTANCE_INDIVIDUAL_LIQUIDATION",
                "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            return document

        document = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )["document"]
        self._event(
            document,
            event_type="IMMEDIATE_LIQUIDATION_REQUESTED",
            identity=command,
            timestamp=timestamp,
            instance_id=instance_id,
            reason=clean_text(kwargs.get("reason")),
            payload={
                "instance_operation": True,
                "setting_only": True,
                **snapshot,
            },
        )
        return {
            "status": "REQUESTED",
            "duplicate": False,
            "setting_only": True,
            "document": document,
        }

    def request_instance_liquidation_boundary(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._request_instance_close(
            session_id,
            routine_instance_id=routine_instance_id,
            source="LIQUIDATION",
            **kwargs,
        )

    def _request_instance_close(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        source: str,
        method: str,
        reason: str,
        as_of: datetime,
        command_id: str,
        profit_percent: Any = None,
        loss_percent: Any = None,
        individual_liquidation_minutes: int | None = None,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        close_method = normalize_close_method(method)
        command = clean_text(command_id)
        instance_id = clean_text(routine_instance_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        if instance_id not in before.get("instance_execution", {}):
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root["instance_operations"].get(instance_id)
        allowed_states = {OPERATION_RUNNING}
        if source in {"EARLY", "IMMEDIATE", "LIQUIDATION"}:
            allowed_states.add(OPERATION_CLOSING)
        if (
            not isinstance(operation, dict)
            or operation.get("state") not in allowed_states
        ):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_CLOSE_STATE_INVALID")
        operation_snapshot = operation.get("operation_policy_snapshot")
        operation_snapshot = (
            operation_snapshot if isinstance(operation_snapshot, dict) else {}
        )
        snapshot_settings = operation_snapshot.get(
            "mock_instance_effective_settings"
        )
        snapshot_settings = (
            snapshot_settings if isinstance(snapshot_settings, dict) else {}
        )
        operation_mode = clean_text(
            operation_snapshot.get("operation_mode")
            or snapshot_settings.get("operation_mode")
        ).upper()
        if source == "EARLY" and operation_mode == "CONTINUOUS":
            individual = operation.get("individual_liquidation_time_snapshot")
            individual = individual if isinstance(individual, dict) else {}
            liquidation = operation_snapshot.get("liquidation")
            liquidation = liquidation if isinstance(liquidation, dict) else {}
            activated_method = normalize_close_method(
                individual.get("method")
                if individual
                else liquidation.get("method", CLOSE_CARRYOVER)
            )
            if activated_method in {
                CLOSE_MARKET,
                CLOSE_CURRENT_PRICE,
                CLOSE_CARRYOVER,
            }:
                close_method = activated_method
        profit_loss = self._profit_loss_snapshot(
            close_method, profit_percent, loss_percent
        )
        timestamp = as_of.isoformat(timespec="microseconds")
        if source == "EARLY":
            position = _positions(before)[instance_id]
            if int(position.get("holding_qty", 0) or 0) <= 0:
                self._event(
                    before,
                    event_type="EARLY_CLOSE_BLOCKED",
                    identity=command,
                    timestamp=timestamp,
                    instance_id=instance_id,
                    reason="NO_HOLDING",
                    payload={
                        "instance_operation": True,
                        "operation_identity": clean_text(
                            operation.get("operation_session_id")
                        ),
                        "requested_method": close_method,
                        "admission_level": 2,
                        "result": "BLOCKED",
                    },
                )
                raise MockValidationError("NO_HOLDING")
            time_reason, time_evidence = _mock_early_close_time_reason(
                operation,
                as_of,
            )
            if time_reason:
                self._event(
                    before,
                    event_type="EARLY_CLOSE_BLOCKED",
                    identity=command,
                    timestamp=timestamp,
                    instance_id=instance_id,
                    reason=time_reason,
                    payload={
                        "instance_operation": True,
                        "requested_method": close_method,
                        "admission_level": 4,
                        "result": "BLOCKED",
                        **time_evidence,
                    },
                )
                raise MockValidationError(time_reason)
        deferred_close = close_method in {CLOSE_ROUTINE, CLOSE_PROFIT_LOSS}
        current_method = clean_text(operation.get("close_method")).upper()
        current_source = clean_text(operation.get("close_source")).upper()
        effective_source = (
            current_source
            if source == "EARLY" and current_method and current_source in {"AUTO", "EARLY"}
            else source
        )
        if (
            source == "EARLY"
            and operation.get("state") == OPERATION_CLOSING
            and close_method == CLOSE_ROUTINE
            and current_method in {
                CLOSE_MARKET,
                CLOSE_CURRENT_PRICE,
                CLOSE_PROFIT_LOSS,
            }
        ):
            self._event(
                before,
                event_type="EARLY_CLOSE_BLOCKED",
                identity=command,
                timestamp=timestamp,
                instance_id=instance_id,
                reason="RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED",
                payload={
                    "instance_operation": True,
                    "operation_identity": clean_text(
                        operation.get("operation_session_id")
                    ),
                    "requested_action": "CHANGE_CLOSE_METHOD",
                    "requested_method": close_method,
                    "current_method": current_method,
                    "current_lifecycle_state": clean_text(
                        operation.get("state")
                    ),
                    "result": "BLOCKED",
                },
            )
            raise MockValidationError("RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED")
        transition_pending = bool(
            source == "EARLY"
            and operation.get("state") == OPERATION_CLOSING
            and current_method
            and current_method != close_method
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["instance_operations"][instance_id]
            if transition_pending:
                current["close_transition_pending"] = {
                    "method": close_method,
                    "source": effective_source,
                    "reason": clean_text(reason),
                    "requested_at": timestamp,
                    **profit_loss,
                }
                state["commands"][command] = {
                    "operation": "INSTANCE_CLOSE_TRANSITION",
                    "applied_at": timestamp,
                    "entity_id": current["operation_session_id"],
                }
                return document
            current.update(
                {
                    "state": OPERATION_RUNNING if deferred_close else OPERATION_CLOSING,
                    "closing_requested_at": current.get("closing_requested_at")
                    or timestamp,
                    "close_source": effective_source,
                    "close_reason": clean_text(reason),
                    "close_method": close_method,
                    "close_pending": deferred_close,
                    "final_sell_evidence": None,
                    "close_policy_snapshot": {
                        "source": effective_source,
                        "method": close_method,
                        "reason": clean_text(reason),
                        "captured_at": timestamp,
                        **profit_loss,
                    },
                    "close_transition_pending": None,
                    "individual_liquidation_time_snapshot": (
                        {
                            "minutes_before_regular_close": individual_liquidation_minutes,
                            "captured_at": timestamp,
                        }
                        if individual_liquidation_minutes is not None
                        else current.get("individual_liquidation_time_snapshot")
                    ),
                }
            )
            state["commands"][command] = {
                "operation": f"{effective_source}_INSTANCE_CLOSE",
                "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            document["instance_execution"][instance_id].update(
                {
                    "state": SESSION_RUNNING if deferred_close else SESSION_CLOSING,
                    "progression_allowed": deferred_close,
                }
            )
            _sync_instance_container_state(document)
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type=(
                "IMMEDIATE_LIQUIDATION_REQUESTED"
                if source == "IMMEDIATE"
                else _REQUEST_EVENTS[effective_source]
            ),
            identity=command,
            timestamp=timestamp,
            instance_id=instance_id,
            reason=reason,
            payload={"instance_operation": True, "method": close_method},
        )
        if transition_pending:
            return {
                "status": "TRANSITION_PENDING",
                "duplicate": False,
                "document": document,
            }
        if close_method == CLOSE_ROUTINE:
            completion = self.complete_instance_routine_close_if_ready(
                session_id,
                routine_instance_id=instance_id,
                as_of=as_of,
                lifecycle_cycle_id=deterministic_mock_identity(
                    "MC", session_id, instance_id, command,
                    "ROUTINE_CLOSE_COMPLETION",
                ),
            )
            if completion.get("status") == OUTCOME_DONE:
                return {**completion, "duplicate": False}
        return {
            "status": "REQUESTED" if source == "IMMEDIATE" else "CLOSING",
            "duplicate": False,
            "document": document,
        }

    def cancel_instance_early_close(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        command_id: str,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root["instance_operations"].get(instance_id)
        if (
            not isinstance(operation, dict)
            or clean_text(operation.get("close_source")).upper() != "EARLY"
            or not clean_text(operation.get("close_method"))
            or operation.get("final_sell_evidence")
            or operation.get("processed_cycles")
            or any(
                clean_text(item.get("child_identity")).startswith(
                    f"MOCK_INSTANCE_CLOSE:{operation.get('operation_session_id')}:"
                )
                for item in _instance_live_orders(before, instance_id)
            )
        ):
            self._event(
                before,
                event_type="EARLY_CLOSE_BLOCKED",
                identity=command,
                timestamp=as_of.isoformat(timespec="microseconds"),
                instance_id=instance_id,
                reason="MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED",
                payload={
                    "instance_operation": True,
                    "operation_identity": clean_text(
                        operation.get("operation_session_id")
                        if isinstance(operation, dict)
                        else ""
                    ),
                    "requested_action": "CANCEL_EARLY_CLOSE",
                    "current_method": clean_text(
                        operation.get("close_method")
                        if isinstance(operation, dict)
                        else ""
                    ),
                    "current_lifecycle_state": clean_text(
                        operation.get("state")
                        if isinstance(operation, dict)
                        else ""
                    ),
                    "result": "BLOCKED",
                },
            )
            raise MockValidationError("MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED")
        timestamp = as_of.isoformat(timespec="microseconds")
        evidence = {
            "command_id": command,
            "cancelled_at": timestamp,
            "previous_method": operation.get("close_method"),
            "previous_snapshot": deepcopy(operation.get("close_policy_snapshot")),
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["instance_operations"][instance_id]
            current.update(
                {
                    "state": OPERATION_RUNNING,
                    "closing_requested_at": "",
                    "close_source": "",
                    "close_reason": "",
                    "close_method": "",
                    "close_pending": False,
                    "close_policy_snapshot": None,
                    "early_close_cancel_evidence": deepcopy(evidence),
                }
            )
            state["commands"][command] = {
                "operation": "CANCEL_INSTANCE_EARLY_CLOSE",
                "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            document["instance_execution"][instance_id].update(
                {"state": SESSION_RUNNING, "progression_allowed": True}
            )
            _sync_instance_container_state(document)
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type="EARLY_CLOSE_CANCELLED",
            identity=command,
            timestamp=timestamp,
            instance_id=instance_id,
            payload={"instance_operation": True, **evidence},
        )
        return {"status": "CANCELLED", "duplicate": False, "document": document}

    def return_instance_early_close_to_auto(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        command_id: str,
    ) -> dict[str, Any]:
        """Withdraw one EARLY intervention to its frozen SCHEDULED timeline."""

        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        command = clean_text(command_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        root = _root(before)
        if command in root["commands"]:
            return {"status": "NOOP", "duplicate": True, "document": before}
        operation = root["instance_operations"].get(instance_id)
        if not isinstance(operation, dict):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_NOT_STARTED")
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        settings = snapshot.get("mock_instance_effective_settings")
        settings = settings if isinstance(settings, dict) else {}
        mode = clean_text(settings.get("operation_mode") or snapshot.get("operation_mode")).upper()
        end_text = clean_text(settings.get("end_buy_time") or settings.get("buy_end_time"))
        try:
            end_time = datetime.strptime(end_text, "%H:%M:%S").time()
        except ValueError as exc:
            raise MockValidationError("MOCK_EARLY_AUTO_RETURN_TIME_INVALID") from exc
        if (
            mode != "SCHEDULED"
            or clean_text(operation.get("close_source")).upper() != "EARLY"
            or not clean_text(operation.get("close_method"))
            or as_of.timetz().replace(tzinfo=None) >= end_time
        ):
            self._event(
                before,
                event_type="EARLY_CLOSE_BLOCKED",
                identity=command,
                timestamp=as_of.isoformat(timespec="microseconds"),
                instance_id=instance_id,
                reason="MOCK_EARLY_AUTO_RETURN_BLOCKED",
                payload={
                    "instance_operation": True,
                    "operation_identity": clean_text(
                        operation.get("operation_session_id")
                    ),
                    "requested_action": "RETURN_TO_AUTO_TIMELINE",
                    "requested_method": "AUTO_TIMELINE",
                    "current_method": clean_text(operation.get("close_method")),
                    "current_lifecycle_state": clean_text(operation.get("state")),
                    "relevant_time": end_text,
                    "result": "BLOCKED",
                },
            )
            raise MockValidationError("MOCK_EARLY_AUTO_RETURN_BLOCKED")
        close_prefix = f"MOCK_INSTANCE_CLOSE:{operation['operation_session_id']}:"
        filled_qty = sum(
            max(0, int(item.get("filled_qty", 0) or 0))
            for item in before.get("orders", ())
            if isinstance(item, dict)
            and clean_text(item.get("routine_instance_id")) == instance_id
            and clean_text(item.get("side")).upper() == "SELL"
            and clean_text(item.get("child_identity")).startswith(close_prefix)
        )
        if filled_qty > 0:
            self._event(
                before,
                event_type="EARLY_CLOSE_BLOCKED",
                identity=command,
                timestamp=as_of.isoformat(timespec="microseconds"),
                instance_id=instance_id,
                reason="MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED",
                payload={
                    "instance_operation": True,
                    "operation_identity": clean_text(
                        operation.get("operation_session_id")
                    ),
                    "requested_action": "RETURN_TO_AUTO_TIMELINE",
                    "requested_method": "AUTO_TIMELINE",
                    "current_method": clean_text(operation.get("close_method")),
                    "current_lifecycle_state": clean_text(operation.get("state")),
                    "filled_qty": filled_qty,
                    "result": "BLOCKED",
                },
            )
            raise MockValidationError("MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED")
        timestamp = as_of.isoformat(timespec="microseconds")
        live = _instance_live_orders(before, instance_id)
        transition_required = bool(live) and operation.get("close_method") != CLOSE_ROUTINE

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = _root(document)
            current = state["instance_operations"][instance_id]
            if transition_required:
                current["close_transition_pending"] = {
                    "method": "AUTO_TIMELINE",
                    "source": "EARLY",
                    "requested_at": timestamp,
                }
            else:
                current.update(
                    {
                        "state": OPERATION_RUNNING,
                        "closing_requested_at": "",
                        "close_source": "",
                        "close_reason": "",
                        "close_method": "",
                        "close_pending": False,
                        "close_policy_snapshot": None,
                        "close_transition_pending": None,
                        "liquidation_execution_method": "",
                        "early_auto_returned_at": timestamp,
                    }
                )
                document["instance_execution"][instance_id].update(
                    {"state": SESSION_RUNNING, "progression_allowed": True}
                )
            state["commands"][command] = {
                "operation": "RETURN_INSTANCE_EARLY_CLOSE_TO_AUTO",
                "applied_at": timestamp,
                "entity_id": current["operation_session_id"],
            }
            _sync_instance_container_state(document)
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type="EARLY_CLOSE_CANCELLED",
            identity=command,
            timestamp=timestamp,
            instance_id=instance_id,
            payload={"return_to_auto_timeline": True},
        )
        return {
            "status": "TRANSITION_PENDING" if transition_required else "CANCELLED",
            "duplicate": False,
            "document": document,
        }

    def activate_instance_profit_loss_if_triggered(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        current_price: Any,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        before = self.repository.read_session(session_id)
        operation = _root(before)["instance_operations"].get(instance_id)
        if (
            not isinstance(operation, dict)
            or operation.get("state") != OPERATION_RUNNING
            or operation.get("close_method") != CLOSE_PROFIT_LOSS
            or operation.get("close_pending") is not True
        ):
            return {"status": "NOOP", "document": before}
        position = next(
            item
            for item in before["positions"]
            if item.get("routine_instance_id") == instance_id
        )
        quantity = int(position.get("available_qty", 0) or 0)
        if quantity <= 0:
            return self._complete_instance(
                session_id,
                instance_id,
                deterministic_mock_identity(
                    "MC", session_id, instance_id, "PROFIT_LOSS_ZERO_POSITION"
                ),
                as_of,
                outcome=OUTCOME_DONE,
                completion_reason="MOCK_INSTANCE_PROFIT_LOSS_NO_POSITION",
            )
        try:
            average_price = float(position.get("average_price", 0) or 0)
            live_price = float(current_price or 0)
        except (TypeError, ValueError) as exc:
            raise MockValidationError("MOCK_CURRENT_PRICE_UNAVAILABLE") from exc
        if average_price <= 0 or live_price <= 0:
            raise MockValidationError("MOCK_CURRENT_PRICE_UNAVAILABLE")
        policy = operation.get("close_policy_snapshot")
        policy = policy if isinstance(policy, dict) else {}
        profit_percent = float(policy.get("profit_percent", 0) or 0)
        loss_percent = float(policy.get("loss_percent", 0) or 0)
        return_percent = ((live_price - average_price) / average_price) * 100.0
        triggered = (
            profit_percent > 0 and return_percent >= profit_percent
        ) or (loss_percent > 0 and return_percent <= -loss_percent)
        if not triggered:
            return {
                "status": "WAIT",
                "reason": "MOCK_PROFIT_LOSS_THRESHOLD_NOT_REACHED",
                "return_percent": return_percent,
                "document": before,
            }
        timestamp = as_of.isoformat(timespec="microseconds")

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = _root(document)["instance_operations"][instance_id]
            current.update(
                {
                    "state": OPERATION_CLOSING,
                    "close_pending": False,
                    "liquidation_execution_method": CLOSE_CURRENT_PRICE,
                    "profit_loss_triggered_at": timestamp,
                    "profit_loss_return_percent": return_percent,
                }
            )
            document["instance_execution"][instance_id].update(
                {"state": SESSION_CLOSING, "progression_allowed": False}
            )
            _sync_instance_container_state(document)
            return document

        document = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )["document"]
        return {
            "status": "CLOSING",
            "return_percent": return_percent,
            "document": document,
        }

    def record_instance_routine_final_sell(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        evaluation_cycle_id: str,
        result: dict[str, Any],
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance_id = clean_text(routine_instance_id)
        signal = result.get("signal") if isinstance(result, dict) else None
        if (
            not isinstance(signal, dict)
            or clean_text(signal.get("signal")).upper() != "SELL"
            or not clean_text(result.get("plan_id"))
        ):
            current = document if isinstance(document, dict) else self.repository.read_session(session_id)
            return {"status": "NOOP", "document": current}
        before = document if isinstance(document, dict) else self.repository.read_session(session_id)
        operation = _root(before)["instance_operations"].get(instance_id)
        if (
            not isinstance(operation, dict)
            or operation.get("state") != OPERATION_RUNNING
            or operation.get("close_pending") is not True
            or operation.get("close_method") != CLOSE_ROUTINE
            or operation.get("final_sell_evidence")
        ):
            return {"status": "NOOP", "document": before}
        timestamp = as_of.isoformat(timespec="microseconds")
        evidence = {
            "evaluation_cycle_id": clean_text(evaluation_cycle_id),
            "plan_id": clean_text(result.get("plan_id")),
            "decision_id": clean_text(result.get("decision_id")),
            "recorded_at": timestamp,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = _root(document)["instance_operations"][instance_id]
            current["final_sell_evidence"] = deepcopy(evidence)
            current["close_pending"] = False
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type="ROUTINE_CLOSE_FINAL_SELL_ACCEPTED",
            identity=evidence["plan_id"],
            timestamp=timestamp,
            instance_id=instance_id,
            payload={"instance_operation": True, **evidence},
        )
        return {"status": "RECORDED", "document": document}

    def complete_instance_routine_close_if_ready(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        lifecycle_cycle_id: str,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance_id = clean_text(routine_instance_id)
        before = document if isinstance(document, dict) else self.repository.read_session(session_id)
        operation = _root(before)["instance_operations"].get(instance_id)
        if (
            not isinstance(operation, dict)
            or operation.get("state") != OPERATION_RUNNING
            or operation.get("close_method") != CLOSE_ROUTINE
        ):
            return {"status": "NOOP", "document": before}
        position = _positions(before)[instance_id]
        holding_qty = int(position.get("holding_qty", 0) or 0)
        final_sell_evidence = operation.get("final_sell_evidence")
        if holding_qty > 0:
            return {"status": "WAIT", "document": before}
        if _instance_pending_position_execution(before, instance_id):
            return {"status": "WAIT", "document": before}
        if final_sell_evidence is not None and not isinstance(final_sell_evidence, dict):
            return {"status": "NOOP", "document": before}
        return self._complete_instance(
            session_id,
            instance_id,
            clean_text(lifecycle_cycle_id),
            as_of,
            outcome=OUTCOME_DONE,
            completion_reason=(
                "MOCK_INSTANCE_LIQUIDATION_COMPLETE"
                if isinstance(final_sell_evidence, dict)
                else "MOCK_INSTANCE_ROUTINE_CLOSE_NO_POSITION"
            ),
        )

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
        policy_snapshot = operation.get("operation_policy_snapshot")
        policy_snapshot = policy_snapshot if isinstance(policy_snapshot, dict) else {}
        carry_enabled = bool(policy_snapshot.get("long_hold_enabled", True))
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
                if item.get("state") in {INSTANCE_ERROR, INSTANCE_VALIDATION_STOPPED}:
                    continue
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
            return {"status": "INSTANCE_ERROR", "reason": "MOCK_LEGACY_REVIEW_STATE", "document": before}
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
                order = close_orders[0]
                result = self.engine.request_cancel(
                    session_id,
                    order["mock_order_id"],
                    command_id=deterministic_mock_identity(
                        "MC", session_id, cycle_id, order["mock_order_id"], "FINAL_CARRY_CANCEL"
                    ),
                    allow_closing=True,
                )
                return self._finish_cycle(
                    session_id, cycle_id, timestamp, "FINAL_CARRY_CANCEL", result
                )
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
            positions = _positions(before)
            instance_id = next(
                (
                    value
                    for value, position in positions.items()
                    if int(position.get("holding_qty", 0) or 0) > 0
                ),
                "",
            )
            return self._review_residual(
                session_id,
                operation,
                {
                    "routine_instance_id": instance_id,
                    "requested_qty": 0,
                    "filled_qty": 0,
                    "mock_order_id": "",
                },
                cycle_id,
                as_of,
                market,
            )
        ready, reason, current_price = self._market_ready(
            market, policy, as_of,
            require_trade=operation.get("close_method") == CLOSE_CURRENT_PRICE,
        )
        if not ready:
            return self._finish_cycle(session_id, cycle_id, timestamp, "WAIT", {"status": "WAIT", "reason": reason})
        positions = _positions(before)
        created: list[dict[str, Any]] = []
        for instance_id in sorted(positions):
            if before["instance_execution"][instance_id].get("state") in {
                INSTANCE_ERROR,
                INSTANCE_VALIDATION_STOPPED,
            }:
                continue
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

    def process_instance_operation_cycle(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        lifecycle_cycle_id: str,
        as_of: datetime,
        market: MockMarketSnapshot | None,
        policy: MockExecutionPolicy,
        final_close_boundary: bool = False,
        pending_order_cleanup_boundary: bool = False,
    ) -> dict[str, Any]:
        """Advance only one Instance close lifecycle and its Mock ledgers."""

        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        cycle_id = clean_text(lifecycle_cycle_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        if instance_id not in before.get("instance_execution", {}):
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
        operation = _root(before)["instance_operations"].get(instance_id)
        if not isinstance(operation, dict):
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_NOT_STARTED",
                "document": before,
            }
        if cycle_id in operation["processed_cycles"]:
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_CYCLE_ALREADY_PROCESSED",
                "document": before,
            }
        if operation.get("state") == OPERATION_ENDED:
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_ALREADY_ENDED",
                "document": before,
            }
        if operation.get("state") != OPERATION_CLOSING:
            return {
                "status": "WAIT",
                "reason": "MOCK_INSTANCE_OPERATION_NOT_CLOSING",
                "document": before,
            }

        timestamp = as_of.isoformat(timespec="microseconds")
        live = _instance_live_orders(before, instance_id)
        pending = [item for item in live if item.get("state") == ORDER_CANCEL_PENDING]
        if pending:
            order = pending[0]
            result = self.engine.finalize_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC", session_id, instance_id, cycle_id,
                    order["mock_order_id"], "CANCEL_EFFECT",
                ),
                allow_closing=True,
            )
            return self._finish_instance_cycle(
                session_id, instance_id, cycle_id, timestamp,
                "CANCEL_EFFECT", result,
            )

        transition = operation.get("close_transition_pending")
        transition = transition if isinstance(transition, dict) else {}
        if (
            clean_text(transition.get("method")).upper() == CLOSE_CARRYOVER
            and not pending_order_cleanup_boundary
            and not final_close_boundary
        ):
            return {
                "status": "WAIT",
                "reason": "MOCK_CARRYOVER_HOLD_UNTIL_CLEANUP",
                "document": before,
            }
        if transition and live:
            order = live[0]
            result = self.engine.request_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    cycle_id,
                    order["mock_order_id"],
                    "CLOSE_TRANSITION_CANCEL",
                ),
                allow_closing=True,
            )
            return self._finish_instance_cycle(
                session_id,
                instance_id,
                cycle_id,
                timestamp,
                "CLOSE_TRANSITION_CANCEL_REQUEST",
                result,
            )
        if transition:
            before_transition = self.repository.read_session(session_id)
            transition_method = clean_text(transition.get("method")).upper()
            if transition_method == "AUTO_TIMELINE":
                close_prefix = f"MOCK_INSTANCE_CLOSE:{operation['operation_session_id']}:"
                filled_qty = sum(
                    max(0, int(item.get("filled_qty", 0) or 0))
                    for item in before_transition.get("orders", ())
                    if isinstance(item, dict)
                    and clean_text(item.get("routine_instance_id")) == instance_id
                    and clean_text(item.get("side")).upper() == "SELL"
                    and clean_text(item.get("child_identity")).startswith(close_prefix)
                )
                if filled_qty > 0:
                    def block_auto_return(document: dict[str, Any]) -> dict[str, Any]:
                        current = _root(document)["instance_operations"][instance_id]
                        current["close_transition_pending"] = None
                        current["processed_cycles"][cycle_id] = {
                            "action": "EARLY_AUTO_TIMELINE_RETURN_BLOCKED",
                            "recorded_at": timestamp,
                            "reason": "MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED",
                        }
                        return document

                    document = self.repository.mutate_session(
                        session_id,
                        block_auto_return,
                        expected_revision=before_transition["revision"],
                    )["document"]
                    self._event(
                        document,
                        event_type="EARLY_CLOSE_BLOCKED",
                        identity=cycle_id,
                        timestamp=timestamp,
                        instance_id=instance_id,
                        reason="MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED",
                        payload={
                            "instance_operation": True,
                            "operation_identity": clean_text(
                                operation.get("operation_session_id")
                            ),
                            "requested_action": "RETURN_TO_AUTO_TIMELINE",
                            "requested_method": "AUTO_TIMELINE",
                            "current_method": clean_text(
                                operation.get("close_method")
                            ),
                            "current_lifecycle_state": clean_text(
                                operation.get("state")
                            ),
                            "filled_qty": filled_qty,
                            "result": "BLOCKED",
                        },
                    )
                    return {
                        "status": "BLOCKED",
                        "reason": "MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED",
                        "document": document,
                    }

            def apply_transition(document: dict[str, Any]) -> dict[str, Any]:
                current = _root(document)["instance_operations"][instance_id]
                pending_transition = current.get("close_transition_pending")
                pending_transition = (
                    pending_transition
                    if isinstance(pending_transition, dict)
                    else {}
                )
                if clean_text(pending_transition.get("method")).upper() == "AUTO_TIMELINE":
                    current.update(
                        {
                            "state": OPERATION_RUNNING,
                            "closing_requested_at": "",
                            "close_source": "",
                            "close_reason": "",
                            "close_method": "",
                            "close_pending": False,
                            "close_policy_snapshot": None,
                            "close_transition_pending": None,
                            "liquidation_execution_method": "",
                            "early_auto_returned_at": timestamp,
                        }
                    )
                    document["instance_execution"][instance_id].update(
                        {"state": SESSION_RUNNING, "progression_allowed": True}
                    )
                    current["processed_cycles"][cycle_id] = {
                        "action": "EARLY_AUTO_TIMELINE_RESTORED",
                        "recorded_at": timestamp,
                        "reason": "",
                    }
                    _sync_instance_container_state(document)
                    return document
                next_method = normalize_close_method(
                    pending_transition.get("method")
                )
                current.update(
                    {
                        "state": (
                            OPERATION_RUNNING
                            if next_method in {CLOSE_ROUTINE, CLOSE_PROFIT_LOSS}
                            else OPERATION_CLOSING
                        ),
                        "close_method": next_method,
                        "close_source": pending_transition.get("source") or "EARLY",
                        "close_reason": pending_transition.get("reason") or "",
                        "close_pending": next_method
                        in {CLOSE_ROUTINE, CLOSE_PROFIT_LOSS},
                        "close_policy_snapshot": deepcopy(pending_transition),
                        "close_transition_pending": None,
                        "liquidation_execution_method": "",
                    }
                )
                document["instance_execution"][instance_id].update(
                    {
                        "state": (
                            SESSION_RUNNING
                            if current["state"] == OPERATION_RUNNING
                            else SESSION_CLOSING
                        ),
                        "progression_allowed": current["state"]
                        == OPERATION_RUNNING,
                    }
                )
                current["processed_cycles"][cycle_id] = {
                    "action": "CLOSE_TRANSITION_APPLIED",
                    "recorded_at": timestamp,
                    "reason": "",
                }
                _sync_instance_container_state(document)
                return document

            document = self.repository.mutate_session(
                session_id,
                apply_transition,
                expected_revision=before_transition["revision"],
            )["document"]
            return {
                "status": "PROGRESSED",
                "action": "CLOSE_TRANSITION_APPLIED",
                "document": document,
            }

        close_prefix = f"MOCK_INSTANCE_CLOSE:{operation['operation_session_id']}:"
        close_orders = [
            item
            for item in live
            if clean_text(item.get("child_identity")).startswith(close_prefix)
        ]
        other_orders = [item for item in live if item not in close_orders]
        carryover_snapshot = operation.get("operation_policy_snapshot")
        carryover_snapshot = (
            carryover_snapshot if isinstance(carryover_snapshot, dict) else {}
        )
        if (
            operation.get("close_method") == CLOSE_CARRYOVER
            and carryover_snapshot.get("long_hold_enabled") is not False
            and not pending_order_cleanup_boundary
            and not final_close_boundary
        ):
            return {
                "status": "WAIT",
                "reason": "MOCK_CARRYOVER_HOLD_UNTIL_CLEANUP",
                "document": before,
            }
        if (pending_order_cleanup_boundary or final_close_boundary) and close_orders:
            order = close_orders[0]
            result = self.engine.request_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    cycle_id,
                    order["mock_order_id"],
                    "REGULAR_END_CANCEL",
                ),
                allow_closing=True,
            )
            return self._finish_instance_cycle(
                session_id,
                instance_id,
                cycle_id,
                timestamp,
                "REGULAR_END_CANCEL_REQUEST",
                result,
            )
        if other_orders:
            order = other_orders[0]
            result = self.engine.request_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC", session_id, instance_id, cycle_id,
                    order["mock_order_id"], "INSTANCE_CLOSE_CANCEL",
                ),
                allow_closing=True,
            )
            return self._finish_instance_cycle(
                session_id, instance_id, cycle_id, timestamp,
                "CANCEL_REQUEST", result,
            )

        if close_orders:
            ready, reason, _ = self._market_ready(
                market,
                policy,
                as_of,
                require_trade=(
                    _instance_liquidation_execution_method(operation)
                    == CLOSE_CURRENT_PRICE
                ),
            )
            if not ready:
                return self._finish_instance_cycle(
                    session_id, instance_id, cycle_id, timestamp, "WAIT",
                    {"status": "WAIT", "reason": reason},
                )
            order = close_orders[0]
            result = self.engine.process_orderbook(
                session_id,
                order["mock_order_id"],
                market=market,
                policy=policy,
                command_id=deterministic_mock_identity(
                    "MC", session_id, instance_id, cycle_id,
                    order["mock_order_id"], "LIQUIDATION_PROGRESS",
                ),
                allow_closing=True,
            )
            return self._finish_instance_cycle(
                session_id, instance_id, cycle_id, timestamp,
                "LIQUIDATION_PROGRESS", result,
            )

        position = next(
            item
            for item in before["positions"]
            if item.get("routine_instance_id") == instance_id
        )
        quantity = int(position.get("available_qty", 0) or 0)
        if operation.get("close_method") == CLOSE_CARRYOVER and quantity > 0:
            snapshot = operation.get("operation_policy_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            if snapshot.get("long_hold_enabled") is False:
                provenance = (
                    "LIQUIDATION_CARRYOVER"
                    if clean_text(operation.get("close_source")).upper()
                    == "LIQUIDATION"
                    else "CLOSE_CARRYOVER"
                )
                return self._review_instance_liquidation_residual(
                    session_id,
                    instance_id,
                    cycle_id,
                    as_of,
                    operation=operation,
                    residual_qty=quantity,
                    market=market,
                    reason_code="MOCK_LONG_HOLD_DISABLED",
                    termination_provenance=provenance,
                )
        if operation.get("close_method") == CLOSE_CARRYOVER or quantity <= 0:
            outcome = (
                OUTCOME_CARRYOVER_DONE
                if operation.get("close_method") == CLOSE_CARRYOVER
                else OUTCOME_DONE
            )
            return self._complete_instance(
                session_id,
                instance_id,
                cycle_id,
                as_of,
                outcome=outcome,
            )
        if pending_order_cleanup_boundary and not final_close_boundary:
            return self._finish_instance_cycle(
                session_id,
                instance_id,
                cycle_id,
                timestamp,
                "REGULAR_END_PENDING_CLEANUP_COMPLETE",
                {"status": "WAIT", "reason": "REGULAR_END_PENDING_CLEANUP"},
            )
        if final_close_boundary:
            return self._review_instance_liquidation_residual(
                session_id,
                instance_id,
                cycle_id,
                as_of,
                operation=operation,
                residual_qty=quantity,
                market=market,
            )

        ready, reason, current_price = self._market_ready(
            market,
            policy,
            as_of,
            require_trade=(
                _instance_liquidation_execution_method(operation)
                == CLOSE_CURRENT_PRICE
            ),
        )
        if not ready:
            return self._finish_instance_cycle(
                session_id, instance_id, cycle_id, timestamp, "WAIT",
                {"status": "WAIT", "reason": reason},
            )
        order_type = (
            "MARKET"
            if _instance_liquidation_execution_method(operation) == CLOSE_MARKET
            else "LIMIT"
        )
        result = self.engine.submit_order(
            session_id,
            routine_instance_id=instance_id,
            side="SELL",
            order_type=order_type,
            requested_qty=quantity,
            limit_price=None if order_type == "MARKET" else current_price,
            market=market,
            policy=policy,
            generation=0,
            child_identity=f"{close_prefix}{instance_id}",
            command_id=deterministic_mock_identity(
                "MC", session_id, instance_id, cycle_id, "LIQUIDATE"
            ),
            allow_closing=True,
        )
        if result.get("status") == RESULT_BLOCKED:
            raise MockValidationError(
                clean_text(result.get("reason")) or "MOCK_LIQUIDATION_BLOCKED"
            )
        return self._finish_instance_cycle(
            session_id, instance_id, cycle_id, timestamp,
            "LIQUIDATION_STARTED", result,
        )

    def process_instance_regular_end_pending_cleanup(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        lifecycle_cycle_id: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        """Advance one Mock-owned pending-order cancel step before regular end."""

        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        cycle_id = clean_text(lifecycle_cycle_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        operation = _root(before)["instance_operations"].get(instance_id)
        if not isinstance(operation, dict) or operation.get("state") not in {
            OPERATION_RUNNING,
            OPERATION_CLOSING,
        }:
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_NOT_ACTIVE",
                "document": before,
            }
        if cycle_id in operation.get("processed_cycles", {}):
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_CYCLE_ALREADY_PROCESSED",
                "document": before,
            }
        live = _instance_live_orders(before, instance_id)
        if not live:
            return {
                "status": "NOOP",
                "reason": "MOCK_PENDING_ORDER_ABSENT",
                "document": before,
            }
        timestamp = as_of.isoformat(timespec="microseconds")
        pending = [item for item in live if item.get("state") == ORDER_CANCEL_PENDING]
        order = pending[0] if pending else live[0]
        if pending:
            result = self.engine.finalize_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    cycle_id,
                    order["mock_order_id"],
                    "REGULAR_END_CANCEL_EFFECT",
                ),
                allow_closing=True,
            )
            action = "REGULAR_END_CANCEL_EFFECT"
        else:
            result = self.engine.request_cancel(
                session_id,
                order["mock_order_id"],
                command_id=deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    cycle_id,
                    order["mock_order_id"],
                    "REGULAR_END_CANCEL_REQUEST",
                ),
                allow_closing=True,
            )
            action = "REGULAR_END_CANCEL_REQUEST"
        return self._finish_instance_cycle(
            session_id,
            instance_id,
            cycle_id,
            timestamp,
            action,
            result,
        )

    def complete_instance_normal_termination(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        as_of: datetime,
        lifecycle_cycle_id: str,
        ats_continuation: bool,
    ) -> dict[str, Any]:
        """Classify a close-free CONTINUOUS final session in the Mock domain."""

        if as_of.tzinfo is None:
            raise MockValidationError("MOCK_OPERATION_TIMESTAMP_INVALID")
        instance_id = clean_text(routine_instance_id)
        cycle_id = clean_text(lifecycle_cycle_id)
        before = self.repository.read_session(session_id)
        _validate_operation_integrity(before)
        operation = _root(before)["instance_operations"].get(instance_id)
        if not isinstance(operation, dict):
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_NOT_STARTED",
                "document": before,
            }
        if cycle_id in operation.get("processed_cycles", {}):
            return {
                "status": "NOOP",
                "reason": "MOCK_INSTANCE_OPERATION_CYCLE_ALREADY_PROCESSED",
                "document": before,
            }
        if (
            operation.get("state") != OPERATION_RUNNING
            or clean_text(operation.get("close_source"))
            or clean_text(operation.get("close_method"))
        ):
            return {
                "status": "NOOP",
                "reason": "MOCK_NORMAL_TERMINATION_NOT_APPLICABLE",
                "document": before,
            }
        provenance = (
            "ATS_FINAL_NO_TERMINATION"
            if ats_continuation
            else "CONTINUOUS_NO_CLOSE"
        )
        position = _positions(before)[instance_id]
        residual_qty = int(position.get("holding_qty", 0) or 0)
        live_orders = _instance_live_orders(before, instance_id)
        if live_orders:
            return self._review_instance_liquidation_residual(
                session_id,
                instance_id,
                cycle_id,
                as_of,
                operation=operation,
                residual_qty=residual_qty,
                market=None,
                reason_code="MOCK_NORMAL_TERMINATION_PENDING_ORDER",
                termination_provenance=provenance,
            )
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        if residual_qty > 0 and snapshot.get("long_hold_enabled") is False:
            return self._review_instance_liquidation_residual(
                session_id,
                instance_id,
                cycle_id,
                as_of,
                operation=operation,
                residual_qty=residual_qty,
                market=None,
                reason_code="MOCK_LONG_HOLD_DISABLED",
                termination_provenance=provenance,
            )
        return self._complete_instance(
            session_id,
            instance_id,
            cycle_id,
            as_of,
            outcome=(
                OUTCOME_CARRYOVER_DONE if residual_qty > 0 else OUTCOME_DONE
            ),
            completion_reason=(
                "MOCK_NORMAL_HOLDING_CONTINUATION"
                if residual_qty > 0
                else "MOCK_NORMAL_TERMINATION_COMPLETE"
            ),
            termination_provenance=provenance,
        )

    def _review_instance_liquidation_residual(
        self,
        session_id: str,
        instance_id: str,
        cycle_id: str,
        as_of: datetime,
        *,
        operation: dict[str, Any],
        residual_qty: int,
        market: MockMarketSnapshot | None,
        reason_code: str = "MOCK_LIQUIDATION_RESIDUAL",
        termination_provenance: str = "",
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)
        evidence = {
            "operation_session_id": operation.get("operation_session_id"),
            "routine_instance_id": instance_id,
            "intended_close_method": operation.get("close_method"),
            "residual_qty": int(residual_qty),
            "market_snapshot_identity": market.snapshot_identity if market else "",
            "reason": reason_code,
            "occurred_at": timestamp,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = _root(document)["instance_operations"][instance_id]
            current.update(
                {
                    "state": OPERATION_REVIEW_STOPPED,
                    "outcome": OUTCOME_REVIEW_REQUIRED,
                    "ended_at": timestamp,
                    "termination_provenance": (
                        termination_provenance
                        or f"LIQUIDATION_{current.get('close_method')}_RESIDUAL"
                    ),
                }
            )
            current["processed_cycles"][cycle_id] = {
                "action": OUTCOME_REVIEW_REQUIRED,
                "recorded_at": timestamp,
                "reason": reason_code,
            }
            return document

        document = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )["document"]
        self._event(
            document,
            event_type="CLOSE_RESIDUAL_DETECTED",
            identity=cycle_id,
            timestamp=timestamp,
            instance_id=instance_id,
            reason=reason_code,
            payload=evidence,
        )
        stopped = MockValidationSessionService(
            self.repository,
            now_factory=lambda: timestamp,
        ).stop_for_instance_error(
            session_id,
            source_routine_instance_id=instance_id,
            reason_code=reason_code,
            reason=(
                "Mock holding continuation is disabled"
                if reason_code == "MOCK_LONG_HOLD_DISABLED"
                else "Mock liquidation left an unresolved residual position"
            ),
            command_id=deterministic_mock_identity(
                "MC", session_id, instance_id, cycle_id, "LIQUIDATION_RESIDUAL"
            ),
            source_category="MOCK_OPERATION_LIFECYCLE",
        )
        return {
            "status": OUTCOME_REVIEW_REQUIRED,
            "action": OUTCOME_REVIEW_REQUIRED,
            "reason": reason_code,
            "document": stopped["document"],
        }

    def _finish_instance_cycle(
        self,
        session_id: str,
        instance_id: str,
        cycle_id: str,
        timestamp: str,
        action: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            operation = _root(document)["instance_operations"][instance_id]
            operation["processed_cycles"][cycle_id] = {
                "action": action,
                "recorded_at": timestamp,
                "reason": clean_text(result.get("reason")),
            }
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        return {
            "status": clean_text(result.get("status")) or "PROGRESSED",
            "reason": clean_text(result.get("reason")),
            "action": action,
            "document": document,
        }

    def _complete_instance(
        self,
        session_id: str,
        instance_id: str,
        cycle_id: str,
        as_of: datetime,
        *,
        outcome: str,
        completion_reason: str = "",
        termination_provenance: str = "",
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            root = _root(document)
            operation = root["instance_operations"][instance_id]
            operation.update(
                {
                    "state": OPERATION_ENDED,
                    "ended_at": timestamp,
                    "outcome": outcome,
                    "individual_liquidation_time_snapshot": None,
                    **(
                        {"termination_provenance": termination_provenance}
                        if termination_provenance
                        else {}
                    ),
                }
            )
            operation["processed_cycles"][cycle_id] = {
                "action": outcome,
                "recorded_at": timestamp,
                "reason": (
                    clean_text(completion_reason)
                    or (
                        "MOCK_INSTANCE_CARRYOVER_QUALIFIED"
                        if outcome == OUTCOME_CARRYOVER_DONE
                        else "MOCK_INSTANCE_LIQUIDATION_COMPLETE"
                    )
                ),
            }
            operation["close_pending"] = False
            execution = document["instance_execution"][instance_id]
            execution.update(
                {
                    "state": SESSION_ENDED,
                    "progression_allowed": False,
                    "last_operation_session_id": operation["operation_session_id"],
                    "operation_session_id": "",
                }
            )
            if outcome == OUTCOME_DONE:
                cycle = document["cycle_state_by_instance"].get(instance_id)
                if isinstance(cycle, dict) and cycle.get("active") is True:
                    cycle.update({"active": False, "completed_at": timestamp})
            _sync_instance_container_state(document)
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        self._event(
            document,
            event_type=(
                "OPERATION_CARRYOVER_DONE"
                if outcome == OUTCOME_CARRYOVER_DONE
                else "OPERATION_DONE"
            ),
            identity=cycle_id,
            timestamp=timestamp,
            instance_id=instance_id,
            payload={
                "instance_operation": True,
                **(
                    {"termination_provenance": termination_provenance}
                    if termination_provenance
                    else {}
                ),
            },
        )
        return {
            "status": outcome,
            "action": outcome,
            "document": document,
        }

    def _review_structural(
        self, session_id: str, cycle_id: str, as_of: datetime,
        error: MockValidationError,
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)
        instance_ids = sorted(before.get("instance_execution", {}))
        instance_id = instance_ids[0]
        reason = clean_text(error) or "MOCK_OPERATION_INTEGRITY_FAILURE"

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            root = document.get("mock_operation_lifecycle")
            current = root.get("current") if isinstance(root, dict) else None
            if isinstance(current, dict):
                current["integrity_error"] = reason
                cycles = current.get("processed_cycles")
                if isinstance(cycles, dict):
                    cycles[cycle_id] = {
                        "action": "INSTANCE_ERROR",
                        "recorded_at": timestamp,
                        "reason": reason,
                    }
            return document

        document = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]
        service = MockValidationSessionService(self.repository, now_factory=lambda: timestamp)
        stopped_document = document
        for affected_id in instance_ids:
            stopped = service.stop_for_instance_error(
                session_id,
                source_routine_instance_id=affected_id,
                reason_code="MOCK_OPERATION_INTEGRITY_FAILURE",
                reason=reason,
                command_id=deterministic_mock_identity(
                    "MC", session_id, cycle_id, affected_id, "OPERATION_INTEGRITY"
                ),
                source_category="MOCK_OPERATION_LIFECYCLE",
            )
            stopped_document = stopped["document"]
        return {
            "status": "INSTANCE_ERROR",
            "action": "INSTANCE_ERROR",
            "reason": reason,
            "document": stopped_document,
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
                if item.get("state") in {INSTANCE_ERROR, INSTANCE_VALIDATION_STOPPED}:
                    continue
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
            provenance = (
                "CLOSE_CARRYOVER"
                if current.get("close_method") == CLOSE_CARRYOVER
                else f"LIQUIDATION_{current.get('close_method')}_RESIDUAL"
            )
            current.update(
                {
                    "state": OPERATION_REVIEW_STOPPED,
                    "outcome": OUTCOME_REVIEW_REQUIRED,
                    "termination_provenance": provenance,
                }
            )
            current["processed_cycles"][cycle_id] = {
                "action": OUTCOME_REVIEW_REQUIRED, "recorded_at": timestamp,
                "reason": "MOCK_CLOSE_RESIDUAL",
            }
            document["session"]["state"] = SESSION_REVIEW_STOPPED
            document["review"] = {
                "review_required": True,
                "review_reason": "MOCK_CLOSE_RESIDUAL",
                "source_routine_instance_id": instance_id,
                "occurred_at": timestamp,
                "resolved_at": "",
                "resolution": "",
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
            source_category="MOCK_OPERATION_LIFECYCLE",
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
    "CLOSE_PROFIT_LOSS",
    "CLOSE_ROUTINE",
    "MockOperationLifecycleCoordinator",
    "OPERATION_CLOSING",
    "OPERATION_ENDED",
    "OPERATION_REVIEW_STOPPED",
    "OPERATION_VALIDATION_STOPPED",
    "OPERATION_RUNNING",
    "OUTCOME_CARRYOVER_DONE",
    "OUTCOME_DONE",
    "OUTCOME_NOT_READY",
    "OUTCOME_REVIEW_REQUIRED",
    "evaluate_mock_operation_completion",
    "instance_operation_state",
    "mock_validation_end_eligibility",
    "normalize_close_method",
]
