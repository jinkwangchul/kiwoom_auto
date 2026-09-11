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

from manual_ats_runtime import (
    VALID_SESSION_KEYS,
    normalized_manual_ats_session_keys,
)


MOCK_SESSION_SCHEMA_VERSION = "mock_validation_session_v1"
MOCK_EVENT_SCHEMA_VERSION = "mock_validation_event_v1"
MOCK_HISTORY_SCHEMA_VERSION = "mock_validation_history_v1"
MOCK_CURRENT_INDEX_SCHEMA_VERSION = "mock_validation_current_index_v1"
MOCK_SETTINGS_SCHEMA_VERSION = "mock_validation_settings_v1"

SESSION_WAITING = "WAITING"
SESSION_RUNNING = "RUNNING"
SESSION_REVIEW_STOPPED = "REVIEW_STOPPED"
SESSION_CLOSING = "CLOSING"
SESSION_ENDED = "ENDED"
INSTANCE_ERROR = "ERROR"
INSTANCE_VALIDATION_STOPPED = "VALIDATION_STOPPED"
SESSION_STATES = {
    SESSION_WAITING,
    SESSION_RUNNING,
    SESSION_REVIEW_STOPPED,
    SESSION_CLOSING,
    SESSION_ENDED,
}
INSTANCE_EXECUTION_STATES = SESSION_STATES | {
    INSTANCE_ERROR,
    INSTANCE_VALIDATION_STOPPED,
}
MOCK_INSTANCE_OPERATION_MODES = {"SCHEDULED", "CONTINUOUS"}
LEGACY_MOCK_INSTANCE_OPERATION_MODES = {"MANUAL", "MANUAL_ATS"}

