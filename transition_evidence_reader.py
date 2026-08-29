# -*- coding: utf-8 -*-
"""Read-only Queue/Fill evidence for Close/Liquidation transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Iterable

from close_liquidation_transition_service import TransitionEvidence
from execution_fill_recorder import read_execution_fill_records
from execution_queue_writer import read_execution_queue_records
from stock_code_contract import normalize_broker_stock_code


PRESENT: Final = "PRESENT"
ABSENT: Final = "ABSENT"
UNKNOWN: Final = "UNKNOWN"
COMMAND_REQUEST_SCOPE: Final = "COMMAND_REQUEST"
TIME_POLICY_SCOPE: Final = "TIME_POLICY"

_MATCH: Final = "MATCH"
_SKIP: Final = "SKIP"
_INDETERMINATE: Final = "INDETERMINATE"

_TIMESTAMP_KEYS: Final = frozenset(
    {
        "created_at",
        "updated_at",
        "requested_at",
        "queued_at",
        "recorded_at",
        "received_at",
        "send_order_called_at",
        "claimed_at",
    }
)
_CODE_KEYS: Final = frozenset({"code", "stock_code", "target_stock"})
_ROUTINE_INSTANCE_KEYS: Final = frozenset(
    {"routine_instance_id", "assigned_routine_instance_id"}
)
_COMMAND_KEYS: Final = frozenset({"operation_command_id", "command_id"})


@dataclass(frozen=True)
class TransitionEvidenceScope:
    scope_type: str
    stock_code: str
    trade_date: str
    routine_instance_id: str
    transition_requested_at: str = ""
    auto_close_requested_at: str = ""
    source: str = ""
    operation_command_id: str = ""

    @property
    def started_at(self) -> str:
        if _text(self.scope_type).upper() == TIME_POLICY_SCOPE:
            return _text(self.auto_close_requested_at)
        return _text(self.transition_requested_at)


@dataclass(frozen=True)
class QueueTransitionEvidence:
    order_created: str
    cancellation_started: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class FillTransitionEvidence:
    buy_fill_detected: str
    sell_fill_detected: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionEvidenceBuildResult:
    routine_close_started: str
    order_created: str
    buy_fill_detected: str
    sell_fill_detected: str
    cancellation_started: str
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(
            value in {PRESENT, ABSENT}
            for value in (
                self.routine_close_started,
                self.order_created,
                self.buy_fill_detected,
                self.sell_fill_detected,
                self.cancellation_started,
            )
        )

    def to_transition_evidence(self) -> TransitionEvidence | None:
        if not self.complete:
            return None
        return TransitionEvidence(
            routine_close_action_started=self.routine_close_started == PRESENT,
            actual_order_created=self.order_created == PRESENT,
            buy_occurred=self.buy_fill_detected == PRESENT,
            sell_occurred=self.sell_fill_detected == PRESENT,
            pending_order_cancellation_started=self.cancellation_started == PRESENT,
        )


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_code(value: object) -> str:
    return normalize_broker_stock_code(value)


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        return None


def _named_values(value: object, names: frozenset[str]) -> list[object]:
    values: list[object] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in names:
                values.append(child)
            if isinstance(child, (dict, list)):
                values.extend(_named_values(child, names))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                values.extend(_named_values(child, names))
    return values


def _scope_errors(scope: TransitionEvidenceScope) -> tuple[str, ...]:
    errors: list[str] = []
    scope_type = _text(scope.scope_type).upper()
    if scope_type not in {COMMAND_REQUEST_SCOPE, TIME_POLICY_SCOPE}:
        errors.append("scope.scope_type is invalid")
    if not _normalized_code(scope.stock_code):
        errors.append("scope.stock_code is required")
    trade_date = _text(scope.trade_date)
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        errors.append("scope.trade_date is invalid")
    if not _text(scope.routine_instance_id):
        errors.append("scope.routine_instance_id is required")
    if _parse_datetime(scope.started_at) is None:
        errors.append("scope start time is invalid")
    if (
        scope_type == TIME_POLICY_SCOPE
        and _text(scope.source).upper() != TIME_POLICY_SCOPE
    ):
        errors.append("TIME_POLICY scope.source must be TIME_POLICY")
    return tuple(errors)


def _identity_match(values: Iterable[object], expected: str) -> str:
    normalized = {_text(value) for value in values if _text(value)}
    if not normalized:
        return _INDETERMINATE
    return _MATCH if expected in normalized else _SKIP


def _record_scope_match(
    record: dict[str, object],
    scope: TransitionEvidenceScope,
) -> str:
    code_values = _named_values(record, _CODE_KEYS)
    normalized_codes = {
        _normalized_code(value) for value in code_values if _normalized_code(value)
    }
    if not normalized_codes:
        return _INDETERMINATE
    if _normalized_code(scope.stock_code) not in normalized_codes:
        return _SKIP

    timestamps = [
        parsed
        for parsed in (
            _parse_datetime(value)
            for value in _named_values(record, _TIMESTAMP_KEYS)
        )
        if parsed is not None
    ]
    if not timestamps:
        return _INDETERMINATE
    started_at = _parse_datetime(scope.started_at)
    if started_at is None:
        return _INDETERMINATE
    eligible_timestamps = [
        value
        for value in timestamps
        if value.date().isoformat() == _text(scope.trade_date)
        and value >= started_at
    ]
    if not eligible_timestamps:
        return _SKIP

    routine_match = _identity_match(
        _named_values(record, _ROUTINE_INSTANCE_KEYS),
        _text(scope.routine_instance_id),
    )
    if routine_match != _MATCH:
        return routine_match

    command_id = _text(scope.operation_command_id)
    if not command_id:
        return _MATCH
    return _identity_match(_named_values(record, _COMMAND_KEYS), command_id)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() not in {"", "0", "false", "none", "no", "n"}


def _order_action(record: dict[str, object]) -> str:
    actions = {
        _text(value).upper()
        for value in _named_values(record, frozenset({"order_action"}))
        if _text(value)
    }
    return "CANCEL" if "CANCEL" in actions else next(iter(actions), "")


def _order_side(record: dict[str, object]) -> str:
    sides = {
        _text(value).upper()
        for value in _named_values(record, frozenset({"side"}))
        if _text(value)
    }
    if len(sides) != 1:
        return ""
    return next(iter(sides))


def _committed_record(record: dict[str, object]) -> bool:
    if _truthy(record.get("preview_only")) or _truthy(record.get("no_write")):
        return False
    return bool(_text(record.get("status")) or _text(record.get("id")))


def read_transition_queue_evidence(
    queue_path: str | Path,
    scope: TransitionEvidenceScope,
) -> QueueTransitionEvidence:
    scope_errors = _scope_errors(scope)
    if scope_errors:
        return QueueTransitionEvidence(UNKNOWN, UNKNOWN, scope_errors)
    read_result = read_execution_queue_records(queue_path)
    if read_result.get("ok") is not True:
        return QueueTransitionEvidence(
            UNKNOWN,
            UNKNOWN,
            (f"Queue read failed: {read_result.get('error')}",),
        )
    records = [
        dict(item)
        for item in read_result.get("records", ())
        if isinstance(item, dict)
    ]

    order_created = False
    cancellation_started = False
    indeterminate = False
    for record in records:
        match = _record_scope_match(record, scope)
        if match == _INDETERMINATE:
            if _normalized_code(scope.stock_code) in {
                _normalized_code(value)
                for value in _named_values(record, _CODE_KEYS)
            }:
                indeterminate = True
            continue
        if match != _MATCH or not _committed_record(record):
            continue
        action = _order_action(record)
        side = _order_side(record)
        if action not in {"NEW", "MODIFY", "CANCEL"} or side not in {
            "BUY",
            "SELL",
            "매수",
            "매도",
        }:
            indeterminate = True
            continue
        if action == "CANCEL":
            cancellation_started = True
        else:
            order_created = True

    return QueueTransitionEvidence(
        PRESENT if order_created else UNKNOWN if indeterminate else ABSENT,
        PRESENT if cancellation_started else UNKNOWN if indeterminate else ABSENT,
        ("matching stock Queue evidence lacks required scope identity",)
        if indeterminate
        else (),
    )


def _positive_fill(record: dict[str, object]) -> bool:
    values = _named_values(record, frozenset({"filled_quantity"}))
    for value in values:
        try:
            if int(value) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def read_transition_fill_evidence(
    fills_path: str | Path,
    scope: TransitionEvidenceScope,
) -> FillTransitionEvidence:
    scope_errors = _scope_errors(scope)
    if scope_errors:
        return FillTransitionEvidence(UNKNOWN, UNKNOWN, scope_errors)
    read_result = read_execution_fill_records(fills_path)
    if read_result.get("ok") is not True:
        return FillTransitionEvidence(
            UNKNOWN,
            UNKNOWN,
            (f"Fill read failed: {read_result.get('error')}",),
        )
    records = [
        dict(item)
        for item in read_result.get("records", ())
        if isinstance(item, dict)
    ]

    buy_fill = False
    sell_fill = False
    indeterminate = False
    for record in records:
        match = _record_scope_match(record, scope)
        if match == _INDETERMINATE:
            if _normalized_code(scope.stock_code) in {
                _normalized_code(value)
                for value in _named_values(record, _CODE_KEYS)
            }:
                indeterminate = True
            continue
        if match != _MATCH or not _positive_fill(record):
            continue
        sides = {
            _text(value).upper()
            for value in _named_values(record, frozenset({"side"}))
            if _text(value)
        }
        if len(sides) != 1 or not sides & {"BUY", "SELL", "매수", "매도"}:
            indeterminate = True
            continue
        buy_fill = buy_fill or bool(sides & {"BUY", "매수"})
        sell_fill = sell_fill or bool(sides & {"SELL", "매도"})

    return FillTransitionEvidence(
        PRESENT if buy_fill else UNKNOWN if indeterminate else ABSENT,
        PRESENT if sell_fill else UNKNOWN if indeterminate else ABSENT,
        ("matching stock Fill evidence lacks required scope identity",)
        if indeterminate
        else (),
    )


def _routine_close_evidence(
    runtime_state: dict[str, object] | None,
    scope: TransitionEvidenceScope,
    runtime_routine_instance_id: object,
) -> tuple[str, tuple[str, ...]]:
    if not isinstance(runtime_state, dict):
        return UNKNOWN, ("runtime state is unavailable",)
    state_routine = _text(runtime_routine_instance_id)
    state_command = _text(runtime_state.get("operation_command_id"))
    if state_routine != _text(scope.routine_instance_id):
        return UNKNOWN, ("runtime routine instance identity is unavailable or mismatched",)
    if _text(scope.operation_command_id) and state_command != _text(
        scope.operation_command_id
    ):
        return UNKNOWN, ("runtime operation command identity is unavailable or mismatched",)
    started_flag = bool(runtime_state.get("close_routine_final_sell_ordered"))
    started_text = _text(runtime_state.get("close_routine_final_sell_ordered_at"))
    if not started_flag and not started_text:
        return ABSENT, ()
    started_at = _parse_datetime(started_text)
    scope_started_at = _parse_datetime(scope.started_at)
    if started_at is None or scope_started_at is None:
        return UNKNOWN, ("routine close evidence lacks a scoped start timestamp",)
    if (
        started_at.date().isoformat() != _text(scope.trade_date)
        or started_at < scope_started_at
    ):
        return ABSENT, ()
    return PRESENT, ()


def build_transition_evidence(
    *,
    queue_path: str | Path,
    fills_path: str | Path,
    runtime_state: dict[str, object] | None,
    runtime_routine_instance_id: object,
    scope: TransitionEvidenceScope,
) -> TransitionEvidenceBuildResult:
    """Build a fail-closed, read-only evidence snapshot."""

    queue = read_transition_queue_evidence(queue_path, scope)
    fills = read_transition_fill_evidence(fills_path, scope)
    routine_close, routine_errors = _routine_close_evidence(
        runtime_state,
        scope,
        runtime_routine_instance_id,
    )
    errors = tuple(dict.fromkeys((*routine_errors, *queue.errors, *fills.errors)))
    return TransitionEvidenceBuildResult(
        routine_close_started=routine_close,
        order_created=queue.order_created,
        buy_fill_detected=fills.buy_fill_detected,
        sell_fill_detected=fills.sell_fill_detected,
        cancellation_started=queue.cancellation_started,
        errors=errors,
    )
