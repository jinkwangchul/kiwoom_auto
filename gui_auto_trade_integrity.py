# -*- coding: utf-8 -*-
"""
gui_auto_trade_integrity.py

자동매매 안전성/무결성 판정 헬퍼 모듈.
- 검토관리 대상 여부 판정
- 내부 데이터 불일치 판정
- 서버/프로그램 불일치 표시 판정
- 재시작 초기검사 사유 판정

주의:
- QTableWidget 등 화면 직접 조작은 포함하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from gui_order_utils import (
    format_number_value,
    pending_order_side_quantities,
)
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display


def unique_review_reasons(reasons) -> list[str]:
    """검토 사유 목록에서 빈값/중복을 제거하고 입력 순서를 유지한다."""
    result: list[str] = []
    seen: set[str] = set()

    for reason in reasons:
        text = str(reason).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)

    return result


def is_review_required_state(state: dict[str, object] | None) -> bool:
    """검토관리 전용 분리 판정.

    자동매매설정 창에서는 이 조건에 걸린 종목을 절대 표시하지 않는다.
    검토관리 창에서는 이 조건에 걸린 종목만 표시한다.
    """
    if not isinstance(state, dict):
        return False

    raw_status = str(state.get("status", "")).strip().upper()
    if raw_status in {"REVIEW_REQUIRED", "REVIEW"}:
        return True

    if bool(state.get("review_required", False)):
        return True

    try:
        return auto_trade_status_display(raw_status) == "검토종목"
    except Exception:
        return False


def is_review_required_stock_dir(stock_dir: Path) -> bool:
    """runtime 폴더 기준 검토관리 전용 종목 여부."""
    try:
        state = read_json_dict(stock_dir / "state.json")
    except Exception:
        return False
    return is_review_required_state(state)


def auto_trade_setting_data_inconsistency_reasons(state: dict[str, object] | None) -> list[str]:
    """운영 중/재시작/안정성검사 공통 내부 데이터 불일치 판정.

    주의:
    - holding_qty/current_qty/qty 계열은 수량으로 본다.
    - holding_amount 계열은 수량이 아니라 보유금액/평가금액 계열로 본다.
    - 보유수량 0인데 평단 또는 보유금액이 남아 있으면 비정상으로 본다.
    """
    if not isinstance(state, dict):
        return ["state.json 형식 이상"]

    reasons: list[str] = []

    def present(key: str) -> bool:
        return key in state and state.get(key) not in (None, "")

    def number_value(key: str, default: float = 0.0) -> tuple[float, bool]:
        if not present(key):
            return default, False
        value = state.get(key)
        try:
            if isinstance(value, str):
                value = value.replace(",", "").strip()
            return float(value), True
        except Exception:
            reasons.append(f"{key} 숫자 형식 오류")
            return default, True

    qty_keys = [
        "holding_qty",
        "current_qty",
        "current_quantity",
        "qty",
        "balance_qty",
        "position_qty",
    ]
    amount_keys = [
        "holding_amount",
        "holding_value",
        "holding_eval_amount",
        "position_amount",
        "stock_value",
    ]
    avg_keys = [
        "avg_price",
        "average_price",
        "avg_buy_price",
        "buy_avg_price",
        "average_buy_price",
    ]

    qty_values: dict[str, float] = {}
    amount_values: dict[str, float] = {}
    avg_values: dict[str, float] = {}

    for key in qty_keys:
        value, exists = number_value(key)
        if exists:
            qty_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in amount_keys:
        value, exists = number_value(key)
        if exists:
            amount_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in avg_keys:
        value, exists = number_value(key)
        if exists:
            avg_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    primary_qty = qty_values.get("holding_qty", 0.0)
    if primary_qty == 0:
        positive_qtys = [value for value in qty_values.values() if value > 0]
        if positive_qtys:
            primary_qty = max(positive_qtys)

    primary_avg = avg_values.get("avg_price", 0.0)
    if primary_avg == 0:
        positive_avgs = [value for value in avg_values.values() if value > 0]
        if positive_avgs:
            primary_avg = max(positive_avgs)

    primary_amount = amount_values.get("holding_amount", 0.0)
    if primary_amount == 0:
        positive_amounts = [value for value in amount_values.values() if value > 0]
        if positive_amounts:
            primary_amount = max(positive_amounts)

    positive_qty_pairs = {key: value for key, value in qty_values.items() if value > 0}
    if len(set(positive_qty_pairs.values())) > 1:
        reasons.append("보유수량 필드 불일치")

    if primary_qty <= 0 and primary_avg > 0:
        reasons.append("보유 0인데 평단 존재")
    if primary_qty <= 0 and primary_amount > 0:
        reasons.append("보유 0인데 보유금액 존재")
    if primary_qty > 0 and primary_avg <= 0:
        reasons.append("보유 존재인데 평단 없음")

    return unique_review_reasons(reasons)


def restart_initial_review_reason_for_stock(
    stock_dir: Path,
    state: dict[str, object],
) -> tuple[bool, str, dict[str, object]]:
    """프로그램 가동 전 재시작 초기검사 기준.

    재시작은 운영 전 리셋 단계이므로 데이터 불일치뿐 아니라
    정상 보유/미체결도 자동매매 대상에서 제외하고 검토관리로 보낸다.
    """
    if not isinstance(state, dict):
        return True, "재시작 시 state.json 형식 이상", {
            "holding_qty": 0,
            "avg_price": 0.0,
            "holding_amount": 0.0,
            "buy_pending_qty": "?",
            "sell_pending_qty": "?",
        }

    def numeric_state_value(keys: list[str], default: float = 0.0) -> float:
        for key in keys:
            if key not in state or state.get(key) in (None, ""):
                continue
            try:
                value = state.get(key)
                if isinstance(value, str):
                    value = value.replace(",", "").strip()
                return float(value)
            except Exception:
                return default
        return default

    qty_keys = [
        "holding_qty",
        "current_qty",
        "current_quantity",
        "qty",
        "balance_qty",
        "position_qty",
    ]
    amount_keys = [
        "holding_amount",
        "holding_value",
        "holding_eval_amount",
        "position_amount",
        "stock_value",
    ]
    avg_keys = [
        "avg_price",
        "average_price",
        "avg_buy_price",
        "buy_avg_price",
        "average_buy_price",
    ]

    holding_qty = int(numeric_state_value(qty_keys, 0.0))
    avg_price = numeric_state_value(avg_keys, 0.0)
    holding_amount = numeric_state_value(amount_keys, 0.0)
    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

    details = {
        "holding_qty": holding_qty,
        "avg_price": avg_price,
        "holding_amount": holding_amount,
        "buy_pending_qty": buy_pending_qty,
        "sell_pending_qty": sell_pending_qty,
    }

    data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
    if data_reasons:
        return True, "재시작 시 " + data_reasons[0], details

    # 재시작은 프로그램 가동 전 안전 리셋 단계다.
    # 데이터가 서로 일치하더라도 보유/보유금액/미체결이 남아 있으면 자동복구하지 않는다.
    if holding_qty > 0:
        return True, "재시작 시 보유잔량 존재", details
    if holding_amount > 0:
        return True, "재시작 시 보유금액 존재", details
    if avg_price > 0:
        return True, "재시작 시 평단 잔존", details
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return True, "재시작 시 미체결 매수 존재", details
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return True, "재시작 시 미체결 매도 존재", details
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return True, "재시작 시 미체결 수량 확인 필요", details

    return False, "재시작 초기검사 정상", details


def auto_trade_setting_server_mismatch_detected(state: dict[str, object] | None) -> bool:
    """키움 서버 정보와 프로그램 내부 정보 불일치/서버 불안 표시 여부.

    실제 키움 연동 단계에서 아래 플래그 중 하나가 저장되면 현황을 빨강으로 표시한다.
    빨강은 자동 검토관리 이동이 아니라 즉시 운영정지/안정성검사 대상이라는 뜻이다.
    """
    if not isinstance(state, dict):
        return False

    if auto_trade_setting_data_inconsistency_reasons(state):
        return True

    bool_keys = {
        "server_mismatch",
        "kiwoom_mismatch",
        "server_data_mismatch",
        "kiwoom_data_mismatch",
        "data_mismatch",
        "server_unstable",
        "kiwoom_server_unstable",
    }
    for key in bool_keys:
        value = state.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().lower() in {"true", "1", "yes", "y", "on"}:
            return True

    status_keys = {
        "kiwoom_sync_status",
        "server_sync_status",
        "reconciliation_status",
        "server_status",
    }
    danger_values = {"MISMATCH", "UNSTABLE", "ERROR", "FAILED", "FAIL", "UNKNOWN"}
    for key in status_keys:
        if str(state.get(key, "")).strip().upper() in danger_values:
            return True

    return False

