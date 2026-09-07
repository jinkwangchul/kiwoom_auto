# -*- coding: utf-8 -*-
"""Read-only Main/UI projections for current Mock Validation sessions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from gui_auto_trade_policy import auto_trade_operation_display
from mock_validation_contract import (
    INSTANCE_ERROR,
    INSTANCE_VALIDATION_STOPPED,
    MockValidationError,
    clean_text,
    instance_initial_buy_adjustment,
    instance_effective_settings,
    normalized_stock_code,
    payload_hash,
)
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
    """Mock membership is an overlay and never excludes Production start."""
    _ = owner, target
    return None


def _record_by_instance(records: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(records, (list, tuple)):
        return {}
    return {
        clean_text(item.get("routine_instance_id")): item
        for item in records
        if isinstance(item, dict) and clean_text(item.get("routine_instance_id"))
    }


def _instance_period(rules: Any) -> Any:
    if not isinstance(rules, dict):
        return ""
    bar = rules.get("bar")
    if isinstance(bar, dict) and bar.get("bar_minutes") not in (None, ""):
        return bar.get("bar_minutes")
    for key in ("bar_minutes", "timeframe_minutes", "minute_interval"):
        if rules.get(key) not in (None, ""):
            return rules.get(key)
    return ""


def _mock_operation_display(
    effective_settings: dict[str, Any],
) -> tuple[str, str, str, list[str]]:
    """Borrow the official Production operation presentation without its writers."""

    schedule = effective_settings["operation_schedule"]
    manual_ats = effective_settings["manual_ats"]
    return auto_trade_operation_display(
        {
            "operation_mode": effective_settings["operation_mode"],
            "start_time": schedule["start_time"],
            "end_buy_time": schedule["end_buy_time"],
        },
        {
            "manual_ats_selection": {
                "selected_sessions": list(manual_ats["selected_sessions"]),
            }
        },
    )


def mock_instance_projection(
    document: dict[str, Any],
    routine_instance_id: str,
    *,
    current_price: int | float | None = None,
) -> dict[str, Any]:
    """Project one isolated Routine Instance without stock-level aggregation."""

    instance_id = clean_text(routine_instance_id)
    reference = document.get("reference_snapshot", {})
    references = {
        clean_text(item.get("routine_instance_id")): item
        for item in reference.get("routine_instances", ())
        if isinstance(item, dict) and clean_text(item.get("routine_instance_id"))
    }
    if instance_id not in references:
        raise KeyError(instance_id)
    instance = references[instance_id]
    display_contract = (
        reference.get("display_contract")
        if isinstance(reference.get("display_contract"), dict)
        else {}
    )
    mutable_settings = document.get("effective_settings_by_instance")
    has_mutable_settings = (
        isinstance(mutable_settings, dict)
        and isinstance(mutable_settings.get(instance_id), dict)
    )
    projected_settings = instance_effective_settings(document, instance_id)
    effective_settings = projected_settings if has_mutable_settings else None
    operation_display = (
        _mock_operation_display(projected_settings)
        if has_mutable_settings
        else None
    )
    effective_display_contract = deepcopy(display_contract)
    if effective_settings is not None:
        initial = effective_settings["initial_buy"]
        initial_mode = initial["mode"]
        initial_value = int(initial["value"])
        schedule = effective_settings["operation_schedule"]
        effective_display_contract["initial_buy"] = {
            "mode": initial_mode,
            "badge": "금액" if initial_mode == "AMOUNT" else "주수",
            "value": initial_value,
            "value_text": (
                f"{initial_value:,}원"
                if initial_mode == "AMOUNT"
                else f"{initial_value:,}주"
            ),
        }
        effective_display_contract["operation_schedule"] = {
            "display_text": operation_display[0]
        }
    execution = document.get("instance_execution", {}).get(instance_id, {})
    position = _record_by_instance(document.get("positions", ()) ).get(instance_id, {})
    pnl = _record_by_instance(document.get("pnl", ()) ).get(instance_id, {})
    cycle = document.get("cycle_state_by_instance", {}).get(instance_id, {})
    progression = document.get("progression_by_instance", {}).get(instance_id, {})
    orders = tuple(
        item
        for item in document.get("orders", ())
        if isinstance(item, dict) and clean_text(item.get("routine_instance_id")) == instance_id
    )
    live_orders = tuple(
        item for item in orders if clean_text(item.get("state")) in {"CREATED", "OPEN", "PARTIAL_FILL", "CANCEL_PENDING"}
    )
    buy_pending_qty = sum(
        int(item.get("remaining_qty", 0) or 0)
        for item in live_orders
        if clean_text(item.get("side")).upper() == "BUY"
    )
    sell_pending_qty = sum(
        int(item.get("remaining_qty", 0) or 0)
        for item in live_orders
        if clean_text(item.get("side")).upper() == "SELL"
    )
    fills = tuple(
        item
        for item in document.get("fills", ())
        if isinstance(item, dict)
        and clean_text(item.get("routine_instance_id")) == instance_id
    )
    filled_order_ids = {
        clean_text(item.get("mock_order_id"))
        for item in fills
        if clean_text(item.get("mock_order_id"))
    }
    side_by_order_id = {
        clean_text(item.get("mock_order_id")): clean_text(item.get("side")).upper()
        for item in orders
        if clean_text(item.get("mock_order_id"))
    }
    buy_trade_count = sum(
        1 for order_id in filled_order_ids if side_by_order_id.get(order_id) == "BUY"
    )
    sell_trade_count = sum(
        1 for order_id in filled_order_ids if side_by_order_id.get(order_id) == "SELL"
    )
    state = clean_text(execution.get("state")) or clean_text(document["session"].get("state"))
    is_error = state == INSTANCE_ERROR
    rules = instance.get("rules_snapshot") if isinstance(instance.get("rules_snapshot"), dict) else {}
    mark_price = pnl.get("mark_price")
    effective_current_price = current_price if current_price is not None else mark_price
    last_side = clean_text(live_orders[-1].get("side")) if live_orders else ""
    trade_value = last_side or clean_text(cycle.get("side")) or clean_text(cycle.get("status"))
    return {
        "row_kind": "mock_routine_instance",
        "validation_session_id": document["session"]["validation_session_id"],
        "stock_code": document["session"]["stock_code"],
        "stock_name": document["session"]["stock_name"],
        "routine_instance_id": instance_id,
        "routine_instance_name": clean_text(instance.get("routine_instance_name")) or instance_id,
        "routine_definition_id": clean_text(instance.get("routine_definition_id")),
        "routine_type": clean_text(instance.get("routine_type")),
        "state": state,
        "state_label": "오류" if is_error else {
            "WAITING": "운영대기",
            "RUNNING": "운영중",
            "CLOSING": "마감중",
            "ENDED": "종료",
            INSTANCE_VALIDATION_STOPPED: "검증정지",
        }.get(state, state or "-"),
        "error": is_error,
        "status_led": "red" if is_error else "normal",
        "error_code": clean_text(execution.get("error_code")),
        "error_reason": clean_text(execution.get("error_reason")),
        "error_occurred_at": clean_text(execution.get("error_occurred_at")),
        "started_at": clean_text(execution.get("started_at")),
        "period": _instance_period(rules),
        "holding_qty": int(position.get("holding_qty", 0) or 0),
        "available_qty": int(position.get("available_qty", 0) or 0),
        "average_price": position.get("average_price", 0),
        "realized_cost_basis": position.get("realized_cost_basis", 0),
        "current_price": effective_current_price,
        "gross_pnl": pnl.get("gross_pnl", 0),
        "net_pnl": pnl.get("net_pnl", 0),
        "buy_pending_qty": buy_pending_qty,
        "sell_pending_qty": sell_pending_qty,
        "buy_trade_count": buy_trade_count,
        "sell_trade_count": sell_trade_count,
        "trade": trade_value or "-",
        "live_order_count": len(live_orders),
        "cycle_state": deepcopy(cycle) if isinstance(cycle, dict) else {},
        "progression": deepcopy(progression) if isinstance(progression, dict) else {},
        "rules_snapshot": deepcopy(rules),
        "display_contract": effective_display_contract,
        "operation_display": deepcopy(operation_display),
        "display_contract_frozen": isinstance(reference.get("display_contract"), dict),
        "effective_settings": deepcopy(effective_settings),
        "effective_settings_mutable": has_mutable_settings,
        "initial_buy_adjustment": instance_initial_buy_adjustment(
            document, instance_id
        ),
    }


def mock_monitoring_tree_projection(
    document: dict[str, Any],
    *,
    current_price: int | float | None = None,
) -> dict[str, Any]:
    """Return the Mock-only Stock -> Routine Instance monitoring tree."""

    session = document["session"]
    reference = document.get("reference_snapshot", {})
    stock_reference = reference.get("stock_identity_reference")
    stock_path = (
        clean_text(stock_reference.get("stock_path"))
        if isinstance(stock_reference, dict)
        else ""
    )
    instance_ids = tuple(sorted(document.get("instance_execution", {})))
    children = tuple(
        mock_instance_projection(document, instance_id, current_price=current_price)
        for instance_id in instance_ids
    )
    profit_amount = sum(float(child.get("net_pnl", 0) or 0) for child in children)
    profit_cost_basis = sum(
        float(child.get("realized_cost_basis", 0) or 0) for child in children
    )
    profit_rate = (
        (profit_amount / profit_cost_basis) * 100.0
        if profit_cost_basis > 0
        else 0.0
    )
    return {
        "row_kind": "mock_stock",
        "validation_session_id": session["validation_session_id"],
        "stock_code": session["stock_code"],
        "stock_name": session["stock_name"],
        "stock_path": stock_path,
        "stock_identity_reference": deepcopy(reference.get("stock_identity_reference")),
        "routine_instance_count": len(children),
        "routine_instance_ids": instance_ids,
        "profit_amount": int(profit_amount) if profit_amount.is_integer() else profit_amount,
        "profit_cost_basis": (
            int(profit_cost_basis)
            if profit_cost_basis.is_integer()
            else profit_cost_basis
        ),
        "profit_rate": profit_rate,
        "children": children,
        "revision": int(document.get("revision", 0) or 0),
    }


def current_mock_monitoring_trees(
    repository: MockValidationRepository,
    *,
    current_price_by_stock: dict[str, int | float | None] | None = None,
) -> tuple[dict[str, Any], ...]:
    prices = current_price_by_stock or {}
    rows = [
        mock_monitoring_tree_projection(
            document,
            current_price=prices.get(document["session"]["stock_code"]),
        )
        for document in repository.current_sessions()
    ]
    rows.sort(key=lambda row: row["stock_code"])
    return tuple(rows)


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
        events = self.repository.read_events(self.session_id)
        if not events:
            return {"events": [], "errors": [], "diagnostics": [], "count": 0}
        document = self.repository.read_session(self.session_id)
        session = document.get("session") if isinstance(document.get("session"), dict) else {}
        stock_code = clean_text(session.get("stock_code"))
        stock_name = clean_text(session.get("stock_name"))
        reference = document.get("reference_snapshot")
        instances = reference.get("routine_instances") if isinstance(reference, dict) else []
        instance_names = {
            clean_text(item.get("routine_instance_id")): clean_text(
                item.get("routine_instance_name")
            )
            for item in instances
            if isinstance(item, dict) and clean_text(item.get("routine_instance_id"))
        }
        for event in events:
            try:
                event_stock_code = normalized_stock_code(event.get("stock_code"))
            except MockValidationError as exc:
                raise MockValidationError("MOCK_EVENT_STOCK_IDENTITY_INVALID") from exc
            if event_stock_code != stock_code:
                raise MockValidationError("MOCK_EVENT_STOCK_IDENTITY_MISMATCH")
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
            instance_id = clean_text(event.get("routine_instance_id"))
            instance_name = instance_names.get(instance_id, "")
            stock_target = " ".join(value for value in (stock_code, stock_name) if value)
            target_name = (
                f"{stock_target} / {instance_name or instance_id}"
                if instance_id
                else stock_target
            )
            row = {
                "event_id": event.get("event_id"),
                "occurred_at": occurred,
                "category": "MOCK",
                "severity": "ERROR" if is_error else "INFO",
                "event_type": event_type,
                "result": "FAILED" if is_error else "COMPLETED",
                "reason_code": reason,
                "source": "mock_validation",
                "summary": event_type,
                "target_type": "MOCK_ROUTINE_INSTANCE" if instance_id else "MOCK_SESSION",
                "target_id": instance_id or event.get("validation_session_id"),
                "target_name": target_name,
                "stock_code": event.get("stock_code"),
                "routine": instance_id,
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
    "current_mock_monitoring_trees",
    "current_mock_projection_hash",
    "current_mock_projections",
    "mock_badge_count",
    "mock_current_stock_codes",
    "mock_instance_projection",
    "mock_monitoring_tree_projection",
    "mock_operation_start_exclusion_reason",
    "mock_session_projection",
]
