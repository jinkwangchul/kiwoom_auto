# -*- coding: utf-8 -*-
"""Preview-only final manual gate before a SendOrder entrypoint.

This module only evaluates whether a Kiwoom SendOrder request preview may move
to a future single SendOrder entrypoint. It never imports/calls adapter code,
reads/writes runtime files, mutates queue records, or sends orders.
"""

from __future__ import annotations

from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_SEND_ORDER_ENTRYPOINT_REQUIRED = "SEND_ORDER_ENTRYPOINT_REQUIRED"
ADAPTER_NEXT_STAGE_REQUIRED = "FINAL_SEND_GATE_REQUIRED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "final_send_gate_ok": False,
        "send_gate_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _manual_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_final_send_confirmed") is True
        or ctx.get("operator_confirmed_for_final_send") is True
    )


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _request_preview(adapter_preview_result: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(adapter_preview_result.get("send_order_request_preview"))


def _identity_mismatch(
    record: dict[str, Any],
    request_preview: dict[str, Any],
    field: str,
) -> str | None:
    record_value = _clean_text(record.get(field))
    preview_value = _clean_text(request_preview.get(field))
    if not record_value or not preview_value:
        return f"{field} is required"
    if record_value != preview_value:
        return f"record.{field} does not match send_order_request_preview.{field}"
    return None


def evaluate_final_send_gate(
    adapter_preview_result: Any,
    order_queued_record: Any,
    current_guard: Any = None,
    queue_snapshot: Any = None,
    current_queue_snapshot: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """Evaluate the final manual gate without sending or writing anything."""
    if not isinstance(adapter_preview_result, dict):
        return _blocked("adapter_preview", "adapter_preview_result must be a dict")

    if adapter_preview_result.get("adapter_preview_ok") is not True:
        return _blocked("adapter_preview", "adapter_preview_result.adapter_preview_ok is not true")

    if adapter_preview_result.get("next_stage") != ADAPTER_NEXT_STAGE_REQUIRED:
        return _blocked(
            "adapter_preview",
            "adapter_preview_result.next_stage is not FINAL_SEND_GATE_REQUIRED",
        )

    if adapter_preview_result.get("no_send") is not True:
        return _blocked("adapter_preview", "adapter_preview_result.no_send is not true")

    if adapter_preview_result.get("send_order_called") is not False:
        return _blocked("adapter_preview", "adapter_preview_result.send_order_called is not false")

    blocked_reasons = adapter_preview_result.get("blocked_reasons")
    if isinstance(blocked_reasons, list) and blocked_reasons:
        return _blocked("adapter_preview", "adapter_preview_result.blocked_reasons is not empty")
    if blocked_reasons not in (None, []) and not isinstance(blocked_reasons, list):
        return _blocked("adapter_preview", "adapter_preview_result.blocked_reasons must be a list")

    request_preview = _request_preview(adapter_preview_result)
    if not request_preview:
        return _blocked("adapter_preview", "send_order_request_preview is required")

    if not isinstance(order_queued_record, dict):
        return _blocked("record", "order_queued_record must be a dict")

    if order_queued_record.get("status") != "ORDER_QUEUED":
        return _blocked("record", "order_queued_record.status is not ORDER_QUEUED")

    if order_queued_record.get("send_order_called") is not False:
        return _blocked("record", "order_queued_record.send_order_called is not false")

    if order_queued_record.get("execution_enabled") is not False:
        return _blocked("record", "order_queued_record.execution_enabled is not false")

    for field in ("order_id", "request_hash", "lock_id", "execution_id", "source_signal_id"):
        reason = _identity_mismatch(order_queued_record, request_preview, field)
        if reason:
            return _blocked("record_consistency", reason)

    guard = _as_dict(current_guard)
    if not guard:
        return _blocked("guard", "current_guard is required")

    if not _truthy(guard.get("real_trade_enabled")):
        return _blocked("guard", "current_guard.real_trade_enabled is not true")

    if not _truthy(guard.get("kiwoom_logged_in")):
        return _blocked("guard", "current_guard.kiwoom_logged_in is not true")

    if not _truthy(guard.get("account_selected")):
        return _blocked("guard", "current_guard.account_selected is not true")

    guard_account_no = _clean_text(guard.get("account_no"))
    if not guard_account_no:
        return _blocked("guard", "current_guard.account_no is required")

    if not _truthy(guard.get("operator_confirmed")):
        return _blocked("guard", "current_guard.operator_confirmed is not true")

    preview_account_no = _clean_text(request_preview.get("account_no"))
    if guard_account_no != preview_account_no:
        return _blocked("guard", "current_guard.account_no does not match send_order_request_preview.account_no")

    if not _manual_confirmed(context):
        return _blocked("operator_confirmation", "manual final send confirmation is required")

    if isinstance(queue_snapshot, dict) and isinstance(current_queue_snapshot, dict):
        before_sha = _snapshot_sha256(queue_snapshot)
        current_sha = _snapshot_sha256(current_queue_snapshot)
        if not before_sha or not current_sha:
            return _blocked("stale_queue", "queue snapshot sha256 is required")
        if before_sha != current_sha:
            return _blocked(
                "stale_queue",
                "queue file changed after send order preview; rerun review and adapter preview",
            )

    order_id = _clean_text(request_preview.get("order_id"))
    return {
        "final_send_gate_ok": True,
        "send_gate_stage": "final_send_gate_approved",
        "next_stage": NEXT_STAGE_SEND_ORDER_ENTRYPOINT_REQUIRED,
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "order_id": order_id,
        "order_queued_id": _clean_text(order_queued_record.get("id")),
        "request_hash": _clean_text(request_preview.get("request_hash")),
        "lock_id": _clean_text(request_preview.get("lock_id")),
        "execution_id": _clean_text(request_preview.get("execution_id")),
        "blocked_reasons": [],
        "warnings": [],
    }
