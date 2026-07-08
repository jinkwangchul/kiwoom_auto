# -*- coding: utf-8 -*-
"""Adapter from approved rule apply preview to a REAL_READY order contract.

This module is preview-only. It does not write rules.json, runtime files, or
order_queue.json, and it does not call queue commit, SendOrder, Kiwoom, or GUI
flows. It only builds an in-memory order dict that existing execution preview
services can consume.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "RULE_APPLY_PREVIEW_EXECUTION_ORDER_ADAPTER"
ORDER_STATUS = "REAL_READY"
SOURCE = "rule_apply_preview_execution_order_adapter"

_REQUIRED_SIGNAL_FIELDS = (
    "order_id",
    "source_signal_id",
    "code",
    "side",
    "quantity",
    "price",
    "hoga",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _merged_context(defaults: Any, signal_context: Any) -> dict[str, Any]:
    merged = deepcopy(_as_dict(defaults))
    merged.update(deepcopy(_as_dict(signal_context)))
    return merged


def _missing_required_fields(context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in _REQUIRED_SIGNAL_FIELDS:
        value = context.get(field)
        if field in {"quantity", "price"}:
            if value is None or value == "":
                missing.append(field)
            continue
        if not _clean_text(value):
            missing.append(field)
    return missing


def _validate_apply_preview(apply_preview: Any) -> dict[str, Any]:
    preview = _as_dict(apply_preview)
    if not preview:
        raise ValueError("apply_preview must be a non-empty dict")
    if preview.get("mode") != "approved_rule_apply_preview":
        raise ValueError("apply_preview.mode must be approved_rule_apply_preview")
    if preview.get("stage") != "RULE_APPLY_PREVIEW":
        raise ValueError("apply_preview.stage must be RULE_APPLY_PREVIEW")
    if not isinstance(preview.get("applied_rules_preview"), dict):
        raise ValueError("apply_preview.applied_rules_preview is required")
    return preview


def build_rule_apply_preview_execution_order_contract(
    apply_preview: Any,
    signal_context: Any,
    guard_defaults: Any = None,
    order_defaults: Any = None,
) -> dict[str, Any]:
    """Build a preview-only REAL_READY order contract from rule apply preview.

    ``signal_context`` supplies the execution-specific order fields that a rule
    apply preview intentionally does not contain. ``order_defaults`` may provide
    defaults for those fields, and explicit ``signal_context`` values win.
    """
    preview = _validate_apply_preview(apply_preview)
    context = _merged_context(order_defaults, signal_context)
    if not isinstance(signal_context, dict):
        raise ValueError("signal_context must be a dict")

    missing = _missing_required_fields(context)
    if missing:
        raise ValueError(f"signal_context missing required fields: {', '.join(missing)}")

    order_id = _clean_text(context.get("order_id"))
    side = _clean_text(context.get("side")).upper()
    hoga = _clean_text(context.get("hoga"))
    order_intent = deepcopy(_as_dict(context.get("order_intent")))
    order_intent.update({"side": side, "hoga": hoga})

    order_contract = deepcopy(_as_dict(order_defaults))
    order_contract.update(
        {
            "id": order_id,
            "order_id": order_id,
            "source_order_id": _clean_text(context.get("source_order_id")) or order_id,
            "source_signal_id": _clean_text(context.get("source_signal_id")),
            "status": ORDER_STATUS,
            "code": _clean_text(context.get("code")),
            "side": side,
            "quantity": deepcopy(context.get("quantity")),
            "price": deepcopy(context.get("price")),
            "execution_enabled": True,
            "order_intent": order_intent,
            "preview_only": True,
            "adapter_type": ADAPTER_TYPE,
            "source": SOURCE,
            "rule_apply_preview": deepcopy(preview),
            "rule_snapshot_preview": deepcopy(preview.get("applied_rules_preview")),
            "rule_apply_summary": deepcopy(_as_dict(preview.get("summary"))),
            "guard_defaults": deepcopy(_as_dict(guard_defaults)),
        }
    )
    return order_contract
