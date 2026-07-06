# -*- coding: utf-8 -*-

"""
gui_stock_register_window.py

MASTER_SPEC v1.1 Windows GUI Edition 기준
Windows GUI 창 클래스 정의 파일.

현재 단계:
- 메인 윈도우 안정 버전
- 자동매매 루틴 폴더 자동 탐색
- __pycache__ 제외
- budget.json 이 있는 폴더만 루틴으로 인정
- 키움 로그인, 주문, 실시간 수신 기능은 아직 연결하지 않음
- 수동등록/검색등록 검증 강화
- 신규 종목은 stock_library.json 검색 결과에서만 등록 허용
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QFrame, 
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QTextEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from integrity_checker import (
    run_integrity_checks,
    write_invalid_items_log,
)
from gui_table_utils import next_sort_order
from gui_centered_checkbox_delegate import CenteredCheckBoxDelegate
from gui_styles import (
    apply_plain_table_header,
    apply_selected_routine_label_style,
)
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_stock_data import (
    active_routine_for_stock,
    assigned_runtime_dirs_for_stock,
    stock_runtime_dir_for_routine,
)
from gui_order_utils import (
    pending_order_side_quantities,
    order_value,
    order_status_display,
    order_side_display,
    format_number_value,
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
    order_sort_key,
)
from gui_order_status_window import OrderStatusWindow
from gui_log_view_window import LogViewWindow
from gui_integrity_check_window import IntegrityCheckWindow
from gui_blocked_report_window import (
    BlockedActionReportViewDialog,
    blocked_items_preview,
    latest_blocked_action_report_path,
    write_blocked_action_report,
)
from gui_schedule_utils import (
    schedule_config_updates,
    schedule_change_log_text,
    schedule_status_suffix,
)
from gui_schedule_window import (
    ScheduleOperationDialog,
    ScheduleTradeManagementDialog,
)
from gui_config_utils import (
    default_config,
    default_state,
    default_orders,
    ensure_stock_runtime_files,
)
from gui_config_window import show_deferred_config_message
from gui_force_unregister_dialog import ForceUnregisterConfirmDialog
from gui_search_stock_register_dialog import SearchStockRegisterDialog
from gui_routine_assign_window import (
    RoutineAssignWindow,
    RoutineUnassignConfirmDialog,
)
from gui_auto_trade_utils import auto_trade_unregister_category
from gui_review_utils import (
    build_review_required_item,
    compact_time_text,
    pending_order_summary,
    review_required_for_start,
    review_reason_summary,
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
    routine_action_reasons_for_stock,
    classify_routine_assign_targets,
    can_unassign_active_routine_from_stock,
)
from stock_repository import repository as stock_repository_factory
from gui_routine_service import ensure_single_real_trade_routine_for_stock
from runtime_io import (
    read_json_dict,
    read_orders_data,
    write_json_if_missing,
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
    real_trade_enabled,
    trade_permission_display,
    operation_mode_recalculation_target_status,
    operation_text_and_color,
    read_global_schedule,
    schedule_override_enabled,
    scheduled_status_for_now,
    seconds_from_hhmmss,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
    write_global_schedule,
)
from gui_ats_utils import (
    ManualAtsSettingsDialog,
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
    manual_ats_source,
)
from gui_auto_trade_display import (
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    auto_trade_setting_display_status,
    auto_trade_setting_status_color,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    yes_no_display,
    display_status_text_for_gui,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_setting_window import (
    AutoTradeSettingWindow,
    AutoTradeUnregisterConfirmDialog,
    IndividualLiquidationSettingsDialog,
    ProfitLossEarlyCloseDialog,
    StockPolicyOverrideDialog,
    append_changelog,
    append_stock_log,
    assigned_stock_dirs_in_routine,
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_data_inconsistency_reasons,
    auto_trade_setting_early_close_metadata_is_stale,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_effective_liquidation_method,
    auto_trade_setting_has_buy_pending_problem,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_has_unresolved_quantity,
    auto_trade_setting_is_after_regular_end,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_liquidation_completed_today,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_liquidation_result_policy,
    auto_trade_setting_liquidation_text,
    auto_trade_setting_mark_liquidation_result_for_display,
    auto_trade_setting_method_text,
    auto_trade_setting_no_next_step_notice,
    auto_trade_setting_regular_end_seconds,
    auto_trade_setting_server_mismatch_detected,
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_today_date_text,
    auto_trade_setting_trade_started,
    base_stock_routine_assignments,
    clear_auto_close_runtime_metadata,
    clear_early_close_runtime_metadata_only,
    close_method_from_state_or_policy,
    compact_operation_time_range,
    create_auto_trade_situation_item,
    default_operation_policy,
    effective_liquidation_policy_for_config,
    ensure_single_real_trade_routine_for_all_stocks,
    find_library_stock_by_code,
    get_routine_dirs,
    get_stock_dirs_in_routine,
    individual_liquidation_policy_from_config,
    is_review_required_state,
    is_review_required_stock_dir,
    is_stock_assigned_to_routine,
    is_valid_stock_code,
    load_stock_library,
    normalize_base_stock_single_routine_file,
    normalize_stock_code,
    now_text,
    operation_policy_section,
    parse_stock_folder_name,
    read_base_stocks,
    read_operation_policy,
    reset_runtime_orders_for_force_unregister,
    reset_runtime_state_for_force_unregister,
    reset_runtime_statuses_for_program_start,
    restart_initial_review_reason_for_stock,
    routine_display_name,
    short_close_method_text,
    single_routine_list,
    unique_review_reasons,
    update_base_stock_routines,
    validate_base_stock_record,
)



PROJECT_ROOT = Path(__file__).resolve().parent
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"
ARCHIVED_STOCKS_DIR = PROJECT_ROOT / "archived_stocks"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
INVALID_ITEMS_LOG_PATH = PROJECT_ROOT / "invalid_items.log"
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
BLOCKED_ACTION_REPORT_DIR = PROJECT_ROOT / "reports" / "blocked_actions"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
PROGRAM_START_RESET_APPLIED = False







































def stock_runtime_status_for_routine(routine_name: str, code: str, name: str) -> str:
    """
    루틴별 종목 state.json 기준 자동매매 상태를 반환한다.
    """
    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return "대기"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return "감시/대기"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "오류"

    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    return display_status_text_for_gui(raw_status)


def pending_routine_names_for_stock(
    code: str,
    name: str,
    assigned_routines: list[str],
) -> list[str]:
    """
    중앙 종목관리 에는 현재 루틴 등록이 없지만,
    루틴 폴더 안에 종목 저장 폴더가 남아 있는 경우 등록대기로 표시한다.
    """
    assigned_set = {routine.strip() for routine in assigned_routines if routine.strip()}
    pending: list[str] = []

    for routine_dir in get_routine_dirs():
        routine_name = routine_display_name(routine_dir)
        if routine_name in assigned_set:
            continue

        stock_dir = routine_dir / f"{sanitize_path_part(code)}_{sanitize_path_part(name)}"
        if stock_dir.exists():
            pending.append(routine_name)

    return pending









def stock_runtime_dirs_for_stock(code: str, name: str) -> list[tuple[str, Path]]:
    """
    해당 종목이 배정된 중앙 runtime 폴더를 반환한다.
    """
    return assigned_runtime_dirs_for_stock(code, name)



def runtime_delete_block_reasons(stock_dir: Path) -> list[str]:
    """
    종목 삭제 차단 사유를 runtime 상태 기준으로 반환한다.
    """
    reasons: list[str] = []
    state = read_json_dict(stock_dir / "state.json")
    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    if raw_status and raw_status != "STOPPED":
        reasons.append(auto_trade_status_display(raw_status))

    try:
        holding_qty = int(state.get("holding_qty", 0) or 0)
    except Exception:
        holding_qty = 0
    if holding_qty > 0:
        reasons.append(f"보유 {holding_qty}")

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    pending_parts: list[str] = []
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        pending_parts.append(f"매수미결 {buy_pending_qty}")
    elif buy_pending_qty == "?":
        pending_parts.append("매수미결 확인필요")

    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        pending_parts.append(f"매도미결 {sell_pending_qty}")
    elif sell_pending_qty == "?":
        pending_parts.append("매도미결 확인필요")

    reasons.extend(pending_parts)
    return reasons




def routine_status_color(status: str) -> str:
    """
    루틴별 상태 점 색상을 반환한다.

    자동매매설정 창 상태 색상과 같은 팔레트를 사용해 상태별 식별성을 맞춘다.
    """
    normalized = display_status_text_for_gui(status)
    if normalized == "대기":
        return auto_trade_status_color("등록대기")
    if normalized == "운영":
        normalized = "운영중"
    return auto_trade_status_color(normalized)


def create_routine_status_widget(status_lines: list[tuple[str, str]]) -> QWidget:
    """
    연결 루틴 셀에 넣을 상태 위젯을 생성한다.
    색상 점과 루틴명을 분리해 시인성을 높인다.
    """
    container = QWidget()
    layout = QVBoxLayout()
    layout.setContentsMargins(12, 5, 12, 5)
    layout.setSpacing(5)

    if not status_lines:
        label = QLabel("-")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #555555;")
        layout.addWidget(label)
    else:
        for routine_name, status in status_lines:
            line_widget = QWidget()
            line_layout = QHBoxLayout()
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(9)

            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(
                "border-radius: 6px;"
                "border: 1px solid #555555;"
                f"background-color: {routine_status_color(status)};"
            )

            text_label = QLabel(routine_status_display_text(routine_name, status))
            text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            text_label.setStyleSheet("font-weight: 600; padding: 1px 0px;")

            line_layout.addWidget(dot)
            line_layout.addWidget(text_label, 1)
            line_widget.setLayout(line_layout)
            layout.addWidget(line_widget)

    container.setLayout(layout)
    return container


def has_running_routine(code: str, name: str, routines: list[str]) -> tuple[bool, list[str]]:
    """
    선택 종목에 운영중 루틴이 있는지 확인한다.
    """
    running_routines: list[str] = []

    for routine_name in routines:
        status = stock_runtime_status_for_routine(routine_name, code, name)
        if status not in ("감시/대기", "대기"):
            running_routines.append(f"{routine_name}({status})")

    return bool(running_routines), running_routines


def stock_register_unavailable_reason(code: str, name: str) -> tuple[str, str, list[str], list[tuple[str, Path]]]:
    """
    종목등록설정 삭제/등록해제 정책에 따라 선택 종목을 분류한다.

    반환값:
    - category: immediate / force / blocked
    - title: 화면 표시용 종목명
    - reasons: 사유 목록
    - runtime_dirs: 해당 종목의 루틴 runtime 폴더 목록
    """
    runtime_dirs = stock_runtime_dirs_for_stock(code, name)
    title = f"{code} {name}"

    if not runtime_dirs:
        return "immediate", title, ["루틴 연결 없음"], []

    force_reasons: list[str] = []
    blocked_reasons: list[str] = []

    allowed_statuses = {"STOPPED", "STOP", "MONITORING", "WATCHING", ""}
    blocked_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}

    for routine_name, stock_dir in runtime_dirs:
        state = read_json_dict(stock_dir / "state.json")
        raw_status = str(state.get("status", "STOPPED")).strip().upper()
        display_status = display_status_text_for_gui(raw_status)

        try:
            holding_qty = int(state.get("holding_qty", 0) or 0)
        except Exception:
            holding_qty = 0

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

        routine_prefix = f"{routine_name}: "

        if raw_status in blocked_statuses:
            blocked_reasons.append(f"{routine_prefix}{display_status} 상태")
            continue

        if raw_status not in allowed_statuses:
            blocked_reasons.append(f"{routine_prefix}{display_status or '상태확인필요'} 상태")
            continue

        if buy_pending_qty == "?" or sell_pending_qty == "?":
            blocked_reasons.append(f"{routine_prefix}미체결 확인 필요")
            continue

        pending_parts: list[str] = []
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            pending_parts.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            pending_parts.append(f"매도미결 {sell_pending_qty}")

        if holding_qty > 0 or pending_parts:
            force_reason = f"{routine_prefix}{display_status}"
            details: list[str] = []
            if holding_qty > 0:
                details.append(f"보유 {holding_qty}")
            details.extend(pending_parts)
            if details:
                force_reason += f" / {', '.join(details)}"
            force_reasons.append(force_reason)

    if blocked_reasons:
        return "blocked", title, blocked_reasons, runtime_dirs

    if force_reasons:
        return "force", title, force_reasons, runtime_dirs

    return "immediate", title, ["정지/감시중, 보유·미체결 없음"], runtime_dirs






def active_stock_register_status_display(code: str, name: str, routine_name: str) -> str:
    """
    종목등록설정 창의 운영상태 표시용 문구를 반환한다.

    원칙:
    - 루틴 미등록 종목은 미지정으로 표시한다.
    - 루틴 등록 종목은 자동매매설정 창과 동일하게 state.json 상태를 사용자 표시명으로 변환한다.
    - SELL_ONLY 등 내부값은 화면에 직접 노출하지 않는다.
    """
    routine_name = str(routine_name).strip()
    if not routine_name or routine_name == "미등록":
        return "미지정"

    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return "미생성"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return auto_trade_status_display("STOPPED")

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "오류"

    return auto_trade_status_display(state.get("status", "STOPPED"))


class StockRegisterWindow(QDialog):
    """
    종목등록설정 창.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("종목등록설정")
        self.resize(860, 560)

        self.stock_search_input = QLineEdit()
        self.stock_search_input.setPlaceholderText("목록 필터: 코드, 종목명, 루틴명, 상태")
        self.stock_table = QTableWidget()

        self.btn_search_register = QPushButton("검색식등록")
        self.btn_search_register.setEnabled(False)
        self.btn_search_register.setToolTip("키움 조건검색식 연동 단계에서 구현 예정입니다.")
        self.btn_manual_register = QPushButton("수동등록")
        self.btn_manual_register.setToolTip("종목 라이브러리에서 직접 선택 등록합니다.")
        self.btn_routine_assign = QPushButton("매매루틴지정")
        self.btn_integrity_check = QPushButton("무결성검증")
        self.btn_blocked_report = QPushButton("처리불가 리포트")
        self.btn_delete_stock = QPushButton("선택 종목 삭제")
        self.btn_delete_stock.setEnabled(False)
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        self._setup_stock_table()

        buttons = [
            self.btn_search_register,
            self.btn_manual_register,
            self.btn_routine_assign,
            self.btn_integrity_check,
            self.btn_blocked_report,
            self.btn_delete_stock,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("중앙 종목 관리"))
        header_layout.addStretch(1)
        header_layout.addWidget(QLabel("검색"))
        header_layout.addWidget(self.stock_search_input)
        self.stock_search_input.setMinimumWidth(360)

        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.stock_table)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_stock_table(self) -> None:
        headers = [
            "종목코드",
            "종목명",
            "연결 루틴",
            "운영상태",
            "검증상태",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.stock_table.horizontalHeader().setStretchLastSection(True)
        self.stock_table.setShowGrid(True)
        self.stock_table.setColumnWidth(0, 105)
        self.stock_table.setColumnWidth(1, 165)
        self.stock_table.setColumnWidth(2, 250)
        self.stock_table.setColumnWidth(3, 120)
        self.stock_table.setColumnWidth(4, 120)
        self.stock_table.setWordWrap(False)
        self.stock_table.verticalHeader().setDefaultSectionSize(42)
        self.stock_table.setStyleSheet(
            "QHeaderView::section { border-bottom: 1px solid #c8c8c8; }"
            "QTableWidget { gridline-color: #d6d6d6; }"
        )
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setSortingEnabled(True)
        self.stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.btn_close.clicked.connect(self.close)
        self.btn_manual_register.clicked.connect(self.open_manual_register_dialog)
        self.btn_routine_assign.clicked.connect(self.open_routine_assign_window)
        self.btn_integrity_check.clicked.connect(self.open_integrity_check_window)
        self.btn_blocked_report.clicked.connect(self.open_latest_blocked_report)
        self.btn_delete_stock.clicked.connect(self.delete_selected_stock)
        self.stock_search_input.textChanged.connect(self.refresh_stock_table)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.itemClicked.connect(self.on_stock_table_item_clicked)
        self.stock_table.itemDoubleClicked.connect(self.open_routine_assign_for_stock)
        self.stock_table.customContextMenuRequested.connect(self.show_stock_table_context_menu)


    def on_stock_selection_changed(self) -> None:
        selected_rows = self.stock_table.selectionModel().selectedRows()
        self.btn_delete_stock.setEnabled(len(selected_rows) >= 1)

    def on_stock_table_item_clicked(self, item: QTableWidgetItem) -> None:
        """
        종목등록설정 창에서 종목 행을 1회 클릭했을 때의 보조 처리.

        itemClicked 시그널 연결은 유지하되, 실제 삭제 버튼 활성화 여부는
        현재 선택 상태를 기준으로 다시 계산한다.
        더블클릭으로 매매루틴지정 창을 여는 기존 동작은 변경하지 않는다.
        """
        self.on_stock_selection_changed()

    def show_stock_table_context_menu(self, position) -> None:
        """
        종목등록설정 창 종목표 우클릭 메뉴를 표시한다.
        """
        row = self.stock_table.rowAt(position.y())
        if row >= 0 and not self.stock_table.selectionModel().isRowSelected(row):
            self.stock_table.clearSelection()
            self.stock_table.selectRow(row)

        selected_count = len(self.selected_registered_stocks())
        menu = QMenu(self)

        action_select_all = menu.addAction("전체 선택")
        action_select_unassigned = menu.addAction("미등록 선택")
        action_delete = menu.addAction("선택 삭제")
        action_clear = menu.addAction("선택 해제")
        menu.addSeparator()
        action_assign = menu.addAction("루틴 지정")
        action_unassign = menu.addAction("루틴 해제")

        has_selected = selected_count > 0
        action_assign.setEnabled(has_selected)
        action_unassign.setEnabled(has_selected)
        action_delete.setEnabled(has_selected)

        selected_action = menu.exec_(self.stock_table.viewport().mapToGlobal(position))
        if selected_action is None:
            return

        if selected_action == action_assign:
            self.confirm_open_routine_assign_from_context_menu()
        elif selected_action == action_unassign:
            self.unassign_selected_stock_routines()
        elif selected_action == action_delete:
            self.delete_selected_stock()
        elif selected_action == action_select_all:
            self.select_all_visible_stocks()
        elif selected_action == action_select_unassigned:
            self.select_unassigned_visible_stocks()
        elif selected_action == action_clear:
            self.stock_table.clearSelection()
            self.on_stock_selection_changed()

    def confirm_open_routine_assign_from_context_menu(self) -> None:
        """
        종목등록설정 창 우클릭 루틴 지정 진입 전 확인창을 표시한다.

        우클릭으로 선택한 전체 종목 수와 실제 자동 체크 대상 수를 분리해서 안내한다.
        매매루틴지정 창에는 루틴 지정 가능 종목만 자동 체크 대상으로 전달되므로,
        확인창에서도 "선택 종목 전체를 넘긴다"는 식의 오해 소지가 없도록 표시한다.
        """
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.warning(
                self,
                "선택 오류",
                "루틴 지정할 종목을 1개 이상 선택하세요.",
            )
            return

        assignable_stocks, blocked_items = classify_routine_assign_targets(selected_stocks)
        selected_count = len(selected_stocks)
        assignable_count = len(assignable_stocks)
        blocked_count = len(blocked_items)

        def build_stock_preview(stocks: list[tuple[str, str]], limit: int = 10) -> str:
            if not stocks:
                return "- 없음"
            lines = [f"- {code} {name}" for code, name in stocks[:limit]]
            if len(stocks) > limit:
                lines.append(f"- ... 외 {len(stocks) - limit}개")
            return "\n".join(lines)

        def blocked_reason_text(item: dict[str, object]) -> str:
            reason = str(item.get("reason", "")).strip()
            status = str(item.get("status_display", item.get("status", ""))).strip()
            holding_qty = int(item.get("holding_qty", 0) or 0)
            pending_qty = int(item.get("pending_qty", 0) or 0)

            details: list[str] = []
            if reason:
                details.append(reason)
            if status:
                details.append(f"상태: {status}")
            if holding_qty:
                details.append(f"보유: {holding_qty}")
            if pending_qty:
                details.append(f"미체결: {pending_qty}")
            return " / ".join(details) if details else "루틴 지정 제한"

        blocked_preview_lines: list[str] = []
        for item in blocked_items[:10]:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            blocked_preview_lines.append(f"- {code} {name} ({blocked_reason_text(item)})")
        if blocked_count > 10:
            blocked_preview_lines.append(f"- ... 외 {blocked_count - 10}개")
        blocked_preview = "\n".join(blocked_preview_lines) if blocked_preview_lines else "- 없음"

        if assignable_count <= 0:
            QMessageBox.information(
                self,
                "루틴 지정 대상 없음",
                f"선택 종목: {selected_count}개\n\n"
                f"[루틴 지정 가능 종목: 0개]\n"
                "- 없음\n\n"
                f"[루틴 지정 제한 종목: {blocked_count}개]\n"
                f"{blocked_preview}\n\n"
                "루틴 지정 가능한 종목이 없어 매매루틴지정 창을 열지 않습니다.",
            )
            return

        message = (
            f"선택 종목: {selected_count}개\n\n"
            f"[루틴 지정 가능 종목: {assignable_count}개]\n"
            f"{build_stock_preview(assignable_stocks)}\n\n"
            f"[루틴 지정 제한 종목: {blocked_count}개]\n"
            f"{blocked_preview}"
        )

        if blocked_count > 0:
            message += "\n\n확인 후 창을 열면 제한 종목은 처리불가 리포트에 기록됩니다."

        message += "\n\n매매루틴지정 창을 여시겠습니까?"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("루틴 지정 확인")
        box.setText(message)
        open_button = box.addButton("열기", QMessageBox.YesRole)
        box.addButton("취소", QMessageBox.NoRole)
        box.setDefaultButton(open_button)
        box.exec_()

        if box.clickedButton() != open_button:
            return

        self.confirm_and_open_routine_assign(selected_stocks)

    def select_all_visible_stocks(self) -> None:
        """현재 화면에 표시된 모든 종목 행을 선택한다."""
        self.stock_table.clearSelection()
        selection_model = self.stock_table.selectionModel()
        if selection_model is None:
            return
        for row in range(self.stock_table.rowCount()):
            index = self.stock_table.model().index(row, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.on_stock_selection_changed()

    def select_unassigned_visible_stocks(self) -> None:
        """현재 화면에서 연결 루틴이 미등록인 종목만 선택한다."""
        self.stock_table.clearSelection()
        selection_model = self.stock_table.selectionModel()
        if selection_model is None:
            return
        for row in range(self.stock_table.rowCount()):
            routine_item = self.stock_table.item(row, 2)
            routine_text = routine_item.text().strip() if routine_item is not None else ""
            if routine_text != "미등록":
                continue
            index = self.stock_table.model().index(row, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.on_stock_selection_changed()

    def unassign_selected_stock_routines(self) -> None:
        """
        선택 종목의 루틴 연결만 해제한다.
        종목 자체와 runtime 폴더는 삭제하지 않는다.
        """
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.warning(self, "선택 오류", "루틴 해제할 종목을 1개 이상 선택하세요.")
            return

        allowed: list[tuple[str, str, str]] = []
        skipped_unassigned: list[str] = []
        blocked_items: list[dict[str, object]] = []

        for code, name in selected_stocks:
            can_unassign, routine_name, reasons = can_unassign_active_routine_from_stock(code, name)
            title = f"{code} {name}"
            if not routine_name and reasons and "연결 루틴이 없습니다." in reasons:
                skipped_unassigned.append(title)
                continue
            if can_unassign:
                allowed.append((code, name, routine_name))
            else:
                info = routine_action_guard_info(code, name)
                info["reasons"] = reasons
                blocked_items.append(info)

        if not allowed and not blocked_items:
            if skipped_unassigned:
                QMessageBox.information(self, "루틴 해제 없음", "선택 종목은 이미 미등록 상태입니다.")
            else:
                QMessageBox.information(self, "루틴 해제 없음", "루틴 해제할 종목이 없습니다.")
            return

        first_routine_name = allowed[0][2] if allowed else ""
        if not first_routine_name and blocked_items:
            first_routine_name = str(blocked_items[0].get("routine_name", "")).strip()

        confirm_dialog = RoutineUnassignConfirmDialog(
            routine_name=first_routine_name or "선택 루틴",
            removable_items=[(code, name) for code, name, _ in allowed],
            blocked_items=blocked_items,
            parent=self,
        )
        if confirm_dialog.exec_() != QDialog.Accepted:
            return

        removed_items: list[str] = []
        for code, name, routine_name in allowed:
            if update_base_stock_routines(code, name, []):
                ensure_single_real_trade_routine_for_stock(code, name)
                removed_items.append(f"{code},{name}({routine_name})")

        report_path = write_blocked_action_report("루틴 해제", blocked_items)

        if removed_items:
            append_changelog(
                "UPDATE",
                "중앙 종목관리",
                f"종목등록설정 루틴 해제: {' / '.join(removed_items)} / runtime 폴더 유지",
            )

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        result_lines = [f"루틴 해제 완료: {len(removed_items)}개"]
        if blocked_items:
            result_lines.append(f"해제 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_unassigned:
            result_lines.append(f"이미 미등록: {len(skipped_unassigned)}개")

        QMessageBox.information(self, "루틴 해제 결과", "\n".join(result_lines))


    def delete_selected_stock(self) -> None:
        """
        선택 종목을 중앙 stocks/ 구조에서 등록해제한다.

        정책:
        - 기초종목 파일을 사용하지 않고 중앙 stocks/ 구조만 사용한다.
        - 즉시 삭제 가능 종목은 stocks/종목폴더를 archive로 이동한다.
        - 강제 등록해제 대상은 선택된 경우 state/orders 초기화 후 archive로 이동한다.
        - 처리불가 종목은 삭제하지 않고 리포트에 기록한다.
        """
        selected_rows = self.stock_table.selectionModel().selectedRows()

        if not selected_rows:
            QMessageBox.warning(
                self,
                "선택 오류",
                "삭제할 종목을 1개 이상 선택하세요.",
            )
            return

        selected_stocks: list[tuple[str, str]] = []
        invalid_rows: list[int] = []

        for index in selected_rows:
            selected_row = index.row()
            code_item = self.stock_table.item(selected_row, 0)
            name_item = self.stock_table.item(selected_row, 1)

            if code_item is None or name_item is None:
                invalid_rows.append(selected_row + 1)
                continue

            code = code_item.text().strip()
            name = name_item.text().strip()

            if not code or not name:
                invalid_rows.append(selected_row + 1)
                continue

            selected_stocks.append((code, name))

        if invalid_rows:
            QMessageBox.warning(
                self,
                "삭제 오류",
                "선택한 종목 중 정보를 읽을 수 없는 행이 있습니다.\n\n"
                f"문제 행: {', '.join(str(row) for row in invalid_rows)}",
            )
            return

        if not selected_stocks:
            QMessageBox.warning(
                self,
                "선택 오류",
                "삭제할 종목 정보를 찾지 못했습니다.",
            )
            return

        # 같은 종목 행이 중복 선택되는 경우를 방어한다.
        seen_stocks: set[tuple[str, str]] = set()
        unique_stocks: list[tuple[str, str]] = []
        for code, name in selected_stocks:
            key = (code, name)
            if key in seen_stocks:
                continue
            seen_stocks.add(key)
            unique_stocks.append(key)

        immediate_items: list[dict[str, object]] = []
        force_items: list[dict[str, object]] = []
        blocked_items: list[dict[str, object]] = []

        for code, name in unique_stocks:
            category, title, reasons, runtime_dirs = stock_register_unavailable_reason(code, name)
            item = {
                "code": code,
                "name": name,
                "title": title,
                "reasons": reasons,
                "runtime_dirs": runtime_dirs,
            }
            if category == "immediate":
                immediate_items.append(item)
            elif category == "force":
                force_items.append(item)
            else:
                blocked_items.append(item)

        selected_force_items: list[dict[str, object]] = []
        blocked_report_items: list[dict[str, object]] = []
        for item in blocked_items:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            info = routine_action_guard_info(code, name)
            info["reasons"] = item.get("reasons", [])
            blocked_report_items.append(info)
        blocked_report_path = write_blocked_action_report("종목 삭제", blocked_report_items)

        if force_items or blocked_items:
            dialog = ForceUnregisterConfirmDialog(
                self,
                force_items=force_items,
                blocked_items=blocked_items,
                immediate_count=len(immediate_items),
            )
            dialog_result = dialog.exec_()
            if dialog_result == QDialog.Accepted:
                selected_force_items = dialog.selected_items()
            else:
                selected_force_items = []
                if not immediate_items and not force_items and blocked_items:
                    return

        process_items = immediate_items + selected_force_items

        if not process_items:
            if blocked_items or force_items:
                QMessageBox.information(
                    self,
                    "삭제 없음",
                    "삭제 처리할 종목이 선택되지 않았습니다.",
                )
            return

        try:
            repo = stock_repository_factory()
        except Exception:
            QMessageBox.warning(
                self,
                "삭제 오류",
                "중앙 stocks 종목관리 계층을 불러오지 못했습니다.",
            )
            return

        archive_root = ARCHIVED_STOCKS_DIR
        archive_root.mkdir(exist_ok=True)

        force_targets = {(str(item["code"]), str(item["name"])) for item in selected_force_items}
        deleted_items: list[tuple[str, str]] = []
        delete_failed_items: list[str] = []
        reset_failed_items: list[str] = []

        for item in process_items:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()

            if (code, name) in force_targets:
                for routine_name, stock_dir in item.get("runtime_dirs", []):
                    if not reset_runtime_state_for_force_unregister(stock_dir):
                        reset_failed_items.append(f"{code} {name} / {routine_name}")
                        continue
                    append_stock_log(
                        stock_dir,
                        "FORCE_UNREGISTER_RESET",
                        "강제 삭제로 state.json과 orders.json 현재 표시/판단값 초기화",
                    )

            if reset_failed_items:
                continue

            stock_dir = repo.resolve_stock_dir(code, name)
            if not stock_dir.exists() or not stock_dir.is_dir():
                delete_failed_items.append(f"{code} {name}: 중앙 stocks 폴더 없음")
                continue

            # 삭제 전 루틴 연결 필드를 먼저 비워 둔다.
            try:
                repo.update_stock_routine(code, name, [])
            except Exception:
                pass

            timestamp = now_text().replace("-", "").replace(":", "").replace(" ", "_")
            archive_dir = archive_root / f"{stock_dir.name}_{timestamp}"
            try:
                if archive_dir.exists():
                    archive_dir = archive_root / f"{stock_dir.name}_{timestamp}_{len(deleted_items)+1}"
                stock_dir.rename(archive_dir)
                deleted_items.append((code, name))
            except Exception as exc:
                delete_failed_items.append(f"{code} {name}: {exc}")

        if reset_failed_items:
            preview_text = "\n".join(reset_failed_items[:10])
            if len(reset_failed_items) > 10:
                preview_text += f"\n... 외 {len(reset_failed_items) - 10}개"
            QMessageBox.warning(
                self,
                "상태 초기화 오류",
                "일부 종목의 state.json 초기화에 실패했습니다.\n"
                "중앙 종목관리 등록해제는 아직 저장하지 않았습니다.\n\n"
                f"{preview_text}",
            )
            return

        if delete_failed_items:
            preview_text = "\n".join(delete_failed_items[:10])
            if len(delete_failed_items) > 10:
                preview_text += f"\n... 외 {len(delete_failed_items) - 10}개"
            QMessageBox.warning(
                self,
                "삭제 일부 실패",
                "일부 종목을 archive로 이동하지 못했습니다.\n\n"
                f"{preview_text}",
            )

        try:
            deleted_text = " / ".join(f"{code},{name}" for code, name in deleted_items)
            force_text = " / ".join(f"{code},{name}" for code, name in sorted(force_targets))
            message = f"선택 종목 삭제: {deleted_text} / stocks 폴더 archive 이동"
            if force_text:
                message += f" / 강제 삭제 상태/주문표시 초기화: {force_text}"
            append_changelog("UPDATE", "중앙 종목관리", message)
        except Exception:
            pass

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        blocked_count = len(blocked_items)
        force_skipped_count = len(force_items) - len(selected_force_items)
        info_lines = [
            f"삭제 완료: {len(deleted_items)}개",
        ]
        if selected_force_items:
            info_lines.append(f"강제 삭제 및 상태/주문표시 초기화: {len(selected_force_items)}개")
        if force_skipped_count > 0:
            info_lines.append(f"선택하지 않아 유지: {force_skipped_count}개")
        if blocked_count > 0:
            info_lines.append(f"삭제 불가: {blocked_count}개")
            if blocked_report_path is not None:
                info_lines.append("처리불가 리포트 저장")
        if delete_failed_items:
            info_lines.append(f"삭제 실패: {len(delete_failed_items)}개")

        result_message = " / ".join(info_lines)
        if hasattr(parent, "statusBar"):
            parent.statusBar().showMessage(result_message, 7000)
        else:
            QMessageBox.information(self, "등록해제 결과", result_message)


    def selected_registered_stocks(self) -> list[tuple[str, str]]:
        """현재 화면에서 선택된 종목을 종목코드/종목명 기준으로 반환한다."""
        selected_rows = self.stock_table.selectionModel().selectedRows()
        selected: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for index in selected_rows:
            row = index.row()
            code_item = self.stock_table.item(row, 0)
            name_item = self.stock_table.item(row, 1)
            if code_item is None or name_item is None:
                continue

            code = code_item.text().strip()
            name = name_item.text().strip()
            key = (code, name)
            if not code or not name or key in seen:
                continue

            selected.append(key)
            seen.add(key)

        return selected

    def refresh_stock_table(self) -> None:
        stocks = read_base_stocks()
        keyword_text = self.stock_search_input.text().strip().lower() if hasattr(self, "stock_search_input") else ""
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]

        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            code = str(stock.get("code", "")).strip().lower()
            name = str(stock.get("name", "")).strip().lower()
            validation = str(stock.get("validation_status", "")).strip().lower()
            routines = stock.get("routines", [])
            routine_text = ",".join(str(item).strip().lower() for item in routines) if isinstance(routines, list) else str(routines).lower()
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            registered_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(code, name, registered_routine).lower()

            searchable_values = [
                code,
                name,
                validation,
                routine_text,
                operation_status,
            ]
            return any(keyword in value for value in searchable_values)

        if keywords:
            filtered: list[dict[str, object]] = []
            added_keys: set[tuple[str, str]] = set()

            for keyword in keywords:
                for stock in stocks:
                    key = (
                        str(stock.get("code", "")).strip(),
                        str(stock.get("name", "")).strip(),
                    )
                    if key in added_keys:
                        continue

                    if stock_matches(stock, keyword):
                        filtered.append(stock)
                        added_keys.add(key)

            stocks = filtered

        sort_column = self.stock_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.stock_table.horizontalHeader().sortIndicatorOrder()

        self.stock_table.blockSignals(True)
        self.stock_table.setSortingEnabled(False)
        self.stock_table.setRowCount(len(stocks))

        for row, stock in enumerate(stocks):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            routines = stock.get("routines", [])

            if isinstance(routines, list):
                routine_list = [str(item).strip() for item in routines if str(item).strip()]
            else:
                routine_text_raw = str(routines).strip()
                routine_list = [routine_text_raw] if routine_text_raw else []

            # 연결 루틴 컬럼은 중앙 종목관리에 실제 연결된 활성 루틴만 표시한다.
            # 루틴 폴더에 남아 있는 과거 runtime 폴더나 상태값은 이 창에서 표시하지 않는다.
            # 종목당 활성 루틴 1개 정책에 따라 첫 번째 루틴만 표시하고, 루틴이 없으면 미등록으로 표시한다.
            registered_routine = routine_list[0] if routine_list else "미등록"
            routine_tooltip = registered_routine
            operation_status = active_stock_register_status_display(code, name, registered_routine)

            values = [
                code,
                name,
                registered_routine,
                operation_status,
                str(stock.get("validation_status", "정상")),
            ]

            for col, value in enumerate(values):
                if col == 3:
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
                item.setToolTip(routine_tooltip if col == 2 else value)
                self.stock_table.setItem(row, col, item)

        self.stock_table.resizeRowsToContents()
        self.stock_table.setSortingEnabled(True)
        if 0 <= sort_column < self.stock_table.columnCount():
            self.stock_table.sortItems(sort_column, sort_order)
        self.stock_table.blockSignals(False)
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

    def open_search_register_dialog(self) -> None:
        """
        검색식등록은 현재 단계에서 비활성화한다.
        이 메서드는 실수로 호출되어도 메시지창이나 검색창을 띄우지 않는다.
        """
        return

    def open_manual_register_dialog(self) -> None:
        """
        수동등록 버튼은 종목 라이브러리에서 직접 선택 등록한다.
        임의 종목코드/종목명 직접 입력 방식은 제공하지 않는다.
        """
        dialog = SearchStockRegisterDialog(self, title="수동등록")
        dialog.exec_()
        self.refresh_stock_table()

    def confirm_and_open_routine_assign(self, selected_stocks: list[tuple[str, str]]) -> None:
        """
        매매루틴지정 창을 연다.

        창 진입은 종목 선택/상태와 무관하게 허용한다.
        선택 종목이 있으면 루틴 변경 가능한 종목만 자동 체크 대상으로 넘기고,
        불가능한 종목은 창 진입을 막지 않고 처리불가 리포트만 남긴다.
        """
        auto_check_stocks: list[tuple[str, str]] = []
        blocked_items: list[dict[str, object]] = []

        if selected_stocks:
            auto_check_stocks, blocked_items = classify_routine_assign_targets(selected_stocks)
            report_path = write_blocked_action_report("루틴 지정 사전검사", blocked_items)

            if blocked_items:
                message = (
                    f"선택 종목 중 루틴 지정 불가: {len(blocked_items)}개"
                    " / 매매루틴지정 창은 열립니다."
                )
                if report_path is not None:
                    message += " / 처리불가 리포트 저장"

                parent = self.parent()
                if parent is not None and hasattr(parent, "statusBar"):
                    parent.statusBar().showMessage(message, 7000)
                else:
                    self.show_status(message) if hasattr(self, "show_status") else None

        dialog = RoutineAssignWindow(self, target_stocks=auto_check_stocks)
        dialog.exec_()
        self.refresh_stock_table()

    def open_routine_assign_window(self) -> None:
        selected_stocks = self.selected_registered_stocks()
        self.confirm_and_open_routine_assign(selected_stocks)

    def open_routine_assign_for_stock(self, item: QTableWidgetItem) -> None:
        """
        종목 행 더블클릭 시 해당 종목을 루틴 지정 사전 검사 후 매매루틴지정 창으로 넘긴다.
        """
        row = item.row()
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)

        if code_item is None or name_item is None:
            return

        code = code_item.text().strip()
        name = name_item.text().strip()

        if not code or not name:
            return

        self.confirm_and_open_routine_assign([(code, name)])

    def open_latest_blocked_report(self) -> None:
        report_path = latest_blocked_action_report_path()
        if report_path is None:
            QMessageBox.information(
                self,
                "처리불가 리포트",
                "저장된 처리불가 리포트가 없습니다.",
            )
            return

        dialog = BlockedActionReportViewDialog(report_path, self)
        dialog.exec_()

    def open_integrity_check_window(self) -> None:
        dialog = IntegrityCheckWindow(self)
        dialog.exec_()
        self.refresh_stock_table()

    def is_duplicate_stock(self, code: str, name: str) -> bool:
        stocks = read_base_stocks()
        normalized_name = name.strip()

        for stock in stocks:
            existing_code = str(stock.get("code", "")).strip()
            existing_name = str(stock.get("name", "")).strip()

            if existing_code == code or existing_name == normalized_name:
                return True

        return False

    def not_implemented(self) -> None:
        QMessageBox.information(
            self,
            "안내",
            "이 기능은 다음 단계에서 구현합니다.",
        )
