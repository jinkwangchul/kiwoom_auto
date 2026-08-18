# -*- coding: utf-8 -*-
"""Canonical Broker snapshot -> Review stock position reconciliation boundary.

This module is intentionally not connected to a Production caller.  It applies
only the two canonical position fields in one Review stock's ``state.json``
after complete Recovery, identity, Queue, open-order, and stale-state evidence
have all been verified.  It does not own a new Source of Truth and it never
changes Review, Emergency, close/liquidation, routine, or operation lifecycle
state.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any, Callable, Mapping

from execution_queue_writer import read_execution_queue_records
from gui_auto_trade_integrity import is_review_required_state
from gui_order_utils import order_current_pending_qty
from operation_close_completion_evaluator import (
    ACTIVE_QUEUE_STATUSES,
    CLOSED_QUEUE_STATUSES,
)
from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    BrokerAccountSnapshot,
    BrokerHoldingSnapshotItem,
    RecoverySessionIdentity,
    normalize_stock_code,
    recovery_request_id,
)
from runtime_atomic_writer import STATUS_OK as ATOMIC_WRITE_OK
from runtime_atomic_writer import write_json_atomic


STATUS_APPLIED = "APPLIED"
STATUS_NO_CHANGE = "NO_CHANGE"
STATUS_BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"
STATUS_FAILED = "FAILED"

_STATE_FILE_NAME = "state.json"
_STOCK_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
_POSITION_FIELDS = frozenset({"holding_qty", "avg_price", "holding_amount"})
_RECONCILIATION_LOCK = RLock()

AtomicWriter = Callable[[str | Path, dict[str, Any]], Mapping[str, Any]]
StateReader = Callable[[Path], dict[str, Any]]


def state_file_sha256(path: str | Path) -> str:
    """Return the upper-case SHA-256 of the actual state file bytes."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _result(
    status: str,
    reason: str,
    *,
    stock_code: str,
    before_sha: str = "",
    after_sha: str = "",
    changed_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "stock_code": stock_code,
        "before_state_sha256": before_sha,
        "after_state_sha256": after_sha,
        "changed_fields": changed_fields,
    }


def _blocked(
    reason: str,
    *,
    stock_code: str,
    before_sha: str = "",
) -> dict[str, object]:
    return _result(
        STATUS_BLOCKED_EVIDENCE,
        reason,
        stock_code=stock_code,
        before_sha=before_sha,
    )


def _failed(
    reason: str,
    *,
    stock_code: str,
    before_sha: str = "",
    after_sha: str = "",
) -> dict[str, object]:
    return _result(
        STATUS_FAILED,
        reason,
        stock_code=stock_code,
        before_sha=before_sha,
        after_sha=after_sha,
    )


