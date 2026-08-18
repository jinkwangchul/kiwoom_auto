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

import json
from pathlib import Path

from gui_order_utils import (
    format_number_value,
    pending_order_side_quantities,
)
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display

PENDING_INTEGRITY_USER_REASON = "처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."
REVIEW_REASON_OPERATION_DATA_MISSING = "운영 데이터 없음"
REVIEW_REASON_OPERATION_DATA_READ_ERROR = "운영 데이터 읽기 오류"
REVIEW_REASON_OPERATION_DATA_MISMATCH = "운영 데이터 불일치"
REVIEW_REASON_PENDING_ORDER_DATA_ERROR = "미체결 데이터 오류"
REVIEW_REASON_LIQUIDATION_REMAINS = "청산 후 보유잔량"
REVIEW_REASON_LIQUIDATION_PROCESSING_ERROR = "청산 처리 오류"
REVIEW_REASON_RECOVERY_STATE_ERROR = "복구 상태 오류"

_REVIEW_LOCATION_DISPLAY_BY_SOURCE = {
    "운영시작": "운영 시작",
    "운영 시작": "운영 시작",
    "운영중": "운영 중",
    "운영 중": "운영 중",
    "안정성검사": "안정성 검사",
    "무결성검사": "안정성 검사",
    "안정성 검사": "안정성 검사",
    "긴급정지해제": "긴급정지 해제",
    "긴급정지 해제": "긴급정지 해제",
    "사용자 긴급정지": "전체 긴급정지",
    "종목 우클릭 긴급정지": "종목 긴급정지",
    "종목 우클릭 검토정지": "종목 검토정지",
    "강제종료": "운영 종료",
    "종목등록 창 미체결 데이터 무결성 오류": "종목 등록",
    "등록해제 미체결 데이터 무결성 오류": "종목 해제",
    "루틴 이동 미체결 데이터 무결성 오류": "루틴 등록",
    "루틴 해제 미체결 데이터 무결성 오류": "루틴 해제",
    "PRODUCTION_RECOVERY": "프로그램 시작",
    "종목관리": "종목관리",
}

_OPERATION_DATA_MISMATCH_TEXTS = {
    "state.json 형식 이상",
    "state.json 이상",
    "config.json 이상",
    "orders.json 누락",
    "보유수량 필드 불일치",
    "보유 0인데 평단 존재",
    "보유 0인데 보유금액 존재",
    "보유 존재인데 평단 없음",
    "SERVER_MISMATCH",
}


def operator_review_location(source: object, *, default: str = "미기록") -> str:
    """Map a producer source to the stable operator-facing detection point."""
    raw = str(source or "").strip()
    if not raw or raw == "-":
        return default
    return _REVIEW_LOCATION_DISPLAY_BY_SOURCE.get(raw, raw)


def operator_review_reason(reason: object, *, default: str = "-") -> str:
    """Return a short operator cause tag while retaining evidence at its source."""
    raw = str(reason or "").strip()
    if not raw or raw == "-":
        return default
    if "PENDING_ORDER_DATA_INTEGRITY" in raw:
        return REVIEW_REASON_PENDING_ORDER_DATA_ERROR
    if raw in _OPERATION_DATA_MISMATCH_TEXTS or any(
        text in raw for text in _OPERATION_DATA_MISMATCH_TEXTS
    ):
        return REVIEW_REASON_OPERATION_DATA_MISMATCH
    if raw.startswith("[") and "]" in raw:
        return REVIEW_REASON_OPERATION_DATA_MISMATCH
    if " 숫자 형식 오류" in raw or raw.endswith(" 음수"):
        return REVIEW_REASON_OPERATION_DATA_MISMATCH
    if raw in {"EARLY_CLOSE_EXECUTION_FAILED", "EVIDENCE_CONFLICT"}:
        return REVIEW_REASON_LIQUIDATION_PROCESSING_ERROR
    if raw in {"HOLDING_REMAINS", "LIQUIDATION_HOLDING_REMAINS"}:
        return REVIEW_REASON_LIQUIDATION_REMAINS
    if raw == "USER_EMERGENCY_STOP":
        return "사용자 긴급정지"
    if raw == "USER_REVIEW_STOP":
        return "사용자 검토정지"
    if raw == "PENDING_ORDER":
        return "미체결 주문 존재"
    if raw == "PENDING_CANCEL":
        return "주문 취소 처리 중"
    if raw.startswith("RECOVERY_"):
        return REVIEW_REASON_RECOVERY_STATE_ERROR
    if raw == "ACTIVE_CLOSE_OR_LIQUIDATION":
        return "청산 처리 중"
    return raw
