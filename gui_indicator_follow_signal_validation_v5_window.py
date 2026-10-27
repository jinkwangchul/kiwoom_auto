# -*- coding: utf-8 -*-
"""Independent candle/indicator signal-validation window."""

from __future__ import annotations

from indicator_follow_signal_validation_perf_trace import (
    ENABLED as _PERF_ENABLED,
    phase as _perf_phase,
    stage as _perf_stage,
)

from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
from datetime import datetime, timedelta
import hashlib
import json
import math
from threading import Thread
import sys
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QDoubleValidator, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from engines.condition_engine import parse_condition_expression
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
    AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    auto_trade_setting_badge_stylesheet,
)
from indicator_follow_signal_validation_execution import (
    simulate_validation_execution,
    validation_virtual_fill_price,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
    build_signal_validation_snapshot,
    project_signal_validation_rules,
    project_signal_validation_ui_state,
    project_validation_execution_state,
    project_validation_market_scope,
    require_resolved_buy_bollinger_sign_selection,
    require_expression_aware_sell_price_selections,
)
from indicator_follow_signal_validation_presentation import (
    canonical_validation_condition_key,
    chart_operand_label,
    signal_evidence_tooltip,
)
from indicator_follow_validation_timeframe import (
    normalize_validation_timeframe,
    timeframe_display_label,
)
from gui_indicator_follow_timeframe_combo import IndicatorFollowTimeframeComboBox
from indicator_follow_signal_validation_visualization import (
    FAMILY_BOLLINGER,
    FAMILY_MA_ARRANGEMENT,
    FAMILY_MACD_SIGNAL,
    FAMILY_MOVING_AVERAGE,
    FAMILY_OCR_OSC,
    FAMILY_PRICE_BOX,
    FAMILY_PRICE_COMPARISON,
    FAMILY_RSI,
    LOWER_AXIS,
    PRICE_AXIS,
    ValidationFilterDescriptor,
    ValidationIndicatorSeriesCache,
    ValidationLowerFilterPresentation,
    ValidationProjectionCancelled,
    active_filter_identities_for_entry,
    build_validation_filter_universe,
    build_validation_indicator_cache,
    build_validation_lower_filter_presentations,
    indicator_transition_indexes,
)
from gui_toast import show_toast
from indicator_follow_signal_validation_runtime_trace import (
    signal_validation_trace_enabled,
    trace_candle_signature,
    trace_signal_validation_event,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)


_BACKGROUND = QColor("#111827")
_GRID = QColor("#374151")
_DAY_BOUNDARY = QColor("#4b5563")
_TEXT = QColor("#d1d5db")
_UP = QColor("#ef4444")
_DOWN = QColor("#3b82f6")
_FLAT = QColor("#9ca3af")
_BUY = QColor("#22c55e")
_SELL = QColor("#f59e0b")
_SELECTION_LINE = QColor("#fde047")
_VALIDATION_RANGE_SELECTION = QColor(96, 165, 250, 34)
_VALIDATION_RANGE_LINE = QColor("#60a5fa")
_CYCLE_AVERAGE_LINE = QColor("#d1d5db")
_VISUAL_SERIES_COLORS = (
    QColor("#f59e0b"),
    QColor("#22d3ee"),
    QColor("#a78bfa"),
    QColor("#84cc16"),
    QColor("#f472b6"),
    QColor("#38bdf8"),
    QColor("#facc15"),
    QColor("#fb7185"),
)
_LOWER_SEPARATOR = QColor("#4b5563")
_LOWER_LABEL = QColor("#9ca3af")
_NOT_FILTER_TEXT = QColor(255, 95, 95, 160)
_INNER_NOT_FILTER_TEXT = QColor(255, 215, 0, 220)
_INACTIVE_SERIES = QColor(107, 114, 128, 105)
_UNSUPPORTED_SERIES = QColor(75, 85, 99, 135)
_ERROR_SERIES = QColor("#ef4444")
_OSC_POSITIVE = QColor("#fb7185")
_OSC_NEGATIVE = QColor("#3b82f6")
_CROSSHAIR = QColor(203, 213, 225, 175)
_CROSSHAIR_TEXT = QColor("#e5e7eb")
_CROSSHAIR_BOX = QColor(17, 24, 39, 205)
_MAX_SIGNAL_VALIDATION_VISIBLE_CANDLE_SPAN = 3_500.0
_DISPLAY_BADGE_WIDTH = 68
_DISPLAY_BADGE_IDLE_TEXT_COLOR = "#404040"
_VALID_FILTER_DISPLAY_TOOLTIP = (
    "현재 UI에서 활성화된 모든 매수·매도 필터를 강조합니다. "
    "마킹·기간·신호 결과와 무관합니다."
)

_cycle_label_numpy_module = None
_cycle_label_numpy_started = False


def _prepare_cycle_label_numpy() -> None:
    """Start optional NumPy loading outside the paint path, at most once."""
    global _cycle_label_numpy_started
    if _cycle_label_numpy_started:
        return
    _cycle_label_numpy_started = True

    def load() -> None:
        global _cycle_label_numpy_module
        try:
            import numpy as ready_numpy
        except (ImportError, OSError):
            return
        _cycle_label_numpy_module = ready_numpy

    try:
        Thread(target=load, name="v5-cycle-label-numpy-prepare", daemon=True).start()
    except Exception:
        _cycle_label_numpy_started = False


def _configure_display_badge(button: QPushButton, font) -> None:
    button.setFont(font)
    button.setFixedSize(
        _DISPLAY_BADGE_WIDTH,
        AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    )
    button.setFocusPolicy(Qt.NoFocus)
    button.setCursor(Qt.PointingHandCursor)
    button.setCheckable(True)
    selector = f"QPushButton#{button.objectName()}"
    button.setStyleSheet(
        auto_trade_setting_badge_stylesheet(
            selector,
            text_color=_DISPLAY_BADGE_IDLE_TEXT_COLOR,
            border_color=AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
        )
        + f"{selector}:checked, {selector}:checked:hover {{"
        f" color: {AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR};"
        f" border-color: {AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR};"
        "}"
        + f"{selector}:focus {{ outline: none; }}"
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@lru_cache(maxsize=128)
def _histogram_condition(parameter_json: str) -> Mapping[str, Any] | None:
    """Read the immutable histogram condition without repeated descriptor decoding."""
    parameters = json.loads(parameter_json)
    condition = parameters.get("condition") if isinstance(parameters, Mapping) else None
    return MappingProxyType(dict(condition)) if isinstance(condition, Mapping) else None


class _IndicatorFollowSignalValidationChartCanvasBase(QWidget):
    """V2-owned read-only candle projection and selection boundary."""

    bar_selected = pyqtSignal(int)
    validation_range_drag_started = pyqtSignal(int)
    validation_range_drag_completed = pyqtSignal(int, int)
    validation_range_clear_requested = pyqtSignal()
    time_scale_wheel_requested = pyqtSignal(float, int)
    pan_requested = pyqtSignal(float, float)
    pointer_interaction_started = pyqtSignal()
    pointer_interaction_ended = pyqtSignal(bool)

    _LEFT = 48
    _RIGHT = 24
    _TOP = 42
    _BOTTOM = 48
    _MIN_WIDTH = 640
    _DRAG_THRESHOLD = 6
    _DRAG_AXIS_DOMINANCE_RATIO = 1.35
    _PAN_FRAME_INTERVAL_MS = 24

    def __init__(self, candles, markers, parent=None) -> None:
        super().__init__(parent)
        self._candles = _detached_validation_candles(candles)
        self._markers = deepcopy(markers)
        self._markers_by_index: dict[
            int, list[tuple[int, dict[str, Any]]]
        ] = {}
        self._rebuild_marker_index()
        self._selected_index: int | None = None
        self._validation_range_drag_start_index: int | None = None
        self._validation_range_drag_current_index: int | None = None
        self._visible_start_index = 0.0
        self._visible_candle_span = float(max(1, len(self._candles)))
        self._drag_press_x: float | None = None
        self._drag_press_y: float | None = None
        self._drag_last_x: float | None = None
        self._drag_last_y: float | None = None
        self._pan_drag_active = False
        self._pan_drag_axis: str | None = None
        self._pending_pan_delta_x = 0.0
        self._pending_pan_delta_y = 0.0
        self._time_pan_preview_raw_offset_x = 0.0
        self._time_pan_preview_offset_x = 0.0
        self._time_pan_preview_started = False
        self._time_pan_gesture_delta_x = 0.0
        self._pointer_interaction_active = False
        self._input_frame_owner = None
        self._pan_frame_timer = QTimer(self)
        self._pan_frame_timer.setSingleShot(True)
        self._pan_frame_timer.setInterval(self._PAN_FRAME_INTERVAL_MS)
        self._pan_frame_timer.timeout.connect(self._pan_frame_timeout)

    @property
    def candle_count(self) -> int:
        return len(self._candles)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    @property
    def visible_start_index(self) -> float:
        return self._visible_start_index

    @property
    def visible_candle_span(self) -> float:
        return self._visible_candle_span

    @property
    def pixels_per_candle(self) -> float:
        return self._plot_width() / self._visible_candle_span

    def set_time_view(self, start_index: float, candle_span: float) -> None:
        start = _finite_number(start_index)
        span = _finite_number(candle_span)
        if start is None or span is None or span <= 0:
            raise ValueError("time view requires finite start and positive span")
        candle_count = len(self._candles)
        normalized_span = min(float(max(1, candle_count)), span)
        maximum_start = max(0.0, candle_count - normalized_span)
        self._visible_start_index = min(maximum_start, max(0.0, start))
        self._visible_candle_span = normalized_span
        self.update()

    def to_candles(self) -> list[dict[str, Any]]:
        return deepcopy(self._candles)

    def marker_records(self) -> list[dict[str, Any]]:
        return deepcopy(self._markers)

    def set_marker_tooltips(
        self,
        tooltips: Mapping[tuple[int, str], str],
    ) -> None:
        for marker in self._markers:
            index = marker.get("evaluation_index")
            side = str(marker.get("side") or "").strip().upper()
            if marker.get("provenance") != "marking":
                tooltip = str(tooltips.get((index, side), ""))
                title, separator, rest = tooltip.partition(" ")
                marker["tooltip"] = {"BUY": "\uB9E4\uC218", "SELL": "\uB9E4\uB3C4"}.get(title, title) + separator + rest
        self._rebuild_marker_index()

    def marker_count(self, side: str | None = None) -> int:
        normalized = str(side or "").strip().upper()
        if not normalized:
            return len(self._markers)
        return sum(marker.get("side") == normalized for marker in self._markers)

    def _rebuild_marker_index(self) -> None:
        markers_by_index: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for order, marker in enumerate(self._markers):
            index = marker.get("evaluation_index")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            markers_by_index.setdefault(index, []).append((order, marker))
        self._markers_by_index = markers_by_index

    def _visible_marker_records(self) -> list[dict[str, Any]]:
        visible_start, visible_end = self._visible_index_bounds()
        ordered_records: list[tuple[int, dict[str, Any]]] = []
        for index in range(visible_start, visible_end):
            if self._is_index_visible(index):
                ordered_records.extend(self._markers_by_index.get(index, ()))
        ordered_records.sort(key=lambda record: record[0])
        return [marker for _order, marker in ordered_records]

    def set_selected_index(self, index: int | None) -> None:
        if index is not None and (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return
        self._selected_index = index
        self.update()

    @property
    def validation_range_drag_start_index(self) -> int | None:
        return self._validation_range_drag_start_index

    def _clear_validation_range_drag(self) -> None:
        self._validation_range_drag_start_index = None
        self._validation_range_drag_current_index = None

    def _plot_width(self) -> float:
        return float(max(1, self.width() - self._LEFT - self._RIGHT))

    def _x_for_index(self, index: int) -> float:
        return self._LEFT + (
            (float(index) - self._visible_start_index) + 0.5
        ) * self.pixels_per_candle

    def _index_float_for_x(self, x: float) -> float:
        return (
            self._visible_start_index
            + (float(x) - self._LEFT) / self.pixels_per_candle
            - 0.5
        )

    def _visible_index_bounds(self) -> tuple[int, int]:
        if not self._candles:
            return (0, 0)
        start = max(0, math.floor(self._visible_start_index))
        end = min(
            len(self._candles),
            math.ceil(self._visible_start_index + self._visible_candle_span),
        )
        return start, max(start, end)

    def _is_index_visible(self, index: int) -> bool:
        return (
            0 <= index < len(self._candles)
            and self._visible_start_index <= index + 0.5
            <= self._visible_start_index + self._visible_candle_span
        )

    def _nearest_candle_index(self, x: float) -> int | None:
        if (
            not self._candles
            or x < self._LEFT
            or x > self.width() - self._RIGHT
        ):
            return None
        raw_index = round(self._index_float_for_x(x))
        if not 0 <= raw_index < len(self._candles):
            return None
        return raw_index

    def _cancel_hover_pointer_update(self) -> None:
        self._pending_hover_pointer = None
        self._hover_frame_timer.stop()

    def _process_hover_pointer(
        self,
        pointer_x: float,
        pointer_y: float,
        global_pos: QPoint,
    ) -> None:
        snapped_index = self._nearest_candle_index(pointer_x)
        self._set_crosshair_pointer(snapped_index, pointer_y)
        marker = self._marker_at(pointer_x, pointer_y)
        marker_key = None if marker is None else self._marker_key(marker)
        self._set_hover_marker_key(marker_key)
        tooltip = "" if marker is None else str(marker.get("tooltip") or "")
        if not tooltip:
            tooltip = self.series_tooltip_at(pointer_x, pointer_y)
        if not tooltip:
            tooltip = self.candle_tooltip_at(pointer_x, pointer_y)
        if tooltip:
            if tooltip != self._active_tooltip:
                QToolTip.hideText()
            self._active_tooltip = tooltip
            QToolTip.showText(QPoint(global_pos), tooltip, self)
        else:
            self.clear_marker_tooltip()

    def _queue_hover_pointer(
        self,
        pointer_x: float,
        pointer_y: float,
        global_pos: QPoint,
    ) -> None:
        pointer = (float(pointer_x), float(pointer_y), QPoint(global_pos))
        if not self._hover_frame_timer.isActive():
            self._pending_hover_pointer = None
            self._process_hover_pointer(*pointer)
            self._hover_frame_timer.start()
            return
        self._pending_hover_pointer = pointer

    def _hover_frame_timeout(self) -> None:
        pointer = self._pending_hover_pointer
        self._pending_hover_pointer = None
        if pointer is None:
            return
        self._process_hover_pointer(*pointer)
        self._hover_frame_timer.start()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._cancel_hover_pointer_update()
            x = float(event.pos().x())
            y = float(event.pos().y())
            self._drag_press_x = x
            self._drag_press_y = y
            self._drag_last_x = x
            self._drag_last_y = y
            self._pan_drag_active = False
            self._pan_drag_axis = None
            self._begin_pointer_interaction()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_press_x is not None:
            was_drag = self._pan_drag_active
            self._clear_pan_drag()
            self._end_pointer_interaction(cancelled=False)
            if not was_drag:
                index = self._nearest_candle_index(event.pos().x())
                if index is not None:
                    self.set_selected_index(index)
                    self.bar_selected.emit(index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if self.is_pointer_interaction_active():
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta < 0:
            # Respect a queued reversal before deciding that zoom-out is a no-op.
            pending = getattr(self.window(), "_pending_wheel_zoom", None)
            effective_span = (
                pending[2]
                if isinstance(pending, tuple) and len(pending) == 3
                and pending[0] == "time"
                else self._visible_candle_span
            )
            owner = self.window()
            if (
                effective_span >= _MAX_SIGNAL_VALIDATION_VISIBLE_CANDLE_SPAN
                and not (
                    getattr(owner, "_historical_pool_installed", False)
                    and int(getattr(owner, "_historical_candle_count", 0)) < 5000
                )
            ):
                event.accept()
                return
        if delta:
            self.time_scale_wheel_requested.emit(float(event.pos().x()), int(delta))
            event.accept()
            return
        super().wheelEvent(event)

    def _clear_pan_drag(self) -> None:
        if self._time_pan_preview_started or self._time_pan_preview_offset_x:
            self._flush_time_pan_preview()
        self._flush_pending_pan()
        self._pan_frame_timer.stop()
        self._pending_pan_delta_x = 0.0
        self._pending_pan_delta_y = 0.0
        self._time_pan_preview_raw_offset_x = 0.0
        self._set_time_pan_preview_offset(0.0)
        self._time_pan_preview_started = False
        self._drag_press_x = None
        self._drag_press_y = None
        self._drag_last_x = None
        self._drag_last_y = None
        self._pan_drag_active = False
        self._pan_drag_axis = None

    def is_pointer_interaction_active(self) -> bool:
        return self._pointer_interaction_active

    def _begin_pointer_interaction(self) -> None:
        if self._pointer_interaction_active:
            return
        self._pointer_interaction_active = True
        self.pointer_interaction_started.emit()

    def _end_pointer_interaction(self, *, cancelled: bool) -> None:
        if not self._pointer_interaction_active:
            return
        self._pointer_interaction_active = False
        self.pointer_interaction_ended.emit(bool(cancelled))

    def discard_pointer_interaction(self) -> None:
        had_preview = bool(self._time_pan_preview_offset_x)
        self._pan_frame_timer.stop()
        self._pending_pan_delta_x = 0.0
        self._pending_pan_delta_y = 0.0
        self._time_pan_preview_raw_offset_x = 0.0
        self._time_pan_preview_offset_x = 0.0
        self._time_pan_preview_started = False
        self._drag_press_x = None
        self._drag_press_y = None
        self._drag_last_x = None
        self._drag_last_y = None
        self._pan_drag_active = False
        self._pan_drag_axis = None
        if had_preview:
            self.update()
        self._end_pointer_interaction(cancelled=True)

    def _queue_time_pan_delta(self, delta_x: float) -> None:
        if not delta_x:
            return
        self._time_pan_gesture_delta_x += float(delta_x)
        if not self._time_pan_preview_started:
            self._time_pan_preview_started = True
            legal_delta_x = self._clamp_time_pan_delta(float(delta_x))
            if legal_delta_x:
                self.pan_requested.emit(legal_delta_x, 0.0)
            self._time_pan_preview_raw_offset_x = 0.0
            self._set_time_pan_preview_offset(0.0)
            return
        legal_offset_x = self._clamp_time_pan_delta(
            self._time_pan_preview_offset_x + float(delta_x)
        )
        self._time_pan_preview_raw_offset_x = legal_offset_x
        self._set_time_pan_preview_offset(legal_offset_x)

    def _clamp_time_pan_delta(self, delta_x: float) -> float:
        maximum_start = max(
            0.0,
            float(len(self._candles)) - self._visible_candle_span,
        )
        pixels = self.pixels_per_candle
        legal_minimum = (
            self._visible_start_index - maximum_start
        ) * pixels
        legal_maximum = self._visible_start_index * pixels
        return min(legal_maximum, max(legal_minimum, float(delta_x)))

    def _set_time_pan_preview_offset(self, offset_x: float) -> None:
        if offset_x == self._time_pan_preview_offset_x:
            return
        self._time_pan_preview_offset_x = offset_x
        self.update()

    def _flush_time_pan_preview(self) -> None:
        delta_x = self._time_pan_preview_offset_x
        self._time_pan_preview_raw_offset_x = 0.0
        self._time_pan_preview_offset_x = 0.0
        self._time_pan_preview_started = False
        if delta_x:
            self.update()
            self.pan_requested.emit(delta_x, 0.0)

    def _queue_pan_delta(self, delta_x: float, delta_y: float) -> None:
        if not delta_x and not delta_y:
            return
        if not self._pan_frame_timer.isActive():
            self._pan_frame_timer.start()
            self.pan_requested.emit(float(delta_x), float(delta_y))
            return
        self._pending_pan_delta_x += float(delta_x)
        self._pending_pan_delta_y += float(delta_y)

    def _flush_pending_pan(self, *, continue_cadence: bool = False) -> None:
        delta_x = self._pending_pan_delta_x
        delta_y = self._pending_pan_delta_y
        self._pending_pan_delta_x = 0.0
        self._pending_pan_delta_y = 0.0
        if delta_x or delta_y:
            if continue_cadence:
                self._pan_frame_timer.start()
            self.pan_requested.emit(delta_x, delta_y)

    def _pan_frame_timeout(self) -> None:
        self._flush_pending_pan(continue_cadence=True)


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    return text or "-"


def estimated_signal_return_percent(
    replay_snapshot: ValidationReplaySnapshot,
) -> float | None:
    """Return the aggregate estimate across completed BUY-to-SELL cycles."""
    if not isinstance(replay_snapshot, ValidationReplaySnapshot):
        raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
    cycles = completed_validation_cycles(
        replay_snapshot.to_candles(),
        replay_snapshot.to_entries(),
    )
    return aggregate_completed_cycle_return_percent(cycles)


def _stock_metadata_tooltip(
    stock: ValidationStockRef | None,
    metadata: object,
) -> str:
    if stock is None:
        return ""
    record = dict(metadata) if isinstance(metadata, dict) else {}

    def finite_number(value: object) -> float | None:
        if isinstance(value, bool) or value in (None, "", "-"):
            return None
        try:
            number = float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def price_text(value: object) -> str:
        number = finite_number(value)
        if number is None or number <= 0:
            return "-"
        return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"

    def signed_percent_text(value: object) -> str:
        number = finite_number(value)
        return f"{number:+.2f}%" if number is not None else "-"

    def strength_text(value: object) -> str:
        number = finite_number(value)
        return f"{number:.1f}" if number is not None and number >= 0 else "-"

    status_tokens = [
        token.strip()
        for token in str(record.get("status", "") or "").split("|")
        if token.strip()
    ]
    status = " | ".join(
        token
        for token in status_tokens
        if not token.startswith("증거금")
        and token not in {"담보대출", "신용가능"}
    ) or "-"
    market = str(record.get("market", "") or "").strip().upper() or "-"
    first_line = [
        f"▪  {stock.code} {stock.name}",
        market,
        f"상태 {status}",
    ]
    if record.get("nxt_available") is True:
        first_line.append("NXT")
    second_line = [
        f"▪  현재가 {price_text(record.get('current_price'))}",
        f"시가 {price_text(record.get('open_price'))}",
        f"고가 {price_text(record.get('high_price'))}",
        f"저가 {price_text(record.get('low_price'))}",
    ]
    third_line = [
        f"▪  등락률 {signed_percent_text(record.get('change_rate'))}",
        f"전일대비 {signed_percent_text(record.get('previous_day_volume_rate'))}",
        f"체결강도 {strength_text(record.get('execution_strength'))}",
    ]
    separator = "  |  "
    return "\n".join(
        separator.join(parts)
        for parts in (first_line, second_line, third_line)
    )


def _valid_close(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _marker_records(
    candles: list[dict[str, Any]],
    entries: list[ValidationReplayEntry],
    tooltips: dict[tuple[int, str], str] | None = None,
) -> list[dict[str, Any]]:
    markers = []
    for entry in entries:
        index = entry.evaluation_index
        if (
            entry.signal not in {"BUY", "SELL"}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(candles)
            or str(candles[index].get("time") or "") != entry.evaluation_time
        ):
            continue
        markers.append({
            "side": entry.signal,
            "evaluation_index": index,
            "evaluation_time": entry.evaluation_time,
            "tooltip": str((tooltips or {}).get((index, entry.signal), "")),
        })
    return markers


@dataclass(frozen=True, slots=True)
class IndicatorFollowValidationCompletedCycle:
    cycle_number: int
    buy_indexes: tuple[int, ...]
    buy_start_index: int
    buy_end_index: int
    buy_count: int
    average_buy_price: float
    sell_index: int
    sell_price: float
    estimated_return_percent: float
    buy_quantity: int = 0
    buy_cost: float = 0.0
    trading_cost_amount: float = 0.0


@dataclass(frozen=True, slots=True)
class _ValidationEntryState:
    ui_state_json: str
    stock: ValidationStockRef | None
    candle_count: int

    def to_ui_state(self) -> dict[str, Any]:
        state = json.loads(self.ui_state_json)
        if not isinstance(state, dict):
            raise ValueError("entry UI state must decode to an object")
        return state


@dataclass(frozen=True, slots=True)
class _ValidationChartViewState:
    visible_start_index: float
    visible_candle_span: float
    default_price_minimum: float | None
    default_price_maximum: float | None
    current_price_minimum: float | None
    current_price_maximum: float | None
    time_scale_manually_adjusted: bool
    price_scale_manually_adjusted: bool
    selected_evaluation_index: int | None
    lower_pane_heights: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class _EntryChartViewAnchor:
    view_state: _ValidationChartViewState
    center_time: str
    center_fraction: float
    selected_time: str | None


@dataclass(frozen=True, slots=True)
class _HistoryExtensionViewAnchor:
    view_state: _ValidationChartViewState
    visible_start_time: str
    visible_start_fraction: float
    selected_time: str | None
    validated_ui_fingerprint: str | None
    primary_action_state: str


@dataclass(frozen=True, slots=True)
class PreparedSignalValidationPresentation:
    settings_hash: str
    calculation_signature: tuple[int, str, str]
    chart_start_index: int
    chart_count: int
    calculation_entries: tuple[ValidationReplayEntry, ...]
    chart_entries: tuple[ValidationReplayEntry, ...]
    visualization_descriptors: tuple[ValidationFilterDescriptor, ...]
    visualization_cache: ValidationIndicatorSeriesCache
    visualization_active_by_marker: dict[tuple[int, str], tuple[str, ...]]
    signal_tooltips: dict[tuple[int, str], str]
    visualization_evidence_identities_by_side: dict[str, tuple[str, ...]] | None = None
    trust_metadata: dict[str, Any] | None = None
    chart_calculation_indexes: tuple[int | None, ...] = ()


def _detached_validation_candles(candles):
    """Clone plain scalar rows; retain deepcopy semantics for all other inputs."""
    none_type = type(None)
    if type(candles) is list and all(
        type(candle) is dict
        and all(kind is str for kind in map(type, candle))
        and all(
            kind is float or kind is str or kind is int
            or kind is bool or kind is none_type
            for kind in map(type, candle.values())
        )
        for candle in candles
    ):
        # Preserve repeated-row aliases just as deepcopy's memo does.
        copied = {id(candle): dict(candle) for candle in candles}
        return [copied[id(candle)] for candle in candles]
    return deepcopy(candles)


def _validation_candle_signature(
    candles: list[dict[str, Any]],
) -> tuple[int, str, str]:
    return (
        len(candles),
        str(candles[0].get("time") or "") if candles else "",
        str(candles[-1].get("time") or "") if candles else "",
    )


def _integrated_display_with_krx_prefix(
    calculation_candles: list[dict[str, Any]],
    integrated_candles: list[dict[str, Any]],
    target_count: int,
) -> list[dict[str, Any]] | None:
    """Keep the full calculation history only when display is its exact suffix."""
    if (
        isinstance(target_count, bool)
        or not isinstance(target_count, int)
        or target_count <= 0
        or not calculation_candles
        or not integrated_candles
    ):
        return None
    source: list[dict[str, Any]] = []
    display: list[dict[str, Any]] = []
    for rows, destination in ((calculation_candles, source), (integrated_candles, display)):
        previous = ""
        for candle in rows:
            if not isinstance(candle, dict):
                return None
            stamp = str(candle.get("time") or "")
            if not stamp or (previous and stamp <= previous):
                return None
            destination.append(candle)
            previous = stamp
    if len(display) > len(source) or source[-len(display):] != display:
        return None
    return _detached_validation_candles(source[-target_count:])


def _calculation_indexes_for_display(
    calculation_candles: list[dict[str, Any]],
    display_candles: list[dict[str, Any]],
) -> tuple[int, ...]:
    lookup: dict[str, int] = {}
    for index, candle in enumerate(calculation_candles):
        timestamp = str(candle.get("time") or "")
        if not timestamp or timestamp in lookup:
            raise ValueError("CALCULATION_CANDLE_TIME_IDENTITY_INVALID")
        lookup[timestamp] = index
    indexes = tuple(
        lookup.get(str(candle.get("time") or ""), -1)
        for candle in display_candles
    )
    if any(index < 0 for index in indexes) or any(
        right <= left for left, right in zip(indexes, indexes[1:])
    ):
        raise ValueError("DISPLAY_CANDLE_TIME_IDENTITY_INVALID")
    return indexes


def _calculation_indexes_for_mixed_display(
    calculation_candles: list[dict[str, Any]],
    display_candles: list[dict[str, Any]],
) -> tuple[int | None, ...]:
    lookup: dict[str, int] = {}
    for index, candle in enumerate(calculation_candles):
        timestamp = str(candle.get("time") or "")
        if not timestamp or timestamp in lookup:
            raise ValueError("CALCULATION_CANDLE_TIME_IDENTITY_INVALID")
        lookup[timestamp] = index
    display_times = tuple(str(candle.get("time") or "") for candle in display_candles)
    if any(not timestamp for timestamp in display_times) or (
        len(set(display_times)) != len(display_times)
    ):
        raise ValueError("DISPLAY_CANDLE_TIME_IDENTITY_INVALID")
    indexes = tuple(lookup.get(timestamp) for timestamp in display_times)
    mapped = tuple(index for index in indexes if index is not None)
    if any(right <= left for left, right in zip(mapped, mapped[1:])):
        raise ValueError("DISPLAY_CANDLE_TIME_IDENTITY_INVALID")
    return indexes


def _calculation_bounds_for_display_range(
    calculation_candles: list[dict[str, Any]],
    display_candles: list[dict[str, Any]],
    start: int,
    end: int,
) -> tuple[int, int] | None:
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start <= end < len(display_candles)
    ):
        return None
    start_time = str(display_candles[start].get("time") or "")
    end_time = str(display_candles[end].get("time") or "")
    if not start_time or not end_time:
        return None
    lower, upper = sorted((start_time, end_time))
    matches = [
        index
        for index, candle in enumerate(calculation_candles)
        if lower <= str(candle.get("time") or "") <= upper
    ]
    return (matches[0], matches[-1]) if matches else None

def _mapped_validation_entries(
    calculation_candles: list[dict[str, Any]],
    entries: list[ValidationReplayEntry] | tuple[ValidationReplayEntry, ...],
    *,
    chart_start_index: int,
    chart_count: int,
    chart_calculation_indexes: tuple[int | None, ...] | None = None,
) -> tuple[list[ValidationReplayEntry], list[ValidationReplayEntry]]:
    calculation_lookup = {
        str(candle.get("time") or ""): index
        for index, candle in enumerate(calculation_candles)
    }
    calculation_entries: list[ValidationReplayEntry] = []
    chart_entries: list[ValidationReplayEntry] = []
    displayed_indexes = (
        chart_calculation_indexes
        if chart_calculation_indexes is not None
        else tuple(range(chart_start_index, chart_start_index + chart_count))
    )
    chart_lookup = {
        calculation_index: chart_index
        for chart_index, calculation_index in enumerate(displayed_indexes)
        if calculation_index is not None
    }
    for entry in entries:
        calculation_index = calculation_lookup.get(entry.evaluation_time)
        if calculation_index is None:
            continue
        signal_index = None
        if entry.signal_index is not None:
            signal_index = calculation_lookup.get(str(entry.signal_time or ""))
            if signal_index is None:
                continue
        calculation_entry = entry.remap_indexes(
            calculation_index,
            signal_index,
        )
        calculation_entries.append(calculation_entry)
        chart_index = chart_lookup.get(calculation_index)
        if chart_index is None:
            continue
        chart_signal_index = (
            None
            if signal_index is None
            else chart_lookup.get(signal_index)
        )
        if signal_index is not None and chart_signal_index is None:
            continue
        chart_entries.append(entry.remap_indexes(
            chart_index,
            chart_signal_index,
        ))
    return calculation_entries, chart_entries


def prepare_signal_validation_presentation(
    calculation_candles: list[dict[str, Any]],
    signal_entries: list[ValidationReplayEntry] | tuple[ValidationReplayEntry, ...],
    settings_snapshot: ValidationSettingsSnapshot,
    *,
    chart_start_index: int,
    chart_count: int,
    trust_metadata: Mapping[str, Any] | None = None,
    display_candles: list[dict[str, Any]] | None = None,
    display_only_integrated: bool = False,
    visualization_descriptors: tuple[ValidationFilterDescriptor, ...] | None = None,
    precomputed_indicator_cache: ValidationIndicatorSeriesCache | None = None,
    capture_indicator_cache: Callable[[ValidationIndicatorSeriesCache], None] | None = None,
) -> PreparedSignalValidationPresentation:
    if not isinstance(settings_snapshot, ValidationSettingsSnapshot):
        raise TypeError("settings_snapshot must be ValidationSettingsSnapshot")
    chart_indexes = (
        (
            _calculation_indexes_for_mixed_display(
                calculation_candles,
                display_candles,
            )
            if display_only_integrated
            else _calculation_indexes_for_display(
                calculation_candles,
                display_candles,
            )
        )
        if display_candles is not None
        else tuple(range(chart_start_index, chart_start_index + chart_count))
    )
    if len(chart_indexes) != chart_count:
        raise ValueError("DISPLAY_CANDLE_COUNT_MISMATCH")
    calculation_entries, chart_entries = _mapped_validation_entries(
        calculation_candles,
        signal_entries,
        chart_start_index=chart_start_index,
        chart_count=chart_count,
        chart_calculation_indexes=chart_indexes,
    )
    rules = settings_snapshot.to_dict()
    evidence_descriptors = build_validation_filter_universe(rules)
    descriptors = (
        evidence_descriptors
        if visualization_descriptors is None
        else tuple(visualization_descriptors)
    )
    indicator_probe = (_perf_phase(
        None, calculation_bars=len(calculation_candles),
        display_bars=chart_count, entries=len(calculation_entries),
        descriptors=len(descriptors),
    ) if _PERF_ENABLED else None)
    if precomputed_indicator_cache is not None:
        if (
            not isinstance(precomputed_indicator_cache, ValidationIndicatorSeriesCache)
            or precomputed_indicator_cache.candle_count != len(calculation_candles)
        ):
            raise ValueError("INDICATOR_SERIES_CACHE_SOURCE_MISMATCH")
        full_cache = precomputed_indicator_cache
    else:
        full_cache = build_validation_indicator_cache(
            calculation_candles,
            rules,
            descriptors,
            entries=calculation_entries,
        )
        if capture_indicator_cache is not None:
            capture_indicator_cache(full_cache)
    if indicator_probe is not None:
        indicator_probe.checkpoint(
            'presentation.indicator_cache', series=len(full_cache.series),
        )
    cache = ValidationIndicatorSeriesCache(
        candle_count=chart_count,
        series=tuple(
            (
                identity,
                channel,
                tuple(
                    None if index is None else values[index]
                    for index in chart_indexes
                ),
            )
            for identity, channel, values in full_cache.series
        ),
    )
    if indicator_probe is not None:
        indicator_probe.checkpoint('presentation.chart_series_mapping')
    evidence_identities_by_side: dict[str, set[str]] = {
        "BUY": set(),
        "SELL": set(),
    }
    for entry in calculation_entries:
        side = str(entry.evaluation_side or "").strip().upper()
        if entry.signal != entry.evaluation_side or side not in evidence_identities_by_side:
            continue
        evidence_identities_by_side[side].update(
            active_filter_identities_for_entry(
                entry,
                rules,
                evidence_descriptors,
            )
        )

    active_by_marker: dict[tuple[int, str], tuple[str, ...]] = {}
    tooltips: dict[tuple[int, str], str] = {}
    for entry in chart_entries:
        if entry.signal != entry.evaluation_side:
            continue
        marker_key = (entry.evaluation_index, entry.evaluation_side)
        active_by_marker[marker_key] = active_filter_identities_for_entry(
            entry,
            rules,
            descriptors,
        )
        try:
            tooltip = signal_evidence_tooltip(entry, rules)
        except (TypeError, ValueError, OverflowError):
            tooltip = ""
        if tooltip:
            tooltips[marker_key] = tooltip
    return PreparedSignalValidationPresentation(
        settings_hash=settings_snapshot.rules_hash,
        calculation_signature=_validation_candle_signature(calculation_candles),
        chart_start_index=chart_start_index,
        chart_count=chart_count,
        calculation_entries=tuple(calculation_entries),
        chart_entries=tuple(chart_entries),
        visualization_descriptors=descriptors,
        visualization_cache=cache,
        visualization_active_by_marker=active_by_marker,
        signal_tooltips=tooltips,
        visualization_evidence_identities_by_side={
            side: tuple(sorted(identities))
            for side, identities in evidence_identities_by_side.items()
        },
        trust_metadata=deepcopy(dict(trust_metadata or {})),
        chart_calculation_indexes=chart_indexes,
    )


def _build_validation_projection_data(
    calculation_candles: list[dict[str, Any]],
    calculation_entries: list[ValidationReplayEntry],
    display_entries: list[ValidationReplayEntry],
    chart_indexes: tuple[int | None, ...],
    rules: Mapping[str, Any],
    descriptors: tuple[ValidationFilterDescriptor, ...],
    *,
    range_projection: tuple[
        list[dict[str, Any]],
        list[ValidationReplayEntry],
        tuple[int | None, ...],
    ] | None = None,
    cancel_predicate: Callable[[], bool] | None = None,
) -> tuple[
    ValidationIndicatorSeriesCache,
    dict[tuple[int, str], tuple[str, ...]],
]:
    """Build detached indicator geometry from plain immutable snapshots."""
    full_cache = build_validation_indicator_cache(
        calculation_candles,
        rules,
        descriptors,
        entries=calculation_entries,
        **({"cancel_predicate": cancel_predicate} if cancel_predicate is not None else {}),
    )
    if cancel_predicate is not None and cancel_predicate():
        raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
    if range_projection is not None:
        range_candles, range_entries, range_indexes = range_projection
        average_descriptors = []
        for descriptor in descriptors:
            if cancel_predicate is not None and cancel_predicate():
                raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
            parameters = descriptor.parameters
            condition = (
                parameters.get("condition")
                if isinstance(parameters, Mapping)
                else None
            )
            condition = condition if isinstance(condition, Mapping) else {}
            if (
                "AVG_PRICE" in descriptor.series_keys
                or str(condition.get("target") or "").upper() == "AVG_PRICE"
                or str(condition.get("compare_target") or "").upper()
                == "AVG_PRICE"
            ):
                average_descriptors.append(descriptor)
        if range_candles and average_descriptors:
            range_cache = build_validation_indicator_cache(
                range_candles,
                rules,
                tuple(average_descriptors),
                entries=range_entries,
                **({"cancel_predicate": cancel_predicate} if cancel_predicate is not None else {}),
            )
            range_series = {
                (identity, channel): values
                for identity, channel, values in range_cache.series
            }
            average_ids = {
                descriptor.identity for descriptor in average_descriptors
            }
            merged_series = []
            for identity, channel, values in full_cache.series:
                if cancel_predicate is not None and cancel_predicate():
                    raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
                local_values = range_series.get((identity, channel))
                if (
                    identity in average_ids
                    and local_values is not None
                    and len(local_values) == len(range_indexes)
                ):
                    merged_values = list(values)
                    for calculation_index, local_value in zip(
                        range_indexes,
                        local_values,
                    ):
                        if cancel_predicate is not None and cancel_predicate():
                            raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
                        if calculation_index is not None:
                            merged_values[calculation_index] = local_value
                    values = tuple(merged_values)
                merged_series.append((identity, channel, values))
            full_cache = ValidationIndicatorSeriesCache(
                candle_count=full_cache.candle_count,
                series=tuple(merged_series),
            )

    if cancel_predicate is not None and cancel_predicate():
        raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
    projected_series = []
    for identity, channel, values in full_cache.series:
        if cancel_predicate is not None and cancel_predicate():
            raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
        projected_values = []
        for index in chart_indexes:
            if cancel_predicate is not None and cancel_predicate():
                raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
            projected_values.append(None if index is None else values[index])
        projected_series.append((identity, channel, tuple(projected_values)))
    cache = ValidationIndicatorSeriesCache(
        candle_count=len(chart_indexes),
        series=tuple(projected_series),
    )
    active_by_marker = {}
    for entry in display_entries:
        if cancel_predicate is not None and cancel_predicate():
            raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
        if entry.signal != entry.evaluation_side:
            continue
        active_by_marker[(
            entry.evaluation_index,
            entry.evaluation_side,
        )] = active_filter_identities_for_entry(
            entry,
            rules,
            descriptors,
        )
        if cancel_predicate is not None and cancel_predicate():
            raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
    if cancel_predicate is not None and cancel_predicate():
        raise ValidationProjectionCancelled("VALIDATION_PROJECTION_CANCELLED")
    return cache, active_by_marker


def aggregate_completed_cycle_return_percent(
    cycles: list[IndicatorFollowValidationCompletedCycle]
    | tuple[IndicatorFollowValidationCompletedCycle, ...],
) -> float | None:
    """Aggregate completed Cycle returns using each Cycle's estimated cost."""
    if not cycles:
        return None
    total_cost = sum(
        cycle.buy_cost
        if math.isfinite(cycle.buy_cost) and cycle.buy_cost > 0
        else cycle.average_buy_price * cycle.buy_count
        for cycle in cycles
    )
    if not math.isfinite(total_cost) or total_cost <= 0:
        return None
    total_profit = sum(
        (
            cycle.buy_cost
            if math.isfinite(cycle.buy_cost) and cycle.buy_cost > 0
            else cycle.average_buy_price * cycle.buy_count
        )
        * cycle.estimated_return_percent
        / 100.0
        for cycle in cycles
    )
    aggregate = total_profit / total_cost * 100.0
    return aggregate if math.isfinite(aggregate) else None


def _format_summary_return_percent(value: float | None) -> str:
    number = _finite_number(value)
    if number is None:
        number = 0.0
    return f"{number:+.2f}%" if number != 0 else "0.00%"


def _completed_cycle_invested_amount(
    cycle: IndicatorFollowValidationCompletedCycle,
) -> float | None:
    quantity = (
        cycle.buy_quantity
        if isinstance(cycle.buy_quantity, int)
        and not isinstance(cycle.buy_quantity, bool)
        and cycle.buy_quantity > 0
        else cycle.buy_count
    )
    if quantity <= 0:
        return None
    invested = (
        cycle.buy_cost
        if math.isfinite(cycle.buy_cost) and cycle.buy_cost > 0
        else cycle.average_buy_price * quantity
    )
    return invested if math.isfinite(invested) and invested > 0 else None


def _completed_cycle_profit_amount(
    cycle: IndicatorFollowValidationCompletedCycle,
) -> float | None:
    invested = _completed_cycle_invested_amount(cycle)
    if invested is None:
        return None
    quantity = (
        cycle.buy_quantity
        if isinstance(cycle.buy_quantity, int)
        and not isinstance(cycle.buy_quantity, bool)
        and cycle.buy_quantity > 0
        else cycle.buy_count
    )
    sell_amount = cycle.sell_price * quantity
    trading_cost = (
        cycle.trading_cost_amount
        if math.isfinite(cycle.trading_cost_amount)
        and cycle.trading_cost_amount > 0
        else 0.0
    )
    profit = sell_amount - invested - trading_cost
    return profit if math.isfinite(profit) else None


def _average_completed_cycle_invested_amount(
    cycles: list[IndicatorFollowValidationCompletedCycle]
    | tuple[IndicatorFollowValidationCompletedCycle, ...],
) -> float:
    invested_values = [
        invested
        for cycle in cycles
        if (invested := _completed_cycle_invested_amount(cycle)) is not None
    ]
    if not invested_values:
        return 0.0
    return sum(invested_values) / len(invested_values)


def _completed_cycle_financial_summary(
    cycles: list[IndicatorFollowValidationCompletedCycle]
    | tuple[IndicatorFollowValidationCompletedCycle, ...],
) -> tuple[float, float, float | None, float]:
    total_invested = 0.0
    total_sell_amount = 0.0
    total_trading_cost = 0.0
    total_quantity = 0
    for cycle in cycles:
        quantity = (
            cycle.buy_quantity
            if isinstance(cycle.buy_quantity, int)
            and not isinstance(cycle.buy_quantity, bool)
            and cycle.buy_quantity > 0
            else cycle.buy_count
        )
        invested = (
            cycle.buy_cost
            if math.isfinite(cycle.buy_cost) and cycle.buy_cost > 0
            else cycle.average_buy_price * quantity
        )
        sell_amount = cycle.sell_price * quantity
        trading_cost = (
            cycle.trading_cost_amount
            if math.isfinite(cycle.trading_cost_amount)
            and cycle.trading_cost_amount > 0
            else 0.0
        )
        if (
            quantity <= 0
            or not math.isfinite(invested)
            or invested <= 0
            or not math.isfinite(sell_amount)
        ):
            continue
        total_quantity += quantity
        total_invested += invested
        total_sell_amount += sell_amount
        total_trading_cost += trading_cost
    average_price = (
        total_invested / total_quantity
        if total_quantity > 0 and total_invested > 0
        else None
    )
    profit_amount = total_sell_amount - total_invested - total_trading_cost
    sell_price = (
        total_sell_amount / total_quantity
        if total_quantity > 0
        else 0.0
    )
    return total_invested, profit_amount, average_price, sell_price


def _format_summary_amount(value: float, *, signed: bool = False) -> str:
    if not math.isfinite(value):
        value = 0.0
    if signed and value != 0:
        return f"{value:+,.0f}원"
    return f"{value:,.0f}원"


def completed_validation_cycles(
    candles: list[dict[str, Any]],
    entries: list[ValidationReplayEntry],
) -> list[IndicatorFollowValidationCompletedCycle]:
    """Project completed BUY-to-SELL segments from immutable replay results."""
    valid_entries = [
        entry
        for entry in entries
        if entry.signal in {"BUY", "SELL"}
        and isinstance(entry.evaluation_index, int)
        and not isinstance(entry.evaluation_index, bool)
        and 0 <= entry.evaluation_index < len(candles)
        and str(candles[entry.evaluation_index].get("time") or "")
        == entry.evaluation_time
    ]
    sell_indexes = sorted({
        entry.evaluation_index
        for entry in valid_entries
        if entry.signal == "SELL"
    })
    cycles: list[IndicatorFollowValidationCompletedCycle] = []
    for sell_index in sell_indexes:
        context = build_validation_average_price_context(
            sell_index,
            "SELL",
            candles,
            valid_entries,
        )
        trace_context = context.get("validation_trace_context")
        buy_indexes = (
            trace_context.get("contributing_buy_indexes")
            if isinstance(trace_context, dict)
            else None
        )
        normalized_buy_indexes = tuple(
            index
            for index in (buy_indexes if isinstance(buy_indexes, list) else [])
            if isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < sell_index
        )
        average_buy = _valid_close(context.get("average_price"))
        sell_price = validation_virtual_fill_price(candles[sell_index])
        if not normalized_buy_indexes or average_buy is None or sell_price is None:
            continue
        estimated_return = (sell_price - average_buy) / average_buy * 100.0
        if not math.isfinite(estimated_return):
            continue
        cycles.append(IndicatorFollowValidationCompletedCycle(
            cycle_number=len(cycles) + 1,
            buy_indexes=normalized_buy_indexes,
            buy_start_index=normalized_buy_indexes[0],
            buy_end_index=normalized_buy_indexes[-1],
            buy_count=len(normalized_buy_indexes),
            average_buy_price=average_buy,
            sell_index=sell_index,
            sell_price=sell_price,
            estimated_return_percent=estimated_return,
        ))
    return cycles


def validation_execution_cycles(
    candles: list[dict[str, Any]],
    entries: list[ValidationReplayEntry],
    execution_policy: dict[str, Any],
) -> list[IndicatorFollowValidationCompletedCycle]:
    simulation = simulate_validation_execution(candles, entries, execution_policy)
    return [
        IndicatorFollowValidationCompletedCycle(
            cycle_number=cycle.cycle_number,
            buy_indexes=cycle.buy_indexes,
            buy_start_index=cycle.buy_start_index,
            buy_end_index=cycle.buy_end_index,
            buy_count=cycle.buy_count,
            average_buy_price=cycle.average_buy_price,
            sell_index=cycle.sell_index,
            sell_price=cycle.sell_price,
            estimated_return_percent=cycle.estimated_return_percent,
            buy_quantity=cycle.buy_quantity,
            buy_cost=cycle.buy_cost,
            trading_cost_amount=cycle.trading_cost_amount,
        )
        for cycle in simulation.cycles
    ]


@lru_cache(maxsize=8192)
def _parse_candle_time_text(text: str) -> datetime | None:
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _parse_candle_time(value: Any) -> datetime | None:
    return _parse_candle_time_text(str(value or "").strip())


def _time_axis_label_records(
    candles: list[dict[str, Any]],
    *,
    target_count: int = 10,
) -> list[dict[str, Any]]:
    """Select readable labels from real Candle timestamps without inferring dates."""
    parsed = [
        (index, parsed_time)
        for index, candle in enumerate(candles)
        if (parsed_time := _parse_candle_time(candle.get("time"))) is not None
    ]
    if not parsed:
        return []
    first_time = parsed[0][1]
    last_time = parsed[-1][1]
    duration = last_time - first_time
    year_changed = first_time.year != last_time.year

    boundary_indices = [
        parsed[position][0]
        for position in range(1, len(parsed))
        if parsed[position - 1][1].date() != parsed[position][1].date()
    ]
    priority = [parsed[0][0], parsed[-1][0], *boundary_indices]
    uniform_slots = max(2, min(12, int(target_count)))
    if len(parsed) > 1:
        priority.extend(
            parsed[round(slot * (len(parsed) - 1) / (uniform_slots - 1))][0]
            for slot in range(uniform_slots)
        )

    maximum_labels = 12
    minimum_gap = max(1, len(candles) // maximum_labels)
    selected: list[int] = []
    for index in priority:
        if index in selected:
            continue
        if index not in (parsed[0][0], parsed[-1][0]) and any(
            abs(index - existing) < minimum_gap for existing in selected
        ):
            continue
        selected.append(index)
        if len(selected) >= maximum_labels:
            break
    selected = sorted(set(selected))

    times_by_index = dict(parsed)
    records = []
    for index in selected:
        candle_time = times_by_index[index]
        if year_changed:
            label = candle_time.strftime("%Y/%m/%d")
        elif duration <= timedelta(days=1):
            label = candle_time.strftime("%H:%M")
        elif duration <= timedelta(days=3):
            previous = times_by_index.get(index - 1)
            if previous is None or previous.date() != candle_time.date():
                label = candle_time.strftime("%m/%d %H:%M")
            else:
                label = candle_time.strftime("%H:%M")
        elif duration < timedelta(days=7):
            label = candle_time.strftime("%m/%d %H:%M")
        elif duration < timedelta(days=90):
            label = candle_time.strftime("%m/%d")
        else:
            label = candle_time.strftime("%y/%m/%d")
        records.append({
            "index": index,
            "time": candles[index].get("time"),
            "label": label,
        })
    return records


@dataclass(frozen=True, slots=True)
class _ValidationPriceScale:
    minimum: float
    maximum: float
    plot_top: int
    plot_bottom: int

    def y_for_price(self, price: float) -> float:
        ratio = (self.maximum - price) / (self.maximum - self.minimum)
        return self.plot_top + ratio * (self.plot_bottom - self.plot_top)

    def price_for_y(self, y: float) -> float:
        ratio = (float(y) - self.plot_top) / (self.plot_bottom - self.plot_top)
        return self.maximum - ratio * (self.maximum - self.minimum)

    def tick_records(self) -> list[dict[str, Any]]:
        return [
            {
                "step": step,
                "price": self.maximum - (self.maximum - self.minimum) * step / 4,
                "y": int(self.plot_top + (self.plot_bottom - self.plot_top) * step / 4),
            }
            for step in range(5)
        ]


def _validation_price_scale(
    candles: list[dict[str, Any]],
    height: int,
    *,
    plot_top: int,
    plot_bottom_margin: int,
    minimum: float | None = None,
    maximum: float | None = None,
) -> _ValidationPriceScale | None:
    if minimum is None or maximum is None:
        prices = [
            number
            for candle in candles
            for field in ("open", "high", "low", "close")
            if (number := _finite_number(candle.get(field))) is not None
        ]
        if not prices:
            return None
        minimum = min(prices)
        maximum = max(prices)
    if maximum == minimum:
        padding = max(abs(maximum) * 0.01, 1.0)
        minimum -= padding
        maximum += padding
    return _ValidationPriceScale(
        minimum=minimum,
        maximum=maximum,
        plot_top=plot_top,
        plot_bottom=max(plot_top + 1, int(height) - plot_bottom_margin),
    )


def _validation_price_text(value: float) -> str:
    return f"{float(value):,.0f}"


def _krx_regular_session_candles(
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return direct target bars whose start stamp is inside KRX regular trading."""
    return [
        candle
        for candle in candles
        if (
            (stamp := str(candle.get("time") or ""))
            and len(stamp) == 14
            and stamp.isdigit()
            and "0900" <= stamp[8:12] < "1530"
        )
    ]


class IndicatorFollowSignalValidationChartCanvas(
    _IndicatorFollowSignalValidationChartCanvasBase
):
    """V2 scroll canvas containing Candle, markers, and static filter series."""

    _LEFT = 12
    _TOP = 58
    _CANDLE_HORIZONTAL_GAP = 2.0
    _LOWER_TOTAL_HEIGHT = 180
    _PRICE_TO_LOWER_GAP = 24
    _LOWER_FAMILY_ORDER = (FAMILY_RSI, FAMILY_OCR_OSC)
    _MIN_LOWER_PANE_HEIGHT = 48.0
    _MIN_PRICE_PLOT_HEIGHT = 100.0
    _LOWER_BOUNDARY_HIT_TOLERANCE = 6.0
    _SERIES_HIT_TOLERANCE = 6.0
    _HOVER_FRAME_INTERVAL_MS = 16

    pane_layout_changed = pyqtSignal()
    price_legend_visibility_changed = pyqtSignal(object)
    price_legend_highlight_changed = pyqtSignal(object)
    price_legend_hide_unhighlighted_changed = pyqtSignal(bool)
    price_legend_prefix_reset_requested = pyqtSignal()
    lower_legend_highlight_changed = pyqtSignal(object)

    @staticmethod
    def _display_lower_family(family: str) -> str:
        return FAMILY_OCR_OSC if family == FAMILY_MACD_SIGNAL else family

    @staticmethod
    def _normalized_series_values(
        values: tuple[float | None, ...],
    ) -> tuple[float | None, ...]:
        return tuple(_finite_number(value) for value in values)

    def __init__(
        self,
        candles,
        markers,
        parent=None,
        *,
        visualization_descriptors: tuple[ValidationFilterDescriptor, ...] = (),
        visualization_cache: ValidationIndicatorSeriesCache | None = None,
        visualization_active_by_marker: dict[tuple[int, str], tuple[str, ...]] | None = None,
        lower_filter_presentations: tuple[
            ValidationLowerFilterPresentation, ...
        ] | None = None,
        hidden_price_filter_identities: tuple[str, ...] = (),
        highlighted_price_filter_identities: tuple[tuple[str, str], ...] = (),
        highlighted_lower_filter_identities: tuple[tuple[str, str], ...] = (),
        not_filter_identities: tuple[tuple[str, str], ...] = (),
        inner_not_filter_identities: tuple[tuple[str, str], ...] = (),
        hide_unhighlighted_price_filters: bool = False,
        timeframe_key: str | None = None,
        bridgeable_display_only_indexes: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__(candles, markers, parent)
        self._bridgeable_display_only_indexes = frozenset(
            bridgeable_display_only_indexes
        )
        self._timeframe_key = str(timeframe_key or "").strip().upper()
        self._is_minute_timeframe = False
        if self._timeframe_key:
            try:
                self._is_minute_timeframe = (
                    normalize_validation_timeframe(self._timeframe_key).get("kind")
                    == "MINUTE"
                )
            except (TypeError, ValueError):
                self._is_minute_timeframe = False
        self._active_tooltip = ""
        self._pending_hover_pointer: tuple[float, float, QPoint] | None = None
        self._hover_frame_timer = QTimer(self)
        self._hover_frame_timer.setSingleShot(True)
        self._hover_frame_timer.setInterval(self._HOVER_FRAME_INTERVAL_MS)
        self._hover_frame_timer.timeout.connect(self._hover_frame_timeout)
        self._price_minimum: float | None = None
        self._price_maximum: float | None = None
        self._time_view_initialized = False
        self._visualization_descriptors = tuple(visualization_descriptors)
        self._visualization_cache = visualization_cache
        self._lower_filter_presentations = (
            None
            if lower_filter_presentations is None
            else tuple(lower_filter_presentations)
        )
        self._not_filter_identities = {
            (str(side or "").strip().upper(), str(identity or "").strip())
            for side, identity in not_filter_identities
            if str(side or "").strip().upper() in {"BUY", "SELL"}
            and str(identity or "").strip()
        }
        self._inner_not_filter_identities = {
            (str(side or "").strip().upper(), str(identity or "").strip())
            for side, identity in inner_not_filter_identities
            if str(side or "").strip().upper() in {"BUY", "SELL"}
            and str(identity or "").strip()
        }.intersection(self._not_filter_identities)
        self._series_values_by_key = {
            (identity, channel): self._normalized_series_values(values)
            for identity, channel, values in (
                visualization_cache.series
                if visualization_cache is not None
                else ()
            )
        }
        self._visualization_active_by_marker = {
            (int(index), str(side).strip().upper()): tuple(identities)
            for (index, side), identities in (visualization_active_by_marker or {}).items()
        }
        self._hover_marker_key: tuple[int, str] | None = None
        self._pinned_marker_key: tuple[int, str] | None = None
        self._validation_range: tuple[int, int] | None = None
        self._completed_cycle_average_lines: tuple[
            IndicatorFollowValidationCompletedCycle, ...
        ] = ()
        self._selected_completed_cycle_number: int | None = None
        self._cycle_label_last_records = ()
        self._cycle_label_last_affine = None
        self._cycle_label_last_context = None
        self._cycle_label_last_signature = None
        self._cycle_label_last_cache_key = None
        self._cycle_label_handoff = None
        self._cycle_label_preview_active = False
        self._cycle_label_preview_records = ()
        self._cycle_label_preview_affine = None
        self._cycle_label_preview_context_key = None
        self._cycle_label_settle_timer = QTimer(self)
        self._cycle_label_settle_timer.setSingleShot(True)
        self._cycle_label_settle_timer.timeout.connect(self._settle_cycle_label_preview)
        self.pointer_interaction_ended.connect(self._cycle_label_pointer_ended)
        self._crosshair_hover_index: int | None = None
        self._crosshair_pointer_y: float | None = None
        self._static_chart_cache: QPixmap | None = None
        self._time_axis_layout_cache_key = None
        self._time_axis_layout_cache_records = ()
        self._static_chart_cache_key: tuple[Any, ...] | None = None
        self._completed_cycle_label_cache_key: tuple[Any, ...] | None = None
        self._completed_cycle_label_cache_records: tuple[dict[str, Any], ...] = ()
        self._series_line_path_cache_bounds: tuple[int, int] | None = None
        self._series_line_path_cache: dict[
            int,
            tuple[QPainterPath, bool],
        ] = {}
        self._series_polygon_cache: dict[
            int,
            tuple[tuple[int, int, QPolygonF], ...],
        ] = {}
        self._lower_families = tuple(
            family
            for family in self._LOWER_FAMILY_ORDER
            if any(
                descriptor.axis == LOWER_AXIS
                and self._display_lower_family(descriptor.family) == family
                for descriptor in self._visualization_descriptors
            )
        )
        self._lower_pane_heights: dict[str, float] = {}
        self._lower_boundary_drag_index: int | None = None
        self._price_legend_press_identities: tuple[str, tuple[str, ...]] | None = None
        self._price_legend_double_click_consumed = False
        self._hide_unhighlighted_price_filters = bool(hide_unhighlighted_price_filters)
        self._hidden_price_filter_identities = {
            str(identity or "").strip()
            for identity in hidden_price_filter_identities
            if str(identity or "").strip()
        }
        self._highlighted_price_filter_identities = {
            (str(side).upper(), str(identity).strip())
            for side, identity in highlighted_price_filter_identities
            if str(side).upper() in {"BUY", "SELL"} and str(identity).strip()
        }
        self._highlighted_lower_filter_identities = {
            (str(side).upper(), str(identity).strip())
            for side, identity in highlighted_lower_filter_identities
            if str(side).upper() in {"BUY", "SELL"} and str(identity).strip()
        }
        self._valid_price_filter_identities: set[tuple[str, str]] = set()
        self._valid_lower_filter_identities: set[tuple[str, str]] = set()
        self._valid_filter_index_ranges: dict[
            tuple[str, str], tuple[tuple[int, int], ...]
        ] = {}
        self._valid_filter_delay_ranges: dict[
            tuple[str, str], tuple[tuple[int, int, int], ...]
        ] = {}
        self._valid_filter_mode_enabled = False
        self._configured_ocr_turn_delays: dict[tuple[str, str], int] = {}
        self._indicator_transition_index_cache: dict[
            tuple[int, str], frozenset[int]
        ] = {}
        valid_price_keys = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_price_filter_identities.intersection_update(
            valid_price_keys
        )
        valid_lower_keys = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == LOWER_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_lower_filter_identities.intersection_update(
            valid_lower_keys
        )
        self._hide_unhighlighted_price_filters = bool(
            self._highlighted_price_filter_identities
            or self._highlighted_lower_filter_identities
        )
        self._sync_hidden_price_filter_identities()
        self._reset_lower_pane_layout_state()
        self._price_series_groups = self._build_series_groups(PRICE_AXIS)
        self._price_legend_series_groups = self._build_series_groups(
            PRICE_AXIS,
            include_hidden_price=True,
        )
        self._lower_series_groups = {
            family: self._build_series_groups(LOWER_AXIS, family=family)
            for family in self._lower_families
        }
        self._price_data_bounds_cache = self._compute_price_data_bounds()
        self._lower_data_bounds_cache = {
            family: self._compute_lower_data_bounds(family)
            for family in self._lower_families
        }
        self.setMouseTracking(True)
        minimum_height = (
            self._price_plot_top_margin()
            + self._BOTTOM
            + self._PRICE_TO_LOWER_GAP
            + self._LOWER_TOTAL_HEIGHT
            + 80
            if self._lower_families
            else 0
        )
        self.setMinimumHeight(minimum_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        extra = (
            self._PRICE_TO_LOWER_GAP + self._LOWER_TOTAL_HEIGHT
            if self._lower_families
            else 0
        )
        return QSize(self._MIN_WIDTH, 160 + extra)

    def _refresh_visible_data_bounds(self) -> None:
        self._price_data_bounds_cache = self._compute_price_data_bounds()
        self._lower_data_bounds_cache = {
            family: self._compute_lower_data_bounds(family)
            for family in self._lower_families
        }
        self._static_chart_cache = None
        self._static_chart_cache_key = None

    def set_time_view(
        self, start_index: float, candle_span: float, *, refresh_lower_bounds: bool = True,
    ) -> None:
        old_view = (self._visible_start_index, self._visible_candle_span)
        super().set_time_view(start_index, candle_span)
        if old_view != (self._visible_start_index, self._visible_candle_span):
            self._maybe_begin_cycle_label_preview()
        if not self._time_view_initialized:
            self._price_data_bounds_cache = self._compute_price_data_bounds()
            self._time_view_initialized = True
        if refresh_lower_bounds:
            self._lower_data_bounds_cache = {
                family: self._compute_lower_data_bounds(family)
                for family in self._lower_families
            }
        self._static_chart_cache = None
        self._static_chart_cache_key = None

    def set_marker_records(self, markers: list[dict[str, Any]]) -> None:
        """Publish marker ownership immediately, without rebuilding indicator series."""
        self._markers = deepcopy(markers)
        self._rebuild_marker_index()
        valid_keys = {
            (m["evaluation_index"], m["side"]) for m in self._markers
            if isinstance(m.get("evaluation_index"), int)
        }
        if self._hover_marker_key not in valid_keys:
            self._set_hover_marker_key(None)
        if self._pinned_marker_key not in valid_keys:
            self.clear_evidence_pin()
        self.clear_marker_tooltip()
        self.update()

    def set_visualization_projection(
        self,
        descriptors: tuple[ValidationFilterDescriptor, ...],
        cache: ValidationIndicatorSeriesCache | None,
        active_by_marker: dict[tuple[int, str], tuple[str, ...]],
        markers: list[dict[str, Any]],
        lower_filter_presentations: tuple[
            ValidationLowerFilterPresentation, ...
        ] | None = None,
        *,
        ocr_turn_delays: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        self._cancel_cycle_label_preview()
        self._markers = deepcopy(markers)
        self._rebuild_marker_index()
        self._visualization_descriptors = tuple(descriptors)
        if ocr_turn_delays is not None:
            self.set_ocr_turn_delays(ocr_turn_delays, repaint=False)
        self._visualization_cache = cache
        self._lower_filter_presentations = (
            None
            if lower_filter_presentations is None
            else tuple(lower_filter_presentations)
        )
        self._series_values_by_key = {
            (identity, channel): self._normalized_series_values(values)
            for identity, channel, values in (
                cache.series if cache is not None else ()
            )
        }
        self._series_line_path_cache_bounds = None
        self._series_line_path_cache.clear()
        self._series_polygon_cache.clear()
        self._indicator_transition_index_cache.clear()
        self._visualization_active_by_marker = {
            (int(index), str(side).strip().upper()): tuple(identities)
            for (index, side), identities in active_by_marker.items()
        }
        previous_lower_families = tuple(self._lower_families)
        self._lower_families = tuple(
            family
            for family in self._LOWER_FAMILY_ORDER
            if any(
                descriptor.axis == LOWER_AXIS
                and self._display_lower_family(descriptor.family) == family
                for descriptor in self._visualization_descriptors
            )
        )
        if self._lower_families != previous_lower_families:
            self._reset_lower_pane_layout_state()
        valid_price_keys = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_price_filter_identities.intersection_update(valid_price_keys)
        self._valid_price_filter_identities.intersection_update(valid_price_keys)
        valid_lower_keys = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == LOWER_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_lower_filter_identities.intersection_update(
            valid_lower_keys
        )
        self._valid_lower_filter_identities.intersection_update(valid_lower_keys)
        self._hide_unhighlighted_price_filters = bool(
            self._highlighted_price_filter_identities
            or self._highlighted_lower_filter_identities
        )
        self._sync_hidden_price_filter_identities()
        self._price_series_groups = self._build_series_groups(PRICE_AXIS)
        self._price_legend_series_groups = self._build_series_groups(
            PRICE_AXIS,
            include_hidden_price=True,
        )
        self._lower_series_groups = {
            family: self._build_series_groups(LOWER_AXIS, family=family)
            for family in self._lower_families
        }
        self._refresh_visible_data_bounds()
        if self._lower_families != previous_lower_families:
            self.pane_layout_changed.emit()
        self.update()

    @property
    def hidden_price_filter_identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._hidden_price_filter_identities))

    @property
    def highlighted_price_filter_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._highlighted_price_filter_identities))

    @property
    def hide_unhighlighted_price_filters(self) -> bool:
        return self._hide_unhighlighted_price_filters

    @property
    def highlighted_lower_filter_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._highlighted_lower_filter_identities))

    @property
    def valid_filter_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(
            self._valid_price_filter_identities
            | self._valid_lower_filter_identities
        ))

    def _effective_price_filter_identities(self) -> set[tuple[str, str]]:
        return (
            self._valid_price_filter_identities
            if self._valid_filter_mode_enabled
            else self._highlighted_price_filter_identities
        )

    def _effective_lower_filter_identities(self) -> set[tuple[str, str]]:
        return (
            self._valid_lower_filter_identities
            if self._valid_filter_mode_enabled
            else self._highlighted_lower_filter_identities
        )

    def _descriptor_highlighted(self, descriptor, side: str | None = None) -> bool:
        identity = str(descriptor.identity or "").strip()
        sides = (side,) if side else descriptor.sides
        emphasized = self._effective_price_filter_identities()
        return any((str(value).strip().upper(), identity) in emphasized
                   for value in sides)

    def _lower_descriptor_highlighted(
        self,
        descriptor: ValidationFilterDescriptor,
        side: str | None = None,
    ) -> bool:
        identity = str(descriptor.identity or "").strip()
        sides = (side,) if side else descriptor.sides
        emphasized = self._effective_lower_filter_identities()
        return any(
            (str(value or "").strip().upper(), identity) in emphasized
            for value in sides
        )

    def _descriptor_valid(
        self,
        descriptor: ValidationFilterDescriptor,
        side: str | None = None,
    ) -> bool:
        identities = (
            self._valid_price_filter_identities
            if descriptor.axis == PRICE_AXIS
            else self._valid_lower_filter_identities
        )
        identity = str(descriptor.identity or "").strip()
        sides = (side,) if side else descriptor.sides
        return any(
            (str(value or "").strip().upper(), identity) in identities
            for value in sides
        )

    @staticmethod
    def _normalized_lower_condition_channel(value: object) -> str:
        channel = str(value or "").strip().upper()
        return "OSC" if channel == "OCR" else channel

    def _required_lower_channels(
        self,
        descriptor: ValidationFilterDescriptor,
    ) -> frozenset[str]:
        available = {
            str(channel or "").strip().upper()
            for channel in descriptor.series_keys
            if str(channel or "").strip()
        }
        criterion = {
            channel for channel in available
            if channel.startswith("CRITERION")
        }
        if descriptor.family == FAMILY_RSI:
            return frozenset(criterion)
        condition = descriptor.parameters.get("condition")
        condition = condition if isinstance(condition, Mapping) else {}
        if descriptor.family == FAMILY_MACD_SIGNAL:
            required = {
                self._normalized_lower_condition_channel(condition.get(name))
                for name in ("target", "compare_target")
            }
            required.discard("")
            required.intersection_update(available)
            if not required and not criterion:
                required = set(available)
            return frozenset(required | criterion)
        if descriptor.family == FAMILY_OCR_OSC:
            required = {"OSC"} & available
            target = self._normalized_lower_condition_channel(
                condition.get("target")
            )
            if target in available:
                required.add(target)
            return frozenset(required | criterion)
        return frozenset(available)

    def _lower_channel_role(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
    ) -> str | None:
        if not self._lower_descriptor_highlighted(descriptor):
            return None
        normalized = str(channel or "").strip().upper()
        if descriptor.family == FAMILY_RSI and normalized == "RSI":
            return "COMMON"
        if normalized in self._required_lower_channels(descriptor):
            return "DIRECT"
        return None

    def _sync_hidden_price_filter_identities(self) -> None:
        self._hidden_price_filter_identities = {
            str(descriptor.identity or "").strip()
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            and str(descriptor.identity or "").strip()
            and self._price_visibility_filter_active()
            and not self._descriptor_highlighted(descriptor)
        }

    def _price_visibility_filter_active(self) -> bool:
        return bool(
            self._valid_filter_mode_enabled
            or self._hide_unhighlighted_price_filters
            or self._highlighted_price_filter_identities
            or self._highlighted_lower_filter_identities
        )

    def _lower_visibility_filter_active(self) -> bool:
        return self._price_visibility_filter_active()

    def _refresh_price_legend_visibility(self, *, emit: bool = True) -> None:
        self._price_series_groups = self._build_series_groups(PRICE_AXIS)
        self._price_legend_series_groups = self._build_series_groups(
            PRICE_AXIS,
            include_hidden_price=True,
        )
        self._price_data_bounds_cache = self._compute_price_data_bounds()
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        if emit:
            self.price_legend_visibility_changed.emit(
                self.hidden_price_filter_identities
            )
            self.price_legend_highlight_changed.emit(
                self.highlighted_price_filter_identities
            )
            self.price_legend_hide_unhighlighted_changed.emit(
                self._hide_unhighlighted_price_filters
            )
            self.pane_layout_changed.emit()
        self.update()

    def set_hidden_price_filter_identities(
        self,
        identities: tuple[str, ...] | list[str] | set[str],
        *,
        emit: bool = False,
    ) -> None:
        valid = {
            str(descriptor.identity or "").strip()
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            and str(descriptor.identity or "").strip()
        }
        self._hidden_price_filter_identities = {
            str(identity or "").strip()
            for identity in identities
            if str(identity or "").strip() in valid
        }
        self._refresh_price_legend_visibility(emit=emit)

    def set_highlighted_price_filter_identities(
        self,
        identities: tuple[tuple[str, str], ...] | list[tuple[str, str]] | set[tuple[str, str]],
        *,
        emit: bool = False,
    ) -> None:
        valid = {
            str(descriptor.identity or "").strip()
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_price_filter_identities = {
            (str(side).strip().upper(), str(identity).strip())
            for side, identity in identities
            if str(identity).strip() in valid
            and str(side).strip().upper() in {"BUY", "SELL"}
        }
        self._hide_unhighlighted_price_filters = bool(
            self._highlighted_price_filter_identities
            or self._highlighted_lower_filter_identities
        )
        self._sync_hidden_price_filter_identities()
        self._refresh_price_legend_visibility(emit=emit)

    def set_highlighted_lower_filter_identities(
        self,
        identities: tuple[tuple[str, str], ...]
        | list[tuple[str, str]]
        | set[tuple[str, str]],
        *,
        emit: bool = False,
    ) -> None:
        valid = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == LOWER_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        self._highlighted_lower_filter_identities = {
            (str(side).strip().upper(), str(identity).strip())
            for side, identity in identities
            if (str(side).strip().upper(), str(identity).strip()) in valid
        }
        self._hide_unhighlighted_price_filters = bool(
            self._highlighted_price_filter_identities
            or self._highlighted_lower_filter_identities
        )
        self._sync_hidden_price_filter_identities()
        self._refresh_price_legend_visibility(emit=emit)
        if emit:
            self.lower_legend_highlight_changed.emit(
                self.highlighted_lower_filter_identities
            )

    def set_not_filter_identities(
        self,
        identities: tuple[tuple[str, str], ...]
        | list[tuple[str, str]]
        | set[tuple[str, str]],
        inner_identities: tuple[tuple[str, str], ...]
        | list[tuple[str, str]]
        | set[tuple[str, str]] = (),
    ) -> None:
        self._not_filter_identities = {
            (str(side or "").strip().upper(), str(identity or "").strip())
            for side, identity in identities
            if str(side or "").strip().upper() in {"BUY", "SELL"}
            and str(identity or "").strip()
        }
        self._inner_not_filter_identities = {
            (str(side or "").strip().upper(), str(identity or "").strip())
            for side, identity in inner_identities
            if str(side or "").strip().upper() in {"BUY", "SELL"}
            and str(identity or "").strip()
        }.intersection(self._not_filter_identities)
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()

    def set_valid_filter_identities(
        self,
        identities: tuple[tuple[str, str], ...]
        | list[tuple[str, str]]
        | set[tuple[str, str]],
        *,
        index_ranges: Mapping[
            tuple[str, str], tuple[tuple[int, int], ...]
        ] | None = None,
        delay_ranges: Mapping[
            tuple[str, str], tuple[tuple[int, int, int], ...]
        ] | None = None,
        valid_mode_enabled: bool | None = None,
    ) -> None:
        valid_price = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == PRICE_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        valid_lower = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.axis == LOWER_AXIS
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        requested = {
            (str(side).strip().upper(), str(identity).strip())
            for side, identity in identities
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(identity).strip()
        }
        next_price = requested & valid_price
        next_lower = requested & valid_lower
        next_ranges: dict[tuple[str, str], tuple[tuple[int, int], ...]] = {}
        for raw_key, raw_ranges in (index_ranges or {}).items():
            side, identity = raw_key
            key = (str(side).strip().upper(), str(identity).strip())
            if key not in requested:
                continue
            ranges = tuple(
                (max(0, int(start)), min(len(self._candles) - 1, int(end)))
                for start, end in raw_ranges
                if len(self._candles) and int(start) <= int(end)
            )
            if ranges:
                next_ranges[key] = ranges
        if index_ranges is None:
            next_ranges = {
                key: ((0, len(self._candles) - 1),)
                for key in requested
                if self._candles
            }
        next_delay_ranges: dict[
            tuple[str, str], tuple[tuple[int, int, int], ...]
        ] = {}
        for raw_key, raw_ranges in (delay_ranges or {}).items():
            side, identity = raw_key
            key = (str(side).strip().upper(), str(identity).strip())
            if key not in next_ranges:
                continue
            ranges = tuple(
                (max(0, int(start)), min(len(self._candles) - 1, int(end)), int(delay))
                for start, end, delay in raw_ranges
                if (
                    len(self._candles)
                    and not isinstance(delay, bool)
                    and 0 <= int(delay)
                    and int(start) <= int(end)
                )
            )
            if ranges:
                next_delay_ranges[key] = ranges
        if (
            next_price == self._valid_price_filter_identities
            and next_lower == self._valid_lower_filter_identities
            and next_ranges == self._valid_filter_index_ranges
            and next_delay_ranges == self._valid_filter_delay_ranges
            and (
                valid_mode_enabled is None
                or bool(valid_mode_enabled) == self._valid_filter_mode_enabled
            )
        ):
            return
        self._valid_price_filter_identities = next_price
        self._valid_lower_filter_identities = next_lower
        self._valid_filter_index_ranges = next_ranges
        self._valid_filter_delay_ranges = next_delay_ranges
        if valid_mode_enabled is not None:
            self._valid_filter_mode_enabled = bool(valid_mode_enabled)
        self._sync_hidden_price_filter_identities()
        self._refresh_price_legend_visibility(emit=False)
        self.pane_layout_changed.emit()

    def set_ocr_turn_delays(
        self,
        delays: Mapping[tuple[str, str], int],
        *,
        repaint: bool = True,
    ) -> None:
        valid_keys = {
            (str(side).strip().upper(), str(descriptor.identity or "").strip())
            for descriptor in self._visualization_descriptors
            if descriptor.family == FAMILY_OCR_OSC
            and str(
                descriptor.parameters.get("condition", {}).get("operator") or ""
            ).strip().upper() in {"TURN_UP", "TURN_DOWN"}
            for side in descriptor.sides
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        normalized = {
            (str(side).strip().upper(), str(identity).strip()): delay
            for (side, identity), delay in delays.items()
            if not isinstance(delay, bool)
            and isinstance(delay, int)
            and delay >= 0
            and (str(side).strip().upper(), str(identity).strip()) in valid_keys
        }
        if normalized == self._configured_ocr_turn_delays:
            return
        self._configured_ocr_turn_delays = normalized
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        if repaint:
            self.update()

    def reset_price_legend_visibility(self) -> None:
        if (
            not self._hidden_price_filter_identities
            and not self._highlighted_price_filter_identities
            and not self._highlighted_lower_filter_identities
            and not self._hide_unhighlighted_price_filters
        ):
            return
        self._hidden_price_filter_identities.clear()
        self._highlighted_price_filter_identities.clear()
        self._highlighted_lower_filter_identities.clear()
        self._hide_unhighlighted_price_filters = False
        self._refresh_price_legend_visibility()
        self.lower_legend_highlight_changed.emit(
            self.highlighted_lower_filter_identities
        )

    def _price_legend_target_at(
        self,
        x: float,
        y: float,
    ) -> tuple[str, tuple[str, ...]] | None:
        scale = self.price_scale()
        if scale is None:
            return None
        point = QPointF(float(x), float(y))
        for row in self._price_legend_layout(scale, self.font()):
            if row["prefix_rect"].contains(point):
                return (row["side"], ())
            for layout in row.get("blocks", ()):
                rect = layout.get("rect")
                if not isinstance(rect, QRectF) or not rect.contains(point):
                    continue
                block = layout.get("block") or {}
                fragment_targets = tuple(layout.get("fragment_targets") or ())
                for fragment_rect, fragment_identities in fragment_targets:
                    if fragment_rect.contains(point):
                        return (row["side"], tuple(fragment_identities))
                if fragment_targets or block.get("filter_text_spans"):
                    return None
                return (row["side"], tuple(
                    str(identity or "").strip()
                    for identity in block.get("descriptor_identities", ())
                    if str(identity or "").strip()
                ))
        return None

    def _toggle_price_legend_identities(
        self,
        side: str,
        identities: tuple[str, ...],
    ) -> None:
        clean = {
            str(identity or "").strip()
            for identity in identities
            if str(identity or "").strip()
        }
        if not clean:
            return

        normalized_side = str(side or "").strip().upper()
        price_keys: set[tuple[str, str]] = set()
        lower_keys: set[tuple[str, str]] = set()
        matched_identities: set[str] = set()
        for descriptor in self._visualization_descriptors:
            identity = str(descriptor.identity or "").strip()
            if identity not in clean:
                continue
            matched_identities.add(identity)
            descriptor_sides = {
                str(value or "").strip().upper()
                for value in descriptor.sides
                if str(value or "").strip().upper() in {"BUY", "SELL"}
            }
            sides = (
                {normalized_side}
                if normalized_side in descriptor_sides
                else descriptor_sides
                if normalized_side not in {"BUY", "SELL"}
                else set()
            )
            target = (
                lower_keys
                if descriptor.axis == LOWER_AXIS
                else price_keys
                if descriptor.axis == PRICE_AXIS
                else None
            )
            if target is not None:
                target.update((descriptor_side, identity) for descriptor_side in sides)

        if normalized_side in {"BUY", "SELL"}:
            price_keys.update(
                (normalized_side, identity)
                for identity in clean - matched_identities
            )

        if lower_keys:
            if lower_keys.issubset(self._highlighted_lower_filter_identities):
                self._highlighted_lower_filter_identities.difference_update(
                    lower_keys
                )
            else:
                self._highlighted_lower_filter_identities.update(lower_keys)
            self.lower_legend_highlight_changed.emit(
                self.highlighted_lower_filter_identities
            )

        if price_keys:
            highlighted = self._highlighted_price_filter_identities
            highlighted.symmetric_difference_update(price_keys)
        if price_keys or lower_keys:
            self._hide_unhighlighted_price_filters = bool(
                self._highlighted_price_filter_identities
                or self._highlighted_lower_filter_identities
            )
            self._sync_hidden_price_filter_identities()
            self._refresh_price_legend_visibility()

    def _default_lower_pane_heights(self) -> dict[str, float]:
        if not self._lower_families:
            return {}
        height = float(self._LOWER_TOTAL_HEIGHT) / len(self._lower_families)
        return {family: height for family in self._lower_families}

    def _reset_lower_pane_layout_state(self) -> None:
        self._lower_pane_heights = self._default_lower_pane_heights()

    def _lower_total_height(self) -> float:
        return sum(
            float(self._lower_pane_heights.get(family, 0.0))
            for family in self._lower_families
        )

    def _maximum_lower_total_height(self) -> float:
        if not self._lower_families:
            return 0.0
        minimum_total = self._MIN_LOWER_PANE_HEIGHT * len(self._lower_families)
        available = (
            float(self.height())
            - self._BOTTOM
            - self._PRICE_TO_LOWER_GAP
            - self._price_plot_top_margin()
            - self._MIN_PRICE_PLOT_HEIGHT
        )
        return max(minimum_total, available)

    @property
    def lower_pane_heights(self) -> tuple[tuple[str, float], ...]:
        return tuple(
            (family, float(self._lower_pane_heights.get(family, 0.0)))
            for family in self._lower_families
        )

    def set_lower_pane_heights(
        self,
        heights: tuple[tuple[str, float], ...] | list[tuple[str, float]],
    ) -> None:
        if not self._lower_families:
            if self._lower_pane_heights:
                self._lower_pane_heights = {}
                self._static_chart_cache = None
                self._static_chart_cache_key = None
                self.update()
                self.pane_layout_changed.emit()
            return
        supplied = {
            str(family): float(height)
            for family, height in heights
            if _finite_number(height) is not None
        }
        defaults = self._default_lower_pane_heights()
        normalized = {
            family: max(
                self._MIN_LOWER_PANE_HEIGHT,
                supplied.get(family, defaults[family]),
            )
            for family in self._lower_families
        }
        maximum_total = self._maximum_lower_total_height()
        excess = max(0.0, sum(normalized.values()) - maximum_total)
        if excess:
            for family in self._lower_families:
                reducible = max(
                    0.0,
                    normalized[family] - self._MIN_LOWER_PANE_HEIGHT,
                )
                reduction = min(excess, reducible)
                normalized[family] -= reduction
                excess -= reduction
                if excess <= 1e-9:
                    break
        if normalized == self._lower_pane_heights:
            return
        self._lower_pane_heights = normalized
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()
        self.pane_layout_changed.emit()

    def reset_lower_pane_layout(self) -> None:
        self.set_lower_pane_heights(tuple(self._default_lower_pane_heights().items()))

    @property
    def indicator_total_height(self) -> int:
        return round(self._lower_total_height()) if self._lower_families else 0

    @property
    def lower_families(self) -> tuple[str, ...]:
        return tuple(self._lower_families)

    @property
    def validation_range(self) -> tuple[int, int] | None:
        return self._validation_range

    def set_validation_range(
        self,
        start_index: int | None,
        end_index: int | None,
    ) -> None:
        if start_index is None or end_index is None:
            self._cancel_cycle_label_preview()
            self._validation_range = None
            self.update()
            return
        if (
            isinstance(start_index, bool)
            or not isinstance(start_index, int)
            or isinstance(end_index, bool)
            or not isinstance(end_index, int)
        ):
            raise TypeError("validation range indexes must be integers")
        start = min(start_index, end_index)
        end = max(start_index, end_index)
        if not 0 <= start <= end < len(self._candles):
            raise ValueError("validation range is outside candles")
        if self._validation_range != (start, end):
            self._cancel_cycle_label_preview()
        self._validation_range = (start, end)
        self.update()

    def set_completed_cycle_average_lines(
        self,
        cycles: tuple[IndicatorFollowValidationCompletedCycle, ...],
    ) -> None:
        normalized = tuple(
            cycle
            for cycle in cycles
            if isinstance(cycle, IndicatorFollowValidationCompletedCycle)
            and 0 <= cycle.buy_start_index <= cycle.sell_index < len(self._candles)
            and math.isfinite(float(cycle.average_buy_price))
            and float(cycle.average_buy_price) > 0
        )
        if normalized == self._completed_cycle_average_lines:
            return
        self._cancel_cycle_label_preview()
        self._completed_cycle_average_lines = normalized
        if normalized:
            _prepare_cycle_label_numpy()
        if self._selected_completed_cycle_number not in {
            cycle.cycle_number for cycle in normalized
        }:
            self._selected_completed_cycle_number = None
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()

    @property
    def selected_completed_cycle_number(self) -> int | None:
        return self._selected_completed_cycle_number

    def set_selected_completed_cycle_number(self, cycle_number: int | None) -> None:
        if cycle_number is not None:
            if isinstance(cycle_number, bool) or not isinstance(cycle_number, int):
                return
            if cycle_number not in {
                cycle.cycle_number for cycle in self._completed_cycle_average_lines
            }:
                return
        if cycle_number == self._selected_completed_cycle_number:
            return
        self._selected_completed_cycle_number = cycle_number
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()

    def _completed_cycle_render_color(self, cycle_number: int) -> QColor:
        return (
            _SELECTION_LINE
            if cycle_number == self._selected_completed_cycle_number
            else _CYCLE_AVERAGE_LINE
        )

    def completed_cycle_average_line_records(
        self,
        scale: _ValidationPriceScale | None = None,
    ) -> list[dict[str, Any]]:
        if self._validation_range is None or not self._completed_cycle_average_lines:
            return []
        resolved_scale = scale if scale is not None else self.price_scale()
        if resolved_scale is None:
            return []

        plot_left = float(self.plot_left)
        plot_right = float(self.width() - self._RIGHT)
        records: list[dict[str, Any]] = []
        for cycle in self._completed_cycle_average_lines:
            start_x = self._x_for_index(cycle.buy_start_index)
            end_x = self._x_for_index(cycle.sell_index)
            left = max(plot_left, min(start_x, end_x))
            right = min(plot_right, max(start_x, end_x))
            if right < left:
                continue
            price = float(cycle.average_buy_price)
            profit_amount = _completed_cycle_profit_amount(cycle)
            records.append({
                "cycle_number": cycle.cycle_number,
                "buy_start_index": cycle.buy_start_index,
                "sell_index": cycle.sell_index,
                "average_buy_price": price,
                "sell_price": float(cycle.sell_price),
                "estimated_return_percent": float(cycle.estimated_return_percent),
                "profit_amount": profit_amount if profit_amount is not None else 0.0,
                "x1": left,
                "x2": right,
                "y": resolved_scale.y_for_price(price),
            })
        return records

    @_perf_stage('labels.blockers')
    def _completed_cycle_label_blockers(
        self,
        scale: _ValidationPriceScale,
    ) -> list[QRectF]:
        blockers: list[QRectF] = []
        visible_start, visible_end = self._visible_index_bounds()
        half_width = max(2.0, self._candle_body_width() / 2.0)

        for index in range(visible_start, visible_end):
            candle = self._candles[index]
            high = _finite_number(candle.get("high"))
            low = _finite_number(candle.get("low"))
            close = _finite_number(candle.get("close"))
            if high is None or low is None:
                high = close
                low = close
            if high is None or low is None:
                continue
            x = self._x_for_index(index)
            y1 = scale.y_for_price(high)
            y2 = scale.y_for_price(low)
            top = min(y1, y2)
            bottom = max(y1, y2)
            blockers.append(QRectF(
                x - half_width - 4.0,
                top - 5.0,
                half_width * 2.0 + 8.0,
                max(10.0, bottom - top + 10.0),
            ))

        for marker in self._visible_marker_records():
            index = marker.get("evaluation_index")
            side = str(marker.get("side") or "").strip().upper()
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            y = self._marker_y(index, side, scale)
            if y is None:
                continue
            x = self._x_for_index(index)
            blockers.append(QRectF(x - 10.0, y - 12.0, 20.0, 24.0))

        price_legend_layout = self._price_legend_layout(scale, self.font())
        if price_legend_layout:
            legend_rects = [
                rect
                for row in price_legend_layout
                for rect in (
                    row["prefix_rect"],
                    *(layout["rect"] for layout in row.get("blocks", ())),
                )
            ]
            legend_height = max(rect.bottom() for rect in legend_rects) - min(
                rect.top() for rect in legend_rects
            ) + 4.0
            blockers.append(QRectF(
                float(self.plot_left),
                float(scale.plot_top),
                float(max(1, self.width() - self._RIGHT - self.plot_left)),
                float(legend_height),
            ))
        return blockers

    @_perf_stage('labels.signature')
    def _cycle_label_visual_signature(self, scale):
        """Identity of the exact layout's screen-space inputs, not raw indexes."""
        if scale is None or not self._validation_range:
            return None
        start, end = self._visible_index_bounds()
        candles = self._candles

        def stamp(index):
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(candles):
                return None
            return str(candles[index].get("time") or "") or None

        timestamps = tuple(stamp(index) for index in range(len(candles)))
        if None in timestamps or len(set(timestamps)) != len(timestamps):
            return None
        visible = tuple(
            (stamp(index), *(candles[index].get(field) for field in
                              ("open", "high", "low", "close", "volume")))
            for index in range(start, end)
        )
        if not visible or any(row[0] is None for row in visible):
            return None
        if len({row[0] for row in visible}) != len(visible):
            return None
        markers = []
        for marker in self._markers:
            index = marker.get("evaluation_index")
            timestamp = stamp(index)
            if timestamp is None or timestamp != marker.get("evaluation_time"):
                return None
            markers.append((timestamp, marker.get("side"), marker.get("identity"),
                            self._x_for_index(index),
                            self._marker_y(index, marker.get("side"), scale)))
        cycles = []
        for cycle in self._completed_cycle_average_lines:
            buy_stamps = tuple(stamp(index) for index in cycle.buy_indexes)
            if (None in buy_stamps or stamp(cycle.buy_start_index) is None
                    or stamp(cycle.buy_end_index) is None
                    or stamp(cycle.sell_index) is None):
                return None
            cycles.append((cycle.cycle_number, buy_stamps,
                           stamp(cycle.buy_start_index), stamp(cycle.buy_end_index),
                           stamp(cycle.sell_index), cycle.buy_count,
                           cycle.average_buy_price, cycle.sell_price,
                           cycle.estimated_return_percent, cycle.buy_quantity,
                           cycle.buy_cost, cycle.trading_cost_amount))
        if len({cycle[0] for cycle in cycles}) != len(cycles):
            return None
        lines = self.completed_cycle_average_line_records(scale)
        line_identity = tuple(
            (record["cycle_number"], stamp(record["buy_start_index"]),
             stamp(record["sell_index"]), *(record[key] for key in (
                 "average_buy_price", "sell_price", "estimated_return_percent",
                 "profit_amount", "x1", "x2", "y")))
            for record in lines
        )
        blockers = tuple(
            (rect.left(), rect.top(), rect.right(), rect.bottom())
            for rect in self._completed_cycle_label_blockers(scale)
        )
        range_identity = tuple(stamp(index) for index in self._validation_range)
        if None in range_identity:
            return None
        legend = tuple(
            (side, title, tuple(
                (block.get("caption"), tuple(block.get("descriptor_identities") or ()),
                 tuple(block.get("channels") or ()))
                for block in blocks
            )) for side, title, blocks in self._price_legend_rows()
        )
        return (
            visible, tuple(markers), tuple(cycles), line_identity, blockers,
            range_identity, self._validation_range[1] - self._validation_range[0],
            self._visible_candle_span, self._x_for_index(start),
            scale.minimum, scale.maximum, scale.plot_top, scale.plot_bottom,
            self.pixels_per_candle, self._candle_body_width(),
            self.width(), self.height(), self.plot_left, self._RIGHT,
            self.font().toString(), self.logicalDpiX(), self.logicalDpiY(),
            self.devicePixelRatioF(), tuple(self._lower_families),
            tuple(sorted(self._lower_pane_heights.items())),
            tuple(sorted(self._hidden_price_filter_identities)),
            tuple(sorted(self._highlighted_price_filter_identities)),
            self._hide_unhighlighted_price_filters,
            self._selected_completed_cycle_number, legend,
        )

    def capture_cycle_label_handoff(self):
        if (self._cycle_label_preview_active or not self._cycle_label_last_records
                or self._cycle_label_last_signature is None
                or self._cycle_label_last_records != self._completed_cycle_label_cache_records):
            return None
        signature = self._cycle_label_visual_signature(self.price_scale())
        if signature is None or signature != self._cycle_label_last_signature:
            return None
        return (signature, tuple(deepcopy(self._cycle_label_last_records)))

    def install_cycle_label_handoff(self, handoff) -> bool:
        self._cycle_label_handoff = None
        if (handoff is None or self._cycle_label_preview_active
                or not isinstance(handoff, tuple) or len(handoff) != 2):
            return False
        signature, records = handoff
        if not records or signature != self._cycle_label_visual_signature(self.price_scale()):
            return False
        self._cycle_label_handoff = (signature, tuple(deepcopy(records)))
        return True

    @staticmethod
    def _rect_overlap_area(rect: QRectF, blocker: QRectF) -> float:
        intersection = rect.intersected(blocker)
        if intersection.isEmpty():
            return 0.0
        return max(0.0, intersection.width()) * max(
            0.0,
            intersection.height(),
        )

    @_perf_stage('labels.layout')
    def completed_cycle_average_label_records(
        self,
        scale: _ValidationPriceScale | None = None,
    ) -> list[dict[str, Any]]:
        phase = _perf_phase(self) if _PERF_ENABLED else None
        resolved_scale = scale if scale is not None else self.price_scale()
        line_records = (
            self.completed_cycle_average_line_records(resolved_scale)
            if resolved_scale is not None
            else []
        )
        visible_start, visible_end = self._visible_index_bounds()
        if phase is not None:
            phase.update(
                visible_bars=max(0, int(visible_end - visible_start)),
                markers=int(len(self._markers)),
                cycles=int(len(line_records)),
            )
        # Only content that affects label geometry; exclude large tooltip strings.
        content_key = (
            tuple((candle.get("high"), candle.get("low"), candle.get("close"))
                  for candle in self._candles[visible_start:visible_end]),
            tuple((type(marker.get("evaluation_index")), marker.get("evaluation_index"), marker.get("side"))
                  for marker in self._markers),
            tuple(tuple(record.items()) for record in line_records),
        )
        font = self.font()
        cache_key = (
            resolved_scale,
            content_key,
            self._completed_cycle_average_lines,
            self._validation_range,
            float(self._visible_start_index),
            float(self._visible_candle_span),
            self.width(),
            self.height(),
            self.plot_left,
            font.toString(),
            float(self.devicePixelRatioF()),
            self.logicalDpiX(), self.logicalDpiY(),
            self._candle_body_width(),
            len(self._price_legend_rows()),
            tuple(sorted(self._lower_pane_heights.items())),
        )
        if phase is not None:
            phase.checkpoint(
                'labels.phase.cache_key', candidate_count=0, blocker_count=0,
            )
        if cache_key == self._completed_cycle_label_cache_key:
            if phase is not None:
                phase.checkpoint(
                    'labels.phase.cache_hit', cache_hit=True,
                    candidate_count=0, blocker_count=0,
                )
            self._cycle_label_handoff = None
            return [deepcopy(record) for record in self._completed_cycle_label_cache_records]
        if phase is not None:
            phase.checkpoint(
                'labels.phase.cache_hit', cache_hit=False,
                candidate_count=0, blocker_count=0,
            )

        if phase is not None:
            candidate_count = 0
            blocker_count = 0

        def cache(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            started = phase.begin() if phase is not None else None
            self._completed_cycle_label_cache_key = cache_key
            self._completed_cycle_label_cache_records = tuple(deepcopy(records))
            if phase is not None:
                phase.end('labels.phase.final_cache', started)
                phase.flush(
                    'labels.phase.geometry', candidate_count=candidate_count,
                    blocker_count=blocker_count,
                )
                phase.flush(
                    'labels.phase.scoring', candidate_count=candidate_count,
                    blocker_count=blocker_count,
                )
                phase.flush(
                    'labels.phase.final_cache', candidate_count=candidate_count,
                    blocker_count=blocker_count,
                )
            return records

        handoff = self._cycle_label_handoff
        self._cycle_label_handoff = None
        if handoff is not None and handoff[0] == self._cycle_label_visual_signature(resolved_scale):
            by_number = {record["cycle_number"]: record for record in handoff[1]}
            if len(by_number) == len(handoff[1]) == len(line_records) and all(
                    record["cycle_number"] in by_number for record in line_records):
                reused = cache([
                    {**deepcopy(by_number[line["cycle_number"]]), **line}
                    for line in line_records
                ])
                self._cycle_label_last_signature = handoff[0]
                self._cycle_label_last_cache_key = cache_key
                return reused

        if resolved_scale is None or not line_records:
            return cache([])
        # No NumPy import in paint; pending or failed prepare uses the spatial fallback.
        # Both implementations preserve the existing label placement contract.
        np = _cycle_label_numpy_module
        if phase is not None:
            phase.checkpoint(
                'labels.phase.numpy_import', candidate_count=0, blocker_count=0,
                numpy_ready=np is not None,
            )

        metrics = QFontMetrics(self.font())
        line_height = max(14, metrics.height() + 1)
        block_height = float(line_height * 3)
        plot_left = float(self.plot_left) + 4.0
        plot_right = float(self.width() - self._RIGHT) - 4.0
        plot_top = float(resolved_scale.plot_top) + 4.0
        plot_bottom = float(resolved_scale.plot_bottom) - 4.0
        blockers = self._completed_cycle_label_blockers(resolved_scale)
        blocker_bounds = [
            (
                float(blocker.left()),
                float(blocker.top()),
                float(blocker.right()),
                float(blocker.bottom()),
            )
            for blocker in blockers
        ]
        labels: list[dict[str, Any]] = []
        # Conservative screen-space buckets; each blocker retains its original ID.
        # Clamp cell enumeration to the reachable plot, even for off-screen wicks.
        cell_size = 64.0
        grid_limits = (
            math.floor((plot_left - 8.0) / cell_size),
            math.floor((plot_right + 8.0) / cell_size),
            math.floor((plot_top - 8.0) / cell_size),
            math.floor((plot_bottom + 8.0) / cell_size),
        )
        grid: dict[tuple[int, int], list[int]] = {}

        def cells(bounds):
            left, top, right, bottom = bounds
            first_x = max(grid_limits[0], math.floor(left / cell_size))
            last_x = min(grid_limits[1], math.floor(right / cell_size))
            first_y = max(grid_limits[2], math.floor(top / cell_size))
            last_y = min(grid_limits[3], math.floor(bottom / cell_size))
            for column in range(first_x, last_x + 1):
                for row in range(first_y, last_y + 1):
                    yield column, row

        def index_blocker(index):
            for cell in cells(blocker_bounds[index]):
                grid.setdefault(cell, []).append(index)

        if np is None:
            for index in range(len(blocker_bounds)):
                index_blocker(index)
        else:
            # Convert fixed obstacles once, reserve one additional row per label.
            numeric_bounds = np.empty((len(blocker_bounds) + len(line_records), 4),
                                      dtype=np.float64)
            if blocker_bounds:
                numeric_bounds[:len(blocker_bounds)] = blocker_bounds
        if phase is not None:
            blocker_count = len(blocker_bounds)
            phase.checkpoint(
                'labels.phase.blockers', candidate_count=0,
                blocker_count=int(blocker_count),
            )

        def nearby_blockers(bounds):
            indexes: set[int] = set()
            for cell in cells(bounds):
                indexes.update(grid.get(cell, ()))
            # Preserve the legacy summation order and exact score/tie behavior.
            return sorted(indexes)


        for record in sorted(
            line_records,
            key=lambda item: (float(item["x2"]), int(item["cycle_number"])),
        ):
            texts = (
                f"평단 {_validation_price_text(record['average_buy_price'])}",
                f"매도 {_validation_price_text(record['sell_price'])}",
                (
                    f"손익 {_format_summary_return_percent(record['estimated_return_percent'])}/"
                    f"{_format_summary_amount(record['profit_amount'], signed=True)}"
                ),
            )
            label_width = float(
                max(metrics.horizontalAdvance(text) for text in texts) + 4
            )
            max_left = plot_right - label_width
            max_top = plot_bottom - block_height
            if max_left < plot_left or max_top < plot_top:
                continue

            anchor_x = float(record["x2"])
            anchor_y = float(record["y"])
            preferred_left = anchor_x + 16.0

            geometry_started = phase.begin() if phase is not None else None
            x_candidates: list[float] = []
            if preferred_left <= max_left:
                x_candidates.append(preferred_left)
                grid_start = max(preferred_left, plot_left)
            else:
                grid_start = plot_left
            x_candidates.extend((max_left, plot_left))
            x_step = max(36.0, min(90.0, label_width * 0.45))
            cursor_x = grid_start
            while cursor_x <= max_left + 0.5:
                x_candidates.append(cursor_x)
                cursor_x += x_step

            if preferred_left <= max_left:
                x_candidates = [
                    value for value in x_candidates
                    if value >= preferred_left - 0.5
                ]
            if not x_candidates:
                x_candidates = [plot_left, max_left]

            y_candidates: list[float] = [
                min(max(plot_top, anchor_y - block_height / 2.0), max_top),
                plot_top,
                max_top,
            ]
            y_step = max(12.0, float(line_height))
            cursor_y = plot_top
            while cursor_y <= max_top + 0.5:
                y_candidates.append(cursor_y)
                cursor_y += y_step

            x_candidates = list(dict.fromkeys(round(v, 3) for v in x_candidates))
            y_candidates = list(dict.fromkeys(round(v, 3) for v in y_candidates))
            # Keep the legacy candidates and scores, but never search dates away
            # from SELL: the horizontal gap is at most one text-block width.
            x_candidates = [left for left in x_candidates
                            if max(left - anchor_x, anchor_x - left - label_width, 0.0)
                            <= label_width]

            # QRectF x/y components are separable. Reuse the exact Qt-derived
            # centers and expanded edges instead of allocating for every grid pair.
            x_geometry = []
            for label_left in x_candidates:
                rect = QRectF(float(label_left), 0.0, label_width, block_height)
                expanded = rect.adjusted(-4.0, -4.0, 4.0, 4.0)
                x_geometry.append((float(label_left), float(rect.center().x()),
                    float(expanded.left()), float(expanded.right()),
                    0.0 if rect.left() >= anchor_x + 8.0 else 120.0))
            y_geometry = []
            for label_top in y_candidates:
                rect = QRectF(0.0, float(label_top), label_width, block_height)
                expanded = rect.adjusted(-4.0, -4.0, 4.0, 4.0)
                y_geometry.append((float(label_top), float(rect.center().y()),
                                   float(expanded.top()), float(expanded.bottom())))
            candidates = []
            ordinal = 0
            for label_left, center_x, left, right, penalty in x_geometry:
                for label_top, center_y, top, bottom in y_geometry:
                    distance = math.hypot(center_x - anchor_x, center_y - anchor_y)
                    candidates.append((distance + penalty, ordinal,
                        (label_left, label_top), (left, top, right, bottom)))
                    ordinal += 1
            if phase is not None:
                candidate_count += len(candidates)
                phase.end('labels.phase.geometry', geometry_started)

            scoring_started = phase.begin() if phase is not None else None
            numeric_scores: dict[int, tuple[float, float]] = {}
            if np is not None:
                bounds_array = numeric_bounds[:len(blocker_bounds)]
                by_x: dict[float, list[tuple]] = {}
                for candidate in candidates:
                    by_x.setdefault(candidate[3][0], []).append(candidate)

                def numeric_score(candidate):
                    ordinal = candidate[1]
                    if ordinal in numeric_scores:
                        return numeric_scores[ordinal]
                    group = by_x[candidate[3][0]]
                    expanded_left, _, expanded_right, _ = candidate[3]
                    widths = np.maximum(0.0,
                        np.minimum(expanded_right, bounds_array[:, 2])
                        - np.maximum(expanded_left, bounds_array[:, 0]))
                    retained = widths > 0.0
                    widths = widths[retained]
                    if widths.size == 0:
                        for item in group:
                            numeric_scores[item[1]] = (0.0, 0.0)
                    else:
                        tops = bounds_array[retained, 1]
                        bottoms = bounds_array[retained, 3]
                        # Bound temporary arrays; process every candidate without a cap.
                        batch_size = max(1, min(32, 131072 // int(widths.size)))
                        for start in range(0, len(group), batch_size):
                            batch = group[start:start + batch_size]
                            upper = np.asarray([item[3][1] for item in batch])[:, None]
                            lower = np.asarray([item[3][3] for item in batch])[:, None]
                            areas = np.maximum(0.0,
                                np.minimum(lower, bottoms)
                                - np.maximum(upper, tops)) * widths
                            counts = np.count_nonzero(areas > 0.0, axis=1)
                            # Accumulate left-to-right, not pairwise sum: retain the
                            # legacy positive-overlap order and float rounding.
                            sums = np.cumsum(areas, axis=1)[:, -1]
                            for item, count, area in zip(batch, counts, sums):
                                numeric_scores[item[1]] = (float(count), float(area))
                    return numeric_scores[ordinal]

            best: tuple[tuple[float, ...], int, tuple[float, float]] | None = None
            for distance_score, candidate_ordinal, rect, expanded_bounds in sorted(
                candidates,
                key=lambda candidate: (candidate[0], candidate[1]),
            ):
                expanded_left, expanded_top, expanded_right, expanded_bottom = (
                    expanded_bounds
                )
                if np is not None:
                    collision_count, overlap_area = numeric_score((
                        distance_score, candidate_ordinal, rect, expanded_bounds))
                else:
                    collision_count = 0.0
                    overlap_area = 0.0
                    dominated = False
                    for blocker_index in nearby_blockers(expanded_bounds):
                        blocker_left, blocker_top, blocker_right, blocker_bottom = (
                            blocker_bounds[blocker_index]
                        )
                        overlap_width = min(expanded_right, blocker_right) - max(
                            expanded_left,
                            blocker_left,
                        )
                        overlap_height = min(expanded_bottom, blocker_bottom) - max(
                            expanded_top,
                            blocker_top,
                        )
                        area = max(0.0, overlap_width) * max(0.0, overlap_height)
                        if area > 0.0:
                            collision_count += 1.0
                            overlap_area += area
                            # Remaining overlap contributions cannot decrease the score.
                            if best is not None and (
                                overlap_area > best[0][1]
                                or (overlap_area == best[0][1]
                                    and collision_count > best[0][2])
                            ):
                                dominated = True
                                break
                    if dominated:
                        continue
                score = (
                    1.0 if collision_count else 0.0,
                    overlap_area,
                    collision_count,
                    distance_score,
                )
                if not collision_count:
                    best = (score, candidate_ordinal, rect)
                    break
                if best is None or (score, candidate_ordinal) < (best[0], best[1]):
                    best = (score, candidate_ordinal, rect)
            if phase is not None:
                phase.end('labels.phase.scoring', scoring_started)

            if best is None:
                continue
            label_rect = QRectF(*best[2], label_width, block_height)
            label_blocker = label_rect.adjusted(-6.0, -6.0, 6.0, 6.0)
            blockers.append(label_blocker)
            blocker_bounds.append((
                float(label_blocker.left()),
                float(label_blocker.top()),
                float(label_blocker.right()),
                float(label_blocker.bottom()),
            ))
            if np is None:
                index_blocker(len(blocker_bounds) - 1)
            else:
                numeric_bounds[len(blocker_bounds) - 1] = blocker_bounds[-1]
            if phase is not None:
                blocker_count = len(blocker_bounds)

            label_left = float(label_rect.left())
            label_top = float(label_rect.top())
            if label_left >= anchor_x:
                leader_end_x = label_left - 4.0
            else:
                leader_end_x = float(label_rect.right()) + 4.0
            leader_end_y = label_top + line_height / 2.0
            placement = "TOP" if label_rect.center().y() < anchor_y else "BOTTOM"

            labels.append({
                **record,
                "texts": texts,
                "label_left": label_left,
                "label_top": label_top,
                "label_width": label_width,
                "label_height": block_height,
                "line_height": float(line_height),
                "placement": placement,
                "collision_count": int(best[0][2]),
                "overlap_area": float(best[0][1]),
                "leader_x1": anchor_x,
                "leader_y1": anchor_y,
                "leader_x2": leader_end_x,
                "leader_y2": leader_end_y,
            })
        return cache(labels)

    def _cycle_label_preview_context(self):
        return (self.width(), self.height(), self.font().toString(),
                self.devicePixelRatioF(), self._validation_range,
                self._completed_cycle_average_lines, id(self._markers),
                id(self._candles), tuple(sorted(self._lower_pane_heights.items())),
                tuple(sorted(self._hidden_price_filter_identities)))

    def _cycle_label_view_affine(self, scale):
        pixels = self.pixels_per_candle
        price_pixels = (scale.plot_bottom - scale.plot_top) / (scale.maximum - scale.minimum)
        return (self._LEFT + (0.5 - self._visible_start_index) * pixels, pixels,
                scale.plot_top + scale.maximum * price_pixels, -price_pixels)

    def _maybe_begin_cycle_label_preview(self):
        owner = self._input_frame_owner
        if (not self._validation_range or not self._completed_cycle_average_lines
                or owner is None or not owner.is_chart_view_update_busy()):
            return
        if not self._cycle_label_preview_active:
            self._cycle_label_preview_active = True
            self._cycle_label_preview_records = self._cycle_label_last_records
            self._cycle_label_preview_affine = self._cycle_label_last_affine
            self._cycle_label_preview_context_key = self._cycle_label_last_context
        self._cycle_label_settle_timer.start(180)

    def _cancel_cycle_label_preview(self):
        self._cycle_label_settle_timer.stop()
        was_active = self._cycle_label_preview_active
        self._cycle_label_preview_active = False
        self._cycle_label_preview_records = ()
        self._cycle_label_preview_affine = None
        self._cycle_label_preview_context_key = None
        self._cycle_label_last_records = ()
        self._cycle_label_last_affine = None
        self._cycle_label_last_context = None
        self._cycle_label_last_signature = None
        self._cycle_label_last_cache_key = None
        self._cycle_label_handoff = None
        if was_active:
            self._static_chart_cache = None
            self._static_chart_cache_key = None
            self.update()

    def _cycle_label_pointer_ended(self, cancelled):
        if cancelled:
            self._cancel_cycle_label_preview()
        elif self._cycle_label_preview_active:
            self._cycle_label_settle_timer.start(0)

    @_perf_stage('labels.settle')
    def _settle_cycle_label_preview(self):
        if not self._cycle_label_preview_active:
            return
        owner = self._input_frame_owner
        if self.is_pointer_interaction_active() or (
                owner is not None and owner.is_chart_view_update_busy()):
            self._cycle_label_settle_timer.start(20)
            return
        self._cycle_label_preview_active = False
        self._cycle_label_preview_records = ()
        self._cycle_label_preview_affine = None
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()

    @_perf_stage('labels.records')
    def _cycle_label_records_for_paint(self, scale):
        context = self._cycle_label_preview_context()
        if self._cycle_label_preview_active:
            saved_context = self._cycle_label_preview_context_key
            # Canvas dimensions are already handled by the view affine. A
            # navigation scrollbar resize can reuse this provisional layout;
            # font, DPI, content and pane changes must still fail closed.
            if saved_context is not None and saved_context[2:] != context[2:]:
                self._cancel_cycle_label_preview()
            else:
                return self._transformed_cycle_label_records(scale)
        records = self.completed_cycle_average_label_records(scale)
        self._cycle_label_last_records = tuple(deepcopy(records))
        self._cycle_label_last_affine = self._cycle_label_view_affine(scale)
        self._cycle_label_last_context = context
        if self._cycle_label_last_cache_key != self._completed_cycle_label_cache_key:
            self._cycle_label_last_signature = self._cycle_label_visual_signature(scale)
            self._cycle_label_last_cache_key = self._completed_cycle_label_cache_key
        return records

    def _transformed_cycle_label_records(self, scale):
        old = self._cycle_label_preview_affine
        current = self._cycle_label_view_affine(scale)
        previous = {r['cycle_number']: r for r in self._cycle_label_preview_records}
        results = []
        metrics = QFontMetrics(self.font())
        for line in self.completed_cycle_average_line_records(scale):
            saved = previous.get(line['cycle_number'])
            if saved is not None and old is not None:
                item = dict(saved)
                left = current[0] + (saved['label_left'] - old[0]) / old[1] * current[1]
                top = current[2] + (saved['label_top'] - old[2]) / old[3] * current[3]
            else:
                # Newly visible cycles get a cheap provisional anchor, not a search.
                texts = (f"평단 {_validation_price_text(line['average_buy_price'])}",
                         f"매도 {_validation_price_text(line['sell_price'])}",
                         f"손익 {_format_summary_return_percent(line['estimated_return_percent'])}/"
                         f"{_format_summary_amount(line['profit_amount'], signed=True)}")
                line_height = float(max(14, metrics.height() + 1))
                width = float(max(metrics.horizontalAdvance(t) for t in texts) + 4)
                item = {'texts': texts, 'label_width': width,
                        'label_height': line_height * 3, 'line_height': line_height,
                        'collision_count': 0, 'overlap_area': 0.0}
                left = max(self.plot_left + 4., min(line['x2'] + 16.,
                           self.width() - self._RIGHT - 4. - width))
                top = max(scale.plot_top + 4., min(line['y'] - line_height * 1.5,
                          scale.plot_bottom - 4. - line_height * 3))
            width, height = item['label_width'], item['label_height']
            min_left, min_top = self.plot_left + 4., scale.plot_top + 4.
            max_left = self.width() - self._RIGHT - 4. - width
            max_top = scale.plot_bottom - 4. - height
            if max_left < min_left or max_top < min_top:
                continue
            gap_x = max(left - line['x2'], line['x2'] - left - width, 0.)
            if (not min_left <= left <= max_left or not min_top <= top <= max_top
                    or gap_x > width):
                # Repair only invalid previews in O(1); defer collision search to settle.
                left = max(min_left, min(line['x2'] + 16., max_left))
                top = max(min_top, min(line['y'] - height / 2., max_top))
            item.update(line)
            item.update(label_left=left, label_top=top,
                        leader_x1=line['x2'], leader_y1=line['y'],
                        leader_x2=left-4. if left >= line['x2'] else left+item['label_width']+4.,
                        leader_y2=top+item['line_height']/2.,
                        placement='TOP' if top+item['label_height']/2. < line['y'] else 'BOTTOM')
            results.append(item)
        return results

    @_perf_stage('labels.draw')
    def _draw_completed_cycle_average_labels(
        self,
        painter: QPainter,
        scale: _ValidationPriceScale,
    ) -> None:
        records = self._cycle_label_records_for_paint(scale)
        if not records:
            return
        painter.save()
        painter.setClipRect(self._price_plot_rect(scale))
        for record in records:
            color = self._completed_cycle_render_color(record["cycle_number"])
            painter.setPen(QPen(color, 1.5, Qt.SolidLine))
            painter.drawLine(
                QPointF(
                    float(record["leader_x1"]),
                    float(record["leader_y1"]),
                ),
                QPointF(
                    float(record["leader_x2"]),
                    float(record["leader_y2"]),
                ),
            )
            painter.setPen(color)
            for line_index, text in enumerate(record["texts"]):
                painter.drawText(
                    QRectF(
                        float(record["label_left"]),
                        float(record["label_top"])
                        + line_index * float(record["line_height"]),
                        float(record["label_width"]),
                        float(record["line_height"]),
                    ),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    str(text),
                )
        painter.restore()

    def _price_plot_bottom_margin(self) -> int:
        if not self._lower_families:
            return self._BOTTOM
        return round(
            self._BOTTOM
            + self._lower_total_height()
            + self._PRICE_TO_LOWER_GAP
        )

    def lower_pane_records(self) -> list[dict[str, Any]]:
        if not self._lower_families:
            return []
        area_top = float(self.height() - self._BOTTOM) - self._lower_total_height()
        cursor = area_top
        records = []
        for index, family in enumerate(self._lower_families):
            top = round(cursor)
            cursor += float(self._lower_pane_heights.get(family, 0.0))
            bottom = (
                self.height() - self._BOTTOM
                if index == len(self._lower_families) - 1
                else round(cursor)
            )
            records.append({
                "family": family,
                "top": top,
                "bottom": bottom,
                "height": bottom - top,
                "descriptor_ids": tuple(
                    descriptor.identity
                    for descriptor in self._visualization_descriptors
                    if descriptor.axis == LOWER_AXIS
                    and self._display_lower_family(descriptor.family) == family
                ),
            })
        return records

    def lower_boundary_records(self) -> list[dict[str, Any]]:
        panes = self.lower_pane_records()
        return [
            {
                "index": index,
                "y": float(pane["top"]),
                "upper": "PRICE" if index == 0 else panes[index - 1]["family"],
                "lower": pane["family"],
            }
            for index, pane in enumerate(panes)
        ]

    def _lower_boundary_at(self, y: float) -> int | None:
        pointer_y = float(y)
        for record in self.lower_boundary_records():
            if abs(pointer_y - float(record["y"])) <= self._LOWER_BOUNDARY_HIT_TOLERANCE:
                return int(record["index"])
        return None

    def _resize_lower_boundary_to(self, boundary_index: int, y: float) -> bool:
        if not 0 <= boundary_index < len(self._lower_families):
            return False
        heights = [
            float(self._lower_pane_heights[family])
            for family in self._lower_families
        ]
        fixed_bottom = float(self.height() - self._BOTTOM)
        area_top = fixed_bottom - sum(heights)
        requested_y = float(y)

        if boundary_index == 0:
            first_bottom = area_top + heights[0]
            minimum_boundary_y = (
                self._price_plot_top_margin()
                + self._MIN_PRICE_PLOT_HEIGHT
                + self._PRICE_TO_LOWER_GAP
            )
            maximum_boundary_y = first_bottom - self._MIN_LOWER_PANE_HEIGHT
            if maximum_boundary_y < minimum_boundary_y:
                return False
            boundary_y = min(
                maximum_boundary_y,
                max(float(minimum_boundary_y), requested_y),
            )
            heights[0] = first_bottom - boundary_y
        else:
            previous_top = area_top + sum(heights[: boundary_index - 1])
            pair_bottom = area_top + sum(heights[: boundary_index + 1])
            minimum_boundary_y = previous_top + self._MIN_LOWER_PANE_HEIGHT
            maximum_boundary_y = pair_bottom - self._MIN_LOWER_PANE_HEIGHT
            boundary_y = min(
                maximum_boundary_y,
                max(minimum_boundary_y, requested_y),
            )
            heights[boundary_index - 1] = boundary_y - previous_top
            heights[boundary_index] = pair_bottom - boundary_y

        updated = {
            family: height
            for family, height in zip(self._lower_families, heights)
        }
        if updated == self._lower_pane_heights:
            return False
        self._lower_pane_heights = updated
        self._static_chart_cache = None
        self._static_chart_cache_key = None
        self.update()
        self.pane_layout_changed.emit()
        return True

    def _cached_values(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
    ) -> tuple[float | None, ...]:
        return self._series_values_by_key.get(
            (
                str(descriptor.identity or "").strip(),
                str(channel or "").strip().upper(),
            ),
            (),
        )

    def _build_series_groups(
        self,
        axis: str,
        *,
        family: str | None = None,
        include_hidden_price: bool = False,
    ) -> tuple[
        tuple[
            str,
            tuple[float | None, ...],
            tuple[ValidationFilterDescriptor, ...],
        ],
        ...,
    ]:
        grouped: dict[
            tuple[str, tuple[float | None, ...]],
            list[ValidationFilterDescriptor],
        ] = {}
        for descriptor in self._visualization_descriptors:
            if descriptor.axis != axis:
                continue
            if (
                axis == PRICE_AXIS
                and not include_hidden_price
                and str(descriptor.identity or "").strip()
                in self._hidden_price_filter_identities
            ):
                continue
            if family is not None:
                descriptor_family = (
                    self._display_lower_family(descriptor.family)
                    if axis == LOWER_AXIS
                    else descriptor.family
                )
                if descriptor_family != family:
                    continue
            for channel in descriptor.series_keys:
                values = self._cached_values(descriptor, channel)
                if len(values) != len(self._candles):
                    continue
                grouped.setdefault((channel, values), []).append(descriptor)
        return tuple(
            (channel, values, tuple(owners))
            for (channel, values), owners in grouped.items()
        )

    def _visible_lower_series_owners(
        self,
        channel: str,
        owners: tuple[ValidationFilterDescriptor, ...],
    ) -> tuple[ValidationFilterDescriptor, ...]:
        if not self._lower_visibility_filter_active():
            return owners
        return tuple(
            descriptor for descriptor in owners
            if self._lower_channel_role(descriptor, channel) is not None
        )

    def _lower_histogram_evidence_owners(
        self,
        family: str,
        owners: tuple[ValidationFilterDescriptor, ...],
    ) -> tuple[ValidationFilterDescriptor, ...]:
        if family != FAMILY_OCR_OSC:
            return owners
        return tuple(dict.fromkeys((
            *owners,
            *(
                descriptor for descriptor in self._visualization_descriptors
                if descriptor.axis == LOWER_AXIS
                and self._display_lower_family(descriptor.family) == family
            ),
        )))

    def _compute_price_data_bounds(
        self,
    ) -> tuple[float | None, float | None]:
        visible_start, visible_end = self._visible_index_bounds()
        values = [
            number
            for candle in self._candles[visible_start:visible_end]
            for field in ("open", "high", "low", "close")
            if (number := _finite_number(candle.get(field))) is not None
        ]
        for _channel, series_values, _owners in self._price_series_groups:
            values.extend(
                value
                for value in series_values[visible_start:visible_end]
                if value is not None
            )
        if not values:
            return None, None
        return min(values), max(values)

    def _price_data_bounds(self) -> tuple[float | None, float | None]:
        return self._price_data_bounds_cache

    def _compute_lower_data_bounds(
        self,
        family: str,
    ) -> tuple[float, float] | None:
        if family == FAMILY_RSI:
            return 0.0, 100.0
        visible_start, visible_end = self._visible_index_bounds()
        values = [
            value
            for _channel, series_values, _owners
            in self._lower_series_groups.get(family, ())
            for value in series_values[visible_start:visible_end]
            if value is not None
        ]
        if not values:
            return None
        minimum = min(min(values), 0.0)
        maximum = max(max(values), 0.0)
        if maximum == minimum:
            padding = max(abs(maximum) * 0.1, 1.0)
            return minimum - padding, maximum + padding
        padding = (maximum - minimum) * 0.08
        return minimum - padding, maximum + padding

    def _lower_scale(
        self,
        family: str,
        top: int,
        bottom: int,
    ) -> _ValidationPriceScale | None:
        bounds = self._lower_data_bounds_cache.get(family)
        if bounds is None:
            return None
        minimum, maximum = bounds
        plot_top = min(bottom - 2, top + 18)
        plot_bottom = max(plot_top + 1, bottom - 8)
        return _ValidationPriceScale(
            minimum=minimum,
            maximum=maximum,
            plot_top=plot_top,
            plot_bottom=plot_bottom,
        )

    @staticmethod
    def _descriptor_caption(
        descriptor: ValidationFilterDescriptor,
    ) -> str:
        parameters = descriptor.parameters
        criterion_label = str(
            parameters.get("criterion_label") or ""
        ).strip()
        if criterion_label:
            return criterion_label
        if descriptor.family == FAMILY_RSI:
            return f"RSI({parameters.get('period', 14)})"
        if descriptor.family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
            return (
                f"{descriptor.label}("
                f"{parameters.get('fast', 12)},"
                f"{parameters.get('slow', 26)},"
                f"{parameters.get('signal', 9)})"
            )
        periods = parameters.get("periods")
        if isinstance(periods, (list, tuple)) and periods:
            return f"{descriptor.label}({','.join(str(v) for v in periods)})"
        if "period" in parameters:
            return f"{descriptor.label}({parameters['period']})"
        return descriptor.label

    def _styled_descriptor_caption(
        self,
        descriptor: ValidationFilterDescriptor,
    ) -> str:
        return self._descriptor_caption(descriptor)

    def _descriptor_legend_line_pens(
        self,
        descriptor: ValidationFilterDescriptor,
        side: str | None = None,
    ) -> tuple[tuple[str, QPen], ...]:
        groups = (
            self._price_legend_series_groups
            if descriptor.axis == PRICE_AXIS
            else self._lower_series_groups.get(
                self._display_lower_family(descriptor.family),
                (),
            )
        )
        result: list[tuple[str, QPen]] = []
        for channel, _values, owners in groups:
            if descriptor not in owners:
                continue
            if descriptor.family == FAMILY_OCR_OSC and channel == "OSC":
                continue
            pen = self._combined_series_pen(list(owners), channel)
            if (
                descriptor.axis == PRICE_AXIS
                and str(descriptor.identity or "").strip()
                in self._hidden_price_filter_identities
            ):
                muted = QPen(pen)
                color = muted.color()
                color.setAlpha(180)
                muted.setColor(color)
                muted.setWidth(1)
                pen = muted
            elif (
                descriptor.axis == PRICE_AXIS
                and self._descriptor_highlighted(descriptor, side)
            ):
                highlighted = QPen(pen)
                color = highlighted.color()
                color.setAlpha(255)
                highlighted.setColor(color)
                highlighted.setWidth(max(2, highlighted.width()))
                pen = highlighted
            elif (
                descriptor.axis == PRICE_AXIS
                and self._descriptor_valid(descriptor, side)
            ):
                emphasized = QPen(pen)
                color = emphasized.color()
                color.setAlpha(255)
                emphasized.setColor(color)
                emphasized.setWidth(max(2, emphasized.width()))
                pen = emphasized
            result.append((channel, pen))
        return tuple(result)

    def legend_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for descriptor in self._visualization_descriptors:
            swatches = []
            for channel, pen in self._descriptor_legend_line_pens(descriptor):
                color = pen.color()
                swatches.append({
                    "channel": channel,
                    "color": color.name(),
                    "alpha": color.alpha(),
                    "pen_width": pen.width(),
                    "pen_style": int(pen.style()),
                })
            records.append({
                "identity": descriptor.identity,
                "family": descriptor.family,
                "axis": descriptor.axis,
                "caption": self._styled_descriptor_caption(descriptor),
                "swatches": tuple(swatches),
            })
        return records

    @property
    def hovered_marker_key(self) -> tuple[int, str] | None:
        return self._hover_marker_key

    @property
    def pinned_marker_key(self) -> tuple[int, str] | None:
        return self._pinned_marker_key

    @property
    def effective_marker_key(self) -> tuple[int, str] | None:
        return self._pinned_marker_key or self._hover_marker_key

    @property
    def crosshair_index(self) -> int | None:
        marker_key = self.effective_marker_key
        if marker_key is not None:
            return marker_key[0]
        return self._crosshair_hover_index

    def _set_crosshair_pointer(
        self,
        index: int | None,
        y: float | None,
    ) -> None:
        if index is not None and (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            index = None
        resolved_y = _finite_number(y) if y is not None else None
        if (
            index == self._crosshair_hover_index
            and resolved_y == self._crosshair_pointer_y
        ):
            return
        self._crosshair_hover_index = index
        self._crosshair_pointer_y = resolved_y
        self.update()

    def _horizontal_crosshair_record(self) -> dict[str, Any] | None:
        y = self._crosshair_pointer_y
        if y is None:
            return None
        scale = self.price_scale()
        if scale is not None and scale.plot_top <= y <= scale.plot_bottom:
            return {
                "pane": "PRICE",
                "y": float(y),
                "value": scale.price_for_y(y),
            }
        for pane in self.lower_pane_records():
            top = int(pane["top"])
            bottom = int(pane["bottom"])
            if not top <= y <= bottom:
                continue
            lower_scale = self._lower_scale(
                str(pane["family"]),
                top,
                bottom,
            )
            if lower_scale is None:
                return {
                    "pane": str(pane["family"]),
                    "y": float(y),
                    "value": None,
                }
            bounded_y = min(
                lower_scale.plot_bottom,
                max(lower_scale.plot_top, float(y)),
            )
            return {
                "pane": str(pane["family"]),
                "y": bounded_y,
                "value": lower_scale.price_for_y(bounded_y),
            }
        return None

    def crosshair_records(self) -> dict[str, Any]:
        index = self.crosshair_index
        visible = bool(
            index is not None
            and self._is_index_visible(index)
        )
        return {
            "index": index,
            "x": (
                self._x_for_index(index)
                if visible and index is not None
                else None
            ),
            "visible": visible,
            "pinned": self._pinned_marker_key is not None,
            "horizontal": self._horizontal_crosshair_record(),
        }

    @staticmethod
    def _crosshair_number_text(value: Any) -> str:
        number = _finite_number(value)
        if number is None:
            return "-"
        if abs(number) >= 1000:
            text = f"{number:,.2f}".rstrip("0").rstrip(".")
        else:
            text = f"{number:.4f}".rstrip("0").rstrip(".")
        return "0" if text == "-0" else text

    def crosshair_value_records(self) -> list[dict[str, Any]]:
        index = self.crosshair_index
        if index is None or not 0 <= index < len(self._candles):
            return []
        candle = self._candles[index]
        records: list[dict[str, Any]] = [{
            "kind": "CANDLE",
            "label": "OHLC",
            "state": "NORMAL",
            "time": str(candle.get("time") or ""),
            "open": _finite_number(candle.get("open")),
            "high": _finite_number(candle.get("high")),
            "low": _finite_number(candle.get("low")),
            "close": _finite_number(candle.get("close")),
        }]
        for descriptor in self._visualization_descriptors:
            for channel in descriptor.series_keys:
                state = self._series_state(descriptor, channel)
                values = self._cached_values(descriptor, channel)
                value = (
                    _finite_number(values[index])
                    if len(values) == len(self._candles)
                    else None
                )
                records.append({
                    "kind": "FILTER",
                    "identity": descriptor.identity,
                    "family": descriptor.family,
                    "label": self._descriptor_caption(descriptor),
                    "channel": channel,
                    "state": state,
                    "value": value,
                })
        return records

    @staticmethod
    def _marker_key(marker: Mapping[str, Any]) -> tuple[int, str] | None:
        index = marker.get("evaluation_index")
        side = str(marker.get("side") or "").strip().upper()
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and side in {"BUY", "SELL"}
        ):
            return (index, side)
        return None

    def _marker_y(
        self,
        index: int,
        side: str,
        scale: _ValidationPriceScale | None = None,
    ) -> float | None:
        if not 0 <= index < len(self._candles):
            return None
        normalized_side = str(side or "").strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            return None
        effective_scale = scale if scale is not None else self.price_scale()
        if effective_scale is None:
            return None
        candle = self._candles[index]
        field = "low" if normalized_side == "BUY" else "high"
        price = _finite_number(candle.get(field))
        if price is None:
            price = _finite_number(candle.get("close"))
        if price is None:
            return None
        candle_y = effective_scale.y_for_price(price)
        return (
            candle_y + 12
            if normalized_side == "BUY"
            else candle_y - 12
        )

    def _marker_at(self, x: float, y: float) -> dict[str, Any] | None:
        scale = self.price_scale()
        if scale is None:
            return None
        price_plot_rect = self._price_plot_rect(scale)
        if not price_plot_rect.contains(QPointF(float(x), float(y))):
            return None
        center_index = round(self._index_float_for_x(x))
        index_radius = max(1, math.ceil(7.0 / self.pixels_per_candle))
        candidates: list[tuple[int, dict[str, Any]]] = []
        for index in range(
            center_index - index_radius,
            center_index + index_radius + 1,
        ):
            candidates.extend(self._markers_by_index.get(index, ()))
        candidates.sort(key=lambda record: record[0], reverse=True)
        for _order, marker in candidates:
            key = self._marker_key(marker)
            if key is None:
                continue
            index, side = key
            if not 0 <= index < len(self._candles):
                continue
            if not self._is_index_visible(index):
                continue
            marker_y = self._marker_y(index, side, scale)
            if marker_y is None:
                continue
            marker_x = self._x_for_index(index)
            if abs(x - marker_x) <= 7 and abs(y - marker_y) <= 11:
                overlapping = [m for _, m in self._markers_by_index.get(index, ())
                               if m.get("side") == side]
                preferred = "manual" if self._validation_range is not None else "marking"
                marker = next((m for m in overlapping
                               if m.get("provenance") == preferred), marker)
                return marker
        return None

    def _series_base_color(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
    ) -> QColor:
        token = f"{descriptor.identity}|{channel}"
        index = sum(ord(character) for character in token) % len(
            _VISUAL_SERIES_COLORS
        )
        return QColor(_VISUAL_SERIES_COLORS[index])

    def _series_state(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
    ) -> str:
        values = self._cached_values(descriptor, channel)
        if len(values) != len(self._candles):
            return "ERROR"

        marker_key = self.effective_marker_key
        if marker_key is None:
            return "NORMAL" if descriptor.supported else "UNSUPPORTED"

        _index, side = marker_key
        if side in descriptor.unsupported_sides:
            return "UNSUPPORTED"
        active = set(self._visualization_active_by_marker.get(marker_key, ()))
        if descriptor.identity in active:
            return "ACTIVE"
        return "INACTIVE"

    def _series_pen(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
    ) -> QPen:
        state = self._series_state(descriptor, channel)
        return self._series_pen_for_state(
            descriptor,
            channel,
            state,
        )

    def visualization_style_records(self) -> list[dict[str, Any]]:
        marker_key = self.effective_marker_key
        records = []
        for descriptor in self._visualization_descriptors:
            for channel in descriptor.series_keys:
                pen = self._series_pen(descriptor, channel)
                records.append({
                    "identity": descriptor.identity,
                    "family": descriptor.family,
                    "channel": channel,
                    "state": self._series_state(descriptor, channel),
                    "marker_key": marker_key,
                    "pen_width": pen.width(),
                    "pen_style": int(pen.style()),
                    "color": pen.color().name(),
                    "alpha": pen.color().alpha(),
                })
        return records

    def _candle_body_width(self) -> float:
        slot_width = self.pixels_per_candle
        effective_gap = min(
            self._CANDLE_HORIZONTAL_GAP,
            max(0.0, slot_width - 1.0),
        )
        return max(1.0, slot_width - effective_gap)

    def time_axis_records(self) -> list[dict[str, Any]]:
        start, end = self._visible_index_bounds()
        candles = self._candles[start:end]
        # Time-axis layout depends on timestamps and slice length, not prices.
        # Keep one exact content key; never reuse by list identity alone.
        times = tuple(candle.get("time") for candle in candles)
        cacheable = all(type(value) in (str, int, float, bool, type(None))
                        for value in times)
        key = tuple((type(value), value) for value in times) if cacheable else None
        if cacheable and key == self._time_axis_layout_cache_key:
            records = self._time_axis_layout_cache_records
        else:
            records = _time_axis_label_records(candles)
            if cacheable:
                self._time_axis_layout_cache_key = key
                self._time_axis_layout_cache_records = tuple(dict(record) for record in records)
        return [
            {**record, "index": record["index"] + start}
            for record in records
        ]

    @property
    def plot_left(self) -> int:
        return int(self._LEFT)

    def _price_plot_rect(
        self,
        scale: _ValidationPriceScale,
    ) -> QRectF:
        return QRectF(
            self.plot_left,
            scale.plot_top,
            max(1, self.width() - self._RIGHT - self.plot_left),
            max(1, scale.plot_bottom - scale.plot_top),
        )


    def price_scale(self) -> _ValidationPriceScale | None:
        minimum = self._price_minimum
        maximum = self._price_maximum
        if minimum is None or maximum is None:
            minimum, maximum = self._price_data_bounds()
        return _validation_price_scale(
            self._candles,
            self.height(),
            plot_top=self._price_plot_top_margin(),
            plot_bottom_margin=self._price_plot_bottom_margin(),
            minimum=minimum,
            maximum=maximum,
        )

    def set_price_view(self, minimum: float, maximum: float) -> None:
        resolved_minimum = _finite_number(minimum)
        resolved_maximum = _finite_number(maximum)
        if (
            resolved_minimum is None
            or resolved_maximum is None
            or resolved_maximum <= resolved_minimum
        ):
            raise ValueError("price view requires finite increasing bounds")
        changed = (self._price_minimum, self._price_maximum) != (resolved_minimum, resolved_maximum)
        self._price_minimum = resolved_minimum
        self._price_maximum = resolved_maximum
        if changed:
            self._maybe_begin_cycle_label_preview()
        self.update()

    def grid_line_records(self) -> list[dict[str, Any]]:
        scale = self.price_scale()
        return [] if scale is None else scale.tick_records()

    def marker_tooltip_at(self, x: float, y: float) -> str:
        marker = self._marker_at(x, y)
        return "" if marker is None else str(marker.get("tooltip") or "")

    def candle_tooltip_at(self, x: float, y: float) -> str:
        scale = self.price_scale()
        if not self._candles or scale is None:
            return ""
        if not scale.plot_top <= y <= scale.plot_bottom:
            return ""
        index = self._nearest_candle_index(x)
        if index is None or not self._is_index_visible(index):
            return ""
        candle = self._candles[index]
        high = _finite_number(candle.get("high"))
        low = _finite_number(candle.get("low"))
        if high is None or low is None:
            return ""
        high_y = scale.y_for_price(high)
        low_y = scale.y_for_price(low)
        candle_top = min(high_y, low_y)
        candle_bottom = max(high_y, low_y)
        hit_top = max(scale.plot_top, candle_top)
        hit_bottom = min(scale.plot_bottom, candle_bottom)
        if hit_top > hit_bottom or not hit_top <= y <= hit_bottom:
            return ""
        value = lambda field: (
            candle.get(field) if candle.get(field) is not None else "-"
        )
        return "\n".join((
            _display_time(candle.get("time")),
            f"시가: {value('open')}",
            f"고가: {value('high')}",
            f"저가: {value('low')}",
            f"종가: {value('close')}",
        ))

    @staticmethod
    def _point_segment_distance(
        x: float,
        y: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> float:
        dx = x2 - x1
        dy = y2 - y1
        length_squared = dx * dx + dy * dy
        if length_squared <= 0:
            return math.hypot(x - x1, y - y1)
        ratio = ((x - x1) * dx + (y - y1) * dy) / length_squared
        ratio = min(1.0, max(0.0, ratio))
        px = x1 + ratio * dx
        py = y1 + ratio * dy
        return math.hypot(x - px, y - py)

    def _line_series_hit(
        self,
        values: tuple[float | None, ...],
        scale: _ValidationPriceScale,
        x: float,
        y: float,
    ) -> tuple[float, float] | None:
        visible_start, visible_end = self._visible_index_bounds()
        if visible_end <= visible_start or len(values) != len(self._candles):
            return None

        pixels_per_candle = self.pixels_per_candle
        x_origin = (
            float(self._LEFT)
            + (0.5 - self._visible_start_index) * pixels_per_candle
        )
        price_span = scale.maximum - scale.minimum
        y_per_price = (scale.plot_bottom - scale.plot_top) / price_span
        y_origin = scale.plot_top + scale.maximum * y_per_price

        best_distance: float | None = None
        best_value: float | None = None
        raw_index = (float(x) - x_origin) / pixels_per_candle
        base_index = math.floor(raw_index)
        for left_index in range(base_index - 1, base_index + 2):
            right_index = left_index + 1
            if (
                left_index < visible_start
                or right_index >= visible_end
                or left_index < 0
                or right_index >= len(values)
            ):
                continue
            left_value = values[left_index]
            right_value = values[right_index]
            if left_value is None or right_value is None:
                continue
            x1 = x_origin + float(left_index) * pixels_per_candle
            x2 = x_origin + float(right_index) * pixels_per_candle
            y1 = y_origin - left_value * y_per_price
            y2 = y_origin - right_value * y_per_price
            distance = self._point_segment_distance(
                x, y, x1, y1, x2, y2
            )
            if best_distance is None or distance < best_distance:
                best_distance = distance
                ratio = min(1.0, max(0.0, (x - x1) / (x2 - x1)))
                best_value = left_value + (right_value - left_value) * ratio

        nearest = self._nearest_candle_index(x)
        if (
            nearest is not None
            and visible_start <= nearest < visible_end
            and 0 <= nearest < len(values)
        ):
            value = values[nearest]
            if value is not None:
                point_distance = math.hypot(
                    x - (x_origin + float(nearest) * pixels_per_candle),
                    y - (y_origin - value * y_per_price),
                )
                if best_distance is None or point_distance < best_distance:
                    best_distance = point_distance
                    best_value = value

        if (
            best_distance is None
            or best_value is None
            or best_distance > self._SERIES_HIT_TOLERANCE
        ):
            return None
        return best_distance, best_value

    def _histogram_series_hit(
        self,
        values: tuple[float | None, ...],
        scale: _ValidationPriceScale,
        x: float,
        y: float,
    ) -> tuple[float, float] | None:
        pixels_per_candle = self.pixels_per_candle
        x_origin = (
            float(self._LEFT)
            + (0.5 - self._visible_start_index) * pixels_per_candle
        )
        index = self._nearest_candle_index(x)
        if (
            index is None
            or not self._is_index_visible(index)
            or index < 0
            or index >= len(values)
        ):
            return None
        value = values[index]
        if value is None:
            return None
        center_x = x_origin + float(index) * pixels_per_candle
        half_width = max(
            1.0,
            pixels_per_candle
            - min(
                self._CANDLE_HORIZONTAL_GAP,
                max(0.0, pixels_per_candle - 1.0),
            ),
        ) / 2.0
        price_span = scale.maximum - scale.minimum
        y_per_price = (scale.plot_bottom - scale.plot_top) / price_span
        y_origin = scale.plot_top + scale.maximum * y_per_price
        zero_y = y_origin
        value_y = y_origin - value * y_per_price
        top = min(zero_y, value_y)
        bottom = max(zero_y, value_y)
        tolerance = self._SERIES_HIT_TOLERANCE
        if (
            center_x - half_width - tolerance <= x <= center_x + half_width + tolerance
            and top - tolerance <= y <= bottom + tolerance
        ):
            dx = max(0.0, abs(x - center_x) - half_width)
            dy = (
                top - y
                if y < top
                else y - bottom
                if y > bottom
                else 0.0
            )
            return math.hypot(dx, dy), value
        return None

    @staticmethod
    def _series_channel_label(channel: str) -> str:
        normalized = str(channel or "").strip().upper()
        if normalized.startswith("CRITERION"):
            return "기준선"
        return chart_operand_label(normalized)

    def _series_tooltip_text(
        self,
        descriptors: list[ValidationFilterDescriptor],
        channel: str,
        value: float,
    ) -> str:
        normalized_channel = str(channel or "").strip().upper()
        captions: list[str] = []
        for descriptor in descriptors:
            parameters = descriptor.parameters
            if descriptor.family == FAMILY_OCR_OSC:
                caption = (
                    f"OCR({parameters.get('fast', 12)},"
                    f"{parameters.get('slow', 26)},"
                    f"{parameters.get('signal', 9)})"
                )
            elif descriptor.family == FAMILY_RSI:
                caption = f"RSI({parameters.get('period', 14)})"
            elif descriptor.family == FAMILY_MACD_SIGNAL:
                caption = (
                    f"{self._series_channel_label(normalized_channel)}("
                    f"{parameters.get('fast', 12)},"
                    f"{parameters.get('slow', 26)},"
                    f"{parameters.get('signal', 9)})"
                )
            elif descriptor.family in {
                FAMILY_MOVING_AVERAGE,
                FAMILY_MA_ARRANGEMENT,
                FAMILY_PRICE_COMPARISON,
            }:
                caption = self._series_channel_label(normalized_channel)
            elif descriptor.family in {FAMILY_BOLLINGER, FAMILY_PRICE_BOX}:
                if descriptor.family == FAMILY_BOLLINGER:
                    owner = (
                        f"볼린저밴드({parameters.get('period', 20)},"
                        f"{parameters.get('std', parameters.get('stddev', 2))})"
                    )
                    criterion_lines = {
                        "BOLLINGER_UPPER": "상단",
                        "BOLLINGER_MIDDLE": "중단",
                        "BOLLINGER_LOWER": "하단",
                    }
                else:
                    owner = f"가격박스({parameters.get('period', 20)})"
                    criterion_lines = {
                        "PRICE_BOX_UPPER": "상단",
                        "PRICE_BOX_MIDDLE": "중단",
                        "PRICE_BOX_LOWER": "하단",
                    }
                raw_line = {
                    "UPPER": "상단", "HIGH": "상단",
                    "MIDDLE": "중단", "MID": "중단",
                    "LOWER": "하단", "LOW": "하단",
                }.get(normalized_channel)
                if normalized_channel.startswith("CRITERION"):
                    condition = parameters.get("condition")
                    condition = condition if isinstance(condition, Mapping) else {}
                    line = criterion_lines.get(
                        str(condition.get("compare_target") or "").strip().upper()
                    )
                    offset = _finite_number(condition.get("value"))
                    if offset is not None and condition.get("signed_percent_offset") is not True:
                        operator = str(condition.get("operator") or "").strip().upper()
                        if operator in {">", ">=", "GT", "GTE", "ABOVE"}:
                            offset = abs(offset)
                        elif operator in {"<", "<=", "LT", "LTE", "BELOW"}:
                            offset = -abs(offset)
                        else:
                            offset = 0.0
                    line_text = f" {line}" if line else ""
                    offset_text = f" {offset:+g}%" if offset not in (None, 0.0) else ""
                    caption = f"{owner}{line_text}{offset_text} 기준선"
                elif raw_line:
                    caption = f"{owner} {raw_line}"
                else:
                    channel_label = self._series_channel_label(normalized_channel)
                    if channel_label == normalized_channel:
                        channel_label = descriptor.label
                    caption = f"{channel_label}{owner[owner.index('('):]}"
            else:
                caption = self._series_channel_label(normalized_channel)
            if caption not in captions:
                captions.append(caption)
        return (
            f"{' / '.join(captions) if captions else self._series_channel_label(normalized_channel)}: "
            f"{self._crosshair_number_text(value)}"
        )

    def series_tooltip_at(self, x: float, y: float) -> str:
        if not self._visualization_descriptors or self._visualization_cache is None:
            return ""
        if x < self.plot_left or x > self.width() - self._RIGHT:
            return ""

        candidates: list[
            tuple[
                float,
                list[ValidationFilterDescriptor],
                str,
                float,
            ]
        ] = []
        price_scale = self.price_scale()
        if price_scale is not None and (
            price_scale.plot_top - self._SERIES_HIT_TOLERANCE
            <= y
            <= price_scale.plot_bottom + self._SERIES_HIT_TOLERANCE
        ):
            for channel, values, owners in self._price_series_groups:
                hit = self._line_series_hit(
                    values,
                    price_scale,
                    x,
                    y,
                )
                if hit is not None:
                    candidates.append((hit[0], owners, channel, hit[1]))

        for pane in self.lower_pane_records():
            family = str(pane["family"])
            top = int(pane["top"])
            bottom = int(pane["bottom"])
            if not (
                top - self._SERIES_HIT_TOLERANCE
                <= y
                <= bottom + self._SERIES_HIT_TOLERANCE
            ):
                continue
            scale = self._lower_scale(family, top, bottom)
            if scale is None:
                continue
            for channel, values, owners in self._lower_series_groups.get(
                family,
                (),
            ):
                if family == FAMILY_OCR_OSC and channel == "OSC":
                    hit = self._histogram_series_hit(
                        values,
                        scale,
                        x,
                        y,
                    )
                else:
                    hit = self._line_series_hit(
                        values,
                        scale,
                        x,
                        y,
                    )
                if hit is not None:
                    candidates.append((hit[0], owners, channel, hit[1]))

        if not candidates:
            return ""
        _distance, owners, channel, value = min(
            candidates,
            key=lambda item: item[0],
        )
        return self._series_tooltip_text(
            owners,
            channel,
            value,
        )

    def _set_hover_marker_key(
        self,
        marker_key: tuple[int, str] | None,
    ) -> None:
        if marker_key == self._hover_marker_key:
            return
        self._hover_marker_key = marker_key
        self.update()

    def clear_evidence_pin(self) -> None:
        if self._pinned_marker_key is None:
            return
        self._pinned_marker_key = None
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._price_legend_double_click_consumed = False
            legend_target = self._price_legend_target_at(
                float(event.pos().x()),
                float(event.pos().y()),
            )
            if legend_target is not None:
                self._clear_pan_drag()
                self._price_legend_press_identities = legend_target
                self._begin_pointer_interaction()
                self._set_hover_marker_key(None)
                self._set_crosshair_pointer(None, None)
                self.clear_marker_tooltip()
                event.accept()
                return
            boundary_index = self._lower_boundary_at(float(event.pos().y()))
            if boundary_index is not None:
                self._clear_pan_drag()
                self._lower_boundary_drag_index = boundary_index
                self._begin_pointer_interaction()
                self.setCursor(Qt.SizeVerCursor)
                self._set_hover_marker_key(None)
                self._set_crosshair_pointer(None, None)
                self.clear_marker_tooltip()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._price_legend_double_click_consumed:
            self._price_legend_double_click_consumed = False
            event.accept()
            return
        if (
            event.button() == Qt.LeftButton
            and self._price_legend_press_identities is not None
        ):
            pressed = self._price_legend_press_identities
            self._price_legend_press_identities = None
            released = self._price_legend_target_at(
                float(event.pos().x()),
                float(event.pos().y()),
            )
            if released == pressed:
                if pressed[1]:
                    self._toggle_price_legend_identities(*pressed)
                else:
                    self.reset_price_legend_visibility()
                    self.price_legend_prefix_reset_requested.emit()
            self._end_pointer_interaction(cancelled=False)
            event.accept()
            return
        if (
            event.button() == Qt.LeftButton
            and self._lower_boundary_drag_index is not None
        ):
            boundary_index = self._lower_boundary_drag_index
            self._resize_lower_boundary_to(
                boundary_index,
                float(event.pos().y()),
            )
            self._lower_boundary_drag_index = None
            if self._lower_boundary_at(float(event.pos().y())) is None:
                self.unsetCursor()
            self._end_pointer_interaction(cancelled=False)
            event.accept()
            return

        if (
            event.button() == Qt.LeftButton
            and self._validation_range_drag_start_index is not None
        ):
            start = self._validation_range_drag_start_index
            end = self._nearest_candle_index(event.pos().x())
            if end is None:
                end = self._validation_range_drag_current_index
            self._clear_validation_range_drag()
            self._clear_pan_drag()
            self._end_pointer_interaction(cancelled=False)
            if (
                isinstance(end, int)
                and not isinstance(end, bool)
                and end > start
            ):
                self.set_validation_range(start, end)
                self.validation_range_drag_completed.emit(start, end)
            else:
                self.set_validation_range(None, None)
                self.validation_range_clear_requested.emit()
            event.accept()
            return

        if event.button() == Qt.LeftButton and self._drag_press_x is not None:
            was_drag = self._pan_drag_active
            if was_drag and self._input_frame_owner is None:
                x = float(event.pos().x())
                y = float(event.pos().y())
                last_x = self._drag_last_x if self._drag_last_x is not None else x
                last_y = self._drag_last_y if self._drag_last_y is not None else y
                if self._pan_drag_axis == "TIME":
                    self._queue_time_pan_delta(x - last_x)
                elif self._pan_drag_axis == "PRICE":
                    self._queue_pan_delta(0.0, y - last_y)
            self._clear_pan_drag()
            self._end_pointer_interaction(cancelled=False)
            if not was_drag:
                if self._validation_range is not None:
                    self.set_validation_range(None, None)
                    self.set_selected_index(None)
                    self.validation_range_clear_requested.emit()
                    event.accept()
                    return
                marker = self._marker_at(
                    float(event.pos().x()),
                    float(event.pos().y()),
                )
                if marker is not None:
                    marker_key = self._marker_key(marker)
                    if marker_key is not None:
                        self._pinned_marker_key = marker_key
                        self._hover_marker_key = marker_key
                        self._crosshair_hover_index = marker_key[0]
                        self._crosshair_pointer_y = float(event.pos().y())
                        index = marker_key[0]
                        self.set_selected_index(index)
                        self.bar_selected.emit(index)
                        self.update()
                else:
                    self._set_hover_marker_key(None)
                    self.clear_evidence_pin()
                    index = self._nearest_candle_index(event.pos().x())
                    self._set_crosshair_pointer(
                        index,
                        float(event.pos().y()),
                    )
                    if index is not None:
                        self.set_selected_index(index)
                        self.bar_selected.emit(index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._cancel_hover_pointer_update()
            legend_target = self._price_legend_target_at(
                float(event.pos().x()),
                float(event.pos().y()),
            )
            if legend_target is not None:
                self._price_legend_press_identities = None
                self._price_legend_double_click_consumed = True
                self._clear_pan_drag()
                self._end_pointer_interaction(cancelled=False)
                event.accept()
                return
        if (
            event.button() == Qt.LeftButton
            and self._lower_boundary_at(float(event.pos().y())) is not None
        ):
            self._clear_pan_drag()
            self._end_pointer_interaction(cancelled=False)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            # Double-click is reserved for range-drag start.
            index = self._nearest_candle_index(event.pos().x())
            if index is None:
                self._clear_pan_drag()
                self._end_pointer_interaction(cancelled=False)
                event.accept()
                return

            self._clear_pan_drag()
            self._validation_range_drag_start_index = index
            self._validation_range_drag_current_index = index
            self._begin_pointer_interaction()
            self.set_validation_range(None, None)
            self.set_selected_index(index)
            self.validation_range_drag_started.emit(index)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.is_pointer_interaction_active() and not (
            event.buttons() & Qt.LeftButton
        ):
            self.discard_pointer_interaction()
            event.accept()
            return
        if self._lower_boundary_drag_index is not None:
            self._cancel_hover_pointer_update()
            self._resize_lower_boundary_to(
                self._lower_boundary_drag_index,
                float(event.pos().y()),
            )
            self._set_hover_marker_key(None)
            self._set_crosshair_pointer(None, None)
            self.clear_marker_tooltip()
            self.setCursor(Qt.SizeVerCursor)
            event.accept()
            return

        if self._drag_press_x is None:
            if self._lower_boundary_at(float(event.pos().y())) is not None:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.unsetCursor()

        if self._validation_range_drag_start_index is not None:
            self._cancel_hover_pointer_update()
            start = self._validation_range_drag_start_index
            index = self._nearest_candle_index(event.pos().x())
            if index is not None:
                self._validation_range_drag_current_index = index
                if index > start:
                    self.set_validation_range(start, index)
                else:
                    self.set_validation_range(None, None)
            self._set_hover_marker_key(None)
            self._set_crosshair_pointer(None, None)
            self.clear_marker_tooltip()
            event.accept()
            return

        if self._drag_press_x is not None and self._drag_press_y is not None:
            x = float(event.pos().x())
            y = float(event.pos().y())
            total_dx = x - self._drag_press_x
            total_dy = y - self._drag_press_y
            if (
                not self._pan_drag_active
                and max(abs(total_dx), abs(total_dy)) >= self._DRAG_THRESHOLD
            ):
                abs_dx = abs(total_dx)
                abs_dy = abs(total_dy)
                axis = None
                if (
                    abs_dx >= self._DRAG_THRESHOLD
                    and abs_dx >= abs_dy * self._DRAG_AXIS_DOMINANCE_RATIO
                ):
                    axis = "TIME"
                elif (
                    abs_dy >= self._DRAG_THRESHOLD
                    and abs_dy >= abs_dx * self._DRAG_AXIS_DOMINANCE_RATIO
                ):
                    axis = "PRICE"
                if axis is not None:
                    self._pan_drag_active = True
                    self._pan_drag_axis = axis
                    self.clear_marker_tooltip()
            if self._pan_drag_active:
                self._cancel_hover_pointer_update()
                last_x = self._drag_last_x if self._drag_last_x is not None else x
                last_y = self._drag_last_y if self._drag_last_y is not None else y
                delta_x = x - last_x
                delta_y = y - last_y
                self._drag_last_x = x
                self._drag_last_y = y
                if self._pan_drag_axis == "TIME":
                    self._queue_time_pan_delta(delta_x)
                elif self._pan_drag_axis == "PRICE":
                    self._queue_pan_delta(0.0, delta_y)
                self._set_hover_marker_key(None)
                self._set_crosshair_pointer(None, None)
                event.accept()
                return
        self._queue_hover_pointer(
            float(event.pos().x()),
            float(event.pos().y()),
            QPoint(event.globalPos()),
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._cancel_hover_pointer_update()
        self._set_hover_marker_key(None)
        self._set_crosshair_pointer(None, None)
        self.clear_marker_tooltip()
        if self._lower_boundary_drag_index is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def discard_pointer_interaction(self) -> None:
        self._cancel_cycle_label_preview()
        range_was_active = self._validation_range_drag_start_index is not None
        self._cancel_hover_pointer_update()
        self._lower_boundary_drag_index = None
        self._price_legend_press_identities = None
        self._clear_validation_range_drag()
        self.unsetCursor()
        super().discard_pointer_interaction()
        if range_was_active:
            self.set_validation_range(None, None)
            self.validation_range_clear_requested.emit()

    def hideEvent(self, event) -> None:
        self._cancel_hover_pointer_update()
        self._lower_boundary_drag_index = None
        self.unsetCursor()
        if self._validation_range_drag_start_index is not None:
            self._clear_validation_range_drag()
            self.set_validation_range(None, None)
            self.validation_range_clear_requested.emit()
        self._price_legend_press_identities = None
        self.discard_pointer_interaction()
        self._set_hover_marker_key(None)
        self._set_crosshair_pointer(None, None)
        self.clear_marker_tooltip()
        super().hideEvent(event)

    def clear_marker_tooltip(self) -> None:
        self._active_tooltip = ""
        QToolTip.hideText()

    def visualization_series_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for descriptor in self._visualization_descriptors:
            for channel in descriptor.series_keys:
                values = self._cached_values(descriptor, channel)
                if len(values) != len(self._candles):
                    continue
                records.append({
                    "identity": descriptor.identity,
                    "family": descriptor.family,
                    "axis": descriptor.axis,
                    "channel": channel,
                    "caption": self._descriptor_caption(descriptor),
                    "value_count": len(values),
                })
        return records

    def _series_line_data_path(
        self,
        values: tuple[float | None, ...],
        visible_start: int,
        visible_end: int,
    ) -> tuple[QPainterPath, bool]:
        from bisect import bisect_left

        bounds = (visible_start, visible_end)
        if bounds != self._series_line_path_cache_bounds:
            self._series_line_path_cache_bounds = bounds
            self._series_line_path_cache.clear()
        cache_key = id(values)
        cached = self._series_line_path_cache.get(cache_key)
        if cached is not None:
            return cached

        segments = self._series_polygon_cache.get(cache_key)
        if segments is None:
            built_segments = []
            segment_start = 0
            points: list[QPointF] = []
            for index, value in enumerate(values):
                if value is None:
                    if index in self._bridgeable_display_only_indexes:
                        continue
                    if points:
                        built_segments.append((
                            segment_start,
                            index,
                            QPolygonF(points),
                        ))
                        points = []
                    continue
                if not points:
                    segment_start = index
                points.append(QPointF(float(index), value))
            if points:
                built_segments.append((
                    segment_start,
                    len(values),
                    QPolygonF(points),
                ))
            segments = tuple(built_segments)
            self._series_polygon_cache[cache_key] = segments

        path = QPainterPath()
        has_segment = False
        for segment_start, segment_end, polygon in segments:
            start = max(visible_start, segment_start)
            end = min(visible_end, segment_end)
            if end <= start:
                continue
            # Bridgeable gaps leave raw candle indexes sparse in the polygon.
            point_start = bisect_left(polygon, start, key=lambda point: point.x())
            point_end = bisect_left(polygon, end, key=lambda point: point.x())
            count = point_end - point_start
            if count <= 0:
                continue
            path.addPolygon(polygon.mid(point_start, count))
            if count >= 2:
                has_segment = True
        result = (path, has_segment)
        self._series_line_path_cache[cache_key] = result
        return result

    def _draw_series_line(
        self,
        painter: QPainter,
        values: tuple[float | None, ...],
        scale: _ValidationPriceScale,
        pen: QPen,
    ) -> None:
        visible_start, visible_end = self._visible_index_bounds()
        pixels_per_candle = self.pixels_per_candle
        x_origin = (
            float(self._LEFT)
            + (0.5 - self._visible_start_index) * pixels_per_candle
        )
        price_span = scale.maximum - scale.minimum
        y_per_price = (scale.plot_bottom - scale.plot_top) / price_span
        y_origin = scale.plot_top + scale.maximum * y_per_price
        data_path, has_segment = self._series_line_data_path(
            values,
            visible_start,
            visible_end,
        )
        if has_segment:
            transform = QTransform(
                pixels_per_candle,
                0.0,
                0.0,
                -y_per_price,
                x_origin,
                y_origin,
            )
            path = transform.map(data_path)
            painter.setPen(pen)
            painter.drawPath(path)

    def _draw_series_histogram(
        self,
        painter: QPainter,
        values: tuple[float | None, ...],
        scale: _ValidationPriceScale,
        pen: QPen,
        owners: tuple[ValidationFilterDescriptor, ...],
    ) -> None:
        visible_start, visible_end = self._visible_index_bounds()
        pixels_per_candle = self.pixels_per_candle
        x_origin = (
            float(self._LEFT)
            + (0.5 - self._visible_start_index) * pixels_per_candle
        )
        price_span = scale.maximum - scale.minimum
        y_per_price = (scale.plot_bottom - scale.plot_top) / price_span
        y_origin = scale.plot_top + scale.maximum * y_per_price
        zero_y = y_origin
        bar_width = max(
            1.0,
            pixels_per_candle
            - min(
                self._CANDLE_HORIZONTAL_GAP,
                max(0.0, pixels_per_candle - 1.0),
            ),
        )
        preserved_state_colors = (
            _INACTIVE_SERIES,
            _UNSUPPORTED_SERIES,
            _ERROR_SERIES,
        )
        base_color = pen.color()
        preserve_base_color = any(
            base_color == state_color for state_color in preserved_state_colors
        )
        base_color_key = int(base_color.rgba())
        positive_color_key = int(_OSC_POSITIVE.rgba())
        negative_color_key = int(_OSC_NEGATIVE.rgba())
        half_bar = bar_width / 2
        rectangles_by_color: dict[
            tuple[int, bool], tuple[QColor, list[QRectF], bool]
        ] = {}
        ocr_filter_plan = self._prepare_ocr_histogram_filters(owners)
        signal_plan = self._prepare_histogram_signal_owners(owners)
        for index in range(visible_start, visible_end):
            if index >= len(values):
                break
            value = values[index]
            if value is None:
                continue
            value_y = y_origin - value * y_per_price
            top = min(zero_y, value_y)
            height = max(1.0, abs(value_y - zero_y))
            rect = QRectF(
                x_origin + float(index) * pixels_per_candle - half_bar,
                top,
                bar_width,
                height,
            )
            bar_color = base_color
            color_key = base_color_key
            ocr_matches = self._ocr_histogram_filter_matches(
                index, owners, prepared=ocr_filter_plan,
            )
            ocr_sides = {side for side, _identities in ocr_matches}
            signal_color = self._histogram_valid_signal_color(
                index, owners, ocr_sides=ocr_sides, prepared=signal_plan,
            )
            valid_emphasis = bool(
                signal_color is not None
                and self._valid_filter_mode_enabled
                and len(ocr_sides) == 1
            )
            if signal_color is not None:
                bar_color = signal_color
                color_key = int(signal_color.rgba())
            elif not preserve_base_color:
                if value > 0:
                    bar_color = _OSC_POSITIVE
                    color_key = positive_color_key
                elif value < 0:
                    bar_color = _OSC_NEGATIVE
                    color_key = negative_color_key
            group_key = (color_key, valid_emphasis)
            color_group = rectangles_by_color.get(group_key)
            if color_group is None:
                color_group = (QColor(bar_color), [], valid_emphasis)
                rectangles_by_color[group_key] = color_group
            color_group[1].append(rect)
        for bar_color, rectangles, valid_emphasis in rectangles_by_color.values():
            bar_pen = QPen(pen)
            bar_pen.setColor(bar_color)
            if valid_emphasis:
                bar_pen.setWidthF(max(2.0, bar_pen.widthF()))
            painter.setPen(bar_pen)
            painter.setBrush(QBrush(bar_color))
            painter.drawRects(rectangles)

    def _indicator_transition_indexes(
        self,
        values: tuple[float | None, ...],
        operator: str,
    ) -> frozenset[int]:
        key = (id(values), str(operator or "").strip().upper())
        indexes = self._indicator_transition_index_cache.get(key)
        if indexes is None:
            indexes = frozenset(indicator_transition_indexes(values, key[1]))
            self._indicator_transition_index_cache[key] = indexes
        return indexes

    def _valid_identity_covers_index(
        self,
        side: str,
        identity: str,
        index: int,
    ) -> bool:
        ranges = self._valid_filter_index_ranges.get((side, identity))
        return bool(ranges) and any(start <= index <= end for start, end in ranges)

    def _valid_identity_delay_at_index(
        self,
        side: str,
        identity: str,
        index: int,
    ) -> int | None:
        ranges = self._valid_filter_delay_ranges.get((side, identity), ())
        for start, end, delay in ranges:
            if start <= index <= end:
                return delay
        return 0 if not ranges else None

    def _histogram_valid_signal_color(
        self,
        index: int,
        owners: tuple[ValidationFilterDescriptor, ...],
        *,
        ocr_sides: set[str] | None = None,
        prepared=None,
    ) -> QColor | None:
        """Return one side color for configured or confirmed oscillator transitions."""
        sides = (
            self._ocr_histogram_filter_sides(index, owners)
            if ocr_sides is None
            else set(ocr_sides)
        )
        if prepared is not None:
            for family, identity, operator, transition_indexes, owner_sides in prepared:
                for side in owner_sides:
                    transition_index = int(index)
                    if family == FAMILY_OCR_OSC and operator in {"TURN_UP", "TURN_DOWN"}:
                        if self._valid_filter_mode_enabled:
                            continue
                        if not self._valid_filter_delay_ranges.get((side, identity)):
                            continue
                        delay = self._valid_identity_delay_at_index(side, identity, int(index))
                        if delay is None:
                            continue
                        transition_index -= delay
                        if (
                            transition_index >= 0
                            and transition_index in transition_indexes
                            and (side, identity) in self._valid_lower_filter_identities
                            and self._valid_identity_covers_index(side, identity, int(index))
                        ):
                            sides.add(side)
                        continue
                    if (
                        side in {"BUY", "SELL"}
                        and transition_index >= 0
                        and transition_index in transition_indexes
                        and (side, identity) in self._valid_lower_filter_identities
                        and self._valid_identity_covers_index(side, identity, int(index))
                    ):
                        sides.add(side)
            if len(sides) != 1:
                return None
            return _BUY if "BUY" in sides else _SELL
        for descriptor in owners:
            if descriptor.family not in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
                continue
            identity = str(descriptor.identity or "").strip()
            condition = _histogram_condition(descriptor.parameter_json)
            if not identity or not isinstance(condition, Mapping):
                continue
            operator = str(condition.get("operator") or "").strip().upper()
            if operator not in {
                "TURN_UP", "TURN_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN",
            }:
                continue
            target = self._normalized_lower_condition_channel(condition.get("target"))
            if not target:
                target = "OSC" if descriptor.family == FAMILY_OCR_OSC else "MACD"
            transition_values = self._cached_values(descriptor, target)
            if (
                len(transition_values) != len(self._candles)
            ):
                continue
            transition_indexes = self._indicator_transition_indexes(
                transition_values, operator,
            )
            for raw_side in descriptor.sides:
                side = str(raw_side or "").strip().upper()
                transition_index = int(index)
                if (
                    descriptor.family == FAMILY_OCR_OSC
                    and operator in {"TURN_UP", "TURN_DOWN"}
                ):
                    if self._valid_filter_mode_enabled:
                        continue
                    if not self._valid_filter_delay_ranges.get((side, identity)):
                        continue
                    delay = self._valid_identity_delay_at_index(
                        side, identity, int(index),
                    )
                    if delay is None:
                        continue
                    transition_index -= delay
                    if (
                        transition_index >= 0
                        and transition_index in transition_indexes
                        and (side, identity) in self._valid_lower_filter_identities
                        and self._valid_identity_covers_index(side, identity, int(index))
                    ):
                        sides.add(side)
                    continue
                if (
                    side in {"BUY", "SELL"}
                    and transition_index >= 0
                    and transition_index in transition_indexes
                    and (side, identity) in self._valid_lower_filter_identities
                    and self._valid_identity_covers_index(side, identity, int(index))
                ):
                    sides.add(side)
        if len(sides) != 1:
            return None
        return _BUY if "BUY" in sides else _SELL

    def _prepare_histogram_signal_owners(self, owners):
        prepared = []
        for descriptor in owners:
            if descriptor.family not in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}:
                continue
            identity = str(descriptor.identity or "").strip()
            condition = _histogram_condition(descriptor.parameter_json)
            if not identity or not isinstance(condition, Mapping):
                continue
            operator = str(condition.get("operator") or "").strip().upper()
            if operator not in {
                "TURN_UP", "TURN_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN",
            }:
                continue
            target = self._normalized_lower_condition_channel(condition.get("target"))
            if not target:
                target = "OSC" if descriptor.family == FAMILY_OCR_OSC else "MACD"
            values = self._cached_values(descriptor, target)
            if len(values) != len(self._candles):
                continue
            prepared.append((
                descriptor.family, identity, operator,
                self._indicator_transition_indexes(values, operator),
                tuple(str(side or "").strip().upper() for side in descriptor.sides),
            ))
        return tuple(prepared)

    @staticmethod
    def _histogram_threshold_matches(
        value: float,
        operator: str,
        threshold: float,
    ) -> bool:
        normalized = str(operator or "").strip().upper()
        if normalized in {">", "GT", "ABOVE", "GREATER_THAN"}:
            return value > threshold
        if normalized in {">=", "GTE", "GREATER_THAN_OR_EQUAL"}:
            return value >= threshold
        if normalized in {"<", "LT", "BELOW", "LESS_THAN"}:
            return value < threshold
        if normalized in {"<=", "LTE", "LESS_THAN_OR_EQUAL"}:
            return value <= threshold
        if normalized in {"=", "==", "EQ", "EQUAL"}:
            return value == threshold
        return False

    def _ocr_histogram_filter_matches(
        self,
        index: int,
        owners: tuple[ValidationFilterDescriptor, ...],
        *,
        prepared=None,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        if prepared is not None:
            matches = []
            for side, identities, signal_delay, components in prepared:
                saw_threshold = False
                saw_turn = False
                matched = True
                expected_turn = "TURN_UP" if side == "BUY" else "TURN_DOWN"
                for component in components:
                    if component is None:
                        matched = False
                        break
                    operator, values, delay, bar_offset, threshold, transitions = component
                    if operator in {"TURN_UP", "TURN_DOWN"}:
                        saw_turn = True
                        transition_index = int(index) - delay if delay is not None else -1
                        if (
                            operator != expected_turn
                            or delay is None
                            or len(values) != len(self._candles)
                            or transition_index < 0
                            or transition_index not in transitions
                        ):
                            matched = False
                            break
                        continue
                    effective_index = (
                        int(index) - signal_delay - bar_offset
                        if signal_delay is not None and bar_offset >= 0
                        else -1
                    )
                    value = (
                        _finite_number(values[effective_index])
                        if 0 <= effective_index < len(values)
                        else None
                    )
                    saw_threshold = True
                    if (
                        threshold is None
                        or value is None
                        or not self._histogram_threshold_matches(value, operator, threshold)
                    ):
                        matched = False
                        break
                if matched and saw_threshold and saw_turn:
                    matches.append((side, identities))
            return tuple(matches)
        descriptor_by_identity = {
            str(descriptor.identity or "").strip(): descriptor
            for descriptor in owners
            if descriptor.family == FAMILY_OCR_OSC
            and str(descriptor.identity or "").strip()
        }
        matches: list[tuple[str, tuple[str, ...]]] = []
        for presentation in self._lower_filter_presentations or ():
            side = str(presentation.side or "").strip().upper()
            if side not in {"BUY", "SELL"}:
                continue
            component_identities = tuple(dict.fromkeys(
                str(identity or "").strip()
                for identity in presentation.component_descriptor_identities
                if str(identity or "").strip()
            ))
            if (
                not component_identities
                or any(
                    identity not in descriptor_by_identity
                    for identity in component_identities
                )
            ):
                continue
            if self._valid_filter_mode_enabled and any(
                (side, identity) not in self._valid_lower_filter_identities
                for identity in component_identities
            ):
                continue
            identities = component_identities
            configured_turn_delays = {
                self._configured_ocr_turn_delays[(side, identity)]
                for identity in identities
                if (condition := _histogram_condition(
                    descriptor_by_identity[identity].parameter_json
                )) is not None
                and str(condition.get("operator") or ""
                ).strip().upper() in {"TURN_UP", "TURN_DOWN"}
                and (side, identity) in self._configured_ocr_turn_delays
            }
            signal_delay = (
                next(iter(configured_turn_delays))
                if len(configured_turn_delays) == 1
                else None
            )

            saw_threshold = False
            saw_turn = False
            matched = True
            expected_turn = "TURN_UP" if side == "BUY" else "TURN_DOWN"
            for identity in identities:
                descriptor = descriptor_by_identity[identity]
                condition = _histogram_condition(descriptor.parameter_json)
                if condition is None:
                    matched = False
                    break
                operator = str(condition.get("operator") or "").strip().upper()
                if operator in {"TURN_UP", "TURN_DOWN"}:
                    saw_turn = True
                    if operator != expected_turn:
                        matched = False
                        break
                    delay = self._configured_ocr_turn_delays.get((side, identity))
                    transition_index = int(index) - delay if delay is not None else -1
                    target = self._normalized_lower_condition_channel(
                        condition.get("target")
                    ) or "OSC"
                    values = self._cached_values(descriptor, target)
                    if (
                        delay is None
                        or len(values) != len(self._candles)
                        or transition_index < 0
                        or transition_index not in self._indicator_transition_indexes(
                            values, operator,
                        )
                    ):
                        matched = False
                        break
                    continue

                threshold = _finite_number(
                    condition.get("threshold", condition.get("value"))
                )
                target = self._normalized_lower_condition_channel(
                    condition.get("target")
                ) or "OSC"
                values = self._cached_values(descriptor, target)
                try:
                    bar_offset = int(condition.get("bar_offset", 0))
                except (TypeError, ValueError):
                    bar_offset = -1
                effective_index = (
                    int(index) - signal_delay - bar_offset
                    if signal_delay is not None and bar_offset >= 0
                    else -1
                )
                value = (
                    _finite_number(values[effective_index])
                    if 0 <= effective_index < len(values)
                    else None
                )
                saw_threshold = True
                if (
                    threshold is None
                    or value is None
                    or not self._histogram_threshold_matches(
                        value, operator, threshold,
                    )
                ):
                    matched = False
                    break
            if matched and saw_threshold and saw_turn:
                matches.append((side, identities))
        return tuple(matches)

    def _prepare_ocr_histogram_filters(self, owners):
        descriptor_by_identity = {
            str(descriptor.identity or "").strip(): descriptor
            for descriptor in owners
            if descriptor.family == FAMILY_OCR_OSC
            and str(descriptor.identity or "").strip()
        }
        prepared = []
        for presentation in self._lower_filter_presentations or ():
            side = str(presentation.side or "").strip().upper()
            if side not in {"BUY", "SELL"}:
                continue
            identities = tuple(dict.fromkeys(
                str(identity or "").strip()
                for identity in presentation.component_descriptor_identities
                if str(identity or "").strip()
            ))
            if not identities or any(identity not in descriptor_by_identity for identity in identities):
                continue
            if self._valid_filter_mode_enabled and any(
                (side, identity) not in self._valid_lower_filter_identities
                for identity in identities
            ):
                continue
            conditions = tuple(
                _histogram_condition(descriptor_by_identity[identity].parameter_json)
                for identity in identities
            )
            configured_turn_delays = {
                self._configured_ocr_turn_delays[(side, identity)]
                for identity, condition in zip(identities, conditions)
                if condition is not None
                and str(condition.get("operator") or "").strip().upper()
                in {"TURN_UP", "TURN_DOWN"}
                and (side, identity) in self._configured_ocr_turn_delays
            }
            signal_delay = (
                next(iter(configured_turn_delays))
                if len(configured_turn_delays) == 1 else None
            )
            components = []
            for identity, condition in zip(identities, conditions):
                if condition is None:
                    components.append(None)
                    continue
                descriptor = descriptor_by_identity[identity]
                operator = str(condition.get("operator") or "").strip().upper()
                target = self._normalized_lower_condition_channel(
                    condition.get("target")
                ) or "OSC"
                values = self._cached_values(descriptor, target)
                if operator in {"TURN_UP", "TURN_DOWN"}:
                    delay = self._configured_ocr_turn_delays.get((side, identity))
                    transitions = (
                        self._indicator_transition_indexes(values, operator)
                        if delay is not None and len(values) == len(self._candles)
                        else frozenset()
                    )
                    components.append((operator, values, delay, None, None, transitions))
                else:
                    threshold = _finite_number(
                        condition.get("threshold", condition.get("value"))
                    )
                    try:
                        bar_offset = int(condition.get("bar_offset", 0))
                    except (TypeError, ValueError):
                        bar_offset = -1
                    components.append((operator, values, None, bar_offset, threshold, None))
            prepared.append((side, identities, signal_delay, tuple(components)))
        return tuple(prepared)

    def _ocr_histogram_filter_sides(
        self,
        index: int,
        owners: tuple[ValidationFilterDescriptor, ...],
    ) -> set[str]:
        return {
            side
            for side, _identities in self._ocr_histogram_filter_matches(index, owners)
        }

    def _combined_series_pen(
        self,
        descriptors: list[ValidationFilterDescriptor],
        channel: str,
    ) -> QPen:
        states = [
            self._series_state(descriptor, channel)
            for descriptor in descriptors
        ]
        for state in (
            "ACTIVE",
            "ERROR",
            "NORMAL",
            "INACTIVE",
            "UNSUPPORTED",
        ):
            if state not in states:
                continue
            owner = next(
                descriptor
                for descriptor in descriptors
                if self._series_state(descriptor, channel) == state
            )
            pen = self._series_pen_for_state(
                owner,
                channel,
                state,
            )
            if any(self._descriptor_highlighted(descriptor)
                   for descriptor in descriptors):
                color = pen.color()
                color.setAlpha(255)
                pen.setColor(color)
                pen.setWidth(max(2, pen.width()))
            if any(
                self._lower_channel_role(descriptor, channel) is not None
                for descriptor in descriptors
            ):
                color = pen.color()
                color.setAlpha(255)
                pen.setColor(color)
                pen.setWidth(max(2, pen.width()))
            return pen
        return QPen(_ERROR_SERIES, 1, Qt.DotLine)

    def _draw_price_overlays(
        self,
        painter: QPainter,
        scale: _ValidationPriceScale,
    ) -> None:
        painter.save()
        price_plot_rect = self._price_plot_rect(scale)
        painter.setClipRect(price_plot_rect)
        for channel, values, owners in self._price_series_groups:
            self._draw_series_line(
                painter,
                values,
                scale,
                self._combined_series_pen(owners, channel),
            )
        painter.restore()

    @staticmethod
    def _legend_separator_layout(
        metrics: QFontMetrics,
        separator: str,
    ) -> tuple[str, float, float]:
        text = str(separator or "").strip() or str(separator or "")
        padding = 8.0
        return (
            text,
            padding,
            float(metrics.horizontalAdvance(text)) + padding * 2.0,
        )
    @staticmethod
    def _series_channel_pen_style(channel: str) -> Qt.PenStyle:
        normalized = str(channel or "").strip().upper()
        if normalized == "SIGNAL":
            return Qt.DashLine
        if normalized.startswith("CRITERION"):
            return Qt.DotLine
        return Qt.SolidLine
    def _series_pen_for_state(
        self,
        descriptor: ValidationFilterDescriptor,
        channel: str,
        state: str,
    ) -> QPen:
        normalized_state = str(state or "").strip().upper()
        if normalized_state == "ERROR":
            return QPen(_ERROR_SERIES, 1, Qt.DotLine)

        color = QColor(self._series_base_color(descriptor, channel))
        if normalized_state == "INACTIVE":
            color.setAlpha(170)
        elif normalized_state == "UNSUPPORTED":
            color.setAlpha(210)

        width = 2 if normalized_state == "ACTIVE" else 1
        return QPen(
            color,
            width,
            self._series_channel_pen_style(channel),
        )

    @staticmethod
    def _compact_lower_parameter(value: object, default: object) -> str:
        actual = default if value is None else value
        if isinstance(actual, float) and actual.is_integer():
            return str(int(actual))
        return str(actual)

    @staticmethod
    def _lower_parameter_sort_key(value: str) -> tuple[int, float | str]:
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (1, value)

    @staticmethod
    def _lower_filter_component_caption(
        descriptor: ValidationFilterDescriptor,
    ) -> str:
        caption = (
            IndicatorFollowSignalValidationChartCanvas
            ._descriptor_caption(descriptor)
        )
        if descriptor.family == FAMILY_RSI:
            if caption.startswith("RSI("):
                close = caption.find(")")
                if close >= 0:
                    return caption[close + 1:].strip()
            if caption.startswith("RSI "):
                return caption[4:].strip()
        if descriptor.family == FAMILY_MACD_SIGNAL:
            if caption == "MACD":
                return "MACD선"
            if caption == "시그널":
                return "시그널선"
            return caption.replace("MACD ", "MACD선 ").replace(
                "시그널 ", "시그널선 "
            )
        return caption

    @staticmethod
    def _lower_filter_component_order(
        descriptor: ValidationFilterDescriptor,
        original_index: int,
    ) -> tuple[int, int]:
        if descriptor.family != FAMILY_OCR_OSC:
            return (0, original_index)
        condition = descriptor.parameters.get("condition")
        condition = condition if isinstance(condition, Mapping) else {}
        operator = str(condition.get("operator") or "").strip().upper()
        threshold = condition.get("threshold")
        if threshold is not None and operator not in {"TURN_UP", "TURN_DOWN"}:
            return (0, original_index)
        if operator in {"TURN_UP", "TURN_DOWN"}:
            return (1, original_index)
        return (2, original_index)

    def _lower_filter_presentation_caption(
        self,
        presentation: ValidationLowerFilterPresentation,
        descriptor_by_identity: Mapping[str, ValidationFilterDescriptor],
    ) -> str:
        components = [
            (index, descriptor_by_identity[identity])
            for index, identity in enumerate(
                presentation.component_descriptor_identities
            )
            if identity in descriptor_by_identity
        ]
        components.sort(
            key=lambda item: self._lower_filter_component_order(
                item[1], item[0]
            )
        )
        captions = [
            self._lower_filter_component_caption(descriptor)
            for _index, descriptor in components
        ]
        if presentation.pane_family == FAMILY_OCR_OSC:
            captions = [
                (
                    caption
                    if index == 0 or not caption.startswith("OCR ")
                    else caption[4:].strip()
                )
                for index, caption in enumerate(captions)
            ]
        return " ".join(caption for caption in captions if caption)

    def _lower_bundle_title(
        self,
        pane_family: str,
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> str:
        if pane_family == FAMILY_RSI:
            periods = sorted({
                self._compact_lower_parameter(
                    descriptor.parameters.get("period"), 14
                )
                for descriptor in descriptors
                if descriptor.family == FAMILY_RSI
            }, key=self._lower_parameter_sort_key)
            return f"RSI({'/'.join(periods)})"

        signatures = sorted({
            (
                self._compact_lower_parameter(
                    descriptor.parameters.get("fast"), 12
                ),
                self._compact_lower_parameter(
                    descriptor.parameters.get("slow"), 26
                ),
                self._compact_lower_parameter(
                    descriptor.parameters.get("signal"), 9
                ),
            )
            for descriptor in descriptors
            if descriptor.family in {FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC}
        }, key=lambda signature: tuple(
            self._lower_parameter_sort_key(value)
            for value in signature
        ))
        return f"MACD({'/'.join(','.join(signature) for signature in signatures)})"

    def _presentation_lower_legend_blocks(
        self,
        side: str,
    ) -> list[dict[str, Any]] | None:
        presentations = self._lower_filter_presentations
        if presentations is None:
            return None

        descriptor_by_identity = {
            str(descriptor.identity or "").strip(): descriptor
            for descriptor in self._visualization_descriptors
            if descriptor.axis == LOWER_AXIS
            and str(descriptor.identity or "").strip()
        }
        grouped: dict[
            str,
            list[
                tuple[
                    ValidationLowerFilterPresentation,
                    tuple[str, ...],
                ]
            ],
        ] = {}
        for presentation in presentations:
            if str(presentation.side or "").strip().upper() != side:
                continue
            identities = tuple(
                identity
                for identity in presentation.descriptor_identities
                if identity in descriptor_by_identity
            )
            pane_families = {
                self._display_lower_family(
                    descriptor_by_identity[identity].family
                )
                for identity in identities
            }
            if len(pane_families) != 1:
                continue
            pane_family = next(iter(pane_families))
            if pane_family not in self._lower_families:
                continue
            grouped.setdefault(pane_family, []).append(
                (presentation, identities)
            )

        blocks: list[dict[str, Any]] = []
        for pane_family in self._lower_families:
            records = grouped.get(pane_family, ())
            if not records:
                continue
            identities: list[str] = []
            filter_captions: list[
                tuple[str, tuple[str, ...], bool, bool]
            ] = []
            for presentation, record_identities in records:
                for identity in record_identities:
                    if identity not in identities:
                        identities.append(identity)
                caption = self._lower_filter_presentation_caption(
                    presentation,
                    descriptor_by_identity,
                )
                if caption:
                    component_identities = tuple(
                        identity
                        for identity in (
                            presentation.component_descriptor_identities
                        )
                        if identity in descriptor_by_identity
                    )
                    filter_captions.append((
                        caption,
                        tuple(dict.fromkeys(component_identities)),
                        bool(component_identities) and all(
                            (side, identity) in self._not_filter_identities
                            for identity in component_identities
                        ),
                        bool(component_identities) and all(
                            (side, identity) in self._inner_not_filter_identities
                            for identity in component_identities
                        ),
                    ))
            if not identities or not filter_captions:
                continue
            pane_descriptors = tuple(
                descriptor_by_identity[identity]
                for identity in identities
            )
            title = self._lower_bundle_title(
                pane_family,
                pane_descriptors,
            )
            prefix = f"[ {title} : "
            caption = prefix + " | ".join(
                text
                for text, _identities, _is_not, _is_inner_not
                in filter_captions
            ) + " ]"
            not_text_spans: list[tuple[int, int]] = []
            inner_not_text_spans: list[tuple[int, int]] = []
            filter_text_spans: list[tuple[int, int, tuple[str, ...]]] = []
            offset = len(prefix)
            for index, (
                text, component_identities, is_not, is_inner_not,
            ) in enumerate(filter_captions):
                if index:
                    offset += len(" | ")
                filter_text_spans.append((
                    offset,
                    offset + len(text),
                    component_identities,
                ))
                if is_not:
                    not_text_spans.append((offset, offset + len(text)))
                if is_inner_not:
                    inner_not_text_spans.append((offset, offset + len(text)))
                offset += len(text)
            blocks.append({
                "caption": caption,
                "pens": (),
                "segments": (),
                "_channels": (),
                "_identities": tuple(identities),
                "legend_role": "LOWER_FILTER",
                "lower_descriptor_identity": identities[0],
                "not_text_spans": tuple(not_text_spans),
                "inner_not_text_spans": tuple(inner_not_text_spans),
                "filter_text_spans": tuple(filter_text_spans),
            })
        return blocks

    def _price_legend_blocks(
        self,
        side: str,
    ) -> list[dict[str, Any]]:
        normalized_side = str(side or "").strip().upper()

        blocks: list[dict[str, Any]] = []
        standard_by_caption: dict[str, dict[str, Any]] = {}
        arrangement_by_key: dict[tuple[str, ...], dict[str, Any]] = {}

        for descriptor in self._visualization_descriptors:
            if descriptor.axis != PRICE_AXIS:
                continue
            descriptor_sides = {
                str(value or "").strip().upper()
                for value in descriptor.sides
                if str(value or "").strip()
            }
            if normalized_side and normalized_side not in descriptor_sides:
                continue

            line_pens = self._descriptor_legend_line_pens(
                descriptor,
                normalized_side or None,
            )
            identity = str(descriptor.identity or "").strip()
            if descriptor.family == FAMILY_MA_ARRANGEMENT:
                segments: list[tuple[str, QPen]] = []
                for channel, pen in line_pens:
                    normalized_channel = str(channel or "").strip().upper()
                    if (
                        normalized_channel.startswith("MA")
                        and normalized_channel[2:].isdigit()
                    ):
                        label = f"{normalized_channel[2:]}이평"
                    else:
                        label = self._styled_descriptor_caption(descriptor)
                    segments.append((label, QPen(pen)))
                key = tuple(label for label, _pen in segments)
                block = arrangement_by_key.get(key)
                if segments and block is None:
                    block = {
                        "caption": "",
                        "pens": (),
                        "segments": tuple(segments),
                        "_channels": [
                            str(channel or "").strip().upper()
                            for channel, _pen in line_pens
                        ],
                        "_identities": [],
                    }
                    arrangement_by_key[key] = block
                    blocks.append(block)
                if (
                    block is not None
                    and identity not in block["_identities"]
                ):
                    block["_identities"].append(identity)
                continue

            caption = self._styled_descriptor_caption(descriptor)
            block = standard_by_caption.get(caption)
            if block is None:
                block = {
                    "caption": caption,
                    "pens": [],
                    "segments": (),
                    "_channels": set(),
                    "_identities": [],
                }
                standard_by_caption[caption] = block
                blocks.append(block)

            channels = block["_channels"]
            for channel, pen in line_pens:
                if channel in channels:
                    continue
                block["pens"].append(QPen(pen))
                channels.add(channel)
            if identity not in block["_identities"]:
                block["_identities"].append(identity)

        presentation_blocks = self._presentation_lower_legend_blocks(
            normalized_side
        )
        if presentation_blocks is not None:
            blocks.extend(presentation_blocks)
        else:
            for descriptor in self._visualization_descriptors:
                if descriptor.axis != LOWER_AXIS:
                    continue
                descriptor_sides = {
                    str(value or "").strip().upper()
                    for value in descriptor.sides
                    if str(value or "").strip()
                }
                if normalized_side and normalized_side not in descriptor_sides:
                    continue
                display_family = self._display_lower_family(descriptor.family)
                if display_family not in self._lower_families:
                    continue
                pane_index = self._lower_families.index(display_family) + 1
                identity = str(descriptor.identity or "").strip()
                blocks.append({
                    "caption": (
                        f"하단{pane_index} "
                        f"{self._styled_descriptor_caption(descriptor)}"
                    ).strip(),
                    "pens": (),
                    "segments": (),
                    "_channels": (),
                    "_identities": (identity,) if identity else (),
                    "legend_role": "LOWER_FILTER",
                    "lower_descriptor_identity": identity,
                })

        normalized_blocks: list[dict[str, Any]] = []
        for block in blocks:
            identities = tuple(
                identity
                for identity in block.get("_identities", ())
                if identity
            )
            caption = str(block.get("caption") or "")
            segments = tuple(block.get("segments") or ())
            identity_set = set(identities)
            legend_role = str(
                block.get("legend_role") or "PRICE_FILTER"
            )
            label_color = QColor(_LOWER_LABEL)
            if legend_role == "LOWER_FILTER":
                highlighted = any(
                    (normalized_side, identity)
                    in self._effective_lower_filter_identities()
                    for identity in identity_set
                )
                valid = any(
                    (normalized_side, identity)
                    in self._valid_lower_filter_identities
                    for identity in identity_set
                ) and self._valid_filter_mode_enabled
            elif normalized_side in {"BUY", "SELL"}:
                highlighted = any(
                    (normalized_side, identity)
                    in self._effective_price_filter_identities()
                    for identity in identity_set
                )
                valid = any(
                    (normalized_side, identity)
                    in self._valid_price_filter_identities
                    for identity in identity_set
                ) and self._valid_filter_mode_enabled
            else:
                highlighted = any(
                    (descriptor_side, identity)
                    in self._effective_price_filter_identities()
                    for descriptor_side in ("BUY", "SELL")
                    for identity in identity_set
                )
                valid = any(
                    (descriptor_side, identity)
                    in self._valid_price_filter_identities
                    for descriptor_side in ("BUY", "SELL")
                    for identity in identity_set
                ) and self._valid_filter_mode_enabled
            if (highlighted or valid) and not block.get("filter_text_spans"):
                label_color = QColor(_TEXT)
            elif (
                legend_role != "LOWER_FILTER"
                and identity_set
                and identity_set.issubset(
                    self._hidden_price_filter_identities
                )
            ):
                label_color.setAlpha(190)
            not_text = bool(identity_set) and all(
                (normalized_side, identity) in self._not_filter_identities
                for identity in identity_set
            )
            inner_not_text = bool(identity_set) and all(
                (normalized_side, identity) in self._inner_not_filter_identities
                for identity in identity_set
            )
            not_text_spans = tuple(block.get("not_text_spans") or ())
            inner_not_text_spans = tuple(
                block.get("inner_not_text_spans") or ()
            )
            filter_text_spans = tuple(block.get("filter_text_spans") or ())
            emphasized_text_spans = tuple(
                (int(start), int(end))
                for start, end, fragment_identities in filter_text_spans
                if any(
                    (normalized_side, identity)
                    in self._effective_lower_filter_identities()
                    for identity in fragment_identities
                )
            )
            if not_text and caption and legend_role != "LOWER_FILTER":
                not_text_spans = ((0, len(caption)),)
            if inner_not_text and caption and legend_role != "LOWER_FILTER":
                inner_not_text_spans = ((0, len(caption)),)
            normalized_blocks.append({
                "caption": caption,
                "pens": tuple(
                    QPen(pen)
                    for pen in (block.get("pens") or ())
                ),
                "segments": tuple(
                    (label, QPen(pen))
                    for label, pen in segments
                ),
                "descriptor_identities": identities,
                "channels": tuple(block.get("_channels") or ()),
                "label_color": QColor(label_color),
                "emphasized": bool(
                    (highlighted or valid) and not filter_text_spans
                ),
                "legend_role": str(
                    block.get("legend_role") or "PRICE_FILTER"
                ),
                "lower_descriptor_identity": str(
                    block.get("lower_descriptor_identity") or ""
                ),
                "not_text": bool(not_text and segments),
                "not_text_spans": not_text_spans,
                "inner_not_text": bool(inner_not_text and segments),
                "inner_not_text_spans": inner_not_text_spans,
                "filter_text_spans": filter_text_spans,
                "emphasized_text_spans": emphasized_text_spans,
            })
        return normalized_blocks
    def _price_legend_rows(
        self,
    ) -> list[
        tuple[
            str,
            str,
            list[dict[str, Any]],
        ]
    ]:
        rows = []
        for side, label in (
            ("BUY", "▪ 매수 :"),
            ("SELL", "▪ 매도 :"),
        ):
            blocks = self._price_legend_blocks(side)
            if blocks:
                rows.append((side, label, blocks))
        return rows
    @staticmethod
    def _layout_price_legend_blocks(
        blocks: list[dict[str, Any]],
        rect: QRectF,
        font: QFont,
    ) -> list[dict[str, Any]]:
        metrics = QFontMetrics(font)
        layouts: list[dict[str, Any]] = []
        left = float(rect.left())
        x = left
        top = float(rect.top())
        right = float(rect.right())
        line_gap = 2.0
        swatch_slot_width = 16.0
        internal_separator_width = float(metrics.horizontalAdvance(", "))

        def block_font(block: Mapping[str, Any]) -> QFont:
            resolved = QFont(font)
            resolved.setBold(bool(block.get("emphasized")))
            return resolved

        def overlaps(
            spans: tuple[tuple[int, int], ...],
            start: int,
            end: int,
        ) -> bool:
            return any(
                int(span_start) < end and int(span_end) > start
                for span_start, span_end in spans
            )

        def caption_runs(
            block: Mapping[str, Any],
            caption_text: str,
            caption_offset: int,
        ) -> list[dict[str, Any]]:
            caption_end = caption_offset + len(caption_text)
            emphasized_spans = tuple(
                block.get("emphasized_text_spans") or ()
            )
            not_spans = tuple(block.get("not_text_spans") or ())
            inner_not_spans = tuple(
                block.get("inner_not_text_spans") or ()
            )
            boundaries = {
                caption_offset,
                caption_end,
                *(
                    max(caption_offset, min(caption_end, int(value)))
                    for span in (
                        *emphasized_spans,
                        *not_spans,
                        *inner_not_spans,
                    )
                    for value in span[:2]
                ),
            }
            runs: list[dict[str, Any]] = []
            for start, end in zip(sorted(boundaries), sorted(boundaries)[1:]):
                if end <= start:
                    continue
                relative_start = start - caption_offset
                relative_end = end - caption_offset
                text = caption_text[relative_start:relative_end]
                emphasized = bool(block.get("emphasized")) or overlaps(
                    emphasized_spans, start, end,
                )
                run_font = QFont(font)
                run_font.setBold(emphasized)
                color = QColor(block.get("label_color") or _LOWER_LABEL)
                if overlaps(inner_not_spans, start, end):
                    color = QColor(_INNER_NOT_FILTER_TEXT)
                elif overlaps(not_spans, start, end):
                    color = QColor(_NOT_FILTER_TEXT)
                elif emphasized:
                    color = QColor(_TEXT)
                runs.append({
                    "start": start,
                    "end": end,
                    "text": text,
                    "font": run_font,
                    "color": color,
                    "width": float(QFontMetrics(run_font).horizontalAdvance(text)),
                })
            return runs

        def caption_width(
            block: Mapping[str, Any],
            caption_text: str,
            caption_offset: int,
        ) -> float:
            return sum(
                float(run["width"])
                for run in caption_runs(block, caption_text, caption_offset)
            )

        def text_chunks(
            text: str,
            width: float,
            text_metrics: QFontMetrics,
        ) -> list[str]:
            """Character-wrap rather than eliding a filter label."""
            if not text:
                return [""]
            limit = max(1, int(width))
            chunks: list[str] = []
            chunk = ""
            for character in text:
                candidate = chunk + character
                if chunk and text_metrics.horizontalAdvance(candidate) > limit:
                    chunks.append(chunk)
                    chunk = character
                else:
                    chunk = candidate
            if chunk:
                chunks.append(chunk)
            return chunks

        def caption_chunks(
            block: Mapping[str, Any],
            caption: str,
            width: float,
        ) -> list[tuple[str, int]]:
            if not caption:
                return [("", 0)]
            limit = max(1.0, float(width))
            chunks: list[tuple[str, int]] = []
            start = 0
            for end in range(1, len(caption) + 1):
                if (
                    end > start + 1
                    and caption_width(block, caption[start:end], start) > limit
                ):
                    chunks.append((caption[start:end - 1], start))
                    start = end - 1
            chunks.append((caption[start:], start))
            return chunks

        def next_line() -> None:
            nonlocal x, top
            x = left
            top += float(rect.height()) + line_gap

        def add_layout(
            block: dict[str, Any],
            *,
            segments: tuple[tuple[str, QPen | None], ...] = (),
            caption_text: str = "",
            caption_offset: int = 0,
            pens: tuple[QPen, ...] = (),
            separator: dict[str, Any] | None = None,
        ) -> None:
            nonlocal x
            text_metrics = QFontMetrics(block_font(block))
            if separator is None:
                prefix_width = 0.0
            elif "full_width" in separator:
                prefix_width = float(separator["full_width"])
            elif "rect" in separator:
                # Legacy separator records only retained the glyph rect.  Its
                # left offset from the current cursor is the leading padding.
                prefix_width = float(separator["rect"].width()) + 2.0 * max(
                    0.0,
                    float(separator["rect"].left()) - x,
                )
            else:
                prefix_width = float(metrics.horizontalAdvance(str(separator["text"])))
            runs = (
                caption_runs(block, caption_text, caption_offset)
                if not segments else []
            )
            content_width = (
                sum(
                    (swatch_slot_width if pen is not None else 0.0)
                    + float(text_metrics.horizontalAdvance(label))
                    for label, pen in segments
                )
                + max(0, len(segments) - 1) * internal_separator_width
                if segments else
                len(pens) * swatch_slot_width
                + sum(float(run["width"]) for run in runs)
            )
            content_left = x + prefix_width
            text_left = content_left + len(pens) * swatch_slot_width

            def advance_to(position: int) -> float:
                advance = 0.0
                for run in runs:
                    start = int(run["start"])
                    end = int(run["end"])
                    if position >= end:
                        advance += float(run["width"])
                        continue
                    if position > start:
                        advance += float(QFontMetrics(run["font"]).horizontalAdvance(
                            str(run["text"])[:position - start]
                        ))
                    break
                return advance

            fragment_targets: list[tuple[QRectF, tuple[str, ...]]] = []
            caption_end = caption_offset + len(caption_text)
            for start, end, identities in block.get("filter_text_spans") or ():
                target_start = max(caption_offset, int(start))
                target_end = min(caption_end, int(end))
                if target_end <= target_start:
                    continue
                target_left = text_left + advance_to(target_start)
                target_right = text_left + advance_to(target_end)
                if target_right > target_left:
                    fragment_targets.append((
                        QRectF(
                            target_left,
                            top,
                            target_right - target_left,
                            rect.height(),
                        ),
                        tuple(identities),
                    ))
            layouts.append({
                "block": block,
                "rect": QRectF(content_left, top, content_width, rect.height()),
                "separator": separator,
                "caption_text": caption_text,
                "caption_offset": caption_offset,
                "segments": segments,
                "pens": pens,
                "font": block_font(block),
                "caption_runs": tuple(runs),
                "fragment_targets": tuple(fragment_targets),
            })
            x += prefix_width + content_width

        for block in blocks:
            separator = None
            if x > left:
                text, padding, full_width = (
                    IndicatorFollowSignalValidationChartCanvas._legend_separator_layout(
                        metrics,
                        "  |  ",
                    )
                )
                separator = {
                    "text": text,
                    "full_width": float(full_width),
                    "rect": QRectF(
                        x + padding,
                        top,
                        metrics.horizontalAdvance(text),
                        rect.height(),
                    ),
                }

            segments = tuple(block.get("segments") or ())
            text_metrics = QFontMetrics(block_font(block))
            if segments:
                pieces: list[tuple[str, QPen | None]] = []
                for segment_index, (label, pen) in enumerate(segments):
                    chunks = text_chunks(
                        (", " if segment_index else "") + str(label),
                        float(rect.width()) - swatch_slot_width,
                        text_metrics,
                    )
                    pieces.extend(
                        (
                            chunk,
                            pen if index == 0 else None,
                        )
                        for index, chunk in enumerate(chunks)
                    )
            else:
                caption = str(block.get("caption") or "")
                pens = tuple(block.get("pens") or ())
                pieces = []
                for index, (chunk, offset) in enumerate(caption_chunks(
                    block,
                    caption,
                    float(rect.width()) - len(pens) * swatch_slot_width,
                )):
                    pieces.append((chunk, pens if index == 0 else (), offset))

            first_piece = True
            for piece in pieces:
                if segments:
                    label, pen = piece
                    width = swatch_slot_width * (pen is not None) + float(text_metrics.horizontalAdvance(label))
                else:
                    label, piece_pens, caption_offset = piece
                    width = (
                        len(piece_pens) * swatch_slot_width
                        + caption_width(block, label, caption_offset)
                    )
                separator_width = (
                    float(separator.get("full_width", metrics.horizontalAdvance(
                        str(separator["text"]),
                    )))
                    if first_piece and separator is not None else 0.0
                )
                if x > left and x + separator_width + width > right:
                    next_line()
                    separator = None
                if segments:
                    add_layout(
                        block,
                        segments=((label, pen),),
                        separator=separator if first_piece else None,
                    )
                else:
                    add_layout(
                        block,
                        caption_text=label,
                        caption_offset=caption_offset,
                        pens=piece_pens,
                        separator=separator if first_piece else None,
                    )
                first_piece = False
        return layouts
    def _price_legend_layout_at(
        self,
        rows,
        font: QFont,
        legend_top: float,
    ) -> list[dict[str, Any]]:
        metrics = QFontMetrics(font)
        emphasized_font = QFont(font)
        emphasized_font.setBold(True)
        line_height = max(metrics.height(), QFontMetrics(emphasized_font).height()) + 2
        row_gap = 2.0
        prefix_gap = 8.0
        left = float(self.plot_left + 4)
        right = float(self.plot_left + self._plot_width() - 4)
        layouts: list[dict[str, Any]] = []
        top = legend_top
        for side, prefix, blocks in rows:
            prefix_width = float(metrics.horizontalAdvance(prefix))
            blocks_left = left + prefix_width + prefix_gap
            if blocks_left >= right:
                break
            block_layouts = self._layout_price_legend_blocks(
                blocks,
                QRectF(
                    blocks_left,
                    top,
                    max(1.0, right - blocks_left),
                    line_height,
                ),
                font,
            )
            prefix_rect = QRectF(left, top, prefix_width, line_height)
            layouts.append({
                "side": side,
                "prefix": prefix,
                "prefix_rect": prefix_rect,
                "blocks": block_layouts,
            })
            row_bottom = max(
                [prefix_rect.bottom()]
                + [layout["rect"].bottom() for layout in block_layouts]
            )
            top = row_bottom + row_gap
        return layouts

    def _price_legend_required_height(self, font) -> int:
        rows = self._price_legend_rows()
        if not rows:
            return 0
        metrics = QFontMetrics(font)
        layouts = self._price_legend_layout_at(
            rows,
            font,
            float(metrics.height()),
        )
        return int(math.ceil(max(
            max(
                [row["prefix_rect"].bottom()]
                + [layout["rect"].bottom() for layout in row["blocks"]]
            )
            for row in layouts
        ))) if layouts else 0

    def _price_plot_top_margin(self) -> int:
        return max(self._TOP, self._price_legend_required_height(self.font()))

    def _price_legend_layout(
        self,
        scale: _ValidationPriceScale,
        font,
    ) -> list[dict[str, Any]]:
        rows = self._price_legend_rows()
        if not rows:
            return []
        layouts = self._price_legend_layout_at(
            rows,
            font,
            float(QFontMetrics(font).height()),
        )
        if not layouts:
            return []
        top = min(row["prefix_rect"].top() for row in layouts)
        bottom = max(
            max(
                [row["prefix_rect"].bottom()]
                + [layout["rect"].bottom() for layout in row["blocks"]]
            )
            for row in layouts
        )
        if bottom > float(scale.plot_top):
            layouts = self._price_legend_layout_at(
                rows,
                font,
                max(0.0, float(scale.plot_top) - (bottom - top)),
            )
        return layouts
    @staticmethod
    def _draw_price_legend_block_layout(
        painter: QPainter,
        layouts: list[dict[str, Any]],
    ) -> None:
        swatch_width = 12.0
        swatch_gap = 4.0
        internal_separator = ", "
        for layout in layouts:
            painter.save()
            separator = layout.get("separator")
            if separator is not None:
                painter.setPen(_LOWER_LABEL)
                painter.drawText(
                    separator["rect"],
                    Qt.AlignLeft | Qt.AlignVCenter,
                    separator["text"],
                )

            block = layout["block"]
            rect = layout["rect"]
            x = float(rect.left())
            center_y = float(rect.center().y())
            label_color = QColor(
                block.get("label_color") or _LOWER_LABEL
            )
            font = QFont(layout.get("font") or painter.font())
            painter.setFont(font)
            metrics = QFontMetrics(font)
            segments = tuple(
                layout["segments"]
                if "segments" in layout
                else block.get("segments") or ()
            )
            if segments:
                for segment_index, (label, pen) in enumerate(segments):
                    if segment_index:
                        width = metrics.horizontalAdvance(internal_separator)
                        painter.setPen(label_color)
                        painter.drawText(
                            QRectF(x, rect.top(), width, rect.height()),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            internal_separator,
                        )
                        x += width
                    if pen is not None:
                        painter.setPen(pen)
                        painter.drawLine(
                            QPointF(x, center_y),
                            QPointF(x + swatch_width, center_y),
                        )
                        x += swatch_width + swatch_gap
                    width = metrics.horizontalAdvance(label)
                    painter.setPen(
                        _INNER_NOT_FILTER_TEXT
                        if block.get("inner_not_text")
                        else (
                            _NOT_FILTER_TEXT
                            if block.get("not_text")
                            else label_color
                        )
                    )
                    painter.drawText(
                        QRectF(x, rect.top(), width, rect.height()),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        label,
                    )
                    x += width
                painter.restore()
                continue

            for pen in tuple(
                layout["pens"] if "pens" in layout else block.get("pens") or ()
            ):
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(x, center_y),
                    QPointF(x + swatch_width, center_y),
                )
                x += swatch_width + swatch_gap
            runs = tuple(layout.get("caption_runs") or ())
            if not runs:
                caption = str(layout["caption_text"])
                runs = ({
                    "text": caption,
                    "font": font,
                    "color": label_color,
                    "width": float(metrics.horizontalAdvance(caption)),
                },)
            for run in runs:
                run_font = QFont(run["font"])
                width = float(run["width"])
                painter.setFont(run_font)
                painter.setPen(QColor(run["color"]))
                painter.drawText(
                    QRectF(x, rect.top(), width, rect.height()),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    str(run["text"]),
                )
                x += width
            painter.restore()

    def _draw_price_overlay_legend(
        self,
        painter: QPainter,
        scale: _ValidationPriceScale,
    ) -> None:
        for row in self._price_legend_layout(scale, painter.font()):
            painter.setPen(_LOWER_LABEL)
            painter.drawText(
                row["prefix_rect"],
                Qt.AlignLeft | Qt.AlignVCenter,
                row["prefix"],
            )
            self._draw_price_legend_block_layout(
                painter,
                row["blocks"],
            )

    @staticmethod
    def _reference_side_label(side: str) -> str:
        return {
            "BUY": "매수",
            "SELL": "매도",
        }.get(str(side or "").strip().upper(), str(side or "").strip())

    def lower_transition_line_records(
        self,
        family: str,
    ) -> list[dict[str, Any]]:
        visible_start, visible_end = self._visible_index_bounds()
        grouped: dict[
            tuple[int, str, str],
            dict[str, Any],
        ] = {}

        for descriptor in self._visualization_descriptors:
            if descriptor.axis != LOWER_AXIS:
                continue
            if self._display_lower_family(descriptor.family) != family:
                continue
            if (
                self._lower_visibility_filter_active()
                and not self._lower_descriptor_highlighted(descriptor)
            ):
                continue
            condition = descriptor.parameters.get("condition")
            if not isinstance(condition, Mapping):
                continue
            operator = str(condition.get("operator") or "").strip().upper()
            if operator not in {
                "TURN_UP",
                "TURN_DOWN",
                "ZERO_CROSS_UP",
                "ZERO_CROSS_DOWN",
            }:
                continue

            target = str(condition.get("target") or "").strip().upper()
            if target == "OCR":
                target = "OSC"
            if not target:
                target = (
                    "OSC"
                    if descriptor.family == FAMILY_OCR_OSC
                    else "MACD"
                )
            values = self._cached_values(descriptor, target)
            if len(values) != len(self._candles):
                continue

            direction = "UP" if operator.endswith("_UP") else "DOWN"
            render_mode = "TRANSITION_VERTICAL_LINE"
            # TURN at the left boundary needs its two preceding values.
            scan_start = max(0, visible_start - 2)
            scan_end = min(len(values), visible_end + 1)
            for offset in indicator_transition_indexes(values[scan_start:scan_end], operator):
                index = scan_start + offset
                if not visible_start <= index < visible_end:
                    continue
                marker_value = _finite_number(values[index])
                key = (int(index), direction, render_mode)
                record = grouped.setdefault(
                    key,
                    {
                        "family": family,
                        "index": int(index),
                        "x": self._x_for_index(int(index)),
                        "direction": direction,
                        "render_mode": render_mode,
                        "value": marker_value,
                        "operators": set(),
                        "sides": set(),
                        "descriptor_identities": set(),
                    },
                )
                record["operators"].add(operator)
                record["sides"].update(
                    str(side or "").strip().upper()
                    for side in descriptor.sides
                    if str(side or "").strip()
                )
                identity = str(descriptor.identity or "").strip()
                if identity:
                    record["descriptor_identities"].add(identity)

        result: list[dict[str, Any]] = []
        for key in sorted(grouped):
            record = grouped[key]
            result.append({
                **record,
                "operators": tuple(sorted(record["operators"])),
                "sides": tuple(sorted(record["sides"])),
                "descriptor_identities": tuple(
                    sorted(record["descriptor_identities"])
                ),
            })
        return result

    def lower_reference_line_records(
        self,
        family: str,
        scale: _ValidationPriceScale,
    ) -> list[dict[str, Any]]:
        grouped: dict[float, dict[str, Any]] = {}
        visible_start, visible_end = self._visible_index_bounds()

        for descriptor in self._visualization_descriptors:
            if descriptor.axis != LOWER_AXIS:
                continue
            if self._display_lower_family(descriptor.family) != family:
                continue
            if (
                self._lower_visibility_filter_active()
                and not self._lower_descriptor_highlighted(descriptor)
            ):
                continue

            descriptor_sides = tuple(
                str(side or "").strip().upper()
                for side in descriptor.sides
                if str(side or "").strip()
            )
            values_to_record: list[float] = []
            for channel in descriptor.series_keys:
                if not str(channel).upper().startswith("CRITERION"):
                    continue
                values = self._cached_values(descriptor, channel)
                if len(values) != len(self._candles):
                    continue
                value = next(
                    (
                        number
                        for raw in values[visible_start:visible_end]
                        if (number := _finite_number(raw)) is not None
                    ),
                    None,
                )
                if value is not None:
                    values_to_record.append(value)

            for value in values_to_record:
                key = round(float(value), 12)
                bucket = grouped.setdefault(
                    key,
                    {
                        "selected_sides": set(),
                    },
                )
                bucket["selected_sides"].update(descriptor_sides)

        records: list[dict[str, Any]] = []
        for value in sorted(grouped):
            bucket = grouped[value]
            selected = set(bucket["selected_sides"])
            sides = tuple(
                side
                for side in ("SELL", "BUY")
                if side in selected
            ) + tuple(sorted(selected - {"BUY", "SELL"}))
            value_text = self._crosshair_number_text(value)
            labels = [
                f"{self._reference_side_label(side)} {value_text}"
                for side in sides
                if self._reference_side_label(side)
            ]
            if labels:
                text = "\n".join(labels)
            else:
                text = value_text
            records.append({
                "family": family,
                "value": float(value),
                "y": scale.y_for_price(float(value)),
                "sides": sides,
                "text": text,
            })
        return records

    def lower_reference_axis_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for pane in self.lower_pane_records():
            family = str(pane["family"])
            scale = self._lower_scale(
                family,
                int(pane["top"]),
                int(pane["bottom"]),
            )
            if scale is None:
                continue
            records.extend(self.lower_reference_line_records(family, scale))
        return records

    def _draw_lower_panes(self, painter: QPainter) -> None:
        if not self._lower_families:
            return
        for pane in self.lower_pane_records():
            family = pane["family"]
            top = int(pane["top"])
            bottom = int(pane["bottom"])
            painter.setPen(QPen(_LOWER_SEPARATOR, 1))
            painter.drawLine(
                self.plot_left,
                top,
                self.width() - self._RIGHT,
                top,
            )
            scale = self._lower_scale(family, top, bottom)
            if scale is None:
                continue
            if family == FAMILY_OCR_OSC:
                zero_y = int(scale.y_for_price(0.0))
                painter.setPen(QPen(_GRID, 1, Qt.DashLine))
                painter.drawLine(
                    self.plot_left,
                    zero_y,
                    self.width() - self._RIGHT,
                    zero_y,
                )
            painter.save()
            painter.setClipRect(QRectF(
                self.plot_left,
                scale.plot_top,
                max(1, self.width() - self._RIGHT - self.plot_left),
                max(1, scale.plot_bottom - scale.plot_top),
            ))
            visible_start, visible_end = self._visible_index_bounds()
            drawn_criterion_series: dict[
                float, set[tuple[float | None, ...]]
            ] = {}
            for channel, values, owners in self._lower_series_groups.get(
                family,
                (),
            ):
                visible_owners = self._visible_lower_series_owners(channel, owners)
                if not visible_owners:
                    continue
                criterion_value = None
                criterion_series = None
                if str(channel).upper().startswith("CRITERION"):
                    criterion_series = tuple(
                        round(float(number), 12)
                        if (number := _finite_number(raw)) is not None
                        else None
                        for raw in values[visible_start:visible_end]
                    )
                    visible_values = {
                        number for number in criterion_series
                        if number is not None
                    }
                    if len(visible_values) == 1:
                        criterion_value = next(iter(visible_values))
                        if criterion_series in drawn_criterion_series.get(
                            criterion_value, set(),
                        ):
                            continue
                pen = self._combined_series_pen(list(visible_owners), channel)
                if family == FAMILY_OCR_OSC and channel == "OSC":
                    self._draw_series_histogram(
                        painter,
                        values,
                        scale,
                        pen,
                        self._lower_histogram_evidence_owners(
                            family, visible_owners,
                        ),
                    )
                else:
                    self._draw_series_line(
                        painter,
                        values,
                        scale,
                        pen,
                    )
                if criterion_value is not None:
                    drawn_criterion_series.setdefault(
                        criterion_value, set(),
                    ).add(criterion_series)

            painter.restore()

    def _draw_crosshair(
        self,
        painter: QPainter,
        price_scale: _ValidationPriceScale,
    ) -> None:
        record = self.crosshair_records()
        if not record.get("visible"):
            return
        x = record.get("x")
        if x is None:
            return

        vertical_bottom = (
            self.height() - self._BOTTOM
            if self._lower_families
            else price_scale.plot_bottom
        )
        painter.setPen(QPen(_CROSSHAIR, 1, Qt.DashLine))
        painter.drawLine(
            QPointF(float(x), float(price_scale.plot_top)),
            QPointF(float(x), float(vertical_bottom)),
        )

        horizontal = record.get("horizontal")
        if isinstance(horizontal, Mapping):
            horizontal_y = _finite_number(horizontal.get("y"))
            if horizontal_y is not None:
                painter.drawLine(
                    QPointF(float(self.plot_left), horizontal_y),
                    QPointF(float(self.width() - self._RIGHT), horizontal_y),
                )
                value_text = self._crosshair_number_text(
                    horizontal.get("value")
                )
                if value_text != "-":
                    metrics = QFontMetrics(painter.font())
                    label_width = metrics.horizontalAdvance(value_text) + 10
                    label_height = metrics.height() + 4
                    label_left = max(
                        self.plot_left,
                        self.width() - self._RIGHT - label_width,
                    )
                    painter.fillRect(
                        QRectF(
                            label_left,
                            horizontal_y - label_height / 2,
                            label_width,
                            label_height,
                        ),
                        QBrush(_CROSSHAIR_BOX),
                    )
                    painter.setPen(_CROSSHAIR_TEXT)
                    painter.drawText(
                        QRectF(
                            label_left + 4,
                            horizontal_y - label_height / 2,
                            label_width - 8,
                            label_height,
                        ),
                        Qt.AlignRight | Qt.AlignVCenter,
                        value_text,
                    )


    def day_boundary_records(self) -> list[dict[str, Any]]:
        if not self._is_minute_timeframe or len(self._candles) < 2:
            return []
        visible_start, visible_end = self._visible_index_bounds()
        records: list[dict[str, Any]] = []
        plot_right = float(self.width() - self._RIGHT)
        first = max(1, visible_start)
        previous_time = _parse_candle_time(self._candles[first - 1].get("time"))
        for index in range(first, visible_end):
            current_time = _parse_candle_time(self._candles[index].get("time"))
            prior_time, previous_time = previous_time, current_time
            if (
                prior_time is None
                or current_time is None
                or prior_time.date() == current_time.date()
            ):
                continue
            x = (self._x_for_index(index - 1) + self._x_for_index(index)) / 2.0
            if float(self.plot_left) <= x <= plot_right:
                records.append({
                    "index": index,
                    "x": x,
                    "date": current_time.strftime("%Y%m%d"),
                })
        return records

    @_perf_stage('canvas.static')
    def _paint_static_chart(
        self,
        painter: QPainter,
        scale: _ValidationPriceScale | None,
    ) -> None:
        painter.fillRect(self.rect(), _BACKGROUND)
        if not self._candles:
            painter.setPen(_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter, "표시할 Candle이 없습니다.")
            return
        if scale is None:
            painter.setPen(_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter, "표시할 가격이 없습니다.")
            return

        painter.setPen(QPen(_GRID, 1, Qt.DashLine))
        for record in scale.tick_records():
            painter.drawLine(
                self.plot_left,
                record["y"],
                self.width() - self._RIGHT,
                record["y"],
            )

        day_boundary_bottom = max(
            int(scale.plot_top),
            self.height() - self._BOTTOM,
        )
        painter.setPen(QPen(_DAY_BOUNDARY, 1, Qt.SolidLine))
        for record in self.day_boundary_records():
            x = float(record["x"])
            painter.drawLine(
                QPointF(x, float(scale.plot_top)),
                QPointF(x, float(day_boundary_bottom)),
            )

        if self._validation_range is not None:
            range_start, range_end = self._validation_range
            visible_start, visible_end = self._visible_index_bounds()
            clipped_start = max(range_start, visible_start)
            clipped_end = min(range_end, visible_end - 1)
            if clipped_start <= clipped_end:
                slot_width = self.pixels_per_candle
                left = self._x_for_index(clipped_start) - slot_width / 2
                right = self._x_for_index(clipped_end) + slot_width / 2
                painter.fillRect(
                    QRectF(
                        left,
                        0,
                        max(1.0, right - left),
                        self.height(),
                    ),
                    QBrush(_VALIDATION_RANGE_SELECTION),
                )
                painter.setPen(QPen(_VALIDATION_RANGE_LINE, 1))
                if range_start >= visible_start:
                    start_x = self._x_for_index(range_start)
                    painter.drawLine(
                        int(start_x),
                        0,
                        int(start_x),
                        self.height(),
                    )
                if range_end < visible_end:
                    end_x = self._x_for_index(range_end)
                    painter.drawLine(
                        int(end_x),
                        0,
                        int(end_x),
                        self.height(),
                    )

        self._draw_price_overlays(painter, scale)

        visible_start, visible_end = self._visible_index_bounds()
        body_width = self._candle_body_width()
        half_body = body_width / 2
        painter.save()
        price_plot_rect = self._price_plot_rect(scale)
        painter.setClipRect(price_plot_rect)
        pixels = self.pixels_per_candle
        up_pen, down_pen, neutral_pen = (QPen(color, 1) for color in (_UP, _DOWN, _FLAT))
        up_brush, down_brush, neutral_brush = (QBrush(color) for color in (_UP, _DOWN, _FLAT))
        flat_pen = QPen(_FLAT, 2)
        # Keep y_for_price arithmetic order exact while hoisting invariant bounds.
        price_maximum = scale.maximum
        price_span = scale.maximum - scale.minimum
        plot_top = scale.plot_top
        plot_height = scale.plot_bottom - scale.plot_top
        left = self._LEFT
        view_start = self._visible_start_index
        set_pen, draw_line, fill_rect = painter.setPen, painter.drawLine, painter.fillRect
        finite_number = _finite_number
        for index in range(visible_start, visible_end):
            candle = self._candles[index]
            close = finite_number(candle.get("close"))
            if close is None:
                continue
            x = left + ((float(index) - view_start) + 0.5) * pixels
            opened = finite_number(candle.get("open"))
            high = finite_number(candle.get("high"))
            low = finite_number(candle.get("low"))
            if opened is None or high is None or low is None:
                set_pen(flat_pen)
                y = plot_top + (price_maximum - close) / price_span * plot_height
                draw_line(
                    QPointF(x - half_body, y),
                    QPointF(x + half_body, y),
                )
                continue
            if close > opened:
                candle_pen, candle_brush = up_pen, up_brush
            elif close < opened:
                candle_pen, candle_brush = down_pen, down_brush
            else:
                candle_pen, candle_brush = neutral_pen, neutral_brush
            set_pen(candle_pen)
            draw_line(
                int(x),
                int(plot_top + (price_maximum - high) / price_span * plot_height),
                int(x),
                int(plot_top + (price_maximum - low) / price_span * plot_height),
            )
            open_y = plot_top + (price_maximum - opened) / price_span * plot_height
            close_y = plot_top + (price_maximum - close) / price_span * plot_height
            body_top = min(open_y, close_y)
            body_height = max(
                abs(open_y - close_y),
                1.0,
            )
            fill_rect(
                QRectF(x - half_body, body_top, body_width, body_height),
                candle_brush,
            )
        painter.restore()

        painter.save()
        painter.setClipRect(price_plot_rect)
        for record in self.completed_cycle_average_line_records(scale):
            color = self._completed_cycle_render_color(record["cycle_number"])
            painter.setPen(QPen(color, 2.0, Qt.DashLine))
            painter.drawLine(
                QPointF(float(record["x1"]), float(record["y"])),
                QPointF(float(record["x2"]), float(record["y"])),
            )
        painter.restore()

        self._draw_price_overlay_legend(painter, scale)

        painter.save()
        painter.setClipRect(price_plot_rect)
        marker_shapes = {
            "BUY": QPolygonF([QPointF(0, -8), QPointF(-5, 2), QPointF(5, 2)]),
            "SELL": QPolygonF([QPointF(0, 8), QPointF(-5, -2), QPointF(5, -2)]),
        }
        marker_pens = {side: QPen(color, 1) for side, color in (("BUY", _BUY), ("SELL", _SELL))}
        marker_brushes = {side: QBrush(color) for side, color in (("BUY", _BUY), ("SELL", _SELL))}
        for marker in self._visible_marker_records():
            index = marker.get("evaluation_index")
            side = marker.get("side")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(self._candles):
                continue
            x = left + ((float(index) - view_start) + 0.5) * pixels
            y = self._marker_y(index, str(side or ""), scale)
            if y is None:
                continue
            if side == "BUY":
                marker_side = "BUY"
            elif side == "SELL":
                marker_side = "SELL"
            else:
                continue
            painter.setPen(marker_pens[marker_side])
            painter.setBrush(marker_brushes[marker_side])
            painter.drawPolygon(marker_shapes[marker_side].translated(x, y))
        painter.restore()

        self._draw_completed_cycle_average_labels(painter, scale)

        self._draw_lower_panes(painter)

        time_axis_records = self.time_axis_records()
        if not time_axis_records:
            return
        painter.setPen(_TEXT)
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        label_top = max(0, self.height() - 32)
        for record in time_axis_records:
            center_x = self._x_for_index(record["index"])
            painter.drawText(
                QRectF(center_x - 42, label_top, 84, 26),
                Qt.AlignHCenter | Qt.AlignTop,
                record["label"],
            )


    def _static_chart_state_key(self) -> tuple[Any, ...]:
        return (
            self._timeframe_key,
            self.width(),
            self.height(),
            round(self._visible_start_index, 8),
            round(self._visible_candle_span, 8),
            self._price_minimum,
            self._price_maximum,
            self._selected_index,
            self._validation_range,
            self._cycle_label_preview_active,
            self.effective_marker_key,
            self.font().toString(),
        )

    @_perf_stage('canvas.paint')
    def paintEvent(self, event) -> None:
        owner = self._input_frame_owner
        painter = None
        try:
            # A paint can arrive before the zero-delay wheel timer. Converge
            # ordered input math before rebuilding the existing raster once.
            flush_wheel = getattr(owner, "_flush_pending_wheel_zoom", None)
            if callable(flush_wheel):
                flush_wheel()
            scale = self.price_scale()
            cache_key = self._static_chart_state_key()
            if (
                self._static_chart_cache is None
                or self._static_chart_cache_key != cache_key
                or self._static_chart_cache.size() != self.size()
            ):
                cache = QPixmap(self.size())
                cache.fill(_BACKGROUND)
                cache_painter = QPainter(cache)
                try:
                    cache_painter.setRenderHint(QPainter.Antialiasing, True)
                    self._paint_static_chart(cache_painter, scale)
                finally:
                    cache_painter.end()
                self._static_chart_cache = cache
                self._static_chart_cache_key = cache_key

            painter = QPainter(self)
            if self._static_chart_cache is not None:
                painter.drawPixmap(0, 0, self._static_chart_cache)
                preview_offset_x = self._time_pan_preview_offset_x
                if preview_offset_x:
                    content_rect = QRectF(
                        float(self._LEFT),
                        0.0,
                        self._plot_width(),
                        float(self.height()),
                    )
                    translated_rect = content_rect.translated(preview_offset_x, 0.0)
                    painter.save()
                    painter.setClipRect(content_rect)
                    painter.fillRect(content_rect, _BACKGROUND)
                    painter.drawPixmap(
                        translated_rect,
                        self._static_chart_cache,
                        content_rect,
                    )
                    painter.restore()
            if self._candles and scale is not None:
                painter.setRenderHint(QPainter.Antialiasing, True)
                self._draw_crosshair(painter, scale)
        except Exception:
            cancel_frame = getattr(owner, "_cancel_chart_input_frame", None)
            if callable(cancel_frame):
                cancel_frame()
            raise
        finally:
            if painter is not None and painter.isActive():
                painter.end()
        # Count the quiet period from a completed admitted frame, not its start.
        if (self._cycle_label_preview_active
                and getattr(owner, "_chart_input_frame_active", False)
                and getattr(owner, "_chart_input_frame_canvas", None) is self):
            self._cycle_label_settle_timer.start(180)
        frame_painted = getattr(owner, "_chart_input_frame_painted", None)
        if callable(frame_painted):
            frame_painted(self)


class IndicatorFollowSignalValidationFixedPriceAxis(QWidget):
    """V2-only price labels fixed outside the horizontal scroll viewport."""

    price_scale_wheel_requested = pyqtSignal(float, int)

    _HORIZONTAL_PADDING = 8
    _FIXED_WIDTH_REFERENCE_PRICE = "9,999,999,999"
    _REFERENCE_COLUMN_GAP = 4

    def __init__(self, scroll_area: QScrollArea, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll_area = scroll_area
        self._canvas: IndicatorFollowSignalValidationChartCanvas | None = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        # Size once from a fixed price example; visible reference labels must
        # never resize the candle viewport as filters or selection change.
        self._fixed_axis_width = self._fixed_width_for_font()
        self._scroll_area.viewport().installEventFilter(self)
        self._sync_width()

    def _fixed_width_for_font(self) -> int:
        return (
            QFontMetrics(self.font()).horizontalAdvance(self._FIXED_WIDTH_REFERENCE_PRICE)
            + self._HORIZONTAL_PADDING * 2
        )

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.FontChange:
            self._fixed_axis_width = self._fixed_width_for_font()
            self._sync_width()
            self.update()
        super().changeEvent(event)

    def set_canvas(
        self,
        canvas: IndicatorFollowSignalValidationChartCanvas | None,
    ) -> None:
        if self._canvas is not None:
            self._canvas.removeEventFilter(self)
        self._canvas = canvas
        if canvas is not None:
            canvas.installEventFilter(self)
        self._sync_width()
        self.update()

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Resize, QEvent.Show, QEvent.LayoutRequest}:
            self.update()
        return super().eventFilter(watched, event)


    def _sync_width(self) -> None:
        if self.minimumWidth() != self._fixed_axis_width or self.maximumWidth() != self._fixed_axis_width:
            self.setFixedWidth(self._fixed_axis_width)

    def refresh_scale(self) -> None:
        self._sync_width()
        self.update()

    def price_axis_records(self) -> list[dict[str, Any]]:
        if self._canvas is None:
            return []
        viewport_top = self._scroll_area.viewport().geometry().top()
        return [
            {
                **record,
                "label": _validation_price_text(record["price"]),
                "y": viewport_top + record["y"],
            }
            for record in self._canvas.grid_line_records()
        ]

    def lower_reference_axis_records(self) -> list[dict[str, Any]]:
        if self._canvas is None:
            return []
        viewport_top = self._scroll_area.viewport().geometry().top()
        return [
            {
                **record,
                "y": viewport_top + float(record["y"]),
            }
            for record in self._canvas.lower_reference_axis_records()
        ]


    def _lower_reference_text_layout(
        self,
        metrics: QFontMetrics,
        records: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if self._canvas is None:
            return []
        source_records = (
            self.lower_reference_axis_records()
            if records is None
            else records
        )
        viewport_top = float(self._scroll_area.viewport().geometry().top())
        panes = {
            str(pane["family"]): (
                viewport_top + float(pane["top"]),
                viewport_top + float(pane["bottom"]),
            )
            for pane in self._canvas.lower_pane_records()
        }
        available_width = float(
            max(1, self.width() - self._HORIZONTAL_PADDING * 2)
        )
        line_height = float(metrics.height())
        layouts: list[dict[str, Any]] = []

        for family, (pane_top, pane_bottom) in panes.items():
            blocks: list[dict[str, Any]] = []
            for order, record in enumerate(source_records):
                if str(record.get("family") or "") != family:
                    continue
                lines = str(record["text"]).splitlines() or [""]
                height = line_height * len(lines)
                blocks.append({
                    "order": order,
                    "record": record,
                    "lines": lines,
                    "height": height,
                    "desired_top": float(record["y"]) - height / 2.0,
                    "text_width": max(
                        float(metrics.horizontalAdvance(line))
                        for line in lines
                    ),
                })
            if not blocks:
                continue

            column_count = 1
            column_width = available_width
            block_columns = {int(block["order"]): 0 for block in blocks}
            ordered = sorted(
                blocks,
                key=lambda block: (
                    float(block["desired_top"]), int(block["order"]),
                ),
            )
            has_collision = any(
                float(first["desired_top"]) + float(first["height"])
                > float(second["desired_top"])
                for first, second in zip(ordered, ordered[1:])
            )
            two_column_width = (
                available_width - self._REFERENCE_COLUMN_GAP
            ) / 2.0
            if has_collision and all(
                float(block["text_width"]) <= two_column_width
                for block in blocks
            ):
                candidate_columns: dict[int, int] = {}
                column_bottoms = [float("-inf"), float("-inf")]
                for block in ordered:
                    top = float(block["desired_top"])
                    available_columns = [
                        index for index, bottom in enumerate(column_bottoms)
                        if bottom <= top
                    ]
                    if not available_columns:
                        break
                    column = available_columns[0]
                    candidate_columns[int(block["order"])] = column
                    column_bottoms[column] = top + float(block["height"])
                else:
                    column_count = 2
                    column_width = two_column_width
                    block_columns = candidate_columns

            for block in sorted(blocks, key=lambda item: int(item["order"])):
                order = int(block["order"])
                column = block_columns[order]
                left = float(self._HORIZONTAL_PADDING) + column * (
                    column_width + self._REFERENCE_COLUMN_GAP
                )
                top = float(block["desired_top"])
                sides = tuple(block["record"].get("sides") or ())
                for line_index, line in enumerate(block["lines"]):
                    layouts.append({
                        "record": block["record"],
                        "text": line,
                        "side": (
                            sides[line_index]
                            if line_index < len(sides)
                            else ""
                        ),
                        "rect": QRectF(
                            left,
                            top + line_index * line_height,
                            column_width,
                            line_height,
                        ),
                        "column": column,
                        "column_count": column_count,
                    })
        return layouts

    def wheelEvent(self, event) -> None:
        canvas = self._canvas
        delta = event.angleDelta().y()
        if canvas is None or not delta:
            super().wheelEvent(event)
            return
        if canvas.is_pointer_interaction_active():
            event.accept()
            return
        viewport_top = self._scroll_area.viewport().geometry().top()
        canvas_y = float(event.pos().y() - viewport_top)
        scale = canvas.price_scale()
        if scale is None or not scale.plot_top <= canvas_y <= scale.plot_bottom:
            event.ignore()
            return
        self.price_scale_wheel_requested.emit(canvas_y, int(delta))
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), _BACKGROUND)
        painter.setPen(_TEXT)
        metrics = QFontMetrics(painter.font())
        for record in self.price_axis_records():
            painter.drawText(
                QRectF(
                    self._HORIZONTAL_PADDING,
                    record["y"] - metrics.height() / 2,
                    max(1, self.width() - self._HORIZONTAL_PADDING * 2),
                    metrics.height(),
                ),
                Qt.AlignRight | Qt.AlignVCenter,
                record["label"],
            )
        lower_reference_records = self.lower_reference_axis_records()
        for layout in self._lower_reference_text_layout(
            metrics, lower_reference_records,
        ):
            side = str(layout.get("side") or "")
            painter.setPen(QPen(
                _BUY if side == "BUY" else _SELL if side == "SELL" else _CROSSHAIR_TEXT
            ))
            painter.drawText(
                layout["rect"],
                Qt.AlignRight | Qt.AlignVCenter,
                layout["text"],
            )


class IndicatorFollowSignalValidationStockDisplay(QWidget):
    """Plain stock identity display for the V2 compact header."""

    full_stock_selection_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tooltip_text = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stock_label = QLabel("종목 선택")
        self.stock_label.setMinimumWidth(232)
        self.stock_label.setFixedHeight(30)
        self.stock_label.setStyleSheet(
            "QLabel { font-size: 13pt; font-weight: bold; padding: 0 4px; }"
            "QToolTip { font-size: 12pt; font-weight: normal; }"
        )
        self.stock_label.setCursor(Qt.PointingHandCursor)
        self.stock_label.installEventFilter(self)
        layout.addWidget(self.stock_label)

    @property
    def tooltip_text(self) -> str:
        return self._tooltip_text

    def set_current_stock(
        self,
        stock: ValidationStockRef | None,
        metadata: object = None,
    ) -> None:
        if stock is not None and (
            not isinstance(stock, ValidationStockRef) or not stock.code or not stock.name
        ):
            raise TypeError("stock must be None or a populated ValidationStockRef")
        self._hide_tooltip()
        self.stock_label.setText(
            "종목 선택" if stock is None else f"{stock.code} {stock.name}"
        )
        self._tooltip_text = _stock_metadata_tooltip(stock, metadata)
        self.stock_label.setToolTip(self._tooltip_text)

    def eventFilter(self, watched, event):
        if watched is self.stock_label:
            if event.type() == QEvent.MouseButtonDblClick:
                if event.button() == Qt.LeftButton:
                    self._hide_tooltip()
                    self.full_stock_selection_requested.emit()
                    return True
            elif event.type() == QEvent.Enter and self._tooltip_text:
                QToolTip.showText(
                    self.stock_label.mapToGlobal(QPoint(0, self.stock_label.height())),
                    self._tooltip_text,
                    self.stock_label,
                    self.stock_label.rect(),
                    2_000_000_000,
                )
            elif event.type() in {QEvent.Leave, QEvent.Hide}:
                self._hide_tooltip()
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self._hide_tooltip()
        super().hideEvent(event)

    @staticmethod
    def _hide_tooltip() -> None:
        QToolTip.hideText()


class IndicatorFollowSignalValidationRecentStockRow(QWidget):
    """Single-line, stock-by-stock view of the retained V2 recent-stock MRU."""

    stock_activated = pyqtSignal(object)
    stock_remove_requested = pyqtSignal(object)

    _ITEM_HORIZONTAL_PADDING = 8
    _REMOVE_HORIZONTAL_PADDING = 4
    _SEPARATOR_TEXT = " | "
    _NAVIGATION_WIDTH = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_stocks: tuple[ValidationStockRef, ...] = ()
        self._fitted_stocks: tuple[ValidationStockRef, ...] = ()
        self._available_width = 0
        self._visible_start_index = 0
        self.stock_buttons: list[QPushButton] = []
        self.remove_buttons: list[QPushButton] = []
        self._row_layout = QHBoxLayout(self)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(0)
        row_height = max(26, QFontMetrics(self.font()).height() + 8)
        self.setFixedHeight(row_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.previous_button = QPushButton("◁")
        self.next_button = QPushButton("▷")
        for button in (self.previous_button, self.next_button):
            button.setFixedSize(self._NAVIGATION_WIDTH, row_height)
            button.setCursor(Qt.PointingHandCursor)
            button.hide()
        self.previous_button.clicked.connect(self._show_previous_stock)
        self.next_button.clicked.connect(self._show_next_stock)
        self._items_container = QWidget(self)
        self._items_layout = QHBoxLayout(self._items_container)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(0)
        self._row_layout.addWidget(self.previous_button)
        self._row_layout.addWidget(self._items_container, 1)
        self._row_layout.addWidget(self.next_button)

    @property
    def recent_stocks(self) -> tuple[ValidationStockRef, ...]:
        return tuple(self._fitted_stocks)

    def set_recent_stocks(self, stocks: object) -> None:
        normalized: list[ValidationStockRef] = []
        seen_codes: set[str] = set()
        source = stocks if isinstance(stocks, (list, tuple)) else ()
        for stock in source:
            if (
                not isinstance(stock, ValidationStockRef)
                or not stock.code
                or not stock.name
                or stock.code in seen_codes
            ):
                continue
            normalized.append(ValidationStockRef(stock.code, stock.name))
            seen_codes.add(stock.code)
        updated = tuple(normalized)
        if updated != self._source_stocks:
            self._visible_start_index = 0
        self._source_stocks = updated
        self._refresh_projection()

    def set_available_width(self, width: int) -> None:
        normalized_width = max(0, int(width))
        if normalized_width == self._available_width:
            return
        self._available_width = normalized_width
        self._refresh_projection()

    def fitting_stocks_for_width(
        self,
        available_width: int,
    ) -> tuple[ValidationStockRef, ...]:
        available = max(0, int(available_width))
        if available <= 0:
            return ()
        overflow = self._total_stocks_width() > available
        if overflow:
            available = max(0, available - self._NAVIGATION_WIDTH * 2)
        return self._fitted_stocks_from(self._visible_start_index, available)

    def _stock_width(self, stock: ValidationStockRef, metrics: QFontMetrics) -> int:
        return (
            metrics.horizontalAdvance("X")
            + self._REMOVE_HORIZONTAL_PADDING * 2
            + metrics.horizontalAdvance(f"{stock.code} {stock.name}")
            + self._ITEM_HORIZONTAL_PADDING * 2
        )

    def _total_stocks_width(self) -> int:
        metrics = QFontMetrics(self.font())
        return (
            sum(self._stock_width(stock, metrics) for stock in self._source_stocks)
            + max(0, len(self._source_stocks) - 1)
            * metrics.horizontalAdvance(self._SEPARATOR_TEXT)
        )

    def _fitted_stocks_from(
        self, start_index: int, available: int
    ) -> tuple[ValidationStockRef, ...]:
        metrics = QFontMetrics(self.font())
        separator_width = metrics.horizontalAdvance(self._SEPARATOR_TEXT)
        used_width = 0
        fitted: list[ValidationStockRef] = []
        for stock in self._source_stocks[start_index:]:
            required_width = self._stock_width(stock, metrics)
            if fitted:
                required_width += separator_width
            if used_width + required_width > available:
                break
            fitted.append(stock)
            used_width += required_width
        return tuple(fitted)

    def _refresh_projection(self) -> None:
        if self._available_width <= 0:
            return
        overflow = self._total_stocks_width() > self._available_width
        if not overflow:
            self._visible_start_index = 0
        elif self._source_stocks:
            self._visible_start_index = min(
                self._visible_start_index, len(self._source_stocks) - 1
            )
        fitted = self.fitting_stocks_for_width(self._available_width)
        self.previous_button.setVisible(overflow)
        self.next_button.setVisible(overflow)
        self.previous_button.setEnabled(self._visible_start_index > 0)
        self.next_button.setEnabled(
            self._visible_start_index < len(self._source_stocks) - 1
            and self._visible_start_index + len(fitted) < len(self._source_stocks)
        )
        if (
            fitted == self._fitted_stocks
            and len(self.stock_buttons) == len(fitted)
            and len(self.remove_buttons) == len(fitted)
        ):
            return
        self._fitted_stocks = fitted
        self._clear_row()
        metrics = QFontMetrics(self.font())
        remove_width = (
            metrics.horizontalAdvance("X") + self._REMOVE_HORIZONTAL_PADDING * 2
        )
        for index, stock in enumerate(fitted):
            if index:
                separator = QLabel(self._SEPARATOR_TEXT)
                separator.setFixedWidth(metrics.horizontalAdvance(self._SEPARATOR_TEXT))
                separator.setAlignment(Qt.AlignCenter)
                self._items_layout.addWidget(separator)
            remove_button = QPushButton("X")
            remove_button.setFlat(True)
            remove_button.setCursor(Qt.PointingHandCursor)
            remove_button.setFixedHeight(self.height())
            remove_button.setFixedWidth(remove_width)
            remove_button.setStyleSheet(
                "QPushButton { border: none; background: transparent; padding: 0 4px; }"
                "QPushButton:hover { font-weight: bold; }"
            )
            remove_button.clicked.connect(
                lambda _checked=False, selected=stock: self.stock_remove_requested.emit(selected)
            )
            self.remove_buttons.append(remove_button)
            self._items_layout.addWidget(remove_button)

            text = f"{stock.code} {stock.name}"
            button = QPushButton(text)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(self.height())
            button.setFixedWidth(
                metrics.horizontalAdvance(text) + self._ITEM_HORIZONTAL_PADDING * 2
            )
            button.setStyleSheet(
                "QPushButton { border: none; background: transparent; padding: 0 8px; }"
                "QPushButton:hover { text-decoration: underline; }"
            )
            button.clicked.connect(
                lambda _checked=False, selected=stock: self.stock_activated.emit(selected)
            )
            self.stock_buttons.append(button)
            self._items_layout.addWidget(button)
        self._items_layout.addStretch(1)

    def _show_previous_stock(self) -> None:
        if self._visible_start_index > 0:
            self._visible_start_index -= 1
            self._refresh_projection()

    def _show_next_stock(self) -> None:
        if (
            self._visible_start_index < len(self._source_stocks) - 1
            and self._visible_start_index + len(self._fitted_stocks)
            < len(self._source_stocks)
        ):
            self._visible_start_index += 1
            self._refresh_projection()

    def _clear_row(self) -> None:
        self.stock_buttons = []
        self.remove_buttons = []
        while self._items_layout.count():
            item = self._items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class IndicatorFollowSignalValidationV5Window(
    IndicatorFollowRoutineSettingsDialog
):
    """Top-level working copy for repeated signal-only historical replay."""

    _V2_SECTION_HEADER_HEIGHT = 44
    _V2_SECTION_VERTICAL_MARGIN = 6
    _REFERENCE_CANDLE_COUNT = 250.0
    _INITIAL_HISTORICAL_CANDLE_COUNT = 5_000
    _INITIAL_EVALUATION_CANDLE_COUNT = 100
    _HISTORY_PREFETCH_MIN_CANDLES = 50.0
    _HISTORY_PREFETCH_VISIBLE_RATIO = 0.50
    _HISTORY_EXTENSION_IDLE_DELAY_MS = 350
    _MIN_VISIBLE_CANDLE_SPAN = 10.0
    _MAX_VISIBLE_CANDLE_SPAN = _MAX_SIGNAL_VALIDATION_VISIBLE_CANDLE_SPAN
    _TIME_SCROLL_UNITS_PER_CANDLE = 1000
    _TIME_ZOOM_FACTOR = 1.08
    _TIME_ZOOM_INTENT_DELTA = 60
    _TIME_ZOOM_INTENT_RESET_SECONDS = 0.18
    # Batch queued inputs on the next GUI turn, without delaying reversal.
    _WHEEL_ZOOM_FRAME_INTERVAL_MS = 0
    _PRICE_ZOOM_FACTOR = 1.05
    _VISIBLE_PRICE_PADDING_RATIO = 0.08
    _MIN_PRICE_RANGE_RATIO = 0.001
    _MAX_PRICE_RANGE_RATIO = 100.0
    _INITIAL_AVAILABLE_GEOMETRY_RATIO = 0.85
    _MIN_FIXED_CHART_HEIGHT = (
        IndicatorFollowSignalValidationChartCanvas._TOP
        + IndicatorFollowSignalValidationChartCanvas._BOTTOM
        + IndicatorFollowSignalValidationChartCanvas._PRICE_TO_LOWER_GAP
        + IndicatorFollowSignalValidationChartCanvas._LOWER_TOTAL_HEIGHT
        + 80
    )
    _CYCLE_TABLE_VISIBLE_ROWS = 5
    _CYCLE_TABLE_COLUMN_RATIOS = (
        0.055,
        0.18,
        0.075,
        0.13,
        0.11,
        0.16,
        0.10,
        0.10,
        0.09,
    )

    validation_run_requested = pyqtSignal(object)
    validation_range_evaluation_requested = pyqtSignal(int, int)
    marking_evaluation_requested = pyqtSignal(object)
    historical_extension_requested = pyqtSignal()
    settings_apply_requested = pyqtSignal(object)
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)
    recent_stock_remove_requested = pyqtSignal(object)
    visualization_projection_completed = pyqtSignal(object)
    chart_interaction_ended = pyqtSignal(int, bool)
    chart_input_invalidated = pyqtSignal()
    validation_range_changed = pyqtSignal(object)

    def __init__(
        self,
        stock: ValidationStockRef | None,
        seed: IndicatorFollowSignalValidationSeed,
        parent: QWidget | None = None,
    ) -> None:
        if stock is not None and (
            not isinstance(stock, ValidationStockRef) or not stock.code or not stock.name
        ):
            raise TypeError("stock must be None or a populated ValidationStockRef")
        if not isinstance(seed, IndicatorFollowSignalValidationSeed):
            raise TypeError("seed must be IndicatorFollowSignalValidationSeed")
        self._signal_validation_mode = True
        self._indicator_follow_rule_mapper_cache: object | None = None
        self._validation_preferences_store: object | None = None
        self._validation_preference_connections_bound = False
        self._signal_validation_stock = (
            None if stock is None else ValidationStockRef(stock.code, stock.name)
        )
        self._signal_validation_seed = IndicatorFollowSignalValidationSeed(
            seed.settings_snapshot,
            seed.to_ui_state(),
        )
        self._result_ui_state = seed.to_ui_state()
        self._normal_base_requested = True
        self._normal_base_ready = False
        self._normal_base_timeframe_key = ""
        self._range_replay_from_normal_base = False
        self._marking_generation = 0
        self._marking_times = None
        self._marking_key = None
        self._marking_source_start = None
        self._marking_display_source = None
        self._marking_display_reprojection_pending = False
        self._marking_pool_publication_pending = False
        self._marking_atomic_publication_active = False
        self._marking_policy_refresh_pending = False
        self._marking_snapshot = None
        self._marking_settings = None
        self._marking_comparison_cache = None
        self._marking_cache = None
        self._replay_snapshot: ValidationReplaySnapshot | None = None
        self._calculation_candles: list[dict[str, Any]] = []
        self._calculation_entries: list[ValidationReplayEntry] = []
        self._calculation_signal_marker_entries: list[ValidationReplayEntry] = []
        self._candles: list[dict[str, Any]] = []
        self._display_only_integrated_candles: list[dict[str, Any]] = []
        self._display_only_integrated_active = False
        self._entries: list[ValidationReplayEntry] = []
        self._signal_marker_entries_available = False
        self._signal_marker_coverage_times: tuple[str, str] | None = None
        self._range_replay_calculation_entries: list[ValidationReplayEntry] = []
        self._range_replay_entries: list[ValidationReplayEntry] = []
        self._range_replay_entries_available = False
        self._range_replay_revalidation_pending = False
        self._completed_cycles: list[IndicatorFollowValidationCompletedCycle] = []
        self._selected_index: int | None = None
        self._historical_candle_count = self._INITIAL_HISTORICAL_CANDLE_COUNT
        self._evaluation_candle_count = self._INITIAL_EVALUATION_CANDLE_COUNT
        self._chart_pool_start_index = 0
        self._chart_calculation_indexes: tuple[int | None, ...] = ()
        self._historical_pool_installed = False
        self._validation_range_anchor_index: int | None = None
        self._validation_range_anchor_time: str | None = None
        self._validation_range: tuple[int, int] | None = None
        self._validation_range_times: tuple[str, str] | None = None
        self._initial_validation_requested = False
        self._initial_natural_fit_pending = True
        self._programmatic_resize_in_progress = False
        self._initial_geometry_committed = False
        self._user_geometry_owned = False
        self._fixed_chart_height: int | None = None
        self._last_control_height: int | None = None
        self._primary_validation_action_state = "validate"
        self._settings_apply_after_validation = False
        self._preserve_chart_for_pending_validation = False
        self._entry_state: _ValidationEntryState | None = None
        self._entry_chart_view_state: _ValidationChartViewState | None = None
        self._entry_chart_view_anchor: _EntryChartViewAnchor | None = None
        self._historical_pool_signature: tuple[int, str, str] | None = None
        self._preserve_view_on_next_replay = False
        self._pending_history_extension_view_anchor: _HistoryExtensionViewAnchor | None = None
        self._history_browse_installing = False
        self._pending_prepared_presentation: PreparedSignalValidationPresentation | None = None
        self._pending_prepared_visualization_epoch: int | None = None
        self._backtest_trust_metadata: dict[str, Any] = {}
        self._pending_validation_ui_fingerprint: str | None = None
        self._validated_ui_fingerprint: str | None = None
        self._pending_result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._pending_result_ui_state: dict[str, Any] | None = None
        self._pending_ocr_turn_ui_state: dict[str, Any] | None = None
        self._result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._range_replay_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._signal_tooltips: dict[tuple[int, str], str] = {}
        self._signal_tooltip_provenance: tuple[Any, ...] | None = None
        self._visualization_descriptors: tuple[ValidationFilterDescriptor, ...] = ()
        self._visualization_cache: ValidationIndicatorSeriesCache | None = None
        self._visualization_active_by_marker: dict[tuple[int, str], tuple[str, ...]] = {}
        self._visualization_lower_filter_presentations: tuple[
            ValidationLowerFilterPresentation, ...
        ] = ()
        self._not_filter_identities: set[tuple[str, str]] = set()
        self._inner_not_filter_identities: set[tuple[str, str]] = set()
        self._filter_display_evidence_cache_epoch: int | None = None
        self._filter_display_evidence_identities_by_side: dict[
            str, frozenset[str]
        ] = {
            "BUY": frozenset(),
            "SELL": frozenset(),
        }
        self._visualization_data_error = ""
        self._visualization_projection_generation = 0
        self._visualization_projection_active: object | None = None
        self._visualization_projection_pending: dict[str, Any] | None = None
        self._visualization_projection_deferred: tuple[int, dict[str, Any]] | None = None
        self._visualization_projection_cache: dict[
            tuple[Any, ...],
            tuple[
                ValidationIndicatorSeriesCache,
                dict[tuple[int, str], tuple[str, ...]],
            ],
        ] = {}
        self._visualization_current_key: tuple[Any, ...] | None = None
        self._visualization_data_epoch = 0
        self._visualization_projection_source: tuple[
            int,
            tuple[dict[str, Any], ...],
            tuple[ValidationReplayEntry, ...],
            tuple[ValidationReplayEntry, ...],
            tuple[int | None, ...],
            tuple[
                tuple[dict[str, Any], ...],
                tuple[ValidationReplayEntry, ...],
                tuple[int | None, ...],
            ] | None,
        ] | None = None
        self._all_filter_descriptor_cache_key: tuple[str, str] | None = None
        self._all_filter_descriptor_cache_ui_state: object | None = None
        self._all_filter_descriptor_cache: tuple[
            ValidationFilterDescriptor, ...
        ] = ()
        self._all_filter_descriptor_cache_display_rules: dict[str, Any] | None = None
        self._result_ui_state_revision = 0
        self._visualization_projection_closed = False
        self._chart_results_suspended = False
        self._candle_preview_token: object | None = None
        self._candle_preview_canvas: IndicatorFollowSignalValidationChartCanvas | None = None
        self._hidden_price_filter_identities: set[str] = set()
        self._highlighted_price_filter_identities: set[tuple[str, str]] = set()
        self._highlighted_lower_filter_identities: set[tuple[str, str]] = set()
        self._hide_unhighlighted_price_filters = False
        self._filter_display_enabled = True
        self._filter_display_valid_enabled = False
        self._valid_filter_identity_cache_key: tuple[str, str, str] | None = None
        self._valid_filter_identity_cache: frozenset[tuple[str, str]] = frozenset()
        self._valid_filter_selection_error = ""
        self._visible_start_index = 0.0
        self._visible_candle_span = 1.0
        self._time_scale_manually_adjusted = False
        self._syncing_time_navigation_scrollbar = False
        self._time_zoom_wheel_accumulator = 0
        self._time_zoom_wheel_last_input_at: float | None = None
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._history_extension_intent_generation = 0
        self._history_extension_intent_source: str | None = None
        self._history_extension_retry_generation: int | None = None
        self._chart_interaction_epoch = 0
        self._chart_input_frame_generation = 0
        self._chart_input_frame_active = False
        self._chart_input_frame_canvas = None
        self._chart_input_frame_release_scheduled = False
        self._chart_input_frame_history_source = "navigation"
        self._default_price_minimum: float | None = None
        self._default_price_maximum: float | None = None
        self._current_price_minimum: float | None = None
        self._current_price_maximum: float | None = None
        self._price_scale_manually_adjusted = False
        super().__init__(
            rules_path=__file__,
            routine_name="지표추종매매 신호검증 V5",
            definition_id="indicator_follow_signal_validation_v2",
            settings_mode="registration",
            parent=parent,
        )
        self._pending_wheel_zoom: tuple[str, float, float] | None = None
        self._wheel_zoom_frame_timer = QTimer(self)
        self._wheel_zoom_frame_timer.setSingleShot(True)
        self._wheel_zoom_frame_timer.setInterval(self._WHEEL_ZOOM_FRAME_INTERVAL_MS)
        self._wheel_zoom_frame_timer.timeout.connect(self._wheel_zoom_frame_timeout)
        self._pending_time_navigation_value: int | None = None
        self._time_navigation_timer = QTimer(self)
        self._time_navigation_timer.setSingleShot(True)
        self._time_navigation_timer.setInterval(16)
        self._time_navigation_timer.timeout.connect(self._on_time_navigation_timeout)
        self.setModal(False)
        self.visualization_projection_completed.connect(
            self._on_visualization_projection_completed
        )
        self.setMinimumSize(0, 700)
        self.resize(max(1, self.sizeHint().width()), 900)
        self._connect_signal_ui_change_tracking()
        QApplication.instance().installEventFilter(self)
        self._set_primary_validation_action_state("validate")

    @property
    def stock(self) -> ValidationStockRef | None:
        return self._signal_validation_stock

    @property
    def replay_snapshot(self) -> ValidationReplaySnapshot | None:
        return self._replay_snapshot

    @property
    def backtest_trust_metadata(self) -> dict[str, Any]:
        return deepcopy(self._backtest_trust_metadata)

    def capture_history_browse_state(
        self,
        settings_snapshot: ValidationSettingsSnapshot,
    ) -> dict[str, Any] | None:
        replay_snapshot = self._replay_snapshot
        result_settings = self._result_settings_snapshot
        if (
            not isinstance(replay_snapshot, ValidationReplaySnapshot)
            or not isinstance(result_settings, ValidationSettingsSnapshot)
            or not isinstance(settings_snapshot, ValidationSettingsSnapshot)
            or replay_snapshot.stock != self.stock
            or replay_snapshot.settings_hash != settings_snapshot.rules_hash
            or result_settings.rules_hash != settings_snapshot.rules_hash
            or not self._calculation_candles
        ):
            return None
        return {
            "replay_snapshot": replay_snapshot,
            "source_candles": deepcopy(self._calculation_candles),
            # Preserve the full calculated marker set, not only the markers
            # visible in the current chart window. History browse extends by
            # 500 bars while the validated calculation view keeps 900 warmup
            # bars, so the newly revealed candles are already covered.
            "signal_entries": tuple(self._marker_entries_for_calculation()),
            "signal_marker_coverage_times": self._signal_marker_coverage_times,
            "visualization_descriptors": self._filter_display_descriptors(
                settings_snapshot.to_dict()
            ),
            "trust_metadata": self.backtest_trust_metadata,
        }

    @property
    def selected_evaluation_index(self) -> int | None:
        return self._selected_index

    @property
    def visible_start_index(self) -> float:
        return self._visible_start_index

    @property
    def visible_candle_span(self) -> float:
        return self._visible_candle_span

    @property
    def time_scale_manually_adjusted(self) -> bool:
        return self._time_scale_manually_adjusted

    @property
    def current_price_bounds(self) -> tuple[float, float] | None:
        if self._current_price_minimum is None or self._current_price_maximum is None:
            return None
        return self._current_price_minimum, self._current_price_maximum

    @property
    def price_scale_manually_adjusted(self) -> bool:
        return self._price_scale_manually_adjusted

    def _update_window_title(self) -> None:
        self.setWindowTitle("지표추종매매 - 독립 신호검증 V5")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.title_label = QLabel("")
        self.title_label.setVisible(False)
        self._build_signal_validation_control_tab()
        root.addWidget(self.control_tab, 0)

        action_row = QHBoxLayout()
        self.validation_status_label = QLabel(
            "검증 종목을 선택하세요."
            if self.stock is None
            else "과거 분봉 데이터 조회 중..."
        )
        self.validation_status_label.setWordWrap(False)
        self.validation_status_label.setMinimumWidth(0)
        self.validation_status_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Fixed,
        )
        self.result_summary_label = QLabel("")
        self.estimated_return_label = QLabel("")
        summary_font = self.result_summary_label.font()
        summary_font.setBold(False)
        self.result_summary_label.setFont(summary_font)
        self.estimated_return_label.setFont(summary_font)
        self.marking_display_check = QPushButton("마킹")
        self.marking_display_check.setObjectName("signalValidationMarkingEnabled")
        _configure_display_badge(self.marking_display_check, summary_font)
        self.marking_display_check.setChecked(False)
        self.marking_display_check.toggled.connect(self._on_marking_toggled)
        self.filter_display_check = QPushButton("필터")
        self.filter_display_check.setObjectName("signalValidationFilterDisplayEnabled")
        _configure_display_badge(self.filter_display_check, summary_font)
        self.filter_display_check.setChecked(self._filter_display_enabled)
        self.filter_display_check.setToolTip("차트의 필터 지표 표시 여부")
        self.filter_display_check.toggled.connect(
            self._on_filter_display_controls_changed
        )
        self.filter_display_valid_check = QPushButton("유효")
        self.filter_display_valid_check.setObjectName(
            "signalValidationFilterDisplayValid"
        )
        _configure_display_badge(self.filter_display_valid_check, summary_font)
        self.filter_display_valid_check.setChecked(
            self._filter_display_valid_enabled
        )
        self.filter_display_valid_check.setToolTip(
            _VALID_FILTER_DISPLAY_TOOLTIP
        )
        self.filter_display_valid_check.toggled.connect(
            self._on_filter_display_valid_changed
        )
        self.reset_button = QPushButton("초기화")
        self.replay_validation_button = QPushButton("검증")
        self.primary_validation_action_button = QPushButton("설정적용")
        self.run_validation_button = self.primary_validation_action_button
        self.close_button = QPushButton("닫기")
        self.reset_button.clicked.connect(self._handle_reset_requested)
        self.replay_validation_button.clicked.connect(self.request_validation)
        self.primary_validation_action_button.clicked.connect(
            self._handle_primary_validation_action
        )
        self.close_button.clicked.connect(self.close)
        action_row.addWidget(self.validation_status_label)
        action_row.addWidget(self.result_summary_label)
        action_row.addWidget(self.estimated_return_label)
        action_row.addStretch(1)
        action_row.addWidget(self.marking_display_check)
        action_row.addWidget(self.filter_display_check)
        action_row.addWidget(self.filter_display_valid_check)
        action_row.addWidget(self.reset_button)
        action_row.addWidget(self.replay_validation_button)
        action_row.addWidget(self.primary_validation_action_button)
        action_row.addWidget(self.close_button)
        self._signal_validation_action_layout = action_row
        root.addLayout(action_row)

        self.result_widget = QWidget()
        result_layout = QVBoxLayout(self.result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self._signal_validation_result_layout = result_layout

        self.chart_stack = QStackedWidget()
        self.loading_label = QLabel(
            "상단 종목 영역을 두 번 클릭하여 검증 종목을 선택하세요."
            if self.stock is None
            else "과거 분봉 데이터 조회 중..."
        )
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setWordWrap(True)
        self.loading_label.setMinimumWidth(0)
        self.loading_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        self.loading_label.setStyleSheet("font-size: 12pt; color: #555555;")
        self.chart_scroll_area = QScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_scroll_area.viewport().installEventFilter(self)
        self.time_navigation_scrollbar = QScrollBar(Qt.Horizontal)
        # Reserve one permanent navigation row: zooming must not resize candles.
        self.time_navigation_scrollbar.setFixedHeight(
            max(14, self.time_navigation_scrollbar.sizeHint().height())
        )
        self.time_navigation_scrollbar.valueChanged.connect(
            self._queue_time_navigation_scrollbar
        )
        self.time_navigation_scrollbar.sliderPressed.connect(self._begin_time_navigation)
        self.time_navigation_scrollbar.sliderReleased.connect(self._finish_time_navigation)
        self.fixed_price_axis = IndicatorFollowSignalValidationFixedPriceAxis(
            self.chart_scroll_area
        )
        self.fixed_price_axis.price_scale_wheel_requested.connect(
            self._queue_price_scale_wheel
        )
        self.chart_view = QWidget()
        chart_view_layout = QHBoxLayout(self.chart_view)
        chart_view_layout.setContentsMargins(0, 0, 0, 0)
        chart_view_layout.setSpacing(0)
        chart_view_layout.addWidget(self.fixed_price_axis)
        self.chart_viewport_column = QWidget()
        chart_viewport_layout = QVBoxLayout(self.chart_viewport_column)
        chart_viewport_layout.setContentsMargins(0, 0, 0, 0)
        chart_viewport_layout.setSpacing(0)
        chart_viewport_layout.addWidget(self.chart_scroll_area, 1)
        chart_viewport_layout.addWidget(self.time_navigation_scrollbar, 0)
        chart_view_layout.addWidget(self.chart_viewport_column, 1)
        self.chart_stack.addWidget(self.loading_label)
        self.chart_stack.addWidget(self.chart_view)
        self.chart_stack.setCurrentWidget(self.loading_label)
        self.chart_stack.setMinimumHeight(self._MIN_FIXED_CHART_HEIGHT)
        result_layout.addWidget(self.chart_stack, 1)

        self.completed_cycle_table = QTableWidget(0, 9)
        self.completed_cycle_table.setObjectName("signalValidationCompletedCycleTable")
        self.completed_cycle_table.setHorizontalHeaderLabels([
            "회차",
            "매수구간",
            "매수횟수",
            "매수총액",
            "평단",
            "매도시각",
            "매도가",
            "손익",
            "수익률",
        ])
        self.completed_cycle_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.completed_cycle_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.completed_cycle_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.completed_cycle_table.verticalHeader().setVisible(False)
        self.completed_cycle_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.completed_cycle_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.completed_cycle_table.setWordWrap(False)
        self.completed_cycle_table.cellClicked.connect(
            self._completed_cycle_row_clicked
        )
        cycle_header = self.completed_cycle_table.horizontalHeader()
        for column in range(self.completed_cycle_table.columnCount()):
            cycle_header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.completed_cycle_empty_label = QLabel(
            "완료 매매사이클 없음",
            self.completed_cycle_table.viewport(),
        )
        self.completed_cycle_empty_label.setAlignment(Qt.AlignCenter)
        self.completed_cycle_empty_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.completed_cycle_table.viewport().installEventFilter(self)
        self._sync_completed_cycle_table_geometry()
        result_layout.addWidget(self.completed_cycle_table)
        self.result_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.result_widget, 1)


    @staticmethod
    def _new_basic_header_spaced_separator() -> QLabel:
        separator = QLabel("|")
        separator.setContentsMargins(12, 0, 12, 0)
        separator.setAlignment(Qt.AlignCenter)
        return separator

    def _build_validation_execution_controls(self) -> None:
        widget = QWidget()
        widget.setObjectName("signalValidationExecutionCompact")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignVCenter)

        self.validation_averaging_enabled_check = QCheckBox("평단관리:")
        self.validation_averaging_enabled_check.setChecked(False)
        layout.addWidget(self.validation_averaging_enabled_check)

        self.validation_execution_detail_widget = QWidget()
        detail_layout = QHBoxLayout(self.validation_execution_detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(7)
        detail_layout.setAlignment(Qt.AlignVCenter)

        self.validation_start_budget_label = QLabel("시작예산")
        self.validation_start_budget_label.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter
        )
        detail_layout.addWidget(self.validation_start_budget_label)

        self.validation_first_buy_quantity_line = QLineEdit("1")
        self.validation_first_buy_quantity_line.setFixedSize(42, 28)
        self.validation_first_buy_quantity_line.setAlignment(Qt.AlignCenter)
        detail_layout.addWidget(self.validation_first_buy_quantity_line)

        self.validation_first_buy_unit_label = QLabel("주")
        self.validation_first_buy_unit_label.setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        self.validation_first_buy_unit_label.setContentsMargins(0, 0, 5, 0)
        detail_layout.addWidget(self.validation_first_buy_unit_label)

        self.validation_repeat_mode_combo = QComboBox()
        self.validation_repeat_mode_combo.addItems(
            ["회차기준", "예산기준", "능동매수"]
        )
        self.validation_repeat_mode_combo.setFixedSize(106, 28)
        detail_layout.addWidget(self.validation_repeat_mode_combo)

        self.validation_repeat_stack = QStackedWidget()
        self.validation_repeat_stack.setFixedSize(308, 30)
        self.validation_repeat_stack.setSizePolicy(
            QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )

        round_widget = QWidget()
        round_layout = QHBoxLayout(round_widget)
        round_layout.setContentsMargins(0, 0, 0, 0)
        round_layout.setSpacing(6)
        round_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.validation_round_previous_label = QLabel("직전회차")
        self.validation_round_operator_combo = QComboBox()
        self.validation_round_operator_combo.addItems(["+", "x"])
        self.validation_round_operator_combo.setFixedSize(48, 28)
        self.validation_round_budget_line = QLineEdit("0.5")
        self.validation_round_budget_line.setFixedSize(50, 28)
        self.validation_round_start_budget_label = QLabel("x 시작예산")
        round_layout.addWidget(self.validation_round_previous_label)
        round_layout.addWidget(self.validation_round_operator_combo)
        round_layout.addWidget(self.validation_round_budget_line)
        round_layout.addWidget(self.validation_round_start_budget_label)
        round_layout.addStretch(1)
        self.validation_repeat_stack.addWidget(round_widget)

        budget_widget = QWidget()
        budget_layout = QHBoxLayout(budget_widget)
        budget_layout.setContentsMargins(0, 0, 0, 0)
        budget_layout.setSpacing(6)
        budget_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.validation_budget_previous_amount_label = QLabel("직전예산")
        self.validation_budget_multiply_label = QLabel("x")
        self.validation_budget_ratio_line = QLineEdit("2.0")
        self.validation_budget_ratio_line.setFixedSize(50, 28)
        self.validation_budget_ratio_line.setValidator(
            QDoubleValidator(1.000001, 999999999.0, 6, self)
        )
        budget_layout.addWidget(self.validation_budget_previous_amount_label)
        budget_layout.addWidget(self.validation_budget_multiply_label)
        budget_layout.addWidget(self.validation_budget_ratio_line)
        budget_layout.addStretch(1)
        self.validation_repeat_stack.addWidget(budget_widget)

        active_widget = QWidget()
        active_layout = QHBoxLayout(active_widget)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(5)
        active_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.validation_active_average_label = QLabel("평단")
        self.validation_active_direction_combo = QComboBox()
        self.validation_active_direction_combo.addItems(["상향", "하향", "상하"])
        self.validation_active_direction_combo.setFixedSize(76, 28)
        self.validation_active_ratio_line = QLineEdit("0.45")
        self.validation_active_ratio_line.setFixedSize(50, 28)
        self.validation_active_percent_label = QLabel("%")
        self.validation_active_compare_combo = QComboBox()
        self.validation_active_compare_combo.addItems(["이상", "이하", "이내", "이탈"])
        self.validation_active_compare_combo.setFixedSize(76, 28)
        active_layout.addWidget(self.validation_active_average_label)
        active_layout.addWidget(self.validation_active_direction_combo)
        active_layout.addWidget(self.validation_active_ratio_line)
        active_layout.addWidget(self.validation_active_percent_label)
        active_layout.addWidget(self.validation_active_compare_combo)
        active_layout.addStretch(1)
        self.validation_repeat_stack.addWidget(active_widget)

        for label in (
            self.validation_start_budget_label,
            self.validation_first_buy_unit_label,
            self.validation_round_previous_label,
            self.validation_round_start_budget_label,
            self.validation_budget_previous_amount_label,
            self.validation_budget_multiply_label,
            self.validation_active_average_label,
            self.validation_active_percent_label,
        ):
            label.setMinimumWidth(
                QFontMetrics(label.font()).horizontalAdvance(label.text()) + 8
            )

        self._validation_execution_line_edits = (
            self.validation_first_buy_quantity_line,
            self.validation_round_budget_line,
            self.validation_budget_ratio_line,
            self.validation_active_ratio_line,
        )
        self._validation_execution_combos = (
            self.validation_repeat_mode_combo,
            self.validation_round_operator_combo,
            self.validation_active_direction_combo,
            self.validation_active_compare_combo,
        )
        for line_edit in self._validation_execution_line_edits:
            line_edit.setFrame(False)
            line_edit.setAlignment(Qt.AlignCenter)
            line_edit.setStyleSheet("QLineEdit { border: none; padding: 0; }")
        self._validation_execution_edit_styles = tuple(
            (control, control.styleSheet())
            for control in (
                *self._validation_execution_line_edits,
                *self._validation_execution_combos,
            )
        )
        self._validation_execution_combo_focus_policies = tuple(
            (combo, combo.focusPolicy())
            for combo in self._validation_execution_combos
        )
        self._validation_execution_locked = False
        for combo in self._validation_execution_combos:
            combo.installEventFilter(self)

        detail_layout.addWidget(self.validation_repeat_stack)
        layout.addWidget(self.validation_execution_detail_widget)
        self.validation_execution_box = widget
        self.validation_execution_separator = (
            self._new_basic_header_spaced_separator()
        )
        self.basic_header_row.insertWidget(
            self.basic_header_row.count() - 1,
            self.validation_execution_separator,
            0,
            Qt.AlignVCenter,
        )
        self.basic_header_row.insertWidget(
            self.basic_header_row.count() - 1,
            widget,
            0,
            Qt.AlignLeft | Qt.AlignVCenter,
        )

        self.validation_trading_cost_separator = (
            self._new_basic_header_spaced_separator()
        )
        self.validation_trading_cost_box = QWidget()
        trading_cost_layout = QHBoxLayout(self.validation_trading_cost_box)
        trading_cost_layout.setContentsMargins(0, 0, 0, 0)
        trading_cost_layout.setSpacing(2)
        trading_cost_layout.setAlignment(Qt.AlignVCenter)
        self.validation_trading_cost_enabled_check = QCheckBox("거래비용 :")
        self.validation_trading_cost_enabled_check.setChecked(False)
        self.validation_trading_cost_percent_line = QLineEdit("0.2")
        self.validation_trading_cost_percent_line.setFixedWidth(40)
        self.validation_trading_cost_percent_line.setFrame(False)
        self.validation_trading_cost_percent_line.setAlignment(Qt.AlignCenter)
        self.validation_trading_cost_percent_line.setStyleSheet(
            "QLineEdit { border: none; padding: 0; }"
        )
        trading_cost_layout.addWidget(self.validation_trading_cost_enabled_check)
        trading_cost_layout.addWidget(self.validation_trading_cost_percent_line)
        trading_cost_layout.addWidget(QLabel("%"))

        self.validation_regular_market_separator = (
            self._new_basic_header_spaced_separator()
        )
        self.validation_regular_market_only_check = QCheckBox("정규장만 표시")
        self.validation_regular_market_only_check.setChecked(False)

        for header_item in (
            self.validation_trading_cost_separator,
            self.validation_trading_cost_box,
            self.validation_regular_market_separator,
            self.validation_regular_market_only_check,
        ):
            self.basic_header_row.insertWidget(
                self.basic_header_row.count() - 1,
                header_item,
                0,
                Qt.AlignLeft | Qt.AlignVCenter,
            )

        def sync_mode(*_args):
            index = {
                "회차기준": 0,
                "예산기준": 1,
                "능동매수": 2,
            }.get(self.validation_repeat_mode_combo.currentText(), 0)
            self.validation_repeat_stack.setCurrentIndex(index)

        def sync_enabled(*_args):
            self.validation_execution_detail_widget.setEnabled(True)
            self._sync_validation_execution_lock_state()

        def sync_trading_cost_enabled(*_args):
            self.validation_trading_cost_percent_line.setEnabled(True)

        def normalize_budget_ratio_input():
            try:
                ratio = float(self.validation_budget_ratio_line.text().strip())
            except (TypeError, ValueError):
                ratio = 0.0
            if not math.isfinite(ratio) or ratio <= 1.0:
                self.validation_budget_ratio_line.setText("2.0")

        self.validation_repeat_mode_combo.currentTextChanged.connect(sync_mode)
        self.validation_averaging_enabled_check.toggled.connect(sync_enabled)
        self.validation_trading_cost_enabled_check.toggled.connect(
            sync_trading_cost_enabled
        )
        self.validation_budget_ratio_line.editingFinished.connect(
            normalize_budget_ratio_input
        )
        sync_mode()
        sync_enabled()
        sync_trading_cost_enabled()
        normalize_budget_ratio_input()

    def _sync_validation_execution_lock_state(self) -> None:
        """Freeze averaging inputs without using the disabled visual state."""
        checkbox = getattr(self, "validation_averaging_enabled_check", None)
        if checkbox is None:
            return
        locked = bool(checkbox.isChecked())
        self._validation_execution_locked = locked

        for line_edit in getattr(
            self,
            "_validation_execution_line_edits",
            (),
        ):
            line_edit.setReadOnly(locked)

        unlocked_styles = dict(getattr(
            self,
            "_validation_execution_edit_styles",
            (),
        ))
        for line_edit in getattr(
            self,
            "_validation_execution_line_edits",
            (),
        ):
            base_style = unlocked_styles.get(line_edit, "")
            if locked:
                line_edit.setStyleSheet(
                    base_style
                    + " QLineEdit { background: transparent; }"
                )
            else:
                line_edit.setStyleSheet(base_style)

        focus_policies = dict(getattr(
            self,
            "_validation_execution_combo_focus_policies",
            (),
        ))
        for combo in getattr(
            self,
            "_validation_execution_combos",
            (),
        ):
            base_style = unlocked_styles.get(combo, "")
            if locked:
                combo.hidePopup()
                combo.setFocusPolicy(Qt.NoFocus)
                combo.setStyleSheet(
                    base_style
                    + " QComboBox { background: transparent; border: none;"
                    " padding: 0 2px; }"
                    " QComboBox::drop-down { border: none; width: 0px; }"
                    " QComboBox::down-arrow { image: none; width: 0px;"
                    " height: 0px; }"
                )
            else:
                combo.setFocusPolicy(
                    focus_policies.get(combo, Qt.StrongFocus)
                )
                combo.setStyleSheet(base_style)

    def _build_signal_validation_control_tab(self) -> None:
        """Build the compact V2 shell while reusing only BUY/SELL signal builders."""
        self.control_tab = QWidget()
        outer = QVBoxLayout(self.control_tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self.control_page = QWidget()
        page_layout = QVBoxLayout(self.control_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(2)
        page_layout.setAlignment(Qt.AlignTop)

        self.basic_box = QGroupBox("")
        self.basic_box.setObjectName("signalValidationCompactHeader")
        self.basic_box.setStyleSheet(
            "QGroupBox#signalValidationCompactHeader {"
            "border: 1px solid #8A98A8; border-radius: 2px; background: transparent;"
            "}"
        )
        basic_layout = QVBoxLayout(self.basic_box)
        basic_layout.setContentsMargins(10, 3, 10, 3)
        basic_layout.setSpacing(2)
        header_widget = QWidget()
        self.basic_header_widget = header_widget
        self.basic_header_widget.setFixedHeight(self._V2_SECTION_HEADER_HEIGHT)
        header_row = QHBoxLayout(header_widget)
        self.basic_header_row = header_row
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.setAlignment(Qt.AlignVCenter)
        self.basic_toggle_button = QLabel("▶ 기본설정")
        self.basic_toggle_button.setCursor(Qt.PointingHandCursor)
        self.basic_toggle_button.setFixedHeight(30)
        self.basic_toggle_button.setMinimumWidth(132)
        self.basic_toggle_button.setAlignment(Qt.AlignCenter)
        self.basic_toggle_button.setStyleSheet(
            "font-size: 13pt; font-weight: bold; color: #2E6B3A;"
            " padding: 0px 5px; border: 1px solid #000000; border-radius: 2px;"
            " background: transparent;"
        )
        self.basic_toggle_button.installEventFilter(self)
        self.compact_stock_display = IndicatorFollowSignalValidationStockDisplay()
        self.compact_header_arrow = self.basic_toggle_button
        self.compact_stock_label = self.compact_stock_display.stock_label
        self.compact_stock_display.full_stock_selection_requested.connect(
            self.stock_selection_requested.emit
        )
        self.recent_stock_row = IndicatorFollowSignalValidationRecentStockRow()
        self.recent_stock_row.stock_activated.connect(
            self.recent_stock_selected.emit
        )
        self.recent_stock_row.stock_remove_requested.connect(
            self.recent_stock_remove_requested.emit
        )
        self.recent_stock_panel = QWidget()
        self.recent_stock_panel_layout = QHBoxLayout(self.recent_stock_panel)
        self.recent_stock_panel_layout.setContentsMargins(4, 3, 4, 3)
        self.recent_stock_panel_layout.setSpacing(8)
        self.stock_selection_button = QPushButton("종목선택")
        self.stock_selection_button.setObjectName(
            "signalValidationStockSelectionButton"
        )
        self.stock_selection_button.setFixedHeight(26)
        self.stock_selection_button.setCursor(Qt.PointingHandCursor)
        self.stock_selection_button.setStyleSheet(
            "QPushButton#signalValidationStockSelectionButton {"
            "font-size: 9pt; padding: 0px; text-align: center;"
            "border: 1px solid #7A8794; border-radius: 3px;"
            "background: #F4F6F8; color: #1F2933;"
            "}"
            "QPushButton#signalValidationStockSelectionButton:hover {"
            "border-color: #4C78A8; background: #E8F1FA;"
            "}"
            "QPushButton#signalValidationStockSelectionButton:pressed {"
            "border-color: #3B638A; background: #D7E4F0;"
            "}"
        )
        self.stock_selection_button.clicked.connect(
            lambda _checked=False: self.stock_selection_requested.emit()
        )
        self.recent_stock_panel_layout.addWidget(self.stock_selection_button)
        self.recent_stock_panel_layout.addWidget(self.recent_stock_row, 1)
        self.recent_stock_panel.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.recent_stock_panel_layout.activate()
        self.recent_stock_panel.setFixedHeight(
            self.recent_stock_panel_layout.sizeHint().height()
        )
        self._recent_stock_row_expanded = False
        self.recent_stock_panel.setVisible(False)
        self.compact_stock_display.set_current_stock(self.stock)
        self.basic_signal_interval_combo = IndicatorFollowTimeframeComboBox()
        self.basic_signal_interval_combo.setCurrentText("5")
        timeframe_combo_width = max(
            98,
            self.basic_signal_interval_combo.sizeHint().width(),
        )
        self.basic_signal_interval_combo.setFixedWidth(timeframe_combo_width)
        self.basic_signal_interval_combo.setFixedHeight(30)
        self.basic_signal_interval_combo.setMaxVisibleItems(
            self.basic_signal_interval_combo.count()
        )
        self.basic_signal_interval_combo.view().setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.basic_signal_interval_combo.view().setMinimumWidth(timeframe_combo_width)
        header_row.addWidget(self.basic_toggle_button)
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(self.compact_stock_display)
        header_row.addWidget(self._new_basic_header_spaced_separator())
        header_row.addWidget(QLabel("기준봉"))
        header_row.addWidget(self.basic_signal_interval_combo)
        header_row.addStretch(1)
        basic_layout.addWidget(header_widget)
        basic_layout.addWidget(self.recent_stock_panel)
        self.basic_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        basic_layout.activate()
        self.basic_box.setFixedHeight(basic_layout.sizeHint().height())
        page_layout.addWidget(self.basic_box)

        buy_title = self._build_control_buy_section(page_layout)
        sell_title = self._build_control_sell_section(page_layout)
        self._build_validation_execution_controls()
        self._normalize_v2_section_geometry()
        self._normalize_v2_header_internal_geometry()
        self._control_section_mode = "summary"
        self._control_header_click_modes = {
            buy_title: "buy",
            sell_title: "sell",
        }
        for title in self._control_header_click_modes:
            title.installEventFilter(self)
            title.setCursor(Qt.PointingHandCursor)
        self._apply_control_section_mode("summary", force=True)
        outer.addWidget(self.control_page)
        self.control_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    def _normalize_v2_section_geometry(self) -> None:
        collapsed_height = (
            self._V2_SECTION_HEADER_HEIGHT
            + self._V2_SECTION_VERTICAL_MARGIN * 2
        )
        sections = (
            (self.basic_box, self.basic_header_widget),
            (self.buy_box, self.buy_header_widget),
            (self.sell_box, self.sell_header_widget),
        )
        for box, header_widget in sections:
            section_layout = box.layout()
            section_layout.setContentsMargins(
                10,
                self._V2_SECTION_VERTICAL_MARGIN,
                10,
                self._V2_SECTION_VERTICAL_MARGIN,
            )
            section_layout.setSpacing(2)
            header_widget.setFixedHeight(self._V2_SECTION_HEADER_HEIGHT)
            box.setMinimumHeight(collapsed_height)
            box.setMaximumHeight(collapsed_height)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        for box, inherited_margin in (
            (self.buy_box, "4px"),
            (self.sell_box, "8px"),
        ):
            box.setStyleSheet(
                box.styleSheet().replace(
                    f"margin-top: {inherited_margin};",
                    "margin-top: 0px;",
                )
            )
        for box, _header_widget in sections:
            box.setContentsMargins(1, 1, 1, 1)
        self._v2_collapsed_section_height = collapsed_height
        self._buy_collapsed_height = collapsed_height
        self._sell_collapsed_height = collapsed_height

    def _normalize_v2_header_internal_geometry(self) -> None:
        separator_style = (
            "font-size: 13pt; font-weight: bold; color: #000000; padding: 0px 1px;"
        )
        header_contracts = (
            ("basic", self.basic_header_widget, self.basic_toggle_button, "기본설정"),
            ("buy", self.buy_header_widget, self.buy_title, "매수설정"),
            ("sell", self.sell_header_widget, self.sell_title, "매도설정"),
        )
        common_title_width = 132
        for _name, _header_widget, title_widget, title_text in header_contracts:
            original_text = title_widget.text()
            for arrow in ("▶", "▼"):
                title_widget.setText(f"{arrow} {title_text}")
                common_title_width = max(
                    common_title_width,
                    title_widget.sizeHint().width(),
                )
            title_widget.setText(original_text)
        for name, header_widget, title_widget, _title_text in header_contracts:
            header_layout = header_widget.layout()
            header_layout.setAlignment(Qt.AlignVCenter)
            for index in range(header_layout.count()):
                widget = header_layout.itemAt(index).widget()
                if widget is not None:
                    header_layout.setAlignment(widget, Qt.AlignVCenter)
            title_widget.setFixedWidth(common_title_width)
            title_widget.setFixedHeight(30)
            title_widget.setAlignment(Qt.AlignCenter)
            separator = header_layout.itemAt(1).widget()
            separator.setText("|")
            separator.setFixedSize(12, 30)
            separator.setAlignment(Qt.AlignCenter)
            separator.setStyleSheet(separator_style)
            setattr(self, f"{name}_header_separator", separator)

    def eventFilter(self, watched, event):
        if watched in getattr(self, "_signal_ui_confirmation_inputs", ()):
            if event.type() == QEvent.FocusOut or (
                event.type() == QEvent.KeyPress
                and event.key() in {Qt.Key_Return, Qt.Key_Enter}
            ):
                self._queue_signal_validation_ui_confirmation()
        if (
            watched in getattr(self, "_signal_ui_confirmation_backgrounds", ())
            and event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
        ):
            # Header and overview backgrounds keep a line edit focused, so
            # commit the expression before their click exposes stale results.
            self._flush_pending_signal_validation_ui_confirmation()
        if (
            isinstance(watched, QPushButton)
            and watched not in getattr(self, "_signal_ui_expression_buttons", ())
            and event.type() == QEvent.MouseButtonRelease
            and watched.focusPolicy() == Qt.NoFocus
            and QApplication.focusWidget() in getattr(
                self, "_signal_ui_expression_lines", ()
            )
        ):
            # NoFocus buttons do not make the expression line emit FocusOut.
            # A display checkbox is deliberately not a QPushButton, so merely
            # changing presentation still leaves the draft alone.
            self._queue_signal_validation_ui_confirmation()
        canvas = getattr(self, "canvas", None)
        if (
            (watched is canvas and event.type() == QEvent.UngrabMouse)
            or (
                watched is getattr(self, "time_navigation_scrollbar", None)
                and event.type() == QEvent.UngrabMouse
            )
            or (watched is self and event.type() == QEvent.WindowDeactivate)
        ) and (
            self.is_chart_interaction_busy()
            or getattr(self, "_chart_input_frame_active", False)
            or getattr(self, "_pending_wheel_zoom", None) is not None
            or getattr(self, "_pending_time_navigation_value", None) is not None
        ):
            self._invalidate_user_background_results()
            self._discard_chart_input()
        if watched is canvas and event.type() in {
            QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
        }:
            self._flush_pending_time_navigation()
        if (
            watched is getattr(self, "canvas", None)
            and self._pending_wheel_zoom is not None
            and event.type()
            in {QEvent.MouseButtonPress, QEvent.MouseButtonDblClick}
        ):
            self._flush_pending_wheel_zoom()
        if (
            event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
        ):
            cycle_table = getattr(self, "completed_cycle_table", None)
            cycle_viewport = (
                cycle_table.viewport() if cycle_table is not None else None
            )
            if watched is cycle_viewport:
                if not cycle_table.indexAt(event.pos()).isValid():
                    self._clear_completed_cycle_selection()
            else:
                self._clear_completed_cycle_selection()

        if (
            getattr(self, "_validation_execution_locked", False)
            and watched in getattr(self, "_validation_execution_combos", ())
            and event.type() in {
                QEvent.MouseButtonPress,
                QEvent.MouseButtonDblClick,
                QEvent.Wheel,
                QEvent.KeyPress,
                QEvent.KeyRelease,
            }
        ):
            event.accept()
            return True

        chart_viewport = getattr(
            getattr(self, "chart_scroll_area", None),
            "viewport",
            lambda: None,
        )()
        if watched is chart_viewport:
            if event.type() == QEvent.Resize:
                canvas = getattr(self, "canvas", None)
                if canvas is not None:
                    QTimer.singleShot(0, canvas.update)
            return super().eventFilter(watched, event)
        cycle_table = getattr(self, "completed_cycle_table", None)
        if cycle_table is not None and watched is cycle_table.viewport():
            if event.type() == QEvent.Resize:
                self._sync_completed_cycle_empty_label_geometry()
                self._resize_completed_cycle_columns()
            return super().eventFilter(watched, event)
        if (
            watched is getattr(self, "basic_toggle_button", None)
            and event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
        ):
            self._toggle_recent_stock_row()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _clamped_visible_candle_span(self, requested_span: float) -> float:
        candle_count = len(self._candles)
        if candle_count <= 0:
            return 1.0
        minimum_span = min(self._MIN_VISIBLE_CANDLE_SPAN, float(candle_count))
        maximum_span = min(self._MAX_VISIBLE_CANDLE_SPAN, float(candle_count))
        return min(maximum_span, max(minimum_span, float(requested_span)))

    def _maximum_visible_start(self, span: float | None = None) -> float:
        effective_span = (
            self._visible_candle_span if span is None else float(span)
        )
        return max(0.0, len(self._candles) - effective_span)

    def _auto_fit_price_to_visible_time_range(self) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        canvas._refresh_visible_data_bounds()
        minimum, maximum = canvas._price_data_bounds()
        if minimum is None or maximum is None:
            return
        if maximum == minimum:
            padding = max(abs(maximum) * 0.01, 1.0)
        else:
            padding = (maximum - minimum) * self._VISIBLE_PRICE_PADDING_RATIO
        minimum -= padding
        maximum += padding
        self._default_price_minimum = minimum
        self._default_price_maximum = maximum
        self._current_price_minimum = minimum
        self._current_price_maximum = maximum
        self._price_scale_manually_adjusted = False
        canvas.set_price_view(minimum, maximum)
        self.fixed_price_axis.refresh_scale()

    def _set_time_view(
        self,
        start_index: float,
        candle_span: float,
        *,
        manually_adjusted: bool | None = None,
        auto_fit_price: bool = False,
        user_horizontal_navigation: bool = False,
    ) -> None:
        span = self._clamped_visible_candle_span(candle_span)
        start = min(
            self._maximum_visible_start(span),
            max(0.0, float(start_index)),
        )
        time_view_changed = not (
            math.isclose(
                start,
                self._visible_start_index,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            and math.isclose(
                span,
                self._visible_candle_span,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        )
        marking_check = getattr(self, "marking_display_check", None)
        if (
            user_horizontal_navigation
            and time_view_changed
            and (
                self._marking_times is not None
                or (marking_check is not None and marking_check.isChecked())
            )
        ):
            self._disable_marking(reason="horizontal_navigation")
        self._cancel_pending_time_navigation()
        self._cancel_pending_wheel_zoom(reset_intent=False)
        self._visible_start_index = start
        self._visible_candle_span = span
        if manually_adjusted is not None:
            self._time_scale_manually_adjusted = bool(manually_adjusted)
        # Queue scrollbar layout before the canvas update, so its final size
        # is available to the static raster rather than painting both sizes.
        self._sync_time_navigation_scrollbar()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_time_view(start, span, refresh_lower_bounds=not auto_fit_price)
        if auto_fit_price:
            self._auto_fit_price_to_visible_time_range()

    def _history_extension_is_needed(self) -> bool:
        if (
            self.canvas is None
            or not self._historical_pool_installed
            or not self._candles
        ):
            return False
        threshold = max(
            self._HISTORY_PREFETCH_MIN_CANDLES,
            self._visible_candle_span * self._HISTORY_PREFETCH_VISIBLE_RATIO,
        )
        return self._visible_start_index <= threshold

    @property
    def chart_interaction_epoch(self) -> int:
        return self._chart_interaction_epoch

    def is_chart_interaction_busy(self) -> bool:
        canvas = getattr(self, "canvas", None)
        scrollbar = getattr(self, "time_navigation_scrollbar", None)
        return bool(
            (canvas is not None and canvas.is_pointer_interaction_active())
            or (scrollbar is not None and scrollbar.isSliderDown())
        )

    def is_chart_view_update_busy(self) -> bool:
        # Applying a result waits for navigation; routing new input does not.
        wheel_timer = getattr(self, "_wheel_zoom_frame_timer", None)
        navigation_timer = getattr(self, "_time_navigation_timer", None)
        return bool(
            self.is_chart_interaction_busy()
            or self._chart_input_frame_active
            or getattr(self, "_pending_wheel_zoom", None) is not None
            or getattr(self, "_pending_time_navigation_value", None) is not None
            or (wheel_timer is not None and wheel_timer.isActive())
            or (navigation_timer is not None and navigation_timer.isActive())
        )

    def _try_admit_chart_input_frame(self, *, history_source: str) -> bool:
        canvas = getattr(self, "canvas", None)
        if self._chart_input_frame_active or canvas is None:
            return False
        self._chart_input_frame_generation += 1
        self._chart_input_frame_active = True
        self._chart_input_frame_canvas = canvas
        self._chart_input_frame_release_scheduled = False
        self._chart_input_frame_history_source = history_source
        return True

    def _cancel_chart_input_frame(self) -> None:
        self._chart_input_frame_generation += 1
        self._chart_input_frame_active = False
        self._chart_input_frame_canvas = None
        self._chart_input_frame_release_scheduled = False
        self._chart_input_frame_history_source = "navigation"

    def _chart_input_frame_painted(self, canvas) -> None:
        if sip.isdeleted(self):
            return
        if (
            not self._chart_input_frame_active
            or canvas is not self._chart_input_frame_canvas
            or self._chart_input_frame_release_scheduled
        ):
            return
        generation = self._chart_input_frame_generation
        # Painting is complete: accept fresh input now; notify idle later.
        self._chart_input_frame_active = False
        self._chart_input_frame_release_scheduled = True
        try:
            QTimer.singleShot(
                0,
                lambda expected=generation, owner=canvas: (
                    self._release_chart_input_frame(expected, owner)
                ),
            )
        except RuntimeError:
            self._cancel_chart_input_frame()

    def _release_chart_input_frame(self, generation: int, canvas) -> None:
        if sip.isdeleted(self):
            return
        if (
            generation != self._chart_input_frame_generation
            or not self._chart_input_frame_release_scheduled
            or canvas is not self._chart_input_frame_canvas
        ):
            return
        if sip.isdeleted(canvas):
            self._cancel_chart_input_frame()
            return
        history_source = self._chart_input_frame_history_source
        self._chart_input_frame_active = False
        self._chart_input_frame_canvas = None
        self._chart_input_frame_release_scheduled = False
        self._chart_input_frame_history_source = "navigation"
        self._notify_chart_navigation_idle(history_source=history_source)

    def _on_chart_interaction_started(self) -> None:
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._chart_interaction_epoch += 1
        canvas = getattr(self, "canvas", None)
        self._history_pointer_gesture = bool(
            canvas is not None and canvas.is_pointer_interaction_active()
        )
        if canvas is not None:
            canvas._time_pan_gesture_delta_x = 0.0

    def _on_chart_interaction_ended(
        self, cancelled: bool, *, history_source: str = "navigation",
    ) -> None:
        if not cancelled and self._chart_input_frame_active:
            return
        pointer_gesture = getattr(self, "_history_pointer_gesture", False)
        self._history_pointer_gesture = False
        canvas = getattr(self, "canvas", None)
        browse_older = (
            not pointer_gesture
            or (canvas is not None and canvas._time_pan_gesture_delta_x > 0)
        )
        if cancelled:
            self._invalidate_user_background_results()
            self._discard_deferred_visualization_projection()
        else:
            self._resume_deferred_visualization_projection(
                self._chart_interaction_epoch
            )
        self.chart_interaction_ended.emit(
            self._chart_interaction_epoch,
            bool(cancelled),
        )
        if (
            not cancelled
            and history_source != "price"
            and self._history_extension_is_needed()
            and (history_source != "navigation" or browse_older)
        ):
            self._maybe_request_history_extension(source=history_source)

    def _discard_chart_input(self) -> None:
        self._deferred_wheel_zoom = None
        self._cancel_chart_input_frame()
        self._cancel_pending_time_navigation()
        scrollbar = getattr(self, "time_navigation_scrollbar", None)
        if scrollbar is not None and scrollbar.isSliderDown():
            blocked = scrollbar.blockSignals(True)
            try:
                scrollbar.setSliderDown(False)
            finally:
                scrollbar.blockSignals(blocked)
        self._cancel_pending_wheel_zoom()
        self._history_extension_intent_generation += 1
        self._history_extension_intent_source = None
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.discard_pointer_interaction()

    def _invalidate_user_background_results(self) -> None:
        self.clear_candle_preview(self._candle_preview_token)
        self._cancel_visualization_projection_work()
        self.chart_input_invalidated.emit()

    def _maybe_request_history_extension(
        self, *, source: str = "navigation", continued_wheel_prepend: bool = False,
    ) -> None:
        if (
            source == "wheel"
            and self._visible_candle_span >= self._MAX_VISIBLE_CANDLE_SPAN
            and (
                self._historical_candle_count >= 5000
                or self._history_extension_intent_source == "navigation"
            )
        ):
            return
        self._history_extension_intent_generation += 1
        # A still-pending genuine navigation request must survive intervening
        # wheel steps. The wheel can delay it, but cannot relabel it as its own.
        if source == "wheel" and self._history_extension_intent_source == "navigation":
            source = "navigation"
        self._history_extension_intent_source = source
        generation = self._history_extension_intent_generation
        if not self._history_extension_is_needed():
            self._history_extension_intent_source = None
            return
        QTimer.singleShot(
            0 if continued_wheel_prepend and source == "wheel"
            else self._HISTORY_EXTENSION_IDLE_DELAY_MS,
            lambda expected=generation: self._emit_history_extension_if_idle(expected),
        )

    def _emit_history_extension_if_idle(self, generation: int) -> None:
        if (
            generation != self._history_extension_intent_generation
            or self._history_extension_intent_source is None
        ):
            return
        if self.is_chart_view_update_busy():
            self._history_extension_retry_generation = generation
            return
        if not self._history_extension_is_needed():
            self._history_extension_intent_source = None
            return
        # Provenance belongs to one pending request, not all future wheels.
        self._history_extension_intent_source = None
        try:
            self.historical_extension_requested.emit()
        except RuntimeError:
            return

    def _zoom_time_scale_at(self, cursor_x: float, wheel_delta: int) -> None:
        self._flush_pending_time_navigation()
        if self.is_chart_interaction_busy():
            return
        self._flush_pending_wheel_zoom()
        target = self._time_zoom_target(
            cursor_x,
            wheel_delta,
            self._visible_start_index,
            self._visible_candle_span,
        )
        if target is None:
            return
        self._apply_time_zoom_target(*target)

    def _time_zoom_target(
        self,
        cursor_x: float,
        wheel_delta: int,
        current_start: float,
        current_span: float,
        *,
        accumulate: bool = True,
    ) -> tuple[float, float] | None:
        canvas = getattr(self, "canvas", None)
        cursor = _finite_number(cursor_x)
        delta = _finite_number(wheel_delta)
        start_value = _finite_number(current_start)
        span_value = _finite_number(current_span)
        if (
            canvas is None
            or cursor is None
            or delta is None
            or not delta
            or start_value is None
            or span_value is None
            or span_value <= 0
            or not self._candles
        ):
            return None
        if accumulate:
            now = time.monotonic()
            previous_at = self._time_zoom_wheel_last_input_at
            if (
                previous_at is None
                or now - previous_at > self._TIME_ZOOM_INTENT_RESET_SECONDS
                or (
                    self._time_zoom_wheel_accumulator
                    and (self._time_zoom_wheel_accumulator > 0) != (delta > 0)
                )
            ):
                self._time_zoom_wheel_accumulator = 0
            self._time_zoom_wheel_last_input_at = now
            self._time_zoom_wheel_accumulator += int(delta)
            if abs(self._time_zoom_wheel_accumulator) < self._TIME_ZOOM_INTENT_DELTA:
                return None
            effective_wheel_delta = self._time_zoom_wheel_accumulator
            self._time_zoom_wheel_accumulator = 0
            self._time_zoom_wheel_last_input_at = None
        else:
            effective_wheel_delta = int(delta)

        plot_width = _finite_number(canvas._plot_width())
        canvas_width = _finite_number(canvas.width())
        if plot_width is None or plot_width <= 0 or canvas_width is None:
            return None
        bounded_x = min(
            canvas_width - canvas._RIGHT,
            max(float(canvas._LEFT), cursor),
        )
        anchor_ratio = min(
            1.0,
            max(
                0.0,
                (bounded_x - canvas._LEFT) / plot_width,
            ),
        )
        wheel_steps = abs(float(effective_wheel_delta)) / 120.0
        factor = self._TIME_ZOOM_FACTOR ** wheel_steps
        requested_span = (
            span_value / factor
            if effective_wheel_delta > 0
            else max(span_value, self._deferred_wheel_zoom[0]
                     if self._deferred_wheel_zoom else span_value) * factor
        )
        if (effective_wheel_delta < 0 and requested_span > len(self._candles)
                and span_value < self._MAX_VISIBLE_CANDLE_SPAN
                and self._historical_pool_installed):
            settings = self._result_settings_snapshot
            snapshot = self._replay_snapshot
            self._deferred_wheel_zoom = (
                min(requested_span, self._MAX_VISIBLE_CANDLE_SPAN), anchor_ratio,
                snapshot.timeframe_key if isinstance(snapshot, ValidationReplaySnapshot)
                else self._normal_base_timeframe_key,
                settings.to_dict() if settings is not None else None,
                snapshot.settings_hash if isinstance(snapshot, ValidationReplaySnapshot) else None,
            )
        span = self._clamped_visible_candle_span(requested_span)
        if span == span_value:
            if (effective_wheel_delta < 0
                    and span >= self._MAX_VISIBLE_CANDLE_SPAN
                    and start_value > 0
                    and self._historical_pool_installed):
                # Once zoom saturates, the same wheel input navigates older candles.
                older_step = min(500.0, max(1.0, span * 0.04 * wheel_steps))
                return max(0.0, start_value - older_step), span
            # Either the loaded-data boundary or the display cap can need history.
            if (
                effective_wheel_delta < 0
                and (span >= len(self._candles)
                     or span >= self._MAX_VISIBLE_CANDLE_SPAN)
                and self._historical_pool_installed
            ):
                self._maybe_request_history_extension(source="wheel")
            return None
        start = start_value + anchor_ratio * (span_value - span)
        start = min(self._maximum_visible_start(span), max(0.0, start))
        return start, span

    def _apply_time_zoom_target(self, start: float, span: float) -> None:
        shrinking = span < self._visible_candle_span
        wheel_pan = (
            math.isclose(span, self._visible_candle_span, rel_tol=1e-12, abs_tol=1e-12)
            and not math.isclose(start, self._visible_start_index, rel_tol=1e-12, abs_tol=1e-12)
        )
        self._set_time_view(
            start,
            span,
            manually_adjusted=True,
            auto_fit_price=not wheel_pan,
            user_horizontal_navigation=True,
        )
        if shrinking and self._history_extension_intent_source == "wheel":
            self._history_extension_intent_generation += 1
            self._history_extension_intent_source = None
            self._history_extension_retry_generation = None
        if not shrinking and (self._history_extension_is_needed() or self._history_extension_intent_source):
            self._maybe_request_history_extension(source="wheel")

    def _resume_history_wheel_zoom(
        self, *, timeframe_key: str,
        settings_snapshot: ValidationSettingsSnapshot | None = None,
        settings_hash: str | None = None,
    ) -> None:
        candidate = self._history_wheel_zoom_candidate
        self._history_wheel_zoom_candidate = None
        if candidate is None:
            return
        intent, old_display, added = candidate
        desired, ratio, old_timeframe, old_settings, old_hash = intent
        if (timeframe_key != old_timeframe
                or (settings_snapshot is not None
                    and settings_snapshot.to_dict() != old_settings)
                or (settings_hash is not None and settings_hash != old_hash)
                or added <= 0 or len(self._candles) <= len(old_display)
                or self._candles[-len(old_display):] != old_display):
            return
        span = min(desired, float(len(self._candles)), self._MAX_VISIBLE_CANDLE_SPAN)
        start = self._visible_start_index + ratio * (self._visible_candle_span - span)
        self._set_time_view(start, span, manually_adjusted=True, auto_fit_price=True)
        if desired > span and added >= 500 and len(self._calculation_candles) < 5000:
            self._deferred_wheel_zoom = intent
            self._maybe_request_history_extension(
                source="wheel", continued_wheel_prepend=True,
            )

    def _queue_time_scale_wheel(self, cursor_x: float, wheel_delta: int) -> None:
        self._queue_wheel_zoom("time", cursor_x, wheel_delta)

    def _pan_chart_target(
        self,
        delta_x: float,
        delta_y: float,
    ) -> tuple[str, float, float] | None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return None

        dx = _finite_number(delta_x)
        dy = _finite_number(delta_y)
        if dx is None or dy is None:
            return None
        if dx:
            pixels = _finite_number(canvas.pixels_per_candle)
            if pixels is None or pixels <= 0:
                return None
            start = min(
                self._maximum_visible_start(self._visible_candle_span),
                max(0.0, self._visible_start_index - dx / pixels),
            )
            if math.isclose(
                start,
                self._visible_start_index,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                return None
            return "time", start, self._visible_candle_span

        if not dy:
            return None
        scale = canvas.price_scale()
        if scale is None:
            return None
        minimum = _finite_number(scale.minimum)
        maximum = _finite_number(scale.maximum)
        plot_top = _finite_number(scale.plot_top)
        plot_bottom = _finite_number(scale.plot_bottom)
        if None in (minimum, maximum, plot_top, plot_bottom):
            return None
        current_range = maximum - minimum
        plot_height = plot_bottom - plot_top
        if current_range <= 0 or plot_height <= 0:
            return None
        price_shift = dy / plot_height * current_range
        if not math.isfinite(price_shift) or not price_shift:
            return None
        return "price", minimum + price_shift, maximum + price_shift

    def _apply_pan_chart_target(self, target: tuple[str, float, float]) -> None:
        axis, minimum, maximum = target
        if axis == "time":
            moving_to_older = minimum < self._visible_start_index
            self._set_time_view(
                minimum,
                maximum,
                manually_adjusted=True,
                auto_fit_price=False,
                user_horizontal_navigation=True,
            )
            if moving_to_older and (
                self._history_extension_is_needed() or self._history_extension_intent_source
            ):
                self._maybe_request_history_extension()
            elif not moving_to_older and self._history_extension_intent_source == "navigation":
                # Reversing toward the latest candle cancels stale older-browse intent.
                self._history_extension_intent_generation += 1
                self._history_extension_intent_source = None
            return
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        self._current_price_minimum = minimum
        self._current_price_maximum = maximum
        self._price_scale_manually_adjusted = True
        canvas.set_price_view(minimum, maximum)
        self.fixed_price_axis.refresh_scale()

    def _pan_chart_view(self, delta_x: float, delta_y: float) -> None:
        target = self._pan_chart_target(delta_x, delta_y)
        if target is not None:
            self._apply_pan_chart_target(target)

    def _handle_pan_requested(self, delta_x: float, delta_y: float) -> None:
        target = self._pan_chart_target(delta_x, delta_y)
        if target is None:
            return
        if target[0] == "time":
            self._deferred_wheel_zoom = None
            self._history_wheel_zoom_candidate = None
        if not self._chart_input_frame_active and not self._try_admit_chart_input_frame(history_source="navigation"):
            return
        try:
            self._apply_pan_chart_target(target)
            self.canvas.update()
        except Exception:
            self._cancel_chart_input_frame()
            raise

    def _sync_time_navigation_scrollbar(self) -> None:
        scrollbar = getattr(self, "time_navigation_scrollbar", None)
        if scrollbar is None:
            return
        units = self._TIME_SCROLL_UNITS_PER_CANDLE
        maximum = round(self._maximum_visible_start() * units)
        value = round(self._visible_start_index * units)
        page_step = max(1, round(self._visible_candle_span * units))
        self._syncing_time_navigation_scrollbar = True
        try:
            scrollbar.setRange(0, maximum)
            scrollbar.setPageStep(page_step)
            scrollbar.setSingleStep(units)
            scrollbar.setValue(min(maximum, max(0, value)))
            scrollbar.setVisible(True)
            scrollbar.setEnabled(maximum > 0)
        finally:
            self._syncing_time_navigation_scrollbar = False

    def _cancel_pending_time_navigation(self) -> None:
        timer = getattr(self, "_time_navigation_timer", None)
        if timer is not None:
            timer.stop()
        self._pending_time_navigation_value = None

    def _flush_pending_time_navigation(self) -> None:
        value = getattr(self, "_pending_time_navigation_value", None)
        self._cancel_pending_time_navigation()
        if value is not None:
            self._queue_time_navigation_scrollbar(value)

    def _notify_chart_navigation_idle(self, *, history_source: str = "navigation") -> None:
        if not self.is_chart_view_update_busy():
            self._on_chart_interaction_ended(False, history_source=history_source)
            retry = self._history_extension_retry_generation
            self._history_extension_retry_generation = None
            if retry is not None:
                self._emit_history_extension_if_idle(retry)

    def _on_time_navigation_timeout(self) -> None:
        self._flush_pending_time_navigation()
        self._notify_chart_navigation_idle()

    def _begin_time_navigation(self) -> None:
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._cancel_pending_wheel_zoom()
        canvas = getattr(self, "canvas", None)
        if canvas is not None and canvas.is_pointer_interaction_active():
            canvas.discard_pointer_interaction()
        self._on_chart_interaction_started()

    def _finish_time_navigation(self) -> None:
        self._flush_pending_time_navigation()
        self._notify_chart_navigation_idle()

    def _queue_time_navigation_scrollbar(self, value: int) -> None:
        if self._syncing_time_navigation_scrollbar:
            return
        canvas = getattr(self, "canvas", None)
        if canvas is not None and canvas.is_pointer_interaction_active():
            canvas.discard_pointer_interaction()
            self._cancel_chart_input_frame()
        requested = _finite_number(value)
        if requested is None:
            self._cancel_pending_time_navigation()
            self._sync_time_navigation_scrollbar()
            return
        maximum_value = round(
            self._maximum_visible_start(self._visible_candle_span)
            * self._TIME_SCROLL_UNITS_PER_CANDLE
        )
        requested_value = min(
            maximum_value,
            max(0, int(round(requested))),
        )
        if self._chart_input_frame_active:
            self._pending_time_navigation_value = requested_value
            self._time_navigation_timer.start()
            return
        self._cancel_pending_time_navigation()
        start = requested_value / self._TIME_SCROLL_UNITS_PER_CANDLE
        if math.isclose(
            start,
            self._visible_start_index,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            self._sync_time_navigation_scrollbar()
            return
        if not self._try_admit_chart_input_frame(history_source="navigation"):
            self._pending_time_navigation_value = requested_value
            self._time_navigation_timer.start()
            return
        try:
            self._set_time_view(
                start,
                self._visible_candle_span,
                manually_adjusted=True,
                auto_fit_price=True,
                user_horizontal_navigation=True,
            )
            self._maybe_request_history_extension()
            self.canvas.update()
        except Exception:
            self._cancel_chart_input_frame()
            self._sync_time_navigation_scrollbar()
            raise

    def _time_navigation_scrollbar_changed(self, value: int) -> None:
        if self._syncing_time_navigation_scrollbar:
            return
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._cancel_pending_wheel_zoom()
        start = float(value) / self._TIME_SCROLL_UNITS_PER_CANDLE
        self._set_time_view(
            start,
            self._visible_candle_span,
            manually_adjusted=True,
            auto_fit_price=True,
            user_horizontal_navigation=True,
        )
        self._maybe_request_history_extension()

    def _zoom_price_scale_at(self, cursor_y: float, wheel_delta: int) -> None:
        self._flush_pending_time_navigation()
        if self.is_chart_interaction_busy():
            return
        self._flush_pending_wheel_zoom()
        canvas = getattr(self, "canvas", None)
        scale = None if canvas is None else canvas.price_scale()
        if scale is None:
            return
        target = self._price_zoom_target(
            cursor_y,
            wheel_delta,
            scale.minimum,
            scale.maximum,
            scale.plot_top,
            scale.plot_bottom,
        )
        if target is None:
            return
        self._apply_price_zoom_target(*target)

    def _price_zoom_target(
        self,
        cursor_y: float,
        wheel_delta: int,
        current_minimum: float,
        current_maximum: float,
        plot_top: float,
        plot_bottom: float,
    ) -> tuple[float, float] | None:
        cursor = _finite_number(cursor_y)
        delta = _finite_number(wheel_delta)
        minimum_value = _finite_number(current_minimum)
        maximum_value = _finite_number(current_maximum)
        top = _finite_number(plot_top)
        bottom = _finite_number(plot_bottom)
        if (
            cursor is None
            or delta is None
            or not delta
            or None in (minimum_value, maximum_value, top, bottom)
            or bottom <= top
        ):
            return None
        default_minimum = _finite_number(self._default_price_minimum)
        default_maximum = _finite_number(self._default_price_maximum)
        if default_minimum is None or default_maximum is None:
            return None
        default_range = default_maximum - default_minimum
        if default_range <= 0:
            return None
        current_range = maximum_value - minimum_value
        if current_range <= 0:
            return None
        wheel_steps = abs(delta) / 120.0
        factor = self._PRICE_ZOOM_FACTOR ** wheel_steps
        requested_range = (
            current_range / factor if delta > 0 else current_range * factor
        )
        minimum_range = max(default_range * self._MIN_PRICE_RANGE_RATIO, 1e-9)
        maximum_range = default_range * self._MAX_PRICE_RANGE_RATIO
        new_range = min(maximum_range, max(minimum_range, requested_range))
        if math.isclose(new_range, current_range, rel_tol=1e-12, abs_tol=1e-12):
            return None
        bounded_y = min(bottom, max(top, cursor))
        cursor_ratio = (bounded_y - top) / (bottom - top)
        anchor_price = maximum_value - cursor_ratio * current_range
        maximum = anchor_price + cursor_ratio * new_range
        minimum = maximum - new_range
        if maximum < default_minimum:
            maximum = default_minimum
            minimum = maximum - new_range
        elif minimum > default_maximum:
            minimum = default_maximum
            maximum = minimum + new_range
        return minimum, maximum

    def _apply_price_zoom_target(self, minimum: float, maximum: float) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        self._current_price_minimum = minimum
        self._current_price_maximum = maximum
        self._price_scale_manually_adjusted = True
        canvas.set_price_view(minimum, maximum)
        self.fixed_price_axis.refresh_scale()

    def _queue_price_scale_wheel(self, cursor_y: float, wheel_delta: int) -> None:
        self._queue_wheel_zoom("price", cursor_y, wheel_delta)

    def _queue_wheel_zoom(
        self,
        axis: str,
        cursor_position: float,
        wheel_delta: int,
    ) -> None:
        delta = _finite_number(wheel_delta)
        if delta is None or not delta:
            return
        if self.is_chart_interaction_busy():
            return
        pending = self._pending_wheel_zoom
        if pending is not None and pending[0] != axis:
            # Time auto-fit changes the default price range; an axis handover
            # must observe that result before applying its own nonlinear math.
            self._flush_pending_wheel_zoom()
            pending = None
        if axis == "time":
            if delta > 0:
                self._deferred_wheel_zoom = None
                self._history_wheel_zoom_candidate = None
            target = self._time_zoom_target(
                cursor_position,
                int(delta),
                self._visible_start_index if pending is None else pending[1],
                self._visible_candle_span if pending is None else pending[2],
            )
        else:
            canvas = getattr(self, "canvas", None)
            scale = None if canvas is None else canvas.price_scale()
            if scale is None:
                return
            target = self._price_zoom_target(
                cursor_position,
                int(delta),
                scale.minimum if pending is None else pending[1],
                scale.maximum if pending is None else pending[2],
                scale.plot_top,
                scale.plot_bottom,
            )
        if target is None:
            return
        if not self._chart_input_frame_active and not self._try_admit_chart_input_frame(
            history_source="price" if axis == "price" else "wheel"
        ):
            return
        self._cancel_pending_time_navigation()
        # Store the running result, not a summed delta: each input has already
        # applied its own cursor anchor, saturation and signed zoom in order.
        self._pending_wheel_zoom = (axis, *target)
        if not self._wheel_zoom_frame_timer.isActive():
            self._wheel_zoom_frame_timer.start(0)

    def _wheel_zoom_frame_timeout(self) -> None:
        if not self._flush_pending_wheel_zoom():
            # A wheel frame ending is not a new pan/scrollbar history request.
            self._notify_chart_navigation_idle(history_source="wheel")

    @_perf_stage('input.wheel_flush')
    def _flush_pending_wheel_zoom(self) -> bool:
        timer = getattr(self, "_wheel_zoom_frame_timer", None)
        if timer is not None:
            timer.stop()
        pending = self._pending_wheel_zoom
        self._pending_wheel_zoom = None
        if pending is None:
            return False
        try:
            axis, minimum, maximum = pending
            if axis == "time":
                self._apply_time_zoom_target(minimum, maximum)
            else:
                self._apply_price_zoom_target(minimum, maximum)
        except Exception:
            self._cancel_chart_input_frame()
            raise
        return True

    def _cancel_pending_wheel_zoom(self, *, reset_intent: bool = True) -> None:
        timer = getattr(self, "_wheel_zoom_frame_timer", None)
        if timer is not None:
            timer.stop()
        self._pending_wheel_zoom = None
        if reset_intent:
            self._time_zoom_wheel_accumulator = 0
            self._time_zoom_wheel_last_input_at = None
            self._deferred_wheel_zoom = None


    def _collect_validation_execution_state(self) -> dict[str, Any]:
        repeat_mode = {
            "회차기준": "ROUND",
            "예산기준": "BUDGET",
            "능동매수": "ACTIVE_BUY",
        }.get(self.validation_repeat_mode_combo.currentText(), "ROUND")
        round_operator = (
            "MULTIPLY"
            if self.validation_round_operator_combo.currentText().lower() == "x"
            else "ADD"
        )
        active_direction = {
            "상향": "UP",
            "하향": "DOWN",
            "상하": "BOTH",
        }.get(self.validation_active_direction_combo.currentText(), "UP")
        active_compare = {
            "이상": ">=",
            "이하": "<=",
            "이내": "WITHIN",
            "이탈": "OUTSIDE",
        }.get(self.validation_active_compare_combo.currentText(), ">=")
        return project_validation_execution_state({
            "validation_execution": {
                "enabled": self.validation_averaging_enabled_check.isChecked(),
                "first_buy_quantity": self.validation_first_buy_quantity_line.text(),
                "repeat_mode": repeat_mode,
                "round_operator": round_operator,
                "round_budget_value": self.validation_round_budget_line.text(),
                "budget_ratio": self.validation_budget_ratio_line.text(),
                "active_direction": active_direction,
                "active_ratio": self.validation_active_ratio_line.text(),
                "active_compare": active_compare,
                "trading_cost_enabled": (
                    self.validation_trading_cost_enabled_check.isChecked()
                ),
                "trading_cost_percent": (
                    self.validation_trading_cost_percent_line.text()
                ),
            }
        })

    def collect_indicator_follow_ui_state(self):
        state = super().collect_indicator_follow_ui_state()
        if hasattr(self, "validation_repeat_mode_combo"):
            state["validation_execution"] = self._collect_validation_execution_state()
        if hasattr(self, "validation_regular_market_only_check"):
            state["validation_market_scope"] = project_validation_market_scope({
                "validation_market_scope": {
                    "regular_market_only": (
                        self.validation_regular_market_only_check.isChecked()
                    ),
                },
            })
        return state

    def _apply_projected_signal_validation_ui_state(self, projected):
        result = super()._apply_projected_signal_validation_ui_state(projected)
        execution = (
            projected.get("validation_execution")
            if isinstance(projected, dict)
            else None
        )
        if not isinstance(execution, dict) or not hasattr(
            self, "validation_repeat_mode_combo"
        ):
            return result
        policy = project_validation_execution_state({
            "validation_execution": execution
        })
        averaging_blocker = QSignalBlocker(
            self.validation_averaging_enabled_check
        )
        self.validation_averaging_enabled_check.setChecked(policy["enabled"])
        del averaging_blocker
        self.validation_execution_detail_widget.setEnabled(True)
        self.validation_first_buy_quantity_line.setText(
            str(policy["first_buy_quantity"])
        )
        self.validation_repeat_mode_combo.setCurrentText({
            "ROUND": "회차기준",
            "BUDGET": "예산기준",
            "ACTIVE_BUY": "능동매수",
        }[policy["repeat_mode"]])
        self.validation_round_operator_combo.setCurrentText(
            "x" if policy["round_operator"] == "MULTIPLY" else "+"
        )
        self.validation_round_budget_line.setText(
            str(policy["round_budget_value"])
        )
        self.validation_budget_ratio_line.setText(str(policy["budget_ratio"]))
        self.validation_active_direction_combo.setCurrentText({
            "UP": "상향",
            "DOWN": "하향",
            "BOTH": "상하",
        }[policy["active_direction"]])
        self.validation_active_compare_combo.setCurrentText({
            ">=": "이상",
            "<=": "이하",
            "WITHIN": "이내",
            "OUTSIDE": "이탈",
        }[policy["active_compare"]])
        self.validation_active_ratio_line.setText(str(policy["active_ratio"]))
        self._sync_validation_execution_lock_state()
        trading_cost_blocker = QSignalBlocker(
            self.validation_trading_cost_enabled_check
        )
        self.validation_trading_cost_enabled_check.setChecked(
            policy["trading_cost_enabled"]
        )
        del trading_cost_blocker
        self.validation_trading_cost_percent_line.setEnabled(True)
        self.validation_trading_cost_percent_line.setText(
            str(policy["trading_cost_percent"])
        )

        market_scope = project_validation_market_scope(
            projected if isinstance(projected, dict) else {}
        )
        regular_blocker = QSignalBlocker(
            self.validation_regular_market_only_check
        )
        self.validation_regular_market_only_check.setChecked(
            market_scope["regular_market_only"]
        )
        del regular_blocker

        result.setdefault("applied", []).append("validation_execution")
        result.setdefault("applied", []).append("validation_market_scope")
        return result

    def load_rules(self) -> None:
        self.rules_data = self._signal_validation_seed.settings_snapshot.to_dict()
        self.rules = deepcopy(self.rules_data)
        # Entry restoration must retain unresolved legacy operands for an
        # explicit operator re-selection.  The strict apply projection is for
        # applying a validated V2 candidate back to its parent dialog.
        self.restore_signal_validation_entry_ui_state(
            self._signal_validation_seed.to_ui_state()
        )
        self.compact_stock_display.set_current_stock(self.stock)

    def _show_with_initial_control_section_state(self) -> None:
        self.showNormal()
        self._apply_control_section_mode("summary", force=True)

    def _defer_fit_dialog_height_to_control_mode(self, mode=None) -> None:
        QTimer.singleShot(
            0,
            lambda m=mode: self._fit_signal_validation_window_to_control_mode(m),
        )

    def _sync_control_page_size(self) -> None:
        """Keep V2 height in sync without shrinking its no-scroll page width."""
        if not hasattr(self, "control_page"):
            return

        self.control_page.setMinimumHeight(0)
        self.control_page.setMaximumHeight(16777215)
        page_layout = self.control_page.layout()
        if page_layout is not None:
            page_layout.invalidate()
            page_layout.activate()

        page_height = self.control_page.sizeHint().height()
        if page_height > 0:
            self.control_page.setFixedHeight(page_height)

        self.control_page.updateGeometry()
        control_layout = self.control_tab.layout()
        if control_layout is not None:
            control_layout.invalidate()
            control_layout.activate()

    def _current_signal_validation_control_height(self) -> int:
        self._sync_control_page_size()
        return max(
            self.control_tab.sizeHint().height(),
            self.control_page.sizeHint().height(),
        )

    def _capture_signal_validation_layout_baseline(self) -> None:
        if getattr(self, "_control_section_mode", "summary") != "summary":
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self._signal_validation_result_layout.activate()
        control_height = self._current_signal_validation_control_height()
        chart_height = max(
            self.chart_stack.minimumHeight(),
            self.chart_stack.height(),
        )
        self._fixed_chart_height = int(chart_height)
        self.chart_stack.setFixedHeight(self._fixed_chart_height)
        self._last_control_height = int(control_height)

    def _fit_signal_validation_window_to_control_mode(self, mode=None) -> None:
        if self.isMaximized() or not hasattr(self, "control_page"):
            return
        if (
            self._fixed_chart_height is None
            or self._last_control_height is None
        ):
            self._fit_signal_validation_window()
            return

        current_control_height = self._current_signal_validation_control_height()
        height_delta = current_control_height - self._last_control_height
        self._last_control_height = int(current_control_height)
        if height_delta == 0:
            return

        target_height = self.height() + height_delta
        position = self.pos()
        self._programmatic_resize_in_progress = True
        try:
            self.resize(self.width(), max(1, int(target_height)))
            self.move(position)
        finally:
            self._programmatic_resize_in_progress = False

    def _fit_signal_validation_window(self) -> None:
        initial_fit = bool(getattr(self, "_initial_natural_fit_pending", False))
        if not initial_fit or self._user_geometry_owned:
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()
            contents_hint = layout.sizeHint()
        else:
            contents_hint = self.sizeHint()
        desired_height = max(
            700,
            contents_hint.height(),
            self._required_signal_validation_window_height(),
        )
        natural_width = self._natural_signal_validation_window_width(contents_hint)
        desired_width = natural_width
        available = self._available_signal_validation_geometry()
        if available is not None:
            frame_extra_width = max(0, self.frameGeometry().width() - self.width())
            frame_extra_height = max(0, self.frameGeometry().height() - self.height())
            desired_width = max(
                desired_width,
                round(available.width() * self._INITIAL_AVAILABLE_GEOMETRY_RATIO)
                - frame_extra_width,
            )
            desired_height = max(
                desired_height,
                round(available.height() * self._INITIAL_AVAILABLE_GEOMETRY_RATIO)
                - frame_extra_height,
            )
            desired_width = min(desired_width, max(1, available.width() - frame_extra_width))
            desired_height = min(desired_height, max(1, available.height() - frame_extra_height))
        self._programmatic_resize_in_progress = True
        try:
            self.resize(int(desired_width), int(desired_height))
        finally:
            self._programmatic_resize_in_progress = False
        self._initial_natural_fit_pending = False
        self._initial_geometry_committed = True
        self._capture_signal_validation_layout_baseline()
        self._center_on_initial_screen()

    def _natural_signal_validation_window_width(self, contents_hint: QSize) -> int:
        root_layout = self.layout()
        if root_layout is None:
            return max(1, contents_hint.width())
        root_margins = root_layout.contentsMargins()
        horizontal_chrome = root_margins.left() + root_margins.right()
        return max(
            1,
            contents_hint.width(),
            self.control_tab.sizeHint().width() + horizontal_chrome,
            self.result_widget.minimumSizeHint().width() + horizontal_chrome,
            self._signal_validation_action_layout.sizeHint().width()
            + horizontal_chrome,
        )

    def _available_signal_validation_geometry(self):
        screen = self.screen()
        if screen is None:
            application = QApplication.instance()
            screen = application.primaryScreen() if application is not None else None
        return screen.availableGeometry() if screen is not None else None

    def _required_signal_validation_window_height(self) -> int:
        root_layout = self.layout()
        if root_layout is None:
            return self.sizeHint().height()
        root_layout.activate()
        self._signal_validation_result_layout.activate()
        root_margins = root_layout.contentsMargins()
        result_margins = self._signal_validation_result_layout.contentsMargins()
        control_height = max(
            self.control_tab.sizeHint().height(),
            self.control_page.sizeHint().height(),
        )
        action_height = self._signal_validation_action_layout.sizeHint().height()
        cycle_summary_height = self.completed_cycle_table.height()
        result_height = (
            self.chart_stack.minimumHeight()
            + cycle_summary_height
            + result_margins.top()
            + result_margins.bottom()
            + self._signal_validation_result_layout.spacing()
        )
        return (
            root_margins.top()
            + root_margins.bottom()
            + control_height
            + action_height
            + result_height
            + root_layout.spacing() * max(0, root_layout.count() - 1)
        )

    def changeEvent(self, event) -> None:
        QDialog.changeEvent(self, event)

    @property
    def historical_candle_count(self) -> int:
        return self._historical_candle_count

    def set_historical_candle_count(self, candle_count: int) -> None:
        if (
            isinstance(candle_count, bool)
            or not isinstance(candle_count, int)
            or candle_count <= 0
        ):
            raise ValueError("candle_count must be a positive integer")
        self._historical_candle_count = candle_count

    def commit_entry_state(self) -> _ValidationEntryState:
        """Capture the immutable V2-open baseline exactly once."""
        if self._entry_state is None:
            ui_state = project_signal_validation_ui_state(
                self._signal_validation_seed.to_ui_state()
            )
            canonical = json.dumps(
                ui_state,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stock = self.stock
            self._entry_state = _ValidationEntryState(
                canonical,
                None if stock is None else ValidationStockRef(stock.code, stock.name),
                self._historical_candle_count,
            )
        return self._entry_state

    @staticmethod
    def _signal_ui_fingerprint(ui_state: object) -> str:
        projected = project_signal_validation_ui_state(ui_state)
        canonical = json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _current_signal_ui_fingerprint(self) -> str:
        return self._signal_ui_fingerprint(
            self.collect_indicator_follow_ui_state()
        )

    def _validation_preferences_snapshot(self) -> dict[str, Any]:
        return {
            "averaging_enabled": self.validation_averaging_enabled_check.isChecked(),
            "first_buy_quantity": self.validation_first_buy_quantity_line.text(),
            "repeat_mode": self.validation_repeat_mode_combo.currentText(),
            "round_operator": self.validation_round_operator_combo.currentText(),
            "round_value": self.validation_round_budget_line.text(),
            "budget_ratio": self.validation_budget_ratio_line.text(),
            "active_direction": self.validation_active_direction_combo.currentText(),
            "active_ratio": self.validation_active_ratio_line.text(),
            "active_compare": self.validation_active_compare_combo.currentText(),
            "trading_cost_enabled": self.validation_trading_cost_enabled_check.isChecked(),
            "trading_cost_percent": self.validation_trading_cost_percent_line.text(),
            "regular_market_only": self.validation_regular_market_only_check.isChecked(),
            "filter_display_enabled": self.filter_display_check.isChecked(),
        }

    def _persist_validation_preferences(self, *_args) -> None:
        store = self._validation_preferences_store
        save = getattr(store, "save", None)
        if not callable(save):
            return
        try:
            save(self._validation_preferences_snapshot())
        except Exception:
            return

    def _restore_validation_preferences(self, payload: Mapping[str, Any]) -> None:
        controls = (
            self.validation_averaging_enabled_check,
            self.validation_first_buy_quantity_line,
            self.validation_repeat_mode_combo,
            self.validation_round_operator_combo,
            self.validation_round_budget_line,
            self.validation_budget_ratio_line,
            self.validation_active_direction_combo,
            self.validation_active_ratio_line,
            self.validation_active_compare_combo,
            self.validation_trading_cost_enabled_check,
            self.validation_trading_cost_percent_line,
            self.validation_regular_market_only_check,
            self.filter_display_check,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        try:
            bool_fields = (
                ("averaging_enabled", self.validation_averaging_enabled_check),
                ("trading_cost_enabled", self.validation_trading_cost_enabled_check),
                ("regular_market_only", self.validation_regular_market_only_check),
                ("filter_display_enabled", self.filter_display_check),
            )
            for key, control in bool_fields:
                if key in payload:
                    control.setChecked(bool(payload.get(key)))
            text_fields = (
                ("first_buy_quantity", self.validation_first_buy_quantity_line),
                ("round_value", self.validation_round_budget_line),
                ("budget_ratio", self.validation_budget_ratio_line),
                ("active_ratio", self.validation_active_ratio_line),
                ("trading_cost_percent", self.validation_trading_cost_percent_line),
            )
            for key, control in text_fields:
                if key in payload:
                    control.setText(str(payload.get(key) or ""))
            combo_fields = (
                ("repeat_mode", self.validation_repeat_mode_combo),
                ("round_operator", self.validation_round_operator_combo),
                ("active_direction", self.validation_active_direction_combo),
                ("active_compare", self.validation_active_compare_combo),
            )
            for key, control in combo_fields:
                value = str(payload.get(key) or "").strip()
                if value and control.findText(value) >= 0:
                    control.setCurrentText(value)
        finally:
            del blockers

        self.validation_repeat_stack.setCurrentIndex({
            "회차기준": 0,
            "예산기준": 1,
            "능동매수": 2,
        }.get(self.validation_repeat_mode_combo.currentText(), 0))
        self._filter_display_enabled = self.filter_display_check.isChecked()
        self._sync_validation_execution_lock_state()

    def _bind_validation_preference_tracking(self) -> None:
        if self._validation_preference_connections_bound:
            return
        controls = (
            self.validation_averaging_enabled_check,
            self.validation_trading_cost_enabled_check,
            self.validation_regular_market_only_check,
            self.filter_display_check,
        )
        for control in controls:
            control.toggled.connect(self._persist_validation_preferences)
        for control in (
            self.validation_first_buy_quantity_line,
            self.validation_round_budget_line,
            self.validation_budget_ratio_line,
            self.validation_active_ratio_line,
            self.validation_trading_cost_percent_line,
        ):
            control.textChanged.connect(self._persist_validation_preferences)
        for control in (
            self.validation_repeat_mode_combo,
            self.validation_round_operator_combo,
            self.validation_active_direction_combo,
            self.validation_active_compare_combo,
        ):
            control.currentTextChanged.connect(self._persist_validation_preferences)
        self._validation_preference_connections_bound = True

    def set_validation_preferences_store(self, store: object | None) -> None:
        self._validation_preferences_store = store
        load = getattr(store, "load", None)
        if callable(load):
            try:
                payload = load()
            except Exception:
                payload = {}
            if isinstance(payload, Mapping):
                self._restore_validation_preferences(payload)
        self._bind_validation_preference_tracking()

    def _connect_signal_ui_change_tracking(self) -> None:
        try:
            self._confirmed_signal_ui_fingerprint = (
                self._current_signal_ui_fingerprint()
            )
            self._confirmed_signal_ui_validation_warnings = (
                self._current_signal_ui_validation_warnings()
            )
        except Exception:
            self._confirmed_signal_ui_fingerprint = None
            self._confirmed_signal_ui_validation_warnings = ()
        self._signal_ui_confirmation_inputs = set()
        self._signal_ui_expression_lines = {
            self.buy_signal_expr_line,
            self.sell_signal_expr_line,
        }
        self._signal_ui_expression_buttons = set()
        self._signal_ui_confirmation_backgrounds = {
            self.control_page,
            self.buy_box,
            self.sell_box,
            self.buy_header_widget,
            self.sell_header_widget,
            self.buy_overview_filter,
            self.sell_signal_condition_1,
            self.sell_signal_condition_2,
            self.sell_signal_condition_3,
        }
        for header in (self.buy_header_widget, self.sell_header_widget):
            self._signal_ui_confirmation_backgrounds.update(
                header.findChildren(QLabel)
            )
            for button in header.findChildren(QPushButton):
                self._signal_ui_expression_buttons.add(button)
                # These connections are made after ControlTab's edit handlers,
                # so the queued confirmation observes the completed expression.
                button.clicked.connect(
                    lambda _checked=False: self._queue_signal_validation_ui_confirmation()
                )
        for widget in self._signal_ui_confirmation_backgrounds:
            widget.installEventFilter(self)
        for line_edit in self.findChildren(QLineEdit):
            self._signal_ui_confirmation_inputs.add(line_edit)
            line_edit.installEventFilter(self)
        for combo in self.findChildren(QComboBox):
            if combo is self.basic_signal_interval_combo:
                continue
            self._signal_ui_confirmation_inputs.add(combo)
            combo.installEventFilter(self)
        validation_context_checkboxes = (
            self.validation_averaging_enabled_check,
            self.validation_trading_cost_enabled_check,
        )
        display_only_checkboxes = (
            self.marking_display_check,
            self.validation_regular_market_only_check,
            self.filter_display_check,
        )
        for checkbox in self.findChildren(QCheckBox):
            if checkbox in validation_context_checkboxes or checkbox in display_only_checkboxes:
                continue
            checkbox.toggled.connect(self._on_signal_validation_ui_changed)
        self.basic_signal_interval_combo.currentTextChanged.connect(
            self._on_validation_timeframe_changed
        )
        self.validation_averaging_enabled_check.toggled.connect(
            self._on_validation_averaging_policy_changed
        )
        self.validation_trading_cost_enabled_check.toggled.connect(
            self._on_validation_execution_policy_changed
        )
        self.validation_regular_market_only_check.toggled.connect(
            self._on_validation_regular_market_display_changed
        )

    @staticmethod
    def _filter_display_all_ui_state(
        ui_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a presentation-only UI snapshot with all indicator filters visible."""
        state = deepcopy(dict(ui_state))

        buy_ui = state.get("buy_ui")
        if isinstance(buy_ui, dict):
            price_compare = buy_ui.get("price_compare")
            if isinstance(price_compare, dict):
                price_compare["check"] = True

        sell_ui = state.get("sell_ui")
        signal_conditions = (
            sell_ui.get("signal_conditions")
            if isinstance(sell_ui, dict)
            else None
        )
        if isinstance(signal_conditions, dict):
            condition_a = signal_conditions.get("condition_a")
            if isinstance(condition_a, dict):
                condition_a["gap_check"] = True
                for key in ("ocr_check", "rsi_check"):
                    condition_a[key] = True

            condition_b = signal_conditions.get("condition_b")
            if isinstance(condition_b, dict):
                condition_b["gap_check"] = True
                for key in ("price_box_check", "bollinger_check"):
                    condition_b[key] = True

            condition_c = signal_conditions.get("condition_c")
            if isinstance(condition_c, dict):
                condition_c["gap_check"] = True
                for key in ("macd_check", "array_check"):
                    condition_c[key] = True

        return state

    @staticmethod
    def _filter_display_ui_price_axis(value: Any) -> str | None:
        text = str(value or "").strip()
        normalized = text.upper()
        mapping = {
            "현재가": "CURRENT_PRICE",
            "종가": "CURRENT_PRICE",
            "CURRENT": "CURRENT_PRICE",
            "CURRENT_PRICE": "CURRENT_PRICE",
            "CLOSE": "CURRENT_PRICE",
            "평단가": "AVG_PRICE",
            "AVERAGE_PRICE": "AVG_PRICE",
            "AVG_PRICE": "AVG_PRICE",
            "주문가": "ORDER_PRICE",
            "신호가": "SIGNAL_PRICE",
            "ORDER": "ORDER_PRICE",
            "ORDER_PRICE": "ORDER_PRICE",
            "SIGNAL_PRICE": "SIGNAL_PRICE",
        }
        return mapping.get(text, mapping.get(normalized))

    @classmethod
    def _filter_display_sell_gap_visual_condition(
        cls,
        source: Mapping[str, Any],
        *,
        label: str,
    ) -> dict[str, Any] | None:
        if source.get("gap_check") is False:
            return None
        left = cls._filter_display_ui_price_axis(source.get("gap_left_combo"))
        right = cls._filter_display_ui_price_axis(source.get("gap_right_combo"))
        direction = {
            "상향": "UP",
            "하향": "DOWN",
            "상하": "BOTH",
            "UP": "UP",
            "DOWN": "DOWN",
            "BOTH": "BOTH",
        }.get(str(source.get("gap_direction_combo") or "").strip())
        compare_mode = {
            "이상": "GTE",
            "이하": "LTE",
            "이내": "WITHIN",
            "이탈": "OUTSIDE",
            "GTE": "GTE",
            "LTE": "LTE",
            "WITHIN": "WITHIN",
            "OUTSIDE": "OUTSIDE",
        }.get(str(source.get("gap_compare_combo") or "").strip())
        try:
            value = float(source.get("gap_value_line"))
        except (TypeError, ValueError):
            return None
        if (
            left is None
            or right is None
            or direction is None
            or compare_mode is None
            or not math.isfinite(value)
            or value < 0
        ):
            return None
        return {
            "enabled": True,
            "not": False,
            "target": right,
            "operator": "PERCENT_GAP",
            "compare_target": left,
            "direction": direction,
            "compare_mode": compare_mode,
            "value": value,
            "description": f"Display-only: {label} GAP condition",
            "expression_id": "GAP_0",
        }

    @classmethod
    def _filter_display_restore_sell_gap_visualization(
        cls,
        rules: dict[str, Any],
        ui_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        visual_root = rules.setdefault("validation_visualization_rules", {})
        if not isinstance(visual_root, dict):
            return rules
        sell_visual = visual_root.setdefault("sell", {})
        if not isinstance(sell_visual, dict):
            return rules
        signals = sell_visual.setdefault("signals", {})
        if not isinstance(signals, dict):
            return rules

        sell_ui = ui_state.get("sell_ui")
        signal_conditions = (
            sell_ui.get("signal_conditions")
            if isinstance(sell_ui, Mapping)
            else None
        )
        if not isinstance(signal_conditions, Mapping):
            return rules

        expression_contract = next(
            (
                signal.get("signal_expression")
                for signal in signals.values()
                if isinstance(signal, Mapping)
                and isinstance(signal.get("signal_expression"), Mapping)
            ),
            None,
        )
        for letter, group_name, signal_name in (
            ("A", "condition_a", "ui_condition_a"),
            ("B", "condition_b", "ui_condition_b"),
            ("C", "condition_c", "ui_condition_c"),
        ):
            source = signal_conditions.get(group_name)
            if not isinstance(source, Mapping):
                continue
            gap_condition = cls._filter_display_sell_gap_visual_condition(
                source,
                label=f"sell condition {letter}",
            )
            if gap_condition is None:
                continue

            signal = signals.get(signal_name)
            if not isinstance(signal, dict):
                signal = {
                    "enabled": True,
                    "groups_logic": "OR",
                    "groups": [],
                }
                signals[signal_name] = signal
            groups = signal.get("groups")
            if not isinstance(groups, list):
                groups = []
                signal["groups"] = groups
            if groups and isinstance(groups[0], dict):
                group = groups[0]
            else:
                group = {
                    "enabled": True,
                    "name": group_name,
                    "conditions_logic": "AND",
                    "conditions": [],
                }
                groups.insert(0, group)
            conditions = group.get("conditions")
            if not isinstance(conditions, list):
                conditions = []
                group["conditions"] = conditions
            conditions[:] = [
                condition
                for condition in conditions
                if not (
                    isinstance(condition, Mapping)
                    and (
                        str(condition.get("operator") or "").strip().upper()
                        == "PERCENT_GAP"
                        or str(condition.get("expression_id") or "")
                        .strip()
                        .upper()
                        .startswith("GAP_")
                    )
                )
            ]
            conditions.append(gap_condition)
            if (
                not isinstance(signal.get("signal_expression"), Mapping)
                and isinstance(expression_contract, Mapping)
            ):
                signal["signal_expression"] = deepcopy(expression_contract)
        return rules

    @staticmethod
    def _filter_display_side_rules(
        rules: Mapping[str, Any],
        side: str,
    ) -> Mapping[str, Any]:
        normalized_side = str(side or "").strip().upper()
        visual_root = rules.get("validation_visualization_rules")
        if isinstance(visual_root, Mapping):
            visual_side = visual_root.get(normalized_side.lower())
            if isinstance(visual_side, Mapping):
                return visual_side
        side_rules = rules.get(normalized_side.lower())
        return side_rules if isinstance(side_rules, Mapping) else {}

    @staticmethod
    def _filter_display_expression_metadata(
        rules: Mapping[str, Any],
        side: str,
    ) -> Mapping[str, Any]:
        normalized_side = str(side or "").strip().upper()
        side_rules = (
            IndicatorFollowSignalValidationV5Window
            ._filter_display_side_rules(rules, normalized_side)
        )
        if not isinstance(side_rules, Mapping):
            return {}
        if normalized_side == "BUY":
            filters = side_rules.get("filters")
            composite = (
                filters.get("composite")
                if isinstance(filters, Mapping)
                else None
            )
            expression = (
                composite.get("expression")
                if isinstance(composite, Mapping)
                else None
            )
            return expression if isinstance(expression, Mapping) else {}
        if normalized_side == "SELL":
            signals = side_rules.get("signals")
            if not isinstance(signals, Mapping):
                return {}
            for signal in signals.values():
                if not isinstance(signal, Mapping):
                    continue
                expression = signal.get("signal_expression")
                if isinstance(expression, Mapping):
                    return expression
        return {}

    @staticmethod
    def _filter_display_expression_identifiers(
        ui_state: Mapping[str, Any],
        side: str,
    ) -> tuple[str, ...]:
        basic = (
            ui_state.get("basic")
            if isinstance(ui_state.get("basic"), Mapping)
            else {}
        )
        normalized_side = str(side or "").strip().upper()
        if normalized_side == "BUY":
            field = "buy_signal_expr_line"
            allowed = {"A", "B", "C", "D"}
        elif normalized_side == "SELL":
            field = "sell_signal_expr_line"
            allowed = {"A", "B", "C"}
        else:
            return ()
        parsed = parse_condition_expression(
            basic.get(field),
            allowed_identifiers=allowed,
            allow_duplicate_identifiers=True,
        )
        if not parsed.get("ok"):
            return ()
        return tuple(dict.fromkeys(
            str(value).strip().upper()
            for value in parsed.get("identifiers", ())
            if str(value).strip().upper() in allowed
        ))

    @staticmethod
    def _filter_display_selected_evidence_keys(
        rules: Mapping[str, Any],
        side: str,
        identifiers_override: tuple[str, ...] | None = None,
    ) -> set[str]:
        normalized_side = str(side or "").strip().upper()
        expression = (
            IndicatorFollowSignalValidationV5Window
            ._filter_display_expression_metadata(rules, normalized_side)
        )
        identifiers = tuple(
            str(value or "").strip().upper()
            for value in (
                expression.get("identifiers", ())
                if identifiers_override is None
                else identifiers_override
            )
            if str(value or "").strip()
        )
        identifier_map = expression.get("identifier_map")
        if not identifiers or not isinstance(identifier_map, Mapping):
            return set()

        side_rules = (
            IndicatorFollowSignalValidationV5Window
            ._filter_display_side_rules(rules, normalized_side)
        )
        if not isinstance(side_rules, Mapping):
            return set()
        keys: set[str] = set()

        if normalized_side == "BUY":
            filters = side_rules.get("filters")
            if not isinstance(filters, Mapping):
                return set()
            for identifier in identifiers:
                filter_name = str(identifier_map.get(identifier) or "").strip()
                config = filters.get(filter_name)
                if not isinstance(config, Mapping) or config.get("enabled") is False:
                    continue
                conditions = [
                    condition
                    for condition in config.get("conditions", ())
                    if isinstance(condition, Mapping)
                    and condition.get("enabled") is not False
                ]
                if not conditions:
                    keys.add(f"buy-filter:{filter_name}")
                    continue
                for condition in conditions:
                    key = canonical_validation_condition_key(condition)
                    if key:
                        keys.add(key)
            return keys

        if normalized_side == "SELL":
            signals = side_rules.get("signals")
            if not isinstance(signals, Mapping):
                return set()
            for identifier in identifiers:
                signal_name = str(identifier_map.get(identifier) or "").strip()
                signal = signals.get(signal_name)
                if not isinstance(signal, Mapping) or signal.get("enabled") is False:
                    continue
                for group in signal.get("groups", ()):
                    if not isinstance(group, Mapping) or group.get("enabled") is False:
                        continue
                    for condition in group.get("conditions", ()):
                        if not isinstance(condition, Mapping):
                            continue
                        if condition.get("enabled") is False:
                            continue
                        key = canonical_validation_condition_key(condition)
                        if key:
                            keys.add(key)
            return keys
        return set()

    def _cache_filter_display_evidence_identities(
        self,
        active_by_marker: Mapping[tuple[int, str], tuple[str, ...]],
    ) -> None:
        by_side: dict[str, set[str]] = {
            "BUY": set(),
            "SELL": set(),
        }
        for (_index, raw_side), identities in active_by_marker.items():
            side = str(raw_side or "").strip().upper()
            if side not in by_side:
                continue
            by_side[side].update(
                str(identity or "").strip()
                for identity in identities
                if str(identity or "").strip()
            )
        self._filter_display_evidence_identities_by_side = {
            side: frozenset(values)
            for side, values in by_side.items()
        }
        self._filter_display_evidence_cache_epoch = self._visualization_data_epoch

    def _load_indicator_follow_rule_mapper(self):
        mapper = self._indicator_follow_rule_mapper_cache
        if mapper is None:
            mapper = super()._load_indicator_follow_rule_mapper()
            self._indicator_follow_rule_mapper_cache = mapper
        return mapper

    def _all_filter_display_descriptors(
        self,
        rules: Mapping[str, Any],
    ) -> tuple[ValidationFilterDescriptor, ...]:
        """Build the full presentation universe without changing evaluated rules."""
        ui_state = self.collect_indicator_follow_ui_state()
        cache_key = (
            str(getattr(self._result_settings_snapshot, "rules_hash", "")),
            self._signal_ui_fingerprint(ui_state),
        )
        if (
            self._all_filter_descriptor_cache_key == cache_key
            and self._all_filter_descriptor_cache_ui_state
            is self._result_ui_state
        ):
            return self._all_filter_descriptor_cache
        actual_display_rules = deepcopy(dict(rules))
        actual_descriptors = build_validation_filter_universe(
            actual_display_rules,
            include_disabled=True,
        )
        actual_by_identity = {
            descriptor.identity: descriptor
            for descriptor in actual_descriptors
        }

        if not isinstance(ui_state, Mapping):
            self._all_filter_descriptor_cache_key = cache_key
            self._all_filter_descriptor_cache_ui_state = self._result_ui_state
            self._all_filter_descriptor_cache = actual_descriptors
            self._all_filter_descriptor_cache_display_rules = actual_display_rules
            return actual_descriptors

        display_ui_state = self._filter_display_all_ui_state(ui_state)
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            preview = mapper.build_engine_rules_preview_from_ui_state(
                display_ui_state,
                dict(rules),
            )
            preview_rules = preview.get("preview_rules")
            if not isinstance(preview_rules, dict):
                self._all_filter_descriptor_cache_key = cache_key
                self._all_filter_descriptor_cache_ui_state = self._result_ui_state
                self._all_filter_descriptor_cache = actual_descriptors
                self._all_filter_descriptor_cache_display_rules = actual_display_rules
                return actual_descriptors
            display_rules = deepcopy(
                project_signal_validation_rules(preview_rules)
            )
            self._filter_display_restore_sell_gap_visualization(
                display_rules,
                display_ui_state,
            )
            display_descriptors = build_validation_filter_universe(
                display_rules,
                include_disabled=True,
            )
        except Exception:
            self._all_filter_descriptor_cache_key = cache_key
            self._all_filter_descriptor_cache_ui_state = self._result_ui_state
            self._all_filter_descriptor_cache = actual_descriptors
            self._all_filter_descriptor_cache_display_rules = actual_display_rules
            return actual_descriptors

        result: list[ValidationFilterDescriptor] = []
        for descriptor in display_descriptors:
            actual = actual_by_identity.get(descriptor.identity)
            supported_sides = (
                tuple(
                    side
                    for side in descriptor.sides
                    if actual is not None and side in actual.supported_sides
                )
            )
            unsupported_sides = tuple(
                side
                for side in descriptor.sides
                if side not in supported_sides
            )
            result.append(
                ValidationFilterDescriptor(
                    identity=descriptor.identity,
                    family=descriptor.family,
                    label=descriptor.label,
                    axis=descriptor.axis,
                    sides=descriptor.sides,
                    series_keys=descriptor.series_keys,
                    parameter_json=descriptor.parameter_json,
                    evidence_keys=descriptor.evidence_keys,
                    supported_sides=supported_sides,
                    unsupported_sides=unsupported_sides,
                    supported=bool(supported_sides),
                    unavailable_reason=(
                        ""
                        if supported_sides
                        else "VALIDATION_FILTER_NOT_EVALUATED"
                    ),
                )
            )
        # Complete only missing families; old Replay parameters must not add
        # stale alternatives to a family already projected from Working UI.
        projected_families = {descriptor.family for descriptor in result}
        result.extend(
            descriptor
            for descriptor in actual_descriptors
            if descriptor.family not in projected_families
        )
        self._all_filter_descriptor_cache_key = cache_key
        self._all_filter_descriptor_cache_ui_state = self._result_ui_state
        self._all_filter_descriptor_cache = tuple(result)
        self._all_filter_descriptor_cache_display_rules = display_rules
        return self._all_filter_descriptor_cache

    def _filter_display_descriptors(
        self,
        rules: Mapping[str, Any],
    ) -> tuple[ValidationFilterDescriptor, ...]:
        enabled = (
            self.filter_display_check.isChecked()
            if hasattr(self, "filter_display_check")
            else self._filter_display_enabled
        )
        self._filter_display_enabled = bool(enabled)
        return self._filter_display_descriptors_for_selection(
            rules,
            self._filter_display_enabled,
        )

    def _current_filter_display_selection(self) -> bool:
        enabled = self.filter_display_check.isChecked()
        self._filter_display_enabled = bool(enabled)
        return bool(enabled)

    def _filter_display_descriptors_for_selection(
        self,
        rules: Mapping[str, Any],
        enabled: bool,
    ) -> tuple[ValidationFilterDescriptor, ...]:
        if not enabled:
            return ()
        return self._all_filter_display_descriptors(rules)

    def _refresh_lower_filter_presentations(
        self,
        rules: Mapping[str, Any],
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> None:
        presentation_rules = rules
        if (
            descriptors == self._all_filter_descriptor_cache
            and self._all_filter_descriptor_cache_display_rules is not None
        ):
            presentation_rules = self._all_filter_descriptor_cache_display_rules
        self._visualization_lower_filter_presentations = (
            build_validation_lower_filter_presentations(
                presentation_rules,
                descriptors,
            )
            if descriptors
            else ()
        )
        (
            self._not_filter_identities,
            self._inner_not_filter_identities,
        ) = self._current_not_filter_identities(
            rules,
            descriptors,
        )
        canvas = getattr(self, "canvas", None)
        if isinstance(canvas, IndicatorFollowSignalValidationChartCanvas):
            canvas.set_not_filter_identities(
                self._not_filter_identities,
                self._inner_not_filter_identities,
            )

    @staticmethod
    def _expression_not_identifiers(
        ui_state: Mapping[str, Any],
        side: str,
    ) -> tuple[set[str], set[str], set[str]]:
        normalized_side = str(side or "").strip().upper()
        if normalized_side == "BUY":
            field = "buy_signal_expr_line"
            allowed = {"A", "B", "C", "D"}
        elif normalized_side == "SELL":
            field = "sell_signal_expr_line"
            allowed = {"A", "B", "C"}
        else:
            return set(), set(), set()
        basic = ui_state.get("basic")
        basic = basic if isinstance(basic, Mapping) else {}
        parsed = parse_condition_expression(
            basic.get(field),
            allowed_identifiers=allowed,
            allow_duplicate_identifiers=True,
        )
        if not parsed.get("ok"):
            return set(), set(), set()

        negative: set[str] = set()
        positive: set[str] = set()

        def visit(node: Any, under_not: bool = False) -> bool:
            if not isinstance(node, Mapping):
                return False
            node_type = str(node.get("type") or "").strip().lower()
            if node_type == "identifier":
                name = str(node.get("name") or "").strip().upper()
                if name not in allowed:
                    return False
                (negative if under_not else positive).add(name)
                return True
            if node_type != "binary":
                return False
            operator = str(node.get("operator") or "").strip().upper()
            if operator not in {"AND", "OR", "NOT"}:
                return False
            return visit(node.get("left"), under_not) and visit(
                node.get("right"),
                under_not or operator == "NOT",
            )

        if not visit(parsed.get("ast")):
            return set(), set(), set()
        return negative, positive, allowed

    def _current_not_filter_identities(
        self,
        rules: Mapping[str, Any],
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        if not descriptors:
            return set(), set()
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            source_rules = deepcopy(dict(rules))
            sell = source_rules.get("sell")
            if isinstance(sell, dict):
                sell["signals"] = {}
            preview = (
                self._load_indicator_follow_rule_mapper()
                .build_engine_rules_preview_from_ui_state(
                    ui_state,
                    source_rules,
                )
            )
            preview_rules = preview.get("preview_rules")
            if not isinstance(preview_rules, dict):
                return set(), set()
            actual_rules = project_signal_validation_rules(preview_rules)
            self._filter_display_restore_sell_gap_visualization(
                actual_rules,
                ui_state,
            )
        except Exception:
            return set(), set()

        result: set[tuple[str, str]] = set()
        for side in ("BUY", "SELL"):
            negative, positive, allowed = self._expression_not_identifiers(
                ui_state,
                side,
            )
            if not negative:
                continue
            ordinary = (allowed - negative) | positive
            negative_keys = self._filter_display_selected_evidence_keys(
                actual_rules,
                side,
                tuple(sorted(negative)),
            )
            ordinary_keys = self._filter_display_selected_evidence_keys(
                actual_rules,
                side,
                tuple(sorted(ordinary)),
            )
            if not negative_keys:
                continue
            for descriptor in descriptors:
                identity = str(descriptor.identity or "").strip()
                descriptor_sides = {
                    str(value or "").strip().upper()
                    for value in descriptor.sides
                }
                evidence_keys = set(descriptor.evidence_keys)
                if (
                    identity
                    and side in descriptor_sides
                    and evidence_keys.intersection(negative_keys)
                    and not evidence_keys.intersection(ordinary_keys)
                ):
                    result.add((side, identity))

        group_negative_keys: set[str] = set()
        group_ordinary_keys: set[str] = set()
        sell = actual_rules.get("sell")
        signals = sell.get("signals") if isinstance(sell, Mapping) else None
        for signal in signals.values() if isinstance(signals, Mapping) else ():
            if not isinstance(signal, Mapping) or signal.get("enabled") is False:
                continue
            groups = signal.get("groups")
            if not isinstance(groups, (list, tuple)):
                continue
            for group in groups:
                if not isinstance(group, Mapping) or group.get("enabled") is False:
                    continue
                expression = group.get("condition_expression")
                condition_keys: dict[str, str] = {}
                all_condition_keys: set[str] = set()
                ambiguous = False
                conditions = group.get("conditions")
                if not isinstance(conditions, (list, tuple)):
                    continue
                for condition in conditions:
                    if (
                        not isinstance(condition, Mapping)
                        or condition.get("enabled") is False
                    ):
                        continue
                    expression_id = str(
                        condition.get("expression_id") or ""
                    ).strip().upper()
                    key = canonical_validation_condition_key(condition)
                    if key:
                        all_condition_keys.add(key)
                    if not expression_id or not key:
                        ambiguous = True
                        continue
                    if expression_id in condition_keys:
                        ambiguous = True
                    condition_keys[expression_id] = key

                if not isinstance(expression, Mapping):
                    group_ordinary_keys.update(all_condition_keys)
                    continue
                negative_ids: set[str] = set()
                ordinary_ids: set[str] = set()

                def visit(node: Any, under_not: bool = False) -> bool:
                    if not isinstance(node, Mapping):
                        return False
                    node_type = str(node.get("type") or "").strip().lower()
                    if node_type == "identifier":
                        name = str(node.get("name") or "").strip().upper()
                        if name not in condition_keys:
                            return False
                        (negative_ids if under_not else ordinary_ids).add(name)
                        return True
                    if node_type != "binary":
                        return False
                    operator = str(node.get("operator") or "").strip().upper()
                    if operator not in {"AND", "OR", "NOT"}:
                        return False
                    return visit(node.get("left"), under_not) and visit(
                        node.get("right"), under_not or operator == "NOT",
                    )

                if ambiguous or not visit(expression):
                    group_ordinary_keys.update(all_condition_keys)
                    continue
                group_negative_keys.update(
                    condition_keys[identifier]
                    for identifier in negative_ids - ordinary_ids
                )
                group_ordinary_keys.update(
                    condition_keys[identifier]
                    for identifier in (
                        (set(condition_keys) - negative_ids) | ordinary_ids
                    )
                )

        inner_result: set[tuple[str, str]] = set()
        if group_negative_keys:
            for descriptor in descriptors:
                identity = str(descriptor.identity or "").strip()
                descriptor_sides = {
                    str(value or "").strip().upper()
                    for value in descriptor.sides
                }
                evidence_keys = set(descriptor.evidence_keys)
                if (
                    identity
                    and "SELL" in descriptor_sides
                    and evidence_keys.intersection(group_negative_keys)
                    and not evidence_keys.intersection(group_ordinary_keys)
                ):
                    inner_result.add(("SELL", identity))
        result.update(inner_result)
        return result, inner_result

    def _refresh_filter_display_projection(self) -> None:
        if self._chart_results_suspended:
            return
        snapshot = self._result_settings_snapshot
        if (
            not self._calculation_candles
            or not isinstance(snapshot, ValidationSettingsSnapshot)
        ):
            return
        selection = self._current_filter_display_selection()
        projection_ui_state = deepcopy(self.collect_indicator_follow_ui_state())
        self._visualization_projection_generation += 1
        generation = self._visualization_projection_generation
        intent = {
            "generation": generation,
            "data_epoch": self._visualization_data_epoch,
            "selection": selection,
            "ui_state": projection_ui_state,
        }
        if not selection:
            self._visualization_projection_pending = None
            descriptors: tuple[ValidationFilterDescriptor, ...] = ()
            self._apply_visualization_projection(
                descriptors,
                ValidationIndicatorSeriesCache(
                    candle_count=len(self._candles),
                    series=(),
                ),
                {},
                key=self._visualization_projection_key(descriptors),
                ocr_turn_ui_state=projection_ui_state,
            )
            return
        if self._visualization_projection_active is not None:
            self._visualization_projection_pending = intent
            return
        self._dispatch_visualization_projection(intent)

    def _invalidate_visualization_projection_data(self) -> None:
        self._visualization_data_epoch += 1
        self._signal_tooltip_provenance = None
        self._visualization_projection_source = None
        self._pending_prepared_presentation = None
        self._pending_prepared_visualization_epoch = None
        self._visualization_projection_generation += 1
        self._visualization_projection_pending = None
        self._discard_deferred_visualization_projection()
        self._visualization_projection_cache.clear()
        self._visualization_current_key = None
        self._filter_display_evidence_cache_epoch = None
        self._filter_display_evidence_identities_by_side = {
            "BUY": frozenset(),
            "SELL": frozenset(),
        }
        self._refresh_valid_filter_emphasis()

    def _cancel_visualization_projection_work(self) -> None:
        self._visualization_projection_generation += 1
        self._visualization_projection_pending = None
        self._discard_deferred_visualization_projection()

    def _discard_deferred_visualization_projection(self) -> None:
        deferred = self._visualization_projection_deferred
        self._visualization_projection_deferred = None
        if deferred is None:
            return
        _epoch, payload = deferred
        if payload.get("token") is self._visualization_projection_active:
            payload = dict(payload)
            payload["cancelled"] = True
            self._on_visualization_projection_completed(payload)

    def _resume_deferred_visualization_projection(self, epoch: int) -> None:
        deferred = self._visualization_projection_deferred
        if deferred is None:
            return
        if deferred[0] != epoch:
            if epoch > deferred[0]:
                self._discard_deferred_visualization_projection()
            return
        if self.is_chart_view_update_busy():
            return
        self._visualization_projection_deferred = None
        self._on_visualization_projection_completed(deferred[1])

    def _dispatch_visualization_projection(
        self,
        intent: dict[str, Any],
    ) -> None:
        if (
            self._visualization_projection_closed
            or self._chart_results_suspended
            or intent.get("generation")
            != self._visualization_projection_generation
            or intent.get("data_epoch") != self._visualization_data_epoch
        ):
            return
        snapshot = self._result_settings_snapshot
        if not isinstance(snapshot, ValidationSettingsSnapshot):
            return
        rules = snapshot.to_dict()
        descriptors = self._filter_display_descriptors_for_selection(
            rules,
            intent["selection"],
        )
        if (
            descriptors == self._all_filter_descriptor_cache
            and isinstance(self._all_filter_descriptor_cache_display_rules, Mapping)
        ):
            # The descriptor universe comes from the current draft UI.  Its
            # series must use the same draft rules, never the prior replay.
            rules = self._all_filter_descriptor_cache_display_rules
        key = self._visualization_projection_key(descriptors)

        cached = self._visualization_projection_cache.get(key)
        if cached is None:
            cached = self._cached_projection_for_descriptors(descriptors)
        if cached is not None:
            self._visualization_projection_pending = None
            cache, active_by_marker = cached
            self._apply_visualization_projection(
                descriptors,
                cache,
                active_by_marker,
                key=key,
                ocr_turn_ui_state=intent["ui_state"],
            )
            return

        request = self._visualization_projection_request(
            int(intent["generation"]),
            key,
            rules,
            descriptors,
            intent["ui_state"],
        )
        self._start_visualization_projection(request)

    def _visualization_projection_key(
        self,
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> tuple[Any, ...]:
        try:
            ui_fingerprint = self._current_signal_ui_fingerprint()
        except (TypeError, ValueError):
            ui_fingerprint = ""
        return (
            getattr(self._result_settings_snapshot, "rules_hash", ""),
            self._visualization_data_epoch,
            ui_fingerprint,
            tuple((
                descriptor.identity,
                descriptor.sides,
                descriptor.supported_sides,
                descriptor.unsupported_sides,
                descriptor.supported,
            ) for descriptor in descriptors),
        )

    def _cached_projection_for_descriptors(
        self,
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> tuple[
        ValidationIndicatorSeriesCache,
        dict[tuple[int, str], tuple[str, ...]],
    ] | None:
        if not descriptors:
            return ValidationIndicatorSeriesCache(
                candle_count=len(self._candles),
                series=(),
            ), {}
        cache = self._visualization_cache
        if not isinstance(cache, ValidationIndicatorSeriesCache):
            return None
        desired_key = self._visualization_projection_key(descriptors)
        current_key = self._visualization_current_key
        if (
            not isinstance(current_key, tuple)
            or current_key[:2] != desired_key[:2]
        ):
            return None
        available = {
            (identity, channel): values
            for identity, channel, values in cache.series
        }
        required = {
            (descriptor.identity, channel)
            for descriptor in descriptors
            for channel in descriptor.series_keys
        }
        if not required.issubset(available):
            return None
        identities = {descriptor.identity for descriptor in descriptors}
        filtered_cache = ValidationIndicatorSeriesCache(
            candle_count=cache.candle_count,
            series=tuple(
                (identity, channel, values)
                for identity, channel, values in cache.series
                if identity in identities
            ),
        )
        active_by_marker = {
            marker: tuple(
                identity for identity in active if identity in identities
            )
            for marker, active in self._visualization_active_by_marker.items()
        }
        return filtered_cache, active_by_marker

    def _visualization_projection_request(
        self,
        generation: int,
        key: tuple[Any, ...],
        rules: Mapping[str, Any],
        descriptors: tuple[ValidationFilterDescriptor, ...],
        ui_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        source = self._visualization_projection_source
        if source is None or source[0] != self._visualization_data_epoch:
            range_projection = None
            bounds = self._effective_validation_range()
            if self._range_replay_entries_available and bounds is not None:
                range_candles, range_entries, range_start = (
                    self._validation_range_visualization_projection()
                )
                if range_candles:
                    range_projection = (
                        tuple(range_candles),
                        tuple(range_entries),
                        tuple(range(
                            range_start,
                            range_start + len(range_candles),
                        )),
                    )
            source = (
                self._visualization_data_epoch,
                tuple(self._calculation_candles),
                tuple(self._marker_entries_for_calculation()),
                tuple(self._marker_entries_for_display()),
                tuple(self._chart_calculation_indexes),
                range_projection,
            )
            self._visualization_projection_source = source
        return {
            "generation": generation,
            "data_epoch": self._visualization_data_epoch,
            "key": key,
            "token": object(),
            "rules": rules,
            "descriptors": tuple(descriptors),
            "ui_state": deepcopy(dict(ui_state)),
            "source": source,
        }

    def _start_visualization_projection(self, request: dict[str, Any]) -> None:
        token = request["token"]
        self._visualization_projection_active = token

        def current() -> bool:
            return (
                not self._visualization_projection_closed
                and not self._chart_results_suspended
                and self._visualization_projection_active is token
                and request.get("generation")
                == self._visualization_projection_generation
                and request.get("data_epoch") == self._visualization_data_epoch
            )

        @_perf_stage("projection.build", owner=self)
        def build() -> None:
            payload = {
                "generation": request["generation"],
                "data_epoch": request["data_epoch"],
                "key": request["key"],
                "token": token,
                "descriptors": request["descriptors"],
                "ui_state": request["ui_state"],
                "cache": None,
                "active_by_marker": {},
                "error": "",
                "cancelled": False,
            }
            try:
                if not current():
                    raise ValidationProjectionCancelled(
                        "VALIDATION_PROJECTION_CANCELLED"
                    )
                (
                    _source_epoch,
                    source_candles,
                    calculation_entries,
                    display_entries,
                    chart_indexes,
                    range_projection,
                ) = request["source"]
                detached_range = None
                if range_projection is not None:
                    range_candles, range_entries, range_indexes = range_projection
                    detached_range = (
                        [dict(candle) for candle in range_candles],
                        list(range_entries),
                        range_indexes,
                    )
                cache, active_by_marker = _build_validation_projection_data(
                    [dict(candle) for candle in source_candles],
                    list(calculation_entries),
                    list(display_entries),
                    chart_indexes,
                    deepcopy(request["rules"]),
                    request["descriptors"],
                    range_projection=detached_range,
                    cancel_predicate=lambda: not current(),
                )
                if not current():
                    raise ValidationProjectionCancelled(
                        "VALIDATION_PROJECTION_CANCELLED"
                    )
                payload["cache"] = cache
                payload["active_by_marker"] = active_by_marker
            except ValidationProjectionCancelled:
                payload["cancelled"] = True
            except Exception as exc:
                payload["error"] = str(exc)
            if not current():
                payload["cancelled"] = True
                payload["cache"] = None
                payload["active_by_marker"] = {}
            try:
                self.visualization_projection_completed.emit(payload)
            except RuntimeError:
                pass

        try:
            Thread(
                target=build,
                name="indicator-follow-validation-projection",
                daemon=True,
            ).start()
        except Exception as exc:
            self._visualization_projection_active = None
            self._visualization_data_error = str(exc)
            pending = self._visualization_projection_pending
            self._visualization_projection_pending = None
            if (
                pending is not None
                and pending.get("generation")
                == self._visualization_projection_generation
                and pending.get("data_epoch") == self._visualization_data_epoch
            ):
                self._dispatch_visualization_projection(pending)

    def _on_visualization_projection_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("token") is not self._visualization_projection_active:
            return
        if (
            payload.get("cancelled") is not True
            and not self._visualization_projection_closed
            and not self._chart_results_suspended
            and payload.get("generation") == self._visualization_projection_generation
            and payload.get("data_epoch") == self._visualization_data_epoch
            and not str(payload.get("error") or "")
            and isinstance(payload.get("cache"), ValidationIndicatorSeriesCache)
            and self.is_chart_view_update_busy()
        ):
            self._visualization_projection_deferred = (
                self._chart_interaction_epoch,
                dict(payload),
            )
            return
        self._visualization_projection_active = None
        pending = self._visualization_projection_pending
        self._visualization_projection_pending = None
        if (
            not self._visualization_projection_closed
            and not self._chart_results_suspended
            and payload.get("cancelled") is not True
            and payload.get("generation")
            == self._visualization_projection_generation
            and payload.get("data_epoch") == self._visualization_data_epoch
            and not str(payload.get("error") or "")
            and isinstance(
                payload.get("cache"),
                ValidationIndicatorSeriesCache,
            )
        ):
            descriptors = tuple(payload.get("descriptors") or ())
            cache = payload["cache"]
            active_by_marker = dict(payload.get("active_by_marker") or {})
            key = payload.get("key")
            if isinstance(key, tuple):
                self._visualization_projection_cache[key] = (
                    cache,
                    active_by_marker,
                )
            self._apply_visualization_projection(
                descriptors,
                cache,
                active_by_marker,
                key=key if isinstance(key, tuple) else None,
                ocr_turn_ui_state=payload.get("ui_state"),
            )
        elif (
            not self._visualization_projection_closed
            and not self._chart_results_suspended
            and payload.get("cancelled") is not True
            and payload.get("data_epoch") == self._visualization_data_epoch
            and payload.get("generation")
            == self._visualization_projection_generation
            and str(payload.get("error") or "")
        ):
            self._visualization_data_error = str(payload["error"])
        if (
            pending is not None
            and not self._visualization_projection_closed
            and not self._chart_results_suspended
        ):
            if (
                pending.get("generation")
                == self._visualization_projection_generation
                and pending.get("data_epoch") == self._visualization_data_epoch
            ):
                self._dispatch_visualization_projection(pending)

    @_perf_stage('projection.apply')
    def _apply_visualization_projection(
        self,
        descriptors: tuple[ValidationFilterDescriptor, ...],
        cache: ValidationIndicatorSeriesCache,
        active_by_marker: dict[tuple[int, str], tuple[str, ...]],
        *,
        key: tuple[Any, ...] | None = None,
        ocr_turn_ui_state: Mapping[str, Any] | None = None,
    ) -> None:
        if self._chart_results_suspended:
            return
        cache = self._clip_comparison_cache(descriptors, cache)
        self._visualization_descriptors = tuple(descriptors)
        snapshot = self._result_settings_snapshot
        self._refresh_lower_filter_presentations(
            snapshot.to_dict()
            if isinstance(snapshot, ValidationSettingsSnapshot)
            else {},
            self._visualization_descriptors,
        )
        self._visualization_cache = cache
        self._visualization_active_by_marker = dict(active_by_marker)
        self._visualization_current_key = key
        self._visualization_data_error = ""
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        markers = self._chart_marker_records()
        delay_ui_state = (
            ocr_turn_ui_state
            if isinstance(ocr_turn_ui_state, Mapping)
            else self.collect_indicator_follow_ui_state()
        )
        canvas.set_visualization_projection(
            self._visualization_descriptors,
            self._visualization_cache,
            self._visualization_active_by_marker,
            markers,
            self._visualization_lower_filter_presentations,
            ocr_turn_delays=self._ocr_turn_delays_from_ui_state(
                delay_ui_state,
                self._visualization_descriptors,
            ),
        )
        if self.filter_display_check.isChecked():
            valid_price_keys = {
                (str(side).strip().upper(), str(descriptor.identity or "").strip())
                for descriptor in self._visualization_descriptors
                if descriptor.axis == PRICE_AXIS
                for side in descriptor.sides
                if str(side).strip().upper() in {"BUY", "SELL"}
                and str(descriptor.identity or "").strip()
            }
            self._highlighted_price_filter_identities.intersection_update(
                valid_price_keys
            )
            valid_lower_keys = {
                (
                    str(side).strip().upper(),
                    str(descriptor.identity or "").strip(),
                )
                for descriptor in self._visualization_descriptors
                if descriptor.axis == LOWER_AXIS
                for side in descriptor.sides
                if str(side).strip().upper() in {"BUY", "SELL"}
                and str(descriptor.identity or "").strip()
            }
            self._highlighted_lower_filter_identities.intersection_update(
                valid_lower_keys
            )
            self._hide_unhighlighted_price_filters = bool(
                self._highlighted_price_filter_identities
                or self._highlighted_lower_filter_identities
            )
            canvas.set_highlighted_price_filter_identities(
                self._highlighted_price_filter_identities,
                emit=False,
            )
            canvas.set_highlighted_lower_filter_identities(
                self._highlighted_lower_filter_identities,
                emit=False,
            )
            self._hidden_price_filter_identities = set(
                canvas.hidden_price_filter_identities
            )
        if self._validation_range is not None:
            canvas.set_validation_range(*self._validation_range)
        else:
            canvas.set_validation_range(None, None)
        self.fixed_price_axis.refresh_scale()
        self._refresh_valid_filter_emphasis()

    @staticmethod
    def _marking_source_key_for_candles(
        stock_code,
        settings,
        times,
        *,
        regular_only,
        calculation_candles,
        display_candles,
        prefix_start=None,
    ) -> tuple:
        # Include the indicator prefix, not just OHLC at the two endpoints.
        calculation = [c for c in calculation_candles
                       if (prefix_start is None or str(c.get("time") or "") >= prefix_start)
                       and str(c.get("time") or "") <= times[1]]
        display = list(display_candles)
        if not display or (display[0]["time"], display[-1]["time"]) != times:
            raise ValueError("MARKING_TIMESTAMP_UNAVAILABLE")
        # Display OHLCV is presentation-only; marking depends on causal
        # calculation bars and display timestamp identity, not display prices.
        display_times = tuple(str(candle.get("time") or "") for candle in display)
        digest = hashlib.sha256(json.dumps(
            (calculation, display_times), sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return (stock_code, settings.rules_hash,
                settings.to_dict()["bar"]["bar_minutes"], times,
                bool(regular_only), digest)

    def _marking_source_key(self, settings, times, *, prefix_start=None, frozen_display=None) -> tuple:
        display = (frozen_display if frozen_display is not None else [
            c for c in self._candles
            if times[0] <= str(c.get("time") or "") <= times[1]
        ])
        return self._marking_source_key_for_candles(
            self.stock.code,
            settings,
            times,
            regular_only=self.validation_regular_market_only_check.isChecked(),
            calculation_candles=self._calculation_candles,
            display_candles=display,
            prefix_start=prefix_start,
        )

    def _marking_current_settings(self):
        ui_state = self.collect_indicator_follow_ui_state()
        require_expression_aware_sell_price_selections(ui_state)
        require_resolved_buy_bollinger_sign_selection(ui_state)
        preview = self._load_indicator_follow_rule_mapper().build_engine_rules_preview_from_ui_state(
            ui_state, self._signal_validation_seed.settings_snapshot.to_dict(),
        )
        warnings = [str(w) for w in preview.get("validation_warnings", ())
                    if str(w).lower().startswith(("sell condition", "sell signal"))]
        if warnings:
            raise ValueError("; ".join(warnings))
        return build_signal_validation_snapshot(preview["preview_rules"], ui_state=ui_state)

    def _on_marking_toggled(self, checked: bool) -> None:
        if not checked:
            self._disable_marking(reason="checkbox_off")
            return
        confirmation_tooltip = getattr(
            self, "_marking_confirmation_error_tooltip", "",
        )
        if confirmation_tooltip and self.marking_display_check.toolTip() == confirmation_tooltip:
            self.marking_display_check.setToolTip("")
        self._marking_confirmation_error_tooltip = ""
        if self._validation_range is not None or self._validation_range_anchor_index is not None:
            self._clear_validation_range_interaction()
        if not self._flush_pending_signal_validation_ui_confirmation():
            detail = getattr(self, "_signal_ui_confirmation_error_detail", "")
            self.marking_display_check.setToolTip(detail)
            self._marking_confirmation_error_tooltip = detail
            with QSignalBlocker(self.marking_display_check):
                self.marking_display_check.setChecked(False)
            return
        # Confirmation may retire an older Mark.  This click is explicitly a
        # request for a fresh Mark, so retain the user's checked state before
        # capturing the new settings snapshot.
        with QSignalBlocker(self.marking_display_check):
            self.marking_display_check.setChecked(True)
        if not self.marking_display_check.isChecked():
            with QSignalBlocker(self.marking_display_check):
                self.marking_display_check.setChecked(True)
        canvas = getattr(self, "canvas", None)
        try:
            if canvas is None or self._chart_results_suspended:
                raise ValueError("MARKING_CHART_UNAVAILABLE")
            left, right = canvas._visible_index_bounds()
            if not 0 <= left < right <= len(self._candles):
                raise ValueError("MARKING_RANGE_UNAVAILABLE")
            times = (str(self._candles[left]["time"]), str(self._candles[right - 1]["time"]))
            settings = self._marking_current_settings()
            key = self._marking_source_key(settings, times)
            self._marking_generation += 1
            self._marking_times = times
            self._marking_key = key
            # Completed evidence belongs to this frozen prefix. Older prepends
            # remap timestamps; a new ON captures the expanded prefix afresh.
            self._marking_source_start = str(self._calculation_candles[0]["time"])
            self._marking_display_source = _detached_validation_candles([
                candle for candle in self._candles
                if times[0] <= str(candle.get("time") or "") <= times[1]
            ])
            self._marking_display_reprojection_pending = False
            self._marking_pool_publication_pending = False
            self._marking_policy_refresh_pending = False
            self._marking_settings = settings
            self._invalidate_visualization_projection_data()
            if self._marking_cache is not None and self._marking_cache[0] == key:
                _, snapshot, comparison = self._marking_cache
                self.apply_marking_snapshot(self._marking_generation, key, snapshot, comparison)
            else:
                self.marking_evaluation_requested.emit({
                    "generation": self._marking_generation, "key": key,
                    "times": times, "range": (left, right - 1), "settings": settings,
                    "calculation": _detached_validation_candles(self._calculation_candles),
                    "display": _detached_validation_candles(self._candles),
                    "mixed": self._display_only_integrated_active,
                    "descriptors": tuple(d for d in self._filter_display_descriptors(settings.to_dict())
                                         if d.family == FAMILY_PRICE_COMPARISON),
                })
                QTimer.singleShot(1, self._refresh_filter_display_projection)
        except (TypeError, ValueError, KeyError) as exc:
            self._disable_marking(clear_cache=True)
            self.marking_display_check.setToolTip(str(exc))

    def _disable_marking(self, *, clear_cache: bool = False, refresh: bool = True,
                         reason: str = "context_reset") -> None:
        active = self._marking_times is not None
        check = getattr(self, "marking_display_check", None)
        checkbox_on = bool(check is not None and check.isChecked())
        # Diagnostic only: capture the exact transition owner before mutation.
        # No stack inspection or output when tracing is disabled.
        if (active or checkbox_on) and signal_validation_trace_enabled():
            try:
                caller_path = []
                flow_request_generation = None
                flow_history_target = None
                failure_type = None
                failure_detail = None
                frame = sys._getframe(1)
                try:
                    for _ in range(7):
                        if frame is None:
                            break
                        code = frame.f_code
                        filename = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
                        caller_path.append((filename, code.co_name, frame.f_lineno))
                        owner = frame.f_locals.get("self")
                        generations = getattr(owner, "_request_generation", None)
                        if isinstance(generations, dict) and id(self) in generations:
                            flow_request_generation = generations.get(id(self))
                            targets = getattr(owner, "_history_targets", None)
                            if isinstance(targets, dict):
                                flow_history_target = targets.get(id(self))
                        error = frame.f_locals.get("exc")
                        if isinstance(error, BaseException) and failure_type is None:
                            failure_type = type(error).__name__
                            failure_detail = str(error)[:160]
                        frame = frame.f_back
                finally:
                    del frame
                trace_signal_validation_event(
                    "window.marking.disabled.caller",
                    window_key=id(self),
                    stock_code=self.stock.code,
                    reason=reason, clear_cache=bool(clear_cache),
                    refresh=bool(refresh), active=bool(active),
                    checkbox_on=checkbox_on,
                    caller_path=caller_path,
                    failure_type=failure_type,
                    failure_detail=failure_detail,
                    marking_generation=self._marking_generation,
                    flow_request_generation=flow_request_generation,
                    flow_history_target=flow_history_target,
                    chart_interaction_epoch=self._chart_interaction_epoch,
                    visualization_data_epoch=self._visualization_data_epoch,
                    history_intent_generation=self._history_extension_intent_generation,
                    history_intent_source=self._history_extension_intent_source,
                    timeframe=self._normal_base_timeframe_key,
                    frozen_times=self._marking_times,
                    frozen_source_start=self._marking_source_start,
                    frozen_display=trace_candle_signature(self._marking_display_source),
                    calculation=trace_candle_signature(self._calculation_candles),
                    display=trace_candle_signature(self._candles),
                    visible_start=self._visible_start_index,
                    visible_span=self._visible_candle_span,
                    chart_candle_count=self._historical_candle_count,
                )
            except Exception:
                # Diagnostics must not affect marking, GUI or broker execution.
                pass
        if _PERF_ENABLED:
            marking_probe = _perf_phase(self)
            if marking_probe is not None:
                check = getattr(self, "marking_display_check", None)
                marking_probe.checkpoint(
                    'marking.disabled', reason=reason, active=bool(active),
                    checkbox_on=check.isChecked() if check is not None else False,
                    clear_cache=bool(clear_cache),
                )
        self._marking_generation += 1
        self._marking_times = None
        self._marking_key = None
        self._marking_source_start = None
        self._marking_display_source = None
        self._marking_display_reprojection_pending = False
        self._marking_pool_publication_pending = False
        self._marking_policy_refresh_pending = False
        self._marking_snapshot = None
        self._marking_settings = None
        self._marking_comparison_cache = None
        if clear_cache:
            self._marking_cache = None
        self._refresh_valid_filter_emphasis()
        check = getattr(self, "marking_display_check", None)
        if check is not None:
            with QSignalBlocker(check):
                check.setChecked(False)
        if active and refresh:
            self._invalidate_visualization_projection_data()
            self._refresh_marking_projection()
            self._refresh_filter_display_projection()

    def _revalidate_marking_source(self) -> None:
        if (
            self._marking_times is None
            or self._marking_atomic_publication_active
            or self._marking_policy_refresh_pending
        ):
            return
        source_match = False
        display_times_match = None
        try:
            frozen = self._marking_display_source
            if not isinstance(frozen, list):
                raise ValueError("MARKING_SOURCE_DISPLAY_UNAVAILABLE")
            # The stored interval is the source identity; a viewport can omit it.
            source_match = self._marking_source_key(
                self._marking_settings, self._marking_times,
                prefix_start=self._marking_source_start, frozen_display=frozen,
            ) == self._marking_key
            valid = source_match
            visible = [
                candle for candle in self._candles
                if self._marking_times[0] <= str(candle.get("time") or "") <= self._marking_times[1]
            ]
            if valid and visible and not self._marking_display_reprojection_pending:
                left, right = str(visible[0]["time"]), str(visible[-1]["time"])
                display_times_match = tuple(str(candle.get("time") or "") for candle in visible) == tuple(
                    str(candle.get("time") or "") for candle in frozen
                    if left <= str(candle.get("time") or "") <= right
                )
                valid = display_times_match
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            trace_signal_validation_event(
                "window.marking.revalidation_failed",
                source_match=source_match,
                display_times_match=display_times_match,
                marking_times=self._marking_times,
                source_prefix_start=self._marking_source_start,
                calculation=trace_candle_signature(self._calculation_candles),
                display=trace_candle_signature(self._candles),
            )
            self._disable_marking(clear_cache=True, refresh=False,
                                  reason="source_revalidation_failed")

    def apply_marking_snapshot(self, generation, key, snapshot, comparison) -> bool:
        if generation != self._marking_generation or key != self._marking_key:
            return False
        self._revalidate_marking_source()
        if key != self._marking_key:
            self._refresh_marking_projection()
            self._refresh_filter_display_projection()
            return False
        if (not isinstance(snapshot, ValidationReplaySnapshot)
                or snapshot.stock != self.stock
                or snapshot.settings_hash != self._marking_settings.rules_hash):
            raise ValueError("MARKING_RESULT_IDENTITY_MISMATCH")
        self._marking_snapshot = snapshot
        self._marking_comparison_cache = comparison
        self._marking_cache = (key, snapshot, comparison)
        self._invalidate_visualization_projection_data()
        self._refresh_marking_projection()
        self._refresh_valid_filter_emphasis()
        self._refresh_filter_display_projection()
        return True

    def publish_marking_source_revision(
        self,
        *,
        generation,
        previous_key,
        candidate_key,
        times,
        source_start,
        settings,
        calculation_candles,
        display_candles,
        display_only_integrated,
        snapshot,
        comparison,
        normal_base_presentation,
        chart_candle_count,
        timeframe_key,
    ) -> bool:
        """Publish one fully prepared marking/candle revision in one GUI turn."""
        if (
            generation != self._marking_generation
            or previous_key != self._marking_key
            or times != self._marking_times
            or settings is not self._marking_settings
            or not self.marking_display_check.isChecked()
        ):
            return False
        if (
            not isinstance(settings, ValidationSettingsSnapshot)
            or not isinstance(snapshot, ValidationReplaySnapshot)
            or snapshot.stock != self.stock
            or snapshot.settings_hash != settings.rules_hash
            or not isinstance(calculation_candles, list)
            or not calculation_candles
            or not isinstance(display_candles, list)
            or not display_candles
            or not isinstance(comparison, tuple)
            or len(comparison) != 2
            or not isinstance(normal_base_presentation, tuple)
            or len(normal_base_presentation) != 2
        ):
            raise ValueError("MARKING_SOURCE_REVISION_RESULT_INVALID")
        candidate_display = [
            candle for candle in display_candles
            if times[0] <= str(candle.get("time") or "") <= times[1]
        ]
        verified_key = self._marking_source_key_for_candles(
            self.stock.code,
            settings,
            times,
            regular_only=self.validation_regular_market_only_check.isChecked(),
            calculation_candles=calculation_candles,
            display_candles=candidate_display,
            prefix_start=source_start,
        )
        if verified_key != candidate_key:
            raise ValueError("MARKING_SOURCE_REVISION_KEY_MISMATCH")

        self._marking_atomic_publication_active = True
        try:
            self.set_historical_candle_pool(
                calculation_candles,
                chart_candle_count=chart_candle_count,
            )
            self.set_historical_display_candles(
                display_candles,
                display_only_integrated=bool(display_only_integrated),
            )
            if display_only_integrated:
                self._display_only_integrated_candles = (
                    _detached_validation_candles(display_candles)
                )
            self._marking_key = candidate_key
            self._marking_times = times
            self._marking_source_start = source_start
            self._marking_display_source = _detached_validation_candles(
                candidate_display
            )
            self._marking_settings = settings
            self._marking_snapshot = snapshot
            self._marking_comparison_cache = comparison
            self._marking_cache = (candidate_key, snapshot, comparison)
            self.install_normal_base(
                settings,
                timeframe_key,
                presentation=normal_base_presentation,
            )
            self._marking_display_reprojection_pending = False
            self._marking_pool_publication_pending = False
        finally:
            self._marking_atomic_publication_active = False
        self._revalidate_marking_source()
        if self._marking_key != candidate_key:
            raise ValueError("MARKING_SOURCE_REVISION_REVALIDATION_FAILED")
        self._invalidate_visualization_projection_data()
        self._refresh_marking_projection()
        self._refresh_filter_display_projection()
        return True

    def _chart_marker_records(self) -> list[dict[str, Any]]:
        self._revalidate_marking_source()
        selected_times = (
            self._validation_range_times
            if self._validation_range is not None else None
        )
        if self._marking_snapshot is not None:
            # Frozen marking owns the display OUTSIDE the selected manual
            # interval. INSIDE it only an independently completed range replay
            # may publish evidence; never add the old baseline there.
            manual_entries = [
                entry for entry in self._range_replay_entries
                if self._range_replay_entries_available
                and selected_times is not None
                and selected_times[0] <= entry.evaluation_time <= selected_times[1]
            ]
        else:
            manual_entries = self._marker_entries_for_display()
        if manual_entries:
            self._ensure_signal_tooltips_current()
        records = _marker_records(self._candles, manual_entries, self._signal_tooltips)
        for record in records:
            record["provenance"] = "manual"
            title, separator, rest = record["tooltip"].partition(" ")
            record["tooltip"] = {"BUY": "\uB9E4\uC218", "SELL": "\uB9E4\uB3C4"}.get(title, title) + separator + rest
        if self._marking_snapshot is None:
            return records
        calculation, entries = self._mapped_replay_entries(self._marking_snapshot)
        rules = self._marking_settings.to_dict()
        lines = self._validation_execution_tooltip_lines(rules, calculation_entries=calculation)
        tooltips = {}
        for entry in entries:
            if entry.signal != entry.evaluation_side:
                continue
            key = (entry.evaluation_index, entry.signal)
            tooltips[key] = "\n".join((signal_evidence_tooltip(entry, rules), *lines.get(key, ())))
        marking = _marker_records(self._candles, entries, tooltips)
        if selected_times is not None:
            # Timestamp identity survives history prepend/display remapping.
            # Exclude both same-bar duplicates and stale frozen evidence
            # that is not part of the selected period's own replay.
            marking = [
                record for record in marking
                if not selected_times[0] <= record["evaluation_time"] <= selected_times[1]
            ]
        for record in marking:
            record["provenance"] = "marking"
            title, separator, rest = record["tooltip"].partition(" ")
            record["tooltip"] = {"BUY": "\uB9E4\uC218", "SELL": "\uB9E4\uB3C4"}.get(title, title) + separator + rest
        return records + marking

    def _clip_comparison_cache(self, descriptors, cache):
        self._revalidate_marking_source()
        if cache is None:
            return None
        comparison_ids = {d.identity for d in descriptors if d.family == FAMILY_PRICE_COMPARISON}
        average_ids = {
            d.identity for d in descriptors
            if any(str(d.parameters.get("condition", {}).get(key) or "").upper() == "AVG_PRICE"
                   for key in ("target", "compare_target"))
        }
        manual = self._validation_range_times
        marking = self._marking_times
        marked_series = {}
        if self._marking_comparison_cache is not None:
            times, marking_cache = self._marking_comparison_cache
            lookup = {stamp: i for i, stamp in enumerate(times)}
            marked_series = {(identity, channel): tuple(
                values[lookup[c["time"]]] if c["time"] in lookup else None for c in self._candles
            ) for identity, channel, values in marking_cache.series}
        series = []
        for identity, channel, values in cache.series:
            if identity in comparison_ids or channel == "AVG_PRICE":
                marked = marked_series.get((identity, channel), ())
                values = tuple(
                    value if manual and manual[0] <= self._candles[i]["time"] <= manual[1]
                    else marked[i] if channel != "AVG_PRICE" and identity not in average_ids and marked and marking
                    and marking[0] <= self._candles[i]["time"] <= marking[1]
                    else None
                    for i, value in enumerate(values)
                )
            series.append((identity, channel, values))
        return ValidationIndicatorSeriesCache(cache.candle_count, tuple(series))

    def _refresh_marking_projection(self) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        cache = self._clip_comparison_cache(self._visualization_descriptors, self._visualization_cache)
        self._visualization_cache = cache
        canvas.set_visualization_projection(self._visualization_descriptors, cache,
                                            self._visualization_active_by_marker,
                                            self._chart_marker_records(),
                                            self._visualization_lower_filter_presentations)
        remaining = {(m["evaluation_index"], m["side"]) for m in canvas._markers}
        if canvas._hover_marker_key not in remaining:
            canvas._set_hover_marker_key(None)
        if canvas._pinned_marker_key not in remaining:
            canvas.clear_evidence_pin()
        canvas.clear_marker_tooltip()
        self._refresh_valid_filter_emphasis()

    def _on_filter_display_controls_changed(self, *_args) -> None:
        if not hasattr(self, "filter_display_check"):
            return
        self._filter_display_enabled = self.filter_display_check.isChecked()
        self._refresh_valid_filter_emphasis()
        snapshot = self._result_settings_snapshot
        if not isinstance(snapshot, ValidationSettingsSnapshot):
            return
        self._refresh_filter_display_projection()

    def _on_filter_display_valid_changed(self, checked: bool) -> None:
        self._filter_display_valid_enabled = bool(checked)
        self._refresh_valid_filter_emphasis()

    def _on_price_legend_prefix_reset_requested(self) -> None:
        if self.filter_display_valid_check.isChecked():
            self.filter_display_valid_check.setChecked(False)

    def _current_valid_filter_identities(self) -> set[tuple[str, str]]:
        if not hasattr(self, "filter_display_valid_check") or not (
            self.filter_display_check.isChecked()
            and self.filter_display_valid_check.isChecked()
        ):
            self._valid_filter_selection_error = ""
            self.filter_display_valid_check.setToolTip(
                _VALID_FILTER_DISPLAY_TOOLTIP
            )
            return set()

        try:
            ui_state = self.collect_indicator_follow_ui_state()
            fingerprint = self._signal_ui_fingerprint(ui_state)
            seed_settings = self._signal_validation_seed.settings_snapshot
            result_settings = self._result_settings_snapshot
            cache_key = (
                fingerprint,
                str(seed_settings.rules_hash),
                str(getattr(result_settings, "rules_hash", "")),
            )
            if cache_key != self._valid_filter_identity_cache_key:
                selected_identifiers = {
                    side: self._filter_display_expression_identifiers(
                        ui_state,
                        side,
                    )
                    for side in ("BUY", "SELL")
                }
                invalid_sides = [
                    side for side, identifiers in selected_identifiers.items()
                    if not identifiers
                ]
                if invalid_sides:
                    raise ValueError(
                        "VALID_FILTER_EXPRESSION_INVALID:"
                        + ",".join(invalid_sides)
                    )
                temporary_ui = self._valid_filter_selection_ui_state(ui_state)
                preview = (
                    self._load_indicator_follow_rule_mapper()
                    .build_engine_rules_preview_from_ui_state(
                        temporary_ui,
                        seed_settings.to_dict(),
                    )
                )
                preview_rules = preview.get("preview_rules")
                if not isinstance(preview_rules, dict):
                    raise ValueError("VALID_FILTER_PREVIEW_RULES_UNAVAILABLE")
                projected_rules = project_signal_validation_rules(preview_rules)
                selected_evidence_keys = {
                    side: self._filter_display_selected_evidence_keys(
                        projected_rules,
                        side,
                        identifiers_override=identifiers,
                    )
                    for side, identifiers in selected_identifiers.items()
                }
                self._valid_filter_identity_cache = frozenset(
                    (
                        str(side or "").strip().upper(),
                        str(descriptor.identity or "").strip(),
                    )
                    for descriptor in build_validation_filter_universe(
                        projected_rules,
                        include_disabled=False,
                    )
                    for side in descriptor.sides
                    if str(side or "").strip().upper() in {"BUY", "SELL"}
                    and str(descriptor.identity or "").strip()
                    and selected_evidence_keys[
                        str(side or "").strip().upper()
                    ].intersection(descriptor.evidence_keys)
                )
                self._valid_filter_identity_cache_key = cache_key
            self._valid_filter_selection_error = ""
            self.filter_display_valid_check.setToolTip(
                _VALID_FILTER_DISPLAY_TOOLTIP
            )
        except Exception as exc:
            self._valid_filter_identity_cache_key = None
            self._valid_filter_identity_cache = frozenset()
            self._valid_filter_selection_error = str(exc) or type(exc).__name__
            self.filter_display_valid_check.setToolTip(
                f"{_VALID_FILTER_DISPLAY_TOOLTIP}\n"
                f"지원되지 않는 설정: {self._valid_filter_selection_error}"
            )
            return set()

        displayed = {
            (
                str(side or "").strip().upper(),
                str(descriptor.identity or "").strip(),
            )
            for descriptor in self._visualization_descriptors
            for side in descriptor.sides
            if str(side or "").strip().upper() in {"BUY", "SELL"}
            and str(descriptor.identity or "").strip()
        }
        return set(self._valid_filter_identity_cache & displayed)

    @staticmethod
    def _valid_filter_selection_ui_state(
        ui_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build an expression-independent, presentation-only UI snapshot."""
        state = deepcopy(dict(ui_state))
        basic = state.get("basic")
        if not isinstance(basic, dict):
            raise ValueError("VALID_FILTER_BASIC_UI_UNAVAILABLE")
        basic["buy_signal_expr_line"] = "A OR B OR C OR D"

        sell_ui = state.get("sell_ui")
        conditions = (
            sell_ui.get("signal_conditions")
            if isinstance(sell_ui, dict)
            else None
        )
        if not isinstance(conditions, dict):
            raise ValueError("VALID_FILTER_SELL_UI_UNAVAILABLE")
        enabled_groups = []
        for letter in ("A", "B", "C"):
            group = conditions.get(f"condition_{letter.lower()}")
            if not isinstance(group, Mapping):
                continue
            if any(
                isinstance(name, str)
                and name.endswith("_check")
                and value is True
                for name, value in group.items()
            ):
                enabled_groups.append(letter)
        basic["sell_signal_expr_line"] = " OR ".join(enabled_groups or ("A",))
        return state

    def _refresh_valid_filter_emphasis(self) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        valid_mode_enabled = bool(
            hasattr(self, "filter_display_valid_check")
            and self.filter_display_check.isChecked()
            and self.filter_display_valid_check.isChecked()
        )
        canvas.set_valid_filter_identities(
            self._current_valid_filter_identities(),
            index_ranges={},
            delay_ranges={},
            valid_mode_enabled=valid_mode_enabled,
        )

    @staticmethod
    def _nonnegative_ocr_turn_delay(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            delay = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return delay if delay >= 0 else None

    @classmethod
    def _ocr_turn_delays_from_ui_state(
        cls,
        ui_state: Mapping[str, Any],
        descriptors: tuple[ValidationFilterDescriptor, ...],
    ) -> dict[tuple[str, str], int]:
        buy_ui = ui_state.get("buy_ui")
        signal_filter = (
            buy_ui.get("signal_filter") if isinstance(buy_ui, Mapping) else None
        )
        sell_ui = ui_state.get("sell_ui")
        signal_conditions = (
            sell_ui.get("signal_conditions")
            if isinstance(sell_ui, Mapping)
            else None
        )
        condition_a = (
            signal_conditions.get("condition_a")
            if isinstance(signal_conditions, Mapping)
            else None
        )

        side_delays: dict[str, int] = {}
        if isinstance(signal_filter, Mapping):
            delay = cls._nonnegative_ocr_turn_delay(
                signal_filter.get("buy_ocr_bar_line")
            )
            if delay is not None:
                side_delays["BUY"] = delay
        if isinstance(condition_a, Mapping):
            delay = cls._nonnegative_ocr_turn_delay(
                condition_a.get("ocr_convert_line")
            )
            if delay is not None:
                side_delays["SELL"] = delay

        result: dict[tuple[str, str], int] = {}
        for descriptor in descriptors:
            if descriptor.family != FAMILY_OCR_OSC:
                continue
            condition = descriptor.parameters.get("condition")
            if not isinstance(condition, Mapping) or str(
                condition.get("operator") or ""
            ).strip().upper() not in {"TURN_UP", "TURN_DOWN"}:
                continue
            identity = str(descriptor.identity or "").strip()
            if not identity:
                continue
            for raw_side in descriptor.sides:
                side = str(raw_side or "").strip().upper()
                if side in side_delays:
                    result[(side, identity)] = side_delays[side]
        return result

    def _refresh_ocr_turn_delays(self, ui_state: Mapping[str, Any]) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        canvas.set_ocr_turn_delays(self._ocr_turn_delays_from_ui_state(
            ui_state,
            canvas._visualization_descriptors,
        ))

    def _on_validation_regular_market_display_changed(self, *_args) -> None:
        self._disable_marking(clear_cache=True)
        replay_snapshot = self._replay_snapshot
        if (
            (not isinstance(replay_snapshot, ValidationReplaySnapshot) and not self._normal_base_ready)
            or not self._calculation_candles
        ):
            return
        chart_count = min(
            self._historical_candle_count,
            len(self._calculation_candles),
        )
        regular_only = self.validation_regular_market_only_check.isChecked()
        display_only_integrated = (
            not regular_only and bool(self._display_only_integrated_candles)
        )
        display_candles = (
            self._display_only_integrated_candles[-chart_count:]
            if display_only_integrated
            else self._calculation_candles[-chart_count:]
        )
        if regular_only:
            display_candles = _krx_regular_session_candles(display_candles)
        if (
            not display_candles
            or _validation_candle_signature(display_candles)
            == _validation_candle_signature(self._candles)
        ):
            return
        self.set_historical_display_candles(
            display_candles,
            display_only_integrated=display_only_integrated,
            preserve_completed_cycles=True,
        )
        self._preserve_view_on_next_replay = True
        if self._normal_base_ready and replay_snapshot is None:
            self.install_normal_base(self._result_settings_snapshot, self._normal_base_timeframe_key)
        else:
            self.set_replay_snapshot(replay_snapshot, presentation_only=True)

    def _queue_signal_validation_ui_confirmation(self) -> None:
        if getattr(self, "_signal_ui_confirmation_queued", False):
            return
        self._signal_ui_confirmation_queued = True
        QTimer.singleShot(0, self._on_signal_validation_ui_confirmed)

    def _flush_pending_signal_validation_ui_confirmation(self) -> bool:
        # Mark/range starts are an action boundary: a NoFocus control can leave
        # a draft without a queued FocusOut event, so validate synchronously.
        return self._on_signal_validation_ui_confirmed()

    def _show_signal_validation_confirmation_error(self, detail: object) -> None:
        self._signal_ui_confirmation_error_detail = str(detail or "")
        detail_text = self._signal_ui_confirmation_error_detail.lower()
        expression_side = (
            "buy" if detail_text.startswith("buy signal expression") else
            "sell" if detail_text.startswith("sell signal expression") else None
        )
        message = (
            "매수 신호 조합식이 올바르지 않습니다. 수정 후 다시 확인하세요."
            if expression_side == "buy" else
            "매도 신호 조합식이 올바르지 않습니다. 수정 후 다시 확인하세요."
            if expression_side == "sell" else
            "설정값이 올바르지 않습니다. 수정 후 다시 확인하세요."
        )
        self.validation_status_label.setText(message)
        self._signal_ui_confirmation_error_status = message
        line = QApplication.focusWidget()
        if line not in getattr(self, "_signal_ui_expression_lines", ()):
            line = (
                self.buy_signal_expr_line if expression_side == "buy" else
                self.sell_signal_expr_line if expression_side == "sell" else None
            )
        if line is not None:
            line.setToolTip(message)
            self._signal_ui_confirmation_error_line = line

    def _clear_signal_validation_confirmation_error(self) -> None:
        self._signal_ui_confirmation_error_detail = ""
        message = getattr(self, "_signal_ui_confirmation_error_status", "")
        if message and self.validation_status_label.text() == message:
            self.validation_status_label.setText("")
        line = getattr(self, "_signal_ui_confirmation_error_line", None)
        if line is not None and line.toolTip() == message:
            line.setToolTip("")
        self._signal_ui_confirmation_error_status = ""
        self._signal_ui_confirmation_error_line = None

    def _current_signal_ui_validation_warnings(self) -> tuple[str, ...]:
        ui_state = self.collect_indicator_follow_ui_state()
        preview = self._load_indicator_follow_rule_mapper().build_engine_rules_preview_from_ui_state(
            ui_state, self._signal_validation_seed.settings_snapshot.to_dict(),
        )
        warnings = {
            str(warning)
            for warning in preview.get("validation_warnings", ())
            if str(warning)
        }
        for side, line, identifiers in (
            ("buy", self.buy_signal_expr_line, {"A", "B", "C", "D"}),
            ("sell", self.sell_signal_expr_line, {"A", "B", "C"}),
        ):
            parsed = parse_condition_expression(
                line.text(), allowed_identifiers=identifiers,
                allow_duplicate_identifiers=True,
            )
            if not parsed.get("ok"):
                warnings.add(
                    f"{side} signal expression is invalid: {parsed.get('reason') or ''}"
                )
        return tuple(sorted(warnings))

    def _has_new_signal_ui_validation_warnings(
        self, warnings: tuple[str, ...],
    ) -> bool:
        return bool(set(warnings) - set(
            getattr(self, "_confirmed_signal_ui_validation_warnings", ())
        ))

    def _on_signal_validation_ui_confirmed(self) -> bool:
        self._signal_ui_confirmation_queued = False
        try:
            warnings = self._current_signal_ui_validation_warnings()
            if self._has_new_signal_ui_validation_warnings(warnings):
                expression_warning = next(
                    (
                        warning for warning in warnings
                        if warning.lower().startswith((
                            "buy signal expression", "sell signal expression",
                        ))
                    ),
                    warnings[0],
                )
                self._show_signal_validation_confirmation_error(expression_warning)
                return False
            self._marking_current_settings()
            fingerprint = self._current_signal_ui_fingerprint()
        except Exception as exc:
            self._show_signal_validation_confirmation_error(exc)
            return False
        self._clear_signal_validation_confirmation_error()
        if fingerprint == self._confirmed_signal_ui_fingerprint:
            return True
        self._confirmed_signal_ui_fingerprint = fingerprint
        self._confirmed_signal_ui_validation_warnings = warnings
        self._on_signal_validation_ui_changed()
        self.validation_status_label.setText(
            "설정이 적용되었습니다. 다음 검증 또는 표시에서 변경 내용이 반영됩니다."
        )
        return True

    def _on_signal_validation_ui_changed(self, *_args) -> None:
        self._disable_marking(clear_cache=True)
        if (
            self._validation_range is not None
            or self._validation_range_anchor_index is not None
        ):
            self._clear_validation_range_interaction()
        self._invalidate_user_background_results()
        self._settings_apply_after_validation = False
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._set_primary_validation_action_state("validate")
        # This is presentation-only: request the current draft's descriptors
        # and series without rebuilding the Canvas or replaying signals.
        self._refresh_filter_display_projection()
    def _on_validation_timeframe_changed(self, *_args) -> None:
        self._on_validation_context_changed(normal_base=True)

    def _on_validation_execution_policy_changed(self, *_args) -> None:
        self._on_validation_context_changed(
            normal_base=True,
            preserve_chart=True,
        )

    def _on_validation_averaging_policy_changed(self, *_args) -> None:
        self._on_validation_context_changed(
            normal_base=True,
            preserve_chart=True,
        )

    def _on_validation_context_changed(
        self,
        *_args,
        apply_after_validation: bool = False,
        normal_base: bool = False,
        preserve_chart: bool = False,
        preserve_marking: bool = False,
    ) -> None:
        marking_preserved = bool(
            preserve_marking and self._prepare_marking_policy_refresh()
        )
        if not marking_preserved:
            self._disable_marking(clear_cache=True)
        self._invalidate_user_background_results()
        self._settings_apply_after_validation = apply_after_validation
        if self._request_validation(
            normal_base=normal_base,
            preserve_chart=preserve_chart,
            preserve_marking=marking_preserved,
        ) is None:
            self._settings_apply_after_validation = False

    def _remember_price_legend_visibility(self, identities: object) -> None:
        values = identities if isinstance(identities, (tuple, list, set)) else ()
        self._hidden_price_filter_identities = {
            str(identity or "").strip()
            for identity in values
            if str(identity or "").strip()
        }

    def _remember_price_legend_highlight(self, identities: object) -> None:
        values = identities if isinstance(identities, (tuple, list, set)) else ()
        self._highlighted_price_filter_identities = {
            (str(side).strip().upper(), str(identity).strip())
            for side, identity in values
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(identity).strip()
        }

    def _remember_price_legend_hide_unhighlighted(self, hidden: bool) -> None:
        self._hide_unhighlighted_price_filters = bool(hidden)

    def _remember_lower_legend_highlight(self, identities: object) -> None:
        values = identities if isinstance(identities, (tuple, list, set)) else ()
        self._highlighted_lower_filter_identities = {
            (str(side).strip().upper(), str(identity).strip())
            for side, identity in values
            if str(side).strip().upper() in {"BUY", "SELL"}
            and str(identity).strip()
        }

    def _handle_reset_requested(self, *_args):
        reset_result = self.reset_chart_view()
        self._settings_apply_after_validation = False
        return reset_result

    def reset_chart_view(self) -> bool:
        """Restore the chart to its first-ready entry view."""
        self._disable_marking(clear_cache=True)
        self._invalidate_user_background_results()
        self._discard_chart_input()
        canvas = getattr(self, "canvas", None)
        if canvas is None or self._entry_chart_view_state is None:
            return False

        self._validation_range_anchor_index = None
        self._validation_range_anchor_time = None
        self._set_validation_range(None, None)
        canvas.clear_marker_tooltip()
        canvas.clear_evidence_pin()
        canvas._set_hover_marker_key(None)
        canvas._set_crosshair_pointer(None, None)
        self._hidden_price_filter_identities.clear()
        self._highlighted_price_filter_identities.clear()
        self._highlighted_lower_filter_identities.clear()
        self._hide_unhighlighted_price_filters = False
        canvas.reset_price_legend_visibility()

        self._restore_entry_chart_view_state()
        self._refresh_validation_range_results()
        self._restore_normal_base_after_range_clear()
        return True

    def _set_primary_validation_action_state(
        self,
        state: str,
        *,
        enabled: bool | None = None,
    ) -> None:
        if state not in {"validate", "apply"}:
            raise ValueError("unknown primary validation action state")
        self._primary_validation_action_state = state
        self.primary_validation_action_button.setText("설정적용")
        if enabled is None:
            enabled = True
        self.replay_validation_button.setEnabled(bool(enabled))
        self.primary_validation_action_button.setEnabled(bool(enabled))

    def _handle_primary_validation_action(self):
        # Applying the Working UI is deliberately independent of replay.  The
        # original settings dialog still validates and projects the whitelist.
        return self._request_settings_apply()

    def _request_validation(
        self,
        *,
        force_historical_refresh: bool = False,
        normal_base: bool = False,
        preserve_chart: bool = False,
        preserve_marking: bool = False,
    ) -> IndicatorFollowSignalValidationRunRequest | None:
        if not preserve_marking:
            self._disable_marking(clear_cache=True)
        self.clear_candle_preview(self._candle_preview_token)
        keep_chart_visible = bool(
            preserve_chart
            and getattr(self, "canvas", None) is not None
            and self.chart_stack.currentWidget() is self.chart_view
        )
        self._preserve_chart_for_pending_validation = keep_chart_visible
        if self.stock is None:
            self.show_validation_error(
                "상단 종목 영역을 두 번 클릭하여 검증 종목을 선택하세요."
            )
            return None
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            require_expression_aware_sell_price_selections(ui_state)
            require_resolved_buy_bollinger_sign_selection(ui_state)
            mapper = self._load_indicator_follow_rule_mapper()
            preview = mapper.build_engine_rules_preview_from_ui_state(
                ui_state,
                self._signal_validation_seed.settings_snapshot.to_dict(),
            )
            preview_warnings = tuple(sorted(
                str(warning)
                for warning in preview.get("validation_warnings", ())
                if str(warning)
            ))
            if self._has_new_signal_ui_validation_warnings(preview_warnings):
                raise ValueError("SIGNAL_VALIDATION_INPUT_ERROR")
            sell_warnings = [
                str(item)
                for item in preview.get("validation_warnings", [])
                if str(item).lower().startswith(("sell condition", "sell signal"))
            ]
            if sell_warnings:
                raise ValueError("SELL_VALIDATION_INPUT_ERROR: " + "; ".join(sell_warnings))
            preview_rules = preview.get("preview_rules")
            if not isinstance(preview_rules, dict):
                raise ValueError("signal validation preview rules are unavailable")
            snapshot = build_signal_validation_snapshot(
                preview_rules,
                ui_state=ui_state,
            )
            run_request = IndicatorFollowSignalValidationRunRequest(
                snapshot,
                self._evaluation_candle_count,
                force_historical_refresh=force_historical_refresh,
            )
            pending_fingerprint = self._signal_ui_fingerprint(ui_state)
        except ValueError as exc:
            toast_message = self._sell_input_error_toast_message(exc)
            if toast_message:
                show_toast(self, toast_message, duration_ms=2500)
                return None
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        except Exception:
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self._normal_base_requested = normal_base
        self._range_replay_from_normal_base = False
        self._pending_result_ui_state = deepcopy(ui_state)
        self._pending_result_settings_snapshot = snapshot
        self._pending_validation_ui_fingerprint = pending_fingerprint
        self._validated_ui_fingerprint = None
        if keep_chart_visible:
            self._clear_pending_execution_result_evidence()
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.loading_label.setText("과거 시세 데이터 조회 중...")
        if self._replay_snapshot is None and not keep_chart_visible:
            self.chart_stack.setCurrentWidget(self.loading_label)
        self._set_primary_validation_action_state("validate", enabled=False)
        self.validation_run_requested.emit(run_request)
        return run_request

    def _clear_pending_execution_result_evidence(self) -> None:
        """Keep source data visible while withdrawing the old policy result."""
        self._clear_range_replay_override()
        self._signal_tooltips = {}
        self._signal_tooltip_provenance = None
        self._populate_completed_cycles()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_marker_records([])
        self.estimated_return_label.setText("")

    def _prepare_marking_policy_refresh(self) -> bool:
        if (
            not self.marking_display_check.isChecked()
            or self._marking_times is None
        ):
            return False
        self._marking_generation += 1
        self._marking_key = None
        self._marking_snapshot = None
        self._marking_settings = None
        self._marking_comparison_cache = None
        self._marking_cache = None
        self._marking_display_reprojection_pending = False
        self._marking_pool_publication_pending = False
        self._marking_policy_refresh_pending = True
        self._invalidate_visualization_projection_data()
        self._clear_pending_execution_result_evidence()
        return True

    def _resume_marking_policy_refresh(self) -> bool:
        if not self._marking_policy_refresh_pending:
            return False
        try:
            settings = self._marking_current_settings()
            replay_snapshot = self._replay_snapshot
            normal_base_ready = bool(
                self._normal_base_requested and self._normal_base_ready
            )
            if (
                not self.marking_display_check.isChecked()
                or self._marking_times is None
                or not isinstance(
                    self._result_settings_snapshot,
                    ValidationSettingsSnapshot,
                )
                or (
                    not normal_base_ready
                    and not isinstance(replay_snapshot, ValidationReplaySnapshot)
                )
                or (
                    isinstance(replay_snapshot, ValidationReplaySnapshot)
                    and settings.rules_hash != replay_snapshot.settings_hash
                )
                or settings.rules_hash
                != self._result_settings_snapshot.rules_hash
            ):
                return False
            times = self._marking_times
            source_start = self._marking_source_start
            calculation_times = {
                str(candle.get("time") or "")
                for candle in self._calculation_candles
            }
            if source_start not in calculation_times:
                raise ValueError("MARKING_SOURCE_PREFIX_UNAVAILABLE")
            indexes_by_time = {
                str(candle.get("time") or ""): index
                for index, candle in enumerate(self._candles)
            }
            if times[0] not in indexes_by_time or times[1] not in indexes_by_time:
                raise ValueError("MARKING_TIMESTAMP_UNAVAILABLE")
            left, right = indexes_by_time[times[0]], indexes_by_time[times[1]]
            if not 0 <= left <= right < len(self._candles):
                raise ValueError("MARKING_RANGE_UNAVAILABLE")
            display_source = _detached_validation_candles(
                self._candles[left : right + 1]
            )
            key = self._marking_source_key(
                settings,
                times,
                prefix_start=source_start,
                frozen_display=display_source,
            )
            self._marking_key = key
            self._marking_display_source = display_source
            self._marking_settings = settings
            self._marking_policy_refresh_pending = False
            self.marking_evaluation_requested.emit({
                "generation": self._marking_generation,
                "key": key,
                "times": times,
                "range": (left, right),
                "settings": settings,
                "calculation": _detached_validation_candles(
                    self._calculation_candles
                ),
                "display": _detached_validation_candles(self._candles),
                "mixed": self._display_only_integrated_active,
                "descriptors": tuple(
                    descriptor
                    for descriptor in self._filter_display_descriptors(
                        settings.to_dict()
                    )
                    if descriptor.family == FAMILY_PRICE_COMPARISON
                ),
            })
            return True
        except (TypeError, ValueError, KeyError) as exc:
            self.marking_display_check.setToolTip(str(exc))
            return False

    @staticmethod
    def _operator_warning_detail(value: object) -> str:
        detail = str(value or "").strip()
        if (
            len(detail) >= 2
            and detail[0] == detail[-1]
            and detail[0] in {"'", '"'}
        ):
            detail = detail[1:-1]
        return detail

    @staticmethod
    def _condition_expression_reason_to_operator_text(reason: object) -> str:
        raw = str(reason or "").strip()
        code, separator, detail = raw.partition(":")
        messages = {
            "CONDITION_EXPRESSION_EMPTY":
                "매도 신호 조합식이 비어 있습니다.",
            "CONDITION_EXPRESSION_TOKEN_UNSUPPORTED":
                "매도 신호 조합식에 사용할 수 없는 항목이 있습니다.",
            "CONDITION_EXPRESSION_IDENTIFIER_MISSING":
                "매도 신호 조합식에 A/B/C 조건이 없습니다.",
            "CONDITION_EXPRESSION_IDENTIFIER_LIMIT_EXCEEDED":
                "매도 신호 조합식의 조건 수가 허용 범위를 초과했습니다.",
            "CONDITION_EXPRESSION_DUPLICATE_IDENTIFIER":
                "매도 신호 조합식에서 같은 조건을 중복 사용할 수 없습니다.",
            "CONDITION_EXPRESSION_OPERAND_MISSING":
                "매도 신호 조합식의 연산자 뒤에 조건이 없습니다.",
            "CONDITION_EXPRESSION_PARENTHESIS_UNBALANCED":
                "매도 신호 조합식의 괄호 짝이 맞지 않습니다.",
            "CONDITION_EXPRESSION_OPERAND_INVALID":
                "매도 신호 조합식의 조건 위치에 잘못된 항목이 있습니다.",
            "CONDITION_EXPRESSION_TRAILING_TOKEN":
                "매도 신호 조합식 끝에 처리할 수 없는 항목이 있습니다.",
        }
        message = messages.get(code)
        if message is None:
            return "매도 신호 조합식을 확인하세요."
        if separator and detail:
            return (
                message
                + " ("
                + IndicatorFollowSignalValidationV5Window._operator_warning_detail(
                    detail
                )
                + ")"
            )
        return message

    @staticmethod
    def _sell_validation_warning_to_operator_text(warning: object) -> str:
        text = str(warning or "").strip()
        expression_prefix = "sell signal expression is invalid:"
        if text.startswith(expression_prefix):
            reason = text[len(expression_prefix):].strip()
            return (
                "매도 신호 조합식이 올바르지 않습니다. "
                + IndicatorFollowSignalValidationV5Window
                ._condition_expression_reason_to_operator_text(reason)
            )

        for group_name in ("A", "B", "C"):
            prefix = f"sell condition {group_name} "
            if not text.startswith(prefix):
                continue
            body = text[len(prefix):].strip()

            generic_messages = {
                "is referenced by expression but has no active conditions":
                    f"매도 신호조건 {group_name}가 조합식에 포함되어 있지만 적용된 필터가 없습니다.",
                "candidate group was not generated":
                    f"매도 신호조건 {group_name}를 구성할 수 없습니다. 필터 설정을 확인하세요.",
                "GAP 가격 기준 재선택 필요":
                    f"매도조건 {group_name}의 가격비교 가격기준을 다시 선택하세요.",
                "GAP 동일 가격 기준 비교는 허용되지 않음":
                    f"매도조건 {group_name}의 가격비교에서 왼쪽과 오른쪽에 같은 가격기준을 사용할 수 없습니다.",
                "GAP policy is invalid":
                    f"매도조건 {group_name}의 가격비교 방향·비교조건·값을 확인하세요.",
                "GAP BOTH requires WITHIN/OUTSIDE":
                    f"매도조건 {group_name}의 가격비교가 ‘상하’일 때는 ‘이내’ 또는 ‘이탈’을 선택하세요.",
                "GAP directional policy requires GTE/LTE":
                    f"매도조건 {group_name}의 가격비교가 ‘상향/하향’일 때는 ‘이상’ 또는 ‘이하’를 선택하세요.",
            }
            if body in generic_messages:
                return generic_messages[body]

            if group_name == "A":
                exact_messages = {
                    "OCR convert bar is not a non-negative integer":
                        "매도조건 A의 OCR 전환 봉 수는 0 이상의 정수여야 합니다.",
                    "OCR threshold is not numeric":
                        "매도조건 A의 OCR 기준값은 숫자여야 합니다.",
                    "RSI period or threshold is missing":
                        "매도조건 A의 RSI 기간과 기준값을 입력하세요.",
                    "RSI period is not numeric":
                        "매도조건 A의 RSI 기간은 1 이상의 숫자여야 합니다.",
                    "RSI threshold is not numeric":
                        "매도조건 A의 RSI 기준값은 숫자여야 합니다.",
                }
                if body in exact_messages:
                    return exact_messages[body]
                for internal_prefix, operator_label in (
                    ("OCR turn is not mapped:", "OCR 전환방향"),
                    ("OCR compare is not mapped:", "OCR 비교조건"),
                    ("RSI compare is not mapped:", "RSI 비교조건"),
                ):
                    if body.startswith(internal_prefix):
                        detail = (
                            IndicatorFollowSignalValidationV5Window
                            ._operator_warning_detail(
                                body[len(internal_prefix):]
                            )
                        )
                        return (
                            f"매도조건 A의 {operator_label}을 확인하세요."
                            + (f" ({detail})" if detail else "")
                        )

            if group_name == "B":
                exact_messages = {
                    "Price Box policy is invalid":
                        "매도조건 B의 가격박스 방향·부호·값·비교조건을 확인하세요.",
                    "Bollinger offset is not numeric":
                        "매도조건 B의 볼린저밴드 기준값은 숫자여야 합니다.",
                }
                if body in exact_messages:
                    return exact_messages[body]
                if body.startswith("Bollinger sign is not mapped:"):
                    detail = (
                        IndicatorFollowSignalValidationV5Window
                        ._operator_warning_detail(
                            body[len("Bollinger sign is not mapped:"):]
                        )
                    )
                    return (
                        "매도조건 B의 볼린저밴드 +/- 부호를 확인하세요."
                        + (f" ({detail})" if detail else "")
                    )
                for internal_prefix, operator_label in (
                    ("Bollinger direction is not mapped:", "볼린저밴드 방향"),
                    ("Bollinger compare is not mapped:", "볼린저밴드 비교조건"),
                ):
                    if body.startswith(internal_prefix):
                        detail = (
                            IndicatorFollowSignalValidationV5Window
                            ._operator_warning_detail(
                                body[len(internal_prefix):]
                            )
                        )
                        return (
                            f"매도조건 B의 {operator_label}을 확인하세요."
                            + (f" ({detail})" if detail else "")
                        )

            if group_name == "C":
                exact_messages = {
                    "MACD value is not numeric":
                        "매도조건 C의 MACD 기준값은 숫자여야 합니다.",
                    "ARRAY first period is not numeric":
                        "매도조건 C의 첫 번째 이평 기간은 1 이상의 숫자여야 합니다.",
                    "ARRAY second period is not numeric":
                        "매도조건 C의 두 번째 이평 기간은 1 이상의 숫자여야 합니다.",
                    "ARRAY third period is not numeric":
                        "매도조건 C의 세 번째 이평 기간은 1 이상의 숫자여야 합니다.",
                }
                if body in exact_messages:
                    return exact_messages[body]
                for internal_prefix, operator_label in (
                    (
                        "MACD target is not mapped:",
                        "MACD 대상(MACD선/시그널선)",
                    ),
                    ("MACD compare is not mapped:", "MACD 비교조건"),
                    ("ARRAY first compare is not mapped:", "첫 번째 이평 비교조건"),
                    ("ARRAY second compare is not mapped:", "두 번째 이평 비교조건"),
                ):
                    if body.startswith(internal_prefix):
                        detail = (
                            IndicatorFollowSignalValidationV5Window
                            ._operator_warning_detail(
                                body[len(internal_prefix):]
                            )
                        )
                        return (
                            f"매도조건 C의 {operator_label}을 확인하세요."
                            + (f" ({detail})" if detail else "")
                        )

            return f"매도 신호조건 {group_name}의 필터 설정을 확인하세요."

        if text.startswith("sell signal"):
            return "매도 신호 조합식을 확인하세요."
        if text.startswith("sell condition"):
            return "매도 신호조건의 필터 설정을 확인하세요."
        return text

    @staticmethod
    def _sell_input_error_toast_message(error: ValueError) -> str:
        message = str(error or "")
        if message.startswith("매도 신호 조합식 오류:"):
            reason = message.split(":", 1)[1].strip()
            return (
                "매도 신호 조합식이 올바르지 않습니다. "
                + IndicatorFollowSignalValidationV5Window
                ._condition_expression_reason_to_operator_text(reason)
            )
        if message.startswith("가격 기준 재선택 필요:"):
            return message.split(":", 1)[1].strip()
        if message.startswith("SELL_VALIDATION_INPUT_ERROR:"):
            raw_warnings = [
                item.strip()
                for item in message.split(":", 1)[1].split(";")
                if item.strip()
            ]
            localized: list[str] = []
            for warning in raw_warnings:
                operator_text = (
                    IndicatorFollowSignalValidationV5Window
                    ._sell_validation_warning_to_operator_text(warning)
                )
                if operator_text and operator_text not in localized:
                    localized.append(operator_text)
            return "\n".join(localized) or "매도 신호 설정을 확인하세요."
        if message.startswith("BUY_BOLLINGER_SIGN_SELECTION_REQUIRED:"):
            return message.split(":", 1)[1].strip()
        return ""

    def _request_entry_validation(
        self,
        entry_ui_state: dict[str, Any],
        candle_count: int,
    ) -> IndicatorFollowSignalValidationRunRequest | None:
        """Replay the immutable, entry-approved snapshot without Candidate approval."""
        if self.stock is None:
            self.show_validation_error(
                "상단 종목 영역을 두 번 클릭하여 검증 종목을 선택하세요."
            )
            return None
        try:
            run_request = IndicatorFollowSignalValidationRunRequest(
                self._signal_validation_seed.settings_snapshot,
                self._evaluation_candle_count,
            )
            pending_fingerprint = self._signal_ui_fingerprint(entry_ui_state)
        except (TypeError, ValueError):
            self.show_validation_error("V5 진입 설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self._pending_result_ui_state = deepcopy(entry_ui_state)
        self._pending_result_settings_snapshot = run_request.settings_snapshot
        self._pending_validation_ui_fingerprint = pending_fingerprint
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.loading_label.setText("과거 시세 데이터 조회 중...")
        self.chart_stack.setCurrentWidget(self.loading_label)
        self._set_primary_validation_action_state("validate", enabled=False)
        self.validation_run_requested.emit(run_request)
        return run_request

    def request_validation(self) -> IndicatorFollowSignalValidationRunRequest | None:
        return self._request_validation()

    def request_initial_validation(self) -> IndicatorFollowSignalValidationRunRequest | None:
        if self._initial_validation_requested:
            return None
        self._initial_validation_requested = True
        return self.request_normal_base()

    def request_normal_base(self) -> IndicatorFollowSignalValidationRunRequest | None:
        return self._request_validation(normal_base=True)

    def _request_settings_apply(self) -> IndicatorFollowSignalValidationApplyPayload | None:
        self._pending_ocr_turn_ui_state = None
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            payload = IndicatorFollowSignalValidationApplyPayload(ui_state)
        except ValueError as exc:
            self._on_signal_validation_ui_changed()
            toast_message = self._sell_input_error_toast_message(exc)
            if toast_message:
                show_toast(self, toast_message, duration_ms=2500)
                return None
            self.show_settings_apply_result(
                "현재 신호설정을 반영용 데이터로 만들 수 없습니다.",
                success=False,
            )
            return None
        except Exception:
            self._on_signal_validation_ui_changed()
            self.show_settings_apply_result(
                "현재 신호설정을 반영용 데이터로 만들 수 없습니다.",
                success=False,
            )
            return None
        self._pending_ocr_turn_ui_state = deepcopy(ui_state)
        self.settings_apply_requested.emit(payload)
        return payload

    def show_settings_apply_result(self, message: str, *, success: bool) -> None:
        pending_ocr_turn_ui_state = self._pending_ocr_turn_ui_state
        self._pending_ocr_turn_ui_state = None
        if success:
            if isinstance(pending_ocr_turn_ui_state, Mapping):
                self._refresh_ocr_turn_delays(pending_ocr_turn_ui_state)
            self._set_primary_validation_action_state("apply")
            return
        self.validation_status_label.setText(str(message or "설정 적용 실패"))
        try:
            fingerprint_matches = (
                self._validated_ui_fingerprint is not None
                and self._current_signal_ui_fingerprint()
                == self._validated_ui_fingerprint
            )
        except Exception:
            fingerprint_matches = False
        self._set_primary_validation_action_state(
            "apply" if fingerprint_matches else "validate"
        )

    def install_candle_preview(
        self,
        candles: list[dict[str, Any]],
        *,
        timeframe_key: str,
        token: object,
    ) -> bool:
        """Display detached candles without installing any validation results."""
        if (
            self._replay_snapshot is not None
            or self._chart_results_suspended
            or token is None
            or not isinstance(candles, list)
            or not candles
        ):
            return False
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles, [], timeframe_key=timeframe_key,
        )
        canvas.setEnabled(False)
        self.clear_candle_preview(self._candle_preview_token)
        self._candle_preview_token = token
        self._candle_preview_canvas = canvas
        self.canvas = canvas
        self.chart_view.setEnabled(False)
        self.chart_scroll_area.setWidget(canvas)
        self.fixed_price_axis.set_canvas(canvas)
        self.result_summary_label.setText("")
        self.estimated_return_label.setText("")
        for label in (
            self.result_summary_label, self.estimated_return_label,
            self.validation_status_label,
        ):
            label.setToolTip("")
        self.completed_cycle_table.hide()
        self.completed_cycle_empty_label.hide()
        self.validation_status_label.setText("캔들 미리보기 · 검증 중 (결과 미확정)")
        self.chart_stack.setCurrentWidget(self.chart_view)
        return True

    def clear_candle_preview(self, token: object) -> bool:
        """Remove only the passive Canvas still owned by this prepare token."""
        if token is None or self._candle_preview_token is not token:
            return False
        canvas = self._candle_preview_canvas
        self._candle_preview_token = None
        self._candle_preview_canvas = None
        if self._replay_snapshot is not None or getattr(self, "canvas", None) is not canvas:
            return False
        self.chart_scroll_area.takeWidget()
        canvas.deleteLater()
        self.canvas = None
        self.fixed_price_axis.set_canvas(None)
        self.chart_stack.setCurrentWidget(self.loading_label)
        self.chart_view.setEnabled(True)
        self.completed_cycle_table.show()
        self.completed_cycle_empty_label.setVisible(not self._completed_cycles)
        self.validation_status_label.setText("")
        return True

    def show_validation_error(self, message: str) -> None:
        self.clear_candle_preview(self._candle_preview_token)
        preserve_chart = bool(
            self._preserve_chart_for_pending_validation
            and getattr(self, "canvas", None) is not None
        )
        self._preserve_chart_for_pending_validation = False
        self.validation_status_label.setText(str(message or "검증 실패"))
        if self._replay_snapshot is None and not preserve_chart:
            self.loading_label.setText(str(message or "검증 실패"))
            self.chart_stack.setCurrentWidget(self.loading_label)
        elif preserve_chart:
            self.chart_stack.setCurrentWidget(self.chart_view)
        self._pending_validation_ui_fingerprint = None
        self._pending_result_settings_snapshot = None
        self._pending_result_ui_state = None
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self._set_primary_validation_action_state("validate")

    def set_validation_stock(self, stock: ValidationStockRef) -> bool:
        if not isinstance(stock, ValidationStockRef) or not stock.code or not stock.name:
            raise TypeError("stock must be a populated ValidationStockRef")
        selected = ValidationStockRef(stock.code, stock.name)
        if selected == self.stock:
            return False
        self._disable_marking(clear_cache=True)
        self._invalidate_user_background_results()
        self._discard_chart_input()
        self._signal_validation_stock = selected
        self.compact_stock_display.set_current_stock(selected)
        self._clear_validation_result("새 종목 검증 준비 중")
        return True

    def discard_validation_stock_if_matches(self, stock_code: object) -> bool:
        code = str(stock_code or "").strip().upper()
        current = self.stock
        if (
            not code
            or not isinstance(current, ValidationStockRef)
            or str(current.code).strip().upper() != code
        ):
            return False
        self._invalidate_user_background_results()
        self._discard_chart_input()
        self._signal_validation_stock = None
        self._disable_marking(clear_cache=True, refresh=False)
        self.compact_stock_display.set_current_stock(None)
        self._entry_chart_view_state = None
        self._entry_chart_view_anchor = None
        self._clear_validation_result("검증 종목을 선택하세요.")
        return True

    def set_recent_stocks(self, stocks: object) -> None:
        self.recent_stock_row.set_recent_stocks(stocks)
        QTimer.singleShot(0, self._sync_recent_stock_row_width)

    def set_stock_metadata(self, metadata: object) -> None:
        self.compact_stock_display.set_current_stock(self.stock, metadata)

    def _toggle_recent_stock_row(self) -> None:
        self._recent_stock_row_expanded = not self._recent_stock_row_expanded
        self.basic_toggle_button.setText(
            "▼ 기본설정" if self._recent_stock_row_expanded else "▶ 기본설정"
        )
        self.recent_stock_panel.setVisible(self._recent_stock_row_expanded)
        self._sync_basic_header_height()
        self._sync_control_page_size()
        self._sync_recent_stock_row_width()
        QTimer.singleShot(0, self._fit_signal_validation_window)

    def _sync_basic_header_height(self) -> None:
        layout = self.basic_box.layout()
        if layout is None:
            return
        layout.invalidate()
        layout.activate()
        target_height = layout.sizeHint().height()
        if target_height > 0:
            self.basic_box.setFixedHeight(target_height)

    def _sync_recent_stock_row_width(self) -> None:
        if not hasattr(self, "recent_stock_row"):
            return
        panel_layout = self.recent_stock_panel_layout
        panel_layout.invalidate()
        panel_layout.activate()
        header_center_x = self.basic_toggle_button.mapTo(
            self.basic_box,
            self.basic_toggle_button.rect().center(),
        ).x()
        margins = panel_layout.contentsMargins()
        required_button_width = max(
            self.stock_selection_button.sizeHint().width(),
            self.stock_selection_button.minimumSizeHint().width(),
        )
        self.stock_selection_button.setFixedWidth(required_button_width)
        panel_left_x = self.recent_stock_panel.mapTo(
            self.basic_box,
            QPoint(0, 0),
        ).x()
        button_left_margin = max(
            0,
            header_center_x - required_button_width + 1 - panel_left_x,
        )
        if margins.left() != button_left_margin:
            panel_layout.setContentsMargins(
                button_left_margin,
                margins.top(),
                margins.right(),
                margins.bottom(),
            )
            panel_layout.invalidate()
            panel_layout.activate()
            margins = panel_layout.contentsMargins()
        available_width = max(
            0,
            self.recent_stock_panel.contentsRect().width()
            - margins.left()
            - margins.right()
            - self.stock_selection_button.width()
            - panel_layout.spacing(),
        )
        self.recent_stock_row.set_available_width(available_width)

    def showEvent(self, event) -> None:
        self._chart_results_suspended = False
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_recent_stock_row_width)
        QTimer.singleShot(0, self._sync_completed_cycle_table_geometry)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._record_signal_validation_resize_ownership(event)
        if self.isVisible():
            QTimer.singleShot(0, self._sync_recent_stock_row_width)
            QTimer.singleShot(0, self._resize_completed_cycle_columns)

    def _record_signal_validation_resize_ownership(self, event) -> None:
        if (
            self._initial_geometry_committed
            and not self._programmatic_resize_in_progress
            and event.spontaneous()
        ):
            self._user_geometry_owned = True

    def _clear_validation_result(self, message: str) -> None:
        self._preserve_chart_for_pending_validation = False
        self._disable_marking(clear_cache=True, refresh=False)
        self.clear_candle_preview(self._candle_preview_token)
        self._discard_chart_input()
        QToolTip.hideText()
        self._normal_base_requested = True
        self._normal_base_ready = False
        self._range_replay_from_normal_base = False
        self._replay_snapshot = None
        self._calculation_candles = []
        self._calculation_entries = []
        self._calculation_signal_marker_entries = []
        self._candles = []
        self._display_only_integrated_candles = []
        self._display_only_integrated_active = False
        self._entries = []
        self._signal_marker_entries_available = False
        self._signal_marker_coverage_times = None
        self._range_replay_calculation_entries = []
        self._range_replay_entries = []
        self._range_replay_entries_available = False
        self._range_replay_revalidation_pending = False
        self._completed_cycles = []
        self._selected_index = None
        self._validation_range_anchor_index = None
        self._validation_range_anchor_time = None
        self._validation_range = None
        self._validation_range_times = None
        self._chart_pool_start_index = 0
        self._chart_calculation_indexes = ()
        self._historical_pool_installed = False
        self._historical_pool_signature = None
        self._preserve_view_on_next_replay = False
        self._pending_history_extension_view_anchor = None
        self._pending_prepared_presentation = None
        self._pending_prepared_visualization_epoch = None
        self._backtest_trust_metadata = {}
        self._history_extension_intent_generation += 1
        self._history_extension_intent_source = None
        self._visible_start_index = 0.0
        self._visible_candle_span = 1.0
        self._time_scale_manually_adjusted = False
        self._default_price_minimum = None
        self._default_price_maximum = None
        self._current_price_minimum = None
        self._current_price_maximum = None
        self._price_scale_manually_adjusted = False
        self._signal_tooltips = {}
        self._visualization_descriptors = ()
        self._visualization_lower_filter_presentations = ()
        self._visualization_cache = None
        self._visualization_active_by_marker = {}
        self._visualization_data_error = ""
        self._invalidate_visualization_projection_data()
        self._pending_result_settings_snapshot = None
        self._pending_result_ui_state = None
        self._result_settings_snapshot = None
        self._cancel_chart_input_frame()
        old_canvas = self.chart_scroll_area.takeWidget()
        if old_canvas is not None:
            old_canvas.deleteLater()
        self.canvas = None
        self.fixed_price_axis.set_canvas(None)
        self._sync_time_navigation_scrollbar()
        self.result_summary_label.setText("")
        self.estimated_return_label.setText("")
        self._populate_completed_cycles()
        self.validation_status_label.setText(message)
        self.loading_label.setText(message)
        self.chart_stack.setCurrentWidget(self.loading_label)
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self._set_primary_validation_action_state("validate")

    def _current_chart_view_state(self) -> _ValidationChartViewState:
        return _ValidationChartViewState(
            visible_start_index=self._visible_start_index,
            visible_candle_span=self._visible_candle_span,
            default_price_minimum=self._default_price_minimum,
            default_price_maximum=self._default_price_maximum,
            current_price_minimum=self._current_price_minimum,
            current_price_maximum=self._current_price_maximum,
            time_scale_manually_adjusted=self._time_scale_manually_adjusted,
            price_scale_manually_adjusted=self._price_scale_manually_adjusted,
            selected_evaluation_index=self._selected_index,
            lower_pane_heights=(
                self.canvas.lower_pane_heights
                if self.canvas is not None
                else ()
            ),
        )

    def _capture_entry_chart_view_state(self) -> None:
        if self._entry_chart_view_state is not None:
            return
        state = self._current_chart_view_state()
        self._entry_chart_view_state = state
        if not self._candles:
            return

        center_position = (
            state.visible_start_index + state.visible_candle_span / 2.0
        )
        center_index = min(
            len(self._candles) - 1,
            max(0, math.floor(center_position)),
        )
        center_time = str(self._candles[center_index].get("time") or "")
        if not center_time:
            return

        selected_time = None
        selected_index = state.selected_evaluation_index
        if (
            isinstance(selected_index, int)
            and not isinstance(selected_index, bool)
            and 0 <= selected_index < len(self._candles)
        ):
            selected_time = str(
                self._candles[selected_index].get("time") or ""
            ) or None
        self._entry_chart_view_anchor = _EntryChartViewAnchor(
            view_state=state,
            center_time=center_time,
            center_fraction=center_position - center_index,
            selected_time=selected_time,
        )

    def _restore_chart_view_state(
        self,
        state: _ValidationChartViewState | None,
    ) -> None:
        self._cancel_pending_wheel_zoom()
        canvas = self.canvas
        if state is None or canvas is None:
            return
        self._default_price_minimum = state.default_price_minimum
        self._default_price_maximum = state.default_price_maximum
        self._current_price_minimum = state.current_price_minimum
        self._current_price_maximum = state.current_price_maximum
        canvas.set_lower_pane_heights(list(state.lower_pane_heights))
        if (
            state.current_price_minimum is not None
            and state.current_price_maximum is not None
            and state.current_price_maximum > state.current_price_minimum
        ):
            canvas.set_price_view(
                state.current_price_minimum,
                state.current_price_maximum,
            )
        self._set_time_view(
            state.visible_start_index,
            state.visible_candle_span,
            manually_adjusted=state.time_scale_manually_adjusted,
        )
        self._price_scale_manually_adjusted = state.price_scale_manually_adjusted
        selected = state.selected_evaluation_index
        if (
            isinstance(selected, int)
            and not isinstance(selected, bool)
            and 0 <= selected < len(self._candles)
        ):
            self._selected_index = selected
            canvas.set_selected_index(selected)
        else:
            self._selected_index = None
            canvas.set_selected_index(None)
        self.fixed_price_axis.refresh_scale()

    def _restore_entry_chart_view_state(self) -> None:
        raw_state = self._entry_chart_view_state
        anchor = self._entry_chart_view_anchor
        if raw_state is None or anchor is None:
            self._restore_chart_view_state(raw_state)
            return

        time_to_index = {
            str(candle.get("time") or ""): index
            for index, candle in enumerate(self._candles)
            if str(candle.get("time") or "")
        }
        center_index = time_to_index.get(anchor.center_time)
        if center_index is None:
            self._restore_chart_view_state(raw_state)
            return

        state = anchor.view_state
        selected_index = (
            time_to_index.get(anchor.selected_time)
            if anchor.selected_time is not None
            else None
        )
        if selected_index is None:
            raw_selected = state.selected_evaluation_index
            selected_index = (
                raw_selected
                if (
                    isinstance(raw_selected, int)
                    and not isinstance(raw_selected, bool)
                    and 0 <= raw_selected < len(self._candles)
                )
                else None
            )
        center_position = float(center_index) + anchor.center_fraction
        restored = _ValidationChartViewState(
            visible_start_index=(
                center_position - state.visible_candle_span / 2.0
            ),
            visible_candle_span=state.visible_candle_span,
            default_price_minimum=state.default_price_minimum,
            default_price_maximum=state.default_price_maximum,
            current_price_minimum=state.current_price_minimum,
            current_price_maximum=state.current_price_maximum,
            time_scale_manually_adjusted=state.time_scale_manually_adjusted,
            price_scale_manually_adjusted=state.price_scale_manually_adjusted,
            selected_evaluation_index=selected_index,
            lower_pane_heights=state.lower_pane_heights,
        )
        self._restore_chart_view_state(restored)

    def _capture_history_extension_view_anchor(self) -> _HistoryExtensionViewAnchor | None:
        if self.canvas is None or not self._candles:
            return None
        start_index = min(
            len(self._candles) - 1,
            max(0, math.floor(self._visible_start_index)),
        )
        start_time = str(self._candles[start_index].get("time") or "")
        if not start_time:
            return None
        selected_time = None
        if (
            isinstance(self._selected_index, int)
            and not isinstance(self._selected_index, bool)
            and 0 <= self._selected_index < len(self._candles)
        ):
            selected_time = str(
                self._candles[self._selected_index].get("time") or ""
            ) or None
        return _HistoryExtensionViewAnchor(
            view_state=self._current_chart_view_state(),
            visible_start_time=start_time,
            visible_start_fraction=max(0.0, self._visible_start_index - start_index),
            selected_time=selected_time,
            validated_ui_fingerprint=self._validated_ui_fingerprint,
            primary_action_state=self._primary_validation_action_state,
        )

    def _restore_history_extension_view_anchor(
        self,
        anchor: _HistoryExtensionViewAnchor,
    ) -> None:
        time_to_index = {
            str(candle.get("time") or ""): index
            for index, candle in enumerate(self._candles)
            if str(candle.get("time") or "")
        }
        start_index = time_to_index.get(anchor.visible_start_time)
        selected_index = (
            time_to_index.get(anchor.selected_time)
            if anchor.selected_time is not None
            else None
        )
        state = anchor.view_state
        restored = _ValidationChartViewState(
            visible_start_index=(
                state.visible_start_index
                if start_index is None
                else float(start_index) + anchor.visible_start_fraction
            ),
            visible_candle_span=state.visible_candle_span,
            default_price_minimum=state.default_price_minimum,
            default_price_maximum=state.default_price_maximum,
            current_price_minimum=state.current_price_minimum,
            current_price_maximum=state.current_price_maximum,
            time_scale_manually_adjusted=state.time_scale_manually_adjusted,
            price_scale_manually_adjusted=state.price_scale_manually_adjusted,
            selected_evaluation_index=selected_index,
            lower_pane_heights=state.lower_pane_heights,
        )
        self._restore_chart_view_state(restored)

    def set_historical_candle_pool(
        self,
        candles: list[dict[str, Any]],
        *,
        chart_candle_count: int,
    ) -> None:
        if not isinstance(candles, list) or any(
            not isinstance(candle, dict) for candle in candles
        ):
            raise TypeError("historical candle pool must be a list of objects")
        if (
            isinstance(chart_candle_count, bool)
            or not isinstance(chart_candle_count, int)
            or chart_candle_count <= 0
        ):
            raise ValueError("chart_candle_count must be a positive integer")
        trace_signal_validation_event(
            "window.historical_pool.begin",
            window_key=id(self),
            stock_code=self.stock.code,
            stock_name=self.stock.name,
            chart_candle_count=chart_candle_count,
            incoming_calculation=trace_candle_signature(candles),
            previous_calculation=trace_candle_signature(self._calculation_candles),
            previous_display=trace_candle_signature(self._candles),
            previous_range=self._validation_range,
            previous_range_times=self._validation_range_times,
            previous_range_replay_available=self._range_replay_entries_available,
        )
        intent = self._deferred_wheel_zoom
        old_display = self._candles
        old_pool = self._calculation_candles
        self._history_wheel_zoom_candidate = (
            (intent, old_display, len(candles) - len(old_pool))
            if intent is not None and old_pool and old_display
            and len(candles) > len(old_pool)
            and candles[-len(old_pool):] == old_pool
            else None
        )
        self._discard_chart_input()
        self._invalidate_visualization_projection_data()
        self._pending_prepared_presentation = None
        self._historical_candle_count = chart_candle_count
        # A pool install replaces the display before the prepared view is
        # published, regardless of whether the previous view was tagged mixed.
        # Only the calculation source is authoritative during this transition.
        self._marking_display_reprojection_pending = self._marking_times is not None
        self._marking_pool_publication_pending = self._marking_times is not None
        previous_candles = self._calculation_candles
        preserve_range_replay = (
            self._range_replay_entries_available
            and self._validation_range_times is not None
        )
        history_extension = (
            getattr(self, "canvas", None) is not None
            and bool(previous_candles)
            and bool(candles)
            and len(candles) > len(previous_candles)
            and str(candles[-1].get("time") or "")
            == str(previous_candles[-1].get("time") or "")
            and str(candles[0].get("time") or "")
            < str(previous_candles[0].get("time") or "")
        )
        self._pending_history_extension_view_anchor = (
            self._capture_history_extension_view_anchor()
            if history_extension
            else None
        )
        pool_signature = (
            len(candles),
            str(candles[0].get("time") or "") if candles else "",
            str(candles[-1].get("time") or "") if candles else "",
        )
        self._preserve_view_on_next_replay = (
            self._historical_pool_installed
            and self._historical_pool_signature == pool_signature
            and self.canvas is not None
        )
        self._historical_pool_signature = pool_signature
        self._calculation_candles = _detached_validation_candles(candles)
        self._display_only_integrated_candles = []
        self._display_only_integrated_active = False
        self._historical_pool_installed = True
        chart_count = min(chart_candle_count, len(self._calculation_candles))
        self._chart_pool_start_index = max(
            0,
            len(self._calculation_candles) - chart_count,
        )
        self._candles = _detached_validation_candles(
            self._calculation_candles[self._chart_pool_start_index :]
        )
        self._chart_calculation_indexes = tuple(range(
            self._chart_pool_start_index,
            len(self._calculation_candles),
        ))
        self._calculation_entries = []
        self._calculation_signal_marker_entries = []
        self._entries = []
        self._signal_marker_entries_available = False
        self._range_replay_calculation_entries = []
        self._range_replay_entries = []
        self._range_replay_settings_snapshot = None
        self._range_replay_entries_available = False
        self._range_replay_revalidation_pending = False
        if preserve_range_replay and history_extension:
            range_times = tuple(self._validation_range_times or ())
            calculation_times = {
                str(candle.get("time") or "")
                for candle in self._calculation_candles
            }
            if (
                len(range_times) == 2
                and all(str(value or "") in calculation_times for value in range_times)
            ):
                # A range replay is tied to the old calculation pool. Keep
                # its time selection, but never project stale B evidence.
                self._range_replay_revalidation_pending = True
        trace_signal_validation_event(
            "window.historical_pool.end",
            window_key=id(self),
            stock_code=self.stock.code,
            chart_candle_count=chart_candle_count,
            history_extension=history_extension,
            calculation=trace_candle_signature(self._calculation_candles),
            display=trace_candle_signature(self._candles),
            chart_pool_start_index=self._chart_pool_start_index,
            chart_index_count=len(self._chart_calculation_indexes),
            range=self._validation_range,
            range_times=self._validation_range_times,
            range_replay_available=self._range_replay_entries_available,
            range_revalidation_pending=self._range_replay_revalidation_pending,
        )

    def closeEvent(self, event) -> None:
        self._disable_marking(clear_cache=True, refresh=False)
        self._chart_results_suspended = True
        self._invalidate_user_background_results()
        self._discard_chart_input()
        self._visualization_projection_closed = True
        self._visualization_projection_generation += 1
        self._visualization_projection_pending = None
        self._visualization_projection_source = None
        self._visualization_projection_cache.clear()
        super().closeEvent(event)

    def set_historical_display_candles(
        self,
        display_candles: list[dict[str, Any]],
        *,
        display_only_integrated: bool = False,
        preserve_completed_cycles: bool = False,
    ) -> None:
        """Project visible bars without changing the installed calculation pool."""
        if not isinstance(display_candles, list) or not display_candles:
            raise ValueError("DISPLAY_CANDLES_UNAVAILABLE")
        indexes = (
            _calculation_indexes_for_mixed_display(
                self._calculation_candles,
                display_candles,
            )
            if display_only_integrated
            else _calculation_indexes_for_display(
                self._calculation_candles,
                display_candles,
            )
        )
        cycles = None
        # Only presentation refreshes retain these records. Full replay rebuilds
        # them; history browse owns its remap from the pre-pool display snapshot.
        if preserve_completed_cycles and self._completed_cycles:
            old_times = tuple(str(c.get("time") or "") for c in self._candles)
            new_times = tuple(str(c.get("time") or "") for c in display_candles)
            for times in (old_times, new_times):
                if any(not stamp for stamp in times) or any(
                    right <= left for left, right in zip(times, times[1:])
                ):
                    raise ValueError("COMPLETED_CYCLE_TIME_IDENTITY_INVALID")
            by_time = {stamp: index for index, stamp in enumerate(new_times)}

            def remap(index: int) -> int:
                if not 0 <= index < len(old_times) or old_times[index] not in by_time:
                    raise ValueError("COMPLETED_CYCLE_TIME_IDENTITY_INVALID")
                return by_time[old_times[index]]

            cycles = []
            for cycle in self._completed_cycles:
                mapped = replace(
                    cycle,
                    buy_indexes=tuple(remap(index) for index in cycle.buy_indexes),
                    buy_start_index=remap(cycle.buy_start_index),
                    buy_end_index=remap(cycle.buy_end_index),
                    sell_index=remap(cycle.sell_index),
                )
                cycles.append(cycle if mapped == cycle else mapped)
        # Reject ambiguous/missing cycle timestamps before changing live state.
        self._discard_chart_input()
        preserved_range_entries = (
            list(self._range_replay_calculation_entries)
            if self._range_replay_entries_available
            else None
        )
        self._candles = _detached_validation_candles(display_candles)
        if cycles is not None:
            self._completed_cycles = cycles
            table = self.completed_cycle_table
            with QSignalBlocker(table):
                for row, cycle in enumerate(cycles):
                    for column in range(table.columnCount()):
                        item = table.item(row, column)
                        if item is not None:
                            item.setData(Qt.UserRole, cycle.sell_index)
        self._display_only_integrated_active = display_only_integrated
        if display_only_integrated:
            # During pool installation this view can be an interim 500-bar
            # projection; only a later standalone integrated update is final.
            if not self._marking_pool_publication_pending:
                self._marking_display_reprojection_pending = False
        elif self._marking_display_reprojection_pending and self._marking_times is not None:
            frozen = self._marking_display_source or ()
            times = self._marking_times
            visible = [
                str(candle.get("time") or "") for candle in self._candles
                if times[0] <= str(candle.get("time") or "") <= times[1]
            ]
            if visible:
                low, high = visible[0], visible[-1]
                expected = [
                    str(candle.get("time") or "") for candle in frozen
                    if low <= str(candle.get("time") or "") <= high
                ]
                if visible == expected:
                    self._marking_display_reprojection_pending = False
        self._chart_calculation_indexes = indexes
        self._chart_pool_start_index = next(
            (index for index in indexes if index is not None),
            self._chart_pool_start_index,
        )
        # New display indexes have a different origin: restore the frozen
        # timestamp range before any marker/indicator projection observes it.
        if (
            self._validation_range_times is not None
            or self._validation_range_anchor_time is not None
        ):
            self._restore_validation_range_from_times()
        if preserved_range_entries is not None:
            (
                self._range_replay_calculation_entries,
                self._range_replay_entries,
            ) = self._mapped_entries(preserved_range_entries)
        trace_signal_validation_event(
            "window.display_candles.set",
            window_key=id(self),
            stock_code=self.stock.code,
            calculation=trace_candle_signature(self._calculation_candles),
            display=trace_candle_signature(self._candles),
            chart_pool_start_index=self._chart_pool_start_index,
            chart_index_count=len(self._chart_calculation_indexes),
            range=self._validation_range,
            range_times=self._validation_range_times,
            range_replay_available=self._range_replay_entries_available,
        )
        self._invalidate_visualization_projection_data()

    def set_display_only_integrated_candles(
        self,
        display_candles: list[dict[str, Any]],
    ) -> None:
        """Accept only candles already present in the calculation source."""
        if not isinstance(display_candles, list) or not display_candles:
            return
        projected = _integrated_display_with_krx_prefix(
            self._calculation_candles,
            display_candles,
            self._historical_candle_count,
        )
        if not projected:
            return
        if self.validation_regular_market_only_check.isChecked():
            return
        self.set_historical_display_candles(
            projected,
            display_only_integrated=False,
            preserve_completed_cycles=True,
        )
        self._display_only_integrated_candles = []
        self._preserve_view_on_next_replay = True
        if self._normal_base_ready and self._replay_snapshot is None:
            self.install_normal_base(self._result_settings_snapshot, self._normal_base_timeframe_key)
        elif isinstance(self._replay_snapshot, ValidationReplaySnapshot):
            self.set_replay_snapshot(
                self._replay_snapshot,
                presentation_only=True,
            )

    def _mapped_entries(
        self,
        entries: list[ValidationReplayEntry],
    ) -> tuple[list[ValidationReplayEntry], list[ValidationReplayEntry]]:
        return _mapped_validation_entries(
            self._calculation_candles,
            entries,
            chart_start_index=self._chart_pool_start_index,
            chart_count=len(self._candles),
            chart_calculation_indexes=self._chart_calculation_indexes,
        )

    def _mapped_replay_entries(
        self,
        replay_snapshot: ValidationReplaySnapshot,
    ) -> tuple[list[ValidationReplayEntry], list[ValidationReplayEntry]]:
        return self._mapped_entries(replay_snapshot.to_entries())

    def _set_signal_marker_entries(
        self,
        entries: list[ValidationReplayEntry],
        *,
        refresh_projection: bool,
    ) -> None:
        if not isinstance(entries, list) or any(
            not isinstance(entry, ValidationReplayEntry)
            for entry in entries
        ):
            raise TypeError("signal marker entries must be ValidationReplayEntry values")
        calculation_entries, _ = self._mapped_entries(entries)
        self._signal_tooltip_provenance = None
        self._pending_prepared_presentation = None
        self._pending_prepared_visualization_epoch = None
        self._calculation_signal_marker_entries = calculation_entries
        self._signal_marker_entries_available = True
        if not self._history_browse_installing:
            self._signal_marker_coverage_times = (
                (
                    str(self._calculation_candles[0].get("time") or ""),
                    str(self._calculation_candles[-1].get("time") or ""),
                )
                if self._calculation_candles
                else None
            )

        if not refresh_projection:
            return
        canvas = getattr(self, "canvas", None)
        if canvas is None or self._replay_snapshot is None:
            return
        self._invalidate_visualization_projection_data()
        self._ensure_signal_tooltips_current()
        markers = self._chart_marker_records()
        canvas.set_visualization_projection(
            self._visualization_descriptors,
            self._visualization_cache,
            self._visualization_active_by_marker,
            markers,
            self._visualization_lower_filter_presentations,
        )
        if self._validation_range is not None:
            canvas.set_validation_range(*self._validation_range)
        else:
            canvas.set_validation_range(None, None)
        self.fixed_price_axis.refresh_scale()
        self._refresh_valid_filter_emphasis()
        self._refresh_filter_display_projection()

    def set_signal_marker_entries(
        self,
        entries: list[ValidationReplayEntry],
    ) -> None:
        self._set_signal_marker_entries(entries, refresh_projection=True)

    def stage_signal_marker_entries(
        self,
        entries: list[ValidationReplayEntry],
    ) -> None:
        self._set_signal_marker_entries(entries, refresh_projection=False)

    def stage_prepared_validation_presentation(
        self,
        prepared: PreparedSignalValidationPresentation,
    ) -> None:
        if not isinstance(prepared, PreparedSignalValidationPresentation):
            raise TypeError("prepared presentation is required")
        if prepared.calculation_signature != _validation_candle_signature(
            self._calculation_candles
        ):
            raise ValueError("prepared presentation candle identity mismatch")
        if (
            prepared.chart_start_index != self._chart_pool_start_index
            or prepared.chart_count != len(self._candles)
            or prepared.chart_calculation_indexes
            not in ((), self._chart_calculation_indexes)
        ):
            raise ValueError("prepared presentation chart range mismatch")
        self._calculation_signal_marker_entries = list(
            prepared.calculation_entries
        )
        self._signal_tooltip_provenance = None
        self._signal_marker_entries_available = True
        if not self._history_browse_installing:
            coverage = (prepared.trust_metadata or {}).get(
                "signal_marker_coverage"
            )
            if isinstance(coverage, Mapping):
                start_time = str(coverage.get("start_time") or "")
                end_time = str(coverage.get("end_time") or "")
                self._signal_marker_coverage_times = (
                    (start_time, end_time)
                    if start_time and end_time
                    else None
                )
            else:
                self._signal_marker_coverage_times = None
        self._pending_prepared_presentation = prepared
        self._pending_prepared_visualization_epoch = self._visualization_data_epoch
        self._backtest_trust_metadata = deepcopy(prepared.trust_metadata or {})

    def apply_prepared_validation_result(
        self,
        *,
        calculation_candles: list[dict[str, Any]],
        chart_candle_count: int,
        replay_snapshot: ValidationReplaySnapshot,
        prepared_presentation: PreparedSignalValidationPresentation,
        display_candles: list[dict[str, Any]] | None = None,
        display_only_integrated: bool = False,
    ) -> None:
        """Publish a same-source policy refresh without replacing its canvas."""
        if (
            not isinstance(replay_snapshot, ValidationReplaySnapshot)
            or not isinstance(
                prepared_presentation,
                PreparedSignalValidationPresentation,
            )
            or prepared_presentation.settings_hash
            != replay_snapshot.settings_hash
        ):
            raise ValueError("PREPARED_VALIDATION_RESULT_IDENTITY_MISMATCH")
        if (
            isinstance(chart_candle_count, bool)
            or not isinstance(chart_candle_count, int)
            or chart_candle_count <= 0
        ):
            raise ValueError("chart_candle_count must be a positive integer")
        visible_display = (
            calculation_candles[-chart_candle_count:]
            if display_candles is None
            else display_candles[-chart_candle_count:]
        )
        same_source = bool(
            self.canvas is not None
            and replay_snapshot.stock == self.stock
            and replay_snapshot.timeframe_key == self._normal_base_timeframe_key
            and calculation_candles == self._calculation_candles
            and visible_display == self._candles
            and prepared_presentation.chart_count == len(self._candles)
            and prepared_presentation.chart_start_index
            == self._chart_pool_start_index
            and prepared_presentation.chart_calculation_indexes
            in ((), self._chart_calculation_indexes)
        )
        if not same_source:
            self.set_historical_candle_count(chart_candle_count)
            self.set_historical_candle_pool(
                calculation_candles,
                chart_candle_count=chart_candle_count,
            )
            if display_candles is not None:
                self.set_historical_display_candles(
                    visible_display,
                    display_only_integrated=display_only_integrated,
                )
        self.stage_prepared_validation_presentation(prepared_presentation)
        self.set_replay_snapshot(
            replay_snapshot,
            reuse_canvas=same_source,
        )

    @staticmethod
    def _replace_signal_entries_in_range(
        baseline: list[ValidationReplayEntry],
        override: list[ValidationReplayEntry],
        start_index: int,
        end_index: int,
    ) -> list[ValidationReplayEntry]:
        retained = [
            entry for entry in baseline
            if not start_index <= entry.evaluation_index <= end_index
        ]
        replacements = [
            entry for entry in override
            if (
                start_index <= entry.evaluation_index <= end_index
                and entry.signal == entry.evaluation_side
            )
        ]
        return IndicatorFollowSignalValidationV5Window._merged_replay_entries(
            retained,
            replacements,
        )

    def _marker_entries_for_display(self) -> list[ValidationReplayEntry]:
        # Full-validation entries may be retained for internal calculation,
        # but only an explicitly completed local range may publish idle
        # BUY/SELL triangles. Marking has its separate frozen projection.
        bounds = self._effective_validation_range()
        if not self._range_replay_entries_available or bounds is None:
            return []
        start, end = bounds
        return [
            entry for entry in self._range_replay_entries
            if start <= entry.evaluation_index <= end
            and entry.signal == entry.evaluation_side
        ]

    def _marker_entries_for_calculation(self) -> list[ValidationReplayEntry]:
        return (
            self._calculation_signal_marker_entries
            if self._signal_marker_entries_available
            else self._calculation_entries
            if self._calculation_entries
            else self._entries
        )

    def _authoritative_calculation_marker_entries(
        self,
    ) -> list[ValidationReplayEntry]:
        baseline = list(self._marker_entries_for_calculation())
        bounds = self._effective_validation_range()
        if not self._range_replay_entries_available or bounds is None:
            return baseline
        start, end = bounds
        calculation_bounds = self.calculation_bounds_for_display_range(
            start,
            end,
        )
        if calculation_bounds is None:
            return baseline
        calculation_start, calculation_end = calculation_bounds
        return self._replace_signal_entries_in_range(
            baseline,
            self._range_replay_calculation_entries,
            calculation_start,
            calculation_end,
        )

    @staticmethod
    def _merged_replay_entries(
        existing: list[ValidationReplayEntry],
        incoming: list[ValidationReplayEntry],
    ) -> list[ValidationReplayEntry]:
        merged = {
            (entry.evaluation_index, entry.evaluation_side): entry
            for entry in existing
        }
        for entry in incoming:
            merged[(entry.evaluation_index, entry.evaluation_side)] = entry
        return [
            merged[key]
            for key in sorted(merged)
        ]

    def apply_history_browse_presentation(
        self,
        calculation_candles: list[dict[str, Any]],
        *,
        chart_candle_count: int,
        prepared_presentation: PreparedSignalValidationPresentation,
        display_candles: list[dict[str, Any]] | None = None,
        display_only_integrated: bool = False,
    ) -> None:
        replay_snapshot = self._replay_snapshot
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise ValueError("HISTORY_BROWSE_REPLAY_SNAPSHOT_UNAVAILABLE")
        if replay_snapshot.settings_hash != prepared_presentation.settings_hash:
            raise ValueError("HISTORY_BROWSE_SETTINGS_CHANGED")
        # Validate the complete display projection before mutating live chart state.
        display = (
            calculation_candles[-chart_candle_count:]
            if display_candles is None else display_candles
        )
        indexes = (_calculation_indexes_for_mixed_display if display_only_integrated else _calculation_indexes_for_display)(calculation_candles, display)
        backed_indexes = tuple(
            index for index in indexes if index is not None
        )
        if (
            not backed_indexes
            or prepared_presentation.calculation_signature
            != _validation_candle_signature(calculation_candles)
            or prepared_presentation.chart_start_index != backed_indexes[0]
            or prepared_presentation.chart_count != len(indexes)
            or prepared_presentation.chart_calculation_indexes not in ((), indexes)
        ):
            raise ValueError("HISTORY_BROWSE_PRESENTATION_MISMATCH")
        previous = dict(vars(self))
        old_canvas = self.canvas
        label_handoff = old_canvas.capture_cycle_label_handoff() if old_canvas is not None else None
        old_display = self._candles
        by_time = {str(candle.get("time") or ""): index for index, candle in enumerate(display)}
        def remap(index: int) -> int:
            return by_time[str(old_display[index].get("time") or "")]
        # Reindex completed records; never re-simulate trades on expanded history.
        cycles = [replace(
            cycle,
            buy_indexes=tuple(remap(index) for index in cycle.buy_indexes),
            buy_start_index=remap(cycle.buy_start_index),
            buy_end_index=remap(cycle.buy_end_index),
            sell_index=remap(cycle.sell_index),
        ) for cycle in self._completed_cycles]
        labels = {
            label: label.text() for label in (
                self.validation_status_label, self.result_summary_label,
                self.estimated_return_label,
            )
        }
        roles = [
            (item, item.data(Qt.UserRole))
            for row in range(self.completed_cycle_table.rowCount())
            for column in range(self.completed_cycle_table.columnCount())
            if (item := self.completed_cycle_table.item(row, column)) is not None
        ]
        button_enabled = self.primary_validation_action_button.isEnabled()
        summary_tooltip = self.result_summary_label.toolTip()
        self._history_browse_installing = True
        try:
            self.set_historical_candle_pool(
                calculation_candles,
                chart_candle_count=chart_candle_count,
            )
            stale_range_replay = self._range_replay_revalidation_pending
            self.set_historical_display_candles(
                display,
                display_only_integrated=display_only_integrated,
            )
            if display_only_integrated:
                self._display_only_integrated_candles = (
                    _detached_validation_candles(display)
                )
            self.stage_prepared_validation_presentation(prepared_presentation)
            self.set_replay_snapshot(replay_snapshot)
            self._calculation_entries, self._entries = self._mapped_entries(
                list(previous["_calculation_entries"])
            )
            if not stale_range_replay:
                (
                    self._range_replay_calculation_entries,
                    self._range_replay_entries,
                ) = self._mapped_entries(list(previous["_range_replay_calculation_entries"]))
                self._range_replay_entries_available = previous["_range_replay_entries_available"]
                self._range_replay_revalidation_pending = previous[
                    "_range_replay_revalidation_pending"
                ]
            self._refresh_signal_tooltips_only()
            if stale_range_replay:
                self._refresh_validation_range_results()
            else:
                self._completed_cycles = cycles
                for row, cycle in enumerate(cycles):
                    for column in range(self.completed_cycle_table.columnCount()):
                        item = self.completed_cycle_table.item(row, column)
                        if item is not None:
                            item.setData(Qt.UserRole, cycle.sell_index)
                self.canvas.set_completed_cycle_average_lines(
                    tuple(cycles) if self._effective_validation_range() is not None else ()
                )
            self._refresh_filter_display_projection()
            # Preserve the exact prior pane geometry only at the same viewport
            # size. Default panes can exceed the interactive resize clamp, so
            # passing them through the setter would change an unchanged view.
            if (old_canvas is not None
                    and self.canvas.size() == old_canvas.size()
                    and self.canvas._lower_families == old_canvas._lower_families):
                self.canvas._lower_pane_heights = dict(old_canvas._lower_pane_heights)
                self.canvas._static_chart_cache = None
                self.canvas._static_chart_cache_key = None
                self.fixed_price_axis.refresh_scale()
                self.canvas.update()
            if label_handoff is not None:
                self.canvas.install_cycle_label_handoff(label_handoff)
        except Exception:
            # Keep the old widget alive until the entire browse transaction commits.
            failed_canvas = self.canvas
            generations = {
                name: value for name, value in vars(self).items()
                if name.endswith("_generation")
                or name in ("_visualization_data_epoch", "_chart_interaction_epoch")
            }
            vars(self).update(previous)
            vars(self).update(generations)  # Cancellation identities never go backwards.
            self._discard_chart_input()
            self._history_wheel_zoom_candidate = None
            self._cancel_visualization_projection_work()
            installed = self.chart_scroll_area.widget()
            if installed is not old_canvas:
                installed = self.chart_scroll_area.takeWidget()
                if installed is not None:
                    installed.deleteLater()
                self.chart_scroll_area.setWidget(old_canvas)
            elif failed_canvas is not old_canvas and failed_canvas is not None:
                failed_canvas.deleteLater()
            self.fixed_price_axis.set_canvas(old_canvas)
            self._sync_time_navigation_scrollbar()
            for label, text in labels.items():
                label.setText(text)
            self.result_summary_label.setToolTip(summary_tooltip)
            for item, role in roles:
                item.setData(Qt.UserRole, role)
            self._set_primary_validation_action_state(
                previous["_primary_validation_action_state"], enabled=button_enabled
            )
            raise
        else:
            self._resume_history_wheel_zoom(
                timeframe_key=replay_snapshot.timeframe_key,
                settings_hash=replay_snapshot.settings_hash,
            )
            if old_canvas is not None and old_canvas is not self.canvas:
                old_canvas.deleteLater()
        finally:
            self._history_browse_installing = False

    def _install_normal_chart_canvas(
        self,
        timeframe_key: str,
        markers: list[dict[str, Any]],
        *,
        history_browse: bool = False,
        defer_scrollbar_sync: bool = False,
    ) -> None:
        """Common interactive canvas installation for base and replay results."""
        candle_count = len(self._candles)
        visible_candle_span = min(
            float(max(1, candle_count)),
            self._REFERENCE_CANDLE_COUNT,
        )
        visible_start_index = max(
            0.0,
            candle_count - visible_candle_span,
        )
        next_canvas = IndicatorFollowSignalValidationChartCanvas(
            self._candles,
            markers,
            timeframe_key=timeframe_key,
            visualization_descriptors=self._visualization_descriptors,
            visualization_cache=self._visualization_cache,
            visualization_active_by_marker=self._visualization_active_by_marker,
            lower_filter_presentations=(
                self._visualization_lower_filter_presentations
            ),
            hidden_price_filter_identities=tuple(
                self._hidden_price_filter_identities
            ),
            highlighted_price_filter_identities=tuple(
                self._highlighted_price_filter_identities
            ),
            highlighted_lower_filter_identities=tuple(
                self._highlighted_lower_filter_identities
            ),
            not_filter_identities=tuple(self._not_filter_identities),
            inner_not_filter_identities=tuple(
                self._inner_not_filter_identities
            ),
            hide_unhighlighted_price_filters=self._hide_unhighlighted_price_filters,
            bridgeable_display_only_indexes=frozenset(
                index
                for index, calculation_index in enumerate(
                    self._chart_calculation_indexes
                )
                if self._display_only_integrated_active
                and calculation_index is None
            ),
        )
        try:
            next_canvas.set_ocr_turn_delays(self._ocr_turn_delays_from_ui_state(
                self._result_ui_state, next_canvas._visualization_descriptors,
            ))
            next_canvas.set_time_view(visible_start_index, visible_candle_span)
            initial_price_scale = next_canvas.price_scale()
            next_canvas._input_frame_owner = self
            next_canvas.bar_selected.connect(self.select_evaluation_index)
            next_canvas.validation_range_drag_started.connect(
                self._begin_validation_range_drag
            )
            next_canvas.validation_range_drag_completed.connect(
                self._complete_validation_range_drag
            )
            next_canvas.validation_range_clear_requested.connect(
                self._clear_validation_range_interaction
            )
            next_canvas.time_scale_wheel_requested.connect(self._queue_time_scale_wheel)
            next_canvas.pan_requested.connect(self._handle_pan_requested)
            next_canvas.pointer_interaction_started.connect(
                self._on_chart_interaction_started
            )
            next_canvas.pointer_interaction_ended.connect(
                self._on_chart_interaction_ended
            )
            next_canvas.installEventFilter(self)
        except Exception:
            next_canvas.deleteLater()
            raise
        self._cancel_chart_input_frame()
        old_canvas = self.chart_scroll_area.takeWidget()
        self.canvas = next_canvas
        self._visible_candle_span = visible_candle_span
        self._visible_start_index = visible_start_index
        self._time_scale_manually_adjusted = False
        if initial_price_scale is None:
            self._default_price_minimum = None
            self._default_price_maximum = None
            self._current_price_minimum = None
            self._current_price_maximum = None
        else:
            self._default_price_minimum = initial_price_scale.minimum
            self._default_price_maximum = initial_price_scale.maximum
            self._current_price_minimum = initial_price_scale.minimum
            self._current_price_maximum = initial_price_scale.maximum
        self._price_scale_manually_adjusted = False
        self.chart_scroll_area.setWidget(self.canvas)
        if old_canvas is not None and not history_browse:
            old_canvas.deleteLater()
        # A preserved view restores its timestamp anchor immediately after
        # this installation. Keep the previous scrollbar state until that
        # restore publishes the one final range/page/value combination.
        if not defer_scrollbar_sync:
            self._sync_time_navigation_scrollbar()
        self.fixed_price_axis.set_canvas(self.canvas)
        self.canvas.pane_layout_changed.connect(
            self.fixed_price_axis.refresh_scale
        )
        self.canvas.price_legend_visibility_changed.connect(
            self._remember_price_legend_visibility
        )
        self.canvas.price_legend_highlight_changed.connect(
            self._remember_price_legend_highlight
        )
        self.canvas.price_legend_hide_unhighlighted_changed.connect(
            self._remember_price_legend_hide_unhighlighted
        )
        self.canvas.price_legend_prefix_reset_requested.connect(
            self._on_price_legend_prefix_reset_requested
        )
        self.canvas.lower_legend_highlight_changed.connect(
            self._remember_lower_legend_highlight
        )
        self.chart_stack.setCurrentWidget(self.chart_view)

    def install_normal_base(
        self, settings_snapshot: ValidationSettingsSnapshot, timeframe_key: str, *,
        presentation: tuple[tuple[ValidationFilterDescriptor, ...], ValidationIndicatorSeriesCache] | None = None,
        defer_marking_source_revision: bool = False,
        reuse_canvas: bool = False,
    ) -> None:
        """Publish candle/indicator readiness without manufacturing replay evidence."""
        if not isinstance(settings_snapshot, ValidationSettingsSnapshot):
            raise TypeError("settings_snapshot must be ValidationSettingsSnapshot")
        if not self._historical_pool_installed or not self._calculation_candles or not self._candles:
            raise ValueError("NORMAL_BASE_CANDLES_UNAVAILABLE")
        timeframe_changed = (
            bool(self._normal_base_timeframe_key)
            and self._normal_base_timeframe_key != timeframe_key
        )
        anchor = (
            None
            if timeframe_changed
            else self._pending_history_extension_view_anchor
        )
        self._pending_history_extension_view_anchor = None
        view = (
            self._current_chart_view_state()
            if getattr(self, "canvas", None) is not None and not timeframe_changed
            else None
        )
        if timeframe_changed:
            # Initial price/time axes and Reset must belong to the new timeframe.
            self._entry_chart_view_state = None
            self._entry_chart_view_anchor = None
        self._discard_chart_input()
        self._normal_base_requested = True
        self._normal_base_ready = False
        self._range_replay_from_normal_base = False
        self._normal_base_timeframe_key = timeframe_key
        self._replay_snapshot = None
        self._result_settings_snapshot = settings_snapshot
        if isinstance(self._pending_result_ui_state, Mapping):
            self._result_ui_state = deepcopy(self._pending_result_ui_state)
            self._result_ui_state_revision += 1
        self._pending_result_settings_snapshot = None
        self._pending_result_ui_state = None
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self._signal_tooltips = {}
        self._invalidate_visualization_projection_data()
        if presentation is None:
            self._rebuild_visualization_data()
            if self._visualization_data_error:
                raise ValueError(self._visualization_data_error)
        else:
            self._visualization_descriptors, self._visualization_cache = presentation
            self._visualization_active_by_marker = {}
            self._visualization_current_key = self._visualization_projection_key(self._visualization_descriptors)
            self._visualization_data_error = ""
        self._revalidate_marking_source()
        self._visualization_cache = self._clip_comparison_cache(self._visualization_descriptors, self._visualization_cache)
        self._refresh_lower_filter_presentations(
            settings_snapshot.to_dict(),
            self._visualization_descriptors,
        )
        markers = self._chart_marker_records()
        if reuse_canvas and self.canvas is not None:
            self.canvas.set_visualization_projection(
                self._visualization_descriptors,
                self._visualization_cache,
                self._visualization_active_by_marker,
                markers,
                self._visualization_lower_filter_presentations,
            )
            self.chart_stack.setCurrentWidget(self.chart_view)
            self.fixed_price_axis.refresh_scale()
        else:
            self._install_normal_chart_canvas(
                timeframe_key,
                markers,
                defer_scrollbar_sync=anchor is not None or view is not None,
            )
        self._normal_base_ready = True
        self._restore_validation_range_from_times()
        self.canvas.set_validation_range(*(self._validation_range or (None, None)))
        self.chart_view.setEnabled(True)
        self.validation_status_label.setText("캔들·설정 지표 준비 완료 · 신호 미계산 (유효 여부 미계산)")
        self.result_summary_label.setText(f"{self.stock.code} {self.stock.name} | {timeframe_display_label(timeframe_key)}봉")
        self.estimated_return_label.setText("")
        self.result_summary_label.setToolTip(self.validation_status_label.text())
        self._set_primary_validation_action_state("validate")
        if anchor is not None:
            self._restore_history_extension_view_anchor(anchor)
        elif view is not None:
            self._restore_chart_view_state(view)
        else:
            self.select_evaluation_index(len(self._candles) - 1)
        self._capture_entry_chart_view_state()
        self._resume_history_wheel_zoom(
            timeframe_key=timeframe_key, settings_snapshot=settings_snapshot,
        )
        self._refresh_filter_display_projection()
        self._marking_pool_publication_pending = False
        self._resume_marking_policy_refresh()
        if self._validation_range is not None:
            self.validation_range_evaluation_requested.emit(*self._validation_range)

    def set_live_sor_monitor_signal(self, result: object, *, source_as_of: str) -> None:
        """Show a current routine observation without changing chart or trade state."""
        if (not isinstance(result, dict)
                or result.get("stock_code") != self.stock.code
                or result.get("timeframe_key") != self._normal_base_timeframe_key
                or not self._normal_base_ready
                or self._normal_base_timeframe_key not in {
                    "M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240",
                    "D1", "W1", "MO1", "Y1"
                }
                or self._replay_snapshot is not None
                or self.validation_range is not None
                or self.marking_display_check.isChecked()):
            return
        signal = result.get("signal")
        selection = signal if isinstance(signal, str) and signal in {"BUY", "SELL"} else "none"
        if result.get("unavailable") is True:
            selection = "unavailable"
        conflict = " | BUY/SELL conflict" if result.get("conflict") else ""
        timeframe = self._normal_base_timeframe_key
        self.validation_status_label.setText(
            f"SOR _AL {timeframe} | {selection}{conflict} | {source_as_of[11:19]} | read-only"
        )
        self.result_summary_label.setToolTip(
            f"SOR _AL {timeframe} watch | {selection}{conflict} | as_of {source_as_of}"
            " | read-only; position unknown; no trade permission"
        )

    def set_replay_snapshot(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        *,
        presentation_only: bool = False,
        reuse_canvas: bool = False,
    ) -> None:
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
        if replay_snapshot.stock != self.stock:
            raise ValueError("replay stock identity mismatch")
        self._preserve_chart_for_pending_validation = False
        trace_signal_validation_event(
            "window.replay_snapshot.begin",
            window_key=id(self),
            stock_code=self.stock.code,
            snapshot_timeframe=str(replay_snapshot.timeframe_key),
            snapshot_settings_hash=replay_snapshot.settings_hash,
            snapshot_evaluated_start=int(replay_snapshot.evaluated_start_index),
            snapshot_evaluated_end=int(replay_snapshot.evaluated_end_index),
            snapshot_candles=trace_candle_signature(replay_snapshot.to_candles()),
            snapshot_entry_count=len(replay_snapshot.to_entries()),
            previous_display=trace_candle_signature(self._candles),
            previous_calculation=trace_candle_signature(self._calculation_candles),
            previous_range=self._validation_range,
            previous_range_times=self._validation_range_times,
            previous_range_replay_available=self._range_replay_entries_available,
        )
        self.clear_candle_preview(self._candle_preview_token)
        self._discard_chart_input()
        history_browse = self._history_browse_installing
        if not history_browse and not presentation_only:
            self._range_replay_from_normal_base = False
        previous_snapshot = self._replay_snapshot
        settings_identity_changed = (
            isinstance(previous_snapshot, ValidationReplaySnapshot)
            and previous_snapshot.settings_hash != replay_snapshot.settings_hash
        )
        if settings_identity_changed:
            self._clear_range_replay_override()
        history_extension_anchor = self._pending_history_extension_view_anchor
        self._pending_history_extension_view_anchor = None
        prepared_presentation = self._pending_prepared_presentation
        self._pending_prepared_presentation = None
        prepared_visualization_epoch = self._pending_prepared_visualization_epoch
        self._pending_prepared_visualization_epoch = None
        preserved_view_state = (
            self._current_chart_view_state()
            if reuse_canvas and self.canvas is not None
            else None
        )
        if (
            preserved_view_state is None
            and self._preserve_view_on_next_replay
            and isinstance(previous_snapshot, ValidationReplaySnapshot)
            and previous_snapshot.stock == replay_snapshot.stock
            and previous_snapshot.timeframe_minutes == replay_snapshot.timeframe_minutes
            and previous_snapshot.timeframe_key == replay_snapshot.timeframe_key
            and self.canvas is not None
        ):
            preserved_view_state = self._current_chart_view_state()
        self._preserve_view_on_next_replay = False
        self._normal_base_requested = False
        self._normal_base_ready = False
        self._replay_snapshot = replay_snapshot
        if not self._historical_pool_installed:
            self._calculation_candles = replay_snapshot.to_candles()
            self._chart_pool_start_index = replay_snapshot.evaluated_start_index
            self._candles = replay_snapshot.to_display_candles()
            self._chart_calculation_indexes = (
                _calculation_indexes_for_mixed_display(
                    self._calculation_candles,
                    self._candles,
                )
                if self._display_only_integrated_active
                else _calculation_indexes_for_display(
                    self._calculation_candles,
                    self._candles,
                )
            )
            self._calculation_signal_marker_entries = []
            self._signal_marker_entries_available = False
        calculation_entries, chart_entries = self._mapped_replay_entries(
            replay_snapshot
        )
        if presentation_only and self._range_replay_from_normal_base:
            # A temporary range must never become authoritative base evidence
            # when a display-only refresh rebuilds the chart.
            if self._range_replay_entries_available:
                self._range_replay_calculation_entries = calculation_entries
                self._range_replay_entries = chart_entries
        else:
            self._calculation_entries = calculation_entries
            self._entries = chart_entries
        # Resolve shifted display indexes before range overrides select markers.
        self._restore_validation_range_from_times()
        settings_snapshot = self._pending_result_settings_snapshot
        pending_ui_state = self._pending_result_ui_state
        self._pending_result_settings_snapshot = None
        self._pending_result_ui_state = None
        if (
            isinstance(settings_snapshot, ValidationSettingsSnapshot)
            and settings_snapshot.rules_hash == replay_snapshot.settings_hash
        ):
            self._result_settings_snapshot = settings_snapshot
            if isinstance(pending_ui_state, Mapping):
                self._result_ui_state = deepcopy(dict(pending_ui_state))
                self._result_ui_state_revision += 1
                self._all_filter_descriptor_cache_key = None
                self._all_filter_descriptor_cache_ui_state = None
        elif (
            isinstance(self._result_settings_snapshot, ValidationSettingsSnapshot)
            and self._result_settings_snapshot.rules_hash
            == replay_snapshot.settings_hash
        ):
            # Background history refresh may deliver a second replay for the
            # same validation settings after the pending snapshot was consumed
            # by the cache-first replay. Preserve that settings identity so
            # filter visualization data is rebuilt instead of being cleared.
            pass
        elif (
            self._signal_validation_seed.settings_snapshot.rules_hash
            == replay_snapshot.settings_hash
        ):
            self._result_settings_snapshot = self._signal_validation_seed.settings_snapshot
        else:
            self._result_settings_snapshot = None
        self._invalidate_visualization_projection_data()
        prepared_is_current = (
            isinstance(
                prepared_presentation,
                PreparedSignalValidationPresentation,
            )
            and prepared_presentation.settings_hash
            == replay_snapshot.settings_hash
            and isinstance(self._result_settings_snapshot, ValidationSettingsSnapshot)
            and self._result_settings_snapshot.rules_hash == replay_snapshot.settings_hash
            and prepared_visualization_epoch is not None
            and prepared_visualization_epoch + 1
            == self._visualization_data_epoch
            and prepared_presentation.calculation_signature
            == _validation_candle_signature(self._calculation_candles)
            and prepared_presentation.chart_start_index
            == self._chart_pool_start_index
            and prepared_presentation.chart_count == len(self._candles)
            and prepared_presentation.chart_calculation_indexes
            in ((), self._chart_calculation_indexes)
        )
        if prepared_is_current:
            self._visualization_descriptors = (
                prepared_presentation.visualization_descriptors
            )
            self._visualization_cache = prepared_presentation.visualization_cache
            self._visualization_active_by_marker = dict(
                prepared_presentation.visualization_active_by_marker
            )
            prepared_evidence = (
                prepared_presentation.visualization_evidence_identities_by_side
            )
            if isinstance(prepared_evidence, Mapping):
                self._filter_display_evidence_identities_by_side = {
                    side: frozenset(
                        str(identity or "").strip()
                        for identity in prepared_evidence.get(side, ())
                        if str(identity or "").strip()
                    )
                    for side in ("BUY", "SELL")
                }
                self._filter_display_evidence_cache_epoch = (
                    self._visualization_data_epoch
                )
            else:
                self._cache_filter_display_evidence_identities(
                    prepared_presentation.visualization_active_by_marker
                )
            if (
                not self._range_replay_entries_available
                or self._effective_validation_range() is None
            ):
                # Worker evidence is current for the installed base source.
                # Preserve the existing virtual execution suffixes separately.
                self._signal_tooltips = self._append_signal_execution_tooltip_lines(
                    dict(prepared_presentation.signal_tooltips),
                    self._result_settings_snapshot.to_dict(),
                )
                self._signal_tooltip_provenance = self._signal_tooltip_source_key()
            else:
                self._ensure_signal_tooltips_current()
            self._visualization_data_error = ""
            self._visualization_current_key = self._visualization_projection_key(
                self._visualization_descriptors
            )
        else:
            self._visualization_descriptors = ()
            self._visualization_cache = None
            self._visualization_active_by_marker = {}
            self._visualization_current_key = None
            self._ensure_signal_tooltips_current()
            self._rebuild_visualization_data()
        if isinstance(self._result_settings_snapshot, ValidationSettingsSnapshot):
            display_rules = self._result_settings_snapshot.to_dict()
            desired_descriptors = self._filter_display_descriptors(
                display_rules
            )
            cached_projection = self._cached_projection_for_descriptors(
                desired_descriptors
            )
            self._visualization_descriptors = desired_descriptors
            self._refresh_lower_filter_presentations(
                display_rules,
                self._visualization_descriptors,
            )
            if cached_projection is not None:
                (
                    self._visualization_cache,
                    self._visualization_active_by_marker,
                ) = cached_projection
        markers = self._chart_marker_records()
        candle_count = len(self._candles)
        if reuse_canvas:
            self.canvas.set_visualization_projection(
                self._visualization_descriptors,
                self._visualization_cache,
                self._visualization_active_by_marker,
                markers,
                self._visualization_lower_filter_presentations,
            )
            self.chart_stack.setCurrentWidget(self.chart_view)
            self.fixed_price_axis.refresh_scale()
        else:
            self._install_normal_chart_canvas(
                replay_snapshot.timeframe_key,
                markers,
                history_browse=history_browse,
                defer_scrollbar_sync=(
                    history_extension_anchor is not None
                    or preserved_view_state is not None
                ),
            )
        trace_signal_validation_event(
            "window.replay_snapshot.range_restored",
            window_key=id(self),
            stock_code=self.stock.code,
            snapshot_timeframe=str(replay_snapshot.timeframe_key),
            display=trace_candle_signature(self._candles),
            calculation=trace_candle_signature(self._calculation_candles),
            chart_pool_start_index=self._chart_pool_start_index,
            chart_index_count=len(self._chart_calculation_indexes),
            range=self._validation_range,
            range_times=self._validation_range_times,
            range_replay_available=self._range_replay_entries_available,
            range_revalidation_pending=self._range_replay_revalidation_pending,
        )
        if self._validation_range is not None:
            self.canvas.set_validation_range(*self._validation_range)
        else:
            self.canvas.set_validation_range(None, None)
        if not history_browse and not presentation_only:
            self._refresh_validation_range_results()
        elif not history_browse and presentation_only:
            self.canvas.set_completed_cycle_average_lines(
                tuple(self._completed_cycles)
                if self._effective_validation_range() is not None else ()
            )
        if (
            not presentation_only
            and self._validation_range is not None
            and (
                (
                    not history_browse
                    and (
                        not self._range_replay_entries_available
                        or self._range_replay_revalidation_pending
                    )
                )
                or (
                    history_browse
                    and self._range_replay_revalidation_pending
                )
            )
        ):
            range_start, range_end = self._validation_range
            self._range_replay_revalidation_pending = False
            self.validation_range_evaluation_requested.emit(
                range_start,
                range_end,
            )
        if not presentation_only:
            self.validation_status_label.setText(
                "추가 과거 구간: 표시 전용 · 재검증 필요 (기존 결과는 이전 자료 기준)"
                if history_browse
                else ""
            )
            self.result_summary_label.setToolTip(
                self.validation_status_label.text()
            )
            pending_fingerprint = self._pending_validation_ui_fingerprint
            self._pending_validation_ui_fingerprint = None
            try:
                current_fingerprint = self._current_signal_ui_fingerprint()
            except Exception:
                current_fingerprint = None
            if history_browse:
                self._validated_ui_fingerprint = None
                self._settings_apply_after_validation = False
                self._set_primary_validation_action_state("validate")
            elif history_extension_anchor is not None:
                self._validated_ui_fingerprint = (
                    history_extension_anchor.validated_ui_fingerprint
                )
                self._settings_apply_after_validation = False
                self._set_primary_validation_action_state(
                    history_extension_anchor.primary_action_state
                )
            elif (
                pending_fingerprint is not None
                and current_fingerprint == pending_fingerprint
            ):
                self._validated_ui_fingerprint = pending_fingerprint
                self._set_primary_validation_action_state("apply")
                apply_after_validation = self._settings_apply_after_validation
                self._settings_apply_after_validation = False
                if apply_after_validation:
                    self._request_settings_apply()
            else:
                self._validated_ui_fingerprint = None
                self._settings_apply_after_validation = False
                self._set_primary_validation_action_state("validate")
        if history_extension_anchor is not None:
            self._restore_history_extension_view_anchor(history_extension_anchor)
        elif preserved_view_state is not None:
            self._restore_chart_view_state(preserved_view_state)
        else:
            self.select_evaluation_index(max(0, candle_count - 1))
        self._capture_entry_chart_view_state()
        if not history_browse:
            self._refresh_filter_display_projection()
        self._marking_pool_publication_pending = False
        if not history_browse and not presentation_only:
            self._resume_marking_policy_refresh()

    def apply_range_replay_snapshot(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        *,
        settings_snapshot: ValidationSettingsSnapshot | None = None,
    ) -> None:
        self._apply_range_replay_snapshot(
            replay_snapshot,
            defer_visualization=False,
            settings_snapshot=settings_snapshot,
        )

    def apply_range_replay_snapshot_async(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        *,
        settings_snapshot: ValidationSettingsSnapshot | None = None,
    ) -> None:
        self._apply_range_replay_snapshot(
            replay_snapshot,
            defer_visualization=True,
            settings_snapshot=settings_snapshot,
        )

    def _apply_range_replay_snapshot(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        *,
        defer_visualization: bool,
        settings_snapshot: ValidationSettingsSnapshot | None,
    ) -> None:
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
        if replay_snapshot.stock != self.stock:
            raise ValueError("replay stock identity mismatch")
        if settings_snapshot is not None and (
            not isinstance(settings_snapshot, ValidationSettingsSnapshot)
            or settings_snapshot.rules_hash != replay_snapshot.settings_hash
        ):
            raise ValueError("range replay settings identity mismatch")
        if (
            settings_snapshot is None
            and isinstance(self._result_settings_snapshot, ValidationSettingsSnapshot)
            and self._result_settings_snapshot.rules_hash
            != replay_snapshot.settings_hash
        ):
            raise ValueError("range replay settings identity mismatch")
        trace_signal_validation_event(
            "window.range_replay.apply_begin",
            window_key=id(self),
            stock_code=self.stock.code,
            defer_visualization=defer_visualization,
            snapshot_timeframe=str(replay_snapshot.timeframe_key),
            snapshot_evaluated_start=int(replay_snapshot.evaluated_start_index),
            snapshot_evaluated_end=int(replay_snapshot.evaluated_end_index),
            snapshot_candles=trace_candle_signature(replay_snapshot.to_candles()),
            snapshot_entry_count=len(replay_snapshot.to_entries()),
            display=trace_candle_signature(self._candles),
            calculation=trace_candle_signature(self._calculation_candles),
            range=self._validation_range,
            range_times=self._validation_range_times,
            chart_pool_start_index=self._chart_pool_start_index,
            chart_index_count=len(self._chart_calculation_indexes),
        )
        calculation_entries, chart_entries = self._mapped_replay_entries(
            replay_snapshot
        )
        bounds = self._effective_validation_range()
        if bounds is None:
            raise ValueError("range replay requires an active validation range")
        self._discard_chart_input()
        start, end = bounds
        calculation_bounds = self.calculation_bounds_for_display_range(
            start,
            end,
        )
        if calculation_bounds is None:
            self.apply_no_calculation_range_selection(
                defer_visualization=defer_visualization,
            )
            return
        # Range results are temporary overlays; clearing must reveal the baseline.
        self._range_replay_calculation_entries = list(calculation_entries)
        self._range_replay_entries = list(chart_entries)
        self._range_replay_settings_snapshot = settings_snapshot
        self._range_replay_entries_available = True
        self._range_replay_revalidation_pending = False
        if self._normal_base_requested and self._normal_base_ready and self._replay_snapshot is None:
            self._range_replay_from_normal_base = True
        self._normal_base_requested = False
        self._normal_base_ready = False
        self._invalidate_visualization_projection_data()
        if not defer_visualization:
            self._rebuild_visualization_data()
        self._ensure_signal_tooltips_current()
        markers = self._chart_marker_records()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_visualization_projection(
                self._visualization_descriptors,
                self._visualization_cache,
                self._visualization_active_by_marker,
                markers,
                self._visualization_lower_filter_presentations,
            )
            if self._validation_range is not None:
                canvas.set_validation_range(*self._validation_range)
        self._refresh_valid_filter_emphasis()
        self._refresh_validation_range_results()
        trace_signal_validation_event(
            "window.range_replay.apply_end",
            window_key=id(self),
            stock_code=self.stock.code,
            defer_visualization=defer_visualization,
            display=trace_candle_signature(self._candles),
            calculation=trace_candle_signature(self._calculation_candles),
            range=self._validation_range,
            range_times=self._validation_range_times,
            range_replay_entry_count=len(self._range_replay_entries),
            range_replay_calculation_entry_count=len(
                self._range_replay_calculation_entries
            ),
            completed_cycle_count=len(self._completed_cycles),
            result_summary=self.result_summary_label.text(),
            financial_summary=self.estimated_return_label.text(),
        )
        try:
            if signal_validation_trace_enabled():
                self._trace_range_replay_parity_evidence(
                    replay_snapshot, calculation_bounds
                )
        except Exception:
            # Optional diagnostics cannot affect an accepted replay.
            pass
        self.validation_status_label.setText("")
        self.fixed_price_axis.refresh_scale()
        if defer_visualization:
            self._refresh_filter_display_projection()

    def _trace_range_replay_parity_evidence(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        calculation_bounds: tuple[int, int],
    ) -> None:
        settings = (
            self._range_replay_settings_snapshot
            if self._range_replay_settings_snapshot is not None
            else self._result_settings_snapshot
        )
        bounds = self._effective_validation_range()
        source = self._calculation_candles
        snapshot_candles = replay_snapshot.to_candles()
        range_times = self._validation_range_times
        if (
            not self._range_replay_entries_available
            or self._range_replay_revalidation_pending
            or not isinstance(settings, ValidationSettingsSnapshot)
            or not settings.rules_hash
            or settings.rules_hash != replay_snapshot.settings_hash
            or hashlib.sha256(settings.canonical_json.encode("utf-8")).hexdigest()
            != settings.rules_hash
            or not replay_snapshot.historical_request_id
            or replay_snapshot.timeframe_key != self._normal_base_timeframe_key
            or bounds is None
            or not isinstance(range_times, tuple)
            or len(range_times) != 2
            or not source
        ):
            return
        start, end = bounds
        calc_start, calc_end = calculation_bounds
        if (
            not 0 <= calc_start <= calc_end < len(source)
            or range_times
            != (
                str(self._candles[start].get("time") or ""),
                str(self._candles[end].get("time") or ""),
            )
            or not all(range_times)
            or not all(
                isinstance(candle, dict) and str(candle.get("time") or "")
                for candle in source
            )
            or replay_snapshot.evaluated_start_index != calc_start
            or replay_snapshot.evaluated_end_index != calc_end
            or not snapshot_candles
            or len(snapshot_candles) != calc_end + 1
            or snapshot_candles != source[:calc_end + 1]
            or self._mapped_replay_entries(replay_snapshot)[1]
            != self._range_replay_entries
        ):
            return
        calc_times = (
            str(source[calc_start]["time"]),
            str(source[calc_end]["time"]),
        )
        signals = []
        seen = set()
        for entry in self._marker_entries_for_display():
            evaluation_index = entry.evaluation_index
            signal_index = entry.signal_index
            key = (evaluation_index, entry.evaluation_side)
            if (
                entry.signal not in {"BUY", "SELL"}
                or entry.signal != entry.evaluation_side
                or key in seen
                or isinstance(evaluation_index, bool)
                or not isinstance(evaluation_index, int)
                or not start <= evaluation_index <= end
                or isinstance(signal_index, bool)
                or not isinstance(signal_index, int)
                or not 0 <= signal_index < len(self._candles)
                or self._candles[evaluation_index].get("time") != entry.evaluation_time
                or self._candles[signal_index].get("time") != entry.signal_time
            ):
                return
            seen.add(key)
            signals.append({
                "side": entry.signal,
                "evaluation_index": evaluation_index,
                "evaluation_time": entry.evaluation_time,
                "signal_index": signal_index,
                "signal_time": entry.signal_time,
                "delay_bar": entry.delay_bar,
            })
        signals.sort(key=lambda row: (row["evaluation_index"], row["side"]))
        source_json = json.dumps(
            source, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
        trace_signal_validation_event(
            "window.range_replay.parity_evidence_v1",
            schema_version=1,
            stock_code=self.stock.code,
            timeframe_key=replay_snapshot.timeframe_key,
            window_key=id(self),
            historical_request_id=replay_snapshot.historical_request_id,
            rules_hash=settings.rules_hash,
            rules_canonical_json=settings.canonical_json,
            source_calculation_candles={
                "count": len(source),
                "first_time": str(source[0]["time"]),
                "last_time": str(source[-1]["time"]),
                "sha256": hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
            },
            display_range={
                "start_index": start,
                "end_index": end,
                "start_time": range_times[0],
                "end_time": range_times[1],
            },
            calculation_range={
                "start_index": calc_start,
                "end_index": calc_end,
                "start_time": calc_times[0],
                "end_time": calc_times[1],
            },
            signals=signals,
            signal_counts={
                "BUY": sum(row["side"] == "BUY" for row in signals),
                "SELL": sum(row["side"] == "SELL" for row in signals),
            },
        )

    @property
    def validation_range(self) -> tuple[int, int] | None:
        return self._validation_range

    def _effective_validation_range(self) -> tuple[int, int] | None:
        if not self._candles or self._validation_range is None:
            return None
        start, end = self._validation_range
        if 0 <= start <= end < len(self._candles):
            return (start, end)
        return None

    def calculation_bounds_for_display_range(
        self,
        start: int,
        end: int,
    ) -> tuple[int, int] | None:
        return _calculation_bounds_for_display_range(
            self._calculation_candles,
            self._candles,
            start,
            end,
        )

    def apply_no_calculation_range_selection(
        self,
        *,
        defer_visualization: bool = False,
    ) -> None:
        """Keep a display-only selection without creating replay evidence."""
        self._clear_range_replay_override()
        self._invalidate_visualization_projection_data()
        if not defer_visualization:
            self._rebuild_visualization_data()
        self._ensure_signal_tooltips_current()
        markers = self._chart_marker_records()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_visualization_projection(
                self._visualization_descriptors,
                self._visualization_cache,
                self._visualization_active_by_marker,
                markers,
                self._visualization_lower_filter_presentations,
            )
            if self._validation_range is not None:
                canvas.set_validation_range(*self._validation_range)
        self._refresh_validation_range_results()
        self.validation_status_label.setText("")
        self.fixed_price_axis.refresh_scale()
        if defer_visualization:
            self._refresh_filter_display_projection()

    def _clear_range_replay_override(self) -> None:
        self._range_replay_calculation_entries = []
        self._range_replay_entries = []
        self._range_replay_settings_snapshot = None
        self._range_replay_entries_available = False
        self._range_replay_revalidation_pending = False


    def _restore_normal_base_after_range_clear(self) -> None:
        # Only a local range over an uncalculated normal chart can revert to normal mode.
        if (not self._range_replay_from_normal_base
                or self._validation_range is not None
                or not self._historical_pool_installed
                or not self._calculation_candles
                or not self._candles
                or self._signal_marker_entries_available
                or self._entries
                or self._calculation_entries):
            return
        self._range_replay_from_normal_base = False
        self._normal_base_requested = True
        self._normal_base_ready = True
        self._replay_snapshot = None
        self._signal_tooltips = {}
        self._signal_tooltip_provenance = None
        self._populate_completed_cycles()
        self.validation_status_label.setText("\uce94\ub4e4\u00b7\uc124\uc815 \uc9c0\ud45c \uc900\ube44 \uc644\ub8cc \u00b7 \uc2e0\ud638 \ubbf8\uacc4\uc0b0 (\uc720\ud6a8 \uc5ec\ubd80 \ubbf8\uacc4\uc0b0)")
        self.result_summary_label.setText(
            f"{self.stock.code} {self.stock.name} | "
            f"{timeframe_display_label(self._normal_base_timeframe_key)}\ubd09"
        )
        self.estimated_return_label.setText("")
        self.result_summary_label.setToolTip(self.validation_status_label.text())
        self._set_primary_validation_action_state("validate")
        self._refresh_filter_display_projection()

    def _set_validation_range(
        self,
        start_index: int | None,
        end_index: int | None,
        *,
        defer_projection: bool = False,
    ) -> None:
        previous_range = self._validation_range
        if start_index is None or end_index is None:
            self._validation_range = None
            self._validation_range_times = None
        else:
            start = min(int(start_index), int(end_index))
            end = max(int(start_index), int(end_index))
            if not 0 <= start <= end < len(self._candles):
                return
            if self._marking_times is not None and not (
                self._marking_times[0] <= str(self._candles[start]["time"])
                <= str(self._candles[end]["time"]) <= self._marking_times[1]
            ):
                self._disable_marking(clear_cache=True,
                                      reason="selection_outside_marking_range")
            self._validation_range = (start, end)
            self._validation_range_times = (
                str(self._candles[start].get("time") or ""),
                str(self._candles[end].get("time") or ""),
            )
        range_changed = self._validation_range != previous_range
        trace_signal_validation_event(
            "window.range.set",
            window_key=id(self),
            stock_code=self.stock.code,
            previous_range=previous_range,
            range=self._validation_range,
            range_times=self._validation_range_times,
            range_changed=range_changed,
            display=trace_candle_signature(self._candles),
            calculation=trace_candle_signature(self._calculation_candles),
            chart_pool_start_index=self._chart_pool_start_index,
            chart_index_count=len(self._chart_calculation_indexes),
        )
        if self._validation_range is None or range_changed:
            self._clear_range_replay_override()
        if range_changed:
            self._invalidate_visualization_projection_data()
            self._refresh_valid_filter_emphasis()
            self.validation_range_changed.emit(self._validation_range)
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            if self._validation_range is None:
                canvas.set_validation_range(None, None)
            else:
                canvas.set_validation_range(*self._validation_range)
            if range_changed:
                # Do not leave old BUY/SELL glyphs visible while the async
                # indicator projection for the new selection is pending.
                canvas.set_marker_records(self._chart_marker_records())
        if (
            range_changed
            and isinstance(
                self._result_settings_snapshot,
                ValidationSettingsSnapshot,
            )
            and self._calculation_candles
        ):
            if defer_projection:
                QTimer.singleShot(1, self._refresh_filter_display_projection)
            else:
                self._refresh_filter_display_projection()

    def _begin_validation_range_drag(self, index: int) -> None:
        """Double-click press: start a new rightward aggregation-range drag."""
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._validation_range_anchor_index = index
        self._validation_range_anchor_time = str(
            self._candles[index].get("time") or ""
        )
        self._set_validation_range(None, None)
        self._selected_index = index
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_selected_index(index)
        self._refresh_validation_range_results()

    def _complete_validation_range_drag(
        self,
        start_index: int,
        end_index: int,
    ) -> None:
        """Mouse release: commit a valid rightward drag as the range."""
        if not self._flush_pending_signal_validation_ui_confirmation():
            return
        if (
            isinstance(start_index, bool)
            or not isinstance(start_index, int)
            or isinstance(end_index, bool)
            or not isinstance(end_index, int)
            or not 0 <= start_index < end_index < len(self._candles)
        ):
            self._clear_validation_range_interaction()
            return

        self._validation_range_anchor_index = start_index
        self._validation_range_anchor_time = str(
            self._candles[start_index].get("time") or ""
        )
        self._set_validation_range(
            start_index,
            end_index,
            defer_projection=True,
        )
        self._selected_index = start_index
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_selected_index(start_index)
        self.validation_range_evaluation_requested.emit(
            start_index,
            end_index,
        )
        self._refresh_validation_range_results()

    def _clear_validation_range_interaction(self) -> None:
        """Clear the committed or preview validation range."""
        self._validation_range_anchor_index = None
        self._validation_range_anchor_time = None
        self._set_validation_range(None, None)
        self._selected_index = None
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_selected_index(None)
            canvas.clear_evidence_pin()
            canvas.clear_marker_tooltip()
        self._refresh_validation_range_results()
        self._restore_normal_base_after_range_clear()

    def _restore_validation_range_from_times(self) -> None:
        by_time = {
            str(candle.get("time") or ""): index
            for index, candle in enumerate(self._candles)
        }
        if self._validation_range_times is None:
            self._validation_range = None
        else:
            start_time, end_time = self._validation_range_times
            if start_time not in by_time or end_time not in by_time:
                self._validation_range = None
                self._validation_range_times = None
            else:
                start = min(by_time[start_time], by_time[end_time])
                end = max(by_time[start_time], by_time[end_time])
                self._validation_range = (start, end)
                if not self._validation_range_anchor_time:
                    self._validation_range_anchor_index = start
                    self._validation_range_anchor_time = str(start_time)

        if self._validation_range_anchor_time:
            restored_anchor = by_time.get(self._validation_range_anchor_time)
            if restored_anchor is None:
                self._validation_range_anchor_index = None
                self._validation_range_anchor_time = None
            else:
                self._validation_range_anchor_index = restored_anchor
        else:
            self._validation_range_anchor_index = None

    @staticmethod
    def _range_entry(
        entry: ValidationReplayEntry,
        offset: int,
    ) -> ValidationReplayEntry:
        payload = entry.to_dict()
        payload["evaluation_index"] = entry.evaluation_index - offset
        if entry.signal_index is not None:
            payload["signal_index"] = entry.signal_index - offset
        return ValidationReplayEntry(**payload)

    def _validation_range_visualization_projection(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        list[ValidationReplayEntry],
        int,
    ]:
        bounds = self._effective_validation_range()
        if bounds is None:
            return [], [], 0
        start, end = bounds
        calculation_bounds = self.calculation_bounds_for_display_range(start, end)
        if calculation_bounds is None:
            return [], [], 0
        calculation_start, calculation_end = calculation_bounds
        candles = [
            dict(candle)
            for candle in self._calculation_candles[
                calculation_start : calculation_end + 1
            ]
        ]
        source_entries = (
            self._range_replay_calculation_entries
            if self._range_replay_entries_available
            else self._marker_entries_for_calculation()
        )
        entries = [
            self._range_entry(entry, calculation_start)
            for entry in source_entries
            if calculation_start <= entry.evaluation_index <= calculation_end
        ]
        return candles, entries, calculation_start

    def _validation_range_projection(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        list[ValidationReplayEntry],
        int,
    ]:
        bounds = self._effective_validation_range()
        if bounds is None:
            return [], [], 0
        start, end = bounds
        candles = [dict(candle) for candle in self._candles[start : end + 1]]
        entries = [
            self._range_entry(entry, start)
            for entry in self._marker_entries_for_display()
            if start <= entry.evaluation_index <= end
        ]
        return candles, entries, start

    @staticmethod
    def _offset_completed_cycle(
        cycle: IndicatorFollowValidationCompletedCycle,
        offset: int,
    ) -> IndicatorFollowValidationCompletedCycle:
        return IndicatorFollowValidationCompletedCycle(
            cycle_number=cycle.cycle_number,
            buy_indexes=tuple(index + offset for index in cycle.buy_indexes),
            buy_start_index=cycle.buy_start_index + offset,
            buy_end_index=cycle.buy_end_index + offset,
            buy_count=cycle.buy_count,
            average_buy_price=cycle.average_buy_price,
            sell_index=cycle.sell_index + offset,
            sell_price=cycle.sell_price,
            estimated_return_percent=cycle.estimated_return_percent,
            buy_quantity=cycle.buy_quantity,
            buy_cost=cycle.buy_cost,
            trading_cost_amount=cycle.trading_cost_amount,
        )

    def _replay_timeframe_display_text(self) -> str:
        snapshot = self._replay_snapshot
        if not isinstance(snapshot, ValidationReplaySnapshot):
            return "-"
        key = getattr(snapshot, "timeframe_key", f"M{snapshot.timeframe_minutes}")
        return f"{timeframe_display_label(key)}\ubd09"

    def _refresh_validation_range_results(self) -> None:
        if (
            (self._replay_snapshot is None and not self._range_replay_entries_available)
            or not self._candles
        ):
            return
        self._populate_completed_cycles()
        self._refresh_signal_tooltips_only()
        bounds = self._effective_validation_range()
        if bounds is None:
            self.result_summary_label.setText(
                f"{self.stock.code} {self.stock.name} | "
                f"{self._replay_timeframe_display_text()}"
            )
            self.estimated_return_label.setText("")
            return
        start, end = bounds
        selected_entries = [
            entry
            for entry in self._marker_entries_for_display()
            if start <= entry.evaluation_index <= end
        ]
        buy_count = sum(entry.signal == "BUY" for entry in selected_entries)
        sell_count = sum(entry.signal == "SELL" for entry in selected_entries)
        candle_count = end - start + 1
        self.result_summary_label.setText(
            f"{self.stock.code} {self.stock.name} | "
            f"{self._replay_timeframe_display_text()} | "
            f"{candle_count}캔들 | "
            f"매수신호 {buy_count} | 매도신호 {sell_count}"
        )
        estimated = aggregate_completed_cycle_return_percent(
            self._completed_cycles
        )
        (
            invested_amount,
            profit_amount,
            _average_price,
            _sell_price,
        ) = _completed_cycle_financial_summary(self._completed_cycles)
        average_invested = _average_completed_cycle_invested_amount(
            self._completed_cycles
        )
        summary_text = (
            f"| 손익 {_format_summary_return_percent(estimated)}/"
            f"{_format_summary_amount(profit_amount, signed=True)}"
            f" | 투입 전체 {_format_summary_amount(invested_amount)}/ "
            f"평균 {_format_summary_amount(average_invested)}"
        )
        self.estimated_return_label.setText(summary_text)

    def select_evaluation_index(self, index: int) -> bool:
        snapshot = self._replay_snapshot
        if (
            (snapshot is None and not self._normal_base_ready)
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return False
        self._deferred_wheel_zoom = None
        self._history_wheel_zoom_candidate = None
        self._selected_index = index
        self.canvas.set_selected_index(index)
        self._ensure_candle_visible(index)
        return True


    def _rebuild_visualization_data(self) -> None:
        snapshot = self._result_settings_snapshot
        if not isinstance(snapshot, ValidationSettingsSnapshot):
            self._visualization_descriptors = ()
            self._visualization_lower_filter_presentations = ()
            self._visualization_cache = None
            self._visualization_active_by_marker = {}
            self._visualization_data_error = ""
            return
        self._visualization_data_error = ""
        try:
            rules = snapshot.to_dict()
            descriptors = self._filter_display_descriptors(rules)
            calculation_candles = (
                self._calculation_candles
                if self._calculation_candles
                else self._candles
            )
            calculation_entries = self._marker_entries_for_calculation()
            range_projection = None
            bounds = self._effective_validation_range()
            if self._range_replay_entries_available and bounds is not None:
                range_candles, range_entries, range_start = (
                    self._validation_range_visualization_projection()
                )
                if range_candles:
                    range_projection = (
                        range_candles,
                        range_entries,
                        tuple(range(
                            range_start,
                            range_start + len(range_candles),
                        )),
                    )
            cache, active_by_marker = _build_validation_projection_data(
                calculation_candles,
                calculation_entries,
                self._marker_entries_for_display(),
                self._chart_calculation_indexes,
                rules,
                descriptors,
                range_projection=range_projection,
            )
            self._visualization_descriptors = descriptors
            self._refresh_lower_filter_presentations(
                rules,
                self._visualization_descriptors,
            )
            self._visualization_cache = self._clip_comparison_cache(descriptors, cache)
            self._visualization_active_by_marker = active_by_marker
            self._visualization_current_key = self._visualization_projection_key(
                descriptors
            )
        except Exception as exc:
            self._visualization_data_error = str(exc)

    @property
    def visualization_descriptors(self) -> tuple[ValidationFilterDescriptor, ...]:
        return tuple(self._visualization_descriptors)

    @property
    def visualization_cache(self) -> ValidationIndicatorSeriesCache | None:
        return self._visualization_cache

    def visualization_active_identities(
        self,
        evaluation_index: int,
        side: str,
    ) -> tuple[str, ...]:
        return tuple(self._visualization_active_by_marker.get(
            (evaluation_index, str(side or "").strip().upper()),
            (),
        ))

    def _validation_execution_tooltip_lines(
        self,
        rules: Mapping[str, Any],
        *, calculation_entries: list[ValidationReplayEntry] | None = None,
    ) -> dict[tuple[int, str], tuple[str, ...]]:
        policy = (
            rules.get("validation_execution")
            if isinstance(rules, Mapping)
            else None
        )

        def simulation_records(
            candles: list[dict[str, Any]],
            entries: list[ValidationReplayEntry],
            *,
            index_offset: int,
            chart_lookup: Mapping[int, int] | None = None,
        ) -> dict[tuple[int, str], tuple[str, ...]]:
            if not candles:
                return {}
            try:
                simulation = simulate_validation_execution(
                    candles,
                    entries,
                    policy,
                )
            except (TypeError, ValueError, OverflowError):
                return {}

            result: dict[tuple[int, str], tuple[str, ...]] = {}
            cycle_by_sell_index = {
                int(cycle.sell_index): cycle
                for cycle in simulation.cycles
            }
            cumulative_quantity = 0
            cumulative_amount = 0.0
            current_round = 0
            for fill in simulation.fills:
                side = str(fill.side or "").strip().upper()
                chart_index = (
                    chart_lookup.get(int(fill.evaluation_index), -1)
                    if chart_lookup is not None
                    else int(fill.evaluation_index) + index_offset
                )
                if side == "BUY":
                    current_round = (
                        int(fill.buy_round)
                        if isinstance(fill.buy_round, int)
                        and not isinstance(fill.buy_round, bool)
                        and fill.buy_round > 0
                        else current_round + 1
                    )
                    cumulative_quantity += int(fill.quantity)
                    cumulative_amount += float(fill.amount)
                    lines = (
                        f"▪{current_round}차 / {int(fill.quantity)}주 / "
                        f"{_format_summary_amount(float(fill.amount))}",
                        f"▪총 {cumulative_quantity}주 / "
                        f"{_format_summary_amount(cumulative_amount)}",
                    )
                elif side == "SELL":
                    cycle = cycle_by_sell_index.get(int(fill.evaluation_index))
                    profit_lines: tuple[str, ...] = ()
                    if cycle is not None:
                        profit_amount = _completed_cycle_profit_amount(cycle)
                        profit_percent = (
                            float(cycle.estimated_return_percent)
                            if math.isfinite(float(cycle.estimated_return_percent))
                            else 0.0
                        )
                        if profit_amount is not None:
                            profit_lines = (
                                f"▪수익 {profit_percent:.2f}% / "
                                f"{_format_summary_amount(profit_amount)}",
                            )
                    lines = (
                        f"▪{int(fill.quantity)}주 / 합계 "
                        f"{_format_summary_amount(float(fill.amount))}",
                        *profit_lines,
                    )
                    cumulative_quantity = 0
                    cumulative_amount = 0.0
                    current_round = 0
                else:
                    continue

                if 0 <= chart_index < len(self._candles):
                    result[(chart_index, side)] = lines
            return result

        calculation_candles = (
            self._calculation_candles
            if self._calculation_candles
            else self._candles
        )
        calculation_chart_lookup = {
            calculation_index: chart_index
            for chart_index, calculation_index in enumerate(
                self._chart_calculation_indexes
            )
            if calculation_index is not None
        }
        records = simulation_records(
            calculation_candles,
            (self._authoritative_calculation_marker_entries()
             if calculation_entries is None else calculation_entries),
            index_offset=0,
            chart_lookup=calculation_chart_lookup,
        )

        return records

    def _build_signal_tooltips(self) -> dict[tuple[int, str], str]:
        snapshot = (
            self._range_replay_settings_snapshot
            if self._range_replay_entries_available
            else self._result_settings_snapshot
        )
        if not isinstance(snapshot, ValidationSettingsSnapshot):
            return {}
        rules = snapshot.to_dict()
        tooltips: dict[tuple[int, str], str] = {}
        for entry in self._marker_entries_for_display():
            if entry.signal != entry.evaluation_side:
                continue
            try:
                tooltip = signal_evidence_tooltip(entry, rules)
            except (TypeError, ValueError, OverflowError):
                tooltip = ""
            marker_key = (entry.evaluation_index, entry.evaluation_side)
            if tooltip:
                tooltips[marker_key] = tooltip
        return self._append_signal_execution_tooltip_lines(tooltips, rules)

    def _append_signal_execution_tooltip_lines(
        self,
        tooltips: dict[tuple[int, str], str],
        rules: Mapping[str, Any],
    ) -> dict[tuple[int, str], str]:
        for key, lines in self._validation_execution_tooltip_lines(rules).items():
            tooltip = tooltips.get(key)
            if tooltip and lines:
                tooltips[key] = "\n".join((tooltip, *lines))
        return tooltips

    def _signal_tooltip_source_key(self) -> tuple[Any, ...]:
        # All source mutations replace lists or advance the data epoch. Never
        # compare entry content here: warm GUI apply must remain O(1).
        bounds = self._effective_validation_range()
        has_override = self._range_replay_entries_available and bounds is not None
        return (
            self._visualization_data_epoch,
            id(self._result_settings_snapshot),
            id(self._range_replay_settings_snapshot),
            id(self._marker_entries_for_calculation()),
            bounds,
            id(self._range_replay_entries) if has_override else None,
            id(self._range_replay_calculation_entries) if has_override else None,
        )

    def _ensure_signal_tooltips_current(self) -> bool:
        provenance = self._signal_tooltip_source_key()
        if self._signal_tooltip_provenance == provenance:
            return False
        self._signal_tooltips = self._build_signal_tooltips()
        self._signal_tooltip_provenance = provenance
        return True

    def _refresh_signal_tooltips_only(self) -> None:
        if self._marking_snapshot is not None:
            self._refresh_marking_projection()
            return
        canvas = getattr(self, "canvas", None)
        if canvas is None or self._replay_snapshot is None:
            return
        if self._ensure_signal_tooltips_current():
            canvas.set_marker_tooltips({
                (record["evaluation_index"], record["side"]): record["tooltip"]
                for record in self._chart_marker_records()
            })

    @staticmethod
    def _cycle_time(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) == 14 and text.isdigit():
            return f"{text[4:6]}/{text[6:8]} {text[8:10]}:{text[10:12]}"
        return text or "-"

    def _cycle_buy_range(
        self,
        cycle: IndicatorFollowValidationCompletedCycle,
    ) -> str:
        start_value = self._candles[cycle.buy_start_index].get("time")
        end_value = self._candles[cycle.buy_end_index].get("time")
        start_text = self._cycle_time(start_value)
        if cycle.buy_start_index == cycle.buy_end_index:
            return start_text
        start_time = _parse_candle_time(start_value)
        end_time = _parse_candle_time(end_value)
        if start_time is not None and end_time is not None:
            end_text = (
                end_time.strftime("%H:%M")
                if start_time.date() == end_time.date()
                else end_time.strftime("%m/%d %H:%M")
            )
        else:
            end_text = self._cycle_time(end_value)
        return f"{start_text}~{end_text}"

    @property
    def completed_cycles(self) -> tuple[IndicatorFollowValidationCompletedCycle, ...]:
        return tuple(self._completed_cycles)

    def _populate_completed_cycles(self) -> None:
        QToolTip.hideText()
        self._clear_completed_cycle_selection()
        range_settings = self._range_replay_settings_snapshot
        rules = (
            range_settings.to_dict()
            if (
                self._range_replay_entries_available
                and isinstance(range_settings, ValidationSettingsSnapshot)
            )
            else self._result_settings_snapshot.to_dict()
            if isinstance(self._result_settings_snapshot, ValidationSettingsSnapshot)
            else {}
        )
        execution_policy = (
            rules.get("validation_execution")
            if isinstance(rules, dict)
            else None
        )
        bounds = self._effective_validation_range()
        if bounds is None:
            self._completed_cycles = []
        else:
            start, end = bounds
            selected_candles = self._candles[start : end + 1]
            selected_entries = [
                entry
                for entry in self._marker_entries_for_display()
                if start <= entry.evaluation_index <= end
            ]
            local_entries = [
                entry.remap_indexes(
                    entry.evaluation_index - start,
                    (
                        None
                        if entry.signal_index is None
                        else entry.signal_index - start
                    ),
                )
                for entry in selected_entries
            ]
            local_cycles = (
                validation_execution_cycles(
                    selected_candles,
                    local_entries,
                    dict(execution_policy),
                )
                if isinstance(execution_policy, Mapping)
                else completed_validation_cycles(selected_candles, local_entries)
            )
            self._completed_cycles = [
                replace(
                    cycle,
                    cycle_number=index + 1,
                    buy_indexes=tuple(
                        buy_index + start for buy_index in cycle.buy_indexes
                    ),
                    buy_start_index=cycle.buy_start_index + start,
                    buy_end_index=cycle.buy_end_index + start,
                    sell_index=cycle.sell_index + start,
                )
                for index, cycle in enumerate(local_cycles)
            ]
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_completed_cycle_average_lines(
                tuple(self._completed_cycles)
                if self._effective_validation_range() is not None
                else ()
            )
        table = self.completed_cycle_table
        table.clearContents()
        table.setRowCount(len(self._completed_cycles))
        for row, cycle in enumerate(self._completed_cycles):
            invested_amount = _completed_cycle_invested_amount(cycle)
            profit_amount = _completed_cycle_profit_amount(cycle)
            values = (
                str(cycle.cycle_number),
                self._cycle_buy_range(cycle),
                str(cycle.buy_count),
                (
                    _format_summary_amount(invested_amount)
                    if invested_amount is not None
                    else "-"
                ),
                _validation_price_text(cycle.average_buy_price),
                self._cycle_time(self._candles[cycle.sell_index].get("time")),
                _validation_price_text(cycle.sell_price),
                (
                    _format_summary_amount(profit_amount, signed=True)
                    if profit_amount is not None
                    else "-"
                ),
                f"{cycle.estimated_return_percent:+.2f}%",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, cycle.sell_index)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, column, item)

        self.completed_cycle_empty_label.setVisible(not self._completed_cycles)
        self.completed_cycle_empty_label.raise_()
        self._sync_completed_cycle_table_geometry()
        QTimer.singleShot(0, self._fit_signal_validation_window)

    def _completed_cycle_row_height(self) -> int:
        table = self.completed_cycle_table
        return max(
            table.verticalHeader().defaultSectionSize(),
            QFontMetrics(table.font()).height() + 8,
        )

    def _sync_completed_cycle_table_geometry(self) -> None:
        table = self.completed_cycle_table
        table.ensurePolished()
        row_height = self._completed_cycle_row_height()
        table.verticalHeader().setDefaultSectionSize(row_height)
        header_height = max(
            table.horizontalHeader().height(),
            table.horizontalHeader().sizeHint().height(),
        )
        fixed_height = (
            header_height
            + row_height * self._CYCLE_TABLE_VISIBLE_ROWS
            + table.frameWidth() * 2
        )
        table.setFixedHeight(fixed_height)
        self._sync_completed_cycle_empty_label_geometry()
        self._resize_completed_cycle_columns()

    def _sync_completed_cycle_empty_label_geometry(self) -> None:
        label = getattr(self, "completed_cycle_empty_label", None)
        table = getattr(self, "completed_cycle_table", None)
        if label is not None and table is not None:
            label.setGeometry(table.viewport().rect())

    def _completed_cycle_column_minimum_widths(self) -> list[int]:
        table = self.completed_cycle_table
        metrics = QFontMetrics(table.horizontalHeader().font())
        return [
            metrics.horizontalAdvance(table.horizontalHeaderItem(column).text()) + 16
            for column in range(table.columnCount())
        ]

    def _resize_completed_cycle_columns(self) -> None:
        table = getattr(self, "completed_cycle_table", None)
        if table is None:
            return
        available_width = table.viewport().width()
        if available_width <= 0:
            return
        ratios = self._CYCLE_TABLE_COLUMN_RATIOS
        widths = [int(available_width * ratio) for ratio in ratios[:-1]]
        widths.append(available_width - sum(widths))
        minimums = self._completed_cycle_column_minimum_widths()
        for column, minimum in enumerate(minimums):
            deficit = max(0, minimum - widths[column])
            if not deficit:
                continue
            for donor in sorted(
                range(len(widths)),
                key=lambda index: widths[index] - minimums[index],
                reverse=True,
            ):
                if donor == column:
                    continue
                available = max(0, widths[donor] - minimums[donor])
                transfer = min(deficit, available)
                widths[donor] -= transfer
                widths[column] += transfer
                deficit -= transfer
                if not deficit:
                    break
        header = table.horizontalHeader()
        for column, width in enumerate(widths):
            header.resizeSection(column, width)

    def _clear_completed_cycle_selection(self) -> None:
        table = getattr(self, "completed_cycle_table", None)
        if table is not None:
            table.clearSelection()
            table.setCurrentCell(-1, -1)
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_selected_completed_cycle_number(None)

    def _completed_cycle_row_clicked(self, row: int, _column: int) -> None:
        item = self.completed_cycle_table.item(row, 0)
        if item is None or not 0 <= row < len(self._completed_cycles):
            return
        cycle = self._completed_cycles[row]
        self.completed_cycle_table.selectRow(row)
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_selected_completed_cycle_number(cycle.cycle_number)
        index = item.data(Qt.UserRole)
        if isinstance(index, int) and not isinstance(index, bool):
            self.select_evaluation_index(index)

    def _ensure_candle_visible(self, index: int) -> None:
        if self.canvas is None:
            return
        start = self._visible_start_index
        end = start + self._visible_candle_span
        if index < start:
            start = float(index)
        elif index + 1 > end:
            start = float(index) + 1.0 - self._visible_candle_span
        else:
            return
        self._set_time_view(
            start,
            self._visible_candle_span,
            auto_fit_price=True,
        )

    def hideEvent(self, event) -> None:
        self._chart_results_suspended = True
        self._invalidate_user_background_results()
        self._discard_chart_input()
        QToolTip.hideText()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.clear_marker_tooltip()
        super().hideEvent(event)
