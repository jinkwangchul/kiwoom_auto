# -*- coding: utf-8 -*-
"""Preview-only runtime projection for committed lifecycle events.

This module builds in-memory read-model projections after a lifecycle commit.
It never writes runtime files, updates GUI state, calls SendOrder, or connects
to live Chejan handlers. The output is a projection preview that another layer
may review before any real persistence is considered.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any


PROJECTION_TYPE = "LIFECYCLE_RUNTIME_PROJECTION"
STATUS_PROJECTED = "PROJECTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

FILL_EVENTS = {"PARTIAL_FILL", "FULL_FILL"}
TERMINAL_EVENTS = {"ORDER_REJECTED", "ORDER_CANCELLED", "FULL_FILL"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _nested_sources(commit_result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [commit_result]
    for key in (
        "lifecycle_transition",
        "transition",
        "transition_record",
        "committed_transition",
        "commit_contract",
        "record",
        "lifecycle_record",
    ):
        value = commit_result.get(key)
        if isinstance(value, dict):
            sources.append(value)

    payload = _json_payload(commit_result.get("payload"))
    if payload:
        sources.append(payload)
        for key in ("commit_contract", "commit_plan", "transition_preview"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                sources.append(nested)

    transition_payload = _json_payload(_as_dict(commit_result.get("transition_record")).get("payload"))
    if transition_payload:
        sources.append(transition_payload)
        contract = transition_payload.get("commit_contract")
        if isinstance(contract, dict):
            sources.append(contract)
    return sources


def _first_text(sources: list[dict[str, Any]], *fields: str) -> str:
    for source in sources:
        for field in fields:
            value = _text(source.get(field))
            if value:
                return value
    return ""


def _first_number(sources: list[dict[str, Any]], *fields: str) -> int | float | None:
    for source in sources:
        for field in fields:
            value = source.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    number = float(value)
                except ValueError:
                    continue
                return int(number) if number.is_integer() else number
    return None


def _identity_from_sources(sources: list[dict[str, Any]]) -> dict[str, str]:
    identity: dict[str, str] = {}
    for source in sources:
        nested = _as_dict(source.get("identity"))
        if nested:
            for key, value in nested.items():
                if _text(value) and not identity.get(key):
                    identity[key] = _text(value)

    for field in (
        "order_id",
        "dispatch_id",
        "source_signal_id",
        "order_queued_id",
        "record_id",
        "evidence_id",
        "commit_token",
    ):
        value = _first_text(sources, field)
        if value:
            identity[field] = value
    return identity


def _base_result(
    *,
    status: str,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    lifecycle_event: str = "",
    identity: dict[str, Any] | None = None,
    runtime_projection: dict[str, Any] | None = None,
    position_projection: dict[str, Any] | None = None,
    balance_projection: dict[str, Any] | None = None,
    runtime_snapshot_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "projection_type": PROJECTION_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "position_write": False,
        "balance_write": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "lifecycle_event": lifecycle_event,
        "identity": deepcopy(identity or {}),
        "runtime_projection": deepcopy(runtime_projection or {}),
        "position_projection": deepcopy(position_projection or {}),
        "balance_projection": deepcopy(balance_projection or {}),
        "runtime_snapshot_projection": deepcopy(runtime_snapshot_projection or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _runtime_state_for_event(event: str) -> str:
    if event == "ORDER_RECEIVED":
        return "ORDER_RECEIVED"
    if event == "ORDER_REJECTED":
        return "ORDER_REJECTED"
    if event == "ORDER_CANCELLED":
        return "ORDER_CANCELLED"
    if event == "PARTIAL_FILL":
        return "PARTIALLY_FILLED"
    if event == "FULL_FILL":
        return "FILLED"
    if event == "UNKNOWN_EVENT":
        return "UNKNOWN_EVENT_REVIEW_REQUIRED"
    return "LIFECYCLE_EVENT_REVIEW_REQUIRED"


def _position_projection(event: str, identity: dict[str, Any], sources: list[dict[str, Any]], now: str) -> dict[str, Any]:
    code = _first_text(sources, "code", "stock_code", "symbol")
    account_no = _first_text(sources, "account_no", "account")
    side = _first_text(sources, "side", "order_side").upper()
    quantity = _first_number(sources, "filled_quantity", "quantity", "executed_quantity")
    price = _first_number(sources, "filled_price", "price", "executed_price")
    fill_like = event in FILL_EVENTS
    return {
        "projection_kind": "POSITION_PROJECTION",
        "position_update_preview": fill_like,
        "position_write": False,
        "position_id": "POSITION_{}_{}".format(account_no, code) if account_no and code else "",
        "order_id": identity.get("order_id", ""),
        "code": code,
        "account_no": account_no,
        "side": side,
        "quantity_delta": quantity if fill_like else 0,
        "price": price,
        "source_lifecycle_event": event,
        "projected_at": now,
        "requires_fill_fields": fill_like and not all([code, account_no, side, quantity, price]),
    }


def _balance_projection(event: str, sources: list[dict[str, Any]], now: str) -> dict[str, Any]:
    side = _first_text(sources, "side", "order_side").upper()
    quantity = _first_number(sources, "filled_quantity", "quantity", "executed_quantity")
    price = _first_number(sources, "filled_price", "price", "executed_price")
    cash_delta: int | float | None = None
    if event in FILL_EVENTS and quantity is not None and price is not None:
        value = quantity * price
        cash_delta = -value if side == "BUY" else value if side == "SELL" else None
    return {
        "projection_kind": "BALANCE_PROJECTION",
        "balance_update_preview": event in FILL_EVENTS,
        "balance_write": False,
        "cash_delta_preview": cash_delta,
        "source_lifecycle_event": event,
        "projected_at": now,
        "requires_cash_review": event in FILL_EVENTS and cash_delta is None,
    }


def _runtime_snapshot_projection(
    snapshot: dict[str, Any],
    event: str,
    identity: dict[str, Any],
    runtime_projection: dict[str, Any],
    position_projection: dict[str, Any],
    balance_projection: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    projected = deepcopy(snapshot)
    projected["snapshot_projected"] = True
    projected["preview_only"] = True
    projected["runtime_write"] = False
    projected["last_projected_at"] = now

    events = list(_as_list(projected.get("lifecycle_events")))
    events.append(
        {
            "event": event,
            "identity": deepcopy(identity),
            "runtime_state": runtime_projection.get("runtime_state"),
            "projected_at": now,
        }
    )
    projected["lifecycle_events"] = events

    orders = deepcopy(_as_dict(projected.get("orders")))
    order_id = _text(identity.get("order_id"))
    if order_id:
        order_view = deepcopy(_as_dict(orders.get(order_id)))
        order_view.update(
            {
                "order_id": order_id,
                "runtime_state": runtime_projection.get("runtime_state"),
                "last_lifecycle_event": event,
                "terminal": event in TERMINAL_EVENTS,
                "updated_at": now,
            }
        )
        orders[order_id] = order_view
    projected["orders"] = orders

    projected["position_projection"] = deepcopy(position_projection)
    projected["balance_projection"] = deepcopy(balance_projection)
    return projected


def project_lifecycle_commit_to_runtime_view(
    lifecycle_commit_result: Any,
    current_runtime_snapshot: Any = None,
    projection_context: Any = None,
) -> dict[str, Any]:
    """Project a committed lifecycle event into preview-only runtime views.

    The function is pure from the repository/runtime perspective: it only
    returns dictionaries and does not persist anything.
    """
    commit_result = _as_dict(lifecycle_commit_result)
    if not commit_result:
        return _base_result(status=STATUS_INVALID, issues=["lifecycle_commit_result must be a dict"])

    status = _text(commit_result.get("status")).upper()
    warnings = list(commit_result.get("warnings") or [])
    if status in {"ABORTED", "BLOCKED", "DENIED"}:
        return _base_result(
            status=STATUS_BLOCKED,
            issues=["lifecycle commit is not committed"] + list(commit_result.get("issues") or []),
            warnings=warnings,
        )
    if status in {"INVALID", "ERROR"}:
        return _base_result(
            status=STATUS_INVALID,
            issues=["lifecycle commit is invalid or errored"] + list(commit_result.get("issues") or []),
            warnings=warnings,
        )
    if status != "COMMITTED":
        return _base_result(status=STATUS_INVALID, issues=["lifecycle_commit_result.status is not supported"], warnings=warnings)

    sources = _nested_sources(commit_result)
    event = _first_text(sources, "candidate_lifecycle_event", "lifecycle_event", "candidate_event", "event").upper()
    identity = _identity_from_sources(sources)
    if not event:
        return _base_result(status=STATUS_INVALID, issues=["committed lifecycle event is required"], warnings=warnings, identity=identity)
    if not _text(identity.get("order_id")):
        return _base_result(
            status=STATUS_INVALID,
            issues=["order_id is required for runtime projection"],
            warnings=warnings,
            lifecycle_event=event,
            identity=identity,
        )

    snapshot = deepcopy(_as_dict(current_runtime_snapshot))
    context = deepcopy(_as_dict(projection_context))
    now = _text(context.get("projected_at")) or _now_text()

    runtime_projection = {
        "projection_kind": "RUNTIME_STATE_PROJECTION",
        "runtime_state": _runtime_state_for_event(event),
        "order_id": identity.get("order_id", ""),
        "dispatch_id": identity.get("dispatch_id", ""),
        "source_signal_id": identity.get("source_signal_id", ""),
        "commit_token": identity.get("commit_token") or _text(commit_result.get("commit_token")),
        "source_lifecycle_event": event,
        "terminal": event in TERMINAL_EVENTS,
        "runtime_write": False,
        "projected_at": now,
    }
    position = _position_projection(event, identity, sources, now)
    balance = _balance_projection(event, sources, now)
    snapshot_projection = _runtime_snapshot_projection(snapshot, event, identity, runtime_projection, position, balance, now)

    projection = {
        "runtime": deepcopy(runtime_projection),
        "position": deepcopy(position),
        "balance": deepcopy(balance),
        "runtime_snapshot": deepcopy(snapshot_projection),
    }
    return _base_result(
        status=STATUS_PROJECTED,
        lifecycle_event=event,
        identity=identity,
        runtime_projection=runtime_projection,
        position_projection=position,
        balance_projection=balance,
        runtime_snapshot_projection=snapshot_projection,
        warnings=warnings,
    ) | {"projection": projection}

