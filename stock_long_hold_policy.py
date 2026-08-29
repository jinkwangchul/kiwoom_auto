# -*- coding: utf-8 -*-
"""Read-only normal long-hold route and final close-intent helpers.

Per-stock Production evidence determines whether a holding is a normal
long-hold route.  The program-wide allow/deny switch belongs to the existing
operation policy and is supplied by callers; this module owns no writer.
"""

from __future__ import annotations

from typing import Any, Iterable

from manual_ats_runtime import manual_ats_runtime_execution_method_result


ROUTE_CLOSE_INTENT = "CLOSE_INTENT"
ROUTE_CARRYOVER = "CARRYOVER"
ROUTE_CONTINUOUS_NO_CLOSE = "CONTINUOUS_NO_CLOSE"
ROUTE_ATS_FINAL_NO_TERMINATION = "ATS_FINAL_NO_TERMINATION"
ROUTE_UNKNOWN = "UNKNOWN"

_CLOSE_ACTIVE_STATUSES = {
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "LIQUIDATION",
    "LIQUIDATING",
}
_CLOSE_TERMINAL_STATUSES = {
    "AUTO_CLOSED",
    "EARLY_CLOSED",
    "LIQUIDATED",
    "CLOSED",
    "DONE",
}
_DISPATCH_EVIDENCE_STATUSES = {
    "SEND_CALL_ACCEPTED",
    "SEND_CALL_REJECTED",
    "SEND_CALL_UNCERTAIN",
    "BROKER_ACCEPTED",
    "PARTIALLY_FILLED",
    "PARTIAL_FILLED",
    "FILLED",
    "REJECTED",
    "FAILED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
}
_TERMINAL_QUEUE_STATUSES = {
    "FILLED",
    "REJECTED",
    "FAILED",
    "CANCELED",
    "CANCELLED",
    "CANCEL_COMPLETE",
    "EXPIRED",
    "LOCAL_RESET",
}
_ATS_TERMINATION_EXECUTED_STATUSES = {
    "SEND_CALL_ACCEPTED",
    "SEND_CALL_REJECTED",
    "SEND_CALL_UNCERTAIN",
    "COMPLETED",
}
_ATS_TERMINATION_FAILED_STATUSES = {"FAILED", "ORDER_BLOCKED"}


def normalized_close_method(value: object) -> str:
    text = str(value or "").strip()
    upper = text.upper().replace(" ", "_")
    if text == "이월" or upper in {"CARRYOVER", "ROLLOVER"}:
        return "CARRYOVER"
    if text == "시장가" or upper in {"MARKET", "MARKET_PRICE"}:
        return "MARKET"
    if text == "현재가" or upper in {"CURRENT", "CURRENT_PRICE"}:
        return "CURRENT_PRICE"
    return ""


def final_close_liquidation_method(state: dict[str, Any] | None) -> str:
    """Return the final method with the newest individual request first."""
    if not isinstance(state, dict):
        return ""

    request = state.get("individual_liquidation_request")
    if isinstance(request, dict):
        status = str(request.get("status") or "").strip().upper()
        method = normalized_close_method(request.get("method"))
        if status == "REQUESTED":
            return method

    # Liquidation evidence outranks earlier close-method evidence.
    for key in ("liquidation_method", "early_close_method", "auto_close_method"):
        method = normalized_close_method(state.get(key))
        if method:
            return method
    return ""


def has_active_individual_liquidation_request(
    state: dict[str, Any] | None,
) -> bool:
    if not isinstance(state, dict):
        return False
    request = state.get("individual_liquidation_request")
    return isinstance(request, dict) and str(
        request.get("status") or ""
    ).strip().upper() == "REQUESTED"


def _text(value: object) -> str:
    return str(value or "").strip()


def _upper(value: object) -> str:
    return _text(value).upper()


def _normalized_operation_mode(value: object) -> str:
    text = _upper(value)
    if not text:
        return "CONTINUOUS"
    if text in {"CONTINUOUS", "MANUAL", "수동"}:
        return "CONTINUOUS"
    if text in {"SCHEDULED", "TIME", "시간"}:
        return "SCHEDULED"
    return text


def _queue_route_evidence(
    queue_records: Iterable[dict[str, Any]] | None,
    *,
    command_ids: set[str],
) -> dict[str, object]:
    statuses: list[str] = []
    order_ids: list[str] = []
    for record in queue_records or ():
        if not isinstance(record, dict):
            continue
        source = _upper(record.get("source"))
        reason = _upper(record.get("reason") or record.get("candidate_reason"))
        source_signal_id = _text(record.get("source_signal_id"))
        order_id = _text(record.get("id") or record.get("order_id"))
        route_owned = (
            source in {"OPERATION_COMMAND", "MANUAL_ATS_LIQUIDATION"}
            or "LIQUIDATION" in reason
            or order_id.startswith(("CLOSE_LIQUIDATION_", "ATS_LIQUIDATION_"))
            or (source_signal_id and source_signal_id in command_ids)
        )
        if not route_owned:
            continue
        status = _upper(record.get("status") or record.get("order_status"))
        if status:
            statuses.append(status)
        if order_id:
            order_ids.append(order_id)
    return {
        "statuses": tuple(statuses),
        "order_ids": tuple(order_ids),
        "dispatch_evidence": any(
            status in _DISPATCH_EVIDENCE_STATUSES for status in statuses
        ),
        "terminal_evidence": any(
            status in _TERMINAL_QUEUE_STATUSES for status in statuses
        ),
    }


