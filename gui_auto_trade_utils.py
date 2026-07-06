# -*- coding: utf-8 -*-
"""
gui_auto_trade_utils.py

자동매매설정 창에서 사용하는 자동매매 관련 판정 유틸리티.
UI에 직접 의존하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from gui_order_utils import pending_order_side_quantities
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display


def auto_trade_unregister_category(
    routine_name: str,
    stock_dir: Path,
    code: str,
    name: str,
) -> dict[str, object]:
    """
    자동매매설정 창의 루틴 등록해제 정책에 따라 선택 종목을 분류한다.

    반환 category:
    - immediate: 정지/감시중 + 보유 0 + 현재 미체결 0
    - force: 정지/감시중 + 보유 또는 현재 미체결 있음
    - blocked: 매수/매도, 매도만, 일시중지, 검토필요, 확인불가 등
    """
    state = read_json_dict(stock_dir / "state.json")
    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    display_status = auto_trade_status_display(raw_status)

    try:
        holding_qty = int(state.get("holding_qty", 0) or 0)
    except Exception:
        holding_qty = 0

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    item: dict[str, object] = {
        "code": code,
        "name": name,
        "title": f"{code} {name}",
        "runtime_dirs": [(routine_name, stock_dir)],
    }

    allowed_statuses = {"STOPPED", "STOP", "MONITORING", "WATCHING", ""}
    blocked_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}

    if raw_status in blocked_statuses:
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: {display_status} 상태"]
        return item

    if raw_status not in allowed_statuses:
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: {display_status or '상태확인필요'} 상태"]
        return item

    if buy_pending_qty == "?" or sell_pending_qty == "?":
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: 미체결 확인 필요"]
        return item

    pending_parts: list[str] = []
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        pending_parts.append(f"매수미결 {buy_pending_qty}")
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        pending_parts.append(f"매도미결 {sell_pending_qty}")

    if holding_qty > 0 or pending_parts:
        details: list[str] = []
        if holding_qty > 0:
            details.append(f"보유 {holding_qty}")
        details.extend(pending_parts)
        reason = f"{routine_name}: {display_status}"
        if details:
            reason += f" / {', '.join(details)}"
        item["category"] = "force"
        item["reasons"] = [reason]
        return item

    item["category"] = "immediate"
    item["reasons"] = [f"{routine_name}: 정지/감시중, 보유·미체결 없음"]
    return item
