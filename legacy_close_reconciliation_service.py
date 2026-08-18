# -*- coding: utf-8 -*-
"""Isolated legacy EARLY_CLOSE no-target reconciliation boundary.

The service is intentionally not wired to Production.  It closes only the
legacy *active proxy* left by ``operation_command_mode=EARLY_CLOSE`` when one
complete Recovery snapshot proves that no position, pending order, Queue work,
or real close/liquidation work exists.  The historical command mode and every
Review/Emergency/position field are preserved.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any, Callable, Mapping

from gui_auto_trade_integrity import is_review_required_state
from gui_order_utils import pending_order_side_quantities
from production_recovery_contract import (
    BrokerAccountSnapshot,
    RecoverySessionIdentity,
    normalize_stock_code,
)
from runtime_atomic_writer import STATUS_OK as ATOMIC_WRITE_OK
from runtime_atomic_writer import write_json_atomic
from stock_position_reconciliation_service import (
    _identity_gate_reason,
    _queue_block_reason,
)


STATUS_COMPLETED = "COMPLETED"
STATUS_NO_CHANGE = "NO_CHANGE"
STATUS_BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"
STATUS_FAILED = "FAILED"

_STATE_FILE_NAME = "state.json"
_STOCK_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
_ALLOWED_FIELDS = frozenset(
    {
        "operation_notice",
        "operation_notice_reason",
        "operation_notice_at",
        "updated_at",
    }
)
_TERMINAL_NOTICES = frozenset(
    {
        "EARLY_CLOSE_NO_TARGET",
        "EARLY_CLOSED",
        "EARLY_CLOSE_COMPLETED",
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        "EARLY_CLOSED",
        "EARLY_CLOSE_COMPLETED",
        "LIQUIDATED",
        "LIQUIDATION_COMPLETED",
    }
)
_ACTIVE_CLOSE_STATUSES = frozenset(
    {
        "AUTO_CLOSING",
        "EARLY_CLOSING",
        "LIQUIDATING",
        "LIQUIDATION_IN_PROGRESS",
        "CLOSE_IN_PROGRESS",
        "SELLING",
    }
)
_REQUEST_TERMINAL_STATUSES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "ORDER_BLOCKED",
        "CANCELLED",
        "CANCELED",
    }
)
_LOCK = RLock()

AtomicWriter = Callable[[str | Path, dict[str, Any]], Mapping[str, Any]]
StateReader = Callable[[Path], dict[str, Any]]
Clock = Callable[[], datetime]


def state_file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _read_state_strict(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("state root must be an object")
    return value


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


def _blocked(reason: str, *, stock_code: str, before_sha: str = "") -> dict[str, object]:
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


def _zero_integer(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value).replace(",", "").strip())
        return number.is_finite() and number == 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _current_day_completion(state: Mapping[str, object], trading_day: str) -> bool:
    day = _text(trading_day)
    for key in (
        "liquidation_completed_at",
        "liquidation_finished_at",
        "early_close_completed_at",
        "close_completed_at",
    ):
        if _text(state.get(key)).startswith(day):
            return True
    return bool(state.get("daily_liquidation_completed", False)) and _text(
        state.get("daily_liquidation_completed_date")
    ) == day


def _already_terminal(state: Mapping[str, object], trading_day: str) -> bool:
    if _text(state.get("operation_notice")).upper() in _TERMINAL_NOTICES:
        return True
    if _text(state.get("status")).upper() in _TERMINAL_STATUSES:
        return True
    return _current_day_completion(state, trading_day)


def _active_close_evidence(state: Mapping[str, object]) -> str:
    """Return real subordinate close evidence, excluding mode and legacy intent."""

    if _text(state.get("status")).upper() in _ACTIVE_CLOSE_STATUSES:
        return "ACTIVE_CLOSE_STATUS"
    if bool(state.get("liquidation_policy_forced", False)):
        return "LIQUIDATION_POLICY_FORCED"
    if bool(state.get("close_routine_final_sell_ordered", False)) or _text(
        state.get("close_routine_final_sell_ordered_at")
    ):
        return "FINAL_SELL_EVIDENCE"
    for key in ("individual_liquidation_request", "manual_ats_liquidation_request"):
        request = state.get(key)
        if not isinstance(request, Mapping):
            continue
        request_status = _text(request.get("status") or "REQUESTED").upper()
        if request_status not in _REQUEST_TERMINAL_STATUSES:
            return "ACTIVE_LIQUIDATION_REQUEST"
    for key in (
        "active_close_order_id",
        "active_liquidation_order_id",
        "final_sell_order_id",
        "close_broker_order_no",
        "liquidation_broker_order_no",
    ):
        if _text(state.get(key)):
            return "ACTIVE_CLOSE_ORDER_EVIDENCE"
    return ""


def reconcile_legacy_early_close_no_target(
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
    now_provider: Clock = datetime.now,
) -> dict[str, object]:
    """Reconcile one Review stock's proven legacy EARLY_CLOSE no-target residue."""

    code = normalize_stock_code(stock_code)
    target_dir = Path(stock_dir)
    if not _STOCK_CODE_PATTERN.fullmatch(code):
        return _blocked("INVALID_STOCK_CODE", stock_code=code)
    if normalize_stock_code(target_dir.name.split("_", 1)[0]) != code:
        return _blocked("STOCK_DIRECTORY_IDENTITY_MISMATCH", stock_code=code)
    if not isinstance(broker_snapshot, BrokerAccountSnapshot):
        return _blocked("BROKER_SNAPSHOT_INVALID", stock_code=code)
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
        expected_account_no=_text(expected_account_no),
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
    if any(item.account_no != _text(expected_account_no) for item in holding_rows):
        return _blocked("BROKER_HOLDING_ACCOUNT_MISMATCH", stock_code=code)
    if any(int(item.holding_quantity) != 0 for item in holding_rows):
        return _blocked("BROKER_HOLDING_EXISTS", stock_code=code)
    if any(
        normalize_stock_code(item.stock_code) == code
        for item in broker_snapshot.open_orders
    ):
        return _blocked("BROKER_OPEN_ORDER_EXISTS", stock_code=code)

    queue_reason = _queue_block_reason(code, Path(order_queue_path))
    if queue_reason:
        return _blocked(queue_reason, stock_code=code)

    state_path = target_dir / _STATE_FILE_NAME
    with _LOCK:
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
        if _text(before_state.get("operation_command_mode")).upper() != "EARLY_CLOSE":
            return _blocked("NOT_LEGACY_EARLY_CLOSE", stock_code=code, before_sha=before_sha)
        if not _zero_integer(before_state.get("holding_qty")):
            return _blocked("LOCAL_HOLDING_EXISTS_OR_INVALID", stock_code=code, before_sha=before_sha)
        if not _zero_integer(before_state.get("avg_price")):
            return _blocked("LOCAL_POSITION_NOT_RECONCILED", stock_code=code, before_sha=before_sha)

        buy_pending, sell_pending = pending_order_side_quantities(target_dir, before_state)
        if buy_pending == "?" or sell_pending == "?":
            return _blocked("PENDING_ORDER_UNKNOWN", stock_code=code, before_sha=before_sha)
        if int(buy_pending) > 0:
            return _blocked("PENDING_BUY_EXISTS", stock_code=code, before_sha=before_sha)
        if int(sell_pending) > 0:
            return _blocked("PENDING_SELL_EXISTS", stock_code=code, before_sha=before_sha)

        if _already_terminal(before_state, recovery_identity.trading_day):
            return _result(
                STATUS_NO_CHANGE,
                "EARLY_CLOSE_ALREADY_TERMINAL",
                stock_code=code,
                before_sha=before_sha,
                after_sha=before_sha,
            )
        active_reason = _active_close_evidence(before_state)
        if active_reason:
            return _blocked(active_reason, stock_code=code, before_sha=before_sha)

        try:
            now_text = now_provider().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return _failed("RECONCILIATION_TIME_INVALID", stock_code=code, before_sha=before_sha)
        after_state = deepcopy(before_state)
        after_state["operation_notice"] = "EARLY_CLOSE_NO_TARGET"
        after_state["operation_notice_reason"] = "조기마감 대상 없음"
        after_state["operation_notice_at"] = now_text
        after_state["updated_at"] = now_text
        changed_fields = tuple(
            key
            for key in (
                "operation_notice",
                "operation_notice_reason",
                "operation_notice_at",
                "updated_at",
            )
            if before_state.get(key) != after_state.get(key)
        )

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
            return _failed("READ_BACK_INVALID", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        if read_back.get("operation_notice") != "EARLY_CLOSE_NO_TARGET":
            return _failed("READ_BACK_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        if read_back.get("operation_notice_reason") != "조기마감 대상 없음":
            return _failed("READ_BACK_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        if not _text(read_back.get("operation_notice_at")):
            return _failed("READ_BACK_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        if _text(read_back.get("operation_command_mode")).upper() != "EARLY_CLOSE":
            return _failed("READ_BACK_MODE_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        preserved_before = {key: value for key, value in before_state.items() if key not in _ALLOWED_FIELDS}
        preserved_after = {key: value for key, value in read_back.items() if key not in _ALLOWED_FIELDS}
        if preserved_after != preserved_before:
            return _failed("PRESERVED_STATE_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        if normalize_stock_code(target_dir.name.split("_", 1)[0]) != code:
            return _failed("READ_BACK_STOCK_IDENTITY_MISMATCH", stock_code=code, before_sha=before_sha, after_sha=after_sha)
        return _result(
            STATUS_COMPLETED,
            "LEGACY_EARLY_CLOSE_NO_TARGET_RECONCILED",
            stock_code=code,
            before_sha=before_sha,
            after_sha=after_sha,
            changed_fields=changed_fields,
        )
