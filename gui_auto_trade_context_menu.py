# -*- coding: utf-8 -*-
"""
gui_auto_trade_context_menu.py

자동매매설정창 종목 테이블 우클릭 메뉴 처리.
"""

from __future__ import annotations

import json

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PyQt5.QtWidgets import QMenu

from gui_operation_environment import OPERATION_POLICY_PATH


_EARLY_CLOSE_MENU_LABELS = {
    "루틴매도신호": "루틴마감",
    "시장가": "시장가",
    "현재가": "현재가",
    "익절/손절": "손/익절",
    "이월": "이월",
    "취소": "취소",
}


class _EarlyCloseStatusIconEngine(QIconEngine):
    def __init__(self, selected: bool) -> None:
        super().__init__()
        self._selected = bool(selected)

    def clone(self):
        return _EarlyCloseStatusIconEngine(self._selected)

    def paint(self, painter, rect, mode, state) -> None:
        if not self._selected:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#6B7280"))
        center = rect.center()
        painter.drawEllipse(center, 3, 3)
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def _early_close_status_icon(selected: bool) -> QIcon:
    return QIcon(_EarlyCloseStatusIconEngine(selected))


def _selected_early_close_menu_label() -> str:
    try:
        policy = json.loads(
            OPERATION_POLICY_PATH.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(policy, dict):
        return ""
    early_close = policy.get("early_close")
    if not isinstance(early_close, dict):
        return ""
    method = str(early_close.get("method", "")).strip()
    return _EARLY_CLOSE_MENU_LABELS.get(method, "")


def show_auto_trade_stock_context_menu(window, pos) -> None:
    """하단 종목표 우클릭 메뉴.

    1차 정리 범위:
    - 메뉴 형태만 정리한다.
    - 개별 청산/추가 시간은 자리만 만든다.
    - 실제 저장/청산/비정규운영 판정은 다음 단계에서 연결한다.
    """
    item = window.stock_table.itemAt(pos)
    if item is not None:
        window.ensure_context_row_selected(item.row())

    selected = window.selected_stock_infos()
    has_selection = bool(selected)
    selected_modes = window.selected_operation_mode_set(selected)

    menu = QMenu(window)

    action_select_all = menu.addAction("전체 선택")
    action_clear_selection = menu.addAction("전체 해제")
    action_unregister = menu.addAction("등록 해제")
    action_unregister.setEnabled(has_selection)

    menu.addSeparator()
    early_close_menu = menu.addMenu("조기마감")
    action_early_routine = early_close_menu.addAction("루틴마감")
    action_early_market = early_close_menu.addAction("시장가")
    action_early_current = early_close_menu.addAction("현재가")
    action_early_profit_loss = early_close_menu.addAction("손/익절")
    action_early_carry = early_close_menu.addAction("이월")
    early_close_menu.addSeparator()
    action_early_cancel = early_close_menu.addAction("취소")
    early_close_menu.setEnabled(has_selection)
    selected_early_close_label = _selected_early_close_menu_label()
    for label, action in (
        ("루틴마감", action_early_routine),
        ("시장가", action_early_market),
        ("현재가", action_early_current),
        ("손/익절", action_early_profit_loss),
        ("이월", action_early_carry),
        ("취소", action_early_cancel),
    ):
        is_selected = label == selected_early_close_label
        action.setText(label)
        action.setIcon(_early_close_status_icon(is_selected))
        action.setProperty("earlyCloseCurrent", is_selected)

    action_individual_liquidation = menu.addAction("개별 청산")
    action_individual_liquidation.setEnabled(has_selection)

    action_time_change = None
    action_time_reset = None
    action_ats_settings = None

    menu.addSeparator()
    if not has_selection:
        action_header = menu.addAction("운영방식별 설정: 종목 선택 필요")
        action_header.setEnabled(False)
    elif selected_modes == {"SCHEDULED"}:
        action_time_change = menu.addAction("시간 변경")
        action_time_reset = menu.addAction("변경 리셋")
    elif selected_modes == {"CONTINUOUS"}:
        action_ats_settings = menu.addAction("ATS설정")
    else:
        action_header = menu.addAction("혼합 선택: 공통 메뉴만 사용")
        action_header.setEnabled(False)

    chosen = menu.exec_(window.stock_table.viewport().mapToGlobal(pos))
    if chosen is None:
        return

    if chosen == action_select_all:
        window.select_all_current_routine_stocks()
    elif chosen == action_clear_selection:
        window.clear_current_routine_stock_selection()
    elif chosen == action_unregister:
        window.unregister_selected_auto_trade_stocks()
    elif chosen == action_individual_liquidation:
        window.open_selected_individual_liquidation_settings()
    elif chosen == action_early_routine:
        window.apply_selected_early_close("루틴", source="우클릭")
    elif chosen == action_early_market:
        window.apply_selected_early_close("시장가즉시", source="우클릭")
    elif chosen == action_early_current:
        window.apply_selected_early_close("현재가즉시", source="우클릭")
    elif chosen == action_early_profit_loss:
        window.apply_selected_early_close_profit_loss()
    elif chosen == action_early_carry:
        window.apply_selected_early_close("이월", source="우클릭")
    elif chosen == action_early_cancel:
        window.cancel_selected_early_close()
    elif action_time_change is not None and chosen == action_time_change:
        window.set_selected_individual_schedule_time()
    elif action_time_reset is not None and chosen == action_time_reset:
        window.reset_selected_schedule_to_global()
    elif action_ats_settings is not None and chosen == action_ats_settings:
        window.open_selected_manual_ats_settings_dialog()
