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
from pathlib import Path

from gui_stock_data import (
    assigned_runtime_dirs_for_stock,
    write_stock_config,
)
from runtime_io import read_json_dict
from state_policy import real_trade_enabled

OPERATION_EXCLUDED_CONFIG_KEY = "operation_excluded"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def auto_trade_global_operation_running_for_assignment(window: object) -> bool:
    current = window
    for _ in range(6):
        parent_getter = getattr(current, "parent", None)
        if not callable(parent_getter):
            return False
        parent = parent_getter()
        if parent is None:
            return False

        candidates = [parent]
        auto_trade_setting = getattr(parent, "auto_trade_setting_window", None)
        if auto_trade_setting is not None:
            candidates.append(auto_trade_setting)

        for candidate in candidates:
            running_targets = getattr(
                candidate,
                "running_registered_operation_targets",
                None,
            )
            if callable(running_targets):
                try:
                    return bool(running_targets())
                except Exception:
                    return False
        current = parent
    return False


def apply_default_operation_exclusion_for_new_running_assignment(
    window: object,
    stock_dir: Path,
    previous_config: dict[str, object],
) -> bool:
    previous_instance_id = str(
        previous_config.get("assigned_routine_instance_id", "") or ""
    ).strip()
    if previous_instance_id:
        return False
    if not auto_trade_global_operation_running_for_assignment(window):
        return False

    config_path = stock_dir / "config.json"
    config = read_json_dict(config_path)
    if OPERATION_EXCLUDED_CONFIG_KEY in config:
        return False

    config[OPERATION_EXCLUDED_CONFIG_KEY] = True
    config["updated_at"] = now_text()
    try:
        write_stock_config(stock_dir, config)
        saved_config = read_json_dict(config_path)
    except Exception:
        return False
    return saved_config.get(OPERATION_EXCLUDED_CONFIG_KEY) is True


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
