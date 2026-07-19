# -*- coding: utf-8 -*-
"""
gui_auto_trade_display.py

자동매매 설정/관제 표시 전용 유틸리티.
- 현황 점
- 상태 표시
- 상태 색상
- 방식/청산 비활성 스타일

주의:
- 상태 변경, 청산정책 계산, ATS 판정 로직은 포함하지 않는다.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QTableWidgetItem, QWidget

SORT_ROLE = Qt.UserRole + 100


class SortableTableWidgetItem(QTableWidgetItem):
    """화면 표시값과 정렬 기준값을 분리하는 표 아이템."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            try:
                return left < right
            except Exception:
                return str(left) < str(right)
        return self.text() < (other.text() if other is not None else "")


ROUTINE_PROFIT_SIGNAL_COLORS = {
    "LOSS": "#dc2626",
    "COST_NOT_RECOVERED": "#d97706",
    "NET_PROFIT": "#16a34a",
    "NEUTRAL": "#9ca3af",
}


def format_routine_buy_limit(
    *,
    enabled: bool,
    amount: object = None,
) -> str:
    """루틴 매수한도를 독립 금액 셀용 문자열로 만든다."""
    if not enabled:
        return "-"

    try:
        limit_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "-"

    if limit_value <= 0:
        return "-"

    return f"₩{limit_value:,}"


def format_routine_used_amount(amount: object = None) -> str:
    """루틴의 공식 사용금액이 공급된 경우 원화 형식으로 표시한다."""
    if amount in (None, "", "-"):
        return "-"

    try:
        amount_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "-"

    return f"₩{amount_value:,}"


def format_routine_buy_limit_usage(
    *,
    enabled: bool,
    limit_amount: object = None,
    used_amount: object = None,
) -> str:
    """사용금액이 매수한도에서 차지하는 비율을 독립 셀로 표시한다."""
    if not enabled:
        return "-"

    try:
        limit_value = float(str(limit_amount).replace(",", "").strip())
        used_value = float(str(used_amount).replace(",", "").strip())
    except (TypeError, ValueError):
        return "-"

    if limit_value <= 0:
        return "-"

    usage_rate = (used_value / limit_value) * 100.0
    if usage_rate.is_integer():
        return f"{int(usage_rate)}%"
    return f"{usage_rate:.2f}%"


def routine_profit_signal(
    gross_rate: object = None,
    net_rate: object = None,
) -> tuple[str, str, str]:
    """수익률 표시값과 비용 반영 상태를 신호등 계약으로 변환한다."""
    try:
        gross_value = float(gross_rate)
    except (TypeError, ValueError):
        return "NEUTRAL", "-", ROUTINE_PROFIT_SIGNAL_COLORS["NEUTRAL"]

    display_text = f"{gross_value:+.2f}%" if gross_value != 0 else "0.00%"

    if gross_value < 0:
        signal = "LOSS"
    elif gross_value == 0:
        signal = "NEUTRAL"
    else:
        try:
            net_value = float(net_rate)
        except (TypeError, ValueError):
            signal = "NEUTRAL"
        else:
            signal = "NET_PROFIT" if net_value > 0 else "COST_NOT_RECOVERED"

    return signal, display_text, ROUTINE_PROFIT_SIGNAL_COLORS[signal]


def create_routine_profit_signal_widget(
    gross_rate: object = None,
    net_rate: object = None,
) -> QWidget:
    """숫자 글자색은 유지하고 원형 점에만 신호색을 적용한다."""
    signal, display_text, color = routine_profit_signal(gross_rate, net_rate)

    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.setSpacing(5)
    layout.setAlignment(Qt.AlignCenter)

    value_label = QLabel(display_text)
    value_label.setObjectName("routineProfitSignalValue")

    if display_text != "-":
        dot_label = QLabel("●")
        dot_label.setObjectName("routineProfitSignalDot")
        dot_label.setAlignment(Qt.AlignCenter)
        dot_label.setStyleSheet(f"color: {color}; font-size: 11pt;")
        dot_label.setProperty("signal", signal)
        layout.addWidget(dot_label)
    layout.addWidget(value_label)
    return widget


from state_policy import (
    auto_trade_status_color,
    auto_trade_status_display,
    auto_trade_status_dot,
)


