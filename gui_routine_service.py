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
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_setting_trade_started,
)

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        return ""

    for routine_name, stock_dir in assigned:
        config = read_json_dict(stock_dir / "config.json") or default_config()
        next_enabled = routine_name == selected_routine
        if config.get("real_trade_enabled") != next_enabled:
            config["real_trade_enabled"] = next_enabled
            config["real_trade_policy_updated_at"] = now_text()
            write_stock_config(stock_dir, config)

    return selected_routine


_TRADE_PERMISSION_BLOCKED_STATUSES = {
    "RUNNING",
    "SELL_ONLY",
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "AUTO_CLOSED",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "EARLY_CLOSED",
    "REVIEW_REQUIRED",
    "REVIEW",
    "EMERGENCY_STOPPED",
    "EMERGENCY_STOP",
    "EMERGENCY",
}


def _trade_permission_change_block_reason(
    window,
    stock_dir: Path,
    code: str,
) -> str:
    state = read_json_dict(stock_dir / "state.json")
    if not isinstance(state, dict):
        state = {}
    if str(code or "").strip() in auto_trade_current_session_operation_participant_codes(window):
        return "CURRENT_SESSION_PARTICIPANT"
    if auto_trade_setting_trade_started(state):
        return "TRADE_STARTED"
    status = str(state.get("status", "") or "").strip().upper()
    if status in _TRADE_PERMISSION_BLOCKED_STATUSES:
        return f"STATUS_{status}"
    return ""


def set_stock_real_trade_enabled(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    enabled: bool,
) -> dict[str, object]:
    """Persist the existing real-trade permission field while the stock is stopped."""

    stock_dir = Path(stock_dir)
    reason = _trade_permission_change_block_reason(window, stock_dir, code)
    if reason:
        return {
            "ok": False,
            "changed": False,
            "reason": reason,
            "code": code,
            "name": name,
        }

    config = read_json_dict(stock_dir / "config.json")
    if not isinstance(config, dict) or not config:
        return {
            "ok": False,
            "changed": False,
            "reason": "CONFIG_MISSING",
            "code": code,
            "name": name,
        }

    requested = bool(enabled)
    before = real_trade_enabled(config)
    if before == requested and config.get("real_trade_enabled") is requested:
        return {
            "ok": True,
            "changed": False,
            "reason": "UNCHANGED",
            "code": code,
            "name": name,
            "real_trade_enabled": requested,
        }

    next_config = dict(config)
    next_config["real_trade_enabled"] = requested
    next_config["real_trade_policy_updated_at"] = now_text()
    next_config["updated_at"] = now_text()
    write_stock_config(stock_dir, next_config)

    if requested:
        routine_name = str(
            next_config.get("routine_instance_name")
            or next_config.get("routine")
            or next_config.get("routine_name")
            or ""
        ).strip()
        ensure_single_real_trade_routine_for_stock(code, name, routine_name or None)

    saved = read_json_dict(stock_dir / "config.json")
    if real_trade_enabled(saved) is not requested:
        return {
            "ok": False,
            "changed": False,
            "reason": "READ_BACK_FAILED",
            "code": code,
            "name": name,
        }

    return {
        "ok": True,
        "changed": before != requested or config.get("real_trade_enabled") is not requested,
        "reason": "UPDATED",
        "code": code,
        "name": name,
        "real_trade_enabled": requested,
    }
