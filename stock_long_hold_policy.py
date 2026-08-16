# -*- coding: utf-8 -*-
"""Read-only normal long-hold route and final close-intent helpers.

Per-stock Production evidence determines whether a holding is a normal
long-hold route.  The program-wide allow/deny switch belongs to the existing
operation policy and is supplied by callers; this module owns no writer.
"""

from __future__ import annotations

from typing import Any


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

    method = final_close_liquidation_method(state)
    if has_active_individual_liquidation_request(state) and not method:
        return False
    if method in {"MARKET", "CURRENT_PRICE"}:
        return False
    if not method:
        raw_status = str(state.get("status") or "").strip().upper()
        if raw_status in {"AUTO_CLOSING", "EARLY_CLOSING", "LIQUIDATION", "LIQUIDATING"}:
            return False

    for key in ("recovery_status", "reconciliation_status", "integrity_status"):
        if str(state.get(key) or "").strip().upper() in {
            "FAILED", "FAIL", "ERROR", "MISMATCH", "UNKNOWN", "UNSTABLE"
        }:
            return False
    if state.get("emergency_reason") or state.get("emergency_stopped_at"):
        return False
    return review_reason_is_holding_residual(state)


def long_hold_excludes_holding_review(
    global_policy_enabled: bool,
    state: dict[str, Any] | None,
    *,
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
    safety_issue: bool,
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
    )
