# -*- coding: utf-8 -*-
"""Read-only timeout eligibility for broker-confirmed open orders."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"

_OPEN_STATUSES = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_TERMINAL_STATUSES = {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "PARTIAL_CANCELLED",
    "BROKER_REJECTED",
    "SEND_CALL_REJECTED",
    "REJECTED",
}
_UNCERTAIN_STATUSES = {
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
}
_ACTIVE_CANCEL_STATUSES = {
    "PENDING", "APPROVED", "EXECUTABLE", "EXECUTION_ENABLED", "REAL_READY",
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
    "BROKER_ACCEPTED",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def _execution_intent(order: dict[str, Any]) -> dict[str, Any]:
    direct = _as_dict(order.get("execution_intent"))
    if direct:
        return direct
    request = _as_dict(order.get("execution_request"))
    return _as_dict(request.get("execution_intent"))


def _request_preview(order: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(order.get("execution_request")).get("request_preview"))


def _cancel_original_order_no(order: dict[str, Any]) -> str:
    request = _as_dict(order.get("execution_request"))
    preview = _as_dict(request.get("request_preview"))
    return _text(preview.get("original_order_no") or order.get("original_order_no"))


def _timeout_anchor(order: dict[str, Any]) -> tuple[datetime | None, str]:
    broker_accepted_at = _timestamp(order.get("broker_accepted_at"))
    if broker_accepted_at is not None:
        return broker_accepted_at, "BROKER_ACCEPTED_AT"
    accepted_events: list[datetime] = []
    events = order.get("chejan_events")
    if isinstance(events, list):
        for item in events:
            event = item if isinstance(item, dict) else {}
            event_type = _text(event.get("event_type")).upper()
            if event_type not in {"ORDER_ACCEPTED", "ORDER_OPEN", "BROKER_ACCEPT"}:
                continue
            received_at = _timestamp(event.get("received_at"))
            if received_at is not None:
                accepted_events.append(received_at)
    if accepted_events:
        return min(accepted_events), "CHEJAN_ORDER_RECEIVED_AT"
    if order.get("send_call_accepted") is True:
        send_accepted_at = _timestamp(order.get("send_call_result_recorded_at"))
        if send_accepted_at is not None:
            return send_accepted_at, "SEND_CALL_ACCEPTED_AT"
    return None, ""


def _review(order: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    preview = _request_preview(order)
    return {
        "code": _text(order.get("code") or preview.get("code")),
        "name": _text(order.get("name")),
        "source_signal_id": _text(order.get("source_signal_id")),
        "execution_process_id": _text(order.get("execution_process_id")),
        "order_queued_id": _text(order.get("id")),
        "order_id": _text(order.get("order_id")),
        "broker_order_no": _text(order.get("broker_order_no")),
        "review_reasons": sorted(set(reasons)),
        "review_location": "UNFILLED_TIMEOUT_CANCEL_RECONCILIATION",
    }


def cancel_effect_state(order: dict[str, Any]) -> str:
    """Read control-order evidence; never treat a CANCEL as another NEW child."""
    if _text(order.get("order_action") or _request_preview(order).get("order_action")).upper() != "CANCEL":
        return ""
    status = _text(order.get("status")).upper()
    if (status == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True
            or order.get("send_uncertain") is True or order.get("call_execution_uncertain") is True):
        return "UNCERTAIN"
    if order.get("original_order_effect_confirmed") is True:
        return "CONFIRMED"
    if status in {"SEND_CALL_REJECTED", "BROKER_REJECTED", "REJECTED", "BLOCKED", "BLOCKED_POLICY"}:
        return "REJECTED"
    if status in _ACTIVE_CANCEL_STATUSES:
        return "PENDING"
    return "UNCERTAIN"


def inspect_unfilled_cancel_eligibility(
    *,
    selected_account_no: str,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    now: datetime | None = None,
    limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
) -> dict[str, Any]:
    """Return timeout-qualified original orders without writing Queue or calling Broker."""
    account_no = _text(selected_account_no)
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None
        else None
    )
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}
    try:
        root = json.loads(Path(order_queue_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "proposals": [],
            "reviews": [],
            "waiting": [],
            "errors": [f"ORDER_QUEUE_READ_FAILED:{exc}"],
        }
    orders = root.get("orders") if isinstance(root, dict) else None
    if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
        return {
            "ok": False,
            "proposals": [],
            "reviews": [],
            "waiting": [],
            "errors": ["ORDER_QUEUE_SCHEMA_INVALID"],
        }

    current = now or datetime.now()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    max_proposals = max(0, int(limit or 0))
    active_cancel_records: dict[str, list[dict[str, Any]]] = {}
    for item in orders:
        if (
            _text(item.get("order_action") or _request_preview(item).get("order_action")).upper()
            not in {"CANCEL", "MODIFY"}
            or _text(item.get("status")).upper() not in _ACTIVE_CANCEL_STATUSES
            or item.get("original_order_effect_confirmed") is True
        ):
            continue
        original_no = _cancel_original_order_no(item)
        if original_no:
            active_cancel_records.setdefault(original_no, []).append(item)
    active_cancel_originals = set(active_cancel_records)
    uncertain_cancel_originals = {
        _cancel_original_order_no(item)
        for item in orders
        if _text(item.get("order_action")).upper() == "CANCEL"
        and _text(item.get("status")).upper() == "SEND_UNCERTAIN"
        and item.get("original_order_effect_confirmed") is not True
        and _cancel_original_order_no(item)
    }

    eligible: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for raw in orders:
        order = deepcopy(raw)
        intent = _execution_intent(order)
        request_preview = _request_preview(order)
        policy = _as_dict(intent.get("unfilled_timeout_policy"))
        if not policy or policy.get("enabled") is False:
            continue
        if _text(policy.get("policy")).upper() != "CANCEL_PENDING_ORDER":
            reviews.append(_review(order, ["UNFILLED_TIMEOUT_POLICY_INVALID"]))
            continue
        code = _text(order.get("code") or request_preview.get("code"))
        if allowed is not None and code not in allowed:
            continue
        if _text(order.get("account_no") or request_preview.get("account_no")) != account_no:
            continue
        side = _text(order.get("side") or request_preview.get("side")).upper()
        if side not in {"BUY", "SELL"}:
            reviews.append(_review(order, ["UNFILLED_TIMEOUT_ORDER_SIDE_MISMATCH"]))
            continue
        order_action = _text(
            order.get("order_action") or request_preview.get("order_action") or "NEW"
        ).upper()
        if order_action not in {"NEW", "MODIFY"}:
            continue
        status = _text(order.get("status")).upper()
        if status in _TERMINAL_STATUSES:
            continue
        # Source candidate remains beside its dispatch record in the Queue.
        if status in {"PENDING", "APPROVED", "EXECUTABLE", "EXECUTION_ENABLED", "REAL_READY"}:
            continue
        if status in _UNCERTAIN_STATUSES:
            if status == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True:
                reviews.append(_review(order, ["UNFILLED_TIMEOUT_ORDER_IDENTITY_UNCERTAIN"]))
            else:
                waiting.append({"order_queued_id": _text(order.get("id")), "reason": "BROKER_ACCEPTANCE_PENDING"})
            continue
        if status not in _OPEN_STATUSES:
            reviews.append(_review(order, [f"UNFILLED_TIMEOUT_STATUS_UNSUPPORTED:{status or 'EMPTY'}"]))
            continue
        reasons: list[str] = []
        broker_order_no = _text(order.get("broker_order_no"))
        remaining = _positive_int(order.get("remaining_quantity"))
        if _nonnegative_int(order.get("remaining_quantity")) == 0:
            continue
        process_id = _text(order.get("execution_process_id") or intent.get("execution_process_id"))
        source_signal_id = _text(order.get("source_signal_id") or intent.get("source_signal_id"))
        routine_instance_id = _text(
            order.get("routine")
            or intent.get("routine_instance_id")
            or _as_dict(_as_dict(order.get("execution_request")).get("routine_provenance")).get("routine_instance_id")
        )
        accepted_at, anchor_source = _timeout_anchor(order)
        timeout_ms = _nonnegative_int(policy.get("timeout_ms"))
        scope = _text(policy.get("scope")).upper()
        if not broker_order_no:
            reasons.append("UNFILLED_TIMEOUT_BROKER_ORDER_NO_MISSING")
        if remaining is None:
            reasons.append("UNFILLED_TIMEOUT_REMAINING_QUANTITY_INVALID")
        if not process_id:
            reasons.append("UNFILLED_TIMEOUT_EXECUTION_PROCESS_ID_MISSING")
        if not source_signal_id:
            reasons.append("UNFILLED_TIMEOUT_SOURCE_SIGNAL_ID_MISSING")
        if not routine_instance_id:
            reasons.append("UNFILLED_TIMEOUT_ROUTINE_INSTANCE_ID_MISSING")
        if accepted_at is None:
            reasons.append("UNFILLED_TIMEOUT_BROKER_ACCEPTED_AT_MISSING")
        if timeout_ms is None:
            reasons.append("UNFILLED_TIMEOUT_DURATION_INVALID")
        if scope not in {"EACH", "BATCH"}:
            reasons.append("UNFILLED_TIMEOUT_SCOPE_INVALID")
        if order.get("manual_reconciliation_required") is True:
            reasons.append("UNFILLED_TIMEOUT_MANUAL_RECONCILIATION_REQUIRED")
        for field, expected in (("source_signal_id", source_signal_id), ("execution_process_id", process_id), ("side", side)):
            if intent.get(field) not in (None, "", expected):
                reasons.append("UNFILLED_TIMEOUT_ORDER_IDENTITY_MISMATCH")
        if order.get("send_uncertain") is True or order.get("call_execution_uncertain") is True:
            reasons.append("UNFILLED_TIMEOUT_ORDER_IDENTITY_UNCERTAIN")
        if broker_order_no in uncertain_cancel_originals:
            reasons.append("UNFILLED_TIMEOUT_CANCEL_SEND_UNCERTAIN")
        for cancel in active_cancel_records.get(broker_order_no, []):
            cancel_preview = _request_preview(cancel)
            cancel_account = _text(cancel.get("account_no") or cancel_preview.get("account_no"))
            cancel_code = _text(cancel.get("code") or cancel_preview.get("code"))
            cancel_side = _text(cancel.get("side") or cancel_preview.get("side")).upper()
            if (
                cancel_account != account_no
                or cancel_code != code
                or cancel_side != side
            ):
                reasons.append("UNFILLED_TIMEOUT_ACTIVE_CANCEL_IDENTITY_MISMATCH")
        if reasons:
            reviews.append(_review(order, reasons))
            continue
        assert accepted_at is not None and timeout_ms is not None and remaining is not None
        due_at = accepted_at + timedelta(milliseconds=timeout_ms)
        item = {
            "source_order": order,
            "order_queued_id": _text(order.get("id")),
            "order_id": _text(order.get("order_id")),
            "broker_order_no": broker_order_no,
            "account_no": account_no,
            "code": code,
            "side": side,
            "routine_instance_id": routine_instance_id,
            "source_signal_id": source_signal_id,
            "execution_process_id": process_id,
            "remaining_quantity": remaining,
            "scope": scope,
            "timeout_ms": timeout_ms,
            "timeout_anchor": anchor_source,
            "timeout_anchor_at": accepted_at.isoformat(timespec="milliseconds"),
            "timeout_due_at": due_at.isoformat(timespec="milliseconds"),
            "due": current >= due_at,
            "active_cancel": broker_order_no in active_cancel_originals,
        }
        eligible.append(item)

    by_process: dict[str, list[dict[str, Any]]] = {}
    for item in eligible:
        by_process.setdefault(item["execution_process_id"], []).append(item)

    invalid_processes: set[str] = set()
    for process_id, process_orders in by_process.items():
        identities = {
            (
                item["account_no"],
                item["side"],
                item["code"],
                item["source_signal_id"],
                item["routine_instance_id"],
                item["scope"],
                item["timeout_ms"],
            )
            for item in process_orders
        }
        if len(identities) <= 1:
            continue
        invalid_processes.add(process_id)
        reviews.extend(
            _review(item["source_order"], ["UNFILLED_TIMEOUT_PROCESS_POLICY_MISMATCH"])
            for item in process_orders
        )

    proposals: list[dict[str, Any]] = []
    # An ambiguous sibling must not be bypassed by the otherwise valid batch.
    invalid_processes.update(_text(item.get("execution_process_id")) for item in reviews)
    proposed_originals: set[str] = set()
    for item in eligible:
        if max_proposals == 0:
            break
        if item["execution_process_id"] in invalid_processes:
            continue
        if item["active_cancel"]:
            waiting.append({"order_queued_id": item["order_queued_id"], "reason": "ACTIVE_CANCEL_EXISTS"})
            continue
        if not item["due"]:
            waiting.append({"order_queued_id": item["order_queued_id"], "reason": "TIMEOUT_NOT_REACHED"})
            continue
        targets = [item]
        if item["scope"] == "BATCH":
            targets = by_process.get(item["execution_process_id"], [item])
        for target in targets:
            original_no = target["broker_order_no"]
            if target["active_cancel"] or original_no in proposed_originals:
                continue
            proposed_originals.add(original_no)
            proposals.append(deepcopy(target))
            if len(proposals) >= max_proposals:
                break
        if len(proposals) >= max_proposals:
            break

    return {
        "ok": not bool(reviews),
        "proposals": proposals,
        "reviews": reviews,
        "waiting": waiting,
        "errors": [],
        "inspected_open_orders": len(eligible),
        "proposal_limit": max_proposals,
    }


# Preserve old callers while the operation timer uses the side-neutral name.
inspect_unfilled_sell_cancel_eligibility = inspect_unfilled_cancel_eligibility

__all__ = ["inspect_unfilled_cancel_eligibility", "inspect_unfilled_sell_cancel_eligibility", "cancel_effect_state"]
