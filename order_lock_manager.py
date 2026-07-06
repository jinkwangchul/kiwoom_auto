# -*- coding: utf-8 -*-
"""Preview-only order lock manager.

This module only builds lock candidates in memory. It never creates or writes
runtime/order_locks.json, never writes order_queue.json, never creates
ORDER_QUEUED records, and never calls SendOrder.
"""

from __future__ import annotations

import re
from typing import Any


STAGE = "ORDER_LOCK_PREVIEW"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _slug(value: Any) -> str:
    text = _norm(value)
    text = re.sub(r"[^A-Z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "UNRESOLVED"


def _extract_order_id(order: dict[str, Any]) -> str:
    for key in ("id", "order_id", "source_order_id"):
        value = _clean_text(order.get(key))
        if value:
            return value
    return ""


def _extract_source_signal_id(order: dict[str, Any]) -> str:
    value = _clean_text(order.get("source_signal_id"))
    if value:
        return value

    provenance = _as_dict(order.get("order_provenance"))
    return _clean_text(provenance.get("source_signal_id"))


def _extract_side_or_order_type(order: dict[str, Any], execution_preview: dict[str, Any]) -> str:
    for key in ("side", "order_type"):
        value = _norm(order.get(key))
        if value:
            return value

    order_type_preview = _as_dict(execution_preview.get("order_type_preview"))
    return _norm(order_type_preview.get("order_type"))


def build_order_lock_preview(order: Any, execution_preview: Any) -> dict[str, Any]:
    """Build a deterministic in-memory order lock candidate."""
    order_dict = _as_dict(order)
    preview_dict = _as_dict(execution_preview)
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(order, dict):
        warnings.append("order must be a dict")
    if not isinstance(execution_preview, dict):
        warnings.append("execution_preview must be a dict")

    order_id = _extract_order_id(order_dict)
    source_signal_id = _extract_source_signal_id(order_dict)
    code = _clean_text(order_dict.get("code"))
    side_or_order_type = _extract_side_or_order_type(order_dict, preview_dict)

    if not order_id:
        blocked_reasons.append("order_id is required")
    if not source_signal_id:
        blocked_reasons.append("source_signal_id is required")
    if not code:
        blocked_reasons.append("code is required")
    if not side_or_order_type:
        blocked_reasons.append("side/order_type is required")

    unresolved = bool(blocked_reasons)
    lock_key = None
    lock_id = None

    if not unresolved:
        lock_key = ":".join(
            [
                _slug(code),
                _slug(side_or_order_type),
                _slug(source_signal_id),
            ]
        )
        lock_id = "_".join(
            [
                "LOCK_PREVIEW",
                _slug(order_id),
                _slug(code),
                _slug(side_or_order_type),
                _slug(source_signal_id),
            ]
        )

    return {
        "ok": not unresolved,
        "stage": STAGE,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": code,
        "side_or_order_type": side_or_order_type,
        "lock_key": lock_key,
        "lock_id": lock_id,
        "unresolved": unresolved,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
    }
