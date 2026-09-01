# -*- coding: utf-8 -*-
"""Common read-only stock instance chart window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from pathlib import Path
from typing import Any, Callable, Iterable
import weakref

from PyQt5 import sip
from PyQt5.QtCore import QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
)
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from candle_timeframe_aggregation import SEOUL_TIMEZONE, parse_market_datetime
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_BADGE_BORDER_COLOR,
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    apply_auto_trade_setting_protection_row_style,
    auto_trade_operation_identity_color,
    create_auto_trade_setting_status_item,
    profit_loss_value_color,
)
from gui_order_utils import DIRECTIONAL_NEUTRAL_COLOR, format_signed_money, format_signed_percent
from stock_code_contract import normalize_broker_stock_code, normalize_stock_code
from stock_instance_day_projection import (
    CHART_PROJECTION_NO_DAY_DATA,
    CHART_PROJECTION_NOT_READY,
    CHART_PROJECTION_REFRESH_FAILED,
    CHART_PROJECTION_RULES_UNAVAILABLE,
    CHART_PROJECTION_STALE_REJECTED,
    CHART_PROJECTION_VALID,
    project_stock_instance_day,
)
from pnl_ui_refresh import (
    PNL_REFRESH_INTERVAL_MS,
    project_current_stock_pnl,
    project_current_stock_pnl_snapshot,
)
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)


BUY_COLOR = QColor("#DC2626")
SELL_COLOR = QColor("#2563EB")
LINE_COLOR = QColor("#2F6BFF")
LIVE_PRICE_COLOR = QColor("#059669")
ACTUAL_BUY_FILL_COLOR = QColor("#16A34A")
ACTUAL_SELL_FILL_COLOR = QColor("#F97316")
AVERAGE_PRICE_COLOR = QColor("#F59E0B")
PROCESS_RAIL_COLOR = QColor("#6B7280")
CHART_OPEN_STOCK_CODE_COLOR = "#2563EB"
BASE_CHART_START_TIME = "09:00:00"
BASE_CHART_END_TIME = "15:30:00"
ProjectionProvider = Callable[[str, str], dict[str, Any]]
ChartFactory = Callable[[QWidget], "StockInstanceCloseChart"]
PROJECT_ROOT = Path(__file__).resolve().parent
_OPEN_STOCK_INSTANCE_CHARTS: dict[str, "StockInstanceChartWindow"] = {}
_PENDING_STOCK_INSTANCE_CHART_REFRESH_CODES: set[str] = set()
_STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED = False
_STOCK_INSTANCE_CHART_REFRESH_GENERATION = 0
_COMMON_PNL_REFRESH_TIMER: QTimer | None = None
_CHART_TILE_GAP = 8
_CHART_TILE_FRAME_TOLERANCE = 12
_CHART_TILE_MIN_OVERLAP_RATIO = 0.45


def _today_trade_date() -> str:
    return datetime.now(SEOUL_TIMEZONE).date().isoformat()


def _main_monitoring_owner(parent: QWidget | None) -> QWidget | None:
    """Resolve the stable MainWindow owner using the existing monitoring contract."""
    current = parent
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if (
            hasattr(current, "routine_table")
            and callable(
                getattr(current, "main_monitoring_auto_trade_operation_host", None)
            )
            and callable(getattr(current, "statusBar", None))
        ):
            return current
        parent_getter = getattr(current, "parent", None)
        try:
            current = persistent_feature_owner(current)
            if current is None:
                current = parent_getter() if callable(parent_getter) else None
        except RuntimeError:
            current = None
    return None


def _live_stock_instance_charts() -> list["StockInstanceChartWindow"]:
    live: list["StockInstanceChartWindow"] = []
    for stock_code, window in list(_OPEN_STOCK_INSTANCE_CHARTS.items()):
        try:
            deleted = sip.isdeleted(window)
        except RuntimeError:
            deleted = True
        if deleted:
            if _OPEN_STOCK_INSTANCE_CHARTS.get(stock_code) is window:
                _OPEN_STOCK_INSTANCE_CHARTS.pop(stock_code, None)
            continue
        live.append(window)
    return live


def _screen_available_geometry(screen: object) -> QRect | None:
    try:
        geometry = screen.availableGeometry()
    except (AttributeError, RuntimeError, TypeError):
        return None
    if not isinstance(geometry, QRect) or not geometry.isValid():
        return None
    return QRect(geometry)


def _ordered_chart_screens(
    parent: QWidget | None,
    *,
    screens: Iterable[object] | None = None,
    primary_screen: object | None = None,
) -> list[object]:
    application = QApplication.instance()
    candidates = list(screens) if screens is not None else (
        list(application.screens()) if application is not None else []
    )
    primary = primary_screen
    if primary is None and parent is not None:
        try:
            primary = parent.screen()
        except (AttributeError, RuntimeError, TypeError):
            primary = None
    if primary is None and application is not None and parent is not None:
        try:
            primary = application.screenAt(parent.frameGeometry().center())
        except (AttributeError, RuntimeError, TypeError):
            primary = None
    if primary is None and application is not None:
        primary = application.primaryScreen()

    ordered: list[object] = []
    seen: set[int] = set()
    for screen in [primary, *candidates]:
        if screen is None or id(screen) in seen:
            continue
        if _screen_available_geometry(screen) is None:
            continue
        seen.add(id(screen))
        ordered.append(screen)
    return ordered


def _chart_minimum_tile_size(windows: Iterable[object]) -> tuple[int, int]:
    minimum_width = 1
    minimum_height = 1
    for window in windows:
        try:
            minimum = window.minimumSize()
            minimum_width = max(
                minimum_width,
                int(minimum.width()),
                int(window.minimumWidth()),
            )
            minimum_height = max(
                minimum_height,
                int(minimum.height()),
                int(window.minimumHeight()),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return minimum_width, minimum_height


def _chart_frame_overhead(windows: Iterable[object]) -> tuple[int, int]:
    extra_width = 0
    extra_height = 0
    for window in windows:
        try:
            frame = window.frameGeometry()
            extra_width = max(extra_width, int(frame.width()) - int(window.width()))
            extra_height = max(extra_height, int(frame.height()) - int(window.height()))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return max(0, extra_width), max(0, extra_height)


def _move_chart_frame_to(window: object, x: int, y: int, available: QRect) -> None:
    """Move a shown top-level window by its frame top-left and keep it visible."""
    try:
        frame = window.frameGeometry()
        frame_width = max(1, int(frame.width()))
        frame_height = max(1, int(frame.height()))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        frame_width = max(1, int(window.width()))
        frame_height = max(1, int(window.height()))

    max_x = max(available.left(), available.right() - frame_width + 1)
    max_y = max(available.top(), available.bottom() - frame_height + 1)
    desired_x = min(max(int(x), available.left()), max_x)
    desired_y = min(max(int(y), available.top()), max_y)
    window.move(desired_x, desired_y)

    try:
        frame = window.frameGeometry()
        delta_x = desired_x - frame.left()
        delta_y = desired_y - frame.top()
        if delta_x or delta_y:
            window.move(window.x() + delta_x, window.y() + delta_y)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def _chart_tile_slots(
    screens: Iterable[object],
    *,
    frame_width: int,
    frame_height: int,
    gap: int,
) -> list[tuple[QRect, QRect]]:
    slots: list[tuple[QRect, QRect]] = []
    for screen in screens:
        available = _screen_available_geometry(screen)
        if available is None:
            continue
        columns = max(1, (available.width() + gap) // (frame_width + gap))
        rows = max(1, (available.height() + gap) // (frame_height + gap))
        for slot in range(columns * rows):
            row, column = divmod(slot, columns)
            slots.append(
                (
                    available,
                    QRect(
                        available.left() + column * (frame_width + gap),
                        available.top() + row * (frame_height + gap),
                        frame_width,
                        frame_height,
                    ),
                )
            )
    return slots


def _occupied_chart_tile_slots(
    slots: list[tuple[QRect, QRect]],
    windows: Iterable[object],
) -> tuple[set[int], int]:
    occupied: set[int] = set()
    unmatched = 0
    for window in windows:
        try:
            frame = QRect(window.frameGeometry())
        except (AttributeError, RuntimeError, TypeError):
            unmatched += 1
            continue
        matched = False
        for index, (_available, slot) in enumerate(slots):
            near_origin = (
                abs(frame.left() - slot.left()) <= _CHART_TILE_FRAME_TOLERANCE
                and abs(frame.top() - slot.top()) <= _CHART_TILE_FRAME_TOLERANCE
            )
            intersection = frame.intersected(slot)
            slot_area = max(1, slot.width() * slot.height())
            overlap_ratio = (
                intersection.width() * intersection.height() / slot_area
                if intersection.isValid()
                else 0.0
            )
            if near_origin or overlap_ratio >= _CHART_TILE_MIN_OVERLAP_RATIO:
                occupied.add(index)
                matched = True
        if not matched:
            unmatched += 1
    return occupied, unmatched


def place_new_stock_instance_charts(
    parent: QWidget | None,
    windows: Iterable[object],
    *,
    screens: Iterable[object] | None = None,
    primary_screen: object | None = None,
    gap: int = _CHART_TILE_GAP,
) -> list[object]:
    """Place only new charts into the first free common tile slots."""
    new_windows = [
        window
        for window in windows
        if window is not None
        and all(
            callable(getattr(window, method_name, None))
            for method_name in (
                "minimumSize",
                "minimumWidth",
                "minimumHeight",
                "resize",
                "move",
                "frameGeometry",
            )
        )
    ]
    if not new_windows:
        return new_windows

    ordered_screens = _ordered_chart_screens(
        parent,
        screens=screens,
        primary_screen=primary_screen,
    )
    if not ordered_screens:
        return new_windows

    minimum_width, minimum_height = _chart_minimum_tile_size(new_windows)
    for window in new_windows:
        try:
            window.resize(minimum_width, minimum_height)
        except (AttributeError, RuntimeError, TypeError):
            continue

    frame_extra_width, frame_extra_height = _chart_frame_overhead(new_windows)
    frame_width = minimum_width + frame_extra_width
    frame_height = minimum_height + frame_extra_height
    tile_gap = max(0, int(gap))
    slots = _chart_tile_slots(
        ordered_screens,
        frame_width=frame_width,
        frame_height=frame_height,
        gap=tile_gap,
    )
    new_ids = {id(window) for window in new_windows}
    existing_windows = [
        window for window in _live_stock_instance_charts() if id(window) not in new_ids
    ]
    occupied, overflow_index = _occupied_chart_tile_slots(slots, existing_windows)

    remaining: list[object] = []
    for window in new_windows:
        free_index = next(
            (index for index in range(len(slots)) if index not in occupied),
            None,
        )
        if free_index is None:
            remaining.append(window)
            continue
        available, slot = slots[free_index]
        _move_chart_frame_to(window, slot.left(), slot.top(), available)
        occupied.add(free_index)

    while remaining:
        screen = ordered_screens[overflow_index % len(ordered_screens)]
        available = _screen_available_geometry(screen)
        if available is None:
            overflow_index += 1
            continue
        max_x_offset = max(0, available.width() - frame_width)
        max_y_offset = max(0, available.height() - frame_height)
        cascade = overflow_index * 24
        x_offset = min(max_x_offset, cascade % (max_x_offset + 1))
        y_offset = min(max_y_offset, cascade % (max_y_offset + 1))
        _move_chart_frame_to(
            remaining.pop(0),
            available.left() + x_offset,
            available.top() + y_offset,
            available,
        )
        overflow_index += 1
    return new_windows


def stock_instance_chart_is_open(stock_code: str) -> bool:
    """Return whether ``stock_code`` currently has a reusable live chart."""
    registry_key = str(stock_code or "").strip()
    existing = _OPEN_STOCK_INSTANCE_CHARTS.get(registry_key)
    if existing is None:
        return False
    try:
        reusable = not sip.isdeleted(existing) and existing.isVisible()
    except RuntimeError:
        reusable = False
    if not reusable and _OPEN_STOCK_INSTANCE_CHARTS.get(registry_key) is existing:
        _OPEN_STOCK_INSTANCE_CHARTS.pop(registry_key, None)
    return reusable


def _open_stock_instance_chart_for_refresh(
    stock_code: object,
) -> "StockInstanceChartWindow | None":
    """Resolve a currently visible matching chart without creating one."""
    canonical_code = normalize_broker_stock_code(stock_code)
    if not canonical_code:
        return None
    for registry_key, window in list(_OPEN_STOCK_INSTANCE_CHARTS.items()):
        window_code = normalize_broker_stock_code(
            getattr(window, "stock_code", registry_key)
        )
        if window_code != canonical_code:
            continue
        try:
            reusable = not sip.isdeleted(window) and window.isVisible()
        except RuntimeError:
            reusable = False
        if reusable:
            return window
        if _OPEN_STOCK_INSTANCE_CHARTS.get(registry_key) is window:
            _OPEN_STOCK_INSTANCE_CHARTS.pop(registry_key, None)
    return None


def _drain_pending_stock_instance_chart_refreshes(
    generation: int | None = None,
) -> int:
    """Refresh each still-open invalidated stock chart at most once."""
    global _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED
    if (
        generation is not None
        and generation != _STOCK_INSTANCE_CHART_REFRESH_GENERATION
    ):
        return 0
    pending_codes = set(_PENDING_STOCK_INSTANCE_CHART_REFRESH_CODES)
    _PENDING_STOCK_INSTANCE_CHART_REFRESH_CODES.clear()
    _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED = False
    refreshed = 0
    for stock_code in sorted(pending_codes):
        window = _open_stock_instance_chart_for_refresh(stock_code)
        if window is None:
            continue
        try:
            window.refresh_projection(preserve_pnl_if_same_bar=True)
        except RuntimeError:
            # A WA_DeleteOnClose chart can disappear between registry lookup and call.
            continue
        refreshed += 1
    return refreshed


def queue_open_stock_instance_chart_refresh(stock_code: object) -> bool:
    """Coalesce a read-only refresh for an already-open matching stock chart."""
    global _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED
    canonical_code = normalize_broker_stock_code(stock_code)
    if not canonical_code or _open_stock_instance_chart_for_refresh(canonical_code) is None:
        return False
    _PENDING_STOCK_INSTANCE_CHART_REFRESH_CODES.add(canonical_code)
    if not _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED:
        _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED = True
        generation = _STOCK_INSTANCE_CHART_REFRESH_GENERATION
        QTimer.singleShot(
            0,
            lambda current_generation=generation: (
                _drain_pending_stock_instance_chart_refreshes(current_generation)
            ),
        )
    return True


def clear_pending_stock_instance_chart_refreshes() -> None:
    """Invalidate queued callbacks during application shutdown."""
    global _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED
    global _STOCK_INSTANCE_CHART_REFRESH_GENERATION
    _PENDING_STOCK_INSTANCE_CHART_REFRESH_CODES.clear()
    _STOCK_INSTANCE_CHART_REFRESH_DRAIN_SCHEDULED = False
    _STOCK_INSTANCE_CHART_REFRESH_GENERATION += 1


def _refresh_chart_open_code_views(owner: QWidget | None = None) -> None:
    """Repaint chart-entry code cells without reloading Runtime projections."""
    main_owner = _main_monitoring_owner(owner)
    if main_owner is not None:
        table = getattr(main_owner, "routine_table", None)
        viewport = getattr(table, "viewport", None)
        if callable(viewport):
            try:
                viewport().update()
            except RuntimeError:
                pass

    settings_windows: list[QWidget] = []
    settings = getattr(main_owner, "auto_trade_setting_window", None)
    if settings is not None:
        settings_windows.append(settings)
    application = QApplication.instance()
    if application is not None:
        for widget in application.topLevelWidgets():
            try:
                if widget.objectName() == "autoTradeSettingWindow":
                    settings_windows.append(widget)
            except RuntimeError:
                continue
    seen: set[int] = set()
    for settings_window in settings_windows:
        if id(settings_window) in seen:
            continue
        seen.add(id(settings_window))
        try:
            if sip.isdeleted(settings_window):
                continue
            refresh_styles = getattr(
                settings_window,
                "refresh_stock_instance_chart_open_code_styles",
                None,
            )
            if callable(refresh_styles):
                refresh_styles()
        except (AttributeError, RuntimeError, TypeError):
            continue


def _common_pnl_refresh_timer(*, create: bool = False) -> QTimer | None:
    global _COMMON_PNL_REFRESH_TIMER
    timer = _COMMON_PNL_REFRESH_TIMER
    if timer is not None:
        try:
            if not sip.isdeleted(timer):
                return timer
        except RuntimeError:
            pass
        _COMMON_PNL_REFRESH_TIMER = None
    if not create:
        return None
    application = QApplication.instance()
    timer = QTimer(application)
    timer.setObjectName("stockInstanceChartCommonPnlRefreshTimer")
    timer.setInterval(PNL_REFRESH_INTERVAL_MS)
    timer.timeout.connect(_refresh_live_chart_pnl)
    _COMMON_PNL_REFRESH_TIMER = timer
    return timer


def _refresh_live_chart_pnl() -> None:
    today = _today_trade_date()
    windows = []
    for window in _live_stock_instance_charts():
        try:
            if (
                sip.isdeleted(window)
                or _OPEN_STOCK_INSTANCE_CHARTS.get(window.stock_code) is not window
                or window.trade_date != today
            ):
                continue
            windows.append(window)
        except RuntimeError:
            continue
    if windows:
        try:
            snapshot = project_current_stock_pnl_snapshot(
                (window.stock_code for window in windows),
                project_root=PROJECT_ROOT,
            )
        except Exception:
            snapshot = {}
        for window in windows:
            try:
                if (
                    sip.isdeleted(window)
                    or _OPEN_STOCK_INSTANCE_CHARTS.get(window.stock_code) is not window
                ):
                    continue
                result = snapshot.get(window.stock_code)
                if isinstance(result, dict):
                    window.apply_pnl_result(result)
            except RuntimeError:
                continue
    _update_common_pnl_refresh_timer()


def _update_common_pnl_refresh_timer() -> None:
    has_today_chart = any(
        window.trade_date == _today_trade_date()
        for window in _live_stock_instance_charts()
    )
    timer = _common_pnl_refresh_timer(create=has_today_chart)
    if timer is None:
        return
    if has_today_chart:
        if not timer.isActive():
            timer.start()
    elif timer.isActive():
        timer.stop()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_chart_pnl_display(
    amount: Any,
    rate: Any,
    *,
    available: bool = True,
) -> str:
    """Format the chart's compact PnL value without changing PnL semantics."""

    amount_value = _finite_number(amount) if available else None
    rate_value = _finite_number(rate) if available else None
    if amount_value is None:
        amount_value = 0.0
        rate_value = 0.0
    elif rate_value is None:
        rate_value = 0.0
    return (
        f"{format_signed_money(amount_value)}"
        f"({format_signed_percent(rate_value, digits=2)})"
    )


