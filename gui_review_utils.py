# -*- coding: utf-8 -*-
"""
gui_review_utils.py

검토필요 종목 산출/표시용 순수 유틸리티.
UI 창에 직접 의존하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from gui_common_utils import safe_int_value
from gui_order_utils import order_current_pending_qty
from runtime_io import read_json_dict, read_orders_data
from state_policy import auto_trade_status_display

def safe_float_value(value: object, default: float = 0.0) -> float:
    """
    GUI 표시 및 검토관리창 계산용 안전 실수 변환.
    """
    if value in (None, "", "-"):
        return default

    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def current_price_from_state(state: dict[str, object]) -> float | None:
    """
    현재가는 향후 키움 현재가 조회 결과가 state.json 에 반영되면 사용한다.

    현재 단계에서는 키움 현재가 조회가 연결되어 있지 않으므로,
    아래 후보 필드가 없으면 확인불가로 처리한다.
    """
    for key in ("current_price", "last_checked_price", "market_price", "price"):
        value = safe_float_value(state.get(key), 0.0)
        if value > 0:
            return value
    return None


def pending_order_summary(stock_dir: Path, state: dict[str, object]) -> tuple[bool, int]:
    """
    현재 미체결 존재 여부와 수량 합계를 반환한다.

    orders.json 에 보존된 과거 매결/도결/체결완료 기록은 판단에서 제외한다.
    """
    pending_qty = 0
    unknown_pending = False

    state_pending_qty = safe_int_value(state.get("pending_qty"), 0)
    if bool(state.get("pending_order", False)) and state_pending_qty > 0:
        pending_qty += state_pending_qty

    for order in read_orders_data(stock_dir / "orders.json"):
        order_pending_qty, unknown = order_current_pending_qty(order)
        pending_qty += order_pending_qty
        unknown_pending = unknown_pending or unknown

    return pending_qty > 0 or unknown_pending, pending_qty


def split_review_reason_text(value: object) -> list[str]:
    """
    review_reason 에 저장된 복합 사유 문자열을 개별 사유로 분리한다.

    과거 버전에서 "A / B / A / B" 형태로 누적 저장된 값이 있을 수 있으므로
    화면 표시와 재저장 시에는 항상 개별 사유 단위로 중복 제거한다.
    """
    text = str(value or "").strip()
    if not text:
        return []

    parts: list[str] = []
    for raw in text.replace("\n", " / ").split("/"):
        part = raw.strip()
        if part:
            parts.append(part)
    return parts


def unique_review_reasons(values: list[object]) -> list[str]:
    """검토사유 목록을 순서 유지 방식으로 정리한다."""
    unique: list[str] = []
    for value in values:
        for reason_text in split_review_reason_text(value):
            if reason_text and reason_text not in unique:
                unique.append(reason_text)
    return unique


def build_review_required_item(
    routine_name: str,
    stock_dir: Path,
    code: str,
    name: str,
    force_reasons: list[str] | None = None,
) -> dict[str, object]:
    """
    검토관리창에 표시할 종목 정보를 구성한다.

    현재가 확인은 향후 키움 API 연동 전까지 state.json 후보 필드만 사용한다.
    보유수량이 있는데 현재가가 없으면 검토필요 사유로 본다.
    """
    state = read_json_dict(stock_dir / "state.json")
    orders = read_orders_data(stock_dir / "orders.json")

    status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    avg_price = safe_float_value(state.get("avg_price"), 0.0)
    buy_count = safe_int_value(state.get("buy_count"), 0)
    missed_buy = safe_int_value(state.get("missed_buy_signal_count"), 0)
    missed_sell = safe_int_value(state.get("missed_sell_signal_count"), 0)
    check_status = str(state.get("pause_signal_check_status", "UNCHECKED")).strip().upper()
    pending_exists, pending_qty = pending_order_summary(stock_dir, state)
    current_price = current_price_from_state(state)

    reasons: list[object] = []
    if force_reasons:
        reasons.extend(reason for reason in force_reasons if str(reason).strip())

    review_reason = str(state.get("review_reason", "")).strip()
    if status == "REVIEW_REQUIRED" and review_reason:
        reasons.extend(split_review_reason_text(review_reason))

    if missed_buy > 0 or missed_sell > 0:
        reasons.append("일시중지 기간 중 매수/매도 신호 발생")
    elif status == "PAUSED" and check_status != "CHECKED":
        reasons.append("일시중지 신호 확인 필요")

    if pending_exists:
        reasons.append("미체결 주문 있음")

    if holding_qty > 0 and current_price is None:
        reasons.append("보유수량 있음 + 현재가 확인 불가")

    if holding_qty > 0 and avg_price <= 0:
        reasons.append("보유수량 있음 + 평균단가 오류")

    if status == "REVIEW_REQUIRED" and not reasons:
        reasons.append("수동 검토 필요")

    # 중복 사유 제거, 순서 유지
    unique_reasons = unique_review_reasons(reasons)

    pnl_text = "-"
    pnl_rate_text = "-"
    if current_price is not None and avg_price > 0 and holding_qty > 0:
        pnl = (current_price - avg_price) * holding_qty
        pnl_rate = ((current_price - avg_price) / avg_price) * 100
        pnl_text = f"{int(pnl):,}"
        pnl_rate_text = f"{pnl_rate:+.2f}%"

    return {
        "routine_name": routine_name,
        "stock_dir": stock_dir,
        "code": code,
        "name": name,
        "status": status,
        "display_status": auto_trade_status_display(status),
        "review_reasons": unique_reasons,
        "review_reason_text": " / ".join(unique_reasons) if unique_reasons else "-",
        "pause_signal_check_status": check_status,
        "holding_qty": holding_qty,
        "avg_price": avg_price,
        "current_price": current_price,
        "current_price_text": f"{int(current_price):,}" if current_price is not None else "확인불가",
        "pnl_text": pnl_text,
        "pnl_rate_text": pnl_rate_text,
        "buy_count": buy_count,
        "pending_exists": pending_exists,
        "pending_qty": pending_qty,
        "missed_buy_signal_count": missed_buy,
        "missed_sell_signal_count": missed_sell,
        "paused_at": str(state.get("paused_at", "")),
        "review_checked_at": str(state.get("review_checked_at", "")),
        "review_status": str(state.get("review_status", "PENDING") or "PENDING"),
        "orders_count": len(orders),
    }


def review_reason_summary(item: dict[str, object]) -> str:
    """
    검토관리창 표의 사유 컬럼에 표시할 짧고 명확한 요약 문구를 만든다.

    상세 사유는 state.json 의 review_reason 과 하단 상세 영역에 유지하고,
    표에서는 한눈에 위험 요소를 읽을 수 있도록 O/X 형태로 표시한다.
    """
    pending_text = "미체결O" if bool(item.get("pending_exists")) else "미체결X"
    current_text = "현재가O" if item.get("current_price") is not None else "현재가X"

    missed_buy = safe_int_value(item.get("missed_buy_signal_count"), 0)
    missed_sell = safe_int_value(item.get("missed_sell_signal_count"), 0)
    check_status = str(item.get("pause_signal_check_status", "CHECKED")).strip().upper()

    if missed_buy > 0 or missed_sell > 0:
        signal_text = "매매신호O"
    elif check_status != "CHECKED" and str(item.get("status", "")).strip().upper() in ("PAUSED", "REVIEW_REQUIRED"):
        signal_text = "매매신호확인X"
    else:
        signal_text = "매매신호X"

    tokens = [pending_text, current_text, signal_text]

    holding_qty = safe_int_value(item.get("holding_qty"), 0)
    avg_price = safe_float_value(item.get("avg_price"), 0.0)
    if holding_qty > 0 and avg_price <= 0:
        tokens.append("평단오류")

    return " / ".join(tokens)


def compact_time_text(value: object) -> str:
    """검토관리창 표에서 일시중지 시각을 HH:MM:SS 중심으로 짧게 표시한다."""
    text = str(value or "").strip()
    if not text:
        return "-"

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%H:%M:%S")
        except Exception:
            pass

    if len(text) >= 19 and text[10] in (" ", "T"):
        return text[11:19]

    if len(text) >= 8 and text[2:3] == ":" and text[5:6] == ":":
        return text[:8]

    return text


def review_required_for_start(item: dict[str, object]) -> bool:
    """
    자동매매 시작 전 사전점검 결과 검토관리창으로 보낼지 판단한다.
    """
    return bool(item.get("review_reasons"))


