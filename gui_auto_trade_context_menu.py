# -*- coding: utf-8 -*-
"""
gui_auto_trade_context_menu.py

자동매매설정창 종목 테이블 우클릭 메뉴 처리.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PyQt5.QtWidgets import QMenu

from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_operation_excluded,
)
from gui_ats_utils import (
    manual_ats_session_labels,
    manual_ats_visible_session_keys,
)
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
    emergency_stop: Callable[[], None] | None = None
    emergency_release: Callable[[], None] | None = None
    unregister: Callable[[], None] | None = None
    stock_register: Callable[[], None] | None = None
    time_change: Callable[[], None] | None = None
    time_reset: Callable[[], None] | None = None
    ats_state: Callable[[], dict[str, bool]] | None = None
    ats_toggle: Callable[[str, bool, str], None] | None = None
    ats_liquidation_available: Callable[[], bool] | None = None
    ats_liquidation: Callable[
        [str, dict[str, bool], tuple[str, ...], tuple[str, ...]],
        None,
    ] | None = None
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


class _PersistentAtsToggleFilter(QObject):
    """Handle ATS session clicks without closing the surrounding QMenu."""

    def __init__(self, menu: QMenu) -> None:
        super().__init__(menu)
        self._handlers: dict[int, Callable[[], None]] = {}

    def register(self, action, handler: Callable[[], None]) -> None:
        self._handlers[id(action)] = handler

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.MouseButtonRelease:
            return super().eventFilter(watched, event)
        action_at = getattr(watched, "actionAt", None)
        action = action_at(event.pos()) if callable(action_at) else None
        handler = self._handlers.get(id(action))
        if handler is None:
            return super().eventFilter(watched, event)
        handler()
        set_active_action = getattr(watched, "setActiveAction", None)
        if callable(set_active_action):
            set_active_action(action)
        return True


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
    operation_excluded: bool = False,
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
    early_close_menu.setEnabled(has_selection and not operation_excluded)
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


def _add_ats_settings_menu(
    menu: QMenu,
    *,
    has_selection: bool,
    state_getter: Callable[[], dict[str, bool]] | None,
    toggle: Callable[[str, bool, str], None] | None,
    liquidation_available_getter: Callable[[], bool] | None,
):
    visible_keys = manual_ats_visible_session_keys()
    labels = manual_ats_session_labels()
    initial_state = state_getter() if state_getter is not None else {}
    current_state = dict(initial_state) if isinstance(initial_state, dict) else {}
    ats_menu = menu.addMenu("ATS설정")
    ats_menu.setEnabled(has_selection and bool(visible_keys))

    session_actions: list[tuple[str, str, object]] = []
    for key in visible_keys:
        label = str(labels.get(key, key))
        action = ats_menu.addAction(label)
        selected = bool(current_state.get(key, False))
        action.setIcon(_menu_status_icon(selected))
        action.setProperty("atsSessionCurrent", selected)
        action.setProperty("atsSessionKey", key)
        session_actions.append((key, label, action))

    ats_menu.addSeparator()
    action_market = ats_menu.addAction("시장가")
    action_current = ats_menu.addAction("현재가")

    def refresh_liquidation_actions() -> None:
        enabled = bool(
            liquidation_available_getter is not None
            and liquidation_available_getter()
        )
        action_market.setEnabled(enabled)
        action_current.setEnabled(enabled)

    def refresh_session_status() -> None:
        refreshed_value = state_getter() if state_getter is not None else current_state
        refreshed = (
            dict(refreshed_value)
            if isinstance(refreshed_value, dict)
            else dict(current_state)
        )
        current_state.clear()
        current_state.update(refreshed)
        for action_key, _label, action in session_actions:
            selected = bool(current_state.get(action_key, False))
            action.setIcon(_menu_status_icon(selected))
            action.setProperty("atsSessionCurrent", selected)
        refresh_liquidation_actions()

    def toggle_session(key: str, label: str) -> None:
        if toggle is None:
            return
        toggle(key, not bool(current_state.get(key, False)), label)
        refresh_session_status()

    install_event_filter = getattr(ats_menu, "installEventFilter", None)
    if callable(install_event_filter) and isinstance(ats_menu, QObject):
        toggle_filter = _PersistentAtsToggleFilter(ats_menu)
        for key, label, action in session_actions:
            toggle_filter.register(
                action,
                lambda key=key, label=label: toggle_session(key, label),
            )
        install_event_filter(toggle_filter)
        ats_menu._ats_toggle_filter = toggle_filter

    refresh_liquidation_actions()
    return {
        "menu": ats_menu,
        "visible_keys": visible_keys,
        "current_state": current_state,
        "session_actions": tuple(session_actions),
        "toggle_session": toggle_session,
        "market": action_market,
        "current": action_current,
    }


def _dispatch_ats_settings_action(
    chosen,
    actions: dict[str, object],
    *,
    toggle: Callable[[str, bool, str], None] | None,
    liquidate: Callable[
        [str, dict[str, bool], tuple[str, ...], tuple[str, ...]],
        None,
    ] | None,
) -> bool:
    current_state = dict(actions["current_state"])
    for key, label, action in actions["session_actions"]:
        if chosen == action:
            toggle_session = actions.get("toggle_session")
            if callable(toggle_session):
                toggle_session(key, label)
            elif toggle is not None:
                toggle(key, not bool(current_state.get(key, False)), label)
            return True

    method = ""
    if chosen == actions["market"]:
        method = "시장가"
    elif chosen == actions["current"]:
        method = "현재가"
    if not method:
        return False

    if liquidate is not None:
        visible_keys = tuple(actions["visible_keys"])
        selected_sessions = tuple(
            key for key in visible_keys if bool(current_state.get(key, False))
        )
        liquidate(method, current_state, visible_keys, selected_sessions)
    return True


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

    action_start = None
    if callbacks.start is not None:
        action_start = menu.addAction("운영시작")
        action_start.setEnabled(has_selection)
    action_emergency_stop = None
    if callbacks.emergency_stop is not None:
        action_emergency_stop = menu.addAction("긴급정지")
        action_emergency_stop.setEnabled(has_selection)
    action_emergency_release = None
    if callbacks.emergency_release is not None:
        action_emergency_release = menu.addAction("정지해제")
        action_emergency_release.setEnabled(has_selection)
    if action_start is not None:
        menu.addSeparator()
    action_select_all = menu.addAction("전체선택")
    action_clear_selection = menu.addAction("선택해제")

    action_set_exclusion = None
    action_clear_exclusion = None
    if operation_excluded and callbacks.clear_operation_exclusion is not None:
        action_clear_exclusion = menu.addAction("제외해제")
        action_clear_exclusion.setEnabled(has_selection)
    elif callbacks.set_operation_exclusion is not None:
        action_set_exclusion = menu.addAction("운영제외")
        action_set_exclusion.setEnabled(has_selection)

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_excluded=operation_excluded,
        operation_policy=operation_policy,
    )

    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

    action_time_change = None
    action_time_reset = None
    ats_settings = None
    selected_modes = set(selected_modes or ())
    if selected_modes == {"SCHEDULED"}:
        menu.addSeparator()
        action_time_change = menu.addAction("시간변경")
        action_time_reset = menu.addAction("변경리셋")
    elif selected_modes == {"CONTINUOUS"}:
        menu.addSeparator()
        ats_settings = _add_ats_settings_menu(
            menu,
            has_selection=has_selection,
            state_getter=callbacks.ats_state,
            toggle=callbacks.ats_toggle,
            liquidation_available_getter=callbacks.ats_liquidation_available,
        )

    action_stock_register = None
    action_unregister = None
    if callbacks.stock_register is not None or callbacks.unregister is not None:
        menu.addSeparator()
        if callbacks.stock_register is not None:
            action_stock_register = menu.addAction("종목등록")
            action_stock_register.setEnabled(has_selection)
        if callbacks.unregister is not None:
            action_unregister = menu.addAction("등록해제")
            action_unregister.setEnabled(has_selection)

    chosen = menu.exec_(global_pos)
    if chosen is None:
        return
    if action_start is not None and chosen == action_start:
        callbacks.start()
    elif action_emergency_stop is not None and chosen == action_emergency_stop:
        callbacks.emergency_stop()
    elif action_emergency_release is not None and chosen == action_emergency_release:
        callbacks.emergency_release()
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
    elif ats_settings is not None and _dispatch_ats_settings_action(
        chosen,
        ats_settings,
        toggle=callbacks.ats_toggle,
        liquidate=callbacks.ats_liquidation,
    ):
        return
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
    operation_excluded = has_selection and all(
        is_operation_excluded(read_json_dict(stock_dir / "config.json"))
        for stock_dir, _code, _name in selected
    )
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
    action_clear_selection = menu.addAction("선택해제")
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
    action_stock_register = None
    action_unregister = None
    stock_register_target = _stock_register_context_instance_metadata(window)

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_excluded=operation_excluded,
        operation_policy=operation_policy,
    )

    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )

    action_time_change = None
    action_time_reset = None
    ats_settings = None

    if selected_modes == {"SCHEDULED"}:
        menu.addSeparator()
        action_time_change = menu.addAction("시간변경")
        action_time_reset = menu.addAction("변경리셋")
    elif selected_modes == {"CONTINUOUS"}:
        menu.addSeparator()
        ats_settings = _add_ats_settings_menu(
            menu,
            has_selection=has_selection,
            state_getter=lambda: window.selected_manual_ats_state(selected),
            toggle=window.set_selected_manual_ats_flag,
            liquidation_available_getter=lambda: (
                window.selected_manual_ats_liquidation_available(selected)
            ),
        )

    if not excluded_view and (
        stock_register_target is not None or has_selection
    ):
        menu.addSeparator()
        if stock_register_target is not None:
            action_stock_register = menu.addAction("종목등록")
        action_unregister = menu.addAction("등록해제")
        action_unregister.setEnabled(has_selection)

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
    elif ats_settings is not None and _dispatch_ats_settings_action(
        chosen,
        ats_settings,
        toggle=window.set_selected_manual_ats_flag,
        liquidate=lambda method, state, visible_keys, selected_sessions: (
            window.execute_selected_manual_ats_liquidation(
                method,
                state,
                selected,
                visible_keys,
                selected_sessions,
            )
        ),
    ):
        return
