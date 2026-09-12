# -*- coding: utf-8 -*-
"""Domain-neutral Qt primitives shared by Production and Mock operation UIs."""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPalette, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QStyle,
    QStyleOptionMenuItem,
    QVBoxLayout,
    QWidget,
)

from gui_toast import show_toast


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

_INDIVIDUAL_LIQUIDATION_MINUTES = ("1", "3", "5", "10", "15", "20", "30")

CONTEXT_MENU_DANGER_TEXT_COLOR = "#DC2626"
CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR = "#15803D"
CONTEXT_MENU_DISABLED_TEXT_COLOR = "#AFB2B9"
_MENU_TEXT_COLOR_PROPERTY = "menuTextColor"


def ats_session_ui_options(
    operation_policy: dict[str, object] | None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Project ATS session visibility and labels from an injected policy snapshot."""

    keys = ("extra1", "extra2", "extra3")
    labels = {"extra1": "추가1", "extra2": "추가2", "extra3": "추가3"}
    policy = operation_policy if isinstance(operation_policy, dict) else {}
    sessions = policy.get("extra_sessions", [])
    if not isinstance(sessions, list):
        return keys, labels
    visible: list[str] = []
    for index, key in enumerate(keys):
        session = sessions[index] if index < len(sessions) else None
        if isinstance(session, dict):
            name = str(session.get("name", "")).strip()
            if name:
                labels[key] = name
            if not bool(session.get("enabled", True)):
                continue
        visible.append(key)
    return tuple(visible), labels


class PersistentContextMenu(QMenu):
    """Keep one popup instance open while registered leaf actions run."""

    def __init__(self, parent=None, *, persistent_root=None) -> None:
        super().__init__(parent)
        self._persistent_root = persistent_root or self
        if self._persistent_root is self:
            self._persistent_handlers: dict[int, tuple[Callable[[], None], bool]] = {}
            self._persistent_context_invalidated = False

    def addMenu(self, title):
        if isinstance(title, QMenu):
            return super().addMenu(title)
        submenu = PersistentContextMenu(self, persistent_root=self._persistent_root)
        submenu.setTitle(str(title))
        super().addMenu(submenu)
        return submenu

    def register_persistent_action(
        self,
        action,
        handler: Callable[[], None],
        *,
        terminal: bool = False,
    ) -> None:
        root = self._persistent_root
        root._persistent_handlers[id(action)] = (handler, bool(terminal))
        action.setProperty("persistentContextAction", not terminal)
        action.setProperty("terminalContextAction", bool(terminal))

    def _activate_registered_action(self, action) -> bool:
        if action is None or not action.isEnabled() or action.isSeparator():
            return False
        if action.menu() is not None:
            return False
        ancestor: object = self
        while isinstance(ancestor, PersistentContextMenu):
            if not ancestor.isEnabled():
                return False
            if ancestor is self._persistent_root:
                break
            ancestor = ancestor.parentWidget()
        registered = self._persistent_root._persistent_handlers.get(id(action))
        if registered is None:
            return False
        handler, terminal = registered
        root = self._persistent_root
        visible_path: list[tuple[PersistentContextMenu, QPoint, object]] = []
        current: object = self
        while isinstance(current, PersistentContextMenu):
            if current.isVisible():
                visible_path.append((current, QPoint(current.pos()), current.activeAction()))
            if current is root:
                break
            current = current.parentWidget()
        visible_path.reverse()
        root._persistent_context_invalidated = False
        if action.isCheckable():
            action.toggle()
        handler()
        if terminal:
            root.close()
        elif not root._persistent_context_invalidated:
            for popup, position, active_action in visible_path:
                if not popup.isVisible():
                    popup.move(position)
                    popup.show()
                if active_action is not None:
                    popup.setActiveAction(active_action)
            self.setActiveAction(action)
        return True

    def invalidate_persistent_context(self) -> None:
        root = self._persistent_root
        root._persistent_context_invalidated = True
        root.close()

    def mouseReleaseEvent(self, event) -> None:
        if self._activate_registered_action(self.actionAt(event.pos())):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if self._activate_registered_action(self.activeAction()):
                event.accept()
                return
        super().keyPressEvent(event)


def _menu_item_text_color(widget: QMenu, option: QStyleOptionMenuItem) -> QColor:
    if not bool(option.state & QStyle.State_Enabled):
        return QColor(CONTEXT_MENU_DISABLED_TEXT_COLOR)
    colors = getattr(widget, "_menu_action_text_colors", {})
    color_text = colors.get(str(option.text or "")) if isinstance(colors, dict) else None
    return QColor(str(color_text or ""))


class _MenuActionColorProxyStyle(QProxyStyle):
    """Apply an opt-in foreground color to individual QMenu actions."""

    def drawControl(self, element, option, painter, widget=None) -> None:
        if (
            element == QStyle.CE_MenuItem
            and isinstance(option, QStyleOptionMenuItem)
            and isinstance(widget, QMenu)
        ):
            color = _menu_item_text_color(widget, option)
            if color.isValid():
                colored_option = QStyleOptionMenuItem(option)
                palette = QPalette(colored_option.palette)
                for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                    for role in (
                        QPalette.Text,
                        QPalette.ButtonText,
                        QPalette.WindowText,
                        QPalette.HighlightedText,
                    ):
                        palette.setColor(group, role, color)
                colored_option.palette = palette
                super().drawControl(element, colored_option, painter, widget)
                return
        super().drawControl(element, option, painter, widget)


def set_menu_action_text_color(menu, action, color: str) -> None:
    """Color one QAction label without changing its behavior or enabled state."""

    if action is None:
        return
    setter = getattr(action, "setProperty", None)
    if callable(setter):
        setter(_MENU_TEXT_COLOR_PROPERTY, color)
    if not isinstance(menu, QObject) or not callable(getattr(menu, "setStyle", None)):
        return
    colors = getattr(menu, "_menu_action_text_colors", None)
    if not isinstance(colors, dict):
        colors = {}
        menu._menu_action_text_colors = colors
    text_getter = getattr(action, "text", None)
    text = str(text_getter() if callable(text_getter) else "")
    if text:
        colors[text] = color
    if getattr(menu, "_menu_action_color_style", None) is None:
        style = _MenuActionColorProxyStyle()
        style.setParent(menu)
        menu._menu_action_color_style = style
        menu.setStyle(style)
    menu.update()


class ProfitLossEarlyCloseDialog(QDialog):
    """Shared early-close profit/loss input dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("손/익절 조기마감")
        self.resize(330, 120)

        layout = QVBoxLayout()
        guide = QLabel("익절/손절 비율(%)을 입력하세요.")
        layout.addWidget(guide)

        row_layout = QHBoxLayout()
        self.enabled_check = QCheckBox("익절/손절")
        self.enabled_check.setChecked(True)
        self.enabled_check.setEnabled(False)
        row_layout.addWidget(self.enabled_check)

        row_layout.addWidget(QLabel("+"))
        self.profit_edit = QLineEdit()
        self.profit_edit.setPlaceholderText("입력")
        self.profit_edit.setMaximumWidth(70)
        row_layout.addWidget(self.profit_edit)

        row_layout.addWidget(QLabel("/ -"))
        self.loss_edit = QLineEdit()
        self.loss_edit.setPlaceholderText("입력")
        self.loss_edit.setMaximumWidth(70)
        row_layout.addWidget(self.loss_edit)
        row_layout.addStretch(1)
        layout.addLayout(row_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("확인")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self) -> tuple[str, str]:
        return self.profit_edit.text().strip(), self.loss_edit.text().strip()

    def _positive_number_from_text(self, value: str) -> float:
        return abs(float(value))

    def accept(self) -> None:
        profit_text, loss_text = self.values()
        if not profit_text and not loss_text:
            parent = self.parentWidget() or self
            super().reject()
            show_toast(
                parent,
                "익절 또는 손절 비율 중 최소 1개 값을 입력하세요.",
                duration_ms=2500,
            )
            return

        for label, value, widget in (
            ("익절", profit_text, self.profit_edit),
            ("손절", loss_text, self.loss_edit),
        ):
            if not value:
                continue
            try:
                number = self._positive_number_from_text(value)
            except ValueError:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 숫자로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return
            if number <= 0:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 0보다 큰 값으로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return

        super().accept()


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
    action_early_auto = early_close_menu.addAction("자동마감")
    action_early_auto.setEnabled(False)
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
            ("자동마감", action_early_auto),
            ("취소", action_early_cancel),
        ),
        _selected_policy_menu_label(operation_policy, "early_close", _EARLY_CLOSE_MENU_LABELS),
        "earlyCloseCurrent",
    )
    menu_action_getter = getattr(early_close_menu, "menuAction", None)
    if callable(menu_action_getter):
        set_menu_action_text_color(
            menu,
            menu_action_getter(),
            CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
        )
    for action in (
        action_early_routine,
        action_early_market,
        action_early_current,
        action_early_profit_loss,
        action_early_carry,
        action_early_auto,
    ):
        set_menu_action_text_color(
            early_close_menu,
            action,
            CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
        )
    return {
        "routine": action_early_routine,
        "market": action_early_market,
        "current": action_early_current,
        "profit_loss": action_early_profit_loss,
        "carry": action_early_carry,
        "auto": action_early_auto,
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
    return_auto: Callable[[], None] | None = None,
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
    elif chosen == actions.get("auto") and actions.get("auto") is not None and callable(return_auto):
        return_auto()
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
    individual_policy = operation_policy.get("liquidation", {})
    if not isinstance(individual_policy, dict):
        individual_policy = {}
    individual_minutes = str(
        individual_policy.get("minutes_before_regular_close", "5")
    ).strip() or "5"
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
        (minute, individual_time_menu.addAction(f"{minute}분"))
        for minute in minute_values
    )
    result = {
        "menu": individual_liquidation_menu,
        "market": action_individual_market,
        "current": action_individual_current,
        "carry": action_individual_carry,
        "time_actions": individual_time_actions,
        "method": individual_method,
        "minutes": individual_minutes,
        "time_menu": individual_time_menu,
        "has_selection": bool(has_selection),
    }
    _refresh_individual_liquidation_menu_state(result)
    return result


