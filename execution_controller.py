# -*- coding: utf-8 -*-
"""Preview-only execution controller.

This controller only assembles in-memory previews. It does not write
order_queue.json, does not change execution_enabled, does not create
ORDER_QUEUED records, and does not call SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from order_hoga_mapper import map_order_hoga_preview
from order_type_mapper import map_order_type_preview

try:
    from kiwoom_order_adapter import build_kiwoom_order_request
except Exception:  # pragma: no cover - defensive import boundary
    build_kiwoom_order_request = None


STAGE = "EXECUTION_PREVIEW"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _extract_order_id(order: Any) -> str:
    if not isinstance(order, dict):
        return ""
    for key in ("id", "order_id", "source_order_id"):
        value = _clean_text(order.get(key))
        if value:
            return value
    return ""


def _adapter_preview(order: dict[str, Any], guard: Any) -> dict[str, Any]:
    available = callable(build_kiwoom_order_request)
    result: dict[str, Any] = {
        "available": available,
        "request_preview_built": False,
        "send_order_called": False,
        "warnings": [],
    }

    if not available:
        result["warnings"].append("adapter request preview builder is unavailable")
        return result

    if guard is None:
        result["warnings"].append("guard not supplied; adapter request preview not built")
        return result

    if not isinstance(guard, dict):
        result["warnings"].append("guard must be a dict; adapter request preview not built")
        return result

    request_preview = build_kiwoom_order_request(deepcopy(order), deepcopy(guard))
    result.update(
        {
            "request_preview_built": True,
            "request_preview": request_preview,
            "send_order_called": False,
        }
    )
    return result


def build_execution_preview(order: Any, guard: Any = None) -> dict[str, Any]:
    """Build an in-memory execution preview without promoting the order."""
    warnings: list[str] = []

    order_dict = order if isinstance(order, dict) else {}
    order_id = _extract_order_id(order_dict)
    status = _norm(order_dict.get("status"))
    status_is_real_ready = status == "REAL_READY"

    hoga_preview = map_order_hoga_preview(order_dict)
    order_type_preview = map_order_type_preview(order_dict)
    adapter_request_preview = _adapter_preview(order_dict, guard)

    if not isinstance(order, dict):
        warnings.append("order must be a dict")

    if not status_is_real_ready:
        warnings.append(f"order status is not REAL_READY: {status or 'EMPTY'}")

    warnings.extend(hoga_preview.get("warnings", []))
    warnings.extend(order_type_preview.get("warnings", []))
    warnings.extend(adapter_request_preview.get("warnings", []))

    unresolved = (
        not status_is_real_ready
        or bool(hoga_preview.get("unresolved"))
        or bool(order_type_preview.get("unresolved"))
    )

    return {
        "ok": not unresolved,
        "stage": STAGE,
        "order_id": order_id,
        "status": status,
        "status_is_real_ready": status_is_real_ready,
        "hoga_preview": hoga_preview,
        "order_type_preview": order_type_preview,
        "adapter_request_preview": adapter_request_preview,
        "unresolved": unresolved,
        "warnings": warnings,
    }
