from __future__ import annotations

from datetime import datetime
from typing import Any

from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_required_state,
)
from gui_ats_utils import manual_ats_active_now
from gui_auto_trade_policy import (
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
)
from operation_policy_gate import is_emergency_stop
from state_policy import (
    in_manual_trading_session,
    normalize_operation_mode,
    scheduled_status_for_now,
)


_CLOSE_STATUSES = {
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "AUTO_CLOSE",
    "AUTO_CLOSING",
}
_LIQUIDATION_STATUSES = {
    "LIQUIDATION",
    "LIQUIDATING",
    "LIQUIDATED",
}
_LIQUIDATION_REQUEST_KEYS = (
    "individual_liquidation_request",
    "manual_ats_liquidation_request",
)
_REQUEST_TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "ORDER_BLOCKED",
    "CANCELED",
    "CANCELLED",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _method_text(value: object) -> str:
    text = _text(value)
    upper = text.upper()
    aliases = {
        "ROUTINE": "루틴",
        "ROUTINE_CLOSE": "루틴",
        "MARKET": "시장가",
        "CURRENT_PRICE": "현재가",
        "CARRY_OVER": "이월",
    }
    if upper in aliases:
        return aliases[upper]
    if text in {"루틴매도신호", "루틴매도", "루틴마감"}:
        return "루틴"
    if text in {"시장가즉시"}:
        return "시장가"
    if text in {"현재가즉시"}:
        return "현재가"
    return text


def _state_close_methods(state: dict[str, Any]) -> set[str]:
    methods: set[str] = set()
    for method_key, policy_key in (
        ("early_close_method", "early_close_policy"),
        ("auto_close_method", "auto_close_policy"),
    ):
        direct = _method_text(state.get(method_key))
        if direct:
            methods.add(direct)
        policy = state.get(policy_key)
        if isinstance(policy, dict):
            policy_method = _method_text(policy.get("method"))
            if policy_method:
                methods.add(policy_method)
    return methods


def _active_liquidation_request(state: dict[str, Any]) -> bool:
    for key in _LIQUIDATION_REQUEST_KEYS:
        request = state.get(key)
        if not isinstance(request, dict) or not request:
            continue
        request_status = _text(request.get("status")).upper() or "REQUESTED"
        if request_status not in _REQUEST_TERMINAL_STATUSES:
            return True
    return False


def _today_normal_ended(
    operation_state: dict[str, Any],
    now_dt: datetime,
) -> bool:
    return (
        _text(operation_state.get("operation_date"))
        == now_dt.date().isoformat()
        and _text(operation_state.get("operation_status")).upper()
        == "NORMAL_ENDED"
    )


def canonical_stock_trading_time_status(
    *,
    config: dict[str, Any] | None,
    state: dict[str, Any] | None,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    """Project the existing per-stock regular/ATS trading-time contract."""

    if not isinstance(config, dict):
        return {
            "evaluable": False,
            "active": False,
            "mode": "",
            "reason": "CONFIG_UNAVAILABLE",
        }

    current = now_dt or datetime.now()
    try:
        mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        if mode == "CONTINUOUS":
            runtime_state = state if isinstance(state, dict) else {}
            regular_active = in_manual_trading_session(
                now_dt=current,
                config=config,
            )
            ats_active = manual_ats_active_now(
                config,
                runtime_state,
                current,
            )
            return {
                "evaluable": True,
                "active": bool(regular_active or ats_active),
                "mode": mode,
                "reason": (
                    "ACTIVE_REGULAR"
                    if regular_active
                    else "ACTIVE_ATS"
                    if ats_active
                    else "OUTSIDE_OPERATION_TIME"
                ),
            }

        active = scheduled_status_for_now(config, current) == "RUNNING"
        return {
            "evaluable": True,
            "active": active,
            "mode": mode,
            "reason": "ACTIVE_SCHEDULED" if active else "OUTSIDE_OPERATION_TIME",
        }
    except Exception as exc:
        return {
            "evaluable": False,
            "active": False,
            "mode": "",
            "reason": "TIME_POLICY_ERROR",
            "error": str(exc),
        }


def canonical_routine_order_permission(
    *,
    state: dict[str, Any] | None,
    signal_type: object,
    display_status: str = "",
    config: dict[str, Any] | None = None,
    operation_state: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    """Return canonical, read-only BUY/SELL permission for OrderManager."""
    runtime_state = state if isinstance(state, dict) else {}
    signal = _text(signal_type).upper()
    current = now_dt or datetime.now()
    global_state = operation_state if isinstance(operation_state, dict) else {}
    status = _text(runtime_state.get("status") or display_status).upper()
    command_mode = _text(runtime_state.get("operation_command_mode")).upper()
    close_methods = _state_close_methods(runtime_state)
    routine_close_method = (
        command_mode in {"EARLY_CLOSE", "AUTO_CLOSE"}
        and "루틴" in close_methods
        and not (close_methods & {"시장가", "현재가", "이월"})
    )

    def blocked(reason: str) -> dict[str, Any]:
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": reason,
            "close_routine_active": False,
            "mark_close_final_sell_after_order": False,
        }

    if signal not in {"BUY", "SELL"}:
        return blocked("지원하지 않는 루틴 신호")
    if _today_normal_ended(global_state, current):
        return blocked("당일 정상운영 종료 상태")
    if is_emergency_stop(global_state) or is_emergency_stopped_state(runtime_state):
        return blocked("긴급정지 상태")
    if is_review_required_state(runtime_state):
        return blocked("검토관리 상태")
    if runtime_state.get("trade_enabled") is not True:
        return blocked("매매 비활성 상태")
    if runtime_state.get("signal_probe_only") is True:
        return blocked("신호평가 전용 상태")
    if (
        status in _LIQUIDATION_STATUSES
        or (
            bool(runtime_state.get("liquidation_policy_forced", False))
            and not routine_close_method
        )
        or _active_liquidation_request(runtime_state)
    ):
        return blocked("청산 진행 상태")

    if command_mode == "CARRY_OVER" or "이월" in close_methods:
        return blocked("이월 상태")
    if close_methods & {"시장가", "현재가"}:
        return blocked("직접청산 진행 상태")

    final_sell_ordered = bool(
        runtime_state.get("close_routine_final_sell_ordered")
        or _text(runtime_state.get("close_routine_final_sell_ordered_at"))
    )
    if final_sell_ordered:
        return blocked("조기/자동마감 루틴 마지막 매도 이후 추가 주문 차단")

    close_routine_active = auto_trade_setting_close_routine_mode_active(
        runtime_state,
        display_status=status,
    )
    if close_routine_active:
        close_allowed, close_reason = auto_trade_setting_close_routine_order_allowed(
            runtime_state,
            signal,
            display_status=status,
        )
        if not close_allowed:
            return blocked(close_reason)
        return {
            "allowed": True,
            "signal_type": signal,
            "reason": close_reason,
            "close_routine_active": True,
            "mark_close_final_sell_after_order": signal == "SELL",
        }

    if status in _CLOSE_STATUSES:
        return blocked("루틴마감이 아닌 마감 진행 상태")
    if status != "RUNNING":
        return blocked("운영 상태가 RUNNING이 아님")

    if isinstance(config, dict):
        time_status = canonical_stock_trading_time_status(
            config=config,
            state=runtime_state,
            now_dt=current,
        )
        if time_status.get("active") is not True:
            return blocked("운영시간 밖")

    return {
        "allowed": True,
        "signal_type": signal,
        "reason": "주문판정 통과",
        "close_routine_active": False,
        "mark_close_final_sell_after_order": False,
    }
