# -*- coding: utf-8 -*-
"""Adapt SendOrder record review into a preview-only Chejan entry contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "SEND_ORDER_RECORD_REVIEW_TO_CHEJAN_ENTRY_ADAPTER"
STATUS_READY = "CHEJAN_ENTRY_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

READY_LOOKUP_STATUSES = {"QUEUE_RECORD_LOOKUP_OK", "READY", "OK"}
BLOCKED_LOOKUP_STATUSES = {"BLOCKED", "QUEUE_RECORD_LOOKUP_BLOCKED"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    chejan_entry_contract: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "chejan_entry_contract": deepcopy(chejan_entry_contract) if isinstance(chejan_entry_contract, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "preview_only": True,
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
    }


def _lookup_record(lookup: dict[str, Any]) -> dict[str, Any]:
    for key in ("order_queued_record", "queue_record", "record"):
        value = lookup.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _identity_from_lookup(lookup: dict[str, Any], record: dict[str, Any]) -> dict[str, str]:
    identity = _as_dict(lookup.get("identity"))
    return {
        "order_id": _text(identity.get("order_id") or lookup.get("order_id") or record.get("order_id")),
        "dispatch_id": _text(identity.get("dispatch_id") or lookup.get("dispatch_id") or record.get("dispatch_id")),
        "source_signal_id": _text(
            identity.get("source_signal_id") or lookup.get("source_signal_id") or record.get("source_signal_id")
        ),
        "order_queued_id": _text(
            identity.get("order_queued_id") or lookup.get("order_queued_id") or record.get("id")
        ),
        "request_hash": _text(identity.get("request_hash") or lookup.get("request_hash") or record.get("request_hash")),
        "lock_id": _text(identity.get("lock_id") or lookup.get("lock_id") or record.get("lock_id")),
        "execution_id": _text(identity.get("execution_id") or lookup.get("execution_id") or record.get("execution_id")),
    }


def _review_identity(review: dict[str, Any]) -> dict[str, str]:
    return {
        "record_id": _text(review.get("record_id")),
        "order_id": _text(review.get("order_id")),
        "dispatch_id": _text(review.get("dispatch_id")),
        "source_order_id": _text(review.get("source_order_id")),
        "source_signal_id": _text(review.get("source_signal_id")),
        "code": _text(review.get("code")),
        "side": _text(review.get("side")),
    }


def _validate_context(context: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ctx = _as_dict(context)
    if not ctx:
        return ctx, _result(status=STATUS_INVALID, issues=["chejan_entry_context must be a non-empty dict"])
    if ctx.get("chejan_entry_enabled") is not True:
        return ctx, _result(status=STATUS_BLOCKED, issues=["chejan_entry_context.chejan_entry_enabled is not true"])
    return ctx, None


def build_chejan_entry_contract_from_send_order_record_review(
    recorder_review_result: Any,
    queue_record_lookup_preview: Any,
    chejan_entry_context: Any,
) -> dict[str, Any]:
    """Build a Chejan entry candidate contract without calling Chejan components."""
    review_result = _as_dict(recorder_review_result)
    lookup = _as_dict(queue_record_lookup_preview)
    context, context_blocked = _validate_context(chejan_entry_context)

    if not review_result:
        return _result(status=STATUS_INVALID, issues=["recorder_review_result must be a dict"])
    review_status = _text(review_result.get("status")).upper()
    warnings = list(review_result.get("warnings") or [])

    if review_status == "RECORD_REVIEW_BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["recorder_review_result.status is RECORD_REVIEW_BLOCKED"] + list(review_result.get("issues") or []),
            warnings=warnings,
        )
    if review_status in {"INVALID", "ERROR"}:
        return _result(
            status=STATUS_INVALID,
            issues=[f"recorder_review_result.status is {review_status}"] + list(review_result.get("issues") or []),
            warnings=warnings,
        )
    if review_status != "RECORD_REVIEW_OK":
        return _result(status=STATUS_INVALID, issues=["recorder_review_result.status is not supported"], warnings=warnings)
    if review_result.get("record_verified") is not True:
        return _result(status=STATUS_INVALID, issues=["recorder_review_result.record_verified is not true"], warnings=warnings)

    if context_blocked is not None:
        return context_blocked

    review = _as_dict(review_result.get("review"))
    if not review:
        return _result(status=STATUS_INVALID, issues=["recorder_review_result.review is required"], warnings=warnings)

    if not lookup:
        return _result(status=STATUS_INVALID, issues=["queue_record_lookup_preview must be a dict"], warnings=warnings)
    lookup_status = _text(lookup.get("status")).upper()
    if lookup_status in BLOCKED_LOOKUP_STATUSES or lookup.get("lookup_ok") is False:
        return _result(
            status=STATUS_BLOCKED,
            issues=["queue_record_lookup_preview is blocked"] + list(lookup.get("issues") or []),
            warnings=warnings + list(lookup.get("warnings") or []),
        )
    if lookup_status not in READY_LOOKUP_STATUSES and lookup.get("lookup_ok") is not True:
        return _result(status=STATUS_INVALID, issues=["queue_record_lookup_preview status is not ready"], warnings=warnings)

    order_record = _lookup_record(lookup)
    if not order_record:
        return _result(status=STATUS_INVALID, issues=["queue lookup order_queued_record is required"], warnings=warnings)
    if order_record.get("status") != "ORDER_QUEUED":
        return _result(status=STATUS_INVALID, issues=["queue record status is not ORDER_QUEUED"], warnings=warnings)

    review_identity = _review_identity(review)
    lookup_identity = _identity_from_lookup(lookup, order_record)
    for field in ("order_id", "dispatch_id", "source_signal_id"):
        if not review_identity[field]:
            return _result(status=STATUS_INVALID, issues=[f"recorder review {field} is required"], warnings=warnings)
        if not lookup_identity[field]:
            return _result(status=STATUS_INVALID, issues=[f"queue lookup {field} is required"], warnings=warnings)
        if review_identity[field] != lookup_identity[field]:
            return _result(status=STATUS_INVALID, issues=[f"{field} mismatch"], warnings=warnings)

    contract = {
        "contract_type": "CHEJAN_ENTRY_CONTRACT",
        "source": ADAPTER_TYPE,
        "next_stage": "CHEJAN_ENTRY_OPEN_POLICY_REQUIRED",
        "record_review": deepcopy(review),
        "queue_record_lookup_preview": deepcopy(lookup),
        "order_queued_record": deepcopy(order_record),
        "identity": {
            **lookup_identity,
            "record_id": review_identity["record_id"],
            "source_order_id": review_identity["source_order_id"],
            "code": review_identity["code"],
            "side": review_identity["side"],
        },
        "chejan_entry_context": deepcopy(context),
        "chejan_live_connected": False,
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
    }

    missing = [
        field
        for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id")
        if not _text(contract["identity"].get(field))
    ]
    if missing:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract missing fields: " + ", ".join(missing)], warnings=warnings)

    return _result(
        status=STATUS_READY,
        chejan_entry_contract=contract,
        issues=[],
        warnings=warnings + list(lookup.get("warnings") or []),
    )
