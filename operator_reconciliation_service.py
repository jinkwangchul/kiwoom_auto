# -*- coding: utf-8 -*-
"""Operator-facing reconciliation assembly and retry helpers.

This module does not own runtime state. It reads existing runtime evidence and
routes operator-confirmed retry actions through the canonical Fill, Position,
and Queue reconciliation boundaries.
"""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from chejan_event_recorder import (
    existing_chejan_record_result,
    inspect_incomplete_order_reconciliation,
    mark_chejan_reconciliation_state,
)
from execution_fill_recorder import find_existing_execution_fill_record, record_execution_fill
from position_update_service import update_position_from_fill


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
DEFAULT_FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
DEFAULT_POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
DEFAULT_BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
DEFAULT_ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
DEFAULT_ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"
DEFAULT_ROUTINE_SIGNALS_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"

STARTUP_RESUME_READY = "RESUME_READY"
STARTUP_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STARTUP_BLOCKED_RECOVERY = "BLOCKED_RECOVERY"
STARTUP_INVALID_RUNTIME = "INVALID_RUNTIME"

_STARTUP_TERMINAL_QUEUE_STATUSES = {
    "FILLED",
    "CANCELLED",
    "PARTIAL_CANCELLED",
    "BROKER_REJECTED",
    "SEND_CALL_REJECTED",
    "BLOCKED",
    "BLOCKED_POLICY",
}
_STARTUP_BLOCKED_QUEUE_STATUSES = {
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_UNCERTAIN",
    "STALE_DISPATCH_CLAIM",
}
_STARTUP_REVIEW_QUEUE_STATUSES = {
    "PENDING",
    "APPROVED",
    "EXECUTABLE",
    "REAL_READY",
    "ORDER_QUEUED",
    "SEND_CALL_ACCEPTED",
    "BROKER_ACCEPTED",
    "PARTIALLY_FILLED",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _read_json(path: str | Path) -> tuple[dict[str, Any] | None, str]:
    target = Path(path)
    try:
        if not target.exists():
            return None, f"{target.name} missing"
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, f"{target.name} root must be object"
        return data, ""
    except Exception as exc:
        return None, str(exc)


def _strict_runtime_list(path: str | Path, list_field: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "status": "MISSING",
            "path": str(target),
            "data": None,
            "items": [],
            "reason": f"{target.name} missing",
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "INVALID",
            "path": str(target),
            "data": None,
            "items": [],
            "reason": f"{target.name} read failed: {exc}",
        }
    if not isinstance(data, dict):
        return {
            "status": "INVALID",
            "path": str(target),
            "data": None,
            "items": [],
            "reason": f"{target.name} root must be an object",
        }
    items = data.get(list_field)
    if not isinstance(items, list):
        return {
            "status": "INVALID",
            "path": str(target),
            "data": data,
            "items": [],
            "reason": f"{target.name}.{list_field} must be a list",
        }
    if any(not isinstance(item, dict) for item in items):
        return {
            "status": "INVALID",
            "path": str(target),
            "data": data,
            "items": [],
            "reason": f"{target.name}.{list_field} entries must be objects",
        }
    return {
        "status": "READY",
        "path": str(target),
        "data": data,
        "items": [dict(item) for item in items],
        "reason": "",
    }


def _snapshot_hash(paths: list[Path]) -> tuple[str, dict[str, str]]:
    evidence: dict[str, str] = {}
    for path in sorted({Path(item) for item in paths}, key=lambda item: str(item)):
        key = str(path)
        if not path.exists():
            evidence[key] = "MISSING"
            continue
        try:
            evidence[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            evidence[key] = f"ERROR:{exc}"
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), evidence


def _startup_stock_state_summary(stock_state_paths: list[str | Path]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    review_items: list[str] = []
    blocked_items: list[str] = []
    invalid_items: list[str] = []
    for raw_path in stock_state_paths:
        path = Path(raw_path)
        if not path.exists():
            invalid_items.append(f"{path}: missing")
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid_items.append(f"{path}: {exc}")
            continue
        if not isinstance(state, dict):
            invalid_items.append(f"{path}: root must be an object")
            continue
        status = _clean_text(state.get("status")).upper()
        if not status:
            invalid_items.append(f"{path}: required field status is missing")
            continue
        statuses[status] = statuses.get(status, 0) + 1
        if status in {"EMERGENCY_STOP", "EMERGENCY_STOPPED", "EMERGENCY"}:
            blocked_items.append(f"{path.parent.name}: {status}")
        elif status in {"REVIEW_REQUIRED", "REVIEW"} or state.get("review_required") is True:
            review_items.append(f"{path.parent.name}: REVIEW_REQUIRED")
        elif status not in {"STOPPED", "STOP", "IDLE"}:
            review_items.append(f"{path.parent.name}: previous session status {status}")
    return {
        "status_counts": statuses,
        "review_items": review_items,
        "blocked_items": blocked_items,
        "invalid_items": invalid_items,
    }


def _identity_key(source: Any, identity: Any) -> str:
    src = _clean_text(source)
    value = _clean_text(identity)
    return f"{src}:{value}" if src and value else ""


def _order_apply_key(fill: dict[str, Any]) -> str:
    for field in ("execution_id", "order_queued_id", "order_id", "request_hash", "lock_id", "broker_order_no"):
        value = _clean_text(fill.get(field))
        if value:
            return f"{field}:{value}"
    return "unknown_order"


def _find_position(positions_data: dict[str, Any] | None, fill: dict[str, Any]) -> dict[str, Any] | None:
    positions = _as_dict(positions_data).get("positions")
    if not isinstance(positions, list):
        return None
    account_no = _clean_text(fill.get("account_no"))
    code = _clean_text(fill.get("code"))
    broker = _clean_text(fill.get("broker"))
    for item in positions:
        position = _as_dict(item)
        if (
            _clean_text(position.get("account_no")) == account_no
            and _clean_text(position.get("code")) == code
            and _clean_text(position.get("broker")) == broker
        ):
            return position
    return None


def _position_applied(position: dict[str, Any] | None, fill: dict[str, Any] | None) -> bool:
    if not isinstance(position, dict) or not isinstance(fill, dict):
        return False
    fill_id = _clean_text(fill.get("fill_id"))
    identity = _identity_key(fill.get("execution_identity_source"), fill.get("execution_identity"))
    applied_ids = position.get("applied_fill_ids")
    if fill_id and isinstance(applied_ids, list) and fill_id in applied_ids:
        return True
    applied_identities = position.get("applied_fill_identities")
    if identity and isinstance(applied_identities, list) and identity in applied_identities:
        return True
    cumulative_by_order = _as_dict(position.get("last_applied_cumulative_by_order"))
    last_cumulative = cumulative_by_order.get(_order_apply_key(fill))
    return isinstance(last_cumulative, int) and isinstance(fill.get("filled_quantity"), int) and last_cumulative >= fill["filled_quantity"]


def _identity_conflicts(
    left: dict[str, Any],
    right: dict[str, Any],
    fields: tuple[str, ...],
) -> list[str]:
    conflicts: list[str] = []
    for field in fields:
        left_value = _clean_text(left.get(field))
        right_value = _clean_text(right.get(field))
        if left_value and right_value and left_value != right_value:
            conflicts.append(field)
    return conflicts


def _pending_chejan_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    items = record.get("chejan_reconciliation_items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and item.get("required") is True]


def _stored_event_by_identity(record: dict[str, Any], event_identity: str) -> dict[str, Any] | None:
    events = record.get("chejan_events")
    if not isinstance(events, list):
        return None
    target = _clean_text(event_identity).upper()
    for item in events:
        event = _as_dict(item)
        if _clean_text(event.get("event_identity")).upper() == target:
            return event
        normalized = _as_dict(event.get("normalized_event"))
        if _clean_text(normalized.get("event_identity")).upper() == target:
            return event
    return None


def _chejan_reconciliation_row(
    *,
    record: dict[str, Any],
    item: dict[str, Any],
    stored_event: dict[str, Any] | None,
    fills_path: Path,
    positions_data: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = _as_dict(_as_dict(stored_event).get("normalized_event"))
    chejan_result = existing_chejan_record_result(record, normalized) if normalized else None
    fill_record = None
    if chejan_result is not None:
        fill_record = find_existing_execution_fill_record(fills_path, chejan_result, normalized)
    position = _find_position(positions_data, fill_record or normalized)
    fill_applied = isinstance(fill_record, dict)
    position_applied = _position_applied(position, fill_record)
    missing_stage = _clean_text(item.get("failed_stage")) or ("POSITION_UPDATE" if fill_applied else "FILL_RECORD")
    retryable = stored_event is not None and normalized and chejan_result is not None
    status = "RETRYABLE" if retryable else "MANUAL_REVIEW_REQUIRED"
    if fill_applied and position_applied:
        status = "RETRYABLE"
    reason = "; ".join(str(reason) for reason in item.get("blocked_reasons", []) if reason) if isinstance(item.get("blocked_reasons"), list) else ""
    return {
        "item_id": f"CHEJAN::{_clean_text(record.get('id'))}::{_clean_text(item.get('event_identity')).upper()}",
        "source_type": "CHEJAN_RECONCILIATION",
        "status": status,
        "occurrence_type": f"CHEJAN_{missing_stage or 'FOLLOW_UP'}",
        "account_no": _clean_text(record.get("account_no") or normalized.get("account_no")),
        "code": _clean_text(record.get("code") or normalized.get("code")),
        "name": _clean_text(normalized.get("name")),
        "order_id": _clean_text(record.get("order_id")),
        "order_queued_id": _clean_text(record.get("id")),
        "broker_order_no": _clean_text(record.get("broker_order_no") or normalized.get("broker_order_no")),
        "original_order_no": _clean_text(record.get("original_order_no") or normalized.get("original_order_no")),
        "event_identity": _clean_text(item.get("event_identity")).upper(),
        "queue_status": _clean_text(record.get("status")),
        "fill_applied": fill_applied,
        "position_applied": position_applied,
        "broker_reconciliation_status": "",
        "reason": reason or missing_stage or "Chejan follow-up is incomplete",
        "recommended_action": "safe_retry" if retryable else "manual_review",
        "last_checked_at": _now_text(),
        "retryable": retryable,
    }


def _manual_order_row(record: dict[str, Any]) -> dict[str, Any]:
    reason = _clean_text(record.get("manual_reconciliation_reason") or record.get("manual_reconciliation_stage") or record.get("manual_reconciliation_source"))
    return {
        "item_id": f"ORDER::{_clean_text(record.get('id'))}",
        "source_type": "ORDER_RECONCILIATION",
        "status": "MANUAL_REVIEW_REQUIRED",
        "occurrence_type": "ORDER_MANUAL_RECONCILIATION",
        "account_no": _clean_text(record.get("account_no")),
        "code": _clean_text(record.get("code")),
        "name": _clean_text(record.get("name")),
        "order_id": _clean_text(record.get("order_id")),
        "order_queued_id": _clean_text(record.get("id")),
        "broker_order_no": _clean_text(record.get("broker_order_no")),
        "original_order_no": _clean_text(record.get("original_order_no")),
        "event_identity": "",
        "queue_status": _clean_text(record.get("status")),
        "fill_applied": False,
        "position_applied": False,
        "broker_reconciliation_status": "",
        "reason": reason or "manual reconciliation is required",
        "recommended_action": "manual_review",
        "last_checked_at": _now_text(),
        "retryable": False,
    }


def _broker_holding_rows(holdings_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    holdings = _as_dict(holdings_data).get("holdings")
    if not isinstance(holdings, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in holdings:
        record = _as_dict(item)
        if record.get("manual_reconciliation_required") is not True:
            continue
        mismatch = record.get("mismatch_fields")
        reason = ", ".join(str(field) for field in mismatch if field) if isinstance(mismatch, list) else ""
        rows.append(
            {
                "item_id": f"BROKER_HOLDING::{_clean_text(record.get('account_no'))}::{_clean_text(record.get('code'))}",
                "source_type": "BROKER_HOLDING_RECONCILIATION",
                "status": "MANUAL_REVIEW_REQUIRED",
                "occurrence_type": "BROKER_POSITION_MISMATCH",
                "account_no": _clean_text(record.get("account_no")),
                "code": _clean_text(record.get("code")),
                "name": _clean_text(record.get("name")),
                "order_id": "",
                "order_queued_id": "",
                "broker_order_no": "",
                "original_order_no": "",
                "event_identity": "",
                "queue_status": "",
                "fill_applied": False,
                "position_applied": False,
                "broker_reconciliation_status": _clean_text(record.get("reconciliation_status")),
                "reason": reason or _clean_text(record.get("position_read_failure_reason")) or "broker holding differs from internal position",
                "recommended_action": "manual_review",
                "last_checked_at": _clean_text(record.get("reconciliation_detected_at") or record.get("updated_at")),
                "retryable": False,
            }
        )
    return rows


def collect_operator_reconciliation_items(
    *,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    fills_path: str | Path = DEFAULT_FILLS_PATH,
    positions_path: str | Path = DEFAULT_POSITIONS_PATH,
    broker_holdings_path: str | Path = DEFAULT_BROKER_HOLDINGS_PATH,
) -> dict[str, Any]:
    queue_data, queue_error = _read_json(queue_path)
    fills_target = Path(fills_path)
    positions_data, positions_error = _read_json(positions_path)
    holdings_data, holdings_error = _read_json(broker_holdings_path)
    rows: list[dict[str, Any]] = []

    orders = _as_dict(queue_data).get("orders")
    if isinstance(orders, list):
        for order in orders:
            record = _as_dict(order)
            pending_items = _pending_chejan_items(record)
            for item in pending_items:
                stored_event = _stored_event_by_identity(record, _clean_text(item.get("event_identity")))
                rows.append(
                    _chejan_reconciliation_row(
                        record=record,
                        item=item,
                        stored_event=stored_event,
                        fills_path=fills_target,
                        positions_data=positions_data,
                    )
                )
            if record.get("manual_reconciliation_required") is True and not pending_items:
                rows.append(_manual_order_row(record))

    rows.extend(_broker_holding_rows(holdings_data))
    summary = {
        "total": len(rows),
        "retryable": sum(1 for row in rows if row.get("retryable") is True),
        "manual_review_required": sum(1 for row in rows if row.get("status") == "MANUAL_REVIEW_REQUIRED"),
        "chejan_reconciliation": sum(1 for row in rows if row.get("source_type") == "CHEJAN_RECONCILIATION"),
        "broker_holding_reconciliation": sum(1 for row in rows if row.get("source_type") == "BROKER_HOLDING_RECONCILIATION"),
    }
    errors = [error for error in (queue_error, positions_error, holdings_error) if error]
    return {
        "status": "OK" if not errors else "PARTIAL",
        "items": rows,
        "summary": summary,
        "read_errors": errors,
        "queue_path": str(queue_path),
        "fills_path": str(fills_path),
        "positions_path": str(positions_path),
        "broker_holdings_path": str(broker_holdings_path),
    }


def assess_startup_recovery(
    *,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    fills_path: str | Path = DEFAULT_FILLS_PATH,
    positions_path: str | Path = DEFAULT_POSITIONS_PATH,
    broker_holdings_path: str | Path = DEFAULT_BROKER_HOLDINGS_PATH,
    order_executions_path: str | Path = DEFAULT_ORDER_EXECUTIONS_PATH,
    order_locks_path: str | Path = DEFAULT_ORDER_LOCKS_PATH,
    routine_signals_path: str | Path = DEFAULT_ROUTINE_SIGNALS_PATH,
    stock_state_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Assess whether the current process may resume automatic trading.

    The assessment is read-only. It preserves every runtime file and reports
    the current evidence snapshot that an operator approval must bind to.
    """
    paths_by_name = {
        "order_queue": (Path(queue_path), "orders"),
        "fills": (Path(fills_path), "fills"),
        "positions": (Path(positions_path), "positions"),
        "broker_holdings": (Path(broker_holdings_path), "holdings"),
        "order_executions": (Path(order_executions_path), "executions"),
        "order_locks": (Path(order_locks_path), "locks"),
        "routine_signals": (Path(routine_signals_path), "signals"),
    }
    reads = {
        name: _strict_runtime_list(path, field)
        for name, (path, field) in paths_by_name.items()
    }
    stock_paths = [Path(path) for path in (stock_state_paths or [])]
    snapshot_paths = [path for path, _field in paths_by_name.values()] + stock_paths
    snapshot_hash, snapshot_evidence = _snapshot_hash(snapshot_paths)

    invalid_reasons = [
        read["reason"]
        for read in reads.values()
        if read.get("status") == "INVALID"
    ]
    missing_files = [
        read["path"]
        for read in reads.values()
        if read.get("status") == "MISSING"
    ]
    invalid_reasons.extend(
        read["reason"]
        for read in reads.values()
        if read.get("status") == "MISSING"
    )

    executions_present = reads["order_executions"]["status"] != "MISSING"
    locks_present = reads["order_locks"]["status"] != "MISSING"
    if executions_present != locks_present:
        invalid_reasons.append(
            "order_executions.json and order_locks.json must both exist or both be absent"
        )

    queue_orders = reads["order_queue"].get("items") or []
    queue_status_counts: dict[str, int] = {}
    blocked_reasons: list[str] = []
    review_reasons: list[str] = []
    reconciliation_details: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()

    for order in queue_orders:
        order_id = _clean_text(order.get("id") or order.get("order_id"))
        if order_id:
            if order_id in seen_order_ids:
                invalid_reasons.append(f"duplicate queue order identity: {order_id}")
            seen_order_ids.add(order_id)
        status = _clean_text(order.get("status") or "UNKNOWN").upper()
        queue_status_counts[status] = queue_status_counts.get(status, 0) + 1
        label = order_id or _clean_text(order.get("code")) or "unknown order"

        if status in _STARTUP_BLOCKED_QUEUE_STATUSES:
            blocked_reasons.append(f"{label}: unresolved {status}")
        elif status in _STARTUP_REVIEW_QUEUE_STATUSES:
            review_reasons.append(f"{label}: unfinished queue status {status}")
        elif status not in _STARTUP_TERMINAL_QUEUE_STATUSES:
            review_reasons.append(f"{label}: unclassified queue status {status}")

        if order.get("manual_reconciliation_required") is True:
            review_reasons.append(f"{label}: manual reconciliation required")

        if status in {"SEND_CALL_IN_PROGRESS", "SEND_UNCERTAIN", "BROKER_ACCEPTED", "PARTIALLY_FILLED"}:
            inspection = inspect_incomplete_order_reconciliation(
                queue_path,
                order,
                fills_path=fills_path,
            )
            reconciliation_details.append(inspection)
            classification = _clean_text(
                inspection.get("reconciliation_candidate_status")
            ).upper()
            reasons = [
                _clean_text(reason)
                for reason in inspection.get("blocked_reasons", [])
                if _clean_text(reason)
            ]
            if classification == "BLOCKED":
                blocked_reasons.extend(f"{label}: {reason}" for reason in reasons)
            elif classification in {"REVIEW_REQUIRED", "RECONCILIATION_CANDIDATE"}:
                review_reasons.extend(f"{label}: {reason}" for reason in reasons)

    fills = reads["fills"].get("items") or []
    positions = reads["positions"].get("items") or []
    holdings = reads["broker_holdings"].get("items") or []
    executions = reads["order_executions"].get("items") or []
    locks = reads["order_locks"].get("items") or []
    signals = reads["routine_signals"].get("items") or []

    if reads["fills"]["status"] == "MISSING" and any(
        _clean_text(order.get("status")).upper() in {"PARTIALLY_FILLED", "FILLED", "PARTIAL_CANCELLED"}
        or _safe_int(order.get("cumulative_filled_quantity")) > 0
        for order in queue_orders
    ):
        invalid_reasons.append("fills.json is missing while Queue contains fill evidence")
    if reads["positions"]["status"] == "MISSING" and fills:
        invalid_reasons.append("positions.json is missing while fills.json contains records")
    if reads["broker_holdings"]["status"] == "MISSING":
        review_reasons.append("broker_holdings.json is not available for broker/internal position comparison")

    queue_by_order_id: dict[str, dict[str, Any]] = {}
    for order in queue_orders:
        order_id = _clean_text(order.get("order_id"))
        if order_id and order_id not in queue_by_order_id:
            queue_by_order_id[order_id] = order

    seen_execution_ids: set[str] = set()
    for execution in executions:
        execution_id = _clean_text(execution.get("execution_id"))
        if execution_id:
            if execution_id in seen_execution_ids:
                invalid_reasons.append(f"duplicate runtime execution identity: {execution_id}")
            seen_execution_ids.add(execution_id)
        order_id = _clean_text(execution.get("order_id"))
        queue_order = queue_by_order_id.get(order_id)
        label = execution_id or order_id or "unknown execution"
        if queue_order is None:
            blocked_reasons.append(f"{label}: runtime execution has no matching Queue order")
            continue
        conflicts = _identity_conflicts(
            execution,
            queue_order,
            ("order_id", "execution_id", "request_hash", "lock_id"),
        )
        if conflicts:
            invalid_reasons.append(
                f"{label}: runtime execution/Queue identity mismatch ({', '.join(conflicts)})"
            )

    for fill in fills:
        fill_id = _clean_text(fill.get("fill_id")) or _identity_key(
            fill.get("execution_identity_source"),
            fill.get("execution_identity"),
        ) or "unknown fill"
        position = _find_position(
            reads["positions"].get("data"),
            fill,
        )
        if not _position_applied(position, fill):
            review_reasons.append(f"{fill_id}: Fill is not applied to internal Position")

    for lock in locks:
        lock_id = _clean_text(lock.get("lock_id") or lock.get("id") or "unknown lock")
        blocked_reasons.append(f"{lock_id}: runtime order lock remains")

    for holding in holdings:
        if holding.get("manual_reconciliation_required") is True:
            review_reasons.append(
                f"{_clean_text(holding.get('account_no'))}/{_clean_text(holding.get('code'))}: "
                f"broker holding reconciliation {_clean_text(holding.get('reconciliation_status')) or 'required'}"
            )

    for signal in signals:
        signal_id = _clean_text(signal.get("id")) or "unknown signal"
        signal_status = _clean_text(signal.get("status")).upper()
        if signal_status in {"PENDING", "PREVIEWED", "READY", "ORDER_QUEUED", "ERROR"}:
            review_reasons.append(
                f"{signal_id}: unfinished routine signal status {signal_status or 'UNKNOWN'}"
            )

    stock_summary = _startup_stock_state_summary(stock_paths)
    invalid_reasons.extend(stock_summary["invalid_items"])
    blocked_reasons.extend(stock_summary["blocked_items"])
    review_reasons.extend(stock_summary["review_items"])

    operator_items = collect_operator_reconciliation_items(
        queue_path=queue_path,
        fills_path=fills_path,
        positions_path=positions_path,
        broker_holdings_path=broker_holdings_path,
    )
    if operator_items["summary"]["total"]:
        review_reasons.append(
            f"operator reconciliation items: {operator_items['summary']['total']}"
        )

    invalid_reasons = list(dict.fromkeys(reason for reason in invalid_reasons if reason))
    blocked_reasons = list(dict.fromkeys(reason for reason in blocked_reasons if reason))
    review_reasons = list(dict.fromkeys(reason for reason in review_reasons if reason))

    if invalid_reasons:
        status = STARTUP_INVALID_RUNTIME
    elif blocked_reasons:
        status = STARTUP_BLOCKED_RECOVERY
    elif review_reasons:
        status = STARTUP_REVIEW_REQUIRED
    else:
        status = STARTUP_RESUME_READY

    return {
        "assessment_type": "STARTUP_RECOVERY_SESSION_RESUME",
        "status": status,
        "operator_approval_allowed": status in {
            STARTUP_RESUME_READY,
            STARTUP_REVIEW_REQUIRED,
        },
        "automatic_trading_allowed": False,
        "requires_operator_confirmation": True,
        "snapshot_hash": snapshot_hash,
        "snapshot_evidence": snapshot_evidence,
        "invalid_reasons": invalid_reasons,
        "blocked_reasons": blocked_reasons,
        "review_reasons": review_reasons,
        "missing_files": missing_files,
        "queue_status_counts": queue_status_counts,
        "runtime_counts": {
            "orders": len(queue_orders),
            "fills": len(fills),
            "positions": len(positions),
            "broker_holdings": len(holdings),
            "executions": len(executions),
            "locks": len(locks),
            "routine_signals": len(signals),
            "stock_states": len(stock_paths),
        },
        "stock_state_summary": stock_summary,
        "operator_reconciliation": operator_items,
        "reconciliation_details": reconciliation_details,
        "read_results": reads,
        "checked_at": _now_text(),
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order_called": False,
        "broker_api_called": False,
    }


def retry_operator_chejan_reconciliation(
    *,
    order_queued_id: str,
    event_identity: str,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    fills_path: str | Path = DEFAULT_FILLS_PATH,
    positions_path: str | Path = DEFAULT_POSITIONS_PATH,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue_data, queue_error = _read_json(queue_path)
    if queue_data is None:
        return {"retried": False, "stage": "queue_read", "blocked_reasons": [queue_error]}
    target_id = _clean_text(order_queued_id)
    target_identity = _clean_text(event_identity).upper()
    orders = queue_data.get("orders")
    if not isinstance(orders, list):
        return {"retried": False, "stage": "queue_structure", "blocked_reasons": ["queue orders must be a list"]}
    record = None
    for order in orders:
        item = _as_dict(order)
        if _clean_text(item.get("id")) == target_id:
            record = item
            break
    if record is None:
        return {"retried": False, "stage": "order_match", "blocked_reasons": ["target order was not found"]}
    pending = [item for item in _pending_chejan_items(record) if _clean_text(item.get("event_identity")).upper() == target_identity]
    if not pending:
        return {"retried": False, "stage": "reconciliation_item", "blocked_reasons": ["matching pending Chejan reconciliation item was not found"]}
    stored_event = _stored_event_by_identity(record, target_identity)
    normalized = _as_dict(_as_dict(stored_event).get("normalized_event"))
    if not normalized:
        return {"retried": False, "stage": "stored_chejan_event", "blocked_reasons": ["stored normalized Chejan event was not found"]}
    chejan_result = existing_chejan_record_result(record, normalized)
    if chejan_result is None:
        return {"retried": False, "stage": "stored_chejan_event", "blocked_reasons": ["stored Chejan event cannot be reconstructed"]}

    retry_context = {
        "manual_fill_record_confirmed": True,
        "manual_position_update_confirmed": True,
        "operator_reconciliation_action": True,
        "chejan_reconciliation_reprocess": True,
        "operator_reconciliation_confirmed_at": _now_text(),
    }
    retry_context.update(_as_dict(context))

    completed_steps = ["QUEUE_LIFECYCLE"]
    fill_result = record_execution_fill(chejan_result, normalized, fills_path, context=retry_context)
    fill_record = _as_dict(fill_result.get("fill_record")) if isinstance(fill_result, dict) else {}
    if not fill_record:
        existing_fill = find_existing_execution_fill_record(fills_path, chejan_result, normalized)
        fill_record = _as_dict(existing_fill)
    if not fill_record:
        reconciliation = mark_chejan_reconciliation_state(
            queue_path,
            chejan_result,
            required=True,
            failed_stage="FILL_RECORD",
            completed_steps=completed_steps,
            reasons=list(fill_result.get("blocked_reasons") or []) if isinstance(fill_result, dict) else ["fill record failed"],
            context=retry_context,
        )
        return {
            "retried": False,
            "stage": "fill_record",
            "fill_result": fill_result,
            "reconciliation_result": reconciliation,
            "manual_reconciliation_required": True,
        }

    completed_steps.append("FILL_RECORD")
    position_input = fill_result if isinstance(fill_result, dict) and fill_result.get("fill_recorded") is True else {
        "fill_recorded": True,
        "fill_stage": "execution_fill_already_recorded",
        "next_stage": "POSITION_UPDATE_REQUIRED",
        "fill_id": fill_record.get("fill_id"),
        "event_type": fill_record.get("event_type"),
        "order_id": fill_record.get("order_id"),
        "order_queued_id": fill_record.get("order_queued_id"),
        "broker_order_no": fill_record.get("broker_order_no"),
        "request_hash": fill_record.get("request_hash"),
        "lock_id": fill_record.get("lock_id"),
        "execution_id": fill_record.get("execution_id"),
        "filled_quantity": fill_record.get("filled_quantity"),
        "filled_price": fill_record.get("filled_price"),
        "blocked_reasons": [],
        "warnings": [],
    }
    position_result = update_position_from_fill(position_input, fill_record, positions_path, context=retry_context)
    position_ok = position_result.get("position_updated") is True or _clean_text(position_result.get("position_stage")) in {
        "duplicate_fill",
        "fill_delta_noop",
        "later_cumulative_fill_already_applied",
    }
    if not position_ok:
        reconciliation = mark_chejan_reconciliation_state(
            queue_path,
            chejan_result,
            required=True,
            failed_stage="POSITION_UPDATE",
            completed_steps=completed_steps,
            reasons=list(position_result.get("blocked_reasons") or []),
            context=retry_context,
        )
        return {
            "retried": False,
            "stage": "position_update",
            "fill_result": fill_result,
            "position_result": position_result,
            "reconciliation_result": reconciliation,
            "manual_reconciliation_required": True,
        }

    completed_steps.append("POSITION_UPDATE")
    reconciliation = mark_chejan_reconciliation_state(
        queue_path,
        chejan_result,
        required=False,
        completed_steps=completed_steps,
        context=retry_context,
    )
    return {
        "retried": reconciliation.get("reconciliation_persisted") is True,
        "stage": "resolved" if reconciliation.get("reconciliation_persisted") is True else "reconciliation_persist",
        "fill_result": deepcopy(fill_result),
        "position_result": deepcopy(position_result),
        "reconciliation_result": reconciliation,
        "manual_reconciliation_required": reconciliation.get("manual_reconciliation_required") is True,
    }
