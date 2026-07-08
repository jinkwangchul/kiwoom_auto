# -*- coding: utf-8 -*-
"""Preview-only runtime/broker reconciliation after recovery planning.

This module compares provided runtime and broker snapshots in memory. It never
loads runtime files, calls Kiwoom/Broker APIs, writes SQLite/runtime state, or
connects GUI/SendOrder/Chejan flows.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


RECONCILIATION_TYPE = "LIFECYCLE_RUNTIME_RECONCILIATION_PREVIEW"
STATUS_READY = "RECONCILIATION_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


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


def _result(
    *,
    status: str,
    runtime_view: dict[str, Any] | None = None,
    broker_view: dict[str, Any] | None = None,
    mismatch_candidates: list[dict[str, Any]] | None = None,
    reconciliation_actions: list[dict[str, Any]] | None = None,
    review_required_items: list[dict[str, Any]] | None = None,
    reconciliation_summary: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "reconciliation_type": RECONCILIATION_TYPE,
        "status": status,
        "preview_only": True,
        "reconciliation_executed": False,
        "runtime_write": False,
        "broker_write": False,
        "position_write": False,
        "balance_write": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "runtime_view": deepcopy(runtime_view or {}),
        "broker_view": deepcopy(broker_view or {}),
        "mismatch_candidates": deepcopy(mismatch_candidates or []),
        "reconciliation_actions": deepcopy(reconciliation_actions or []),
        "review_required_items": deepcopy(review_required_items or []),
        "reconciliation_summary": deepcopy(reconciliation_summary or {}),
        "validation_result": deepcopy(validation_result or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": status == STATUS_READY,
        "status": status,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _orders_from_view(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    orders = view.get("orders")
    if isinstance(orders, dict):
        return {str(key): _as_dict(value) for key, value in orders.items()}
    if isinstance(orders, list):
        result: dict[str, dict[str, Any]] = {}
        for item in orders:
            payload = _as_dict(item)
            order_id = _text(payload.get("order_id"))
            if order_id:
                result[order_id] = payload
        return result
    order = _as_dict(view.get("order"))
    order_id = _text(order.get("order_id"))
    return {order_id: order} if order_id else {}


def _comparable_fields(context: dict[str, Any]) -> list[str]:
    fields = context.get("compare_fields")
    if isinstance(fields, list) and fields:
        return [_text(field) for field in fields if _text(field)]
    return ["runtime_state", "broker_state", "quantity", "filled_quantity", "remaining_quantity", "price"]


def _candidate(order_id: str, field: str, runtime_value: Any, broker_value: Any, now: str) -> dict[str, Any]:
    return {
        "mismatch_id": "RUNTIME_BROKER_MISMATCH_{}".format(uuid4().hex),
        "order_id": order_id,
        "field": field,
        "runtime_value": deepcopy(runtime_value),
        "broker_value": deepcopy(broker_value),
        "candidate_type": "RUNTIME_BROKER_VALUE_MISMATCH",
        "review_required": True,
        "detected_at": now,
    }


def _missing_candidate(order_id: str, side: str, now: str) -> dict[str, Any]:
    return {
        "mismatch_id": "RUNTIME_BROKER_MISSING_{}".format(uuid4().hex),
        "order_id": order_id,
        "field": "order_presence",
        "runtime_value": side == "runtime",
        "broker_value": side == "broker",
        "candidate_type": "ORDER_MISSING_FROM_{}_VIEW".format("BROKER" if side == "runtime" else "RUNTIME"),
        "review_required": True,
        "detected_at": now,
    }


def _build_mismatches(runtime_view: dict[str, Any], broker_view: dict[str, Any], fields: list[str], now: str) -> list[dict[str, Any]]:
    runtime_orders = _orders_from_view(runtime_view)
    broker_orders = _orders_from_view(broker_view)
    order_ids = sorted(set(runtime_orders) | set(broker_orders))
    mismatches: list[dict[str, Any]] = []
    for order_id in order_ids:
        runtime_order = runtime_orders.get(order_id)
        broker_order = broker_orders.get(order_id)
        if runtime_order is None:
            mismatches.append(_missing_candidate(order_id, "broker", now))
            continue
        if broker_order is None:
            mismatches.append(_missing_candidate(order_id, "runtime", now))
            continue
        for field in fields:
            runtime_value = runtime_order.get(field)
            broker_value = broker_order.get(field)
            if runtime_value != broker_value:
                mismatches.append(_candidate(order_id, field, runtime_value, broker_value, now))
    return mismatches


def _actions_from_mismatches(mismatches: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    return [
        {
            "action_id": "RECONCILIATION_ACTION_{}".format(uuid4().hex),
            "mismatch_id": mismatch.get("mismatch_id", ""),
            "order_id": mismatch.get("order_id", ""),
            "action_type": "MANUAL_REVIEW_REQUIRED",
            "runtime_write": False,
            "broker_write": False,
            "reconciliation_executed": False,
            "planned_at": now,
        }
        for mismatch in mismatches
    ]


def _summary(
    recovery_preview: dict[str, Any],
    mismatches: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    recovery_summary = _as_dict(recovery_preview.get("recovery_summary"))
    return {
        "status": STATUS_READY,
        "persistence_id": recovery_summary.get("persistence_id", ""),
        "order_id": recovery_summary.get("order_id", ""),
        "mismatch_count": len(mismatches),
        "action_count": len(actions),
        "review_required_count": len([item for item in mismatches if item.get("review_required") is True]),
        "preview_only": True,
        "reconciliation_executed": False,
        "generated_at": now,
    }


def build_runtime_reconciliation_preview(
    recovery_preview: Any,
    runtime_snapshot: Any = None,
    broker_snapshot: Any = None,
    reconciliation_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only reconciliation data from supplied snapshots."""
    recovery = _as_dict(recovery_preview)
    context = deepcopy(_as_dict(reconciliation_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(recovery.get("warnings") or [])

    if not recovery:
        issues = ["recovery_preview must be a dict"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    status = _text(recovery.get("status")).upper()
    if status == "BLOCKED":
        issues = ["recovery preview is BLOCKED"] + list(recovery.get("issues") or [])
        return _result(status=STATUS_BLOCKED, issues=issues, warnings=warnings, validation_result=_validation(STATUS_BLOCKED, issues, warnings))
    if status == "INVALID":
        issues = ["recovery preview is INVALID"] + list(recovery.get("issues") or [])
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if status != "RECOVERY_PREVIEW_READY":
        issues = ["recovery preview status is not supported"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if recovery.get("preview_only") is not True:
        issues = ["recovery preview_only must be true"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    runtime_view = deepcopy(_as_dict(runtime_snapshot))
    broker_view = deepcopy(_as_dict(broker_snapshot))
    if not runtime_view:
        issues = ["runtime_snapshot must be a dict"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))
    if not broker_view:
        issues = ["broker_snapshot must be a dict"]
        return _result(status=STATUS_INVALID, issues=issues, warnings=warnings, validation_result=_validation(STATUS_INVALID, issues, warnings))

    fields = _comparable_fields(context)
    mismatches = _build_mismatches(runtime_view, broker_view, fields, now)
    actions = _actions_from_mismatches(mismatches, now)
    review_required = [deepcopy(item) for item in mismatches if item.get("review_required") is True]
    summary = _summary(recovery, mismatches, actions, now)
    validation = _validation(STATUS_READY, [], warnings)

    return _result(
        status=STATUS_READY,
        runtime_view=runtime_view,
        broker_view=broker_view,
        mismatch_candidates=mismatches,
        reconciliation_actions=actions,
        review_required_items=review_required,
        reconciliation_summary=summary,
        validation_result=validation,
        issues=[],
        warnings=warnings,
    )

