# -*- coding: utf-8 -*-
"""
gui_routine_assign_window.py

매매루틴지정 창 및 루틴 해제 확인 다이얼로그.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QLineEdit,
)

from gui_centered_checkbox_delegate import CenteredCheckBoxDelegate
from gui_table_utils import next_sort_order
from gui_styles import apply_plain_table_header
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_order_utils import (
    format_number_value,
    pending_order_side_quantities,
)
from gui_review_utils import safe_float_value
from gui_config_utils import default_config
from gui_blocked_report_window import (
    BlockedActionReportViewDialog,
    blocked_items_preview,
    latest_blocked_action_report_path,
    write_blocked_action_report,
)
from gui_routine_assign_utils import (
    build_routine_assign_result_lines,
    build_routine_assign_status_text,
    build_routine_unassign_result_lines,
    build_routine_unassign_status_text,
)
from gui_routine_guard import routine_action_guard_info
from gui_routine_policy import (
    routine_action_reasons_for_stock,
    classify_routine_assign_targets,
    can_unassign_active_routine_from_stock,
)
from gui_routine_service import ensure_single_real_trade_routine_for_stock
from stock_repository import repository as stock_repository_factory
from runtime_io import read_json_dict
from state_policy import (
    schedule_override_enabled,
    normalize_operation_mode,
    operation_text_and_color,
)
from gui_auto_trade_display import (
    display_status_text_for_gui,
    create_auto_trade_status_item,
)
from gui_auto_trade_setting_window import (
    is_valid_stock_code,
    find_library_stock_by_code,
    PROJECT_ROOT,
    append_changelog,
    append_stock_log,
    get_routine_dirs,
    now_text,
    parse_stock_folder_name,
    read_base_stocks,
    routine_display_name,
    update_base_stock_routines,
)
def active_stock_register_status_display(code: str, name: str, current_routine: str) -> str:
    """루틴지정창 좌측 목록의 운영상태 표시값을 중앙 stocks 기준으로 반환한다."""
    routine_name = str(current_routine or "").strip()
    if not routine_name or routine_name == "미등록":
        return "미지정"

    try:
        repo = stock_repository_factory()
        stock_dir = repo.resolve_stock_dir(code, name)
    except Exception:
        return "미생성"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return "미생성"

    try:
        state = read_json_dict(state_path)
    except Exception:
        return "오류"

    if not isinstance(state, dict):
        return "오류"

    raw_status = str(state.get("status", "STOPPED")).strip() or "STOPPED"
    try:
        return display_status_text_for_gui(raw_status)
    except Exception:
        return raw_status



class RoutineUnassignConfirmDialog(QDialog):
    """루틴 해제 가능/불가 대상을 한 번에 보여주고 진행 여부를 확인한다."""

    def __init__(
        self,
        routine_name: str,
        removable_items: list[tuple[str, str]],
        blocked_items: list[dict[str, object]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("루틴 해제 확인")
        self.resize(760, 520)
        self.confirmed = False

        main_layout = QVBoxLayout()

        summary_label = QLabel(
            f"즉시 해제 가능 {len(removable_items)}개 / 해제 불가 {len(blocked_items)}개"
        )
        summary_label.setMinimumHeight(44)
        main_layout.addWidget(summary_label)

        if blocked_items:
            blocked_title = QLabel("해제 불가")
            blocked_title.setStyleSheet("color: #d00000; font-weight: bold;")
            main_layout.addWidget(blocked_title)

            blocked_list = QListWidget()
            blocked_list.setMinimumHeight(130)
            for item in blocked_items:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                current_routine = str(item.get("routine_name", "")).strip() or routine_name
                reasons = item.get("reasons", [])
                if not isinstance(reasons, list):
                    reasons = [str(reasons)]
                reason_text = ", ".join(str(reason) for reason in reasons if str(reason).strip()) or "-"
                display_status = str(item.get("display_status", "")).strip() or "-"
                line = f"{code} / {name} / {routine_name if 'routine_name' in locals() else '-'} / {reason if 'reason' in locals() else '차단됨'}"
                blocked_list.addItem(QListWidgetItem(line))
            main_layout.addWidget(blocked_list)

        if removable_items:
            removable_title = QLabel("해제 가능")
            removable_title.setStyleSheet("font-weight: bold;")
            main_layout.addWidget(removable_title)

            removable_list = QListWidget()
            removable_list.setMinimumHeight(100)
            for code, name in removable_items:
                removable_list.addItem(QListWidgetItem(f"{code} / {name} / {routine_name if 'routine_name' in locals() else '-'} / {reason if 'reason' in locals() else '차단됨'}"))
            main_layout.addWidget(removable_list)

        notice = QLabel(
            "※ 해제 가능 종목만 처리됩니다.\n"
            "※ 해제 불가 종목은 처리불가 누적리포트에 기록됩니다."
        )
        notice.setStyleSheet("color: #555555;")
        main_layout.addWidget(notice)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_confirm = QPushButton("해제 실행")
        self.btn_cancel = QPushButton("취소")
        self.btn_confirm.setMinimumWidth(120)
        self.btn_cancel.setMinimumWidth(100)
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_confirm)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)


class RoutineAssignWindow(QDialog):
    """
    매매루틴지정 창.

    역할:
    - 기초종목.txt 등록 종목 중 루틴 변경이 가능한 종목만 좌측에 표시한다.
    - 체크박스는 실제 처리 대상 표시용으로 유지한다.
    - 종목등록설정 창에서 전달된 종목 중 루틴 변경 가능한 종목은 자동 체크한다.
    - 루틴 지정/해제 실행 시점에도 삭제/등록해제 안전 규칙을 다시 검사한다.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        target_code: str = "",
        target_name: str = "",
        target_stocks: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)

        self.target_code = target_code.strip()
        self.target_name = target_name.strip()
        self.target_stocks = [
            (str(code).strip(), str(name).strip())
            for code, name in (target_stocks or [])
            if str(code).strip() and str(name).strip()
        ]
        if not self.target_stocks and (self.target_code or self.target_name):
            self.target_stocks = [(self.target_code, self.target_name)]

        self.setWindowTitle("매매루틴지정")
        self.resize(1060, 820)

        self.stock_search_input = QLineEdit()
        self.stock_search_input.setPlaceholderText("루틴 지정 가능 종목 검색")
        self.stock_table = QTableWidget()
        self.routine_table = QTableWidget()
        self.assigned_stock_table = QTableWidget()

        self.btn_apply = QPushButton("루틴 지정")
        self.btn_unassign = QPushButton("루틴 해제")
        self.btn_close = QPushButton("닫기")
        self.status_label = QLabel("")
        self.btn_unassign.setEnabled(False)
        self._updating_stock_checks = False
        self._updating_routine_checks = False
        self._updating_assigned_checks = False
        self._stock_selection_synced = False
        self._assigned_selection_synced = False
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._assigned_sort_column = -1
        self._assigned_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self._connect_events()

        self.refresh_all()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()

        stock_panel = QWidget()
        stock_layout = QVBoxLayout()
        stock_header_layout = QHBoxLayout()
        self._setup_stock_table()
        stock_header_layout.addWidget(QLabel("루틴 지정 가능 종목"))
        stock_header_layout.addStretch(1)
        stock_header_layout.addWidget(QLabel("검색"))
        stock_header_layout.addWidget(self.stock_search_input)
        stock_layout.addLayout(stock_header_layout)
        stock_layout.addWidget(self.stock_table)
        stock_panel.setLayout(stock_layout)

        routine_panel = QWidget()
        routine_layout = QVBoxLayout()
        routine_header_layout = QHBoxLayout()
        self._setup_routine_table()
        routine_header_layout.addWidget(QLabel("자동매매 루틴"))
        routine_header_layout.addStretch(1)
        routine_header_layout.addWidget(self.btn_apply)
        routine_layout.addLayout(routine_header_layout)
        routine_layout.addWidget(self.routine_table)
        routine_panel.setLayout(routine_layout)

        top_layout.addWidget(stock_panel, 4)
        top_layout.addWidget(routine_panel, 2)

        assigned_panel = QWidget()
        assigned_layout = QVBoxLayout()
        assigned_header_layout = QHBoxLayout()
        assigned_footer_layout = QHBoxLayout()
        self._setup_assigned_stock_table()
        assigned_header_layout.addWidget(QLabel("선택 루틴 연결 종목"))
        assigned_header_layout.addStretch(1)
        assigned_header_layout.addWidget(self.btn_unassign)
        assigned_layout.addLayout(assigned_header_layout)
        assigned_layout.addWidget(self.assigned_stock_table)

        assigned_footer_layout.setContentsMargins(0, 6, 0, 0)
        assigned_footer_layout.addWidget(self.status_label, 1, Qt.AlignVCenter)
        assigned_footer_layout.addStretch(1)
        assigned_footer_layout.addWidget(self.btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)
        assigned_layout.addLayout(assigned_footer_layout)
        assigned_panel.setLayout(assigned_layout)

        self.status_label.setMinimumHeight(34)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.btn_apply.setMinimumHeight(32)
        self.btn_unassign.setMinimumHeight(32)
        self.btn_close.setMinimumHeight(34)
        self.btn_close.setMinimumWidth(110)
        self.btn_apply.setMinimumWidth(110)
        self.btn_unassign.setMinimumWidth(90)
        self.assigned_stock_table.setMinimumHeight(300)
        self.stock_search_input.setMinimumWidth(240)

        main_layout.addLayout(top_layout, 3)
        main_layout.addWidget(assigned_panel, 4)
        self.setLayout(main_layout)

    def _configure_fixed_fit_columns(
        self,
        table: QTableWidget,
        fixed_widths: dict[int, int],
        stretch_column: int | None,
        min_section_width: int = 44,
    ) -> None:
        """
        정보 표시 영역과 컬럼 폭이 빈틈없이 맞도록 설정한다.
        """
        header = table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setCascadingSectionResizes(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(min_section_width)
        header.setDefaultAlignment(Qt.AlignCenter)

        old_handler = getattr(table, "_first_column_width_restore_handler", None)
        if old_handler is not None:
            try:
                header.sectionResized.disconnect(old_handler)
            except Exception:
                pass
            table._first_column_width_restore_handler = None

        for col in range(table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            if col in fixed_widths:
                header.resizeSection(col, fixed_widths[col])
                table.setColumnWidth(col, fixed_widths[col])

        if stretch_column is not None:
            header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.verticalHeader().setSectionsMovable(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.verticalHeader().setMinimumWidth(40)
        table.verticalHeader().setMaximumWidth(40)
        table.verticalHeader().setFixedWidth(40)
        
    def _setup_stock_table(self) -> None:
        headers = ["선택", "종목코드", "종목명", "현재 루틴", "운영상태"]
        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        self._configure_fixed_fit_columns(
            self.stock_table,
            fixed_widths={0: 42, 1: 105, 3: 150, 4: 120},
            stretch_column=2,
            min_section_width=34,
        )
        self.stock_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.stock_table))
        self.stock_table.horizontalHeader().setSectionsClickable(True)
        self.stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _setup_routine_table(self) -> None:
        headers = ["선택", "루틴명"]
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        self._configure_fixed_fit_columns(
            self.routine_table,
            fixed_widths={0: 44},
            stretch_column=1,
            min_section_width=34,
        )
        self.routine_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.routine_table))
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)

    def _setup_assigned_stock_table(self) -> None:
        headers = ["선택", "코드", "종목", "운영", "상태", "보유", "평단", "현재가", "미수", "미도", "수익률"]
        self.assigned_stock_table.setColumnCount(len(headers))
        self.assigned_stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.assigned_stock_table)
        self._configure_fixed_fit_columns(
            self.assigned_stock_table,
            fixed_widths={
                0: 46,   # 선택: 헤더 글자 + 체크박스
                1: 74,   # 코드
                2: 160,  # 종목: 기본 폭, 실제 남는 폭은 Stretch로 자동 보정
                3: 58,   # 운영
                4: 120,  # 상태
                5: 72,   # 보유: 주식수
                6: 92,   # 평단: 금액
                7: 92,   # 현재가: 금액
                8: 75,   # 미수: 주식수
                9: 75,   # 미도: 주식수
                10: 75,  # 수익률
            },
            stretch_column=2,
            min_section_width=34,
        )
        self.assigned_stock_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.assigned_stock_table))
        self.assigned_stock_table.horizontalHeader().setSectionsClickable(True)
        self.assigned_stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.assigned_stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.assigned_stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.assigned_stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.assigned_stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.stock_search_input.textChanged.connect(self.load_stock_table)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemChanged.connect(self.on_stock_check_changed)
        self.stock_table.itemClicked.connect(self.on_stock_item_clicked)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.customContextMenuRequested.connect(self.show_stock_table_context_menu)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.routine_table.itemChanged.connect(self.on_routine_check_changed)
        self.routine_table.itemClicked.connect(self.on_routine_item_clicked)
        self.routine_table.itemSelectionChanged.connect(self.load_selected_routine_stocks)
        self.assigned_stock_table.horizontalHeader().sectionClicked.connect(self.sort_assigned_stock_table_by_column)
        self.assigned_stock_table.itemChanged.connect(self.on_assigned_stock_check_changed)
        self.assigned_stock_table.itemClicked.connect(self.on_assigned_stock_item_clicked)
        self.assigned_stock_table.itemSelectionChanged.connect(self.on_assigned_stock_selection_changed)
        self.assigned_stock_table.customContextMenuRequested.connect(self.show_assigned_stock_table_context_menu)
        self.btn_apply.clicked.connect(self.apply_routines_to_checked_stocks)
        self.btn_unassign.clicked.connect(self.unassign_checked_stocks_from_selected_routine)
        self.btn_close.clicked.connect(self.close)

    def refresh_all(self) -> None:
        self.load_stock_table()
        self.load_routine_table()
        self.assigned_stock_table.setRowCount(0)
        self.btn_unassign.setEnabled(False)

        if self.target_stocks:
            self.select_target_stocks()
        else:
            self.show_status("")

    def show_status(self, message: str, timeout_ms: int = 5000) -> None:
        display_message = f"※ {message}" if message else ""
        self.status_label.setText(display_message)
        parent = self.parent()
        main_window = parent.parent() if parent is not None and hasattr(parent, "parent") else None
        if main_window is not None and hasattr(main_window, "statusBar"):
            try:
                main_window.statusBar().showMessage(display_message, timeout_ms)
            except Exception:
                pass

    def sort_stock_table_by_column(self, column: int) -> None:
        self._stock_sort_order = next_sort_order(self._stock_sort_column, column, self._stock_sort_order)
        self._stock_sort_column = column
        self.stock_table.sortItems(column, self._stock_sort_order)
        self.stock_table.horizontalHeader().setSortIndicator(column, self._stock_sort_order)

    def sort_routine_table_by_column(self, column: int) -> None:
        self._routine_sort_order = next_sort_order(self._routine_sort_column, column, self._routine_sort_order)
        self._routine_sort_column = column
        self.routine_table.sortItems(column, self._routine_sort_order)
        self.routine_table.horizontalHeader().setSortIndicator(column, self._routine_sort_order)
        self.load_selected_routine_stocks()

    def sort_assigned_stock_table_by_column(self, column: int) -> None:
        self._assigned_sort_order = next_sort_order(self._assigned_sort_column, column, self._assigned_sort_order)
        self._assigned_sort_column = column
        self.assigned_stock_table.sortItems(column, self._assigned_sort_order)
        self.assigned_stock_table.horizontalHeader().setSortIndicator(column, self._assigned_sort_order)

    def _apply_saved_stock_sort(self) -> None:
        if self._stock_sort_column >= 0 and self._stock_sort_column < self.stock_table.columnCount():
            self.stock_table.sortItems(self._stock_sort_column, self._stock_sort_order)
            self.stock_table.horizontalHeader().setSortIndicator(self._stock_sort_column, self._stock_sort_order)

    def _apply_saved_routine_sort(self) -> None:
        if self._routine_sort_column >= 0 and self._routine_sort_column < self.routine_table.columnCount():
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)
            self.routine_table.horizontalHeader().setSortIndicator(self._routine_sort_column, self._routine_sort_order)

    def _apply_saved_assigned_sort(self) -> None:
        if self._assigned_sort_column >= 0 and self._assigned_sort_column < self.assigned_stock_table.columnCount():
            self.assigned_stock_table.sortItems(self._assigned_sort_column, self._assigned_sort_order)
            self.assigned_stock_table.horizontalHeader().setSortIndicator(self._assigned_sort_column, self._assigned_sort_order)

    def load_stock_table(self) -> None:
        keyword_text = self.stock_search_input.text().strip().lower() if hasattr(self, "stock_search_input") else ""
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]
        stocks = read_base_stocks()

        # 이 창의 상단 목록은 "신규 루틴 지정 가능 종목"만 표시한다.
        # 이미 어떤 루틴이든 지정된 종목은 하단 "선택 루틴 연결 종목"에서 관리한다.
        allowed_stocks: list[dict[str, object]] = []
        for stock in stocks:
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            if not code or not name:
                continue

            routines = stock.get("routines", [])
            routine_list = [
                str(item).strip()
                for item in routines
                if str(item).strip()
            ] if isinstance(routines, list) else []

            if routine_list:
                continue

            can_process, _ = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
            if can_process:
                allowed_stocks.append(stock)
        stocks = allowed_stocks

        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            code = str(stock.get("code", "")).strip().lower()
            name = str(stock.get("name", "")).strip().lower()
            routines = stock.get("routines", [])
            routine_text = ",".join(str(item).strip().lower() for item in routines) if isinstance(routines, list) else str(routines).lower()
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            current_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(
                str(stock.get("code", "")).strip(),
                str(stock.get("name", "")).strip(),
                current_routine,
            ).lower()
            validation = str(stock.get("validation_status", "")).strip().lower()
            searchable_values = [code, name, routine_text, operation_status, validation]
            return any(keyword in value for value in searchable_values)

        if keywords:
            filtered: list[dict[str, object]] = []
            added_keys: set[tuple[str, str]] = set()
            for keyword in keywords:
                for stock in stocks:
                    key = (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip())
                    if key in added_keys:
                        continue
                    if stock_matches(stock, keyword):
                        filtered.append(stock)
                        added_keys.add(key)
            stocks = filtered

        previously_checked = {
            (code, name)
            for code, name, _ in self.checked_stocks()
        } if self.stock_table.rowCount() else set()
        target_keys = set(self.target_stocks)
        checked_keys = previously_checked | target_keys

        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        self.stock_table.setRowCount(len(stocks))

        for row, stock in enumerate(stocks):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            current_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(code, name, current_routine)

            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked if (code, name) in checked_keys else Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self.stock_table.setItem(row, 0, check_item)

            values = [code, name, current_routine, operation_status]
            for offset, value in enumerate(values, start=1):
                if offset == 4:
                    if value == "미지정":
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignCenter)
                    elif value in ("미생성", "오류"):
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    else:
                        item = create_auto_trade_status_item(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                self.stock_table.setItem(row, offset, item)

        self._apply_saved_stock_sort()
        self.stock_table.clearSelection()
        self.stock_table.blockSignals(False)
        self._updating_stock_checks = False
        self.sync_routine_with_checked_stocks()

    def load_routine_table(self) -> None:
        routine_dirs = get_routine_dirs()

        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            self.routine_table.setColumnCount(2)
            self.routine_table.setHorizontalHeaderLabels(["선택", "루틴명"])
            self.routine_table.setRowCount(len(routine_dirs))

            for row, routine_dir in enumerate(routine_dirs):
                display_name = routine_display_name(routine_dir)

                check_item = QTableWidgetItem("")
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                check_item.setCheckState(Qt.Unchecked)
                check_item.setTextAlignment(Qt.AlignCenter)
                self.routine_table.setItem(row, 0, check_item)

                name_item = QTableWidgetItem(display_name)
                name_item.setTextAlignment(Qt.AlignCenter)
                self.routine_table.setItem(row, 1, name_item)

            apply_plain_table_header(self.routine_table)
            self._configure_fixed_fit_columns(
                self.routine_table,
                fixed_widths={0: 54},
                stretch_column=1,
                min_section_width=54,
            )
            self.routine_table.horizontalHeader().setSectionsClickable(True)
            self.routine_table.horizontalHeader().setSortIndicatorShown(True)
            self._apply_saved_routine_sort()
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False

    def stock_from_row(self, row: int) -> tuple[str, str, list[str]] | None:
        code_item = self.stock_table.item(row, 1)
        name_item = self.stock_table.item(row, 2)
        if code_item is None or name_item is None:
            return None

        code = code_item.text().strip()
        name = name_item.text().strip()
        stocks = read_base_stocks()
        stock_by_key = {
            (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip()): stock
            for stock in stocks
        }
        stock = stock_by_key.get((code, name), {})
        routines_raw = stock.get("routines", [])
        routines = [str(item).strip() for item in routines_raw] if isinstance(routines_raw, list) else []
        return code, name, routines

    def checked_stocks(self) -> list[tuple[str, str, list[str]]]:
        result: list[tuple[str, str, list[str]]] = []
        for row in range(self.stock_table.rowCount()):
            check_item = self.stock_table.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.Checked:
                continue

            stock = self.stock_from_row(row)
            if stock is not None:
                result.append(stock)

        return result

    def checked_stock_common_routine(self) -> str:
        checked = self.checked_stocks()
        if not checked:
            return ""

        common: str | None = None
        for _, _, routines in checked:
            routine_name = routines[0] if routines else ""
            if common is None:
                common = routine_name
            elif common != routine_name:
                return ""
        return common or ""

    def clear_routine_checks(self) -> None:
        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                item = self.routine_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False

    def set_checked_routine_by_name(self, routine_name: str) -> None:
        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(row, 0)
                name_item = self.routine_table.item(row, 1)
                if check_item is None or name_item is None:
                    continue
                checked = name_item.text().strip() == routine_name
                check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                if checked:
                    self.routine_table.selectRow(row)
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False
        self.load_selected_routine_stocks()

    def sync_routine_with_checked_stocks(self) -> None:
        """체크된 종목 수만 상태에 표시한다.

        좌측 종목 체크는 루틴 지정 대상 선택만 의미한다.
        우측 루틴 표는 새로 지정할 루틴을 사용자가 직접 선택해야 하므로,
        좌측 종목의 현재 루틴을 자동 체크하지 않는다.
        """
        checked = self.checked_stocks()
        if len(checked) == 1:
            code, name, _ = checked[0]
            self.show_status(f"루틴 지정 대상: {code} {name}")
        elif checked:
            self.show_status(f"루틴 지정 대상: {len(checked)}개")
        else:
            self.show_status("")

    def select_target_stock(self) -> None:
        self.select_target_stocks()

    def select_target_stocks(self) -> None:
        targets = set(self.target_stocks)
        if not targets:
            self.show_status("")
            return

        found_rows: list[int] = []
        found_stocks: list[tuple[str, str, list[str]]] = []
        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        try:
            for row in range(self.stock_table.rowCount()):
                stock = self.stock_from_row(row)
                if stock is None:
                    continue
                code, name, _ = stock
                if (code, name) not in targets:
                    continue
                check_item = self.stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked)
                    found_rows.append(row)
                    found_stocks.append(stock)
            self.stock_table.clearSelection()
            for row in found_rows:
                self.stock_table.selectRow(row)
        finally:
            self.stock_table.blockSignals(False)
            self._updating_stock_checks = False

        if not found_rows:
            self.show_status("선택 종목 중 루틴 지정 가능한 종목을 찾지 못했습니다.")
            return

        self.stock_table.scrollToItem(
            self.stock_table.item(found_rows[0], 1),
            QAbstractItemView.PositionAtCenter,
        )
        self.sync_routine_with_checked_stocks()

    def _set_stock_rows_checked(self, rows: set[int], checked: bool) -> None:
        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        try:
            for row in rows:
                if row < 0 or row >= self.stock_table.rowCount():
                    continue
                check_item = self.stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.stock_table.blockSignals(False)
            self._updating_stock_checks = False
        self.sync_routine_with_checked_stocks()

    def _set_all_stock_checks(self, checked: bool) -> None:
        self._set_stock_rows_checked(set(range(self.stock_table.rowCount())), checked)

    def on_stock_selection_changed(self) -> None:
        if self._updating_stock_checks:
            return

        selected_rows = {index.row() for index in self.stock_table.selectionModel().selectedRows()}
        if len(selected_rows) <= 1:
            self._stock_selection_synced = False
            return

        self._stock_selection_synced = True
        self._set_stock_rows_checked(selected_rows, True)

    def show_stock_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.stock_table.viewport().mapToGlobal(pos))

        if selected_action == action_select_all:
            self._set_all_stock_checks(True)
        elif selected_action == action_clear_all:
            self._set_all_stock_checks(False)

    def on_stock_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.sync_routine_with_checked_stocks()
            return

        if self._stock_selection_synced:
            self._stock_selection_synced = False
            # 드래그/범위 선택 직후 발생하는 클릭 이벤트는 체크 토글로 해석하지 않는다.
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            selected_rows = {index.row() for index in self.stock_table.selectionModel().selectedRows()}
            if selected_rows:
                self._set_stock_rows_checked(selected_rows, True)
            return

        check_item = self.stock_table.item(item.row(), 0)
        if check_item is None:
            return
        next_state = Qt.Unchecked if check_item.checkState() == Qt.Checked else Qt.Checked
        check_item.setCheckState(next_state)

    def on_stock_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_stock_checks or item.column() != 0:
            return
        self.sync_routine_with_checked_stocks()

    def on_routine_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_routine_checks or item.column() != 0:
            return

        if item.checkState() != Qt.Checked:
            return

        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(row, 0)
                if check_item is not None and check_item is not item:
                    check_item.setCheckState(Qt.Unchecked)
            self.routine_table.selectRow(item.row())
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False
        self.load_selected_routine_stocks()

    def on_routine_item_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        name_item = self.routine_table.item(row, 1)
        if name_item is None:
            return
        self.set_checked_routine_by_name(name_item.text().strip())

    def checked_routines(self) -> list[tuple[str, Path]]:
        routines: list[tuple[str, Path]] = []
        routine_dir_by_name = {routine_display_name(path): path for path in get_routine_dirs()}

        for row in range(self.routine_table.rowCount()):
            check_item = self.routine_table.item(row, 0)
            routine_item = self.routine_table.item(row, 1)
            if check_item is None or routine_item is None:
                continue

            if check_item.checkState() == Qt.Checked:
                routine_name = routine_item.text().strip()
                routine_dir = routine_dir_by_name.get(routine_name)
                if routine_dir is not None:
                    routines.append((routine_name, routine_dir))

        return routines

    def selected_routine_for_detail(self) -> tuple[str, Path] | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        row: int | None = selected_rows[0].row() if len(selected_rows) == 1 else None

        if row is None:
            for target_row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(target_row, 0)
                if check_item is not None and check_item.checkState() == Qt.Checked:
                    row = target_row
                    break

        if row is None:
            return None

        routine_item = self.routine_table.item(row, 1)
        if routine_item is None:
            return None

        routine_name = routine_item.text().strip()
        routine_dir_by_name = {routine_display_name(path): path for path in get_routine_dirs()}
        routine_dir = routine_dir_by_name.get(routine_name)
        if routine_dir is None:
            return None

        return routine_name, routine_dir

    def assigned_stock_name_display(self, name: str) -> str:
        """선택 루틴 연결 종목 표의 종목명은 최대 12자까지만 표시한다."""
        clean_name = str(name).strip()
        if len(clean_name) <= 12:
            return clean_name
        return clean_name[:12]

    def load_selected_routine_stocks(self) -> None:
        selected_routine = self.selected_routine_for_detail()
        self.assigned_stock_table.blockSignals(True)
        self.assigned_stock_table.setRowCount(0)

        if selected_routine is None:
            self.assigned_stock_table.blockSignals(False)
            self.btn_unassign.setEnabled(False)
            return

        routine_name, routine_dir = selected_routine
        stocks = read_base_stocks()
        assigned = []

        for stock in stocks:
            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            if routine_name in routine_list:
                assigned.append(stock)

        self.assigned_stock_table.setRowCount(len(assigned))

        for row, stock in enumerate(assigned):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            summary = self.runtime_assigned_stock_summary(routine_dir, code, name)
            status = summary["status"]

            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self.assigned_stock_table.setItem(row, 0, check_item)

            display_name = self.assigned_stock_name_display(name)
            values = [
                code,
                display_name,
                summary["operation"],
                status,
                summary["holding_summary"],
                summary["avg_price"],
                summary["current_price"],
                summary["buy_pending_qty"],
                summary["sell_pending_qty"],
                summary["pnl_summary"],
            ]

            for offset, value in enumerate(values, start=1):
                if offset == 4 and value not in ("-", "오류"):
                    try:
                        item = create_auto_trade_status_item(value)
                    except Exception:
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)

                if offset == 3:
                    operation_color = summary.get("operation_color", "#000000")
                    item.setForeground(QColor(operation_color))

                if offset == 2 and display_name != name:
                    item.setToolTip(name)

                self.assigned_stock_table.setItem(row, offset, item)

        self._apply_saved_assigned_sort()
        self.assigned_stock_table.blockSignals(False)
        self.btn_unassign.setEnabled(False)

    def runtime_status_text(self, routine_dir: Path, code: str, name: str) -> str:
        return self.runtime_assigned_stock_summary(routine_dir, code, name)["status"]

    def runtime_assigned_stock_summary(self, routine_dir: Path, code: str, name: str) -> dict[str, str]:
        """
        선택 루틴 연결 종목의 표시 정보를 중앙 stocks/종목폴더 기준으로 읽는다.

        routine_dir 인자는 기존 시그니처 호환용이며,
        구형 루틴폴더 내부 종목폴더는 더 이상 조회하지 않는다.
        """
        empty_summary = {
            "operation": "-",
            "operation_color": "#000000",
            "status": "-",
            "holding_qty": "-",
            "holding_summary": "-",
            "avg_price": "-",
            "current_price": "-",
            "buy_pending_qty": "-",
            "sell_pending_qty": "-",
            "pnl_rate": "-",
            "pnl_summary": "-",
        }

        try:
            repo = stock_repository_factory()
            stock_dir = repo.resolve_stock_dir(code, name)
        except Exception:
            return empty_summary

        state_path = stock_dir / "state.json"
        config_path = stock_dir / "config.json"

        if not state_path.exists():
            return empty_summary

        try:
            state = read_json_dict(state_path)
        except Exception:
            error_summary = dict(empty_summary)
            error_summary["operation"] = "오류"
            error_summary["status"] = "오류"
            return error_summary

        if not isinstance(state, dict):
            return empty_summary

        try:
            config = read_json_dict(config_path) if config_path.exists() else {}
        except Exception:
            config = {}

        if not isinstance(config, dict):
            config = {}

        raw_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

        if raw_mode == "CONTINUOUS":
            operation = "수동"
            operation_color = "#8A2BE2"
        else:
            operation = "시간"
            try:
                operation_color = "#0066CC" if schedule_override_enabled(config) else "#000000"
            except Exception:
                operation_color = "#000000"

        raw_status = str(state.get("status", "-")).strip()
        status = display_status_text_for_gui(raw_status)

        holding_qty = safe_int_value(state.get("holding_qty", 0))
        avg_price_value = safe_float_value(state.get("avg_price"), 0.0)
        current_price_value = safe_float_value(state.get("current_price"), 0.0)

        if holding_qty > 0 and avg_price_value > 0 and current_price_value > 0:
            pnl_rate = f"{((current_price_value - avg_price_value) / avg_price_value * 100):+.2f}"
        else:
            pnl_rate = "-"

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        buy_pending_text = f"{buy_pending_qty:,}" if isinstance(buy_pending_qty, int) else str(buy_pending_qty)
        sell_pending_text = f"{sell_pending_qty:,}" if isinstance(sell_pending_qty, int) else str(sell_pending_qty)

        holding_summary = f"{holding_qty:,}"

        return {
            "operation": operation,
            "operation_color": operation_color,
            "status": status,
            "holding_qty": str(holding_qty),
            "holding_summary": holding_summary,
            "avg_price": format_number_value(avg_price_value),
            "current_price": format_number_value(current_price_value),
            "buy_pending_qty": buy_pending_text,
            "sell_pending_qty": sell_pending_text,
            "pnl_rate": pnl_rate,
            "pnl_summary": pnl_rate,
        }


    def _checked_assigned_stock_count(self) -> int:
        checked_count = 0
        for row in range(self.assigned_stock_table.rowCount()):
            check_item = self.assigned_stock_table.item(row, 0)
            if check_item is not None and check_item.checkState() == Qt.Checked:
                checked_count += 1
        return checked_count

    def _set_assigned_rows_checked(self, rows: set[int], checked: bool) -> None:
        self._updating_assigned_checks = True
        self.assigned_stock_table.blockSignals(True)
        try:
            for row in rows:
                if row < 0 or row >= self.assigned_stock_table.rowCount():
                    continue
                check_item = self.assigned_stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.assigned_stock_table.blockSignals(False)
            self._updating_assigned_checks = False
        self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)

    def _set_all_assigned_checks(self, checked: bool) -> None:
        self._set_assigned_rows_checked(set(range(self.assigned_stock_table.rowCount())), checked)

    def on_assigned_stock_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_assigned_checks or item.column() != 0:
            return
        self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)

    def on_assigned_stock_selection_changed(self) -> None:
        if self._updating_assigned_checks:
            return

        selected_rows = {index.row() for index in self.assigned_stock_table.selectionModel().selectedRows()}
        if len(selected_rows) <= 1:
            self._assigned_selection_synced = False
            return

        self._assigned_selection_synced = True
        self._set_assigned_rows_checked(selected_rows, True)

    def show_assigned_stock_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.assigned_stock_table.viewport().mapToGlobal(pos))

        if selected_action == action_select_all:
            self._set_all_assigned_checks(True)
        elif selected_action == action_clear_all:
            self._set_all_assigned_checks(False)

    def on_assigned_stock_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)
            return

        if self._assigned_selection_synced:
            self._assigned_selection_synced = False
            # 드래그/범위 선택 직후 발생하는 클릭 이벤트는 체크 토글로 해석하지 않는다.
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            selected_rows = {index.row() for index in self.assigned_stock_table.selectionModel().selectedRows()}
            if selected_rows:
                self._set_assigned_rows_checked(selected_rows, True)
            return

        check_item = self.assigned_stock_table.item(item.row(), 0)
        if check_item is None:
            return

        next_state = Qt.Unchecked if check_item.checkState() == Qt.Checked else Qt.Checked
        check_item.setCheckState(next_state)

    def apply_routines_to_checked_stocks(self) -> None:
        selected = self.checked_stocks()
        if not selected:
            self.show_status("루틴을 지정할 종목을 체크하세요.")
            return

        selected_routines = self.checked_routines()
        if not selected_routines:
            self.show_status("지정할 루틴을 체크하세요.")
            return

        if len(selected_routines) != 1:
            self.show_status("지정할 루틴은 1개만 선택하세요.")
            return

        selected_routine_name, selected_routine_dir = selected_routines[0]
        selected_routine_names = [selected_routine_name]
        applied_items: list[str] = []
        created_paths: list[str] = []
        blocked_items: list[dict[str, object]] = []
        skipped_items: list[str] = []

        for code, name, existing_routines in selected:
            existing_routine_list = [
                str(item).strip()
                for item in existing_routines
                if str(item).strip()
            ] if isinstance(existing_routines, list) else []

            if existing_routine_list:
                skipped_items.append(f"{code} {name}: 이미 루틴 지정됨({', '.join(existing_routine_list)})")
                continue

            can_process, guard_info = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
            if not can_process:
                blocked_items.append(guard_info)
                continue

            if not is_valid_stock_code(code):
                skipped_items.append(f"{code} {name}: 종목코드 오류")
                continue

            library_stock = find_library_stock_by_code(code)
            if library_stock is None or library_stock.get("name", "").strip() != name:
                skipped_items.append(f"{code} {name}: 라이브러리 불일치")
                continue

            final_routines = selected_routine_names

            if not update_base_stock_routines(code, name, final_routines):
                skipped_items.append(f"{code} {name}: 기초종목.txt 갱신 실패")
                continue

            # 중앙 stocks/ 구조 사용:
            # 과거처럼 selected_routine_dir 아래에 코드_종목명 폴더를 만들지 않는다.
            try:
                stock_dir = stock_repository_factory().ensure_stock_folder(
                    code,
                    name,
                    routine=selected_routine_name,
                )
            except Exception:
                skipped_items.append(f"{code} {name}: 중앙 stocks 폴더 준비 실패")
                continue

            created_paths.append(str(stock_dir.relative_to(PROJECT_ROOT)))
            ensure_single_real_trade_routine_for_stock(code, name, selected_routine_name)
            applied_items.append(f"{code},{name}({selected_routine_name})")

        report_path = write_blocked_action_report(
            "루틴 지정",
            blocked_items,
            target_routine=selected_routine_name,
        )

        if not applied_items:
            message = "루틴을 지정한 종목이 없습니다."
            if blocked_items:
                message += f"\n\n처리 불가: {len(blocked_items)}개"
                if report_path is not None:
                    message += f"\n리포트: {report_path}"
            if skipped_items:
                message += f"\n처리 제외: {len(skipped_items)}개"
            QMessageBox.information(self, "루틴 지정 결과", message)
            self.show_status("루틴을 지정한 종목이 없습니다.")
            return

        append_changelog(
            "UPDATE",
            "기초종목.txt",
            f"매매루틴 지정: {' / '.join(applied_items)} -> {', '.join(selected_routine_names)}",
        )

        if created_paths:
            append_changelog(
                "ADD",
                "종목별 저장 구조",
                f"종목 폴더 및 기본 파일 확인/생성: {' / '.join(created_paths)}",
            )

        self.load_stock_table()
        self.load_selected_routine_stocks()
        self.clear_routine_checks()

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_stock_table"):
            try:
                parent.refresh_stock_table()
            except Exception:
                pass
            main_window = parent.parent() if hasattr(parent, "parent") else None
            if main_window is not None and hasattr(main_window, "refresh_all"):
                try:
                    main_window.refresh_all()
                except Exception:
                    pass

        result_lines = [
            f"{len(applied_items)}개 종목이 {selected_routine_name}에 연결되었습니다."
        ]
        if blocked_items:
            result_lines.append(f"처리 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_items:
            result_lines.append(f"처리 제외: {len(skipped_items)}개")

        QMessageBox.information(self, "루틴 지정 결과", "\n".join(result_lines))
        self.show_status(
            f"{len(applied_items)}개 종목이 {selected_routine_name}에 연결되었습니다."
        )

    def unassign_checked_stocks_from_selected_routine(self) -> None:
        selected_routine = self.selected_routine_for_detail()
        if selected_routine is None:
            self.show_status("해제할 루틴을 선택하세요.")
            return

        routine_name, _ = selected_routine
        checked_stocks: list[tuple[str, str]] = []

        for row in range(self.assigned_stock_table.rowCount()):
            check_item = self.assigned_stock_table.item(row, 0)
            code_item = self.assigned_stock_table.item(row, 1)
            name_item = self.assigned_stock_table.item(row, 2)
            if check_item is None or code_item is None or name_item is None:
                continue
            if check_item.checkState() == Qt.Checked:
                checked_stocks.append((code_item.text().strip(), name_item.text().strip()))

        if not checked_stocks:
            self.show_status("루틴 해제할 종목을 체크하세요.")
            return

        stock_lookup = {
            (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip()): stock
            for stock in read_base_stocks()
        }

        removable_items: list[tuple[str, str]] = []
        blocked_items: list[dict[str, object]] = []
        skipped_items: list[str] = []

        for code, name in checked_stocks:
            stock = stock_lookup.get((code, name))
            if not stock:
                skipped_items.append(f"{code} {name}: 기초종목.txt에서 종목을 찾지 못했습니다.")
                continue

            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            if routine_name not in routine_list:
                skipped_items.append(f"{code} {name}: 선택 루틴에 연결되어 있지 않음")
                continue

            can_process, guard_info = routine_action_reasons_for_stock(code, name, allow_unassigned=False)
            if not can_process:
                blocked_items.append(guard_info)
                continue

            removable_items.append((code, name))

        if not removable_items and not blocked_items:
            QMessageBox.information(
                self,
                "루틴 해제 결과",
                "루틴 해제할 수 있는 종목이 없습니다."
                + (f"\n처리 제외: {len(skipped_items)}개" if skipped_items else ""),
            )
            self.show_status("루틴 해제할 수 있는 종목이 없습니다.")
            return

        confirm_dialog = RoutineUnassignConfirmDialog(
            routine_name=routine_name,
            removable_items=removable_items,
            blocked_items=blocked_items,
            parent=self,
        )
        if confirm_dialog.exec_() != QDialog.Accepted:
            self.show_status("루틴 해제를 취소했습니다.")
            return

        removed_items: list[str] = []
        for code, name in removable_items:
            stock = stock_lookup.get((code, name))
            if not stock:
                skipped_items.append(f"{code} {name}: 기초종목.txt에서 종목을 찾지 못했습니다.")
                continue

            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            new_routines = [item for item in routine_list if item != routine_name]

            if update_base_stock_routines(code, name, new_routines):
                ensure_single_real_trade_routine_for_stock(code, name)
                removed_items.append(f"{code},{name}")
            else:
                skipped_items.append(f"{code} {name}: 기초종목.txt 갱신 실패")

        report_path = write_blocked_action_report(
            "루틴 해제",
            blocked_items,
            target_routine=routine_name,
        )

        if removed_items:
            append_changelog(
                "UPDATE",
                "기초종목.txt",
                f"매매루틴 해제: {routine_name} -> {' / '.join(removed_items)}",
            )

        self.load_stock_table()
        self.load_selected_routine_stocks()
        self.clear_routine_checks()

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_stock_table"):
            try:
                parent.refresh_stock_table()
            except Exception:
                pass
            main_window = parent.parent() if hasattr(parent, "parent") else None
            if main_window is not None and hasattr(main_window, "refresh_all"):
                try:
                    main_window.refresh_all()
                except Exception:
                    pass

        result_lines = [
            f"{len(removed_items)}개 종목의 {routine_name} 연결이 해제되었습니다."
        ]
        if blocked_items:
            result_lines.append(f"해제 불가 : {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_items:
            result_lines.append(f"처리 제외: {len(skipped_items)}개")

        QMessageBox.information(self, "루틴 해제 결과", "\n".join(result_lines))
        self.show_status(
            f"{len(removed_items)}개 종목의 {routine_name} 연결이 해제되었습니다."
        )