def create_auto_trade_status_item(display_status: str) -> QTableWidgetItem:
    """
    상태 컬럼 표시용 아이템.
    내부 상태코드는 GUI 표시명으로 변환해 보여준다.
    SELL_ONLY도 화면에서는 감시/매도로 표시한다.
    """
    normalized_status = display_status_text_for_gui(display_status)

    item = SortableTableWidgetItem(f"{auto_trade_status_dot(normalized_status)} {normalized_status}")
    item.setToolTip(normalized_status)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setForeground(QColor(auto_trade_status_color(normalized_status)))
    return item


def auto_trade_setting_display_status(display_status: str) -> str:
    """자동매매설정창 표시용 상태명.

    이 창은 운영 가능 종목의 설정/현황을 보는 곳이므로
    기존 감시/매도 표시를 운영자 기준의 자동마감으로 보여준다.
    """
    normalized = display_status_text_for_gui(display_status)
    if normalized == "감시/매도":
        return "자동마감"
    return normalized


def auto_trade_setting_status_color(display_status: str) -> str:
    """자동매매설정창 상태 컬럼 색상."""
    normalized = str(display_status or "").strip()
    color_map = {
        "감시/대기": "#2563eb",
        "매수/매도": "#16a34a",
        "자동마감": "#7c3aed",
        "조기마감": "#ea580c",
    }
    return color_map.get(normalized, auto_trade_status_color(normalized))


def create_auto_trade_setting_status_item(display_status: str) -> QTableWidgetItem:
    """자동매매설정창 전용 상태 아이템. 상태 컬럼에는 점을 표시하지 않는다."""
    normalized = auto_trade_setting_display_status(display_status)
    item = SortableTableWidgetItem(normalized)
    item.setToolTip(normalized)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setForeground(QColor(auto_trade_setting_status_color(normalized)))
    return item




def apply_auto_trade_setting_activity_style(item: QTableWidgetItem, active: bool) -> None:
    """정보는 유지하고 현재 주도권 없는 칸만 회색으로 표시한다."""
    if active:
        item.setBackground(QColor("#FFFFFF"))
        return
    item.setBackground(QColor("#F4F5F7"))
    item.setForeground(QColor("#AFB2B9"))


def apply_auto_trade_setting_liquidation_style(
    item: QTableWidgetItem,
    active: bool,
    has_policy: bool = True,
    is_individual: bool = False,
) -> None:
    """청산정책 표시 스타일.

    - 개별 청산정책: 배경/굵게 없이 글자색만 주황
    - 환경설정 청산정책 활성: 기존 연노랑 강조
    - 환경설정 청산정책 비활성: 기존 회색
    - 청산정책 자체가 없는 종목('-'): 기본 흰색 유지
    """
    if not has_policy:
        item.setBackground(QColor("#FFFFFF"))
        return

    if is_individual:
        if active:
            item.setBackground(QColor("#FFFFFF"))
        else:
            item.setBackground(QColor("#F4F5F7"))
        item.setForeground(QColor("#D97706"))
        return

    if active:
        item.setBackground(QColor("#FFFFFF"))
        item.setForeground(QColor("#5C4300"))
        return

    item.setBackground(QColor("#F4F5F7"))
    item.setForeground(QColor("#9CA3AF"))


def yes_no_display(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"

    text_value = str(value).strip().lower()
    if text_value in ("true", "1", "yes", "y"):
        return "예"

    return "아니오"


def display_status_text_for_gui(raw_status: object) -> str:
    """GUI 표시용 상태명. state_policy 기준 6개 표시 상태로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def routine_status_display_text(raw_status: object) -> str:
    """루틴/리포트용 운영상태 표시명을 state_policy 기준으로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def routine_status_display_text(routine_name: str, status: str) -> str:
    """
    루틴별 상태 표시 문구를 반환한다.

    v20.9.1a:
    - 등록 루틴 컬럼에서도 감시중/운영중/매도만 등 상태 차이를 숨기지 않는다.
    - 대기 상태만 삭제보호용 표시 목적에 맞춰 등록대기로 표시한다.
    """
    normalized = str(status or "").strip()

    if normalized in ("운영", "운영중"):
        return f"{routine_name}(운영중)"

    if normalized == "대기":
        return f"{routine_name}(등록대기)"

    if normalized:
        return f"{routine_name}({normalized})"

    return f"{routine_name}(상태없음)"
