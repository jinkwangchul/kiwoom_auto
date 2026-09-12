# -*- coding: utf-8 -*-
"""
gui_auto_trade_context_menu.py

자동매매설정창 종목 테이블 우클릭 메뉴 처리.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QIconEngine,
    QPalette,
    QPainter,
    QPixmap,
)
from PyQt5.QtWidgets import QMenu, QProxyStyle, QStyle, QStyleOptionMenuItem

from gui_auto_trade_integrity import (
    inspect_stock_review_state,
    is_emergency_stopped_state,
    is_operation_excluded,
)
from gui_auto_trade_status_ops import (
    inspect_auto_trade_operation_exclusion_availability,
)
from gui_ats_utils import (
    manual_ats_session_labels,
    manual_ats_visible_session_keys,
)
from gui_operation_environment import OPERATION_POLICY_PATH
from gui_stock_data import is_valid_stock_code, normalize_stock_code
from runtime_io import read_json_dict
from event_journal_production import append_production_event
from assignment_authorization_service import inspect_stock_unregister_availability
from close_liquidation_command import (
    EARLY_CLOSE_CANCEL,
    EARLY_CLOSE_REQUEST,
    INDIVIDUAL_LIQUIDATION,
    inspect_close_liquidation_availability,
)
from gui_user_reason import user_reason_message
from gui_auto_trade_policy import individual_liquidation_setting_policy_from_state


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

CONTEXT_MENU_DANGER_TEXT_COLOR = "#DC2626"
CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR = "#15803D"
CONTEXT_MENU_DISABLED_TEXT_COLOR = "#AFB2B9"
_MENU_TEXT_COLOR_PROPERTY = "menuTextColor"
_QT_MENU_CLASS = QMenu


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
        submenu = PersistentContextMenu(
            self,
            persistent_root=self._persistent_root,
        )
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
                visible_path.append(
                    (current, QPoint(current.pos()), current.activeAction())
                )
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
            # A callback refresh or modal Toast can dismiss the complete popup
            # chain. Restore the same root/submenu instances and active path;
            # never rebuild the context menu.
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

def tile_new_stock_instance_charts(
    parent,
    windows: Iterable[object],
    *,
    screens=None,
    primary_screen=None,
    gap: int | None = None,
) -> list[object]:
    """Compatibility wrapper for the common chart placement policy."""
    from gui_stock_instance_chart_window import place_new_stock_instance_charts

    placement_options = {
        "screens": screens,
        "primary_screen": primary_screen,
    }
    if gap is not None:
        placement_options["gap"] = gap
    return place_new_stock_instance_charts(parent, windows, **placement_options)

@dataclass(frozen=True)
class StockContextMenuCallbacks:
    select_all: Callable[[], None]
    clear_selection: Callable[[], None]
    early_close: Callable[[str], None]
    early_close_profit_loss: Callable[[], None]
    early_close_cancel: Callable[[], None]
    individual_liquidation: Callable[[str, str], object]
    early_close_return_auto: Callable[[], None] | None = None
    open_charts: Callable[[], None] | None = None
    start: Callable[[], None] | None = None
    emergency_stop: Callable[[], None] | None = None
    emergency_release: Callable[[], None] | None = None
    unregister: Callable[[], None] | None = None
    unregister_available: Callable[[], bool] | None = None
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
    mock_create: Callable[[], None] | None = None
    mock_actions: Callable[[], dict[str, Any]] | None = None


@dataclass(frozen=True)
class StockContextMenuAvailability:
    """Read-only projection of the shared stock Context Menu commands."""

    review_managed: bool
    excluded_management: bool
    start_allowed: bool
    emergency_stop_allowed: bool
    exclusion_allowed: bool
    early_close_allowed: bool
    early_close_cancel_allowed: bool
    individual_liquidation_allowed: bool
    time_management_allowed: bool
    ats_settings_allowed: bool
    stock_register_allowed: bool
    unregister_allowed: bool
    chart_allowed: bool
    reason_codes: tuple[tuple[str, str], ...]

    def reason_for(self, action_key: str) -> str:
        reasons = dict(self.reason_codes)
        return str(reasons.get(str(action_key or ""), "") or "")


def inspect_stock_context_menu_availability(
    parent,
    *,
    has_selection: bool,
    callbacks: StockContextMenuCallbacks,
    selected_targets: Iterable[tuple[object, str, str]] | None,
    operation_excluded: bool,
    operation_exclusion_action: str,
    stock_register_enabled: bool | None,
    scheduled_excluded_management: bool,
    operation_policy: dict[str, object] | None = None,
) -> StockContextMenuAvailability:
    """Inspect menu command availability without mutating config or runtime."""

    targets = list(selected_targets or [])
    review_managed = bool(
        has_selection
        and any(
            inspect_stock_review_state(
                Path(stock_dir),
                loaded_state=read_json_dict(Path(stock_dir) / "state.json"),
            ).review_required
            for stock_dir, _code, _name in targets
        )
    )
    excluded_management = bool(
        scheduled_excluded_management
        and operation_excluded
        and not review_managed
    )
    reasons: dict[str, str] = {}
    if not has_selection:
        for action_key in (
            "start",
            "emergency_stop",
            "exclusion",
            "early_close",
            "early_close_cancel",
            "individual_liquidation",
            "time_management",
            "ats_settings",
            "stock_register",
            "unregister",
            "chart",
        ):
            reasons[action_key] = "SELECT_STOCK_REQUIRED"

    exclusion_action = str(operation_exclusion_action or "").strip().lower()
    if not exclusion_action:
        exclusion_action = "clear" if operation_excluded else "set"
    exclusion_requested = exclusion_action == "set"
    exclusion_allowed = bool(has_selection and exclusion_action in {"set", "clear"})
    if exclusion_allowed and targets:
        exclusion_decisions = [
            inspect_auto_trade_operation_exclusion_availability(
                parent,
                target,
                exclusion_requested,
            )
            for target in targets
        ]
        exclusion_allowed = all(
            decision.allowed
            for decision in exclusion_decisions
        )
        if not exclusion_allowed:
            reasons["exclusion"] = next(
                (
                    str(decision.reason_code or "EXCLUSION_UNAVAILABLE")
                    for decision in exclusion_decisions
                    if not decision.allowed
                ),
                "EXCLUSION_UNAVAILABLE",
            )

    structural_target_available = bool(has_selection)
    if structural_target_available and targets:
        structural_target_available = all(
            Path(stock_dir).exists()
            and bool(normalize_stock_code(str(code or "")))
            and normalize_stock_code(Path(stock_dir).name.partition("_")[0])
            == normalize_stock_code(str(code or ""))
            for stock_dir, code, _name in targets
        )
    if has_selection and not structural_target_available:
        reasons["early_close"] = "CONTEXT_TARGET_INVALID"
        reasons["early_close_cancel"] = "CONTEXT_TARGET_INVALID"
        reasons["individual_liquidation"] = "CONTEXT_TARGET_INVALID"

    start_allowed = bool(has_selection)
    emergency_stop_allowed = bool(has_selection)
    early_close_allowed = structural_target_available
    early_close_cancel_allowed = False
    individual_liquidation_allowed = structural_target_available
    time_management_allowed = bool(has_selection)
    ats_settings_allowed = bool(has_selection)
    stock_register_allowed = bool(
        has_selection
        if stock_register_enabled is None
        else stock_register_enabled
    )
    if not stock_register_allowed and "stock_register" not in reasons:
        reasons["stock_register"] = "STOCK_REGISTER_UNAVAILABLE"
    unregister_allowed = bool(has_selection)
    if unregister_allowed and callable(callbacks.unregister_available):
        try:
            unregister_allowed = bool(callbacks.unregister_available())
        except Exception:
            unregister_allowed = False
        if not unregister_allowed:
            reasons["unregister"] = "UNREGISTER_UNAVAILABLE"
    chart_allowed = bool(has_selection)

    canonical_targets = [
        (Path(stock_dir), str(code or "").strip(), str(name or "").strip())
        for stock_dir, code, name in targets
        if (Path(stock_dir) / "state.json").is_file()
    ]
    if structural_target_available and canonical_targets and len(canonical_targets) == len(targets):
        cancel_decisions = [
            inspect_close_liquidation_availability(
                parent,
                stock_dir,
                code,
                intent=EARLY_CLOSE_CANCEL,
            )
            for stock_dir, code, _name in canonical_targets
        ]
        early_close_cancel_allowed = bool(
            all(decision.allowed for decision in cancel_decisions)
        )
        if not early_close_cancel_allowed:
            reasons["early_close_cancel"] = next(
                (
                    decision.reason_code
                    for decision in cancel_decisions
                    if not decision.allowed
                ),
                "EARLY_CLOSE_CANCEL_UNAVAILABLE",
            )

    if review_managed:
        for action_key in (
            "start",
            "emergency_stop",
            "exclusion",
            "stock_register",
            "unregister",
            "chart",
        ):
            reasons[action_key] = "REVIEW_REQUIRED"
        start_allowed = False
        emergency_stop_allowed = False
        exclusion_allowed = False
        stock_register_allowed = False
        unregister_allowed = False
        chart_allowed = False
    elif excluded_management:
        reasons["emergency_stop"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        reasons["stock_register"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        emergency_stop_allowed = False
        stock_register_allowed = False

    return StockContextMenuAvailability(
        review_managed=review_managed,
        excluded_management=excluded_management,
        start_allowed=start_allowed,
        emergency_stop_allowed=emergency_stop_allowed,
        exclusion_allowed=exclusion_allowed,
        early_close_allowed=early_close_allowed,
        early_close_cancel_allowed=early_close_cancel_allowed,
        individual_liquidation_allowed=individual_liquidation_allowed,
        time_management_allowed=time_management_allowed,
        ats_settings_allowed=ats_settings_allowed,
        stock_register_allowed=stock_register_allowed,
        unregister_allowed=unregister_allowed,
        chart_allowed=chart_allowed,
        reason_codes=tuple(reasons.items()),
    )


def _menu_entry_enabled(entry) -> bool:
    if entry is None:
        return False
    getter = getattr(entry, "isEnabled", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return False
    return bool(getattr(entry, "enabled", True))


def open_selected_stock_instance_charts(
    parent,
    selected: Iterable[tuple[object, str, str]],
) -> list[object]:
    """Open each valid selected stock through the common singleton opener."""
    from gui_stock_instance_chart_window import open_stock_instance_chart

    opened: list[object] = []
    seen_codes: set[str] = set()
    for _stock_dir, raw_code, _stock_name in selected:
        stock_code = normalize_stock_code(str(raw_code or ""))
        if not is_valid_stock_code(stock_code) or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        try:
            window = open_stock_instance_chart(
                stock_code,
                trade_date=None,
                parent=parent,
            )
            opened.append(window)
        except Exception:
            # A single damaged target must not prevent the remaining charts.
            continue
    return opened


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
    # Tests and compatibility callers may replace the module-level QMenu with a
    # lightweight stand-in.  Keep that legacy construction path intact.
    menu = (
        PersistentContextMenu(parent)
        if QMenu is _QT_MENU_CLASS
        else QMenu(parent)
    )
    set_tooltips_visible = getattr(menu, "setToolTipsVisible", None)
    if callable(set_tooltips_visible):
        set_tooltips_visible(True)
    return menu


def _set_disabled_reason(entry, *, enabled: bool, reason_code: str, fallback: str) -> None:
    """Describe a disabled menu entry without changing its enabled state."""

    if entry is None or enabled:
        return
    message = user_reason_message(reason_code, fallback=fallback)
    set_tool_tip = getattr(entry, "setToolTip", None)
    if callable(set_tool_tip):
        set_tool_tip(message)
    set_status_tip = getattr(entry, "setStatusTip", None)
    if callable(set_status_tip):
        set_status_tip(message)
    menu_action_getter = getattr(entry, "menuAction", None)
    menu_action = menu_action_getter() if callable(menu_action_getter) else None
    if menu_action is not None:
        menu_action.setToolTip(message)
        menu_action.setStatusTip(message)


def selected_emergency_context_state(
    selected: Iterable[tuple[object, str, str]],
) -> tuple[bool, bool]:
    """Return SELECTED-release and ordinary-stop eligibility for one selection."""
    has_selected_emergency = False
    has_non_emergency = False
    for stock_dir, _code, _name in selected:
        inspection = inspect_stock_review_state(
            Path(stock_dir),
            loaded_state=read_json_dict(Path(stock_dir) / "state.json"),
        )
        state = inspection.state
        if is_emergency_stopped_state(state) or inspection.review_required:
            scope = str(state.get("emergency_scope", "") or "").strip().upper()
            if scope == "SELECTED":
                has_selected_emergency = True
        else:
            has_non_emergency = True
    return has_selected_emergency, has_non_emergency


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
        _selected_policy_menu_label(
            operation_policy,
            "early_close",
            _EARLY_CLOSE_MENU_LABELS,
        ),
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
    """Refresh one open individual-liquidation menu without rebuilding it."""

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
        tuple(
            (f"{minute}분", action)
            for minute, action in individual["time_actions"]
        ),
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
    state_getter: Callable[[], dict[str, bool]] | None,
    toggle: Callable[[str, bool, str], None] | None,
    liquidation_available_getter: Callable[[], bool] | None = None,
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
        "refresh": refresh_session_status,
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


def _append_stock_context_decision(
    selected_option: str,
    decision_event_type: str,
    targets: Iterable[tuple[object, str, str]],
) -> None:
    if not selected_option:
        return
    target_list = list(targets)
    codes = [
        str(code or "").strip()
        for _path, code, _name in target_list
        if str(code or "").strip()
    ]
    names = [
        str(name or "").strip()
        for _path, _code, name in target_list
        if str(name or "").strip()
    ]
    correlation = (
        {"stock_code": codes[0], "stock_name": names[0] if names else None}
        if len(codes) == 1
        else {}
    )
    append_production_event(
        decision_event_type,
        result="ACCEPTED",
        source="gui_auto_trade_context_menu.show_monitor_stock_context_menu",
        target_type="STOCK_SELECTION",
        target_id=",".join(codes) or None,
        target_name=",".join(names) or "선택 종목",
        details={
            "interaction_type": "SELECTION",
            "prompt_key": "MONITOR_STOCK_CONTEXT_MENU",
            "prompt_title": "종목 운영 메뉴",
            "prompt_summary": "선택 종목에 적용할 context action",
            "offered_options": [
                "OPERATION_START",
                "EMERGENCY_STOP",
                "EMERGENCY_RELEASE",
                "OPERATION_EXCLUDE",
                "OPERATION_EXCLUSION_RELEASE",
                "EARLY_CLOSE_ROUTINE",
                "EARLY_CLOSE_MARKET",
                "EARLY_CLOSE_CURRENT",
                "EARLY_CLOSE_PROFIT_LOSS",
                "EARLY_CLOSE_CARRY",
                "EARLY_CLOSE_CANCEL",
                "LIQUIDATION_MARKET",
                "LIQUIDATION_CURRENT",
                "LIQUIDATION_CARRY",
                "ATS_LIQUIDATION_MARKET",
                "ATS_LIQUIDATION_CURRENT",
            ],
            "selected_option": selected_option,
            "target_count": len(target_list),
        },
        **correlation,
    )


def show_monitor_stock_context_menu(
    parent,
    global_pos,
    *,
    has_selection: bool,
    callbacks: StockContextMenuCallbacks,
    selected_modes: set[str] | None = None,
    operation_excluded: bool = False,
    operation_exclusion_action: str | None = None,
    stock_register_enabled: bool | None = None,
    selected_targets: Iterable[tuple[object, str, str]] | None = None,
    selected_scope_emergency: bool | None = None,
    scheduled_excluded_management: bool = False,
) -> None:
    """Show the monitoring stock-row profile with the shared menu form."""

    menu = _new_stock_context_menu(parent)
    mock_context: dict[str, Any] = {}
    if callable(callbacks.mock_actions):
        try:
            projected = callbacks.mock_actions()
        except Exception:
            projected = {}
        if isinstance(projected, dict):
            mock_context = projected
    if mock_context.get("current") is True:
        action_mock_start = menu.addAction("운영시작")
        action_mock_start.setEnabled(bool(mock_context.get("can_start")))
        menu.addSeparator()
        action_select_all = menu.addAction("전체선택")
        action_clear_selection = menu.addAction("선택해제")

        menu.addSeparator()
        early_close = _add_early_close_menu(
            menu,
            has_selection=has_selection,
            operation_policy={},
        )
        can_early_close = bool(mock_context.get("can_early_close"))
        early_close["menu"].setEnabled(can_early_close)
        for key in ("market", "current", "carry"):
            early_close[key].setEnabled(can_early_close)
        for key in ("routine", "profit_loss", "cancel"):
            early_close[key].setEnabled(False)

        individual = _add_individual_liquidation_menu(
            menu,
            has_selection=has_selection,
            operation_policy={},
        )
        can_immediate = bool(mock_context.get("can_immediate"))
        individual["menu"].setEnabled(can_immediate)
        individual["market"].setEnabled(can_immediate)
        individual["current"].setEnabled(can_immediate)
        individual["carry"].setEnabled(False)
        individual["time_menu"].setEnabled(False)

        menu.addSeparator()
        action_mock_register = menu.addAction("종목등록")
        action_mock_unregister = menu.addAction("등록해제")
        action_mock_register.setEnabled(callbacks.mock_create is not None)
        action_mock_unregister.setEnabled(bool(mock_context.get("can_unregister")))

        action_open_charts = None
        if callbacks.open_charts is not None:
            menu.addSeparator()
            action_open_charts = menu.addAction("간이차트")
            action_open_charts.setEnabled(has_selection)

        menu.addSeparator()
        action_mock_reset = menu.addAction("종목리셋")
        action_mock_reset.setEnabled(bool(mock_context.get("can_reset")))
        menu._mock_validation_actions = {
            "start": action_mock_start,
            "select_all": action_select_all,
            "clear_selection": action_clear_selection,
            "early_close": early_close,
            "individual_liquidation": individual,
            "register": action_mock_register,
            "unregister": action_mock_unregister,
            "chart": action_open_charts,
            "reset": action_mock_reset,
        }
        registrar = getattr(menu, "register_persistent_action", None)
        if callable(registrar):
            def refresh_mock_actions() -> None:
                try:
                    refreshed = callbacks.mock_actions()
                except Exception:
                    refreshed = {}
                if not isinstance(refreshed, dict) or refreshed.get("current") is not True:
                    menu.invalidate_persistent_context()
                    return
                mock_context.clear()
                mock_context.update(refreshed)
                action_mock_start.setEnabled(bool(refreshed.get("can_start")))
                refreshed_early = bool(refreshed.get("can_early_close"))
                early_close["menu"].setEnabled(refreshed_early)
                for key in ("market", "current", "carry"):
                    early_close[key].setEnabled(refreshed_early)
                refreshed_immediate = bool(refreshed.get("can_immediate"))
                individual["menu"].setEnabled(refreshed_immediate)
                individual["market"].setEnabled(refreshed_immediate)
                individual["current"].setEnabled(refreshed_immediate)
                action_mock_reset.setEnabled(bool(refreshed.get("can_reset")))
                action_mock_unregister.setEnabled(bool(refreshed.get("can_unregister")))

            def run_mock_action(key: str, *args) -> None:
                callback = mock_context.get(key)
                if callable(callback):
                    callback(*args)
                refresh_mock_actions()

            registrar(action_mock_start, lambda: run_mock_action("start"))
            registrar(action_mock_reset, lambda: run_mock_action("reset"))
            if action_open_charts is not None:
                registrar(action_open_charts, callbacks.open_charts)
            for key, method in (
                ("market", "시장가즉시"),
                ("current", "현재가즉시"),
                ("carry", "이월"),
            ):
                registrar(
                    early_close[key],
                    lambda method=method: run_mock_action("early_close", method),
                )
            for key, method in (("market", "시장가"), ("current", "현재가")):
                registrar(
                    individual[key],
                    lambda method=method: run_mock_action(
                        "immediate_liquidation",
                        method,
                        "",
                    ),
                )
            registrar(action_select_all, callbacks.select_all, terminal=True)
            registrar(action_clear_selection, callbacks.clear_selection, terminal=True)
            registrar(action_mock_register, callbacks.mock_create, terminal=True)
            registrar(
                action_mock_unregister,
                lambda: run_mock_action("unregister"),
                terminal=True,
            )
        chosen = menu.exec_(global_pos)
        if callable(registrar):
            return
        dispatch = {
            action_mock_start: mock_context.get("start"),
            action_mock_reset: mock_context.get("reset"),
            action_mock_unregister: mock_context.get("unregister"),
            action_select_all: callbacks.select_all,
            action_clear_selection: callbacks.clear_selection,
            action_mock_register: callbacks.mock_create,
            action_open_charts: callbacks.open_charts,
        }
        callback = dispatch.get(chosen)
        if callable(callback) and chosen is not None and chosen.isEnabled():
            callback()
            return
        early_methods = {
            early_close["market"]: "시장가즉시",
            early_close["current"]: "현재가즉시",
            early_close["carry"]: "이월",
        }
        if chosen in early_methods and chosen.isEnabled():
            callback = mock_context.get("early_close")
            if callable(callback):
                callback(early_methods[chosen])
            return
        immediate_methods = {
            individual["market"]: "시장가",
            individual["current"]: "현재가",
        }
        if chosen in immediate_methods and chosen.isEnabled():
            callback = mock_context.get("immediate_liquidation")
            if callable(callback):
                callback(immediate_methods[chosen], "")
        return
    operation_policy = _context_menu_operation_policy()
    targets = list(selected_targets or [])
    selected_individual_policies = [
        individual_liquidation_setting_policy_from_state(
            read_json_dict(Path(stock_dir) / "state.json")
        )
        for stock_dir, _code, _name in targets
    ]
    if (
        selected_individual_policies
        and selected_individual_policies[0]
        and all(
            policy == selected_individual_policies[0]
            for policy in selected_individual_policies[1:]
        )
    ):
        operation_policy = deepcopy(operation_policy)
        operation_policy["liquidation"] = deepcopy(
            selected_individual_policies[0]
        )
    def active_close_context() -> tuple[set[str], bool, bool]:
        methods: set[str] = set()
        operation_modes: set[str] = set()
        for stock_dir, _code, _name in targets:
            state = read_json_dict(Path(stock_dir) / "state.json")
            if not state or str(state.get("status") or "").strip().upper() not in {
                "AUTO_CLOSE",
                "AUTO_CLOSING",
                "EARLY_CLOSE",
                "EARLY_CLOSING",
            }:
                continue
            if not str(
                state.get("early_close_requested_at")
                or state.get("auto_close_requested_at")
                or ""
            ).strip():
                continue
            methods.add(
                str(
                    state.get("early_close_method")
                    or state.get("auto_close_method")
                    or ""
                ).strip()
            )
            snapshot = state.get("operation_policy_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            operation_modes.add(
                str(snapshot.get("operation_mode") or "").strip().upper()
            )
        active = bool(methods)
        return methods, active, bool(active and operation_modes == {"SCHEDULED"})

    active_close_methods, active_close_change, active_auto_return = (
        active_close_context()
    )
    if len(active_close_methods) == 1:
        operation_policy = deepcopy(operation_policy)
        early_policy = operation_policy.get("early_close")
        early_policy = deepcopy(early_policy) if isinstance(early_policy, dict) else {}
        early_policy["method"] = next(iter(active_close_methods))
        operation_policy["early_close"] = early_policy
    exclusion_action = str(operation_exclusion_action or "").strip().lower()
    if not exclusion_action:
        exclusion_action = "clear" if operation_excluded else "set"
    availability = inspect_stock_context_menu_availability(
        parent,
        has_selection=has_selection,
        callbacks=callbacks,
        selected_targets=targets,
        operation_excluded=operation_excluded,
        operation_exclusion_action=exclusion_action,
        stock_register_enabled=stock_register_enabled,
        scheduled_excluded_management=scheduled_excluded_management,
        operation_policy=operation_policy,
    )
    menu._stock_context_availability = availability

    action_start = None
    if callbacks.start is not None:
        action_start = menu.addAction("운영시작")
        action_start.setEnabled(availability.start_allowed)
    action_emergency_stop = None
    if callbacks.emergency_stop is not None:
        action_emergency_stop = menu.addAction("검토정지")
        action_emergency_stop.setEnabled(availability.emergency_stop_allowed)
    if action_start is not None:
        menu.addSeparator()
    action_select_all = menu.addAction("전체선택")
    action_clear_selection = menu.addAction("선택해제")

    action_set_exclusion = None
    action_clear_exclusion = None
    if exclusion_action == "clear" and callbacks.clear_operation_exclusion is not None:
        action_clear_exclusion = menu.addAction("제외해제")
        action_clear_exclusion.setEnabled(availability.exclusion_allowed)
    elif exclusion_action == "set" and callbacks.set_operation_exclusion is not None:
        action_set_exclusion = menu.addAction("운영제외")
        action_set_exclusion.setEnabled(availability.exclusion_allowed)

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_excluded=operation_excluded,
        operation_policy=operation_policy,
    )
    if active_close_change:
        early_close["menu"].setTitle("마감변경")
    early_close["menu"].setEnabled(
        availability.early_close_allowed
        or availability.early_close_cancel_allowed
        or active_close_change
    )
    for key in ("routine", "market", "current", "profit_loss", "carry"):
        early_close[key].setEnabled(availability.early_close_allowed)
    early_close["cancel"].setEnabled(availability.early_close_cancel_allowed)
    auto_return_action = early_close.get("auto")
    if auto_return_action is not None:
        auto_return_action.setEnabled(
            active_auto_return and callbacks.early_close_return_auto is not None
        )
        set_visible = getattr(auto_return_action, "setVisible", None)
        if callable(set_visible):
            set_visible(active_auto_return)

    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=has_selection,
        operation_policy=operation_policy,
    )
    individual["menu"].setEnabled(availability.individual_liquidation_allowed)

    action_time_change = None
    action_time_reset = None
    ats_settings = None
    selected_modes = set(selected_modes or ())
    if selected_modes == {"SCHEDULED"}:
        menu.addSeparator()
        action_time_change = menu.addAction("시간변경")
        action_time_reset = menu.addAction("변경리셋")
        action_time_change.setEnabled(availability.time_management_allowed)
        action_time_reset.setEnabled(availability.time_management_allowed)
    elif selected_modes == {"CONTINUOUS"}:
        menu.addSeparator()
        ats_settings = _add_ats_settings_menu(
            menu,
            has_selection=has_selection,
            state_getter=callbacks.ats_state,
            toggle=callbacks.ats_toggle,
            liquidation_available_getter=callbacks.ats_liquidation_available,
        )
        ats_settings["menu"].setEnabled(
            availability.ats_settings_allowed
            and _menu_entry_enabled(ats_settings["menu"])
        )

    action_mock_create = None
    if callbacks.mock_create is not None:
        menu.addSeparator()
        action_mock_create = menu.addAction("모의검증")
        action_mock_create.setEnabled(has_selection)

    action_stock_register = None
    action_unregister = None
    if callbacks.stock_register is not None or callbacks.unregister is not None:
        menu.addSeparator()
        if callbacks.stock_register is not None:
            action_stock_register = menu.addAction("종목등록")
            action_stock_register.setEnabled(availability.stock_register_allowed)
        if callbacks.unregister is not None:
            action_unregister = menu.addAction("등록해제")
            action_unregister.setEnabled(availability.unregister_allowed)

    action_open_charts = None
    if callbacks.open_charts is not None:
        menu.addSeparator()
        action_open_charts = menu.addAction("간이차트")
        action_open_charts.setEnabled(availability.chart_allowed)

    _set_disabled_reason(
        action_start,
        enabled=availability.start_allowed,
        reason_code=availability.reason_for("start"),
        fallback="현재 선택한 종목은 운영을 시작할 수 없습니다.",
    )
    _set_disabled_reason(
        action_emergency_stop,
        enabled=availability.emergency_stop_allowed,
        reason_code=availability.reason_for("emergency_stop"),
        fallback="현재 선택한 종목은 운영을 정지할 수 없습니다.",
    )
    exclusion_entry = action_set_exclusion or action_clear_exclusion
    _set_disabled_reason(
        exclusion_entry,
        enabled=availability.exclusion_allowed,
        reason_code=availability.reason_for("exclusion"),
        fallback="현재 선택한 종목의 운영 제외 상태를 변경할 수 없습니다.",
    )
    _set_disabled_reason(
        early_close["menu"],
        enabled=(availability.early_close_allowed or availability.early_close_cancel_allowed or active_close_change),
        reason_code=(
            availability.reason_for("early_close")
            or availability.reason_for("early_close_cancel")
        ),
        fallback="현재 선택한 종목은 조기마감 작업을 할 수 없습니다.",
    )
    for key in ("routine", "market", "current", "profit_loss", "carry"):
        _set_disabled_reason(
            early_close[key],
            enabled=availability.early_close_allowed,
            reason_code=availability.reason_for("early_close"),
            fallback="현재 선택한 종목은 조기마감할 수 없습니다.",
        )
    _set_disabled_reason(
        early_close["cancel"],
        enabled=availability.early_close_cancel_allowed,
        reason_code=availability.reason_for("early_close_cancel"),
        fallback="현재 선택한 종목은 조기마감을 취소할 수 없습니다.",
    )
    _set_disabled_reason(
        auto_return_action,
        enabled=(active_auto_return and callbacks.early_close_return_auto is not None),
        reason_code="EARLY_AUTO_RETURN_NOT_APPLICABLE",
        fallback="현재 마감은 자동마감 일정으로 복귀할 수 없습니다.",
    )
    _set_disabled_reason(
        individual["menu"],
        enabled=availability.individual_liquidation_allowed,
        reason_code=availability.reason_for("individual_liquidation"),
        fallback="현재 선택한 종목은 개별청산할 수 없습니다.",
    )
    _set_disabled_reason(
        action_time_change,
        enabled=availability.time_management_allowed,
        reason_code=availability.reason_for("time_management"),
        fallback="대상 종목을 선택하세요.",
    )
    _set_disabled_reason(
        action_time_reset,
        enabled=availability.time_management_allowed,
        reason_code=availability.reason_for("time_management"),
        fallback="대상 종목을 선택하세요.",
    )
    if ats_settings is not None:
        _set_disabled_reason(
            ats_settings["menu"],
            enabled=availability.ats_settings_allowed and _menu_entry_enabled(ats_settings["menu"]),
            reason_code=availability.reason_for("ats_settings"),
            fallback="현재 선택한 종목의 ATS 설정을 변경할 수 없습니다.",
        )
    _set_disabled_reason(
        action_stock_register,
        enabled=availability.stock_register_allowed,
        reason_code=availability.reason_for("stock_register"),
        fallback="현재 선택한 종목은 등록할 수 없습니다.",
    )
    _set_disabled_reason(
        action_unregister,
        enabled=availability.unregister_allowed,
        reason_code=availability.reason_for("unregister"),
        fallback="현재 선택한 종목은 루틴에서 해제할 수 없습니다.",
    )
    _set_disabled_reason(
        action_open_charts,
        enabled=availability.chart_allowed,
        reason_code=availability.reason_for("chart"),
        fallback="대상 종목을 선택하세요.",
    )

    registrar = getattr(menu, "register_persistent_action", None)
    if callable(registrar):
        def context_targets_valid() -> bool:
            for raw_path, raw_code, _name in targets:
                target_path = Path(raw_path)
                stock_code = normalize_stock_code(str(raw_code or ""))
                folder_code = normalize_stock_code(target_path.name.partition("_")[0])
                if not target_path.exists() or not stock_code or folder_code != stock_code:
                    return False
            return True

        def refresh_entry(action, enabled: bool, reason_code: str, fallback: str) -> None:
            if action is None:
                return
            action.setEnabled(bool(enabled))
            if enabled:
                for setter_name in ("setToolTip", "setStatusTip"):
                    setter = getattr(action, setter_name, None)
                    if callable(setter):
                        setter("")
                return
            _set_disabled_reason(
                action,
                enabled=False,
                reason_code=reason_code,
                fallback=fallback,
            )

        def refresh_persistent_state() -> None:
            if not context_targets_valid():
                menu.invalidate_persistent_context()
                return
            current_excluded = bool(
                targets
                and all(
                    is_operation_excluded(read_json_dict(Path(stock_dir) / "config.json"))
                    for stock_dir, _code, _name in targets
                )
            )
            refreshed = inspect_stock_context_menu_availability(
                parent,
                has_selection=has_selection,
                callbacks=callbacks,
                selected_targets=targets,
                operation_excluded=current_excluded,
                operation_exclusion_action=exclusion_action,
                stock_register_enabled=stock_register_enabled,
                scheduled_excluded_management=scheduled_excluded_management,
                operation_policy=_context_menu_operation_policy(),
            )
            menu._stock_context_availability = refreshed
            (
                _refreshed_methods,
                refreshed_active_close,
                refreshed_auto_return,
            ) = active_close_context()
            early_close["menu"].setTitle(
                "마감변경" if refreshed_active_close else "조기마감"
            )
            refresh_entry(action_start, refreshed.start_allowed, refreshed.reason_for("start"), "현재 선택한 종목은 운영을 시작할 수 없습니다.")
            refresh_entry(action_emergency_stop, refreshed.emergency_stop_allowed, refreshed.reason_for("emergency_stop"), "현재 선택한 종목은 운영을 정지할 수 없습니다.")
            refresh_entry(action_stock_register, refreshed.stock_register_allowed, refreshed.reason_for("stock_register"), "현재 선택한 종목은 등록할 수 없습니다.")
            refresh_entry(action_unregister, refreshed.unregister_allowed, refreshed.reason_for("unregister"), "현재 선택한 종목은 루틴에서 해제할 수 없습니다.")
            refresh_entry(action_open_charts, refreshed.chart_allowed, refreshed.reason_for("chart"), "대상 종목을 선택하세요.")
            refresh_entry(action_time_change, refreshed.time_management_allowed, refreshed.reason_for("time_management"), "대상 종목을 선택하세요.")
            refresh_entry(action_time_reset, refreshed.time_management_allowed, refreshed.reason_for("time_management"), "대상 종목을 선택하세요.")
            refresh_entry(
                early_close["menu"],
                refreshed.early_close_allowed
                or refreshed.early_close_cancel_allowed
                or refreshed_active_close,
                refreshed.reason_for("early_close") or refreshed.reason_for("early_close_cancel"),
                "현재 선택한 종목은 조기마감 설정을 변경할 수 없습니다.",
            )
            for key in ("routine", "market", "current", "profit_loss", "carry"):
                refresh_entry(
                    early_close[key],
                    refreshed.early_close_allowed,
                    refreshed.reason_for("early_close"),
                    "현재 선택한 종목은 조기마감할 수 없습니다.",
                )
            refresh_entry(early_close["cancel"], refreshed.early_close_cancel_allowed, refreshed.reason_for("early_close_cancel"), "현재 선택한 종목은 조기마감을 취소할 수 없습니다.")
            refresh_entry(
                auto_return_action,
                refreshed_auto_return
                and callbacks.early_close_return_auto is not None,
                "EARLY_AUTO_RETURN_NOT_APPLICABLE",
                "현재 마감은 자동마감 일정으로 복귀할 수 없습니다.",
            )
            if auto_return_action is not None:
                set_visible = getattr(auto_return_action, "setVisible", None)
                if callable(set_visible):
                    set_visible(refreshed_auto_return)
            refresh_entry(
                individual["menu"],
                refreshed.individual_liquidation_allowed,
                refreshed.reason_for("individual_liquidation"),
                "현재 선택한 종목은 개별청산할 수 없습니다.",
            )
            if ats_settings is not None:
                refresh_entry(
                    ats_settings["menu"],
                    refreshed.ats_settings_allowed,
                    refreshed.reason_for("ats_settings"),
                    "현재 선택한 종목의 ATS 설정을 변경할 수 없습니다.",
                )
                ats_refresh = ats_settings.get("refresh")
                if callable(ats_refresh):
                    ats_refresh()

        def persistent_decision(chosen_action) -> tuple[str, str]:
            event_type = "OPERATOR_OPERATION_DECISION"
            option = ""
            if chosen_action == action_start:
                option = "OPERATION_START"
            elif chosen_action == action_emergency_stop:
                option = "EMERGENCY_STOP"
            elif chosen_action == action_set_exclusion:
                option, event_type = "OPERATION_EXCLUDE", "OPERATOR_SETTING_DECISION"
            elif chosen_action == action_clear_exclusion:
                option, event_type = "OPERATION_EXCLUSION_RELEASE", "OPERATOR_SETTING_DECISION"
            elif chosen_action == early_close["routine"]:
                option = "EARLY_CLOSE_ROUTINE"
            elif chosen_action == early_close["market"]:
                option = "EARLY_CLOSE_MARKET"
            elif chosen_action == early_close["current"]:
                option = "EARLY_CLOSE_CURRENT"
            elif chosen_action == early_close["profit_loss"]:
                option = "EARLY_CLOSE_PROFIT_LOSS"
            elif chosen_action == early_close["carry"]:
                option = "EARLY_CLOSE_CARRY"
            elif chosen_action == auto_return_action and auto_return_action is not None:
                option = "EARLY_CLOSE_RETURN_AUTO"
            elif chosen_action == early_close["cancel"]:
                option = "EARLY_CLOSE_CANCEL"
            elif chosen_action == individual["market"]:
                option = "LIQUIDATION_MARKET"
            elif chosen_action == individual["current"]:
                option = "LIQUIDATION_CURRENT"
            elif chosen_action == individual["carry"]:
                option = "LIQUIDATION_CARRY"
            elif ats_settings is not None and chosen_action == ats_settings["market"]:
                option = "ATS_LIQUIDATION_MARKET"
            elif ats_settings is not None and chosen_action == ats_settings["current"]:
                option = "ATS_LIQUIDATION_CURRENT"
            return option, event_type

        def dispatch_persistent_action(chosen_action) -> None:
            if not context_targets_valid() or not _menu_entry_enabled(chosen_action):
                menu.invalidate_persistent_context()
                return
            option, event_type = persistent_decision(chosen_action)
            _append_stock_context_decision(option, event_type, targets)
            if chosen_action == action_start and callbacks.start is not None:
                callbacks.start()
            elif chosen_action == action_emergency_stop and callbacks.emergency_stop is not None:
                callbacks.emergency_stop()
            elif chosen_action == action_stock_register and callbacks.stock_register is not None:
                callbacks.stock_register()
            elif chosen_action == action_select_all:
                callbacks.select_all()
            elif chosen_action == action_clear_selection:
                callbacks.clear_selection()
            elif chosen_action == action_set_exclusion and callbacks.set_operation_exclusion is not None:
                callbacks.set_operation_exclusion()
            elif chosen_action == action_clear_exclusion and callbacks.clear_operation_exclusion is not None:
                callbacks.clear_operation_exclusion()
            elif chosen_action == action_unregister and callbacks.unregister is not None:
                callbacks.unregister()
            elif chosen_action == action_open_charts and callbacks.open_charts is not None:
                callbacks.open_charts()
            elif chosen_action == action_mock_create and callbacks.mock_create is not None:
                callbacks.mock_create()
            elif _dispatch_early_close_action(
                chosen_action,
                early_close,
                apply_method=callbacks.early_close,
                apply_profit_loss=callbacks.early_close_profit_loss,
                cancel=callbacks.early_close_cancel,
                return_auto=callbacks.early_close_return_auto,
            ):
                pass
            elif chosen_action == individual["market"]:
                result = callbacks.individual_liquidation(
                    "시장가", individual["minutes"]
                )
                if _individual_liquidation_action_applied(result):
                    _refresh_individual_liquidation_menu_state(
                        individual, method="시장가"
                    )
            elif chosen_action == individual["current"]:
                result = callbacks.individual_liquidation(
                    "현재가", individual["minutes"]
                )
                if _individual_liquidation_action_applied(result):
                    _refresh_individual_liquidation_menu_state(
                        individual, method="현재가"
                    )
            elif chosen_action == individual["carry"]:
                result = callbacks.individual_liquidation(
                    "이월", individual["minutes"]
                )
                if _individual_liquidation_action_applied(result):
                    _refresh_individual_liquidation_menu_state(
                        individual, method="이월"
                    )
            elif chosen_action == action_time_change and callbacks.time_change is not None:
                callbacks.time_change()
            elif chosen_action == action_time_reset and callbacks.time_reset is not None:
                callbacks.time_reset()
            elif ats_settings is not None and _dispatch_ats_settings_action(
                chosen_action,
                ats_settings,
                toggle=callbacks.ats_toggle,
                liquidate=callbacks.ats_liquidation,
            ):
                pass
            else:
                for minute, time_action in individual["time_actions"]:
                    if chosen_action == time_action:
                        result = callbacks.individual_liquidation(
                            individual["method"], minute
                        )
                        if _individual_liquidation_action_applied(result):
                            _refresh_individual_liquidation_menu_state(
                                individual, minutes=minute
                            )
                        break
            refresh_persistent_state()

        persistent_actions = [
            action_start,
            action_emergency_stop,
            action_open_charts,
            action_time_change,
            action_time_reset,
            *[
                early_close[key]
                for key in (
                    "routine",
                    "market",
                    "current",
                    "profit_loss",
                    "carry",
                    "auto",
                    "cancel",
                )
            ],
            individual["market"],
            individual["current"],
            individual["carry"],
            *[action for _minute, action in individual["time_actions"]],
        ]
        if ats_settings is not None:
            persistent_actions.extend((ats_settings["market"], ats_settings["current"]))
        for persistent_action in persistent_actions:
            if persistent_action is not None:
                registrar(
                    persistent_action,
                    lambda action=persistent_action: dispatch_persistent_action(action),
                )
        for terminal_action in (
            action_select_all,
            action_clear_selection,
            action_set_exclusion,
            action_clear_exclusion,
            action_stock_register,
            action_unregister,
            action_mock_create,
        ):
            if terminal_action is not None:
                registrar(
                    terminal_action,
                    lambda action=terminal_action: dispatch_persistent_action(action),
                    terminal=True,
                )

    chosen = menu.exec_(global_pos)
    if callable(registrar):
        return
    if chosen is None:
        return
    allowed_actions = [action_select_all, action_clear_selection]

    def allow(action, allowed: bool) -> None:
        if action is not None and allowed and _menu_entry_enabled(action):
            allowed_actions.append(action)

    allow(action_start, availability.start_allowed)
    allow(action_emergency_stop, availability.emergency_stop_allowed)
    allow(action_set_exclusion, availability.exclusion_allowed)
    allow(action_clear_exclusion, availability.exclusion_allowed)
    allow(action_stock_register, availability.stock_register_allowed)
    allow(action_unregister, availability.unregister_allowed)
    allow(action_open_charts, availability.chart_allowed)
    allow(action_mock_create, has_selection)
    allow(action_time_change, availability.time_management_allowed)
    allow(action_time_reset, availability.time_management_allowed)
    if _menu_entry_enabled(early_close["menu"]):
        if availability.early_close_allowed:
            for key in ("routine", "market", "current", "profit_loss", "carry"):
                allow(early_close[key], True)
        if availability.early_close_cancel_allowed:
            allow(early_close["cancel"], True)
        if active_auto_return and callbacks.early_close_return_auto is not None:
            allow(auto_return_action, True)
    if (
        availability.individual_liquidation_allowed
        and _menu_entry_enabled(individual["menu"])
    ):
        for key in ("market", "current", "carry"):
            allow(individual[key], True)
        if _menu_entry_enabled(individual["time_menu"]):
            for _minute, action in individual["time_actions"]:
                allow(action, True)
    if (
        ats_settings is not None
        and availability.ats_settings_allowed
        and _menu_entry_enabled(ats_settings["menu"])
    ):
        for _key, _label, action in ats_settings["session_actions"]:
            allow(action, True)
        allow(ats_settings["market"], True)
        allow(ats_settings["current"], True)
    if chosen not in allowed_actions:
        return
    selected_option = ""
    decision_event_type = "OPERATOR_OPERATION_DECISION"
    if action_start is not None and chosen == action_start:
        selected_option = "OPERATION_START"
    elif action_emergency_stop is not None and chosen == action_emergency_stop:
        selected_option = "EMERGENCY_STOP"
    elif action_set_exclusion is not None and chosen == action_set_exclusion:
        selected_option = "OPERATION_EXCLUDE"
        decision_event_type = "OPERATOR_SETTING_DECISION"
    elif action_clear_exclusion is not None and chosen == action_clear_exclusion:
        selected_option = "OPERATION_EXCLUSION_RELEASE"
        decision_event_type = "OPERATOR_SETTING_DECISION"
    elif chosen == early_close["routine"]:
        selected_option = "EARLY_CLOSE_ROUTINE"
    elif chosen == early_close["market"]:
        selected_option = "EARLY_CLOSE_MARKET"
    elif chosen == early_close["current"]:
        selected_option = "EARLY_CLOSE_CURRENT"
    elif chosen == early_close["profit_loss"]:
        selected_option = "EARLY_CLOSE_PROFIT_LOSS"
    elif chosen == early_close["carry"]:
        selected_option = "EARLY_CLOSE_CARRY"
    elif chosen == early_close["cancel"]:
        selected_option = "EARLY_CLOSE_CANCEL"
    elif chosen == individual["market"]:
        selected_option = "LIQUIDATION_MARKET"
    elif chosen == individual["current"]:
        selected_option = "LIQUIDATION_CURRENT"
    elif chosen == individual["carry"]:
        selected_option = "LIQUIDATION_CARRY"
    elif ats_settings is not None and chosen == ats_settings["market"]:
        selected_option = "ATS_LIQUIDATION_MARKET"
    elif ats_settings is not None and chosen == ats_settings["current"]:
        selected_option = "ATS_LIQUIDATION_CURRENT"

    _append_stock_context_decision(selected_option, decision_event_type, targets)
    if action_start is not None and chosen == action_start:
        callbacks.start()
    elif action_emergency_stop is not None and chosen == action_emergency_stop:
        callbacks.emergency_stop()
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
    elif action_open_charts is not None and chosen == action_open_charts:
        callbacks.open_charts()
    elif action_mock_create is not None and chosen == action_mock_create:
        callbacks.mock_create()
    elif _dispatch_early_close_action(
        chosen,
        early_close,
        apply_method=callbacks.early_close,
        apply_profit_loss=callbacks.early_close_profit_loss,
        cancel=callbacks.early_close_cancel,
        return_auto=callbacks.early_close_return_auto,
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
    _has_selected_provenance, has_non_emergency = selected_emergency_context_state(selected)
    status_filter = str(getattr(window, "_stock_status_filter", "") or "").strip().lower()
    excluded_view = status_filter == "excluded"
    running_view = status_filter in {"running", "stopped"}

    stock_register_target = _stock_register_context_instance_metadata(window)

    def window_callback(name: str):
        callback = getattr(window, name, None)
        return callback if callable(callback) else (lambda *_args, **_kwargs: None)

    callbacks = StockContextMenuCallbacks(
        select_all=window_callback("select_all_current_routine_stocks"),
        clear_selection=window_callback("clear_current_routine_stock_selection"),
        start=window_callback("start_selected_rows_auto_trades"),
        emergency_stop=(
            window_callback("emergency_stop_selected_auto_trade_stocks")
            if has_non_emergency
            else None
        ),
        stock_register=(
            (
                lambda target=stock_register_target: (
                    window.open_instance_stock_search_register_window(target)
                )
            )
            if not excluded_view and stock_register_target is not None
            else None
        ),
        unregister=(
            window_callback("unregister_selected_auto_trade_stocks")
            if not excluded_view and (stock_register_target is not None or has_selection)
            else None
        ),
        unregister_available=(
            lambda: all(
                inspect_stock_unregister_availability(
                    window,
                    Path(__file__).resolve().parent,
                    code,
                    name,
                ).allowed
                for _stock_dir, code, name in selected
            )
        ),
        early_close=lambda method: window_callback("apply_selected_early_close")(
            method,
            source="우클릭",
        ),
        early_close_profit_loss=window_callback("apply_selected_early_close_profit_loss"),
        early_close_cancel=window_callback("cancel_selected_early_close"),
        early_close_return_auto=window_callback("return_selected_early_close_to_auto"),
        individual_liquidation=window_callback(
            "apply_selected_individual_liquidation_method"
        ),
        open_charts=lambda: open_selected_stock_instance_charts(window, selected),
        time_change=window_callback("set_selected_individual_schedule_time"),
        time_reset=window_callback("reset_selected_schedule_to_global"),
        ats_state=lambda: window_callback("selected_manual_ats_state")(selected),
        ats_toggle=window_callback("set_selected_manual_ats_flag"),
        ats_liquidation_available=lambda: (
            window_callback("selected_manual_ats_liquidation_available")(selected)
        ),
        ats_liquidation=(
            lambda method, state, visible_keys, selected_sessions: (
                window_callback("execute_selected_manual_ats_liquidation")(
                    method,
                    state,
                    selected,
                    visible_keys,
                    selected_sessions,
                )
            )
        ),
        set_operation_exclusion=(
            window_callback("set_selected_stock_operation_exclusions")
            if running_view and not excluded_view
            else None
        ),
        clear_operation_exclusion=(
            window_callback("clear_selected_stock_operation_exclusions")
            if excluded_view
            else None
        ),
    )
    show_monitor_stock_context_menu(
        window,
        window.stock_table.viewport().mapToGlobal(pos),
        has_selection=has_selection,
        callbacks=callbacks,
        selected_modes=selected_modes,
        operation_excluded=operation_excluded,
        operation_exclusion_action=(
            "clear" if excluded_view else "set" if running_view else "none"
        ),
        stock_register_enabled=stock_register_target is not None,
        selected_targets=selected,
    )
