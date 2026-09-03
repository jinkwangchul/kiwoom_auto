# -*- coding: utf-8 -*-
"""Read-only Main/UI projections for current Mock Validation sessions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from mock_validation_contract import clean_text, payload_hash
from mock_validation_repository import MockValidationRepository


_STATE_SORT = {
    "RUNNING": 0,
    "CLOSING": 0,
    "WAITING": 1,
    "REVIEW_STOPPED": 2,
    "ENDED": 3,
}


def mock_current_stock_codes(repository: MockValidationRepository) -> frozenset[str]:
    return frozenset(repository.current_session_ids())


def mock_badge_count(repository: MockValidationRepository) -> int:
    return len(mock_current_stock_codes(repository))


def mock_operation_start_exclusion_reason(owner: Any, target: Any) -> str | None:
    """Return the read-only Production-start exclusion for one stock target."""

    try:
        stock_code = clean_text(target[1])
    except (IndexError, TypeError):
        return "MOCK_MEMBERSHIP_CHECK_FAILED"
    if not stock_code:
        return "MOCK_MEMBERSHIP_CHECK_FAILED"

    host = getattr(owner, "mock_validation_host", None) if owner is not None else None
    try:
        if host is not None:
            current_codes = host.current_stock_codes()
            return "MOCK_VALIDATION_ACTIVE" if stock_code in current_codes else None
        repository = MockValidationRepository()
        return (
            "MOCK_VALIDATION_ACTIVE"
            if repository.current_session_id(stock_code)
            else None
        )
    except Exception:
        return "MOCK_MEMBERSHIP_CHECK_FAILED"


def mock_session_projection(document: dict[str, Any]) -> dict[str, Any]:
    session = document["session"]
    reference = document.get("reference_snapshot", {})
    instances = reference.get("routine_instances", ())
    positions = document.get("positions", ())
    pnl = document.get("pnl", ())
    review = document.get("review", {})
    lifecycle = document.get("mock_operation_lifecycle")
    operation = lifecycle.get("current") if isinstance(lifecycle, dict) else None
    state = clean_text(session.get("state"))
    instance_names = tuple(
        clean_text(item.get("routine_instance_name"))
        or clean_text(item.get("routine_instance_id"))
        for item in instances
        if isinstance(item, dict)
    )
    stock_reference = reference.get("stock_identity_reference")
    stock_path = (
        clean_text(stock_reference.get("stock_path"))
        if isinstance(stock_reference, dict)
        else ""
    )
    holding_qty = sum(int(item.get("holding_qty", 0) or 0) for item in positions if isinstance(item, dict))
    available_qty = sum(int(item.get("available_qty", 0) or 0) for item in positions if isinstance(item, dict))
    net_pnl = sum(float(item.get("net_pnl", 0) or 0) for item in pnl if isinstance(item, dict))
    operation_state = clean_text(operation.get("state")) if isinstance(operation, dict) else ""
    return {
        "validation_session_id": session["validation_session_id"],
        "stock_code": session["stock_code"],
        "stock_name": session["stock_name"],
        "stock_path": stock_path,
        "state": state,
        "state_label": {
            "WAITING": "운영대기",
            "RUNNING": "운영중",
            "CLOSING": "마감중",
            "REVIEW_STOPPED": "검토정지",
            "ENDED": "종료",
        }.get(state, state or "-"),
        "operation_state": operation_state,
        "routine_instance_ids": tuple(
            clean_text(item.get("routine_instance_id"))
            for item in instances
            if isinstance(item, dict)
        ),
        "routine_instance_names": instance_names,
        "routine_summary": ", ".join(instance_names),
        "holding_qty": holding_qty,
        "available_qty": available_qty,
        "net_pnl": int(net_pnl) if net_pnl.is_integer() else net_pnl,
        "review_required": bool(review.get("review_required") is True),
        "review_reason": clean_text(review.get("review_reason")),
        "review_culprit": clean_text(review.get("source_routine_instance_id")),
        "review_occurred_at": clean_text(review.get("occurred_at")),
        "mock_tax_enabled": bool(session.get("mock_tax_enabled") is True),
        "mock_tax_rate": session.get("mock_tax_rate", 0.002),
        "revision": int(document.get("revision", 0) or 0),
    }


def current_mock_projections(repository: MockValidationRepository) -> tuple[dict[str, Any], ...]:
    rows = [mock_session_projection(document) for document in repository.current_sessions()]
    rows.sort(key=lambda row: (_STATE_SORT.get(row["state"], 9), row["stock_code"]))
    return tuple(rows)


def current_mock_projection_hash(repository: MockValidationRepository) -> str:
    return payload_hash(current_mock_projections(repository))


class MockEventReaderAdapter:
    """Project the isolated Mock journal into EventRecordPrototypeWindow's schema."""

    def __init__(self, repository: MockValidationRepository, session_id: str) -> None:
        self.repository = repository
        self.session_id = clean_text(session_id)

    def read_events(
        self,
        *,
        start_at=None,
        end_at=None,
        category=None,
        severity=None,
        query="",
        descending=True,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        needle = clean_text(query).casefold()
        for event in self.repository.read_events(self.session_id):
            occurred = clean_text(event.get("timestamp"))
            try:
                occurred_dt = datetime.fromisoformat(occurred)
            except ValueError:
                occurred_dt = None
            if start_at is not None and occurred_dt is not None and occurred_dt < start_at:
                continue
            if end_at is not None and occurred_dt is not None and occurred_dt > end_at:
                continue
            event_type = clean_text(event.get("event_type"))
            reason = clean_text(event.get("reason_code"))
            is_error = bool("ERROR" in event_type or "FAILED" in event_type or "REVIEW" in event_type)
            row = {
                "event_id": event.get("event_id"),
                "occurred_at": occurred,
                "category": "MOCK",
                "severity": "ERROR" if is_error else "INFO",
                "event_type": event_type,
                "result": "FAILED" if is_error else "COMPLETED",
                "reason_code": reason,
                "source": "mock_validation",
                "summary": reason or event_type,
                "target_type": "MOCK_SESSION",
                "target_id": event.get("validation_session_id"),
                "target_name": event.get("stock_code"),
                "stock_code": event.get("stock_code"),
                "routine": event.get("routine_instance_id"),
                "details": deepcopy(event.get("payload") or {}),
            }
            if category not in (None, "", "ALL") and row["category"] != category:
                continue
            if severity not in (None, "", "ALL") and row["severity"] != severity:
                continue
            if needle and needle not in str(row).casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda item: clean_text(item.get("occurred_at")), reverse=bool(descending))
        return {"events": rows, "errors": [], "diagnostics": [], "count": len(rows)}


__all__ = [
    "MockEventReaderAdapter",
    "current_mock_projection_hash",
    "current_mock_projections",
    "mock_badge_count",
    "mock_current_stock_codes",
    "mock_operation_start_exclusion_reason",
    "mock_session_projection",
]
