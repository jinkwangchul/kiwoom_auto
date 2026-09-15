# -*- coding: utf-8 -*-
"""Independent candle/indicator signal-validation window."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
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
    QSpinBox,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRestorePayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
    build_signal_validation_snapshot,
    project_signal_validation_ui_state,
    require_resolved_sell_price_selections,
)
from indicator_follow_signal_validation_presentation import (
    signal_evidence_tooltip,
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
_TEXT = QColor("#d1d5db")
_UP = QColor("#ef4444")
_DOWN = QColor("#3b82f6")
_FLAT = QColor("#9ca3af")
_BUY = QColor("#22c55e")
_SELL = QColor("#f59e0b")
_SELECTION = QColor(250, 204, 21, 45)
_SELECTION_LINE = QColor("#fde047")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class _IndicatorFollowSignalValidationChartCanvasBase(QWidget):
    """V2-owned read-only candle projection and selection boundary."""

    bar_selected = pyqtSignal(int)
    time_scale_drag_requested = pyqtSignal(float, float, float)

    _LEFT = 48
    _RIGHT = 24
    _TOP = 42
    _BOTTOM = 48
    _MIN_WIDTH = 640
    _DRAG_THRESHOLD = 6
    _DRAG_SENSITIVITY_RATIO = 0.75

    def __init__(self, candles, markers, parent=None) -> None:
        super().__init__(parent)
        self._candles = deepcopy(candles)
        self._markers = deepcopy(markers)
        self._selected_index: int | None = None
        self._visible_start_index = 0.0
        self._visible_candle_span = float(max(1, len(self._candles)))
        self._drag_press_x: float | None = None
        self._drag_initial_span: float | None = None
        self._drag_anchor_index: float | None = None
        self._drag_anchor_ratio: float | None = None
        self._time_scale_drag_active = False

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

    def marker_count(self, side: str | None = None) -> int:
        normalized = str(side or "").strip().upper()
        if not normalized:
            return len(self._markers)
        return sum(marker.get("side") == normalized for marker in self._markers)

    def set_selected_index(self, index: int | None) -> None:
        if index is not None and (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return
        self._selected_index = index
        self.update()

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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            x = float(event.pos().x())
            self._drag_press_x = x
            self._drag_initial_span = self._visible_candle_span
            self._drag_anchor_index = self._index_float_for_x(x)
            self._drag_anchor_ratio = min(
                1.0,
                max(0.0, (x - self._LEFT) / self._plot_width()),
            )
            self._time_scale_drag_active = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_press_x is not None:
            was_drag = self._time_scale_drag_active
            self._clear_time_scale_drag()
            if not was_drag:
                index = self._nearest_candle_index(event.pos().x())
                if index is not None:
                    self.set_selected_index(index)
                    self.bar_selected.emit(index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clear_time_scale_drag(self) -> None:
        self._drag_press_x = None
        self._drag_initial_span = None
        self._drag_anchor_index = None
        self._drag_anchor_ratio = None
        self._time_scale_drag_active = False


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


def aggregate_completed_cycle_return_percent(
    cycles: list[IndicatorFollowValidationCompletedCycle]
    | tuple[IndicatorFollowValidationCompletedCycle, ...],
) -> float | None:
    """Aggregate completed Cycle returns using each Cycle's estimated cost."""
    if not cycles:
        return None
    total_cost = sum(
        cycle.average_buy_price * cycle.buy_count
        for cycle in cycles
    )
    if not math.isfinite(total_cost) or total_cost <= 0:
        return None
    total_profit = sum(
        cycle.average_buy_price
        * cycle.buy_count
        * cycle.estimated_return_percent
        / 100.0
        for cycle in cycles
    )
    aggregate = total_profit / total_cost * 100.0
    return aggregate if math.isfinite(aggregate) else None


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
        sell_price = _valid_close(candles[sell_index].get("close"))
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


def _parse_candle_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None


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


