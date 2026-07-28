# -*- coding: utf-8 -*-
"""
gui_auto_trade_context_menu.py

자동매매설정창 종목 테이블 우클릭 메뉴 처리.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

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

_INDIVIDUAL_LIQUIDATION_MENU_LABELS = {
    "시장가": "시장가",
    "현재가": "현재가",
    "이월": "이월",
}

_INDIVIDUAL_LIQUIDATION_MINUTES = (
    "1",
    "3",
    "5",
    "10",
    "15",
    "20",
    "30",
)


@dataclass(frozen=True)
class StockContextMenuCallbacks:
    select_all: Callable[[], None]
    clear_selection: Callable[[], None]
    early_close: Callable[[str], None]
    early_close_profit_loss: Callable[[], None]
    early_close_cancel: Callable[[], None]
    individual_liquidation: Callable[[str, str], None]


class _MenuStatusIconEngine(QIconEngine):
    def __init__(self, selected: bool) -> None:
        super().__init__()
        self._selected = bool(selected)

    def clone(self):
        return _MenuStatusIconEngine(self._selected)

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


def _menu_status_icon(selected: bool) -> QIcon:
    return QIcon(_MenuStatusIconEngine(selected))


def _context_menu_operation_policy() -> dict[str, object]:
    try:
        policy = json.loads(
            OPERATION_POLICY_PATH.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(policy, dict):
        return {}
    return policy


def _selected_policy_menu_label(
    policy: dict[str, object],
    section_name: str,
    labels: dict[str, str],
) -> str:
    section = policy.get(section_name)
    if not isinstance(section, dict):
        return ""
    method = str(section.get("method", "")).strip()
    return labels.get(method, "")


def _apply_menu_status(
    actions: tuple[tuple[str, object], ...],
    selected_label: str,
    property_name: str,
) -> None:
    for label, action in actions:
        is_selected = label == selected_label
        action.setText(label)
        action.setIcon(_menu_status_icon(is_selected))
        action.setProperty(property_name, is_selected)


def _new_stock_context_menu(parent) -> QMenu:
    menu = QMenu(parent)
    set_tooltips_visible = getattr(menu, "setToolTipsVisible", None)
    if callable(set_tooltips_visible):
        set_tooltips_visible(True)
    return menu


def _add_early_close_menu(
    menu: QMenu,
    *,
    has_selection: bool,
    operation_policy: dict[str, object],
):
    early_close_menu = menu.addMenu("조기마감")
    action_early_routine = early_close_menu.addAction("루틴마감")
    action_early_market = early_close_menu.addAction("시장가")
    action_early_current = early_close_menu.addAction("현재가")
    action_early_profit_loss = early_close_menu.addAction("손/익절")
    action_early_carry = early_close_menu.addAction("이월")
    early_close_menu.addSeparator()
    action_early_cancel = early_close_menu.addAction("취소")
    early_close_menu.setEnabled(has_selection)
    _apply_menu_status(
        (
            ("루틴마감", action_early_routine),
            ("시장가", action_early_market),
            ("현재가", action_early_current),
            ("손/익절", action_early_profit_loss),
            ("이월", action_early_carry),
            ("취소", action_early_cancel),
        ),
        _selected_policy_menu_label(
            operation_policy,
            "early_close",
            _EARLY_CLOSE_MENU_LABELS,
        ),
        "earlyCloseCurrent",
    )
    return {
        "routine": action_early_routine,
        "market": action_early_market,
        "current": action_early_current,
        "profit_loss": action_early_profit_loss,
        "carry": action_early_carry,
        "cancel": action_early_cancel,
        "menu": early_close_menu,
    }


def _dispatch_early_close_action(
    chosen,
    actions: dict[str, object],
    *,
    apply_method: Callable[[str], None],
    apply_profit_loss: Callable[[], None],
    cancel: Callable[[], None],
) -> bool:
    if chosen == actions["routine"]:
        apply_method("루틴")
    elif chosen == actions["market"]:
        apply_method("시장가즉시")
    elif chosen == actions["current"]:
        apply_method("현재가즉시")
    elif chosen == actions["profit_loss"]:
        apply_profit_loss()
    elif chosen == actions["carry"]:
        apply_method("이월")
    elif chosen == actions["cancel"]:
        cancel()
    else:
        return False
    return True


def _add_individual_liquidation_menu(
    menu: QMenu,
    *,
    has_selection: bool,
    operation_policy: dict[str, object],
):
    individual_liquidation_menu = menu.addMenu("개별청산")
    action_individual_market = individual_liquidation_menu.addAction("시장가")
    action_individual_current = individual_liquidation_menu.addAction("현재가")
    action_individual_carry = individual_liquidation_menu.addAction("이월")
    individual_liquidation_menu.setEnabled(has_selection)
    _apply_menu_status(
        (
            ("시장가", action_individual_market),
            ("현재가", action_individual_current),
            ("이월", action_individual_carry),
        ),
        _selected_policy_menu_label(
            operation_policy,
            "liquidation",
            _INDIVIDUAL_LIQUIDATION_MENU_LABELS,
        ),
        "individualLiquidationCurrent",
    )
    individual_policy = operation_policy.get("liquidation", {})
    if not isinstance(individual_policy, dict):
        individual_policy = {}
    individual_minutes = (
        str(
            individual_policy.get(
                "minutes_before_regular_close",
                "5",
            )
        ).strip()
        or "5"
    )
    individual_method = _selected_policy_menu_label(
        operation_policy,
        "liquidation",
        _INDIVIDUAL_LIQUIDATION_MENU_LABELS,
    ) or "이월"

    individual_liquidation_menu.addSeparator()
    individual_time_menu = individual_liquidation_menu.addMenu("시간")
    minute_values = list(_INDIVIDUAL_LIQUIDATION_MINUTES)
    if individual_minutes not in minute_values:
        minute_values.append(individual_minutes)
    individual_time_actions = tuple(
        (
            minute,
            individual_time_menu.addAction(f"{minute}분"),
        )
        for minute in minute_values
    )
    _apply_menu_status(
        tuple(
            (f"{minute}분", action)
            for minute, action in individual_time_actions
        ),
        f"{individual_minutes}분",
        "individualLiquidationMinutesCurrent",
    )
    individual_time_menu.setEnabled(
        has_selection and individual_method != "이월"
    )
    return {
        "market": action_individual_market,
        "current": action_individual_current,
        "carry": action_individual_carry,
        "time_actions": individual_time_actions,
        "method": individual_method,
        "minutes": individual_minutes,
        "time_menu": individual_time_menu,
    }


def show_monitor_stock_context_menu(
    parent,
    global_pos,
    *,
    has_selection: bool,
    callbacks: StockContextMenuCallbacks,
) -> None:
    """Show the monitoring stock-row profile with the shared menu form."""

    menu = _new_stock_context_menu(parent)
    operation_policy = _context_menu_operation_policy()

    action_select_all = menu.addAction("전체 선택")
    action_clear_selection = menu.addAction("전체 해제")

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

    chosen = menu.exec_(global_pos)
    if chosen is None:
        return
    if chosen == action_select_all:
        callbacks.select_all()
    elif chosen == action_clear_selection:
        callbacks.clear_selection()
    elif _dispatch_early_close_action(
        chosen,
        early_close,
        apply_method=callbacks.early_close,
        apply_profit_loss=callbacks.early_close_profit_loss,
        cancel=callbacks.early_close_cancel,
    ):
        return
    elif chosen == individual["market"]:
        callbacks.individual_liquidation("시장가", individual["minutes"])
    elif chosen == individual["current"]:
        callbacks.individual_liquidation("현재가", individual["minutes"])
    elif chosen == individual["carry"]:
        callbacks.individual_liquidation("이월", individual["minutes"])
    else:
        for minute, action in individual["time_actions"]:
            if chosen == action:
                callbacks.individual_liquidation(individual["method"], minute)
                return


def show_auto_trade_stock_context_menu(window, pos) -> None:
    """하단 종목표 우클릭 메뉴.

    조기마감과 개별청산은 환경설정의 현재 방식을 표시하고,
    선택한 항목은 기존 실행·저장 경로로 전달한다.
    """
    item = window.stock_table.itemAt(pos)
    if item is not None:
        window.ensure_context_row_selected(item.row())

    selected = window.selected_stock_infos()
    has_selection = bool(selected)
    selected_modes = window.selected_operation_mode_set(selected)

    menu = _new_stock_context_menu(window)
    operation_policy = _context_menu_operation_policy()

    action_start = menu.addAction("운영시작")
    action_start.setEnabled(has_selection)
    menu.addSeparator()
    action_select_all = menu.addAction("전체 선택")
    action_clear_selection = menu.addAction("전체 해제")
    action_unregister = menu.addAction("등록 해제")
    action_unregister.setEnabled(has_selection)

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

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

    for minute, action in individual["time_actions"]:
        if chosen == action:
            window.apply_selected_individual_liquidation_method(
                individual["method"],
                minute,
            )
            return

    if chosen == action_start:
        window.start_selected_rows_auto_trades()
    elif chosen == action_select_all:
        window.select_all_current_routine_stocks()
    elif chosen == action_clear_selection:
        window.clear_current_routine_stock_selection()
    elif chosen == action_unregister:
        window.unregister_selected_auto_trade_stocks()
    elif chosen == individual["market"]:
        window.apply_selected_individual_liquidation_method(
            "시장가",
            individual["minutes"],
        )
    elif chosen == individual["current"]:
        window.apply_selected_individual_liquidation_method(
            "현재가",
            individual["minutes"],
        )
    elif chosen == individual["carry"]:
        window.apply_selected_individual_liquidation_method(
            "이월",
            individual["minutes"],
        )
    elif _dispatch_early_close_action(
        chosen,
        early_close,
        apply_method=lambda method: window.apply_selected_early_close(
            method,
            source="우클릭",
        ),
        apply_profit_loss=window.apply_selected_early_close_profit_loss,
        cancel=window.cancel_selected_early_close,
    ):
        return
    elif action_time_change is not None and chosen == action_time_change:
        window.set_selected_individual_schedule_time()
    elif action_time_reset is not None and chosen == action_time_reset:
        window.reset_selected_schedule_to_global()
    elif action_ats_settings is not None and chosen == action_ats_settings:
        window.open_selected_manual_ats_settings_dialog()
