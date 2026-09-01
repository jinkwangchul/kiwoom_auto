# -*- coding: utf-8 -*-
"""
gui_auto_trade_setting_window.py

자동매매설정창 전용 모듈.
- AutoTradeSettingWindow
- 자동매매설정창에서 직접 쓰는 상태/청산/등록해제 헬퍼
- 자동매매설정창 전용 소형 다이얼로그

주의:
- MainWindow 본체와 StockRegisterWindow 본체는 포함하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from PyQt5 import sip
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect, QSize, QEvent, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)
from event_journal_production import append_production_event, observe_owner_failure_transition
from routine_tree_title_display import (
    tree_title_slot_width,
    tree_title_text,
    tree_title_tooltip,
)
from pnl_ui_refresh import PNL_REFRESH_INTERVAL_MS, project_current_stock_pnl_snapshot
from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from kiwoom_screen_allocator import project_order_default_screen_no

LOGGER = logging.getLogger(__name__)
ROUTINE_INLINE_EDIT_STYLE = """
QLineEdit {
    border: none;
    background: transparent;
    padding: 0px;
    margin: 0px;
}
QLineEdit:focus {
    background: transparent;
}
"""


def _setting_leaf_changes(
    before: object,
    after: object,
    *,
    prefix: str,
) -> list[dict[str, object]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(set(before) | set(after)):
            changes.extend(
                _setting_leaf_changes(
                    before.get(key),
                    after.get(key),
                    prefix=f"{prefix}.{key}" if prefix else str(key),
                )
            )
        return changes
    if before == after:
        return []
    return [{"field_key": prefix, "before": before, "after": after}]


from gui_styles import (
    PLAIN_HEADER_GRID_COLOR_PROPERTY,
    PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY,
    REGISTERED_STOCK_STATUS_GRID_COLOR,
    apply_plain_table_header,
    registered_stock_status_table_stylesheet,
)
from gui_toast import show_toast
from gui_stock_name_tooltip import (
    TOOLTIP_POINT_SIZE,
    install_persistent_stock_name_tooltips,
)
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_stock_data import (
    STOCK_LIBRARY_EMPTY_SOURCE,
    append_base_stock,
    active_routine_for_stock,
    load_stock_library_snapshot,
    stock_runtime_dir_for_routine,
)
from gui_stock_instance_chart_window import open_stock_instance_chart
from gui_order_utils import (
    directional_value_color,
    format_signed_percent,
    pending_order_side_quantities,
    order_value,
    order_status_display,
    order_side_display,
    format_signed_money,
    build_order_rows,
    build_order_timeline_text,
    filter_orders_by_range,
    build_grouped_order_timeline_text,
    settlement_summary_text,
    date_range_for_mode,
    filter_orders_by_dates,
    today_orders,
    build_current_status_rows,
    build_full_trade_export_text,
    numeric_order_value,
    order_datetime,
    parse_order_datetime_value,
    order_sort_key,
    summarize_orders,
)
from gui_schedule_utils import schedule_change_log_text, schedule_status_suffix
from gui_schedule_window import (
    ScheduleOperationDialog,
    ScheduleTradeManagementDialog,
)
from gui_config_utils import (
    default_config,
    ensure_stock_runtime_files,
)
from gui_search_stock_register_dialog import SearchStockRegisterDialog
from gui_auto_trade_utils import auto_trade_unregister_category
from gui_review_utils import (
    build_review_required_item,
    safe_float_value,
)
from gui_routine_assign_utils import (
    build_routine_assign_result_lines,
    build_routine_assign_status_text,
    build_routine_unassign_result_lines,
    build_routine_unassign_status_text,
)
from gui_routine_guard import routine_action_guard_info
from gui_routine_policy import (
    CURRENT_STOCK_RELATION,
    HISTORICAL_STOCK_RELATION,
    RoutineUnassignDecision,
    routine_action_reasons_for_stock,
    classify_routine_assign_targets,
    can_unassign_active_routine_from_stock,
    routine_unassign_decision,
)
from assignment_authorization_service import (
    ASSIGNMENT_INTENT_UNASSIGN,
    execute_assignment_unassign,
    inspect_stock_unregister_availability,
)
from runtime_io import (
    read_json_dict,
    read_orders_data,
    write_json_if_missing,
)
from gui_auto_trade_runtime import (
    all_registered_stock_dirs,
    now_text,
    parse_stock_folder_name,
    get_stock_dirs_in_routine,
)


def _apply_routine_inline_edit_style(editor: QLineEdit, table) -> None:
    editor.setFrame(False)
    editor.setStyleSheet(ROUTINE_INLINE_EDIT_STYLE)
    editor.setFont(table.font())
    editor.setContentsMargins(0, 0, 0, 0)


class _AutoTradeRoutineInstanceNameEdit(QLineEdit):
    def __init__(self, window: "AutoTradeSettingWindow") -> None:
        super().__init__(window.routine_table.viewport())
        self.window = window

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.window.finish_routine_instance_name_edit(save=True)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.window.finish_routine_instance_name_edit(save=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.window.finish_routine_instance_name_edit(save=True)
        super().focusOutEvent(event)


def _snapshot_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


class _NumericSnapshotTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = _snapshot_number(self.data(Qt.UserRole))
        right = _snapshot_number(other.data(Qt.UserRole))
        if left is None:
            return right is None and self.text() < other.text()
        if right is None:
            return True
        return left < right


class InstanceStockSearchRegisterDialog(QDialog):
    """Instance-scoped stock search and one-stock registration window."""

    RESULT_EVIDENCE_ROLE = Qt.UserRole + 1
    CODE_COLUMN = 0
    NAME_COLUMN = 1
    MARKET_COLUMN = 2
    REGISTRATION_STATUS_COLUMN = 3
    CATEGORY_COLUMN = REGISTRATION_STATUS_COLUMN
    INSTRUMENT_CLASSIFICATION_COLUMN = 4
    AFTER_MARKET_COLUMN = 5
    CURRENT_PRICE_COLUMN = 6
    CHANGE_RATE_COLUMN = 7
    EXECUTION_STRENGTH_COLUMN = 8
    PREVIOUS_DAY_VOLUME_RATE_COLUMN = 9
    TRADING_VALUE_COLUMN = 10
    VOLUME_COLUMN = 11
    MARKET_CAP_COLUMN = 12
    STOCK_STATUS_COLUMN = 13
    NUMERIC_SNAPSHOT_COLUMNS = frozenset(
        {
            CURRENT_PRICE_COLUMN,
            CHANGE_RATE_COLUMN,
            EXECUTION_STRENGTH_COLUMN,
            PREVIOUS_DAY_VOLUME_RATE_COLUMN,
            TRADING_VALUE_COLUMN,
            VOLUME_COLUMN,
            MARKET_CAP_COLUMN,
        }
    )
    MARKET_TEXT_COLORS = {
        "KOSPI": "#1E3A5F",
        "코스닥": "#6B3E2E",
    }
    BASE_DIALOG_WIDTH = 520
    STOCK_NAME_DISPLAY_CHARACTERS = 14
    SEARCH_DISPLAY_CHARACTERS = 12
    ROW_NUMBER_HORIZONTAL_PADDING = 1
    TABLE_SEPARATOR_COLOR = "#EBEBEB"
    RANKING_BADGE_HORIZONTAL_PADDING = 3
    REGISTRATION_BADGE_INACTIVE_COLOR = "#4B5563"
    RANKING_HIGHLIGHT_BACKGROUND_COLOR = "#EFF6FF"
    RANKING_BADGES = (
        ("VOLUME_TOP", "거래량"),
        ("VALUE_TOP", "거래대금"),
        ("RISE_TOP", "급상승"),
        ("FALL_TOP", "급하락"),
    )
    RANKING_HIGHLIGHT_COLUMNS = {
        "VOLUME_TOP": VOLUME_COLUMN,
        "VALUE_TOP": TRADING_VALUE_COLUMN,
        "RISE_TOP": CHANGE_RATE_COLUMN,
        "FALL_TOP": CHANGE_RATE_COLUMN,
    }
    STOCK_STATUS_SINGLE_VALUES = (
        "정상",
        "관리",
        "관리종목",
        "거래정지",
        "증거금100%",
        "감리종목",
        "투자유의종목",
        "담보대출",
        "액면분할",
        "신용가능",
        "투자주의",
        "투자경고",
        "투자위험",
        "투자주의환기",
        "투자주의환기종목",
    )
    closed = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        instance_metadata: dict[str, object] | None = None,
        stock_source: str = STOCK_LIBRARY_EMPTY_SOURCE,
        kiwoom_api: object | None = None,
    ) -> None:
        super().__init__(parent)
        configure_persistent_feature_window(self, parent)
        owner = persistent_feature_owner(self)
        self.kiwoom_api = (
            kiwoom_api
            if kiwoom_api is not None
            else getattr(owner, "kiwoom_api", None)
        )
        self.instance_metadata = dict(instance_metadata or {})
        # Search is intentionally local-only. Server Master collection is owned by
        # the login-session sync service and never by dialog open/textChanged.
        self.stock_source = str(stock_source or STOCK_LIBRARY_EMPTY_SOURCE)
        self.setWindowTitle(self._window_title())
        self.resize(self.BASE_DIALOG_WIDTH, 420)

        self.search_input = QLineEdit(self)
        self.search_input.setObjectName("instanceStockSearchInput")
        self.btn_search = QPushButton("검색", self)
        self.btn_search.setObjectName("instanceStockSearchButton")
        self.btn_search.setAutoDefault(False)
        self.btn_search.setDefault(False)
        self.general_stock_button = QPushButton("일반종목", self)
        self.general_stock_button.setObjectName("instanceStockGeneralVisibilityButton")
        self.general_stock_button.setAutoDefault(False)
        self.general_stock_button.setDefault(False)
        self.general_stock_button.setCheckable(True)
        self.general_stock_button.setChecked(False)
        self.ranking_separator_label = QLabel("|", self)
        self.ranking_separator_label.setObjectName("instanceStockRankingSeparatorLabel")
        self.ranking_title_label = QLabel("TOP100 :", self)
        self.ranking_title_label.setObjectName("instanceStockRankingTitleLabel")
        self.ranking_title_label.setStyleSheet(
            "QLabel#instanceStockRankingTitleLabel {"
            " color: #111827;"
            " font-weight: 700;"
            "}"
        )
        self.ranking_title_label.setSizePolicy(
            QSizePolicy.Fixed,
            QSizePolicy.Preferred,
        )
        self.ranking_title_label.setFixedWidth(
            self.ranking_title_label.sizeHint().width()
        )
        self.ranking_buttons: dict[str, QPushButton] = {}
        for source, text in self.RANKING_BADGES:
            button = QPushButton(text, self)
            button.setObjectName(f"instanceStockRanking{source.title().replace('_', '')}")
            button.setAutoDefault(False)
            button.setDefault(False)
            self.ranking_buttons[source] = button
        self.result_table = QTableWidget(self)
        self.result_table.setObjectName("instanceStockSearchResultTable")
        self.btn_register = QPushButton("등록", self)
        self.btn_register.setObjectName("instanceStockRegisterButton")
        self.btn_register.setAutoDefault(False)
        self.btn_register.setDefault(False)
        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setObjectName("instanceStockRegisterCloseButton")
        self.btn_close.setAutoDefault(False)
        self.btn_close.setDefault(False)
        self._result_sort_column = -1
        self._result_sort_order = Qt.AscendingOrder
        self._result_source = "SEARCH"
        self._active_ranking_source = ""
        self._search_generation = 0
        self._search_stock_codes: set[str] = set()
        self._market_snapshot_by_code: dict[str, dict[str, object]] = {}
        self._market_snapshot_request_result: dict[str, object] = {}
        self._last_market_snapshot_result: dict[str, object] = {}
        self._ranking_request_result: dict[str, object] = {}
        self._last_ranking_result: dict[str, object] = {}

        self._setup_ui()
        self.search_input.returnPressed.connect(
            lambda: self.search_stocks(notify_empty=True)
        )
        self.btn_search.clicked.connect(lambda: self.search_stocks(notify_empty=True))
        self.general_stock_button.toggled.connect(
            self._on_general_stock_visibility_toggled
        )
        for source, button in self.ranking_buttons.items():
            button.clicked.connect(
                lambda _checked=False, ranking_source=source: (
                    self.request_stock_ranking(ranking_source)
                )
            )
        self.result_table.itemDoubleClicked.connect(self.on_result_item_double_clicked)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(
            self.on_result_table_context_menu
        )
        self.result_table.itemSelectionChanged.connect(
            self._update_register_button_enabled
        )
        self.result_table.horizontalHeader().sectionClicked.connect(
            self.on_result_header_clicked
        )
        self.btn_register.clicked.connect(
            lambda _checked=False: self.register_selected_result_rows()
        )
        self.btn_close.clicked.connect(self.close)
        sync_service = getattr(owner, "stock_library_sync_service", None)
        sync_finished = getattr(sync_service, "sync_finished", None)
        if sync_finished is not None:
            sync_finished.connect(self._on_stock_library_sync_finished)
        self.search_stocks()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

    def _on_stock_library_sync_finished(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        if (
            str(payload.get("state", "") or "") == "SUCCEEDED"
            and self._result_source == "SEARCH"
        ):
            self.search_stocks()

    def _window_title(self) -> str:
        for key in ("instance_name", "display_name", "routine_name"):
            display_name = str(self.instance_metadata.get(key, "") or "").strip()
            if display_name:
                return f"{display_name} - 종목등록"
        return "종목등록"

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)
        self._update_ranking_badge_styles()
        self._update_general_stock_badge_style()
        ranking_width = (
            next(iter(self.ranking_buttons.values()))
            .fontMetrics()
            .horizontalAdvance("거래대금")
            + (self.RANKING_BADGE_HORIZONTAL_PADDING * 2)
            + 2
        )
        search_layout = QHBoxLayout()
        search_label = QLabel("검색어", self)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.btn_search)
        search_layout.addStretch(1)
        general_width = (
            self.general_stock_button.fontMetrics().horizontalAdvance("일반종목")
            + (self.RANKING_BADGE_HORIZONTAL_PADDING * 2)
            + 2
        )
        self.general_stock_button.setFixedWidth(general_width)
        self.general_stock_button.setFixedHeight(AUTO_TRADE_SETTING_BADGE_HEIGHT)
        self.general_stock_button.setCursor(Qt.PointingHandCursor)
        search_layout.addWidget(self.general_stock_button, 0, Qt.AlignBottom)
        search_layout.addSpacing(6)
        search_layout.addWidget(self.ranking_separator_label, 0, Qt.AlignBottom)
        search_layout.addSpacing(6)
        search_layout.addWidget(self.ranking_title_label, 0, Qt.AlignBottom)
        for index, (source, _text) in enumerate(self.RANKING_BADGES):
            if index:
                search_layout.addSpacing(4)
            button = self.ranking_buttons[source]
            button.setFixedWidth(ranking_width)
            button.setFixedHeight(AUTO_TRADE_SETTING_BADGE_HEIGHT)
            button.setCursor(Qt.PointingHandCursor)
            search_layout.addWidget(button, 0, Qt.AlignBottom)
        search_margin = self.search_input.style().pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.search_input,
        ) + 1
        self.search_input.setFixedWidth(
            self.search_input.fontMetrics().horizontalAdvance(
                "한" * self.SEARCH_DISPLAY_CHARACTERS
            )
            + (search_margin * 2)
        )
        self.search_input.setMinimumHeight(self.search_input.sizeHint().height() + 6)
        self.search_input.setStyleSheet(
            "QLineEdit#instanceStockSearchInput {"
            "border: none;"
            "padding: 3px 4px;"
            "background: #FFFFFF;"
            "}"
            "QLineEdit#instanceStockSearchInput:focus {"
            "border: none;"
            "}"
        )

        self.result_table.setColumnCount(14)
        self.result_table.setHorizontalHeaderLabels(
            [
                "종목코드",
                "종목명",
                "시장",
                "등록상태",
                "분류",
                "비고",
                "현재주가",
                "등락률",
                "체결강도",
                "전일대비",
                "거래대금",
                "거래량",
                "시총",
                "상태",
            ]
        )
        header = self.result_table.horizontalHeader()
        header.setObjectName("instanceStockSearchHorizontalHeader")
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        vertical_header = self.result_table.verticalHeader()
        vertical_header.setObjectName("instanceStockSearchVerticalHeader")
        vertical_header.setSectionResizeMode(QHeaderView.Fixed)
        vertical_header.setSectionsMovable(False)
        vertical_header.setDefaultAlignment(Qt.AlignCenter)
        item_margin = self.result_table.style().pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.result_table,
        ) + 1
        section_border_width = self.result_table.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table,
        ) if self.result_table.showGrid() else 0
        name_column_width = (
            self.result_table.fontMetrics().horizontalAdvance(
                "한" * self.STOCK_NAME_DISPLAY_CHARACTERS
            )
            + (item_margin * 2)
            + section_border_width
        )
        code_column_width = self._symmetric_text_column_width("0000000000")
        code_text_width = self.result_table.fontMetrics().horizontalAdvance("000000")
        code_horizontal_margin = max(
            item_margin,
            (code_column_width - section_border_width - code_text_width) // 2,
        )
        market_column_width = self._text_column_width_with_margin(
            code_horizontal_margin,
            "시장",
            "KOSPI",
            "코스닥",
        )
        category_column_width = self._text_column_width_with_margin(
            code_horizontal_margin,
            "등록상태",
            "등록대기",
            "검토관리",
        )
        instrument_classification_width = self._text_column_width_with_margin(
            code_horizontal_margin,
            "분류",
            "일반종목",
            "SPAC",
            "REIT",
        )
        remarks_column_width = self._text_column_width_with_margin(
            code_horizontal_margin,
            "비고",
            "NXT",
        )
        self.result_table.setColumnWidth(self.CODE_COLUMN, code_column_width)
        self.result_table.setColumnWidth(self.NAME_COLUMN, name_column_width)
        self.result_table.setColumnWidth(self.MARKET_COLUMN, market_column_width)
        self.result_table.setColumnWidth(self.CATEGORY_COLUMN, category_column_width)
        self.result_table.setColumnWidth(
            self.INSTRUMENT_CLASSIFICATION_COLUMN,
            instrument_classification_width,
        )
        self.result_table.setColumnWidth(self.AFTER_MARKET_COLUMN, remarks_column_width)
        numeric_column_samples = {
            self.CURRENT_PRICE_COLUMN: ("현재주가", "999,999,999"),
            self.CHANGE_RATE_COLUMN: ("등락률", "+999.99%"),
            self.EXECUTION_STRENGTH_COLUMN: ("체결강도", "999.99"),
            self.PREVIOUS_DAY_VOLUME_RATE_COLUMN: ("전일대비", "+999.99%"),
            self.TRADING_VALUE_COLUMN: ("거래대금", "99,999,999억", "9,999만원"),
            self.VOLUME_COLUMN: ("거래량", "99,999,999주", "999.9억주"),
            self.MARKET_CAP_COLUMN: ("시총", "99,999,999억"),
        }
        for column, samples in numeric_column_samples.items():
            self.result_table.setColumnWidth(
                column,
                self._text_column_width(*samples),
            )
        self.result_table.setColumnWidth(
            self.STOCK_STATUS_COLUMN,
            self._text_column_width("상태", *self.STOCK_STATUS_SINGLE_VALUES)
            + section_border_width,
        )
        for column in range(self.result_table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            header_item = self.result_table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setBackground(QBrush(QColor("#FFFFFF")))
        self._stock_name_clip_delegate = ClippedTextItemDelegate(
            self.result_table,
            selected_text_color=QColor("#111827"),
        )
        self.result_table.setItemDelegateForColumn(1, self._stock_name_clip_delegate)
        self._market_text_delegate = SelectedTextReadableDelegate(self.result_table)
        self.result_table.setItemDelegateForColumn(
            self.MARKET_COLUMN,
            self._market_text_delegate,
        )
        self._stock_name_tooltip_filter = install_persistent_stock_name_tooltips(
            self.result_table,
            {self.NAME_COLUMN},
        )
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.setSortingEnabled(False)
        self.result_table.horizontalHeader().setSortIndicatorShown(False)
        self.result_table.setAlternatingRowColors(False)
        self.result_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        grid_line_color = self.TABLE_SEPARATOR_COLOR
        self.result_table.setStyleSheet(
            f"""
            QTableWidget#instanceStockSearchResultTable {{
                gridline-color: {grid_line_color};
                border: 1px solid {grid_line_color};
                background: #FFFFFF;
            }}
            QTableWidget#instanceStockSearchResultTable::viewport {{
                border: none;
            }}
            QTableWidget::item {{
                padding-left: {item_margin}px;
                padding-right: {item_margin}px;
            }}
            QHeaderView#instanceStockSearchHorizontalHeader::section {{
                padding-left: {item_margin}px;
                padding-right: {item_margin}px;
                background: #FFFFFF;
                border: none;
                border-right: 1px solid {grid_line_color};
                border-bottom: 1px solid {grid_line_color};
            }}
            QHeaderView#instanceStockSearchHorizontalHeader {{
                background: #FFFFFF;
                border: none;
            }}
            QHeaderView#instanceStockSearchVerticalHeader::section {{
                padding-left: {self.ROW_NUMBER_HORIZONTAL_PADDING}px;
                padding-right: {self.ROW_NUMBER_HORIZONTAL_PADDING}px;
                background: #FFFFFF;
                border: none;
                border-right: 1px solid {grid_line_color};
                border-bottom: 1px solid {grid_line_color};
            }}
            QHeaderView#instanceStockSearchVerticalHeader {{
                background: #FFFFFF;
                border: none;
            }}
            QTableCornerButton::section {{
                border: none;
                border-right: 1px solid {grid_line_color};
                border-bottom: 1px solid {grid_line_color};
            }}
            """
            + """
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:selected:active {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:selected:!active {
                background: #dbeafe;
                color: #111827;
            }
            """
            + f"QToolTip {{ font-size: {TOOLTIP_POINT_SIZE}pt; }}"
        )
        header.setStyleSheet(
            f"QHeaderView::section {{"
            f"background: #FFFFFF;"
            f"border: none;"
            f"border-right: 1px solid {grid_line_color};"
            f"border-bottom: 1px solid {grid_line_color};"
            f"}}"
        )
        self.result_table.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { border: none; margin: 0px; padding: 0px; }"
        )

        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.result_table)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 1, 0, 0)
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_register)
        button_layout.addWidget(self.btn_close)
        main_layout.addLayout(button_layout)
        self._normalize_row_number_header_width()
        self._normalize_dialog_width_to_result_table()
        self._update_register_button_enabled()

    def _update_ranking_badge_styles(self) -> None:
        active_source = str(
            getattr(self, "_active_ranking_source", "") or ""
        ).strip().upper()
        for source, button in self.ranking_buttons.items():
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if source == active_source
                else self.REGISTRATION_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=color,
                )
                + (
                    "QPushButton, QPushButton:hover {"
                    f" padding-left: {self.RANKING_BADGE_HORIZONTAL_PADDING}px;"
                    f" padding-right: {self.RANKING_BADGE_HORIZONTAL_PADDING}px;"
                    "}"
                )
            )

    def _update_general_stock_badge_style(self) -> None:
        color = (
            AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            if self.general_stock_button.isChecked()
            else self.REGISTRATION_BADGE_INACTIVE_COLOR
        )
        self.general_stock_button.setStyleSheet(
            auto_trade_setting_badge_stylesheet(
                "QPushButton",
                text_color=color,
                border_color=color,
            )
            + (
                "QPushButton, QPushButton:hover {"
                f" padding-left: {self.RANKING_BADGE_HORIZONTAL_PADDING}px;"
                f" padding-right: {self.RANKING_BADGE_HORIZONTAL_PADDING}px;"
                "}"
            )
        )

    def _on_general_stock_visibility_toggled(self, _checked: bool) -> None:
        self._update_general_stock_badge_style()
        self._apply_general_stock_visibility()

    def _apply_general_stock_visibility(self) -> None:
        general_only = self.general_stock_button.isChecked()
        selection_model = self.result_table.selectionModel()
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, self.INSTRUMENT_CLASSIFICATION_COLUMN)
            classification = str(item.data(Qt.UserRole) or "") if item is not None else ""
            hide_row = general_only and classification != "일반종목"
            if hide_row and selection_model is not None:
                selection_model.select(
                    self.result_table.model().index(row, self.CODE_COLUMN),
                    QItemSelectionModel.Deselect | QItemSelectionModel.Rows,
                )
            self.result_table.setRowHidden(row, hide_row)
        self._update_register_button_enabled()

    @staticmethod
    def _instrument_classification_display_text(value: object) -> str:
        classification = str(value or "-").strip() or "-"
        return "일반" if classification == "일반종목" else classification

    def _update_ranking_column_highlight(self) -> None:
        active_source = str(
            getattr(self, "_active_ranking_source", "") or ""
        ).strip().upper()
        active_column = self.RANKING_HIGHLIGHT_COLUMNS.get(active_source, -1)
        highlight_color = QColor(AUTO_TRADE_SETTING_AMBER_TEXT_COLOR)
        highlight_background = QColor(self.RANKING_HIGHLIGHT_BACKGROUND_COLOR)
        ranking_columns = set(self.RANKING_HIGHLIGHT_COLUMNS.values())
        for column in ranking_columns:
            foreground = (
                QBrush(highlight_color) if column == active_column else QBrush()
            )
            header_background = QBrush(QColor("#FFFFFF"))
            cell_background = (
                QBrush(highlight_background) if column == active_column else QBrush()
            )
            header_item = self.result_table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setForeground(foreground)
                header_item.setBackground(header_background)
            for row in range(self.result_table.rowCount()):
                item = self.result_table.item(row, column)
                if item is not None:
                    item.setForeground(foreground)
                    item.setBackground(cell_background)

    def _text_column_width(self, *samples: str) -> int:
        style = self.result_table.style()
        item_margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.result_table,
        ) + 1
        header_margin = style.pixelMetric(
            QStyle.PM_HeaderMargin,
            None,
            self.result_table.horizontalHeader(),
        )
        sort_indicator_width = style.pixelMetric(
            QStyle.PM_SmallIconSize,
            None,
            self.result_table.horizontalHeader(),
        )
        metrics = self.result_table.fontMetrics()
        item_width = max(metrics.horizontalAdvance(sample) for sample in samples) + (item_margin * 2)
        header_width = metrics.horizontalAdvance(samples[0]) + (header_margin * 2) + sort_indicator_width
        return max(item_width, header_width)

    def _symmetric_text_column_width(self, *samples: str) -> int:
        item_margin = self.result_table.style().pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.result_table,
        ) + 1
        section_border_width = self.result_table.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table,
        ) if self.result_table.showGrid() else 0
        text_width = max(
            self.result_table.fontMetrics().horizontalAdvance(sample)
            for sample in samples
        )
        return text_width + (item_margin * 2) + section_border_width

    def _text_column_width_with_margin(
        self,
        horizontal_margin: int,
        *samples: str,
    ) -> int:
        section_border_width = self.result_table.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table,
        ) if self.result_table.showGrid() else 0
        text_width = max(
            self.result_table.fontMetrics().horizontalAdvance(sample)
            for sample in samples
        )
        return text_width + (max(0, horizontal_margin) * 2) + section_border_width

    def _normalize_row_number_header_width(self) -> None:
        text_width = self.result_table.verticalHeader().fontMetrics().horizontalAdvance("999")
        section_border_width = self.result_table.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table.verticalHeader(),
        )
        fixed_width = (
            text_width
            + (self.ROW_NUMBER_HORIZONTAL_PADDING * 2)
            + section_border_width
        )
        self._fixed_row_number_header_width = fixed_width
        self.result_table.verticalHeader().setFixedWidth(fixed_width)

    def _result_table_required_width(self) -> int:
        section_width = sum(
            self.result_table.horizontalHeader().sectionSize(column)
            for column in range(self.result_table.columnCount())
        )
        vertical_header = self.result_table.verticalHeader()
        vertical_header_width = vertical_header.width()
        return (
            section_width
            + vertical_header_width
            + self.result_table.verticalScrollBar().sizeHint().width()
            + (self.result_table.frameWidth() * 2)
        )

    def _normalize_dialog_width_to_result_table(self) -> None:
        fixed_width = getattr(self, "_fixed_result_dialog_width", None)
        if isinstance(fixed_width, int) and fixed_width > 0:
            self.setFixedWidth(fixed_width)
            return
        margins = self.layout().contentsMargins()
        required_width = (
            self._result_table_required_width()
            + margins.left()
            + margins.right()
        )
        self._fixed_result_dialog_width = required_width
        self.setFixedWidth(required_width)

    def _update_register_button_enabled(self) -> None:
        has_selection = bool(self.result_table.selectionModel().selectedRows())
        self.btn_register.setEnabled(has_selection)

    def search_stocks(self, *_args, notify_empty: bool = False) -> None:
        self._search_generation += 1
        search_generation = self._search_generation
        self._result_source = "SEARCH"
        self._active_ranking_source = ""
        self._update_ranking_badge_styles()
        self._update_ranking_column_highlight()
        self._search_stock_codes = set()
        self._market_snapshot_by_code = {}
        self._market_snapshot_request_result = {}
        self._last_market_snapshot_result = {}
        self._ranking_request_result = {}
        self._last_ranking_result = {}
        library_snapshot = load_stock_library_snapshot()
        self.stock_source = library_snapshot.source
        library = [dict(item) for item in library_snapshot.records]
        keyword_text = self.search_input.text().strip().lower()
        keywords = [
            part.strip()
            for part in re.split(r"[,，、]+", keyword_text)
            if part.strip()
        ]
        if not keywords:
            self.result_table.setRowCount(0)
            self._update_register_button_enabled()
            return

        matches: list[dict[str, object]] = []
        seen_codes: set[str] = set()
        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            searchable_values = [
                str(stock.get("code", "") or "").strip().lower(),
                str(stock.get("name", "") or "").strip().lower(),
                str(stock.get("chosung", "") or "").strip().lower(),
            ]
            return any(keyword in value for value in searchable_values)

        for keyword in keywords:
            for stock in library:
                code = str(stock.get("code", "") or "").strip()
                name = str(stock.get("name", "") or "").strip()
                if not code or not name or code in seen_codes:
                    continue
                if stock_matches(stock, keyword):
                    matches.append(
                        {
                            "code": code,
                            "name": name,
                            "market": str(stock.get("market", "") or "").strip(),
                            "nxt_available": stock.get("nxt_available"),
                            "status": stock.get("status"),
                            "classification": stock.get("classification"),
                        }
                    )
                    seen_codes.add(code)

        self._populate_result_table(matches, source="SEARCH")
        self._request_market_snapshot(search_generation)
        if notify_empty and not matches:
            self._toast("검색 결과가 없습니다.")

    def _populate_result_table(
        self,
        stocks: list[dict[str, object]],
        *,
        source: str,
    ) -> None:
        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(stocks))
        self._search_stock_codes = set()
        for row, stock in enumerate(stocks):
            full_status_text = self._stock_status_full_text(stock.get("status"))
            status_display_text = self._elided_stock_status_text(full_status_text)
            instrument_classification = (
                str(stock.get("classification", "") or "-").strip() or "-"
            )
            values = (
                stock.get("code", ""),
                stock.get("name", ""),
                self._market_display_text(stock.get("market", "")),
                self._classification_text(
                    str(stock.get("code", "") or ""),
                    str(stock.get("name", "") or ""),
                ),
                self._instrument_classification_display_text(
                    instrument_classification
                ),
                "NXT" if stock.get("nxt_available") is True else "",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                status_display_text,
            )
            for col, value in enumerate(values):
                item = (
                    _NumericSnapshotTableWidgetItem(str(value))
                    if col in self.NUMERIC_SNAPSHOT_COLUMNS
                    else QTableWidgetItem(str(value))
                )
                item.setTextAlignment(
                    self._stock_name_alignment(value)
                    if col == self.NAME_COLUMN
                    else Qt.AlignCenter
                )
                if col == self.NAME_COLUMN:
                    item.setToolTip(self._stock_name_tooltip(value))
                elif col == self.MARKET_COLUMN:
                    color = self.MARKET_TEXT_COLORS.get(str(value or ""))
                    if color:
                        item.setForeground(QBrush(QColor(color)))
                elif col == self.STOCK_STATUS_COLUMN:
                    if full_status_text != "-":
                        item.setToolTip(full_status_text)
                item.setData(
                    Qt.UserRole,
                    (
                        full_status_text
                        if col == self.STOCK_STATUS_COLUMN
                        else instrument_classification
                        if col == self.INSTRUMENT_CLASSIFICATION_COLUMN
                        else value
                    ),
                )
                item.setData(self.RESULT_EVIDENCE_ROLE, source)
                self.result_table.setItem(row, col, item)
            code = normalize_stock_code(stock.get("code", ""))
            if code:
                self._search_stock_codes.add(code)
            ranking_snapshot = stock.get("ranking_snapshot")
            if isinstance(ranking_snapshot, dict):
                self._market_snapshot_by_code[code] = dict(ranking_snapshot)
                self._apply_market_snapshot_to_row(row, dict(ranking_snapshot))
        self._apply_result_sort()
        self._update_register_button_enabled()
        self._update_ranking_column_highlight()
        self._apply_general_stock_visibility()

    def request_stock_ranking(self, source: str) -> None:
        clean_source = str(source or "").strip().upper()
        if clean_source not in {item[0] for item in self.RANKING_BADGES}:
            return
        self._search_generation += 1
        request_generation = self._search_generation
        self._result_source = clean_source
        self._active_ranking_source = ""
        self._update_ranking_badge_styles()
        self._update_ranking_column_highlight()
        self._search_stock_codes = set()
        self._market_snapshot_by_code = {}
        self._market_snapshot_request_result = {}
        self._last_market_snapshot_result = {}
        self._ranking_request_result = {}
        self._last_ranking_result = {}
        self.result_table.setRowCount(0)
        self._update_register_button_enabled()

        request = getattr(self.kiwoom_api, "request_stock_ranking_snapshot", None)
        if not callable(request):
            self._ranking_request_result = {
                "ok": False,
                "error": "stock ranking snapshot API is unavailable",
            }
            return
        try:
            result = request(
                clean_source,
                callback=lambda payload, generation=request_generation, expected=clean_source: (
                    self._on_stock_ranking_result(generation, expected, payload)
                ),
            )
        except Exception as exc:
            self._ranking_request_result = {
                "ok": False,
                "error": str(exc),
            }
            return
        self._ranking_request_result = (
            dict(result) if isinstance(result, dict) else {"ok": False}
        )

    def _on_stock_ranking_result(
        self,
        request_generation: int,
        expected_source: str,
        result: object,
    ) -> None:
        if (
            request_generation != self._search_generation
            or expected_source != self._result_source
            or sip.isdeleted(self)
        ):
            return
        payload = dict(result) if isinstance(result, dict) else {}
        self._last_ranking_result = payload
        if payload.get("ok") is not True:
            return
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return

        library_snapshot = load_stock_library_snapshot()
        self.stock_source = library_snapshot.source
        library_by_code = {
            normalize_stock_code(item.get("code", "")): dict(item)
            for item in library_snapshot.records
            if normalize_stock_code(item.get("code", ""))
        }
        stocks: list[dict[str, object]] = []
        seen_codes: set[str] = set()
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            code = normalize_stock_code(raw_row.get("stock_code", ""))
            if not code or code in seen_codes:
                continue
            library_stock = library_by_code.get(code, {})
            name = str(
                raw_row.get("stock_name")
                or library_stock.get("name")
                or ""
            ).strip()
            if not name:
                continue
            stocks.append(
                {
                    "code": code,
                    "name": name,
                    "market": library_stock.get("market", ""),
                    "nxt_available": library_stock.get("nxt_available"),
                    "status": library_stock.get("status"),
                    "classification": library_stock.get("classification"),
                    "ranking_snapshot": dict(raw_row),
                }
            )
            seen_codes.add(code)
            if len(stocks) >= 100:
                break

        self._populate_result_table(stocks, source=expected_source)
        if stocks:
            self._active_ranking_source = expected_source
            self._update_ranking_badge_styles()
            self._update_ranking_column_highlight()
        self._request_market_snapshot(request_generation)

    def _request_market_snapshot(self, search_generation: int) -> None:
        if not self._search_stock_codes:
            return
        request = getattr(self.kiwoom_api, "request_initial_market_snapshot", None)
        if not callable(request):
            return
        try:
            result = request(
                tuple(sorted(self._search_stock_codes)),
                callback=lambda payload, generation=search_generation: (
                    self._on_market_snapshot_result(generation, payload)
                ),
            )
        except Exception as exc:
            self._market_snapshot_request_result = {
                "ok": False,
                "error": str(exc),
            }
            return
        self._market_snapshot_request_result = (
            dict(result) if isinstance(result, dict) else {"ok": False}
        )

    def _on_market_snapshot_result(
        self,
        search_generation: int,
        result: object,
    ) -> None:
        if search_generation != self._search_generation or sip.isdeleted(self):
            return
        payload = dict(result) if isinstance(result, dict) else {}
        self._last_market_snapshot_result = payload
        if payload.get("ok") is not True:
            return
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return

        selected_codes = self._selected_result_stock_codes()
        self.result_table.setSortingEnabled(False)
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            code = normalize_stock_code(raw_row.get("stock_code", ""))
            if code not in self._search_stock_codes:
                continue
            row = self._find_result_row_by_stock_code(code)
            if row < 0:
                continue
            snapshot = dict(raw_row)
            self._market_snapshot_by_code[code] = snapshot
            self._apply_market_snapshot_to_row(row, snapshot)
        self._apply_result_sort()
        self._restore_result_selection(selected_codes)

    def _apply_market_snapshot_to_row(
        self,
        row: int,
        snapshot: dict[str, object],
    ) -> None:
        values = {
            self.CURRENT_PRICE_COLUMN: (
                snapshot.get("current_price"),
                self._format_snapshot_integer(snapshot.get("current_price")),
            ),
            self.CHANGE_RATE_COLUMN: (
                snapshot.get("change_rate"),
                self._format_snapshot_percent(snapshot.get("change_rate")),
            ),
            self.EXECUTION_STRENGTH_COLUMN: (
                snapshot.get("execution_strength"),
                self._format_snapshot_decimal(snapshot.get("execution_strength")),
            ),
            self.PREVIOUS_DAY_VOLUME_RATE_COLUMN: (
                snapshot.get("previous_day_volume_rate"),
                self._format_snapshot_percent(
                    snapshot.get("previous_day_volume_rate")
                ),
            ),
            self.TRADING_VALUE_COLUMN: (
                snapshot.get("cumulative_trading_value"),
                self._format_snapshot_trading_value(
                    snapshot.get("cumulative_trading_value")
                ),
            ),
            self.VOLUME_COLUMN: (
                snapshot.get("cumulative_volume"),
                self._format_snapshot_volume(snapshot.get("cumulative_volume")),
            ),
            self.MARKET_CAP_COLUMN: (
                snapshot.get("market_capitalization"),
                self._format_snapshot_market_cap(
                    snapshot.get("market_capitalization")
                ),
            ),
        }
        for column, (sort_value, display_text) in values.items():
            item = self.result_table.item(row, column)
            if item is None:
                item = _NumericSnapshotTableWidgetItem()
                self.result_table.setItem(row, column, item)
            item.setText(display_text)
            item.setData(Qt.UserRole, sort_value)
            item.setTextAlignment(Qt.AlignCenter)

    @staticmethod
    def _snapshot_number(value: object) -> int | float | None:
        return _snapshot_number(value)

    @classmethod
    def _format_snapshot_integer(cls, value: object) -> str:
        number = cls._snapshot_number(value)
        return "-" if number is None else f"{int(number):,}"

    @staticmethod
    def _compact_one_decimal(value: int | float) -> str:
        return f"{float(value):.1f}".rstrip("0").rstrip(".")

    @classmethod
    def _format_snapshot_volume(cls, value: object) -> str:
        """Format OPTKWFID FID 13, whose raw unit is one share."""
        number = cls._snapshot_number(value)
        if number is None:
            return "-"
        if number >= 100_000_000:
            return f"{cls._compact_one_decimal(number / 100_000_000)}억주"
        return f"{int(number):,}주"

    @classmethod
    def _format_snapshot_trading_value(cls, value: object) -> str:
        """Format OPTKWFID FID 14, whose raw unit is one million won."""
        number = cls._snapshot_number(value)
        if number is None:
            return "-"
        if number >= 100:
            return f"{int(number // 100):,}억"
        manwon = number * 100
        return f"{int(manwon):,}만원"

    @classmethod
    def _format_snapshot_market_cap(cls, value: object) -> str:
        """Format OPTKWFID FID 311, whose raw unit is one hundred million won."""
        number = cls._snapshot_number(value)
        if number is None:
            return "-"
        if number >= 1:
            return f"{int(number):,}억"
        return f"{int(number * 10_000):,}만원"

    @classmethod
    def _format_snapshot_decimal(cls, value: object) -> str:
        number = cls._snapshot_number(value)
        return "-" if number is None else f"{float(number):.2f}"

    @classmethod
    def _format_snapshot_percent(cls, value: object) -> str:
        number = cls._snapshot_number(value)
        return "-" if number is None else format_signed_percent(number)

    @staticmethod
    def _stock_status_full_text(value: object) -> str:
        if isinstance(value, (list, tuple, set, frozenset)):
            parts = [str(item or "").strip() for item in value]
            text = " | ".join(part for part in parts if part)
        else:
            text = str(value or "").strip()
        return text if text else "-"

    def _stock_status_text_available_width(self) -> int:
        item_margin = self.result_table.style().pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.result_table,
        ) + 1
        section_border_width = self.result_table.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table,
        ) if self.result_table.showGrid() else 0
        return max(
            0,
            self.result_table.columnWidth(self.STOCK_STATUS_COLUMN)
            - (item_margin * 2)
            - section_border_width,
        )

    def _elided_stock_status_text(self, full_text: str) -> str:
        text = str(full_text or "-")
        metrics = self.result_table.fontMetrics()
        available_width = self._stock_status_text_available_width()
        if metrics.horizontalAdvance(text) <= available_width:
            return text
        suffix = "..."
        prefix = text
        while prefix and metrics.horizontalAdvance(prefix + suffix) > available_width:
            prefix = prefix[:-1]
        return prefix + suffix

    @staticmethod
    def _market_display_text(market: object) -> str:
        return {
            "KOSPI": "KOSPI",
            "KOSDAQ": "코스닥",
        }.get(str(market or "").strip().upper(), "")

    def _stock_name_tooltip(self, stock_name: object) -> str:
        text = str(stock_name or "")
        style = self.result_table.style()
        item_margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin,
            None,
            self.result_table,
        ) + 1
        section_border_width = style.pixelMetric(
            QStyle.PM_DefaultFrameWidth,
            None,
            self.result_table,
        ) if self.result_table.showGrid() else 0
        available_width = max(
            0,
            self.result_table.columnWidth(self.NAME_COLUMN)
            - (item_margin * 2)
            - section_border_width,
        )
        return (
            text
            if self.result_table.fontMetrics().horizontalAdvance(text) > available_width
            else ""
        )

    def _stock_name_alignment(self, stock_name: object) -> Qt.Alignment:
        return (
            Qt.AlignLeft | Qt.AlignVCenter
            if self._stock_name_tooltip(stock_name)
            else Qt.AlignCenter
        )

    def on_result_header_clicked(self, column: int) -> None:
        if column == self._result_sort_column:
            self._result_sort_order = (
                Qt.DescendingOrder
                if self._result_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._result_sort_column = column
            self._result_sort_order = Qt.AscendingOrder
        self._apply_result_sort()

    def _apply_result_sort(self) -> None:
        if self._result_sort_column < 0:
            self.result_table.setSortingEnabled(False)
            return
        self.result_table.setSortingEnabled(True)
        self.result_table.horizontalHeader().setSortIndicator(
            self._result_sort_column,
            self._result_sort_order,
        )
        self.result_table.horizontalHeader().setSortIndicatorShown(False)
        self.result_table.sortItems(
            self._result_sort_column,
            self._result_sort_order,
        )
        self._apply_general_stock_visibility()

    def _toast(self, message: str) -> None:
        show_toast(self, message)

    def _target_instance(self) -> tuple[str, str, str, str] | None:
        instance_id = str(self.instance_metadata.get("instance_id", "") or "").strip()
        instance_name = str(self.instance_metadata.get("instance_name", "") or "").strip()
        definition_id = str(self.instance_metadata.get("definition_id", "") or "").strip()
        routine_type = str(self.instance_metadata.get("definition_name", "") or "").strip()
        if self._is_unassigned_target() and instance_name:
            return "", instance_name, "", routine_type or instance_name
        if not all((instance_id, instance_name, definition_id, routine_type)):
            return None
        return instance_id, instance_name, definition_id, routine_type

    def _is_unassigned_target(self) -> bool:
        return str(self.instance_metadata.get("target_kind", "") or "").strip() == "unassigned"

    def _registered_stock(self, code: str) -> dict[str, object] | None:
        clean_code = normalize_stock_code(code)
        for stock in read_base_stocks():
            if normalize_stock_code(str(stock.get("code", "") or "")) == clean_code:
                return dict(stock)
        return None

    def _classification_text(self, code: str, name: str) -> str:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
            if inspect_stock_review_state(
                stock_dir,
                loaded_state=state,
            ).review_required:
                return "검토관리"
        except Exception:
            pass

        stock = self._registered_stock(code)
        if stock is None:
            return "등록가능" if self._is_unassigned_target() else "등록대기"

        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if not assigned_instance_id:
            return "등록대기"

        current_instance_id = str(
            self.instance_metadata.get("instance_id", "") or ""
        ).strip()
        if assigned_instance_id == current_instance_id:
            routine_name = str(
                self.instance_metadata.get("instance_name", "") or ""
            ).strip()
            return routine_name or assigned_instance_id

        for instance in load_persisted_routine_instances():
            if str(getattr(instance, "instance_id", "") or "").strip() == assigned_instance_id:
                routine_name = str(getattr(instance, "display_name", "") or "").strip()
                return routine_name or assigned_instance_id
        return assigned_instance_id

    def _refresh_parent_views(self, *, sync_monitoring_universe: bool = False) -> None:
        if sync_monitoring_universe:
            sync_auto_trade_monitoring_universe(self)
        try:
            refresh_auto_trade_views(self)
        except Exception:
            LOGGER.exception("Failed to refresh assignment views")

    def on_result_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        self.register_or_assign_result_row(item.row())

    def on_result_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        select_all_action = menu.addAction("전체선택")
        clear_selection_action = menu.addAction("선택해제")
        register_action = menu.addAction("선택등록")
        unregister_action = menu.addAction("등록해제")
        selected_rows = self.result_table.selectionModel().selectedRows()
        has_selection = bool(selected_rows)
        unregister_action.setEnabled(
            self._has_selected_routine_registered_stock()
        )
        register_action.setEnabled(has_selection)
        clear_selection_action.setEnabled(has_selection)
        select_all_action.triggered.connect(lambda _checked=False: self.result_table.selectAll())
        clear_selection_action.triggered.connect(lambda _checked=False: self.result_table.clearSelection())
        register_action.triggered.connect(lambda _checked=False: self.register_selected_result_rows())
        unregister_action.triggered.connect(lambda _checked=False: self.unregister_selected_result_rows())
        menu.exec_(self.result_table.viewport().mapToGlobal(pos))

    def _result_stock_at_row(self, row: int) -> tuple[str, str] | None:
        code_item = self.result_table.item(row, self.CODE_COLUMN)
        name_item = self.result_table.item(row, self.NAME_COLUMN)
        if code_item is None or name_item is None:
            return None
        code = normalize_stock_code(code_item.text())
        name = name_item.text().strip()
        return code, name

    def _selected_result_stocks(self) -> list[tuple[str, str]]:
        selection_model = self.result_table.selectionModel()
        if selection_model is None:
            return []
        selected: list[tuple[str, str]] = []
        seen_codes: set[str] = set()
        for index in selection_model.selectedRows():
            stock = self._result_stock_at_row(index.row())
            if stock is None:
                continue
            code, name = stock
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            selected.append((code, name))
        return selected

    def _is_review_required_stock(self, code: str, name: str) -> bool:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
            return inspect_stock_review_state(
                stock_dir,
                loaded_state=state,
            ).review_required
        except Exception:
            return True

    def _registered_routine_name_for_stock(self, code: str, name: str) -> str:
        stock = self._registered_stock(code)
        if stock is None:
            return ""
        routines = stock.get("routines", [])
        if isinstance(routines, list):
            active_routines = single_routine_list(routines)
            if active_routines:
                return str(active_routines[0]).strip()
        else:
            routine_name = str(routines or "").strip()
            if routine_name:
                return routine_name

        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if not assigned_instance_id:
            return ""

        current_instance_id = str(
            self.instance_metadata.get("instance_id", "") or ""
        ).strip()
        if assigned_instance_id == current_instance_id:
            routine_name = str(
                self.instance_metadata.get("instance_name", "") or ""
            ).strip()
            return routine_name or assigned_instance_id

        for instance in load_persisted_routine_instances():
            if str(getattr(instance, "instance_id", "") or "").strip() == assigned_instance_id:
                routine_name = str(getattr(instance, "display_name", "") or "").strip()
                return routine_name or assigned_instance_id
        return assigned_instance_id

    def _has_selected_routine_registered_stock(self) -> bool:
        owner = persistent_feature_owner(self)
        for code, name in self._selected_result_stocks():
            routine_name = self._registered_routine_name_for_stock(code, name)
            if not routine_name:
                continue
            availability = inspect_stock_unregister_availability(
                owner,
                PROJECT_ROOT,
                code,
                name,
            )
            if availability.allowed:
                return True
        return False

    def _find_result_row_by_stock_code(self, code: str) -> int:
        target_code = normalize_stock_code(code)
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, self.CODE_COLUMN)
            if item is None:
                continue
            if normalize_stock_code(item.text()) == target_code:
                return row
        return -1

    def _selected_result_stock_codes(self) -> set[str]:
        selected_codes: set[str] = set()
        selection_model = self.result_table.selectionModel()
        if selection_model is None:
            return selected_codes
        for index in selection_model.selectedRows():
            item = self.result_table.item(index.row(), self.CODE_COLUMN)
            if item is not None:
                selected_codes.add(normalize_stock_code(item.text()))
        return selected_codes

    def _restore_result_selection(self, selected_codes: set[str]) -> None:
        if not selected_codes:
            return
        self.result_table.clearSelection()
        for code in selected_codes:
            row = self._find_result_row_by_stock_code(code)
            if row >= 0:
                self.result_table.selectRow(row)

    def _refresh_classification_for_stock(self, code: str) -> bool:
        row = self._find_result_row_by_stock_code(code)
        if row < 0:
            return False
        stock = self._result_stock_at_row(row)
        if stock is None:
            return False
        stock_code, stock_name = stock
        selected_codes = self._selected_result_stock_codes()
        classification = self._classification_text(stock_code, stock_name)
        item = self.result_table.item(row, self.CATEGORY_COLUMN)
        if item is None:
            item = QTableWidgetItem()
            self.result_table.setItem(row, self.CATEGORY_COLUMN, item)
        item.setText(classification)
        item.setData(Qt.UserRole, classification)
        item.setTextAlignment(Qt.AlignCenter)
        self._apply_result_sort()
        self._restore_result_selection(selected_codes)
        return True

    def unregister_selected_result_rows(self) -> bool:
        selected = self._selected_result_stocks()
        if not selected:
            self._toast("등록해제할 종목을 선택하세요.")
            return False

        allowed: list[tuple[str, str, str]] = []
        skipped = 0
        blocked: list[tuple[str, str, list[str]]] = []
        seen_codes: set[str] = set()
        owner = persistent_feature_owner(self)
        for code, name in selected:
            if code in seen_codes:
                continue
            seen_codes.add(code)
            availability = inspect_stock_unregister_availability(
                owner,
                PROJECT_ROOT,
                code,
                name,
            )
            routine_name = self._registered_routine_name_for_stock(code, name)
            if not routine_name:
                skipped += 1
                continue
            if availability.allowed:
                allowed.append((code, name, routine_name))
            else:
                blocked.append((code, name, [availability.reason_code]))

        if not allowed:
            if blocked:
                self._toast(f"등록해제 차단 {len(blocked)}건")
            else:
                self._toast("등록해제할 종목이 없습니다.")
            return False

        succeeded: list[str] = []
        succeeded_names: list[str] = []
        failed: list[str] = []
        for code, name, _routine_name in allowed:
            current = StockRepository(PROJECT_ROOT).find_by_code(code)
            expected_instance_id = str(
                getattr(current, "assigned_routine_instance_id", "") or ""
            ).strip()
            result = execute_assignment_unassign(
                owner,
                PROJECT_ROOT,
                code,
                name,
                expected_instance_id=expected_instance_id,
                intent=ASSIGNMENT_INTENT_UNASSIGN,
            )
            if result.ok and result.changed:
                ensure_single_real_trade_routine_for_stock(code, name)
                succeeded.append(code)
                succeeded_names.append(name)
            else:
                failed.append(code)

        if succeeded:
            self._refresh_parent_views(sync_monitoring_universe=True)
            for code in succeeded:
                self._refresh_classification_for_stock(code)

        parts: list[str] = []
        if succeeded:
            self._toast(f"등록해제 {len(succeeded)}건 | {', '.join(succeeded_names)}")
            return True
        if failed:
            parts.append(f"처리불가 {len(failed)}건")
        self._toast(" | ".join(parts) if parts else "등록해제할 종목이 없습니다.")
        return False

    def _valid_library_stock(self, code: str, name: str) -> bool:
        library_stock = find_library_stock_by_code(code)
        return bool(
            library_stock is not None
            and library_stock.get("name", "").strip() == name
            and is_valid_stock_code(code)
        )

    def _valid_result_stock(self, row: int, code: str, name: str) -> bool:
        return self._valid_library_stock(code, name)

    def _assignment_block_reason(self, code: str, name: str) -> str:
        can_process, guard_info = routine_action_reasons_for_stock(
            code,
            name,
            allow_unassigned=True,
        )
        if can_process:
            return ""
        reasons = guard_info.get("reasons", [])
        return ", ".join(str(reason) for reason in reasons) if reasons else "처리할 수 없는 종목입니다."

    def _assign_stock_to_target_instance(
        self,
        code: str,
        name: str,
        target: tuple[str, str, str, str],
        *,
        needs_registration: bool,
    ) -> bool:
        instance_id, instance_name, definition_id, routine_type = target
        if needs_registration and not append_base_stock(code, name):
            return False

        from stock_assignment_registration_service import (
            register_unassigned_stock_to_instance,
        )

        result = register_unassigned_stock_to_instance(
            PROJECT_ROOT,
            code,
            name,
            operation_owner=persistent_feature_owner(self),
            instance_id=instance_id,
            instance_name=instance_name,
            definition_id=definition_id,
            routine_type=routine_type,
        )
        if not result.success or not result.changed:
            return False

        try:
            repo = StockRepository(PROJECT_ROOT)
            stock_dir = repo.ensure_stock_folder(code, name, routine=routine_type)
            ensure_single_real_trade_routine_for_stock(code, name, routine_type)
        except Exception:
            LOGGER.exception("Failed to prepare stock assignment files")
            return False

        append_changelog(
            "UPDATE",
            "config.json",
            f"검색형 종목등록 지정: {code},{name}({instance_name})",
        )
        return True

    def _register_stock_to_unassigned(
        self,
        code: str,
        name: str,
        *,
        needs_registration: bool,
    ) -> bool:
        if not needs_registration:
            return False
        return append_base_stock(code, name)

    def _summary_toast_text(self, counts: dict[str, int]) -> str:
        labels = (
            ("new", "등록"),
            ("moved", "이동"),
            ("duplicate", "중복"),
            ("blocked", "차단"),
            ("move_cancelled", "등록 취소"),
            ("failed", "처리불가"),
            ("invalid", "검증실패"),
        )
        parts = [
            f"{label} {int(counts.get(key, 0))}건"
            for key, label in labels
            if int(counts.get(key, 0)) > 0
        ]
        return " | ".join(parts) if parts else "처리할 종목이 없습니다."

    def register_selected_result_rows(self) -> bool:
        selected_rows = sorted(
            {index.row() for index in self.result_table.selectionModel().selectedRows()}
        )
        if not selected_rows:
            self._toast("등록할 종목을 선택하세요.")
            return False
        target = self._target_instance()
        if target is None:
            self._toast("대상 루틴 정보를 확인하지 못했습니다.")
            return False
        instance_id = target[0]

        new_targets: list[tuple[str, str, bool]] = []
        move_targets: list[tuple[str, str]] = []
        counts = {
            "new": 0,
            "duplicate": 0,
            "moved": 0,
            "move_cancelled": 0,
            "blocked": 0,
            "failed": 0,
            "invalid": 0,
        }
        seen_codes: set[str] = set()
        unassigned_target = self._is_unassigned_target()

        for row in selected_rows:
            result_stock = self._result_stock_at_row(row)
            if result_stock is None:
                counts["invalid"] += 1
                continue
            code, name = result_stock
            if not code or code in seen_codes or not self._valid_result_stock(row, code, name):
                counts["invalid"] += 1
                continue
            seen_codes.add(code)

            stock = self._registered_stock(code)
            assigned_instance_id = (
                str(stock.get("assigned_routine_instance_id", "") or "").strip()
                if stock is not None
                else ""
            )
            if unassigned_target:
                if stock is None:
                    new_targets.append((code, name, True))
                elif assigned_instance_id:
                    counts["blocked"] += 1
                else:
                    counts["duplicate"] += 1
                continue
            if assigned_instance_id == instance_id:
                counts["duplicate"] += 1
            elif assigned_instance_id:
                counts["blocked"] += 1
            else:
                new_targets.append((code, name, stock is None))

        move_allowed = False
        if move_targets:
            answer = QMessageBox.question(
                self,
                "선택 종목 처리 결과",
                "선택 종목 처리 결과\n\n"
                f"등록 {len(new_targets)}건 | 이동 {len(move_targets)}건 | 중복 {counts['duplicate']}건 | 차단 {counts['blocked']}건\n\n"
                f"다른 루틴에 등록된 {len(move_targets)}종목을 현재 루틴으로 이동하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            move_allowed = answer == QMessageBox.Yes
            if not move_allowed:
                counts["move_cancelled"] = len(move_targets)

        changed = False
        changed_codes: list[str] = []
        for code, name, needs_registration in new_targets:
            if unassigned_target:
                assigned = self._register_stock_to_unassigned(
                    code,
                    name,
                    needs_registration=needs_registration,
                )
            else:
                assigned = self._assign_stock_to_target_instance(
                    code,
                    name,
                    target,
                    needs_registration=needs_registration,
                )
            if assigned:
                counts["new"] += 1
                changed = True
                changed_codes.append(code)
            else:
                counts["failed"] += 1

        if move_allowed:
            for code, name in move_targets:
                if self._assign_stock_to_target_instance(
                    code,
                    name,
                    target,
                    needs_registration=False,
                ):
                    counts["moved"] += 1
                    changed = True
                    changed_codes.append(code)
                else:
                    counts["failed"] += 1

        if changed:
            self._refresh_parent_views(
                sync_monitoring_universe=not unassigned_target,
            )
            for code in changed_codes:
                self._refresh_classification_for_stock(code)
        if counts.get("move_cancelled") and not counts.get("new") and not changed:
            self._toast("등록 취소")
        else:
            self._toast(self._summary_toast_text(counts))
        return changed

    def register_or_assign_result_row(self, row: int) -> bool:
        result_stock = self._result_stock_at_row(row)
        if result_stock is None:
            self._toast("처리할 종목을 확인하지 못했습니다.")
            return False
        code, name = result_stock
        target = self._target_instance()
        if target is None:
            self._toast("대상 루틴 정보를 확인하지 못했습니다.")
            return False
        instance_id, instance_name, definition_id, routine_type = target

        if not self._valid_result_stock(row, code, name):
            self._toast("선택한 종목의 등록 Evidence가 유효하지 않습니다.")
            return False

        stock = self._registered_stock(code)
        assigned_instance_id = (
            str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if stock is not None
            else ""
        )
        if self._is_unassigned_target():
            if stock is not None and not assigned_instance_id:
                self._toast("중복 1건")
                return False
            if assigned_instance_id:
                self._toast("차단 1건")
                return False
            if not self._register_stock_to_unassigned(
                code,
                name,
                needs_registration=stock is None,
            ):
                self._toast("종목 등록에 실패했습니다.")
                return False
            self._refresh_parent_views()
            self._refresh_classification_for_stock(code)
            self._toast("등록 1건")
            return True

        if assigned_instance_id == instance_id:
            self._toast("이미 같은 루틴에 지정된 종목입니다.")
            return False

        if assigned_instance_id and assigned_instance_id != instance_id:
            self._toast("다른 루틴에 등록된 종목입니다. 기존 등록전환 경로를 사용하세요.")
            return False

        newly_registered = False
        if stock is None:
            newly_registered = True

        if not self._assign_stock_to_target_instance(
            code,
            name,
            target,
            needs_registration=newly_registered,
        ):
            if newly_registered:
                self._toast("종목 등록에 실패했습니다.")
            else:
                self._toast("종목 지정에 실패했습니다.")
            return False

        self._refresh_parent_views(sync_monitoring_universe=True)
        self._refresh_classification_for_stock(code)
        if newly_registered:
            self._toast("종목 등록 및 지정이 완료됐습니다.")
        elif assigned_instance_id:
            self._toast("종목 지정이 변경됐습니다.")
        else:
            self._toast("종목 지정이 완료됐습니다.")
        return True


_INSTANCE_STOCK_SEARCH_DIALOGS: dict[str, InstanceStockSearchRegisterDialog] = {}


def open_instance_stock_search_register_dialog(
    owner: QWidget,
    metadata: dict[str, object],
    *,
    owner_attribute: str = "instance_stock_search_register_window",
    delete_on_close: bool = False,
    finished_callback=None,
) -> InstanceStockSearchRegisterDialog:
    """Open one process-wide search dialog per target Instance."""
    target_id = str(metadata.get("instance_id", "") or "").strip()
    target_kind = str(metadata.get("target_kind", "") or "").strip()
    target_key = target_id or f"kind:{target_kind or 'unassigned'}"
    dialogs = _INSTANCE_STOCK_SEARCH_DIALOGS

    existing = dialogs.get(target_key)
    if existing is not None:
        try:
            if not sip.isdeleted(existing):
                if existing.parentWidget() is not owner:
                    existing.setParent(owner, existing.windowFlags() | Qt.Window)
                setattr(owner, owner_attribute, existing)
                owner_refs = getattr(existing, "_registration_dialog_owner_refs", [])
                owner_ref = (owner, owner_attribute)
                if owner_ref not in owner_refs:
                    owner_refs.append(owner_ref)
                existing._registration_dialog_owner_refs = owner_refs
                if existing.isMinimized():
                    existing.showNormal()
                else:
                    existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
        except RuntimeError:
            pass
        dialogs.pop(target_key, None)

    dialog = InstanceStockSearchRegisterDialog(
        owner,
        instance_metadata=dict(metadata),
    )
    if delete_on_close:
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    dialogs[target_key] = dialog
    setattr(owner, owner_attribute, dialog)
    dialog._registration_dialog_owner_refs = [(owner, owner_attribute)]
    if callable(finished_callback):
        dialog.finished.connect(finished_callback)

    def clear_dialog(_obj=None, *, target=dialog, key=target_key) -> None:
        if dialogs.get(key) is target:
            dialogs.pop(key, None)
        owner_refs = tuple(getattr(target, "_registration_dialog_owner_refs", ()))
        for target_owner, attribute_name in owner_refs:
            try:
                if getattr(target_owner, attribute_name, None) is target:
                    try:
                        setattr(target_owner, attribute_name, None)
                    except RuntimeError:
                        continue
            except RuntimeError:
                continue
        target._registration_dialog_owner_refs = []

    dialog.destroyed.connect(clear_dialog)
    closed_signal = getattr(dialog, "closed", None)
    if closed_signal is not None:
        closed_signal.connect(clear_dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def auto_trade_register_historical_stock_to_original_instance(
    parent: QWidget,
    metadata: dict[str, object],
) -> bool:
    """Register a historical stock back to its original routine instance."""
    if not bool(metadata.get("is_historical", False)):
        return False

    code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
    name = str(metadata.get("display_name", "") or "").strip()
    instance_id = str(metadata.get("instance_id", "") or "").strip()
    instance_name = str(metadata.get("instance_name", "") or "").strip()
    definition_id = str(metadata.get("definition_id", "") or "").strip()
    definition_name = str(metadata.get("definition_name", "") or "").strip()
    if not all((code, name, instance_id, instance_name, definition_id, definition_name)):
        show_toast(parent, "처리할 수 없는 종목입니다.")
        return False

    dialog = InstanceStockSearchRegisterDialog(
        parent,
        instance_metadata={
            "instance_id": instance_id,
            "instance_name": instance_name,
            "definition_id": definition_id,
            "definition_name": definition_name,
        },
    )
    try:
        def emit_toast(message: str) -> None:
            show_toast(parent, message)

        if not dialog._valid_library_stock(code, name):
            emit_toast("종목 정보를 확인하지 못했습니다.")
            return False

        stock = dialog._registered_stock(code)
        assigned_instance_id = (
            str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if stock is not None
            else ""
        )
        if assigned_instance_id == instance_id:
            emit_toast("이미 같은 루틴에 지정된 종목입니다.")
            return False
        if assigned_instance_id:
            emit_toast("중복 1건")
            return False

        reason_text = dialog._assignment_block_reason(code, name)
        if reason_text:
            emit_toast(reason_text)
            return False

        target = (instance_id, instance_name, definition_id, definition_name)
        if not dialog._assign_stock_to_target_instance(
            code,
            name,
            target,
            needs_registration=stock is None,
        ):
            emit_toast("종목 등록에 실패했습니다.")
            return False

        dialog._refresh_parent_views(sync_monitoring_universe=True)
        dialog._refresh_classification_for_stock(code)
        emit_toast(f"등록 1건 | {name}")
        return True
    finally:
        dialog.close()
        dialog.deleteLater()


from gui_base_stock_service import (
    ensure_single_real_trade_routine_for_all_stocks,
    find_library_stock_by_code,
    is_valid_stock_code,
    load_stock_library,
    normalize_base_stock_single_routine_file,
    normalize_stock_code,
    read_base_stocks,
    single_routine_list,
    validate_base_stock_record,
)
from state_policy import (
    auto_trade_status_color,
    auto_trade_status_display,
    auto_trade_status_dot,
    effective_schedule_times,
    minutes_from_hhmm,
    normalize_after_trade_end_status,
    normalize_operation_mode,
    normalized_hhmm_or_empty,
    normalized_hhmmss_or_empty,
    operation_mode_check_text,
    operation_mode_display,
    trade_permission_display,
    operation_mode_recalculation_target_status,
    operation_text_and_color,
    schedule_override_enabled,
    scheduled_status_for_now,
    seconds_from_hhmmss,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
    write_global_schedule,
)
from gui_ats_utils import (
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
)
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_BORDER_COLOR,
    AUTO_TRADE_SETTING_BADGE_HEIGHT,
    AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_TEXT_COLOR,
    AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    auto_trade_setting_badge_stylesheet,
    auto_trade_setting_display_status,
    auto_trade_setting_status_color,
    confirmable_stock_profit_metric,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    draw_stock_position_metric,
    yes_no_display,
    display_status_text_for_gui,
    profit_loss_value_color,
    ratio_metric_text,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_auto_trade_policy import (
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_start_target_decision,
    auto_trade_setting_trade_started,
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_no_next_step_notice,
    short_close_method_text,
    compact_operation_time_range,
    operation_policy_section,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_early_close_metadata_is_stale,
    clear_early_close_runtime_metadata_only,
    auto_trade_setting_early_close_requested,
    clear_auto_close_runtime_metadata,
    close_method_from_state_or_policy,
    auto_trade_setting_method_text,
    individual_liquidation_policy_from_config,
    effective_liquidation_policy_for_config,
    auto_trade_setting_liquidation_text,
    auto_trade_setting_regular_end_seconds,
    auto_trade_setting_is_after_regular_end,
    auto_trade_setting_has_unresolved_quantity,
    auto_trade_setting_has_buy_pending_problem,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_today_date_text,
    auto_trade_setting_liquidation_completed_today,
    auto_trade_setting_effective_liquidation_method,
    auto_trade_setting_liquidation_result_policy,
    auto_trade_setting_mark_liquidation_result_for_display,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
)
from gui_auto_trade_integrity import (
    unique_review_reasons,
    inspect_stock_review_state,
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_state,
    is_review_required_stock_dir,
    auto_trade_setting_data_inconsistency_reasons,
    restart_initial_review_reason_for_stock,
    operator_review_location,
    operator_review_reason,
    auto_trade_setting_server_mismatch_detected,
)
from gui_stock_performance_window import open_stock_performance
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)
from gui_auto_trade_unregister import (
    unregister_selected_auto_trade_stocks,
)
from gui_auto_trade_context_menu import (
    CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
    show_auto_trade_stock_context_menu,
)
from gui_auto_trade_selection import (
    clear_current_routine_stock_selection,
    ensure_context_row_selected,
    has_selected_stock,
    has_single_selected_stock,
    select_all_current_routine_stocks,
    selected_stock_dir,
    selected_stock_info,
    selected_stock_infos,
    selected_stock_rows,
)
from gui_auto_trade_close import (
    ProfitLossEarlyCloseDialog,
    auto_trade_apply_selected_individual_liquidation_method,
    auto_trade_apply_selected_early_close,
    auto_trade_apply_selected_early_close_default,
    auto_trade_apply_selected_early_close_profit_loss,
    auto_trade_cancel_selected_early_close,
)
from gui_auto_trade_ats_ops import (
    auto_trade_execute_selected_manual_ats_liquidation,
    auto_trade_save_selected_manual_ats_state,
    auto_trade_selected_manual_ats_execution_method_state,
    auto_trade_selected_manual_ats_liquidation_available,
    auto_trade_selected_manual_ats_state,
    auto_trade_set_selected_manual_ats_execution_method,
    auto_trade_set_selected_manual_ats_flag,
)
from gui_auto_trade_timer import (
    auto_trade_current_time_policy_minute_key,
    auto_trade_on_time_policy_gui_timer_tick,
)
from gui_operation_ui_context import (
    refresh_auto_trade_views,
    sync_auto_trade_monitoring_universe,
)
from gui_auto_trade_status_ops import (
    OPERATION_EXCLUDED_CONFIG_KEY,
    OPERATION_EXCLUSION_REVIEW_BLOCK_MESSAGE,
    OPERATION_EXCLUSION_RUNNING_BLOCK_MESSAGE,
    append_changelog,
    append_stock_log,
    auto_trade_apply_schedule_times_to_targets,
    auto_trade_clear_selected_stock_operation_exclusions,
    auto_trade_finalize_operation_mode_result,
    auto_trade_operation_exclusion_mutation_decision,
    auto_trade_operation_policy_protected_status,
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_reset_schedule_times_for_targets,
    auto_trade_set_selected_stock_operation_exclusions,
    auto_trade_set_stock_operation_exclusion,
    auto_trade_stock_operation_excluded,
    auto_trade_set_selected_operation_mode,
    auto_trade_set_selected_schedule_operation_mode,
    auto_trade_toggle_stock_operation_exclusion,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
    handle_auto_trade_operation_mode_double_click,
)
from gui_auto_trade_run_control import (
    OperationStartCommandRequest,
    OperationStartIntent,
    _show_operation_start_summary_toast,
    auto_trade_registered_operation_start_targets,
    auto_trade_registered_operation_targets,
    auto_trade_running_registered_operation_targets,
    auto_trade_start_selected_auto_trades,
    auto_trade_start_selected_rows_auto_trades,
    auto_trade_start_status_indicator,
    auto_trade_update_global_operation_button_state,
    execute_operation_start_command,
    startup_recovery_operation_block_message,
    today_global_operation_status as _today_global_operation_status,
)
from operation_policy_gate import read_operation_state
from order_manager import (
    decide_routine_order_for_stock_dir,
    mark_routine_order_accepted_for_stock_dir,
)
from gui_auto_trade_review_ops import (
    auto_trade_open_review_required_window,
)
from gui_review_required_window import (
    _read_central_review_state,
    collect_global_review_required_rows,
)
from gui_auto_trade_table_loader import (
    _selected_instance_stock_dirs,
    auto_trade_load_selected_routine_stocks,
    refresh_auto_trade_chart_open_code_styles,
)
from gui_routine_service import (
    ensure_single_real_trade_routine_for_stock,
    execute_selected_stock_real_trade_command,
    selected_stock_real_trade_target_enabled,
    selected_stock_trade_permission_available,
    selected_stock_trade_permission_label,
)
from gui_operation_environment import (
    OperationEnvironmentSettingsDialog,
    TimeComboWidget,
    default_operation_policy,
    read_operation_policy,
    write_operation_policy,
)
from program_factory_reset import (
    execute_program_factory_reset,
    validate_factory_reset_safety,
)
from gui_review_required_window import (
    GlobalReviewRequiredWindow,
)
from gui_routine_registry import (
    get_group_dirs as registry_get_group_dirs,
    get_group_records,
    group_record_by_id,
    routine_display_name as registry_routine_display_name,
    read_routine_budget,
)
from main_group_projection import build_main_group_projection
from auto_trade_performance_ui import (
    CanonicalPerformanceUiSnapshot,
    build_canonical_performance_ui_snapshot,
    normalize_profit_factor,
    routine_tree_performance_texts,
    routine_tree_stock_performance_source,
)
from performance_metrics import MetricStatus
from production_recovery_state_registry import recovery_stock_is_review_required
from routine_instance_registry import (
    load_persisted_routine_instances,
    load_routine_definitions,
    routine_definition_by_id,
    routine_instance_by_id,
)
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import (
    STOCK_CONFIG_DELETE_FIELD,
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_INVALID_PATCH,
    STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
    STOCK_CONFIG_WRITE_NO_CHANGE,
    STOCK_CONFIG_WRITE_READBACK_FAILED,
    StockConfigWriteResult,
    StockRepository,
)
from group_scope import load_group_scope
from execution_enable_service import commit_execution_enable, preview_execution_enable
from execution_final_send_gate_input_adapter import adapt_final_send_gate_readiness_to_input
from execution_final_send_gate_orchestrator import orchestrate_final_send_gate_preview
from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness
from execution_queue_commit_service import commit_execution_queue_manually
from execution_queue_commit_readiness_policy import evaluate_execution_queue_commit_readiness
from execution_queue_review_to_send_order_preview_adapter import adapt_queue_review_to_send_order_preview
from execution_queue_writer import claim_order_for_dispatch, commit_execution_queue_write
from execution_preview_order_service import preview_execution_for_real_ready_order
from execution_preview_reporter import build_execution_preview_report
from execution_readiness_preview_controller import build_execution_readiness_preview_from_context
from execution_runtime_commit_service import commit_execution_runtime_plan
from execution_runtime_controller import run_execution_runtime_dry_run
from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_commit_service import commit_execution_runtime_file_init_plan
from execution_runtime_file_init_open_policy import evaluate_execution_runtime_file_init_open_policy
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness
from execution_runtime_storage import ExecutionRuntimeStorage
from execution_fill_recorder import find_existing_execution_fill_record, record_execution_fill
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call
from kiwoom_send_order_executor import execute_claimed_send_order
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety
from broker_holding_recorder import record_broker_holding_snapshot
from chejan_event_normalizer import normalize_kiwoom_chejan_event
from chejan_event_recorder import (
    chejan_event_identity,
    existing_chejan_record_result,
    mark_chejan_reconciliation_state,
    record_chejan_event,
)
from chejan_event_review_service import review_chejan_event
from final_send_gate_service import evaluate_final_send_gate
from order_queued_review_service import review_order_queued_record
from position_update_service import update_position_from_fill
from realized_pnl_ledger import record_realized_pnl
from production_performance_linkage import (
    append_performance_from_realization,
    prepare_sell_fifo_realization,
    record_buy_entry_lot,
)
from real_order_preflight_service import commit_real_order_preflight, preview_real_order_preflight


ROUTINE_INSTANCE_REQUIRED_MESSAGE = "이 작업을 수행할 대상 루틴을 선택하세요."
ROUTINE_STATUS_DEFAULT = "기본운영"
ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS = 7
ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS = 7
AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR = "#4B5563"
AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN = 1
AUTO_TRADE_SETTING_TOP_CONTROL_BODY_SPACING = 2
AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE = "color: #2563eb; font-weight: bold;"
AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA = {
    "category": frozenset({"period", "profit", "average", "efficiency"}),
    "routine": frozenset({"period", "profit", "average", "efficiency"}),
    "stock": frozenset({"period", "profit", "average", "efficiency"}),
}
AUTO_TRADE_SETTING_STOCK_ROW_TEXT_COLOR = "#7E22CE"
AUTO_TRADE_SETTING_HISTORICAL_STOCK_ROW_TEXT_COLOR = "#9CA3AF"
AUTO_TRADE_SETTING_APP_ENV = "KIWOOM_AUTO_APP_ENV"
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV = (
    "KIWOOM_AUTO_HISTORICAL_STOCK_FIXTURE"
)
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_CANDIDATES = (
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("005380", "현대차"),
    ("051910", "LG화학"),
    ("068270", "셀트리온"),
    ("055550", "신한지주"),
    ("066570", "LG전자"),
    ("086520", "에코프로"),
    ("105560", "KB금융"),
    ("247540", "에코프로비엠"),
    ("091990", "셀트리온헬스케어"),
    ("293490", "카카오게임즈"),
    ("323410", "카카오뱅크"),
    ("003550", "LG"),
    ("012330", "현대모비스"),
    ("028260", "삼성물산"),
    ("006400", "삼성SDI"),
    ("005930", "삼성전자"),
    ("096770", "SK이노베이션"),
)
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE = (
    {
        "trade_days": 3,
        "realized_profit": 125000.0,
        "profit_rate": 3.25,
        "average": 62500.0,
        "average_rate": 1.63,
        "gross_profit": 125000.0,
        "gross_loss_abs": 39062.5,
        "profit_factor": 3.2,
    },
    {
        "trade_days": 2,
        "realized_profit": -48000.0,
        "profit_rate": -1.40,
        "average": -24000.0,
        "average_rate": -0.70,
        "gross_profit": None,
        "gross_loss_abs": None,
        "profit_factor": 0.0,
    },
    {
        "trade_days": 0,
        "realized_profit": 0.0,
        "profit_rate": 0.0,
        "average": 0.0,
        "average_rate": 0.0,
        "gross_profit": None,
        "gross_loss_abs": None,
        "profit_factor": 0.0,
    },
)
AUTO_TRADE_SETTING_HISTORICAL_MANUAL_AGGREGATION_FIXTURES = (
    {
        "stock_code": "000660",
        "stock_name": "SK하이닉스",
        "performance_fixture": {
            "trade_days": 2,
            "realized_profit": 48000.0,
            "profit_rate": 0.0,
            "average": 24000.0,
            "average_rate": 0.0,
            "gross_profit": 48000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "000660",
        "stock_name": "SK하이닉스",
        "performance_fixture": {
            "trade_days": 5,
            "realized_profit": 77000.0,
            "profit_rate": 0.0,
            "average": 15400.0,
            "average_rate": 0.0,
            "gross_profit": 77000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "006400",
        "stock_name": "삼성SDI",
        "performance_fixture": {
            "trade_days": 2,
            "realized_profit": 40000.0,
            "profit_rate": 0.0,
            "average": 20000.0,
            "average_rate": 0.0,
            "gross_profit": 40000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "006400",
        "stock_name": "삼성SDI",
        "performance_fixture": {
            "trade_days": 1,
            "realized_profit": 20000.0,
            "profit_rate": 0.0,
            "average": 20000.0,
            "average_rate": 0.0,
            "gross_profit": 20000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
)
AUTO_TRADE_SETTING_STOCK_TABLE_COLUMN_WIDTHS = {
    0: 80,    # 코드: 6자리 여유
    1: 205,   # 종목: 13자 기준
    2: 120,   # 운영: 09:30~13:30 표시
    3: 50,    # 현황: 종목 운영 건강도 표시등
    4: 90,    # 상태: 감시/대기, 매수/매도
    5: 80,    # 방식: 루틴, 시장가, 현재가
    6: 120,   # 청산: 10분/시장가, 10분/현재가
    7: 204,   # 보유: 수량 / 총매수금액
    8: 194,   # 가격: 평단가 / 현재가
    9: 199,   # 손익: 손익금 / 수익률
    10: 78,   # 매매: 매수회차 / 매도회차
}
AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN = 6
AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP = 6
AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT = 24
AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X = 4
AUTO_TRADE_SETTING_STOCK_ROW_SPACING = 2
AUTO_TRADE_SETTING_ROUTINE_TREE_METRIC_SAMPLES = {
    "period": (("9999",), ()),
    "profit": (("-99,999,999", "+99,999,999"), ("-99.99%", "+99.99%")),
    "average": (("-99,999,999", "+99,999,999"), ("-99.99%", "+99.99%")),
    "efficiency": (("999.9",), ()),
}
AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS = (
    {
        "key": "period",
        "object_name": "autoTradeSettingRoutineTreePerformancePeriod",
        "label": "기간",
        "left_fallback": "0",
        "left_sample": "9999",
        "right_fallback": "",
        "right_sample": "",
    },
    {
        "key": "profit",
        "object_name": "autoTradeSettingRoutineTreePerformanceProfit",
        "label": "수익",
        "left_fallback": "0",
        "left_sample": "-99,999,999",
        "right_fallback": "0.00%",
        "right_sample": "-99.99%",
    },
    {
        "key": "average",
        "object_name": "autoTradeSettingRoutineTreePerformanceAverage",
        "label": "평균",
        "left_fallback": "0",
        "left_sample": "-99,999,999",
        "right_fallback": "0.00%",
        "right_sample": "-99.99%",
    },
    {
        "key": "efficiency",
        "object_name": "autoTradeSettingRoutineTreePerformanceEfficiency",
        "label": "효율",
        "left_fallback": "0.0",
        "left_sample": "999.9",
        "right_fallback": "",
        "right_sample": "",
    },
)
AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP = 9
AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE = (
    "QGroupBox {"
    " border: 1px solid #9CA3AF;"
    f" margin-top: {AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP}px;"
    f" padding-top: {AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP}px;"
    "}"
    " QGroupBox::title {"
    " subcontrol-origin: margin;"
    " subcontrol-position: top left;"
    " left: 4px;"
    " padding: 0 4px;"
    " background-color: palette(window);"
    "}"
)


def routine_tree_title_text(display_name: object) -> str:
    """종목 제목은 7자까지 표시하고 8자부터 7자로 축약한다."""
    text = str(display_name or "").strip()
    if len(text) <= ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS:
        return text
    return f"{text[:ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS]}..."


def auto_trade_setting_historical_fixture_enabled() -> bool:
    app_env = str(
        os.environ.get(AUTO_TRADE_SETTING_APP_ENV, "") or ""
    ).strip().lower()
    if app_env in {"production", "prod"}:
        return False
    if app_env not in {"", "development", "dev", "test", "testing"}:
        return False
    fixture_value = str(
        os.environ.get(AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV, "")
        or ""
    ).strip().lower()
    if fixture_value in {"0", "false", "no", "off"}:
        return False
    return fixture_value in {"1", "true", "yes", "on"}


def auto_trade_projected_instance_ids(
    instances: list[object],
    *,
    groups: list[object] | None = None,
    stocks: list[dict[str, object]] | None = None,
) -> set[str]:
    """Return instances backed by the canonical Group/stock assignment relation."""
    projection = build_main_group_projection(
        get_group_records() if groups is None else groups,
        instances,
        read_base_stocks() if stocks is None else stocks,
    )
    return {
        projected_instance.instance_id
        for projected_group in projection
        for projected_instance in projected_group.instances
    }


def auto_trade_initial_read_snapshot(window) -> dict[str, object] | None:
    snapshot = getattr(window, "_auto_trade_initial_read_snapshot", None)
    return snapshot if isinstance(snapshot, dict) else None


def routine_tree_instance_title_text(display_name: object) -> str:
    return tree_title_text(display_name)


def routine_tree_parent_identity(metadata: dict[str, object]) -> str:
    return str(
        metadata.get("group_id", "")
        or metadata.get("definition_id", "")
        or ""
    ).strip()


def routine_tree_title_width(font_metrics) -> int:
    samples = (
        "가" * ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS,
        ("가" * ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS) + "...",
    )
    return max(
        max(font_metrics.horizontalAdvance(sample), font_metrics.boundingRect(sample).width())
        for sample in samples
    )


def routine_tree_layout_metrics(font: QFont) -> dict[str, int]:
    """Return font-derived geometry for the shared tree identity region."""
    base_metrics = QFontMetrics(font)
    label_font = QFont(QApplication.font("QLabel"))
    label_metrics = QFontMetrics(label_font)
    parent_font = QFont(font)
    parent_font.setPointSize(parent_font.pointSize() + 1)
    parent_font.setWeight(QFont.DemiBold)
    parent_metrics = QFontMetrics(parent_font)
    parent_label_font = QFont(label_font)
    parent_label_font.setPointSize(parent_label_font.pointSize() + 1)
    parent_label_font.setWeight(QFont.DemiBold)
    parent_label_metrics = QFontMetrics(parent_label_font)

    # Keep the established Group origin while deriving child depth from the
    # footprint reserved for the hierarchy icon.
    outer_margin = 6
    # The live 32-bit layout leaves 24 px after efficiency with 1 px gaps.
    # Symmetric 5/4/5 px separator gaps consume that space while retaining a
    # 2 px right-edge safety margin.
    performance_separator_edge_side_gap = 5
    performance_separator_inner_side_gap = 4
    performance_trailing_margin = 0
    item_gap = 4
    parent_icon_width = 28
    child_icon_width = 18
    hierarchy_step = max(item_gap, child_icon_width // 2)
    count_badge_width = 64
    parent_title_width = max(
        tree_title_slot_width(metrics, padding=0)
        for metrics in (parent_metrics, parent_label_metrics)
    )
    child_title_width = max(
        tree_title_slot_width(metrics, padding=0)
        for metrics in (base_metrics, label_metrics)
    )
    stock_title_width = routine_tree_title_width(base_metrics)
    hangul_width = max(
        width
        for metrics in (base_metrics, label_metrics)
        for width in (
            metrics.horizontalAdvance("가"),
            metrics.boundingRect("가").width(),
        )
    )
    group_metric_gap = max(1, hangul_width // 2)
    compact_metric_gap = max(1, hangul_width // 2)
    parent_title_x = parent_icon_width + item_gap
    child_left_shift = item_gap
    child_title_shift = compact_metric_gap
    original_instance_title_x = parent_title_x + hierarchy_step - child_left_shift
    original_stock_title_x = parent_title_x + (hierarchy_step * 2) - child_left_shift
    instance_title_x = original_instance_title_x - child_title_shift
    stock_title_x = original_stock_title_x - child_title_shift
    instance_metric_x = instance_title_x + child_title_width + compact_metric_gap
    instance_metric_gap = max(
        0,
        instance_metric_x - instance_title_x - child_title_width,
    )
    stock_metric_gap = max(
        0,
        instance_metric_x - stock_title_x - stock_title_width,
    )
    instance_indent = max(0, instance_title_x - child_icon_width - item_gap)
    stock_indent = max(0, stock_title_x - child_icon_width - item_gap)
    parent_identity_width = parent_title_x + parent_title_width + count_badge_width
    instance_identity_width = instance_metric_x
    stock_identity_width = stock_title_x + stock_title_width + stock_metric_gap
    identity_width = max(
        parent_identity_width,
        instance_identity_width,
        stock_identity_width,
    )
    return {
        "outer_margin": outer_margin,
        "performance_separator_edge_side_gap": performance_separator_edge_side_gap,
        "performance_separator_inner_side_gap": performance_separator_inner_side_gap,
        "performance_trailing_margin": performance_trailing_margin,
        "item_gap": item_gap,
        "hierarchy_step": hierarchy_step,
        "child_left_shift": child_left_shift,
        "child_title_shift": child_title_shift,
        "parent_icon_width": parent_icon_width,
        "child_icon_width": child_icon_width,
        "count_badge_width": count_badge_width,
        "parent_title_width": parent_title_width,
        "child_title_width": child_title_width,
        "stock_title_width": stock_title_width,
        "group_metric_gap": group_metric_gap,
        "instance_metric_gap": instance_metric_gap,
        "stock_metric_gap": stock_metric_gap,
        "instance_indent": instance_indent,
        "stock_indent": stock_indent,
        "parent_identity_width": parent_identity_width,
        "instance_identity_width": instance_identity_width,
        "stock_identity_width": stock_identity_width,
        "identity_width": identity_width,
    }


class StockPositionMetricDelegate(QStyledItemDelegate):
    """보유/가격/손익/매매 셀의 숫자 슬롯을 우측 정렬해 그린다."""

    LABEL_BY_COLUMN = {
        7: "보유",
        8: "가격",
        9: "수익",
        10: "매매",
    }

    def paint(self, painter, option, index) -> None:
        painter.setFont(option.font)
        font_metrics = QFontMetrics(option.font)
        text = str(index.data(Qt.DisplayRole) or "")
        label_hint = self.LABEL_BY_COLUMN.get(index.column())
        foreground = index.data(Qt.ForegroundRole)
        color = (
            foreground.color()
            if isinstance(foreground, QBrush)
            else option.palette.text().color()
        )
        metric_option = QStyleOptionViewItem(option)
        metric_option.text = ""
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, metric_option, painter, option.widget)
        if draw_stock_position_metric(
            painter,
            option.rect,
            text,
            color,
            label_hint=label_hint,
            compact=True,
            compact_margins=self._operation_column_margins(
                option.widget,
                font_metrics,
            ),
        ):
            return
        super().paint(painter, option, index)

    @staticmethod
    def _operation_column_margins(
        table,
        font_metrics: QFontMetrics,
    ) -> tuple[int, int]:
        if table is None:
            return 2, 2
        text_width = font_metrics.horizontalAdvance("09:30~13:30")
        column_width = table.columnWidth(2)
        spare_width = max(0, column_width - text_width)
        left_margin = spare_width // 2
        right_margin = spare_width - left_margin
        return left_margin, right_margin


class SelectedTextReadableDelegate(QStyledItemDelegate):
    """옅은 선택 배경에서도 셀의 상태별 foreground를 유지한다."""

    def paint(self, painter, option, index) -> None:
        if option.state & QStyle.State_Selected:
            readable_option = QStyleOptionViewItem(option)
            self.initStyleOption(readable_option, index)
            foreground = index.data(Qt.ForegroundRole)
            if isinstance(foreground, QBrush):
                readable_option.palette.setBrush(QPalette.HighlightedText, foreground)
            style = option.widget.style() if option.widget is not None else QApplication.style()
            style.drawControl(
                QStyle.CE_ItemViewItem,
                readable_option,
                painter,
                option.widget,
            )
            return
        super().paint(painter, option, index)


class ClippedTextItemDelegate(QStyledItemDelegate):
    """Clip overflowing text at the cell edge without drawing an ellipsis."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        selected_text_color: QColor | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_text_color = selected_text_color

    def paint(self, painter, option, index) -> None:
        clipped_option = QStyleOptionViewItem(option)
        self.initStyleOption(clipped_option, index)
        style = option.widget.style() if option.widget is not None else QApplication.style()
        text = clipped_option.text
        text_rect = style.subElementRect(
            QStyle.SE_ItemViewItemText,
            clipped_option,
            option.widget,
        )
        clipped_option.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, clipped_option, painter, option.widget)
        painter.save()
        painter.setClipRect(text_rect, Qt.IntersectClip)
        painter.setFont(option.font)
        if option.state & QStyle.State_Selected and self.selected_text_color is not None:
            painter.setPen(self.selected_text_color)
        else:
            painter.setPen(
                clipped_option.palette.highlightedText().color()
                if option.state & QStyle.State_Selected
                else clipped_option.palette.text().color()
            )
        painter.drawText(text_rect, int(clipped_option.displayAlignment), text)
        painter.restore()


class AutoTradeNotificationPopup(QFrame):
    """자동매매설정창 안에서 쓰는 버튼 없는 비모달 자동닫힘 알림."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setObjectName("autoTradeNotificationPopup")
        self._label = QLabel(self)
        self._label.setObjectName("autoTradeNotificationText")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(self._label)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.setStyleSheet(
            """
            QFrame#autoTradeNotificationPopup {
                background-color: #111827;
                border: 1px solid #374151;
                border-radius: 6px;
            }
            QLabel#autoTradeNotificationText {
                color: #ffffff;
                font-weight: 600;
            }
            """
        )

    def show_message(self, message: str, timeout_ms: int = 2500) -> None:
        self._hide_timer.stop()
        self._label.setText(str(message or ""))
        self.adjustSize()
        self._move_to_parent_center()
        self.show()
        self.raise_()
        if timeout_ms > 0:
            self._hide_timer.start(timeout_ms)

    def hideEvent(self, event: object) -> None:
        self._hide_timer.stop()
        super().hideEvent(event)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() in (QEvent.Close, QEvent.Hide):
            self.hide()
        return super().eventFilter(watched, event)

    def text(self) -> str:
        return self._label.text()

    def button_count(self) -> int:
        return 0

    def _move_to_parent_center(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        parent_rect = parent.frameGeometry()
        popup_rect = self.frameGeometry()
        center = parent_rect.center()
        popup_rect.moveCenter(center)
        self.move(popup_rect.topLeft())


PROJECT_ROOT = Path(__file__).resolve().parent
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
REAL_TRADE_GUARD_PATH = PROJECT_ROOT / "runtime" / "real_trade_guard.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
REALIZED_PNL_LEDGER_PATH = PROJECT_ROOT / "runtime" / "realized_pnl.json"


def startup_recovery_action_allowed(window, action: str) -> bool:
    """Enforce session recovery only for the real MainWindow production caller."""
    if not isinstance(window, AutoTradeSettingWindow):
        return True
    try:
        parent = persistent_feature_owner(window)
    except Exception:
        return True
    if "_startup_recovery_result" not in getattr(parent, "__dict__", {}):
        return True
    if not callable(getattr(type(parent), "startup_recovery_session_ready", None)):
        return True
    checker = getattr(window, "require_startup_recovery_session", None)
    if callable(checker):
        return checker(action) is True
    return True


def handle_kiwoom_raw_chejan_event(
    raw_event: dict[str, object],
    live_context: dict[str, object] | None = None,
) -> dict[str, object]:
    if str(raw_event.get("gubun") or "").strip() == "1":
        result = record_broker_holding_snapshot(
            raw_event,
            BROKER_HOLDINGS_PATH,
            POSITIONS_PATH,
            context=live_context or {},
        )
        result["recorded"] = result.get("holding_recorded") is True
        result["stage"] = result.get("holding_stage", "broker_holding_snapshot")
        result["balance_event_received"] = True
        return result

    normalized = normalize_kiwoom_chejan_event(raw_event)
    if normalized.get("normalized") is not True:
        return {"recorded": False, "stage": "normalize", "normalized_event": normalized}

    queue_path = ORDER_QUEUE_PATH
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"recorded": False, "stage": "queue_read", "blocked_reasons": [str(exc)]}
    orders = data.get("orders") if isinstance(data, dict) else None
    if not isinstance(orders, list):
        return {"recorded": False, "stage": "queue_structure", "blocked_reasons": ["queue orders must be a list"]}

    broker_order_no = str(normalized.get("broker_order_no") or "").strip()
    account_no = str(normalized.get("account_no") or "").strip()
    code = str(normalized.get("code") or "").strip()
    side = str(normalized.get("side") or "").strip().upper()
    candidates: list[dict[str, object]] = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status not in {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN", "BROKER_ACCEPTED", "PARTIALLY_FILLED", "FILLED"}:
            continue
        if str(item.get("account_no") or "").strip() not in {"", account_no}:
            continue
        if str(item.get("code") or "").strip() != code:
            continue
        if str(item.get("side") or "").strip().upper() != side:
            continue
        item_broker_order_no = str(item.get("broker_order_no") or "").strip()
        if item_broker_order_no and broker_order_no and item_broker_order_no != broker_order_no:
            continue
        if (
            status == "FILLED"
            and not _has_pending_chejan_reconciliation_for_event(item, normalized)
            and existing_chejan_record_result(item, normalized) is None
        ):
            continue
        candidates.append(dict(item))

    if len(candidates) != 1:
        return {
            "recorded": False,
            "stage": "chejan_target_match",
            "normalized_event": normalized,
            "blocked_reasons": [f"matching send order record count is {len(candidates)}"],
        }

    review = review_chejan_event(normalized, order_record=candidates[0])
    if review.get("chejan_review_ok") is not True:
        return {
            "recorded": False,
            "stage": "chejan_review",
            "normalized_event": normalized,
            "review_result": review,
            "blocked_reasons": list(review.get("blocked_reasons") or []),
        }
    recorded = record_chejan_event(
        review,
        normalized,
        queue_path,
        context=live_context or {},
    )
    response = {
        "recorded": recorded.get("recorded") is True or recorded.get("committed") is True,
        "stage": "chejan_record",
        "normalized_event": normalized,
        "review_result": review,
        "record_result": recorded,
        "blocked_reasons": list(recorded.get("blocked_reasons") or []),
    }
    if response["recorded"] is True:
        response["close_routine_final_sell_marker"] = (
            _mark_close_routine_final_sell_from_broker_acceptance(
                candidates[0],
                normalized,
                recorded,
            )
        )
    downstream_source = recorded
    if response["recorded"] is not True and _chejan_record_duplicate(recorded):
        if _has_pending_chejan_reconciliation_for_event(candidates[0], normalized):
            reconstructed = existing_chejan_record_result(candidates[0], normalized, recorded)
        else:
            reconstructed = None
        if reconstructed is not None:
            downstream_source = reconstructed
            response["duplicate_reprocess"] = True
            live_context = dict(live_context or {})
            live_context["chejan_reconciliation_reprocess"] = True
        else:
            response["duplicate_noop"] = True
            return response

    fill_result, position_result, realized_pnl_result, reconciliation_result = _record_fill_and_position_from_chejan(
        downstream_source,
        normalized,
        candidates[0],
        live_context or {},
    )
    if fill_result is not None:
        response["fill_result"] = fill_result
        if fill_result.get("fill_recorded") is not True:
            response["manual_reconciliation_required"] = True
            response["fill_blocked_reasons"] = list(fill_result.get("blocked_reasons") or [])
    if position_result is not None:
        response["position_result"] = position_result
        if position_result.get("position_updated") is not True:
            response["manual_reconciliation_required"] = True
            response["position_blocked_reasons"] = list(position_result.get("blocked_reasons") or [])
    if realized_pnl_result is not None:
        response["realized_pnl_result"] = realized_pnl_result
        if realized_pnl_result.get("realized_pnl_recorded") is not True:
            response["manual_reconciliation_required"] = True
            response["realized_pnl_blocked_reasons"] = list(realized_pnl_result.get("blocked_reasons") or [])
    if reconciliation_result is not None:
        response["reconciliation_result"] = reconciliation_result
        response["reconciliation_persisted"] = reconciliation_result.get("reconciliation_persisted") is True
        if response["reconciliation_persisted"] is not True:
            response["manual_reconciliation_required"] = True
            response["reconciliation_persist_failed_reasons"] = list(
                reconciliation_result.get("reconciliation_persist_failed_reasons")
                or reconciliation_result.get("blocked_reasons")
                or ["chejan reconciliation state was not persisted"]
            )
    return response


def _order_routine_instance_id(order: dict[str, object]) -> str:
    for container in (
        order,
        order.get("execution_intent"),
        order.get("order_provenance"),
    ):
        if not isinstance(container, dict):
            continue
        value = str(
            container.get("routine_instance_id")
            or container.get("assigned_routine_instance_id")
            or ""
        ).strip()
        if value:
            return value
    return ""


def _mark_close_routine_final_sell_from_broker_acceptance(
    order: dict[str, object],
    normalized_event: dict[str, object],
    record_result: dict[str, object],
) -> dict[str, object]:
    """Persist the routine-close final SELL marker only at broker acceptance."""
    event_type = str(
        record_result.get("event_type") or normalized_event.get("event_type") or ""
    ).strip().upper()
    if event_type not in {"ORDER_ACCEPTED", "ORDER_OPEN"}:
        return {"attempted": False, "marked": False, "reason": "not broker acceptance"}
    if str(order.get("source") or "").strip() != "routine_signals":
        return {"attempted": False, "marked": False, "reason": "not routine signal order"}
    if str(order.get("side") or "").strip().upper() != "SELL":
        return {"attempted": False, "marked": False, "reason": "not SELL order"}

    code = str(order.get("code") or normalized_event.get("code") or "").strip()
    routine_instance_id = _order_routine_instance_id(order)
    matches: list[Path] = []
    for stock_dir_value in all_registered_stock_dirs():
        stock_dir = Path(stock_dir_value)
        stock_code, _ = parse_stock_folder_name(stock_dir.name)
        if stock_code != code:
            continue
        config = read_json_dict(stock_dir / "config.json")
        assigned_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        if routine_instance_id and assigned_instance_id != routine_instance_id:
            continue
        matches.append(stock_dir)

    if len(matches) != 1:
        return {
            "attempted": True,
            "marked": False,
            "reason": f"runtime stock match count is {len(matches)}",
        }

    stock_dir = matches[0]
    state = read_json_dict(stock_dir / "state.json")
    display_status = str(state.get("status") or "")
    decision = decide_routine_order_for_stock_dir(
        stock_dir,
        "SELL",
        display_status=display_status,
    )
    if decision.get("allowed") is not True or decision.get(
        "mark_close_final_sell_after_order"
    ) is not True:
        return {
            "attempted": True,
            "marked": False,
            "reason": str(decision.get("reason") or "routine close marker not required"),
            "stock_dir": str(stock_dir),
        }

    before_marker = (
        state.get("close_routine_final_sell_ordered"),
        state.get("close_routine_final_sell_ordered_at"),
    )
    write_succeeded = mark_routine_order_accepted_for_stock_dir(
        stock_dir,
        decision,
        source="kiwoom_chejan",
    )
    saved_state = read_json_dict(stock_dir / "state.json") if write_succeeded else {}
    saved_marker = (
        saved_state.get("close_routine_final_sell_ordered"),
        saved_state.get("close_routine_final_sell_ordered_at"),
    )
    marked = bool(
        write_succeeded
        and saved_state.get("close_routine_final_sell_ordered") is True
        and str(saved_state.get("close_routine_final_sell_source") or "").strip()
        == "kiwoom_chejan"
    )
    return {
        "attempted": True,
        "marked": marked,
        "changed": marked and saved_marker != before_marker,
        "read_back_verified": marked,
        "reason": (
            "broker accepted routine SELL"
            if marked
            else "state write or read-back verification failed"
        ),
        "stock_dir": str(stock_dir),
    }


def _clean_runtime_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _chejan_record_duplicate(result: dict[str, object]) -> bool:
    return result.get("duplicate") is True or result.get("idempotent") is True or _clean_runtime_text(result.get("record_stage")) == "duplicate_event"


def _has_pending_chejan_reconciliation_for_event(
    order_record: dict[str, object],
    normalized_event: dict[str, object],
) -> bool:
    broker_order_no = _clean_runtime_text(order_record.get("broker_order_no") or normalized_event.get("broker_order_no"))
    event_identity, _ = chejan_event_identity(normalized_event, broker_order_no=broker_order_no)
    items = order_record.get("chejan_reconciliation_items")
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("required") is True
        and _clean_runtime_text(item.get("event_identity")).upper() == event_identity
        for item in items
    )


def _position_result_ok(result: dict[str, object] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("position_updated") is True:
        return True
    return _clean_runtime_text(result.get("position_stage")) in {
        "duplicate_fill",
        "fill_delta_noop",
        "later_cumulative_fill_already_applied",
    }


def _record_fill_and_position_from_chejan(
    chejan_result: dict[str, object],
    normalized_event: dict[str, object],
    order_record: dict[str, object],
    live_context: dict[str, object],
) -> tuple[
    dict[str, object] | None,
    dict[str, object] | None,
    dict[str, object] | None,
    dict[str, object] | None,
]:
    if chejan_result.get("recorded") is not True or chejan_result.get("next_stage") != "FILL_RECORD_REQUIRED":
        return None, None, None, None

    completed_steps = ["QUEUE_LIFECYCLE"]
    live_context = dict(live_context)
    provenance = order_record.get("routine_provenance")
    if isinstance(provenance, dict):
        live_context.setdefault(
            "routine_instance_id",
            str(provenance.get("routine_instance_id") or "").strip(),
        )
    else:
        live_context.setdefault(
            "routine_instance_id",
            str(order_record.get("routine_instance_id") or "").strip(),
        )
    fill_result = record_execution_fill(
        chejan_result,
        normalized_event,
        FILLS_PATH,
        context=live_context,
    )
    fill_record = fill_result.get("fill_record") if isinstance(fill_result, dict) else None
    if not isinstance(fill_record, dict):
        fill_record = find_existing_execution_fill_record(FILLS_PATH, chejan_result, normalized_event)
    if not isinstance(fill_record, dict):
        reconciliation = mark_chejan_reconciliation_state(
            ORDER_QUEUE_PATH,
            chejan_result,
            required=True,
            failed_stage="FILL_RECORD",
            completed_steps=completed_steps,
            reasons=list(fill_result.get("blocked_reasons") or []) if isinstance(fill_result, dict) else ["fill record failed"],
            context=live_context,
        )
        return fill_result, None, None, reconciliation

    completed_steps.append("FILL_RECORD")
    position_result = update_position_from_fill(
        fill_result if isinstance(fill_result, dict) and fill_result.get("fill_recorded") is True else {
            "fill_recorded": True,
            "fill_stage": "execution_fill_already_recorded",
            "next_stage": "POSITION_UPDATE_REQUIRED",
            "fill_id": fill_record.get("fill_id"),
            "event_type": fill_record.get("event_type"),
            "order_id": fill_record.get("order_id"),
            "order_queued_id": fill_record.get("order_queued_id"),
            "broker_order_no": fill_record.get("broker_order_no"),
            "request_hash": fill_record.get("request_hash"),
            "lock_id": fill_record.get("lock_id"),
            "execution_id": fill_record.get("execution_id"),
            "filled_quantity": fill_record.get("filled_quantity"),
            "filled_price": fill_record.get("filled_price"),
            "blocked_reasons": [],
            "warnings": [],
        },
        fill_record,
        POSITIONS_PATH,
        context=live_context,
    )
    if not _position_result_ok(position_result):
        reconciliation = mark_chejan_reconciliation_state(
            ORDER_QUEUE_PATH,
            chejan_result,
            required=True,
            failed_stage="POSITION_UPDATE",
            completed_steps=completed_steps,
            reasons=list(position_result.get("blocked_reasons") or []) if isinstance(position_result, dict) else ["position update failed"],
            context=live_context,
        )
        return fill_result, position_result, None, reconciliation

    completed_steps.append("POSITION_UPDATE")
    if _clean_runtime_text(position_result.get("position_stage")) == "later_cumulative_fill_already_applied":
        reconciliation = mark_chejan_reconciliation_state(
            ORDER_QUEUE_PATH,
            chejan_result,
            required=False,
            completed_steps=completed_steps,
            context=live_context,
        )
        return fill_result, position_result, None, reconciliation

    realized_pnl_result = None
    canonical_root = Path(FILLS_PATH).parent
    if canonical_root.name == "runtime":
        canonical_root = canonical_root.parent
    canonical_enabled = (
        (canonical_root / "groups" / "registry.json").is_file()
        and (canonical_root / "stocks").is_dir()
    )
    fill_side = _clean_runtime_text(fill_record.get("side")).upper()
    if canonical_enabled and fill_side == "BUY":
        lot_result = record_buy_entry_lot(canonical_root, fill_record, position_result)
        if lot_result.get("success") is not True:
            reconciliation = mark_chejan_reconciliation_state(
                ORDER_QUEUE_PATH,
                chejan_result,
                required=True,
                failed_stage="ENTRY_LOT_LINKAGE",
                completed_steps=completed_steps,
                reasons=list(lot_result.get("blocked_reasons") or ["BUY entry-lot linkage failed"]),
                context=live_context,
            )
            return fill_result, position_result, None, reconciliation
        completed_steps.append("ENTRY_LOT_LINKAGE")
    if fill_side == "SELL":
        fifo_result = None
        if canonical_enabled:
            fifo_result = prepare_sell_fifo_realization(canonical_root, fill_record, position_result)
            if fifo_result.get("success") is not True:
                reconciliation = mark_chejan_reconciliation_state(
                    ORDER_QUEUE_PATH,
                    chejan_result,
                    required=True,
                    failed_stage="FIFO_ENTRY_LOT",
                    completed_steps=completed_steps,
                    reasons=list(fifo_result.get("blocked_reasons") or ["SELL FIFO ownership failed"]),
                    context=live_context,
                )
                return fill_result, position_result, None, reconciliation
        realized_context = dict(live_context)
        realized_context["fills_path"] = str(FILLS_PATH)
        if fifo_result is not None:
            realized_context["canonical_fifo_allocations"] = fifo_result["allocations"]
        realized_pnl_result = record_realized_pnl(
            fill_record,
            position_result,
            order_record,
            REALIZED_PNL_LEDGER_PATH,
            context=realized_context,
        )
        if realized_pnl_result.get("realized_pnl_recorded") is not True:
            reconciliation = mark_chejan_reconciliation_state(
                ORDER_QUEUE_PATH,
                chejan_result,
                required=True,
                failed_stage="REALIZED_PNL_LEDGER",
                completed_steps=completed_steps,
                reasons=list(realized_pnl_result.get("blocked_reasons") or ["realized P/L ledger write failed"]),
                context=live_context,
            )
            return fill_result, position_result, realized_pnl_result, reconciliation
        completed_steps.append("REALIZED_PNL_LEDGER")
        if fifo_result is not None:
            performance_result = append_performance_from_realization(
                canonical_root,
                fill_record,
                realized_pnl_result,
                fifo_result,
            )
            realized_pnl_result["performance_linkage_result"] = performance_result
            if performance_result.get("success") is not True:
                reconciliation = mark_chejan_reconciliation_state(
                    ORDER_QUEUE_PATH,
                    chejan_result,
                    required=True,
                    failed_stage="PERFORMANCE_LEDGER",
                    completed_steps=completed_steps,
                    reasons=list(performance_result.get("blocked_reasons") or ["canonical Performance Ledger append failed"]),
                    context=live_context,
                )
                return fill_result, position_result, realized_pnl_result, reconciliation
            completed_steps.append("PERFORMANCE_LEDGER")
    reconciliation = mark_chejan_reconciliation_state(
        ORDER_QUEUE_PATH,
        chejan_result,
        required=False,
        completed_steps=completed_steps,
        context=live_context,
    )
    return fill_result, position_result, realized_pnl_result, reconciliation


def get_group_dirs() -> list[Path]:
    """Return project-root Group paths used by stock assignment operations."""
    return registry_get_group_dirs()




def routine_display_name(routine_dir: Path) -> str:
    """루틴 원본 경로에서 GUI 표시 루틴명을 반환한다."""
    return registry_routine_display_name(routine_dir)









def default_operation_policy() -> dict[str, object]:
    """운영환경설정 기본값.

    현재 단계에서는 UI/저장 구조를 먼저 확정한다.
    실제 자동판정 엔진 연결은 후속 패치에서 단계적으로 반영한다.
    """
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {"enabled": False, "name": "추가시간1", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "추가시간2", "start_time": "15:40:00", "end_time": "19:50:00"},
            {"enabled": False, "name": "추가시간3", "start_time": "", "end_time": ""},
        ],
        "scheduled_operation": {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        },
        "manual_operation": {
            "use_regular_market": True,
            "use_extra_session_1": False,
            "use_extra_session_2": False,
            "use_extra_session_3": False,
            "enabled_status": "매수/매도",
            "disabled_status": "감시/대기",
            "use_liquidation_policy": False,
        },
        "auto_close": {
            "method": "루틴매도신호",
            "profit_percent": "",
            "loss_percent": "",
        },
        "early_close": {
            "method": "시장가",
            "profit_percent": "",
            "loss_percent": "",
        },
        "liquidation": {
            "minutes_before_regular_close": "5",
            "method": "이월",
        },
        "updated_at": "",
    }


def read_operation_policy() -> dict[str, object]:
    default = default_operation_policy()
    if not OPERATION_POLICY_PATH.exists():
        return default
    try:
        data = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default

    # 얕은 병합: 누락된 상위 항목은 기본값으로 보완한다.
    merged = default_operation_policy()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)  # type: ignore[index]
        else:
            merged[key] = value
    scheduled = merged.get("scheduled_operation")
    if isinstance(scheduled, dict):
        scheduled.pop("after_buy_end_status", None)
    return merged


POLICY_OVERRIDE_VALUE_FIELDS = (
    "policy_override_enabled",
    "operation_policy_override",
    "manual_operation_override",
    "scheduled_operation_override",
    "auto_close_override",
    "early_close_override",
    "liquidation_override",
)
POLICY_OVERRIDE_EDITABLE_FIELDS = (
    "policy_override_enabled",
    "policy_override_memo",
)
POLICY_OVERRIDE_WRITABLE_FIELDS = frozenset(
    POLICY_OVERRIDE_VALUE_FIELDS
    + (
        "policy_override_memo",
        "policy_override_updated_at",
        "policy_override_reset_at",
        "updated_at",
    )
)


def _stock_policy_override_expected_fields(
    config: dict[str, object],
    field_keys: tuple[str, ...],
) -> dict[str, object]:
    return {
        key: config[key] if key in config else STOCK_CONFIG_EXPECTED_MISSING
        for key in field_keys
    }


def _stock_policy_override_write_result(
    *,
    ok: bool,
    changed: bool,
    field_keys: tuple[str, ...],
    reason_code: str,
    read_back_verified: bool,
) -> StockConfigWriteResult:
    return StockConfigWriteResult(
        ok=ok,
        changed=changed,
        field_keys=field_keys,
        conflict_detected=False,
        read_back_verified=read_back_verified,
        reason_code=reason_code,
    )


def _patch_stock_policy_override_config(
    stock_dir: Path,
    code: str,
    patch: dict[str, object],
    *,
    expected_fields: dict[str, object],
) -> StockConfigWriteResult:
    field_keys = tuple(patch.keys()) if isinstance(patch, dict) else ()
    if (
        not isinstance(patch, dict)
        or not patch
        or not isinstance(expected_fields, dict)
        or any(key not in POLICY_OVERRIDE_WRITABLE_FIELDS for key in patch)
        or any(key not in patch for key in expected_fields)
    ):
        return _stock_policy_override_write_result(
            ok=False,
            changed=False,
            field_keys=field_keys,
            reason_code=STOCK_CONFIG_WRITE_INVALID_PATCH,
            read_back_verified=False,
        )

    target_dir = Path(stock_dir)
    stocks_dir = target_dir.parent
    clean_code = normalize_stock_code(target_dir.name.partition("_")[0])
    requested_code = normalize_stock_code(str(code or ""))
    if stocks_dir.name != "stocks" or not clean_code or clean_code != requested_code:
        return _stock_policy_override_write_result(
            ok=False,
            changed=False,
            field_keys=field_keys,
            reason_code=STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
            read_back_verified=False,
        )
    repository = StockRepository(stocks_dir.parent)
    if repository.resolve_stock_dir(clean_code).resolve() != target_dir.resolve():
        return _stock_policy_override_write_result(
            ok=False,
            changed=False,
            field_keys=field_keys,
            reason_code=STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
            read_back_verified=False,
        )
    return repository.patch_stock_config(
        clean_code,
        patch,
        expected_fields=expected_fields,
    )


def _policy_override_patch_matches_readback(
    patch: dict[str, object],
    read_back: dict[str, object],
) -> bool:
    return all(
        (key not in read_back)
        if value is STOCK_CONFIG_DELETE_FIELD
        else (key in read_back and read_back[key] == value)
        for key, value in patch.items()
    )


def _policy_override_display_value(config: dict[str, object], key: str) -> object:
    if key == "policy_override_enabled":
        return bool(config.get(key, False))
    if key == "policy_override_memo":
        return str(config.get(key, "") or "").strip()
    return config.get(key)


class StockPolicyOverrideDialog(QDialog):
    """개별종목 예외설정 1차 UI.

    환경설정이 디폴트이고, 이 창은 해당 종목만 예외로 둘 때 사용한다.
    전체 리셋은 종목별 예외 설정값을 제거한다.
    """

    OVERRIDE_KEYS = POLICY_OVERRIDE_VALUE_FIELDS

    def __init__(self, stock_dir: Path, code: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stock_dir = stock_dir
        self.code = code
        self.name = name
        self.config_path = stock_dir / "config.json"
        self.config = read_json_dict(self.config_path) or default_config()
        self._policy_override_opening_config = deepcopy(self.config)
        self._last_config_write_result = None
        self.setWindowTitle(f"개별종목 설정 - {code} {name}")
        self.resize(520, 360)

        self.use_override = QCheckBox("이 종목만 개별설정 사용")
        self.memo = QTextEdit()
        self.memo.setPlaceholderText("개별 예외 사유 또는 메모")
        self.memo.setMinimumHeight(90)
        self.btn_reset_all = QPushButton("환경설정값으로 전체 리셋")
        self.btn_save = QPushButton("저장")
        self.btn_cancel = QPushButton("취소")

        self._setup_ui()
        self.load_config_to_widgets()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        info = QLabel(
            "환경설정은 기본값입니다.\n"
            "선택 종목만 예외로 적용합니다.\n"
            "현재 1차 구현은 예외 사용 여부와 메모, 전체 리셋 흐름을 먼저 제공합니다."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(self.use_override)
        layout.addWidget(QLabel("개별설정 메모"))
        layout.addWidget(self.memo)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.btn_reset_all)
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.btn_reset_all.clicked.connect(self.reset_all_to_global)
        self.btn_save.clicked.connect(self.save_override)
        self.btn_cancel.clicked.connect(self.reject)

    def load_config_to_widgets(self) -> None:
        self.use_override.setChecked(bool(self.config.get("policy_override_enabled", False)))
        self.memo.setPlainText(str(self.config.get("policy_override_memo", "")))

    def write_config(
        self,
        patch: dict[str, object],
        *,
        expected_fields: dict[str, object],
    ) -> StockConfigWriteResult:
        result = _patch_stock_policy_override_config(
            self.stock_dir,
            self.code,
            patch,
            expected_fields=expected_fields,
        )
        read_back = read_json_dict(self.config_path)
        self._latest_config_readback = read_back
        if result.ok and not _policy_override_patch_matches_readback(patch, read_back):
            result = StockConfigWriteResult(
                ok=False,
                changed=result.changed,
                field_keys=result.field_keys,
                conflict_detected=False,
                read_back_verified=False,
                reason_code=STOCK_CONFIG_WRITE_READBACK_FAILED,
                before_fingerprint=result.before_fingerprint,
                after_fingerprint=result.after_fingerprint,
            )
        if result.ok:
            self.config = read_back
        self._last_config_write_result = result
        return result

    def _append_override_changed(
        self,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        changes: list[dict[str, object]] = []
        for key in self.OVERRIDE_KEYS:
            changes.extend(
                _setting_leaf_changes(
                    before.get(key),
                    after.get(key),
                    prefix=key,
                )
            )
        before_memo = bool(str(before.get("policy_override_memo") or "").strip())
        after_memo = bool(str(after.get("policy_override_memo") or "").strip())
        if before_memo != after_memo:
            changes.append(
                {
                    "field_key": "policy_override_memo_present",
                    "before": before_memo,
                    "after": after_memo,
                }
            )
        if not changes:
            return
        append_production_event(
            "SETTING_CHANGED",
            result="SUCCESS",
            source="STOCK_POLICY_OVERRIDE_DIALOG",
            template_args={"target": "개별종목 설정"},
            target_type="STOCK",
            target_id=self.code,
            target_name=self.name,
            stock_code=self.code,
            stock_name=self.name,
            changes=changes,
        )

    def save_override(self) -> None:
        opening_config = deepcopy(
            getattr(self, "_policy_override_opening_config", self.config)
        )
        desired = {
            "policy_override_enabled": bool(self.use_override.isChecked()),
            "policy_override_memo": self.memo.toPlainText().strip(),
        }
        semantic_patch = {
            key: desired[key]
            for key in POLICY_OVERRIDE_EDITABLE_FIELDS
            if desired[key] != _policy_override_display_value(opening_config, key)
        }
        before = read_json_dict(self.config_path)
        if not semantic_patch:
            self.config = before
            self._last_config_write_result = _stock_policy_override_write_result(
                ok=True,
                changed=False,
                field_keys=(),
                reason_code=STOCK_CONFIG_WRITE_NO_CHANGE,
                read_back_verified=True,
            )
            QMessageBox.information(self, "저장 완료", "개별종목 설정을 저장했습니다.")
            self.accept()
            return

        changed_at = now_text()
        patch = {
            **semantic_patch,
            "policy_override_updated_at": changed_at,
            "updated_at": changed_at,
        }
        expected_fields = _stock_policy_override_expected_fields(
            opening_config,
            tuple(semantic_patch),
        )
        try:
            result = self.write_config(patch, expected_fields=expected_fields)
            if not result.ok:
                raise RuntimeError(result.reason_code)
            saved = deepcopy(self.config)
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 저장")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 저장: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"개별종목 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        self._append_override_changed(before, saved)
        QMessageBox.information(self, "저장 완료", "개별종목 설정을 저장했습니다.")
        self.accept()

    def reset_all_to_global(self) -> None:
        opening_config = deepcopy(
            getattr(self, "_policy_override_opening_config", self.config)
        )
        reset_field_keys = self.OVERRIDE_KEYS + ("policy_override_memo",)
        reset_needed = bool(opening_config.get("policy_override_enabled", False)) or any(
            key in opening_config
            for key in self.OVERRIDE_KEYS[1:] + ("policy_override_memo",)
        )
        before = read_json_dict(self.config_path)
        if not reset_needed:
            self.config = before
            self._last_config_write_result = _stock_policy_override_write_result(
                ok=True,
                changed=False,
                field_keys=(),
                reason_code=STOCK_CONFIG_WRITE_NO_CHANGE,
                read_back_verified=True,
            )
            QMessageBox.information(
                self,
                "리셋 완료",
                "해당 종목의 개별설정을 환경설정값으로 전체 리셋했습니다.",
            )
            self.accept()
            return

        changed_at = now_text()
        patch = {
            "policy_override_enabled": False,
            **{
                key: STOCK_CONFIG_DELETE_FIELD
                for key in self.OVERRIDE_KEYS[1:] + ("policy_override_memo",)
            },
            "policy_override_reset_at": changed_at,
            "updated_at": changed_at,
        }
        expected_fields = _stock_policy_override_expected_fields(
            opening_config,
            reset_field_keys,
        )
        try:
            result = self.write_config(patch, expected_fields=expected_fields)
            if not result.ok:
                raise RuntimeError(result.reason_code)
            saved = deepcopy(self.config)
            if bool(saved.get("policy_override_enabled", False)):
                raise RuntimeError("개별종목 설정 리셋 후 검증이 일치하지 않습니다.")
            if any(key in saved for key in self.OVERRIDE_KEYS[1:]):
                raise RuntimeError("개별종목 설정 리셋 후 예외값이 남아 있습니다.")
            if str(saved.get("policy_override_memo") or ""):
                raise RuntimeError("개별종목 설정 리셋 후 메모가 남아 있습니다.")
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 전체 리셋")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 전체 리셋: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "리셋 오류", f"개별종목 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
            return
        self._append_override_changed(before, saved)
        QMessageBox.information(self, "리셋 완료", "해당 종목의 개별설정을 환경설정값으로 전체 리셋했습니다.")
        self.accept()


def handle_stock_name_operation_exclusion_double_click(
    host,
    target: tuple[Path, str, str],
) -> bool:
    stock_dir, _code, _name = target
    config = read_json_dict(stock_dir / "config.json") or default_config()
    requested_excluded = not is_operation_excluded(config)
    decision = auto_trade_operation_exclusion_mutation_decision(
        host,
        target,
        requested_excluded,
    )
    if decision.get("allowed") is not True:
        status_message = getattr(host, "statusBarMessage", None)
        if callable(status_message):
            status_message(
                OPERATION_EXCLUSION_REVIEW_BLOCK_MESSAGE
                if decision.get("reason_code") == "REVIEW_REQUIRED"
                else OPERATION_EXCLUSION_RUNNING_BLOCK_MESSAGE
            )
        return False
    changed = bool(
        auto_trade_toggle_stock_operation_exclusion(
            host,
            target,
            refresh=False,
        )
    )
    if not changed:
        return False

    def refresh_after_double_click(context=host) -> None:
        try:
            refresh_auto_trade_views(context)
        except RuntimeError:
            # The originating window may close before the queued UI refresh runs.
            return

    QTimer.singleShot(0, refresh_after_double_click)
    return True


def delete_routine_instance_with_existing_policy(
    window: QWidget,
    metadata: dict[str, object],
) -> None:
    """Apply the canonical RoutineInstance deletion policy for a UI owner."""
    if str(metadata.get("row_kind", "") or "") != "instance":
        return
    instance_id = str(metadata.get("instance_id", "") or "").strip()
    instance_name = str(metadata.get("instance_name", "") or "").strip()
    if not instance_id:
        return

    answer = QMessageBox.question(
        window,
        "등록삭제",
        f"'{instance_name or instance_id}' 루틴 등록을 삭제하시겠습니까?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return

    from routine_instance_deletion_service import (
        collect_routine_instance_deletion_scope,
        delete_routine_instance_completely,
    )

    try:
        scope = collect_routine_instance_deletion_scope(PROJECT_ROOT, instance_id)
        running_stock_dirs = [
            stock_dir
            for stock_dir, _code, _name in auto_trade_running_registered_operation_targets(window)
        ]
    except Exception as exc:
        QMessageBox.warning(window, "등록삭제", str(exc))
        return
    result = delete_routine_instance_completely(
        scope,
        running_stock_dirs=running_stock_dirs,
    )
    if not result.success:
        reason = result.error
        if result.blocked:
            reason = "\n".join(block.message for block in result.blocked)
        QMessageBox.warning(
            window,
            "등록삭제",
            reason or "루틴 등록을 삭제하지 못했습니다.",
        )
        return
    append_production_event(
        "ROUTINE_INSTANCE_DELETED",
        result="COMPLETED",
        source="gui_auto_trade_setting_window.delete_routine_instance_with_existing_policy",
        target_type="ROUTINE_INSTANCE",
        target_id=instance_id,
        target_name=instance_name,
        details={
            "unassigned_stock_codes": list(result.cleared_stock_codes),
            "before_assignment": instance_id,
            "after_assignment": "UNASSIGNED",
        },
    )
    if result.cleared_stock_codes:
        sync_auto_trade_monitoring_universe(window)
    refresh_auto_trade_views(window)


def clone_routine_instance_with_existing_policy(
    owner: QWidget,
    metadata: dict[str, object],
    *,
    owning_group_ids: set[str] | None = None,
    group_record: object | None = None,
) -> bool:
    """Clone one canonical RoutineInstance through the existing registration workflow."""
    if str(metadata.get("row_kind", "") or "") != "instance":
        return False

    group_id = str(metadata.get("group_id", "") or "").strip()
    instance_id = str(metadata.get("instance_id", "") or "").strip()
    if not group_id or not instance_id:
        QMessageBox.warning(owner, "루틴 복제", "복제할 루틴의 Group 귀속을 확인할 수 없습니다.")
        return False

    if owning_group_ids is not None:
        normalized_group_ids = {
            str(candidate or "").strip()
            for candidate in owning_group_ids
            if str(candidate or "").strip()
        }
        if normalized_group_ids != {group_id}:
            QMessageBox.warning(
                owner,
                "루틴 복제",
                "원본 루틴의 Group 귀속을 하나로 확인할 수 없어 복제할 수 없습니다.",
            )
            return False

    instance = routine_instance_by_id(instance_id)
    if instance is None:
        QMessageBox.warning(owner, "루틴 복제", "복제할 루틴 설정을 확인할 수 없습니다.")
        return False
    if owning_group_ids is None:
        instance_group_id = str(getattr(instance, "group_id", "") or "").strip()
        if instance_group_id != group_id:
            QMessageBox.warning(
                owner,
                "루틴 복제",
                "원본 루틴의 Group 귀속을 하나로 확인할 수 없어 복제할 수 없습니다.",
            )
            return False

    definition_id = str(getattr(instance, "definition_id", "") or "").strip()
    definition = routine_definition_by_id(definition_id) if definition_id else None
    rules_path = getattr(instance, "rules_path", None)
    if definition is None or rules_path is None:
        QMessageBox.warning(owner, "루틴 복제", "복제할 루틴 설정을 확인할 수 없습니다.")
        return False

    source_group = group_record if group_record is not None else group_record_by_id(group_id)
    group_display_name = str(
        getattr(source_group, "display_name", "") or ""
    ).strip()
    if not group_display_name:
        QMessageBox.warning(owner, "루틴 복제", "원본 루틴의 Group을 확인할 수 없습니다.")
        return False

    def rules_provider() -> dict[str, object]:
        try:
            rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
            if not isinstance(rules, dict):
                raise ValueError("rules.json root must be an object")
            return {"success": True, "rules": rules, "error": ""}
        except Exception as exc:
            return {"success": False, "rules": {}, "error": str(exc)}

    from gui_indicator_follow_routine_settings_dialog import (
        register_routine_instance_snapshot,
    )

    return register_routine_instance_snapshot(
        owner,
        definition_id=definition_id,
        definition_display_name=str(
            getattr(definition, "display_name", "") or ""
        ).strip(),
        group_id=group_id,
        group_display_name=group_display_name,
        source_instance_display_name=str(
            getattr(instance, "display_name", "") or ""
        ).strip(),
        rules_provider=rules_provider,
    ) is not None


def open_routine_settings_dialog_for_owner(
    owner: QWidget,
    metadata: dict[str, object],
    *,
    registration: bool,
) -> None:
    definition_id = str(metadata.get("definition_id", "") or "").strip()
    definition = routine_definition_by_id(definition_id)
    if definition is None:
        QMessageBox.warning(owner, "루틴 설정", "선택한 루틴 정의를 확인할 수 없습니다.")
        return

    instance = None
    if not registration:
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            QMessageBox.warning(owner, "루틴 설정", "선택한 루틴을 확인할 수 없습니다.")
            return

    settings_ui = str(definition.settings_ui or "").strip().lower()
    if settings_ui != "indicator_follow":
        QMessageBox.information(
            owner,
            "루틴 설정",
            f"선택한 루틴의 설정창이 아직 연결되지 않았습니다.\n루틴명: {definition.display_name}",
        )
        return

    rules_path = (
        definition.package_dir / definition.default_rules_file
        if registration
        else instance.rules_path
    )
    if rules_path is None or not rules_path.exists():
        QMessageBox.warning(
            owner,
            "rules.json 없음",
            f"선택한 루틴의 rules.json을 찾을 수 없습니다.\n{rules_path}",
        )
        return

    try:
        from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "설정창 로드 실패",
            "gui_indicator_follow_routine_settings_dialog.py 파일을 불러오지 못했습니다.\n"
            f"{exc}",
        )
        return

    registration_display_name = str(
        metadata.get("group_display_name", "")
        or metadata.get("display_name", "")
        or metadata.get("definition_name", "")
        or definition.display_name
    ).strip()
    dialog = IndicatorFollowRoutineSettingsDialog(
        rules_path=rules_path,
        routine_path=definition.package_dir,
        routine_name=registration_display_name if registration else instance.display_name,
        parent=owner,
        definition_id=definition.definition_id,
        definition_display_name=definition.display_name,
        instance_id="" if registration else instance.instance_id,
        group_id=str(metadata.get("group_id", "") or "").strip(),
        group_display_name=registration_display_name if registration else "",
        settings_mode="registration" if registration else "edit",
    )
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    windows = getattr(owner, "_routine_settings_windows", None)
    if not isinstance(windows, set):
        windows = set()
        owner._routine_settings_windows = windows
    windows.add(dialog)
    dialog.destroyed.connect(
        lambda _obj=None, target=dialog: windows.discard(target)
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class AutoTradeSettingWindow(QDialog):
    """
    자동매매설정 창.

    1차 구현 범위:
    - 자동매매 루틴 목록 표시
    - 선택 루틴의 종목별 저장 폴더 표시
    - state.json 기준 상태 요약 표시
    - 실제 자동매매 시작/정지/삭제/환경설정/로그 기능은 다음 단계에서 구현
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)

        self.setObjectName("autoTradeSettingWindow")
        self.setWindowTitle("자동매매설정")
        flags = self.windowFlags()
        flags &= ~Qt.WindowContextHelpButtonHint
        flags |= Qt.WindowMinimizeButtonHint
        flags |= Qt.WindowMaximizeButtonHint
        flags |= Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)
        self.setMinimumHeight(650)

        self.routine_table = QTableWidget()
        self.routine_table.setObjectName("autoTradeSettingRoutineTable")
        self.stock_table = QTableWidget()
        self.stock_table.setObjectName("autoTradeSettingStockTable")

        self.btn_start = QPushButton("▶ 운영시작")
        self.btn_early_close = QPushButton("조기마감")
        self.btn_early_close.setStyleSheet(AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE)
        self.btn_all_stocks = QPushButton("전체")
        self.btn_all_stocks.setFocusPolicy(Qt.NoFocus)
        self.btn_all_stocks.setCursor(Qt.PointingHandCursor)
        self.btn_all_stocks.setFixedSize(
            64,
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
        )
        self.all_stocks_command_separator = QLabel("|")
        self.all_stocks_command_separator.setObjectName(
            "autoTradeSettingAllStocksCommandSeparator"
        )
        self.all_stocks_command_separator.setAlignment(Qt.AlignCenter)
        self.all_stocks_command_separator.setFixedSize(
            12,
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
        )
        self.all_stocks_command_separator.setFocusPolicy(Qt.NoFocus)
        self.all_stocks_command_separator.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.all_stocks_command_separator.setStyleSheet(
            "background: transparent; color: #9CA3AF;"
        )
        self.btn_preview_order_candidates = QPushButton("주문후보검증")
        self.btn_execution_enable = QPushButton("수동 실주문 후보 활성화")
        self.btn_real_ready_preflight = QPushButton("REAL_READY 수동 점검")
        self.btn_execution_preview = QPushButton("Execution Preview")
        self.btn_manual_send_order = QPushButton("Manual SendOrder")
        self.btn_manual_cancel_pending_order = QPushButton("Manual Cancel")
        self.btn_manual_modify_pending_order = QPushButton("Manual Modify")
        self.btn_manual_queue_commit = QPushButton("수동 Queue 저장")
        self.btn_fetch_minute_candles = QPushButton("분봉조회")
        self.btn_early_close.setMinimumHeight(28)
        self.btn_preview_order_candidates.setMinimumHeight(28)
        self.btn_execution_enable.setMinimumHeight(28)
        self.btn_real_ready_preflight.setMinimumHeight(28)
        self.btn_execution_preview.setMinimumHeight(28)
        self.btn_manual_send_order.setMinimumHeight(28)
        self.btn_manual_cancel_pending_order.setMinimumHeight(28)
        self.btn_manual_modify_pending_order.setMinimumHeight(28)
        self.btn_manual_queue_commit.setMinimumHeight(28)
        self.btn_manual_queue_commit.setEnabled(False)
        self.btn_fetch_minute_candles.setMinimumHeight(28)
        for button in (
            self.btn_fetch_minute_candles,
            self.btn_preview_order_candidates,
            self.btn_execution_enable,
            self.btn_real_ready_preflight,
            self.btn_execution_preview,
            self.btn_manual_queue_commit,
            self.btn_manual_send_order,
            self.btn_manual_cancel_pending_order,
            self.btn_manual_modify_pending_order,
        ):
            button.setVisible(False)
        self.btn_set_schedule = QPushButton("환경설정")
        self.btn_stock_register = QPushButton("종목관리")
        self.btn_log_view = QPushButton("종목실적")
        self.btn_review_view = QPushButton("검토관리(0)")
        self.btn_close = QPushButton("닫기")
        for button, object_name in (
            (self.btn_start, "autoTradeSettingStartButton"),
            (self.btn_early_close, "autoTradeSettingEarlyCloseButton"),
            (self.btn_all_stocks, "autoTradeSettingAllStocksButton"),
            (self.btn_preview_order_candidates, "autoTradeSettingPreviewOrderCandidatesButton"),
            (self.btn_execution_enable, "autoTradeSettingExecutionEnableButton"),
            (self.btn_real_ready_preflight, "autoTradeSettingRealReadyPreflightButton"),
            (self.btn_execution_preview, "autoTradeSettingExecutionPreviewButton"),
            (self.btn_manual_send_order, "autoTradeSettingManualSendOrderButton"),
            (self.btn_manual_cancel_pending_order, "autoTradeSettingManualCancelPendingOrderButton"),
            (self.btn_manual_modify_pending_order, "autoTradeSettingManualModifyPendingOrderButton"),
            (self.btn_manual_queue_commit, "autoTradeSettingManualQueueCommitButton"),
            (self.btn_fetch_minute_candles, "autoTradeSettingFetchMinuteCandlesButton"),
            (self.btn_set_schedule, "autoTradeSettingScheduleButton"),
            (self.btn_stock_register, "autoTradeSettingStockRegisterButton"),
            (self.btn_log_view, "autoTradeSettingStockPerformanceButton"),
            (self.btn_review_view, "autoTradeSettingReviewViewButton"),
            (self.btn_close, "autoTradeSettingCloseButton"),
        ):
            button.setObjectName(object_name)
        self._notification_popup = None

        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        # 헤더 정렬 후에는 정렬 규칙이 아니라 "그 순간의 화면 순서"를 보존한다.
        # 설정 변경/조기마감/개별청산 저장 중 종목 위치가 튀는 것을 막기 위한 고정 순서다.
        self._stock_visual_order: list[str] = []
        self._collapsed_auto_trade_definition_ids: set[str] = set()
        self._default_operation_instance_by_definition: dict[str, str] = {}
        self._routine_operation_status_by_instance: dict[str, str] = {}
        # Run one policy reconciliation as soon as startup recovery permits it.
        self._last_time_policy_gui_minute_key = ""
        self._time_policy_timer = QTimer(self)
        self._time_policy_timer.setInterval(10_000)
        self._time_policy_timer.timeout.connect(self.on_time_policy_timer_tick)

        self._pnl_refresh_timer = QTimer(self)
        self._pnl_refresh_timer.setInterval(PNL_REFRESH_INTERVAL_MS)
        self._pnl_refresh_timer.timeout.connect(self.refresh_stock_pnl_cells)
        self._last_execution_preview_result: dict[str, object] | None = None
        self._last_execution_preview_queue_snapshot: dict[str, object] | None = None
        self._last_execution_enable_preview_result: dict[str, object] | None = None
        self._last_execution_enable_queue_snapshot: dict[str, object] | None = None
        self._last_real_preflight_preview_result: dict[str, object] | None = None
        self._last_real_preflight_queue_snapshot: dict[str, object] | None = None
        self._stock_status_filter = "normal"
        self._selected_stock_normal_projection_active = True
        self._selected_stock_double_click_release_pending = False
        self._last_strategy_workspace_width = 0
        self._collapsed_auto_trade_instance_ids: set[str] = set()
        self._routine_tree_display_level = "stock"
        self._routine_tree_display_scope = ""
        self._routine_tree_last_stock_scope = "all"
        self._routine_tree_display_criterion = "profit"
        self._routine_tree_stock_performance_sort_active = False
        self._routine_tree_valid_only = False
        self._hidden_historical_stock_fixture_keys: set[tuple[str, str]] = set()
        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        self._routine_instance_name_edit_finishing = False
        self._all_stocks_scope_active = False
        self._fixed_signals_connected = False

        self._setup_ui()
        # Initialization contract: font, state, input state, geometry, then signals.
        for control_type in (QPushButton, QLabel, QGroupBox):
            for control in self.findChildren(control_type):
                control.setFont(QFont(control.font()))
        for widget_type in (QPushButton, QLabel, QGroupBox, QTableWidget, QComboBox, QCheckBox):
            for widget in self.findChildren(widget_type):
                widget.setEnabled(widget.isEnabled())
                widget.setVisible(not widget.isHidden())
        for input_type in (QLineEdit, QTextEdit):
            for input_widget in self.findChildren(input_type):
                input_widget.setReadOnly(input_widget.isReadOnly())
        self._apply_initial_strategy_workspace_size()
        self._apply_default_filter_state_for_open()
        self._connect_events()

        self._initializing_open_refresh = True
        try:
            self.refresh_all()
        finally:
            self._initializing_open_refresh = False
            self._auto_trade_initial_read_snapshot = None

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        routine_box = QGroupBox("자동매매운영실적")
        routine_box.setObjectName("autoTradeSettingRoutineGroup")
        routine_layout = QVBoxLayout()
        self._setup_routine_table()
        routine_layout.addWidget(self.routine_table)
        routine_box.setLayout(routine_layout)
        routine_box.setMinimumWidth(1000)
        self.routine_box = routine_box
        self._setup_routine_tree_display_level_badges()

        self.stock_box = QGroupBox("등록종목상태")
        self.stock_box.setObjectName("autoTradeSettingStockGroup")
        stock_layout = QVBoxLayout()
        self._setup_selected_routine_status_bar()
        self._setup_stock_table()

        selected_routine_header_layout = QHBoxLayout()
        selected_routine_header_layout.setContentsMargins(0, 0, 0, 0)
        selected_routine_header_layout.addWidget(self.selected_routine_status_bar)
        selected_routine_header_layout.addStretch(1)
        selected_routine_header_layout.addWidget(self.btn_fetch_minute_candles)
        selected_routine_header_layout.addWidget(self.btn_preview_order_candidates)
        selected_routine_header_layout.addWidget(self.btn_execution_enable)
        selected_routine_header_layout.addWidget(self.btn_real_ready_preflight)
        selected_routine_header_layout.addWidget(self.btn_execution_preview)
        selected_routine_header_layout.addSpacing(16)
        selected_routine_header_layout.addWidget(self.btn_manual_queue_commit)
        selected_routine_header_layout.addWidget(self.btn_manual_send_order)
        selected_routine_header_layout.addWidget(self.btn_manual_cancel_pending_order)
        selected_routine_header_layout.addWidget(self.btn_manual_modify_pending_order)
        selected_routine_header_layout.addWidget(self.btn_all_stocks, 0, Qt.AlignVCenter)
        selected_routine_header_layout.addWidget(
            self.all_stocks_command_separator,
            0,
            Qt.AlignVCenter,
        )
        selected_routine_header_layout.addWidget(self.btn_early_close, 0, Qt.AlignVCenter)

        stock_layout.addLayout(selected_routine_header_layout)
        stock_layout.addWidget(self.stock_table)
        self.stock_box.setLayout(stock_layout)
        for group_box in (routine_box, self.stock_box):
            group_box.setAlignment(Qt.AlignLeft)
            group_box.setFlat(False)
            group_box.setStyleSheet(AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE)

        workspace_widget = QWidget()
        workspace_widget.setObjectName("autoTradeSettingStockWorkspace")
        workspace_widget.setMinimumWidth(700)
        self.strategy_workspace_widget = workspace_widget
        workspace_layout = QVBoxLayout()
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.stock_box, 1)
        workspace_widget.setLayout(workspace_layout)

        self.strategy_workspace_splitter = QSplitter(Qt.Horizontal)
        self.strategy_workspace_splitter.setObjectName("autoTradeSettingWorkspaceSplitter")
        self.strategy_workspace_splitter.addWidget(routine_box)
        self.strategy_workspace_splitter.addWidget(workspace_widget)
        self.strategy_workspace_splitter.setStretchFactor(0, 0)
        self.strategy_workspace_splitter.setStretchFactor(1, 1)

        buttons = [
            self.btn_start,
            self.btn_set_schedule,
            self.btn_stock_register,
            self.btn_log_view,
            self.btn_review_view,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        # v20.9.1g: 좌우 분할 구조를 상하 구조로 변경한다.
        # 루틴 목록은 상단 요약 영역으로 압축하고, 종목표는 하단 전체 폭을 사용한다.
        main_layout.addWidget(self.strategy_workspace_splitter, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_routine_tree_display_level_badges()
        if not hasattr(self, "strategy_workspace_splitter"):
            return
        current_width = int(self.width())
        grew = current_width > int(getattr(self, "_last_strategy_workspace_width", 0) or 0)
        self._last_strategy_workspace_width = current_width
        if grew or self.isMaximized():
            QTimer.singleShot(0, self._rebalance_strategy_workspace_splitter)

    def _stock_table_required_width(self, last_column: int | None = None) -> int:
        self._apply_stock_table_column_widths()
        if last_column is None:
            column_range = range(self.stock_table.columnCount())
        else:
            column_range = range(min(last_column, self.stock_table.columnCount() - 1) + 1)
        header_width = sum(
            self.stock_table.horizontalHeader().sectionSize(col)
            for col in column_range
        )
        vertical_header_width = self.stock_table.verticalHeader().width()
        if vertical_header_width <= 0:
            vertical_header_width = self.stock_table.verticalHeader().minimumWidth()
        vertical_scroll_width = self.stock_table.verticalScrollBar().sizeHint().width()
        frame_width = self.stock_table.frameWidth() * 2
        return header_width + vertical_header_width + vertical_scroll_width + frame_width

    def _right_workspace_required_width(self, last_stock_column: int | None = None) -> int:
        stock_layout_margins = self.stock_box.layout().contentsMargins()
        stock_box_extra = (
            stock_layout_margins.left()
            + stock_layout_margins.right()
        )
        stock_required_width = self._stock_table_required_width(last_stock_column) + stock_box_extra
        return max(stock_required_width, self.strategy_workspace_widget.minimumWidth())

    def _right_workspace_initial_width(self) -> int:
        return self._right_workspace_required_width(AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN)

    def _apply_initial_strategy_workspace_size(self) -> None:
        self._apply_stock_table_column_widths()
        main_layout = self.layout()
        margins = main_layout.contentsMargins() if main_layout is not None else None
        outer_width = (margins.left() + margins.right()) if margins is not None else 0
        left_width = self.routine_box.minimumWidth()
        right_width = self._right_workspace_initial_width()
        handle_width = self.strategy_workspace_splitter.handleWidth()
        initial_width = left_width + right_width + handle_width + outer_width
        self.setMinimumSize(initial_width, 650)
        self.resize(initial_width, 680)
        self.strategy_workspace_splitter.setSizes([left_width, right_width])
        self._last_strategy_workspace_width = initial_width
        QTimer.singleShot(0, self._rebalance_strategy_workspace_splitter)

    def _rebalance_strategy_workspace_splitter(self) -> None:
        if not hasattr(self, "strategy_workspace_splitter"):
            return
        available_width = self.strategy_workspace_splitter.width()
        if available_width <= 0:
            return
        handle_width = self.strategy_workspace_splitter.handleWidth()
        left_minimum_width = self.routine_box.minimumWidth()
        right_required_width = self._right_workspace_required_width()
        right_width = min(
            right_required_width,
            max(self.strategy_workspace_widget.minimumWidth(), available_width - left_minimum_width - handle_width),
        )
        left_width = max(left_minimum_width, available_width - right_width - handle_width)
        current_sizes = self.strategy_workspace_splitter.sizes()
        if len(current_sizes) == 2 and current_sizes[1] >= right_required_width:
            return
        if right_width > 0 and left_width > 0:
            self.strategy_workspace_splitter.setSizes([left_width, right_width])

    def _setup_selected_routine_status_bar(self) -> None:
        self.selected_routine_status_bar = QWidget()
        self.selected_routine_status_bar.setObjectName("autoTradeSettingSelectedRoutineStatusBar")
        self.selected_routine_status_bar.setFixedHeight(AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        layout = QHBoxLayout(self.selected_routine_status_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.selected_routine_signal_label = QLabel("●")
        self.selected_routine_signal_label.setObjectName("autoTradeSettingSelectedRoutineSignal")
        self.selected_routine_signal_label.setAlignment(Qt.AlignCenter)
        self.selected_routine_signal_label.setFixedWidth(18)
        self.selected_routine_signal_label.setStyleSheet("background: transparent; color: #6B7280;")
        layout.addWidget(self.selected_routine_signal_label, 0, Qt.AlignVCenter)

        self.selected_routine_name_button = QPushButton("-")
        self.selected_routine_name_button.setObjectName("autoTradeSettingSelectedRoutineNameButton")
        self.selected_routine_name_button.setFlat(True)
        self.selected_routine_name_button.setFocusPolicy(Qt.NoFocus)
        self.selected_routine_name_button.setCursor(Qt.PointingHandCursor)
        self.selected_routine_name_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR}; "
            "font-weight: 600; padding: 0 2px; text-align: left; }"
            "QPushButton:hover { color: #1D4ED8; }"
        )
        self.selected_routine_name_button.clicked.connect(lambda: self.set_stock_status_filter("all"))
        layout.addWidget(self.selected_routine_name_button, 0, Qt.AlignVCenter)

        self.selected_routine_group_count_badge = QLabel("")
        self.selected_routine_group_count_badge.setObjectName(
            "autoTradeSettingSelectedRoutineGroupCount"
        )
        self.selected_routine_group_count_badge.setAlignment(Qt.AlignCenter)
        self.selected_routine_group_count_badge.setFixedWidth(64)
        self.selected_routine_group_count_badge.setStyleSheet(
            "background-color: transparent; color: #7E22CE; border: 1px solid #C084FC; "
            "border-radius: 3px; padding: 0 3px;"
        )
        self.selected_routine_group_count_badge.hide()
        layout.addWidget(self.selected_routine_group_count_badge, 0, Qt.AlignVCenter)

        self.selected_routine_instance_count_badge = QLabel("")
        self.selected_routine_instance_count_badge.setObjectName("autoTradeSettingSelectedRoutineInstanceCount")
        self.selected_routine_instance_count_badge.setAlignment(Qt.AlignCenter)
        self.selected_routine_instance_count_badge.setFixedWidth(64)
        self.selected_routine_instance_count_badge.setStyleSheet(
            "background-color: transparent; color: #7E22CE; border: 1px solid #C084FC; "
            "border-radius: 3px; padding: 0 3px;"
        )
        layout.addWidget(self.selected_routine_instance_count_badge, 0, Qt.AlignVCenter)

        self.selected_routine_status_buttons: dict[str, QPushButton] = {}
        for filter_key, object_name in (
            ("all", "autoTradeSettingSelectedRoutineRegistered"),
            ("operation", "autoTradeSettingSelectedRoutineRunning"),
            ("waiting", "autoTradeSettingSelectedRoutineWaiting"),
            ("excluded", "autoTradeSettingSelectedRoutineExcluded"),
            ("review", "autoTradeSettingSelectedRoutineError"),
        ):
            button = QPushButton()
            button.setObjectName(object_name)
            button.setFlat(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; "
                f"color: {AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR}; padding: 0 2px; }}"
                "QPushButton:hover { color: #1D4ED8; text-decoration: underline; }"
            )
            button.clicked.connect(lambda _checked=False, key=filter_key: self.set_stock_status_filter(key))
            if filter_key == "all" and isinstance(self, AutoTradeSettingWindow):
                button.installEventFilter(self)
            self.selected_routine_status_buttons[filter_key] = button
            layout.addWidget(button, 0, Qt.AlignVCenter)

        layout.addStretch(1)
        self.update_selected_routine_status_bar()

    def set_stock_status_filter(self, filter_key: str) -> None:
        normalized = str(filter_key or "all").strip().lower()
        normalized = {
            "running": "operation",
            "stopped": "waiting",
            "error": "review",
        }.get(normalized, normalized)
        if normalized not in {"all", "operation", "waiting", "excluded", "review"}:
            normalized = "operation"
        self._stock_status_filter = (
            "normal"
            if normalized == "all"
            and bool(getattr(self, "_selected_stock_normal_projection_active", False))
            else normalized
        )
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def _toggle_selected_stock_normal_projection(self) -> None:
        """Toggle operation/waiting projection in the selected-stock table only."""
        projection_active = not bool(
            getattr(self, "_selected_stock_normal_projection_active", False)
        )
        self._selected_stock_normal_projection_active = projection_active
        self._stock_status_filter = "normal" if projection_active else "all"
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def update_selected_routine_status_bar(self) -> None:
        if not hasattr(self, "selected_routine_status_bar"):
            return
        all_stocks_scope_active = bool(
            getattr(self, "_all_stocks_scope_active", False)
        )
        all_stocks_badge_text_color = (
            AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            if all_stocks_scope_active
            else AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
        )
        all_stocks_badge_border_color = (
            AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            if all_stocks_scope_active
            else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
        )
        all_stocks_button = getattr(self, "btn_all_stocks", None)
        if all_stocks_button is not None:
            all_stocks_button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=all_stocks_badge_text_color,
                    border_color=all_stocks_badge_border_color,
                )
            )
        if all_stocks_scope_active:
            counts = (
                self._left_flat_filter_stock_scope_summary()
                if self._left_flat_filter_scope_active()
                else self._all_stocks_scope_summary()
            )
            self.selected_routine_signal_label.hide()
            self.selected_routine_name_button.setText("전체")
            self.selected_routine_group_count_badge.setText(
                f"그룹({counts['groups']})"
            )
            self.selected_routine_group_count_badge.show()
            self.selected_routine_instance_count_badge.setText(
                f"루틴({counts['routines']})"
            )
            self.selected_routine_instance_count_badge.show()
        else:
            self.selected_routine_signal_label.show()
            self.selected_routine_group_count_badge.setText("")
            self.selected_routine_group_count_badge.hide()
            counts = None
        if counts is None:
            metadata = self.current_selected_routine_row_metadata()
            if not metadata:
                self.selected_routine_signal_label.setText("●")
                self.selected_routine_name_button.setText("-")
                self.selected_routine_instance_count_badge.setText("")
                self.selected_routine_instance_count_badge.hide()
                counts = {
                    "registered": 0,
                    "normal": 0,
                    "operation_running": 0,
                    "waiting": 0,
                    "excluded": 0,
                    "review": 0,
                }
            else:
                row_kind = str(metadata.get("row_kind", "") or "")
                self.selected_routine_signal_label.setText("●")
                if row_kind in {"instance", "stock"}:
                    self.selected_routine_name_button.setText(str(metadata.get("instance_name", "") or "-"))
                    self.selected_routine_instance_count_badge.setText("")
                    self.selected_routine_instance_count_badge.hide()
                    counts = self._stock_display_scope_summary()
                else:
                    self.selected_routine_name_button.setText(str(metadata.get("definition_name", "") or "-"))
                    self.selected_routine_instance_count_badge.setText("")
                    self.selected_routine_instance_count_badge.hide()
                    counts = self._stock_display_scope_summary()
        projection_active = bool(
            getattr(self, "_selected_stock_normal_projection_active", False)
        )
        stock_display_count = (
            int(
                counts.get(
                    "normal",
                    int(counts.get("operation_running", 0) or 0)
                    + int(counts.get("waiting", 0) or 0),
                )
                or 0
            )
            if projection_active
            else int(counts.get("registered", 0) or 0)
        )
        button_texts = {
            "all": f"종목({stock_display_count})",
            "operation": f"운영({counts['operation_running']})",
            "waiting": f"대기({counts['waiting']})",
            "excluded": f"제외({counts['excluded']})",
            "review": f"검토({counts['review']})",
        }
        current_filter = str(getattr(self, "_stock_status_filter", "all") or "all")
        current_filter = {
            "running": "operation",
            "stopped": "waiting",
            "error": "review",
        }.get(current_filter, current_filter)
        if current_filter == "normal":
            current_filter = "all"
        for key, button in self.selected_routine_status_buttons.items():
            button.setText(button_texts[key])
            is_active = key == current_filter
            active_color = (
                CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR
                if key == "all" and projection_active
                else "#1D4ED8"
            )
            text_color = (
                CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR
                if key == "all" and projection_active
                else active_color if is_active else AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
            )
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; "
                f"color: {text_color}; "
                f"font-weight: {'600' if is_active else '400'}; padding: 0 2px; }}"
                "QPushButton:hover { color: #1D4ED8; text-decoration: underline; }"
            )

    def _stock_operation_status_label(self) -> str:
        try:
            operation_status = _today_global_operation_status(read_operation_state())
        except Exception:
            operation_status = ""
        return "운영" if operation_status in {"RUNNING", "CLOSING"} else "정지"

    def _all_stocks_scope_summary(self) -> dict[str, int]:
        summary = {
            "groups": 0,
            "routines": 0,
            "registered": 0,
            "normal": 0,
            "operation_running": 0,
            "waiting": 0,
            "excluded": 0,
            "review": 0,
        }
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("row_kind", "") or "") != "definition":
                continue
            summary["groups"] += 1
            summary["routines"] += int(metadata.get("instance_count", 0) or 0)
            for key in (
                "registered", "normal", "operation_running", "waiting",
                "excluded", "review",
            ):
                summary[key] += int(metadata.get(key, 0) or 0)
        return summary

    def _stock_display_scope_summary(self) -> dict[str, int]:
        """Summarize the selected parent-instance stock scope."""
        summary = {
            "groups": 0,
            "routines": 0,
            "registered": 0,
            "normal": 0,
            "operation_running": 0,
            "waiting": 0,
            "excluded": 0,
            "review": 0,
        }
        target_instance_ids = self.current_selected_target_instance_ids()
        if not target_instance_ids:
            return summary
        snapshot = auto_trade_initial_read_snapshot(self)
        loaded_instances = (
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        instances = {
            str(instance.instance_id or "").strip(): instance
            for instance in loaded_instances
            if str(instance.instance_id or "").strip()
        }
        counts_by_instance = self._routine_instance_operation_counts()
        group_ids: set[str] = set()
        for instance_id in target_instance_ids:
            instance = instances.get(str(instance_id))
            counts = counts_by_instance.get(instance_id, {})
            if int(counts.get("registered", 0) or 0) <= 0:
                continue
            group_id = str(getattr(instance, "group_id", "") or "").strip() if instance else ""
            if group_id:
                group_ids.add(group_id)
            summary["routines"] += 1
            for key in (
                "registered", "normal", "operation_running", "waiting",
                "excluded", "review",
            ):
                summary[key] += int(
                    counts.get(
                        key,
                        counts.get("error", 0) if key == "review" else 0,
                    )
                    or 0
                )
        summary["groups"] = len(group_ids)
        return summary

    def _left_flat_filter_scope_active(self) -> bool:
        return (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and str(
                getattr(self, "_routine_tree_display_level", "") or ""
            ).strip()
            == "stock"
            and str(
                getattr(self, "_routine_tree_display_scope", "") or ""
            ).strip()
            in {"all", "current", "historical"}
        )

    def _right_stock_scope_mode(self) -> str:
        return (
            "FLAT_FILTER_SCOPE"
            if self._left_flat_filter_scope_active()
            else "HIERARCHICAL_SELECTION_SCOPE"
        )

    def _left_flat_filter_stock_codes(self) -> tuple[str, ...]:
        if not self._left_flat_filter_scope_active():
            return ()
        codes: set[str] = set()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("row_kind", "") or "") != "stock":
                continue
            code = normalize_stock_code(metadata.get("stock_code", ""))
            if code:
                codes.add(code)
        return tuple(sorted(codes))

    def _left_flat_filter_stock_scope_summary(self) -> dict[str, int]:
        from gui_main_table_loader import _instance_stock_counts

        summary = {
            "groups": 0,
            "routines": 0,
            "registered": 0,
            "normal": 0,
            "operation_running": 0,
            "waiting": 0,
            "excluded": 0,
            "review": 0,
        }
        target_codes = set(self._left_flat_filter_stock_codes())
        if not target_codes:
            return summary
        snapshot = auto_trade_initial_read_snapshot(self)
        stocks = (
            snapshot.get("stocks", ())
            if snapshot is not None
            else read_base_stocks()
        )
        target_stock_paths = {
            str(stock.get("stock_path", "") or "").strip()
            for stock in stocks
            if normalize_stock_code(stock.get("code", "")) in target_codes
            and str(stock.get("stock_path", "") or "").strip()
        }
        if not target_stock_paths:
            return summary
        count_kwargs: dict[str, object] = {
            "window": self,
            "stock_paths": target_stock_paths,
        }
        if snapshot is not None:
            count_kwargs["static_data"] = snapshot.get("count_static_data", {})
            count_kwargs["state_by_stock_dir"] = {
                stock_dir: data.get("state", {})
                for stock_dir, data in snapshot.get(
                    "stock_data_by_dir",
                    {},
                ).items()
            }
            count_kwargs["state_issue_by_stock_dir"] = {
                stock_dir: str(data.get("state_issue_reason", "") or "")
                for stock_dir, data in snapshot.get(
                    "stock_data_by_dir",
                    {},
                ).items()
            }
        counts_by_instance = _instance_stock_counts(**count_kwargs)
        target_instance_ids = {
            str(instance_id or "").strip()
            for instance_id, counts in counts_by_instance.items()
            if int(counts.get("registered", 0) or 0) > 0
            and str(instance_id or "").strip()
        }
        for instance_id in target_instance_ids:
            counts = counts_by_instance.get(instance_id, {})
            for key in (
                "registered",
                "normal",
                "operation_running",
                "waiting",
                "excluded",
                "review",
            ):
                summary[key] += int(counts.get(key, 0) or 0)
        loaded_instances = (
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        group_ids: set[str] = set()
        for instance in loaded_instances:
            instance_id = str(getattr(instance, "instance_id", "") or "").strip()
            if instance_id not in target_instance_ids:
                continue
            summary["routines"] += 1
            group_id = str(getattr(instance, "group_id", "") or "").strip()
            definition_id = str(
                getattr(instance, "definition_id", "") or ""
            ).strip()
            group_ids.add(group_id or f"definition:{definition_id}")
        summary["groups"] = len(group_ids)
        return summary

    def all_registered_instance_ids(self) -> tuple[str, ...]:
        snapshot = auto_trade_initial_read_snapshot(self)
        instances = (
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        return tuple(
            str(instance.instance_id)
            for instance in instances
            if str(instance.instance_id or "").strip()
        )

    def show_all_registered_stocks(self) -> None:
        """전체 그룹/루틴의 등록 종목을 조회하는 전용 Production Caller."""
        was_blocked = self.routine_table.blockSignals(True)
        try:
            self.routine_table.clearSelection()
        finally:
            self.routine_table.blockSignals(was_blocked)
        self._all_stocks_scope_active = True
        self.update_selection_summary_panel()
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def _apply_default_filter_state_for_open(self) -> None:
        """Apply the open-time filter contract without rebuilding either table."""
        self._routine_tree_valid_only = False
        self._routine_tree_display_level = "stock"
        self._routine_tree_display_scope = "all"
        self._routine_tree_last_stock_scope = "all"
        self._routine_tree_display_criterion = "profit"
        self._routine_tree_stock_performance_sort_active = False
        self._selected_stock_normal_projection_active = True
        self._stock_status_filter = "normal"
        self._all_stocks_scope_active = True
        self._apply_routine_tree_display_level_command("stock")
        self._update_routine_tree_display_level_badges()
        was_blocked = self.routine_table.blockSignals(True)
        try:
            self.routine_table.clearSelection()
        finally:
            self.routine_table.blockSignals(was_blocked)

    def reset_default_filters_for_open(self) -> None:
        """창을 열 때마다 자동매매설정 기본 필터 계약을 적용한다."""
        self._apply_default_filter_state_for_open()
        self.refresh_all()

    def update_selection_summary_panel(self) -> None:
        return

    def _setup_routine_table(self) -> None:
        headers = [
            "루틴명",
            "종목수",
            "총예산",
            "사용예산",
            "가용예산",
        ]

        routine_table_font = QFont(self.routine_table.font())
        self.routine_table.setFont(routine_table_font)
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        header = self.routine_table.horizontalHeader()
        header.setObjectName("autoTradeSettingRoutineHeader")
        header.setFont(routine_table_font)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(False)
        for col in range(len(headers)):
            header_item = self.routine_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setFont(routine_table_font)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.routine_table.setColumnWidth(0, 220)
        self.routine_table.setColumnWidth(1, 90)
        self.routine_table.setColumnWidth(2, 140)
        self.routine_table.setColumnWidth(3, 140)
        self.routine_table.setColumnWidth(4, 140)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.routine_table.setFocusPolicy(Qt.StrongFocus)
        self.routine_table.setTabKeyNavigation(True)
        self.routine_table.setMouseTracking(False)
        self.routine_table.viewport().setMouseTracking(False)
        self.routine_table.setAttribute(Qt.WA_Hover, False)
        self.routine_table.viewport().setAttribute(Qt.WA_Hover, False)
        self.routine_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerItem)
        self.routine_table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.routine_table.setSortingEnabled(False)
        self.routine_table.setColumnCount(1)
        self.routine_table.horizontalHeader().hide()
        self.routine_table.verticalHeader().hide()
        self.routine_table.setShowGrid(False)
        self.routine_table.setAlternatingRowColors(False)
        self.routine_table.setWordWrap(False)
        self.routine_table.setTextElideMode(Qt.ElideRight)
        self.routine_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.routine_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.routine_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.routine_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.routine_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.routine_table.horizontalHeader().setStretchLastSection(True)
        self.routine_table.verticalHeader().setMinimumSectionSize(22)
        self.routine_table.setMinimumWidth(970)
        self.routine_table.setColumnWidth(0, 950)
        self.routine_table.setStyleSheet(
            """
            QTableWidget {
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:focus {
                outline: 0;
            }
            """
        )

    def _setup_stock_table(self) -> None:
        headers = [
            "코드",
            "종목",
            "운영",
            "현황",
            "상태",
            "방식",
            "청산",
            "보유",
            "가격",
            "손익",
            "매매",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        header = self.stock_table.horizontalHeader()
        header.setObjectName("autoTradeSettingStockHeader")
        header.setProperty(PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY, True)
        vertical_header = self.stock_table.verticalHeader()
        stock_table_grid_color = REGISTERED_STOCK_STATUS_GRID_COLOR
        header.setProperty(PLAIN_HEADER_GRID_COLOR_PROPERTY, stock_table_grid_color)
        header_font = QFont(self.stock_table.font())
        header.setFont(header_font)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(False)
        for col in range(len(headers)):
            header_item = self.stock_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setFont(header_font)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.stock_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.stock_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.stock_table.setShowGrid(True)
        self.stock_table.setGridStyle(Qt.SolidLine)
        header.setMinimumSectionSize(30)
        vertical_header.setSectionsMovable(False)
        vertical_header.setDefaultAlignment(Qt.AlignCenter)
        vertical_header.setSectionResizeMode(QHeaderView.Fixed)
        vertical_header.setMinimumWidth(40)
        vertical_header.setMaximumWidth(40)
        vertical_header.setFixedWidth(40)
        self._stock_position_metric_delegate = StockPositionMetricDelegate(self.stock_table)
        self._stock_selected_text_delegate = SelectedTextReadableDelegate(self.stock_table)
        self._stock_name_clip_delegate = ClippedTextItemDelegate(self.stock_table)
        self.stock_table.setItemDelegateForColumn(1, self._stock_name_clip_delegate)
        for col in (2, 4, 5, 6):
            self.stock_table.setItemDelegateForColumn(
                col,
                self._stock_selected_text_delegate,
            )
        for col in (7, 8, 9, 10):
            self.stock_table.setItemDelegateForColumn(
                col,
                self._stock_position_metric_delegate,
            )

        # 자동매매설정창 하단 종목표 고정폭 배분.
        # 보유/가격/손익/매매는 관제 트리와 같은 묶음 단위로 표시한다.
        self._apply_stock_table_column_widths()
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        body_background = self.stock_table.viewport().palette().color(QPalette.Base).name()
        self.stock_table.setStyleSheet(
            registered_stock_status_table_stylesheet(
                self.stock_table.objectName(),
                body_background,
            )
        )
        self._sync_stock_table_header_background_to_body()
        self.stock_table.setWordWrap(True)
        self.stock_table.setTextElideMode(Qt.ElideRight)
        self.stock_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stock_table.setFocusPolicy(Qt.StrongFocus)
        self.stock_table.setTabKeyNavigation(True)
        self.stock_table.setMouseTracking(False)
        self.stock_table.viewport().setMouseTracking(False)
        self.stock_table.setAttribute(Qt.WA_Hover, False)
        self.stock_table.viewport().setAttribute(Qt.WA_Hover, False)
        self.stock_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerItem)
        self.stock_table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.stock_table.setSortingEnabled(False)
        QTimer.singleShot(0, self._apply_stock_table_column_widths)

    def _sync_stock_table_header_background_to_body(self) -> None:
        """등록종목상태 표의 header 빈 영역을 body 배경과 맞춘다."""
        body_color = self.stock_table.viewport().palette().color(QPalette.Base)

        header = self.stock_table.horizontalHeader()
        header.setProperty(PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY, True)
        header.setProperty(
            PLAIN_HEADER_GRID_COLOR_PROPERTY,
            REGISTERED_STOCK_STATUS_GRID_COLOR,
        )
        header.viewport().update()

        vertical_header = self.stock_table.verticalHeader()
        vertical_header.setDefaultAlignment(Qt.AlignCenter)
        vertical_palette = QPalette(vertical_header.palette())
        for role in (QPalette.Button, QPalette.Window, QPalette.Base):
            vertical_palette.setColor(role, body_color)
        vertical_header.setPalette(vertical_palette)
        vertical_header.viewport().setPalette(vertical_palette)
        vertical_header.viewport().update()

    def _apply_stock_table_column_widths(self) -> None:
        """자동매매설정창 하단 종목표 컬럼 폭을 강제로 재적용한다."""
        self.stock_table.verticalHeader().setMinimumWidth(40)
        self.stock_table.verticalHeader().setMaximumWidth(40)
        self.stock_table.verticalHeader().setFixedWidth(40)
        header = self.stock_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col, width in AUTO_TRADE_SETTING_STOCK_TABLE_COLUMN_WIDTHS.items():
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)
            self.stock_table.setColumnWidth(col, width)

    def _connect_events(self) -> None:
        if self._fixed_signals_connected:
            return
        self.routine_table.itemSelectionChanged.connect(self.on_routine_selection_changed)
        self.routine_table.itemClicked.connect(self.on_routine_table_item_clicked)
        self.routine_table.customContextMenuRequested.connect(self.on_routine_table_context_menu)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemDoubleClicked.connect(self.on_stock_table_item_double_clicked)
        self.stock_table.itemDoubleClicked.connect(
            self.on_stock_table_name_item_double_clicked
        )
        self.stock_table.itemDoubleClicked.connect(
            self.on_stock_table_code_item_double_clicked
        )
        self.stock_table.customContextMenuRequested.connect(self.on_stock_table_context_menu)
        self.btn_close.clicked.connect(self.close)
        self.btn_start.clicked.connect(self.start_selected_auto_trades)
        self.btn_fetch_minute_candles.clicked.connect(self.fetch_minute_candles_for_selected_stock)
        self.btn_preview_order_candidates.clicked.connect(self.preview_order_candidates_for_pending_signals)
        self.btn_execution_enable.clicked.connect(self.enable_execution_candidate_manually)
        self.btn_real_ready_preflight.clicked.connect(self.run_real_ready_preflight_manually)
        self.btn_execution_preview.clicked.connect(self.preview_execution_for_real_ready_order_manual)
        self.btn_manual_queue_commit.clicked.connect(self.commit_last_execution_preview_queue_manually)
        self.btn_manual_send_order.clicked.connect(self.send_order_for_order_queued_manually)
        self.btn_manual_cancel_pending_order.clicked.connect(self.cancel_pending_order_manually)
        self.btn_manual_modify_pending_order.clicked.connect(self.modify_pending_order_manually)
        self.btn_all_stocks.clicked.connect(self.show_all_registered_stocks)
        self.btn_early_close.clicked.connect(self.apply_selected_early_close_default)
        self.btn_set_schedule.clicked.connect(self.open_operation_environment_settings)
        self.btn_stock_register.clicked.connect(self.open_stock_register_window)
        self.btn_log_view.clicked.connect(self.open_stock_performance_window)
        self.btn_review_view.clicked.connect(self.open_review_required_window)
        self._fixed_signals_connected = True

    def sort_routine_table_by_column(self, column: int) -> None:
        """상단 루틴표 헤더 클릭 정렬."""
        return

    def capture_stock_visual_order(self) -> list[str]:
        """현재 하단 종목표에 보이는 행 순서를 종목 runtime 경로 기준으로 저장한다."""
        order: list[str] = []
        seen: set[str] = set()
        for row in range(self.stock_table.rowCount()):
            path_text = ""
            for col in range(self.stock_table.columnCount()):
                item = self.stock_table.item(row, col)
                if item is None:
                    continue
                value = item.data(Qt.UserRole)
                if value:
                    path_text = str(value)
                    break
            if path_text and path_text not in seen:
                order.append(path_text)
                seen.add(path_text)
        return order

    def sort_stock_table_by_column(self, column: int) -> None:
        """하단 종목표 헤더 클릭 정렬.

        헤더 클릭 순간에만 정렬 규칙을 적용하고, 그 결과 화면 순서를 고정한다.
        이후 설정 변경/조기마감/개별청산 저장으로 표가 다시 로딩되어도
        정렬 규칙을 재적용하지 않고 이 화면 순서를 유지한다.
        """
        if column < 0 or column >= self.stock_table.columnCount():
            return

        if self._stock_sort_column == column:
            self._stock_sort_order = (
                Qt.DescendingOrder
                if self._stock_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._stock_sort_column = column
            self._stock_sort_order = Qt.AscendingOrder

        selected_paths = set()
        for row in self.selected_stock_rows():
            item = self.stock_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole):
                selected_paths.add(str(item.data(Qt.UserRole)))

        self.stock_table.sortItems(column, self._stock_sort_order)
        self._stock_visual_order = self.capture_stock_visual_order()

        if selected_paths:
            self.stock_table.clearSelection()
            for row in range(self.stock_table.rowCount()):
                item = self.stock_table.item(row, 0)
                if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                    self.stock_table.selectRow(row)
        self.update_action_buttons()

    def apply_auto_trade_table_sorts(self) -> None:
        """목록 갱신 후 상단 루틴표 정렬만 재적용한다.

        하단 종목표는 헤더 클릭 시점의 화면 순서를 고정 보존한다.
        refresh/load 중 stock_table.sortItems()를 재실행하면 작업 중인 종목이
        정렬 규칙에 따라 이동하므로 여기서는 재정렬하지 않는다.
        """
        if self._routine_sort_column >= 0:
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)

    def _active_initial_read_snapshot(self) -> dict[str, object] | None:
        return auto_trade_initial_read_snapshot(self)

    def _build_initial_read_snapshot(self) -> dict[str, object]:
        """Read one coherent data set for a full Settings refresh."""
        definitions = tuple(load_routine_definitions())
        instances = tuple(load_persisted_routine_instances())
        groups = tuple(get_group_records())
        stocks = tuple(read_base_stocks())
        stock_data_by_dir: dict[str, dict[str, object]] = {}
        count_stocks: list[dict[str, object]] = []

        for stock in stocks:
            stock_path = str(stock.get("stock_path", "") or "").strip()
            if not stock_path:
                continue
            stock_dir = PROJECT_ROOT / stock_path
            config = read_json_dict(stock_dir / "config.json")
            state, state_issue_reason = _read_central_review_state(
                stock_dir / "state.json"
            )
            stock_data_by_dir[str(stock_dir)] = {
                "config": dict(config),
                "state": dict(state),
                "state_issue_reason": state_issue_reason,
                "orders": read_orders_data(stock_dir / "orders.json"),
            }
            instance_id = str(
                stock.get("assigned_routine_instance_id", "")
                or config.get("assigned_routine_instance_id", "")
                or ""
            ).strip()
            if not instance_id:
                continue
            raw_routines = stock.get("routines", ())
            routines = (
                tuple(
                    str(item or "").strip()
                    for item in raw_routines
                    if str(item or "").strip()
                )
                if isinstance(raw_routines, (list, tuple, set))
                else tuple()
            )
            count_stocks.append(
                {
                    "stock_path": stock_path,
                    "stock_dir": stock_dir,
                    "stock_dir_key": str(stock_dir),
                    "instance_id": instance_id,
                    "operation_excluded": is_operation_excluded(config),
                    "code": str(stock.get("code", "") or "").strip(),
                    "name": str(stock.get("name", "") or "").strip(),
                    "enabled": bool(stock.get("enabled", True)),
                    "routines": routines,
                    "assigned_routine_instance_id": instance_id,
                }
            )

        canonical_performance = None
        canonical_performance_error = ""
        try:
            canonical_performance = build_canonical_performance_ui_snapshot(
                PROJECT_ROOT,
                stocks=stocks,
                instances=instances,
                groups=groups,
            )
        except Exception as exc:
            canonical_performance_error = str(exc)
            LOGGER.exception("canonical AutoTrade performance snapshot failed")

        return {
            "definitions": definitions,
            "instances": instances,
            "groups": groups,
            "stocks": stocks,
            "stock_data_by_dir": stock_data_by_dir,
            "count_static_data": {
                "definitions": definitions,
                "instances": instances,
                "stocks": tuple(count_stocks),
            },
            "canonical_performance": canonical_performance,
            "canonical_performance_error": canonical_performance_error,
        }

    def refresh_all(self) -> None:
        # 자동매매설정 창 전체 갱신 전 하단 종목표 위치를 보존한다.
        # 시간변경/운영시작 등 상태 갱신 후 종목표가 맨 위로 튀는 문제를 막는다.
        selected_stock_paths, stock_scroll_value = self.capture_stock_table_view_state()

        normalize_base_stock_single_routine_file()
        from gui_main_table_loader import _invalidate_main_pnl_refresh_cache

        # Config-backed exclusion is mutable; rebuild the aggregate input for
        # this refresh so badges and row filters share the latest projection.
        _invalidate_main_pnl_refresh_cache(self)
        previous_snapshot = getattr(self, "_auto_trade_initial_read_snapshot", None)
        snapshot_builder = getattr(self, "_build_initial_read_snapshot", None)
        self._auto_trade_initial_read_snapshot = (
            snapshot_builder() if callable(snapshot_builder) else previous_snapshot
        )
        try:
            selected_routine_metadata = self.current_selected_routine_row_metadata()
            all_stocks_scope_active = bool(
                getattr(self, "_all_stocks_scope_active", False)
            )
            routine_selection_blocker = QSignalBlocker(self.routine_table)
            self.routine_table.setUpdatesEnabled(False)
            try:
                self.load_routine_table()

                if selected_routine_metadata:
                    self.restore_routine_selection_metadata(selected_routine_metadata)

                if (
                    not all_stocks_scope_active
                    and self.current_selected_routine_row_metadata() is None
                    and self.routine_table.rowCount() > 0
                ):
                    self.routine_table.selectRow(0)
            finally:
                del routine_selection_blocker
                self.routine_table.setUpdatesEnabled(True)
                self.routine_table.viewport().update()

            self.update_selected_routine_status_bar()
            self._defer_stock_loader_action_update = True
            try:
                self.load_selected_routine_stocks()
            finally:
                self._defer_stock_loader_action_update = False
            self.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
            self.update_review_required_button_text()
            self.update_action_buttons()
        finally:
            self._auto_trade_initial_read_snapshot = previous_snapshot

    def review_required_stock_count(self) -> int:
        """검토관리창과 동일 Collector 기준으로 대상 종목 수를 계산한다."""
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is None:
            return len(collect_global_review_required_rows())
        return len(
            collect_global_review_required_rows(
                preloaded_stocks=snapshot.get("stocks", ()),
                preloaded_stock_data_by_dir=snapshot.get(
                    "stock_data_by_dir",
                    {},
                ),
            )
        )

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_view"):
            return
        self.btn_review_view.setText(f"검토관리({self.review_required_stock_count()})")

    def current_time_policy_minute_key(self) -> str:
        return auto_trade_current_time_policy_minute_key(self)

    def on_time_policy_timer_tick(self) -> None:
        previous_minute_key = self._last_time_policy_gui_minute_key
        try:
            auto_trade_on_time_policy_gui_timer_tick(self)
        except Exception:
            LOGGER.exception("Time policy GUI timer refresh failed")
            self._last_time_policy_gui_minute_key = previous_minute_key
            self.statusBarMessage(
                "시간정책 상태를 갱신하지 못했습니다. "
                "로그를 확인한 뒤 Recovery를 다시 실행하십시오."
            )

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        parent = persistent_feature_owner(self)
        checker = getattr(parent, "startup_recovery_session_ready", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(refresh=refresh))
        except Exception:
            return False

    def rebind_startup_recovery_after_trusted_runtime_update(self) -> bool:
        parent = persistent_feature_owner(self)
        rebind = getattr(
            parent,
            "rebind_startup_recovery_after_trusted_runtime_update",
            None,
        )
        if not callable(rebind):
            return False
        try:
            return bool(rebind())
        except Exception:
            return False

    def require_startup_recovery_session(self, action: str) -> bool:
        if self.startup_recovery_session_ready(refresh=True):
            self._last_operation_block_reason = ""
            return True
        parent = persistent_feature_owner(self)
        reason_getter = getattr(parent, "startup_recovery_block_reason", None)
        reason = ""
        if callable(reason_getter):
            try:
                reason = str(reason_getter() or "").strip()
            except Exception:
                reason = ""
        self._last_operation_block_reason = reason or "RECOVERY_NOT_READY"
        message = startup_recovery_operation_block_message(action, reason)
        self.statusBarMessage(message)
        self.update_startup_recovery_controls()
        return False

    def start_target_is_review_isolated(
        self,
        stock_dir: Path,
        stock_code: str,
    ) -> bool:
        if is_review_required_stock_dir(stock_dir):
            return True
        return recovery_stock_is_review_required(stock_code)

    def filter_start_targets_by_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        action: str,
    ) -> dict[str, object]:
        parent = persistent_feature_owner(self)
        filter_targets = getattr(
            parent,
            "filter_start_targets_by_production_recovery",
            None,
        )
        if callable(filter_targets):
            result = filter_targets(targets, caller_name=action)
            if result.get("allowed") is not True:
                reason = str(result.get("reason") or "RECOVERY_NOT_READY")
                user_message = str(result.get("user_message") or "").strip()
                self._last_operation_block_reason = reason
                self._last_operation_user_message = (
                    user_message
                    or startup_recovery_operation_block_message(action, reason)
                )
                self.statusBarMessage(self._last_operation_user_message)
            return result
        if self.require_startup_recovery_session(action):
            return {
                "allowed": True,
                "reason": "RECOVERY_COMPLETED",
                "eligible": tuple(targets),
                "excluded_review": (),
            }
        return {
            "allowed": False,
            "reason": self._last_operation_block_reason or "RECOVERY_NOT_READY",
            "eligible": (),
            "excluded_review": (),
        }

    def update_startup_recovery_controls(self) -> None:
        ready = self.startup_recovery_session_ready(refresh=False)
        for button in (
            self.btn_execution_enable,
            self.btn_real_ready_preflight,
            self.btn_execution_preview,
            self.btn_manual_send_order,
            self.btn_manual_cancel_pending_order,
            self.btn_manual_modify_pending_order,
        ):
            button.setEnabled(ready)
        if ready:
            self.update_manual_queue_commit_button_state()
        else:
            self.btn_manual_queue_commit.setEnabled(False)
        self.update_global_operation_button_state()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for timer in (self._time_policy_timer, self._pnl_refresh_timer):
            if not timer.isActive():
                timer.start()

    def start_periodic_timers_after_recovery(self, identity) -> dict[str, object]:
        """Compatibility hook: these timers refresh the settings UI only."""
        del identity
        started_count = 0
        if self.isVisible():
            for timer in (self._time_policy_timer, self._pnl_refresh_timer):
                if timer.isActive():
                    continue
                timer.start()
                started_count += 1
        return {
            "started": True,
            "reason_code": "SETTINGS_GUI_TIMERS_STARTED",
            "started_count": started_count,
        }

    def stop_periodic_timers_for_recovery(self) -> dict[str, object]:
        """Stop settings-only GUI refresh timers."""
        stopped_count = 0
        for timer in (self._time_policy_timer, self._pnl_refresh_timer):
            if not timer.isActive():
                continue
            timer.stop()
            stopped_count += 1
        return {
            "stopped": True,
            "reason_code": "SETTINGS_GUI_TIMERS_STOPPED",
            "stopped_count": stopped_count,
        }

    def closeEvent(self, event) -> None:
        """창을 닫을 때 주기 갱신 타이머를 정리한다."""
        self.stop_periodic_timers_for_recovery()
        super().closeEvent(event)

    def refresh_stock_pnl_cells(self) -> None:
        """Update only PnL cells; never rebuild the settings table."""
        rows: list[tuple[object, str]] = []
        for row in range(self.stock_table.rowCount()):
            code_item = self.stock_table.item(row, 0)
            pnl_item = self.stock_table.item(row, 9)
            if code_item is None or pnl_item is None:
                continue
            code = str(code_item.text() or "").strip().lstrip("A")
            if code:
                rows.append((pnl_item, code))
        pnl_by_code = project_current_stock_pnl_snapshot(
            (code for _item, code in rows),
            project_root=PROJECT_ROOT,
        )
        for pnl_item, code in rows:
            result = pnl_by_code.get(code, {})
            if result.get("available") is not True:
                continue
            profit_metric, amount, _rate = confirmable_stock_profit_metric(result)
            text = ratio_metric_text(profit_metric)
            if pnl_item.text() != text:
                pnl_item.setText(text)
            color = QColor(profit_loss_value_color(amount))
            if pnl_item.foreground().color() != color:
                pnl_item.setForeground(color)

    def capture_stock_table_view_state(self) -> tuple[set[str], int]:
        """하단 종목표의 선택 종목 경로와 세로 스크롤 위치를 저장한다."""
        selected_paths: set[str] = set()
        try:
            for row in self.selected_stock_rows():
                item = self.stock_table.item(row, 0)
                if item is not None and item.data(Qt.UserRole):
                    selected_paths.add(str(item.data(Qt.UserRole)))
        except Exception:
            selected_paths = set()

        try:
            scroll_value = self.stock_table.verticalScrollBar().value()
        except Exception:
            scroll_value = 0

        return selected_paths, scroll_value

    def restore_stock_table_view_state(self, selected_paths: set[str], scroll_value: int) -> None:
        """하단 종목표의 선택 종목과 세로 스크롤 위치를 복원한다.

        selectRow()는 선택 복원 중 현재 행으로 자동 스크롤을 이동시킬 수 있다.
        설정 저장/갱신 직후 종목이 튀는 현상을 막기 위해 selectionModel().select()로
        선택 상태만 복원하고, 마지막에 기존 스크롤 위치를 다시 적용한다.
        """
        try:
            if selected_paths:
                self.stock_table.clearSelection()
                selection_model = self.stock_table.selectionModel()
                if selection_model is not None:
                    flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
                    for row in range(self.stock_table.rowCount()):
                        item = self.stock_table.item(row, 0)
                        if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                            index = self.stock_table.model().index(row, 0)
                            selection_model.select(index, flags)
        except Exception:
            pass

        try:
            scroll_bar = self.stock_table.verticalScrollBar()
            scroll_bar.setValue(min(max(0, scroll_value), scroll_bar.maximum()))
        except Exception:
            pass

    def selected_stock_rows(self) -> list[int]:
        return selected_stock_rows(self)

    def has_selected_stock(self) -> bool:
        return has_selected_stock(self)

    def has_single_selected_stock(self) -> bool:
        return has_single_selected_stock(self)

    def update_action_buttons(self) -> None:
        has_stock = self.has_selected_stock()
        single_stock = self.has_single_selected_stock()

        self.btn_early_close.setEnabled(self.has_early_close_scope_targets())
        self.btn_set_schedule.setEnabled(True)
        self.btn_stock_register.setEnabled(True)
        self.btn_log_view.setEnabled(single_stock)
        self.btn_review_view.setEnabled(True)
        self.update_startup_recovery_controls()

    def refresh_stock_instance_chart_open_code_styles(self) -> None:
        refresh_auto_trade_chart_open_code_styles(self)

    def registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is not None:
            targets: list[tuple[Path, str, str]] = []
            for stock in snapshot.get("stocks", ()):
                stock_path = str(stock.get("stock_path", "") or "").strip()
                code = str(stock.get("code", "") or "").strip()
                name = str(stock.get("name", "") or "").strip()
                if stock_path and code:
                    targets.append((PROJECT_ROOT / stock_path, code, name))
            return targets
        return auto_trade_registered_operation_targets(self)

    def registered_operation_start_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_registered_operation_start_targets(self)

    def running_registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is not None:
            stock_data_by_dir = snapshot.get("stock_data_by_dir", {})
            return auto_trade_running_registered_operation_targets(
                self,
                registered_targets=self.registered_operation_targets(),
                operation_excluded_by_stock_dir={
                    stock_dir: is_operation_excluded(
                        data.get("config", {})
                    )
                    for stock_dir, data in stock_data_by_dir.items()
                },
                state_by_stock_dir={
                    stock_dir: data.get("state", {})
                    for stock_dir, data in stock_data_by_dir.items()
                },
            )
        return auto_trade_running_registered_operation_targets(self)

    def update_global_operation_button_state(self) -> None:
        auto_trade_update_global_operation_button_state(self)

    def on_stock_selection_changed(self) -> None:
        self.update_action_buttons()

    def stock_info_from_row(self, row: int) -> tuple[Path, str, str] | None:
        stock_dir = self.operation_stock_dir_from_row(row)
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)
        if stock_dir is None or code_item is None or name_item is None:
            return None
        return stock_dir, code_item.text().strip(), name_item.text().strip()

    def on_stock_table_code_item_double_clicked(
        self,
        item: QTableWidgetItem,
    ) -> None:
        """Open the common instance chart only from the stock-code column."""
        if item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= self.stock_table.rowCount():
            return
        stock_code = item.text().strip()
        if not stock_code:
            return
        open_stock_instance_chart(
            stock_code,
            trade_date=None,
            parent=self,
        )

    def on_stock_table_name_item_double_clicked(
        self,
        item: QTableWidgetItem,
    ) -> None:
        if item.column() != 1:
            return
        target = self.stock_info_from_row(item.row())
        if target is None:
            return
        handle_stock_name_operation_exclusion_double_click(self, target)

    def set_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
        excluded: bool,
        *,
        notify: bool = True,
        refresh: bool = True,
    ) -> bool:
        return auto_trade_set_stock_operation_exclusion(
            self,
            target,
            excluded,
            notify=notify,
            refresh=refresh,
        )

    def toggle_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
        *,
        refresh: bool = True,
    ) -> bool:
        return auto_trade_toggle_stock_operation_exclusion(
            self,
            target,
            refresh=refresh,
        )

    def set_selected_stock_operation_exclusions(self) -> None:
        auto_trade_set_selected_stock_operation_exclusions(self)

    def clear_selected_stock_operation_exclusions(self) -> None:
        auto_trade_clear_selected_stock_operation_exclusions(self)

    def operation_stock_dir_from_row(self, row: int) -> Path | None:
        code_item = self.stock_table.item(row, 0)
        if code_item is None:
            return None
        stock_dir_text = code_item.data(Qt.UserRole)
        if not stock_dir_text:
            return None
        stock_dir = Path(str(stock_dir_text))
        if not stock_dir.exists():
            return None
        return stock_dir

    def on_stock_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """운영 칸 더블클릭 시 시간/수동을 빠르게 전환한다."""
        if item.column() != 2:
            return

        row = item.row()
        stock_dir = self.operation_stock_dir_from_row(row)
        if stock_dir is None:
            return

        self.stock_table.selectRow(row)
        target = self.stock_info_from_row(row)
        if target is None:
            return
        if bool(getattr(self, "_stock_operation_mode_double_click_pending", False)):
            return
        self._stock_operation_mode_double_click_pending = True

        def run_operation_mode_toggle(target_snapshot=target) -> None:
            try:
                handle_auto_trade_operation_mode_double_click(self, target_snapshot)
            finally:
                try:
                    self._stock_operation_mode_double_click_pending = False
                except RuntimeError:
                    pass

        QTimer.singleShot(0, run_operation_mode_toggle)

    def ensure_context_row_selected(self, row: int) -> None:
        ensure_context_row_selected(self, row)

    def select_all_current_routine_stocks(self) -> None:
        select_all_current_routine_stocks(self)

    def clear_current_routine_stock_selection(self) -> None:
        clear_current_routine_stock_selection(self)

    def on_stock_table_context_menu(self, pos) -> None:
        show_auto_trade_stock_context_menu(self, pos)

    def apply_selected_individual_liquidation_method(
        self,
        method: str,
        minutes_before_regular_close: str = "5",
    ) -> None:
        auto_trade_apply_selected_individual_liquidation_method(
            self,
            method,
            minutes_before_regular_close,
        )

    def individual_liquidation_status_text(self, policy_values: dict[str, object]) -> str:
        if not bool(policy_values.get("enabled", False)):
            return "환경설정 사용"
        method = short_close_method_text(policy_values.get("method", "이월"))
        if method == "이월":
            return "청산 안함(이월)"
        minutes = str(policy_values.get("minutes_before_regular_close", "5")).strip() or "5"
        return f"개별 {minutes}분/{method}"

    def selected_manual_ats_state(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, bool]:
        return auto_trade_selected_manual_ats_state(self, selected)

    def selected_manual_ats_liquidation_available(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> bool:
        return auto_trade_selected_manual_ats_liquidation_available(
            self,
            selected,
        )

    def selected_manual_ats_execution_method_state(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, object]:
        return auto_trade_selected_manual_ats_execution_method_state(self, selected)

    def save_selected_manual_ats_state(
        self,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
    ) -> int:
        return auto_trade_save_selected_manual_ats_state(
            self,
            ats_state,
            selected,
            editable_keys,
        )

    def set_selected_manual_ats_flag(self, flag_key: str, enabled: bool, label: str) -> None:
        auto_trade_set_selected_manual_ats_flag(self, flag_key, enabled, label)

    def set_selected_manual_ats_execution_method(
        self,
        execution_method: str,
        label: str,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, object]:
        return auto_trade_set_selected_manual_ats_execution_method(
            self,
            execution_method,
            label,
            selected,
        )

    def execute_selected_manual_ats_liquidation(
        self,
        method: str,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
        selected_sessions: tuple[str, ...] | None = None,
    ) -> None:
        auto_trade_execute_selected_manual_ats_liquidation(
            self,
            method,
            ats_state,
            selected,
            editable_keys,
            selected_sessions,
        )

    def selected_operation_mode_set(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> set[str]:
        """선택 종목들의 운영방식 집합을 반환한다."""
        selected = selected if selected is not None else self.selected_stock_infos()
        modes: set[str] = set()
        for stock_dir, _, _ in selected:
            config = read_json_dict(stock_dir / "config.json")
            if not config:
                config = default_config()
            modes.add(normalize_operation_mode(config.get("operation_mode", "SCHEDULED")))
        return modes

    def selected_trade_permission_context_label(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> str:
        selected = selected if selected is not None else self.selected_stock_infos()
        return selected_stock_trade_permission_label(selected)

    def selected_trade_permission_available(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> bool:
        selected = selected if selected is not None else self.selected_stock_infos()
        return selected_stock_trade_permission_available(self, selected)

    def toggle_selected_trade_permission(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, object]:
        selected = selected if selected is not None else self.selected_stock_infos()
        if not selected:
            self.statusBarMessage("거래권한을 변경할 종목을 1개 이상 선택하세요.")
            return {"ok": False, "changed": 0, "blocked": 0, "reason": "NO_SELECTION"}
        target_enabled = selected_stock_real_trade_target_enabled(selected)
        result = execute_selected_stock_real_trade_command(
            self,
            selected,
            target_enabled,
        )
        changed_targets = tuple(result.get("changed_targets", ()) or ())
        blocked_targets = tuple(result.get("blocked_targets", ()) or ())
        if changed_targets:
            append_changelog(
                "UPDATE",
                "config.json",
                f"거래권한 변경: {' / '.join(changed_targets)} -> {'실주문' if target_enabled else '감시전용'}",
            )
            refresh_auto_trade_views(self)
        message = f"거래권한 변경: {result.get('changed', 0)}개"
        if blocked_targets:
            message += f" / 차단 {len(blocked_targets)}개"
        self.statusBarMessage(message)
        return result

    def toggle_selected_manual_override_flag(self, flag_key: str, label: str) -> None:
        """수동운영 종목의 개별 수동시간 사용 여부를 즉시 전환한다.

        저장 위치:
        - config.json / manual_operation_override
        - 아직 실제 주문 연동 전 단계이므로, 우클릭 즉시 설정값을 먼저 보존한다.
        """
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "설정할 종목을 1개 이상 선택하세요.")
            return

        changed: list[str] = []
        for stock_dir, code, name in selected:
            config_path = stock_dir / "config.json"
            config = read_json_dict(config_path)
            if not config:
                config = default_config()

            if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
                continue

            manual_override = config.get("manual_operation_override", {})
            if not isinstance(manual_override, dict):
                manual_override = {}
            manual_override = deepcopy(manual_override)

            current_value = bool(manual_override.get(flag_key, False))
            manual_override[flag_key] = not current_value
            changed_at = now_text()
            patch = {
                "manual_operation_override": manual_override,
                "policy_override_enabled": True,
                "policy_override_updated_at": changed_at,
                "updated_at": changed_at,
            }

            result = _patch_stock_policy_override_config(
                stock_dir,
                code,
                patch,
                expected_fields=_stock_policy_override_expected_fields(
                    config,
                    ("manual_operation_override", "policy_override_enabled"),
                ),
            )
            if not result.ok:
                QMessageBox.critical(
                    self,
                    "저장 오류",
                    f"{code} {name} 설정 저장 중 오류가 발생했습니다.\n\n{result.reason_code}",
                )
                continue

            changed.append(f"{code} {name}({label}: {'ON' if manual_override[flag_key] else 'OFF'})")
            append_stock_log(stock_dir, "GUI", f"우클릭 수동운영 설정 변경: {label} -> {'ON' if manual_override[flag_key] else 'OFF'}")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 변경: {' / '.join(changed)}")
            self.statusBarMessage(f"{label} 전환 완료: {len(changed)}개")
            refresh_auto_trade_views(self)
        else:
            QMessageBox.information(self, "처리 없음", "수동운영 종목만 이 메뉴를 사용할 수 있습니다.")

    def reset_selected_manual_override(self) -> None:
        """선택 수동운영 종목의 수동운영 개별설정을 제거한다."""
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "리셋할 종목을 1개 이상 선택하세요.")
            return

        changed: list[str] = []
        for stock_dir, code, name in selected:
            config_path = stock_dir / "config.json"
            config = read_json_dict(config_path)
            if not config:
                config = default_config()

            if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
                continue

            if "manual_operation_override" not in config:
                continue

            changed_at = now_text()
            patch = {
                "manual_operation_override": STOCK_CONFIG_DELETE_FIELD,
                "policy_override_updated_at": changed_at,
                "updated_at": changed_at,
            }

            result = _patch_stock_policy_override_config(
                stock_dir,
                code,
                patch,
                expected_fields=_stock_policy_override_expected_fields(
                    config,
                    ("manual_operation_override",),
                ),
            )
            if not result.ok:
                QMessageBox.critical(
                    self,
                    "리셋 오류",
                    f"{code} {name} 설정 리셋 중 오류가 발생했습니다.\n\n{result.reason_code}",
                )
                continue

            changed.append(f"{code} {name}")
            append_stock_log(stock_dir, "GUI", "우클릭 수동운영 개별설정 리셋")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 리셋: {' / '.join(changed)}")
            self.statusBarMessage(f"수동운영 기본 리셋 완료: {len(changed)}개")
            refresh_auto_trade_views(self)
        else:
            QMessageBox.information(self, "처리 없음", "리셋할 수동운영 개별설정이 없습니다.")

    def _routine_instance_stock_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        root = Path(__file__).resolve().parent
        for stock_dir, instance_id, _stock in self._current_stock_entries_by_instance(root=root):
            counts[instance_id] = counts.get(instance_id, 0) + 1
        return counts

    def _current_stock_entries_by_instance(
        self,
        root: Path | None = None,
    ) -> list[tuple[Path, str, dict[str, object]]]:
        project_root = root if root is not None else Path(__file__).resolve().parent
        entries: list[tuple[Path, str, dict[str, object]]] = []
        snapshot = auto_trade_initial_read_snapshot(self)
        stocks = (
            snapshot.get("stocks", ())
            if snapshot is not None
            else read_base_stocks()
        )
        stock_data_by_dir = (
            snapshot.get("stock_data_by_dir", {})
            if snapshot is not None
            else {}
        )
        for stock in stocks:
            stock_path = str(stock.get("stock_path", "") or "").strip()
            if not stock_path:
                continue
            stock_dir = project_root / stock_path
            instance_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if not instance_id:
                snapshot_data = stock_data_by_dir.get(str(stock_dir), {})
                config = (
                    snapshot_data.get("config", {})
                    if snapshot_data
                    else read_json_dict(stock_dir / "config.json")
                )
                instance_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
            if not instance_id:
                continue
            entries.append((stock_dir, instance_id, dict(stock)))
        return entries

    def _current_stocks_by_instance(self) -> dict[str, list[dict[str, object]]]:
        stocks_by_instance: dict[str, list[dict[str, object]]] = {}
        for stock_dir, instance_id, stock in self._current_stock_entries_by_instance():
            stock_name = str(stock.get("name", "") or "").strip()
            stock_code = str(stock.get("code", "") or "").strip()
            if not stock_name or not stock_code:
                parsed_code, parsed_name = parse_stock_folder_name(stock_dir.name)
                stock_code = stock_code or parsed_code
                stock_name = stock_name or parsed_name
            stocks_by_instance.setdefault(instance_id, []).append(
                {
                    "stock_path": str(stock.get("stock_path", "") or stock_dir),
                    "stock_code": stock_code,
                    "stock_name": stock_name or stock_code or stock_dir.name,
                }
            )
        for stocks in stocks_by_instance.values():
            stocks.sort(key=lambda item: (str(item.get("stock_name", "")), str(item.get("stock_code", ""))))
        return stocks_by_instance

    def _historical_stocks_by_instance(self) -> dict[str, list[dict[str, object]]]:
        stocks_by_instance: dict[str, list[dict[str, object]]] = {}
        snapshot = auto_trade_initial_read_snapshot(self)
        assignment_history = (
            snapshot.get("assignment_history", ())
            if snapshot is not None
            else StockRepository(PROJECT_ROOT).list_routine_assignment_history()
        )
        for stock in assignment_history:
            instance_id = str(stock.get("instance_id", "") or "").strip()
            if not instance_id:
                continue
            stocks_by_instance.setdefault(instance_id, []).append(
                {
                    **stock,
                    "is_historical": True,
                }
            )
        if auto_trade_setting_historical_fixture_enabled():
            current_stocks_by_instance = self._current_stocks_by_instance()
            hidden_fixture_keys = getattr(
                self,
                "_hidden_historical_stock_fixture_keys",
                set(),
            )
            existing_keys = {
                (
                    instance_id,
                    str(stock.get("stock_code", "") or "").strip(),
                )
                for instance_id, stocks in stocks_by_instance.items()
                for stock in stocks
            }
            fixture_instances = list(
                snapshot.get("instances", ())
                if snapshot is not None
                else load_persisted_routine_instances()
            )
            for fixture in AUTO_TRADE_SETTING_HISTORICAL_MANUAL_AGGREGATION_FIXTURES:
                if not fixture_instances:
                    break
                stock_code = str(fixture.get("stock_code", "") or "").strip()
                stock_name = str(fixture.get("stock_name", "") or "").strip()
                if not stock_code or not stock_name:
                    continue
                target_instance = None
                for instance in fixture_instances:
                    instance_id = str(instance.instance_id)
                    fixture_key = (instance_id, stock_code)
                    current_codes = {
                        str(stock.get("stock_code", "") or "").strip()
                        for stock in current_stocks_by_instance.get(instance_id, [])
                    }
                    if (
                        stock_code in current_codes
                        or fixture_key in existing_keys
                        or fixture_key in hidden_fixture_keys
                    ):
                        continue
                    target_instance = instance
                    break
                if target_instance is None:
                    continue
                instance_id = str(target_instance.instance_id)
                fixture_key = (instance_id, stock_code)
                stocks_by_instance.setdefault(instance_id, []).append(
                    {
                        "instance_id": instance_id,
                        "stock_path": "",
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "is_historical": True,
                        "is_development_fixture": True,
                        "performance_fixture": dict(
                            fixture.get("performance_fixture", {})
                        ),
                    }
                )
                existing_keys.add(fixture_key)

            for instance in fixture_instances:
                instance_id = str(instance.instance_id)
                current_codes = {
                    str(stock.get("stock_code", "") or "").strip()
                    for stock in current_stocks_by_instance.get(instance_id, [])
                }
                fixture_stocks: list[tuple[str, str]] = []
                for stock_code, stock_name in (
                    AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_CANDIDATES
                ):
                    fixture_key = (instance_id, stock_code)
                    if (
                        stock_code in current_codes
                        or fixture_key in existing_keys
                    ):
                        continue
                    fixture_stocks.append((stock_code, stock_name))
                    if len(fixture_stocks) == 5:
                        break
                for fixture_index, (stock_code, stock_name) in enumerate(
                    fixture_stocks
                ):
                    fixture_key = (instance_id, stock_code)
                    if fixture_key in hidden_fixture_keys:
                        continue
                    stocks_by_instance.setdefault(instance_id, []).append(
                        {
                            "instance_id": instance_id,
                            "stock_path": "",
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "is_historical": True,
                            "is_development_fixture": True,
                            "performance_fixture": dict(
                                AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE[
                                    fixture_index
                                    % len(
                                        AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE
                                    )
                                ]
                            ),
                        }
                    )
        for stocks in stocks_by_instance.values():
            stocks.sort(
                key=lambda item: (
                    str(item.get("stock_name", "")),
                    str(item.get("stock_code", "")),
                )
            )
        return stocks_by_instance

    def _canonical_performance_snapshot_for_tree(
        self,
        *,
        stocks: list[object],
        instances: list[object],
        groups: list[object],
    ) -> CanonicalPerformanceUiSnapshot | None:
        initial = auto_trade_initial_read_snapshot(self)
        if initial is not None:
            snapshot = initial.get("canonical_performance")
            if isinstance(snapshot, CanonicalPerformanceUiSnapshot):
                return snapshot
            self._canonical_performance_snapshot_error = str(
                initial.get("canonical_performance_error", "") or ""
            )
            return None
        try:
            snapshot = build_canonical_performance_ui_snapshot(
                PROJECT_ROOT,
                stocks=stocks,
                instances=instances,
                groups=groups,
            )
        except Exception as exc:
            self._canonical_performance_snapshot_error = str(exc)
            LOGGER.exception("canonical AutoTrade performance snapshot failed")
            return None
        self._canonical_performance_snapshot_error = ""
        return snapshot

    def _canonical_metric_status_rank(self, status: MetricStatus) -> int:
        return {
            MetricStatus.VALID: 0,
            MetricStatus.VALID_ZERO: 0,
            MetricStatus.INCOMPLETE: 1,
            MetricStatus.UNDEFINED: 2,
            MetricStatus.UNAVAILABLE: 3,
        }[status]

    def _canonical_metric_tooltip(self, label: str, metric) -> str:
        lines = [f"{label}: {metric.status.value}"]
        if metric.reasons:
            lines.append("사유: " + ", ".join(metric.reasons))
        return "\n".join(lines)

    def _routine_tree_canonical_performance_texts(
        self,
        projection_row: object | None,
        canonical_snapshot: CanonicalPerformanceUiSnapshot | None,
    ) -> dict[str, object]:
        unavailable_reason = str(
            getattr(self, "_canonical_performance_snapshot_error", "") or ""
        ).strip()
        if projection_row is None or canonical_snapshot is None:
            reason = unavailable_reason or "CANONICAL_PERFORMANCE_NOT_RECORDED"
            tooltip = f"UNAVAILABLE\n사유: {reason}"
            initial_zero_display = (
                projection_row is None
                and canonical_snapshot is not None
                and not unavailable_reason
            )
            period_value = "0" if initial_zero_display else "-"
            profit_amount = "0" if initial_zero_display else "-"
            profit_rate = "0.00%" if initial_zero_display else "-"
            average_amount = "0" if initial_zero_display else "-"
            average_rate = "0.00%" if initial_zero_display else "-"
            efficiency_value = "0.0" if initial_zero_display else "-"
            return {
                "performance_period_text": f"기간({period_value})",
                "performance_profit_text": f"수익({profit_amount} / {profit_rate})",
                "performance_average_text": f"평균({average_amount} / {average_rate})",
                "performance_efficiency_text": f"효율({efficiency_value})",
                "performance_period_value": period_value,
                "performance_profit_amount": profit_amount,
                "performance_profit_rate": profit_rate,
                "performance_average_amount": average_amount,
                "performance_average_rate": average_rate,
                "performance_efficiency_value": efficiency_value,
                "performance_period_sort_value": 0.0,
                "performance_profit_sort_value": 0.0,
                "performance_average_sort_value": 0.0,
                "performance_efficiency_sort_value": 0.0,
                "performance_period_sort_status_rank": 3,
                "performance_profit_sort_status_rank": 3,
                "performance_average_sort_status_rank": 3,
                "performance_efficiency_sort_status_rank": 3,
                "performance_period_tooltip": tooltip,
                "performance_profit_tooltip": tooltip,
                "performance_average_tooltip": tooltip,
                "performance_efficiency_tooltip": tooltip,
                "performance_source": "CANONICAL",
            }

        metrics = canonical_snapshot.metric_result(projection_row)
        def _display(metric, formatter) -> str:
            if metric.value is None:
                return "-"
            return formatter(metric.value)

        period_value = _display(metrics.period, lambda value: str(int(value)))
        profit_amount = _display(metrics.profit_amount, format_signed_money)
        profit_rate = _display(
            metrics.profit_rate,
            lambda value: format_signed_percent(value, digits=2),
        )
        average_amount = _display(metrics.average_amount, format_signed_money)
        average_rate = _display(
            metrics.average_rate,
            lambda value: format_signed_percent(value, digits=2),
        )
        efficiency_value = _display(
            metrics.efficiency,
            lambda value: f"{float(value):.1f}",
        )

        aggregate = getattr(projection_row, "lifetime", None) or getattr(
            projection_row,
            "aggregate",
            None,
        )
        initial_zero_display = (
            aggregate is not None
            and int(
                getattr(
                    aggregate,
                    "event_count",
                    getattr(aggregate, "performance_event_count", -1),
                )
            ) == 0
            and metrics.period.status == MetricStatus.VALID_ZERO
            and metrics.profit_amount.status == MetricStatus.VALID_ZERO
        )
        if initial_zero_display:
            profit_rate = "0.00%"
            average_amount = "0"
            average_rate = "0.00%"
            efficiency_value = "0.0"

        status_metrics = {
            "period": metrics.period,
            "profit": metrics.profit_amount,
            "average": metrics.average_amount,
            "efficiency": metrics.efficiency,
        }
        status_ranks = {
            key: self._canonical_metric_status_rank(metric.status)
            for key, metric in status_metrics.items()
        }
        tooltips = {
            "period": self._canonical_metric_tooltip("기간", metrics.period),
            "profit": "\n".join(
                (
                    self._canonical_metric_tooltip("수익 금액", metrics.profit_amount),
                    self._canonical_metric_tooltip("수익률", metrics.profit_rate),
                )
            ),
            "average": "\n".join(
                (
                    self._canonical_metric_tooltip("평균 금액", metrics.average_amount),
                    self._canonical_metric_tooltip("평균 수익률", metrics.average_rate),
                    (
                        "기간 진단: "
                        f"전체 {metrics.average_rate_diagnostics.period_count}, "
                        f"유효 {metrics.average_rate_diagnostics.valid_rate_period_count}, "
                        f"불완전 {metrics.average_rate_diagnostics.incomplete_rate_period_count}, "
                        f"미정의 {metrics.average_rate_diagnostics.undefined_rate_period_count}"
                    ),
                )
            ),
            "efficiency": self._canonical_metric_tooltip("효율", metrics.efficiency),
        }

        absence_reason = str(
            getattr(projection_row, "performance_absence_reason", "") or ""
        ).strip()
        if absence_reason:
            diagnostic = "Parent 실적 진단: " + absence_reason
            tooltips = {
                key: f"{value}\n{diagnostic}" for key, value in tooltips.items()
            }

        mismatch_reasons = canonical_snapshot.current_relation_mismatch_reasons(
            projection_row
        )
        if mismatch_reasons:
            mismatch = "CURRENT 관계 불일치: " + ", ".join(mismatch_reasons)
            tooltips = {key: f"{value}\n{mismatch}" for key, value in tooltips.items()}

        return {
            "performance_period_text": f"기간({period_value})",
            "performance_profit_text": f"수익({profit_amount} / {profit_rate})",
            "performance_average_text": f"평균({average_amount} / {average_rate})",
            "performance_efficiency_text": f"효율({efficiency_value})",
            "performance_period_value": period_value,
            "performance_profit_amount": profit_amount,
            "performance_profit_rate": profit_rate,
            "performance_profit_color": profit_loss_value_color(
                metrics.profit_amount.value
            ),
            "performance_average_amount": average_amount,
            "performance_average_rate": average_rate,
            "performance_average_color": profit_loss_value_color(
                metrics.average_amount.value
            ),
            "performance_efficiency_value": efficiency_value,
            "performance_efficiency_color": directional_value_color(
                metrics.efficiency.value
            ),
            "performance_period_sort_value": float(metrics.period.sort_value or 0),
            "performance_profit_sort_value": float(metrics.profit_amount.sort_value or 0),
            "performance_average_sort_value": float(metrics.average_amount.sort_value or 0),
            "performance_efficiency_sort_value": float(metrics.efficiency.sort_value or 0),
            "performance_period_sort_status_rank": status_ranks["period"],
            "performance_profit_sort_status_rank": status_ranks["profit"],
            "performance_average_sort_status_rank": status_ranks["average"],
            "performance_efficiency_sort_status_rank": status_ranks["efficiency"],
            "performance_period_tooltip": tooltips["period"],
            "performance_profit_tooltip": tooltips["profit"],
            "performance_average_tooltip": tooltips["average"],
            "performance_efficiency_tooltip": tooltips["efficiency"],
            "performance_source": "CANONICAL",
        }

    def _routine_tree_stock_performance_source(
        self,
        stock: dict[str, object],
    ) -> dict[str, object]:
        return routine_tree_stock_performance_source(self, stock)

    def _routine_tree_performance_texts(
        self,
        stocks: list[dict[str, object]],
        source_cache: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return routine_tree_performance_texts(self, stocks, source_cache)

    def _routine_tree_stock_group_performance_source(
        self,
        stocks: list[dict[str, object]],
        source_cache: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        cache = source_cache if source_cache is not None else {}
        source_rows: list[dict[str, object]] = []
        for stock in stocks:
            stock_path_key = str(stock.get("stock_path", "") or "").strip()
            is_historical = bool(stock.get("is_historical", False))
            cache_key = stock_path_key
            if is_historical or not cache_key:
                cache_key = "|".join(
                    (
                        str(stock.get("instance_id", "") or "").strip(),
                        str(stock.get("stock_code", "") or "").strip(),
                        stock_path_key,
                        "historical" if is_historical else "current",
                    )
                )
            if cache_key not in cache:
                cache[cache_key] = self._routine_tree_stock_performance_source(stock)
            source_rows.append(cache[cache_key])

        trade_day_total = sum(
            int(source.get("trade_days", 0) or 0)
            for source in source_rows
            if int(source.get("trade_days", 0) or 0) > 0
        )
        realized_values = [
            float(source["realized_profit"])
            for source in source_rows
            if source.get("realized_profit") is not None
        ]
        realized_profit = sum(realized_values) if realized_values else 0.0
        average = (
            realized_profit / trade_day_total
            if trade_day_total > 0 and realized_values
            else 0.0
        )
        weighted_average_rates = [
            (
                float(source.get("average_rate", 0.0) or 0.0),
                int(source.get("trade_days", 0) or 0),
            )
            for source in source_rows
            if source.get("average_rate") is not None
            and int(source.get("trade_days", 0) or 0) > 0
        ]
        average_rate = (
            sum(rate * weight for rate, weight in weighted_average_rates)
            / sum(weight for _rate, weight in weighted_average_rates)
            if weighted_average_rates
            else 0.0
        )
        gross_profit_values = [
            float(source.get("gross_profit", 0.0) or 0.0)
            for source in source_rows
            if source.get("gross_profit") is not None
        ]
        gross_loss_values = [
            float(source.get("gross_loss_abs", 0.0) or 0.0)
            for source in source_rows
            if source.get("gross_loss_abs") is not None
        ]
        gross_profit = sum(gross_profit_values) if gross_profit_values else None
        gross_loss_abs = sum(gross_loss_values) if gross_loss_values else None
        profit_factor = 0.0
        if gross_profit is not None and gross_loss_abs and gross_loss_abs > 0:
            profit_factor = gross_profit / gross_loss_abs
        elif len(source_rows) == 1:
            profit_factor = normalize_profit_factor(
                source_rows[0].get(
                    "profit_factor",
                    source_rows[0].get("efficiency"),
                )
            )

        return {
            "trade_days": trade_day_total,
            "realized_profit": realized_profit,
            "profit_rate": None,
            "average": average,
            "average_rate": average_rate,
            "gross_profit": gross_profit,
            "gross_loss_abs": gross_loss_abs,
            "profit_factor": profit_factor,
            "is_current": any(
                bool(source.get("is_current", False)) for source in source_rows
            ),
        }

    def _routine_tree_row_sort_value(
        self,
        row: dict[str, object],
        criterion: str,
    ) -> float:
        sort_key_by_criterion = {
            "period": "performance_period_sort_value",
            "profit": "performance_profit_sort_value",
            "average": "performance_average_sort_value",
            "efficiency": "performance_efficiency_sort_value",
        }
        raw_value = row.get(sort_key_by_criterion.get(criterion, ""))
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return 0.0

    def _routine_tree_instance_identity_sort_key(
        self,
        row: dict[str, object],
    ) -> tuple[str, str]:
        return (
            str(row.get("instance_name", "") or "").casefold(),
            str(row.get("instance_id", "") or ""),
        )

    def _routine_tree_canonical_sort_key(
        self,
        row: dict[str, object],
        criterion: str,
    ) -> tuple[int, float, str, str]:
        rank = int(
            row.get(
                f"performance_{criterion}_sort_status_rank",
                0,
            )
            or 0
        )
        stable_id = str(
            row.get("group_id", "")
            or row.get("instance_id", "")
            or row.get("stock_code", "")
            or ""
        )
        return (
            rank,
            -self._routine_tree_row_sort_value(row, criterion),
            str(row.get("display_name", "") or "").casefold(),
            stable_id,
        )

    def _routine_tree_sort_definition_blocks(
        self,
        rows: list[dict[str, object]],
        criterion: str,
    ) -> list[dict[str, object]]:
        blocks: list[list[dict[str, object]]] = []
        current_block: list[dict[str, object]] = []
        for row in rows:
            if str(row.get("row_kind", "") or "") == "definition":
                if current_block:
                    blocks.append(current_block)
                current_block = [row]
            else:
                current_block.append(row)
        if current_block:
            blocks.append(current_block)

        sorted_blocks = sorted(
            blocks,
            key=lambda block: self._routine_tree_canonical_sort_key(
                block[0],
                criterion,
            ),
        )
        return [row for block in sorted_blocks for row in block]

    def _routine_tree_sort_instance_blocks(
        self,
        rows: list[dict[str, object]],
        criterion: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            if str(row.get("row_kind", "") or "") != "definition":
                result.append(row)
                index += 1
                continue

            result.append(row)
            index += 1
            instance_blocks: list[list[dict[str, object]]] = []
            while index < len(rows) and str(rows[index].get("row_kind", "") or "") != "definition":
                if str(rows[index].get("row_kind", "") or "") != "instance":
                    result.append(rows[index])
                    index += 1
                    continue
                block = [rows[index]]
                index += 1
                while index < len(rows) and str(rows[index].get("row_kind", "") or "") == "stock":
                    block.append(rows[index])
                    index += 1
                if len(block) > 2:
                    sorted_children = sorted(
                        block[1:],
                        key=lambda child: self._routine_tree_canonical_sort_key(
                            child,
                            criterion,
                        ),
                    )
                    for child_index, child in enumerate(sorted_children):
                        child["first_stock_for_instance"] = child_index == 0
                    block = [block[0], *sorted_children]
                instance_blocks.append(block)

            sorted_blocks = sorted(
                instance_blocks,
                key=lambda block: self._routine_tree_canonical_sort_key(
                    block[0],
                    criterion,
                ),
            )
            for block_index, block in enumerate(sorted_blocks):
                block[0]["instance_group_top_gap"] = block_index > 0
                result.extend(block)
        return result

    def _routine_instance_operation_counts(self) -> dict[str, dict[str, object]]:
        from gui_main_table_loader import _instance_stock_counts

        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is None:
            return _instance_stock_counts(window=self)
        return _instance_stock_counts(
            window=self,
            static_data=snapshot.get("count_static_data", {}),
            state_by_stock_dir={
                stock_dir: data.get("state", {})
                for stock_dir, data in snapshot.get(
                    "stock_data_by_dir",
                    {},
                ).items()
            },
            state_issue_by_stock_dir={
                stock_dir: str(data.get("state_issue_reason", "") or "")
                for stock_dir, data in snapshot.get(
                    "stock_data_by_dir",
                    {},
                ).items()
            },
        )

    def _is_default_operation_instance(self, metadata: dict[str, object]) -> bool:
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not definition_id or not instance_id:
            return False
        return (
            getattr(self, "_default_operation_instance_by_definition", {}).get(definition_id)
            == instance_id
        )

    def _routine_status_text_for_metadata(self, metadata: dict[str, object]) -> str:
        if self._is_default_operation_instance(metadata):
            return ROUTINE_STATUS_DEFAULT
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        return str(
            getattr(self, "_routine_operation_status_by_instance", {}).get(instance_id, "")
            or ""
        )

    def set_default_operation_instance_from_metadata(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not definition_id or not instance_id:
            return
        defaults = getattr(self, "_default_operation_instance_by_definition", {})
        if defaults.get(definition_id) == instance_id:
            defaults.pop(definition_id, None)
            self._routine_operation_status_by_instance.pop(instance_id, None)
        else:
            previous_instance_id = defaults.get(definition_id)
            if previous_instance_id:
                self._routine_operation_status_by_instance.pop(previous_instance_id, None)
            defaults[definition_id] = instance_id
            self._routine_operation_status_by_instance[instance_id] = ROUTINE_STATUS_DEFAULT
        self._default_operation_instance_by_definition = defaults
        self._refresh_default_operation_stamps()
        self.update_selection_summary_panel()

    def _refresh_default_operation_stamps(self) -> None:
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            widget = self.routine_table.cellWidget(row, 0)
            if widget is None:
                continue
            stamp = widget.findChild(QPushButton, "autoTradeSettingDefaultOperationStamp")
            if stamp is None:
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            if row_kind == "instance":
                active_default = self._is_default_operation_instance(metadata)
            else:
                active_default = bool(
                    getattr(self, "_default_operation_instance_by_definition", {}).get(
                        str(metadata.get("definition_id", "") or "")
                    )
                )
            from gui_main_table_loader import routine_status_stamp_spec

            _display_status, color = routine_status_stamp_spec(
                ROUTINE_STATUS_DEFAULT if active_default else ""
            )
            stamp_color = color or "#9CA3AF"
            stamp.setChecked(active_default)
            stamp.setStyleSheet(
                "QPushButton {"
                " background-color: #FFFFFF;"
                f" border: 1px solid {stamp_color};"
                " border-radius: 4px;"
                f" color: {stamp_color};"
                " font-weight: 600;"
                "}"
                "QPushButton:disabled {"
                " color: #9CA3AF;"
                " border-color: #D1D5DB;"
                " background-color: #FFFFFF;"
                "}"
            )

    def _routine_tree_metric_text_parts(
        self,
        raw_text: object,
        left_fallback: str,
        right_fallback: str,
    ) -> tuple[str, str]:
        text = str(raw_text or "").strip()
        if text:
            open_index = text.find("(")
            close_index = text.rfind(")")
            if open_index >= 0 and close_index > open_index:
                inside = text[open_index + 1:close_index]
                if " / " in inside:
                    left, right = inside.split(" / ", 1)
                    return left.strip() or left_fallback, right.strip() or right_fallback
                return inside.strip() or left_fallback, right_fallback
        return left_fallback, right_fallback

    def _routine_tree_metric_values(
        self,
        row_data: dict[str, object],
        metric: str,
        left_fallback: str,
        right_fallback: str,
    ) -> tuple[str, str]:
        value_keys = {
            "period": ("performance_period_value", ""),
            "profit": (
                "performance_profit_amount",
                "performance_profit_rate",
            ),
            "average": (
                "performance_average_amount",
                "performance_average_rate",
            ),
            "efficiency": ("performance_efficiency_value", ""),
        }
        left_key, right_key = value_keys.get(metric, ("", ""))
        if left_key and left_key in row_data:
            left_value = str(row_data.get(left_key, "") or "").strip() or left_fallback
            right_value = (
                str(row_data.get(right_key, "") or "").strip() or right_fallback
                if right_key
                else right_fallback
            )
            return left_value, right_value
        return self._routine_tree_metric_text_parts(
            row_data.get(f"performance_{metric}_text", ""),
            left_fallback,
            right_fallback,
        )

    def _configure_routine_tree_row_layout(
        self,
        rows: list[dict[str, object]],
    ) -> None:
        """Configure the fixed title and performance axes used by every tree row."""
        base_font = QFont(self.routine_table.font())
        base_metrics = QFontMetrics(base_font)
        label_metrics = QFontMetrics(QApplication.font("QLabel"))
        geometry = routine_tree_layout_metrics(base_font)

        def _text_width(value: object) -> int:
            text = str(value or "")
            return max(
                width
                for metrics in (base_metrics, label_metrics)
                for width in (
                    metrics.horizontalAdvance(text),
                    metrics.boundingRect(text).width(),
                )
            )

        metric_slots: dict[str, tuple[int, int]] = {}
        for key, (left_samples, right_samples) in (
            AUTO_TRADE_SETTING_ROUTINE_TREE_METRIC_SAMPLES.items()
        ):
            left_width = max(
                (_text_width(sample) for sample in left_samples),
                default=0,
            )
            right_width = max(
                (_text_width(sample) for sample in right_samples),
                default=0,
            )
            metric_slots[key] = (left_width, right_width)

        close_width = _text_width(")")
        metric_geometry: dict[str, dict[str, int]] = {}
        for spec in AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS:
            key = str(spec["key"])
            left_width, right_width = metric_slots[key]
            prefix_width = _text_width(f"{spec['label']}(")
            slash_width = _text_width(" / ") if spec["right_sample"] else 0
            metric_geometry[key] = {
                "prefix_width": prefix_width,
                "left_width": int(left_width),
                "slash_width": slash_width,
                "right_width": int(right_width) if spec["right_sample"] else 0,
                "close_width": close_width,
                "metric_width": (
                    prefix_width
                    + int(left_width)
                    + slash_width
                    + (int(right_width) if spec["right_sample"] else 0)
                    + close_width
                ),
            }

        self._routine_tree_row_geometry = geometry
        self._routine_tree_metric_slots = metric_slots
        self._routine_tree_render_context = {
            "font": base_font,
            "geometry": geometry,
            "metric_geometry": metric_geometry,
            "separator_width": _text_width("|"),
        }

    def _routine_tree_row_widget(self, row_data: dict[str, object], text: str) -> QWidget:
        row_kind = str(row_data.get("row_kind", "") or "")
        is_instance = row_kind == "instance"
        is_stock = row_kind == "stock"
        is_historical_stock = is_stock and bool(row_data.get("is_historical", False))
        is_definition = row_kind == "definition"
        render_context = getattr(self, "_routine_tree_render_context", None)
        if not isinstance(render_context, dict):
            self._configure_routine_tree_row_layout([])
            render_context = self._routine_tree_render_context
        container = QWidget()
        container.setFont(QFont(render_context["font"]))
        container.setFocusPolicy(Qt.NoFocus)
        container.setMouseTracking(True)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        top_margin = (
            AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
            if (
                (is_instance and bool(row_data.get("instance_group_top_gap")))
                or (is_stock and bool(row_data.get("first_stock_for_instance")))
            )
            else 0
        )
        tree_geometry = render_context["geometry"]
        horizontal_margin = tree_geometry["outer_margin"]
        layout.setContentsMargins(
            horizontal_margin,
            top_margin,
            tree_geometry["performance_trailing_margin"],
            0,
        )
        layout.setSpacing(0)
        if is_stock:
            row_data = dict(row_data)
            row_data["tree_icon"] = "\u25aa" if is_historical_stock else "\u2713"
        stock_row_color = (
            AUTO_TRADE_SETTING_HISTORICAL_STOCK_ROW_TEXT_COLOR
            if is_historical_stock
            else AUTO_TRADE_SETTING_STOCK_ROW_TEXT_COLOR
        )
        icon_label = QLabel(str(row_data.get("tree_icon", "") or ""))
        icon_label.setObjectName("autoTradeSettingRoutineTreeIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedWidth(
            tree_geometry["parent_icon_width"]
            if row_kind == "definition"
            else tree_geometry["child_icon_width"]
        )
        icon_label.setWordWrap(False)
        icon_label.setFocusPolicy(Qt.NoFocus)
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon_label.setStyleSheet(
            "background: transparent;"
            f" color: {stock_row_color if is_stock else '#6B7280'};"
        )
        if is_definition:
            icon_font = QFont(container.font())
            icon_font.setPointSize(icon_font.pointSize() + 2)
            icon_label.setFont(icon_font)
            icon_label.setCursor(Qt.PointingHandCursor)
            icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleDefinitionId",
                routine_tree_parent_identity(row_data),
            )
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleEnabled",
                bool(row_data.get("has_toggle_children", True)),
            )
            try:
                can_install_event_filter = isinstance(self, AutoTradeSettingWindow) and self.routine_table is not None
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                icon_label.installEventFilter(self)
        elif is_instance:
            icon_label.setCursor(Qt.PointingHandCursor)
            icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleInstanceId",
                str(row_data.get("instance_id", "") or ""),
            )
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleEnabled",
                bool(row_data.get("has_toggle_children", True)),
            )
            try:
                can_install_event_filter = isinstance(self, AutoTradeSettingWindow) and self.routine_table is not None
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                icon_label.installEventFilter(self)
        raw_title_text = str(row_data.get("display_name", "") or text)
        title_text = (
            routine_tree_instance_title_text(raw_title_text)
            if is_instance
            else tree_title_text(raw_title_text)
            if is_definition
            else routine_tree_title_text(raw_title_text)
        )
        title_label = QLabel(title_text)
        title_label.setObjectName("autoTradeSettingRoutineTreeTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setWordWrap(False)
        title_label.setFocusPolicy(Qt.NoFocus)
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        title_tooltip = (
            str(row_data.get("display_name", "") or text)
            if is_stock
            else tree_title_tooltip(raw_title_text)
            if is_definition or is_instance
            else ""
        )
        title_label.setToolTip(title_tooltip)
        base_title_font = QFont(container.font())
        title_font = QFont(base_title_font)
        if not is_definition:
            title_font.setBold(False)
        else:
            title_font.setPointSize(title_font.pointSize() + 1)
            title_font.setWeight(QFont.DemiBold)
        title_label.setStyleSheet(
            "background: transparent;"
            " border: none;"
            f" color: {stock_row_color if is_stock else '#374151'};"
        )
        title_label.setFont(title_font)
        title_width = (
            tree_geometry["parent_title_width"]
            if is_definition
            else tree_geometry["stock_title_width"]
            if is_stock
            else tree_geometry["child_title_width"]
        )
        title_label.setFixedWidth(title_width)
        title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        performance_item_specs = (
            AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS
        )
        separator_width = int(render_context["separator_width"])

        identity_widget = QWidget()
        identity_widget.setObjectName(
            "autoTradeSettingRoutineTreeMetaGroup"
            if is_definition
            else "autoTradeSettingRoutineTreeIdentity"
        )
        identity_widget.setFocusPolicy(Qt.NoFocus)
        identity_widget.setAttribute(Qt.WA_StyledBackground, True)
        identity_widget.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            not (is_definition or is_instance),
        )
        identity_widget.setStyleSheet("background: transparent;")
        if is_definition or is_instance:
            identity_widget.setMouseTracking(True)
            identity_widget.setProperty(
                "autoTradeSettingRoutineTreeHoverIdentityRowKind",
                row_kind,
            )
            try:
                can_install_event_filter = (
                    isinstance(self, AutoTradeSettingWindow)
                    and self.routine_table is not None
                )
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                identity_widget.installEventFilter(self)
        identity_width = (
            tree_geometry["parent_identity_width"]
            if is_definition
            else tree_geometry["instance_identity_width"]
            if is_instance
            else tree_geometry["stock_identity_width"]
        )
        identity_widget.setFixedWidth(identity_width)
        identity_layout = QHBoxLayout(identity_widget)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(0)
        indent_width = (
            tree_geometry["instance_indent"]
            if is_instance
            else tree_geometry["stock_indent"]
            if is_stock
            else 0
        )
        if indent_width:
            indent_spacer = QWidget()
            indent_spacer.setObjectName(
                "autoTradeSettingRoutineTreeIndent"
                if is_instance
                else "autoTradeSettingRoutineTreeStockIndent"
            )
            indent_spacer.setFixedWidth(indent_width)
            indent_spacer.setFocusPolicy(Qt.NoFocus)
            indent_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            identity_layout.addWidget(indent_spacer, 0, Qt.AlignVCenter)
        identity_layout.addWidget(icon_label, 0, Qt.AlignVCenter)
        identity_layout.addSpacing(tree_geometry["item_gap"])
        identity_layout.addWidget(title_label, 0, Qt.AlignVCenter)
        if is_definition:
            instance_count = int(row_data.get("instance_count", 0) or 0)
            routine_count_label = QLabel(f"루틴{instance_count}")
            routine_count_label.setObjectName("autoTradeSettingRoutineTreeInstanceCount")
            routine_count_label.setAlignment(Qt.AlignCenter)
            routine_count_label.setWordWrap(False)
            routine_count_label.setFixedSize(tree_geometry["count_badge_width"], 22)
            routine_count_label.setFocusPolicy(Qt.NoFocus)
            routine_count_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            routine_count_label.setStyleSheet(auto_trade_setting_badge_stylesheet("QLabel"))
            identity_layout.addWidget(routine_count_label, 0, Qt.AlignVCenter)
        elif is_instance or is_stock:
            title_metric_gap = QWidget()
            title_metric_gap.setObjectName(
                "autoTradeSettingRoutineTreeStockMetricGap"
                if is_stock
                else "autoTradeSettingRoutineTreeInstanceMetricGap"
            )
            title_metric_gap.setFixedWidth(
                tree_geometry[
                    "stock_metric_gap" if is_stock else "instance_metric_gap"
                ]
            )
            title_metric_gap.setFocusPolicy(Qt.NoFocus)
            title_metric_gap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            identity_layout.addWidget(title_metric_gap, 0, Qt.AlignVCenter)
        else:
            identity_layout.addStretch(1)
        layout.addWidget(identity_widget, 0, Qt.AlignVCenter)
        if is_definition:
            group_metric_gap = QWidget()
            group_metric_gap.setObjectName(
                "autoTradeSettingRoutineTreeGroupMetricGap"
            )
            group_metric_gap.setFixedWidth(tree_geometry["group_metric_gap"])
            group_metric_gap.setFocusPolicy(Qt.NoFocus)
            group_metric_gap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(group_metric_gap, 0, Qt.AlignVCenter)

        def _metric_label(
            text: str,
            object_name: str,
            width: int,
            alignment: Qt.AlignmentFlag,
        ) -> QLabel:
            label = QLabel(text, metric_widget)
            label.setObjectName(object_name)
            label.setAlignment(alignment)
            label.setWordWrap(False)
            label.setFixedWidth(width)
            label.setFocusPolicy(Qt.NoFocus)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            label.setToolTip("")
            label.setStyleSheet(
                f"background: transparent; color: {metric_color};"
            )
            if is_definition:
                label.setProperty("autoTradeSettingParentSummaryMetric", True)
            return label

        row_performance_item_specs = (
            performance_item_specs[1:]
            if is_definition
            else performance_item_specs
        )
        separator_count = max(0, len(row_performance_item_specs) - 1)
        for index, spec in enumerate(row_performance_item_specs):
            key = str(spec["key"])
            if index > 0:
                separator_index = index - 1
                separator_side_gap = (
                    tree_geometry["performance_separator_edge_side_gap"]
                    if separator_index in {0, separator_count - 1}
                    else tree_geometry["performance_separator_inner_side_gap"]
                )
                layout.addSpacing(separator_side_gap)
                separator = QLabel("|")
                separator.setObjectName("autoTradeSettingRoutineTreePerformanceSeparator")
                separator.setAlignment(Qt.AlignCenter)
                separator.setFixedWidth(separator_width)
                separator.setFocusPolicy(Qt.NoFocus)
                separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                separator_color = stock_row_color if is_stock else "#9CA3AF"
                separator.setStyleSheet(f"background: transparent; color: {separator_color};")
                if is_definition:
                    separator.setProperty("autoTradeSettingParentSummaryMetric", True)
                layout.addWidget(separator, 0, Qt.AlignVCenter)
                layout.addSpacing(separator_side_gap)
            label_text = str(spec["label"])
            right_sample = str(spec["right_sample"])
            left_value, right_value = self._routine_tree_metric_values(
                row_data,
                key,
                str(spec["left_fallback"]),
                str(spec["right_fallback"]),
            )
            metric_color = (
                str(row_data.get(f"performance_{key}_color", "") or "")
                if key in {"profit", "average", "efficiency"}
                else ""
            )
            if not metric_color:
                metric_color = stock_row_color if is_stock else "#6B7280"
            metric_widget = QWidget(container)
            metric_widget.setObjectName(str(spec["object_name"]))
            metric_widget.setFocusPolicy(Qt.NoFocus)
            metric_widget.setAttribute(Qt.WA_StyledBackground, True)
            metric_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            metric_widget.setStyleSheet("background: transparent;")
            metric_widget.setToolTip("")
            metric_layout = QHBoxLayout(metric_widget)
            metric_layout.setContentsMargins(0, 0, 0, 0)
            metric_layout.setSpacing(0)
            label_prefix = f"{label_text}("
            configured_geometry = render_context["metric_geometry"][key]
            prefix_width = configured_geometry["prefix_width"]
            left_width = configured_geometry["left_width"]
            slash_width = configured_geometry["slash_width"]
            right_width = configured_geometry["right_width"]
            close_width = configured_geometry["close_width"]
            metric_width = configured_geometry["metric_width"]
            metric_layout.addWidget(
                _metric_label(
                    label_prefix,
                    f"{spec['object_name']}Label",
                    prefix_width,
                    Qt.AlignLeft | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            metric_layout.addWidget(
                _metric_label(
                    left_value,
                    f"{spec['object_name']}LeftValue",
                    left_width,
                    Qt.AlignRight | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            if right_sample:
                metric_layout.addWidget(
                    _metric_label(
                        " / ",
                        f"{spec['object_name']}Slash",
                        slash_width,
                        Qt.AlignCenter | Qt.AlignVCenter,
                    ),
                    0,
                    Qt.AlignVCenter,
                )
                metric_layout.addWidget(
                    _metric_label(
                        right_value,
                        f"{spec['object_name']}RightValue",
                        right_width,
                        Qt.AlignRight | Qt.AlignVCenter,
                    ),
                    0,
                    Qt.AlignVCenter,
                )
            metric_layout.addWidget(
                _metric_label(
                    ")",
                    f"{spec['object_name']}Close",
                    close_width,
                    Qt.AlignCenter | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            metric_widget.setFixedWidth(metric_width)
            metric_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            if is_definition:
                metric_widget.setProperty("autoTradeSettingParentSummaryMetric", True)
            layout.addWidget(metric_widget, 0, Qt.AlignVCenter)

        layout.addStretch(1)
        container.setToolTip("")
        container.setProperty("autoTradeSettingRoutineTreeRowKind", row_kind)
        try:
            can_install_event_filter = (
                isinstance(self, AutoTradeSettingWindow)
                and self.routine_table is not None
            )
        except RuntimeError:
            can_install_event_filter = False
        if can_install_event_filter:
            container.installEventFilter(self)
        if is_definition:
            container.setProperty(
                "autoTradeSettingRoutineTreeSummaryPinned",
                str(row_data.get("tree_icon", "") or "") == "\u25b6",
            )
            self._set_routine_tree_parent_summary_visible(
                container,
                bool(container.property("autoTradeSettingRoutineTreeSummaryPinned")),
            )
        return container

    def _set_routine_tree_parent_summary_visible(self, row_widget: QWidget, visible: bool) -> None:
        for widget in row_widget.findChildren(QWidget):
            if widget.property("autoTradeSettingParentSummaryMetric"):
                widget.setVisible(visible)

    def _hide_routine_tree_unpinned_parent_summaries(self) -> None:
        for row in range(self.routine_table.rowCount()):
            row_widget = self.routine_table.cellWidget(row, 0)
            if (
                row_widget is None
                or row_widget.property("autoTradeSettingRoutineTreeRowKind")
                != "definition"
                or bool(
                    row_widget.property(
                        "autoTradeSettingRoutineTreeSummaryPinned"
                    )
                )
            ):
                continue
            self._set_routine_tree_parent_summary_visible(row_widget, False)

    def _setup_routine_tree_display_level_badges(self) -> None:
        container = QWidget(self.routine_box)
        container.setObjectName("autoTradeSettingRoutineTreeDisplayLevelBadges")
        container.setFocusPolicy(Qt.NoFocus)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        valid_button = QPushButton("유효", container)
        valid_button.setObjectName("autoTradeSettingRoutineTreeValidBadge")
        valid_button.setFocusPolicy(Qt.NoFocus)
        valid_button.setCursor(Qt.PointingHandCursor)
        valid_button.setCheckable(True)
        valid_button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        valid_button.clicked.connect(self._set_routine_tree_valid_only)
        layout.addWidget(valid_button, 0, Qt.AlignVCenter)

        separators: list[QLabel] = []

        def add_separator(object_name: str) -> None:
            separator = QLabel("|", container)
            separator.setObjectName(object_name)
            separator.setAlignment(Qt.AlignCenter)
            separator.setFixedSize(12, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            separator.setFocusPolicy(Qt.NoFocus)
            separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            separator.setStyleSheet("background: transparent; color: #9CA3AF;")
            layout.addWidget(separator, 0, Qt.AlignVCenter)
            separators.append(separator)

        add_separator("autoTradeSettingRoutineTreeValidSeparator")

        buttons: dict[str, QPushButton] = {}
        for level, text, object_name in (
            ("category", "그룹", "autoTradeSettingRoutineTreeCategoryLevelBadge"),
            ("routine", "루틴", "autoTradeSettingRoutineTreeRoutineLevelBadge"),
            ("stock", "종목", "autoTradeSettingRoutineTreeStockLevelBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_level=level:
                self._set_routine_tree_display_level(target_level)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            buttons[level] = button

        add_separator("autoTradeSettingRoutineTreeLevelSeparator")
        scope_buttons: dict[str, QPushButton] = {}
        for scope, text, object_name in (
            ("all", "전체", "autoTradeSettingRoutineTreeAllScopeBadge"),
            ("current", "현재", "autoTradeSettingRoutineTreeCurrentScopeBadge"),
            ("historical", "과거", "autoTradeSettingRoutineTreeHistoricalScopeBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_scope=scope:
                self._set_routine_tree_display_scope(target_scope)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            scope_buttons[scope] = button

        add_separator("autoTradeSettingRoutineTreeScopeSeparator")
        criterion_buttons: dict[str, QPushButton] = {}
        for criterion, text, object_name in (
            ("period", "기간", "autoTradeSettingRoutineTreePeriodCriterionBadge"),
            ("profit", "수익", "autoTradeSettingRoutineTreeProfitCriterionBadge"),
            ("average", "평균", "autoTradeSettingRoutineTreeAverageCriterionBadge"),
            ("efficiency", "효율", "autoTradeSettingRoutineTreeEfficiencyCriterionBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_criterion=criterion:
                self._set_routine_tree_display_criterion(target_criterion)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            criterion_buttons[criterion] = button

        container.setFixedSize(layout.sizeHint())
        self._routine_tree_display_level_badges = container
        self._routine_tree_valid_button = valid_button
        self._routine_tree_display_separators = tuple(separators)
        self._routine_tree_display_level_buttons = buttons
        self._routine_tree_display_scope_buttons = scope_buttons
        self._routine_tree_display_criterion_buttons = criterion_buttons
        self._update_routine_tree_display_level_badges()
        container.show()
        container.raise_()
        self.routine_box.installEventFilter(self)
        QTimer.singleShot(0, self._position_routine_tree_display_level_badges)

    def _position_routine_tree_display_level_badges(self) -> None:
        container = getattr(self, "_routine_tree_display_level_badges", None)
        routine_box = getattr(self, "routine_box", None)
        if container is None or routine_box is None:
            return
        stock_box = getattr(self, "stock_box", None)
        stock_layout = stock_box.layout() if stock_box is not None else None
        if stock_layout is not None:
            stock_margins = stock_layout.contentsMargins()
            if stock_margins.top() != AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN:
                stock_layout.setContentsMargins(
                    stock_margins.left(),
                    AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN,
                    stock_margins.right(),
                    stock_margins.bottom(),
                )
            stock_layout.setSpacing(AUTO_TRADE_SETTING_TOP_CONTROL_BODY_SPACING)
            stock_layout.activate()
        routine_layout = routine_box.layout()
        right_margin = routine_layout.contentsMargins().right() if routine_layout is not None else 0
        control_row_top = routine_box.contentsRect().top()
        status_bar = getattr(self, "selected_routine_status_bar", None)
        if status_bar is not None and status_bar.parentWidget() is not None:
            status_bar_top = routine_box.mapFromGlobal(
                status_bar.mapToGlobal(status_bar.rect().topLeft())
            ).y()
            control_row_top = status_bar_top
            container.setFixedHeight(status_bar.height())
        if routine_layout is not None:
            margins = routine_layout.contentsMargins()
            required_top_margin = container.height()
            stock_table = getattr(self, "stock_table", None)
            if stock_table is not None and stock_table.parentWidget() is not None:
                stock_table_top = routine_box.mapFromGlobal(
                    stock_table.mapToGlobal(stock_table.rect().topLeft())
                ).y()
                required_top_margin = max(
                    required_top_margin,
                    stock_table_top - routine_box.contentsRect().top(),
                )
            if margins.top() < required_top_margin:
                routine_layout.setContentsMargins(
                    margins.left(),
                    required_top_margin,
                    margins.right(),
                    margins.bottom(),
                )
                routine_layout.activate()
        container.move(
            max(0, routine_box.width() - right_margin - container.width()),
            control_row_top,
        )
        container.raise_()

    def _update_routine_tree_display_level_badges(self) -> None:
        valid_only = bool(getattr(self, "_routine_tree_valid_only", False))
        valid_button = getattr(self, "_routine_tree_valid_button", None)
        if valid_button is not None:
            valid_button.setChecked(valid_only)
            valid_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if valid_only
                else AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
            )
            valid_border_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if valid_only
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            valid_button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=valid_color,
                    border_color=valid_border_color,
                )
            )

        selected_level = str(getattr(self, "_routine_tree_display_level", "category") or "category")
        for level, button in getattr(self, "_routine_tree_display_level_buttons", {}).items():
            color = AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
            border_color = AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            if level == selected_level:
                color = AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                border_color = AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=border_color,
                )
            )

        selected_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        scope_enabled = self._routine_tree_scope_filter_available()
        disabled_style = (
            "QPushButton:disabled, QPushButton:disabled:hover {"
            " background-color: transparent;"
            " border: 1px solid #D1D5DB;"
            " border-radius: 4px;"
            " color: #9CA3AF;"
            " font-weight: 600;"
            " padding: 0 6px;"
            "}"
        )
        for scope, button in getattr(self, "_routine_tree_display_scope_buttons", {}).items():
            button.setEnabled(scope_enabled)
            button.setCursor(Qt.PointingHandCursor if scope_enabled else Qt.ArrowCursor)
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if scope_enabled and scope == selected_scope
                else AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
            )
            border_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if scope_enabled and scope == selected_scope
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=border_color,
                )
                + disabled_style
            )

        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA.get(
            selected_level,
            frozenset(),
        )
        selected_criterion = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        for criterion, button in getattr(self, "_routine_tree_display_criterion_buttons", {}).items():
            enabled = criterion in supported_criteria
            button.setEnabled(enabled)
            button.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if enabled and criterion == selected_criterion
                else AUTO_TRADE_SETTING_BADGE_IDLE_TEXT_COLOR
            )
            border_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if enabled and criterion == selected_criterion
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=border_color,
                )
                + disabled_style
            )

    def _set_routine_tree_valid_only(self, enabled: bool) -> None:
        previous_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        was_valid_stock_only = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and previous_level == "stock"
        )
        was_representative_sort = previous_level in {"category", "routine"}
        self._routine_tree_valid_only = bool(enabled)
        self._update_routine_tree_display_level_badges()
        current_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        is_representative_sort = current_level in {"category", "routine"}
        is_valid_stock_only = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and current_level == "stock"
        )
        stock_sort_active = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and current_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        self._routine_tree_stock_performance_sort_active = bool(stock_sort_active)
        if (
            was_valid_stock_only
            or is_valid_stock_only
            or
            was_representative_sort
            or is_representative_sort
            or stock_sort_active
        ):
            self.load_routine_table()
        else:
            self._apply_routine_tree_collapse_visibility()
            self.routine_table.viewport().update()
        if current_level == "stock":
            self.on_routine_selection_changed()

    def _routine_tree_scope_filter_available(self) -> bool:
        return (
            str(getattr(self, "_routine_tree_display_level", "category") or "category")
            in {"category", "routine", "stock"}
        )

    def _set_routine_tree_display_scope(self, scope: str) -> None:
        clean_scope = str(scope or "").strip()
        if clean_scope not in {"all", "current", "historical"}:
            return
        if not self._routine_tree_scope_filter_available():
            return
        current_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        if clean_scope == current_scope and not (
            clean_scope == "current"
            and bool(
                getattr(
                    self,
                    "_routine_tree_stock_performance_sort_active",
                    False,
                )
            )
        ):
            return

        scroll_bar = self.routine_table.verticalScrollBar()
        scroll_value = scroll_bar.value()
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        if display_level == "stock":
            self._routine_tree_stock_performance_sort_active = bool(
                getattr(self, "_routine_tree_valid_only", False)
            )
        self._routine_tree_display_scope = clean_scope
        self._routine_tree_last_stock_scope = clean_scope
        self.load_routine_table()
        scroll_bar.setValue(scroll_value)
        self._update_routine_tree_display_level_badges()
        if display_level == "stock":
            self.on_routine_selection_changed()

    def _refresh_routine_tree_display_state(self) -> None:
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        display_metric = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            updated_metadata = dict(metadata)
            updated_metadata["display_level"] = display_level
            updated_metadata["display_scope"] = display_scope
            updated_metadata["display_metric"] = display_metric
            item.setData(Qt.UserRole, updated_metadata)
            row_widget = self.routine_table.cellWidget(row, 0)
            if row_widget is None:
                continue
            for metric in ("period", "profit", "average", "efficiency"):
                metric_name = metric.title()
                left_label = row_widget.findChild(
                    QLabel,
                    f"autoTradeSettingRoutineTreePerformance{metric_name}LeftValue",
                )
                if left_label is None:
                    continue
                right_label = row_widget.findChild(
                    QLabel,
                    f"autoTradeSettingRoutineTreePerformance{metric_name}RightValue",
                )
                default_values = {
                    "period": ("0", ""),
                    "profit": ("0", "0.00%"),
                    "average": ("0", "0.00%"),
                    "efficiency": ("0.0", ""),
                }
                left_fallback, right_fallback = default_values[metric]
                left_value, right_value = self._routine_tree_metric_values(
                    updated_metadata,
                    metric,
                    left_fallback,
                    right_fallback,
                )
                left_label.setText(left_value)
                if right_label is not None:
                    right_label.setText(right_value)
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()
        self.routine_table.viewport().update()

    def _set_routine_tree_display_criterion(self, criterion: str) -> None:
        clean_criterion = str(criterion or "").strip()
        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA.get(
            str(getattr(self, "_routine_tree_display_level", "category") or "category"),
            frozenset(),
        )
        if clean_criterion not in supported_criteria:
            return

        self._routine_tree_display_criterion = clean_criterion
        display_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        should_sort_stocks = (
            display_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        should_sort_representatives = (
            display_level in {"category", "routine"}
        )
        self._routine_tree_stock_performance_sort_active = should_sort_stocks
        self._update_routine_tree_display_level_badges()
        if should_sort_stocks or should_sort_representatives:
            scroll_bar = self.routine_table.verticalScrollBar()
            scroll_value = scroll_bar.value()
            self.load_routine_table()
            scroll_bar.setValue(scroll_value)
        else:
            self._refresh_routine_tree_display_state()

    def _apply_routine_tree_display_level_command(self, level: str) -> None:
        clean_level = str(level or "").strip()
        collapsed_definitions = set(
            getattr(self, "_collapsed_auto_trade_definition_ids", set())
        )
        collapsed_instances = set(
            getattr(self, "_collapsed_auto_trade_instance_ids", set())
        )
        definition_ids: set[str] = set()
        instance_ids: set[str] = set()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict) or not bool(
                metadata.get("has_toggle_children", True)
            ):
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            if row_kind == "definition":
                parent_id = routine_tree_parent_identity(metadata)
                if parent_id:
                    definition_ids.add(parent_id)
            elif row_kind == "instance":
                instance_id = str(metadata.get("instance_id", "") or "").strip()
                if instance_id:
                    instance_ids.add(instance_id)

        if clean_level == "category":
            collapsed_definitions.update(definition_ids)
        elif clean_level == "routine":
            collapsed_definitions.difference_update(definition_ids)
            collapsed_instances.update(instance_ids)
        elif clean_level == "stock":
            collapsed_definitions.difference_update(definition_ids)
            collapsed_instances.difference_update(instance_ids)

        self._collapsed_auto_trade_definition_ids = collapsed_definitions
        self._collapsed_auto_trade_instance_ids = collapsed_instances

    def _set_routine_tree_display_level(self, level: str) -> None:
        clean_level = str(level or "").strip()
        if clean_level not in {"category", "routine", "stock"}:
            return

        previous_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        was_sort_level = previous_level in {"category", "routine"}
        self._routine_tree_display_level = clean_level
        if clean_level == "stock":
            restored_scope = str(
                getattr(self, "_routine_tree_last_stock_scope", "all") or "all"
            )
            self._routine_tree_display_scope = (
                restored_scope
                if restored_scope in {"all", "current", "historical"}
                else "all"
            )
        else:
            current_scope = str(
                getattr(self, "_routine_tree_display_scope", "") or ""
            )
            if current_scope not in {"all", "current", "historical"}:
                current_scope = str(
                    getattr(self, "_routine_tree_last_stock_scope", "all") or "all"
                )
            if current_scope not in {"all", "current", "historical"}:
                current_scope = "all"
            self._routine_tree_display_scope = current_scope
            self._routine_tree_last_stock_scope = current_scope
        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA[clean_level]
        if str(getattr(self, "_routine_tree_display_criterion", "profit") or "profit") not in supported_criteria:
            self._routine_tree_display_criterion = "profit"
        representative_sort = clean_level in {"category", "routine"}
        stock_sort_active = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and
            clean_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        self._routine_tree_stock_performance_sort_active = bool(stock_sort_active)
        self._apply_routine_tree_display_level_command(clean_level)
        self._update_routine_tree_display_level_badges()
        if was_sort_level or representative_sort or stock_sort_active:
            self.load_routine_table()
        else:
            self._refresh_routine_tree_display_state()
        if clean_level == "stock":
            self.on_routine_selection_changed()

    def eventFilter(self, obj, event) -> bool:
        selected_stock_button = getattr(
            self,
            "selected_routine_status_buttons",
            {},
        ).get("all")
        if (
            obj is selected_stock_button
            and event.type() == QEvent.MouseButtonRelease
            and bool(
                getattr(
                    self,
                    "_selected_stock_double_click_release_pending",
                    False,
                )
            )
        ):
            self._selected_stock_double_click_release_pending = False
            return True
        if (
            obj is selected_stock_button
            and event.type() == QEvent.MouseButtonDblClick
        ):
            if hasattr(event, "button") and event.button() != Qt.LeftButton:
                return False
            self._selected_stock_double_click_release_pending = True
            self._toggle_selected_stock_normal_projection()
            return True
        if obj is getattr(self, "routine_box", None) and event.type() == QEvent.Resize:
            self._position_routine_tree_display_level_badges()
        if (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeHoverIdentityRowKind")
            and event.type() in {QEvent.Enter, QEvent.MouseMove}
        ):
            row_widget = obj.parentWidget()
            if obj.property("autoTradeSettingRoutineTreeHoverIdentityRowKind") == "definition":
                self._set_routine_tree_parent_summary_visible(
                    row_widget,
                    True,
                )
            else:
                self._hide_routine_tree_unpinned_parent_summaries()
        if (
            isinstance(obj, QLabel)
            and obj.objectName() == "autoTradeSettingRoutineTreeIcon"
            and (
                obj.property("autoTradeSettingRoutineTreeToggleDefinitionId")
                or obj.property("autoTradeSettingRoutineTreeToggleInstanceId")
            )
        ):
            if (
                obj.property("autoTradeSettingRoutineTreeToggleDefinitionId")
                and event.type() in {QEvent.Enter, QEvent.MouseMove}
            ):
                identity_widget = obj.parentWidget()
                definition_row = (
                    identity_widget.parentWidget()
                    if identity_widget is not None
                    else None
                )
                if definition_row is not None:
                    self._set_routine_tree_parent_summary_visible(
                        definition_row,
                        True,
                    )
            if event.type() == QEvent.MouseButtonPress:
                return True
            if event.type() == QEvent.MouseButtonRelease:
                if hasattr(event, "button") and event.button() != Qt.LeftButton:
                    return True
                if not bool(obj.property("autoTradeSettingRoutineTreeToggleEnabled")):
                    return True
                definition_id = str(obj.property("autoTradeSettingRoutineTreeToggleDefinitionId") or "").strip()
                instance_id = str(obj.property("autoTradeSettingRoutineTreeToggleInstanceId") or "").strip()
                if definition_id:
                    self._toggle_routine_definition_collapsed(definition_id)
                elif instance_id:
                    self._toggle_routine_instance_collapsed(instance_id)
                return True
        if (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeRowKind") == "definition"
        ):
            pinned = bool(obj.property("autoTradeSettingRoutineTreeSummaryPinned"))
            if event.type() in {QEvent.Enter, QEvent.MouseMove}:
                self._set_routine_tree_parent_summary_visible(obj, True)
            elif event.type() == QEvent.Leave:
                self._set_routine_tree_parent_summary_visible(obj, pinned)
        elif (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeRowKind")
            in {"instance", "stock"}
            and event.type() in {QEvent.Enter, QEvent.MouseMove}
        ):
            self._hide_routine_tree_unpinned_parent_summaries()
        return super().eventFilter(obj, event)

    def _toggle_routine_definition_collapsed(self, definition_id: str) -> None:
        clean_definition_id = str(definition_id or "").strip()
        if not clean_definition_id:
            return
        if not self._routine_tree_toggle_enabled("definition", clean_definition_id):
            return
        collapsed = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        if clean_definition_id in collapsed:
            collapsed.remove(clean_definition_id)
        else:
            collapsed.add(clean_definition_id)
        self._collapsed_auto_trade_definition_ids = collapsed
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()

    def _toggle_routine_instance_collapsed(self, instance_id: str) -> None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return
        if not self._routine_tree_toggle_enabled("instance", clean_instance_id):
            return
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        if clean_instance_id in collapsed_instances:
            collapsed_instances.remove(clean_instance_id)
        else:
            collapsed_instances.add(clean_instance_id)
        self._collapsed_auto_trade_instance_ids = collapsed_instances
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()

    def _routine_tree_toggle_enabled(self, row_kind: str, target_id: str) -> bool:
        clean_row_kind = str(row_kind or "").strip()
        clean_target_id = str(target_id or "").strip()
        if not clean_row_kind or not clean_target_id:
            return False
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("row_kind", "") or "") != clean_row_kind:
                continue
            metadata_target_id = (
                routine_tree_parent_identity(metadata)
                if clean_row_kind == "definition"
                else str(metadata.get("instance_id", "") or "")
            )
            if metadata_target_id != clean_target_id:
                continue
            return bool(metadata.get("has_toggle_children", True))
        return False

    def _apply_routine_tree_collapse_visibility(self) -> None:
        collapsed_definitions = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        valid_only = bool(getattr(self, "_routine_tree_valid_only", False))
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        historical_scope = (
            display_level == "stock" and display_scope == "historical"
        )
        valid_stock_only = valid_only and display_level == "stock"
        current_definition_collapsed = False
        current_definition_filtered = False
        current_instance_id = ""
        current_instance_collapsed = False
        current_instance_filtered = False
        child_rows_visible = False
        definition_summary_rows: list[tuple[QWidget, bool, bool]] = []
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            definition_id = routine_tree_parent_identity(metadata)
            instance_id = str(metadata.get("instance_id", "") or "")
            widget = self.routine_table.cellWidget(row, 0)
            icon = widget.findChild(QLabel, "autoTradeSettingRoutineTreeIcon") if widget is not None else None
            if row_kind == "definition":
                has_toggle_children = bool(metadata.get("has_toggle_children", True))
                if display_level == "category":
                    definition_valid = bool(
                        metadata.get("has_valid_stock_relation", False)
                    )
                else:
                    definition_valid = bool(metadata.get("has_stocked_instances", False))
                if (
                    display_scope in {"historical", "all"}
                    and bool(metadata.get("canonical_historical_only", False))
                ):
                    definition_valid = True
                current_definition_filtered = valid_only and not definition_valid
                if not has_toggle_children:
                    collapsed_definitions.discard(definition_id)
                current_definition_collapsed = has_toggle_children and definition_id in collapsed_definitions
                current_instance_id = ""
                current_instance_collapsed = False
                current_instance_filtered = False
                self.routine_table.setRowHidden(
                    row,
                    valid_stock_only or current_definition_filtered,
                )
                if icon is not None:
                    icon.setText("\u25b6" if current_definition_collapsed or not has_toggle_children else "\u25bc")
                if widget is not None:
                    definition_summary_rows.append(
                        (
                            widget,
                            has_toggle_children,
                            current_definition_collapsed,
                        )
                    )
                continue
            hidden_by_definition = current_definition_collapsed
            if row_kind == "instance":
                has_toggle_children = bool(metadata.get("has_toggle_children", True))
                current_instance_filtered = (
                    valid_only
                    and display_level in {"routine", "stock"}
                    and not bool(metadata.get("has_displayable_stocks", False))
                    and not (
                        display_scope in {"historical", "all"}
                        and bool(metadata.get("canonical_historical_only", False))
                    )
                )
                if not has_toggle_children:
                    collapsed_instances.discard(instance_id)
                current_instance_id = instance_id
                current_instance_collapsed = has_toggle_children and instance_id in collapsed_instances
                row_hidden = (
                    valid_stock_only
                    or
                    hidden_by_definition
                    or current_definition_filtered
                    or current_instance_filtered
                )
                self.routine_table.setRowHidden(row, row_hidden)
                child_rows_visible = child_rows_visible or not row_hidden
                if icon is not None:
                    icon.setText("\u25b6" if current_instance_collapsed or not has_toggle_children else "\u25bc")
                continue
            if valid_stock_only and row_kind == "stock":
                self.routine_table.setRowHidden(row, False)
                child_rows_visible = True
                continue
            hidden_by_instance = bool(
                current_instance_id
                and instance_id == current_instance_id
                and current_instance_collapsed
            )
            row_hidden = (
                hidden_by_definition
                or hidden_by_instance
                or current_definition_filtered
                or (current_instance_filtered and row_kind != "stock")
            )
            self.routine_table.setRowHidden(row, row_hidden)
            if row_kind == "stock":
                child_rows_visible = child_rows_visible or not row_hidden

        for widget, has_toggle_children, definition_collapsed in definition_summary_rows:
            summary_visible = (
                definition_collapsed
                if has_toggle_children
                else not child_rows_visible
            )
            widget.setProperty(
                "autoTradeSettingRoutineTreeSummaryPinned",
                summary_visible,
            )
            self._set_routine_tree_parent_summary_visible(
                widget,
                summary_visible,
            )

    def load_routine_table(self) -> None:
        current_metadata = self.current_selected_routine_row_metadata()
        snapshot = auto_trade_initial_read_snapshot(self)
        definitions = list(
            snapshot.get("definitions", ())
            if snapshot is not None
            else load_routine_definitions()
        )
        loaded_instances = list(
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        group_records = list(
            snapshot.get("groups", ())
            if snapshot is not None
            else get_group_records()
        )
        base_stocks = list(
            snapshot.get("stocks", ())
            if snapshot is not None
            else read_base_stocks()
        )
        selected_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or "all"
        )
        if selected_scope not in {"all", "current", "historical"}:
            selected_scope = "all"
        canonical_performance = self._canonical_performance_snapshot_for_tree(
            stocks=base_stocks,
            instances=loaded_instances,
            groups=group_records,
        )
        canonical_group_rows = {
            row.group_id: row
            for row in (
                canonical_performance.group_rows(selected_scope)
                if canonical_performance is not None
                else ()
            )
        }
        canonical_instance_rows = {
            row.instance_id: row
            for row in (
                canonical_performance.instance_rows(selected_scope)
                if canonical_performance is not None
                else ()
            )
        }
        canonical_stock_rows = {
            row.stock_code: row
            for row in (
                canonical_performance.stock_rows(selected_scope)
                if canonical_performance is not None
                else ()
            )
        }
        definition_by_id = {
            str(definition.definition_id): definition
            for definition in definitions
        }
        projection_override = getattr(
            self,
            "_routine_tree_projected_instance_ids_override",
            None,
        )
        parent_specs: list[dict[str, object]] = []
        def definition_parent_specs(
            projected_instance_ids: set[str],
        ) -> tuple[list[object], list[dict[str, object]]]:
            projected_instances = [
                instance
                for instance in loaded_instances
                if str(instance.instance_id) in projected_instance_ids
            ]
            instances_by_definition: dict[str, list[object]] = {}
            for instance in projected_instances:
                instances_by_definition.setdefault(
                    str(instance.definition_id),
                    [],
                ).append(instance)
            specs = [
                {
                    "group_id": "",
                    "parent_id": str(definition.definition_id),
                    "display_name": str(definition.display_name),
                    "registration_definition": definition,
                    "child_instances": instances_by_definition.get(
                        str(definition.definition_id),
                        [],
                    ),
                }
                for definition in definitions
            ]
            return projected_instances, specs

        if callable(projection_override):
            override_instance_ids = {
                str(instance_id or "").strip()
                for instance_id in projection_override(loaded_instances)
                if str(instance_id or "").strip()
            }
            instances, parent_specs = definition_parent_specs(override_instance_ids)
        else:
            group_projection = build_main_group_projection(
                group_records,
                loaded_instances,
                base_stocks,
            )
            canonical_instance_ids = {
                projected_instance.instance_id
                for projected_group in group_projection
                for projected_instance in projected_group.instances
            }
            try:
                effective_instance_ids = auto_trade_projected_instance_ids(
                    loaded_instances,
                    groups=group_records,
                    stocks=base_stocks,
                )
            except TypeError:
                effective_instance_ids = auto_trade_projected_instance_ids(
                    loaded_instances
                )
            if effective_instance_ids != canonical_instance_ids:
                instances, parent_specs = definition_parent_specs(
                    effective_instance_ids
                )
                group_projection = ()
            else:
                instances = [
                projected_instance.instance
                for projected_group in group_projection
                for projected_instance in projected_group.instances
                ]
            group_record_by_id = {
                str(getattr(group, "group_id", "") or "").strip(): group
                for group in group_records
                if str(getattr(group, "group_id", "") or "").strip()
            }
            for projected_group in group_projection:
                child_instances = [
                    projected_instance.instance
                    for projected_instance in projected_group.instances
                ]
                group_record = group_record_by_id.get(projected_group.group_id)
                group_definition_id = str(
                    getattr(group_record, "definition_id", "") or ""
                ).strip()
                registration_definition = definition_by_id.get(group_definition_id)
                parent_specs.append(
                    {
                        "group_id": projected_group.group_id,
                        "parent_id": projected_group.group_id,
                        "display_name": projected_group.display_name,
                        "registration_definition": registration_definition,
                        "child_instances": child_instances,
                    }
                )
            if selected_scope in {"historical", "all"}:
                known_parent_ids = {
                    str(spec.get("parent_id", "") or "").strip()
                    for spec in parent_specs
                }
                instance_by_id = {
                    str(instance.instance_id): instance
                    for instance in loaded_instances
                }
                for group_row in canonical_group_rows.values():
                    if group_row.group_id in known_parent_ids:
                        continue
                    episode_instances: dict[str, object] = {}
                    for episode_id in group_row.episode_ids:
                        episode = canonical_performance.aggregator.snapshot.episodes_by_id.get(
                            episode_id
                        )
                        if episode is None or not episode.instance_id:
                            continue
                        instance = instance_by_id.get(episode.instance_id)
                        if instance is None:
                            instance = SimpleNamespace(
                                instance_id=episode.instance_id,
                                definition_id=episode.definition_id or "",
                                display_name=(
                                    episode.instance_name_snapshot
                                    or episode.instance_id
                                ),
                                group_id=group_row.group_id,
                                rules_path="",
                            )
                        episode_instances[episode.instance_id] = instance
                    latest_episode = max(
                        (
                            canonical_performance.aggregator.snapshot.episodes_by_id[episode_id]
                            for episode_id in group_row.episode_ids
                            if episode_id
                            in canonical_performance.aggregator.snapshot.episodes_by_id
                        ),
                        key=lambda episode: episode.started_at,
                        default=None,
                    )
                    definition_id = str(
                        getattr(latest_episode, "definition_id", "") or ""
                    )
                    parent_specs.append(
                        {
                            "group_id": group_row.group_id,
                            "parent_id": group_row.group_id,
                            "display_name": canonical_performance.group_name(
                                group_row,
                                prefer_episode_snapshot=(
                                    selected_scope == "historical"
                                ),
                            ),
                            "registration_definition": definition_by_id.get(
                                definition_id
                            ),
                            "child_instances": list(episode_instances.values()),
                            "canonical_historical_only": True,
                        }
                    )
            if canonical_performance is not None:
                current_instance_by_id = {
                    str(instance.instance_id): instance
                    for instance in loaded_instances
                }
                scoped_specs: list[dict[str, object]] = []
                for spec in parent_specs:
                    spec_group_id = str(spec.get("group_id", "") or "").strip()
                    if not spec_group_id:
                        scoped_specs.append(spec)
                        continue
                    group_row = canonical_group_rows.get(spec_group_id)
                    if selected_scope in {"current", "historical"} and group_row is None:
                        continue
                    if group_row is not None:
                        scoped_instance_ids: set[str] = set()
                        scoped_episode_instances: dict[str, object] = {}
                        for episode_id in group_row.episode_ids:
                            episode = canonical_performance.aggregator.snapshot.episodes_by_id.get(
                                episode_id
                            )
                            if episode is None or not episode.instance_id:
                                continue
                            scoped_instance_ids.add(episode.instance_id)
                            instance = current_instance_by_id.get(episode.instance_id)
                            if instance is None:
                                instance = SimpleNamespace(
                                    instance_id=episode.instance_id,
                                    definition_id=episode.definition_id or "",
                                    display_name=(
                                        episode.instance_name_snapshot
                                        or episode.instance_id
                                    ),
                                    group_id=spec_group_id,
                                    rules_path="",
                                )
                            scoped_episode_instances[episode.instance_id] = instance
                        children = list(spec.get("child_instances", []) or [])
                        known_children = {
                            str(child.instance_id) for child in children
                        }
                        children.extend(
                            instance
                            for instance_id, instance in scoped_episode_instances.items()
                            if instance_id not in known_children
                        )
                        if selected_scope in {"current", "historical"}:
                            children = [
                                child
                                for child in children
                                if str(child.instance_id) in scoped_instance_ids
                            ]
                        spec = {**spec, "child_instances": children}
                    scoped_specs.append(spec)
                parent_specs = scoped_specs
        instance_counts = self._routine_instance_operation_counts()
        canonical_stocks_by_instance: dict[str, list[dict[str, object]]] = {}
        if canonical_performance is not None:
            for instance_id, instance_row in canonical_instance_rows.items():
                codes = set(canonical_performance.instance_stock_codes(instance_row))
                if selected_scope in {"all", "current"}:
                    codes.update(
                        code
                        for code, relation in canonical_performance.projection.current_relations.stocks_by_code.items()
                        if relation.current_instance_id == instance_id
                    )
                for code in sorted(codes):
                    projected_stock = canonical_stock_rows.get(code)
                    detail = canonical_performance.current_stock_detail(code)
                    current_relation = canonical_performance.projection.current_relations.stocks_by_code.get(code)
                    is_current = bool(
                        current_relation
                        and current_relation.current_instance_id == instance_id
                    )
                    canonical_stocks_by_instance.setdefault(instance_id, []).append(
                        {
                            "instance_id": instance_id,
                            "stock_path": str(detail.get("stock_path", "") or ""),
                            "stock_code": code,
                            "stock_name": (
                                canonical_performance.stock_name(projected_stock)
                                if projected_stock is not None
                                else str(detail.get("stock_name", "") or code)
                            ),
                            "stock_relation_kind": (
                                CURRENT_STOCK_RELATION
                                if is_current
                                else HISTORICAL_STOCK_RELATION
                            ),
                            "is_historical": not is_current,
                            "canonical_projection_row": projected_stock,
                        }
                    )
        collapsed = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        rows: list[dict[str, object]] = []
        display_level_for_rows = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        valid_only_for_rows = bool(
            getattr(self, "_routine_tree_valid_only", False)
        )
        sort_visible_stocks_by_metric = (
            bool(
                getattr(
                    self,
                    "_routine_tree_stock_performance_sort_active",
                    False,
                )
            )
            or (
                display_level_for_rows == "routine"
            )
        )

        for parent_spec in parent_specs:
            definition = parent_spec.get("registration_definition")
            definition_id = str(
                getattr(definition, "definition_id", "") or ""
            ).strip()
            group_id = str(parent_spec.get("group_id", "") or "").strip()
            parent_id = str(parent_spec.get("parent_id", "") or definition_id).strip()
            parent_display_name = str(
                parent_spec.get("display_name", "") or ""
            ).strip()
            if (
                canonical_performance is not None
                and group_id in canonical_group_rows
                and selected_scope == "historical"
            ):
                parent_display_name = canonical_performance.group_name(
                    canonical_group_rows[group_id],
                    prefer_episode_snapshot=True,
                )
            child_instances = sorted(
                list(parent_spec.get("child_instances", []) or []),
                key=lambda instance: (
                    str(getattr(instance, "display_name", "") or "").casefold(),
                    str(getattr(instance, "instance_id", "") or ""),
                ),
            )
            if valid_only_for_rows and display_level_for_rows == "routine":
                child_instances = [
                    instance
                    for instance in child_instances
                    if canonical_stocks_by_instance.get(str(instance.instance_id), [])
                ]
            display_stocks_by_instance: dict[str, list[dict[str, object]]] = {}
            for instance in child_instances:
                instance_id = str(instance.instance_id)
                display_stocks = list(canonical_stocks_by_instance.get(instance_id, []))
                if sort_visible_stocks_by_metric:
                    display_criterion = str(
                        getattr(
                            self,
                            "_routine_tree_display_criterion",
                            "profit",
                        )
                        or "profit"
                    )
                    def _performance_sort_key(
                        stock: dict[str, object],
                    ) -> tuple[int, float, str]:
                        payload = self._routine_tree_canonical_performance_texts(
                            stock.get("canonical_projection_row"),
                            canonical_performance,
                        )
                        rank = int(
                            payload.get(
                                f"performance_{display_criterion}_sort_status_rank",
                                3,
                            )
                        )
                        value = float(
                            payload.get(
                                f"performance_{display_criterion}_sort_value",
                                0.0,
                            )
                            or 0.0
                        )
                        return (
                            rank,
                            -value,
                            str(stock.get("stock_name", "") or "").casefold(),
                        )

                    display_stocks = sorted(
                        display_stocks,
                        key=_performance_sort_key,
                    )
                display_stocks_by_instance[instance_id] = display_stocks
            definition_performance = self._routine_tree_canonical_performance_texts(
                canonical_group_rows.get(group_id),
                canonical_performance,
            )
            has_definition_children = bool(child_instances)
            if not has_definition_children:
                collapsed.discard(parent_id)
            is_collapsed = has_definition_children and parent_id in collapsed
            rows.append(
                {
                    "row_kind": "definition",
                    "definition_id": definition_id,
                    "group_id": group_id,
                    "tree_parent_id": parent_id,
                    "is_discovered_group": bool(group_id),
                    "instance_id": "",
                    "definition_name": parent_display_name,
                    "instance_name": "",
                    "package_dir": str(getattr(definition, "package_dir", "") or ""),
                    "instance_dir": "",
                    "display_name": parent_display_name,
                    "tree_icon": "\u25b6" if is_collapsed or not has_definition_children else "\u25bc",
                    "has_toggle_children": has_definition_children,
                    "has_instances": has_definition_children,
                    "target_instance_ids": tuple(
                        str(instance.instance_id)
                        for instance in child_instances
                        if str(instance.instance_id).strip()
                    ),
                    "has_stocked_instances": any(
                        display_stocks_by_instance.get(
                            str(instance.instance_id),
                            [],
                        )
                        for instance in child_instances
                    ),
                    "has_valid_stock_relation": any(
                        int(
                            instance_counts.get(
                                str(instance.instance_id),
                                {},
                            ).get("normal", 0)
                            or 0
                        )
                        > 0
                        for instance in child_instances
                    ),
                    "instance_count": len(child_instances),
                    "registered": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("registered", 0) or 0)
                        for instance in child_instances
                    ),
                    "running": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("running", 0) or 0)
                        for instance in child_instances
                    ),
                    "stopped": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("stopped", 0) or 0)
                        for instance in child_instances
                    ),
                    "error": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("error", 0) or 0)
                        for instance in child_instances
                    ),
                    "normal": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("normal", 0) or 0)
                        for instance in child_instances
                    ),
                    "operation_running": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("operation_running", 0) or 0)
                        for instance in child_instances
                    ),
                    "waiting": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("waiting", 0) or 0)
                        for instance in child_instances
                    ),
                    "excluded": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("excluded", 0) or 0)
                        for instance in child_instances
                    ),
                    "review": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("review", 0) or 0)
                        for instance in child_instances
                    ),
                    "display_level": str(
                        getattr(self, "_routine_tree_display_level", "category") or "category"
                    ),
                    "display_scope": selected_scope,
                    "display_metric": str(
                        getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                    ),
                    "canonical_historical_only": bool(
                        parent_spec.get("canonical_historical_only", False)
                        or (
                            selected_scope == "historical"
                            and group_id in canonical_group_rows
                        )
                    ),
                    "canonical_identity_tooltip": (
                        canonical_performance.identity_tooltip(
                            canonical_group_rows[group_id]
                        )
                        if canonical_performance is not None
                        and group_id in canonical_group_rows
                        else ""
                    ),
                    **definition_performance,
                }
            )
            for _index, instance in enumerate(child_instances):
                instance_definition_id = str(
                    getattr(instance, "definition_id", "") or ""
                ).strip()
                instance_definition = definition_by_id.get(instance_definition_id)
                rules_path = str(getattr(instance, "rules_path", "") or "")
                instance_dir = Path(rules_path).parent if rules_path else Path()
                instance_id = str(instance.instance_id)
                visible_stocks = display_stocks_by_instance.get(instance_id, [])
                instance_projection_row = canonical_instance_rows.get(instance_id)
                instance_performance = self._routine_tree_canonical_performance_texts(
                    instance_projection_row,
                    canonical_performance,
                )
                has_instance_children = bool(visible_stocks)
                if not has_instance_children:
                    collapsed_instances.discard(instance_id)
                count = instance_counts.get(
                    instance_id,
                    {
                        "registered": 0,
                        "running": 0,
                        "stopped": 0,
                        "error": 0,
                        "normal": 0,
                        "excluded": 0,
                        "review": 0,
                    },
                )
                is_instance_collapsed = has_instance_children and instance_id in collapsed_instances
                rows.append(
                    {
                        "row_kind": "instance",
                        "definition_id": instance_definition_id,
                        "group_id": group_id,
                        "instance_id": instance_id,
                        "definition_name": parent_display_name,
                        "instance_name": (
                            canonical_performance.instance_name(
                                instance_projection_row,
                                prefer_episode_snapshot=(
                                    selected_scope == "historical"
                                ),
                            )
                            if canonical_performance is not None
                            and instance_projection_row is not None
                            else str(instance.display_name)
                        ),
                        "package_dir": str(
                            getattr(instance_definition, "package_dir", "") or ""
                        ),
                        "instance_dir": str(instance_dir) if instance_dir else "",
                        "display_name": (
                            canonical_performance.instance_name(
                                instance_projection_row,
                                prefer_episode_snapshot=(
                                    selected_scope == "historical"
                                ),
                            )
                            if canonical_performance is not None
                            and instance_projection_row is not None
                            else str(instance.display_name)
                        ),
                        "tree_icon": "\u25b6" if is_instance_collapsed or not has_instance_children else "\u25bc",
                        "has_toggle_children": has_instance_children,
                        "has_displayable_stocks": bool(
                            visible_stocks
                        ),
                        "canonical_historical_only": bool(
                            instance_projection_row is not None
                            and (
                                selected_scope == "historical"
                                or instance_id
                                not in {
                                    str(value.instance_id)
                                    for value in loaded_instances
                                }
                            )
                        ),
                        "canonical_identity_tooltip": (
                            canonical_performance.identity_tooltip(
                                instance_projection_row
                            )
                            if canonical_performance is not None
                            and instance_projection_row is not None
                            else ""
                        ),
                        "instance_group_top_gap": _index > 0,
                        "instance_count": 0,
                        "registered": int(count.get("registered", 0) or 0),
                        "running": int(count.get("running", 0) or 0),
                        "stopped": int(count.get("stopped", 0) or 0),
                        "error": int(count.get("error", 0) or 0),
                        "normal": int(count.get("normal", 0) or 0),
                        "operation_running": int(count.get("operation_running", 0) or 0),
                        "waiting": int(count.get("waiting", 0) or 0),
                        "excluded": int(count.get("excluded", 0) or 0),
                        "review": int(count.get("review", 0) or 0),
                        "display_level": str(
                            getattr(self, "_routine_tree_display_level", "category") or "category"
                        ),
                        "display_scope": selected_scope,
                        "display_metric": str(
                            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                        ),
                        **instance_performance,
                    }
                )
                for stock_index, stock in enumerate(visible_stocks):
                    stock_name = str(stock.get("stock_name", "") or "").strip()
                    relation_kind = str(
                        stock.get("stock_relation_kind", "") or ""
                    ).strip().upper()
                    if relation_kind not in {
                        CURRENT_STOCK_RELATION,
                        HISTORICAL_STOCK_RELATION,
                    }:
                        relation_kind = (
                            HISTORICAL_STOCK_RELATION
                            if bool(stock.get("is_historical", False))
                            else CURRENT_STOCK_RELATION
                        )
                    is_historical = relation_kind == HISTORICAL_STOCK_RELATION
                    stock_projection_row = stock.get("canonical_projection_row")
                    stock_performance = self._routine_tree_canonical_performance_texts(
                        stock_projection_row,
                        canonical_performance,
                    )
                    rows.append(
                        {
                            "row_kind": "stock",
                            "definition_id": instance_definition_id,
                            "group_id": group_id,
                            "instance_id": instance_id,
                            "definition_name": parent_display_name,
                            "instance_name": str(instance.display_name),
                            "package_dir": str(
                                getattr(instance_definition, "package_dir", "") or ""
                            ),
                            "instance_dir": str(instance_dir) if instance_dir else "",
                            "display_name": stock_name,
                            "display_level": str(
                                getattr(self, "_routine_tree_display_level", "category") or "category"
                            ),
                            "display_scope": selected_scope,
                            "display_metric": str(
                                getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                            ),
                            "stock_code": str(stock.get("stock_code", "") or ""),
                            "stock_path": str(stock.get("stock_path", "") or ""),
                            "first_stock_for_instance": stock_index == 0,
                            "tree_icon": "\u25aa" if is_historical else "\u2713",
                            "stock_relation_kind": relation_kind,
                            "is_historical": is_historical,
                            "is_development_fixture": bool(
                                stock.get("is_development_fixture", False)
                            ),
                            "canonical_identity_tooltip": (
                                canonical_performance.identity_tooltip(
                                    stock_projection_row
                                )
                                if canonical_performance is not None
                                and stock_projection_row is not None
                                else ""
                            ),
                            "_source_stock": dict(stock),
                            **stock_performance,
                        }
                    )

        valid_stock_only = bool(
            getattr(self, "_routine_tree_valid_only", False)
        ) and str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        ) == "stock"
        if valid_stock_only:
            existing_stock_codes = {
                str(row.get("stock_code", "") or "").strip()
                for row in rows
                if str(row.get("row_kind", "") or "") == "stock"
            }
            for stock_projection_row in canonical_stock_rows.values():
                if stock_projection_row.stock_code in existing_stock_codes:
                    continue
                relation = stock_projection_row.current_relation_consistency
                instance_id = str(
                    stock_projection_row.current_instance_id or ""
                )
                instance = next(
                    (
                        value
                        for value in loaded_instances
                        if str(value.instance_id) == instance_id
                    ),
                    None,
                )
                detail = (
                    canonical_performance.current_stock_detail(
                        stock_projection_row.stock_code
                    )
                    if canonical_performance is not None
                    else {}
                )
                rows.append(
                    {
                        "row_kind": "stock",
                        "definition_id": str(
                            getattr(instance, "definition_id", "") or ""
                        ),
                        "group_id": str(
                            stock_projection_row.current_group_id or ""
                        ),
                        "instance_id": instance_id,
                        "definition_name": "",
                        "instance_name": str(
                            getattr(instance, "display_name", "") or ""
                        ),
                        "package_dir": "",
                        "instance_dir": "",
                        "display_name": canonical_performance.stock_name(
                            stock_projection_row
                        ),
                        "display_level": "stock",
                        "display_scope": selected_scope,
                        "display_metric": str(
                            getattr(
                                self,
                                "_routine_tree_display_criterion",
                                "profit",
                            )
                            or "profit"
                        ),
                        "stock_code": stock_projection_row.stock_code,
                        "stock_path": str(detail.get("stock_path", "") or ""),
                        "first_stock_for_instance": False,
                        "tree_icon": (
                            "\u2713"
                            if stock_projection_row.is_currently_assigned
                            else "\u25aa"
                        ),
                        "stock_relation_kind": (
                            CURRENT_STOCK_RELATION
                            if stock_projection_row.is_currently_assigned
                            else HISTORICAL_STOCK_RELATION
                        ),
                        "is_historical": not stock_projection_row.is_currently_assigned,
                        "canonical_identity_tooltip": canonical_performance.identity_tooltip(
                            stock_projection_row
                        ),
                        "canonical_relation_consistent": relation.consistent,
                        **self._routine_tree_canonical_performance_texts(
                            stock_projection_row,
                            canonical_performance,
                        ),
                    }
                )
            parent_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") != "stock"
            ]
            stock_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") == "stock"
            ]
            stock_by_code: dict[str, dict[str, object]] = {}
            historical_instance_ids_by_code: dict[str, set[str]] = {}
            for stock_row in stock_rows:
                code = str(stock_row.get("stock_code", "") or "").strip()
                if bool(stock_row.get("is_historical", False)):
                    historical_instance_id = str(
                        stock_row.get("instance_id", "") or ""
                    ).strip()
                    if historical_instance_id:
                        historical_instance_ids_by_code.setdefault(code, set()).add(
                            historical_instance_id
                        )
                existing = stock_by_code.get(code)
                if existing is None or (
                    bool(existing.get("is_historical", False))
                    and not bool(stock_row.get("is_historical", False))
                ):
                    stock_by_code[code] = stock_row
            for code, stock_row in stock_by_code.items():
                stock_row["historical_instance_ids"] = tuple(
                    sorted(historical_instance_ids_by_code.get(code, set()))
                )
            stock_rows = list(stock_by_code.values())
            rows = parent_rows + stock_rows

        if valid_stock_only and bool(
            getattr(self, "_routine_tree_stock_performance_sort_active", False)
        ):
            display_criterion = str(
                getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
            )
            def _global_stock_sort_key(row: dict[str, object]):
                rank = int(
                    row.get(
                        f"performance_{display_criterion}_sort_status_rank",
                        3,
                    )
                    or 0
                )
                value = self._routine_tree_row_sort_value(row, display_criterion)
                return (
                    rank,
                    -value,
                    str(row.get("display_name", "") or "").casefold(),
                    str(row.get("stock_code", "") or ""),
                )

            parent_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") != "stock"
            ]
            stock_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") == "stock"
            ]
            stock_rows = sorted(
                stock_rows,
                key=_global_stock_sort_key,
            )
            for index, row in enumerate(stock_rows):
                row["first_stock_for_instance"] = index == 0
            rows = parent_rows + stock_rows

        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_criterion = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        if display_level == "category":
            rows = self._routine_tree_sort_definition_blocks(
                rows,
                display_criterion,
            )
        elif display_level == "routine":
            rows = self._routine_tree_sort_instance_blocks(
                rows,
                display_criterion,
            )

        table_updates_enabled = self.routine_table.updatesEnabled()
        sorting_enabled = self.routine_table.isSortingEnabled()
        table_signal_blocker = QSignalBlocker(self.routine_table)
        if table_updates_enabled:
            self.routine_table.setUpdatesEnabled(False)
        if sorting_enabled:
            self.routine_table.setSortingEnabled(False)
        try:
            self.routine_table.setRowCount(len(rows))
            self._configure_routine_tree_row_layout(rows)

            for row, row_data in enumerate(rows):
                row_kind = str(row_data.get("row_kind", "") or "")
                instance_count_text = (
                    f"루틴({int(row_data.get('instance_count', 0) or 0)}) | "
                    if row_kind == "definition"
                    else ""
                )
                display_text = (
                    f"{row_data['display_name']}   "
                    f"{instance_count_text}"
                )
                item = SortableTableWidgetItem("")
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setData(Qt.UserRole, dict(row_data))
                title_tooltip = (
                    str(row_data.get("display_name", "") or "")
                    if row_kind == "stock"
                    else tree_title_tooltip(row_data.get("display_name", ""))
                    if row_kind in {"definition", "instance"}
                    else ""
                )
                item.setData(Qt.ToolTipRole, title_tooltip)
                item.setData(SORT_ROLE, str(row_data["definition_name"]))
                self.routine_table.setItem(row, 0, item)
                row_widget = self._routine_tree_row_widget(
                    dict(row_data),
                    display_text,
                )
                row_height = (
                    32
                    if row_kind == "definition"
                    else AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT
                    if row_kind == "stock"
                    else 30
                )
                if row_kind == "instance" and bool(
                    row_data.get("instance_group_top_gap")
                ):
                    row_height += AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
                elif row_kind == "stock" and bool(
                    row_data.get("first_stock_for_instance")
                ):
                    row_height += AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
                row_widget.setMinimumHeight(row_height)
                item.setSizeHint(QSize(0, row_height))
                self.routine_table.setCellWidget(row, 0, row_widget)
                self.routine_table.setRowHeight(row, row_height)

            self.routine_table.clearSelection()
            if current_metadata:
                self.restore_routine_selection_metadata(current_metadata)
            self._refresh_routine_tree_display_state()
        finally:
            if sorting_enabled:
                self.routine_table.setSortingEnabled(True)
            del table_signal_blocker
            if table_updates_enabled:
                self.routine_table.setUpdatesEnabled(True)
                self.routine_table.viewport().update()

    def current_selected_routine_row_metadata(self) -> dict[str, object] | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = self.routine_table.item(selected_rows[0].row(), 0)
        if item is None:
            return None
        metadata = item.data(Qt.UserRole)
        return dict(metadata) if isinstance(metadata, dict) else None

    def current_selected_definition_id(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        return str(metadata.get("definition_id", "") or "").strip() if metadata else ""

    def current_selected_instance_id(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return ""
        return str(metadata.get("instance_id", "") or "").strip()

    def current_selected_instance_dir(self) -> Path | None:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return None
        if bool(metadata.get("is_historical", False)):
            return None
        path_text = str(metadata.get("instance_dir", "") or "").strip()
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def current_selected_target_instance_ids(self) -> tuple[str, ...]:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata:
            return ()
        row_kind = str(metadata.get("row_kind", "") or "")
        if row_kind in {"instance", "stock"}:
            instance_id = str(metadata.get("instance_id", "") or "").strip()
            return (instance_id,) if instance_id else ()
        if row_kind == "definition":
            projected_instance_ids = metadata.get("target_instance_ids")
            if isinstance(projected_instance_ids, (list, tuple, set)):
                return tuple(
                    instance_id
                    for instance_id in (
                        str(value or "").strip()
                        for value in projected_instance_ids
                    )
                    if instance_id
                )
            group_id = str(metadata.get("group_id", "") or "").strip()
            snapshot = auto_trade_initial_read_snapshot(self)
            instances = (
                snapshot.get("instances", ())
                if snapshot is not None
                else load_persisted_routine_instances()
            )
            if group_id:
                return tuple(
                    str(instance.instance_id)
                    for instance in instances
                    if str(getattr(instance, "group_id", "") or "").strip()
                    == group_id
                )
            definition_id = str(metadata.get("definition_id", "") or "").strip()
            return tuple(
                str(instance.instance_id)
                for instance in instances
                if str(instance.definition_id) == definition_id
            )
        return ()

    def current_selected_routine_name(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata:
            return ""
        if str(metadata.get("row_kind", "")) in {"instance", "stock"}:
            if bool(metadata.get("is_historical", False)):
                return ""
            return str(metadata.get("instance_name", "") or "").strip()
        return str(metadata.get("definition_name", "") or "").strip()

    def current_selected_routine_dir(self) -> Path | None:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return None
        if bool(metadata.get("is_historical", False)):
            return None
        path_text = str(metadata.get("package_dir", "") or "").strip()
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def order_execution_boundary(self) -> AutoTradeOrderExecutionBoundary:
        """Return this window's widget-free order execution boundary."""
        boundary = self.__dict__.get("_order_execution_boundary")
        if isinstance(boundary, AutoTradeOrderExecutionBoundary):
            return boundary

        def api_object():
            parent = persistent_feature_owner(self)
            return getattr(parent, "kiwoom_api", None)

        def kiwoom_connected() -> bool:
            api = api_object()
            checker = getattr(api, "is_connected", None)
            return bool(checker()) if callable(checker) else False

        def account_numbers() -> list[str]:
            parent = persistent_feature_owner(self)
            getter = getattr(parent, "kiwoom_account_numbers", None)
            if callable(getter):
                values = getter()
            else:
                api = api_object()
                getter = getattr(api, "account_numbers", None)
                values = getter() if callable(getter) else []
            return list(values) if isinstance(values, list) else []

        def selected_account_no() -> str:
            parent = persistent_feature_owner(self)
            getter = getattr(parent, "selected_account_no", None)
            return str(getter() or "").strip() if callable(getter) else ""

        def send_order_callable():
            return getattr(api_object(), "send_order", None)

        def fresh_current_price(stock_code: str):
            owner = persistent_feature_owner(self)
            host_getter = getattr(owner, "main_monitoring_auto_trade_operation_host", None)
            host = host_getter() if callable(host_getter) else None
            state_getter = getattr(host, "fresh_monitoring_market_information_state", None)
            state = state_getter(stock_code) if callable(state_getter) else None
            return getattr(state, "last_price", None)

        context = AutoTradeOrderExecutionContext(
            kiwoom_connected=kiwoom_connected,
            account_numbers=account_numbers,
            selected_account_no=selected_account_no,
            send_order_callable=send_order_callable,
            selected_stock_info=lambda: self.selected_stock_info(),
            selected_routine_metadata=lambda: self.current_selected_routine_row_metadata(),
            selected_target_instance_ids=lambda: self.current_selected_target_instance_ids(),
            selected_routine_dir=lambda: self.current_selected_routine_dir(),
            routine_dirs=lambda: [],
            stock_dirs_in_routine=lambda _routine_dir: [],
            base_stocks=lambda: read_base_stocks(),
            order_queue_path=lambda: ORDER_QUEUE_PATH,
            order_executions_path=lambda: ORDER_EXECUTIONS_PATH,
            order_locks_path=lambda: ORDER_LOCKS_PATH,
            confirm_runtime_file_init=lambda executions_path, locks_path: (
                self.confirm_execution_runtime_file_init(
                    order_executions_path=executions_path,
                    order_locks_path=locks_path,
                )
            ),
            all_group_stock_dirs=lambda: list(
                load_group_scope().all_group_stock_dirs()
            ),
            current_orderable_cash=lambda: (
                persistent_feature_owner(self).current_orderable_cash_for_budget()
                if callable(
                    getattr(
                        persistent_feature_owner(self),
                        "current_orderable_cash_for_budget",
                        None,
                    )
                )
                else None
            ),
            fresh_current_price=fresh_current_price,
        )
        boundary = AutoTradeOrderExecutionBoundary(context)
        self._order_execution_boundary = boundary
        return boundary

    def restore_routine_selection(self, routine_name: str) -> None:
        clean_name = str(routine_name or "").strip()
        if not clean_name:
            return
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if isinstance(metadata, dict):
                names = {
                    str(metadata.get("definition_name", "") or "").strip(),
                    str(metadata.get("instance_name", "") or "").strip(),
                }
                if clean_name in names:
                    self.routine_table.selectRow(row)
                    return

    def restore_routine_selection_metadata(self, metadata: dict[str, object]) -> None:
        row_kind = str(metadata.get("row_kind", "") or "").strip()
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        stock_path = str(metadata.get("stock_path", "") or "").strip()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            candidate = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("row_kind", "") or "") != row_kind:
                continue
            if str(candidate.get("definition_id", "") or "") != definition_id:
                continue
            if row_kind in {"instance", "stock"} and str(candidate.get("instance_id", "") or "") != instance_id:
                continue
            if row_kind == "stock" and str(candidate.get("stock_path", "") or "") != stock_path:
                continue
            self.routine_table.selectRow(row)
            return

    def on_routine_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        metadata = item.data(Qt.UserRole)
        if not isinstance(metadata, dict):
            return
        row_kind = str(metadata.get("row_kind", "") or "")
        return

    def on_routine_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        return

    def _routine_tree_instance_stock_register_metadata(
        self,
        instance_id: object,
    ) -> dict[str, object] | None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return None
        instance = routine_instance_by_id(clean_instance_id)
        if instance is None:
            return None
        definition_id = str(getattr(instance, "definition_id", "") or "").strip()
        definition = routine_definition_by_id(definition_id) if definition_id else None
        if definition is None:
            return None
        rules_path = getattr(instance, "rules_path", None)
        instance_dir = str(Path(rules_path).parent) if rules_path else ""
        return {
            "row_kind": "instance",
            "definition_id": str(getattr(definition, "definition_id", "") or definition_id),
            "definition_name": str(getattr(definition, "display_name", "") or ""),
            "instance_id": clean_instance_id,
            "instance_name": str(getattr(instance, "display_name", "") or ""),
            "instance_dir": instance_dir,
        }

    def on_routine_table_context_menu(self, pos) -> None:
        item = self.routine_table.itemAt(pos)
        if item is None:
            return
        metadata = item.data(Qt.UserRole)
        if not isinstance(metadata, dict):
            return

        row_kind = str(metadata.get("row_kind", "") or "").strip()
        if row_kind == "definition":
            menu = QMenu(self.routine_table)
            register_action = menu.addAction("루틴등록")
            register_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_routine_registration(target)
            )
            group_id = str(metadata.get("group_id", "") or "").strip()
            if group_id:
                menu.addSeparator()
                delete_group_action = menu.addAction("그룹삭제")
                delete_group_action.setEnabled(True)
                delete_group_action.triggered.connect(
                    lambda _checked=False, target=dict(metadata): self.delete_routine_group(target)
                )
                packing_action = menu.addAction("그룹패킹")
                packing_action.setEnabled(True)
                packing_action.triggered.connect(
                    lambda _checked=False, target=dict(metadata): self.pack_routine_group(target)
                )
        elif row_kind == "instance":
            menu = QMenu(self.routine_table)
            settings_action = menu.addAction("설정변경")
            clone_action = menu.addAction("루틴복제")
            delete_action = menu.addAction("루틴삭제")
            rename_action = menu.addAction("이름변경")
            stock_register_action = menu.addAction("종목등록")
            settings_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_routine_instance_settings(target)
            )
            clone_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.clone_routine_instance(target)
            )
            delete_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.delete_routine_instance(target)
            )
            rename_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.rename_routine_instance(target)
            )
            stock_register_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_instance_stock_search_register_window(target)
            )
        elif row_kind == "stock":
            menu = QMenu(self.routine_table)
            menu.setToolTipsVisible(True)
            stock_register_action = menu.addAction("종목등록")
            unregister_action = menu.addAction("등록해제")
            menu.addSeparator()
            convert_action = menu.addAction("등록전환")
            hide_action = menu.addAction("표시삭제")
            context = self._routine_tree_stock_row_action_context(metadata)
            actions = context["actions"]
            register_decision = actions["register"]
            unassign_decision = actions["unassign"]
            convert_decision = actions["convert"]
            hide_decision = actions["hide"]
            stock_register_action.setEnabled(bool(register_decision["enabled"]))
            unregister_action.setEnabled(bool(unassign_decision["enabled"]))
            convert_action.setEnabled(bool(convert_decision["enabled"]))
            hide_action.setEnabled(bool(hide_decision["enabled"]))
            for action, decision in (
                (stock_register_action, register_decision),
                (unregister_action, unassign_decision),
                (convert_action, convert_decision),
                (hide_action, hide_decision),
            ):
                tooltip = str(decision.get("tooltip", "") or "")
                if tooltip:
                    action.setToolTip(tooltip)
            stock_register_action.triggered.connect(
                lambda _checked=False, target=dict(register_decision.get("target") or {}): self.open_instance_stock_search_register_window(target)
            )
            convert_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.convert_historical_stock_to_registered(target)
            )
            unregister_action.triggered.connect(
                lambda _checked=False, target=dict(unassign_decision.get("target") or {}): self.unregister_routine_tree_stock(target)
            )
            hide_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.hide_historical_stock_display(target)
            )
        else:
            return
        menu.exec_(self.routine_table.viewport().mapToGlobal(pos))

    def _routine_tree_stock_is_review_required(self, code: str, name: str) -> bool:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
            return inspect_stock_review_state(
                stock_dir,
                loaded_state=state,
            ).review_required
        except Exception:
            return True

    def _routine_tree_registered_stock_for_code(self, code: str) -> dict[str, object] | None:
        clean_code = normalize_stock_code(code)
        if not clean_code:
            return None
        for stock in read_base_stocks():
            if normalize_stock_code(str(stock.get("code", "") or "")) == clean_code:
                return dict(stock)
        return None

    def _routine_tree_stock_relation_kind(
        self,
        metadata: dict[str, object],
    ) -> str:
        relation_kind = str(
            metadata.get("stock_relation_kind", "") or ""
        ).strip().upper()
        if relation_kind in {CURRENT_STOCK_RELATION, HISTORICAL_STOCK_RELATION}:
            return relation_kind
        return (
            HISTORICAL_STOCK_RELATION
            if bool(metadata.get("is_historical", False))
            else CURRENT_STOCK_RELATION
        )

    def _routine_tree_action_tooltip(
        self,
        title: str,
        reasons: tuple[str, ...] | list[str],
    ) -> str:
        lines = [title]
        lines.extend(f"- {reason}" for reason in reasons if str(reason or "").strip())
        return "\n".join(lines)

    def _routine_tree_stock_row_action_context(
        self,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        relation_kind = self._routine_tree_stock_relation_kind(metadata)
        code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
        name = str(metadata.get("display_name", "") or "").strip()
        row_instance_id = str(metadata.get("instance_id", "") or "").strip()
        registered_stock = self._routine_tree_registered_stock_for_code(code) if code else None
        current_instance_id = str(
            (registered_stock or {}).get("assigned_routine_instance_id", "") or ""
        ).strip()
        historical_instance_ids = tuple(
            sorted(
                {
                    str(value or "").strip()
                    for value in metadata.get("historical_instance_ids", ())
                    if str(value or "").strip()
                }
            )
        )
        target_instance_ids = (
            historical_instance_ids or ((row_instance_id,) if row_instance_id else ())
            if relation_kind == HISTORICAL_STOCK_RELATION
            else ((row_instance_id,) if row_instance_id else ())
        )
        row_instance = routine_instance_by_id(row_instance_id) if row_instance_id else None
        register_target = self._routine_tree_instance_stock_register_metadata(row_instance_id)
        register_reasons: list[str] = []
        if len(target_instance_ids) > 1:
            register_target = None
            register_reasons.append("대상 Instance가 여러 개입니다. 루틴 보기에서 선택하세요.")
        elif row_instance is None or register_target is None:
            register_reasons.append("현재 존재하지 않는 과거 Instance입니다.")

        unassign_decision = routine_unassign_decision(
            code,
            name,
            row_instance_id=row_instance_id,
            row_relation_kind=relation_kind,
        )
        convert_reasons: list[str] = []
        convert_enabled = False
        if relation_kind == CURRENT_STOCK_RELATION:
            convert_reasons.append("현재 등록종목은 등록전환 대상이 아닙니다.")
        elif len(target_instance_ids) > 1:
            convert_reasons.append("대상 Instance가 여러 개입니다. 루틴 보기에서 선택하세요.")
        elif row_instance is None:
            convert_reasons.append("현재 존재하지 않는 과거 Instance입니다.")
        elif current_instance_id:
            current_instance = routine_instance_by_id(current_instance_id)
            current_name = str(
                getattr(current_instance, "display_name", "") or current_instance_id
            ).strip()
            convert_reasons.append(f"현재 '{current_name}' Instance에 등록되어 있습니다.")
        elif self._routine_tree_stock_is_review_required(code, name):
            convert_reasons.append("검토관리 대상입니다.")
        else:
            convert_enabled = bool(code and name and row_instance_id)

        canonical_historical = bool(
            relation_kind == HISTORICAL_STOCK_RELATION
            and str(metadata.get("performance_source", "") or "").upper() == "CANONICAL"
            and not bool(metadata.get("is_development_fixture", False))
        )
        hide_enabled = bool(
            relation_kind == HISTORICAL_STOCK_RELATION
            and code
            and row_instance_id
            and not canonical_historical
        )
        hide_reasons: list[str] = []
        if relation_kind == CURRENT_STOCK_RELATION:
            hide_reasons.append("표시삭제는 과거 종목 전용입니다.")
        elif canonical_historical:
            hide_reasons.append("Canonical 과거실적 표시삭제 계약이 아직 연결되지 않았습니다.")
        elif not hide_enabled:
            hide_reasons.append("표시삭제 대상을 확인할 수 없습니다.")

        if relation_kind == HISTORICAL_STOCK_RELATION:
            unassign_enabled = False
            unassign_tooltip = "과거 종목은 현재 등록해제 대상이 아닙니다."
        else:
            unassign_enabled = bool(
                unassign_decision.allowed
                and row_instance is not None
                and row_instance_id
                and current_instance_id == row_instance_id
            )
            unassign_tooltip = (
                ""
                if unassign_enabled
                else self._routine_tree_action_tooltip(
                    "등록해제 불가",
                    unassign_decision.user_reasons
                    or ("현재 등록 Instance를 확인할 수 없습니다.",),
                )
            )
        return {
            "row_relation_kind": relation_kind,
            "stock_code": code,
            "stock_name": name,
            "row_instance_id": row_instance_id,
            "current_instance_id": current_instance_id,
            "current_assignment_exists": bool(current_instance_id),
            "row_instance_exists": row_instance is not None,
            "review_required": self._routine_tree_stock_is_review_required(code, name) if code else False,
            "unassign_decision": unassign_decision,
            "actions": {
                "register": {
                    "enabled": register_target is not None,
                    "tooltip": self._routine_tree_action_tooltip("종목등록 불가", register_reasons) if register_reasons else "",
                    "target": register_target,
                },
                "unassign": {
                    "enabled": unassign_enabled,
                    "tooltip": unassign_tooltip,
                    "target": {
                        "row_kind": "stock",
                        "stock_relation_kind": relation_kind,
                        "stock_code": code,
                        "stock_name": name,
                        "display_name": name,
                        "instance_id": row_instance_id,
                        "instance_name": str(metadata.get("instance_name", "") or "").strip(),
                        "definition_id": str(metadata.get("definition_id", "") or "").strip(),
                        "definition_name": str(metadata.get("definition_name", "") or "").strip(),
                    },
                },
                "convert": {
                    "enabled": convert_enabled,
                    "tooltip": self._routine_tree_action_tooltip("등록전환 불가", convert_reasons) if convert_reasons else "",
                },
                "hide": {
                    "enabled": hide_enabled,
                    "tooltip": self._routine_tree_action_tooltip("표시삭제 불가", hide_reasons) if hide_reasons else "",
                },
            },
        }

    def _routine_unassign_event_details(
        self,
        decision: RoutineUnassignDecision,
    ) -> dict[str, object]:
        evidence = decision.evidence
        return {
            "reason_codes": list(decision.reason_codes),
            "user_reasons": list(decision.user_reasons),
            "raw_status": evidence.get("raw_status", ""),
            "canonical_status_class": evidence.get("canonical_status_class", ""),
            "holding_qty": evidence.get("holding_qty", 0),
            "buy_pending_qty": evidence.get("buy_pending_qty", 0),
            "sell_pending_qty": evidence.get("sell_pending_qty", 0),
            "row_instance_id": evidence.get("row_instance_id", ""),
            "current_instance_id": evidence.get("current_instance_id", ""),
            "runtime_resolution": evidence.get("runtime_resolution", ""),
            "diagnostic_class": decision.diagnostic_class,
        }

    def _record_routine_unassign_blocked(
        self,
        *,
        code: str,
        name: str,
        decision: RoutineUnassignDecision,
    ) -> None:
        details = self._routine_unassign_event_details(decision)
        append_production_event(
            "ROUTINE_UNASSIGN_BLOCKED",
            severity=(
                "WARNING"
                if decision.diagnostic_class == "INTEGRITY_BLOCK"
                else "NOTICE"
            ),
            result="BLOCKED",
            source="AUTO_TRADE_ROUTINE_TREE",
            template_args={"stock_name": name or code},
            target_type="STOCK",
            target_id=code,
            target_name=name,
            stock_code=code,
            stock_name=name,
            routine=str(
                decision.evidence.get("persisted_routine_fields", ("",))[0]
                if decision.evidence.get("persisted_routine_fields")
                else ""
            ),
            reason_code=decision.primary_reason_code,
            reason_args={"reasons": list(decision.user_reasons)},
            details=details,
        )
        if decision.diagnostic_class != "INTEGRITY_BLOCK":
            return
        signature = json.dumps(
            {
                "stock_code": code,
                "reason_codes": list(decision.reason_codes),
                "raw_status": details["raw_status"],
                "row_instance_id": details["row_instance_id"],
                "current_instance_id": details["current_instance_id"],
                "runtime_resolution": details["runtime_resolution"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        observe_owner_failure_transition(
            self,
            f"routine-unassign-integrity:{code}",
            active=True,
            signature=signature,
            event_type="INTEGRITY_WARNING",
            severity="WARNING",
            result="BLOCKED",
            source="AUTO_TRADE_ROUTINE_TREE",
            template_args={"target": name or code},
            target_type="STOCK",
            target_id=code,
            target_name=name,
            stock_code=code,
            stock_name=name,
            reason_code=decision.primary_reason_code,
            details=details,
        )

    def _escalate_routine_unassign_review(
        self,
        *,
        code: str,
        name: str,
        decision: RoutineUnassignDecision,
    ) -> None:
        if not decision.review_required:
            return
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
        except Exception:
            return
        if not Path(stock_dir).is_dir():
            return
        item = {
            "review_reasons": list(decision.user_reasons),
            "review_location": "루틴 등록해제 무결성 판정",
        }
        try:
            self.mark_review_required(
                Path(stock_dir),
                code,
                name,
                item,
                source="루틴 등록해제 무결성 판정",
            )
        except Exception:
            LOGGER.warning(
                "routine unassign review escalation failed: code=%s name=%s",
                code,
                name,
                exc_info=True,
            )

    def unregister_routine_tree_stock(
        self,
        target: dict[str, object],
    ) -> bool:
        code = normalize_stock_code(str(target.get("stock_code", "") or ""))
        name = str(
            target.get("stock_name", "") or target.get("display_name", "") or ""
        ).strip()
        instance_id = str(target.get("instance_id", "") or "").strip()
        if not code or not name or not instance_id:
            show_toast(self, "등록해제 불가\n- 현재 등록 Instance를 확인할 수 없습니다.")
            return False
        relation_kind = self._routine_tree_stock_relation_kind(target)
        decision = routine_unassign_decision(
            code,
            name,
            row_instance_id=instance_id,
            row_relation_kind=relation_kind,
        )
        if not decision.allowed:
            if decision.event_required:
                self._record_routine_unassign_blocked(
                    code=code,
                    name=name,
                    decision=decision,
                )
            self._escalate_routine_unassign_review(
                code=code,
                name=name,
                decision=decision,
            )
            show_toast(
                self,
                self._routine_tree_action_tooltip(
                    "등록해제 불가",
                    decision.user_reasons,
                ),
            )
            return False

        assignment_result = execute_assignment_unassign(
            persistent_feature_owner(self),
            PROJECT_ROOT,
            code,
            name,
            expected_instance_id=instance_id,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )
        if not assignment_result.ok or not assignment_result.changed:
            show_toast(self, "등록해제할 수 없는 종목입니다.\n검토관리에서 확인하세요.")
            return False

        ensure_single_real_trade_routine_for_stock(code, name)
        sync_auto_trade_monitoring_universe(self)
        refresh_auto_trade_views(self)
        observe_owner_failure_transition(
            self,
            f"routine-unassign-integrity:{code}",
            active=False,
        )
        show_toast(self, f"등록해제 1건 | {name}")
        return True

    def convert_historical_stock_to_registered(
        self,
        metadata: dict[str, object],
    ) -> bool:
        context = self._routine_tree_stock_row_action_context(metadata)
        convert_decision = context["actions"]["convert"]
        if not bool(convert_decision["enabled"]):
            tooltip = str(convert_decision.get("tooltip", "") or "")
            if tooltip:
                show_toast(self, tooltip)
            return False
        return auto_trade_register_historical_stock_to_original_instance(
            self,
            metadata,
        )

    def _open_routine_settings_dialog(
        self,
        metadata: dict[str, object],
        *,
        registration: bool,
    ) -> None:
        open_routine_settings_dialog_for_owner(
            self,
            metadata,
            registration=registration,
        )

    def open_routine_registration(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "definition":
            return
        self._open_routine_settings_dialog(metadata, registration=True)

    def delete_routine_group(self, metadata: dict[str, object]) -> bool:
        owner = persistent_feature_owner(self)
        handler = getattr(owner, "delete_routine_group_completely", None)
        if not callable(handler):
            return False
        return bool(
            handler(
                str(metadata.get("group_id", "") or "").strip(),
                str(metadata.get("display_name", "") or metadata.get("definition_name", "")).strip(),
            )
        )

    def pack_routine_group(self, metadata: dict[str, object]) -> bool:
        owner = persistent_feature_owner(self)
        handler = getattr(owner, "pack_routine_group", None)
        if not callable(handler):
            return False
        return bool(handler(str(metadata.get("group_id", "") or "").strip()))

    def open_routine_instance_settings(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        self._open_routine_settings_dialog(metadata, registration=False)

    def clone_routine_instance(self, metadata: dict[str, object]) -> bool:
        return clone_routine_instance_with_existing_policy(self, metadata)

    def rename_routine_instance(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        current_name = str(metadata.get("instance_name", "") or "").strip()
        if not instance_id:
            return
        self.finish_routine_instance_name_edit(save=True)

        row = -1
        item = None
        for candidate_row in range(self.routine_table.rowCount()):
            candidate_item = self.routine_table.item(candidate_row, 0)
            candidate_metadata = (
                candidate_item.data(Qt.UserRole)
                if candidate_item is not None
                else None
            )
            if not isinstance(candidate_metadata, dict):
                continue
            if str(candidate_metadata.get("row_kind", "") or "") != "instance":
                continue
            if str(candidate_metadata.get("instance_id", "") or "").strip() != instance_id:
                continue
            row = candidate_row
            item = candidate_item
            break
        if row < 0 or item is None:
            return

        index = self.routine_table.model().index(row, 0)
        row_widget = self.routine_table.cellWidget(row, 0)
        title_label = (
            row_widget.findChild(QLabel, "autoTradeSettingRoutineTreeTitle")
            if row_widget is not None
            else None
        )
        if title_label is not None:
            top_left = title_label.mapTo(self.routine_table.viewport(), title_label.rect().topLeft())
            label_rect = QRect(top_left, title_label.size())
        else:
            cell_rect = self.routine_table.visualRect(index)
            label_rect = cell_rect.adjusted(52, 2, -4, -2)

        editor_rect = QRect(
            label_rect.left(),
            label_rect.top(),
            max(96, label_rect.width()),
            max(20, label_rect.height()),
        )
        editor = _AutoTradeRoutineInstanceNameEdit(self)
        editor.setObjectName("routineInstanceNameEditor")
        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setText(current_name)
        editor.setGeometry(editor_rect)
        editor.selectAll()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_instance_name_editor = editor
        self._routine_instance_name_editor_instance_id = instance_id
        self._routine_instance_name_editor_original = current_name

    def finish_routine_instance_name_edit(self, *, save: bool) -> None:
        editor = self._routine_instance_name_editor
        if editor is None or self._routine_instance_name_edit_finishing:
            return
        self._routine_instance_name_edit_finishing = True
        instance_id = self._routine_instance_name_editor_instance_id
        current_name = self._routine_instance_name_editor_original
        new_name = editor.text().strip()

        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        editor.hide()
        editor.deleteLater()
        self._routine_instance_name_edit_finishing = False

        if not save:
            return
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name == current_name:
            return

        result = RoutineInstanceRepository(PROJECT_ROOT).rename_instance(instance_id, clean_name)
        if not result.success:
            QMessageBox.warning(
                self,
                "이름변경",
                result.error or "루틴 이름을 변경하지 못했습니다.",
            )
            return
        refresh_auto_trade_views(self)

    def delete_routine_instance(self, metadata: dict[str, object]) -> None:
        delete_routine_instance_with_existing_policy(self, metadata)

    def open_instance_stock_search_register_window(
        self,
        metadata: dict[str, object],
    ) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        self.instance_stock_search_register_window = open_instance_stock_search_register_dialog(
            self,
            dict(metadata),
        )

    def hide_historical_stock_display(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "stock":
            return
        context = self._routine_tree_stock_row_action_context(metadata)
        hide_decision = context["actions"]["hide"]
        if not bool(hide_decision["enabled"]):
            tooltip = str(hide_decision.get("tooltip", "") or "")
            if tooltip:
                show_toast(self, tooltip)
            return
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        stock_code = str(metadata.get("stock_code", "") or "").strip()
        stock_name = str(metadata.get("display_name", "") or "").strip()
        if not instance_id or not stock_code:
            return
        answer = QMessageBox.question(
            self,
            "표시삭제",
            f"'{stock_name or stock_code}'를 실적 목록에서 제거하시겠습니까?\n\n"
            "이력 데이터는 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if bool(metadata.get("is_development_fixture", False)):
            hidden_fixture_keys = getattr(
                self,
                "_hidden_historical_stock_fixture_keys",
                set(),
            )
            hidden_fixture_keys.add((instance_id, stock_code))
            self._hidden_historical_stock_fixture_keys = hidden_fixture_keys
            self.load_routine_table()
            return
        if not StockRepository(PROJECT_ROOT).hide_routine_assignment_history(
            code=stock_code,
            instance_id=instance_id,
        ):
            QMessageBox.warning(
                self,
                "표시삭제",
                "과거 종목 표시 정보를 갱신하지 못했습니다.",
            )
            return
        refresh_auto_trade_views(self)

    def _left_display_intent_owns_all_stock_scope(self) -> bool:
        return self._left_flat_filter_scope_active()

    def on_routine_selection_changed(self) -> None:
        selected_metadata = self.current_selected_routine_row_metadata()
        self._all_stocks_scope_active = bool(
            self._left_display_intent_owns_all_stock_scope()
            or (
                selected_metadata is None
                and str(
                    getattr(self, "_routine_tree_display_level", "") or ""
                ).strip()
                == "stock"
                and str(
                    getattr(self, "_routine_tree_display_scope", "") or ""
                ).strip()
                == "all"
            )
        )
        self.update_selection_summary_panel()
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def load_selected_routine_stocks(self) -> None:
        auto_trade_load_selected_routine_stocks(self)

    def selected_stock_dir(self) -> Path | None:
        return selected_stock_dir(self)

    def selected_stock_info(self) -> tuple[Path, str, str] | None:
        return selected_stock_info(self)

    def selected_stock_infos(self) -> list[tuple[Path, str, str]]:
        return selected_stock_infos(self)

    def has_early_close_scope_targets(self) -> bool:
        try:
            return bool(_selected_instance_stock_dirs(self))
        except Exception:
            return False

    def fetch_minute_candles_for_selected_stock(self) -> None:
        selected = self.selected_stock_info()
        if selected is None:
            self.statusBarMessage("분봉조회할 종목 1개를 선택하세요.")
            return

        _stock_dir, code, name = selected
        parent = persistent_feature_owner(self)
        api = getattr(parent, "kiwoom_api", None)
        if api is None:
            self.statusBarMessage("키움 API가 초기화되지 않았습니다.")
            return

        if not api.is_available():
            reason = api.unavailable_reason() or "unknown reason"
            self.statusBarMessage(f"키움 API 사용불가: {reason}")
            return

        if not api.is_connected():
            self.statusBarMessage("키움 로그인 후 분봉조회가 가능합니다.")
            return

        def handle_result(result: dict[str, object]) -> None:
            if result.get("ok"):
                saved_count = result.get("saved_count", 0)
                message = f"{code} {name} candles.json 저장 완료: {saved_count}개"
                warning = result.get("warning") or ""
                if result.get("has_more") or warning:
                    message = f"{message} ({warning or 'additional pages available'})"
                self.statusBarMessage(message)
                return

            message = result.get("error") or result.get("message") or result.get("result") or "unknown error"
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {message}")

        try:
            result = api.request_minute_candles(
                code,
                name,
                interval=1,
                count=300,
                callback=handle_result,
            )
        except Exception as exc:
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {exc}")
            return

        if result.get("ok"):
            self.statusBarMessage(f"{code} {name} 분봉조회 요청됨")
        else:
            message = result.get("error") or result.get("message") or result.get("result") or "unknown error"
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {message}")

    def preview_order_candidates_for_pending_signals(self) -> None:
        try:
            from routine_signal_consumer import consume_pending_routine_signals_dry_run

            result = consume_pending_routine_signals_dry_run(limit=5)
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            signals_checked = int(summary.get("signals_checked", 0) or 0)
            blocked = int(summary.get("blocked", 0) or 0)
            allowed = int(summary.get("allowed", 0) or 0)
            errors = int(summary.get("errors", 0) or 0)
            self.statusBarMessage(
                f"주문후보검증: 확인 {signals_checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}"
            )
        except Exception as exc:
            self.statusBarMessage(f"주문후보검증 실패: {exc}")

    def read_order_from_queue_by_id(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).read_order_from_queue_by_id(*args, **kwargs)

    def execution_enable_confirmation_text(
        self,
        order: dict[str, object],
        enable_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        return "\n".join(
            [
                "execution_enabled 수동 활성화 확인",
                "",
                "이 작업은 order.execution_enabled 값을 True로 변경합니다.",
                "SendOrder 호출이 아닙니다.",
                "주문 전송이 아닙니다.",
                "REAL_READY 생성이 아닙니다.",
                "real_order_preflight는 자동 실행되지 않습니다.",
                "status는 EXECUTABLE로 유지됩니다.",
                "",
                f"order_id: {enable_preview_result.get('order_id', order.get('id', '-'))}",
                f"code: {enable_preview_result.get('code', order.get('code', '-'))}",
                f"side: {enable_preview_result.get('side', order.get('side', '-'))}",
                f"quantity: {enable_preview_result.get('quantity', order.get('quantity', '-'))}",
                f"order_type: {enable_preview_result.get('order_type', order.get('order_type', '-'))}",
                f"source_signal_id: {enable_preview_result.get('source_signal_id', order.get('source_signal_id', '-'))}",
                f"approval_status: {order.get('approval_status', '-')}",
                f"policy_status: {order.get('policy_status', '-')}",
                "",
                f"queue_path: {queue_path}",
                f"before_sha256: {queue_snapshot.get('sha256', '-')}",
                f"file_size: {queue_snapshot.get('size', '-')}",
                f"mtime: {queue_snapshot.get('mtime', '-')}",
                f"orders_count: {queue_snapshot.get('orders_count', '-')}",
                "",
                "계속하려면 수동 실주문 후보 활성화를 선택하세요.",
            ]
        )

    def confirm_execution_enable_commit(
        self,
        order: dict[str, object],
        enable_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("execution_enabled 수동 활성화 확인")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.execution_enable_confirmation_text(
                order,
                enable_preview_result,
                queue_path,
                queue_snapshot,
            )
        )
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("수동 실주문 후보 활성화")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(enable_preview_result.get("order_id") or order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_enable_commit",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="Execution Enable",
            order_id=order_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_ENABLE_COMMIT",
                "prompt_title": "execution_enabled 수동 활성화 확인",
                "prompt_summary": "실주문 후보 execution enable 승인",
                "offered_options": ["수동 실주문 후보 활성화", "취소"],
                "selected_option": "수동 실주문 후보 활성화" if accepted else "취소",
                "approval_stage": "EXECUTION_ENABLE",
            },
        )
        return accepted

    def show_execution_enable_result(self, result: dict[str, object]) -> None:
        lines = [
            "Execution Enable Result",
            "",
            f"enabled: {result.get('enabled', result.get('enable_preview', False))}",
            f"enable_stage: {result.get('enable_stage', '-')}",
            f"next_stage: {result.get('next_stage', '-')}",
            f"changed: {result.get('changed', False)}",
            f"order_id: {result.get('order_id', '-')}",
            f"before_status: {result.get('before_status', '-')}",
            f"after_status: {result.get('after_status', '-')}",
            f"before_execution_enabled: {result.get('before_execution_enabled', '-')}",
            f"after_execution_enabled: {result.get('after_execution_enabled', '-')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"backup_path: {result.get('backup_path', '-')}",
            "SendOrder called: False",
            "real_order_preflight auto-called: False",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Enable Result")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def enable_execution_candidate_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Execution Enable"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "수동 실주문 후보 활성화",
            "EXECUTABLE order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("수동 실주문 후보 활성화: order_id를 입력하세요.")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "enabled": False,
                "enable_stage": "read_order",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        enable_preview = preview_execution_enable(
            order_dict,
            {"operator_confirmed_for_execution_enable": True},
        )
        self._last_execution_enable_preview_result = enable_preview
        self._last_execution_enable_queue_snapshot = snapshot

        if enable_preview.get("enable_preview") is not True:
            result = {
                "enabled": False,
                "enable_stage": enable_preview.get("enable_stage"),
                "next_stage": enable_preview.get("next_stage"),
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": enable_preview.get("blocked_reasons", []),
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        if not self.confirm_execution_enable_commit(order_dict, enable_preview, queue_path, snapshot):
            self.statusBarMessage("수동 실주문 후보 활성화 취소")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "enabled": False,
                "enable_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "blocked_reasons": ["queue file changed after execution enable preview; rerun preview"],
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        result = commit_execution_enable(
            enable_preview,
            queue_path,
            preview_queue_snapshot=snapshot,
            context={"manual_execution_enable_commit_confirmed": True},
        )
        self.show_execution_enable_result(result)
        status_text = "완료" if result.get("enabled") else "차단"
        self.statusBarMessage(f"수동 실주문 후보 활성화 {status_text}")

    def real_preflight_confirmation_text(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        preflight_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        return "\n".join(
            [
                "REAL_READY 수동 점검 확인",
                "",
                "이 작업은 대상 order를 REAL_READY로 전환합니다.",
                "",
                "SendOrder 호출이 아닙니다.",
                "",
                "주문 전송이 아닙니다.",
                "",
                "Execution Preview는 자동 실행되지 않습니다.",
                "",
                "Queue 저장이 아닙니다.",
                "",
                "자동 실행 루프에 연결되지 않습니다.",
                "",
                "status",
                "EXECUTABLE",
                "↓",
                "REAL_READY",
                "",
                "execution_enabled",
                "True 유지",
                "",
                f"order_id: {preflight_preview_result.get('order_id', order.get('id', '-'))}",
                f"code: {preflight_preview_result.get('code', order.get('code', '-'))}",
                f"side: {preflight_preview_result.get('side', order.get('side', '-'))}",
                f"quantity: {preflight_preview_result.get('quantity', order.get('quantity', '-'))}",
                f"order_type: {preflight_preview_result.get('order_type', order.get('order_type', '-'))}",
                f"source_signal_id: {preflight_preview_result.get('source_signal_id', order.get('source_signal_id', '-'))}",
                f"approval_status: {order.get('approval_status', '-')}",
                f"policy_status: {order.get('policy_status', '-')}",
                "",
                f"guard.real_trade_enabled: {guard.get('real_trade_enabled', '-')}",
                f"guard.kiwoom_logged_in: {guard.get('kiwoom_logged_in', '-')}",
                f"guard.account_selected: {guard.get('account_selected', '-')}",
                f"guard.account_no: {guard.get('account_no', '-')}",
                f"guard.operator_confirmed: {guard.get('operator_confirmed', '-')}",
                "",
                f"queue_path: {queue_path}",
                f"before_sha256: {queue_snapshot.get('sha256', '-')}",
                f"file_size: {queue_snapshot.get('size', '-')}",
                f"mtime: {queue_snapshot.get('mtime', '-')}",
                f"orders_count: {queue_snapshot.get('orders_count', '-')}",
            ]
        )

    def confirm_real_preflight_commit(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        preflight_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("REAL_READY 수동 점검 확인")
        dialog.resize(760, 560)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.real_preflight_confirmation_text(
                order,
                guard,
                preflight_preview_result,
                queue_path,
                queue_snapshot,
            )
        )
        body.setMinimumHeight(420)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("REAL_READY 수동 점검 실행")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(preflight_preview_result.get("order_id") or order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_real_preflight_commit",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="REAL_READY preflight",
            order_id=order_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "REAL_READY_PREFLIGHT_COMMIT",
                "prompt_title": "REAL_READY 수동 점검 확인",
                "prompt_summary": "REAL_READY 수동 preflight 승인",
                "offered_options": ["REAL_READY 수동 점검 실행", "취소"],
                "selected_option": "REAL_READY 수동 점검 실행" if accepted else "취소",
                "approval_stage": "REAL_READY_PREFLIGHT",
            },
        )
        return accepted

    def show_real_preflight_result(self, result: dict[str, object]) -> None:
        lines = [
            "REAL_READY Manual Preflight Result",
            "",
            f"real_preflight_committed: {result.get('real_preflight_committed', result.get('real_preflight_preview', False))}",
            f"preflight_stage: {result.get('preflight_stage', '-')}",
            f"next_stage: {result.get('next_stage', '-')}",
            f"changed: {result.get('changed', False)}",
            f"order_id: {result.get('order_id', '-')}",
            f"before_status: {result.get('before_status', '-')}",
            f"after_status: {result.get('after_status', '-')}",
            f"execution_enabled: {result.get('execution_enabled', '-')}",
            f"real_preflight_status: {result.get('real_preflight_status', '-')}",
            f"real_preflight_reason: {result.get('real_preflight_reason', '-')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"backup_path: {result.get('backup_path', '-')}",
            f"send_order_called: {result.get('send_order_called', False)}",
            "Execution Preview auto-called: False",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        dialog = QDialog(self)
        dialog.setWindowTitle("REAL_READY Manual Preflight Result")
        dialog.resize(760, 560)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(420)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def real_preflight_stock_config_for_order(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).real_preflight_stock_config_for_order(*args, **kwargs)

    def build_real_preflight_guard_from_gui(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_real_preflight_guard_from_gui(*args, **kwargs)

    def real_preflight_guard_block_reasons(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).real_preflight_guard_block_reasons(*args, **kwargs)

    def real_preflight_confirmation_preview(self, order: dict[str, object]) -> dict[str, object]:
        try:
            quantity = int(order.get("quantity", 0) or 0)
        except Exception:
            quantity = order.get("quantity", "-")
        return {
            "real_preflight_preview": False,
            "preflight_stage": "operator_confirmation_pending",
            "next_stage": "REAL_PREFLIGHT_COMMIT_REQUIRED",
            "order_id": str(order.get("id", "") or "").strip(),
            "source_signal_id": str(order.get("source_signal_id", "") or "").strip(),
            "code": str(order.get("code", "") or "").strip(),
            "side": str(order.get("side", "") or "").strip().upper(),
            "quantity": quantity,
            "order_type": str(order.get("order_type", "") or "").strip(),
            "blocked_reasons": [],
            "send_order_called": False,
        }

    def run_real_ready_preflight_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "REAL_READY 수동 점검"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "REAL_READY 수동 점검",
            "EXECUTABLE order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("REAL_READY 수동 점검: order_id를 입력하세요.")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "read_order",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": read_result.get("blocked_reasons", []),
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=False)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=False)
        if guard_reasons:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "guard",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": guard_reasons,
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        confirmation_preview = self.real_preflight_confirmation_preview(order_dict)
        if not self.confirm_real_preflight_commit(order_dict, guard, confirmation_preview, queue_path, snapshot):
            self.statusBarMessage("REAL_READY manual preflight cancelled")
            return

        guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=True)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=True)
        if guard_reasons:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "guard",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": guard_reasons,
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY manual preflight blocked")
            return

        preflight_preview = preview_real_order_preflight(
            order_dict,
            guard,
            {"manual_real_preflight_confirmed": True},
        )
        self._last_real_preflight_preview_result = preflight_preview
        self._last_real_preflight_queue_snapshot = snapshot

        if preflight_preview.get("real_preflight_preview") is not True:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": preflight_preview.get("preflight_stage"),
                "next_stage": preflight_preview.get("next_stage"),
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": preflight_preview.get("blocked_reasons", []),
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "blocked_reasons": ["queue file changed after real preflight preview; rerun REAL Preflight"],
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        result = commit_real_order_preflight(
            preflight_preview,
            queue_path,
            guard_path=None,
            preview_queue_snapshot=snapshot,
            context={"manual_real_preflight_commit_confirmed": True},
        )
        self.show_real_preflight_result(result)
        status_text = "완료" if result.get("real_preflight_committed") else "차단"
        self.statusBarMessage(f"REAL_READY 수동 점검 {status_text}")

    def execution_runtime_commit_confirmation_text(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        *,
        order_executions_path: Path,
        order_locks_path: Path,
        queue_path: Path,
    ) -> str:
        return "\n".join(
            [
                "Execution Runtime Commit / Queue Commit confirmation",
                "",
                "This action will run Execution Preview, commit runtime records, then allow Queue commit.",
                "SendOrder is not called.",
                "Broker API is not called.",
                "OrderRequest is not created.",
                "DISPATCH_CLAIMED is not entered.",
                "",
                f"account_no: {guard.get('account_no', '-')}",
                f"order_id: {order.get('id', order.get('order_id', '-'))}",
                f"code: {order.get('code', '-')}",
                f"side: {order.get('side', order.get('order_side', '-'))}",
                f"quantity: {order.get('quantity', order.get('order_quantity', '-'))}",
                f"order_executions_path: {order_executions_path}",
                f"order_locks_path: {order_locks_path}",
                f"queue_path: {queue_path}",
                "",
                "Continue only if the selected account, runtime targets, and queue write intent are correct.",
            ]
        )

    def confirm_execution_runtime_commit(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        *,
        order_executions_path: Path,
        order_locks_path: Path,
        queue_path: Path,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Runtime Commit Confirmation")
        dialog.resize(760, 460)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.execution_runtime_commit_confirmation_text(
                order,
                guard,
                order_executions_path=order_executions_path,
                order_locks_path=order_locks_path,
                queue_path=queue_path,
            )
        )
        body.setMinimumHeight(330)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Confirm runtime and queue preview")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        execution_id = str(guard.get("execution_id") or order.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_runtime_commit",
            target_type="EXECUTION",
            target_id=execution_id or order_id or None,
            target_name="Execution Runtime Commit",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_RUNTIME_COMMIT",
                "prompt_title": "Execution Runtime Commit Confirmation",
                "prompt_summary": "Execution Runtime 및 Queue preview commit 승인",
                "offered_options": ["Confirm runtime and queue preview", "Cancel"],
                "selected_option": "Confirm runtime and queue preview" if accepted else "Cancel",
                "approval_stage": "RUNTIME_COMMIT",
            },
        )
        return accepted

    def runtime_file_init_confirmation_text(
        self,
        *,
        order_executions_path: Path,
        order_locks_path: Path,
    ) -> str:
        return "\n".join(
            [
                "Execution Runtime File Initialization",
                "",
                "Both runtime execution files are missing.",
                "The existing runtime file-init service will create the initial files.",
                "No queue commit is performed by this step.",
                "SendOrder is not called.",
                "",
                f"order_executions_path: {order_executions_path}",
                f"order_locks_path: {order_locks_path}",
                "",
                "Continue only if these project runtime files should be initialized now.",
            ]
        )

    def confirm_execution_runtime_file_init(
        self,
        *,
        order_executions_path: Path,
        order_locks_path: Path,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Runtime File Initialization")
        dialog.resize(760, 380)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.runtime_file_init_confirmation_text(
                order_executions_path=order_executions_path,
                order_locks_path=order_locks_path,
            )
        )
        body.setMinimumHeight(260)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Initialize runtime files")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_runtime_file_init",
            target_type="EXECUTION_RUNTIME",
            target_name="Execution Runtime 파일 초기화",
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_RUNTIME_FILE_INIT",
                "prompt_title": "Execution Runtime File Initialization",
                "prompt_summary": "Execution Runtime 파일 초기화 승인",
                "offered_options": ["Initialize runtime files", "Cancel"],
                "selected_option": "Initialize runtime files" if accepted else "Cancel",
                "approval_stage": "RUNTIME_FILE_INITIALIZATION",
            },
        )
        return accepted

    def execution_runtime_environment_flags(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).execution_runtime_environment_flags(*args, **kwargs)

    def ensure_execution_runtime_files_ready(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).ensure_execution_runtime_files_ready(*args, **kwargs)

    def commit_execution_runtime_for_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).commit_execution_runtime_for_preview(*args, **kwargs)

    def preview_execution_for_real_ready_order_manual(self) -> None:
        if not startup_recovery_action_allowed(self, "Execution Preview"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "Execution Preview",
            "REAL_READY order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("Execution Preview: order_id를 입력하세요.")
            return

        try:
            read_result = self.read_order_from_queue_by_id(order_id, ORDER_QUEUE_PATH)
            order = read_result.get("order") if isinstance(read_result, dict) else {}
            order_dict = order if isinstance(order, dict) else {"id": order_id}
            guard_preview = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=False)
            guard_reasons = self.real_preflight_guard_block_reasons(guard_preview, include_operator=False)
            if guard_reasons:
                self.statusBarMessage("Execution Preview blocked: real trade guard is not ready")
                QMessageBox.warning(
                    self,
                    "Execution Preview blocked",
                    "\n".join(str(reason) for reason in guard_reasons),
                )
                return
            if not self.confirm_execution_runtime_commit(
                order_dict,
                guard_preview,
                order_executions_path=ORDER_EXECUTIONS_PATH,
                order_locks_path=ORDER_LOCKS_PATH,
                queue_path=ORDER_QUEUE_PATH,
            ):
                self.statusBarMessage("Execution Preview cancelled before runtime commit confirmation")
                return

            guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=True)
            result = preview_execution_for_real_ready_order(order_id, guard, ORDER_QUEUE_PATH)
            runtime_commit = {}
            if result.get("ok") is True:
                runtime_commit = self.commit_execution_runtime_for_preview(
                    order_dict,
                    guard,
                    result,
                    order_executions_path=ORDER_EXECUTIONS_PATH,
                    order_locks_path=ORDER_LOCKS_PATH,
                )
                result["runtime_dry_run_result"] = runtime_commit.get("runtime_dry_run_result")
                result["commit_plan_orchestrator_result"] = runtime_commit.get("commit_plan_orchestrator_result")
                result["runtime_commit_readiness_policy_result"] = runtime_commit.get("runtime_commit_readiness_policy_result")
                result["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                result["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
                preview_result = result.get("preview_result")
                if isinstance(preview_result, dict):
                    preview_result["runtime_dry_run_result"] = runtime_commit.get("runtime_dry_run_result")
                    preview_result["commit_plan_orchestrator_result"] = runtime_commit.get("commit_plan_orchestrator_result")
                    preview_result["runtime_commit_readiness_policy_result"] = runtime_commit.get("runtime_commit_readiness_policy_result")
                    preview_result["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                    preview_result["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
            self._last_execution_preview_result = result
            self._last_execution_preview_queue_snapshot = AutoTradeSettingWindow.queue_file_snapshot(ORDER_QUEUE_PATH)
            AutoTradeSettingWindow.update_manual_queue_commit_button_state(self)
            report = build_execution_preview_report(result)
            preview_context = {
                "source": "gui_execution_preview_button",
                "guard": guard,
                "legacy_execution_preview_result": result,
            }
            controller_result = build_execution_readiness_preview_from_context(
                order_id=order_id,
                preview_context=preview_context,
            )
            formatted_result = (
                controller_result.get("formatted_result")
                if isinstance(controller_result, dict)
                else None
            )
            readiness_text = ""
            if isinstance(formatted_result, dict):
                readiness_text = str(formatted_result.get("text", "") or "")
            if readiness_text:
                readiness_report = dict(report)
                readiness_report["text"] = readiness_text
                readiness_report["readiness_controller_result"] = controller_result
                report = readiness_report
            if result.get("ok") is True and runtime_commit and runtime_commit.get("runtime_commit_ready") is not True:
                blocked = "\n".join(str(reason) for reason in runtime_commit.get("blocked_reasons") or [])
                runtime_report = dict(report)
                runtime_report["ok"] = False
                runtime_report["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                runtime_report["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
                runtime_report["text"] = f"{runtime_report.get('text', '')}\n\n[Runtime Commit]\nBLOCKED\n{blocked}"
                report = runtime_report
            self.show_execution_preview_report(report)
            status_text = "통과" if report.get("ok") else "차단"
            self.statusBarMessage(f"Execution Preview {status_text}: {order_id}")
        except Exception as exc:
            self.statusBarMessage(f"Execution Preview 실패: {exc}")
            QMessageBox.critical(
                self,
                "Execution Preview 실패",
                f"Execution Preview 처리 중 오류가 발생했습니다.\n\n{exc}",
            )

    def show_execution_preview_report(self, report: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Preview Report")
        dialog.resize(900, 650)

        layout = QVBoxLayout()
        title_label = QLabel("Execution Preview Report")
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(str(report.get("text", "")))
        body.setMinimumHeight(500)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def execution_preview_result_dict(self) -> dict[str, object]:
        result = getattr(self, "_last_execution_preview_result", None)
        return result if isinstance(result, dict) else {}

    def queue_write_preview_from_last_execution_preview(self) -> dict[str, object]:
        result = AutoTradeSettingWindow.execution_preview_result_dict(self)
        if isinstance(result.get("queue_write_preview_result"), dict):
            return result["queue_write_preview_result"]

        preview_result = result.get("preview_result")
        if isinstance(preview_result, dict) and isinstance(preview_result.get("queue_write_preview_result"), dict):
            return preview_result["queue_write_preview_result"]

        return {}

    def runtime_commit_result_from_last_execution_preview(self) -> dict[str, object]:
        result = AutoTradeSettingWindow.execution_preview_result_dict(self)
        if isinstance(result.get("runtime_commit_result"), dict):
            return result["runtime_commit_result"]

        preview_result = result.get("preview_result")
        if isinstance(preview_result, dict) and isinstance(preview_result.get("runtime_commit_result"), dict):
            return preview_result["runtime_commit_result"]

        return {}

    @staticmethod
    def queue_file_snapshot(queue_path: Path) -> dict[str, object]:
        return AutoTradeOrderExecutionBoundary.queue_file_snapshot(queue_path)

    def last_execution_preview_queue_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self, "_last_execution_preview_queue_snapshot", None)
        return snapshot if isinstance(snapshot, dict) else {}

    def update_manual_queue_commit_button_state(self) -> None:
        button = getattr(self, "btn_manual_queue_commit", None)
        if button is None:
            return

        queue_write_preview = AutoTradeSettingWindow.queue_write_preview_from_last_execution_preview(self)
        runtime_commit_result = AutoTradeSettingWindow.runtime_commit_result_from_last_execution_preview(self)
        button.setEnabled(
            queue_write_preview.get("write_preview") is True
            and runtime_commit_result.get("status") == "COMMITTED"
            and runtime_commit_result.get("committed") is True
        )

    def manual_queue_commit_confirmation_text(
        self,
        queue_write_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object] | None = None,
    ) -> str:
        record = queue_write_preview_result.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        snapshot = queue_snapshot if isinstance(queue_snapshot, dict) else AutoTradeSettingWindow.queue_file_snapshot(queue_path)

        return "\n".join(
            [
                "수동 Queue 저장 확인",
                "",
                "이 작업은 ORDER_QUEUED record를 order_queue JSON에 저장합니다.",
                "SendOrder 호출이 아닙니다.",
                "주문 전송이 아닙니다.",
                "자동 실행 루프에 연결되지 않습니다.",
                "",
                f"order_id: {record_dict.get('order_id', '-')}",
                f"request_hash: {record_dict.get('request_hash', '-')}",
                f"lock_id: {record_dict.get('lock_id', '-')}",
                f"queue_pending_id: {record_dict.get('queue_pending_id', '-')}",
                f"order_queued_id: {record_dict.get('id', '-')}",
                f"queue_path: {queue_path}",
                f"before_sha256: {snapshot.get('sha256', '-')}",
                f"file_size: {snapshot.get('size', '-')}",
                f"mtime: {snapshot.get('mtime', '-')}",
                f"orders_count: {snapshot.get('orders_count', '-')}",
                f"backup_path: {queue_path}.bak",
                "",
                "계속하려면 수동 Queue 저장 실행을 선택하세요.",
            ]
        )

    def confirm_manual_queue_commit(
        self,
        queue_write_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object] | None = None,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("수동 Queue 저장 확인")
        dialog.resize(720, 420)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(self.manual_queue_commit_confirmation_text(queue_write_preview_result, queue_path, queue_snapshot))
        body.setMinimumHeight(300)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("수동 Queue 저장 실행")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        queued_record = queue_write_preview_result.get("order_queued_record_preview")
        queued_record = queued_record if isinstance(queued_record, dict) else {}
        order_id = str(
            queued_record.get("order_id")
            or queued_record.get("id")
            or queue_write_preview_result.get("order_id")
            or ""
        ).strip()
        execution_id = str(queued_record.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_queue_commit",
            target_type="ORDER",
            target_id=order_id or execution_id or None,
            target_name="수동 Queue 저장",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(queued_record.get("source_signal_id") or "").strip() or None,
            stock_code=str(queued_record.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_QUEUE_COMMIT",
                "prompt_title": "수동 Queue 저장 확인",
                "prompt_summary": "검증된 수동 주문 Queue 저장 승인",
                "offered_options": ["수동 Queue 저장 실행", "취소"],
                "selected_option": "수동 Queue 저장 실행" if accepted else "취소",
                "approval_stage": "MANUAL_QUEUE_COMMIT",
            },
        )
        return accepted

    def verify_manual_queue_commit_read_back(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).verify_manual_queue_commit_read_back(*args, **kwargs)

    def show_manual_queue_commit_result(self, result: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual Queue Commit Result")
        dialog.resize(760, 520)

        commit_result = result.get("commit_result")
        commit_result_dict = commit_result if isinstance(commit_result, dict) else {}
        lines = [
            "Manual Queue Commit Result",
            "",
            f"manual_commit: {result.get('manual_commit')}",
            f"commit_stage: {result.get('commit_stage')}",
            f"next_stage: {result.get('next_stage')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"changed: {commit_result_dict.get('changed', result.get('changed', '-'))}",
            f"status: {commit_result_dict.get('status', '-')}",
            f"order_id: {commit_result_dict.get('order_id', '-')}",
            f"order_queued_id: {commit_result_dict.get('order_queued_id', '-')}",
            f"request_hash: {commit_result_dict.get('request_hash', '-')}",
            f"lock_id: {commit_result_dict.get('lock_id', '-')}",
            f"order_queue_path: {commit_result_dict.get('order_queue_path', '-')}",
            f"backup_path: {commit_result_dict.get('backup_path', '-')}",
            f"send_order_called: {commit_result_dict.get('send_order_called', False)}",
            f"execution_enabled: {commit_result_dict.get('execution_enabled', False)}",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def commit_last_execution_preview_queue_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "수동 Queue 저장"):
            return
        queue_write_preview = self.queue_write_preview_from_last_execution_preview()
        if queue_write_preview.get("write_preview") is not True:
            self.statusBarMessage("수동 Queue 저장: 먼저 유효한 Execution Preview를 실행하세요.")
            self.update_manual_queue_commit_button_state()
            return

        queue_path = ORDER_QUEUE_PATH
        preview_snapshot = AutoTradeSettingWindow.last_execution_preview_queue_snapshot(self)
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if not preview_snapshot.get("sha256"):
            result = {
                "manual_commit": False,
                "commit_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": preview_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["queue snapshot from preview is required"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.")
            return

        if preview_snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "manual_commit": False,
                "commit_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": preview_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["queue file changed after preview; rerun Execution Preview"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.")
            return

        runtime_commit_result = self.runtime_commit_result_from_last_execution_preview()
        if not runtime_commit_result:
            result = {
                "manual_commit": False,
                "commit_stage": "runtime_commit_result",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": current_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["runtime commit result is required before runtime queue commit"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("Manual Queue commit blocked: runtime commit result is required")
            return

        if not self.confirm_manual_queue_commit(queue_write_preview, queue_path, current_snapshot):
            self.statusBarMessage("수동 Queue 저장: 취소됨")
            return

        queue_commit_readiness = evaluate_execution_queue_commit_readiness(
            runtime_commit_result=runtime_commit_result,
            queue_write_preview_result=queue_write_preview,
            queue_path=queue_path,
            confirmations={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
        )
        if queue_commit_readiness.get("status") != "READY_TO_COMMIT_QUEUE":
            result = {
                "manual_commit": False,
                "commit_stage": "queue_commit_readiness_policy",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": current_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": list(queue_commit_readiness.get("issues") or ["queue commit readiness policy is not ready"]),
                "queue_commit_readiness_policy_result": queue_commit_readiness,
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("Manual Queue commit blocked: readiness policy failed")
            return

        result = commit_execution_queue_manually(
            queue_write_preview,
            queue_path,
            context={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
                "event_journal_order": queue_write_preview.get("order_queued_record_preview"),
            },
            queue_commit_readiness_policy_result=queue_commit_readiness,
            manual_queue_commit_after_runtime_confirmed=True,
        )
        after_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        result["before_sha256"] = current_snapshot.get("sha256")
        result["after_sha256"] = after_snapshot.get("sha256")
        result["changed"] = current_snapshot.get("sha256") != after_snapshot.get("sha256")
        if result.get("manual_commit") is True:
            read_back = self.verify_manual_queue_commit_read_back(
                queue_path=queue_path,
                queue_write_preview_result=queue_write_preview,
                runtime_commit_result=runtime_commit_result,
            )
            result["queue_commit_read_back"] = read_back
            result["queue_commit_read_back_verified"] = read_back.get("verified") is True
            if read_back.get("verified") is not True:
                blocked = result.get("blocked_reasons")
                if not isinstance(blocked, list):
                    blocked = []
                blocked.extend(str(reason) for reason in read_back.get("issues") or [])
                result["blocked_reasons"] = blocked
        self.show_manual_queue_commit_result(result)
        status_text = "완료" if result.get("manual_commit") and result.get("queue_commit_read_back_verified") else "차단"
        self.statusBarMessage(f"수동 Queue 저장 {status_text}")

    def manual_send_order_confirmation_text(
        self,
        order: dict[str, object],
        call_preview: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        preview = call_preview.get("send_order_call_preview")
        preview_dict = preview if isinstance(preview, dict) else {}
        params = preview_dict.get("send_order_params")
        params_dict = params if isinstance(params, dict) else {}
        if not params_dict:
            adapter_contract = call_preview.get("adapter_contract_result")
            adapter_contract_dict = adapter_contract if isinstance(adapter_contract, dict) else {}
            params = adapter_contract_dict.get("send_order_params")
            params_dict = params if isinstance(params, dict) else {}
        return "\n".join(
            [
                "Manual Kiwoom SendOrder confirmation",
                "",
                "This action will call Kiwoom SendOrder exactly once.",
                "Queue claim and SendOrder result are recorded before/after the callable boundary.",
                "Broker acceptance is not assumed from SEND_CALL_ACCEPTED.",
                "",
                f"account_no: {params_dict.get('account_no', '-')}",
                f"order_id: {order.get('order_id', order.get('id', '-'))}",
                f"code: {params_dict.get('code', order.get('code', '-'))}",
                f"side/order_name: {params_dict.get('order_name', order.get('side', '-'))}",
                f"quantity: {params_dict.get('quantity', order.get('quantity', '-'))}",
                f"price: {params_dict.get('price', order.get('price', '-'))}",
                f"hoga: {params_dict.get('hoga', '-')}",
                f"queue_path: {queue_path}",
                f"queue_revision: {queue_snapshot.get('revision', '-')}",
                f"queue_sha256: {queue_snapshot.get('sha256', '-')}",
                "",
                "Continue only if this real order should be submitted now.",
            ]
        )

    def confirm_manual_send_order(
        self,
        order: dict[str, object],
        call_preview: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual Kiwoom SendOrder Confirmation")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(self.manual_send_order_confirmation_text(order, call_preview, queue_path, queue_snapshot))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Call SendOrder once")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        execution_id = str(order.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_send_order",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="Manual SendOrder",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_SEND_ORDER",
                "prompt_title": "Manual Kiwoom SendOrder Confirmation",
                "prompt_summary": "Queue 주문의 Kiwoom SendOrder 호출 승인",
                "offered_options": ["Call SendOrder once", "Cancel"],
                "selected_option": "Call SendOrder once" if accepted else "Cancel",
                "approval_stage": "SEND_ORDER",
            },
        )
        return accepted

    def build_manual_send_order_environment(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_send_order_environment(*args, **kwargs)

    def send_order_identity_from_record(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).send_order_identity_from_record(*args, **kwargs)

    def build_manual_send_order_call_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_send_order_call_preview(*args, **kwargs)

    def build_manual_final_send_gate_result(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_final_send_gate_result(*args, **kwargs)

    def show_manual_send_order_result(self, result: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual SendOrder Result")
        dialog.resize(760, 520)

        lines = [
            "Manual SendOrder Result",
            "",
            f"status: {result.get('status', '-')}",
            f"stage: {result.get('stage', result.get('executor_stage', '-'))}",
            f"order_id: {result.get('order_id', '-')}",
            f"callable_executed: {result.get('callable_executed', False)}",
            f"send_order_called: {result.get('send_order_called', False)}",
            f"broker_api_called: {result.get('broker_api_called', False)}",
            f"actual_order_sent: {result.get('actual_order_sent', False)}",
            f"queue_result_recorded: {result.get('queue_result_recorded', False)}",
            "",
            "blocked_reasons/issues:",
        ]
        reasons = result.get("blocked_reasons") or result.get("issues") or []
        if isinstance(reasons, list) and reasons:
            lines.extend(f"- {reason}" for reason in reasons)
        else:
            lines.append("-")

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def _queue_data_for_manual_order_action(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._queue_data_for_manual_order_action(*args, **kwargs)

    def _pending_cancel_duplicate_reason(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._pending_cancel_duplicate_reason(*args, **kwargs)

    def _pending_modify_duplicate_reason(self, orders: list[object], original_order_no: str) -> str:
        active_statuses = {
            "ORDER_QUEUED",
            "DISPATCH_CLAIMED",
            "SEND_ATTEMPTED",
            "SEND_CALL_IN_PROGRESS",
            "SEND_CALL_ACCEPTED",
            "SEND_UNCERTAIN",
            "BROKER_ACCEPTED",
        }
        for item in orders:
            record = item if isinstance(item, dict) else {}
            execution_request = record.get("execution_request")
            request_preview = execution_request.get("request_preview") if isinstance(execution_request, dict) else {}
            if not isinstance(request_preview, dict):
                continue
            if str(request_preview.get("order_action") or "").strip().upper() not in {"CANCEL", "MODIFY"}:
                continue
            if str(request_preview.get("original_order_no") or "").strip() != original_order_no:
                continue
            if record.get("original_order_effect_confirmed") is True:
                continue
            if str(record.get("status") or "").strip().upper() in active_statuses:
                return "active cancel/modify request already exists for original_order_no"
        return ""

    def _build_manual_cancel_order_queued_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._build_manual_cancel_order_queued_preview(*args, **kwargs)

    def queue_pending_order_cancellations_for_stock_automatically(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).queue_pending_order_cancellations_for_stock_automatically(*args, **kwargs)

    def _build_manual_modify_order_queued_preview(
        self,
        source_order: dict[str, object],
        *,
        queue_revision: object,
        modify_quantity: int,
        modify_price: int,
    ) -> dict[str, object]:
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        source_signal_id = str(source_order.get("source_signal_id") or "").strip()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        account_no = str(source_order.get("account_no") or "").strip()
        code = str(source_order.get("code") or "").strip()
        side = str(source_order.get("side") or "").strip().upper()
        suffix = uuid4().hex[:12]
        order_id = f"{source_order_id}_MODIFY_{suffix}"
        execution_id = f"EXEC_MODIFY_{suffix}"
        lock_id = f"LOCK_MODIFY_{suffix}"
        candidate_id = f"MODIFY_CANDIDATE_{suffix}"
        queue_pending_id = f"QUEUE_PENDING_{candidate_id}"
        hash_payload = {
            "action": "MODIFY",
            "source_order_id": source_order_id,
            "broker_order_no": broker_order_no,
            "account_no": account_no,
            "code": code,
            "side": side,
            "quantity": modify_quantity,
            "price": modify_price,
            "lock_id": lock_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        execution_request = {
            "execution_id": execution_id,
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "lock_id": lock_id,
            "request_hash": request_hash,
            "guard_snapshot": {"account_no": account_no, "source_queue_revision": queue_revision},
            "request_preview": {
                "account_no": account_no,
                "screen_no": project_order_default_screen_no(),
                "side": side,
                "order_action": "MODIFY",
                "code": code,
                "quantity": modify_quantity,
                "price": modify_price,
                "hoga": "LIMIT",
                "original_order_no": broker_order_no,
                "source_order_id": source_order_id,
            },
        }
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": f"ORDER_QUEUED_{order_id}",
                "status": "ORDER_QUEUED",
                "source": "execution_queue_pending",
                "source_signal_id": source_signal_id,
                "order_id": order_id,
                "candidate_id": candidate_id,
                "queue_pending_id": queue_pending_id,
                "request_hash": request_hash,
                "lock_id": lock_id,
                "execution_id": execution_id,
                "execution_request": execution_request,
                "queue_contract_version": "manual-modify-1",
                "send_order_called": False,
                "execution_enabled": False,
                "blocked_reasons": [],
                "account_no": account_no,
                "code": code,
                "side": side,
                "quantity": modify_quantity,
                "price": modify_price,
                "order_type": "LIMIT",
                "order_action": "MODIFY",
                "modify_source_order_id": source_order_id,
            },
        }

    def confirm_manual_cancel_pending_order(self, source_order: dict[str, object], preview: dict[str, object]) -> bool:
        message = "\n".join(
            [
                "Manual pending order cancel",
                "",
                "This creates an ORDER_QUEUED cancel request and then uses the existing Manual SendOrder flow.",
                "The original open order is not marked cancelled until Kiwoom Chejan confirms it.",
                "",
                f"source_order_id: {source_order.get('order_id', source_order.get('id', '-'))}",
                f"broker_order_no: {source_order.get('broker_order_no', '-')}",
                f"remaining_quantity: {source_order.get('remaining_quantity', '-')}",
                f"account_no: {source_order.get('account_no', '-')}",
                f"code: {source_order.get('code', '-')}",
            ]
        )
        answer = QMessageBox.question(self, "Manual Cancel", message, QMessageBox.Yes | QMessageBox.No)
        accepted = answer == QMessageBox.Yes
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "REJECTED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_cancel_pending_order",
            target_type="ORDER",
            target_id=source_order_id or None,
            target_name="Manual Cancel",
            order_id=source_order_id or None,
            broker_order_no=str(source_order.get("broker_order_no") or "").strip() or None,
            stock_code=str(source_order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_ORDER_CANCEL",
                "prompt_title": "Manual Cancel",
                "prompt_summary": "미체결 원주문의 취소 Queue 생성",
                "offered_options": ["예", "아니오"],
                "selected_option": "예" if accepted else "아니오",
                "order_action": "CANCEL",
            },
        )
        return accepted

    def confirm_manual_modify_pending_order(self, source_order: dict[str, object], preview: dict[str, object]) -> bool:
        request_preview = preview["order_queued_record_preview"]["execution_request"]["request_preview"]
        message = "\n".join(
            [
                "Manual pending order modify",
                "",
                "This creates an ORDER_QUEUED modify request and then uses the existing Manual SendOrder flow.",
                "The original open order is not changed until Kiwoom Chejan confirms it.",
                "",
                f"source_order_id: {source_order.get('order_id', source_order.get('id', '-'))}",
                f"broker_order_no: {source_order.get('broker_order_no', '-')}",
                f"remaining_quantity: {source_order.get('remaining_quantity', '-')}",
                f"modify_quantity: {request_preview.get('quantity', '-')}",
                f"modify_price: {request_preview.get('price', '-')}",
            ]
        )
        answer = QMessageBox.question(self, "Manual Modify", message, QMessageBox.Yes | QMessageBox.No)
        accepted = answer == QMessageBox.Yes
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "REJECTED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_modify_pending_order",
            target_type="ORDER",
            target_id=source_order_id or None,
            target_name="Manual Modify",
            order_id=source_order_id or None,
            broker_order_no=str(source_order.get("broker_order_no") or "").strip() or None,
            stock_code=str(source_order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_ORDER_MODIFY",
                "prompt_title": "Manual Modify",
                "prompt_summary": "미체결 원주문의 정정 Queue 생성",
                "offered_options": ["예", "아니오"],
                "selected_option": "예" if accepted else "아니오",
                "order_action": "MODIFY",
                "input_value": {
                    "quantity": request_preview.get("quantity"),
                    "price": request_preview.get("price"),
                },
            },
        )
        return accepted

    def cancel_pending_order_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Manual Cancel"):
            return
        source_id, accepted = QInputDialog.getText(self, "Manual Cancel", "BROKER_ACCEPTED/PARTIALLY_FILLED order id:")
        if not accepted:
            return
        source_id = str(source_id or "").strip()
        if not source_id:
            self.statusBarMessage("Manual Cancel: source order id is required")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        data, orders, issues = self._queue_data_for_manual_order_action(queue_path)
        if issues:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_read_queue", "blocked_reasons": issues})
            return
        source_order = None
        for item in orders:
            record = item if isinstance(item, dict) else {}
            if str(record.get("id") or "").strip() == source_id or str(record.get("order_id") or "").strip() == source_id:
                source_order = deepcopy(record)
                break
        if not isinstance(source_order, dict):
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_source_order", "blocked_reasons": ["source order not found"]})
            return

        status = str(source_order.get("status") or "").strip().upper()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        try:
            remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        except Exception:
            remaining_quantity = 0
        blocked: list[str] = []
        if status not in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}:
            blocked.append("source order status is not cancelable")
        if not broker_order_no:
            blocked.append("source order broker_order_no is required")
        if remaining_quantity <= 0:
            blocked.append("source order remaining_quantity must be greater than 0")
        duplicate_reason = self._pending_cancel_duplicate_reason(orders, broker_order_no)
        if duplicate_reason:
            blocked.append(duplicate_reason)
        environment = self.build_manual_send_order_environment(source_order, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            blocked.extend(list(environment.get("issues") or []))
        if blocked:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_source_order", "blocked_reasons": blocked})
            return

        preview = self._build_manual_cancel_order_queued_preview(source_order, queue_revision=snapshot.get("revision"))
        if not self.confirm_manual_cancel_pending_order(source_order, preview):
            self.statusBarMessage("Manual Cancel cancelled")
            return
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "cancel_stale_queue_snapshot",
                    "blocked_reasons": ["queue file changed after cancel preview; retry from latest queue"],
                }
            )
            return
        commit_result = commit_execution_queue_write(
            preview,
            queue_path,
            context={"manual_queue_write_confirmed": True, "manual_pending_cancel_confirmed": True},
            expected_revision=current_snapshot.get("revision"),
        )
        if commit_result.get("committed") is not True or commit_result.get("post_write_verified") is not True:
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "cancel_queue_commit",
                    "blocked_reasons": list(commit_result.get("blocked_reasons") or ["cancel queue commit failed"]),
                    "cancel_queue_commit_result": commit_result,
                }
            )
            return

        cancel_record = preview["order_queued_record_preview"]
        self.send_order_for_order_queued_manually(str(cancel_record.get("id") or ""))

    def modify_pending_order_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Manual Modify"):
            return
        source_id, accepted = QInputDialog.getText(self, "Manual Modify", "BROKER_ACCEPTED/PARTIALLY_FILLED order id:")
        if not accepted:
            return
        source_id = str(source_id or "").strip()
        if not source_id:
            self.statusBarMessage("Manual Modify: source order id is required")
            return
        raw_details, accepted = QInputDialog.getText(self, "Manual Modify", "modify quantity,price:")
        if not accepted:
            return
        parts = [part.strip() for part in str(raw_details or "").split(",")]
        if len(parts) != 2:
            self.show_manual_send_order_result(
                {"status": "BLOCKED", "stage": "modify_input", "blocked_reasons": ["modify input must be quantity,price"]}
            )
            return
        try:
            modify_quantity = int(parts[0])
            modify_price = int(parts[1])
        except Exception:
            self.show_manual_send_order_result(
                {"status": "BLOCKED", "stage": "modify_input", "blocked_reasons": ["modify quantity and price must be integers"]}
            )
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        data, orders, issues = self._queue_data_for_manual_order_action(queue_path)
        if issues:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_read_queue", "blocked_reasons": issues})
            return
        source_order = None
        for item in orders:
            record = item if isinstance(item, dict) else {}
            if str(record.get("id") or "").strip() == source_id or str(record.get("order_id") or "").strip() == source_id:
                source_order = deepcopy(record)
                break
        if not isinstance(source_order, dict):
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_source_order", "blocked_reasons": ["source order not found"]})
            return

        status = str(source_order.get("status") or "").strip().upper()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        try:
            remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        except Exception:
            remaining_quantity = 0
        blocked: list[str] = []
        if status not in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}:
            blocked.append("source order status is not modifiable")
        if not broker_order_no:
            blocked.append("source order broker_order_no is required")
        if remaining_quantity <= 0:
            blocked.append("source order remaining_quantity must be greater than 0")
        if modify_quantity <= 0 or modify_quantity > remaining_quantity:
            blocked.append("modify quantity must be between 1 and remaining_quantity")
        if modify_price <= 0:
            blocked.append("modify price must be greater than 0")
        duplicate_reason = self._pending_modify_duplicate_reason(orders, broker_order_no)
        if duplicate_reason:
            blocked.append(duplicate_reason)
        environment = self.build_manual_send_order_environment(source_order, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            blocked.extend(list(environment.get("issues") or []))
        if blocked:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_source_order", "blocked_reasons": blocked})
            return

        preview = self._build_manual_modify_order_queued_preview(
            source_order,
            queue_revision=snapshot.get("revision"),
            modify_quantity=modify_quantity,
            modify_price=modify_price,
        )
        if not self.confirm_manual_modify_pending_order(source_order, preview):
            self.statusBarMessage("Manual Modify cancelled")
            return
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "modify_stale_queue_snapshot",
                    "blocked_reasons": ["queue file changed after modify preview; retry from latest queue"],
                }
            )
            return
        commit_result = commit_execution_queue_write(
            preview,
            queue_path,
            context={"manual_queue_write_confirmed": True, "manual_pending_modify_confirmed": True},
            expected_revision=current_snapshot.get("revision"),
        )
        if commit_result.get("committed") is not True or commit_result.get("post_write_verified") is not True:
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "modify_queue_commit",
                    "blocked_reasons": list(commit_result.get("blocked_reasons") or ["modify queue commit failed"]),
                    "modify_queue_commit_result": commit_result,
                }
            )
            return

        modify_record = preview["order_queued_record_preview"]
        self.send_order_for_order_queued_manually(str(modify_record.get("id") or ""))

    def send_order_for_order_queued_manually(self, order_id_override: str | None = None) -> None:
        if not startup_recovery_action_allowed(self, "Manual SendOrder"):
            return
        if order_id_override is None:
            order_id, accepted = QInputDialog.getText(self, "Manual SendOrder", "ORDER_QUEUED record id:")
            if not accepted:
                return
            order_id = str(order_id or "").strip()
        else:
            order_id = str(order_id_override or "").strip()
        if not order_id:
            self.statusBarMessage("Manual SendOrder: ORDER_QUEUED record id is required")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "read_order",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        if order_dict.get("status") != "ORDER_QUEUED":
            result = {
                "status": "BLOCKED",
                "stage": "order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["target record status is not ORDER_QUEUED"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        environment = self.build_manual_send_order_environment(order_dict, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "send_order_environment",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(environment.get("issues") or []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        display_preview = self.build_manual_send_order_call_preview(order_dict, environment, operator_confirmed=False)
        adapter_contract_result = display_preview.get("adapter_contract_result")
        adapter_contract_dict = adapter_contract_result if isinstance(adapter_contract_result, dict) else {}
        if adapter_contract_dict.get("status") != "SEND_ORDER_CONTRACT_READY":
            result = {
                "status": "BLOCKED",
                "stage": "send_order_display_preview",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(adapter_contract_dict.get("issues") or ["send order adapter contract is not ready"]),
                "send_order_call_preview_result": display_preview,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        if not self.confirm_manual_send_order(order_dict, display_preview, queue_path, snapshot):
            self.statusBarMessage("Manual SendOrder cancelled")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "status": "BLOCKED",
                "stage": "stale_queue_snapshot",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["queue file changed after SendOrder preview; retry from latest queue"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        latest_read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if latest_read_result.get("ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "latest_order_read",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": latest_read_result.get("blocked_reasons", []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return
        latest_order = latest_read_result.get("order")
        latest_order_dict = latest_order if isinstance(latest_order, dict) else {}
        if latest_order_dict.get("status") != "ORDER_QUEUED":
            result = {
                "status": "BLOCKED",
                "stage": "latest_order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["latest target record status is not ORDER_QUEUED"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        latest_environment = self.build_manual_send_order_environment(latest_order_dict, queue_path)
        if latest_environment.get("send_order_environment_ready") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "send_order_environment_after_confirmation",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(latest_environment.get("issues") or []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        identity = self.send_order_identity_from_record(latest_order_dict)
        final_gate = self.build_manual_final_send_gate_result(
            latest_order_dict,
            latest_environment,
            queue_path,
            snapshot,
            current_snapshot,
        )
        if final_gate.get("final_send_gate_ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "final_send_gate",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(final_gate.get("blocked_reasons") or ["final send gate blocked"]),
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        call_preview = self.build_manual_send_order_call_preview(latest_order_dict, latest_environment, operator_confirmed=True)
        if call_preview.get("status") != "SEND_ORDER_CALL_READY":
            result = {
                "status": "BLOCKED",
                "stage": "send_order_call_preview",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(call_preview.get("issues") or ["send order call preview is not ready"]),
                "send_order_call_preview_result": call_preview,
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        fresh_preflight = AutoTradeSettingWindow.order_execution_boundary(
            self
        ).evaluate_final_dispatch_fresh_preflight(
            latest_order_dict,
            latest_environment,
            queue_path,
        )
        if fresh_preflight.get("ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "fresh_dispatch_preflight",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(
                    fresh_preflight.get("blocked_reasons")
                    or ["fresh dispatch preflight blocked"]
                ),
                "fresh_dispatch_preflight_result": fresh_preflight,
                "send_order_call_preview_result": call_preview,
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        claim_token = f"GUI_CLAIM_{uuid4().hex}"
        claim = claim_order_for_dispatch(
            queue_path,
            identity,
            final_gate,
            claim_token=claim_token,
            claim_owner="GUI_MANUAL_SEND_ORDER",
            claim_source="gui_manual_send_order",
            context={
                "dispatch_claim_owner": "GUI_MANUAL_SEND_ORDER",
                "dispatch_claim_source": "gui_manual_send_order",
                "dispatch_claim_ttl_sec": 60,
                "queue_path": str(queue_path),
                "queue_snapshot_hash": current_snapshot.get("sha256"),
            },
            expected_revision=current_snapshot.get("revision"),
        )
        if claim.get("claimed") is not True or claim.get("post_write_verified") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "dispatch_claim",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(claim.get("blocked_reasons") or ["dispatch claim failed"]),
                "dispatch_claim_result": claim,
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        result = execute_claimed_send_order(
            queue_path,
            identity,
            str(claim.get("dispatch_claim_id") or ""),
            claim_token,
            "GUI_MANUAL_SEND_ORDER",
            claim.get("revision_after"),
            latest_environment.get("send_order_callable"),
            call_preview.get("send_order_args"),
            context={
                "send_order_attempt_owner": "GUI_MANUAL_SEND_ORDER",
                "send_order_attempt_source": "gui_manual_send_order",
            },
        )
        result["order_id"] = order_id
        result["dispatch_claim_result"] = claim
        result["final_send_gate_result"] = final_gate
        result["send_order_call_preview_result"] = call_preview
        result["fresh_dispatch_preflight_result"] = fresh_preflight
        self.show_manual_send_order_result(result)
        status_text = "completed" if result.get("queue_result_recorded") else "blocked"
        self.statusBarMessage(f"Manual SendOrder {status_text}")

    def auto_trade_runtime_state_for_order(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_trade_runtime_state_for_order(*args, **kwargs)

    def auto_trade_execution_block_reasons(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_trade_execution_block_reasons(*args, **kwargs)

    def order_with_execution_request_defaults(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).order_with_execution_request_defaults(*args, **kwargs)

    def send_order_for_order_queued_automatically(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).send_order_for_order_queued_automatically(*args, **kwargs)

    def process_executable_order_for_auto_trade(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).process_executable_order_for_auto_trade(*args, **kwargs)

    def auto_process_executable_orders_for_real_trade(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_process_executable_orders_for_real_trade(*args, **kwargs)

    def handle_raw_chejan_event(
        self,
        raw_event: dict[str, object],
        live_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return handle_kiwoom_raw_chejan_event(raw_event, live_context)

    def int_state_value(self, state: dict[str, object], key: str) -> int:
        try:
            return int(state.get(key, 0) or 0)
        except Exception:
            return 0

    def pre_start_review_check(self, routine_name: str, stock_dir: Path, code: str, name: str) -> dict[str, object]:
        """
        자동매매 시작 전 사전점검.

        프로그램이 먼저 점검하고, 문제 없는 종목만 RUNNING으로 전환한다.
        문제 소지가 있는 종목은 REVIEW_REQUIRED로 전환한 뒤 검토관리창에서 HTS 검토 후 처리한다.
        """
        item = build_review_required_item(routine_name, stock_dir, code, name)
        state = read_json_dict(stock_dir / "state.json")
        data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
        if data_reasons:
            return build_review_required_item(routine_name, stock_dir, code, name, data_reasons)

        return item

    def mark_review_required(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        item: dict[str, object],
        source: str = "",
    ) -> bool:
        reasons = unique_review_reasons(list(item.get("review_reasons", [])))
        state_before = read_json_dict(stock_dir / "state.json")
        if not isinstance(state_before, dict):
            state_before = {}
        before_status = str(state_before.get("status", "STOPPED")).strip().upper() or "STOPPED"
        data_reasons = auto_trade_setting_data_inconsistency_reasons(state_before)
        operation_active_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}
        operation_data_issue = bool(data_reasons) and before_status in operation_active_statuses
        operator_reasons = unique_review_reasons(
            [operator_review_reason(reason) for reason in reasons]
        )
        reason_text = (
            "운영 데이터 불일치"
            if operation_data_issue
            else (" / ".join(operator_reasons) or "수동 검토 필요")
        )
        review_location = str(
            source
            or item.get("review_location", "")
            or item.get("review_source", "")
            or item.get("detected_by", "")
            or "-"
        ).strip() or "-"
        review_location = operator_review_location(review_location)

        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": review_location,
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "last_checked_price": safe_float_value(item.get("current_price"), 0.0),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
        raw_reason_text = " / ".join(reasons)
        if raw_reason_text and raw_reason_text != reason_text:
            metadata["review_detail"] = raw_reason_text
        if operation_data_issue:
            stopped_at = now_text()
            emergency_metadata = {
                "emergency_stopped_at": stopped_at,
                "emergency_reason": "운영 데이터 불일치",
                "trade_enabled": False,
                "operation_notice": "",
                "operation_notice_reason": "",
                "operation_notice_at": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "auto_close_method": "",
                "auto_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
            }
            if not self.update_stock_status(
                stock_dir,
                code,
                name,
                "EMERGENCY_STOPPED",
                emergency_metadata,
                "운영 데이터 불일치",
            ):
                return False
            metadata.update(emergency_metadata)
            metadata["review_detail"] = " / ".join(data_reasons)
        resume_metadata = item.get("resume_metadata")
        if isinstance(resume_metadata, dict):
            metadata.update(resume_metadata)

        return self.update_stock_status(stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason_text)

    def update_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        return auto_trade_update_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )

    def operation_policy_protected_status(self, status: object) -> bool:
        return auto_trade_operation_policy_protected_status(self, status)

    def recalculate_stock_status_by_operation_policy(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        reason: str,
        extra_state: dict[str, object] | None = None,
        silent_unchanged: bool = False,
    ) -> tuple[str, str, str]:
        return auto_trade_recalculate_stock_status_by_operation_policy(
            self,
            stock_dir,
            code,
            name,
            reason,
            extra_state,
            silent_unchanged,
        )
    def recalculate_all_status_by_operation_policy(
        self,
        reason: str,
        silent_unchanged: bool = False,
        write_changelog_when_unchanged: bool = True,
    ) -> dict[str, int]:
        return auto_trade_recalculate_all_status_by_operation_policy(
            self,
            reason,
            silent_unchanged,
            write_changelog_when_unchanged,
        )
    def update_stock_operation_mode(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        operation_mode: str,
        config_updates: dict[str, object] | None = None,
    ) -> bool:
        return auto_trade_update_stock_operation_mode(
            self,
            stock_dir,
            code,
            name,
            operation_mode,
            config_updates,
        )

    def unregister_selected_auto_trade_stocks(self) -> None:
        unregister_selected_auto_trade_stocks(self)

    def statusBar_message(self, message: str, timeout_ms: int = 7000) -> None:
        self.statusBarMessage(message, timeout_ms)


    def open_operation_environment_settings(self) -> None:
        """스케줄매매관리 대체: 운영환경설정 창을 연다."""
        existing = getattr(self, "operation_environment_settings_window", None)
        if existing is not None and not sip.isdeleted(existing) and existing.isVisible():
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = OperationEnvironmentSettingsDialog(
            self,
            factory_reset_validator=validate_factory_reset_safety,
            factory_reset_executor=execute_program_factory_reset,
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self.operation_environment_settings_window = dialog

        def settings_saved() -> None:
            self._handle_operation_environment_settings_saved(dialog)

        dialog.accepted.connect(settings_saved)
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: (
                setattr(self, "operation_environment_settings_window", None)
                if getattr(self, "operation_environment_settings_window", None) is target
                else None
            )
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _handle_operation_environment_settings_saved(self, dialog) -> None:
        self.statusBarMessage("환경설정 저장 완료")
        refresh_auto_trade_views(self)

    def open_stock_register_window(self) -> None:
        """관제창과 동일한 중앙 종목 등록 창을 연다."""
        owner = persistent_feature_owner(self)
        owner_opener = getattr(owner, "open_stock_register_window", None)
        if callable(owner_opener):
            owner_opener()
            self.stock_register_window = getattr(owner, "stock_register_window", None)
            return
        from gui_stock_register_window import StockRegisterWindow

        self.stock_register_window = StockRegisterWindow(
            self,
            stock_search_register_opener=open_instance_stock_search_register_dialog,
        )
        self.stock_register_window.setAttribute(Qt.WA_DeleteOnClose, True)
        self.stock_register_window.finished.connect(
            lambda result: (
                refresh_auto_trade_views(self)
                if result == QDialog.Accepted
                else None
            )
        )
        self.stock_register_window.show()

    def open_selected_stock_policy_settings(self) -> None:
        """종목 우클릭용 개별종목 설정 창."""
        selected = self.selected_stock_info()
        if selected is None:
            QMessageBox.warning(self, "선택 오류", "개별종목 설정은 종목 1개를 선택한 상태에서 사용할 수 있습니다.")
            return
        stock_dir, code, name = selected
        dialog = StockPolicyOverrideDialog(stock_dir, code, name, self)
        if dialog.exec_() == QDialog.Accepted:
            refresh_auto_trade_views(self)


    def open_schedule_trade_management_window(self) -> None:
        dialog = ScheduleTradeManagementDialog(self)
        dialog.exec_()

    def set_selected_individual_schedule_time(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "시간을 변경할 종목을 1개 이상 선택하세요.")
            return

        first_config = read_json_dict(selected[0][0] / "config.json")
        if not first_config:
            first_config = default_config()
        start_time, end_buy_time, _ = effective_schedule_times(first_config)

        dialog = ScheduleOperationDialog(self, start_time, end_buy_time, len(selected))
        dialog.setWindowTitle("종목 시간 예외 설정")
        if dialog.exec_() != QDialog.Accepted:
            return

        result = auto_trade_apply_schedule_times_to_targets(
            self,
            selected,
            dialog.start_time(),
            dialog.end_buy_time(),
        )
        auto_trade_finalize_operation_mode_result(self, result)

    def reset_selected_schedule_to_global(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "기본 시간으로 리셋할 종목을 1개 이상 선택하세요.")
            return

        result = auto_trade_reset_schedule_times_for_targets(
            self,
            selected,
        )
        auto_trade_finalize_operation_mode_result(self, result)

    def set_selected_schedule_operation_mode(self) -> None:
        auto_trade_set_selected_schedule_operation_mode(self)

    def set_selected_operation_mode(
        self,
        operation_mode: str,
        config_updates: dict[str, object] | None = None,
    ) -> None:
        auto_trade_set_selected_operation_mode(self, operation_mode, config_updates)

    def split_start_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        운영시작 대상과 제외 대상을 분리한다.

        정책:
        - STOPPED: 운영 전/마감 후 상태이므로 운영시작 가능
        - MONITORING/WATCHING/WATCH/WATCH_BUY: 화면상 감시/대기지만 주문 비활성 상태이므로
          운영시작 버튼으로 현재 시간/운영방식에 맞게 재판정 가능
        - RUNNING/SELL_ONLY/REVIEW_REQUIRED/EMERGENCY 계열은 보호 상태로 제외
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []
        block_details: list[dict[str, object]] = []

        for stock_dir, code, name in selected:
            if self.start_target_is_review_isolated(stock_dir, code):
                skipped.append(f"{code} {name}(검토종목)")
                block_details.append(
                    {
                        "stock_code": str(code),
                        "stock_name": str(name),
                        "reason": "REVIEW_REQUIRED",
                        "display_label": f"{code} {name}".strip(),
                    }
                )
                continue
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            decision = auto_trade_setting_start_target_decision(
                self,
                state,
                code,
                config=read_json_dict(stock_dir / "config.json"),
            )
            if decision.get("allowed") is True:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")
                block_details.append(
                    {
                        "stock_code": str(code),
                        "stock_name": str(name),
                        "reason": str(decision.get("reason") or "NOT_STARTABLE"),
                        "status": status,
                        "operation_mode": str(
                            decision.get("operation_mode") or ""
                        ),
                        "session_phase": dict(
                            decision.get("session_phase") or {}
                        ),
                        "display_label": f"{code} {name}".strip(),
                    }
                )

        self._last_start_target_block_details = block_details
        return targets, skipped

    def start_target_block_details(self) -> tuple[dict[str, object], ...]:
        return tuple(
            dict(item)
            for item in getattr(self, "_last_start_target_block_details", ())
            if isinstance(item, dict)
        )

    def show_auto_trade_result_dialog(self, title: str, heading: str, lines: list[str]) -> None:
        """복수 종목 처리 결과를 표시하는 공용 창."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(420, 320)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title_label = QLabel(heading)
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText("\n".join(lines))
        body.setMinimumHeight(180)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        dialog.exec_()

    def start_selected_auto_trades(self) -> None:
        execute_operation_start_command(
            self,
            OperationStartCommandRequest(
                intent=OperationStartIntent.FULL_START,
                source="auto_trade_global_start_button",
            ),
            start_backend=auto_trade_start_selected_auto_trades,
            operation_state_reader=read_operation_state,
            summary_presenter=_show_operation_start_summary_toast,
        )

    def recalculate_routine_limits_for_new_operation_session(self) -> dict[str, object]:
        from routine_limit_recalculation import (
            recalculate_enabled_routine_limits_for_new_session,
        )

        return recalculate_enabled_routine_limits_for_new_session(self)

    def start_selected_rows_auto_trades(self) -> dict[str, object] | None:
        return execute_operation_start_command(
            self,
            OperationStartCommandRequest(
                intent=OperationStartIntent.SELECTIVE_START,
                source="auto_trade_context_menu",
            ),
            start_backend=auto_trade_start_selected_auto_trades,
            selective_backend=auto_trade_start_selected_rows_auto_trades,
        ).as_legacy_dict()

    def emergency_stop_selected_auto_trade_stocks(self) -> dict[str, object]:
        selected_targets = self.selected_stock_infos()
        if not selected_targets:
            return {
                "changed": (),
                "skipped": (),
                "changed_count": 0,
                "skipped_count": 0,
            }
        from gui_main_emergency_ops import execute_selected_emergency_stop

        return execute_selected_emergency_stop(self, selected_targets)

    def apply_selected_early_close_default(self, checked: bool = False) -> None:
        # QPushButton.clicked may pass a checked(bool) argument.
        # The default early-close method is read inside auto_trade_apply_selected_early_close_default().
        auto_trade_apply_selected_early_close_default(self)

    def apply_selected_early_close_profit_loss(self) -> None:
        auto_trade_apply_selected_early_close_profit_loss(self)

    def cancel_selected_early_close(self) -> None:
        auto_trade_cancel_selected_early_close(self)

    def apply_selected_early_close(
        self,
        method: str,
        profit_percent: str = "",
        loss_percent: str = "",
        source: str = "우클릭",
        extra_policy: dict[str, object] | None = None,
    ) -> None:
        if extra_policy is None and (str(profit_percent).strip() or str(loss_percent).strip()):
            extra_policy = {
                "profit_percent": str(profit_percent).strip(),
                "loss_percent": str(loss_percent).strip(),
            }
        auto_trade_apply_selected_early_close(
            self,
            method,
            source=source,
            extra_policy=extra_policy,
        )
    def open_review_required_window(self, _checked: object | None = None) -> None:
        auto_trade_open_review_required_window(self)

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        """부모 창 상태바에 메시지를 전달한다.

        분리 모듈에서는 MainWindow를 직접 참조하지 않는다.
        """
        parent = persistent_feature_owner(self)
        status_bar_getter = getattr(parent, "statusBar", None)
        if callable(status_bar_getter):
            try:
                status_bar_getter().showMessage(message, timeout_ms)
            except Exception:
                pass

    def showAutoTradePopupMessage(self, message: str, timeout_ms: int = 2500) -> None:
        popup = getattr(self, "_notification_popup", None)
        if popup is None:
            popup = AutoTradeNotificationPopup(self)
            self._notification_popup = popup
        popup.show_message(message, timeout_ms)

    def open_stock_performance_window(self) -> None:
        open_stock_performance(self)

def base_stock_routine_assignments() -> dict[tuple[str, str], set[str]]:
    """
    기초종목.txt 기준 종목-루틴 연결 정보를 반환한다.

    자동매매설정 창은 루틴 폴더에 남은 종목 폴더만으로 종목을 표시하지 않고,
    기초종목.txt 에 실제 연결된 종목만 표시한다.
    """
    result: dict[tuple[str, str], set[str]] = {}
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routines = stock.get("routines", [])
        if not code or not name:
            continue
        if isinstance(routines, list):
            routine_set = {str(routine).strip() for routine in routines if str(routine).strip()}
        else:
            routine_text = str(routines).strip()
            routine_set = {routine_text} if routine_text else set()
        result[(code, name)] = routine_set
    return result


def is_stock_assigned_to_routine(code: str, name: str, routine_name: str) -> bool:
    """
    기초종목.txt 기준으로 종목이 해당 루틴에 연결되어 있는지 확인한다.
    """
    assignments = base_stock_routine_assignments()
    return routine_name in assignments.get((code, name), set())
