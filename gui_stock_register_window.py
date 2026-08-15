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
import logging
import shutil
from datetime import date, datetime, timedelta
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect, QPoint
from PyQt5.QtGui import QColor, QFont, QPalette
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
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from event_journal_production import append_production_event

from integrity_checker import (
    LOCAL_STATUS_CHECK_ERROR,
    LOCAL_STATUS_INTEGRITY_ISSUE,
    LOCAL_STATUS_PASS,
    LOCAL_STATUS_REVIEW_REQUIRED,
    SERVER_STATUS_NOT_CHECKED,
    apply_integrity_review_required_issues,
    run_local_stock_integrity_check,
)
from gui_table_utils import next_sort_order
from gui_centered_checkbox_delegate import CenteredCheckBoxDelegate
from gui_styles import (
    TABLE_LIGHT_SELECTION_STYLE,
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
    pending_order_integrity_issue_codes,
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
from gui_search_stock_register_dialog import SearchStockRegisterDialog
from gui_auto_trade_utils import (
    PENDING_INTEGRITY_USER_REASON,
    auto_trade_unregister_category,
    mark_pending_order_integrity_review_required,
)
from gui_auto_trade_run_control import auto_trade_running_registered_operation_targets
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
from gui_base_stock_service import update_base_stock_routine_instance
from routine_instance_registry import (
    load_persisted_routine_instances,
    routine_definition_by_id,
    RoutineDefinitionRecord,
    RoutineInstanceRecord,
)
from stock_repository import repository as stock_repository_factory
from gui_routine_service import (
    apply_default_operation_exclusion_for_new_running_assignment,
    ensure_single_real_trade_routine_for_stock,
)
from gui_toast import show_toast
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)
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
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
)
from gui_auto_trade_display import (
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    auto_trade_setting_display_status,
    auto_trade_setting_status_color,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    profit_loss_value_color,
    yes_no_display,
    display_status_text_for_gui,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_protected_stock_dir,
    is_review_required_state,
)
from gui_auto_trade_setting_window import (
    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
    AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    AutoTradeSettingWindow,
    ProfitLossEarlyCloseDialog,
    StockPolicyOverrideDialog,
    append_changelog,
    append_stock_log,
    assigned_stock_dirs_in_routine,
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_data_inconsistency_reasons,
    auto_trade_setting_badge_stylesheet,
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
    restart_initial_review_reason_for_stock,
    routine_display_name,
    short_close_method_text,
    single_routine_list,
    unique_review_reasons,
    update_base_stock_routines,
    validate_base_stock_record,
)
from gui_main_table_loader import ROUTINE_INSTANCE_GRID_COLUMNS

LOGGER = logging.getLogger(__name__)
UNEXPECTED_STATUS_REASON = "처리할 수 없는 종목입니다."
STOCK_RESET_INITIALIZABLE = "INITIALIZABLE"
STOCK_RESET_NOT_INITIALIZABLE = "NOT_INITIALIZABLE"
STOCK_RESET_ACTIVE_TRADING_STATUSES = {"RUNNING", "STARTED", "AUTO", "TRADING"}
STOCK_RESET_CLOSING_STATUSES = {"AUTO_CLOSE", "AUTO_CLOSING", "AUTO_CLOSED"}
STOCK_RESET_LEGACY_CLOSING_STATUSES = {
    # Legacy compatibility only. New canonical closing states use AUTO_CLOSE
    # variants, but old SELL_ONLY-family values must still block reset safely.
    "SELL_ONLY",
    "WATCH_SELL",
    "BUY_SUSPENDED",
    "BUY_STOPPED",
}
STOCK_RESET_RUNNING_STATUSES = (
    STOCK_RESET_ACTIVE_TRADING_STATUSES
    | STOCK_RESET_CLOSING_STATUSES
    | STOCK_RESET_LEGACY_CLOSING_STATUSES
)
STOCK_RESET_KNOWN_STATUSES = {
    "STOPPED",
    "STOP",
    "MONITORING",
    "WATCHING",
    "",
    *STOCK_RESET_RUNNING_STATUSES,
    "EMERGENCY",
    "EMERGENCY_STOP",
    "EMERGENCY_STOPPED",
    "REVIEW",
    "REVIEW_REQUIRED",
}
STOCK_RESET_IN_PROGRESS_VALUES = {
    "PENDING",
    "REQUESTED",
    "RUNNING",
    "IN_PROGRESS",
    "PROCESSING",
    "STARTED",
}
STOCK_REGISTER_PERFORMANCE_SORT_ROLES = {
    "count": Qt.UserRole + 201,
    "rate": Qt.UserRole + 202,
    "amount": Qt.UserRole + 203,
    "efficiency": Qt.UserRole + 204,
}
STOCK_REGISTER_PERFORMANCE_SORT_LABELS = {
    "count": "횟수",
    "rate": "손익",
    "amount": "금액",
    "efficiency": "효율",
}

