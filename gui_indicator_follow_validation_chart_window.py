# -*- coding: utf-8 -*-
"""Read-only chart UI for an already-computed indicator-follow replay."""

from __future__ import annotations

import json
import math
from typing import Any

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
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


def _json_copy(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return (
            f"{text[0:4]}-{text[4:6]}-{text[6:8]} "
            f"{text[8:10]}:{text[10:12]}"
        )
    return text or "-"


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


class IndicatorFollowValidationChartCanvas(QWidget):
    """Validation-local, horizontally scrollable candlestick canvas."""

    bar_selected = pyqtSignal(int)

    _LEFT = 48
    _RIGHT = 24
    _TOP = 42
    _BOTTOM = 48
    _BAR_SLOT = 12
    _MIN_WIDTH = 640
    _MIN_HEIGHT = 440

    def __init__(
        self,
        candles: list[dict[str, Any]],
        markers: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._candles = _json_copy(candles)
        self._markers = _json_copy(markers)
        self._selected_index: int | None = None
        self.setMinimumWidth(self._content_width())
        self.setMinimumHeight(self._MIN_HEIGHT)

    @property
    def candle_count(self) -> int:
        return len(self._candles)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    def to_candles(self) -> list[dict[str, Any]]:
        return _json_copy(self._candles)

    def marker_records(self) -> list[dict[str, Any]]:
        return _json_copy(self._markers)

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

    def sizeHint(self) -> QSize:
        return QSize(self._content_width(), self._MIN_HEIGHT)

    def _content_width(self) -> int:
        return max(
            self._MIN_WIDTH,
            self._LEFT + self._RIGHT + len(self._candles) * self._BAR_SLOT,
        )

    def _x_for_index(self, index: int) -> float:
        return self._LEFT + (index + 0.5) * self._BAR_SLOT

    def _nearest_candle_index(self, x: float) -> int | None:
        if not self._candles:
            return None
        raw_index = round((x - self._LEFT - self._BAR_SLOT / 2) / self._BAR_SLOT)
        return min(max(raw_index, 0), len(self._candles) - 1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            index = self._nearest_candle_index(event.pos().x())
            if index is not None:
                self.set_selected_index(index)
                self.bar_selected.emit(index)
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), _BACKGROUND)
        if not self._candles:
            painter.setPen(_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter, "표시할 Candle이 없습니다.")
            return

        price_values = []
        for candle in self._candles:
            for field in ("open", "high", "low", "close"):
                number = _finite_number(candle.get(field))
                if number is not None:
                    price_values.append(number)
        if not price_values:
            painter.setPen(_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter, "표시할 가격이 없습니다.")
            return

        minimum = min(price_values)
        maximum = max(price_values)
        if maximum == minimum:
            padding = max(abs(maximum) * 0.01, 1.0)
            minimum -= padding
            maximum += padding
        plot_top = self._TOP
        plot_bottom = max(plot_top + 1, self.height() - self._BOTTOM)

        def price_y(price: float) -> float:
            ratio = (maximum - price) / (maximum - minimum)
            return plot_top + ratio * (plot_bottom - plot_top)

        painter.setPen(QPen(_GRID, 1, Qt.DashLine))
        for step in range(5):
            y = plot_top + (plot_bottom - plot_top) * step / 4
            painter.drawLine(self._LEFT, int(y), self.width() - self._RIGHT, int(y))

        if self._selected_index is not None:
            selected_x = self._x_for_index(self._selected_index)
            painter.fillRect(
                QRectF(selected_x - self._BAR_SLOT / 2, 0, self._BAR_SLOT, self.height()),
                QBrush(_SELECTION),
            )
            painter.setPen(QPen(_SELECTION_LINE, 1))
            painter.drawLine(int(selected_x), 0, int(selected_x), self.height())

        for index, candle in enumerate(self._candles):
            close = _finite_number(candle.get("close"))
            if close is None:
                continue
            x = self._x_for_index(index)
            opened = _finite_number(candle.get("open"))
            high = _finite_number(candle.get("high"))
            low = _finite_number(candle.get("low"))
            if opened is None or high is None or low is None:
                painter.setPen(QPen(_FLAT, 2))
                y = price_y(close)
                painter.drawLine(int(x - 3), int(y), int(x + 3), int(y))
                continue

            color = _UP if close > opened else _DOWN if close < opened else _FLAT
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(x), int(price_y(high)), int(x), int(price_y(low)))
            body_top = min(price_y(opened), price_y(close))
            body_height = max(abs(price_y(opened) - price_y(close)), 1.0)
            painter.fillRect(
                QRectF(x - 3, body_top, 6, body_height),
                QBrush(color),
            )

        for marker in self._markers:
            index = marker.get("evaluation_index")
            side = marker.get("side")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(self._candles):
                continue
            x = self._x_for_index(index)
            if side == "BUY":
                y = plot_bottom + 12
                points = [
                    QPointF(x, y - 8),
                    QPointF(x - 5, y + 2),
                    QPointF(x + 5, y + 2),
                ]
                color = _BUY
            elif side == "SELL":
                y = plot_top - 12
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


