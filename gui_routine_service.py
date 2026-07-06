# -*- coding: utf-8 -*-
"""
gui_routine_service.py

루틴 정합성 보정 Service 함수 모음.

현재 단계:
- 실제 config 수정/저장처럼 상태를 바꾸는 함수만 분리한다.
- UI, QMessageBox, QTableWidget에 의존하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from gui_stock_data import (
    assigned_runtime_dirs_for_stock,
    write_stock_config,
)
from runtime_io import read_json_dict
from state_policy import real_trade_enabled


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_config() -> dict[str, object]:
    return {
        "timeframe": "1m",
        "trade_amount_type": "AMOUNT",
        "buy_amount": 100000,
        "buy_qty": 0,
        "buy_signal_bar": 1,
        "sell_signal_bar": 1,
        "buy_amount_mode": "ADD",
        "buy_amount_step": 1,
        "buy_amount_custom_steps": [],
        "max_buy_count": 3,
        "profit_hold_enabled": False,
        "profit_hold_percent": 0,
        "resell_condition": "NEXT_SELL_SIGNAL",
        "resell_profit_percent": 0,
        "allow_higher_rebuy": False,
        "daily_loss_limit": -3,
        "budget_limit": 1000000,
        "investment_type": "SHORT_TERM",
        "investment_period": 0,
        "start_time": "09:00",
        "end_buy_time": "13:30",
        "auto_start_enabled": False,
        "auto_start_time": "09:00",
        "auto_stop_enabled": False,
        "auto_stop_time": "15:20",
        "auto_stop_mode": "SELL_ONLY",
        "pause_resume_policy": "SIGNAL_REVIEW",
        "operation_mode": "SCHEDULED",
        "real_trade_enabled": True,
    }


def ensure_single_real_trade_routine_for_stock(
    code: str,
    name: str,
    preferred_routine_name: str | None = None,
) -> str:
    """
    동일 종목 다중 루틴 등록 시 실주문 가능 루틴은 1개만 유지한다.

    Service 함수이다.
    config.json을 실제로 수정할 수 있다.
    """
    assigned = assigned_runtime_dirs_for_stock(code, name)
    if not assigned:
        return ""

    assigned_names = [routine_name for routine_name, _ in assigned]
    selected_routine = ""

    if preferred_routine_name and preferred_routine_name in assigned_names:
        selected_routine = preferred_routine_name
    else:
        for routine_name, stock_dir in assigned:
            config = read_json_dict(stock_dir / "config.json") or default_config()
            if real_trade_enabled(config):
                selected_routine = routine_name
                break

    if not selected_routine:
        selected_routine = assigned_names[0]

    for routine_name, stock_dir in assigned:
        config = read_json_dict(stock_dir / "config.json") or default_config()
        next_enabled = routine_name == selected_routine
        if config.get("real_trade_enabled") != next_enabled:
            config["real_trade_enabled"] = next_enabled
            config["real_trade_policy_updated_at"] = now_text()
            write_stock_config(stock_dir, config)

    return selected_routine
