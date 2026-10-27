"""Isolated, pre-request _AL M1 window intent and immutable callback evidence.

opt10080 does not take a start/end time. A matching receipt proves only that its
rows fit the frozen intent; it does not prove broker coverage of absent minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

from indicator_follow_validation_v5_broker_al_m1 import classify_broker_al_m1
from indicator_follow_validation_v5_sor_source_gate import FrozenSession


_KST = timezone(timedelta(hours=9))
_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class BrokerAlM1RequestPlan:
    connection_epoch: int
    login_session_id: str
    stock_code: str
    window_start: datetime
    window_end: datetime
    frozen_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerAlM1RequestEvidence:
    plan: BrokerAlM1RequestPlan
    status: str
    reason: str
    started_json: bytes
    receipt_json: bytes


def _now() -> datetime:
    return datetime.now(_KST)


def _kst_minute(value: object) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == _KST.utcoffset(None)
            and not value.second and not value.microsecond)


def _valid_plan(plan: BrokerAlM1RequestPlan) -> bool:
    start, end, frozen = plan.window_start, plan.window_end, plan.frozen_at
    return bool(
        type(plan.connection_epoch) is int and plan.connection_epoch > 0
        and isinstance(plan.login_session_id, str) and plan.login_session_id
        and isinstance(plan.stock_code, str) and len(plan.stock_code) == 6
        and plan.stock_code.isascii() and plan.stock_code.isdigit()
        and _kst_minute(start) and _kst_minute(end)
        and isinstance(frozen, datetime) and frozen.tzinfo is not None
        and frozen.utcoffset() == _KST.utcoffset(None)
        and start.date() == end.date() and start < end
        and start.hour >= 8
        and end.time() <= datetime.min.time().replace(hour=20)
        and end <= frozen.replace(second=0, microsecond=0)
        and (end - start) / _MINUTE <= 1440
    )


def freeze_broker_al_m1_request_plan(
    connection_epoch: int, login_session_id: str, stock_code: str,
    window_start: datetime, window_end: datetime,
) -> BrokerAlM1RequestPlan:
    """Record one explicit completed window before requesting any broker rows."""
    plan = BrokerAlM1RequestPlan(
        connection_epoch, login_session_id, stock_code,
        window_start, window_end, _now(),
    )
    if not _valid_plan(plan):
        raise ValueError("invalid or uncompleted pre-request _AL M1 window")
    return plan


def capture_broker_al_m1_request_evidence(
    plan: BrokerAlM1RequestPlan, started: object, receipt: object,
) -> BrokerAlM1RequestEvidence:
    """Snapshot both envelopes; never promote a row match to coverage proof."""
    try:
        started_json = json.dumps(started, ensure_ascii=False, sort_keys=True,
                                  allow_nan=False).encode("utf-8")
        receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                                  allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        started_json = receipt_json = b""

    def result(status: str, reason: str) -> BrokerAlM1RequestEvidence:
        return BrokerAlM1RequestEvidence(plan, status, reason, started_json, receipt_json)

    if not isinstance(plan, BrokerAlM1RequestPlan) or not _valid_plan(plan):
        return result("REJECTED", "invalid pre-request plan")
    if not started_json or not receipt_json or not isinstance(started, dict):
        return result("REJECTED", "request or receipt could not be captured")
    try:
        as_of = datetime.fromisoformat(started["request_as_of"])
        started_at = datetime.fromisoformat(started["started_at"])
    except (KeyError, TypeError, ValueError):
        return result("REJECTED", "request timing metadata missing or invalid")
    if (as_of.tzinfo is None or as_of.utcoffset() != _KST.utcoffset(None)
            or started_at.tzinfo is None or started_at.utcoffset() != _KST.utcoffset(None)
            or not plan.frozen_at <= as_of <= started_at):
        return result("REJECTED", "request predates frozen plan")
    session = FrozenSession(
        plan.connection_epoch, plan.login_session_id, plan.stock_code,
        plan.window_start, plan.window_end, as_of,
    )
    classified = classify_broker_al_m1(started, receipt, session)
    if classified.classification != "BROKER_PROVIDED_AL_CONFIRMED_M1":
        return result("REJECTED", classified.reason)
    return result("RECEIPT_ROWS_MATCH_PLAN", "broker window targeting and absent-minute coverage unproved")