_TRUE_TEXT_VALUES = {"TRUE", "1", "YES", "Y", "ON", "검토", "검토필요"}
_REVIEW_STATUS_VALUES = {
    "PENDING",
    "REVIEW_REQUIRED",
    "NEEDS_REVIEW",
    "검토",
    "검토필요",
}
_EMERGENCY_STOPPED_STATUS_VALUES = {
    "EMERGENCY_STOPPED",
    "EMERGENCY_STOP",
    "EMERGENCY",
}


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

    review_required_value = state.get("review_required", False)
    if isinstance(review_required_value, str):
        if review_required_value.strip().upper() in _TRUE_TEXT_VALUES:
            return True
    elif bool(review_required_value):
        return True

    review_status = str(state.get("review_status", "") or "").strip().upper()
    if review_status in _REVIEW_STATUS_VALUES:
        return True

    try:
        return auto_trade_status_display(raw_status) == "검토종목"
    except Exception:
        return False


def is_emergency_stopped_state(state: dict[str, object] | None) -> bool:
    """Return whether a stock state is in emergency stop, including legacy inputs."""
    if not isinstance(state, dict):
        return False
    raw_status = str(state.get("status", "") or "").strip().upper()
    return raw_status in _EMERGENCY_STOPPED_STATUS_VALUES


def is_operation_excluded(config: dict[str, object] | None) -> bool:
    """Return the operation-excluded flag from config without mutating it."""
    if not isinstance(config, dict):
        return False
    value = config.get("operation_excluded", False)
    if isinstance(value, str):
        return value.strip().upper() in _TRUE_TEXT_VALUES
    return bool(value)


def is_review_required_stock_dir(stock_dir: Path) -> bool:
    """runtime 폴더 기준 검토관리 전용 종목 여부."""
    try:
        state = read_json_dict(stock_dir / "state.json")
    except Exception:
        return False
    return is_review_required_state(state)


def read_review_state_with_issue(state_path: Path) -> tuple[dict[str, object], str]:
    """Read state.json while preserving missing/corrupt review issue reasons."""
    if not state_path.exists():
        return {}, REVIEW_REASON_OPERATION_DATA_MISSING

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, REVIEW_REASON_OPERATION_DATA_READ_ERROR

    if not isinstance(data, dict):
        return {}, REVIEW_REASON_OPERATION_DATA_READ_ERROR

    return data, ""


def is_review_protected_stock_dir(stock_dir: Path) -> bool:
    """Return whether a stock dir is protected by review-required collection rules."""
    state, state_issue_reason = read_review_state_with_issue(stock_dir / "state.json")
    return bool(state_issue_reason) or is_review_required_state(state)


def auto_trade_setting_data_inconsistency_reasons(state: dict[str, object] | None) -> list[str]:
    """운영 중/재시작/자동 로컬 무결성검사 공통 내부 데이터 불일치 판정.

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
        return True, PENDING_INTEGRITY_USER_REASON, details

    return False, "재시작 초기검사 정상", details


def auto_trade_setting_server_mismatch_detected(state: dict[str, object] | None) -> bool:
    """키움 서버 정보와 프로그램 내부 정보 불일치/서버 불안 표시 여부.

    실제 키움 연동 단계에서 아래 플래그 중 하나가 저장되면 현황을 빨강으로 표시한다.
    빨강은 자동 검토관리 이동이 아니라 긴급정지/무결성 확인 대상이라는 뜻이다.
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
