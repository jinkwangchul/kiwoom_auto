# -*- coding: utf-8 -*-
"""Isolated domain contracts for Mock Validation.

This module is deliberately independent from Production runtime, queue, broker,
event, review, and budget writers.  It contains only validation, identity, and
state-transition primitives for the Mock domain.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from math import isfinite
from typing import Any, Callable
from uuid import uuid4


MOCK_SESSION_SCHEMA_VERSION = "mock_validation_session_v1"
MOCK_EVENT_SCHEMA_VERSION = "mock_validation_event_v1"
MOCK_HISTORY_SCHEMA_VERSION = "mock_validation_history_v1"
MOCK_CURRENT_INDEX_SCHEMA_VERSION = "mock_validation_current_index_v1"

SESSION_WAITING = "WAITING"
SESSION_RUNNING = "RUNNING"
SESSION_REVIEW_STOPPED = "REVIEW_STOPPED"
SESSION_CLOSING = "CLOSING"
SESSION_ENDED = "ENDED"
SESSION_STATES = {
    SESSION_WAITING,
    SESSION_RUNNING,
    SESSION_REVIEW_STOPPED,
    SESSION_CLOSING,
    SESSION_ENDED,
}

ORDER_CREATED = "CREATED"
ORDER_OPEN = "OPEN"
ORDER_PARTIAL_FILL = "PARTIAL_FILL"
ORDER_FILLED = "FILLED"
ORDER_CANCEL_PENDING = "CANCEL_PENDING"
ORDER_CANCELED = "CANCELED"
ORDER_REJECTED = "REJECTED"
ORDER_STATES = {
    ORDER_CREATED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_FILLED,
    ORDER_CANCEL_PENDING,
    ORDER_CANCELED,
    ORDER_REJECTED,
}
ORDER_TERMINAL_STATES = {ORDER_FILLED, ORDER_CANCELED, ORDER_REJECTED}
ORDER_TRANSITIONS = {
    ORDER_CREATED: {ORDER_OPEN, ORDER_REJECTED, ORDER_CANCELED},
    ORDER_OPEN: {ORDER_PARTIAL_FILL, ORDER_FILLED, ORDER_CANCEL_PENDING, ORDER_REJECTED},
    ORDER_PARTIAL_FILL: {
        ORDER_PARTIAL_FILL,
        ORDER_FILLED,
        ORDER_CANCEL_PENDING,
        ORDER_REJECTED,
    },
    ORDER_CANCEL_PENDING: {
        ORDER_PARTIAL_FILL,
        ORDER_FILLED,
        ORDER_CANCELED,
        ORDER_REJECTED,
    },
    ORDER_FILLED: set(),
    ORDER_CANCELED: set(),
    ORDER_REJECTED: set(),
}

FOUNDATION_EVENT_TYPES = {
    "SESSION_CREATED",
    "SESSION_STARTED",
    "SESSION_REVIEW_STOPPED",
    "INSTANCE_ERROR",
    "SESSION_RESET",
    "SESSION_ENDED",
    "VIRTUAL_ORDER_CREATED",
    "VIRTUAL_ORDER_OPENED",
    "VIRTUAL_ORDER_PARTIAL_FILL",
    "VIRTUAL_ORDER_FILLED",
    "VIRTUAL_ORDER_CANCEL_PENDING",
    "VIRTUAL_ORDER_CANCELED",
    "VIRTUAL_FILL_RECORDED",
    "VIRTUAL_ORDER_BLOCKED",
    "ROUTINE_EVALUATED",
    "ROUTINE_BUY_DECISION",
    "ROUTINE_SELL_DECISION",
    "EXECUTION_PLAN_CREATED",
    "EXECUTION_PLAN_BLOCKED",
    "EXECUTION_CHILD_CREATED",
    "EXECUTION_CHILD_COMPLETED",
    "ORDER_TIMEOUT_DETECTED",
    "VIRTUAL_CANCEL_REQUESTED",
    "VIRTUAL_CANCEL_EFFECT_CONFIRMED",
    "PRICE_RESET_TRIGGERED",
    "PRICE_RESET_REPLANNED",
    "BUY_REPEAT_TRIGGERED",
    "BUY_REPEAT_ROUND_STARTED",
    "BUY_EXIT_TRIGGERED",
    "BUY_EXIT_CONFIRMED",
    "SELL_REPEAT_TRIGGERED",
    "SELL_REPEAT_GENERATION_STARTED",
    "SELL_REPEAT_EXIT_TRIGGERED",
    "FINAL_RESIDUAL_MARKET_STARTED",
    "CONTINUATION_BLOCKED",
    "OPERATION_SESSION_CREATED",
    "OPERATION_STARTED",
    "NORMAL_CLOSE_REQUESTED",
    "AUTO_CLOSE_REQUESTED",
    "EARLY_CLOSE_REQUESTED",
    "LIQUIDATION_STARTED",
    "LIQUIDATION_PROGRESS",
    "LIQUIDATION_COMPLETED",
    "IMMEDIATE_LIQUIDATION_REQUESTED",
    "LONG_HOLD_SELECTED",
    "CARRYOVER_CONFIRMED",
    "OPERATION_DONE",
    "OPERATION_CARRYOVER_DONE",
    "CLOSE_RESIDUAL_DETECTED",
    "OPERATION_REVIEW_STOPPED",
    "OPERATION_RESUMED",
    "OPERATION_SESSION_ENDED",
    "OPERATION_RESET",
    "MOCK_TAX_UPDATED",
    "RETURN_REQUESTED",
    "RETURN_FAILED",
    "RETURN_COMPLETED",
}


class MockValidationError(RuntimeError):
    """Fail-closed Mock-domain contract error."""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MockValidationError(f"MOCK_DOCUMENT_NOT_CANONICAL:{exc}") from exc


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def calculate_mock_pnl(
    *,
    realized_pnl: int | float,
    unrealized_pnl: int | float,
    commission: int | float,
    mock_tax: int | float,
) -> dict[str, int | float]:
    """Return the Phase-1 ledger totals without any market revaluation."""
    realized = _finite_number(realized_pnl, "MOCK_PNL_REALIZED", nonnegative=False)
    unrealized = _finite_number(unrealized_pnl, "MOCK_PNL_UNREALIZED", nonnegative=False)
    fee = _finite_number(commission, "MOCK_PNL_COMMISSION")
    tax = _finite_number(mock_tax, "MOCK_PNL_TAX")
    gross = realized + unrealized
    net = gross - fee - tax
    return {"gross_pnl": gross, "net_pnl": net}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalized_stock_code(value: Any) -> str:
    code = clean_text(value).upper()
    if code.startswith("A") and len(code) == 7:
        code = code[1:]
    if len(code) != 6 or not code.isalnum():
        raise MockValidationError("MOCK_STOCK_CODE_INVALID")
    return code


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MockValidationError(f"{field}_INVALID")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MockValidationError(f"{field}_INVALID") from exc
    if number <= 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise MockValidationError(f"{field}_INVALID")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MockValidationError(f"{field}_INVALID")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MockValidationError(f"{field}_INVALID") from exc
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise MockValidationError(f"{field}_INVALID")
    return number


def _finite_number(value: Any, field: str, *, nonnegative: bool = True) -> int | float:
    if isinstance(value, bool):
        raise MockValidationError(f"{field}_INVALID")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MockValidationError(f"{field}_INVALID") from exc
    if not isfinite(number) or (nonnegative and number < 0):
        raise MockValidationError(f"{field}_INVALID")
    return int(number) if number.is_integer() else number


def new_mock_identity(prefix: str) -> str:
    normalized = clean_text(prefix).upper()
    if normalized not in {"MV", "MO", "MF", "ME", "MS", "MC"}:
        raise MockValidationError("MOCK_IDENTITY_PREFIX_INVALID")
    return f"{normalized}-{uuid4().hex}"


def deterministic_mock_identity(prefix: str, *parts: Any) -> str:
    normalized = clean_text(prefix).upper()
    if normalized not in {"MV", "MO", "MF", "ME", "MS", "MC"}:
        raise MockValidationError("MOCK_IDENTITY_PREFIX_INVALID")
    identity = payload_hash([clean_text(part) for part in parts])[:32]
    return f"{normalized}-{identity}"


def validate_reference_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise MockValidationError("MOCK_REFERENCE_SNAPSHOT_INVALID")
    result = deepcopy(snapshot)
    result["stock_code"] = normalized_stock_code(result.get("stock_code"))
    if not clean_text(result.get("stock_name")):
        raise MockValidationError("MOCK_STOCK_NAME_MISSING")
    if not clean_text(result.get("snapshot_created_at")):
        raise MockValidationError("MOCK_REFERENCE_TIMESTAMP_MISSING")
    instances = result.get("routine_instances")
    if not isinstance(instances, list) or not instances:
        raise MockValidationError("MOCK_ROUTINE_INSTANCES_MISSING")
    ids: set[str] = set()
    for item in instances:
        if not isinstance(item, dict):
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_SNAPSHOT_INVALID")
        instance_id = clean_text(item.get("routine_instance_id"))
        if not instance_id or instance_id in ids:
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_ID_INVALID")
        ids.add(instance_id)
        if not clean_text(item.get("routine_definition_id")):
            raise MockValidationError("MOCK_ROUTINE_DEFINITION_ID_MISSING")
        if not clean_text(item.get("routine_type")):
            raise MockValidationError("MOCK_ROUTINE_TYPE_MISSING")
        rules_hash = clean_text(item.get("rules_hash"))
        rules_snapshot = item.get("rules_snapshot")
        if not rules_hash or not isinstance(rules_snapshot, dict):
            raise MockValidationError("MOCK_ROUTINE_RULES_SNAPSHOT_INVALID")
        if payload_hash(rules_snapshot) != rules_hash:
            raise MockValidationError("MOCK_ROUTINE_RULES_HASH_MISMATCH")
    supplied_hash = clean_text(result.pop("snapshot_hash", ""))
    calculated_hash = payload_hash(result)
    if supplied_hash and supplied_hash != calculated_hash:
        raise MockValidationError("MOCK_REFERENCE_SNAPSHOT_HASH_MISMATCH")
    result["snapshot_hash"] = calculated_hash
    return result


def initial_session_document(
    *,
    validation_session_id: str,
    reference_snapshot: dict[str, Any],
    created_at: str,
    mock_tax_enabled: bool = True,
    mock_tax_rate: float = 0.002,
) -> dict[str, Any]:
    session_id = clean_text(validation_session_id)
    if not session_id.startswith("MV-"):
        raise MockValidationError("MOCK_VALIDATION_SESSION_ID_INVALID")
    snapshot = validate_reference_snapshot(reference_snapshot)
    tax_rate = _finite_number(mock_tax_rate, "MOCK_TAX_RATE")
    if float(tax_rate) > 1:
        raise MockValidationError("MOCK_TAX_RATE_INVALID")
    instance_ids = [item["routine_instance_id"] for item in snapshot["routine_instances"]]
    return {
        "schema_version": MOCK_SESSION_SCHEMA_VERSION,
        "revision": 0,
        "session": {
            "validation_session_id": session_id,
            "stock_code": snapshot["stock_code"],
            "stock_name": snapshot["stock_name"],
            "state": SESSION_WAITING,
            "session_generation": 1,
            "created_at": clean_text(created_at),
            "started_at": "",
            "ended_at": "",
            "start_identity": "",
            "mock_tax_enabled": bool(mock_tax_enabled),
            "mock_tax_rate": tax_rate,
            "reference_snapshot_hash": snapshot["snapshot_hash"],
        },
        "reference_snapshot": snapshot,
        "instance_execution": {
            instance_id: {
                "routine_instance_id": instance_id,
                "state": SESSION_WAITING,
                "started_at": "",
                "progression_allowed": False,
            }
            for instance_id in instance_ids
        },
        "orders": [],
        "fills": [],
        "positions": [
            {
                "validation_session_id": session_id,
                "routine_instance_id": instance_id,
                "stock_code": snapshot["stock_code"],
                "holding_qty": 0,
                "available_qty": 0,
                "average_price": 0,
                "realized_cost_basis": 0,
                "updated_at": clean_text(created_at),
            }
            for instance_id in instance_ids
        ],
        "pnl": [
            {
                "validation_session_id": session_id,
                "routine_instance_id": instance_id,
                "stock_code": snapshot["stock_code"],
                "realized_pnl": 0,
                "unrealized_pnl": 0,
                "gross_pnl": 0,
                "commission": 0,
                "mock_tax": 0,
                "net_pnl": 0,
                "updated_at": clean_text(created_at),
            }
            for instance_id in instance_ids
        ],
        "review": {
            "review_required": False,
            "review_reason": "",
            "source_routine_instance_id": "",
            "occurred_at": "",
            "resolved_at": "",
            "resolution": "",
        },
        "cycle_state_by_instance": {instance_id: {} for instance_id in instance_ids},
        "progression_by_instance": {instance_id: {} for instance_id in instance_ids},
        "applied_commands": {},
    }


def validate_mock_order(order: Any, session: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(order, dict):
        raise MockValidationError("MOCK_ORDER_INVALID")
    result = deepcopy(order)
    if not clean_text(result.get("mock_order_id")).startswith("MO-"):
        raise MockValidationError("MOCK_ORDER_ID_INVALID")
    if result.get("validation_session_id") != session.get("validation_session_id"):
        raise MockValidationError("MOCK_ORDER_SESSION_MISMATCH")
    if normalized_stock_code(result.get("stock_code")) != session.get("stock_code"):
        raise MockValidationError("MOCK_ORDER_STOCK_MISMATCH")
    if clean_text(result.get("side")).upper() not in {"BUY", "SELL"}:
        raise MockValidationError("MOCK_ORDER_SIDE_INVALID")
    order_type = clean_text(result.get("order_type")).upper()
    if order_type not in {"LIMIT", "MARKET"}:
        raise MockValidationError("MOCK_ORDER_TYPE_INVALID")
    requested_price = result.get("requested_price")
    if order_type == "LIMIT":
        price = _finite_number(requested_price, "MOCK_ORDER_REQUESTED_PRICE")
        if float(price) <= 0:
            raise MockValidationError("MOCK_ORDER_REQUESTED_PRICE_INVALID")
    elif requested_price not in (None, 0, 0.0, ""):
        raise MockValidationError("MOCK_MARKET_ORDER_PRICE_MUST_BE_EMPTY")
    state = clean_text(result.get("state")).upper()
    if state not in ORDER_STATES:
        raise MockValidationError("MOCK_ORDER_STATE_INVALID")
    requested = _positive_int(result.get("requested_qty"), "MOCK_ORDER_REQUESTED_QTY")
    filled = _nonnegative_int(result.get("filled_qty"), "MOCK_ORDER_FILLED_QTY")
    remaining = _nonnegative_int(result.get("remaining_qty"), "MOCK_ORDER_REMAINING_QTY")
    if filled + remaining != requested:
        raise MockValidationError("MOCK_ORDER_QUANTITY_MISMATCH")
    if state == ORDER_FILLED and remaining != 0:
        raise MockValidationError("MOCK_ORDER_FILLED_REMAINING_INVALID")
    if state == ORDER_PARTIAL_FILL and not (0 < filled < requested):
        raise MockValidationError("MOCK_ORDER_PARTIAL_QUANTITY_INVALID")
    _nonnegative_int(result.get("generation", 0), "MOCK_ORDER_GENERATION")
    if not clean_text(result.get("routine_instance_id")):
        raise MockValidationError("MOCK_ORDER_INSTANCE_ID_MISSING")
    for field in (
        "queue_ahead_qty",
        "reserved_budget",
        "execution_budget",
    ):
        if field in result and result.get(field) is not None:
            _finite_number(result.get(field), f"MOCK_ORDER_{field.upper()}")
    for field in (
        "last_processed_trade_sequence",
        "market_connection_epoch",
    ):
        if field in result:
            _nonnegative_int(result.get(field), f"MOCK_ORDER_{field.upper()}")
    return result


def validate_mock_fill(fill: Any, session: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fill, dict):
        raise MockValidationError("MOCK_FILL_INVALID")
    result = deepcopy(fill)
    if not clean_text(result.get("mock_fill_id")).startswith("MF-"):
        raise MockValidationError("MOCK_FILL_ID_INVALID")
    if not clean_text(result.get("mock_order_id")).startswith("MO-"):
        raise MockValidationError("MOCK_FILL_ORDER_ID_INVALID")
    if result.get("validation_session_id") != session.get("validation_session_id"):
        raise MockValidationError("MOCK_FILL_SESSION_MISMATCH")
    if normalized_stock_code(result.get("stock_code")) != session.get("stock_code"):
        raise MockValidationError("MOCK_FILL_STOCK_MISMATCH")
    if clean_text(result.get("side")).upper() not in {"BUY", "SELL"}:
        raise MockValidationError("MOCK_FILL_SIDE_INVALID")
    _positive_int(result.get("qty"), "MOCK_FILL_QTY")
    price = _finite_number(result.get("price"), "MOCK_FILL_PRICE")
    if float(price) <= 0:
        raise MockValidationError("MOCK_FILL_PRICE_INVALID")
    _positive_int(result.get("fill_sequence"), "MOCK_FILL_SEQUENCE")
    if not clean_text(result.get("market_snapshot_identity")):
        raise MockValidationError("MOCK_FILL_MARKET_SNAPSHOT_IDENTITY_MISSING")
    if "source_trade_sequence" in result and result.get("source_trade_sequence") is not None:
        _positive_int(result.get("source_trade_sequence"), "MOCK_FILL_SOURCE_TRADE_SEQUENCE")
    for field in ("commission", "mock_tax"):
        if field in result:
            _finite_number(result.get(field), f"MOCK_FILL_{field.upper()}")
    if "realized_pnl" in result:
        _finite_number(result.get("realized_pnl"), "MOCK_FILL_REALIZED_PNL", nonnegative=False)
    return result


def validate_session_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise MockValidationError("MOCK_SESSION_DOCUMENT_INVALID")
    result = deepcopy(document)
    if result.get("schema_version") != MOCK_SESSION_SCHEMA_VERSION:
        raise MockValidationError("MOCK_SESSION_SCHEMA_INVALID")
    _nonnegative_int(result.get("revision"), "MOCK_SESSION_REVISION")
    session = result.get("session")
    if not isinstance(session, dict):
        raise MockValidationError("MOCK_SESSION_HEADER_INVALID")
    session_id = clean_text(session.get("validation_session_id"))
    if not session_id.startswith("MV-"):
        raise MockValidationError("MOCK_VALIDATION_SESSION_ID_INVALID")
    session["stock_code"] = normalized_stock_code(session.get("stock_code"))
    state = clean_text(session.get("state")).upper()
    if state not in SESSION_STATES:
        raise MockValidationError("MOCK_SESSION_STATE_INVALID")
    session["state"] = state
    _positive_int(session.get("session_generation"), "MOCK_SESSION_GENERATION")
    snapshot = validate_reference_snapshot(result.get("reference_snapshot"))
    if snapshot["stock_code"] != session["stock_code"]:
        raise MockValidationError("MOCK_REFERENCE_STOCK_MISMATCH")
    if snapshot["snapshot_hash"] != session.get("reference_snapshot_hash"):
        raise MockValidationError("MOCK_REFERENCE_IDENTITY_MISMATCH")
    result["reference_snapshot"] = snapshot
    instance_ids = {
        clean_text(item.get("routine_instance_id"))
        for item in snapshot["routine_instances"]
    }
    execution = result.get("instance_execution")
    if not isinstance(execution, dict) or set(execution) != instance_ids:
        raise MockValidationError("MOCK_INSTANCE_EXECUTION_SET_MISMATCH")
    for instance_id, item in execution.items():
        if not isinstance(item, dict) or item.get("routine_instance_id") != instance_id:
            raise MockValidationError("MOCK_INSTANCE_EXECUTION_INVALID")
        if clean_text(item.get("state")).upper() not in SESSION_STATES:
            raise MockValidationError("MOCK_INSTANCE_EXECUTION_STATE_INVALID")
        if not isinstance(item.get("progression_allowed"), bool):
            raise MockValidationError("MOCK_INSTANCE_PROGRESSION_FLAG_INVALID")
    for key in ("orders", "fills", "positions", "pnl"):
        if not isinstance(result.get(key), list):
            raise MockValidationError(f"MOCK_{key.upper()}_LEDGER_INVALID")
    order_ids: set[str] = set()
    for order in result["orders"]:
        checked = validate_mock_order(order, session)
        order_id = checked["mock_order_id"]
        if order_id in order_ids or checked.get("routine_instance_id") not in instance_ids:
            raise MockValidationError("MOCK_ORDER_IDENTITY_CONFLICT")
        order_ids.add(order_id)
    fill_ids: set[str] = set()
    for fill in result["fills"]:
        checked = validate_mock_fill(fill, session)
        fill_id = checked["mock_fill_id"]
        if (
            fill_id in fill_ids
            or checked.get("mock_order_id") not in order_ids
            or checked.get("routine_instance_id") not in instance_ids
        ):
            raise MockValidationError("MOCK_FILL_IDENTITY_CONFLICT")
        fill_ids.add(fill_id)
    for ledger_name in ("positions", "pnl"):
        records = result[ledger_name]
        record_ids = [clean_text(item.get("routine_instance_id")) for item in records if isinstance(item, dict)]
        if len(records) != len(instance_ids) or set(record_ids) != instance_ids:
            raise MockValidationError(f"MOCK_{ledger_name.upper()}_INSTANCE_SET_MISMATCH")
        for item in records:
            if not isinstance(item, dict):
                raise MockValidationError(f"MOCK_{ledger_name.upper()}_ENTRY_INVALID")
            if item.get("validation_session_id") != session_id:
                raise MockValidationError(f"MOCK_{ledger_name.upper()}_SESSION_MISMATCH")
            if normalized_stock_code(item.get("stock_code")) != session["stock_code"]:
                raise MockValidationError(f"MOCK_{ledger_name.upper()}_STOCK_MISMATCH")
            if ledger_name == "positions":
                holding = _nonnegative_int(item.get("holding_qty"), "MOCK_POSITION_HOLDING_QTY")
                available = _nonnegative_int(item.get("available_qty"), "MOCK_POSITION_AVAILABLE_QTY")
                if available > holding:
                    raise MockValidationError("MOCK_POSITION_AVAILABLE_EXCEEDS_HOLDING")
                _finite_number(item.get("average_price"), "MOCK_POSITION_AVERAGE_PRICE")
                _finite_number(item.get("realized_cost_basis"), "MOCK_POSITION_REALIZED_COST_BASIS")
            else:
                for field in ("realized_pnl", "unrealized_pnl", "gross_pnl", "net_pnl"):
                    _finite_number(item.get(field), f"MOCK_PNL_{field.upper()}", nonnegative=False)
                for field in ("commission", "mock_tax"):
                    _finite_number(item.get(field), f"MOCK_PNL_{field.upper()}")
                calculated = calculate_mock_pnl(
                    realized_pnl=item.get("realized_pnl"),
                    unrealized_pnl=item.get("unrealized_pnl"),
                    commission=item.get("commission"),
                    mock_tax=item.get("mock_tax"),
                )
                if item.get("gross_pnl") != calculated["gross_pnl"]:
                    raise MockValidationError("MOCK_PNL_GROSS_MISMATCH")
                if item.get("net_pnl") != calculated["net_pnl"]:
                    raise MockValidationError("MOCK_PNL_NET_MISMATCH")
    review = result.get("review")
    if not isinstance(review, dict):
        raise MockValidationError("MOCK_REVIEW_STATE_INVALID")
    if not isinstance(review.get("review_required"), bool):
        raise MockValidationError("MOCK_REVIEW_REQUIRED_FLAG_INVALID")
    review_source = clean_text(review.get("source_routine_instance_id"))
    if review.get("review_required") is True:
        if review_source not in instance_ids or not clean_text(review.get("occurred_at")):
            raise MockValidationError("MOCK_REVIEW_SOURCE_INVALID")
        if session["state"] != SESSION_REVIEW_STOPPED:
            raise MockValidationError("MOCK_REVIEW_SESSION_STATE_MISMATCH")
    for key in ("cycle_state_by_instance", "progression_by_instance"):
        value = result.get(key)
        if not isinstance(value, dict) or set(value) != instance_ids:
            raise MockValidationError(f"MOCK_{key.upper()}_SET_MISMATCH")
    if not isinstance(result.get("applied_commands"), dict):
        raise MockValidationError("MOCK_APPLIED_COMMANDS_INVALID")
    if any(not isinstance(value, dict) for value in result["applied_commands"].values()):
        raise MockValidationError("MOCK_APPLIED_COMMAND_ENTRY_INVALID")
    return result


def transition_mock_order(order: dict[str, Any], next_state: str, *, occurred_at: str) -> dict[str, Any]:
    current = clean_text(order.get("state")).upper()
    target = clean_text(next_state).upper()
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise MockValidationError(f"MOCK_ORDER_TRANSITION_INVALID:{current}->{target}")
    result = deepcopy(order)
    result["state"] = target
    result["updated_at"] = clean_text(occurred_at)
    if target == ORDER_CANCELED:
        result["canceled_at"] = clean_text(occurred_at)
    return result


def mutate_copy(value: Any, mutator: Callable[[Any], None]) -> Any:
    result = deepcopy(value)
    mutator(result)
    return result


__all__ = [name for name in globals() if name.startswith(("MOCK_", "SESSION_", "ORDER_"))] + [
    "MockValidationError",
    "canonical_json_bytes",
    "clean_text",
    "deterministic_mock_identity",
    "initial_session_document",
    "new_mock_identity",
    "normalized_stock_code",
    "now_text",
    "payload_hash",
    "transition_mock_order",
    "validate_mock_fill",
    "validate_mock_order",
    "validate_reference_snapshot",
    "validate_session_document",
]
