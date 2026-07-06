# -*- coding: utf-8 -*-
"""
gui_routine_guard.py

루틴 지정/해제/삭제 전 Policy 판단에 필요한 현재 상태 정보 수집 함수.
UI에 의존하지 않고, 최종 가능/불가 판단은 하지 않는다.
"""

from __future__ import annotations

from gui_common_utils import safe_int_value
from gui_stock_data import active_routine_for_stock, stock_runtime_dir_for_routine
from gui_order_utils import pending_order_side_quantities
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display


def routine_action_guard_info(code: str, name: str) -> dict[str, object]:
    """
    루틴 지정/해제/삭제 사전 점검에 사용할 현재 상태 정보를 수집한다.
    """
    routine_name = active_routine_for_stock(code, name)
    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name) if routine_name else None
    state: dict[str, object] = {}
    raw_status = ""
    display_status = "미지정" if not routine_name else "미생성"
    holding_qty = 0
    buy_pending_qty: object = 0
    sell_pending_qty: object = 0

    if stock_dir is not None:
        state = read_json_dict(stock_dir / "state.json")
        raw_status = str(state.get("status", "STOPPED")).strip().upper()
        display_status = auto_trade_status_display(raw_status)
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

    return {
        "code": code,
        "name": name,
        "routine_name": routine_name,
        "stock_dir": stock_dir,
        "state": state,
        "raw_status": raw_status,
        "display_status": display_status,
        "holding_qty": holding_qty,
        "buy_pending_qty": buy_pending_qty,
        "sell_pending_qty": sell_pending_qty,
    }
