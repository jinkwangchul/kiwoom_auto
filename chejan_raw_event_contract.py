# -*- coding: utf-8 -*-
"""Build a raw Chejan event contract without connecting live handlers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CONTRACT_TYPE = "CHEJAN_RAW_EVENT_CONTRACT"
STATUS_READY = "CHEJAN_EVENT_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    chejan_event_contract: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "chejan_event_contract": deepcopy(chejan_event_contract) if isinstance(chejan_event_contract, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
    }


def _fid(raw_event: dict[str, Any], key: str) -> Any:
    fid_values = _as_dict(raw_event.get("fid_values"))
    return fid_values.get(key)


def _event_type(raw_event: dict[str, Any]) -> str:
    value = _text(raw_event.get("event_type"))
    if value:
        return value
    status = _text(raw_event.get("order_status") or _fid(raw_event, "913"))
    filled = _text(raw_event.get("filled_quantity") or _fid(raw_event, "911"))
    remaining = _text(raw_event.get("remaining_quantity") or _fid(raw_event, "902"))
    if status or filled or remaining:
        return "RAW_CHEJAN_EVENT"
    return ""


def _raw_identity(raw_event: dict[str, Any]) -> dict[str, str]:
    return {
        "order_id": _text(raw_event.get("order_id")),
        "dispatch_id": _text(raw_event.get("dispatch_id")),
        "source_signal_id": _text(raw_event.get("source_signal_id")),
        "broker_order_no": _text(raw_event.get("broker_order_no") or _fid(raw_event, "9203")),
        "account_no": _text(raw_event.get("account_no") or _fid(raw_event, "9201")),
        "code": _text(raw_event.get("code") or _fid(raw_event, "9001")).lstrip("A"),
    }


def _context_ok(context: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ctx = _as_dict(context)
    if not ctx:
        return ctx, _result(status=STATUS_INVALID, issues=["chejan_event_context must be a non-empty dict"])
    if ctx.get("chejan_event_enabled") is not True:
        return ctx, _result(status=STATUS_BLOCKED, issues=["chejan_event_context.chejan_event_enabled is not true"])
    if not _text(ctx.get("received_at")) and not _text(ctx.get("event_timestamp")):
        return ctx, _result(status=STATUS_INVALID, issues=["chejan event timestamp is required"])
    return ctx, None


def build_chejan_raw_event_contract(
    chejan_entry_policy_result: Any,
    raw_chejan_event: Any,
    chejan_event_context: Any,
) -> dict[str, Any]:
    """Convert a raw Chejan event into a validation contract only."""
    policy_result = _as_dict(chejan_entry_policy_result)
    raw_event = _as_dict(raw_chejan_event)
    context, context_blocked = _context_ok(chejan_event_context)

    if not policy_result:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_policy_result must be a dict"])

    policy_status = _text(policy_result.get("status")).upper()
    warnings = list(policy_result.get("warnings") or [])
    if policy_status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["chejan_entry_policy_result.status is BLOCKED"] + list(policy_result.get("issues") or []),
            warnings=warnings,
        )
    if policy_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["chejan_entry_policy_result.status is INVALID"] + list(policy_result.get("issues") or []),
            warnings=warnings,
        )
    if policy_status != "CHEJAN_ENTRY_OPEN":
        return _result(status=STATUS_INVALID, issues=["chejan_entry_policy_result.status is not supported"], warnings=warnings)

    if context_blocked is not None:
        return context_blocked
    if not raw_event:
        return _result(status=STATUS_BLOCKED, issues=["raw_chejan_event is required"], warnings=warnings)

    event_type = _event_type(raw_event)
    if not event_type:
        return _result(status=STATUS_INVALID, issues=["raw_chejan_event.event_type or order status fields are required"], warnings=warnings)
    gubun = _text(raw_event.get("gubun"))
    if not gubun:
        return _result(status=STATUS_INVALID, issues=["raw_chejan_event.gubun is required"], warnings=warnings)

    policy = _as_dict(policy_result.get("policy"))
    policy_identity = _as_dict(policy.get("identity"))
    if not policy_identity:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_policy_result.policy.identity is required"], warnings=warnings)

    raw_identity = _raw_identity(raw_event)
    connected_identity = deepcopy(policy_identity)
    for field in ("order_id", "dispatch_id", "source_signal_id"):
        raw_value = _text(raw_identity.get(field))
        policy_value = _text(policy_identity.get(field))
        if raw_value and policy_value and raw_value != policy_value:
            return _result(status=STATUS_INVALID, issues=[f"{field} mismatch"], warnings=warnings)
        if not policy_value:
            return _result(status=STATUS_INVALID, issues=[f"policy identity {field} is required"], warnings=warnings)
        connected_identity[field] = policy_value

    if not (_text(raw_identity.get("order_id")) or _text(raw_identity.get("broker_order_no"))):
        return _result(status=STATUS_INVALID, issues=["order_id or broker_order_no is required"], warnings=warnings)

    timestamp = _text(raw_event.get("received_at") or context.get("received_at") or context.get("event_timestamp"))
    if not timestamp:
        return _result(status=STATUS_INVALID, issues=["event timestamp is required"], warnings=warnings)

    contract = {
        "contract_type": CONTRACT_TYPE,
        "status": "RAW_CHEJAN_EVENT_RECEIVED",
        "event_type": event_type,
        "gubun": gubun,
        "received_at": timestamp,
        "source": _text(raw_event.get("source")) or "kiwoom_chejan",
        "raw_chejan_event": deepcopy(raw_event),
        "entry_policy": deepcopy(policy),
        "identity": {
            **connected_identity,
            "broker_order_no": raw_identity["broker_order_no"],
            "account_no": raw_identity["account_no"],
            "code": raw_identity["code"],
        },
        "chejan_event_context": deepcopy(context),
        "chejan_live_connected": False,
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
        "next_stage": "CHEJAN_EVENT_NORMALIZE_REQUIRED",
    }
    return _result(status=STATUS_READY, chejan_event_contract=contract, issues=[], warnings=warnings)
