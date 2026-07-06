# -*- coding: utf-8 -*-
"""Final execution guard predicate.

This module only evaluates whether a prepared order passes the final guard
inputs. It does not call SendOrder, does not write order_queue.json, does not
change execution_enabled, and does not map Kiwoom codes.
"""

from __future__ import annotations

from typing import Any


STAGE = "FINAL_EXECUTION_GUARD"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _norm(value) in {"TRUE", "YES", "Y", "1", "ON"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def evaluate_final_execution_guard(
    order: Any,
    guard: Any,
    execution_preview: Any,
) -> dict[str, Any]:
    """Evaluate final execution guard conditions without side effects."""
    order_dict = _as_dict(order)
    guard_dict = _as_dict(guard)
    preview_dict = _as_dict(execution_preview)
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(order, dict):
        warnings.append("order must be a dict")
    if not isinstance(guard, dict):
        warnings.append("guard must be a dict")
    if not isinstance(execution_preview, dict):
        warnings.append("execution_preview must be a dict")

    status = _norm(order_dict.get("status"))
    if status != "REAL_READY":
        blocked_reasons.append("order.status is not REAL_READY")

    if not _truthy(order_dict.get("execution_enabled")):
        blocked_reasons.append("order.execution_enabled is not true")

    if not _truthy(guard_dict.get("operator_confirmed")):
        blocked_reasons.append("guard.operator_confirmed is not true")

    if not _truthy(guard_dict.get("real_trade_enabled")):
        blocked_reasons.append("guard.real_trade_enabled is not true")

    hoga_preview = _as_dict(preview_dict.get("hoga_preview"))
    if bool(hoga_preview.get("unresolved", True)):
        blocked_reasons.append("hoga_preview is unresolved")

    order_type_preview = _as_dict(preview_dict.get("order_type_preview"))
    if bool(order_type_preview.get("unresolved", True)):
        blocked_reasons.append("order_type_preview is unresolved")

    if bool(preview_dict.get("unresolved", True)):
        blocked_reasons.append("execution_preview is unresolved")

    return {
        "ok": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "stage": STAGE,
    }
