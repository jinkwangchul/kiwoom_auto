# -*- coding: utf-8 -*-
"""종목실적 UI 프로토타입.

고정된 카카오게임즈 예시 데이터만 표시하며 Production 원장을 읽지 않는다.
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QDate, QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_auto_trade_display import profit_loss_value_color


PROTOTYPE_STOCK_CODE = "293490"
PROTOTYPE_STOCK_NAME = "카카오게임즈"
PERFORMANCE_FILTER_TOP_MARGIN = 1
PERFORMANCE_FILTER_BODY_SPACING = 2

PERIOD_RANGES = {
    "오늘": (QDate(2026, 8, 7), QDate(2026, 8, 7)),
    "1주": (QDate(2026, 8, 1), QDate(2026, 8, 7)),
    "1개월": (QDate(2026, 7, 9), QDate(2026, 8, 7)),
    "3개월": (QDate(2026, 5, 8), QDate(2026, 8, 7)),
    "6개월": (QDate(2026, 2, 8), QDate(2026, 8, 7)),
    "전체": (QDate(2025, 8, 1), QDate(2026, 8, 7)),
}

GRAPH_SERIES = {
    "오늘": ("사이클별", [("1", 7000), ("2", -8000), ("3", 8000)]),
    "1주": (
        "일별",
        [
            ("08/01", 9800),
            ("08/02", 11100),
            ("08/03", 7300),
            ("08/04", 11500),
            ("08/05", 24000),
            ("08/06", 30000),
            ("08/07", 38000),
        ],
    ),
    "1개월": (
        "일별",
        [("07/09", 4200), ("07/14", 11800), ("07/20", 8700), ("07/26", 19600), ("08/01", 30100), ("08/07", 48200)],
    ),
    "3개월": (
        "주별",
        [("05/2주", 12500), ("05/4주", 21100), ("06/2주", 18400), ("06/4주", 36200), ("07/2주", 44700), ("07/4주", 53100), ("08/1주", 61800)],
    ),
    "6개월": (
        "주별",
        [("02/2주", 9800), ("03/1주", 24100), ("04/1주", 32700), ("05/1주", 56100), ("06/1주", 48900), ("07/1주", 72300), ("08/1주", 81600)],
    ),
    "전체": (
        "월별",
        [("25/08", 18400), ("25/10", 33700), ("25/12", 51600), ("26/02", 42200), ("26/04", 70500), ("26/06", 91800), ("26/08", 103400)],
    ),
}

DAILY_ROWS = [
    ("2026-08-07 (금)", "3", "2 / 1", "66.7%", "+8,000원", "+1.10%", ("지표추종매매-A", "지표추종매매-B"), ()),
    ("2026-08-06 (목)", "2", "1 / 1", "50.0%", "+6,000원", "+0.17%", ("지표추종매매-A",), ()),
    ("2026-08-05 (수)", "3", "2 / 1", "66.7%", "+12,500원", "+1.45%", ("지표추종매매-A",), ()),
    ("2026-08-04 (화)", "1", "1 / 0", "100.0%", "+4,200원", "+0.92%", ("지표추종매매-B",), ()),
    ("2026-08-03 (월)", "2", "1 / 1", "50.0%", "-3,800원", "-0.43%", ("지표추종매매-A",), ("체결/포지션 불일치", "주문 상태 확인 필요")),
    ("2026-08-02 (일)", "2", "1 / 1", "50.0%", "+1,300원", "+0.23%", ("지표추종매매-A",), ()),
    ("2026-08-01 (토)", "1", "1 / 0", "100.0%", "+9,800원", "+1.68%", ("지표추종매매-A",), ()),
]

DAILY_COLUMN_EXTRA_WIDTHS = (32, 9, 9, 9, 32, 12, 20, 10)
DAILY_VISIBLE_ROW_TARGET = 7


def _daily_routine_display(routine_names) -> tuple[str, str]:
    names = [str(name).strip() for name in routine_names if str(name).strip()]
    if not names:
        return "-", ""
    if len(names) == 1:
        return names[0], ""
    return f"{names[0]} 외 {len(names) - 1}루틴", "\n".join(names)


def _daily_review_status(issue_reasons) -> tuple[str, str]:
    reasons = [str(reason).strip() for reason in issue_reasons if str(reason).strip()]
    if not reasons:
        return "정상", ""
    return "이상", "\n".join(reasons)

OVERALL_METRICS = [
    ("전체기간", "2024-01-01 ~ 2026-08-07"),
    ("총 매수금액", "23,845,000원"),
    ("승 / 패", "143 / 98"),
    ("총 손익금", "+1,248,500원", 1248500),
    ("평균 사이클 손익금", "+5,180원", 5180),
    ("총 사이클", "241회"),
    ("평균 보유시간", "1시간 18분"),
    ("거래일", "188일"),
    ("총 매도금액", "25,093,500원"),
    ("승률", "59.3%"),
    ("총 손익율", "+5.24%", 5.24),
    ("평균 사이클 손익율", "+0.78%", 0.78),
    ("최고 / 최저 손익율", "+6.42% / -3.21%"),
    ("효율", "1.8"),
]

PERIOD_METRICS = [
    ("거래일", "7일"),
    ("평균 손익율", "+0.90%", 0.90),
    ("총 사이클", "14회"),
    ("최고 손익율", "+3.14%", 3.14),
    ("승 / 패", "9 / 5"),
    ("최저 손익율", "-2.16%", -2.16),
    ("승률", "64.3%"),
    ("평균 손익금", "+2,714원", 2714),
    ("총 매수금액", "6,325,000원"),
    ("평균 매수회차", "1.7회"),
    ("총 매도금액", "6,363,000원"),
    ("최대 매수회차", "3회"),
    ("총 손익금", "+38,000원", 38000),
    ("평균 보유시간", "1시간 02분"),
]

ROUTINE_ROWS = [
    ("지표추종매매-A", "13", "8 / 5", "+22,000원", "+0.76%", "61.5%"),
    ("지표추종매매-B", "1", "1 / 0", "+16,000원", "+3.14%", "100.0%"),
]


def prototype_cycle_outcome(realized_profit: object) -> str:
    """완결 사이클의 더미 승패 판정. 0원은 패로 취급한다."""
    try:
        value = float(str(realized_profit).replace(",", "").strip())
    except (TypeError, ValueError):
        value = 0.0
    return "승" if value > 0 else "패"


def _readonly_table(headers: list[str], object_name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.setFocusPolicy(Qt.NoFocus)
    table.horizontalHeader().setHighlightSections(False)
    table.verticalHeader().setDefaultSectionSize(28)
    return table


def _summary_cell(title_text: str, value_text: str, directional_value: float | None = None) -> QFrame:
    cell = QFrame()
    cell.setFrameShape(QFrame.NoFrame)
    cell.setObjectName("stockPerformanceSummaryCell")
    cell.setMinimumHeight(76)
    cell.setStyleSheet(
        "QFrame#stockPerformanceSummaryCell {"
        " border: 1px solid #D7DCE2;"
        " background: #FFFFFF;"
        "}"
        "QFrame#stockPerformanceSummaryCell QLabel {"
        " border: none;"
        " background: transparent;"
        "}"
    )
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(8, 11, 8, 11)
    layout.setSpacing(10)
    title = QLabel(title_text)
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("color: #374151;")
    value = QLabel(value_text)
    value.setAlignment(Qt.AlignCenter)
    value_font = QFont(value.font())
    value_font.setBold(True)
    value.setFont(value_font)
    if directional_value is not None:
        value.setStyleSheet(f"color: {profit_loss_value_color(directional_value)};")
    layout.addWidget(title)
    layout.addWidget(value, 1)
    return cell


def _table_item(text: str, alignment: int = Qt.AlignCenter) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(alignment | Qt.AlignVCenter)
    return item


class _DailySortItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(Qt.UserRole)
        right = other.data(Qt.UserRole)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


def _daily_table_item(text: str, sort_key, alignment: int = Qt.AlignCenter) -> QTableWidgetItem:
    item = _DailySortItem(text)
    item.setData(Qt.UserRole, sort_key)
    item.setTextAlignment(alignment | Qt.AlignVCenter)
    return item


def _daily_sort_key(column: int, values: tuple, display_text: str):
    if column == 0:
        return int(str(values[0])[:10].replace("-", ""))
    if column == 1:
        return int(values[1])
    if column == 2:
        return int(values[1])
    if column in (3, 4, 5):
        return _numeric_text_value(display_text)
    if column == 6:
        return display_text.casefold()
    if column == 7:
        return 0 if display_text == "정상" else 1
    return display_text


def _directional_item(text: str, value: float) -> QTableWidgetItem:
    item = _table_item(text, Qt.AlignRight)
    item.setForeground(QColor(profit_loss_value_color(value)))
    font = QFont(item.font())
    font.setBold(True)
    item.setFont(font)
    return item


def _numeric_text_value(text: str) -> float:
    normalized = text.replace(",", "").replace("원", "").replace("%", "").strip()
    try:
        return float(normalized)
    except ValueError:
        return 0.0


class CumulativeProfitChart(QWidget):
    """외부 그래프 의존성 없이 표시하는 더미 누적손익 선 그래프."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockPerformanceCumulativeProfitChart")
        self.setMinimumWidth(360)
        self.unit_text = "일별"
        self.points: list[tuple[str, int]] = []

    def set_series(self, unit_text: str, points: list[tuple[str, int]]) -> None:
        self.unit_text = unit_text
        self.points = list(points)
        self.update()

    @staticmethod
    def _money_text(value: float) -> str:
        rounded = int(round(value))
        return f"{rounded:+,}" if rounded else "0"

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().base())

        plot = QRectF(70, 38, max(1, self.width() - 88), max(1, self.height() - 82))
        text_color = self.palette().text().color()
        grid_color = self.palette().midlight().color()
        axis_color = self.palette().mid().color()

        values = [0] + [value for _label, value in self.points]
        raw_low = min(values) if values else 0
        raw_high = max(values) if values else 1
        rough_step = max(1.0, (raw_high - raw_low) / 4.0)
        magnitude = 10 ** math.floor(math.log10(rough_step))
        normalized = rough_step / magnitude
        if normalized <= 1:
            nice_step = magnitude
        elif normalized <= 2:
            nice_step = 2 * magnitude
        elif normalized <= 5:
            nice_step = 5 * magnitude
        else:
            nice_step = 10 * magnitude
        low = min(0.0, math.floor(raw_low / nice_step) * nice_step)
        high = max(nice_step, math.ceil(raw_high / nice_step) * nice_step)
        span = high - low

        def y_for(value: float) -> float:
            return plot.bottom() - ((value - low) / span) * plot.height()

        painter.setPen(QPen(grid_color, 1, Qt.DotLine))
        for index in range(5):
            ratio = index / 4
            y = plot.top() + ratio * plot.height()
            tick_value = high - ratio * span
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text_color)
            painter.drawText(QRectF(0, y - 9, plot.left() - 8, 18), Qt.AlignRight | Qt.AlignVCenter, self._money_text(tick_value))
            painter.setPen(QPen(grid_color, 1, Qt.DotLine))

        painter.setPen(QPen(axis_color, 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawLine(plot.topLeft(), plot.bottomLeft())
        zero_y = y_for(0)
        painter.setPen(QPen(axis_color, 1, Qt.DashLine))
        painter.drawLine(QPointF(plot.left(), zero_y), QPointF(plot.right(), zero_y))

        if not self.points:
            return

        step = plot.width() / max(1, len(self.points) - 1)
        plotted = [QPointF(plot.left() + index * step, y_for(value)) for index, (_label, value) in enumerate(self.points)]
        line_value = self.points[-1][1]
        line_color = QColor(profit_loss_value_color(line_value))
        path = QPainterPath(plotted[0])
        for point in plotted[1:]:
            path.lineTo(point)
        painter.setPen(QPen(line_color, 2))
        painter.drawPath(path)
        painter.setBrush(line_color)
        for point in plotted:
            painter.drawEllipse(point, 2.5, 2.5)

        painter.setPen(text_color)
        label_font = QFont(painter.font())
        label_font.setPointSize(max(7, label_font.pointSize() - 1))
        painter.setFont(label_font)
        label_width = step if len(self.points) > 1 else plot.width()
        for index, (label, _value) in enumerate(self.points):
            x = plotted[index].x()
            painter.drawText(
                QRectF(x - label_width / 2, plot.bottom() + 8, label_width, 20),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )


class StockPerformancePrototypeWindow(QDialog):
    """카카오게임즈 더미 데이터로 구성한 조회 전용 종목실적 창."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"종목실적 - {PROTOTYPE_STOCK_CODE} {PROTOTYPE_STOCK_NAME}")
        self.resize(1520, 880)
        self.setMinimumSize(1420, 780)
        self.period_buttons: dict[str, QPushButton] = {}
        self.current_period = "1주"
        self._daily_sort_column = -1
        self._daily_sort_order = Qt.AscendingOrder
        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder

        self.daily_table = _readonly_table(
            ["날짜", "횟수", "승 / 패", "승률", "손익금", "손익율", "적용 루틴(종류)", "상태"],
            "stockPerformanceDailyTable",
        )
        self.daily_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.daily_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        daily_header = self.daily_table.horizontalHeader()
        daily_header.setSectionsClickable(True)
        daily_header.setSortIndicatorShown(False)
        daily_header.sectionClicked.connect(self._sort_daily_table)
        self.routine_table = _readonly_table(
            ["루틴", "횟수", "승 / 패", "손익금", "손익률", "승률"],
            "stockPerformanceRoutineTable",
        )
        self.routine_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.routine_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        routine_header = self.routine_table.horizontalHeader()
        routine_header.setSectionsClickable(True)
        routine_header.setSortIndicatorShown(False)
        routine_header.sectionClicked.connect(self._sort_routine_table)
        self.chart = CumulativeProfitChart()
        self.graph_unit_label = QLabel()
        self.graph_unit_label.setObjectName("stockPerformanceGraphUnitLabel")
        self.graph_unit_label.setFixedHeight(18)
        self.period_value_labels: dict[str, QLabel] = {}

        self._setup_ui()
        self._load_daily_rows()
        self._load_routine_rows()
        self.select_period("1주")

    def _add_metric_grid(self, layout: QGridLayout, metrics: list[tuple], columns: int) -> dict[str, QLabel]:
        labels: dict[str, QLabel] = {}
        for index, metric in enumerate(metrics):
            group = index % columns
            row = index // columns
            title_col = group * 2
            title = QLabel(metric[0])
            title.setObjectName("stockPerformancePeriodMetricTitle")
            title_font = QFont(title.font())
            title_font.setWeight(QFont.Medium)
            title.setFont(title_font)
            title.setStyleSheet("color: #4B5563;")
            value = QLabel(metric[1])
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if len(metric) > 2:
                value.setStyleSheet(f"color: {profit_loss_value_color(metric[2])}; font-weight: 600;")
            layout.addWidget(title, row, title_col)
            layout.addWidget(value, row, title_col + 1)
            layout.setColumnStretch(title_col + 1, 1)
            labels[metric[0]] = value
        return labels

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        overall_group = QGroupBox("전체기간결산")
        overall_group.setObjectName("stockPerformanceOverallGroup")
        overall_group.setStyleSheet(
            "QGroupBox#stockPerformanceOverallGroup::title {"
            " color: #1F2937;"
            " font-size: 15px;"
            " font-weight: 700;"
            " subcontrol-origin: margin;"
            " left: 8px;"
            " padding: 0 4px;"
            "}"
        )
        overall_layout = QGridLayout(overall_group)
        overall_layout.setContentsMargins(8, 12, 8, 8)
        overall_layout.setHorizontalSpacing(0)
        overall_layout.setVerticalSpacing(0)
        for index, metric in enumerate(OVERALL_METRICS):
            directional_value = metric[2] if len(metric) > 2 else None
            overall_layout.addWidget(
                _summary_cell(metric[0], metric[1], directional_value),
                index // 7,
                index % 7,
            )
            overall_layout.setColumnStretch(index % 7, 1)
        root.addWidget(overall_group)

        self.daily_group = QGroupBox("일자별 실적 요약")
        self.daily_group.setObjectName("stockPerformanceDailyGroup")
        self.daily_group.setStyleSheet(overall_group.styleSheet())
        daily_layout = QVBoxLayout(self.daily_group)
        daily_layout.setContentsMargins(8, 8, 8, 8)
        daily_layout.setSpacing(PERFORMANCE_FILTER_BODY_SPACING)

        period_group = QWidget()
        period_group.setObjectName("stockPerformancePeriodGroup")
        period_layout = QHBoxLayout(period_group)
        period_layout.setContentsMargins(4, PERFORMANCE_FILTER_TOP_MARGIN, 0, 0)
        period_layout.setSpacing(6)
        self.daily_title_label = QLabel(
            "조회기간 2026-08-01 ~ 2026-08-07"
        )
        self.daily_title_label.setObjectName("stockPerformanceDailyTitleLabel")
        period_layout.addWidget(self.daily_title_label, 0, Qt.AlignVCenter)
        period_layout.addStretch(1)
        period_button_group = QButtonGroup(self)
        period_button_group.setExclusive(True)
        for text in ("오늘", "1주", "1개월", "3개월", "6개월", "전체"):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setFixedWidth(58)
            button.clicked.connect(lambda _checked=False, mode=text: self.select_period(mode))
            self.period_buttons[text] = button
            period_button_group.addButton(button)
            period_layout.addWidget(button)
        daily_layout.addWidget(period_group)
        daily_table_height = (
            self.daily_table.horizontalHeader().sizeHint().height()
            + DAILY_VISIBLE_ROW_TARGET * self.daily_table.verticalHeader().defaultSectionSize()
            + self.daily_table.frameWidth() * 2
            + 4
        )
        self.daily_table.setFixedHeight(daily_table_height)
        self.period_total_panel = QWidget()
        self.period_total_panel.setObjectName("stockPerformancePeriodTotalPanel")
        self.period_total_panel.setFixedHeight(daily_table_height)
        period_total_layout = QGridLayout(self.period_total_panel)
        period_total_layout.setContentsMargins(
            12,
            self.daily_table.horizontalHeader().sizeHint().height(),
            12,
            0,
        )
        period_total_layout.setHorizontalSpacing(10)
        period_total_layout.setVerticalSpacing(0)
        period_total_layout.setAlignment(Qt.AlignTop)
        self.period_value_labels = self._add_metric_grid(period_total_layout, PERIOD_METRICS, 2)
        daily_row_height = self.daily_table.verticalHeader().defaultSectionSize()
        for label in self.period_total_panel.findChildren(QLabel):
            label.setFixedHeight(daily_row_height)
        for row in range((len(PERIOD_METRICS) + 1) // 2):
            period_total_layout.setRowMinimumHeight(row, daily_row_height)
        self.period_total_panel.setMinimumWidth(430)

        period_separator = QFrame()
        period_separator.setObjectName("stockPerformancePeriodSeparator")
        period_separator.setFrameShape(QFrame.VLine)
        period_separator.setFrameShadow(QFrame.Plain)
        period_separator.setStyleSheet("color: #D7DCE2;")

        period_body = QHBoxLayout()
        period_body.setContentsMargins(0, 0, 0, 0)
        period_body.setSpacing(8)
        period_body.addWidget(self.daily_table, 2)
        period_body.addWidget(period_separator)
        period_body.addWidget(self.period_total_panel, 1)
        daily_layout.addLayout(period_body, 1)
        self.daily_group.setFixedHeight(self.daily_group.sizeHint().height())
        root.addWidget(self.daily_group, 1)

        lower = QHBoxLayout()
        lower.setSpacing(10)

        self.routine_group = QGroupBox("루틴별 실적")
        self.routine_group.setObjectName("stockPerformanceRoutineGroup")
        routine_layout = QVBoxLayout(self.routine_group)
        routine_layout.setContentsMargins(8, 14, 8, 8)
        self.routine_table.setMinimumWidth(590)
        routine_layout.addWidget(self.routine_table)

        chart_group = QGroupBox("누적손익")
        chart_group.setObjectName("stockPerformanceChartGroup")
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.setContentsMargins(8, 12, 8, 8)
        self.graph_unit_label.setAlignment(Qt.AlignRight)
        self.graph_unit_label.setStyleSheet("color: #4B5563;")
        chart_layout.addWidget(self.graph_unit_label)
        chart_layout.addWidget(self.chart, 1)
        chart_group.setMinimumWidth(380)
        lower.addWidget(chart_group, 2)
        lower.addWidget(self.routine_group, 3)
        root.addLayout(lower, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("닫기")
        close_button.setFixedWidth(100)
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

    def _load_daily_rows(self) -> None:
        self.daily_table.setRowCount(len(DAILY_ROWS))
        for row, values in enumerate(DAILY_ROWS):
            for column, value in enumerate(values):
                tooltip = ""
                if column == 6:
                    text, tooltip = _daily_routine_display(value)
                elif column == 7:
                    text, tooltip = _daily_review_status(value)
                else:
                    text = value
                alignment = Qt.AlignCenter
                item = _daily_table_item(
                    text,
                    _daily_sort_key(column, values, text),
                    alignment,
                )
                if column in (4, 5):
                    item.setForeground(QColor(profit_loss_value_color(_numeric_text_value(text))))
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                elif column == 7 and text == "이상":
                    item.setForeground(QColor("#D97706"))
                if tooltip:
                    item.setToolTip(tooltip)
                self.daily_table.setItem(row, column, item)
        header = self.daily_table.horizontalHeader()
        for column in range(self.daily_table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.daily_table.resizeColumnsToContents()
        content_widths = [
            self.daily_table.columnWidth(column)
            for column in range(self.daily_table.columnCount())
        ]
        font_metrics = self.daily_table.fontMetrics()
        content_widths[1] = max(
            content_widths[1],
            font_metrics.horizontalAdvance("횟수")
            + self.daily_table.style().pixelMetric(QStyle.PM_HeaderMargin) * 2,
        )
        routine_width = font_metrics.horizontalAdvance("지표추종매매-A 외 1루틴") + 28
        status_width = font_metrics.horizontalAdvance("상태") + 30
        content_widths[6] = routine_width
        content_widths[7] = status_width
        for column, extra_width in enumerate(DAILY_COLUMN_EXTRA_WIDTHS):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.daily_table.setColumnWidth(
                column,
                content_widths[column] + extra_width,
            )
        table_width = (
            sum(self.daily_table.columnWidth(column) for column in range(self.daily_table.columnCount()))
            + self.daily_table.verticalScrollBar().sizeHint().width()
            + self.daily_table.frameWidth() * 2
        )
        self.daily_table.setFixedWidth(table_width)

    def _sort_daily_table(self, column: int) -> None:
        if column == self._daily_sort_column:
            order = (
                Qt.DescendingOrder
                if self._daily_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            order = Qt.AscendingOrder
        self._daily_sort_column = column
        self._daily_sort_order = order
        self.daily_table.sortItems(column, order)
        header = self.daily_table.horizontalHeader()
        header.setSortIndicator(column, order)
        header.setSortIndicatorShown(False)

    def _load_routine_rows(self) -> None:
        self.routine_table.setRowCount(len(ROUTINE_ROWS))
        for row, values in enumerate(ROUTINE_ROWS):
            for column, text in enumerate(values):
                alignment = Qt.AlignLeft if column == 0 else Qt.AlignCenter
                if column == 0:
                    sort_key = text.casefold()
                elif column in (1, 2):
                    sort_key = int(values[1])
                else:
                    sort_key = _numeric_text_value(text)
                item = _daily_table_item(text, sort_key, alignment)
                if column in (3, 4):
                    item.setForeground(QColor(profit_loss_value_color(_numeric_text_value(text))))
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.routine_table.setItem(row, column, item)
        header = self.routine_table.horizontalHeader()
        header.setFixedHeight(42)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for column, width in {
            0: 240,
            1: 70,
            2: 95,
            3: 145,
            4: 110,
            5: 90,
        }.items():
            self.routine_table.setColumnWidth(column, width)
        header.setStretchLastSection(False)
        routine_table_width = (
            sum(
                self.routine_table.columnWidth(column)
                for column in range(self.routine_table.columnCount())
            )
            + self.routine_table.verticalScrollBar().sizeHint().width()
            + self.routine_table.frameWidth() * 2
        )
        self.routine_table.setFixedWidth(routine_table_width)
        routine_margins = self.routine_group.layout().contentsMargins()
        self.routine_group.setFixedWidth(
            routine_table_width + routine_margins.left() + routine_margins.right()
        )

    def _sort_routine_table(self, column: int) -> None:
        if column == self._routine_sort_column:
            order = (
                Qt.DescendingOrder
                if self._routine_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            order = Qt.AscendingOrder
        self._routine_sort_column = column
        self._routine_sort_order = order
        self.routine_table.sortItems(column, order)
        header = self.routine_table.horizontalHeader()
        header.setSortIndicator(column, order)
        header.setSortIndicatorShown(False)

    def select_period(self, mode: str) -> None:
        if mode not in GRAPH_SERIES:
            return
        self.current_period = mode
        start, end = PERIOD_RANGES[mode]
        self.period_buttons[mode].setChecked(True)
        range_text = f"{start.toString('yyyy-MM-dd')} ~ {end.toString('yyyy-MM-dd')}"
        self.daily_title_label.setText(
            f"조회기간 {range_text}"
        )
        unit_text, points = GRAPH_SERIES[mode]
        self.graph_unit_label.clear()
        self.chart.set_series(unit_text, points)


def open_stock_performance_prototype(window: QWidget) -> None:
    """단일 종목 선택 계약을 지키며 고정 더미 프로토타입을 연다."""
    selected = window.selected_stock_info()
    if selected is None:
        QMessageBox.warning(window, "선택 오류", "실적을 확인할 종목을 1개 선택하세요.")
        return

    dialog = StockPerformancePrototypeWindow(parent=window)
    dialog.exec_()