MOCK_BUDGET_POLICY_PRE_OPERATION = "PRE_OPERATION"
MOCK_BUDGET_POLICY_IMMEDIATE = "IMMEDIATE"
MOCK_BUDGET_POLICY_NEXT_CYCLE = "NEXT_CYCLE"
MOCK_BUDGET_STATE_WAIT_FIRST_BUY = "WAIT_FIRST_BUY"
MOCK_BUDGET_STATE_WAIT_SELL = "WAIT_SELL"
MOCK_BUDGET_STATE_APPLIED = "APPLIED"
MOCK_BUDGET_ACTIVE_STATES = {
    MOCK_BUDGET_STATE_WAIT_FIRST_BUY,
    MOCK_BUDGET_STATE_WAIT_SELL,
    MOCK_BUDGET_STATE_APPLIED,
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
    "VALIDATION_STOP_REQUESTED",
    "VALIDATION_STOP_COMPLETED",
    "INSTANCE_RESET",
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
    "BUY_RECOVERY_GENERATION_STARTED",
    "BUY_EXIT_TRIGGERED",
    "BUY_EXIT_CONFIRMED",
    "SELL_REPEAT_TRIGGERED",
    "SELL_REPEAT_GENERATION_STARTED",
    "SELL_REPEAT_EXIT_TRIGGERED",
    "FINAL_RESIDUAL_MARKET_STARTED",
    "CONTINUATION_BLOCKED",
    "ROUTINE_SIGNAL_IGNORED",
    "OPERATION_SESSION_CREATED",
    "OPERATION_STARTED",
    "NORMAL_CLOSE_REQUESTED",
    "AUTO_CLOSE_REQUESTED",
    "EARLY_CLOSE_REQUESTED",
    "EARLY_CLOSE_CANCELLED",
    "ROUTINE_CLOSE_FINAL_SELL_ACCEPTED",
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


def default_mock_settings_document() -> dict[str, Any]:
    return {
        "schema_version": MOCK_SETTINGS_SCHEMA_VERSION,
        "revision": 0,
        "mock_tax_enabled": True,
        "mock_tax_rate": 0.002,
    }


def validate_mock_settings_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MockValidationError("MOCK_SETTINGS_INVALID")
    if value.get("schema_version") != MOCK_SETTINGS_SCHEMA_VERSION:
        raise MockValidationError("MOCK_SETTINGS_SCHEMA_INVALID")
    if not isinstance(value.get("mock_tax_enabled"), bool):
        raise MockValidationError("MOCK_TAX_ENABLED_INVALID")
    rate = _finite_number(value.get("mock_tax_rate"), "MOCK_TAX_RATE")
    if float(rate) > 1:
        raise MockValidationError("MOCK_TAX_RATE_INVALID")
    result = {
        "schema_version": MOCK_SETTINGS_SCHEMA_VERSION,
        "revision": _nonnegative_int(value.get("revision"), "MOCK_SETTINGS_REVISION"),
        "mock_tax_enabled": value["mock_tax_enabled"],
        "mock_tax_rate": rate,
    }
    if "updated_at" in value:
        result["updated_at"] = clean_text(value.get("updated_at"))
    return result


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


def _mock_time_text(value: Any, field: str, default: str) -> str:
    text = clean_text(value) or default
    parts = text.split(":")
    if len(parts) == 2:
        parts.append("00")
    if len(parts) != 3:
        raise MockValidationError(f"{field}_INVALID")
    try:
        hour, minute, second = (int(part) for part in parts)
    except ValueError as exc:
        raise MockValidationError(f"{field}_INVALID") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise MockValidationError(f"{field}_INVALID")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def validate_instance_effective_settings(
    value: Any,
    *,
    preserve_legacy_representation: bool = False,
) -> dict[str, Any]:
    """Validate one mutable, Mock-owned Routine Instance setting snapshot."""

    if not isinstance(value, dict) or set(value) not in ({
        "initial_buy",
        "operation_schedule",
        "operation_mode",
    }, {
        "initial_buy",
        "operation_schedule",
        "operation_mode",
        "manual_ats",
    }):
        raise MockValidationError("MOCK_INSTANCE_EFFECTIVE_SETTINGS_INVALID")
    initial = value.get("initial_buy")
    if not isinstance(initial, dict) or set(initial) != {"mode", "value"}:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_INVALID")
    mode = clean_text(initial.get("mode")).upper()
    if mode not in {"QUANTITY", "AMOUNT"}:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_MODE_INVALID")
    amount = _positive_int(initial.get("value"), "MOCK_INSTANCE_INITIAL_BUY_VALUE")
    schedule = value.get("operation_schedule")
    if not isinstance(schedule, dict) or set(schedule) != {
        "start_time",
        "end_buy_time",
    }:
        raise MockValidationError("MOCK_INSTANCE_OPERATION_SCHEDULE_INVALID")
    start_time = _mock_time_text(
        schedule.get("start_time"),
        "MOCK_INSTANCE_OPERATION_START_TIME",
        "09:00:00",
    )
    end_buy_time = _mock_time_text(
        schedule.get("end_buy_time"),
        "MOCK_INSTANCE_OPERATION_END_BUY_TIME",
        "13:30:00",
    )
    start_parts = [int(part) for part in start_time.split(":")]
    end_parts = [int(part) for part in end_buy_time.split(":")]
    if tuple(start_parts) >= tuple(end_parts):
        raise MockValidationError("MOCK_INSTANCE_OPERATION_SCHEDULE_RANGE_INVALID")
    raw_operation_mode = clean_text(value.get("operation_mode")).upper()
    if raw_operation_mode not in (
        MOCK_INSTANCE_OPERATION_MODES | LEGACY_MOCK_INSTANCE_OPERATION_MODES
    ):
        raise MockValidationError("MOCK_INSTANCE_OPERATION_MODE_INVALID")
    operation_mode = (
        "CONTINUOUS"
        if raw_operation_mode in LEGACY_MOCK_INSTANCE_OPERATION_MODES
        else raw_operation_mode
    )
    manual_ats = value.get("manual_ats")
    if manual_ats is None:
        selected_sessions = (
            VALID_SESSION_KEYS if raw_operation_mode == "MANUAL_ATS" else ()
        )
    else:
        if not isinstance(manual_ats, dict):
            raise MockValidationError("MOCK_INSTANCE_MANUAL_ATS_INVALID")
        manual_keys = set(manual_ats)
        if "selected_sessions" not in manual_keys or not manual_keys.issubset(
            {"selected_sessions", "execution_method"}
        ):
            raise MockValidationError("MOCK_INSTANCE_MANUAL_ATS_INVALID")
        raw_sessions = manual_ats.get("selected_sessions")
        selected_sessions = normalized_manual_ats_session_keys(raw_sessions)
        supplied_sessions = (
            tuple(str(item or "").strip() for item in raw_sessions)
            if isinstance(raw_sessions, (list, tuple, set))
            else ()
        )
        if len(supplied_sessions) != len(selected_sessions) or set(
            supplied_sessions
        ) != set(selected_sessions):
            raise MockValidationError("MOCK_INSTANCE_MANUAL_ATS_SESSIONS_INVALID")
    checked = {
        "initial_buy": {"mode": mode, "value": amount},
        "operation_schedule": {
            "start_time": start_time,
            "end_buy_time": end_buy_time,
        },
        "operation_mode": operation_mode,
        "manual_ats": {
            "selected_sessions": list(selected_sessions),
        },
    }
    if (
        preserve_legacy_representation
        and isinstance(manual_ats, dict)
        and "execution_method" in manual_ats
    ):
        # Stored legacy evidence is retained verbatim, but consumers call this
        # validator without preservation and therefore never receive it as an
        # effective Mock setting.
        checked["manual_ats"]["execution_method"] = deepcopy(
            manual_ats.get("execution_method")
        )
    if (
        preserve_legacy_representation
        and raw_operation_mode in LEGACY_MOCK_INSTANCE_OPERATION_MODES
        and manual_ats is None
    ):
        checked["operation_mode"] = raw_operation_mode
        checked.pop("manual_ats", None)
    return checked


def validate_instance_initial_buy_adjustment(value: Any) -> dict[str, Any]:
    """Validate a running Mock Instance's Production-parity budget request."""

    if not isinstance(value, dict):
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_INVALID")
    required = {
        "version",
        "request_id",
        "routine_instance_id",
        "operation_session_id",
        "mode",
        "requested_value",
        "previous_value",
        "apply_policy",
        "state",
        "requested_at",
        "confirmed_at",
    }
    optional = {
        "last_transition_at",
        "last_transition_signal",
        "last_transition_signal_id",
        "sell_observed_at",
        "sell_signal_id",
        "applied_at",
        "applied_signal_id",
    }
    if not required.issubset(value) or set(value) - required - optional:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_INVALID")
    if value.get("version") != 1:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_VERSION_INVALID")
    request_id = clean_text(value.get("request_id"))
    instance_id = clean_text(value.get("routine_instance_id"))
    operation_id = clean_text(value.get("operation_session_id"))
    if not request_id.startswith("MC-") or not instance_id or not operation_id.startswith("MS-"):
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_IDENTITY_INVALID")
    mode = clean_text(value.get("mode")).upper()
    if mode not in {"QUANTITY", "AMOUNT"}:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_MODE_INVALID")
    requested_value = _positive_int(
        value.get("requested_value"),
        "MOCK_INSTANCE_INITIAL_BUY_VALUE",
    )
    previous_value = _positive_int(
        value.get("previous_value"),
        "MOCK_INSTANCE_INITIAL_BUY_PREVIOUS_VALUE",
    )
    if requested_value > 99_999_999 or previous_value > 99_999_999:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_VALUE_INVALID")
    policy = clean_text(value.get("apply_policy")).upper()
    if policy not in {
        MOCK_BUDGET_POLICY_IMMEDIATE,
        MOCK_BUDGET_POLICY_NEXT_CYCLE,
    }:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_APPLY_POLICY_INVALID")
    state = clean_text(value.get("state")).upper()
    if state not in MOCK_BUDGET_ACTIVE_STATES:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_STATE_INVALID")
    requested_at = clean_text(value.get("requested_at"))
    confirmed_at = clean_text(value.get("confirmed_at"))
    if not requested_at or not confirmed_at:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_TIMESTAMP_INVALID")
    result = {
        "version": 1,
        "request_id": request_id,
        "routine_instance_id": instance_id,
        "operation_session_id": operation_id,
        "mode": mode,
        "requested_value": requested_value,
        "previous_value": previous_value,
        "apply_policy": policy,
        "state": state,
        "requested_at": requested_at,
        "confirmed_at": confirmed_at,
    }
    for key in optional:
        if key in value:
            result[key] = clean_text(value.get(key))
    return result


def instance_initial_buy_adjustment(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any] | None:
    instance_id = clean_text(routine_instance_id)
    adjustments = document.get("initial_buy_adjustments_by_instance")
    raw = adjustments.get(instance_id) if isinstance(adjustments, dict) else None
    if not isinstance(raw, dict):
        return None
    checked = validate_instance_initial_buy_adjustment(raw)
    if checked["routine_instance_id"] != instance_id:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_IDENTITY_INVALID")
    return checked


def default_instance_effective_settings(
    reference_snapshot: dict[str, Any],
    routine_instance_id: str,
) -> dict[str, Any]:
    """Derive a legacy-safe mutable default without reading Production state."""

    instance_id = clean_text(routine_instance_id)
    records = [
        item
        for item in reference_snapshot.get("routine_instances", ())
        if isinstance(item, dict)
        and clean_text(item.get("routine_instance_id")) == instance_id
    ]
    if len(records) != 1:
        raise MockValidationError("MOCK_ROUTINE_INSTANCE_SNAPSHOT_INVALID")
    rules = records[0].get("rules_snapshot")
    rules = rules if isinstance(rules, dict) else {}
    mock_settings = rules.get("mock_validation")
    mock_settings = mock_settings if isinstance(mock_settings, dict) else {}
    stock_config = mock_settings.get("stock_config")
    if not isinstance(stock_config, dict):
        stock_config = rules.get("stock_config")
    stock_config = stock_config if isinstance(stock_config, dict) else {}

    display = reference_snapshot.get("display_contract")
    display = display if isinstance(display, dict) else {}
    display_initial = display.get("initial_buy")
    display_initial = display_initial if isinstance(display_initial, dict) else {}
    initial_mode = clean_text(
        display_initial.get("mode") or stock_config.get("trade_amount_type")
    ).upper()
    if initial_mode not in {"QUANTITY", "AMOUNT"}:
        initial_mode = "QUANTITY"
    configured_key = "buy_amount" if initial_mode == "AMOUNT" else "buy_qty"
    initial_value = display_initial.get("value")
    try:
        initial_value = int(initial_value)
    except (TypeError, ValueError):
        initial_value = 0
    if initial_value <= 0:
        try:
            initial_value = int(stock_config.get(configured_key, 0) or 0)
        except (TypeError, ValueError):
            initial_value = 0
    if initial_value <= 0:
        initial_value = 1

    schedule_display = display.get("operation_schedule")
    schedule_text = (
        clean_text(schedule_display.get("display_text"))
        if isinstance(schedule_display, dict)
        else ""
    )
    operation_mode = clean_text(stock_config.get("operation_mode")).upper()
    if schedule_text == "수동+ATS":
        operation_mode = "MANUAL_ATS"
    elif schedule_text == "수동" or operation_mode in {"CONTINUOUS", "MANUAL"}:
        operation_mode = "MANUAL"
    else:
        operation_mode = "SCHEDULED"

    display_start = ""
    display_end = ""
    if "~" in schedule_text:
        display_start, display_end = schedule_text.split("~", 1)
    return validate_instance_effective_settings(
        {
            "initial_buy": {"mode": initial_mode, "value": initial_value},
            "operation_schedule": {
                "start_time": display_start
                or stock_config.get("start_time")
                or stock_config.get("trade_start_time")
                or "09:00:00",
                "end_buy_time": display_end
                or stock_config.get("end_buy_time")
                or stock_config.get("buy_end_time")
                or "13:30:00",
            },
            "operation_mode": operation_mode,
            "manual_ats": {
                "selected_sessions": (
                    list(VALID_SESSION_KEYS)
                    if operation_mode == "MANUAL_ATS"
                    else []
                ),
            },
        }
    )


def instance_effective_settings(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any]:
    instance_id = clean_text(routine_instance_id)
    settings = document.get("effective_settings_by_instance")
    if isinstance(settings, dict) and isinstance(settings.get(instance_id), dict):
        return validate_instance_effective_settings(settings[instance_id])
    reference = document.get("reference_snapshot")
    if not isinstance(reference, dict):
        raise MockValidationError("MOCK_REFERENCE_SNAPSHOT_INVALID")
    return default_instance_effective_settings(reference, instance_id)


def mock_instance_active_operation(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any] | None:
    """Return the target Instance's current RUNNING/CLOSING operation, if any."""

    instance_id = clean_text(routine_instance_id)
    lifecycle = document.get("mock_operation_lifecycle")
    if not isinstance(lifecycle, dict):
        return None
    operations = lifecycle.get("instance_operations")
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    if (
        isinstance(operation, dict)
        and clean_text(operation.get("state")).upper()
        in {SESSION_RUNNING, SESSION_CLOSING}
    ):
        return operation
    current = lifecycle.get("current")
    current_instance_id = (
        clean_text(current.get("routine_instance_id"))
        if isinstance(current, dict)
        else ""
    )
    if (
        isinstance(current, dict)
        and current_instance_id in {"", instance_id}
        and clean_text(current.get("state")).upper()
        in {SESSION_RUNNING, SESSION_CLOSING}
    ):
        return current
    return None


def mock_instance_pre_start_editable(
    document: dict[str, Any], routine_instance_id: str
) -> bool:
    """Project whether next-start Mock settings are editable without mutation."""

    instance_id = clean_text(routine_instance_id)
    execution = document.get("instance_execution")
    if not isinstance(execution, dict) or not isinstance(execution.get(instance_id), dict):
        return False
    lifecycle = document.get("mock_operation_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    return (
        clean_text(document.get("session", {}).get("state")).upper()
        == SESSION_WAITING
        and clean_text(execution[instance_id].get("state")).upper()
        in {SESSION_WAITING, INSTANCE_VALIDATION_STOPPED}
        and not isinstance(lifecycle.get("current"), dict)
        and mock_instance_active_operation(document, instance_id) is None
    )


def mock_instance_active_effective_settings(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any] | None:
    """Return the immutable settings snapshot only for an active operation."""

    instance_id = clean_text(routine_instance_id)
    operation = mock_instance_active_operation(document, instance_id)
    snapshot = (
        operation.get("operation_policy_snapshot")
        if isinstance(operation, dict)
        else None
    )
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    direct = snapshot.get("mock_instance_effective_settings")
    if isinstance(direct, dict):
        return deepcopy(direct)
    by_instance = snapshot.get("mock_effective_settings_by_instance")
    if isinstance(by_instance, dict) and isinstance(by_instance.get(instance_id), dict):
        return deepcopy(by_instance[instance_id])
    return None


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
    display_contract = result.get("display_contract")
    if display_contract is not None:
        if not isinstance(display_contract, dict) or set(display_contract) != {
            "initial_buy",
            "operation_schedule",
            "liquidation",
        }:
            raise MockValidationError("MOCK_DISPLAY_CONTRACT_INVALID")
        initial_buy = display_contract.get("initial_buy")
        if not isinstance(initial_buy, dict) or set(initial_buy) != {
            "mode",
            "badge",
            "value",
            "value_text",
        }:
            raise MockValidationError("MOCK_INITIAL_BUY_DISPLAY_INVALID")
        mode = clean_text(initial_buy.get("mode")).upper()
        badge = clean_text(initial_buy.get("badge"))
        if mode not in {"QUANTITY", "AMOUNT"}:
            raise MockValidationError("MOCK_INITIAL_BUY_MODE_INVALID")
        if badge != ("주수" if mode == "QUANTITY" else "금액"):
            raise MockValidationError("MOCK_INITIAL_BUY_BADGE_INVALID")
        initial_buy["mode"] = mode
        initial_buy["badge"] = badge
        initial_buy["value"] = _nonnegative_int(
            initial_buy.get("value"),
            "MOCK_INITIAL_BUY_VALUE",
        )
        initial_buy["value_text"] = clean_text(initial_buy.get("value_text"))
        if not initial_buy["value_text"]:
            raise MockValidationError("MOCK_INITIAL_BUY_VALUE_TEXT_INVALID")
        for key, error_code in (
            ("operation_schedule", "MOCK_OPERATION_SCHEDULE_DISPLAY_INVALID"),
            ("liquidation", "MOCK_LIQUIDATION_DISPLAY_INVALID"),
        ):
            item = display_contract.get(key)
            if not isinstance(item, dict) or set(item) != {"display_text"}:
                raise MockValidationError(error_code)
            item["display_text"] = clean_text(item.get("display_text"))
            if not item["display_text"]:
                raise MockValidationError(error_code)
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
    effective_settings_by_instance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = clean_text(validation_session_id)
    if not session_id.startswith("MV-"):
        raise MockValidationError("MOCK_VALIDATION_SESSION_ID_INVALID")
    snapshot = validate_reference_snapshot(reference_snapshot)
    tax_rate = _finite_number(mock_tax_rate, "MOCK_TAX_RATE")
    if float(tax_rate) > 1:
        raise MockValidationError("MOCK_TAX_RATE_INVALID")
    instance_ids = [item["routine_instance_id"] for item in snapshot["routine_instances"]]
    if effective_settings_by_instance is None:
        initial_effective_settings = {
            instance_id: default_instance_effective_settings(snapshot, instance_id)
            for instance_id in instance_ids
        }
    else:
        if set(effective_settings_by_instance) != set(instance_ids):
            raise MockValidationError("MOCK_INSTANCE_EFFECTIVE_SETTINGS_SET_MISMATCH")
        initial_effective_settings = {
            instance_id: validate_instance_effective_settings(
                effective_settings_by_instance[instance_id]
            )
            for instance_id in instance_ids
        }
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
        "effective_settings_by_instance": initial_effective_settings,
        "initial_buy_adjustments_by_instance": {},
        "instance_execution": {
            instance_id: {
                "routine_instance_id": instance_id,
                "state": SESSION_WAITING,
                "started_at": "",
                "progression_allowed": False,
                "error_code": "",
                "error_reason": "",
                "error_occurred_at": "",
                "error_cleared_at": "",
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
    effective_settings = result.get("effective_settings_by_instance")
    if effective_settings is not None:
        if not isinstance(effective_settings, dict) or set(effective_settings) != instance_ids:
            raise MockValidationError("MOCK_INSTANCE_EFFECTIVE_SETTINGS_SET_MISMATCH")
        result["effective_settings_by_instance"] = {
            instance_id: validate_instance_effective_settings(
                effective_settings[instance_id],
                preserve_legacy_representation=True,
            )
            for instance_id in sorted(instance_ids)
        }
    adjustments = result.get("initial_buy_adjustments_by_instance", {})
    if not isinstance(adjustments, dict) or not set(adjustments).issubset(instance_ids):
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_SET_MISMATCH")
    result["initial_buy_adjustments_by_instance"] = {
        instance_id: validate_instance_initial_buy_adjustment(adjustments[instance_id])
        for instance_id in sorted(adjustments)
    }
    if any(
        adjustment["routine_instance_id"] != instance_id
        for instance_id, adjustment in result[
            "initial_buy_adjustments_by_instance"
        ].items()
    ):
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_IDENTITY_INVALID")
    execution = result.get("instance_execution")
    if not isinstance(execution, dict) or set(execution) != instance_ids:
        raise MockValidationError("MOCK_INSTANCE_EXECUTION_SET_MISMATCH")
    for instance_id, item in execution.items():
        if not isinstance(item, dict) or item.get("routine_instance_id") != instance_id:
            raise MockValidationError("MOCK_INSTANCE_EXECUTION_INVALID")
        if clean_text(item.get("state")).upper() not in INSTANCE_EXECUTION_STATES:
            raise MockValidationError("MOCK_INSTANCE_EXECUTION_STATE_INVALID")
        item["state"] = clean_text(item.get("state")).upper()
        if not isinstance(item.get("progression_allowed"), bool):
            raise MockValidationError("MOCK_INSTANCE_PROGRESSION_FLAG_INVALID")
        for field in (
            "error_code",
            "error_reason",
            "error_occurred_at",
            "error_cleared_at",
        ):
            item[field] = clean_text(item.get(field))
        if item["state"] == INSTANCE_ERROR:
            if not item["error_code"] or not item["error_occurred_at"]:
                raise MockValidationError("MOCK_INSTANCE_ERROR_EVIDENCE_INVALID")
            if item.get("progression_allowed") is not False:
                raise MockValidationError("MOCK_INSTANCE_ERROR_PROGRESSION_INVALID")
        if (
            item["state"] == INSTANCE_VALIDATION_STOPPED
            and item.get("progression_allowed") is not False
        ):
            raise MockValidationError("MOCK_INSTANCE_VALIDATION_STOP_PROGRESSION_INVALID")
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
    "INSTANCE_ERROR",
    "INSTANCE_VALIDATION_STOPPED",
    "INSTANCE_EXECUTION_STATES",
    "MockValidationError",
    "canonical_json_bytes",
    "clean_text",
    "default_instance_effective_settings",
    "deterministic_mock_identity",
    "initial_session_document",
    "instance_initial_buy_adjustment",
    "instance_effective_settings",
    "mock_instance_active_effective_settings",
    "mock_instance_active_operation",
    "mock_instance_pre_start_editable",
    "new_mock_identity",
    "normalized_stock_code",
    "now_text",
    "payload_hash",
    "transition_mock_order",
    "validate_mock_fill",
    "validate_mock_order",
    "validate_instance_effective_settings",
    "validate_instance_initial_buy_adjustment",
    "validate_reference_snapshot",
    "validate_session_document",
]
