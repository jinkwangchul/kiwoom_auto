# -*- coding: utf-8 -*-
"""
gui_config_utils.py

종목별 config/state/orders 기본값과 runtime 기본 파일 생성 유틸리티.
UI 위젯에 직접 의존하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from gui_common_utils import sanitize_path_part
from runtime_io import write_json_if_missing


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


def default_state() -> dict[str, object]:
    return {
        "status": "STOPPED",
        "trade_set_status": "WAIT_BUY",
        "current_set_no": 1,
        "current_round": 0,
        "avg_price": 0,
        "holding_qty": 0,
        "holding_amount": 0,
        "buy_count": 0,
        "last_buy_price": 0,
        "last_buy_time": "",
        "last_sell_time": "",
        "allocated_budget": 0,
        "used_budget": 0,
        "last_signal_candle_time": "",
        "last_order_candle_time": "",
        "pending_order": False,
        "ignore_sell_until_next_buy": True,
        "updated_at": "",
        "scheduler_enabled": False,
        "paused_at": "",
        "resumed_at": "",
        "review_required": False,
        "review_reason": "",
        "missed_buy_signal_count": 0,
        "missed_sell_signal_count": 0,
        "pause_signal_check_status": "UNCHECKED",
        "ignore_signals_before": "",
    }


def default_orders() -> dict[str, object]:
    return {"orders": []}


def ensure_stock_runtime_files(routine_dir: Path, code: str, name: str) -> Path:
    """
    루틴 폴더 아래 종목별 저장 구조를 생성한다.
    기존 파일은 덮어쓰지 않는다.
    """
    stock_folder_name = f"{sanitize_path_part(code)}_{sanitize_path_part(name)}"
    stock_dir = routine_dir / stock_folder_name
    stock_dir.mkdir(parents=True, exist_ok=True)

    write_json_if_missing(stock_dir / "config.json", default_config())
    write_json_if_missing(stock_dir / "state.json", default_state())
    write_json_if_missing(stock_dir / "orders.json", default_orders())
    (stock_dir / "logs").mkdir(exist_ok=True)

    return stock_dir