PROJECT_ROOT = Path(__file__).resolve().parent
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"







































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
    중앙 종목관리에는 현재 루틴 등록이 없지만
    루틴 폴더 안에 종목 대상 폴더가 남아 있는 경우 등록대기로 표시한다.
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
    해당 종목에 배정된 중앙 runtime 폴더를 반환한다.
    """
    return assigned_runtime_dirs_for_stock(code, name)


def stock_reset_stock_dirs_for_stock(code: str, name: str) -> list[Path]:
    """
    종목초기화 전용 중앙 stocks/ 폴더를 반환한다.

    루틴 배정 여부는 초기화 가능 여부가 아니므로 assigned runtime helper를
    사용하지 않는다.
    """
    repo = stock_repository_factory()
    matches: list[Path] = []
    clean_code = str(code or "").strip()
    clean_name = str(name or "").strip()
    for stock_dir in repo.list_stock_dirs():
        parsed_code, parsed_name = repo.parse_stock_folder(stock_dir)
        if parsed_code == clean_code and parsed_name == clean_name:
            matches.append(Path(stock_dir))
    return matches


def _read_json_dict_for_stock_reset(path: Path) -> tuple[dict[str, object], str]:
    if not path.exists():
        return {}, "파일 없음"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, "JSON 읽기 실패"

    if not isinstance(data, dict):
        return {}, "JSON 형식 오류"

    return data, ""


def _stock_reset_not_initializable(reason: str, stock_dir: Path | None) -> dict[str, object]:
    return {
        "status": STOCK_RESET_NOT_INITIALIZABLE,
        "reason": reason,
        "stock_dir": stock_dir,
    }


def _stock_reset_initializable(stock_dir: Path) -> dict[str, object]:
    return {
        "status": STOCK_RESET_INITIALIZABLE,
        "reason": "",
        "stock_dir": stock_dir,
    }


def _safe_stock_reset_int(value: object) -> tuple[int, bool]:
    try:
        return int(value or 0), True
    except Exception:
        return 0, False


def _orders_json_has_integrity_issue(orders_state: dict[str, object]) -> bool:
    orders = orders_state.get("orders", [])
    if not isinstance(orders, list):
        return True
    return any(not isinstance(order, dict) for order in orders)


def _state_has_in_progress_lifecycle(state: dict[str, object]) -> bool:
    fields = (
        "command_status",
        "command_state",
        "operation_command_status",
        "transition_status",
        "lifecycle_status",
        "close_intent_status",
        "immediate_liquidation_status",
        "pending_command_status",
    )
    for field in fields:
        value = str(state.get(field, "") or "").strip().upper()
        if value in STOCK_RESET_IN_PROGRESS_VALUES:
            return True
    return False


def stock_reset_eligibility(code: str, name: str) -> dict[str, object]:
    """
    종목초기화 가능 여부를 INITIALIZABLE / NOT_INITIALIZABLE로 판정한다.

    이 helper는 초기화 전용 정책 경계이며, 기존 삭제/등록해제 force/archive
    정책과 섞지 않는다. 판정 중 Runtime이나 주문 파일을 수정하지 않는다.
    """
    try:
        stock_dirs = stock_reset_stock_dirs_for_stock(code, name)
    except Exception as exc:
        LOGGER.error(
            "stock reset eligibility failed to resolve central stock dirs: code=%s name=%s error=%s",
            code,
            name,
            exc,
        )
        return _stock_reset_not_initializable("종목 저장 위치 확인 실패", None)

    if not stock_dirs:
        return _stock_reset_not_initializable("종목 저장 위치 없음", None)

    if len(stock_dirs) != 1:
        return _stock_reset_not_initializable("종목 저장 위치 중복", None)

    stock_dir = Path(stock_dirs[0])
    if not stock_dir.exists() or not stock_dir.is_dir():
        return _stock_reset_not_initializable("종목 저장 위치 없음", stock_dir)

    state, state_issue = _read_json_dict_for_stock_reset(stock_dir / "state.json")
    if state_issue:
        return _stock_reset_not_initializable(f"state 무결성 오류: {state_issue}", stock_dir)

    if is_review_required_state(state) or is_review_protected_stock_dir(stock_dir):
        return _stock_reset_not_initializable("검토관리 상태", stock_dir)

    raw_status = str(state.get("status", "STOPPED") or "").strip().upper()
    if raw_status not in STOCK_RESET_KNOWN_STATUSES:
        LOGGER.error(
            "unexpected stock-reset eligibility status: %s code=%s name=%s stock_dir=%s",
            raw_status,
            code,
            name,
            stock_dir,
        )
        return _stock_reset_not_initializable(UNEXPECTED_STATUS_REASON, stock_dir)

    if is_emergency_stopped_state(state):
        return _stock_reset_not_initializable("긴급정지 상태", stock_dir)

    if raw_status in STOCK_RESET_ACTIVE_TRADING_STATUSES:
        return _stock_reset_not_initializable("운영 중", stock_dir)
    if raw_status in STOCK_RESET_CLOSING_STATUSES or raw_status in STOCK_RESET_LEGACY_CLOSING_STATUSES:
        return _stock_reset_not_initializable("마감 진행 중", stock_dir)

    if _state_has_in_progress_lifecycle(state):
        return _stock_reset_not_initializable("진행 중인 명령 또는 전환 상태", stock_dir)

    holding_qty, holding_ok = _safe_stock_reset_int(state.get("holding_qty", 0))
    if not holding_ok:
        return _stock_reset_not_initializable("보유수량 확인 실패", stock_dir)
    if holding_qty > 0:
        return _stock_reset_not_initializable("보유수량 있음", stock_dir)

    orders_state, orders_issue = _read_json_dict_for_stock_reset(stock_dir / "orders.json")
    if orders_issue:
        return _stock_reset_not_initializable(f"orders 무결성 오류: {orders_issue}", stock_dir)
    if _orders_json_has_integrity_issue(orders_state):
        return _stock_reset_not_initializable("orders 무결성 오류", stock_dir)

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return _stock_reset_not_initializable(PENDING_INTEGRITY_USER_REASON, stock_dir)
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return _stock_reset_not_initializable("매수미결 있음", stock_dir)
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return _stock_reset_not_initializable("매도미결 있음", stock_dir)

    return _stock_reset_initializable(stock_dir)


def _confirm_stock_project_reset(
    parent: QWidget,
    *,
    title: str,
    text: str,
    targets: list[tuple[str, str]] | None = None,
    reset_type: str = "STOCK_PROJECT_RESET",
) -> bool:
    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Warning)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    confirm_button = dialog.addButton("확인", QMessageBox.AcceptRole)
    cancel_button = dialog.addButton("취소", QMessageBox.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec_()
    accepted = dialog.clickedButton() is confirm_button
    target_items = list(targets or [])
    target_codes = [str(code or "").strip() for code, _name in target_items if str(code or "").strip()]
    target_names = [str(name or "").strip() for _code, name in target_items if str(name or "").strip()]
    correlation = {"stock_code": target_codes[0]} if len(target_codes) == 1 else {}
    append_production_event(
        "OPERATOR_SETTING_DECISION",
        result="ACCEPTED" if accepted else "CANCELLED",
        source="gui_stock_register_window._confirm_stock_project_reset",
        target_type="STOCK_SELECTION",
        target_id=",".join(target_codes) or None,
        target_name=",".join(target_names) or "종목 초기화 대상",
        details={
            "interaction_type": "CONFIRM",
            "prompt_key": reset_type,
            "prompt_title": title,
            "prompt_summary": "선택 종목의 프로젝트 기록 초기화",
            "offered_options": ["확인", "취소"],
            "selected_option": "확인" if accepted else "취소",
            "reset_type": reset_type,
            "target_count": len(target_items),
        },
        **correlation,
    )
    return accepted


def _stock_reset_confirmation_text(targets: list[tuple[str, str]]) -> str:
    target_text = "\n".join(f"{code} {name}" for code, name in targets)
    return (
        "해당 종목의 모든 기록을 삭제하고\n"
        "미등록 상태로 초기화합니다.\n\n"
        "이 작업은 되돌릴 수 없으며\n"
        "삭제된 데이터는 복구할 수 없습니다.\n\n"
        f"초기화 대상:\n{target_text}"
    )


def confirm_stock_reset(parent: QWidget, code: str, name: str) -> bool:
    return _confirm_stock_project_reset(
        parent,
        title="⚠ 종목초기화 확인",
        text=_stock_reset_confirmation_text([(code, name)]),
        targets=[(code, name)],
        reset_type="STOCK_PROJECT_RESET",
    )


def confirm_force_stock_reset(
    parent: QWidget,
    targets: list[tuple[str, str]],
) -> bool:
    return _confirm_stock_project_reset(
        parent,
        title="강제초기화 확인",
        text=_stock_reset_confirmation_text(targets),
        targets=targets,
        reset_type="FORCE_STOCK_PROJECT_RESET",
    )


def _stock_reset_failure_message() -> str:
    return "해당 종목은 초기화가 불가능합니다.\n종목 상태를 다시 확인하세요."


def _validate_stock_reset_delete_path(code: str, name: str, stock_dir: Path) -> tuple[bool, str]:
    try:
        stocks_root = (PROJECT_ROOT / "stocks").resolve(strict=True)
        project_root = PROJECT_ROOT.resolve(strict=True)
        raw_stock_dir = Path(stock_dir)
        if raw_stock_dir.is_symlink():
            return False, "symlink 경로"
        stat_result = raw_stock_dir.stat()
        if getattr(stat_result, "st_file_attributes", 0) & 0x400:
            return False, "reparse point 경로"
        resolved_stock_dir = raw_stock_dir.resolve(strict=True)
    except Exception as exc:
        return False, f"경로 확인 실패: {exc}"

    if resolved_stock_dir in {project_root, stocks_root}:
        return False, "프로젝트 또는 stocks 루트 경로"

    if resolved_stock_dir.parent != stocks_root:
        return False, "stocks 바로 아래 종목 폴더가 아님"

    if not resolved_stock_dir.is_dir():
        return False, "디렉터리가 아님"

    parsed_code, parsed_name = parse_stock_folder_name(resolved_stock_dir.name)
    if str(parsed_code).strip() != str(code).strip() or str(parsed_name).strip() != str(name).strip():
        return False, "종목 폴더 identity 불일치"

    return True, ""


def _stock_reset_target_still_exists_in_base_stocks(code: str, name: str) -> bool:
    for stock in read_base_stocks():
        if str(stock.get("code", "")).strip() == code and str(stock.get("name", "")).strip() == name:
            return True
    return False


def force_stock_reset_preflight(
    code: str,
    name: str,
    selected_stock_dir: Path,
) -> dict[str, object]:
    """검토관리 강제초기화 대상의 identity와 삭제 경로만 검증한다."""
    try:
        stock_dirs = stock_reset_stock_dirs_for_stock(code, name)
    except Exception as exc:
        LOGGER.exception(
            "forced stock reset failed to resolve target: code=%s name=%s error=%s",
            code,
            name,
            exc,
        )
        return _stock_reset_not_initializable("종목 저장 위치 확인 실패", None)

    if not stock_dirs:
        return _stock_reset_not_initializable("종목 저장 위치 없음", None)
    if len(stock_dirs) != 1:
        return _stock_reset_not_initializable("종목 저장 위치 중복", None)

    stock_dir = Path(stock_dirs[0])
    try:
        if stock_dir.resolve(strict=True) != Path(selected_stock_dir).resolve(strict=True):
            return _stock_reset_not_initializable("선택 대상 identity 불일치", stock_dir)
    except Exception:
        return _stock_reset_not_initializable("선택 대상 경로 확인 실패", stock_dir)

    if not is_review_protected_stock_dir(stock_dir):
        return _stock_reset_not_initializable("검토관리 대상이 아님", stock_dir)

    path_ok, path_reason = _validate_stock_reset_delete_path(code, name, stock_dir)
    if not path_ok:
        LOGGER.error(
            "forced stock reset path validation failed: code=%s name=%s stock_dir=%s reason=%s",
            code,
            name,
            stock_dir,
            path_reason,
        )
        return _stock_reset_not_initializable(path_reason, stock_dir)

    return _stock_reset_initializable(stock_dir)


def delete_stock_project_data(code: str, name: str, stock_dir: Path) -> dict[str, object]:
    """검증된 중앙 종목 폴더를 삭제하고 등록/연결 제거를 read-back한다."""
    path_ok, path_reason = _validate_stock_reset_delete_path(code, name, stock_dir)
    if not path_ok:
        LOGGER.error(
            "stock reset path validation failed: code=%s name=%s stock_dir=%s reason=%s",
            code,
            name,
            stock_dir,
            path_reason,
        )
        return {"status": "FAILED", "reason": path_reason}

    try:
        shutil.rmtree(stock_dir)
    except Exception as exc:
        LOGGER.exception(
            "stock reset failed to remove stock dir: code=%s name=%s stock_dir=%s error=%s",
            code,
            name,
            stock_dir,
            exc,
        )
        return {"status": "FAILED", "reason": "종목 데이터 삭제 실패"}

    if stock_dir.exists():
        LOGGER.error(
            "stock reset delete verification failed: directory still exists code=%s name=%s stock_dir=%s",
            code,
            name,
            stock_dir,
        )
        return {"status": "FAILED", "reason": "삭제 경로 잔존"}

    try:
        still_registered = _stock_reset_target_still_exists_in_base_stocks(code, name)
        remaining_runtime_dirs = stock_runtime_dirs_for_stock(code, name)
    except Exception as exc:
        LOGGER.exception(
            "stock reset post-delete readback failed: code=%s name=%s error=%s",
            code,
            name,
            exc,
        )
        return {"status": "FAILED", "reason": "삭제 후 상태 확인 실패"}

    if still_registered or remaining_runtime_dirs:
        LOGGER.error(
            "stock reset post-delete verification failed: code=%s name=%s still_registered=%s remaining_runtime_dirs=%s",
            code,
            name,
            still_registered,
            remaining_runtime_dirs,
        )
        return {"status": "FAILED", "reason": "등록 또는 루틴 연결 잔존"}

    return {"status": "DELETED", "reason": ""}


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

    자동매매설정 창 상태 색상과 같은 팔레트를 사용해 상태별 연결성을 맞춘다.
    """
    normalized = display_status_text_for_gui(status)
    if normalized == "대기":
        return auto_trade_status_color("등록대기")
    if normalized == "운영":
        normalized = "운영중"
    return auto_trade_status_color(normalized)


