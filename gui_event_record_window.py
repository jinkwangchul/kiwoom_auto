# -*- coding: utf-8 -*-
"""Read-only Event Journal window backed by the official Production reader."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from event_journal_contract import EVENT_TYPE_LABELS, event_target_display, parse_aware_timestamp
from event_journal_reader import EventJournalReader


EVENT_RECORD_HEADERS = ("일시", "구분", "중요도", "대상", "이벤트", "결과", "내용")
EVENT_RECORD_PERIODS = ("오늘", "1주", "1개월", "3개월", "전체")
CATEGORY_OPTIONS = (
    ("전체", None), ("시스템", "SYSTEM"), ("운영", "OPERATION"),
    ("설정", "SETTING"), ("신호", "SIGNAL"), ("주문", "ORDER"),
    ("체결", "FILL"),
)
SEVERITY_OPTIONS = (
    ("전체", None), ("정보", "INFO"), ("주의", "NOTICE"),
    ("경고", "WARNING"), ("오류", "ERROR"),
)
CATEGORY_LABELS = {value: label for label, value in CATEGORY_OPTIONS if value}
SEVERITY_LABELS = {value: label for label, value in SEVERITY_OPTIONS if value}
RESULT_LABELS = {
    "SUCCESS": "성공", "COMPLETED": "완료", "REQUESTED": "요청",
    "ACCEPTED": "접수", "BLOCKED": "차단", "REJECTED": "거부",
    "FAILED": "실패", "UNCERTAIN": "불확정", "CANCELLED": "취소",
}
SEVERITY_SORT_RANK = {"INFO": 0, "NOTICE": 1, "WARNING": 2, "ERROR": 3}
SEVERITY_COLORS = {
    "NOTICE": QColor("#9A6700"),
    "WARNING": QColor("#B45309"),
    "ERROR": QColor("#B91C1C"),
}
RESULT_COLORS = {
    "BLOCKED": QColor("#B45309"),
    "REJECTED": QColor("#B91C1C"),
    "FAILED": QColor("#B91C1C"),
}
CORRELATION_LABELS = (
    ("app_session_id", "프로그램 세션 ID"),
    ("stock_code", "종목코드"),
    ("routine", "적용 루틴"),
    ("event_id", "이벤트 ID"),
    ("signal_id", "신호 ID"),
    ("order_id", "주문 ID"),
    ("execution_id", "체결 ID"),
    ("broker_order_no", "Broker 주문번호"),
    ("command_id", "명령 ID"),
)
DEFAULT_HIDDEN_EVENT_TYPES = frozenset({
    "ORDER_QUEUED",
    "OPERATION_HOST_STARTED",
    "RECOVERY_COMPLETED",
    "SEND_ORDER_REQUEST_ACCEPTED",
})


class _EventRecordItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:
        left = self.data(Qt.UserRole + 1)
        right = other.data(Qt.UserRole + 1)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class EventRecordPrototypeWindow(QDialog):
    """Program-wide Event Journal reader; kept under the existing public class name."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        reader: EventJournalReader | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(parent)
        self.reader = reader or EventJournalReader()
        self._now_provider = now_provider or (lambda: datetime.now().astimezone())
        self.setWindowTitle("이벤트기록")
        self.resize(1580, 800)
        self.setMinimumSize(1400, 700)
        self.current_period = "1주"
        self.period_buttons: dict[str, QPushButton] = {}
        self._period_button_group = QButtonGroup(self)
        self._period_button_group.setExclusive(True)
        self._setup_ui()
        self.select_period(self.current_period)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        filter_group = QGroupBox("조회 조건")
        filter_layout = QHBoxLayout(filter_group)
        filter_layout.setContentsMargins(10, 12, 10, 8)
        filter_layout.setSpacing(6)
        for period in EVENT_RECORD_PERIODS:
            button = QPushButton(period)
            button.setCheckable(True)
            button.setFixedWidth(58)
            button.clicked.connect(lambda _checked=False, value=period: self.select_period(value))
            self.period_buttons[period] = button
            self._period_button_group.addButton(button)
            filter_layout.addWidget(button)
        control_height = max(button.sizeHint().height() for button in self.period_buttons.values())
        filter_layout.addSpacing(12)
        category_label = QLabel("구분")
        category_label.setFixedHeight(control_height)
        category_label.setAlignment(Qt.AlignCenter)
        filter_layout.addWidget(category_label)
        self.category_combo = QComboBox()
        for label, value in CATEGORY_OPTIONS:
            self.category_combo.addItem(label, value)
        self.category_combo.setMinimumWidth(92)
        self.category_combo.setFixedHeight(control_height)
        self.category_combo.currentIndexChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.category_combo)
        filter_layout.addSpacing(6)
        severity_label = QLabel("중요도")
        severity_label.setFixedHeight(control_height)
        severity_label.setAlignment(Qt.AlignCenter)
        filter_layout.addWidget(severity_label)
        self.severity_combo = QComboBox()
        for label, value in SEVERITY_OPTIONS:
            self.severity_combo.addItem(label, value)
        self.severity_combo.setMinimumWidth(82)
        self.severity_combo.setFixedHeight(control_height)
        self.severity_combo.currentIndexChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.severity_combo)
        filter_layout.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("대상·이벤트·내용·종목코드 검색")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(240)
        self.search_edit.textChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.search_edit)
        root.addWidget(filter_group)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._create_event_list_panel())
        splitter.addWidget(self._create_detail_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1170, 380])
        root.addWidget(splitter, 1)
        bottom = QHBoxLayout()
        self.result_count_label = QLabel()
        self.result_count_label.setStyleSheet("color: #6B7280;")
        bottom.addWidget(self.result_count_label)
        bottom.addStretch(1)
        close_button = QPushButton("닫기")
        close_button.setMinimumWidth(92)
        close_button.clicked.connect(self.close)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

    def _create_event_list_panel(self) -> QWidget:
        group = QGroupBox("이벤트 목록")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 12, 8, 8)
        self.event_table = QTableWidget(0, len(EVENT_RECORD_HEADERS))
        self.event_table.setObjectName("eventRecordTable")
        self.event_table.setHorizontalHeaderLabels(EVENT_RECORD_HEADERS)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.verticalHeader().setDefaultSectionSize(29)
        self.event_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.event_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.event_table.setAlternatingRowColors(True)
        self.event_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.event_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.event_table.setSortingEnabled(True)
        header = self.event_table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setHighlightSections(False)
        header.setSortIndicatorShown(False)
        for column, width in enumerate((190, 66, 70, 210, 190, 76)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.event_table.setColumnWidth(column, width)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.event_table.itemSelectionChanged.connect(self._show_selected_event)
        layout.addWidget(self.event_table)
        return group

    def _create_detail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        detail_group = QGroupBox("이벤트 상세")
        detail_layout = QFormLayout(detail_group)
        detail_layout.setContentsMargins(12, 16, 12, 12)
        detail_layout.setHorizontalSpacing(14)
        detail_layout.setVerticalSpacing(8)
        self.detail_labels: dict[str, QLabel] = {}
        for key, title in (("occurred_at", "발생시각"), ("category", "구분"),
                           ("severity", "중요도"), ("target", "대상"),
                           ("event", "이벤트"), ("result", "결과")):
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail_layout.addRow(title, label)
            self.detail_labels[key] = label
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMinimumHeight(150)
        detail_layout.addRow("상세 내용", self.detail_text)
        layout.addWidget(detail_group, 2)
        correlation_group = QGroupBox("연결 정보")
        correlation_layout = QFormLayout(correlation_group)
        correlation_layout.setContentsMargins(12, 16, 12, 12)
        correlation_layout.setHorizontalSpacing(14)
        correlation_layout.setVerticalSpacing(7)
        self.correlation_rows: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title in CORRELATION_LABELS:
            title_label = QLabel(title)
            value_label = QLabel()
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)
            correlation_layout.addRow(title_label, value_label)
            self.correlation_rows[key] = (title_label, value_label)
        layout.addWidget(correlation_group, 1)
        layout.addStretch(1)
        return panel

    def select_period(self, period: str) -> None:
        if period not in EVENT_RECORD_PERIODS:
            return
        self.current_period = period
        self.period_buttons[period].setChecked(True)
        self.apply_filters()

    def _period_bounds(self) -> tuple[datetime | None, datetime]:
        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            now = now.astimezone()
        if self.current_period == "전체":
            return None, now
        start_day = now.date()
        days = {"오늘": 1, "1주": 7, "1개월": 30, "3개월": 90}[self.current_period]
        start_day -= timedelta(days=days - 1)
        return datetime.combine(start_day, time.min, tzinfo=now.tzinfo), now

    def _filtered_events(self) -> list[dict[str, object]]:
        start_at, end_at = self._period_bounds()
        result = self.reader.read_events(
            start_at=start_at,
            end_at=end_at,
            category=self.category_combo.currentData(),
            severity=self.severity_combo.currentData(),
            query=self.search_edit.text(),
            descending=True,
        )
        events = result.get("events") if isinstance(result, dict) else []
        return [
            dict(event)
            for event in events
            if isinstance(event, dict)
            and str(event.get("event_type") or "") not in DEFAULT_HIDDEN_EVENT_TYPES
        ]

    @staticmethod
    def _display_time(value: object) -> str:
        parsed = parse_aware_timestamp(value)
        return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed is not None else str(value or "")

    @staticmethod
    def _display_event(event: dict[str, object]) -> dict[str, object]:
        category = str(event.get("category") or "")
        severity = str(event.get("severity") or "")
        event_type = str(event.get("event_type") or "")
        result = str(event.get("result") or "")
        return {
            "occurred_at": EventRecordPrototypeWindow._display_time(event.get("occurred_at")),
            "category": CATEGORY_LABELS.get(category, category),
            "severity": SEVERITY_LABELS.get(severity, severity),
            "target": event_target_display(event) or "-",
            "event": EVENT_TYPE_LABELS.get(event_type, event_type),
            "result": RESULT_LABELS.get(result, result) if result else "-",
            "summary": str(event.get("summary") or ""),
        }

    def apply_filters(self, *_args) -> None:
        events = self._filtered_events()
        self.event_table.setSortingEnabled(False)
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            display = self._display_event(event)
            values = tuple(display[key] for key in ("occurred_at", "category", "severity", "target", "event", "result", "summary"))
            severity = str(event.get("severity") or "")
            result = str(event.get("result") or "")
            sort_values = (str(event.get("occurred_at") or ""), str(event.get("category") or ""),
                           SEVERITY_SORT_RANK.get(severity, 99), values[3], values[4], result, values[6])
            for column, value in enumerate(values):
                item = _EventRecordItem(str(value))
                item.setData(Qt.UserRole + 1, sort_values[column])
                if column == 0:
                    item.setData(Qt.UserRole, event)
                if column in {1, 2, 5}:
                    item.setTextAlignment(Qt.AlignCenter)
                if column == 2 and severity in SEVERITY_COLORS:
                    item.setForeground(SEVERITY_COLORS[severity])
                    font = QFont(item.font())
                    font.setWeight(QFont.Medium)
                    item.setFont(font)
                if column == 5 and result in RESULT_COLORS:
                    item.setForeground(RESULT_COLORS[result])
                self.event_table.setItem(row, column, item)
        self.event_table.setSortingEnabled(True)
        self.event_table.sortItems(0, Qt.DescendingOrder)
        self.event_table.horizontalHeader().setSortIndicatorShown(False)
        self.result_count_label.setText(f"표시 {len(events)}건")
        if events:
            self.event_table.selectRow(0)
            self._show_selected_event()
        else:
            self.event_table.clearSelection()
            self._clear_detail()

    def _show_selected_event(self) -> None:
        row = self.event_table.currentRow()
        item = self.event_table.item(row, 0) if row >= 0 else None
        event = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(event, dict):
            self._clear_detail()
            return
        display = self._display_event(event)
        for key, label in self.detail_labels.items():
            label.setText(str(display.get(key, "-") or "-"))
        details = event.get("details")
        if isinstance(details, (dict, list)):
            detail_text = json.dumps(details, ensure_ascii=False, indent=2)
        else:
            detail_text = str(details or event.get("summary") or "")
        self.detail_text.setPlainText(detail_text)
        for key, (title_label, value_label) in self.correlation_rows.items():
            value = str(event.get(key, "") or "").strip()
            title_label.setVisible(bool(value))
            value_label.setVisible(bool(value))
            value_label.setText(value)

    def _clear_detail(self) -> None:
        for label in self.detail_labels.values():
            label.setText("-")
        self.detail_text.clear()
        for title_label, value_label in self.correlation_rows.values():
            title_label.hide()
            value_label.hide()


def _clear_event_record_window_reference(parent: QWidget, target: EventRecordPrototypeWindow) -> None:
    try:
        if getattr(parent, "event_record_window", None) is target:
            parent.event_record_window = None
    except RuntimeError:
        pass


def open_event_record_prototype(parent: QWidget) -> EventRecordPrototypeWindow:
    """Open the single visible Production Event Journal reader window."""
    existing = getattr(parent, "event_record_window", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing
    dialog = EventRecordPrototypeWindow(parent)
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    parent.event_record_window = dialog
    dialog.destroyed.connect(lambda _obj=None, target=dialog: _clear_event_record_window_reference(parent, target))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
