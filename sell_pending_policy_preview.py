# -*- coding: utf-8 -*-
"""Preview-only SELL pending order policy evaluator.

This module reads ``perform3_pending_*`` fields from one SELL method snapshot
and an order context. It never calls cancel APIs and never connects runtime,
queue, execution, or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "SELL_PENDING_POLICY_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
POLICY_CANCEL_PENDING_ORDER = "CANCEL_PENDING_ORDER"
SAFETY_FLAGS = ("execution_connected", "runtime_write", "send_order", "queue_write")
FINAL_ORDER_STATUSES = {"FILLED", "COMPLETED", "CANCELLED", "CANCELED", "전량체결", "취소완료"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).upper()


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _method_preview(source: Any) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    if "method_snapshot" in source:
        return source
    previews = _as_list(source.get("method_previews"))
    if len(previews) == 1 and isinstance(previews[0], dict):
        return previews[0]
    return source


def _method_snapshot(preview: dict[str, Any]) -> Any:
    if "method_snapshot" in preview:
        return preview.get("method_snapshot")
    return preview


def _method_set(preview: dict[str, Any] | None) -> str | None:
    if not isinstance(preview, dict):
        return None
    value = _text(preview.get("method_set"))
    return value or None


def _is_pending_mode(value: Any) -> bool:
    token = _text(value)
    return token in {"미체결", "PENDING", "PENDING_ORDER"} or "誘몄껜" in token


def _scope(value: Any) -> tuple[str | None, str | None]:
    token = _text(value)
    upper = token.upper()
    if token == "매회" or upper == "EACH" or "留ㅽ쉶" in token:
        return "EACH", None
    if token == "일괄" or upper == "BATCH" or "쇨큵" in token:
        return "BATCH", None
    return None, "perform3_pending_scope is invalid"


def _unit_threshold(value: Any, raw_unit: Any) -> tuple[float | None, float | None, str | None]:
    number = _number(value)
    if number is None:
        return None, None, "perform3_pending_value is invalid"
    if number < 0:
        return None, None, "perform3_pending_value is invalid"

    token = _text(raw_unit)
    upper = token.upper()
    if token == "초" or upper in {"SECOND", "SECONDS", "SEC"} or "珥" in token:
        return number, None, None
    if token == "분" or upper in {"MINUTE", "MINUTES", "MIN"} or "遺" in token:
        return number * 60, None, None
    if token == "봉" or upper in {"BAR", "BARS"} or "遊" in token:
        return None, number, None
    return None, None, "perform3_pending_unit is invalid"


def _safety_reasons(*containers: Any) -> list[str]:
    reasons: list[str] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        for flag in SAFETY_FLAGS:
            if container.get(flag) is True:
                reasons.append(f"safety flag must be false: {flag}")
    return reasons


def _final_order_status(value: Any) -> bool:
    return _text(value) in FINAL_ORDER_STATUSES or _norm(value) in FINAL_ORDER_STATUSES


def _action(scope: str, order_id: Any, remaining_qty: float) -> dict[str, Any]:
    return {
        "action": POLICY_CANCEL_PENDING_ORDER,
        "scope": scope,
        "order_id": _text(order_id),
        "remaining_qty": remaining_qty,
        "cancel_order_called": False,
        "execution_connected": False,
    }


def build_sell_pending_policy_preview(
    method_preview: Any,
    order_context: Any = None,
    market_context: Any = None,
    runtime_context: Any = None,
) -> dict[str, Any]:
    """Build pending-order cancellation preview without cancelling orders."""
    preview = _method_preview(method_preview)
    order_is_invalid_type = order_context is not None and not isinstance(order_context, dict)
    order = deepcopy(_as_dict(order_context))
    market = deepcopy(_as_dict(market_context))
    runtime = deepcopy(_as_dict(runtime_context))

    reasons: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []
    method_snapshot_copy: dict[str, Any] | None = None
    policy: str | None = None
    scope: str | None = None
    action_preview: dict[str, Any] | None = None
    remaining_qty: float | None = None
    elapsed_time: float | None = None
    elapsed_bars: float | None = None
    threshold_seconds: float | None = None
    threshold_bars: float | None = None

    if preview is None:
        invalid.append("method_preview must be a dict")
        snapshot = None
    else:
        preview = deepcopy(preview)
        snapshot = _method_snapshot(preview)

    if order_is_invalid_type:
        invalid.append("order_context must be a dict")

    if not isinstance(snapshot, dict):
        invalid.append("method_snapshot must be a dict")
    else:
        method_snapshot_copy = deepcopy(snapshot)
        invalid.extend(_safety_reasons(preview, snapshot, order, market, runtime))

        if not _is_pending_mode(snapshot.get("perform3_title_combo")):
            status = STATUS_NOT_APPLICABLE
            reasons.append("pending policy is not selected")
        else:
            policy = POLICY_CANCEL_PENDING_ORDER
            scope, scope_error = _scope(snapshot.get("perform3_pending_scope"))
            threshold_seconds, threshold_bars, threshold_error = _unit_threshold(
                snapshot.get("perform3_pending_value"),
                snapshot.get("perform3_pending_unit"),
            )
            if scope_error:
                invalid.append(scope_error)
            if threshold_error:
                invalid.append(threshold_error)

            remaining_qty = _number(order.get("remaining_qty"))
            if "remaining_qty" in order and remaining_qty is None:
                invalid.append("remaining_qty is invalid")
            elapsed_time = _number(order.get("elapsed_time"))
            if order.get("elapsed_time") not in (None, "") and elapsed_time is None:
                invalid.append("elapsed_time is invalid")
            elapsed_bars = _number(order.get("elapsed_bars"))
            if order.get("elapsed_bars") not in (None, "") and elapsed_bars is None:
                invalid.append("elapsed_bars is invalid")

            if invalid:
                status = STATUS_INVALID
            elif _final_order_status(order.get("order_status")):
                reasons.append("order status is final")
                status = STATUS_NOT_APPLICABLE
            elif not _text(order.get("order_id")):
                reasons.append("order_id is required")
                status = STATUS_BLOCKED
            elif "remaining_qty" not in order:
                reasons.append("remaining_qty is required")
                status = STATUS_BLOCKED
            elif remaining_qty is not None and remaining_qty <= 0:
                reasons.append("remaining_qty is not greater than 0")
                status = STATUS_NOT_APPLICABLE
            elif threshold_bars is not None:
                if "elapsed_bars" not in order:
                    reasons.append("elapsed_bars is required")
                    status = STATUS_BLOCKED
                else:
                    status = STATUS_READY
                    if elapsed_bars is not None and elapsed_bars >= threshold_bars:
                        assert scope is not None and remaining_qty is not None
                        action_preview = _action(scope, order.get("order_id"), remaining_qty)
            else:
                if "elapsed_time" not in order:
                    reasons.append("elapsed_time is required")
                    status = STATUS_BLOCKED
                else:
                    status = STATUS_READY
                    if elapsed_time is not None and threshold_seconds is not None and elapsed_time >= threshold_seconds:
                        assert scope is not None and remaining_qty is not None
                        action_preview = _action(scope, order.get("order_id"), remaining_qty)

    if invalid:
        status = STATUS_INVALID

    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "send_order": False,
        "queue_write": False,
        "cancel_order_called": False,
        "status": status,
        "method_set": _method_set(preview),
        "policy": policy,
        "scope": scope,
        "order_id": _text(order.get("order_id")) or None,
        "remaining_qty": remaining_qty,
        "elapsed_time": elapsed_time,
        "elapsed_bars": elapsed_bars,
        "threshold_seconds": threshold_seconds,
        "threshold_bars": threshold_bars,
        "action_preview": action_preview,
        "method_snapshot": method_snapshot_copy,
        "market_context_snapshot": deepcopy(market),
        "order_context_snapshot": deepcopy(order),
        "runtime_context_snapshot": deepcopy(runtime),
        "reasons": list(reasons + invalid),
        "warnings": warnings,
    }