def create_routine_status_widget(status_lines: list[tuple[str, str]]) -> QWidget:
    """
    연결 루틴 목록을 상태 점열로 생성한다.
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

    known_statuses = {
        "STOPPED",
        "STOP",
        "MONITORING",
        "WATCHING",
        "",
        "RUNNING",
        "STARTED",
        "AUTO",
        "TRADING",
        "SELL_ONLY",
        "EMERGENCY_STOP",
        "EMERGENCY_STOPPED",
    }

    for routine_name, stock_dir in runtime_dirs:
        state = read_json_dict(stock_dir / "state.json")
        raw_status = str(state.get("status", "STOPPED")).strip().upper()

        try:
            holding_qty = int(state.get("holding_qty", 0) or 0)
        except Exception:
            holding_qty = 0

        routine_prefix = f"{routine_name}: "

        if is_review_protected_stock_dir(stock_dir):
            blocked_reasons.append(f"{routine_prefix}검토관리")
            continue

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

        if raw_status not in known_statuses:
            LOGGER.error(
                "unexpected stock-register unregister policy status: %s code=%s name=%s routine=%s stock_dir=%s",
                raw_status,
                code,
                name,
                routine_name,
                stock_dir,
            )
            blocked_reasons.append(f"{routine_prefix}{UNEXPECTED_STATUS_REASON}")
            continue

        if buy_pending_qty == "?" or sell_pending_qty == "?":
            issue_codes = pending_order_integrity_issue_codes(stock_dir, state)
            mark_pending_order_integrity_review_required(
                routine_name,
                stock_dir,
                code,
                name,
                issue_codes,
                source="종목등록 창 미체결 데이터 무결성 오류",
            )
            blocked_reasons.append(f"{routine_prefix}{PENDING_INTEGRITY_USER_REASON}")
            continue

        pending_parts: list[str] = []
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            pending_parts.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            pending_parts.append(f"매도미결 {sell_pending_qty}")

        if holding_qty > 0 or pending_parts:
            details: list[str] = []
            if holding_qty > 0:
                details.append(f"보유 {holding_qty}")
            details.extend(pending_parts)
            force_reasons.append(f"{routine_prefix}{', '.join(details)}")

    if blocked_reasons:
        return "blocked", title, blocked_reasons, runtime_dirs

    if force_reasons:
        return "force", title, force_reasons, runtime_dirs

    return "immediate", title, ["보유·미체결 없음"], runtime_dirs


def active_stock_register_status_display(
    code: str,
    name: str,
    routine_name: str,
    *,
    current_running: bool = False,
) -> str:
    """
    종목관리 창의 운영자 관점 운영 단계 표시용 문구를 반환한다.

    원칙:
    - 루틴 미연결 종목은 미지정으로 표시한다.
    - 검토관리/긴급정지는 생명주기 보호 상태로 우선 표시한다.
    - 루틴 등록 종목은 공통 현재 세션 참가 판정으로 운영중/운영정지를 표시한다.
    """
    routine_name = str(routine_name).strip()
    if not routine_name or routine_name in {"미등록", "등록대기"}:
        return "미지정"

    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return "운영정지"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return "검토종목"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "검토종목"

    display_status = auto_trade_status_display(state.get("status", "STOPPED"))
    if display_status == "검토종목":
        return "검토종목"
    if display_status == "긴급정지":
        return "긴급정지"
    if current_running:
        return "운영중"
    return "운영정지"


def stock_register_operation_status_color(display_status: str) -> str | None:
    normalized = str(display_status or "").strip()
    if normalized == "운영중":
        return auto_trade_status_color("매수/매도")
    if normalized == "운영정지":
        return auto_trade_status_color("감시전용")
    if normalized == "미지정":
        return None
    return auto_trade_status_color(normalized)


def stock_register_operation_status_renderer_source(display_status: str) -> str | None:
    normalized = str(display_status or "").strip()
    if normalized == "미지정":
        return None
    if normalized == "운영중":
        return "매수/매도"
    if normalized == "운영정지":
        return "감시/대기"
    return normalized


def stock_register_operation_status_rank(display_status: str) -> int:
    normalized = str(display_status or "").strip()
    return {
        "미지정": 0,
        "운영정지": 1,
        "운영중": 2,
        "긴급정지": 3,
        "검토종목": 4,
    }.get(normalized, 99)

class StockRegisterPerformanceAdapter:
    _routine_tree_stock_performance_source = (
        AutoTradeSettingWindow._routine_tree_stock_performance_source
    )
    _routine_tree_performance_texts = (
        AutoTradeSettingWindow._routine_tree_performance_texts
    )


_STOCK_REGISTER_PERFORMANCE_ADAPTER = StockRegisterPerformanceAdapter()


def _parenthesized_value(text: object) -> str:
    normalized = str(text or "").strip()
    if "(" not in normalized or not normalized.endswith(")"):
        return ""
    return normalized.split("(", 1)[1][:-1]


def _signed_text_numeric_value(text: object) -> float:
    normalized = str(text or "").strip()
    for token in (",", "%", "원", "+"):
        normalized = normalized.replace(token, "")
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return 0.0


def stock_register_performance_value_color(text: object) -> str:
    value = _signed_text_numeric_value(text)
    if value == 0:
        return ""
    return profit_loss_value_color(value)


def _stock_register_performance_text_width(font_metrics, text: str) -> int:
    return max(
        font_metrics.horizontalAdvance(text),
        font_metrics.boundingRect(text).width(),
    )


def stock_register_performance_widths(font_metrics) -> dict[str, int]:
    return {
        "count_prefix": _stock_register_performance_text_width(font_metrics, "횟수("),
        "count_value": _stock_register_performance_text_width(font_metrics, "99,999"),
        "rate_prefix": _stock_register_performance_text_width(font_metrics, "손익("),
        "rate_value": _stock_register_performance_text_width(font_metrics, "-999.99%"),
        "amount_prefix": _stock_register_performance_text_width(font_metrics, "금액("),
        "amount_value": _stock_register_performance_text_width(font_metrics, "-999,999,999원"),
        "efficiency_prefix": _stock_register_performance_text_width(font_metrics, "효율("),
        "efficiency_value": _stock_register_performance_text_width(font_metrics, "999.9"),
        "close": _stock_register_performance_text_width(font_metrics, ")"),
        "separator": _stock_register_performance_text_width(font_metrics, " | "),
    }


def stock_register_performance_display_widths(font_metrics) -> dict[str, int]:
    widths = stock_register_performance_widths(font_metrics)
    return {
        **widths,
        "count_value": _stock_register_performance_text_width(font_metrics, "9,999"),
        "rate_value": _stock_register_performance_text_width(font_metrics, "-99.99%"),
        "separator": _stock_register_performance_text_width(font_metrics, " | ") + 8,
    }


def stock_register_performance_slot_widths(font_metrics) -> dict[str, int]:
    widths = stock_register_performance_display_widths(font_metrics)
    return {
        "count": widths["count_prefix"] + widths["count_value"] + widths["close"],
        "rate": widths["rate_prefix"] + widths["rate_value"] + widths["close"],
        "amount": widths["amount_prefix"] + widths["amount_value"] + widths["close"],
        "efficiency": (
            widths["efficiency_prefix"]
            + widths["efficiency_value"]
            + widths["close"]
        ),
        "separator": widths["separator"],
    }


def stock_register_performance_column_width(font_metrics) -> int:
    widths = stock_register_performance_widths(font_metrics)
    return (
        widths["count_prefix"]
        + widths["count_value"]
        + widths["close"]
        + widths["separator"]
        + widths["rate_prefix"]
        + widths["rate_value"]
        + widths["close"]
        + widths["separator"]
        + widths["amount_prefix"]
        + widths["amount_value"]
        + widths["close"]
        + widths["separator"]
        + widths["efficiency_prefix"]
        + widths["efficiency_value"]
        + widths["close"]
        + 12
    )


def stock_register_performance_display(stock: dict[str, object]) -> dict[str, object]:
    code = str(stock.get("code", "") or "").strip()
    name = str(stock.get("name", "") or "").strip()
    source_stock = {
        **stock,
        "stock_code": code,
        "stock_name": name,
        "is_current": True,
    }
    texts = _STOCK_REGISTER_PERFORMANCE_ADAPTER._routine_tree_performance_texts(
        [source_stock],
        {},
    )
    count_text = _parenthesized_value(texts.get("performance_period_text")) or "0"
    profit_text = _parenthesized_value(texts.get("performance_profit_text"))
    amount_text = "0"
    rate_text = "0.00%"
    if " / " in profit_text:
        amount_text, rate_text = [part.strip() for part in profit_text.split(" / ", 1)]
    elif profit_text:
        amount_text = profit_text
    efficiency_text = _parenthesized_value(texts.get("performance_efficiency_text")) or "0.0"
    display_text = (
        f"횟수({count_text}) | 손익({rate_text}) | "
        f"금액({amount_text}원) | 효율({efficiency_text})"
    )
    tooltip_parts = [
        str(texts.get("performance_period_text", "") or ""),
        str(texts.get("performance_profit_text", "") or ""),
        str(texts.get("performance_average_text", "") or ""),
        str(texts.get("performance_efficiency_text", "") or ""),
    ]
    return {
        "text": display_text,
        "tooltip": "\n".join(part for part in tooltip_parts if part),
        "sort": float(texts.get("performance_profit_sort_value", 0.0) or 0.0),
        "sort_count": _signed_text_numeric_value(count_text),
        "sort_rate": _signed_text_numeric_value(rate_text),
        "sort_amount": _signed_text_numeric_value(amount_text),
        "sort_efficiency": _signed_text_numeric_value(efficiency_text),
        "color": str(texts.get("performance_profit_color", "") or ""),
        "count": count_text,
        "rate": rate_text,
        "amount": f"{amount_text}원",
        "efficiency": efficiency_text,
    }


def stock_register_performance_summary_display(stocks: list[dict[str, object]]) -> dict[str, object]:
    source_stocks = []
    for stock in stocks:
        code = str(stock.get("code", "") or "").strip()
        name = str(stock.get("name", "") or "").strip()
        source_stocks.append(
            {
                **stock,
                "stock_code": code,
                "stock_name": name,
                "is_current": True,
            }
        )
    texts = _STOCK_REGISTER_PERFORMANCE_ADAPTER._routine_tree_performance_texts(
        source_stocks,
        {},
    )
    profit_text = _parenthesized_value(texts.get("performance_profit_text"))
    amount_text = "0"
    rate_text = "0.00%"
    if " / " in profit_text:
        amount_text, rate_text = [part.strip() for part in profit_text.split(" / ", 1)]
    elif profit_text:
        amount_text = profit_text
    efficiency_text = _parenthesized_value(texts.get("performance_efficiency_text")) or "0.0"
    return {
        "rate": rate_text,
        "amount": f"{amount_text}원",
        "efficiency": efficiency_text,
        "tooltip": "\n".join(
            part
            for part in (
                str(texts.get("performance_profit_text", "") or ""),
                str(texts.get("performance_average_text", "") or ""),
                str(texts.get("performance_efficiency_text", "") or ""),
            )
            if part
        ),
    }


def stock_register_performance_widget(performance: dict[str, object]) -> QWidget:
    widget = QWidget()
    widget.setFocusPolicy(Qt.NoFocus)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    widget.setToolTip(str(performance.get("tooltip", "") or ""))
    widget.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(2, 0, 0, 0)
    layout.setSpacing(0)
    widths = stock_register_performance_display_widths(widget.fontMetrics())
    layout.addStretch(1)

    def add_label(
        text: str,
        width: int,
        alignment: Qt.AlignmentFlag,
        color: str = "",
        object_name: str = "",
    ) -> None:
        label = QLabel(text)
        if object_name:
            label.setObjectName(object_name)
        label.setAlignment(alignment)
        label.setWordWrap(False)
        label.setFixedWidth(width)
        label.setFocusPolicy(Qt.NoFocus)
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        label.setToolTip(str(performance.get("tooltip", "") or ""))
        if color:
            label.setStyleSheet(f"background: transparent; color: {color};")
        else:
            label.setStyleSheet("background: transparent;")
        layout.addWidget(label, 0, Qt.AlignVCenter)

    rate = str(performance.get("rate", "") or "")
    amount = str(performance.get("amount", "") or "")
    add_label(
        "횟수(",
        widths["count_prefix"],
        Qt.AlignLeft | Qt.AlignVCenter,
        object_name="stockRegisterPerformanceBlockStart",
    )
    add_label(str(performance.get("count", "0")), widths["count_value"], Qt.AlignRight | Qt.AlignVCenter)
    add_label(")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)
    add_label(" | ", widths["separator"], Qt.AlignCenter, object_name="stockRegisterPerformanceSepProfit")
    add_label("손익(", widths["rate_prefix"], Qt.AlignLeft | Qt.AlignVCenter, object_name="stockRegisterPerformanceRateStart")
    add_label(rate, widths["rate_value"], Qt.AlignRight | Qt.AlignVCenter, stock_register_performance_value_color(rate))
    add_label(")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)
    add_label(" | ", widths["separator"], Qt.AlignCenter, object_name="stockRegisterPerformanceSepAmount")
    add_label("금액(", widths["amount_prefix"], Qt.AlignLeft | Qt.AlignVCenter, object_name="stockRegisterPerformanceAmountStart")
    add_label(amount, widths["amount_value"], Qt.AlignRight | Qt.AlignVCenter, stock_register_performance_value_color(amount))
    add_label(")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)
    add_label(" | ", widths["separator"], Qt.AlignCenter, object_name="stockRegisterPerformanceSepEfficiency")
    add_label("효율(", widths["efficiency_prefix"], Qt.AlignLeft | Qt.AlignVCenter, object_name="stockRegisterPerformanceEfficiencyStart")
    add_label(str(performance.get("efficiency", "0.0")), widths["efficiency_value"], Qt.AlignRight | Qt.AlignVCenter)
    add_label(
        ")",
        widths["close"],
        Qt.AlignLeft | Qt.AlignVCenter,
        object_name="stockRegisterPerformanceBlockEnd",
    )
    return widget

def stock_register_routine_display_name(stock: dict[str, object]) -> str:
    assigned_instance_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
    if assigned_instance_id:
        for instance in load_persisted_routine_instances():
            if str(getattr(instance, "instance_id", "") or "").strip() == assigned_instance_id:
                instance_name = str(getattr(instance, "display_name", "") or "").strip()
                if instance_name:
                    return instance_name

        stored_instance_name = str(stock.get("routine_instance_name", "") or "").strip()
        if stored_instance_name:
            return stored_instance_name

    routines = stock.get("routines", [])
    if isinstance(routines, list):
        routine_list = [str(item).strip() for item in routines if str(item).strip()]
    else:
        routine_text_raw = str(routines).strip()
        routine_list = [routine_text_raw] if routine_text_raw else []
    return routine_list[0] if routine_list else "등록대기"


class StockRegisterWindow(QDialog):
    """
    종목관리 창.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)

        self.setWindowTitle("종목관리")

        self.stock_search_input = QLineEdit()
        self.stock_search_input.setPlaceholderText("검색어 입력")
        self.stock_table = QTableWidget()
        self._stock_performance_sort_key = "amount"
        self._stock_performance_sort_order = Qt.DescendingOrder
        self._stock_performance_sort_buttons: dict[str, QPushButton] = {}
        self._stock_performance_summary_labels: dict[str, QLabel] = {}
        self._stock_register_header_required_width = 0
        self._integrity_auto_check_started = False
        self._last_integrity_check_result: dict[str, object] | None = None
        self._last_integrity_toast_message = ""
        self.integrity_status_icon_label = QLabel("")
        self.integrity_status_icon_label.setObjectName("stockRegisterIntegrityStatusIconLabel")
        self.integrity_status_icon_label.setAlignment(Qt.AlignCenter)
        self.integrity_status_icon_label.setFixedSize(20, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        self.integrity_status_icon_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.integrity_status_label = QLabel("")
        self.integrity_status_label.setObjectName("stockRegisterIntegrityStatusLabel")
        self.integrity_status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.integrity_status_label.setMinimumWidth(0)
        self.integrity_status_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._integrity_status_container = self._create_integrity_status_container()

        self.btn_search_register = QPushButton("검색등록")
        self.btn_search_register.setEnabled(False)
        self.btn_search_register.setToolTip("검색 결과에서 선택한 종목을 등록합니다.")
        self.btn_manual_register = QPushButton("종목등록")
        self.btn_manual_register.setToolTip("종목명 또는 종목코드를 직접 입력하여 등록합니다.")
        self.btn_stock_history = QPushButton("종목이력")
        self.btn_stock_history.setObjectName("stockRegisterStockHistoryButton")
        self.btn_stock_history.setEnabled(False)
        self.btn_stock_history.setToolTip("선택한 종목의 주문 이력을 확인합니다.")
        self.btn_delete_stock = QPushButton("종목초기화")
        self.btn_delete_stock.setObjectName("stockRegisterResetStockButton")
        self.btn_delete_stock.setEnabled(False)
        self.btn_delete_stock.setToolTip("선택한 종목을 초기화합니다.")
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._resize_to_stock_table_columns()
        QTimer.singleShot(0, self._ensure_stock_table_viewport_width)
        self._connect_events()
        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()
        self._stock_register_button_layout = button_layout

        self._setup_stock_table()

        buttons = [
            self.btn_search_register,
            self.btn_manual_register,
            self.btn_stock_history,
            self.btn_delete_stock,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        search_label = QLabel("검색")
        header_layout.addWidget(search_label)
        header_layout.addWidget(self.stock_search_input)
        self._setup_stock_performance_sort_badges()
        status_width = self._integrity_status_reserved_width()
        button_spacing = max(0, button_layout.spacing())
        button_width = (
            sum(button.sizeHint().width() for button in buttons)
            + max(0, len(buttons) - 1) * button_spacing
        )
        header_spacing = max(0, header_layout.spacing())
        available_search_width = (
            button_width
            - search_label.sizeHint().width()
            - status_width
            - self._stock_performance_sort_badge_container.width()
            - (header_spacing * 4)
        )
        search_width = (
            self.stock_search_input.fontMetrics().horizontalAdvance(
                self.stock_search_input.placeholderText()
            )
            + 28
        )
        self.stock_search_input.setFixedWidth(max(240, min(search_width, available_search_width)))
        self._stock_register_header_required_width = (
            search_label.sizeHint().width()
            + self.stock_search_input.width()
            + status_width
            + self._stock_performance_sort_badge_container.width()
            + (header_spacing * 4)
        )
        header_layout.addWidget(self._integrity_status_container, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        header_layout.addWidget(self._stock_performance_sort_badge_container, 0, Qt.AlignVCenter)

        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.stock_table)
        self._setup_stock_performance_summary_bar()
        main_layout.addWidget(self._stock_performance_summary_bar)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_stock_performance_summary_bar(self) -> None:
        bar = QWidget(self)
        bar.setObjectName("stockRegisterPerformanceSummaryBar")
        bar.setFocusPolicy(Qt.NoFocus)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bar.setMinimumWidth(0)
        bar.setStyleSheet("background: transparent; border: 0;")
        content = QWidget(bar)
        content.setObjectName("stockRegisterPerformanceSummaryContent")
        content.setFocusPolicy(Qt.NoFocus)
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("background: transparent; border: 0;")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        labels: dict[str, QLabel] = {}
        widths = stock_register_performance_display_widths(bar.fontMetrics())
        slots = stock_register_performance_slot_widths(bar.fontMetrics())

        def add_label(
            key: str,
            text: str,
            width: int,
            alignment: Qt.AlignmentFlag = Qt.AlignCenter,
        ) -> QLabel:
            label = QLabel(text, content)
            label.setObjectName(f"stockRegisterPerformanceSummary{key.title()}")
            label.setFocusPolicy(Qt.NoFocus)
            label.setAlignment(alignment)
            label.setFixedWidth(width)
            label.setStyleSheet("background: transparent;")
            layout.addWidget(label, 0, Qt.AlignVCenter)
            labels[key] = label
            return label

        title_slot = QWidget(content)
        title_slot.setObjectName("stockRegisterPerformanceSummaryTitleSlot")
        title_slot.setFocusPolicy(Qt.NoFocus)
        title_slot.setAttribute(Qt.WA_StyledBackground, True)
        title_slot.setFixedWidth(slots["count"])
        title_slot.setStyleSheet("background: transparent; border: 0;")
        title_slot_layout = QHBoxLayout(title_slot)
        title_slot_layout.setContentsMargins(0, 0, 0, 0)
        title_slot_layout.setSpacing(0)
        title_slot_layout.addStretch(1)
        title_label = QLabel("전체결산", title_slot)
        title_label.setObjectName("stockRegisterPerformanceSummaryTitle")
        title_label.setFocusPolicy(Qt.NoFocus)
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        title_label.setAlignment(Qt.AlignCenter)
        title_width = max(64, title_label.fontMetrics().horizontalAdvance("전체결산") + 20)
        title_label.setFixedSize(title_width, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        title_label.setStyleSheet(
            auto_trade_setting_badge_stylesheet(
                "QLabel",
                text_color=AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                border_color=AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
            )
        )
        title_slot_layout.addWidget(title_label, 0, Qt.AlignVCenter)
        title_slot_layout.addStretch(1)
        layout.addWidget(title_slot, 0, Qt.AlignVCenter)
        labels["title"] = title_label
        labels["title_slot"] = title_slot
        add_label("sep_profit", "│", widths["separator"], Qt.AlignCenter)
        add_label("profit_prefix", "손익(", widths["rate_prefix"], Qt.AlignLeft | Qt.AlignVCenter)
        add_label("profit", "0.00%", widths["rate_value"], Qt.AlignRight | Qt.AlignVCenter)
        add_label("profit_close", ")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)
        add_label("sep_amount", "│", widths["separator"], Qt.AlignCenter)
        add_label("amount_prefix", "금액(", widths["amount_prefix"], Qt.AlignLeft | Qt.AlignVCenter)
        add_label("amount", "0원", widths["amount_value"], Qt.AlignRight | Qt.AlignVCenter)
        add_label("amount_close", ")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)
        add_label("sep_efficiency", "│", widths["separator"], Qt.AlignCenter)
        add_label("efficiency_prefix", "효율(", widths["efficiency_prefix"], Qt.AlignLeft | Qt.AlignVCenter)
        add_label("efficiency", "0.0", widths["efficiency_value"], Qt.AlignRight | Qt.AlignVCenter)
        add_label("efficiency_close", ")", widths["close"], Qt.AlignLeft | Qt.AlignVCenter)

        self._stock_performance_summary_bar = bar
        self._stock_performance_summary_content = content
        self._stock_performance_summary_labels = labels
        content.setFixedSize(layout.sizeHint())
        bar.setFixedHeight(content.height())
        self._position_stock_performance_summary_content()

    def _update_stock_performance_summary_bar(self, stocks: list[dict[str, object]]) -> None:
        labels = getattr(self, "_stock_performance_summary_labels", {})
        if not labels:
            return
        summary = stock_register_performance_summary_display(stocks)
        rate = str(summary.get("rate", "0.00%") or "0.00%")
        amount = str(summary.get("amount", "0원") or "0원")
        efficiency = str(summary.get("efficiency", "0.0") or "0.0")
        tooltip = str(summary.get("tooltip", "") or "")
        labels["profit"].setText(rate)
        labels["amount"].setText(amount)
        labels["efficiency"].setText(efficiency)
        labels["profit"].setStyleSheet(
            f"background: transparent; color: {stock_register_performance_value_color(rate)};"
            if stock_register_performance_value_color(rate)
            else "background: transparent;"
        )
        labels["amount"].setStyleSheet(
            f"background: transparent; color: {stock_register_performance_value_color(amount)};"
            if stock_register_performance_value_color(amount)
            else "background: transparent;"
        )
        labels["efficiency"].setStyleSheet("background: transparent;")
        for label in labels.values():
            label.setToolTip(tooltip)
        self._position_stock_performance_summary_content()

    def _position_stock_performance_summary_content(self) -> None:
        bar = getattr(self, "_stock_performance_summary_bar", None)
        content = getattr(self, "_stock_performance_summary_content", None)
        if bar is None or content is None:
            return
        x = bar.width() - content.width()
        labels = getattr(self, "_stock_performance_summary_labels", {})
        summary_profit_label = labels.get("profit_prefix") if isinstance(labels, dict) else None
        body_profit_label = None
        table = getattr(self, "stock_table", None)
        if table is not None:
            for row in range(table.rowCount()):
                widget = table.cellWidget(row, 4)
                if widget is None:
                    continue
                body_profit_label = widget.findChild(QLabel, "stockRegisterPerformanceRateStart")
                if body_profit_label is not None:
                    break
        if body_profit_label is not None and summary_profit_label is not None:
            body_profit_x = body_profit_label.mapTo(self, QPoint(0, 0)).x()
            bar_x = bar.mapTo(self, QPoint(0, 0)).x()
            x = body_profit_x - bar_x - summary_profit_label.x()
        y = max(0, (bar.height() - content.height()) // 2)
        content.move(x, y)

    def _setup_stock_performance_sort_badges(self) -> None:
        container = QWidget(self)
        container.setObjectName("stockRegisterPerformanceSortBadges")
        container.setFocusPolicy(Qt.NoFocus)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        buttons: dict[str, QPushButton] = {}
        for key in ("count", "rate", "amount", "efficiency"):
            button = QPushButton(container)
            button.setObjectName(f"stockRegisterPerformanceSort{key.title()}Badge")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.setMinimumSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.setMaximumSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_key=key:
                self.set_stock_performance_sort(target_key)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            buttons[key] = button

        container.setFixedSize(layout.sizeHint())
        self._stock_performance_sort_badge_container = container
        self._stock_performance_sort_buttons = buttons
        self._update_stock_performance_sort_badges()

    def _position_stock_performance_sort_badges(self) -> None:
        return
    def _update_stock_performance_sort_badges(self) -> None:
        selected_key = str(getattr(self, "_stock_performance_sort_key", "amount") or "amount")
        for key, button in getattr(self, "_stock_performance_sort_buttons", {}).items():
            active = key == selected_key
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            label = STOCK_REGISTER_PERFORMANCE_SORT_LABELS.get(key, key)
            button.setText(label)
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=color,
                )
            )

    def set_stock_performance_sort(self, key: str) -> None:
        normalized = str(key or "").strip()
        if normalized not in STOCK_REGISTER_PERFORMANCE_SORT_ROLES:
            return
        if normalized == self._stock_performance_sort_key:
            self._stock_performance_sort_order = (
                Qt.AscendingOrder
                if self._stock_performance_sort_order == Qt.DescendingOrder
                else Qt.DescendingOrder
            )
        else:
            self._stock_performance_sort_key = normalized
            self._stock_performance_sort_order = Qt.DescendingOrder
        self._apply_stock_performance_sort_role()
        self._update_stock_performance_sort_badges()
        self.stock_table.sortItems(4, self._stock_performance_sort_order)
        self.stock_table.horizontalHeader().setSortIndicatorShown(False)
        self._position_stock_performance_sort_badges()

    def _apply_stock_performance_sort_role(self) -> None:
        source_role = STOCK_REGISTER_PERFORMANCE_SORT_ROLES.get(
            str(getattr(self, "_stock_performance_sort_key", "amount") or "amount"),
            STOCK_REGISTER_PERFORMANCE_SORT_ROLES["amount"],
        )
        for row in range(self.stock_table.rowCount()):
            item = self.stock_table.item(row, 4)
            if item is not None:
                item.setData(SORT_ROLE, item.data(source_role) or 0.0)

    def _resize_to_stock_table_columns(self) -> None:
        self._unlock_stock_register_width()
        table_width = (
            self.stock_table.verticalHeader().width()
            + sum(
                self.stock_table.columnWidth(column)
                for column in range(self.stock_table.columnCount())
            )
            + (self.stock_table.frameWidth() * 2)
            + self.stock_table.verticalScrollBar().sizeHint().width()
        )
        buttons = [
            self.btn_search_register,
            self.btn_manual_register,
            self.btn_delete_stock,
            self.btn_close,
        ]
        layout = self.layout()
        layout_margins = layout.contentsMargins() if layout is not None else None
        layout_horizontal_margin = (
            layout_margins.left() + layout_margins.right()
            if layout_margins is not None
            else 0
        )
        button_spacing = self._stock_register_button_layout.spacing()
        button_width = (
            sum(button.sizeHint().width() for button in buttons)
            + max(0, len(buttons) - 1) * max(0, button_spacing)
        )
        border_width = max(0, self.frameGeometry().width() - self.geometry().width())
        header_width = int(getattr(self, "_stock_register_header_required_width", 0) or 0)
        required_width = max(table_width, button_width, header_width) + layout_horizontal_margin + border_width
        screen = QApplication.primaryScreen()
        if screen is not None:
            available_width = screen.availableGeometry().width()
            if available_width > 0:
                required_width = min(required_width, available_width)
        self.resize(required_width, 560)
        self._position_stock_performance_sort_badges()

    def _ensure_stock_table_viewport_width(self) -> None:
        if not self.isVisible():
            QTimer.singleShot(0, self._ensure_stock_table_viewport_width)
            return
        self._unlock_stock_register_width()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        screen = QApplication.primaryScreen()
        available_width = 0
        if screen is not None:
            available_width = screen.availableGeometry().width()
        column_width = sum(
            self.stock_table.columnWidth(column)
            for column in range(self.stock_table.columnCount())
        )
        viewport_deficit = column_width - self.stock_table.viewport().width()
        if viewport_deficit > 0 and (available_width <= 0 or self.width() < available_width):
            corrected_width = self.width() + viewport_deficit
            if available_width > 0:
                corrected_width = min(corrected_width, available_width)
            self.resize(corrected_width, self.height())
        self._position_stock_performance_sort_badges()
        self._lock_stock_register_width()

    def _unlock_stock_register_width(self) -> None:
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)

    def _lock_stock_register_width(self) -> None:
        width = self.width()
        if width > 0:
            self.setMinimumWidth(width)
            self.setMaximumWidth(width)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_stock_performance_sort_badges()
        self._position_stock_performance_summary_content()
        QTimer.singleShot(0, self._position_stock_performance_sort_badges)
        QTimer.singleShot(0, self._position_stock_performance_summary_content)
        if not self._integrity_auto_check_started:
            self._integrity_auto_check_started = True
            QTimer.singleShot(0, self.run_initial_integrity_check)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_stock_performance_sort_badges()
        self._position_stock_performance_summary_content()

    def _sync_stock_table_body_background(self) -> str:
        body_color = self.stock_table.viewport().palette().color(QPalette.Base)
        for widget in (self.stock_table.verticalHeader(), self.stock_table.verticalHeader().viewport()):
            palette = widget.palette()
            for role in (QPalette.Button, QPalette.Window, QPalette.Base):
                palette.setColor(role, body_color)
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)
        return body_color.name()

    def _setup_stock_table(self) -> None:
        headers = [
            "종목코드",
            "종목명",
            "연결 루틴",
            "운영상태",
            "실적",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.stock_table.horizontalHeader().setStretchLastSection(False)
        self.stock_table.setShowGrid(False)
        self.stock_table.setFrameShape(QFrame.NoFrame)
        self.stock_table.setColumnWidth(0, 105)
        self.stock_table.setColumnWidth(1, 165)
        self.stock_table.setColumnWidth(2, 250)
        self.stock_table.setColumnWidth(3, 120)
        self.stock_table.setColumnWidth(
            4,
            stock_register_performance_column_width(self.stock_table.fontMetrics()),
        )
        self.stock_table.setWordWrap(False)
        self.stock_table.verticalHeader().setDefaultSectionSize(42)
        body_background = self._sync_stock_table_body_background()
        self.stock_table.setStyleSheet(
            f"QTableWidget {{ background: {body_background}; border: 0; }}"
            f"QTableWidget::viewport {{ background: {body_background}; border: 0; }}"
            f"QHeaderView {{ background: {body_background}; border: 0; }}"
            f"QHeaderView::section {{ background: {body_background}; border: 0; }}"
            f"QHeaderView::section:vertical {{ "
            f"background: {body_background}; "
            "border: 0; "
            "}"
            f"QTableCornerButton::section {{ "
            f"background: {body_background}; "
            "border: 0; "
            "}"
            + TABLE_LIGHT_SELECTION_STYLE
        )
        self.stock_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stock_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setSortingEnabled(True)
        self.stock_table.horizontalHeader().setSortIndicatorShown(False)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.btn_close.clicked.connect(self.close)
        self.btn_manual_register.clicked.connect(self.open_manual_register_dialog)
        self.btn_stock_history.clicked.connect(self.open_selected_stock_history)
        self.btn_delete_stock.clicked.connect(self.delete_selected_stock)
        self.stock_search_input.textChanged.connect(self.refresh_stock_table)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.itemClicked.connect(self.on_stock_table_item_clicked)
        self.stock_table.customContextMenuRequested.connect(self.show_stock_table_context_menu)

    def _create_integrity_status_container(self) -> QWidget:
        container = QWidget(self)
        container.setObjectName("stockRegisterIntegrityStatusContainer")
        container.setFocusPolicy(Qt.NoFocus)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent; border: 0;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(18, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.integrity_status_icon_label, 0, Qt.AlignVCenter)
        layout.addWidget(self.integrity_status_label, 0, Qt.AlignVCenter)
        container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        return container

    def _integrity_status_reserved_width(self) -> int:
        text = "서버/로컬 상태 : 서버 정합성 미확인"
        margins = self._integrity_status_container.layout().contentsMargins()
        spacing = self._integrity_status_container.layout().spacing()
        return (
            margins.left()
            + self.integrity_status_icon_label.width()
            + spacing
            + self.integrity_status_label.fontMetrics().horizontalAdvance(text)
            + 12
        )

    def _set_integrity_status_text(self, message: str, *, dot_color: str) -> None:
        display_message = str(message or "").strip()
        dot = auto_trade_status_dot("등록대기") if display_message else ""
        self.integrity_status_icon_label.setText(dot)
        self.integrity_status_icon_label.setFont(self.stock_table.font())
        dot_metrics = self.integrity_status_icon_label.fontMetrics()
        self.integrity_status_icon_label.setFixedSize(
            dot_metrics.horizontalAdvance(dot),
            dot_metrics.height(),
        )
        self.integrity_status_icon_label.setStyleSheet(
            f"background: transparent; color: {dot_color};"
            if dot
            else "background: transparent;"
        )
        self._integrity_status_container.layout().setSpacing(
            dot_metrics.horizontalAdvance(" ")
        )
        self.integrity_status_label.setText(display_message)
        label_width = (
            self.integrity_status_label.fontMetrics().horizontalAdvance(display_message) + 4
            if display_message
            else 0
        )
        self.integrity_status_label.setFixedWidth(label_width)
        self.integrity_status_label.setStyleSheet("background: transparent; color: #202124;")
        self._integrity_status_container.adjustSize()
        tooltip = f"{dot} {display_message}".strip()
        self.integrity_status_icon_label.setToolTip(tooltip)
        self.integrity_status_label.setToolTip(tooltip)

    def _set_server_local_integrity_status(self, server_status: object) -> None:
        normalized_status = str(server_status or "").strip().upper()
        display_by_status = {
            SERVER_STATUS_NOT_CHECKED: "서버 정합성 미확인",
        }
        display_status = display_by_status.get(normalized_status)
        if display_status is None:
            return
        self._set_integrity_status_text(
            f"서버/로컬 상태 : {display_status}",
            dot_color=auto_trade_status_color("등록대기"),
        )

    def _show_integrity_toast(self, message: str) -> None:
        self._last_integrity_toast_message = message
        parent = persistent_feature_owner(self)
        popup = getattr(parent, "showAutoTradePopupMessage", None)
        if callable(popup):
            popup(message)
            return
        show_toast(self, message, duration_ms=2500, position="bottom_right")

    def _review_writer_callback(self):
        parent = persistent_feature_owner(self)
        writer = getattr(parent, "mark_review_required", None)
        return writer if callable(writer) else None

    def _review_required_stock_summary(self, result: dict[str, object]) -> str:
        issues = result.get("issues", [])
        names: list[str] = []
        seen: set[tuple[str, str]] = set()
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                if issue.get("requires_review") is not True:
                    continue
                if str(issue.get("execution_status", "") or "").strip().upper() != LOCAL_STATUS_REVIEW_REQUIRED:
                    continue
                code = str(issue.get("stock_code", "") or "").strip()
                name = str(issue.get("stock_name", "") or "").strip()
                key = (code, name)
                if key in seen:
                    continue
                seen.add(key)
                title = f"{code} {name}".strip()
                if title:
                    names.append(title)
        if not names:
            return "검토관리 대상 종목 있음"
        if len(names) == 1:
            return f"검토관리 {names[0]}"
        return f"검토관리 {names[0]} 외 {len(names) - 1}종목"

    def _integrity_writer_failed(self, result: dict[str, object]) -> bool:
        issues = result.get("issues", [])
        if not isinstance(issues, list):
            return False
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("issue_code", "") or "").strip() == "CHECK_ERROR" and "Review writer failed" in str(issue.get("message", "") or ""):
                return True
        return False

    def run_initial_integrity_check(self) -> None:
        try:
            result = run_local_stock_integrity_check(PROJECT_ROOT)
            self._set_server_local_integrity_status(result.get("server_status"))
            checked_count = int(result.get("checked_stock_count", 0) or 0)
            local_status = str(result.get("local_status", "") or "").strip().upper()

            if checked_count == 0:
                self._last_integrity_check_result = result
                self._show_integrity_toast("검사 대상 종목이 없습니다.")
                return

            if local_status == LOCAL_STATUS_REVIEW_REQUIRED:
                writer = self._review_writer_callback()
                if writer is None:
                    self._last_integrity_check_result = result
                    self._show_integrity_toast("무결성 검사 처리 오류 | 검토관리 반영에 실패했습니다.")
                    return

                result = apply_integrity_review_required_issues(
                    result,
                    project_root=PROJECT_ROOT,
                    review_writer=writer,
                    source="무결성검사",
                )
                self._last_integrity_check_result = result
                self.refresh_stock_table()
                if self._integrity_writer_failed(result):
                    self._show_integrity_toast("무결성 검사 처리 오류 | 검토관리 반영에 실패했습니다.")
                    return
                self._show_integrity_toast("무결성 검사 완료 | 검토관리 대상 종목이 있습니다.")
                return

            self._last_integrity_check_result = result
            if local_status == LOCAL_STATUS_CHECK_ERROR:
                self._show_integrity_toast("무결성 검사 오류 | 종목관리창을 다시 실행하세요.")
                return

            if local_status == LOCAL_STATUS_INTEGRITY_ISSUE:
                self._show_integrity_toast("로컬 의미 무결성 검사 완료 | 종목 상태값을 확인하세요.")
                return

            if local_status == LOCAL_STATUS_PASS:
                self._show_integrity_toast("로컬 무결성 검사 완료 | 서버 정합성 검사는 실행하지 않았습니다.")
                return

            self._show_integrity_toast("무결성 검사 오류 | 종목관리창을 다시 실행하세요.")
        except Exception:
            LOGGER.exception("Stock register integrity auto check failed")
            self._show_integrity_toast("무결성 검사 오류 | 종목관리창을 다시 실행하세요.")


    def on_stock_selection_changed(self) -> None:
        selected_rows = self.stock_table.selectionModel().selectedRows()
        self.btn_delete_stock.setEnabled(len(selected_rows) == 1)
        self.btn_stock_history.setEnabled(self.can_open_selected_stock_history())

    def selected_stock_history_target(self) -> tuple[Path, str, str, str] | None:
        """종목이력 창을 열 수 있는 단일 선택 대상 정보를 반환한다."""
        selected_stocks = self.selected_registered_stocks()
        if len(selected_stocks) != 1:
            return None

        code, name = selected_stocks[0]
        runtime_dirs = stock_runtime_dirs_for_stock(code, name)
        if len(runtime_dirs) != 1:
            return None

        routine_name, stock_dir = runtime_dirs[0]
        return stock_dir, routine_name, code, name

    def can_open_selected_stock_history(self) -> bool:
        return self.selected_stock_history_target() is not None

    def open_selected_stock_history(self) -> None:
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.information(
                self,
                "종목이력",
                "조회할 종목을 하나 선택하세요.",
            )
            return

        if len(selected_stocks) != 1:
            QMessageBox.information(
                self,
                "종목이력",
                "종목이력은 한 종목씩 확인할 수 있습니다.",
            )
            return

        code, name = selected_stocks[0]
        runtime_dirs = stock_runtime_dirs_for_stock(code, name)
        if not runtime_dirs:
            QMessageBox.information(
                self,
                "종목이력",
                "해당 종목의 이력 저장 위치를 찾을 수 없습니다.",
            )
            return

        if len(runtime_dirs) != 1:
            QMessageBox.information(
                self,
                "종목이력",
                "해당 종목의 이력 저장 위치가 여러 개입니다.",
            )
            return

        routine_name, stock_dir = runtime_dirs[0]
        dialog = OrderStatusWindow(
            stock_dir=stock_dir,
            routine_name=routine_name,
            stock_code=code,
            stock_name=name,
            parent=self,
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        windows = getattr(self, "_order_status_windows", None)
        if not isinstance(windows, set):
            windows = set()
            self._order_status_windows = windows
        windows.add(dialog)
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: windows.discard(target)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def on_stock_table_item_clicked(self, item: QTableWidgetItem) -> None:
        """
        종목등록설정 창에서 종목 행을 1회 클릭했을 때의 보조 처리.

        itemClicked 시그널 연결은 유지하되, 실제 삭제 버튼 활성화 여부는
        현재 선택 상태를 기준으로 다시 계산한다.
        더블클릭으로 매매루틴등록 창을 여는 기존 동작은 변경하지 않는다.
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
        action_clear = menu.addAction("선택 해제")
        menu.addSeparator()
        action_delete = menu.addAction("종목초기화")
        menu.addSeparator()
        routine_actions: dict[object, tuple[RoutineInstanceRecord, RoutineDefinitionRecord]] = {}
        routine_targets = self.available_routine_assign_targets()
        if routine_targets:
            action_assign = menu.addMenu("루틴등록")
            grouped_targets: dict[str, tuple[RoutineDefinitionRecord, list[RoutineInstanceRecord]]] = {}
            for instance, definition in routine_targets:
                key = str(definition.definition_id)
                if key not in grouped_targets:
                    grouped_targets[key] = (definition, [])
                grouped_targets[key][1].append(instance)

            for _key, (definition, instances) in sorted(
                grouped_targets.items(),
                key=lambda item: str(item[1][0].display_name),
            ):
                if routine_actions:
                    action_assign.addSeparator()
                group_action = action_assign.addAction(f"● {definition.display_name}")
                group_action.setEnabled(False)
                for instance in sorted(instances, key=lambda item: str(item.display_name)):
                    action = action_assign.addAction(f"    {instance.display_name}")
                    action.setData(str(instance.instance_id))
                    routine_actions[action] = (instance, definition)
        else:
            action_assign = menu.addAction("루틴등록")
        action_unassign = menu.addAction("루틴해제")

        has_selected = selected_count > 0
        action_assign.setEnabled(has_selected)
        action_unassign.setEnabled(has_selected)
        action_delete.setEnabled(selected_count == 1)

        selected_action = menu.exec_(self.stock_table.viewport().mapToGlobal(position))
        if selected_action is None:
            return

        if selected_action in routine_actions:
            instance, definition = routine_actions[selected_action]
            self.assign_selected_stocks_to_routine_instance(instance, definition)
        elif selected_action == action_unassign:
            self.unassign_selected_stock_routines()
        elif selected_action == action_delete:
            self.delete_selected_stock()
        elif selected_action == action_select_all:
            self.select_all_visible_stocks()
        elif selected_action == action_clear:
            self.stock_table.clearSelection()
            self.on_stock_selection_changed()

    def available_routine_assign_targets(self) -> list[tuple[RoutineInstanceRecord, RoutineDefinitionRecord]]:
        targets: list[tuple[RoutineInstanceRecord, RoutineDefinitionRecord]] = []
        for instance in load_persisted_routine_instances():
            definition = routine_definition_by_id(instance.definition_id)
            if definition is not None:
                targets.append((instance, definition))
        return targets

    def assign_selected_stocks_to_routine_instance(
        self,
        selected_instance: RoutineInstanceRecord,
        selected_definition: RoutineDefinitionRecord,
    ) -> None:
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.warning(self, "선택 오류", "등록할 종목을 1개 이상 선택하세요.")
            return

        selected_routine_name = selected_instance.display_name
        selected_routine_type = selected_definition.display_name
        applied_items: list[str] = []
        created_paths: list[str] = []
        blocked_items: list[dict[str, object]] = []
        skipped_items: list[str] = []

        for code, name in selected_stocks:
            stocks = read_base_stocks()
            existing_routine_list: list[str] = []
            for stock in stocks:
                if str(stock.get("code", "")).strip() == code and str(stock.get("name", "")).strip() == name:
                    routines = stock.get("routines", [])
                    if isinstance(routines, list):
                        existing_routine_list = [str(item).strip() for item in routines if str(item).strip()]
                    break

            if existing_routine_list:
                skipped_items.append(f"{code} {name}: 기존 루틴({', '.join(existing_routine_list)})")
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
                skipped_items.append(f"{code} {name}: 라이브러리 확인 실패")
                continue

            repo = stock_repository_factory()
            resolved_stock_dir = repo.resolve_stock_dir(code, name)
            previous_config = (
                read_json_dict(resolved_stock_dir / "config.json")
                if isinstance(resolved_stock_dir, Path)
                else {}
            )
            if not update_base_stock_routine_instance(
                code,
                name,
                instance_id=selected_instance.instance_id,
                instance_name=selected_instance.display_name,
                definition_id=selected_definition.definition_id,
                routine_type=selected_definition.display_name,
            ):
                skipped_items.append(f"{code} {name}: 저장 실패")
                continue

            try:
                stock_dir = repo.ensure_stock_folder(
                    code,
                    name,
                    routine=selected_routine_type,
                )
                apply_default_operation_exclusion_for_new_running_assignment(
                    self,
                    stock_dir,
                    previous_config,
                )
            except Exception:
                skipped_items.append(f"{code} {name}: stocks 저장 실패")
                continue

            created_paths.append(str(stock_dir.relative_to(PROJECT_ROOT)))
            ensure_single_real_trade_routine_for_stock(code, name, selected_routine_type)
            applied_items.append(f"{code},{name}({selected_routine_name})")

        if applied_items:
            append_changelog(
                "UPDATE",
                "PROJECT_CHANGELOG.txt",
                f"종목관리 루틴등록: {' / '.join(applied_items)} -> {selected_routine_name}",
            )
        if created_paths:
            append_changelog(
                "ADD",
                "종목 런타임",
                f"종목 폴더 생성 및 기본 제외 적용: {' / '.join(created_paths)}",
            )

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = persistent_feature_owner(self)
        if parent is not None and hasattr(parent, "refresh_auto_trade_assignment_views"):
            parent.refresh_auto_trade_assignment_views()
        elif parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        unavailable_count = len(blocked_items) + len(skipped_items)
        show_toast(
            self,
            f"루틴등록 {len(applied_items)}종목 | 등록불가 {unavailable_count}종목",
        )

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
        """현재 화면에서 연결 루틴이 등록대기인 종목만 선택한다."""
        self.stock_table.clearSelection()
        selection_model = self.stock_table.selectionModel()
        if selection_model is None:
            return
        for row in range(self.stock_table.rowCount()):
            routine_item = self.stock_table.item(row, 2)
            routine_text = routine_item.text().strip() if routine_item is not None else ""
            if routine_text != "등록대기":
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
            QMessageBox.warning(self, "선택 오류", "루틴해제할 종목을 1개 이상 선택하세요.")
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
                QMessageBox.information(self, "루틴해제 없음", "선택 종목은 이미 등록대기 상태입니다.")
            else:
                QMessageBox.information(self, "루틴해제 없음", "루틴해제할 종목이 없습니다.")
            return

        removed_items: list[str] = []
        for code, name, routine_name in allowed:
            if update_base_stock_routines(code, name, []):
                ensure_single_real_trade_routine_for_stock(code, name)
                removed_items.append(f"{code},{name}({routine_name})")

        if removed_items:
            append_changelog(
                "UPDATE",
                "중앙 종목관리",
                f"종목관리 루틴해제: {' / '.join(removed_items)} / runtime 폴더 유지",
            )

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = persistent_feature_owner(self)
        if parent is not None and hasattr(parent, "refresh_auto_trade_assignment_views"):
            parent.refresh_auto_trade_assignment_views()
        elif parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        show_toast(
            self,
            f"루틴해제 {len(removed_items)}종목 | 해제불가 {len(blocked_items)}종목",
        )

    def delete_selected_stock(self) -> None:
        """
        선택 종목을 영구 초기화한다.
        """
        selected_rows = self.stock_table.selectionModel().selectedRows()

        if not selected_rows:
            QMessageBox.warning(
                self,
                "선택 오류",
                "초기화할 종목을 하나 선택하세요.",
            )
            return

        if len(selected_rows) != 1:
            QMessageBox.warning(
                self,
                "선택 오류",
                "초기화할 종목을 하나만 선택하세요.",
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
                "초기화 오류",
                "선택한 종목의 코드 또는 이름을 확인할 수 없습니다.\n\n"
                f"문제 행: {', '.join(str(row) for row in invalid_rows)}",
            )
            return

        if not selected_stocks:
            QMessageBox.warning(
                self,
                "선택 오류",
                "초기화할 종목의 정보를 찾을 수 없습니다.",
            )
            return

        seen_stocks: set[tuple[str, str]] = set()
        unique_stocks: list[tuple[str, str]] = []
        for code, name in selected_stocks:
            key = (code, name)
            if key in seen_stocks:
                continue
            seen_stocks.add(key)
            unique_stocks.append(key)

        if len(unique_stocks) != 1:
            QMessageBox.warning(
                self,
                "선택 오류",
                "초기화할 종목을 하나만 선택하세요.",
            )
            return

        code, name = unique_stocks[0]
        eligibility = stock_reset_eligibility(code, name)
        if eligibility.get("status") != STOCK_RESET_INITIALIZABLE:
            show_toast(self, _stock_reset_failure_message())
            return

        first_stock_dir = eligibility.get("stock_dir")
        if not isinstance(first_stock_dir, Path):
            show_toast(self, _stock_reset_failure_message())
            return

        if not confirm_stock_reset(self, code, name):
            return

        verified_eligibility = stock_reset_eligibility(code, name)
        if verified_eligibility.get("status") != STOCK_RESET_INITIALIZABLE:
            show_toast(self, _stock_reset_failure_message())
            return

        verified_stock_dir = verified_eligibility.get("stock_dir")
        if not isinstance(verified_stock_dir, Path):
            show_toast(self, _stock_reset_failure_message())
            return

        try:
            if first_stock_dir.resolve(strict=True) != verified_stock_dir.resolve(strict=True):
                show_toast(self, _stock_reset_failure_message())
                return
        except Exception:
            show_toast(self, _stock_reset_failure_message())
            return

        delete_result = delete_stock_project_data(code, name, verified_stock_dir)
        if delete_result.get("status") != "DELETED":
            self.refresh_stock_table()
            show_toast(self, _stock_reset_failure_message())
            return

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)
        self.btn_stock_history.setEnabled(False)

        parent = persistent_feature_owner(self)
        if parent is not None and hasattr(parent, "refresh_auto_trade_assignment_views"):
            parent.refresh_auto_trade_assignment_views()
        elif parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        show_toast(self, "초기화 완료")

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
        owner = persistent_feature_owner(self)
        running_targets = (
            auto_trade_running_registered_operation_targets(owner)
            if owner is not None
            else []
        )
        current_running_codes: set[str] = set()
        for _stock_dir, code, _name in running_targets:
            normalized_code = normalize_stock_code(code)
            if normalized_code:
                current_running_codes.add(normalized_code)
        keyword_text = self.stock_search_input.text().strip().lower() if hasattr(self, "stock_search_input") else ""
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]

        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            code = str(stock.get("code", "")).strip().lower()
            name = str(stock.get("name", "")).strip().lower()
            routines = stock.get("routines", [])
            routine_text = ",".join(str(item).strip().lower() for item in routines) if isinstance(routines, list) else str(routines).lower()
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            registered_routine = stock_register_routine_display_name(stock).lower()
            status_routine = routine_list[0] if routine_list else "등록대기"
            operation_status = active_stock_register_status_display(
                code,
                name,
                status_routine,
                current_running=normalize_stock_code(code) in current_running_codes,
            ).lower()

            searchable_values = [
                code,
                name,
                routine_text,
                registered_routine,
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

        self._update_stock_performance_summary_bar(stocks)

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

            registered_routine = stock_register_routine_display_name(stock)
            routine_tooltip = registered_routine
            status_routine = routine_list[0] if routine_list else "등록대기"
            operation_status = active_stock_register_status_display(
                code,
                name,
                status_routine,
                current_running=normalize_stock_code(code) in current_running_codes,
            )
            performance = stock_register_performance_display(stock)

            values = [
                code,
                name,
                registered_routine,
                operation_status,
                str(performance.get("text", "")),
            ]

            for col, value in enumerate(values):
                if col == 3:
                    renderer_source = stock_register_operation_status_renderer_source(value)
                    if renderer_source is None:
                        item = SortableTableWidgetItem(value)
                    else:
                        item = create_auto_trade_status_item(renderer_source)
                        item.setText(item.text().replace(renderer_source, value))
                        status_color = stock_register_operation_status_color(value)
                        if status_color:
                            item.setForeground(QColor(status_color))
                    item.setData(SORT_ROLE, stock_register_operation_status_rank(value))
                    item.setTextAlignment(Qt.AlignCenter)
                elif col == 4:
                    item = SortableTableWidgetItem("")
                    item.setData(Qt.UserRole, value)
                    item.setData(
                        STOCK_REGISTER_PERFORMANCE_SORT_ROLES["count"],
                        performance.get("sort_count", 0.0),
                    )
                    item.setData(
                        STOCK_REGISTER_PERFORMANCE_SORT_ROLES["rate"],
                        performance.get("sort_rate", 0.0),
                    )
                    item.setData(
                        STOCK_REGISTER_PERFORMANCE_SORT_ROLES["amount"],
                        performance.get("sort_amount", performance.get("sort", 0.0)),
                    )
                    item.setData(
                        STOCK_REGISTER_PERFORMANCE_SORT_ROLES["efficiency"],
                        performance.get("sort_efficiency", 0.0),
                    )
                    item.setData(SORT_ROLE, performance.get("sort_amount", performance.get("sort", 0.0)))
                    item.setTextAlignment(Qt.AlignCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(
                    routine_tooltip
                    if col == 2
                    else str(performance.get("tooltip", "") or value)
                    if col == 4
                    else value
                )
                self.stock_table.setItem(row, col, item)
                if col == 4:
                    self.stock_table.setCellWidget(
                        row,
                        col,
                        stock_register_performance_widget(performance),
                    )

        self.stock_table.resizeRowsToContents()
        self._apply_stock_performance_sort_role()
        self.stock_table.setSortingEnabled(True)
        if sort_column == 4:
            sort_order = self._stock_performance_sort_order
        if 0 <= sort_column < self.stock_table.columnCount():
            self.stock_table.sortItems(sort_column, sort_order)
        self.stock_table.horizontalHeader().setSortIndicatorShown(False)
        self.stock_table.blockSignals(False)
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)
        self.btn_stock_history.setEnabled(False)
        self._position_stock_performance_sort_badges()
    def open_search_register_dialog(self) -> None:
        """
        검색등록은 현재 검색 입력과 수동등록 경로로 통합되어 있다.
        """
        return

    def open_manual_register_dialog(self) -> None:
        """
        종목등록 버튼에서 공식 검색등록 UI를 연다.
        """
        from gui_auto_trade_setting_window import InstanceStockSearchRegisterDialog

        dialog = InstanceStockSearchRegisterDialog(
            self,
            instance_metadata={
                "row_kind": "unassigned",
                "target_kind": "unassigned",
                "instance_id": "",
                "instance_name": "등록대기",
                "definition_id": "",
                "definition_name": "등록대기",
            },
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self.manual_register_window = dialog
        dialog.finished.connect(lambda _result: self.refresh_stock_table())
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: (
                setattr(self, "manual_register_window", None)
                if getattr(self, "manual_register_window", None) is target
                else None
            )
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

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
            "아직 연결되지 않은 기능입니다.",
        )
