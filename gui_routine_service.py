# -*- coding: utf-8 -*-
"""
gui_routine_service.py

루틴 정합성 보정 Service 함수 모음.

현재 단계:
- 실제 config 수정/저장처럼 상태를 바꾸는 함수만 분리한다.
- UI, QMessageBox, QTableWidget에 의존하지 않는다.
"""

from __future__ import annotations

def default_config() -> dict[str, object]:
    return {
        "timeframe": "1m",
        "trade_amount_type": "QUANTITY",
        "buy_amount": 0,
        "buy_qty": 1,
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
        "operation_mode": "SCHEDULED",
    }