def classify_termination_route(
    state: dict[str, Any] | None,
    *,
    operation_mode: object = "",
    final_session_ended: bool = False,
    queue_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Classify the actual end route without treating a method setting as execution."""

    if not isinstance(state, dict):
        return {
            "route": ROUTE_UNKNOWN,
            "method": "",
            "source": "STATE_UNAVAILABLE",
            "route_completed": False,
            "actual_termination_executed": False,
            "safety_issue": True,
        }

    mode = _normalized_operation_mode(operation_mode)
    status = _upper(state.get("status"))
    individual = state.get("individual_liquidation_request")
    individual = individual if isinstance(individual, dict) else {}
    ats_request = state.get("manual_ats_liquidation_request")
    ats_request = ats_request if isinstance(ats_request, dict) else {}
    individual_status = _upper(individual.get("status"))
    ats_status = _upper(ats_request.get("status"))
    command_ids = {
        value
        for value in (
            _text(individual.get("command_id")),
            _text(ats_request.get("command_id")),
            _text(state.get("operation_command_id")),
        )
        if value
    }
    queue_evidence = _queue_route_evidence(
        queue_records,
        command_ids=command_ids,
    )

    ats_method = normalized_close_method(ats_request.get("sell_method"))
    individual_method = normalized_close_method(individual.get("method"))
    persisted_method = final_close_liquidation_method(state)
    carryover_result = any(
        _upper(state.get(key)) in {"CURRENT_CARRYOVER", "CARRYOVER_DONE"}
        for key in (
            "operation_notice",
            "liquidation_result",
            "liquidation_result_status",
            "auto_close_result",
            "early_close_result",
            "close_result",
        )
    )
    method = (
        ats_method
        or individual_method
        or persisted_method
        or ("CARRYOVER" if carryover_result else "")
    )

    completion_timestamp = next(
        (
            _text(state.get(key))
            for key in (
                "liquidation_completed_at",
                "liquidation_finished_at",
                "daily_liquidation_completed_at",
                "ats_sell_completed_at",
            )
            if _text(state.get(key))
        ),
        "",
    )
    close_entry_evidence = bool(
        individual
        or _text(state.get("auto_close_requested_at"))
        or _text(state.get("early_close_requested_at"))
        or _text(state.get("liquidation_method"))
        or _text(state.get("auto_close_method"))
        or _text(state.get("early_close_method"))
        or carryover_result
        or status in _CLOSE_ACTIVE_STATUSES | _CLOSE_TERMINAL_STATUSES
        or state.get("close_routine_final_sell_ordered") is True
        or _text(state.get("close_routine_final_sell_ordered_at"))
    )
    ats_termination_evidence = bool(ats_request)
    actual_execution = bool(
        ats_status in _ATS_TERMINATION_EXECUTED_STATUSES
        or queue_evidence["dispatch_evidence"]
        or status in _CLOSE_TERMINAL_STATUSES
        or completion_timestamp
    )
    route_completed = bool(
        ats_status == "COMPLETED"
        or queue_evidence["terminal_evidence"]
        or status in _CLOSE_TERMINAL_STATUSES
        or completion_timestamp
    )

    if ats_termination_evidence:
        return {
            "route": ROUTE_CLOSE_INTENT,
            "method": ats_method,
            "source": "MANUAL_ATS_LIQUIDATION_REQUEST",
            "route_completed": route_completed,
            "actual_termination_executed": actual_execution,
            "safety_issue": ats_status in _ATS_TERMINATION_FAILED_STATUSES,
            "request_status": ats_status,
            "queue_evidence": queue_evidence,
        }

    if close_entry_evidence:
        route = ROUTE_CARRYOVER if method == "CARRYOVER" else ROUTE_CLOSE_INTENT
        return {
            "route": route,
            "method": method,
            "source": (
                "INDIVIDUAL_LIQUIDATION_REQUEST"
                if individual
                else "CLOSE_RUNTIME_EVIDENCE"
            ),
            "route_completed": bool(
                route_completed or (route == ROUTE_CARRYOVER and final_session_ended)
            ),
            "actual_termination_executed": actual_execution,
            "safety_issue": bool(individual_status and not individual_method),
            "request_status": individual_status,
            "queue_evidence": queue_evidence,
        }

    selection = state.get("manual_ats_selection")
    selection = selection if isinstance(selection, dict) else {}
    ats_sessions = tuple(
        str(value or "").strip()
        for value in selection.get("selected_sessions", ())
        if str(value or "").strip()
    ) if isinstance(selection.get("selected_sessions", ()), (list, tuple, set)) else ()
    if mode == "CONTINUOUS" and ats_sessions and final_session_ended:
        method_result = manual_ats_runtime_execution_method_result(state)
        if method_result.get("ok") is not True:
            return {
                "route": ROUTE_UNKNOWN,
                "method": "",
                "source": "ATS_EXECUTION_METHOD_INVALID",
                "route_completed": True,
                "actual_termination_executed": False,
                "safety_issue": True,
                "method_result": method_result,
            }
        return {
            "route": ROUTE_ATS_FINAL_NO_TERMINATION,
            "method": str(method_result.get("execution_method") or "ROUTINE"),
            "source": "ATS_FINAL_SESSION_WITHOUT_TERMINATION",
            "route_completed": True,
            "actual_termination_executed": False,
            "safety_issue": False,
            "method_result": method_result,
        }

    if mode == "CONTINUOUS":
        return {
            "route": ROUTE_CONTINUOUS_NO_CLOSE,
            "method": "",
            "source": "NO_CLOSE_RUNTIME_EVIDENCE",
            "route_completed": bool(final_session_ended),
            "actual_termination_executed": False,
            "safety_issue": False,
        }

    return {
        "route": ROUTE_UNKNOWN,
        "method": method,
        "source": "TERMINATION_ROUTE_UNPROVEN",
        "route_completed": False,
        "actual_termination_executed": False,
        "safety_issue": True,
    }


def review_reason_is_holding_residual(state: dict[str, Any] | None) -> bool:
    """Recognize only a persisted review reason whose meaning is residual holding."""
    if not isinstance(state, dict):
        return False
    text = " / ".join(
        str(state.get(key) or "").strip()
        for key in ("review_reason", "review_detail")
        if str(state.get(key) or "").strip()
    ).lower()
    if not text:
        return False
    holding_tokens = (
        "보유수량 존재",
        "보유 잔량",
        "보유잔량",
        "holding remains",
        "holding quantity remains",
        "holding quantity exists",
        "positive durable holding quantity remains",
    )
    danger_tokens = (
        "무결성",
        "불일치",
        "오류",
        "실패",
        "미체결",
        "주문",
        "queue",
        "broker",
        "recovery",
        "integrity",
        "mismatch",
        "error",
        "failed",
        "conflict",
    )
    return any(token in text for token in holding_tokens) and not any(
        token in text for token in danger_tokens
    )


def is_normal_long_term_holding_route(
    state: dict[str, Any] | None,
    *,
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
    safety_issue: bool,
    operation_mode: object = "",
    final_session_ended: bool = False,
    queue_records: Iterable[dict[str, Any]] | None = None,
) -> bool:
    """Return whether Production evidence proves a normal holding route.

    This is deliberately fail-closed. Damaged evidence, pending orders, active
    liquidation intent, or an unclassified reason never form a normal route.
    """
    if not isinstance(state, dict):
        return False
    if safety_issue or holding_qty <= 0:
        return False
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return False
    try:
        if int(buy_pending_qty or 0) > 0 or int(sell_pending_qty or 0) > 0:
            return False
    except (TypeError, ValueError):
        return False

    for key in ("recovery_status", "reconciliation_status", "integrity_status"):
        if str(state.get(key) or "").strip().upper() in {
            "FAILED", "FAIL", "ERROR", "MISMATCH", "UNKNOWN", "UNSTABLE"
        }:
            return False
    if state.get("emergency_reason") or state.get("emergency_stopped_at"):
        return False
    route = classify_termination_route(
        state,
        operation_mode=operation_mode,
        final_session_ended=final_session_ended,
        queue_records=queue_records,
    )
    if route.get("safety_issue") is True:
        return False
    if route.get("route") not in {
        ROUTE_CARRYOVER,
        ROUTE_CONTINUOUS_NO_CLOSE,
        ROUTE_ATS_FINAL_NO_TERMINATION,
    }:
        return False
    if route.get("route_completed") is not True and not review_reason_is_holding_residual(state):
        return False
    return True


def long_hold_excludes_holding_review(
    global_policy_enabled: bool,
    state: dict[str, Any] | None,
    *,
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
    safety_issue: bool,
    operation_mode: object = "",
    final_session_ended: bool = False,
    queue_records: Iterable[dict[str, Any]] | None = None,
) -> bool:
    """Apply the global policy only after the stock route is proven normal."""
    if global_policy_enabled is not True:
        return False
    return is_normal_long_term_holding_route(
        state,
        holding_qty=holding_qty,
        buy_pending_qty=buy_pending_qty,
        sell_pending_qty=sell_pending_qty,
        safety_issue=safety_issue,
        operation_mode=operation_mode,
        final_session_ended=final_session_ended,
        queue_records=queue_records,
    )
