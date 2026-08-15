# -*- coding: utf-8 -*-
"""Read-only Event Journal window backed by the official Production reader."""

from __future__ import annotations

from datetime import datetime, time, timedelta
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
from gui_window_policy import configure_persistent_feature_window

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
EVENT_LIST_PANEL_FIXED_WIDTH = 1170
EVENT_DETAIL_PANEL_MINIMUM_WIDTH = 380
OPERATOR_EVENT_TYPES = frozenset({
    "OPERATOR_SYSTEM_DECISION",
    "OPERATOR_OPERATION_DECISION",
    "OPERATOR_SETTING_DECISION",
    "OPERATOR_ORDER_DECISION",
})
FIELD_LABELS = {
    "total_budget": "전체예산",
    "available_budget_percent": "가용 비율",
    "threshold_percent": "구간마감 비율",
    "application_mode": "적용 방식",
    "display_name": "표시 이름",
    "buy_limit_enabled": "매수 한도 사용",
    "buy_limit_amount": "매수 한도금액",
    "start_time": "운영 시작시간",
    "end_buy_time": "매수 종료시간",
    "routine": "적용 루틴",
    "lifecycle_state": "생명주기 상태",
    "saved_account_info": "저장 계좌정보",
    "profit_percent": "수익률",
    "loss_percent": "손실률",
    "quantity": "수량",
    "price": "가격",
}
DETAIL_LABELS = {
    "interaction_type": "상호작용",
    "prompt_title": "창 제목",
    "prompt_summary": "질문",
    "selected_option": "사용자 선택",
    "input_value": "입력값",
    "confirmation_matched": "확인문구 일치",
    "method": "방식",
    "operation": "작업",
    "stage": "처리 단계",
    "reason": "사유",
    "review_reason": "검토 사유",
    "requested_count": "요청 수",
    "target_count": "대상 수",
}
_SENSITIVE_DETAIL_KEYS = frozenset({
    "account",
    "account_no",
    "api_key",
    "auth",
    "broker_response",
    "directory",
    "file_path",
    "password",
    "path",
    "raw",
    "raw_response",
    "secret",
    "stack",
    "token",
    "traceback",
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
        super().__init__(None)
        configure_persistent_feature_window(self, parent)
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
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.setObjectName("eventRecordRefreshButton")
        self.refresh_button.setFixedHeight(control_height)
        self.refresh_button.clicked.connect(self.apply_filters)
        filter_layout.addWidget(self.refresh_button)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("대상·이벤트·종목·루틴·사유 검색")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(240)
        self.search_edit.textChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.search_edit)
        root.addWidget(filter_group)

        self.event_splitter = QSplitter(Qt.Horizontal)
        self.event_splitter.setChildrenCollapsible(False)
        self.event_list_panel = self._create_event_list_panel()
        self.event_list_panel.setFixedWidth(EVENT_LIST_PANEL_FIXED_WIDTH)
        self.event_detail_panel = self._create_detail_panel()
        self.event_detail_panel.setMinimumWidth(EVENT_DETAIL_PANEL_MINIMUM_WIDTH)
        self.event_splitter.addWidget(self.event_list_panel)
        self.event_splitter.addWidget(self.event_detail_panel)
        self.event_splitter.setStretchFactor(0, 0)
        self.event_splitter.setStretchFactor(1, 1)
        self.event_splitter.setSizes(
            [EVENT_LIST_PANEL_FIXED_WIDTH, EVENT_DETAIL_PANEL_MINIMUM_WIDTH]
        )
        root.addWidget(self.event_splitter, 1)
        margins = root.contentsMargins()
        self.setMinimumWidth(
            EVENT_LIST_PANEL_FIXED_WIDTH
            + EVENT_DETAIL_PANEL_MINIMUM_WIDTH
            + self.event_splitter.handleWidth()
            + margins.left()
            + margins.right()
        )
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
                           ("severity", "중요도"), ("source", "발생 위치"),
                           ("target", "대상"), ("event", "이벤트"),
                           ("result", "결과"), ("reason_code", "사유 코드")):
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
        return (
            parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if parsed is not None
            else str(value or "")
        )

    @staticmethod
    def _field_label(value: object) -> str:
        key = str(value or "").strip()
        if key in FIELD_LABELS:
            return FIELD_LABELS[key]
        leaf = key.rsplit(".", 1)[-1]
        return FIELD_LABELS.get(leaf, key)

    @staticmethod
    def _safe_value(value: object) -> str:
        if value in (None, ""):
            return "없음"
        if isinstance(value, bool):
            return "사용" if value else "미사용"
        if isinstance(value, dict):
            parts = [
                f"{EventRecordPrototypeWindow._field_label(key)}={EventRecordPrototypeWindow._safe_value(item)}"
                for key, item in value.items()
                if not EventRecordPrototypeWindow._sensitive_key(key)
            ]
            return ", ".join(parts) if parts else "-"
        if isinstance(value, (list, tuple)):
            return ", ".join(EventRecordPrototypeWindow._safe_value(item) for item in value) or "없음"
        text = str(value)
        if "Traceback (most recent call last)" in text or ":\\" in text or ":/" in text:
            return "[보안상 숨김]"
        return text[:300] + ("…" if len(text) > 300 else "")

    @classmethod
    def _change_value(cls, field_key: object, value: object) -> str:
        key = str(field_key or "").casefold()
        if isinstance(value, int) and not isinstance(value, bool):
            if any(token in key for token in ("budget", "amount", "price")):
                return f"{value:,}"
        return cls._safe_value(value)

    @staticmethod
    def _sensitive_key(value: object) -> bool:
        key = str(value or "").strip().casefold()
        tokens = {token for token in key.replace("-", "_").replace(".", "_").split("_") if token}
        return (
            key in _SENSITIVE_DETAIL_KEYS
            or bool(tokens & _SENSITIVE_DETAIL_KEYS)
            or "account" in key
            or "계좌" in key
        )

    @classmethod
    def _change_lines(cls, event: dict[str, object]) -> list[str]:
        changes = event.get("changes")
        if not isinstance(changes, list):
            return []
        lines: list[str] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            field_key = change.get("field_key")
            if cls._sensitive_key(field_key):
                continue
            lines.append(
                f"{cls._field_label(field_key)}: "
                f"{cls._change_value(field_key, change.get('before'))} → "
                f"{cls._change_value(field_key, change.get('after'))}"
            )
        return lines

    @classmethod
    def _operator_summary(cls, event: dict[str, object]) -> str:
        details = event.get("details")
        if not isinstance(details, dict):
            return str(event.get("summary") or "")
        title = cls._safe_value(details.get("prompt_title")) if details.get("prompt_title") else "사용자 선택"
        selected = cls._safe_value(details.get("selected_option")) if details.get("selected_option") else "결과 미확인"
        text = f"{title} — {selected}"
        if details.get("input_value") not in (None, "", {}):
            text += f" ({cls._safe_value(details.get('input_value'))})"
        return text

    @classmethod
    def _summary_text(cls, event: dict[str, object]) -> str:
        event_type = str(event.get("event_type") or "")
        if event_type in OPERATOR_EVENT_TYPES:
            return cls._operator_summary(event)
        changes = cls._change_lines(event)
        if changes:
            return changes[0] + (f" 외 {len(changes) - 1}건" if len(changes) > 1 else "")
        summary = str(event.get("summary") or "")
        reason_code = str(event.get("reason_code") or "").strip()
        return f"{summary} ({reason_code})" if reason_code else summary

    @classmethod
    def _detail_text_for_event(cls, event: dict[str, object]) -> str:
        sections: list[str] = []
        summary = str(event.get("summary") or "").strip()
        if summary:
            sections.extend(("[요약]", summary))
        changes = cls._change_lines(event)
        if changes:
            sections.extend(("", "[변경 내용]", *changes))
        details = event.get("details")
        if isinstance(details, dict):
            detail_lines: list[str] = []
            for key, value in details.items():
                if value in (None, "", [], {}) or cls._sensitive_key(key):
                    continue
                if key == "offered_options":
                    continue
                label = DETAIL_LABELS.get(str(key), cls._field_label(key))
                detail_lines.append(f"{label}: {cls._safe_value(value)}")
            if detail_lines:
                heading = "[사용자 선택]" if str(event.get("event_type") or "") in OPERATOR_EVENT_TYPES else "[추가 정보]"
                sections.extend(("", heading, *detail_lines))
        return "\n".join(sections).strip()

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
            "source": str(event.get("source") or "-"),
            "target": event_target_display(event) or "-",
            "event": EVENT_TYPE_LABELS.get(event_type, event_type),
            "result": RESULT_LABELS.get(result, result) if result else "-",
            "reason_code": str(event.get("reason_code") or "-"),
            "summary": EventRecordPrototypeWindow._summary_text(event),
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
            occurred = parse_aware_timestamp(event.get("occurred_at"))
            sort_values = ((occurred.timestamp() if occurred is not None else float("-inf")), str(event.get("category") or ""),
                           SEVERITY_SORT_RANK.get(severity, 99), values[3], values[4], result, values[6])
            for column, value in enumerate(values):
                item = _EventRecordItem(str(value))
                item.setData(Qt.UserRole + 1, sort_values[column])
                if column == 0:
                    item.setData(Qt.UserRole, event)
                if column in {1, 2, 3, 4, 5}:
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
        self.detail_text.setPlainText(self._detail_text_for_event(event))
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