class IndicatorFollowValidationChartWindow(QDialog):
    """Modeless read-only view over one ValidationReplaySnapshot."""

    def __init__(
        self,
        replay_snapshot: ValidationReplaySnapshot,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle("지표추종매매 검증차트")
        self.resize(1100, 620)

        self._candles = replay_snapshot.to_candles()
        self._entries = replay_snapshot.to_entries()
        self._evaluated_start_index = replay_snapshot.evaluated_start_index
        self._evaluated_end_index = replay_snapshot.evaluated_end_index
        self._selected_index: int | None = None

        markers = self._marker_records(self._candles, self._entries)
        buy_count = sum(marker["side"] == "BUY" for marker in markers)
        sell_count = sum(marker["side"] == "SELL" for marker in markers)

        self.summary_label = QLabel(
            f"{replay_snapshot.stock.code} {replay_snapshot.stock.name}  |  "
            f"{replay_snapshot.timeframe_minutes}분봉  |  "
            f"Candle {len(self._candles)}  |  BUY {buy_count}  |  SELL {sell_count}"
        )
        self.summary_label.setToolTip(
            f"settings_hash: {replay_snapshot.settings_hash}\n"
            f"historical_request_id: {replay_snapshot.historical_request_id}"
        )

        self.canvas = IndicatorFollowValidationChartCanvas(self._candles, markers)
        self.canvas.bar_selected.connect(self.select_evaluation_index)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setWidget(self.canvas)

        self.buy_details = self._detail_view()
        self.sell_details = self._detail_view()
        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.buy_details, "BUY")
        self.detail_tabs.addTab(self.sell_details, "SELL")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.scroll_area)
        splitter.addWidget(self.detail_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(splitter, 1)

        self.select_evaluation_index(self._evaluated_end_index)

    @staticmethod
    def _detail_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        return view

    @staticmethod
    def _marker_records(
        candles: list[dict[str, Any]],
        entries: list[ValidationReplayEntry],
    ) -> list[dict[str, Any]]:
        markers = []
        for entry in entries:
            if entry.signal not in {"BUY", "SELL"}:
                continue
            index = entry.evaluation_index
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < len(candles):
                continue
            if str(candles[index].get("time") or "") != entry.evaluation_time:
                continue
            markers.append(
                {
                    "side": entry.signal,
                    "evaluation_index": index,
                    "evaluation_time": entry.evaluation_time,
                }
            )
        return markers

    @property
    def selected_evaluation_index(self) -> int | None:
        return self._selected_index

    def select_evaluation_index(self, index: int) -> bool:
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return False
        self._selected_index = index
        self.canvas.set_selected_index(index)
        if not self._evaluated_start_index <= index <= self._evaluated_end_index:
            text = (
                "Replay 평가 범위 밖\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )
            self.buy_details.setPlainText(text)
            self.sell_details.setPlainText(text)
            return True

        self.buy_details.setPlainText(self._entry_text(index, "BUY"))
        self.sell_details.setPlainText(self._entry_text(index, "SELL"))
        return True

    def _entry_text(self, index: int, side: str) -> str:
        entry = next(
            (
                candidate
                for candidate in self._entries
                if candidate.evaluation_index == index
                and candidate.evaluation_side == side
            ),
            None,
        )
        if entry is None:
            return (
                "Replay 결과 없음\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )

        trace = entry.trace
        return "\n".join(
            [
                f"공식 신호: {entry.signal or '없음'}",
                f"평가시각: {_display_time(entry.evaluation_time)}",
                f"조건 근거봉: {_display_time(entry.signal_time)}",
                f"지연봉: {entry.delay_bar}",
                f"reason: {entry.reason}",
                "",
                "[Matched Groups]",
                _pretty(entry.matched_groups),
                "",
                "[Details]",
                _pretty(entry.details),
                "",
                "[Conditions]",
                _pretty(trace.get("conditions", [])),
                "",
                "[Groups]",
                _pretty(trace.get("groups", [])),
                "",
                "[Aggregations]",
                _pretty(trace.get("aggregations", [])),
            ]
        )
