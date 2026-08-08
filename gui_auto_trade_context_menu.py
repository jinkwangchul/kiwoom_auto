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

from gui_auto_trade_integrity import is_emergency_stopped_state
from gui_operation_environment import OPERATION_POLICY_PATH
from runtime_io import read_json_dict


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
    start: Callable[[], None] | None = None
    unregister: Callable[[], None] | None = None
    stock_register: Callable[[], None] | None = None
    time_change: Callable[[], None] | None = None
    time_reset: Callable[[], None] | None = None
    ats_settings: Callable[[], None] | None = None
    set_operation_exclusion: Callable[[], None] | None = None
    clear_operation_exclusion: Callable[[], None] | None = None


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


def _selected_emergency_state(selected: list[tuple[object, str, str]]) -> tuple[bool, bool]:
    has_emergency = False
    has_non_emergency = False
    for stock_dir, _code, _name in selected:
        state = read_json_dict(stock_dir / "state.json")
        if is_emergency_stopped_state(state):
            has_emergency = True
        else:
            has_non_emergency = True
    return has_emergency, has_non_emergency


def _stock_register_context_instance_metadata(window) -> dict[str, object] | None:
    if bool(getattr(window, "_all_stocks_scope_active", False)):
        return None
    metadata_getter = getattr(window, "current_selected_routine_row_metadata", None)
    if not callable(metadata_getter):
        return None
    metadata = metadata_getter()
    if not isinstance(metadata, dict):
        return None
    row_kind = str(metadata.get("row_kind", "") or "")
    if row_kind not in {"instance", "stock"}:
        return None

    instance_id = str(metadata.get("instance_id", "") or "").strip()
    if not instance_id:
        return None
    target = {
        "row_kind": "instance",
        "definition_id": str(metadata.get("definition_id", "") or "").strip(),
        "definition_name": str(metadata.get("definition_name", "") or "").strip(),
        "instance_id": instance_id,
        "instance_name": str(metadata.get("instance_name", "") or "").strip(),
    }
    return target


def _stock_register_context_action_visible(window) -> bool:
    return _stock_register_context_instance_metadata(window) is not None


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
    selected_modes: set[str] | None = None,
    operation_excluded: bool = False,
) -> None:
    """Show the monitoring stock-row profile with the shared menu form."""

    menu = _new_stock_context_menu(parent)
    operation_policy = _context_menu_operation_policy()

    action_stock_register = None
    if callbacks.stock_register is not None:
        action_stock_register = menu.addAction("종목등록")
        action_stock_register.setEnabled(has_selection)

    action_start = None
    if callbacks.start is not None:
        action_start = menu.addAction("운영시작")
        action_start.setEnabled(has_selection)
        menu.addSeparator()
    action_select_all = menu.addAction("전체선택")
    action_clear_selection = menu.addAction("전체해제")

    action_set_exclusion = None
    action_clear_exclusion = None
    if operation_excluded and callbacks.clear_operation_exclusion is not None:
        action_clear_exclusion = menu.addAction("제외해제")
        action_clear_exclusion.setEnabled(has_selection)
    elif callbacks.set_operation_exclusion is not None:
        action_set_exclusion = menu.addAction("운영제외")
        action_set_exclusion.setEnabled(has_selection)

    action_unregister = None
    if callbacks.unregister is not None:
        action_unregister = menu.addAction("등록해제")
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
    selected_modes = set(selected_modes or ())
    if selected_modes == {"SCHEDULED"}:
        menu.addSeparator()
        action_time_change = menu.addAction("시간변경")
        action_time_reset = menu.addAction("변경리셋")
    elif selected_modes == {"CONTINUOUS"}:
        menu.addSeparator()
        action_ats_settings = menu.addAction("ATS설정")

    chosen = menu.exec_(global_pos)
    if chosen is None:
        return
    if action_start is not None and chosen == action_start:
        callbacks.start()
    elif action_stock_register is not None and chosen == action_stock_register:
        callbacks.stock_register()
    elif chosen == action_select_all:
        callbacks.select_all()
    elif chosen == action_clear_selection:
        callbacks.clear_selection()
    elif action_set_exclusion is not None and chosen == action_set_exclusion:
        callbacks.set_operation_exclusion()
    elif action_clear_exclusion is not None and chosen == action_clear_exclusion:
        callbacks.clear_operation_exclusion()
    elif action_unregister is not None and chosen == action_unregister:
        callbacks.unregister()
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
    elif action_time_change is not None and chosen == action_time_change:
        if callbacks.time_change is not None:
            callbacks.time_change()
    elif action_time_reset is not None and chosen == action_time_reset:
        if callbacks.time_reset is not None:
            callbacks.time_reset()
    elif action_ats_settings is not None and chosen == action_ats_settings:
        if callbacks.ats_settings is not None:
            callbacks.ats_settings()
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
    has_emergency, has_non_emergency = _selected_emergency_state(selected)
    status_filter = str(getattr(window, "_stock_status_filter", "") or "").strip().lower()
    excluded_view = status_filter == "excluded"
    running_view = status_filter in {"running", "stopped"}

    menu = _new_stock_context_menu(window)
    operation_policy = _context_menu_operation_policy()

    action_start = menu.addAction("운영시작")
    action_start.setEnabled(has_selection)
    action_emergency_stop = None
    if has_non_emergency:
        action_emergency_stop = menu.addAction("긴급정지")
        action_emergency_stop.setEnabled(has_selection)
    action_emergency_release = None
    if has_emergency:
        action_emergency_release = menu.addAction("정지해제")
        action_emergency_release.setEnabled(has_selection)
    menu.addSeparator()
    action_select_all = menu.addAction("전체선택")
    action_clear_selection = menu.addAction("전체해제")
    action_set_exclusion = None
    action_unregister = None
    action_clear_exclusion = None
    if excluded_view:
        action_clear_exclusion = menu.addAction("제외해제")
        action_clear_exclusion.setEnabled(has_selection)
    else:
        if running_view:
            action_set_exclusion = menu.addAction("운영제외")
            action_set_exclusion.setEnabled(has_selection)
        action_unregister = menu.addAction("등록해제")
        action_unregister.setEnabled(has_selection)
    action_stock_register = None
    stock_register_target = _stock_register_context_instance_metadata(window)
    if not excluded_view and stock_register_target is not None:
        action_stock_register = menu.addAction("종목등록")

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

    if selected_modes == {"SCHEDULED"}:
        menu.addSeparator()
        action_time_change = menu.addAction("시간변경")
        action_time_reset = menu.addAction("변경리셋")
    elif selected_modes == {"CONTINUOUS"}:
        menu.addSeparator()
        action_ats_settings = menu.addAction("ATS설정")

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
    elif action_emergency_stop is not None and chosen == action_emergency_stop:
        window.emergency_stop_selected_auto_trade_stocks()
    elif action_emergency_release is not None and chosen == action_emergency_release:
        window.release_selected_emergency_stopped_auto_trade_stocks()
    elif chosen == action_select_all:
        window.select_all_current_routine_stocks()
    elif chosen == action_clear_selection:
        window.clear_current_routine_stock_selection()
    elif action_set_exclusion is not None and chosen == action_set_exclusion:
        window.set_selected_stock_operation_exclusions()
    elif action_unregister is not None and chosen == action_unregister:
        window.unregister_selected_auto_trade_stocks()
    elif action_clear_exclusion is not None and chosen == action_clear_exclusion:
        window.clear_selected_stock_operation_exclusions()
    elif action_stock_register is not None and chosen == action_stock_register:
        window.open_instance_stock_search_register_window(stock_register_target)
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
