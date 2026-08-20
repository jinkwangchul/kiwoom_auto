# -*- coding: utf-8 -*-
"""Read-only Buffer > Routine > Stock response admission arbitration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from buffer_response_ownership_service import BufferResponseOwnershipService
from routine_limit_response_ownership_service import (
    RoutineLimitResponseOwnershipService,
    STATUS_OWNED,
)


PROJECT_ROOT = Path(__file__).resolve().parent
BUFFER_CLEAR = "BUFFER_CLEAR"
BUFFER_OWNS = "BUFFER_OWNS"
BUFFER_UNCERTAIN = "BUFFER_UNCERTAIN"
ROUTINE_CLEAR = "ROUTINE_CLEAR"
ROUTINE_OWNS = "ROUTINE_OWNS"
ROUTINE_UNCERTAIN = "ROUTINE_UNCERTAIN"
STAGE_ROUTINE = "ROUTINE"
STAGE_STOCK = "STOCK"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _identity(account_no: object, trading_day: object) -> tuple[str, str]:
    account = _text(account_no)
    if not account:
        raise ValueError("account_no is required")
    try:
        day = date.fromisoformat(_text(trading_day)).isoformat()
    except ValueError as exc:
        raise ValueError("trading_day must be YYYY-MM-DD") from exc
    return account, day


def _result_identity_matches(result: Mapping[str, object], account: str, day: str) -> bool:
    result_account = _text(result.get("account_no"))
    result_day = _text(result.get("trading_day"))
    return (not result_account or result_account == account) and (not result_day or result_day == day)


def _buffer_status(
    result: object,
    *,
    account: str,
    day: str,
    ownership: BufferResponseOwnershipService,
) -> tuple[str, tuple[str, ...], str]:
    active = ownership.active_owned_stock_codes(account_no=account, trading_day=day)
    if active.get("ok") is not True:
        return BUFFER_UNCERTAIN, (), "BUFFER_OWNERSHIP_UNAVAILABLE"
    codes = tuple(str(code) for code in active.get("stock_codes", ()) if _text(code))
    if codes:
        return BUFFER_OWNS, codes, "BUFFER_ACTIVE_OWNERSHIP"
    if not isinstance(result, Mapping) or not _result_identity_matches(result, account, day):
        return BUFFER_UNCERTAIN, (), "BUFFER_RESULT_MALFORMED_OR_IDENTITY_MISMATCH"
    required = (
        "stable", "ingress_committed", "ownership_claimed", "ownership_existing",
        "event_created", "policy_projected",
    )
    if any(key not in result or not isinstance(result.get(key), bool) for key in required):
        return BUFFER_UNCERTAIN, (), "BUFFER_RESULT_MALFORMED"
    if result.get("ownership_claimed") is True or result.get("ownership_existing") is True:
        return BUFFER_OWNS, (), "BUFFER_CURRENT_CYCLE_OWNERSHIP"
    if result.get("event_created") is True and result.get("policy_projected") is not True:
        return BUFFER_UNCERTAIN, (), "BUFFER_POLICY_UNCERTAIN"
    if result.get("stable") is not True or result.get("ingress_committed") is not True:
        return BUFFER_UNCERTAIN, (), "BUFFER_RESULT_NOT_SETTLED"
    return BUFFER_CLEAR, (), ""


def _routine_active_ids(
    ownership: RoutineLimitResponseOwnershipService,
    *,
    account: str,
    day: str,
) -> tuple[bool, tuple[str, ...], str]:
    read = ownership.read_snapshot()
    snapshot = read.get("snapshot")
    if read.get("ok") is not True or not isinstance(snapshot, Mapping):
        return False, (), _text(read.get("reason")) or "ROUTINE_OWNERSHIP_UNAVAILABLE"
    events = snapshot.get("events")
    if not isinstance(events, Mapping):
        return False, (), "ROUTINE_OWNERSHIP_EVENTS_UNAVAILABLE"
    ids = tuple(
        sorted(
            {
                _text(event.get("routine_instance_id"))
                for event in events.values()
                if isinstance(event, Mapping)
                and event.get("account_no") == account
                and event.get("trading_day") == day
                and event.get("status") == STATUS_OWNED
                and _text(event.get("routine_instance_id"))
            }
        )
    )
    return True, ids, ""


def _routine_status(
    result: object,
    *,
    account: str,
    day: str,
    ownership: RoutineLimitResponseOwnershipService,
) -> tuple[str, tuple[str, ...], str]:
    available, routine_ids, reason = _routine_active_ids(
        ownership, account=account, day=day
    )
    if not available:
        return ROUTINE_UNCERTAIN, (), reason
    if routine_ids:
        return ROUTINE_OWNS, routine_ids, "ROUTINE_ACTIVE_OWNERSHIP"
    if not isinstance(result, Mapping) or not _result_identity_matches(result, account, day):
        return ROUTINE_UNCERTAIN, (), "ROUTINE_RESULT_MALFORMED_OR_IDENTITY_MISMATCH"
    required = ("evaluated", "settled", "owns_response")
    if any(key not in result or not isinstance(result.get(key), bool) for key in required):
        return ROUTINE_UNCERTAIN, (), "ROUTINE_RESULT_MALFORMED"
    if result.get("owns_response") is True:
        return ROUTINE_OWNS, (), "ROUTINE_CURRENT_CYCLE_OWNERSHIP"
    if result.get("settled") is not True:
        return ROUTINE_UNCERTAIN, (), "ROUTINE_RESULT_NOT_SETTLED"
    return ROUTINE_CLEAR, (), ""


def arbitrate_limit_response_priority(
    *,
    account_no: object,
    trading_day: object,
    buffer_result: object,
    stage: object,
    routine_result: object = None,
    project_root: str | Path = PROJECT_ROOT,
    buffer_ownership: BufferResponseOwnershipService | None = None,
    routine_ownership: RoutineLimitResponseOwnershipService | None = None,
) -> dict[str, Any]:
    """Decide admission for one lower response layer without mutation."""
    requested_stage = _text(stage).upper()
    base = {
        "admitted": False,
        "stage": requested_stage,
        "account_no": _text(account_no),
        "trading_day": _text(trading_day),
        "buffer_status": BUFFER_UNCERTAIN,
        "routine_status": ROUTINE_UNCERTAIN if requested_stage == STAGE_STOCK else "NOT_EVALUATED",
        "active_buffer_stock_codes": (),
        "active_routine_instance_ids": (),
        "reason": "",
        "runtime_write": False,
    }
    if requested_stage not in {STAGE_ROUTINE, STAGE_STOCK}:
        return {**base, "reason": "PRIORITY_STAGE_INVALID"}
    try:
        account, day = _identity(account_no, trading_day)
    except ValueError as exc:
        return {**base, "reason": str(exc)}
    root = Path(project_root)
    buffer_service = buffer_ownership or BufferResponseOwnershipService(
        root / "runtime" / "buffer_response_ownership.json"
    )
    routine_service = routine_ownership or RoutineLimitResponseOwnershipService(
        root / "runtime" / "routine_limit_response_ownership.json"
    )
    buffer_status, buffer_codes, buffer_reason = _buffer_status(
        buffer_result, account=account, day=day, ownership=buffer_service
    )
    projected = {
        **base,
        "account_no": account,
        "trading_day": day,
        "buffer_status": buffer_status,
        "active_buffer_stock_codes": buffer_codes,
    }
    if buffer_status != BUFFER_CLEAR:
        return {**projected, "reason": buffer_reason}
    if requested_stage == STAGE_ROUTINE:
        return {**projected, "admitted": True, "reason": ""}
    routine_status, routine_ids, routine_reason = _routine_status(
        routine_result, account=account, day=day, ownership=routine_service
    )
    projected.update(
        routine_status=routine_status,
        active_routine_instance_ids=routine_ids,
    )
    if routine_status != ROUTINE_CLEAR:
        return {**projected, "reason": routine_reason}
    return {**projected, "admitted": True, "reason": ""}


__all__ = [
    "BUFFER_CLEAR", "BUFFER_OWNS", "BUFFER_UNCERTAIN", "ROUTINE_CLEAR",
    "ROUTINE_OWNS", "ROUTINE_UNCERTAIN", "STAGE_ROUTINE", "STAGE_STOCK",
    "arbitrate_limit_response_priority",
]
