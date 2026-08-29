# -*- coding: utf-8 -*-
"""Production-safe stock performance window.

There is not yet a complete canonical aggregation contract for this view. The
window therefore shows an explicit empty state instead of inferred or sample
performance.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPainter, QPen
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
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from gui_window_policy import configure_persistent_feature_window


PERFORMANCE_FILTER_TOP_MARGIN = 1
PERFORMANCE_FILTER_BODY_SPACING = 2
DAILY_VISIBLE_ROW_TARGET = 7
PERIOD_MODES = ("오늘", "1주", "1개월", "3개월", "6개월", "전체")

OVERALL_METRIC_TITLES = (
    "전체기간", "총 매수금액", "승 / 패", "총 손익금", "평균 사이클 손익금",
    "총 사이클", "평균 보유시간", "거래일", "총 매도금액", "승률",
    "총 손익율", "평균 사이클 손익율", "최고 / 최저 손익율", "효율",
)
PERIOD_METRIC_TITLES = (
    "거래일", "평균 손익율", "총 사이클", "최고 손익율", "승 / 패",
    "최저 손익율", "승률", "평균 손익금", "총 매수금액", "평균 매수회차",
    "총 매도금액", "최대 매수회차", "총 손익금", "평균 보유시간",
)


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


def _summary_cell(title_text: str) -> QFrame:
    cell = QFrame()
    cell.setObjectName("stockPerformanceSummaryCell")
    cell.setMinimumHeight(76)
    cell.setStyleSheet(
        "QFrame#stockPerformanceSummaryCell { border: 1px solid #D7DCE2; background: #FFFFFF; }"
        "QFrame#stockPerformanceSummaryCell QLabel { border: none; background: transparent; }"
    )
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(8, 11, 8, 11)
    layout.setSpacing(10)
    title = QLabel(title_text)
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("color: #374151;")
    value = QLabel("-")
    value.setObjectName("stockPerformanceSummaryValue")
    value.setAlignment(Qt.AlignCenter)
    font = QFont(value.font())
    font.setBold(True)
    value.setFont(font)
    layout.addWidget(title)
    layout.addWidget(value, 1)
    return cell


class CumulativeProfitChart(QWidget):
    """Empty chart surface reserved for canonical performance data."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockPerformanceCumulativeProfitChart")
        self.setMinimumWidth(360)
        self.unit_text = ""
        self.points: list[tuple[str, int]] = []

    def set_series(self, unit_text: str, points: list[tuple[str, int]]) -> None:
        self.unit_text = unit_text
        self.points = list(points)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(QPen(self.palette().mid().color(), 1))
        left = 38
        bottom = max(24, self.height() - 28)
        painter.drawLine(left, 20, left, bottom)
        painter.drawLine(left, bottom, max(left, self.width() - 18), bottom)