class IndicatorFollowSignalValidationChartCanvas(
    _IndicatorFollowSignalValidationChartCanvasBase
):
    """V2 scroll canvas containing Candle, marker, grid, and real time labels."""

    _LEFT = 12
    _CANDLE_HORIZONTAL_GAP = 2.0

    def __init__(self, candles, markers, parent=None) -> None:
        super().__init__(candles, markers, parent)
        self._active_tooltip = ""
        self._price_minimum: float | None = None
        self._price_maximum: float | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(self._MIN_WIDTH, 160)

    def _candle_body_width(self) -> float:
        slot_width = self.pixels_per_candle
        effective_gap = min(
            self._CANDLE_HORIZONTAL_GAP,
            max(0.0, slot_width - 1.0),
        )
        return max(1.0, slot_width - effective_gap)

    def time_axis_records(self) -> list[dict[str, Any]]:
        start, end = self._visible_index_bounds()
        records = _time_axis_label_records(self._candles[start:end])
        return [
            {**record, "index": record["index"] + start}
            for record in records
        ]

    @property
    def plot_left(self) -> int:
        return int(self._LEFT)

    @staticmethod
    def _price_tick_text(value: float) -> str:
        return _validation_price_text(value)

    def price_scale(self) -> _ValidationPriceScale | None:
        return _validation_price_scale(
            self._candles,
            self.height(),
            plot_top=self._TOP,
            plot_bottom_margin=self._BOTTOM,
            minimum=self._price_minimum,
            maximum=self._price_maximum,
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
        self._price_minimum = resolved_minimum
        self._price_maximum = resolved_maximum
        self.update()

    def grid_line_records(self) -> list[dict[str, Any]]:
        scale = self.price_scale()
        return [] if scale is None else scale.tick_records()

    def marker_tooltip_at(self, x: float, y: float) -> str:
        scale = self.price_scale()
        plot_top = self._TOP if scale is None else scale.plot_top
        plot_bottom = (
            max(plot_top + 1, self.height() - self._BOTTOM)
            if scale is None
            else scale.plot_bottom
        )
        for marker in reversed(self._markers):
            index = marker.get("evaluation_index")
            side = marker.get("side")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(self._candles):
                continue
            if not self._is_index_visible(index):
                continue
            marker_y = plot_bottom + 12 if side == "BUY" else plot_top - 12
            if abs(x - self._x_for_index(index)) <= 7 and abs(y - marker_y) <= 11:
                return str(marker.get("tooltip") or "")
        return ""

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
            f"평가 시각: {_display_time(candle.get('time'))}",
            f"시가: {value('open')}",
            f"고가: {value('high')}",
            f"저가: {value('low')}",
            f"종가: {value('close')}",
        ))

    def mouseMoveEvent(self, event) -> None:
        if self._drag_press_x is not None:
            drag_dx = float(event.pos().x()) - self._drag_press_x
            if (
                not self._time_scale_drag_active
                and abs(drag_dx) >= self._DRAG_THRESHOLD
            ):
                self._time_scale_drag_active = True
                self.clear_marker_tooltip()
            if self._time_scale_drag_active:
                sensitivity = max(
                    1.0,
                    self._plot_width() * self._DRAG_SENSITIVITY_RATIO,
                )
                requested_span = self._drag_initial_span * math.exp(
                    -drag_dx / sensitivity
                )
                self.time_scale_drag_requested.emit(
                    requested_span,
                    self._drag_anchor_index,
                    self._drag_anchor_ratio,
                )
                event.accept()
                return
        tooltip = self.marker_tooltip_at(event.pos().x(), event.pos().y())
        if not tooltip:
            tooltip = self.candle_tooltip_at(event.pos().x(), event.pos().y())
        if tooltip:
            self._active_tooltip = tooltip
            QToolTip.showText(event.globalPos(), tooltip, self)
        elif self._active_tooltip:
            self.clear_marker_tooltip()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.clear_marker_tooltip()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._clear_time_scale_drag()
        self.clear_marker_tooltip()
        super().hideEvent(event)

    def clear_marker_tooltip(self) -> None:
        self._active_tooltip = ""
        QToolTip.hideText()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), _BACKGROUND)
        if not self._candles:
            painter.setPen(_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter, "표시할 Candle이 없습니다.")
            return
        scale = self.price_scale()
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

        if (
            self._selected_index is not None
            and self._is_index_visible(self._selected_index)
        ):
            selected_x = self._x_for_index(self._selected_index)
            slot_width = self.pixels_per_candle
            painter.fillRect(
                QRectF(selected_x - slot_width / 2, 0, slot_width, self.height()),
                QBrush(_SELECTION),
            )
            painter.setPen(QPen(_SELECTION_LINE, 1))
            painter.drawLine(int(selected_x), 0, int(selected_x), self.height())

        visible_start, visible_end = self._visible_index_bounds()
        body_width = self._candle_body_width()
        half_body = body_width / 2
        for index in range(visible_start, visible_end):
            candle = self._candles[index]
            close = _finite_number(candle.get("close"))
            if close is None:
                continue
            x = self._x_for_index(index)
            opened = _finite_number(candle.get("open"))
            high = _finite_number(candle.get("high"))
            low = _finite_number(candle.get("low"))
            if opened is None or high is None or low is None:
                painter.setPen(QPen(_FLAT, 2))
                y = scale.y_for_price(close)
                painter.drawLine(
                    QPointF(x - half_body, y),
                    QPointF(x + half_body, y),
                )
                continue
            color = _UP if close > opened else _DOWN if close < opened else _FLAT
            painter.setPen(QPen(color, 1))
            painter.drawLine(
                int(x),
                int(scale.y_for_price(high)),
                int(x),
                int(scale.y_for_price(low)),
            )
            body_top = min(scale.y_for_price(opened), scale.y_for_price(close))
            body_height = max(
                abs(scale.y_for_price(opened) - scale.y_for_price(close)),
                1.0,
            )
            painter.fillRect(
                QRectF(x - half_body, body_top, body_width, body_height),
                QBrush(color),
            )

        for marker in self._markers:
            index = marker.get("evaluation_index")
            side = marker.get("side")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(self._candles):
                continue
            if not self._is_index_visible(index):
                continue
            x = self._x_for_index(index)
            if side == "BUY":
                y = scale.plot_bottom + 12
                points = [
                    QPointF(x, y - 8),
                    QPointF(x - 5, y + 2),
                    QPointF(x + 5, y + 2),
                ]
                color = _BUY
            elif side == "SELL":
                y = scale.plot_top - 12
                points = [
                    QPointF(x, y + 8),
                    QPointF(x - 5, y - 2),
                    QPointF(x + 5, y - 2),
                ]
                color = _SELL
            else:
                continue
            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(color))
            painter.drawPolygon(QPolygonF(points))

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


