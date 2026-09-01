# -*- coding: utf-8 -*-
"""
gui_auto_trade_context_menu.py

자동매매설정창 종목 테이블 우클릭 메뉴 처리.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Iterable

from PyQt5.QtCore import QEvent, QObject, Qt
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
    individual_liquidation: Callable[[str, str], None]
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
    ats_execution_method_state: Callable[[], dict[str, object]] | None = None
    ats_execution_method_set: Callable[[str, str], None] | None = None
    ats_liquidation_available: Callable[[], bool] | None = None
    ats_liquidation: Callable[
        [str, dict[str, bool], tuple[str, ...], tuple[str, ...]],
        None,
    ] | None = None
    set_operation_exclusion: Callable[[], None] | None = None
    clear_operation_exclusion: Callable[[], None] | None = None
    trade_permission_label: Callable[[], str] | None = None
    trade_permission_available: Callable[[], bool] | None = None
    toggle_trade_permission: Callable[[], None] | None = None


@dataclass(frozen=True)
class StockContextMenuAvailability:
    """Read-only projection of the shared stock Context Menu commands."""

    review_managed: bool
    excluded_management: bool
    start_allowed: bool
    emergency_stop_allowed: bool
    exclusion_allowed: bool
    trade_permission_allowed: bool
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

    permission_available = True
    availability_getter = callbacks.trade_permission_available
    try:
        if callable(availability_getter):
            permission_available = bool(availability_getter())
    except Exception:
        permission_available = False
    trade_permission_allowed = bool(has_selection and permission_available)

    start_allowed = bool(has_selection)
    emergency_stop_allowed = bool(has_selection)
    early_close_allowed = bool(has_selection and not operation_excluded)
    early_close_cancel_allowed = bool(has_selection and not operation_excluded)
    individual_liquidation_allowed = bool(has_selection)
    time_management_allowed = bool(has_selection)
    ats_settings_allowed = bool(has_selection)
    stock_register_allowed = bool(
        has_selection
        if stock_register_enabled is None
        else stock_register_enabled
    )
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
    if has_selection and canonical_targets and len(canonical_targets) == len(targets):
        policy = (
            operation_policy
            if isinstance(operation_policy, dict)
            else _context_menu_operation_policy()
        )
        liquidation = policy.get("liquidation", {})
        liquidation = liquidation if isinstance(liquidation, dict) else {}
        liquidation_method = str(liquidation.get("method") or "이월").strip()
        liquidation_minutes = str(
            liquidation.get("minutes_before_regular_close") or "5"
        ).strip()
        early_decisions = [
            inspect_close_liquidation_availability(
                parent,
                stock_dir,
                code,
                intent=EARLY_CLOSE_REQUEST,
            )
            for stock_dir, code, _name in canonical_targets
        ]
        cancel_decisions = [
            inspect_close_liquidation_availability(
                parent,
                stock_dir,
                code,
                intent=EARLY_CLOSE_CANCEL,
            )
            for stock_dir, code, _name in canonical_targets
        ]
        liquidation_decisions = [
            inspect_close_liquidation_availability(
                parent,
                stock_dir,
                code,
                intent=INDIVIDUAL_LIQUIDATION,
                requested_method=liquidation_method,
                requested_minutes=liquidation_minutes,
            )
            for stock_dir, code, _name in canonical_targets
        ]
        early_close_allowed = bool(
            not operation_excluded
            and all(decision.allowed for decision in early_decisions)
        )
        early_close_cancel_allowed = bool(
            not operation_excluded
            and all(decision.allowed for decision in cancel_decisions)
        )
        individual_liquidation_allowed = all(
            decision.allowed for decision in liquidation_decisions
        )
        if not early_close_allowed:
            reasons["early_close"] = next(
                (
                    decision.reason_code
                    for decision in early_decisions
                    if not decision.allowed
                ),
                "EARLY_CLOSE_UNAVAILABLE",
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
        if not individual_liquidation_allowed:
            reasons["individual_liquidation"] = next(
                (
                    decision.reason_code
                    for decision in liquidation_decisions
                    if not decision.allowed
                ),
                "INDIVIDUAL_LIQUIDATION_UNAVAILABLE",
            )

    if review_managed:
        for action_key in (
            "start",
            "emergency_stop",
            "exclusion",
            "trade_permission",
            "early_close",
            "early_close_cancel",
            "individual_liquidation",
            "stock_register",
            "unregister",
            "chart",
        ):
            reasons[action_key] = "REVIEW_REQUIRED"
        start_allowed = False
        emergency_stop_allowed = False
        exclusion_allowed = False
        trade_permission_allowed = False
        early_close_allowed = False
        early_close_cancel_allowed = False
        individual_liquidation_allowed = False
        stock_register_allowed = False
        unregister_allowed = False
        chart_allowed = False
    elif excluded_management:
        reasons["emergency_stop"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        reasons["early_close"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        reasons["early_close_cancel"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        reasons["individual_liquidation"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        reasons["stock_register"] = "EXCLUDED_MANAGEMENT_RESTRICTED"
        emergency_stop_allowed = False
        early_close_allowed = False
        early_close_cancel_allowed = False
        individual_liquidation_allowed = False
        stock_register_allowed = False

    if not trade_permission_allowed and "trade_permission" not in reasons:
        reasons["trade_permission"] = "TRADE_PERMISSION_UNAVAILABLE"
    return StockContextMenuAvailability(
        review_managed=review_managed,
        excluded_management=excluded_management,
        start_allowed=start_allowed,
        emergency_stop_allowed=emergency_stop_allowed,
        exclusion_allowed=exclusion_allowed,
        trade_permission_allowed=trade_permission_allowed,
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
    menu = QMenu(parent)
    set_tooltips_visible = getattr(menu, "setToolTipsVisible", None)
    if callable(set_tooltips_visible):
        set_tooltips_visible(True)
    return menu


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
        "menu": individual_liquidation_menu,
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
    execution_method_state_getter: Callable[[], dict[str, object]] | None = None,
    execution_method_setter: Callable[[str, str], None] | None = None,
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
    method_menu = ats_menu.addMenu("주문방식")
    method_state_value = (
        execution_method_state_getter()
        if execution_method_state_getter is not None
        else {"ok": True, "execution_method": "ROUTINE", "mixed": False}
    )
    method_state = dict(method_state_value) if isinstance(method_state_value, dict) else {}
    current_method = str(method_state.get("execution_method") or "").strip().upper()
    method_actions: list[tuple[str, str, object]] = []
    for method_key, method_label in (
        ("ROUTINE", "루틴"),
        ("MARKET", "시장가"),
        ("CURRENT_PRICE", "현재가"),
    ):
        action = method_menu.addAction(method_label)
        selected = method_state.get("ok") is True and current_method == method_key
        action.setIcon(_menu_status_icon(selected))
        action.setProperty("atsExecutionMethod", method_key)
        action.setProperty("atsExecutionMethodCurrent", selected)
        method_actions.append((method_key, method_label, action))
    if method_state.get("ok") is not True:
        set_tool_tip = getattr(method_menu, "setToolTip", None)
        if callable(set_tool_tip):
            set_tool_tip("INVALID_ATS_EXECUTION_METHOD")

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
        "method_menu": method_menu,
        "method_state": method_state,
        "method_actions": tuple(method_actions),
        "execution_method_setter": execution_method_setter,
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

    for method_key, method_label, action in actions.get("method_actions", ()):
        if chosen == action:
            setter = actions.get("execution_method_setter")
            if callable(setter):
                setter(method_key, method_label)
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
    operation_exclusion_action: str | None = None,
    stock_register_enabled: bool | None = None,
    selected_targets: Iterable[tuple[object, str, str]] | None = None,
    selected_scope_emergency: bool | None = None,
    scheduled_excluded_management: bool = False,
) -> None:
    """Show the monitoring stock-row profile with the shared menu form."""

    menu = _new_stock_context_menu(parent)
    operation_policy = _context_menu_operation_policy()
    targets = list(selected_targets or [])
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

    action_trade_permission = None
    if callbacks.toggle_trade_permission is not None:
        label_getter = callbacks.trade_permission_label
        try:
            permission_label = label_getter() if callable(label_getter) else ""
        except Exception:
            permission_label = ""
        permission_label = str(permission_label or "").strip() or "거래권한 전환"
        action_trade_permission = menu.addAction(permission_label)
        action_trade_permission.setEnabled(availability.trade_permission_allowed)

    menu.addSeparator()
    early_close = _add_early_close_menu(
        menu,
        has_selection=has_selection,
        operation_excluded=operation_excluded,
        operation_policy=operation_policy,
    )
    early_close["menu"].setEnabled(
        availability.early_close_allowed
        or availability.early_close_cancel_allowed
    )
    for key in ("routine", "market", "current", "profit_loss", "carry"):
        early_close[key].setEnabled(availability.early_close_allowed)
    early_close["cancel"].setEnabled(availability.early_close_cancel_allowed)

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
            execution_method_state_getter=callbacks.ats_execution_method_state,
            execution_method_setter=callbacks.ats_execution_method_set,
            liquidation_available_getter=callbacks.ats_liquidation_available,
        )
        ats_settings["menu"].setEnabled(
            availability.ats_settings_allowed
            and _menu_entry_enabled(ats_settings["menu"])
        )

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

    chosen = menu.exec_(global_pos)
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
    allow(action_trade_permission, availability.trade_permission_allowed)
    allow(action_stock_register, availability.stock_register_allowed)
    allow(action_unregister, availability.unregister_allowed)
    allow(action_open_charts, availability.chart_allowed)
    allow(action_time_change, availability.time_management_allowed)
    allow(action_time_reset, availability.time_management_allowed)
    if _menu_entry_enabled(early_close["menu"]):
        if availability.early_close_allowed:
            for key in ("routine", "market", "current", "profit_loss", "carry"):
                allow(early_close[key], True)
        if availability.early_close_cancel_allowed:
            allow(early_close["cancel"], True)
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
        for _key, _label, action in ats_settings["method_actions"]:
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
    elif action_trade_permission is not None and chosen == action_trade_permission:
        selected_option = "TRADE_PERMISSION_TOGGLE"
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

    if selected_option:
        codes = [str(code or "").strip() for _path, code, _name in targets if str(code or "").strip()]
        names = [str(name or "").strip() for _path, _code, name in targets if str(name or "").strip()]
        correlation = {"stock_code": codes[0], "stock_name": names[0] if names else None} if len(codes) == 1 else {}
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
                    "TRADE_PERMISSION_TOGGLE",
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
                "target_count": len(targets),
            },
            **correlation,
        )
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
    elif action_trade_permission is not None and chosen == action_trade_permission:
        callbacks.toggle_trade_permission()
    elif action_unregister is not None and chosen == action_unregister:
        callbacks.unregister()
    elif action_open_charts is not None and chosen == action_open_charts:
        callbacks.open_charts()
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
        individual_liquidation=window_callback(
            "apply_selected_individual_liquidation_method"
        ),
        open_charts=lambda: open_selected_stock_instance_charts(window, selected),
        time_change=window_callback("set_selected_individual_schedule_time"),
        time_reset=window_callback("reset_selected_schedule_to_global"),
        ats_state=lambda: window_callback("selected_manual_ats_state")(selected),
        ats_toggle=window_callback("set_selected_manual_ats_flag"),
        ats_execution_method_state=lambda: window_callback(
            "selected_manual_ats_execution_method_state"
        )(selected),
        ats_execution_method_set=lambda method, label: window_callback(
            "set_selected_manual_ats_execution_method"
        )(method, label, selected),
        trade_permission_label=window_callback("selected_trade_permission_context_label"),
        trade_permission_available=(
            getattr(window, "selected_trade_permission_available", None)
            if callable(
                getattr(window, "selected_trade_permission_available", None)
            )
            else None
        ),
        toggle_trade_permission=window_callback("toggle_selected_trade_permission"),
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