def _read_state_strict(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state root must be an object")
    return data


def _text(value: object) -> str:
    return str(value or "").strip()


def _queue_named_values(value: object, key: str) -> list[object]:
    values: list[object] = []
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            if str(raw_key).strip().lower() == key:
                values.append(raw_value)
            if isinstance(raw_value, Mapping):
                values.extend(_queue_named_values(raw_value, key))
    return values


def _queue_stock_code(record: Mapping[str, object]) -> str:
    for key in ("stock_code", "code", "종목코드"):
        for value in _queue_named_values(record, key.lower()):
            code = normalize_stock_code(value)
            if code:
                return code
    return ""


def _queue_action(record: Mapping[str, object]) -> str:
    actions = {
        _text(value).upper()
        for value in _queue_named_values(record, "order_action")
        if _text(value)
    }
    if "CANCEL" in actions:
        return "CANCEL"
    return next(iter(actions), "")


def _terminal_blocked_zero_quantity(
    record: Mapping[str, object],
    *,
    pending_qty: int,
    pending_unknown: bool,
) -> bool:
    """Recognize only the existing never-dispatched zero-quantity residue."""

    if _text(record.get("status")).upper() != "BLOCKED":
        return False
    if _text(record.get("approval_status")).upper() != "BLOCKED":
        return False
    if record.get("execution_enabled") is not False:
        return False
    if _text(record.get("candidate_status")).upper() != "NO_HOLDING_QTY":
        return False
    if _text(record.get("order_type")).upper() != "SELL_NO_HOLDING_CANDIDATE":
        return False
    raw_quantity = record.get("quantity")
    if raw_quantity is None or isinstance(raw_quantity, bool):
        return False
    try:
        quantity = int(str(raw_quantity).replace(",", "").strip())
    except (TypeError, ValueError):
        return False
    for field in ("pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"):
        for raw_pending in _queue_named_values(record, field.lower()):
            if raw_pending in (None, ""):
                continue
            if isinstance(raw_pending, bool):
                return False
            try:
                if int(str(raw_pending).replace(",", "").strip()) < 0:
                    return False
            except (TypeError, ValueError):
                return False
    if quantity != 0 or pending_unknown or pending_qty != 0:
        return False
    if record.get("send_order_called") not in (None, False):
        return False
    if record.get("dispatch_claimed") is True:
        return False
    for field in (
        "execution_id",
        "order_id",
        "lock_id",
        "dispatch_claim_id",
        "dispatch_status",
        "send_status",
        "broker_order_no",
        "original_order_no",
    ):
        if any(_text(value) for value in _queue_named_values(record, field)):
            return False
    return True


def _queue_block_reason(stock_code: str, queue_path: Path) -> str:
    snapshot = read_execution_queue_records(queue_path)
    if snapshot.get("ok") is not True:
        return "QUEUE_EVIDENCE_INVALID"
    for raw_record in snapshot.get("records", ()):
        if not isinstance(raw_record, dict):
            return "QUEUE_EVIDENCE_INVALID"
        record_code = _queue_stock_code(raw_record)
        if not record_code:
            return "QUEUE_EVIDENCE_INVALID"
        if record_code != stock_code:
            continue
        try:
            pending_qty, pending_unknown = order_current_pending_qty(raw_record)
        except Exception:
            return "QUEUE_EVIDENCE_INVALID"
        if _terminal_blocked_zero_quantity(
            raw_record,
            pending_qty=pending_qty,
            pending_unknown=pending_unknown,
        ):
            continue
        status = _text(
            raw_record.get("status") or raw_record.get("order_status")
        ).upper()
        has_active_lock = raw_record.get("dispatch_claimed") is True or any(
            _text(value)
            for field in ("lock_id", "dispatch_claim_id")
            for value in _queue_named_values(raw_record, field)
        )
        unresolved = (
            has_active_lock
            or status in ACTIVE_QUEUE_STATUSES
            or pending_unknown
            or pending_qty > 0
            or not status
            or status not in CLOSED_QUEUE_STATUSES
        )
        if not unresolved:
            continue
        if status == "CANCEL_REQUESTED" or _queue_action(raw_record) == "CANCEL":
            return "ACTIVE_QUEUE_CANCEL"
        return "ACTIVE_QUEUE_ORDER"
    return ""


def _canonical_average_price(value: Decimal) -> int | float:
    if not value.is_finite() or value < 0:
        raise ValueError("broker average price is invalid")
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _identity_gate_reason(
    *,
    identity: RecoverySessionIdentity,
    completed_identity: RecoverySessionIdentity,
    snapshot: BrokerAccountSnapshot,
    expected_account_no: str,
    expected_trading_day: str,
    expected_login_session_id: str,
    expected_recovery_session_id: str,
    completed_recovery_status: str,
) -> str:
    if completed_recovery_status != ACCOUNT_COMPLETED:
        return "RECOVERY_NOT_COMPLETED"
    if identity != completed_identity:
        return "COMPLETED_RECOVERY_IDENTITY_MISMATCH"
    if identity.account_no != expected_account_no:
        return "ACCOUNT_IDENTITY_MISMATCH"
    if identity.trading_day != expected_trading_day:
        return "TRADING_DAY_IDENTITY_MISMATCH"
    if identity.login_session_id != expected_login_session_id:
        return "LOGIN_SESSION_IDENTITY_MISMATCH"
    if identity.recovery_session_id != expected_recovery_session_id:
        return "RECOVERY_SESSION_IDENTITY_MISMATCH"
    if snapshot.account_no != identity.account_no:
        return "SNAPSHOT_ACCOUNT_MISMATCH"
    if snapshot.trading_day != identity.trading_day:
        return "SNAPSHOT_TRADING_DAY_MISMATCH"
    if snapshot.recovery_session_id != identity.recovery_session_id:
        return "SNAPSHOT_RECOVERY_SESSION_MISMATCH"
    if snapshot.requested_at != identity.requested_at or not snapshot.completed_at:
        return "SNAPSHOT_COMPLETION_IDENTITY_MISMATCH"
    if snapshot.request_id != recovery_request_id(identity, "ACCOUNT"):
        return "SNAPSHOT_REQUEST_ID_MISMATCH"
    return ""


def reconcile_review_stock_position(
    *,
    stock_dir: str | Path,
    stock_code: str,
    recovery_identity: RecoverySessionIdentity,
    completed_recovery_identity: RecoverySessionIdentity,
    broker_snapshot: BrokerAccountSnapshot,
    expected_account_no: str,
    expected_trading_day: str,
    expected_login_session_id: str,
    expected_recovery_session_id: str,
    completed_recovery_status: str,
    holdings_complete: bool,
    open_orders_complete: bool,
    expected_state_sha256: str,
    order_queue_path: str | Path,
    atomic_writer: AtomicWriter = write_json_atomic,
    state_reader: StateReader = _read_state_strict,
) -> dict[str, object]:
    """Reconcile only canonical position fields for one current Review stock."""

    code = normalize_stock_code(stock_code)
    account_no = _text(expected_account_no)
    if not _STOCK_CODE_PATTERN.fullmatch(code):
        return _blocked("INVALID_STOCK_CODE", stock_code=code)
    target_dir = Path(stock_dir)
    folder_code = normalize_stock_code(target_dir.name.split("_", 1)[0])
    if folder_code != code:
        return _blocked("STOCK_DIRECTORY_IDENTITY_MISMATCH", stock_code=code)
    if not broker_snapshot.is_complete or broker_snapshot.errors:
        return _blocked("BROKER_SNAPSHOT_INCOMPLETE", stock_code=code)
    if holdings_complete is not True:
        return _blocked("HOLDINGS_SNAPSHOT_INCOMPLETE", stock_code=code)
    if open_orders_complete is not True:
        return _blocked("OPEN_ORDERS_SNAPSHOT_INCOMPLETE", stock_code=code)

    identity_reason = _identity_gate_reason(
        identity=recovery_identity,
        completed_identity=completed_recovery_identity,
        snapshot=broker_snapshot,
        expected_account_no=account_no,
        expected_trading_day=_text(expected_trading_day),
        expected_login_session_id=_text(expected_login_session_id),
        expected_recovery_session_id=_text(expected_recovery_session_id),
        completed_recovery_status=_text(completed_recovery_status).upper(),
    )
    if identity_reason:
        return _blocked(identity_reason, stock_code=code)

    holding_rows = [
        item
        for item in broker_snapshot.holdings
        if normalize_stock_code(item.stock_code) == code
    ]
    if len(holding_rows) > 1:
        return _blocked("DUPLICATE_BROKER_HOLDING", stock_code=code)
    if any(item.account_no != account_no for item in holding_rows):
        return _blocked("BROKER_HOLDING_ACCOUNT_MISMATCH", stock_code=code)
    if any(
        normalize_stock_code(item.stock_code) == code
        for item in broker_snapshot.open_orders
    ):
        return _blocked("BROKER_OPEN_ORDER_EXISTS", stock_code=code)

    queue_reason = _queue_block_reason(code, Path(order_queue_path))
    if queue_reason:
        return _blocked(queue_reason, stock_code=code)

    state_path = target_dir / _STATE_FILE_NAME
    with _RECONCILIATION_LOCK:
        try:
            before_bytes = state_path.read_bytes()
            before_sha = hashlib.sha256(before_bytes).hexdigest().upper()
        except Exception:
            return _failed("STATE_READ_FAILED", stock_code=code)
        if before_sha != _text(expected_state_sha256).upper():
            return _blocked("STATE_STALE", stock_code=code, before_sha=before_sha)
        try:
            before_state = json.loads(before_bytes.decode("utf-8"))
        except Exception:
            return _failed("STATE_JSON_INVALID", stock_code=code, before_sha=before_sha)
        if not isinstance(before_state, dict):
            return _failed("STATE_ROOT_INVALID", stock_code=code, before_sha=before_sha)
        if not is_review_required_state(before_state):
            return _blocked("STOCK_NOT_REVIEW_REQUIRED", stock_code=code, before_sha=before_sha)

        if holding_rows:
            row: BrokerHoldingSnapshotItem = holding_rows[0]
            try:
                desired_qty = int(row.holding_quantity)
                desired_avg = _canonical_average_price(row.average_price)
            except (TypeError, ValueError):
                return _blocked("BROKER_HOLDING_VALUE_INVALID", stock_code=code, before_sha=before_sha)
            if desired_qty < 0 or (desired_qty == 0 and desired_avg != 0):
                return _blocked("BROKER_HOLDING_VALUE_INVALID", stock_code=code, before_sha=before_sha)
        else:
            desired_qty = 0
            desired_avg = 0

        after_state = deepcopy(before_state)
        after_state["holding_qty"] = desired_qty
        after_state["avg_price"] = desired_avg
        if not holding_rows and "holding_amount" in after_state:
            after_state["holding_amount"] = 0
        changed_fields = tuple(
            field
            for field in ("holding_qty", "avg_price", "holding_amount")
            if field in after_state and before_state.get(field) != after_state.get(field)
        )
        if not changed_fields:
            return _result(
                STATUS_NO_CHANGE,
                "POSITION_ALREADY_MATCHED",
                stock_code=code,
                before_sha=before_sha,
                after_sha=before_sha,
            )

        # Re-read the bytes immediately before the atomic replace so a stale
        # caller cannot overwrite a state changed after its evidence capture.
        try:
            immediate_sha = state_file_sha256(state_path)
        except Exception:
            return _failed("STATE_PREWRITE_READ_FAILED", stock_code=code, before_sha=before_sha)
        if immediate_sha != before_sha:
            return _blocked("STATE_STALE", stock_code=code, before_sha=immediate_sha)

        try:
            write_result = atomic_writer(state_path, after_state)
        except Exception:
            return _failed("ATOMIC_WRITE_FAILED", stock_code=code, before_sha=before_sha)
        if not isinstance(write_result, Mapping) or write_result.get("status") != ATOMIC_WRITE_OK:
            return _failed("ATOMIC_WRITE_FAILED", stock_code=code, before_sha=before_sha)

        try:
            read_back = state_reader(state_path)
            after_sha = state_file_sha256(state_path)
        except Exception:
            return _failed("READ_BACK_FAILED", stock_code=code, before_sha=before_sha)
        if not isinstance(read_back, dict):
            return _failed(
                "READ_BACK_INVALID",
                stock_code=code,
                before_sha=before_sha,
                after_sha=after_sha,
            )
        if any(read_back.get(key) != after_state.get(key) for key in after_state):
            return _failed(
                "READ_BACK_MISMATCH",
                stock_code=code,
                before_sha=before_sha,
                after_sha=after_sha,
            )
        preserved_before = {
            key: value for key, value in before_state.items() if key not in _POSITION_FIELDS
        }
        preserved_after = {
            key: value for key, value in read_back.items() if key not in _POSITION_FIELDS
        }
        if preserved_after != preserved_before:
            return _failed(
                "PRESERVED_STATE_MISMATCH",
                stock_code=code,
                before_sha=before_sha,
                after_sha=after_sha,
            )
        if normalize_stock_code(target_dir.name.split("_", 1)[0]) != code:
            return _failed(
                "READ_BACK_STOCK_IDENTITY_MISMATCH",
                stock_code=code,
                before_sha=before_sha,
                after_sha=after_sha,
            )
        return _result(
            STATUS_APPLIED,
            "POSITION_RECONCILED",
            stock_code=code,
            before_sha=before_sha,
            after_sha=after_sha,
            changed_fields=changed_fields,
        )