class StockPerformanceWindow(QDialog):
    """Read-only stock performance view with a truthful empty initial state."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        stock_code: str = "",
        stock_name: str = "",
    ) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)
        identity = " ".join(part for part in (stock_code.strip(), stock_name.strip()) if part)
        self.setWindowTitle(f"종목실적 - {identity}" if identity else "종목실적")
        self.resize(1520, 880)
        self.setMinimumSize(1420, 780)
        self.period_buttons: dict[str, QPushButton] = {}
        self.current_period = "1주"

        self.daily_table = _readonly_table(
            ["날짜", "횟수", "승 / 패", "승률", "손익금", "손익율", "적용 루틴(종류)", "상태"],
            "stockPerformanceDailyTable",
        )
        self.daily_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.daily_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.routine_table = _readonly_table(
            ["루틴", "횟수", "승 / 패", "손익금", "손익률", "승률"],
            "stockPerformanceRoutineTable",
        )
        self.routine_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.routine_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart = CumulativeProfitChart()
        self.graph_unit_label = QLabel()
        self.graph_unit_label.setObjectName("stockPerformanceGraphUnitLabel")
        self.graph_unit_label.setFixedHeight(18)
        self.period_value_labels: dict[str, QLabel] = {}
        self.empty_state_label = QLabel("거래 실적 없음")
        self.empty_state_label.setObjectName("stockPerformanceEmptyStateLabel")
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setStyleSheet("color: #6B7280; font-weight: 600;")

        self._setup_ui()
        self._configure_empty_tables()
        self.select_period("1주")

    def _add_metric_grid(
        self, layout: QGridLayout, titles: tuple[str, ...], columns: int
    ) -> dict[str, QLabel]:
        labels: dict[str, QLabel] = {}
        for index, title_text in enumerate(titles):
            group = index % columns
            row = index // columns
            title_col = group * 2
            title = QLabel(title_text)
            title.setObjectName("stockPerformancePeriodMetricTitle")
            font = QFont(title.font())
            font.setWeight(QFont.Medium)
            title.setFont(font)
            title.setStyleSheet("color: #4B5563;")
            value = QLabel("-")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(title, row, title_col)
            layout.addWidget(value, row, title_col + 1)
            layout.setColumnStretch(title_col + 1, 1)
            labels[title_text] = value
        return labels

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        overall_group = QGroupBox("전체기간결산")
        overall_group.setObjectName("stockPerformanceOverallGroup")
        overall_group.setStyleSheet(
            "QGroupBox#stockPerformanceOverallGroup::title { color: #1F2937;"
            " font-size: 15px; font-weight: 700; subcontrol-origin: margin;"
            " left: 8px; padding: 0 4px; }"
        )
        overall_layout = QGridLayout(overall_group)
        overall_layout.setContentsMargins(8, 12, 8, 8)
        overall_layout.setHorizontalSpacing(0)
        overall_layout.setVerticalSpacing(0)
        for index, title_text in enumerate(OVERALL_METRIC_TITLES):
            overall_layout.addWidget(_summary_cell(title_text), index // 7, index % 7)
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
        self.daily_title_label = QLabel("조회기간 -")
        self.daily_title_label.setObjectName("stockPerformanceDailyTitleLabel")
        period_layout.addWidget(self.daily_title_label, 0, Qt.AlignVCenter)
        period_layout.addStretch(1)
        period_button_group = QButtonGroup(self)
        period_button_group.setExclusive(True)
        for text in PERIOD_MODES:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setFixedWidth(58)
            button.clicked.connect(lambda _checked=False, mode=text: self.select_period(mode))
            self.period_buttons[text] = button
            period_button_group.addButton(button)
            period_layout.addWidget(button)
        daily_layout.addWidget(period_group)
        daily_layout.addWidget(self.empty_state_label)

        daily_table_height = (
            self.daily_table.horizontalHeader().sizeHint().height()
            + DAILY_VISIBLE_ROW_TARGET * self.daily_table.verticalHeader().defaultSectionSize()
            + self.daily_table.frameWidth() * 2 + 4
        )
        self.daily_table.setFixedHeight(daily_table_height)
        self.period_total_panel = QWidget()
        self.period_total_panel.setObjectName("stockPerformancePeriodTotalPanel")
        self.period_total_panel.setFixedHeight(daily_table_height)
        period_total_layout = QGridLayout(self.period_total_panel)
        period_total_layout.setContentsMargins(
            12, self.daily_table.horizontalHeader().sizeHint().height(), 12, 0
        )
        period_total_layout.setHorizontalSpacing(10)
        period_total_layout.setVerticalSpacing(0)
        period_total_layout.setAlignment(Qt.AlignTop)
        self.period_value_labels = self._add_metric_grid(
            period_total_layout, PERIOD_METRIC_TITLES, 2
        )
        row_height = self.daily_table.verticalHeader().defaultSectionSize()
        for label in self.period_total_panel.findChildren(QLabel):
            label.setFixedHeight(row_height)
        for row in range((len(PERIOD_METRIC_TITLES) + 1) // 2):
            period_total_layout.setRowMinimumHeight(row, row_height)
        self.period_total_panel.setMinimumWidth(430)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Plain)
        separator.setStyleSheet("color: #D7DCE2;")
        period_body = QHBoxLayout()
        period_body.setContentsMargins(0, 0, 0, 0)
        period_body.setSpacing(8)
        period_body.addWidget(self.daily_table, 2)
        period_body.addWidget(separator)
        period_body.addWidget(self.period_total_panel, 1)
        daily_layout.addLayout(period_body, 1)
        self.daily_group.setFixedHeight(self.daily_group.sizeHint().height())
        root.addWidget(self.daily_group, 1)

        lower = QHBoxLayout()
        lower.setSpacing(10)
        chart_group = QGroupBox("누적손익")
        chart_group.setObjectName("stockPerformanceChartGroup")
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.setContentsMargins(8, 12, 8, 8)
        self.graph_unit_label.setAlignment(Qt.AlignRight)
        self.graph_unit_label.setStyleSheet("color: #4B5563;")
        chart_layout.addWidget(self.graph_unit_label)
        chart_layout.addWidget(self.chart, 1)
        chart_group.setMinimumWidth(380)

        self.routine_group = QGroupBox("루틴별 실적")
        self.routine_group.setObjectName("stockPerformanceRoutineGroup")
        routine_layout = QVBoxLayout(self.routine_group)
        routine_layout.setContentsMargins(8, 14, 8, 8)
        self.routine_table.setMinimumWidth(590)
        routine_layout.addWidget(self.routine_table)
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

    def _configure_empty_tables(self) -> None:
        self.daily_table.setRowCount(0)
        daily_header = self.daily_table.horizontalHeader()
        daily_header.setSectionResizeMode(QHeaderView.Stretch)
        daily_header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.routine_table.setRowCount(0)
        routine_header = self.routine_table.horizontalHeader()
        routine_header.setFixedHeight(42)
        routine_header.setSectionResizeMode(QHeaderView.Stretch)

    def select_period(self, mode: str) -> None:
        if mode not in self.period_buttons:
            return
        self.current_period = mode
        self.period_buttons[mode].setChecked(True)
        self.daily_title_label.setText("조회기간 -")
        self.graph_unit_label.clear()
        self.chart.set_series("", [])


def open_stock_performance(window: QWidget) -> None:
    """Open the empty performance view for the selected real stock identity."""
    selected = window.selected_stock_info()
    if selected is None:
        QMessageBox.warning(window, "선택 오류", "실적을 확인할 종목을 1개 선택하세요.")
        return

    existing = getattr(window, "__dict__", {}).get("stock_performance_window")
    if existing is not None:
        try:
            if existing.isVisible():
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
        except RuntimeError:
            pass

    _stock_path, stock_code, stock_name = selected
    dialog = StockPerformanceWindow(
        parent=window,
        stock_code=str(stock_code or ""),
        stock_name=str(stock_name or ""),
    )
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    window.stock_performance_window = dialog
    dialog.destroyed.connect(
        lambda _obj=None, target=dialog: (
            setattr(window, "stock_performance_window", None)
            if getattr(window, "stock_performance_window", None) is target
            else None
        )
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