def _refresh_individual_liquidation_menu_state(
    individual: dict[str, object],
    *,
    method: str | None = None,
    minutes: str | None = None,
) -> None:
    if method in {"시장가", "현재가", "이월"}:
        individual["method"] = method
    if minutes is not None and str(minutes).strip():
        individual["minutes"] = str(minutes).strip()
    current_method = str(individual.get("method") or "이월")
    current_minutes = str(individual.get("minutes") or "5")
    _apply_menu_status(
        (
            ("시장가", individual["market"]),
            ("현재가", individual["current"]),
            ("이월", individual["carry"]),
        ),
        current_method,
        "individualLiquidationCurrent",
    )
    _apply_menu_status(
        tuple((f"{minute}분", action) for minute, action in individual["time_actions"]),
        f"{current_minutes}분",
        "individualLiquidationMinutesCurrent",
    )
    individual["time_menu"].setEnabled(
        bool(individual.get("has_selection")) and current_method != "이월"
    )


def _individual_liquidation_action_applied(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    if "ok" in result:
        return result.get("ok") is True
    return str(result.get("status") or "").strip().upper() in {
        "APPLIED",
        "COMPLETED",
        "REQUESTED",
        "UPDATED",
    }


def _add_ats_settings_menu(
    menu: QMenu,
    *,
    has_selection: bool,
    visible_keys: tuple[str, ...],
    labels: dict[str, str],
    state_getter: Callable[[], dict[str, bool]] | None,
    toggle: Callable[[str, bool, str], None] | None,
    liquidation_available_getter: Callable[[], bool] | None = None,
):
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
        refreshed = dict(refreshed_value) if isinstance(refreshed_value, dict) else dict(current_state)
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
        "refresh": refresh_session_status,
        "market": action_market,
        "current": action_current,
    }


def _dispatch_ats_settings_action(
    chosen,
    actions: dict[str, object],
    *,
    toggle: Callable[[str, bool, str], None] | None,
    liquidate: Callable[[str, dict[str, bool], tuple[str, ...], tuple[str, ...]], None] | None,
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


__all__ = [
    "CONTEXT_MENU_DANGER_TEXT_COLOR",
    "CONTEXT_MENU_DISABLED_TEXT_COLOR",
    "CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR",
    "PersistentContextMenu",
    "ProfitLossEarlyCloseDialog",
    "ats_session_ui_options",
    "_add_ats_settings_menu",
    "_add_early_close_menu",
    "_add_individual_liquidation_menu",
    "_dispatch_ats_settings_action",
    "_dispatch_early_close_action",
    "_individual_liquidation_action_applied",
    "_refresh_individual_liquidation_menu_state",
    "set_menu_action_text_color",
]
