# -*- coding: utf-8 -*-
"""Common read-only stock instance chart window."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from pathlib import Path
from typing import Any, Callable

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from candle_timeframe_aggregation import SEOUL_TIMEZONE, parse_market_datetime
from gui_auto_trade_display import profit_loss_value_color
from gui_order_utils import DIRECTIONAL_NEUTRAL_COLOR, format_signed_money, format_signed_percent
from stock_instance_day_projection import project_stock_instance_day
from pnl_ui_refresh import PNL_REFRESH_INTERVAL_MS, project_current_stock_pnl


BUY_COLOR = QColor("#DC2626")
SELL_COLOR = QColor("#2563EB")
LINE_COLOR = QColor("#2F6BFF")
BASE_CHART_START_TIME = "09:00:00"
BASE_CHART_END_TIME = "15:30:00"
ProjectionProvider = Callable[[str, str], dict[str, Any]]
ChartFactory = Callable[[QWidget], "StockInstanceCloseChart"]
PROJECT_ROOT = Path(__file__).resolve().parent


def _today_trade_date() -> str:
    return datetime.now(SEOUL_TIMEZONE).date().isoformat()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_count(value: Any, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        count = int(value)
    except (TypeError, ValueError):
        return fallback
    return count if count >= 0 else fallback


class StockInstanceCloseChart(QWidget):
    """Paint one day of close prices and canonical BUY/SELL markers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockInstanceCloseChart")
        self.setMinimumSize(620, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.close_series: list[tuple[datetime, float]] = []
        self.buy_series: list[tuple[datetime, float]] = []
        self.sell_series: list[tuple[datetime, float]] = []
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
        series: list[tuple[datetime, float]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            bar_time = parse_market_datetime(record.get(time_key))
            value = _finite_number(record.get(value_key))
            if bar_time is not None and value is not None:
                series.append((bar_time, value))
        return sorted(series, key=lambda item: item[0])

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
        self.timeframe_minutes = (
            int(timeframe_minutes)
            if isinstance(timeframe_minutes, int)
            and not isinstance(timeframe_minutes, bool)
            and timeframe_minutes > 0
            else None
        )
        self.empty_message = str(empty_message or "표시할 기준봉 데이터가 없습니다.")
        self.update()

    def _line_segments(self) -> list[list[tuple[datetime, float]]]:
        if not self.close_series:
            return []
        if self.timeframe_minutes is None:
            return [list(self.close_series)]
        maximum_gap = timedelta(minutes=self.timeframe_minutes)
        segments: list[list[tuple[datetime, float]]] = []
        current: list[tuple[datetime, float]] = []
        previous_time: datetime | None = None
        for item in self.close_series:
            if previous_time is not None and item[0] - previous_time > maximum_gap:
                segments.append(current)
                current = []
            current.append(item)
            previous_time = item[0]
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
        if not self.close_series or time_range is None:
            return None
        values = [value for _bar_time, value in self.close_series]
        values.extend(value for _bar_time, value in self.buy_series)
        values.extend(value for _bar_time, value in self.sell_series)
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
        super().__init__(parent)
        self.stock_code = str(stock_code or "").strip()
        self.trade_date = str(trade_date or _today_trade_date()).strip()
        self._projection_provider = projection_provider or project_stock_instance_day
        self.last_projection: dict[str, Any] = {}
        self._operation_cycle_signal = None
        self._operation_cycle_refresh_connected = False
        self._operation_command_in_progress = False
        self._stock_operation_adapter = None
        self.setObjectName("stockInstanceChartWindow")
        self.setWindowTitle("종목 인스턴스 차트")
        self.resize(1040, 525)
        self.setMinimumSize(820, 428)

        self.info_labels: dict[str, QLabel] = {}
        self.notice_label = QLabel()
        self.notice_label.setObjectName("stockInstanceChartNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #6B7280;")
        self.chart = (chart_factory or StockInstanceCloseChart)(self)
        self._setup_ui()
        self.refresh_projection()
        self._connect_operation_cycle_refresh()
        self._pnl_refresh_timer = QTimer(self)
        self._pnl_refresh_timer.setInterval(PNL_REFRESH_INTERVAL_MS)
        self._pnl_refresh_timer.timeout.connect(self.refresh_pnl_only)
        if self.trade_date == _today_trade_date():
            self._pnl_refresh_timer.start()

    def _find_operation_cycle_signal(self):
        current = self.parent()
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
        self._operation_cycle_refresh_connected = False

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
        self._pnl_refresh_timer.stop()
        self._disconnect_operation_cycle_refresh()
        super().closeEvent(event)

    def refresh_pnl_only(self) -> None:
        if self.trade_date != _today_trade_date():
            self._pnl_refresh_timer.stop()
            return
        result = project_current_stock_pnl(self.stock_code, project_root=PROJECT_ROOT)
        if result.get("available") is not True:
            return
        amount = _finite_number(result.get("cumulative_profit"))
        rate = _finite_number(result.get("cumulative_rate"))
        if amount is None:
            return
        text = f"{format_signed_money(amount)}원 ({format_signed_percent(rate, digits=2) if rate is not None else '-'})"
        label = self.info_labels["cumulative_pnl"]
        if label.text() != text:
            label.setText(text)
            label.setStyleSheet(f"color: {profit_loss_value_color(amount)};")
        self.last_projection.update({"cumulative_pnl": result.get("cumulative_profit"), "cumulative_return_rate": result.get("cumulative_rate"), "cumulative_return_available": result.get("cumulative_rate") is not None, "pnl_available": True, "pnl_cycle_boundary_id": result.get("boundary_id"), "pnl_evaluation_price": result.get("evaluation_price"), "pnl_evaluation_price_at": result.get("evaluation_price_at")})

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
            QLabel#stockInstanceChartStockValue {
                color: #1D4ED8;
                font-size: 21px;
                font-weight: 700;
            }
            QLabel#stockInstanceChartSummaryValue {
                font-size: 20px;
                font-weight: 700;
            }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(0)

        info_panel = QFrame()
        info_panel.setObjectName("stockInstanceChartInfoPanel")
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(18, 8, 14, 8)
        info_layout.setSpacing(12)

        stock_block = QWidget()
        stock_layout = QVBoxLayout(stock_block)
        stock_layout.setContentsMargins(0, 0, 0, 0)
        stock_value = QLabel("-")
        stock_value.setObjectName("stockInstanceChartStockValue")
        stock_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stock_value.setMinimumWidth(0)
        stock_value.setAlignment(Qt.AlignCenter)
        stock_layout.addWidget(stock_value)
        self.info_labels["stock"] = stock_value
        info_layout.addWidget(stock_block, 18)

        def add_info_block(key: str, stretch: int) -> None:
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            value_label = QLabel("-")
            value_label.setObjectName("stockInstanceChartInfoValue")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setMinimumWidth(0)
            value_label.setAlignment(Qt.AlignCenter)
            block_layout.addWidget(value_label)
            self.info_labels[key] = value_label
            info_layout.addWidget(block, stretch)

        add_info_block("cumulative_pnl", 26)

        self.early_close_button = QPushButton("조기마감")
        self.early_close_button.setObjectName("stockInstanceChartEarlyCloseButton")
        self.early_close_button.setMinimumSize(104, 30)
        self.early_close_button.clicked.connect(self._on_early_close_clicked)
        info_layout.addWidget(self.early_close_button)

        self.immediate_liquidation_button = QPushButton("즉시청산")
        self.immediate_liquidation_button.setObjectName(
            "stockInstanceChartImmediateLiquidationButton"
        )
        self.immediate_liquidation_button.setMinimumSize(104, 30)
        self.immediate_liquidation_button.clicked.connect(
            self._on_immediate_liquidation_clicked
        )
        info_layout.addWidget(self.immediate_liquidation_button)
        root.addWidget(info_panel)

        chart_panel = QFrame()
        chart_panel.setObjectName("stockInstanceChartPanel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(14, 0, 12, 0)
        chart_layout.addWidget(self.chart, 1)
        root.addWidget(chart_panel, 1)
        self._update_operation_button_state()

    def _main_monitoring_window(self):
        current = self.parent()
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
            current = parent_getter() if callable(parent_getter) else None
        return None

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
        from gui_main_stock_context_menu import (
            MainMonitoringStockOperationAdapter,
            MainMonitoringStockTarget,
        )

        stock_dir, code, name, instance_id = context
        adapter = MainMonitoringStockOperationAdapter(
            main_window,
            [
                MainMonitoringStockTarget(
                    stock_dir=stock_dir,
                    code=code,
                    name=name,
                    routine_instance_id=instance_id,
                )
            ],
            request_scope="single",
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

    def _update_operation_button_state(self) -> None:
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
            result: dict[str, object] | None = None
            if operation == "early_close":
                result = adapter.apply_selected_early_close(
                    "루틴",
                    source="간이차트",
                    show_error_dialog=False,
                    show_result_toast=False,
                )
            elif operation == "immediate_liquidation":
                result = adapter.apply_selected_early_close(
                    "시장가",
                    source="간이차트",
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
        self.last_projection = data
        stock_name = str(data.get("stock_name") or "").strip()
        stock_code = str(data.get("stock_code") or self.stock_code).strip()
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
        if pnl_available and cumulative_pnl is not None:
            rate_text = (
                format_signed_percent(cumulative_rate, digits=2)
                if data.get("cumulative_return_available") is True
                and cumulative_rate is not None
                else "-"
            )
            self.info_labels["cumulative_pnl"].setText(
                f"{format_signed_money(cumulative_pnl)}원 ({rate_text})"
            )
            pnl_color = profit_loss_value_color(cumulative_pnl)
        else:
            self.info_labels["cumulative_pnl"].setText("-")
            pnl_color = DIRECTIONAL_NEUTRAL_COLOR
        self.info_labels["cumulative_pnl"].setStyleSheet(
            f"color: {pnl_color};"
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
        )
        buy_fallback = len(buy_markers) if isinstance(buy_markers, list) else 0
        sell_fallback = len(sell_markers) if isinstance(sell_markers, list) else 0
        buy_count = _nonnegative_count(data.get("buy_signal_count"), buy_fallback)
        sell_count = _nonnegative_count(data.get("sell_signal_count"), sell_fallback)
        self.setWindowTitle(
            f"{instance_name or '-'} / {operation_title} / {bar_title} / "
            f"매수 {buy_count} / 매도 {sell_count}"
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
        self.last_projection = {}
        for label in self.info_labels.values():
            label.setText("-")
        self.info_labels["stock"].setText(self.stock_code or "-")
        self.info_labels["cumulative_pnl"].setStyleSheet(
            f"color: {DIRECTIONAL_NEUTRAL_COLOR};"
        )
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
        self.setWindowTitle("- / - / - / 매수 0 / 매도 0")
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
                "holding_quantity",
                "average_price",
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
    """Open an independent common chart window and return it to the caller."""
    dialog = StockInstanceChartWindow(
        stock_code=stock_code,
        trade_date=trade_date,
        parent=parent,
    )
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
