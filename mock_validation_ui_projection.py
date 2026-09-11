# -*- coding: utf-8 -*-
"""Read-only Main/UI projections for current Mock Validation sessions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from gui_auto_trade_policy import auto_trade_operation_display
from gui_ats_utils import (
    auto_trade_operation_activation_phase,
    auto_trade_operation_session_phase,
)
from manual_ats_runtime import VALID_SESSION_KEYS
from mock_validation_contract import (
    INSTANCE_ERROR,
    MockValidationError,
    clean_text,
    instance_initial_buy_adjustment,
    instance_effective_settings,
    mock_instance_active_effective_settings,
    mock_instance_active_operation,
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


def _mock_instance_activation_phase(
    operation: dict[str, Any], as_of: datetime
) -> dict[str, Any]:
    snapshot = operation.get("operation_policy_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    settings = snapshot.get("mock_instance_effective_settings")
    settings = settings if isinstance(settings, dict) else {}
    schedule = settings.get("operation_schedule")
    schedule = schedule if isinstance(schedule, dict) else {}
    config = {
        "operation_mode": settings.get("operation_mode"),
        "start_time": schedule.get("start_time"),
        "end_buy_time": schedule.get("end_buy_time"),
    }
    manual_ats = settings.get("manual_ats")
    selected = list(
        manual_ats.get("selected_sessions", ())
        if isinstance(manual_ats, dict)
        else ()
    )
    state = {"manual_ats_selection": {"selected_sessions": selected}}
    sessions = snapshot.get("extra_sessions")
    sessions = sessions if isinstance(sessions, list) else []

    def ats_reader(key: str) -> dict[str, Any]:
        try:
            index = VALID_SESSION_KEYS.index(key)
        except ValueError:
            return {}
        session = sessions[index] if index < len(sessions) else None
        return deepcopy(session) if isinstance(session, dict) else {}

    phase = auto_trade_operation_session_phase(
        config,
        state,
        now_dt=as_of,
        operation_policy_reader=lambda: snapshot,
        ats_session_reader=ats_reader,
    )
    activation = auto_trade_operation_activation_phase(
        config,
        state,
        now_dt=as_of,
        session_phase=phase,
        operation_policy_reader=lambda: snapshot,
    )
    return activation


def _mock_instance_display_status(
    document: dict[str, Any],
    instance_id: str,
    *,
    state: str,
    progression_allowed: bool,
    live_orders: tuple[dict[str, Any], ...],
    progression: dict[str, Any],
    as_of: datetime,
) -> tuple[str, bool, bool, bool, dict[str, Any]]:
    lifecycle = document.get("mock_operation_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    instance_operations = lifecycle.get("instance_operations")
    instance_operations = instance_operations if isinstance(instance_operations, dict) else {}
    operation = instance_operations.get(instance_id)
    if not isinstance(operation, dict):
        current = lifecycle.get("current")
        current_instance_id = clean_text(current.get("routine_instance_id")) if isinstance(current, dict) else ""
        operation = current if isinstance(current, dict) and current_instance_id in {"", instance_id} else {}

    review = document.get("review")
    review = review if isinstance(review, dict) else {}
    review_instance_id = clean_text(review.get("source_routine_instance_id"))
    review_required = review.get("review_required") is True and review_instance_id in {"", instance_id}
    if state == INSTANCE_ERROR or operation.get("state") == "REVIEW_STOPPED" or review_required:
        return "검토종목", False, False, False, {}

    operation_state = clean_text(operation.get("state"))
    if state in {"WAITING", "ENDED", "VALIDATION_STOPPED"}:
        return "감시/대기", False, False, False, {}
    close_source = clean_text(operation.get("close_source")).upper()
    close_method = clean_text(operation.get("close_method")).upper()
    if close_source and close_method:
        liquidation_active = clean_text(operation.get("close_method")).upper() in {
            "MARKET",
            "CURRENT_PRICE",
        }
        if close_source in {"NORMAL", "AUTO"}:
            return "자동마감", True, False, liquidation_active, {}
        if close_source == "EARLY":
            return "조기마감", True, False, liquidation_active, {}
        if close_source == "IMMEDIATE":
            return "청산", True, False, liquidation_active, {}
    if state == "CLOSING" or operation_state == "CLOSING":
        return "감시/대기", False, False, False, {}

    _ = live_orders, progression
    if state == "RUNNING" and progression_allowed and operation_state == "RUNNING":
        activation = _mock_instance_activation_phase(operation, as_of)
        projection_phase = clean_text(activation.get("projection_phase")).upper()
        controls_active = projection_phase in {
            "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY",
            "ACTIVE_SESSION",
        }
        return (
            "매수/매도"
            if activation.get("actual_trading_session_active") is True
            else "감시/대기",
            True,
            controls_active,
            False,
            activation,
        )
    return "감시/대기", False, False, False, {}


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
    as_of: datetime | None = None,
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
    next_start_settings = instance_effective_settings(document, instance_id)
    active_settings = mock_instance_active_effective_settings(document, instance_id)
    projected_settings = (
        active_settings if isinstance(active_settings, dict) else next_start_settings
    )
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
    (
        display_status,
        status_cell_active,
        method_cell_active,
        liquidation_phase_active,
        activation_phase,
    ) = _mock_instance_display_status(
        document,
        instance_id,
        state=state,
        progression_allowed=execution.get("progression_allowed") is True,
        live_orders=live_orders,
        progression=progression,
        as_of=as_of or datetime.now().astimezone(),
    )
    rules = instance.get("rules_snapshot") if isinstance(instance.get("rules_snapshot"), dict) else {}
    mark_price = pnl.get("mark_price")
    effective_current_price = current_price if current_price is not None else mark_price
    last_side = clean_text(live_orders[-1].get("side")) if live_orders else ""
    trade_value = last_side or clean_text(cycle.get("side")) or clean_text(cycle.get("status"))
    liquidation = (
        effective_display_contract.get("liquidation")
        if isinstance(effective_display_contract.get("liquidation"), dict)
        else {}
    )
    operation_mode = clean_text(projected_settings.get("operation_mode")).upper()
    active_operation = mock_instance_active_operation(document, instance_id) or {}
    close_source = clean_text(active_operation.get("close_source")).upper()
    close_method = clean_text(active_operation.get("close_method")).upper()
    legitimate_close = bool(close_source and close_method)
    if operation_mode == "CONTINUOUS" and not legitimate_close:
        liquidation = {**liquidation, "display_text": "-"}
        effective_display_contract["liquidation"] = liquidation
    elif close_method in {"CARRYOVER", "LONG_HOLD"}:
        liquidation = {**liquidation, "display_text": "-"}
        effective_display_contract["liquidation"] = liquidation
    liquidation_has_policy = clean_text(liquidation.get("display_text")) not in {"", "-"}
    holding_qty = int(position.get("holding_qty", 0) or 0)
    normal_scheduled_liquidation_active = bool(
        operation_mode == "SCHEDULED"
        and state == "RUNNING"
        and execution.get("progression_allowed") is True
        and clean_text(active_operation.get("state")).upper() == "RUNNING"
        and clean_text(activation_phase.get("projection_phase")).upper()
        in {
            "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY",
            "ACTIVE_SESSION",
        }
        and activation_phase.get("ats_session_active") is not True
    )
    liquidation_phase_active = bool(
        liquidation_phase_active or normal_scheduled_liquidation_active
    )
    liquidation_cell_active = bool(
        liquidation_has_policy
        and (
            normal_scheduled_liquidation_active
            or (liquidation_phase_active and holding_qty > 0)
        )
    )
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
        "display_status": display_status,
        "status_cell_active": status_cell_active,
        "method_cell_active": method_cell_active,
        "liquidation_phase_active": liquidation_phase_active,
        "liquidation_cell_active": liquidation_cell_active,
        "liquidation_has_policy": liquidation_has_policy,
        "error": is_error,
        "status_led": "red" if is_error else "normal",
        "error_code": clean_text(execution.get("error_code")),
        "error_reason": clean_text(execution.get("error_reason")),
        "error_occurred_at": clean_text(execution.get("error_occurred_at")),
        "started_at": clean_text(execution.get("started_at")),
        "period": _instance_period(rules),
        "holding_qty": holding_qty,
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
    as_of: datetime | None = None,
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
        mock_instance_projection(
            document,
            instance_id,
            current_price=current_price,
            as_of=as_of,
        )
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
    as_of: datetime | None = None,
) -> tuple[dict[str, Any], ...]:
    prices = current_price_by_stock or {}
    rows = [
        mock_monitoring_tree_projection(
            document,
            current_price=prices.get(document["session"]["stock_code"]),
            as_of=as_of,
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
        operator_events: list[dict[str, Any]] = []
        last_evaluation_by_scope: dict[tuple[str, str], tuple[str, str, str]] = {}
        for event in events:
            if not isinstance(event, dict) or clean_text(event.get("event_type")) != "ROUTINE_EVALUATED":
                operator_events.append(event)
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            instance_id = clean_text(event.get("routine_instance_id"))
            operation_identity = clean_text(payload.get("operation_identity"))
            side = clean_text(payload.get("signal")).upper() or "NONE"
            signature = (
                side,
                clean_text(payload.get("rules_hash")),
                clean_text(payload.get("signal_bar_time")) if side in {"BUY", "SELL"} else "",
            )
            scope = (instance_id, operation_identity)
            if last_evaluation_by_scope.get(scope) == signature:
                continue
            last_evaluation_by_scope[scope] = signature
            operator_events.append(event)
        events = operator_events
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