class IndicatorFollowSignalValidationFixedPriceAxis(QWidget):
    """V2-only price labels fixed outside the horizontal scroll viewport."""

    _HORIZONTAL_PADDING = 8

    def __init__(self, scroll_area: QScrollArea, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll_area = scroll_area
        self._canvas: IndicatorFollowSignalValidationChartCanvas | None = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._scroll_area.viewport().installEventFilter(self)
        self._sync_width()

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

    def _labels(self) -> list[str]:
        if self._canvas is None:
            return []
        return [
            _validation_price_text(record["price"])
            for record in self._canvas.grid_line_records()
        ]

    def _sync_width(self) -> None:
        labels = self._labels()
        metrics = QFontMetrics(self.font())
        widest = max((metrics.horizontalAdvance(label) for label in labels), default=0)
        self.setFixedWidth(max(1, widest + self._HORIZONTAL_PADDING * 2))

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
    """Single-line width-fitted projection of the V2 recent-stock MRU."""

    stock_activated = pyqtSignal(object)
    projection_fitted = pyqtSignal(object)

    _ITEM_HORIZONTAL_PADDING = 8
    _SEPARATOR_TEXT = " | "

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_stocks: tuple[ValidationStockRef, ...] = ()
        self._fitted_stocks: tuple[ValidationStockRef, ...] = ()
        self._available_width = 0
        self.stock_buttons: list[QPushButton] = []
        self._row_layout = QHBoxLayout(self)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(0)
        row_height = max(26, QFontMetrics(self.font()).height() + 8)
        self.setFixedHeight(row_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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
        self._source_stocks = tuple(normalized)
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
        metrics = QFontMetrics(self.font())
        separator_width = metrics.horizontalAdvance(self._SEPARATOR_TEXT)
        used_width = 0
        fitted: list[ValidationStockRef] = []
        for stock in self._source_stocks:
            text_width = metrics.horizontalAdvance(f"{stock.code} {stock.name}")
            required_width = text_width + self._ITEM_HORIZONTAL_PADDING * 2
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
        fitted = self.fitting_stocks_for_width(self._available_width)
        if fitted == self._fitted_stocks and len(self.stock_buttons) == len(fitted):
            return
        changed = fitted != self._fitted_stocks
        self._fitted_stocks = fitted
        self._clear_row()
        metrics = QFontMetrics(self.font())
        for index, stock in enumerate(fitted):
            if index:
                separator = QLabel(self._SEPARATOR_TEXT)
                separator.setFixedWidth(metrics.horizontalAdvance(self._SEPARATOR_TEXT))
                separator.setAlignment(Qt.AlignCenter)
                self._row_layout.addWidget(separator)
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
            self._row_layout.addWidget(button)
        self._row_layout.addStretch(1)
        if changed:
            self.projection_fitted.emit(tuple(fitted))

    def _clear_row(self) -> None:
        self.stock_buttons = []
        while self._row_layout.count():
            item = self._row_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class IndicatorFollowSignalValidationWindow(
    IndicatorFollowRoutineSettingsDialog
):
    """Top-level working copy for repeated signal-only historical replay."""

    _V2_SECTION_HEADER_HEIGHT = 44
    _V2_SECTION_VERTICAL_MARGIN = 6
    _REFERENCE_CANDLE_COUNT = 100.0
    _MIN_VISIBLE_CANDLE_SPAN = 10.0
    _TIME_SCROLL_UNITS_PER_CANDLE = 1000
    _PRICE_ZOOM_FACTOR = 1.15
    _MIN_PRICE_RANGE_RATIO = 0.001
    _MAX_PRICE_RANGE_RATIO = 100.0
    _INITIAL_AVAILABLE_GEOMETRY_RATIO = 0.85
    _CYCLE_TABLE_VISIBLE_ROWS = 5
    _CYCLE_TABLE_COLUMN_RATIOS = (0.06, 0.22, 0.09, 0.15, 0.20, 0.13, 0.15)

    validation_run_requested = pyqtSignal(object)
    settings_apply_requested = pyqtSignal(object)
    entry_reset_started = pyqtSignal()
    entry_reset_requested = pyqtSignal(object)
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)
    recent_stocks_fitted = pyqtSignal(object)

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
        self._signal_validation_stock = (
            None if stock is None else ValidationStockRef(stock.code, stock.name)
        )
        self._signal_validation_seed = IndicatorFollowSignalValidationSeed(
            seed.settings_snapshot,
            seed.to_ui_state(),
        )
        self._result_ui_state = seed.to_ui_state()
        self._replay_snapshot: ValidationReplaySnapshot | None = None
        self._candles: list[dict[str, Any]] = []
        self._entries: list[ValidationReplayEntry] = []
        self._completed_cycles: list[IndicatorFollowValidationCompletedCycle] = []
        self._selected_index: int | None = None
        self._initial_validation_requested = False
        self._initial_natural_fit_pending = True
        self._programmatic_resize_in_progress = False
        self._initial_geometry_committed = False
        self._user_geometry_owned = False
        self._primary_validation_action_state = "validate"
        self._settings_apply_after_validation = False
        self._entry_state: _ValidationEntryState | None = None
        self._entry_chart_view_state: _ValidationChartViewState | None = None
        self._entry_reset_in_progress = False
        self._entry_reset_replay_pending = False
        self._entry_reset_source_ready: bool | None = None
        self._entry_reset_source_message = ""
        self._pending_validation_ui_fingerprint: str | None = None
        self._validated_ui_fingerprint: str | None = None
        self._pending_result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._signal_tooltips: dict[tuple[int, str], str] = {}
        self._visible_start_index = 0.0
        self._visible_candle_span = 1.0
        self._time_scale_manually_adjusted = False
        self._syncing_time_navigation_scrollbar = False
        self._default_price_minimum: float | None = None
        self._default_price_maximum: float | None = None
        self._current_price_minimum: float | None = None
        self._current_price_maximum: float | None = None
        self._price_scale_manually_adjusted = False
        super().__init__(
            rules_path=__file__,
            routine_name="지표추종매매 신호검증 V2",
            definition_id="indicator_follow_signal_validation_v2",
            settings_mode="registration",
            parent=parent,
        )
        self.setModal(False)
        self.setMinimumSize(0, 700)
        self.resize(max(1, self.sizeHint().width()), 900)
        self._connect_signal_ui_change_tracking()
        self._set_primary_validation_action_state("validate")

    @property
    def stock(self) -> ValidationStockRef | None:
        return self._signal_validation_stock

    @property
    def replay_snapshot(self) -> ValidationReplaySnapshot | None:
        return self._replay_snapshot

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
        self.setWindowTitle("지표추종매매 - 독립 신호검증 V2")

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
        self.result_summary_label = QLabel("Candle -  |  BUY -  |  SELL -")
        self.estimated_return_label = QLabel("|  추정 손익률 -")
        self.estimated_return_label.setStyleSheet("font-weight: bold;")
        self.reset_button = QPushButton("초기화")
        self.primary_validation_action_button = QPushButton("설정적용")
        self.run_validation_button = self.primary_validation_action_button
        self.close_button = QPushButton("닫기")
        self.reset_button.clicked.connect(self.reset_to_entry_state)
        self.primary_validation_action_button.clicked.connect(
            self._handle_primary_validation_action
        )
        self.close_button.clicked.connect(self.close)
        action_row.addWidget(self.validation_status_label)
        action_row.addWidget(self.result_summary_label)
        action_row.addWidget(self.estimated_return_label)
        action_row.addStretch(1)
        action_row.addWidget(self.reset_button)
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
        self.loading_label.setStyleSheet("font-size: 12pt; color: #555555;")
        self.chart_scroll_area = QScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_scroll_area.viewport().installEventFilter(self)
        self.time_navigation_scrollbar = QScrollBar(Qt.Horizontal)
        self.time_navigation_scrollbar.setVisible(False)
        self.time_navigation_scrollbar.valueChanged.connect(
            self._time_navigation_scrollbar_changed
        )
        self.fixed_price_axis = IndicatorFollowSignalValidationFixedPriceAxis(
            self.chart_scroll_area
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
        self.chart_stack.setMinimumHeight(280)
        result_layout.addWidget(self.chart_stack, 1)

        self.completed_cycle_table = QTableWidget(0, 7)
        self.completed_cycle_table.setObjectName("signalValidationCompletedCycleTable")
        self.completed_cycle_table.setHorizontalHeaderLabels([
            "회차",
            "매수구간",
            "매수횟수",
            "추정평단",
            "매도시각",
            "매도가",
            "추정수익률",
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
        self.recent_stock_row.projection_fitted.connect(
            self.recent_stocks_fitted.emit
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
        self.basic_signal_interval_combo = QComboBox()
        self.basic_signal_interval_combo.addItems(
            ["1", "3", "5", "10", "15", "30", "60", "120", "240"]
        )
        self.basic_signal_interval_combo.setCurrentText("5")
        self.basic_signal_interval_combo.setFixedWidth(60)
        self.basic_signal_interval_combo.setFixedHeight(30)
        self.basic_signal_interval_combo.setLayoutDirection(Qt.RightToLeft)
        self.historical_candle_count_spin = QSpinBox()
        self.historical_candle_count_spin.setRange(1, 2_147_483_647)
        self.historical_candle_count_spin.setValue(100)
        self.historical_candle_count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.historical_candle_count_spin.setFixedWidth(80)
        self.historical_candle_count_spin.setFixedHeight(30)
        header_row.addWidget(self.basic_toggle_button)
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(self.compact_stock_display)
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(QLabel("기준봉"))
        header_row.addWidget(self.basic_signal_interval_combo)
        header_row.addWidget(QLabel("분봉"))
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(QLabel("봉수"))
        header_row.addWidget(self.historical_candle_count_spin)
        header_row.addWidget(QLabel("봉"))
        header_row.addStretch(1)
        basic_layout.addWidget(header_widget)
        basic_layout.addWidget(self.recent_stock_panel)
        self.basic_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        basic_layout.activate()
        self.basic_box.setFixedHeight(basic_layout.sizeHint().height())
        page_layout.addWidget(self.basic_box)

        buy_title = self._build_control_buy_section(page_layout)
        sell_title = self._build_control_sell_section(page_layout)
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
            elif event.type() == QEvent.Wheel and event.angleDelta().y():
                self._zoom_price_scale_at(
                    event.pos().y(),
                    event.angleDelta().y(),
                )
                event.accept()
                return True
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
        return min(float(candle_count), max(minimum_span, float(requested_span)))

    def _maximum_visible_start(self, span: float | None = None) -> float:
        effective_span = (
            self._visible_candle_span if span is None else float(span)
        )
        return max(0.0, len(self._candles) - effective_span)

    def _set_time_view(
        self,
        start_index: float,
        candle_span: float,
        *,
        manually_adjusted: bool | None = None,
    ) -> None:
        span = self._clamped_visible_candle_span(candle_span)
        start = min(
            self._maximum_visible_start(span),
            max(0.0, float(start_index)),
        )
        self._visible_start_index = start
        self._visible_candle_span = span
        if manually_adjusted is not None:
            self._time_scale_manually_adjusted = bool(manually_adjusted)
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.set_time_view(start, span)
        self._sync_time_navigation_scrollbar()

    def _apply_time_scale_drag(
        self,
        requested_span: float,
        anchor_index: float,
        anchor_ratio: float,
    ) -> None:
        span = self._clamped_visible_candle_span(requested_span)
        start = float(anchor_index) + 0.5 - float(anchor_ratio) * span
        self._set_time_view(start, span, manually_adjusted=True)

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
            scrollbar.setVisible(maximum > 0)
            scrollbar.setEnabled(maximum > 0)
        finally:
            self._syncing_time_navigation_scrollbar = False

    def _time_navigation_scrollbar_changed(self, value: int) -> None:
        if self._syncing_time_navigation_scrollbar:
            return
        start = float(value) / self._TIME_SCROLL_UNITS_PER_CANDLE
        self._set_time_view(start, self._visible_candle_span)

    def _zoom_price_scale_at(self, cursor_y: float, wheel_delta: int) -> None:
        canvas = getattr(self, "canvas", None)
        scale = None if canvas is None else canvas.price_scale()
        if scale is None or not wheel_delta:
            return
        if self._default_price_minimum is None or self._default_price_maximum is None:
            return
        default_range = self._default_price_maximum - self._default_price_minimum
        if default_range <= 0:
            return
        current_range = scale.maximum - scale.minimum
        wheel_steps = abs(float(wheel_delta)) / 120.0
        factor = self._PRICE_ZOOM_FACTOR ** wheel_steps
        requested_range = (
            current_range / factor if wheel_delta > 0 else current_range * factor
        )
        minimum_range = max(default_range * self._MIN_PRICE_RANGE_RATIO, 1e-9)
        maximum_range = default_range * self._MAX_PRICE_RANGE_RATIO
        new_range = min(maximum_range, max(minimum_range, requested_range))
        bounded_y = min(scale.plot_bottom, max(scale.plot_top, float(cursor_y)))
        anchor_price = scale.price_for_y(bounded_y)
        cursor_ratio = (
            (bounded_y - scale.plot_top) / (scale.plot_bottom - scale.plot_top)
        )
        maximum = anchor_price + cursor_ratio * new_range
        minimum = maximum - new_range
        if maximum < self._default_price_minimum:
            maximum = self._default_price_minimum
            minimum = maximum - new_range
        elif minimum > self._default_price_maximum:
            minimum = self._default_price_maximum
            maximum = minimum + new_range
        self._current_price_minimum = minimum
        self._current_price_maximum = maximum
        self._price_scale_manually_adjusted = True
        canvas.set_price_view(minimum, maximum)
        self.fixed_price_axis.refresh_scale()

    def load_rules(self) -> None:
        self.rules_data = self._signal_validation_seed.settings_snapshot.to_dict()
        self.rules = deepcopy(self.rules_data)
        self.apply_signal_validation_ui_state(
            self._signal_validation_seed.to_ui_state()
        )
        self.compact_stock_display.set_current_stock(self.stock)

    def _show_with_initial_control_section_state(self) -> None:
        self.showNormal()
        self._apply_control_section_mode("summary", force=True)

    def _defer_fit_dialog_height_to_control_mode(self, mode=None) -> None:
        QTimer.singleShot(0, self._fit_signal_validation_window)

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

    def set_historical_candle_count(self, candle_count: int) -> None:
        if (
            isinstance(candle_count, bool)
            or not isinstance(candle_count, int)
            or candle_count <= 0
        ):
            raise ValueError("candle_count must be a positive integer")
        self.historical_candle_count_spin.setValue(candle_count)

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
                self.historical_candle_count_spin.value(),
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

    def _connect_signal_ui_change_tracking(self) -> None:
        for line_edit in self.findChildren(QLineEdit):
            if self.historical_candle_count_spin.isAncestorOf(line_edit):
                continue
            line_edit.textChanged.connect(self._on_signal_validation_ui_changed)
        for combo in self.findChildren(QComboBox):
            if combo is self.basic_signal_interval_combo:
                continue
            combo.currentTextChanged.connect(self._on_signal_validation_ui_changed)
        for checkbox in self.findChildren(QCheckBox):
            checkbox.toggled.connect(self._on_signal_validation_ui_changed)
        self.basic_signal_interval_combo.currentTextChanged.connect(
            self._on_validation_context_changed
        )
        self.historical_candle_count_spin.editingFinished.connect(
            self._on_validation_context_changed
        )

    def _on_signal_validation_ui_changed(self, *_args) -> None:
        if self._entry_reset_in_progress:
            return
        self._settings_apply_after_validation = False
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._set_primary_validation_action_state("validate")

    def _on_validation_context_changed(self, *_args) -> None:
        if self._entry_reset_in_progress:
            return
        self._settings_apply_after_validation = False
        self._request_validation()

    def reset_to_entry_state(self):
        self._entry_reset_source_ready = None
        self._entry_reset_source_message = ""
        self.entry_reset_started.emit()
        if self._entry_reset_source_ready is False:
            self.show_validation_error(
                self._entry_reset_source_message or "원본 설정창을 초기화할 수 없습니다."
            )
            return None
        entry = self.commit_entry_state()
        entry_ui_state = entry.to_ui_state()
        try:
            self._entry_reset_in_progress = True
            result = self.restore_signal_validation_entry_ui_state(entry_ui_state)
            skipped = (
                result.get("skipped", [])
                if isinstance(result, dict)
                else ["invalid_result"]
            )
            fatal_skips = [
                item
                for item in skipped
                if not isinstance(item, dict)
                or item.get("reason") != "missing_widget"
            ]
            if fatal_skips:
                raise ValueError("entry UI state could not be restored")
            self.historical_candle_count_spin.setValue(entry.candle_count)
            self._signal_validation_stock = (
                None
                if entry.stock is None
                else ValidationStockRef(entry.stock.code, entry.stock.name)
            )
            self.compact_stock_display.set_current_stock(self.stock)
        except Exception:
            self._entry_reset_replay_pending = False
            self.show_validation_error("V2 진입 상태를 복원할 수 없습니다.")
            return None
        finally:
            self._entry_reset_in_progress = False

        try:
            payload = IndicatorFollowSignalValidationRestorePayload(entry_ui_state)
        except (TypeError, ValueError):
            self._entry_reset_replay_pending = False
            self.show_validation_error("V2 진입 설정을 부모창에 전달할 수 없습니다.")
            return None
        self.entry_reset_requested.emit(payload)
        if self._entry_reset_source_ready is False:
            self._entry_reset_replay_pending = False
            self.show_validation_error(
                self._entry_reset_source_message or "원본 설정창을 초기화할 수 없습니다."
            )
            return payload
        self._clear_validation_result("초기화 검증 준비 중")
        self._entry_reset_replay_pending = True
        if self.stock is None:
            self._entry_reset_replay_pending = False
            self.show_validation_error(
                "상단 종목 영역을 두 번 클릭하여 검증 종목을 선택하세요."
            )
            return payload
        return self._request_entry_validation(entry_ui_state, entry.candle_count)

    def set_entry_reset_source_result(self, success: bool, message: str = "") -> None:
        self._entry_reset_source_ready = bool(success)
        self._entry_reset_source_message = str(message or "")

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
        self.primary_validation_action_button.setEnabled(bool(enabled))

    def _handle_primary_validation_action(self):
        if self._primary_validation_action_state == "apply":
            return self._request_settings_apply()
        if self._primary_validation_action_state == "validate":
            self._settings_apply_after_validation = True
            request = self._request_validation()
            if request is None:
                self._settings_apply_after_validation = False
            return request
        return None

    def _request_validation(self) -> IndicatorFollowSignalValidationRunRequest | None:
        if self.stock is None:
            self.show_validation_error(
                "상단 종목 영역을 두 번 클릭하여 검증 종목을 선택하세요."
            )
            return None
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            require_resolved_sell_price_selections(ui_state)
            mapper = self._load_indicator_follow_rule_mapper()
            preview = mapper.build_engine_rules_preview_from_ui_state(
                ui_state,
                self._signal_validation_seed.settings_snapshot.to_dict(),
            )
            sell_warnings = [
                str(item)
                for item in preview.get("validation_warnings", [])
                if str(item).lower().startswith(("sell condition", "sell signal"))
            ]
            if sell_warnings:
                raise ValueError("; ".join(sell_warnings))
            preview_rules = preview.get("preview_rules")
            if not isinstance(preview_rules, dict):
                raise ValueError("signal validation preview rules are unavailable")
            snapshot = build_signal_validation_snapshot(
                preview_rules,
                ui_state=ui_state,
            )
            run_request = IndicatorFollowSignalValidationRunRequest(
                snapshot,
                self.historical_candle_count_spin.value(),
            )
            pending_fingerprint = self._signal_ui_fingerprint(ui_state)
        except ValueError as exc:
            if "가격 기준 재선택 필요" in str(exc):
                self.show_validation_error(
                    "매도 가격비교의 가격 기준을 현재가 또는 평단가로 다시 선택하세요."
                )
                return None
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        except Exception:
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self._result_ui_state = deepcopy(ui_state)
        self._pending_result_settings_snapshot = snapshot
        self._pending_validation_ui_fingerprint = pending_fingerprint
        self._validated_ui_fingerprint = None
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.loading_label.setText("과거 분봉 데이터 조회 중...")
        if self._replay_snapshot is None:
            self.chart_stack.setCurrentWidget(self.loading_label)
        self._set_primary_validation_action_state("validate", enabled=False)
        self.validation_run_requested.emit(run_request)
        return run_request

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
                candle_count,
            )
            pending_fingerprint = self._signal_ui_fingerprint(entry_ui_state)
        except (TypeError, ValueError):
            self.show_validation_error("V2 진입 설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self._result_ui_state = deepcopy(entry_ui_state)
        self._pending_result_settings_snapshot = run_request.settings_snapshot
        self._pending_validation_ui_fingerprint = pending_fingerprint
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.loading_label.setText("과거 분봉 데이터 조회 중...")
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
        return self._request_validation()

    def _request_settings_apply(self) -> IndicatorFollowSignalValidationApplyPayload | None:
        if (
            self._primary_validation_action_state != "apply"
            or self._validated_ui_fingerprint is None
        ):
            return None
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            current_fingerprint = self._signal_ui_fingerprint(ui_state)
            if current_fingerprint != self._validated_ui_fingerprint:
                self._on_signal_validation_ui_changed()
                return None
            payload = IndicatorFollowSignalValidationApplyPayload(ui_state)
        except ValueError as exc:
            self._on_signal_validation_ui_changed()
            if "가격 기준 재선택 필요" in str(exc):
                self.show_settings_apply_result(
                    "매도 가격비교의 가격 기준을 현재가 또는 평단가로 다시 선택하세요.",
                    success=False,
                )
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
        self.settings_apply_requested.emit(payload)
        return payload

    def show_settings_apply_result(self, message: str, *, success: bool) -> None:
        self.validation_status_label.setText(str(message or "설정 적용 실패"))
        if success:
            self._set_primary_validation_action_state("apply")
            return
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

    def show_validation_error(self, message: str) -> None:
        self.validation_status_label.setText(str(message or "검증 실패"))
        if self._replay_snapshot is None:
            self.loading_label.setText(str(message or "검증 실패"))
            self.chart_stack.setCurrentWidget(self.loading_label)
        self._pending_validation_ui_fingerprint = None
        self._pending_result_settings_snapshot = None
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self._entry_reset_replay_pending = False
        self._set_primary_validation_action_state("validate")

    def set_validation_stock(self, stock: ValidationStockRef) -> bool:
        if not isinstance(stock, ValidationStockRef) or not stock.code or not stock.name:
            raise TypeError("stock must be a populated ValidationStockRef")
        selected = ValidationStockRef(stock.code, stock.name)
        if selected == self.stock:
            return False
        self._signal_validation_stock = selected
        self.compact_stock_display.set_current_stock(selected)
        self._clear_validation_result("새 종목 검증 준비 중")
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
        QToolTip.hideText()
        self._replay_snapshot = None
        self._candles = []
        self._entries = []
        self._completed_cycles = []
        self._selected_index = None
        self._visible_start_index = 0.0
        self._visible_candle_span = 1.0
        self._time_scale_manually_adjusted = False
        self._default_price_minimum = None
        self._default_price_maximum = None
        self._current_price_minimum = None
        self._current_price_maximum = None
        self._price_scale_manually_adjusted = False
        self._signal_tooltips = {}
        self._pending_result_settings_snapshot = None
        self._result_settings_snapshot = None
        old_canvas = self.chart_scroll_area.takeWidget()
        if old_canvas is not None:
            old_canvas.deleteLater()
        self.canvas = None
        self.fixed_price_axis.set_canvas(None)
        self._sync_time_navigation_scrollbar()
        self.result_summary_label.setText("Candle -  |  BUY -  |  SELL -")
        self.estimated_return_label.setText("|  추정 손익률 -")
        self._populate_completed_cycles()
        self.validation_status_label.setText(message)
        self.loading_label.setText(message)
        self.chart_stack.setCurrentWidget(self.loading_label)
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._settings_apply_after_validation = False
        self._set_primary_validation_action_state("validate")

    def _capture_entry_chart_view_state(self) -> None:
        if self._entry_chart_view_state is not None:
            return
        self._entry_chart_view_state = _ValidationChartViewState(
            visible_start_index=self._visible_start_index,
            visible_candle_span=self._visible_candle_span,
            default_price_minimum=self._default_price_minimum,
            default_price_maximum=self._default_price_maximum,
            current_price_minimum=self._current_price_minimum,
            current_price_maximum=self._current_price_maximum,
            time_scale_manually_adjusted=self._time_scale_manually_adjusted,
            price_scale_manually_adjusted=self._price_scale_manually_adjusted,
            selected_evaluation_index=self._selected_index,
        )

    def _restore_entry_chart_view_state(self) -> None:
        state = self._entry_chart_view_state
        canvas = self.canvas
        if state is None or canvas is None:
            return
        self._default_price_minimum = state.default_price_minimum
        self._default_price_maximum = state.default_price_maximum
        self._current_price_minimum = state.current_price_minimum
        self._current_price_maximum = state.current_price_maximum
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
        self.fixed_price_axis.refresh_scale()

    def set_replay_snapshot(self, replay_snapshot: ValidationReplaySnapshot) -> None:
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
        if replay_snapshot.stock != self.stock:
            raise ValueError("replay stock identity mismatch")
        self._replay_snapshot = replay_snapshot
        self._candles = replay_snapshot.to_candles()
        self._entries = replay_snapshot.to_entries()
        settings_snapshot = self._pending_result_settings_snapshot
        self._pending_result_settings_snapshot = None
        if (
            isinstance(settings_snapshot, ValidationSettingsSnapshot)
            and settings_snapshot.rules_hash == replay_snapshot.settings_hash
        ):
            self._result_settings_snapshot = settings_snapshot
        elif (
            self._signal_validation_seed.settings_snapshot.rules_hash
            == replay_snapshot.settings_hash
        ):
            self._result_settings_snapshot = self._signal_validation_seed.settings_snapshot
        else:
            self._result_settings_snapshot = None
        self._signal_tooltips = self._build_signal_tooltips()
        markers = _marker_records(
            self._candles,
            self._entries,
            self._signal_tooltips,
        )
        old_canvas = self.chart_scroll_area.takeWidget()
        if old_canvas is not None:
            old_canvas.deleteLater()
        candle_count = len(self._candles)
        self._visible_candle_span = min(
            self._REFERENCE_CANDLE_COUNT,
            float(max(1, candle_count)),
        )
        visible_end = min(
            max(0, replay_snapshot.evaluated_end_index),
            max(0, candle_count - 1),
        )
        self._visible_start_index = min(
            max(0.0, candle_count - self._visible_candle_span),
            max(0.0, visible_end - self._visible_candle_span + 1.0),
        )
        self._time_scale_manually_adjusted = False
        self.canvas = IndicatorFollowSignalValidationChartCanvas(self._candles, markers)
        initial_price_scale = self.canvas.price_scale()
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
        self.canvas.set_time_view(
            self._visible_start_index,
            self._visible_candle_span,
        )
        self.canvas.bar_selected.connect(self.select_evaluation_index)
        self.canvas.time_scale_drag_requested.connect(self._apply_time_scale_drag)
        self.chart_scroll_area.setWidget(self.canvas)
        self._sync_time_navigation_scrollbar()
        self.fixed_price_axis.set_canvas(self.canvas)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self._populate_completed_cycles()

        buy_count = sum(marker["side"] == "BUY" for marker in markers)
        sell_count = sum(marker["side"] == "SELL" for marker in markers)
        self.result_summary_label.setText(
            f"{self.stock.code} {self.stock.name}  |  "
            f"{replay_snapshot.timeframe_minutes}분봉  |  "
            f"Candle {len(self._candles)}  |  BUY {buy_count}  |  SELL {sell_count}"
        )
        estimated = aggregate_completed_cycle_return_percent(
            self._completed_cycles
        )
        self.estimated_return_label.setText(
            "|  추정 손익률 -"
            if estimated is None
            else f"|  추정 손익률 {estimated:+.2f}%"
        )
        self.validation_status_label.setText("검증 완료")
        pending_fingerprint = self._pending_validation_ui_fingerprint
        self._pending_validation_ui_fingerprint = None
        try:
            current_fingerprint = self._current_signal_ui_fingerprint()
        except Exception:
            current_fingerprint = None
        if (
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
        self.select_evaluation_index(replay_snapshot.evaluated_end_index)
        self._capture_entry_chart_view_state()
        if self._entry_reset_replay_pending:
            self._restore_entry_chart_view_state()
        self._entry_reset_replay_pending = False

    def select_evaluation_index(self, index: int) -> bool:
        snapshot = self._replay_snapshot
        if (
            snapshot is None
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return False
        self._selected_index = index
        self.canvas.set_selected_index(index)
        self._ensure_candle_visible(index)
        return True

    def _build_signal_tooltips(self) -> dict[tuple[int, str], str]:
        snapshot = self._result_settings_snapshot
        if not isinstance(snapshot, ValidationSettingsSnapshot):
            return {}
        rules = snapshot.to_dict()
        tooltips: dict[tuple[int, str], str] = {}
        for entry in self._entries:
            if entry.signal != entry.evaluation_side:
                continue
            try:
                tooltip = signal_evidence_tooltip(entry, rules)
            except (TypeError, ValueError, OverflowError):
                tooltip = ""
            if tooltip:
                tooltips[(entry.evaluation_index, entry.evaluation_side)] = tooltip
        return tooltips

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
        self._completed_cycles = completed_validation_cycles(
            self._candles,
            self._entries,
        )
        table = self.completed_cycle_table
        table.clearContents()
        table.setRowCount(len(self._completed_cycles))
        for row, cycle in enumerate(self._completed_cycles):
            values = (
                str(cycle.cycle_number),
                self._cycle_buy_range(cycle),
                str(cycle.buy_count),
                _validation_price_text(cycle.average_buy_price),
                self._cycle_time(self._candles[cycle.sell_index].get("time")),
                _validation_price_text(cycle.sell_price),
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

    def _completed_cycle_row_clicked(self, row: int, _column: int) -> None:
        item = self.completed_cycle_table.item(row, 0)
        if item is None:
            return
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
        self._set_time_view(start, self._visible_candle_span)

    def hideEvent(self, event) -> None:
        QToolTip.hideText()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.clear_marker_tooltip()
        super().hideEvent(event)
