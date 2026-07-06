# -*- coding: utf-8 -*-
"""Preview-only request hash builder.

This module only builds a stable request hash candidate in memory. It never
writes runtime/order_executions.json, runtime/order_locks.json, or
order_queue.json, and it never calls SendOrder.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any


STAGE = "REQUEST_HASH_PREVIEW"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _extract_hoga(execution_preview: dict[str, Any]) -> str:
    hoga_preview = _as_dict(execution_preview.get("hoga_preview"))
    return _norm(hoga_preview.get("hoga"))


def _extract_lock_id(lock_preview: dict[str, Any]) -> str:
    return _clean_text(lock_preview.get("lock_id"))


def _stable_number_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return _clean_text(value)

    if number == number.to_integral_value():
        return str(number.to_integral_value())
    return format(number.normalize(), "f")


def _stable_hash(hash_source: dict[str, Any]) -> str:
    payload = json.dumps(
        hash_source,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_order_request_hash_preview(
    order: Any,
    execution_preview: Any,
    lock_preview: Any,
) -> dict[str, Any]:
    """Build a stable request hash candidate without side effects."""
    order_dict = _as_dict(order)
    execution_preview_dict = _as_dict(execution_preview)
    lock_preview_dict = _as_dict(lock_preview)
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(order, dict):
        warnings.append("order must be a dict")
    if not isinstance(execution_preview, dict):
        warnings.append("execution_preview must be a dict")
    if not isinstance(lock_preview, dict):
        warnings.append("lock_preview must be a dict")

    hash_source = {
        "order_id": _extract_order_id(order_dict),
        "source_signal_id": _extract_source_signal_id(order_dict),
        "code": _clean_text(order_dict.get("code")),
        "side_or_order_type": _extract_side_or_order_type(order_dict, execution_preview_dict),
        "quantity": _stable_number_text(order_dict.get("quantity")),
        "price": _stable_number_text(order_dict.get("price")),
        "hoga": _extract_hoga(execution_preview_dict),
        "lock_id": _extract_lock_id(lock_preview_dict),
    }

    required_names = {
        "order_id": "order_id is required",
        "source_signal_id": "source_signal_id is required",
        "code": "code is required",
        "side_or_order_type": "side/order_type is required",
        "quantity": "quantity is required",
        "price": "price is required",
        "hoga": "hoga is required",
        "lock_id": "lock_id is required",
    }

    for key, reason in required_names.items():
        if not hash_source[key]:
            blocked_reasons.append(reason)

    unresolved = bool(blocked_reasons)
    request_hash = None if unresolved else _stable_hash(hash_source)

    return {
        "ok": not unresolved,
        "stage": STAGE,
        "request_hash": request_hash,
        "hash_source": hash_source,
        "unresolved": unresolved,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
    }
