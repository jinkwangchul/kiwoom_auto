# -*- coding: utf-8 -*-
"""Read-only evaluator for global close completion.

This module does not write operation_state, stock state, order queue, fills,
positions, or broker holding files.  It only classifies today's CLOSING
participants from durable files so a later writer can decide whether
NORMAL_ENDED is safe.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from gui_auto_trade_integrity import is_review_required_state
from gui_order_utils import order_current_pending_qty, pending_order_side_quantities


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
STOCKS_DIR = PROJECT_ROOT / "stocks"
OPERATION_STATE_PATH = RUNTIME_DIR / "operation_state.json"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
BROKER_HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"

STATUS_DONE = "DONE"
STATUS_CARRYOVER_DONE = "CARRYOVER_DONE"
STATUS_PENDING_ORDER = "PENDING_ORDER"
STATUS_HOLDING_REMAINS = "HOLDING_REMAINS"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_CLOSE_NOT_STARTED = "CLOSE_NOT_STARTED"
STATUS_EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
STATUS_UNKNOWN = "UNKNOWN"

COMPLETE_STATUSES = {STATUS_DONE, STATUS_CARRYOVER_DONE}

ACTIVE_QUEUE_STATUSES = {
    "ORDER_QUEUED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
    "BROKER_ACCEPTED",
    "PARTIALLY_FILLED",
    "PARTIAL_FILLED",
    "DISPATCH_CLAIMED",
    "SEND_ORDER_CALLED",
    "CANCEL_REQUESTED",
}

CLOSED_QUEUE_STATUSES = {
    "FILLED",
    "CANCELED",
    "CANCELLED",
    "CANCEL_COMPLETE",
    "REJECTED",
    "FAILED",
    "EXPIRED",
    "LOCAL_RESET",
}

CLOSE_STARTED_STATUSES = {
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "AUTO_CLOSED",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "EARLY_CLOSED",
    "LIQUIDATING",
    "LIQUIDATION",
    "LIQUIDATED",
}

CLOSED_STOCK_STATUSES = {
    "AUTO_CLOSED",
    "EARLY_CLOSED",
    "LIQUIDATED",
    "CLOSED",
    "DONE",
}

REVIEW_STATUSES = {"REVIEW", "REVIEW_REQUIRED"}


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def evaluate_operation_close_completion(
    *,
    today: str | None = None,
    operation_state_path: str | Path = OPERATION_STATE_PATH,
    stocks_dir: str | Path = STOCKS_DIR,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
) -> dict[str, Any]:
    """Classify all operation participants using durable files only."""

    effective_today = str(today or today_text()).strip()
    operation_state, operation_error = _read_json_object(Path(operation_state_path))
    base = {
        "evaluated": True,
        "blocked": False,
        "global_complete": False,
        "operation_date": None,
        "operation_status": "",
        "participant_stock_codes": [],
        "stock_results": [],
        "status_counts": {},
        "blocking_stock_codes": [],
        "reasons": [],
        "evidence_errors": [],
    }

    if operation_error:
        return _blocked_result(base, "operation_state_read_failed", operation_error)

    operation_date = str(operation_state.get("operation_date") or "").strip()
    operation_status = _norm(operation_state.get("operation_status"))
    participants = _normalized_stock_codes(
        operation_state.get("operation_participant_stock_codes")
    )
    base.update(
        {
            "operation_date": operation_date,
            "operation_status": operation_status,
            "participant_stock_codes": participants,
        }
    )

    if operation_date != effective_today:
        return _blocked_result(
            base,
            "operation_date_is_not_today",
            f"operation_date={operation_date or 'missing'} today={effective_today}",
        )
    if operation_status != "CLOSING":
        return _blocked_result(
            base,
            "operation_status_is_not_closing",
            f"operation_status={operation_status or 'missing'}",
        )
    if not participants:
        return _blocked_result(
            base,
            "operation_participant_stock_codes_empty",
            "participant list is missing or empty",
        )

    queue_data, queue_error = _read_json_object(Path(order_queue_path), default={"orders": []})
    positions_data, positions_error = _read_json_object(Path(positions_path), default={})
    broker_data, broker_error = _read_json_object(Path(broker_holdings_path), default={})
    evidence_errors: list[str] = []
    if queue_error:
        evidence_errors.append(f"order_queue: {queue_error}")
    if positions_error:
        evidence_errors.append(f"positions: {positions_error}")
    if broker_error:
        evidence_errors.append(f"broker_holdings: {broker_error}")

    stock_results = [
        _evaluate_stock(
            code,
            stocks_dir=Path(stocks_dir),
            queue_data=queue_data if not queue_error else None,
            positions_data=positions_data if not positions_error else None,
            broker_data=broker_data if not broker_error else None,
            evidence_errors=evidence_errors,
        )
        for code in participants
    ]
    status_counts: dict[str, int] = {}
    for item in stock_results:
        status = str(item.get("status") or STATUS_UNKNOWN)
        status_counts[status] = status_counts.get(status, 0) + 1

    blocking_stock_codes = [
        str(item.get("stock_code") or "")
        for item in stock_results
        if str(item.get("status") or "") not in COMPLETE_STATUSES
    ]
    reasons = [
        f"{item.get('stock_code')}: {', '.join(item.get('reasons') or [])}"
        for item in stock_results
        if str(item.get("status") or "") not in COMPLETE_STATUSES
    ]

    return {
        **base,
        "global_complete": not blocking_stock_codes,
        "stock_results": stock_results,
        "status_counts": status_counts,
        "blocking_stock_codes": blocking_stock_codes,
        "reasons": reasons,
        "evidence_errors": evidence_errors,
    }


def resolve_liquidation_holding_quantity(
    stock_code: str,
    *,
    positions_path: str | Path = POSITIONS_PATH,
    broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
) -> dict[str, Any]:
    """Resolve a liquidation quantity from the latest durable position sources."""

    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    code = _normalize_stock_code(stock_code)
    result: dict[str, Any] = {
        "ok": False,
        "stock_code": code,
        "holding_checked_at": checked_at,
        "position_qty": None,
        "broker_holding_qty": None,
        "resolved_liquidation_qty": None,
        "reconciliation_result": "SOURCE_INVALID",
        "blocked_reasons": [],
    }
    reasons = result["blocked_reasons"]
    if not code:
        reasons.append("stock code is invalid")
        return result

    positions_data, positions_error = _read_json_object(Path(positions_path))
    broker_data, broker_error = _read_json_object(Path(broker_holdings_path))
    if positions_error:
        reasons.append(f"positions: {positions_error}")
    if broker_error:
        reasons.append(f"broker_holdings: {broker_error}")
    if reasons:
        return result

    position_qtys, position_reason = _quantities_from_collection(
        positions_data,
        code,
        ("positions", "records", "holdings"),
    )
    broker_qtys, broker_reason = _quantities_from_collection(
        broker_data,
        code,
        ("broker_holdings", "holdings", "positions", "records"),
    )
    if position_reason:
        reasons.append(f"positions: {position_reason}")
    if broker_reason:
        reasons.append(f"broker_holdings: {broker_reason}")
    if reasons:
        return result

    position_qty = position_qtys[0] if position_qtys else None
    broker_qty = broker_qtys[0] if broker_qtys else None
    result["position_qty"] = position_qty
    result["broker_holding_qty"] = broker_qty

    if broker_qty is None:
        result["reconciliation_result"] = "BROKER_SOURCE_MISSING"
        reasons.append("broker holding quantity is unavailable")
        return result
    if position_qty is None:
        if broker_qty > 0:
            result["reconciliation_result"] = "BROKER_ONLY"
            reasons.append("positive broker holding has no matching internal position")
            return result
        result.update(
            {
                "ok": True,
                "resolved_liquidation_qty": 0,
                "reconciliation_result": "CONSISTENT",
            }
        )
        return result
    if position_qty != broker_qty:
        result["reconciliation_result"] = "QUANTITY_MISMATCH"
        reasons.append(
            f"holding quantity conflict: positions={position_qty}, broker_holdings={broker_qty}"
        )
        return result

    result.update(
        {
            "ok": True,
            "resolved_liquidation_qty": position_qty,
            "reconciliation_result": "CONSISTENT",
        }
    )
    return result


def _evaluate_stock(
    stock_code: str,
    *,
    stocks_dir: Path,
    queue_data: dict[str, Any] | None,
    positions_data: dict[str, Any] | None,
    broker_data: dict[str, Any] | None,
    evidence_errors: list[str],
) -> dict[str, Any]:
    stock_dir = _find_stock_dir(stocks_dir, stock_code)
    evidence: dict[str, Any] = {"stock_dir": str(stock_dir) if stock_dir else ""}
    if stock_dir is None:
        return _stock_result(
            stock_code,
            STATUS_UNKNOWN,
            reasons=["stock directory not found"],
            evidence=evidence,
        )

    state, state_error = _read_json_object(stock_dir / "state.json")
    if state_error:
        return _stock_result(
            stock_code,
            STATUS_UNKNOWN,
            reasons=[f"state read failed: {state_error}"],
            evidence=evidence,
        )

    status_text = _norm(state.get("status"))
    close_mode = _close_mode(state)
    holding_qty, holding_reasons, holding_evidence = _durable_holding_qty(
        stock_code,
        state,
        positions_data,
        broker_data,
    )
    active_order_ids, queue_reasons, queue_conflicts = _active_queue_orders(
        stock_code,
        queue_data,
    )
    buy_pending_qty, sell_pending_qty, side_pending_reason = _stock_side_pending(
        stock_dir,
        state,
    )
    evidence.update(
        {
            "status": status_text,
            "close_mode": close_mode,
            "holding": holding_evidence,
            "active_order_ids": active_order_ids,
            "queue_reasons": queue_reasons,
            "evidence_errors": list(evidence_errors),
        }
    )

    conflict_reasons = list(holding_reasons) + list(queue_conflicts)
    if _is_review_required(state) and status_text in CLOSED_STOCK_STATUSES:
        conflict_reasons.append("closed status coexists with REVIEW_REQUIRED")
    if (
        status_text in CLOSED_STOCK_STATUSES
        and holding_qty is not None
        and holding_qty > 0
        and not _explicit_carryover_done(state)
    ):
        conflict_reasons.append("closed status has positive durable holding quantity")
    if conflict_reasons:
        return _stock_result(
            stock_code,
            STATUS_EVIDENCE_CONFLICT,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=conflict_reasons,
        )

    if _is_review_required(state):
        return _stock_result(
            stock_code,
            STATUS_REVIEW_REQUIRED,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=["REVIEW_REQUIRED state exists"],
        )

    if active_order_ids or _pending_quantity_active(buy_pending_qty) or _pending_quantity_active(sell_pending_qty):
        reasons = list(queue_reasons)
        if side_pending_reason:
            reasons.append(side_pending_reason)
        return _stock_result(
            stock_code,
            STATUS_PENDING_ORDER,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=reasons or ["active pending order exists"],
        )

    if not _close_started(state):
        return _stock_result(
            stock_code,
            STATUS_CLOSE_NOT_STARTED,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=["no durable AUTO_CLOSE or EARLY_CLOSE evidence"],
        )

    if _explicit_carryover_done(state):
        return _stock_result(
            stock_code,
            STATUS_CARRYOVER_DONE,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=[],
        )

    if holding_qty is None:
        return _stock_result(
            stock_code,
            STATUS_UNKNOWN,
            close_mode=close_mode,
            state=status_text,
            holding_qty=None,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=["durable holding quantity is unknown"],
        )

    if holding_qty > 0:
        return _stock_result(
            stock_code,
            STATUS_HOLDING_REMAINS,
            close_mode=close_mode,
            state=status_text,
            holding_qty=holding_qty,
            pending_buy_qty=buy_pending_qty,
            pending_sell_qty=sell_pending_qty,
            active_order_ids=active_order_ids,
            evidence=evidence,
            reasons=["positive durable holding quantity remains"],
        )

    return _stock_result(
        stock_code,
        STATUS_DONE,
        close_mode=close_mode,
        state=status_text,
        holding_qty=holding_qty,
        pending_buy_qty=buy_pending_qty,
        pending_sell_qty=sell_pending_qty,
        active_order_ids=active_order_ids,
        evidence=evidence,
        reasons=[],
    )


def _stock_result(
    stock_code: str,
    status: str,
    *,
    close_mode: str = "",
    state: str = "",
    holding_qty: int | None = None,
    pending_buy_qty: object = None,
    pending_sell_qty: object = None,
    active_order_ids: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "stock_code": stock_code,
        "status": status,
        "close_mode": close_mode,
        "stock_state": state,
        "holding_qty": holding_qty,
        "pending_buy_qty": pending_buy_qty,
        "pending_sell_qty": pending_sell_qty,
        "active_order_ids": list(active_order_ids or []),
        "evidence": dict(evidence or {}),
        "reasons": list(reasons or []),
    }


def _blocked_result(base: dict[str, Any], reason_code: str, reason: str) -> dict[str, Any]:
    return {
        **base,
        "blocked": True,
        "global_complete": False,
        "reasons": [reason_code, reason],
        "evidence_errors": [reason],
    }


def _read_json_object(path: Path, default: Any | None = None) -> tuple[dict[str, Any], str]:
    if not path.exists():
        if default is not None:
            return dict(default), ""
        return {}, "file does not exist"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, str(exc)
    if not isinstance(data, dict):
        return {}, "json root is not an object"
    return data, ""


def _find_stock_dir(stocks_dir: Path, stock_code: str) -> Path | None:
    if not stocks_dir.exists():
        return None
    prefix = f"{stock_code}_"
    for path in stocks_dir.iterdir():
        if path.is_dir() and (path.name == stock_code or path.name.startswith(prefix)):
            return path
    return None


def _normalized_stock_codes(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for value in values:
        code = _normalize_stock_code(value)
        if code and code not in result:
            result.append(code)
    return sorted(result)


def _normalize_stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A"):
        text = text[1:]
    return text if len(text) == 6 and text.isdigit() else ""


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_review_required(state: dict[str, Any]) -> bool:
    return is_review_required_state(state)


def _close_started(state: dict[str, Any]) -> bool:
    if _norm(state.get("status")) in CLOSE_STARTED_STATUSES:
        return True
    return any(
        str(state.get(key) or "").strip()
        for key in (
            "auto_close_requested_at",
            "early_close_requested_at",
            "operation_command_id",
            "close_routine_final_sell_ordered_at",
        )
    )


def _close_mode(state: dict[str, Any]) -> str:
    status = _norm(state.get("status"))
    if status.startswith("AUTO_") or str(state.get("auto_close_requested_at") or "").strip():
        return "AUTO_CLOSE"
    if status.startswith("EARLY_") or str(state.get("early_close_requested_at") or "").strip():
        return "EARLY_CLOSE"
    return ""


def _explicit_carryover_done(state: dict[str, Any]) -> bool:
    values = [
        state.get("operation_notice"),
        state.get("liquidation_result"),
        state.get("liquidation_result_status"),
        state.get("auto_close_result"),
        state.get("early_close_result"),
        state.get("close_result"),
    ]
    if any(_norm(value) in {"CURRENT_CARRYOVER", "CARRYOVER_DONE", "LIQUIDATION_CURRENT_PRICE_CARRYOVER"} for value in values):
        return True
    methods = [
        state.get("auto_close_method"),
        state.get("early_close_method"),
        state.get("liquidation_method"),
    ]
    return any(_norm(value) in {"CARRYOVER", "ROLLOVER", "\uc774\uc6d4"} for value in methods)


def _stock_side_pending(stock_dir: Path, state: dict[str, Any]) -> tuple[object, object, str]:
    try:
        buy_qty, sell_qty = pending_order_side_quantities(stock_dir, state)
    except Exception as exc:
        return None, None, f"stock orders pending read failed: {exc}"
    reason = ""
    if _pending_quantity_active(buy_qty) or _pending_quantity_active(sell_qty):
        reason = "stock orders pending quantity exists"
    return buy_qty, sell_qty, reason


def _pending_quantity_active(value: object) -> bool:
    if value == "?":
        return True
    return isinstance(value, int) and value > 0


def _active_queue_orders(
    stock_code: str,
    queue_data: dict[str, Any] | None,
) -> tuple[list[str], list[str], list[str]]:
    if queue_data is None:
        return [], ["order queue unavailable"], []
    orders = queue_data.get("orders")
    if not isinstance(orders, list):
        return [], ["order queue orders are unavailable"], []
    active_ids: list[str] = []
    reasons: list[str] = []
    conflicts: list[str] = []
    for order in orders:
        if not isinstance(order, dict) or _order_code(order) != stock_code:
            continue
        status = _norm(order.get("status") or order.get("order_status"))
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        pending_qty, unknown = order_current_pending_qty(order)
        remaining = _int_or_none(order.get("remaining_quantity") or order.get("pending_qty") or order.get("unfilled_qty"))
        if status == "FILLED" and remaining is not None and remaining > 0:
            conflicts.append(f"FILLED order {order_id or '-'} has remaining quantity")
            continue
        if status in ACTIVE_QUEUE_STATUSES or unknown or pending_qty > 0:
            active_ids.append(order_id or str(order.get("order_id") or "-"))
            reasons.append(f"active order {order_id or '-'} status={status or 'UNKNOWN'} pending={pending_qty if not unknown else 'unknown'}")
        elif status and status not in CLOSED_QUEUE_STATUSES:
            active_ids.append(order_id or str(order.get("order_id") or "-"))
            reasons.append(f"order {order_id or '-'} has unresolved status={status}")
    return active_ids, reasons, conflicts


def _order_code(order: dict[str, Any]) -> str:
    for key in ("code", "stock_code", "\uc885\ubaa9\ucf54\ub4dc"):
        code = _normalize_stock_code(order.get(key))
        if code:
            return code
    return ""


def _durable_holding_qty(
    stock_code: str,
    state: dict[str, Any],
    positions_data: dict[str, Any] | None,
    broker_data: dict[str, Any] | None,
) -> tuple[int | None, list[str], dict[str, Any]]:
    sources: dict[str, int] = {}
    state_qty = _int_or_none(
        state.get("holding_qty")
        if "holding_qty" in state
        else state.get("quantity")
    )
    if state_qty is not None:
        sources["state"] = state_qty
    position_qty = _quantity_from_collection(positions_data, stock_code, ("positions", "records", "holdings"))
    if position_qty is not None:
        sources["positions"] = position_qty
    broker_qty = _quantity_from_collection(broker_data, stock_code, ("broker_holdings", "holdings", "positions", "records"))
    if broker_qty is not None:
        sources["broker_holdings"] = broker_qty
    if not sources:
        return None, [], {"sources": sources}
    if len(set(sources.values())) > 1:
        return None, [f"holding quantity conflict: {sources}"], {"sources": sources}
    return next(iter(sources.values())), [], {"sources": sources}


def _quantity_from_collection(
    data: dict[str, Any] | None,
    stock_code: str,
    keys: tuple[str, ...],
) -> int | None:
    if not isinstance(data, dict):
        return None
    candidates: list[Any] = []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if isinstance(data.get(stock_code), dict):
        candidates.append(data[stock_code])
    for item in candidates:
        if not isinstance(item, dict) or _record_code(item) != stock_code:
            continue
        qty = _int_or_none(
            item.get("quantity")
            if "quantity" in item
            else item.get("holding_qty")
            if "holding_qty" in item
            else item.get("qty")
        )
        if qty is not None:
            return qty
    return None


def _quantities_from_collection(
    data: dict[str, Any],
    stock_code: str,
    keys: tuple[str, ...],
) -> tuple[list[int], str]:
    candidates: list[Any] = []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if isinstance(data.get(stock_code), dict):
        candidates.append(data[stock_code])

    quantities: list[int] = []
    for item in candidates:
        if not isinstance(item, dict) or _record_code(item) != stock_code:
            continue
        raw_qty = next(
            (
                item[key]
                for key in ("quantity", "holding_quantity", "holding_qty", "qty")
                if key in item
            ),
            None,
        )
        qty = _int_or_none(raw_qty)
        if qty is None or qty < 0:
            return [], "matching record has an invalid quantity"
        quantities.append(qty)
    if len(quantities) > 1:
        return [], "multiple matching quantity records found"
    return quantities, ""


def _record_code(item: dict[str, Any]) -> str:
    for key in ("code", "stock_code", "\uc885\ubaa9\ucf54\ub4dc"):
        code = _normalize_stock_code(item.get(key))
        if code:
            return code
    return ""


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return None