def _nonnegative_count(value: Any, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        count = int(value)
    except (TypeError, ValueError):
        return fallback
    return count if count >= 0 else fallback


def _stock_name_from_repository(stock_code: str) -> str:
    """Resolve a read-only stock-name fallback for title identity."""

    code = str(stock_code or "").strip()
    if not code:
        return ""
    try:
        from runtime_io import read_json_dict
        from stock_repository import StockRepository

        stock_dir = StockRepository(project_root=PROJECT_ROOT).resolve_stock_dir(code)
        if not stock_dir.exists():
            return ""
        config = read_json_dict(stock_dir / "config.json")
        configured_name = str(config.get("name") or "").strip()
        if configured_name:
            return configured_name
        directory_name = stock_dir.name.partition("_")[2].strip()
        return directory_name
    except (OSError, RuntimeError, TypeError, ValueError):
        return ""


@dataclass(frozen=True)
class StockOperationHeaderDisplay:
    status: str
    method: str
    liquidation: str
    status_color: str
    method_color: str
    liquidation_color: str
    identity_color: str

    @property
    def values(self) -> tuple[str, str, str]:
        return self.status, self.method, self.liquidation

    @property
    def colors(self) -> tuple[str, str, str]:
        return self.status_color, self.method_color, self.liquidation_color


def _auto_trade_setting_default_text_color(owner: QWidget | None = None) -> str:
    """Resolve the text color inherited by an unstyled Settings table item."""

    main_owner = _main_monitoring_owner(owner)
    candidates: list[QWidget] = []
    if main_owner is not None:
        settings_window = getattr(main_owner, "auto_trade_setting_window", None)
        if isinstance(settings_window, QWidget):
            candidates.append(settings_window)
    app = QApplication.instance()
    if app is not None:
        candidates.extend(
            window
            for window in app.topLevelWidgets()
            if window.objectName() == "autoTradeSettingWindow"
        )
    for candidate in candidates:
        table = getattr(candidate, "stock_table", None)
        if isinstance(table, QWidget):
            return table.palette().color(QPalette.Text).name()
    if app is not None:
        return app.palette().color(QPalette.Text).name()
    return "#000000"


def _settings_item_text_color(item: QTableWidgetItem, fallback: str) -> str:
    brush = item.foreground()
    if brush.style() == Qt.NoBrush:
        return fallback
    return brush.color().name()


def _fallback_stock_operation_header_display(
    owner: QWidget | None = None,
) -> StockOperationHeaderDisplay:
    default_color = _auto_trade_setting_default_text_color(owner)
    return StockOperationHeaderDisplay(
        status="-",
        method="-",
        liquidation="-",
        status_color=default_color,
        method_color=default_color,
        liquidation_color=default_color,
        identity_color=auto_trade_operation_identity_color(
            operation_excluded=False,
            review_managed=False,
            emergency_stopped=False,
            current_running=False,
        ),
    )


def project_stock_operation_header_display(
    stock_code: str,
    owner: QWidget | None = None,
) -> StockOperationHeaderDisplay:
    """Project the exact Settings text and foreground-style contracts."""

    code = str(stock_code or "").strip()
    if not code:
        return _fallback_stock_operation_header_display(owner)
    try:
        from gui_auto_trade_policy import (
            auto_trade_setting_liquidation_active,
            auto_trade_setting_current_session_trade_started,
            auto_trade_setting_display_status_for_current_session,
            auto_trade_setting_liquidation_text,
            auto_trade_setting_method_text,
            auto_trade_setting_trade_started,
            effective_liquidation_policy_for_config,
        )
        from gui_auto_trade_integrity import (
            is_emergency_stopped_state,
            is_operation_excluded,
            is_review_required_state,
        )
        from gui_auto_trade_run_control import (
            auto_trade_running_registered_operation_targets,
        )
        from operation_policy_gate import is_emergency_stop, read_operation_state
        from gui_common_utils import safe_int_value
        from gui_config_utils import default_config
        from gui_order_utils import pending_order_side_quantities
        from runtime_io import read_json_dict
        from stock_repository import StockRepository

        stock_dir = StockRepository(project_root=PROJECT_ROOT).resolve_stock_dir(code)
        if not stock_dir.exists():
            return _fallback_stock_operation_header_display(owner)
        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()
        state = read_json_dict(stock_dir / "state.json")
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(
            stock_dir,
            state,
        )
        trade_started = auto_trade_setting_trade_started(state)
        session_owner = _main_monitoring_owner(owner) or owner
        current_session_trade_started = auto_trade_setting_current_session_trade_started(
            session_owner,
            trade_started,
            code,
        )
        display_status = auto_trade_setting_display_status_for_current_session(
            state,
            config,
            holding_qty=holding_qty,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
            current_session_trade_started=current_session_trade_started,
            persisted_trade_started=trade_started,
        )
        method_text = auto_trade_setting_method_text(display_status, config, state)
        liquidation_text = auto_trade_setting_liquidation_text(
            config,
            display_status,
            state,
            holding_qty=holding_qty,
        )
        status_cell_active = (
            current_session_trade_started
            and display_status not in ("긴급정지", "검토종목")
        )
        method_cell_active = (
            status_cell_active
            and display_status not in ("감시/대기", "-", "")
        )
        liquidation_has_policy = str(liquidation_text).strip() not in ("", "-")
        _policy, liquidation_is_individual = effective_liquidation_policy_for_config(
            config,
            state,
        )
        liquidation_cell_active = (
            current_session_trade_started
            and holding_qty > 0
            and auto_trade_setting_liquidation_active(
                config,
                holding_qty,
                display_status=display_status,
                state=state,
            )
            and liquidation_has_policy
        )

        status_item = create_auto_trade_setting_status_item(display_status)
        method_item = QTableWidgetItem(method_text)
        liquidation_item = QTableWidgetItem(liquidation_text)
        apply_auto_trade_setting_activity_style(status_item, status_cell_active)
        if display_status in ("긴급정지", "검토종목"):
            status_item = create_auto_trade_setting_status_item(display_status)
        apply_auto_trade_setting_activity_style(method_item, method_cell_active)
        apply_auto_trade_setting_liquidation_style(
            liquidation_item,
            liquidation_cell_active,
            liquidation_has_policy,
            liquidation_is_individual,
        )
        review_required = is_review_required_state(state)
        operation_excluded = is_operation_excluded(config)
        for item in (status_item, method_item, liquidation_item):
            apply_auto_trade_setting_protection_row_style(
                item,
                review_required=review_required,
                operation_excluded=operation_excluded,
            )

        default_color = _auto_trade_setting_default_text_color(owner)
        current_running = False
        global_emergency_stopped = False
        if session_owner is not None:
            resolved_stock_dir = str(stock_dir.resolve())
            current_running = any(
                str(Path(running_stock_dir).resolve()) == resolved_stock_dir
                for running_stock_dir, _running_code, _running_name in (
                    auto_trade_running_registered_operation_targets(session_owner)
                )
            )
            global_emergency_stopped = is_emergency_stop(read_operation_state())
        return StockOperationHeaderDisplay(
            status=display_status,
            method=method_text,
            liquidation=liquidation_text,
            status_color=_settings_item_text_color(status_item, default_color),
            method_color=_settings_item_text_color(method_item, default_color),
            liquidation_color=_settings_item_text_color(
                liquidation_item,
                default_color,
            ),
            identity_color=auto_trade_operation_identity_color(
                operation_excluded=operation_excluded,
                review_managed=review_required,
                emergency_stopped=(
                    is_emergency_stopped_state(state)
                    or global_emergency_stopped
                ),
                current_running=current_running,
            ),
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return _fallback_stock_operation_header_display(owner)


def _auto_trade_setting_badge_font(owner: QWidget | None = None) -> QFont:
    """Resolve the font inherited by the visible Settings badges."""

    main_owner = _main_monitoring_owner(owner)
    candidates: list[QWidget] = []
    if main_owner is not None:
        settings_window = getattr(main_owner, "auto_trade_setting_window", None)
        if isinstance(settings_window, QWidget):
            candidates.append(settings_window)
    app = QApplication.instance()
    if app is not None:
        candidates.extend(
            window
            for window in app.topLevelWidgets()
            if window.objectName() == "autoTradeSettingWindow"
        )
    for candidate in candidates:
        table = getattr(candidate, "routine_table", None)
        if isinstance(table, QWidget):
            return QFont(table.font())
        return QFont(candidate.font())
    if app is not None:
        return QFont(app.font())
    return QFont()


def _stock_operation_header_segment_widths(font: QFont) -> dict[str, int]:
    """Return stable pixel widths based on the longest supported display values."""

    metrics = QFontMetrics(font)
    samples = {
        "status": ("감시/대기", "매수/매도", "자동마감", "조기마감", "긴급정지", "검토종목"),
        "method": ("루틴", "시장가", "현재가", "익/손", "이월"),
        "liquidation": ("-", "이월", "시장가", "100분/시장가", "100분/현재가"),
    }
    glyph_safety_width = 4
    return {
        key: max(metrics.horizontalAdvance(value) for value in values)
        + glyph_safety_width
        for key, values in samples.items()
    }


def _stock_instance_chart_pnl_display_width(font: QFont) -> int:
    """Reserve the chart header width for the largest supported PnL display."""

    metrics = QFontMetrics(font)
    samples = (
        "+99,999,999(+99.99%)",
        "-99,999,999(-99.99%)",
    )
    return max(metrics.horizontalAdvance(value) for value in samples) + 4


def _button_content_vertical_margins(button: QPushButton) -> tuple[int, int]:
    """Split the active Qt button style's vertical content margin evenly."""

    total_margin = max(
        0,
        int(button.style().pixelMetric(QStyle.PM_ButtonMargin, None, button)),
    )
    top_margin = total_margin // 2
    return top_margin, total_margin - top_margin


def _operation_info_vertical_margins(button: QPushButton) -> tuple[int, int]:
    """Keep the operation outline one pixel tighter than button content padding."""

    button_top, button_bottom = _button_content_vertical_margins(button)
    return max(1, button_top - 1), max(1, button_bottom - 1)


def _build_window_title(
    *,
    stock_code: object,
    stock_name: object = "",
    instance_name: object = "",
    operation_title: object = "",
    bar_title: object = "",
    buy_count: object = 0,
    sell_count: object = 0,
) -> str:
    """Build the single taskbar-friendly StockInstanceChartWindow title."""

    code = str(stock_code or "").strip()
    name = str(stock_name or "").strip()
    identity = " ".join(part for part in (code, name) if part) or "-"
    return " / ".join(
        (
            identity,
            str(instance_name or "").strip() or "-",
            str(operation_title or "").strip() or "-",
            str(bar_title or "").strip() or "-",
            f"매수 {_nonnegative_count(buy_count)}",
            f"매도 {_nonnegative_count(sell_count)}",
        )
    )


class ExecutionProcessRail(QWidget):
    """Compact, read-only rows for persisted execution processes."""

    processSelected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockInstanceExecutionProcessRail")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.processes: list[dict[str, Any]] = []
        self.selected_execution_process_id = ""
        self._row_rects: list[tuple[QRectF, str]] = []
        self.setFixedHeight(0)
        self.hide()

    @staticmethod
    def _row_text(process: dict[str, Any]) -> str:
        side = str(process.get("side") or "-").strip().upper()
        option = str(process.get("option_summary") or "-").strip()
        completed = _nonnegative_count(process.get("child_completed"), 0)
        total = _nonnegative_count(process.get("child_total"), 0)
        status = str(process.get("status") or "-").strip().upper()
        status_text = {
            "COMPLETED": "완료",
            "PARTIAL": "부분체결",
            "APPROVED": "승인",
            "ORDERED": "주문",
        }.get(status, status)
        return f"{side} | {option} | {status_text} {completed}/{total}"

    @staticmethod
    def _tooltip_text(process: dict[str, Any]) -> str:
        lines = [ExecutionProcessRail._row_text(process)]
        source_kind = str(process.get("source_kind") or "").strip()
        source_id = str(
            process.get("source_signal_id")
            or process.get("source_command_id")
            or ""
        ).strip()
        if source_kind or source_id:
            lines.append(f"근거: {' / '.join(part for part in (source_kind, source_id) if part)}")
        children = process.get("children", [])
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                index = child.get("child_sequence_index")
                total = child.get("child_sequence_total")
                kind = str(child.get("child_kind") or "-").strip()
                status = str(child.get("status") or "-").strip()
                fills = child.get("fill_ids", [])
                fill_count = len(fills) if isinstance(fills, list) else 0
                lines.append(f"{index}/{total} {kind} · {status} · Fill {fill_count}건")
        return "\n".join(lines)

    def set_processes(self, processes: Any) -> None:
        self.processes = [
            dict(item)
            for item in processes
            if isinstance(item, dict)
            and str(item.get("execution_process_id") or "").strip()
        ] if isinstance(processes, list) else []
        valid_ids = {
            str(item.get("execution_process_id") or "").strip()
            for item in self.processes
        }
        if self.selected_execution_process_id not in valid_ids:
            self.selected_execution_process_id = ""
        if not self.processes:
            self._row_rects = []
            self.setFixedHeight(0)
            self.hide()
            return
        self.setFixedHeight(min(86, 8 + (20 * len(self.processes))))
        self.show()
        self.update()

    def select_process(self, execution_process_id: object) -> None:
        process_id = str(execution_process_id or "").strip()
        self.selected_execution_process_id = process_id
        tooltip = ""
        for process in self.processes:
            if str(process.get("execution_process_id") or "").strip() == process_id:
                tooltip = self._tooltip_text(process)
                break
        self.setToolTip(tooltip)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(QPen(self.palette().midlight().color(), 1))
        painter.drawLine(0, 0, self.width(), 0)
        self._row_rects = []
        if not self.processes:
            return
        row_height = max(15.0, min(20.0, (self.height() - 6.0) / len(self.processes)))
        font = QFont(self.font())
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        for index, process in enumerate(self.processes):
            process_id = str(process.get("execution_process_id") or "").strip()
            rect = QRectF(8, 3 + index * row_height, max(1, self.width() - 16), row_height)
            self._row_rects.append((rect, process_id))
            if process_id == self.selected_execution_process_id:
                painter.fillRect(rect, QColor("#EFF6FF"))
                painter.setPen(QPen(QColor("#2563EB"), 1))
            else:
                painter.setPen(PROCESS_RAIL_COLOR)
            painter.drawText(rect.adjusted(5, 0, -5, 0), Qt.AlignLeft | Qt.AlignVCenter, self._row_text(process))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        for rect, process_id in self._row_rects:
            if rect.contains(event.pos()):
                self.select_process(process_id)
                self.processSelected.emit(process_id)
                event.accept()
                return
        super().mousePressEvent(event)


class StockInstanceCloseChart(QWidget):
    """Paint one day of close prices and canonical BUY/SELL markers."""

    actualFillMarkerSelected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockInstanceCloseChart")
        self.setMinimumSize(620, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.close_series: list[tuple[datetime, float]] = []
        self.buy_series: list[tuple[datetime, float]] = []
        self.sell_series: list[tuple[datetime, float]] = []
        self.actual_buy_fill_series: list[tuple[datetime, float]] = []
        self.actual_sell_fill_series: list[tuple[datetime, float]] = []
        self.actual_fill_marker_records: list[dict[str, Any]] = []
        self.process_rails: list[dict[str, Any]] = []
        self.average_price: float | None = None
        self.selected_actual_fill_marker_id = ""
        self.selected_execution_process_id = ""
        self.live_price_point: tuple[datetime, float] | None = None
        self.live_price_data_quality = ""
        self.fixed_time_range: tuple[datetime, datetime] | None = None
        self.visible_time_ranges: list[tuple[datetime, datetime]] = []
        self.timeframe_minutes: int | None = None
        self.empty_message = "표시할 기준봉 데이터가 없습니다."

    @staticmethod
    def _series_from(
        records: Any,
        *,
        time_key: str,
        value_key: str,
    ) -> list[tuple[datetime, float]]:
        if not isinstance(records, list):
            return []
        by_timestamp: dict[datetime, float] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            bar_time = parse_market_datetime(record.get(time_key))
            value = _finite_number(record.get(value_key))
            if bar_time is None or value is None or value <= 0:
                continue
            # Canonical candles are already unique, but a defensive last-valid
            # projection keeps duplicate timestamps from creating vertical lines.
            by_timestamp[bar_time] = value
        return sorted(by_timestamp.items(), key=lambda item: item[0])

    def set_projection(
        self,
        candles: Any,
        buy_markers: Any,
        sell_markers: Any,
        *,
        empty_message: str = "표시할 기준봉 데이터가 없습니다.",
        x_range_start: Any = None,
        x_range_end: Any = None,
        visible_time_ranges: Any = None,
        timeframe_minutes: Any = None,
        actual_fill_markers: Any = None,
        process_rails: Any = None,
        average_price: Any = None,
    ) -> None:
        self.close_series = self._series_from(
            candles,
            time_key="bar_time",
            value_key="close",
        )
        self.buy_series = self._series_from(
            buy_markers,
            time_key="signal_bar_time",
            value_key="signal_bar_close",
        )
        self.sell_series = self._series_from(
            sell_markers,
            time_key="signal_bar_time",
            value_key="signal_bar_close",
        )
        self.actual_fill_marker_records = []
        if isinstance(actual_fill_markers, list):
            for marker in actual_fill_markers:
                if not isinstance(marker, dict):
                    continue
                occurred_at = parse_market_datetime(marker.get("occurred_at"))
                price = _finite_number(marker.get("filled_price"))
                side = str(marker.get("side") or "").strip().upper()
                if occurred_at is None or price is None or price <= 0 or side not in {"BUY", "SELL"}:
                    continue
                normalized = dict(marker)
                normalized["_occurred_at"] = occurred_at
                normalized["_filled_price"] = price
                self.actual_fill_marker_records.append(normalized)
        self.actual_fill_marker_records.sort(
            key=lambda marker: (
                marker["_occurred_at"],
                str(marker.get("fill_id") or ""),
            )
        )
        self.process_rails = [dict(item) for item in process_rails if isinstance(item, dict)] if isinstance(process_rails, list) else []
        projected_average = _finite_number(average_price)
        self.average_price = projected_average if projected_average is not None and projected_average > 0 else None
        parsed_start = parse_market_datetime(x_range_start)
        parsed_end = parse_market_datetime(x_range_end)
        self.fixed_time_range = (
            (parsed_start, parsed_end)
            if parsed_start is not None
            and parsed_end is not None
            and parsed_start < parsed_end
            else None
        )
        self.visible_time_ranges = []
        if isinstance(visible_time_ranges, list):
            for item in visible_time_ranges:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                visible_start = parse_market_datetime(item[0])
                visible_end = parse_market_datetime(item[1])
                if visible_start is not None and visible_end is not None and visible_start < visible_end:
                    self.visible_time_ranges.append((visible_start, visible_end))
        active_ranges = list(self.visible_time_ranges)
        if not active_ranges and self.fixed_time_range is not None:
            active_ranges = [self.fixed_time_range]
        if active_ranges:
            def visible(item: tuple[datetime, float]) -> bool:
                return any(start <= item[0] <= end for start, end in active_ranges)

            self.close_series = [
                item for item in self.close_series if visible(item)
            ]
            self.buy_series = [
                item for item in self.buy_series if visible(item)
            ]
            self.sell_series = [
                item for item in self.sell_series if visible(item)
            ]
            self.actual_fill_marker_records = [
                marker
                for marker in self.actual_fill_marker_records
                if visible((marker["_occurred_at"], marker["_filled_price"]))
            ]
        self.actual_buy_fill_series = [
            (marker["_occurred_at"], marker["_filled_price"])
            for marker in self.actual_fill_marker_records
            if marker.get("side") == "BUY"
        ]
        self.actual_sell_fill_series = [
            (marker["_occurred_at"], marker["_filled_price"])
            for marker in self.actual_fill_marker_records
            if marker.get("side") == "SELL"
        ]
        self.timeframe_minutes = (
            int(timeframe_minutes)
            if isinstance(timeframe_minutes, int)
            and not isinstance(timeframe_minutes, bool)
            and timeframe_minutes > 0
            else None
        )
        self.empty_message = str(empty_message or "표시할 기준봉 데이터가 없습니다.")
        self.update()

    def select_execution_process(self, execution_process_id: object) -> None:
        process_id = str(execution_process_id or "").strip()
        if self.selected_execution_process_id == process_id:
            return
        self.selected_execution_process_id = process_id
        self.selected_actual_fill_marker_id = ""
        self.update()

    def set_live_price_projection(
        self,
        market_datetime: Any,
        price: Any,
        *,
        data_quality: object = "NORMAL",
    ) -> bool:
        parsed_time = parse_market_datetime(market_datetime)
        parsed_price = _finite_number(price)
        if parsed_time is None or parsed_price is None or parsed_price <= 0:
            return self.clear_live_price_projection()
        projected = (parsed_time, parsed_price)
        quality = str(data_quality or "").strip().upper() or "NORMAL"
        if (
            self.live_price_point == projected
            and self.live_price_data_quality == quality
        ):
            return False
        self.live_price_point = projected
        self.live_price_data_quality = quality
        self.update()
        return True

    def clear_live_price_projection(self) -> bool:
        if self.live_price_point is None and not self.live_price_data_quality:
            return False
        self.live_price_point = None
        self.live_price_data_quality = ""
        self.update()
        return True

    def _line_segments(self) -> list[list[tuple[datetime, float]]]:
        if not self.close_series:
            return []

        session_ranges = list(self.visible_time_ranges)
        if not session_ranges and self.fixed_time_range is not None:
            session_ranges = [self.fixed_time_range]

        def session_index(bar_time: datetime) -> int:
            for index, (start, end) in enumerate(session_ranges):
                if start <= bar_time <= end:
                    return index
            return -1

        segments: list[list[tuple[datetime, float]]] = []
        current: list[tuple[datetime, float]] = []
        previous_time: datetime | None = None
        previous_session = -1
        for item in self.close_series:
            item_session = session_index(item[0])
            if previous_time is not None and (
                item[0].date() != previous_time.date()
                or item_session != previous_session
            ):
                segments.append(current)
                current = []
            current.append(item)
            previous_time = item[0]
            previous_session = item_session
        if current:
            segments.append(current)
        return segments

    def _plot_rect(self) -> QRectF:
        return QRectF(
            92,
            24,
            max(1, self.width() - 126),
            max(1, self.height() - 66),
        )

    def _time_range(self) -> tuple[datetime, datetime] | None:
        if self.fixed_time_range is not None:
            return self.fixed_time_range
        if not self.close_series:
            return None
        times = [bar_time for bar_time, _value in self.close_series]
        return min(times), max(times)

    def _scale_values(self) -> tuple[datetime, datetime, float, float] | None:
        time_range = self._time_range()
        if time_range is None:
            return None
        values = [value for _bar_time, value in self.close_series]
        values.extend(value for _bar_time, value in self.buy_series)
        values.extend(value for _bar_time, value in self.sell_series)
        values.extend(value for _bar_time, value in self.actual_buy_fill_series)
        values.extend(value for _bar_time, value in self.actual_sell_fill_series)
        if self.live_price_point is not None:
            values.append(self.live_price_point[1])
        if self.average_price is not None:
            values.append(self.average_price)
        if not values:
            return None
        low = min(values)
        high = max(values)
        if high == low:
            padding = max(abs(high) * 0.005, 1.0)
        else:
            padding = max((high - low) * 0.08, 0.01)
        return time_range[0], time_range[1], low - padding, high + padding

    def _x_axis_label_points(self, plot: QRectF) -> list[tuple[datetime, float]]:
        time_range = self._time_range()
        if time_range is None:
            return []
        minimum_time, maximum_time = time_range
        time_span = (maximum_time - minimum_time).total_seconds()
        if self.fixed_time_range is not None:
            label_times = [
                minimum_time + (maximum_time - minimum_time) * (index / 4)
                for index in range(5)
            ]
        elif len(self.close_series) == 1:
            label_times = [self.close_series[0][0]]
        else:
            label_count = min(5, len(self.close_series))
            label_indexes = sorted(
                {
                    round(index * (len(self.close_series) - 1) / (label_count - 1))
                    for index in range(label_count)
                }
            )
            label_times = [self.close_series[index][0] for index in label_indexes]
        return [
            (
                bar_time,
                plot.center().x()
                if time_span <= 0
                else plot.left()
                + (bar_time - minimum_time).total_seconds() / time_span * plot.width(),
            )
            for bar_time in label_times
        ]

    def _draw_x_axis_labels(
        self,
        painter: QPainter,
        plot: QRectF,
        text_color: QColor,
    ) -> None:
        label_font = QFont(painter.font())
        label_font.setPointSize(max(7, label_font.pointSize() - 1))
        painter.setFont(label_font)
        painter.setPen(text_color)
        for bar_time, x in self._x_axis_label_points(plot):
            painter.drawText(
                QRectF(x - 32, plot.bottom() + 7, 64, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                bar_time.strftime("%H:%M"),
            )

    def position_for(
        self,
        bar_time: Any,
        close: Any,
        plot: QRectF | None = None,
    ) -> QPointF | None:
        """Return the exact chart coordinate used by both lines and markers."""
        scales = self._scale_values()
        parsed_time = parse_market_datetime(bar_time)
        parsed_close = _finite_number(close)
        if scales is None or parsed_time is None or parsed_close is None:
            return None
        minimum_time, maximum_time, low, high = scales
        target = plot or self._plot_rect()
        time_span = (maximum_time - minimum_time).total_seconds()
        x_ratio = (
            0.5
            if time_span <= 0
            else (parsed_time - minimum_time).total_seconds() / time_span
        )
        y_ratio = (parsed_close - low) / max(high - low, 1e-12)
        return QPointF(
            target.left() + x_ratio * target.width(),
            target.bottom() - y_ratio * target.height(),
        )

    @staticmethod
    def _price_text(value: float) -> str:
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _draw_marker(
        painter: QPainter,
        point: QPointF,
        color: QColor,
    ) -> None:
        painter.setPen(QPen(color.darker(115), 1))
        painter.setBrush(color)
        painter.drawEllipse(point, 5.0, 5.0)

    @staticmethod
    def _draw_actual_fill_marker(
        painter: QPainter,
        point: QPointF,
        color: QColor,
        *,
        selected: bool,
    ) -> None:
        if selected:
            painter.setPen(QPen(color.darker(125), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(point, 7.0, 7.0)
        painter.setPen(QPen(color.darker(120), 1))
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(point.x(), point.y() - 4.5),
                    QPointF(point.x() + 4.5, point.y()),
                    QPointF(point.x(), point.y() + 4.5),
                    QPointF(point.x() - 4.5, point.y()),
                ]
            )
        )

    @classmethod
    def _signal_label_text(cls, value: float) -> str:
        return cls._price_text(value)

    @staticmethod
    def _draw_plot_axes(
        painter: QPainter,
        plot: QRectF,
        axis_color: QColor,
    ) -> None:
        painter.setPen(QPen(axis_color, 1))
        painter.drawLine(
            QPointF(plot.left(), plot.top()),
            QPointF(plot.left(), plot.bottom()),
        )
        painter.drawLine(
            QPointF(plot.left(), plot.bottom()),
            QPointF(plot.right(), plot.bottom()),
        )

    def _draw_signal_label(
        self,
        painter: QPainter,
        plot: QRectF,
        point: QPointF,
        value: float,
        color: QColor,
        *,
        above: bool,
    ) -> None:
        width = 104.0
        height = 18.0
        x = point.x() + 8.0
        if x + width > plot.right():
            x = point.x() - width - 8.0
        y = point.y() - height - 7.0 if above else point.y() + 7.0
        if y < plot.top():
            y = point.y() + 7.0
        if y + height > plot.bottom():
            y = point.y() - height - 7.0
        label_font = QFont(painter.font())
        label_font.setPointSize(max(7, label_font.pointSize() - 1))
        label_font.setWeight(QFont.Medium)
        painter.setFont(label_font)
        painter.setPen(color)
        painter.drawText(
            QRectF(x, y, width, height),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._signal_label_text(value),
        )
        painter.setFont(self.font())

    def _draw_live_price_projection(
        self,
        painter: QPainter,
        plot: QRectF,
    ) -> None:
        if self.live_price_point is None:
            return
        market_datetime, price = self.live_price_point
        point = self.position_for(market_datetime, price, plot)
        if point is None or not plot.adjusted(-1, -1, 1, 1).contains(point):
            return
        painter.setPen(QPen(LIVE_PRICE_COLOR, 2))
        painter.setBrush(self.palette().base())
        painter.drawEllipse(point, 4.5, 4.5)
        label = self._price_text(price)
        if self.live_price_data_quality == "UNCERTAIN":
            label += " (UNCERTAIN)"
        label_font = QFont(painter.font())
        label_font.setPointSize(max(7, label_font.pointSize() - 1))
        label_font.setWeight(QFont.Medium)
        painter.setFont(label_font)
        label_width = QFontMetrics(label_font).horizontalAdvance(label) + 8
        label_height = QFontMetrics(label_font).height() + 2
        x = min(point.x() + 8, plot.right() - label_width)
        x = max(plot.left(), x)
        y = max(plot.top(), point.y() - label_height - 6)
        painter.setPen(LIVE_PRICE_COLOR)
        painter.drawText(
            QRectF(x, y, label_width, label_height),
            Qt.AlignLeft | Qt.AlignVCenter,
            label,
        )
        painter.setFont(self.font())

    def _draw_average_price_projection(self, painter: QPainter, plot: QRectF) -> None:
        if self.average_price is None:
            return
        point = self.position_for(self._time_range()[0] if self._time_range() else None, self.average_price, plot)
        if point is None:
            return
        y = point.y()
        painter.setPen(QPen(AVERAGE_PRICE_COLOR, 1.5, Qt.DashLine))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        label = f"평단 {self._price_text(self.average_price)}"
        font = QFont(painter.font())
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        width = QFontMetrics(font).horizontalAdvance(label) + 8
        painter.setPen(AVERAGE_PRICE_COLOR.darker(110))
        painter.drawText(
            QRectF(max(plot.left(), plot.right() - width), y - 18, width, 17),
            Qt.AlignRight | Qt.AlignVCenter,
            label,
        )
        painter.setFont(self.font())

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().base())
        plot = self._plot_rect()
        text_color = self.palette().text().color()
        grid_color = self.palette().midlight().color()
        axis_color = self.palette().mid().color()

        self._draw_plot_axes(painter, plot, axis_color)
        self._draw_x_axis_labels(painter, plot, text_color)
        painter.setFont(self.font())
        scales = self._scale_values()
        if scales is None:
            painter.setPen(text_color)
            painter.drawText(plot, Qt.AlignCenter, self.empty_message)
            return

        minimum_time, maximum_time, low, high = scales
        span = high - low
        axis_font = QFont(painter.font())
        axis_font.setPointSize(max(7, axis_font.pointSize() - 1))
        painter.setFont(axis_font)
        painter.setPen(QPen(grid_color, 1, Qt.DotLine))
        for index in range(5):
            ratio = index / 4
            y = plot.top() + ratio * plot.height()
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text_color)
            painter.drawText(
                QRectF(0, y - 9, plot.left() - 8, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                self._price_text(high - ratio * span),
            )
            painter.setPen(QPen(grid_color, 1, Qt.DotLine))

        for segment in self._line_segments():
            plotted = [
                self.position_for(bar_time, value, plot)
                for bar_time, value in segment
            ]
            points = [point for point in plotted if point is not None]
            if not points:
                continue
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            painter.setPen(QPen(LINE_COLOR, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            if len(points) == 1:
                painter.setBrush(LINE_COLOR)
                painter.drawEllipse(points[0], 2.5, 2.5)

        self._draw_average_price_projection(painter, plot)

        for bar_time, value in self.buy_series:
            point = self.position_for(bar_time, value, plot)
            if point is not None:
                self._draw_marker(painter, point, BUY_COLOR)
                self._draw_signal_label(
                    painter,
                    plot,
                    point,
                    value,
                    BUY_COLOR,
                    above=True,
                )
        for bar_time, value in self.sell_series:
            point = self.position_for(bar_time, value, plot)
            if point is not None:
                self._draw_marker(painter, point, SELL_COLOR)
                self._draw_signal_label(
                    painter,
                    plot,
                    point,
                    value,
                    SELL_COLOR,
                    above=False,
                )
        for marker in self.actual_fill_marker_records:
            point = self.position_for(
                marker.get("_occurred_at"),
                marker.get("_filled_price"),
                plot,
            )
            if point is None:
                continue
            process_id = str(marker.get("execution_process_id") or "").strip()
            marker_id = str(marker.get("marker_id") or "").strip()
            selected = bool(
                marker_id == self.selected_actual_fill_marker_id
                or (
                    self.selected_execution_process_id
                    and process_id == self.selected_execution_process_id
                )
            )
            self._draw_actual_fill_marker(
                painter,
                point,
                ACTUAL_BUY_FILL_COLOR if marker.get("side") == "BUY" else ACTUAL_SELL_FILL_COLOR,
                selected=selected,
            )
        self._draw_live_price_projection(painter, plot)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        plot = self._plot_rect()
        nearest: tuple[float, dict[str, Any]] | None = None
        for marker in self.actual_fill_marker_records:
            point = self.position_for(marker.get("_occurred_at"), marker.get("_filled_price"), plot)
            if point is None:
                continue
            distance = ((point.x() - event.pos().x()) ** 2 + (point.y() - event.pos().y()) ** 2) ** 0.5
            if distance <= 9.0 and (nearest is None or distance < nearest[0]):
                nearest = (distance, marker)
        if nearest is not None:
            marker = nearest[1]
            self.selected_actual_fill_marker_id = str(marker.get("marker_id") or "").strip()
            self.selected_execution_process_id = str(marker.get("execution_process_id") or "").strip()
            self.update()
            self.actualFillMarkerSelected.emit(dict(marker))
            event.accept()
            return
        super().mousePressEvent(event)

class StockInstanceChartWindow(QDialog):
    """Read-only common window backed only by project_stock_instance_day()."""

    def __init__(
        self,
        stock_code: str,
        trade_date: str | None = None,
        parent: QWidget | None = None,
        *,
        projection_provider: ProjectionProvider | None = None,
        chart_factory: ChartFactory | None = None,
    ) -> None:
        super().__init__(None)
        monitoring_owner = _main_monitoring_owner(parent)
        configure_persistent_feature_window(
            self,
            monitoring_owner if monitoring_owner is not None else parent,
        )
        self.stock_code = str(stock_code or "").strip()
        self.trade_date = str(trade_date or _today_trade_date()).strip()
        self._projection_provider = projection_provider or project_stock_instance_day
        self.last_projection: dict[str, Any] = {}
        self.last_refresh_status = ""
        self._last_valid_projection_identity: tuple[str, str, int | None] | None = None
        self._operation_cycle_signal = None
        self._operation_cycle_signal_owner = None
        self._operation_cycle_refresh_connected = False
        self._bar_committed_signal = None
        self._bar_committed_signal_owner = None
        self._bar_committed_refresh_connected = False
        self._bar_committed_refresh_pending = False
        self._live_price_operation_host = None
        self._live_price_refresh_timer: QTimer | None = None
        self._operation_command_in_progress = False
        self._stock_operation_adapter = None
        self._stock_operation_executor = None
        self._title_stock_name = _stock_name_from_repository(self.stock_code)
        self._title_instance_name = ""
        self._title_operation = ""
        self._title_bar = ""
        self.setObjectName("stockInstanceChartWindow")
        self.setWindowTitle(
            _build_window_title(
                stock_code=self.stock_code,
                stock_name=self._title_stock_name,
            )
        )
        self.setMinimumSize(820, 428)
        self.resize(self.minimumSize())

        self.info_labels: dict[str, QLabel] = {}
        self.operation_info_labels: dict[str, QLabel] = {}
        self.notice_label = QLabel()
        self.notice_label.setObjectName("stockInstanceChartNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #6B7280;")
        self.chart = (chart_factory or StockInstanceCloseChart)(self)
        self.process_rail = ExecutionProcessRail(self)
        self.fill_detail_label = QLabel()
        self.fill_detail_label.setObjectName("stockInstanceActualFillDetail")
        self.fill_detail_label.setWordWrap(True)
        self.fill_detail_label.setStyleSheet("color: #374151; padding: 3px 8px;")
        self.fill_detail_label.hide()
        fill_selected = getattr(self.chart, "actualFillMarkerSelected", None)
        if fill_selected is not None and callable(getattr(fill_selected, "connect", None)):
            fill_selected.connect(self._on_actual_fill_marker_selected)
        self.process_rail.processSelected.connect(self._on_execution_process_selected)
        self._setup_ui()
        self.refresh_projection()
        self._connect_operation_cycle_refresh()
        self._connect_bar_committed_refresh()
        self._start_live_price_refresh()

    LIVE_PRICE_REFRESH_INTERVAL_MS = 333

    def _find_live_price_operation_host(self):
        current = persistent_feature_owner(self)
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            host = getattr(
                current,
                "_main_monitoring_auto_trade_operation_host",
                None,
            )
            if host is None:
                host_getter = getattr(
                    current,
                    "main_monitoring_auto_trade_operation_host",
                    None,
                )
                if callable(host_getter):
                    try:
                        host = host_getter()
                    except Exception:
                        host = None
            if all(
                callable(getattr(host, method_name, None))
                for method_name in (
                    "high_resolution_market_state",
                    "high_resolution_market_data_snapshot",
                )
            ):
                return host
            parent_getter = getattr(current, "parent", None)
            current = parent_getter() if callable(parent_getter) else None
        return None

    def _start_live_price_refresh(self) -> None:
        if self.trade_date != _today_trade_date():
            return
        host = self._find_live_price_operation_host()
        if host is None:
            return
        self._live_price_operation_host = host
        timer = QTimer(self)
        timer.setObjectName("stockInstanceChartLivePriceRefreshTimer")
        timer.setInterval(self.LIVE_PRICE_REFRESH_INTERVAL_MS)
        timer.timeout.connect(self.refresh_live_price_projection)
        self._live_price_refresh_timer = timer
        self.refresh_live_price_projection()
        timer.start()

    def _clear_live_price_projection(self) -> bool:
        clear = getattr(self.chart, "clear_live_price_projection", None)
        return bool(clear()) if callable(clear) else False

    def refresh_live_price_projection(self) -> bool:
        """Refresh only the UI live marker from process-local market state."""

        if self.trade_date != _today_trade_date():
            return self._clear_live_price_projection()
        host = self._live_price_operation_host
        if host is None:
            return self._clear_live_price_projection()
        try:
            snapshot = host.high_resolution_market_data_snapshot()
            state = host.high_resolution_market_state(self.stock_code)
        except Exception:
            return self._clear_live_price_projection()
        if state is None or snapshot is None:
            return self._clear_live_price_projection()
        if str(getattr(state, "stock_code", "") or "").strip() != self.stock_code:
            return self._clear_live_price_projection()
        if not bool(getattr(snapshot, "broker_connected", False)):
            return self._clear_live_price_projection()
        state_identity = (
            int(getattr(state, "connection_epoch", 0) or 0),
            str(getattr(state, "login_session_id", "") or "").strip(),
        )
        snapshot_identity = (
            int(getattr(snapshot, "connection_epoch", 0) or 0),
            str(getattr(snapshot, "login_session_id", "") or "").strip(),
        )
        if not state_identity[1] or state_identity != snapshot_identity:
            return self._clear_live_price_projection()
        market_datetime = parse_market_datetime(
            getattr(state, "last_market_datetime", None)
        )
        if (
            market_datetime is None
            or market_datetime.date().isoformat() != self.trade_date
        ):
            return self._clear_live_price_projection()
        price = _finite_number(getattr(state, "last_price", None))
        if price is None or price <= 0:
            return self._clear_live_price_projection()
        apply_live = getattr(self.chart, "set_live_price_projection", None)
        if not callable(apply_live):
            return False
        return bool(
            apply_live(
                market_datetime,
                price,
                data_quality=getattr(state, "data_quality", "NORMAL"),
            )
        )

    def _find_operation_cycle_signal(self):
        current = persistent_feature_owner(self)
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            host = getattr(
                current,
                "_main_monitoring_auto_trade_operation_host",
                None,
            )
            if host is None:
                host_getter = getattr(
                    current,
                    "main_monitoring_auto_trade_operation_host",
                    None,
                )
                if callable(host_getter):
                    try:
                        host = host_getter()
                    except Exception:
                        host = None
            signal = getattr(host, "operation_cycle_completed", None)
            if signal is not None and callable(getattr(signal, "connect", None)):
                self._operation_cycle_signal_owner = host
                return signal
            parent_getter = getattr(current, "parent", None)
            current = parent_getter() if callable(parent_getter) else None
        return None

    def _connect_operation_cycle_refresh(self) -> None:
        if self.trade_date != _today_trade_date():
            return
        signal = self._find_operation_cycle_signal()
        if signal is None:
            return
        try:
            signal.connect(self._on_operation_cycle_completed)
        except (RuntimeError, TypeError):
            return
        self._operation_cycle_signal = signal
        self._operation_cycle_refresh_connected = True

    def _disconnect_operation_cycle_refresh(self) -> None:
        signal = self._operation_cycle_signal
        if signal is not None and self._operation_cycle_refresh_connected:
            try:
                signal.disconnect(self._on_operation_cycle_completed)
            except (RuntimeError, TypeError):
                pass
        self._operation_cycle_signal = None
        self._operation_cycle_signal_owner = None
        self._operation_cycle_refresh_connected = False

    def _find_bar_committed_signal(self):
        current = persistent_feature_owner(self)
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            api = getattr(current, "kiwoom_api", None)
            signal = getattr(api, "bar_committed", None)
            if signal is not None and callable(getattr(signal, "connect", None)):
                self._bar_committed_signal_owner = api
                return signal
            parent_getter = getattr(current, "parent", None)
            current = parent_getter() if callable(parent_getter) else None
        return None

    def _connect_bar_committed_refresh(self) -> None:
        if self.trade_date != _today_trade_date():
            return
        signal = self._find_bar_committed_signal()
        if signal is None:
            return
        try:
            signal.connect(self._on_bar_committed)
        except (RuntimeError, TypeError):
            return
        self._bar_committed_signal = signal
        self._bar_committed_refresh_connected = True

    def _disconnect_bar_committed_refresh(self) -> None:
        signal = self._bar_committed_signal
        if signal is not None and self._bar_committed_refresh_connected:
            try:
                signal.disconnect(self._on_bar_committed)
            except (RuntimeError, TypeError):
                pass
        self._bar_committed_signal = None
        self._bar_committed_signal_owner = None
        self._bar_committed_refresh_connected = False
        self._bar_committed_refresh_pending = False

    def _on_bar_committed(self, payload: object) -> None:
        if self.trade_date != _today_trade_date():
            self._disconnect_bar_committed_refresh()
            return
        if not isinstance(payload, dict) or payload.get("event_type") != "BAR_COMMITTED":
            return
        if normalize_stock_code(payload.get("stock_code")) != normalize_stock_code(
            self.stock_code
        ):
            return
        if str(payload.get("trade_date") or "").strip() != self.trade_date:
            return
        if self._bar_committed_refresh_pending:
            return
        self._bar_committed_refresh_pending = True
        QTimer.singleShot(0, self._refresh_after_bar_committed)

    def _refresh_after_bar_committed(self) -> None:
        self._bar_committed_refresh_pending = False
        if not self._bar_committed_refresh_connected:
            return
        try:
            self.refresh_projection(preserve_pnl_if_same_bar=True)
        except RuntimeError:
            self._disconnect_bar_committed_refresh()

    def _on_operation_cycle_completed(self, result: dict[str, Any]) -> None:
        if self.trade_date != _today_trade_date():
            self._disconnect_operation_cycle_refresh()
            return
        if not isinstance(result, dict) or result.get("processed") is not True:
            return
        try:
            self.refresh_projection(preserve_pnl_if_same_bar=True)
        except RuntimeError:
            self._disconnect_operation_cycle_refresh()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        live_timer = self._live_price_refresh_timer
        if live_timer is not None:
            live_timer.stop()
        self._live_price_refresh_timer = None
        self._live_price_operation_host = None
        self._disconnect_operation_cycle_refresh()
        self._disconnect_bar_committed_refresh()
        owner = persistent_feature_owner(self)
        if _OPEN_STOCK_INSTANCE_CHARTS.get(self.stock_code) is self:
            _OPEN_STOCK_INSTANCE_CHARTS.pop(self.stock_code, None)
        _refresh_chart_open_code_views(owner)
        _update_common_pnl_refresh_timer()
        super().closeEvent(event)

    def refresh_pnl_only(self) -> None:
        if self.trade_date != _today_trade_date():
            return
        result = project_current_stock_pnl(self.stock_code, project_root=PROJECT_ROOT)
        self.apply_pnl_result(result)

    def apply_pnl_result(self, result: dict[str, Any]) -> None:
        pnl_available = result.get("available") is True
        amount = _finite_number(result.get("cumulative_profit"))
        rate = _finite_number(result.get("cumulative_rate"))
        self._set_pnl_display(
            available=pnl_available,
            amount=amount,
            rate=rate,
        )
        if not pnl_available or amount is None:
            return
        self.last_projection.update({"cumulative_pnl": result.get("cumulative_profit"), "cumulative_return_rate": result.get("cumulative_rate"), "cumulative_return_available": result.get("cumulative_rate") is not None, "pnl_available": True, "pnl_cycle_boundary_id": result.get("boundary_id"), "pnl_evaluation_price": result.get("evaluation_price"), "pnl_evaluation_price_at": result.get("evaluation_price_at")})

    def _set_pnl_display(
        self,
        *,
        available: bool,
        amount: Any = None,
        rate: Any = None,
    ) -> None:
        amount_value = _finite_number(amount) if available else None
        display_amount = amount_value if amount_value is not None else 0.0
        label = self.info_labels["cumulative_pnl"]
        text = format_chart_pnl_display(amount, rate, available=available)
        color = profit_loss_value_color(display_amount)
        if label.text() != text:
            label.setText(text)
        label.setStyleSheet(f"color: {color};")

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog#stockInstanceChartWindow {
                background: #FFFFFF;
                color: #111827;
            }
            QFrame#stockInstanceChartInfoPanel,
            QFrame#stockInstanceChartPanel {
                background: #FFFFFF;
                border: none;
            }
            QWidget#stockInstanceCloseChart {
                background: #FFFFFF;
            }
            QLabel#stockInstanceChartInfoValue {
                color: #111827;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#stockInstanceChartStockValue,
            QLabel#stockInstanceChartPnlValue {
                font-size: 21px;
                font-weight: 700;
            }
            QLabel#stockInstanceChartStockValue {
                color: #1D4ED8;
            }
            QLabel#stockInstanceChartSummaryValue {
                font-size: 20px;
                font-weight: 700;
            }
            QFrame#stockInstanceChartOperationInfo {
                background: transparent;
                border: 1px solid __AUTO_TRADE_SETTING_PERFORMANCE_BORDER_COLOR__;
                border-radius: 3px;
            }
            """.replace(
                "__AUTO_TRADE_SETTING_PERFORMANCE_BORDER_COLOR__",
                AUTO_TRADE_SETTING_BADGE_BORDER_COLOR,
            )
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(0)

        info_panel = QFrame()
        info_panel.setObjectName("stockInstanceChartInfoPanel")
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(18, 8, 14, 8)
        info_layout.setSpacing(8)

        left_block = QWidget()
        left_block.setObjectName("stockInstanceChartHeaderLeftBlock")
        left_layout = QVBoxLayout(left_block)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        stock_value = QLabel("-")
        stock_value.setObjectName("stockInstanceChartStockValue")
        stock_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stock_value.setMinimumWidth(0)
        stock_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        stock_value.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(stock_value)
        self.info_labels["stock"] = stock_value

        badge_font = _auto_trade_setting_badge_font(persistent_feature_owner(self))
        segment_widths = _stock_operation_header_segment_widths(badge_font)
        operation_info = QFrame()
        operation_info.setObjectName("stockInstanceChartOperationInfo")
        operation_info_layout = QHBoxLayout(operation_info)
        operation_info_layout.setContentsMargins(8, 4, 8, 4)
        operation_info_layout.setSpacing(16)
        for key in ("status", "method", "liquidation"):
            label = QLabel("-")
            label.setObjectName("stockInstanceChartOperationInfoValue")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setFont(QFont(badge_font))
            label.setFixedWidth(segment_widths[key])
            operation_info_layout.addWidget(label)
            self.operation_info_labels[key] = label
        operation_info.setFixedWidth(
            sum(segment_widths.values())
            + (operation_info_layout.spacing() * 2)
            + operation_info_layout.contentsMargins().left()
            + operation_info_layout.contentsMargins().right()
            + 2  # 1px frame border on both sides
        )
        self.operation_info_panel = operation_info
        left_layout.addWidget(operation_info, 0, Qt.AlignHCenter)
        left_block.setMinimumWidth(operation_info.width())
        left_block.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.header_left_block = left_block
        info_layout.addWidget(left_block, 0, Qt.AlignVCenter)

        right_block = QWidget()
        right_block.setObjectName("stockInstanceChartHeaderRightBlock")
        right_layout = QHBoxLayout(right_block)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        pnl_value = QLabel("-")
        pnl_value.setObjectName("stockInstanceChartPnlValue")
        pnl_font = QFont(pnl_value.font())
        pnl_font.setPixelSize(21)
        pnl_font.setBold(True)
        pnl_value.setFont(pnl_font)
        pnl_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pnl_value.setMinimumWidth(0)
        pnl_value.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        pnl_value.setAlignment(Qt.AlignCenter)
        pnl_value.setFixedWidth(
            _stock_instance_chart_pnl_display_width(pnl_font)
        )
        self.info_labels["cumulative_pnl"] = pnl_value
        right_layout.addStretch(1)
        right_layout.addWidget(pnl_value, 0, Qt.AlignVCenter)

        action_block = QWidget()
        action_block.setObjectName("stockInstanceChartHeaderActionBlock")
        action_layout = QVBoxLayout(action_block)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)

        self.early_close_button = QPushButton("조기마감")
        self.early_close_button.setObjectName("stockInstanceChartEarlyCloseButton")
        self.early_close_button.setFont(QFont(badge_font))
        self.early_close_button.clicked.connect(self._on_early_close_clicked)

        self.immediate_liquidation_button = QPushButton("즉시청산")
        self.immediate_liquidation_button.setObjectName(
            "stockInstanceChartImmediateLiquidationButton"
        )
        self.immediate_liquidation_button.setFont(QFont(badge_font))
        self.immediate_liquidation_button.clicked.connect(
            self._on_immediate_liquidation_clicked
        )
        button_metrics = QFontMetrics(badge_font)
        button_width = max(
            button_metrics.horizontalAdvance(self.early_close_button.text()),
            button_metrics.horizontalAdvance(self.immediate_liquidation_button.text()),
        ) + 24
        for button in (
            self.early_close_button,
            self.immediate_liquidation_button,
        ):
            button.setFixedSize(button_width, 30)
            action_layout.addWidget(button)
        operation_top_margin, operation_bottom_margin = (
            _operation_info_vertical_margins(self.early_close_button)
        )
        operation_info_layout.setContentsMargins(
            operation_info_layout.contentsMargins().left(),
            operation_top_margin,
            operation_info_layout.contentsMargins().right(),
            operation_bottom_margin,
        )
        operation_info.setFixedHeight(
            QFontMetrics(badge_font).height()
            + operation_top_margin
            + operation_bottom_margin
            + 2  # 1px frame border on both sides
        )
        self.header_action_block = action_block
        right_layout.addWidget(
            action_block,
            0,
            Qt.AlignRight | Qt.AlignVCenter,
        )
        right_block.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.header_right_block = right_block
        info_layout.addWidget(right_block, 1, Qt.AlignVCenter)
        root.addWidget(info_panel)

        chart_panel = QFrame()
        chart_panel.setObjectName("stockInstanceChartPanel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(14, 0, 12, 0)
        chart_layout.setSpacing(0)
        chart_layout.addWidget(self.chart, 1)
        chart_layout.addWidget(self.process_rail, 0)
        chart_layout.addWidget(self.fill_detail_label, 0)
        root.addWidget(chart_panel, 1)
        self._update_operation_button_state()

    @staticmethod
    def _actual_fill_detail_text(marker: dict[str, Any]) -> str:
        side = "매수" if str(marker.get("side") or "").upper() == "BUY" else "매도"
        price = _finite_number(marker.get("filled_price"))
        quantity = _nonnegative_count(marker.get("filled_quantity_delta"), 0)
        occurred = parse_market_datetime(marker.get("occurred_at"))
        occurred_text = occurred.strftime("%H:%M:%S") if occurred is not None else "-"
        source = str(marker.get("execution_time_source") or "").strip().upper()
        quality = str(marker.get("execution_time_quality") or "").strip().upper()
        if source == "BROKER_FID_908":
            time_basis = "Broker 체결시각"
        elif source == "LOCAL_RECEIVED_AT":
            time_basis = "Chejan 수신시각(근사)"
        else:
            time_basis = quality or "시간근거 없음"
        option = str(marker.get("option_summary") or "").strip()
        index = marker.get("child_sequence_index")
        total = marker.get("child_sequence_total")
        child = f"{index}/{total}" if index not in (None, "") and total not in (None, "") else "-"
        order_no = str(marker.get("broker_order_no") or marker.get("order_id") or "-").strip()
        identity = str(marker.get("execution_identity") or marker.get("execution_id") or "-").strip()
        price_text = StockInstanceCloseChart._price_text(price) if price is not None else "-"
        execution_text = f" · 실행 {option} {child}" if option else f" · 실행 {child}"
        return (
            f"{side} {price_text}원 · {occurred_text} · 수량 {quantity}"
            f"{execution_text} · 시간근거 {time_basis} · 주문 {order_no} · 체결 {identity}"
        )

    def _on_actual_fill_marker_selected(self, marker: object) -> None:
        if not isinstance(marker, dict):
            return
        process_id = str(marker.get("execution_process_id") or "").strip()
        self.process_rail.select_process(process_id)
        self.fill_detail_label.setText(self._actual_fill_detail_text(marker))
        self.fill_detail_label.setToolTip(self.fill_detail_label.text())
        self.fill_detail_label.show()

    def _on_execution_process_selected(self, execution_process_id: str) -> None:
        select = getattr(self.chart, "select_execution_process", None)
        if callable(select):
            select(execution_process_id)
        for process in self.process_rail.processes:
            if str(process.get("execution_process_id") or "").strip() != execution_process_id:
                continue
            self.fill_detail_label.setText(ExecutionProcessRail._tooltip_text(process).replace("\n", " · "))
            self.fill_detail_label.setToolTip(ExecutionProcessRail._tooltip_text(process))
            self.fill_detail_label.show()
            break

    def _main_monitoring_window(self):
        return _main_monitoring_owner(persistent_feature_owner(self))

    def _operation_stock_context(self) -> tuple[Path, str, str, str] | None:
        from runtime_io import read_json_dict
        from stock_repository import StockRepository

        stock_dir = StockRepository(project_root=PROJECT_ROOT).resolve_stock_dir(
            self.stock_code
        )
        if not stock_dir.exists():
            return None
        config = read_json_dict(stock_dir / "config.json")
        instance_id = str(
            config.get("assigned_routine_instance_id")
            or self.last_projection.get("instance_id")
            or ""
        ).strip()
        stock_name = str(
            self.last_projection.get("stock_name")
            or config.get("name")
            or stock_dir.name.split("_", 1)[-1]
            or ""
        ).strip()
        if not instance_id:
            return None
        return stock_dir, self.stock_code, stock_name, instance_id

    def _build_stock_operation_adapter(self):
        main_window = self._main_monitoring_window()
        context = self._operation_stock_context()
        if main_window is None or context is None:
            return None
        build_adapter = getattr(
            main_window,
            "_build_stock_instance_chart_operation_adapter",
            None,
        )
        if not callable(build_adapter):
            return None
        stock_dir, code, name, instance_id = context
        adapter = build_adapter(
            stock_dir,
            code,
            name,
            instance_id,
        )
        self._stock_operation_adapter = adapter
        return adapter

    def _early_close_is_excluded(self) -> bool:
        from gui_auto_trade_integrity import is_operation_excluded
        from runtime_io import read_json_dict

        context = self._operation_stock_context()
        if context is None:
            return True
        return is_operation_excluded(read_json_dict(context[0] / "config.json"))

    def _update_operation_header_info(self) -> None:
        display = project_stock_operation_header_display(
            self.stock_code,
            persistent_feature_owner(self),
        )
        stock_label = self.info_labels.get("stock")
        if stock_label is not None:
            stock_label.setStyleSheet(f"color: {display.identity_color};")
        values = {
            "status": (display.status, display.status_color),
            "method": (display.method, display.method_color),
            "liquidation": (display.liquidation, display.liquidation_color),
        }
        for key, (value, color) in values.items():
            label = self.operation_info_labels.get(key)
            if label is None:
                continue
            label.setText(str(value or "-").strip() or "-")
            label.setStyleSheet(f"color: {color};")

    def _update_operation_button_state(self) -> None:
        self._update_operation_header_info()
        available = self._build_stock_operation_adapter() is not None
        self.early_close_button.setEnabled(
            available
            and not self._operation_command_in_progress
            and not self._early_close_is_excluded()
        )
        self.immediate_liquidation_button.setEnabled(
            available and not self._operation_command_in_progress
        )

    def _run_stock_operation(self, operation: str) -> None:
        if self._operation_command_in_progress:
            return
        adapter = self._build_stock_operation_adapter()
        if adapter is None:
            self._update_operation_button_state()
            return
        self._operation_command_in_progress = True
        self._update_operation_button_state()
        try:
            main_window = self._main_monitoring_window()
            execute_operation = getattr(
                main_window,
                "_execute_stock_instance_chart_operation",
                None,
            )
            if not callable(execute_operation):
                execute_operation = self._stock_operation_executor
            if not callable(execute_operation):
                return
            result: dict[str, object] | None = None
            if operation == "early_close":
                result = execute_operation(
                    adapter,
                    "루틴",
                    source="간이차트",
                    selected=adapter.selected_stock_infos(),
                    show_error_dialog=False,
                    show_result_toast=False,
                )
            elif operation == "immediate_liquidation":
                result = execute_operation(
                    adapter,
                    "시장가",
                    source="간이차트",
                    selected=adapter.selected_stock_infos(),
                    show_error_dialog=False,
                    show_result_toast=False,
                )
            if (
                isinstance(result, dict)
                and result.get("ok") is not True
                and result.get("cancelled") is not True
            ):
                message = str(result.get("message") or "").strip()
                if message:
                    from gui_toast import show_toast

                    show_toast(self, message, duration_ms=2500)
        finally:
            self._operation_command_in_progress = False
            self._update_operation_button_state()
            self.refresh_projection()

    def _on_early_close_clicked(self) -> None:
        self._run_stock_operation("early_close")

    def _on_immediate_liquidation_clicked(self) -> None:
        self._run_stock_operation("immediate_liquidation")

    @staticmethod
    def _malformed_projection(data: dict[str, Any]) -> bool:
        if not isinstance(data.get("candles", []), list):
            return True
        if not isinstance(data.get("buy_signal_markers", []), list):
            return True
        if not isinstance(data.get("sell_signal_markers", []), list):
            return True
        if not isinstance(data.get("actual_fill_markers", []), list):
            return True
        if not isinstance(data.get("execution_process_rails", []), list):
            return True
        diagnostics = data.get("diagnostics", {})
        if diagnostics not in ({}, None) and not isinstance(diagnostics, dict):
            return True
        issues = diagnostics.get("issues", []) if isinstance(diagnostics, dict) else []
        if not isinstance(issues, list):
            return True
        return any(
            "MALFORMED" in str(issue).upper()
            or "CORRUPT" in str(issue).upper()
            or "PROJECTION_ERROR" in str(issue).upper()
            for issue in issues
        )

    @staticmethod
    def _projection_has_valid_candle(data: dict[str, Any]) -> bool:
        candles = data.get("candles", [])
        if not isinstance(candles, list):
            return False
        return any(
            isinstance(candle, dict)
            and parse_market_datetime(candle.get("bar_time")) is not None
            and (close := _finite_number(candle.get("close"))) is not None
            and close > 0
            for candle in candles
        )

    @classmethod
    def _projection_has_renderable_candle(cls, data: dict[str, Any]) -> bool:
        trade_date = str(data.get("trade_date") or "").strip()
        _start, _end, visible_ranges = cls._chart_display_time_range(
            trade_date,
            data.get("ats_session_ranges", []),
        )
        if not visible_ranges:
            return False
        candles = data.get("candles", [])
        if not isinstance(candles, list):
            return False
        for candle in candles:
            if not isinstance(candle, dict):
                continue
            bar_time = parse_market_datetime(candle.get("bar_time"))
            close = _finite_number(candle.get("close"))
            if bar_time is None or close is None or close <= 0:
                continue
            if any(start <= bar_time <= end for start, end in visible_ranges):
                return True
        return False

    @classmethod
    def _projection_refresh_status(cls, data: dict[str, Any]) -> str:
        known = {
            CHART_PROJECTION_VALID,
            CHART_PROJECTION_NO_DAY_DATA,
            CHART_PROJECTION_NOT_READY,
            CHART_PROJECTION_RULES_UNAVAILABLE,
            CHART_PROJECTION_REFRESH_FAILED,
            CHART_PROJECTION_STALE_REJECTED,
        }
        requested = str(data.get("projection_status") or "").strip().upper()
        if cls._malformed_projection(data) or not isinstance(data.get("candles", []), list):
            return CHART_PROJECTION_REFRESH_FAILED
        if requested == CHART_PROJECTION_VALID:
            if cls._projection_has_renderable_candle(data):
                return CHART_PROJECTION_VALID
            return (
                CHART_PROJECTION_STALE_REJECTED
                if cls._projection_has_valid_candle(data)
                else CHART_PROJECTION_NOT_READY
            )
        if requested in known:
            return requested
        if cls._projection_has_renderable_candle(data):
            return CHART_PROJECTION_VALID
        if cls._projection_has_valid_candle(data):
            return CHART_PROJECTION_STALE_REJECTED
        if not str(data.get("instance_id") or "").strip():
            return CHART_PROJECTION_RULES_UNAVAILABLE
        diagnostics = data.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        return (
            CHART_PROJECTION_NOT_READY
            if _nonnegative_count(diagnostics.get("raw_candle_count"), 0) > 0
            else CHART_PROJECTION_NO_DAY_DATA
        )

    @staticmethod
    def _projection_identity(
        data: dict[str, Any],
    ) -> tuple[str, str, int | None]:
        raw_bar_minutes = data.get("bar_minutes")
        bar_minutes = (
            int(raw_bar_minutes)
            if isinstance(raw_bar_minutes, int)
            and not isinstance(raw_bar_minutes, bool)
            and raw_bar_minutes > 0
            else None
        )
        return (
            str(data.get("trade_date") or "").strip(),
            str(data.get("instance_id") or "").strip(),
            bar_minutes,
        )

    @staticmethod
    def _preserved_series_notice(status: str) -> str:
        return {
            CHART_PROJECTION_NO_DAY_DATA: "당일 기준봉 데이터가 없어 이전 그래프를 유지합니다.",
            CHART_PROJECTION_NOT_READY: "새 기준봉 데이터가 준비되지 않아 이전 그래프를 유지합니다.",
            CHART_PROJECTION_RULES_UNAVAILABLE: "루틴 기준봉을 확인할 수 없어 이전 그래프를 유지합니다.",
            CHART_PROJECTION_STALE_REJECTED: "이전 세션 데이터가 거부되어 기존 그래프를 유지합니다.",
        }.get(
            status,
            "차트 데이터를 갱신하지 못해 이전 그래프를 유지합니다.",
        )

    def _can_preserve_last_valid_projection(
        self,
        requested_identity: tuple[str, str, int | None] | None,
    ) -> bool:
        return bool(
            self.chart.close_series
            and self._last_valid_projection_identity is not None
            and requested_identity == self._last_valid_projection_identity
        )

    def _apply_unavailable_projection(
        self,
        data: dict[str, Any],
        status: str,
    ) -> None:
        self.last_refresh_status = status
        if self._can_preserve_last_valid_projection(
            self._projection_identity(data)
        ):
            self.notice_label.setText(self._preserved_series_notice(status))
            self._update_operation_button_state()
            return
        self._apply_projection(data)
        self.last_refresh_status = status

    def _empty_chart_message(self, data: dict[str, Any]) -> str:
        diagnostics = data.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        raw_count = _nonnegative_count(diagnostics.get("raw_candle_count"), 0)
        if raw_count > 0:
            return "아직 오늘 기준봉이 없습니다."
        return "표시할 기준봉 데이터가 없습니다."

    @staticmethod
    def _chart_display_time_range(
        trade_date: str,
        ats_session_ranges: Any,
    ) -> tuple[
        datetime | None,
        datetime | None,
        list[tuple[datetime, datetime]],
    ]:
        start = parse_market_datetime(f"{trade_date}T{BASE_CHART_START_TIME}")
        end = parse_market_datetime(f"{trade_date}T{BASE_CHART_END_TIME}")
        if start is None or end is None or start >= end:
            return None, None, []
        visible_ranges = [(start, end)]
        if not isinstance(ats_session_ranges, list):
            return start, end, visible_ranges
        for session in ats_session_ranges:
            if not isinstance(session, dict):
                continue
            session_start = parse_market_datetime(
                f"{trade_date}T{str(session.get('start_time') or '').strip()}"
            )
            session_end = parse_market_datetime(
                f"{trade_date}T{str(session.get('end_time') or '').strip()}"
            )
            if session_start is None or session_end is None:
                continue
            if session_end <= session_start:
                session_end += timedelta(days=1)
            visible_ranges.append((session_start, session_end))
            start = min(start, session_start)
            end = max(end, session_end)
        return start, end, visible_ranges

    def _apply_projection(self, data: dict[str, Any]) -> None:
        selected_marker_id = str(
            getattr(self.chart, "selected_actual_fill_marker_id", "") or ""
        ).strip()
        selected_process_id = str(
            getattr(self.chart, "selected_execution_process_id", "")
            or self.process_rail.selected_execution_process_id
            or ""
        ).strip()
        self.last_projection = data
        self.last_refresh_status = self._projection_refresh_status(data)
        stock_name = str(data.get("stock_name") or "").strip()
        stock_code = str(data.get("stock_code") or self.stock_code).strip()
        if stock_name:
            self._title_stock_name = stock_name
        instance_id = str(data.get("instance_id") or "").strip()
        instance_name = str(data.get("instance_name") or instance_id or "").strip()
        bar_minutes = data.get("bar_minutes")
        bar_text = (
            f"{bar_minutes}분"
            if isinstance(bar_minutes, int)
            and not isinstance(bar_minutes, bool)
            and bar_minutes > 0
            else "-"
        )

        self.info_labels["stock"].setText(
            " ".join(part for part in (stock_code, stock_name) if part) or "-"
        )
        pnl_available = data.get("pnl_available") is True
        cumulative_pnl = _finite_number(data.get("cumulative_pnl"))
        cumulative_rate = _finite_number(data.get("cumulative_return_rate"))
        self._set_pnl_display(
            available=pnl_available,
            amount=cumulative_pnl,
            rate=(
                cumulative_rate
                if data.get("cumulative_return_available") is True
                else None
            ),
        )
        projected_trade_date = str(data.get("trade_date") or self.trade_date)
        operation_title = str(data.get("operation_title_display") or "-").strip() or "-"
        bar_title = f"{bar_text}봉" if bar_text != "-" else "-"

        candles = data.get("candles", [])
        buy_markers = data.get("buy_signal_markers", [])
        sell_markers = data.get("sell_signal_markers", [])
        empty_message = self._empty_chart_message(data)
        x_range_start, x_range_end, visible_time_ranges = self._chart_display_time_range(
            projected_trade_date,
            data.get("ats_session_ranges", []),
        )
        self.chart.set_projection(
            candles,
            buy_markers,
            sell_markers,
            empty_message=empty_message,
            x_range_start=x_range_start,
            x_range_end=x_range_end,
            visible_time_ranges=visible_time_ranges,
            timeframe_minutes=bar_minutes,
            actual_fill_markers=data.get("actual_fill_markers", []),
            process_rails=data.get("execution_process_rails", []),
            average_price=(
                data.get("average_price")
                if data.get("average_price_visible") is True
                else None
            ),
        )
        self.process_rail.set_processes(data.get("execution_process_rails", []))
        marker_by_id = {
            str(marker.get("marker_id") or "").strip(): marker
            for marker in getattr(self.chart, "actual_fill_marker_records", [])
            if isinstance(marker, dict) and str(marker.get("marker_id") or "").strip()
        }
        valid_process_ids = {
            str(process.get("execution_process_id") or "").strip()
            for process in self.process_rail.processes
            if str(process.get("execution_process_id") or "").strip()
        }
        selected_marker = marker_by_id.get(selected_marker_id)
        if selected_marker is not None:
            marker_process_id = str(
                selected_marker.get("execution_process_id") or ""
            ).strip()
            restored_process_id = (
                marker_process_id if marker_process_id in valid_process_ids else ""
            )
            self.chart.selected_actual_fill_marker_id = selected_marker_id
            self.chart.selected_execution_process_id = restored_process_id
            self.process_rail.select_process(restored_process_id)
            self.fill_detail_label.setText(
                self._actual_fill_detail_text(selected_marker)
            )
            self.fill_detail_label.setToolTip(self.fill_detail_label.text())
            self.fill_detail_label.show()
        elif selected_process_id in valid_process_ids:
            self.chart.selected_actual_fill_marker_id = ""
            self.chart.select_execution_process(selected_process_id)
            self.process_rail.select_process(selected_process_id)
            self._on_execution_process_selected(selected_process_id)
        else:
            self.chart.selected_actual_fill_marker_id = ""
            self.chart.selected_execution_process_id = ""
            self.process_rail.select_process("")
            self.fill_detail_label.clear()
            self.fill_detail_label.hide()
            self.chart.update()
        if self.last_refresh_status == CHART_PROJECTION_VALID and self.chart.close_series:
            self._last_valid_projection_identity = self._projection_identity(data)
        else:
            self._last_valid_projection_identity = None
        buy_fallback = len(buy_markers) if isinstance(buy_markers, list) else 0
        sell_fallback = len(sell_markers) if isinstance(sell_markers, list) else 0
        buy_count = _nonnegative_count(data.get("buy_signal_count"), buy_fallback)
        sell_count = _nonnegative_count(data.get("sell_signal_count"), sell_fallback)
        self._title_instance_name = instance_name
        self._title_operation = operation_title
        self._title_bar = bar_title
        self.setWindowTitle(
            _build_window_title(
                stock_code=stock_code or self.stock_code,
                stock_name=self._title_stock_name,
                instance_name=self._title_instance_name,
                operation_title=self._title_operation,
                bar_title=self._title_bar,
                buy_count=buy_count,
                sell_count=sell_count,
            )
        )
        self._update_operation_button_state()

        if not instance_id:
            notice = "배정된 활성 인스턴스가 없습니다."
        elif self._malformed_projection(data):
            notice = "데이터 손상이 감지되어 확인 가능한 정보만 표시합니다."
        elif not self.chart.close_series:
            notice = empty_message
        else:
            notice = ""
        self.notice_label.setText(notice)
        if notice and not self.chart.close_series:
            self.chart.empty_message = notice
            self.chart.update()

    def _apply_projection_error(self) -> None:
        self.last_refresh_status = CHART_PROJECTION_REFRESH_FAILED
        last_context_identity = self._projection_identity(self.last_projection)
        requested_identity = (
            self.trade_date,
            last_context_identity[1],
            last_context_identity[2],
        )
        if self._can_preserve_last_valid_projection(requested_identity):
            self.notice_label.setText(
                self._preserved_series_notice(CHART_PROJECTION_REFRESH_FAILED)
            )
            self._update_operation_button_state()
            return
        self.last_projection = {}
        for label in self.info_labels.values():
            label.setText("-")
        self.info_labels["stock"].setText(self.stock_code or "-")
        self._set_pnl_display(available=False)
        error_message = "데이터 손상 또는 조회 오류로 차트를 표시할 수 없습니다."
        x_range_start, x_range_end, visible_time_ranges = self._chart_display_time_range(
            self.trade_date,
            [],
        )
        self.chart.set_projection(
            [],
            [],
            [],
            empty_message=error_message,
            x_range_start=x_range_start,
            x_range_end=x_range_end,
            visible_time_ranges=visible_time_ranges,
        )
        self._last_valid_projection_identity = None
        self.setWindowTitle(
            _build_window_title(
                stock_code=self.stock_code,
                stock_name=self._title_stock_name,
                instance_name=self._title_instance_name,
                operation_title=self._title_operation,
                bar_title=self._title_bar,
            )
        )
        self._update_operation_button_state()
        self.notice_label.setText(error_message)

    @staticmethod
    def _last_completed_bar_time(data: dict[str, Any]) -> str:
        candles = data.get("candles", [])
        if not isinstance(candles, list) or not candles:
            return ""
        latest = candles[-1]
        return str(latest.get("bar_time") or "").strip() if isinstance(latest, dict) else ""

    def refresh_projection(self, *, preserve_pnl_if_same_bar: bool = False) -> None:
        """Re-read only the projection; it never requests or writes candles."""
        try:
            projected = self._projection_provider(self.stock_code, self.trade_date)
        except Exception:
            self._apply_projection_error()
            return
        if not isinstance(projected, dict):
            self._apply_projection_error()
            return
        refresh_status = self._projection_refresh_status(projected)
        if refresh_status != CHART_PROJECTION_VALID:
            self._apply_unavailable_projection(projected, refresh_status)
            return
        if (
            preserve_pnl_if_same_bar
            and self._last_completed_bar_time(projected)
            and self._last_completed_bar_time(projected)
            == self._last_completed_bar_time(self.last_projection)
        ):
            projected = dict(projected)
            for key in (
                "daily_realized_gross",
                "completed_buy_cost",
                "open_position_cost",
                "unrealized_pnl_at_bar_close",
                "cumulative_pnl",
                "cumulative_return_rate",
                "pnl_bar_time",
                "pnl_bar_close",
                "pnl_available",
                "cumulative_return_available",
                "pnl_unavailable_reason",
                "pnl_source",
                "pnl_basis",
            ):
                if key in self.last_projection:
                    projected[key] = self.last_projection[key]
        self._apply_projection(projected)


def open_stock_instance_chart(
    stock_code: str,
    trade_date: str | None = None,
    parent: QWidget | None = None,
) -> StockInstanceChartWindow:
    """Open or foreground the single live common chart for ``stock_code``."""
    registry_key = str(stock_code or "").strip()
    requested_trade_date = str(trade_date or _today_trade_date()).strip()
    existing = _OPEN_STOCK_INSTANCE_CHARTS.get(registry_key)
    if existing is not None:
        try:
            reusable = not sip.isdeleted(existing) and existing.isVisible()
        except RuntimeError:
            reusable = False
        existing_trade_date = str(getattr(existing, "trade_date", "") or "").strip()
        if reusable and existing_trade_date == requested_trade_date:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            existing.refresh_projection()
            _refresh_chart_open_code_views(parent or persistent_feature_owner(existing))
            return existing
        if reusable:
            try:
                existing.close()
            except Exception:
                pass
        _OPEN_STOCK_INSTANCE_CHARTS.pop(registry_key, None)

    dialog = StockInstanceChartWindow(
        stock_code=registry_key,
        trade_date=requested_trade_date,
        parent=parent,
    )
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    _OPEN_STOCK_INSTANCE_CHARTS[registry_key] = dialog
    dialog_reference = weakref.ref(dialog)
    view_owner = _main_monitoring_owner(parent) or parent
    view_owner_reference = (
        weakref.ref(view_owner) if view_owner is not None else (lambda: None)
    )

    def remove_destroyed_window(_destroyed: object | None = None) -> None:
        registered = _OPEN_STOCK_INSTANCE_CHARTS.get(registry_key)
        if registered is dialog_reference():
            _OPEN_STOCK_INSTANCE_CHARTS.pop(registry_key, None)
        _refresh_chart_open_code_views(view_owner_reference())
        _update_common_pnl_refresh_timer()

    dialog.destroyed.connect(remove_destroyed_window)
    dialog.show()
    _refresh_chart_open_code_views(parent)
    place_new_stock_instance_charts(parent, [dialog])
    dialog.raise_()
    dialog.activateWindow()
    _update_common_pnl_refresh_timer()
    return dialog
