# -*- coding: utf-8 -*-
"""
gui_routine_policy.py

루틴 지정/변경 가능 여부를 판단하는 Policy 함수.
데이터 수집은 Guard에 맡기고, 이 파일은 가능/불가 판단과 제한 사유 생성만 담당한다.
"""

from __future__ import annotations

from gui_common_utils import safe_int_value
from gui_stock_data import base_stock_routines_for_stock, stock_runtime_dir_for_routine
from gui_order_utils import pending_order_side_quantities
from gui_routine_guard import routine_action_guard_info
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display


def routine_action_reasons_for_stock(code: str, name: str, allow_unassigned: bool = True) -> tuple[bool, dict[str, object]]:
    """
    루틴 지정/변경 가능 여부를 삭제/등록해제 안전 규칙에 맞춰 판정한다.

    허용:
    - 미등록 종목(allow_unassigned=True)
    - 정지/감시중 + 보유 0 + 현재 미체결 0
    """
    info = routine_action_guard_info(code, name)
    reasons: list[str] = []
    routine_name = str(info.get("routine_name", "")).strip()
    raw_status = str(info.get("raw_status", "")).strip().upper()
    display_status = str(info.get("display_status", "")).strip()

    if not routine_name:
        if allow_unassigned:
            info["reasons"] = []
            return True, info
        reasons.append("등록 루틴이 없습니다.")
        info["reasons"] = reasons
        return False, info

    if info.get("stock_dir") is None:
        # 기존 정책상 runtime이 아직 없으면 정지에 준해 루틴명 정리는 허용한다.
        info["reasons"] = []
        return True, info

    allowed_statuses = {"STOPPED", "STOP", "MONITORING", "WATCHING", ""}
    blocked_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY", "PAUSED", "REVIEW_REQUIRED", "EMERGENCY_STOP"}

    if raw_status in blocked_statuses:
        reasons.append(f"{display_status} 상태")
    elif raw_status not in allowed_statuses:
        reasons.append(f"{display_status or '상태확인필요'} 상태")

    holding_qty = safe_int_value(info.get("holding_qty"), 0)
    if holding_qty > 0:
        reasons.append(f"보유 {holding_qty}")

    buy_pending_qty = info.get("buy_pending_qty", 0)
    sell_pending_qty = info.get("sell_pending_qty", 0)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        reasons.append("미체결 확인 필요")
    else:
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            reasons.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            reasons.append(f"매도미결 {sell_pending_qty}")

    info["reasons"] = reasons
    return not reasons, info

def classify_routine_assign_targets(stocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[dict[str, object]]]:
    """루틴 지정 창으로 넘길 수 있는 종목과 차단 종목을 분리한다."""
    allowed: list[tuple[str, str]] = []
    blocked: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for code, name in stocks:
        code = str(code).strip()
        name = str(name).strip()
        key = (code, name)
        if not code or not name or key in seen:
            continue
        seen.add(key)

        can_process, info = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
        if can_process:
            allowed.append(key)
        else:
            blocked.append(info)

    return allowed, blocked


def can_unassign_active_routine_from_stock(code: str, name: str) -> tuple[bool, str, list[str]]:
    """
    종목등록설정 우클릭 '루틴 해제' 가능 여부를 반환한다.

    루틴 해제는 종목 자체는 유지하고 기초종목.txt의 루틴명만 제거한다.
    운영 중이거나 보유/미체결이 있는 종목은 기존 안전 정책에 따라 차단한다.
    """
    exists, routines = base_stock_routines_for_stock(code, name)
    if not exists:
        return False, "", ["기초종목.txt에서 종목을 찾지 못했습니다."]

    routine_name = routines[0] if routines else ""
    if not routine_name:
        return False, "", ["등록 루틴이 없습니다."]

    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return True, routine_name, []

    state = read_json_dict(stock_dir / "state.json")
    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    display_status = auto_trade_status_display(raw_status)
    allowed_statuses = {"STOPPED", "STOP", "MONITORING", "WATCHING", ""}
    blocked_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY", "PAUSED", "REVIEW_REQUIRED", "EMERGENCY_STOP"}

    reasons: list[str] = []
    if raw_status in blocked_statuses:
        reasons.append(f"{display_status} 상태")
    elif raw_status not in allowed_statuses:
        reasons.append(f"{display_status or '상태확인필요'} 상태")

    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    if holding_qty > 0:
        reasons.append(f"보유 {holding_qty}")

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        reasons.append("미체결 확인 필요")
    else:
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            reasons.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            reasons.append(f"매도미결 {sell_pending_qty}")

    return not reasons, routine_name, reasons
