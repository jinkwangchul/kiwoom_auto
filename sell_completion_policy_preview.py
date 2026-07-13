# -*- coding: utf-8 -*-
"""Preview-only SELL completion policy evaluator.

This module derives completion policy from a SELL method snapshot and an
already-built exit preview. It never recalculates exit conditions, creates order
requests, or connects runtime, queue, execution, or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "SELL_COMPLETION_POLICY_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
POLICY_CARRY_TO_NEXT_SIGNAL = "CARRY_TO_NEXT_SIGNAL"
POLICY_MARKET_SELL_REMAINING = "MARKET_SELL_REMAINING"
SAFETY_FLAGS = ("execution_connected", "runtime_write", "send_order", "queue_write")
EXIT_CHECK_KEYS = ("exit_price_check", "exit_count_check", "exit_time_check")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).upper() in {"1", "TRUE", "YES", "Y", "ON", "CHECKED"}


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


def _has_exit_conditions(snapshot: dict[str, Any]) -> bool:
    return any(_truthy(snapshot.get(key)) for key in EXIT_CHECK_KEYS)


def _exit_triggered(exit_preview: dict[str, Any] | None, runtime: dict[str, Any]) -> bool:
    if runtime.get("exit_triggered") is True:
        return True
    if not isinstance(exit_preview, dict):
        return False
    return (
        exit_preview.get("status") == STATUS_READY
        and len(_as_list(exit_preview.get("matched_conditions"))) > 0
    )


def _safety_reasons(*containers: Any) -> list[str]:
    reasons: list[str] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        for flag in SAFETY_FLAGS:
            if container.get(flag) is True:
                reasons.append(f"safety flag must be false: {flag}")
    return reasons


def _remaining_qty(runtime: dict[str, Any]) -> tuple[float | None, str | None]:
    if "remaining_qty" not in runtime:
        return None, None
    value = _number(runtime.get("remaining_qty"))
    if value is None:
        return None, "remaining_qty is invalid"
    return value, None


def build_sell_completion_policy_preview(
    method_preview: Any,
    exit_preview: Any = None,
    market_context: Any = None,
    runtime_context: Any = None,
) -> dict[str, Any]:
    """Build completion policy preview without creating executable orders."""
    preview = _method_preview(method_preview)
    exit_data = deepcopy(exit_preview) if exit_preview is not None else None
    market = deepcopy(_as_dict(market_context))
    runtime = deepcopy(_as_dict(runtime_context))

    reasons: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []
    method_snapshot_copy: dict[str, Any] | None = None
    policy: str | None = None
    action_preview: dict[str, Any] | None = None
    remaining_qty: float | None = None

    if preview is None:
        invalid.append("method_preview must be a dict")
        snapshot = None
    else:
        preview = deepcopy(preview)
        snapshot = _method_snapshot(preview)

    if exit_preview is not None and not isinstance(exit_preview, dict):
        invalid.append("exit_preview must be a dict")
        exit_data = None

    if not isinstance(snapshot, dict):
        invalid.append("method_snapshot must be a dict")
    else:
        method_snapshot_copy = deepcopy(snapshot)
        invalid.extend(_safety_reasons(preview, snapshot, exit_data, market, runtime))
        has_exit = _has_exit_conditions(snapshot)
        policy = POLICY_MARKET_SELL_REMAINING if has_exit else POLICY_CARRY_TO_NEXT_SIGNAL

        qty, qty_error = _remaining_qty(runtime)
        remaining_qty = qty
        if qty_error:
            invalid.append(qty_error)

        if not invalid:
            if not has_exit:
                reasons.append("exit conditions are not configured")
                status = STATUS_NOT_APPLICABLE
            elif not _exit_triggered(exit_data if isinstance(exit_data, dict) else None, runtime):
                reasons.append("exit conditions are not triggered")
                status = STATUS_NOT_APPLICABLE
            elif remaining_qty is None:
                reasons.append("remaining_qty is required")
                status = STATUS_BLOCKED
            elif remaining_qty <= 0:
                reasons.append("remaining_qty is not greater than 0")
                status = STATUS_NOT_APPLICABLE
            else:
                status = STATUS_READY
                action_preview = {
                    "action": POLICY_MARKET_SELL_REMAINING,
                    "quantity": remaining_qty,
                    "order_request_created": False,
                    "execution_connected": False,
                }
        else:
            status = STATUS_INVALID

    if invalid:
        status = STATUS_INVALID

    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "send_order": False,
        "queue_write": False,
        "status": status,
        "method_set": _method_set(preview),
        "policy": policy,
        "remaining_qty": remaining_qty,
        "action_preview": action_preview,
        "method_snapshot": method_snapshot_copy,
        "exit_snapshot": deepcopy(exit_data) if isinstance(exit_data, dict) else {},
        "market_context_snapshot": deepcopy(market),
        "runtime_context_snapshot": deepcopy(runtime),
        "reasons": list(reasons + invalid),
        "warnings": warnings,
    }
