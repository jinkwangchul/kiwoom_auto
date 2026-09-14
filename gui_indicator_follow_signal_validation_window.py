# -*- coding: utf-8 -*-
"""Independent candle/indicator signal-validation window."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any

from PyQt5.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QSplitter,
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
from gui_indicator_follow_validation_chart_window import (
    IndicatorFollowValidationChartCanvas,
    _TEXT,
    _finite_number,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
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


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    return text or "-"


def estimated_signal_return_percent(
    replay_snapshot: ValidationReplaySnapshot,
) -> float | None:
    """Estimate latest SELL return from earlier BUY evaluation-bar closes."""
    if not isinstance(replay_snapshot, ValidationReplaySnapshot):
        raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
    candles = replay_snapshot.to_candles()
    entries = replay_snapshot.to_entries()
    sell_entries = [
        entry for entry in entries
        if entry.signal == "SELL"
        and isinstance(entry.evaluation_index, int)
        and not isinstance(entry.evaluation_index, bool)
        and 0 <= entry.evaluation_index < len(candles)
    ]
    if not sell_entries:
        return None
    latest_sell = max(sell_entries, key=lambda entry: entry.evaluation_index)
    sell_close = _valid_close(candles[latest_sell.evaluation_index].get("close"))
    if sell_close is None:
        return None
    trace = latest_sell.trace
    evaluation_context = (
        trace.get("evaluation_context") if isinstance(trace, dict) else None
    )
    average_buy = _valid_close(
        evaluation_context.get("estimated_average_price")
        if isinstance(evaluation_context, dict)
        else None
    )
    if average_buy is None:
        context = build_validation_average_price_context(
            latest_sell.evaluation_index,
            "SELL",
            candles,
            entries,
        )
        average_buy = _valid_close(context.get("average_price"))
    if average_buy is None:
        return None
    value = (sell_close - average_buy) / average_buy * 100.0
    return value if math.isfinite(value) else None


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


class IndicatorFollowSignalValidationChartCanvas(
    IndicatorFollowValidationChartCanvas
):
    """V2 canvas overlaying sparse labels derived only from Candle timestamps."""

    _PRICE_AXIS_PADDING = 10

    def __init__(self, candles, markers, parent=None) -> None:
        super().__init__(candles, markers, parent)
        self._LEFT = self._required_price_axis_gutter()
        self.setMinimumWidth(self._content_width())
        self._time_axis_records = _time_axis_label_records(self._candles)
        self._active_marker_tooltip = ""
        self.setMouseTracking(True)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(self._content_width(), 160)

    def time_axis_records(self) -> list[dict[str, Any]]:
        return deepcopy(self._time_axis_records)

    @property
    def plot_left(self) -> int:
        return int(self._LEFT)

    @staticmethod
    def _price_tick_text(value: float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    def _price_range(self) -> tuple[float, float] | None:
        prices = []
        for candle in self._candles:
            for field in ("open", "high", "low", "close"):
                number = _finite_number(candle.get(field))
                if number is not None:
                    prices.append(number)
        if not prices:
            return None
        minimum = min(prices)
        maximum = max(prices)
        if maximum == minimum:
            padding = max(abs(maximum) * 0.01, 1.0)
            minimum -= padding
            maximum += padding
        return minimum, maximum

    def _price_tick_values(self) -> list[float]:
        price_range = self._price_range()
        if price_range is None:
            return []
        minimum, maximum = price_range
        return [
            maximum - (maximum - minimum) * step / 4
            for step in range(5)
        ]

    def _required_price_axis_gutter(self) -> int:
        labels = [self._price_tick_text(value) for value in self._price_tick_values()]
        if not labels:
            return type(self)._LEFT
        metrics = QFontMetrics(self.font())
        label_width = max(metrics.horizontalAdvance(label) for label in labels)
        return max(type(self)._LEFT, label_width + self._PRICE_AXIS_PADDING)

    def price_axis_records(self) -> list[dict[str, Any]]:
        plot_top = self._TOP
        plot_bottom = max(plot_top + 1, self.height() - self._BOTTOM)
        records = []
        for step, price in enumerate(self._price_tick_values()):
            y = int(plot_top + (plot_bottom - plot_top) * step / 4)
            records.append({
                "step": step,
                "price": price,
                "label": self._price_tick_text(price),
                "y": y,
            })
        return records

    def marker_tooltip_at(self, x: float, y: float) -> str:
        plot_top = self._TOP
        plot_bottom = max(plot_top + 1, self.height() - self._BOTTOM)
        for marker in reversed(self._markers):
            index = marker.get("evaluation_index")
            side = marker.get("side")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(self._candles):
                continue
            marker_y = plot_bottom + 12 if side == "BUY" else plot_top - 12
            if abs(x - self._x_for_index(index)) <= 7 and abs(y - marker_y) <= 11:
                return str(marker.get("tooltip") or "")
        return ""

    def mouseMoveEvent(self, event) -> None:
        tooltip = self.marker_tooltip_at(event.pos().x(), event.pos().y())
        if tooltip:
            self._active_marker_tooltip = tooltip
            QToolTip.showText(event.globalPos(), tooltip, self)
        elif self._active_marker_tooltip:
            self.clear_marker_tooltip()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.clear_marker_tooltip()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self.clear_marker_tooltip()
        super().hideEvent(event)

    def clear_marker_tooltip(self) -> None:
        self._active_marker_tooltip = ""
        QToolTip.hideText()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        price_records = self.price_axis_records()
        if not price_records and not self._time_axis_records:
            return
        painter = QPainter(self)
        painter.setPen(_TEXT)
        price_metrics = QFontMetrics(painter.font())
        label_right = self.plot_left - self._PRICE_AXIS_PADDING // 2
        for record in price_records:
            painter.drawText(
                QRectF(
                    0,
                    record["y"] - price_metrics.height() / 2,
                    label_right,
                    price_metrics.height(),
                ),
                Qt.AlignRight | Qt.AlignVCenter,
                record["label"],
            )
        if not self._time_axis_records:
            return
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        label_top = max(0, self.height() - 32)
        for record in self._time_axis_records:
            center_x = self._x_for_index(record["index"])
            painter.drawText(
                QRectF(center_x - 42, label_top, 84, 26),
                Qt.AlignHCenter | Qt.AlignTop,
                record["label"],
            )


class IndicatorFollowSignalEvidenceLabel(QLabel):
    """Hover-only signal evidence with an explicit row-activation boundary."""

    activated = pyqtSignal(int)

    def __init__(
        self,
        side: str,
        evaluation_index: int,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(side, parent)
        self.evaluation_index = evaluation_index
        self.setObjectName(f"signalEvidence{side.title()}")
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(str(tooltip or ""))
        color = "#16803a" if side == "BUY" else "#b45309"
        self.setStyleSheet(
            f"QLabel {{ color: {color}; font-weight: bold; padding: 1px 4px; }}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.evaluation_index)
            event.accept()
            return
        super().mousePressEvent(event)


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
    _SIGNAL_LIST_MIN_HEIGHT = 84
    _SIGNAL_LIST_MAX_HEIGHT = 180

    validation_run_requested = pyqtSignal(object)
    settings_apply_requested = pyqtSignal(object)
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
        self._selected_index: int | None = None
        self._initial_validation_requested = False
        self._initial_natural_fit_pending = True
        self._primary_validation_action_state = "validate"
        self._pending_validation_ui_fingerprint: str | None = None
        self._validated_ui_fingerprint: str | None = None
        self._pending_result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._result_settings_snapshot: ValidationSettingsSnapshot | None = None
        self._signal_tooltips: dict[tuple[int, str], str] = {}
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
        self.primary_validation_action_button = QPushButton("검증 실행")
        self.run_validation_button = self.primary_validation_action_button
        self.close_button = QPushButton("닫기")
        self.primary_validation_action_button.clicked.connect(
            self._handle_primary_validation_action
        )
        self.close_button.clicked.connect(self.close)
        action_row.addWidget(self.validation_status_label)
        action_row.addWidget(self.result_summary_label)
        action_row.addWidget(self.estimated_return_label)
        action_row.addStretch(1)
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
        self.chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chart_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_stack.addWidget(self.loading_label)
        self.chart_stack.addWidget(self.chart_scroll_area)
        self.chart_stack.setCurrentWidget(self.loading_label)

        self.selection_summary = self._detail_view()
        self.selection_summary.setObjectName("signalValidationCandleSummary")
        self.selection_summary.setPlainText("선택 Candle 요약\n-")
        self.result_splitter = QSplitter(Qt.Horizontal)
        self.result_splitter.addWidget(self.chart_stack)
        self.result_splitter.addWidget(self.selection_summary)
        self.result_splitter.setStretchFactor(0, 3)
        self.result_splitter.setStretchFactor(1, 1)
        self.result_splitter.setMinimumHeight(280)
        result_layout.addWidget(self.result_splitter, 1)

        self.signal_empty_label = QLabel("발생 신호 없음")
        self.signal_empty_label.setAlignment(Qt.AlignCenter)
        self.signal_empty_label.setFixedHeight(32)
        self.signal_list_table = QTableWidget(0, 3)
        self.signal_list_table.setObjectName("signalValidationSignalList")
        self.signal_list_table.setHorizontalHeaderLabels(["시각", "신호", "종가"])
        self.signal_list_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.signal_list_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.signal_list_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.signal_list_table.verticalHeader().setVisible(False)
        self.signal_list_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.signal_list_table.setWordWrap(False)
        self.signal_list_table.cellClicked.connect(self._signal_list_row_clicked)
        header = self.signal_list_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.signal_list_table.hide()
        result_layout.addWidget(self.signal_empty_label)
        result_layout.addWidget(self.signal_list_table)
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
        if (
            watched is getattr(self, "basic_toggle_button", None)
            and event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
        ):
            self._toggle_recent_stock_row()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _detail_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        return view

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
        initial_fit = bool(getattr(self, "_initial_natural_fit_pending", False))
        natural_width = self._natural_signal_validation_window_width(contents_hint)
        desired_width = natural_width if initial_fit else max(self.width(), natural_width)
        available = self._available_signal_validation_geometry()
        if available is not None:
            frame_extra_width = max(0, self.frameGeometry().width() - self.width())
            frame_extra_height = max(0, self.frameGeometry().height() - self.height())
            desired_width = min(desired_width, max(1, available.width() - frame_extra_width))
            desired_height = min(desired_height, max(1, available.height() - frame_extra_height))
        self.resize(int(desired_width), int(desired_height))
        self._initial_natural_fit_pending = False
        if initial_fit:
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
        signal_list_height = (
            self.signal_list_table.height()
            if not self.signal_list_table.isHidden()
            else self.signal_empty_label.height()
        )
        result_height = (
            self.result_splitter.minimumHeight()
            + signal_list_height
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
            line_edit.textChanged.connect(self._on_signal_validation_ui_changed)
        for combo in self.findChildren(QComboBox):
            combo.currentTextChanged.connect(self._on_signal_validation_ui_changed)
        for checkbox in self.findChildren(QCheckBox):
            checkbox.toggled.connect(self._on_signal_validation_ui_changed)

    def _on_signal_validation_ui_changed(self, *_args) -> None:
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._set_primary_validation_action_state("validate")

    def _set_primary_validation_action_state(
        self,
        state: str,
        *,
        enabled: bool | None = None,
    ) -> None:
        if state not in {"validate", "apply", "applied"}:
            raise ValueError("unknown primary validation action state")
        self._primary_validation_action_state = state
        self.primary_validation_action_button.setText({
            "validate": "검증 실행",
            "apply": "설정 반영",
            "applied": "반영 완료",
        }[state])
        if enabled is None:
            enabled = state != "applied"
        self.primary_validation_action_button.setEnabled(bool(enabled))

    def _handle_primary_validation_action(self):
        if self._primary_validation_action_state == "apply":
            return self._request_settings_apply()
        if self._primary_validation_action_state == "validate":
            return self._request_validation()
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
        self.validation_status_label.setText(str(message or "설정 반영 실패"))
        if success:
            self._set_primary_validation_action_state("applied")
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isVisible():
            QTimer.singleShot(0, self._sync_recent_stock_row_width)

    def _clear_validation_result(self, message: str) -> None:
        QToolTip.hideText()
        self._replay_snapshot = None
        self._candles = []
        self._entries = []
        self._selected_index = None
        self._signal_tooltips = {}
        self._pending_result_settings_snapshot = None
        self._result_settings_snapshot = None
        old_canvas = self.chart_scroll_area.takeWidget()
        if old_canvas is not None:
            old_canvas.deleteLater()
        self.canvas = None
        self.result_summary_label.setText("Candle -  |  BUY -  |  SELL -")
        self.estimated_return_label.setText("|  추정 손익률 -")
        self.selection_summary.setPlainText("선택 Candle 요약\n-")
        self._populate_signal_list([])
        self.validation_status_label.setText(message)
        self.loading_label.setText(message)
        self.chart_stack.setCurrentWidget(self.loading_label)
        self._pending_validation_ui_fingerprint = None
        self._validated_ui_fingerprint = None
        self._set_primary_validation_action_state("validate")

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
        self.canvas = IndicatorFollowSignalValidationChartCanvas(self._candles, markers)
        self.canvas.bar_selected.connect(self.select_evaluation_index)
        self.chart_scroll_area.setWidget(self.canvas)
        self.chart_stack.setCurrentWidget(self.chart_scroll_area)
        self._populate_signal_list(markers)

        buy_count = sum(marker["side"] == "BUY" for marker in markers)
        sell_count = sum(marker["side"] == "SELL" for marker in markers)
        self.result_summary_label.setText(
            f"{self.stock.code} {self.stock.name}  |  "
            f"{replay_snapshot.timeframe_minutes}분봉  |  "
            f"Candle {len(self._candles)}  |  BUY {buy_count}  |  SELL {sell_count}"
        )
        estimated = estimated_signal_return_percent(replay_snapshot)
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
        else:
            self._validated_ui_fingerprint = None
            self._set_primary_validation_action_state("validate")
        self.select_evaluation_index(replay_snapshot.evaluated_end_index)

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
        if not snapshot.evaluated_start_index <= index <= snapshot.evaluated_end_index:
            self.selection_summary.setPlainText(
                "선택 Candle 요약\nReplay 평가 범위 밖\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )
            return True
        buy_entry = self._entry_at(index, "BUY")
        sell_entry = self._entry_at(index, "SELL")
        self.selection_summary.setPlainText(
            self._selection_summary_text(index, buy_entry, sell_entry)
        )
        return True

    def _entry_at(self, index: int, side: str) -> ValidationReplayEntry | None:
        return next(
            (
                candidate for candidate in self._entries
                if candidate.evaluation_index == index
                and candidate.evaluation_side == side
            ),
            None,
        )

    @staticmethod
    def _entry_summary_lines(side: str, entry: ValidationReplayEntry | None) -> list[str]:
        if entry is None:
            return [
                f"{side} 신호: 미발생",
                f"{side} reason: -",
                f"{side} matched group: -",
                f"{side} signal_time: -",
                f"{side} delay_bar: -",
            ]
        return [
            f"{side} 신호: {'발생' if entry.signal == side else '미발생'}",
            f"{side} reason: {entry.reason or '-'}",
            f"{side} matched group: {', '.join(entry.matched_groups) or '-'}",
            f"{side} signal_time: {_display_time(entry.signal_time)}",
            f"{side} delay_bar: {entry.delay_bar}",
        ]

    def _selection_summary_text(
        self,
        index: int,
        buy_entry: ValidationReplayEntry | None,
        sell_entry: ValidationReplayEntry | None,
    ) -> str:
        candle = self._candles[index]
        lines = [
            "선택 Candle 요약",
            f"평가 시각: {_display_time(candle.get('time'))}",
            f"시가: {candle.get('open') if candle.get('open') is not None else '-'}",
            f"고가: {candle.get('high') if candle.get('high') is not None else '-'}",
            f"저가: {candle.get('low') if candle.get('low') is not None else '-'}",
            f"종가: {candle.get('close') if candle.get('close') is not None else '-'}",
            "",
        ]
        lines.extend(self._entry_summary_lines("BUY", buy_entry))
        lines.append("")
        lines.extend(self._entry_summary_lines("SELL", sell_entry))
        return "\n".join(lines)

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
    def _signal_list_time(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) == 14 and text.isdigit():
            return f"{text[4:6]}/{text[6:8]} {text[8:10]}:{text[10:12]}"
        return text or "-"

    def _populate_signal_list(self, markers: list[dict[str, Any]]) -> None:
        QToolTip.hideText()
        table = self.signal_list_table
        table.clearContents()
        by_index: dict[int, list[dict[str, Any]]] = {}
        for marker in markers:
            index = marker.get("evaluation_index")
            if isinstance(index, int) and not isinstance(index, bool):
                by_index.setdefault(index, []).append(marker)
        table.setRowCount(len(by_index))
        for row, index in enumerate(sorted(by_index)):
            candle = self._candles[index]
            time_item = QTableWidgetItem(
                self._signal_list_time(candle.get("time"))
            )
            close_value = _finite_number(candle.get("close"))
            close_item = QTableWidgetItem(
                "-" if close_value is None else self.canvas._price_tick_text(close_value)
            )
            for item in (time_item, close_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, index)
            table.setItem(row, 0, time_item)
            table.setItem(row, 2, close_item)

            signal_widget = QWidget()
            signal_layout = QHBoxLayout(signal_widget)
            signal_layout.setContentsMargins(2, 0, 2, 0)
            signal_layout.setSpacing(4)
            for marker in sorted(
                by_index[index],
                key=lambda value: 0 if value.get("side") == "BUY" else 1,
            ):
                side = str(marker.get("side") or "")
                label = IndicatorFollowSignalEvidenceLabel(
                    side,
                    index,
                    str(marker.get("tooltip") or ""),
                    signal_widget,
                )
                label.activated.connect(self._select_signal_list_index)
                signal_layout.addWidget(label)
            signal_layout.addStretch(1)
            table.setCellWidget(row, 1, signal_widget)

        has_signals = bool(by_index)
        self.signal_empty_label.setVisible(not has_signals)
        table.setVisible(has_signals)
        if has_signals:
            table.resizeRowsToContents()
            margins = table.contentsMargins()
            required_height = (
                table.horizontalHeader().height()
                + sum(table.rowHeight(row) for row in range(table.rowCount()))
                + table.frameWidth() * 2
                + margins.top()
                + margins.bottom()
                + 2
            )
            table.setFixedHeight(min(
                self._SIGNAL_LIST_MAX_HEIGHT,
                max(self._SIGNAL_LIST_MIN_HEIGHT, required_height),
            ))
        QTimer.singleShot(0, self._fit_signal_validation_window)

    def _signal_list_row_clicked(self, row: int, _column: int) -> None:
        item = self.signal_list_table.item(row, 0)
        if item is None:
            return
        index = item.data(Qt.UserRole)
        if isinstance(index, int) and not isinstance(index, bool):
            self._select_signal_list_index(index)

    def _select_signal_list_index(self, index: int) -> None:
        if self.select_evaluation_index(index):
            for row in range(self.signal_list_table.rowCount()):
                item = self.signal_list_table.item(row, 0)
                if item is not None and item.data(Qt.UserRole) == index:
                    self.signal_list_table.selectRow(row)
                    break

    def _ensure_candle_visible(self, index: int) -> None:
        if self.canvas is None:
            return
        self.chart_scroll_area.ensureVisible(
            int(self.canvas._x_for_index(index)),
            max(0, self.canvas.height() // 2),
            48,
            0,
        )

    def hideEvent(self, event) -> None:
        QToolTip.hideText()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.clear_marker_tooltip()
        super().hideEvent(event)
