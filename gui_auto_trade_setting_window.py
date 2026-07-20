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
import shutil
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect
from PyQt5.QtGui import QColor, QFont
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
    QGridLayout,
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
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from gui_styles import (
    apply_plain_table_header,
    apply_selected_routine_label_style,
)
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_stock_data import active_routine_for_stock, stock_runtime_dir_for_routine
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
from runtime_io import (
    read_json_dict,
    read_orders_data,
    write_json_if_missing,
)
from gui_auto_trade_runtime import (
    now_text,
    parse_stock_folder_name,
    get_stock_dirs_in_routine,
    write_state_json,
)
from gui_base_stock_service import (
    ensure_single_real_trade_routine_for_all_stocks,
    find_library_stock_by_code,
    is_valid_stock_code,
    load_stock_library,
    normalize_base_stock_single_routine_file,
    normalize_stock_code,
    read_base_stocks,
    single_routine_list,
    update_base_stock_routines,
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
    draw_stock_position_metric,
    yes_no_display,
    display_status_text_for_gui,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_auto_trade_policy import (
    auto_trade_setting_ats_after_regular_blocked,
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
)
from gui_auto_trade_integrity import (
    unique_review_reasons,
    is_review_required_state,
    is_review_required_stock_dir,
    auto_trade_setting_data_inconsistency_reasons,
    restart_initial_review_reason_for_stock,
    auto_trade_setting_server_mismatch_detected,
)
from gui_auto_trade_order_log import (
    open_auto_trade_log_view_window,
    open_auto_trade_order_status_window,
)
from gui_auto_trade_unregister import (
    AutoTradeUnregisterConfirmDialog,
    reset_runtime_orders_for_force_unregister,
    reset_runtime_state_for_force_unregister,
    unregister_selected_auto_trade_stocks,
)
from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu
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
    IndividualLiquidationSettingsDialog,
    ProfitLossEarlyCloseDialog,
    auto_trade_apply_selected_early_close,
    auto_trade_apply_selected_early_close_default,
    auto_trade_apply_selected_early_close_profit_loss,
    auto_trade_cancel_selected_early_close,
    auto_trade_open_selected_individual_liquidation_settings,
    auto_trade_save_selected_individual_liquidation_settings,
)
from gui_auto_trade_ats_ops import (
    auto_trade_open_selected_manual_ats_settings_dialog,
    auto_trade_save_selected_manual_ats_state,
    auto_trade_selected_manual_ats_state,
    auto_trade_set_selected_manual_ats_flag,
    auto_trade_show_selected_ats_immediate_sell_placeholder,
)
from gui_auto_trade_timer import (
    auto_trade_current_runtime_file_signature,
    auto_trade_current_time_policy_minute_key,
    auto_trade_on_runtime_file_timer_tick,
    auto_trade_on_time_policy_timer_tick,
)
from gui_auto_trade_status_ops import (
    auto_trade_operation_policy_protected_status,
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_resume_status_after_pause,
    auto_trade_set_selected_operation_mode,
    auto_trade_set_selected_schedule_operation_mode,
    auto_trade_set_selected_stocks_buy_end,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
)
from gui_auto_trade_run_control import (
    auto_trade_start_selected_auto_trades,
    auto_trade_stop_selected_auto_trades,
)
from gui_auto_trade_review_ops import (
    auto_trade_open_review_required_window,
    auto_trade_run_current_routine_stability_check,
)
from gui_auto_trade_table_loader import (
    auto_trade_load_selected_routine_stocks,
)
from gui_operation_environment import (
    OperationEnvironmentSettingsDialog,
    TimeComboWidget,
    default_operation_policy,
    read_operation_policy,
    write_operation_policy,
)
from gui_review_required_window import (
    GlobalReviewRequiredWindow,
)
from gui_routine_registry import (
    get_routine_dirs as registry_get_routine_dirs,
    routine_display_name as registry_routine_display_name,
    read_routine_budget,
)
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
from real_order_preflight_service import commit_real_order_preflight, preview_real_order_preflight


class StockPositionMetricDelegate(QStyledItemDelegate):
    """보유/가격/손익/미체결 셀의 숫자 슬롯을 우측 정렬해 그린다."""

    def paint(self, painter, option, index) -> None:
        text = str(index.data(Qt.DisplayRole) or "")
        color = (
            option.palette.highlightedText().color()
            if option.state & QStyle.State_Selected
            else option.palette.text().color()
        )
        if draw_stock_position_metric(
            painter,
            option.rect.adjusted(2, 0, -2, 0),
            text,
            color,
        ):
            return
        super().paint(painter, option, index)


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
        self._label.setText(str(message or ""))
        self.adjustSize()
        self._move_to_parent_center()
        self.show()
        self.raise_()
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, self.hide)

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
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
INVALID_ITEMS_LOG_PATH = PROJECT_ROOT / "invalid_items.log"
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
BLOCKED_ACTION_REPORT_DIR = PROJECT_ROOT / "reports" / "blocked_actions"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
REAL_TRADE_GUARD_PATH = PROJECT_ROOT / "runtime" / "real_trade_guard.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"


def startup_recovery_action_allowed(window, action: str) -> bool:
    """Enforce session recovery only for the real MainWindow production caller."""
    if not isinstance(window, AutoTradeSettingWindow):
        return True
    try:
        parent = window.parent()
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

    fill_result, position_result, reconciliation_result = _record_fill_and_position_from_chejan(
        downstream_source,
        normalized,
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
    live_context: dict[str, object],
) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, object] | None]:
    if chejan_result.get("recorded") is not True or chejan_result.get("next_stage") != "FILL_RECORD_REQUIRED":
        return None, None, None

    completed_steps = ["QUEUE_LIFECYCLE"]
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
        return fill_result, None, reconciliation

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
        return fill_result, position_result, reconciliation

    completed_steps.append("POSITION_UPDATE")
    reconciliation = mark_chejan_reconciliation_state(
        ORDER_QUEUE_PATH,
        chejan_result,
        required=False,
        completed_steps=completed_steps,
        context=live_context,
    )
    return fill_result, position_result, reconciliation


def get_routine_dirs() -> list[Path]:
    """루틴 원본 경로를 조회한다.

    신규 기준:
    - routines/<루틴명>/routine.json 패키지를 우선 인식한다.
    - 신규 패키지가 없을 때만 기존 _루틴폴더/budget.json을 fallback으로 사용한다.
    """
    return registry_get_routine_dirs()


def routine_display_name(routine_dir: Path) -> str:
    """루틴 원본 경로에서 GUI 표시 루틴명을 반환한다."""
    return registry_routine_display_name(routine_dir)









def append_changelog(change_type: str, filename: str, message: str) -> None:
    """
    GUI 조작으로 발생한 변경사항을 PROJECT_CHANGELOG.txt 에 기록한다.
    """
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )

    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    """
    종목별 logs/YYYYMMDD.log 에 GUI 조작 및 상태 변경 내역을 기록한다.

    주의:
    - 실제 키움 주문/체결 로그가 아니라 관리자 GUI 조작 로그이다.
    - logs 폴더가 없으면 생성한다.
    - 기록 실패는 GUI 흐름을 막지 않도록 조용히 무시한다.
    """
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None


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
            "after_buy_end_status": "감시/매도",
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
    return merged


class StockPolicyOverrideDialog(QDialog):
    """개별종목 예외설정 1차 UI.

    환경설정이 디폴트이고, 이 창은 해당 종목만 예외로 둘 때 사용한다.
    전체 리셋은 종목별 예외 설정값을 제거한다.
    """

    OVERRIDE_KEYS = (
        "policy_override_enabled",
        "operation_policy_override",
        "manual_operation_override",
        "scheduled_operation_override",
        "auto_close_override",
        "early_close_override",
        "liquidation_override",
    )

    def __init__(self, stock_dir: Path, code: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stock_dir = stock_dir
        self.code = code
        self.name = name
        self.config_path = stock_dir / "config.json"
        self.config = read_json_dict(self.config_path) or default_config()
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

    def write_config(self) -> None:
        self.config["updated_at"] = now_text()
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def save_override(self) -> None:
        self.config["policy_override_enabled"] = self.use_override.isChecked()
        self.config["policy_override_memo"] = self.memo.toPlainText().strip()
        self.config["policy_override_updated_at"] = now_text()
        try:
            self.write_config()
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 저장")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 저장: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"개별종목 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "저장 완료", "개별종목 설정을 저장했습니다.")
        self.accept()

    def reset_all_to_global(self) -> None:
        for key in self.OVERRIDE_KEYS:
            self.config.pop(key, None)
        self.config.pop("policy_override_memo", None)
        self.config["policy_override_enabled"] = False
        self.config["policy_override_reset_at"] = now_text()
        try:
            self.write_config()
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 전체 리셋")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 전체 리셋: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "리셋 오류", f"개별종목 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "리셋 완료", "해당 종목의 개별설정을 환경설정값으로 전체 리셋했습니다.")
        self.accept()


class AutoTradeSettingWindow(QDialog):
    """
    자동매매설정 창.

    1차 구현 범위:
    - 자동매매 루틴 목록 표시
    - 선택 루틴의 종목별 저장 폴더 표시
    - state.json 기준 상태 요약 표시
    - 실제 자동매매 시작/정지/삭제/환경설정/주문상태/로그 기능은 다음 단계에서 구현
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("자동매매설정")
        self.resize(1180, 680)
        self.setMinimumWidth(1180)

        self.routine_table = QTableWidget()
        self.stock_table = QTableWidget()

        self.btn_start = QPushButton("매매시작")
        self.btn_stop = QPushButton("강제종료")
        self.btn_stop.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.btn_early_close = QPushButton("조기 마감")
        self.btn_early_close.setStyleSheet("color: #2563eb; font-weight: bold;")
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
        self.btn_stop.setMinimumHeight(28)
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
        self.btn_delete = QPushButton("등록 해제")
        self.btn_order_view = QPushButton("주문상태 보기")
        self.btn_log_view = QPushButton("로그 보기")
        self.btn_review_view = QPushButton("검토관리")
        self.btn_refresh = QPushButton("안정성검사")
        self.btn_close = QPushButton("닫기")
        self._notification_popup = None

        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        # 헤더 정렬 후에는 정렬 규칙이 아니라 "그 순간의 화면 순서"를 보존한다.
        # 설정 변경/조기마감/개별청산 저장 중 종목 위치가 튀는 것을 막기 위한 고정 순서다.
        self._stock_visual_order: list[str] = []
        self._last_time_policy_minute_key = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._time_policy_timer = QTimer(self)
        self._time_policy_timer.setInterval(10_000)
        self._time_policy_timer.timeout.connect(self.on_time_policy_timer_tick)

        # 외부에서 state/config/orders 파일을 직접 수정한 경우 화면에 자동 반영한다.
        # 예: VSCode에서 보유수량/평단을 임시 입력하면 별도 버튼 없이 종목표가 갱신된다.
        self._runtime_file_snapshot: dict[str, int] = {}
        self._runtime_file_timer = QTimer(self)
        self._runtime_file_timer.setInterval(2_000)
        self._runtime_file_timer.timeout.connect(self.on_runtime_file_timer_tick)
        self._last_execution_preview_result: dict[str, object] | None = None
        self._last_execution_preview_queue_snapshot: dict[str, object] | None = None
        self._last_execution_enable_preview_result: dict[str, object] | None = None
        self._last_execution_enable_queue_snapshot: dict[str, object] | None = None
        self._last_real_preflight_preview_result: dict[str, object] | None = None
        self._last_real_preflight_queue_snapshot: dict[str, object] | None = None

        self._setup_ui()
        self._connect_events()

        self.refresh_all()
        self.update_startup_recovery_controls()
        self._runtime_file_snapshot = self.current_runtime_file_signature()
        self._time_policy_timer.start()
        self._runtime_file_timer.start()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        routine_box = QGroupBox("자동매매 루틴")
        routine_layout = QVBoxLayout()
        self._setup_routine_table()
        routine_layout.addWidget(self.routine_table)
        routine_box.setLayout(routine_layout)
        routine_box.setMaximumHeight(175)

        self.stock_box = QGroupBox()
        stock_layout = QVBoxLayout()
        self.selected_routine_label = QLabel("선택 루틴: -")
        apply_selected_routine_label_style(self.selected_routine_label)
        self.selected_routine_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.selected_routine_label.setMinimumHeight(28)
        self._setup_stock_table()

        selected_routine_header_layout = QHBoxLayout()
        selected_routine_header_layout.setContentsMargins(0, 0, 0, 0)
        selected_routine_header_layout.addWidget(self.selected_routine_label)
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
        selected_routine_header_layout.addWidget(self.btn_early_close)
        selected_routine_header_layout.addWidget(self.btn_stop)

        stock_layout.addLayout(selected_routine_header_layout)
        stock_layout.addWidget(self.stock_table)
        self.stock_box.setLayout(stock_layout)

        buttons = [
            self.btn_start,
            self.btn_set_schedule,
            self.btn_delete,
            self.btn_order_view,
            self.btn_log_view,
            self.btn_review_view,
            self.btn_refresh,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        # v20.9.1g: 좌우 분할 구조를 상하 구조로 변경한다.
        # 루틴 목록은 상단 요약 영역으로 압축하고, 종목표는 하단 전체 폭을 사용한다.
        main_layout.addWidget(routine_box, 0)
        main_layout.addWidget(self.stock_box, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_routine_table(self) -> None:
        headers = [
            "루틴명",
            "종목수",
            "총예산",
            "사용예산",
            "가용예산",
        ]

        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        self.routine_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.routine_table.horizontalHeader().setStretchLastSection(True)
        self.routine_table.setColumnWidth(0, 220)
        self.routine_table.setColumnWidth(1, 90)
        self.routine_table.setColumnWidth(2, 140)
        self.routine_table.setColumnWidth(3, 140)
        self.routine_table.setColumnWidth(4, 140)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.routine_table.setSortingEnabled(False)
        self.routine_table.horizontalHeader().setSectionsClickable(True)

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
            "미체결",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        header = self.stock_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.stock_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header.setMinimumSectionSize(30)
        self.stock_table.verticalHeader().setSectionsMovable(False)
        self.stock_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.stock_table.verticalHeader().setMinimumWidth(40)
        self.stock_table.verticalHeader().setMaximumWidth(40)
        self.stock_table.verticalHeader().setFixedWidth(40)
        self._stock_position_metric_delegate = StockPositionMetricDelegate(self.stock_table)
        for col in (7, 8, 9, 10):
            self.stock_table.setItemDelegateForColumn(
                col,
                self._stock_position_metric_delegate,
            )

        # 자동매매설정창 하단 종목표 고정폭 배분.
        # 보유/가격/손익/미체결은 관제 트리와 같은 묶음 단위로 표시한다.
        self._apply_stock_table_column_widths()
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.stock_table.setSortingEnabled(False)
        self.stock_table.horizontalHeader().setSectionsClickable(True)
        QTimer.singleShot(0, self._apply_stock_table_column_widths)

    def _apply_stock_table_column_widths(self) -> None:
        """자동매매설정창 하단 종목표 컬럼 폭을 강제로 재적용한다."""
        from gui_main_table_loader import routine_stock_column_widths

        stock_metric_widths = routine_stock_column_widths(self.stock_table.font())
        widths = {
            0: 80,    # 코드: 6자리 여유
            1: 205,   # 종목: 13자 기준
            2: 120,   # 운영: 09:30~13:30 표시
            3: 50,    # 현황: 종목 운영 건강도 표시등
            4: 90,   # 상태: 감시/대기, 매수/매도
            5: 80,    # 방식: 루틴, 시장가, 현재가
            6: 120,   # 청산: 10분/시장가, 10분/현재가
            7: 174,   # 보유: 수량 / 총매수금액
            8: stock_metric_widths[7],   # 가격: 평단가 / 현재가
            9: 174,   # 손익: 손익금 / 수익률
            10: 110,  # 미체결: 미수 / 미도
        }
        self.stock_table.verticalHeader().setMinimumWidth(40)
        self.stock_table.verticalHeader().setMaximumWidth(40)
        self.stock_table.verticalHeader().setFixedWidth(40)
        header = self.stock_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col, width in widths.items():
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)
            self.stock_table.setColumnWidth(col, width)

    def _connect_events(self) -> None:
        self.routine_table.itemSelectionChanged.connect(self.on_routine_selection_changed)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemDoubleClicked.connect(self.on_stock_table_item_double_clicked)
        self.stock_table.customContextMenuRequested.connect(self.on_stock_table_context_menu)
        self.btn_refresh.clicked.connect(self.run_current_routine_stability_check)
        self.btn_close.clicked.connect(self.close)
        self.btn_start.clicked.connect(self.start_selected_auto_trades)
        self.btn_stop.clicked.connect(self.stop_selected_auto_trades)
        self.btn_fetch_minute_candles.clicked.connect(self.fetch_minute_candles_for_selected_stock)
        self.btn_preview_order_candidates.clicked.connect(self.preview_order_candidates_for_pending_signals)
        self.btn_execution_enable.clicked.connect(self.enable_execution_candidate_manually)
        self.btn_real_ready_preflight.clicked.connect(self.run_real_ready_preflight_manually)
        self.btn_execution_preview.clicked.connect(self.preview_execution_for_real_ready_order_manual)
        self.btn_manual_queue_commit.clicked.connect(self.commit_last_execution_preview_queue_manually)
        self.btn_manual_send_order.clicked.connect(self.send_order_for_order_queued_manually)
        self.btn_manual_cancel_pending_order.clicked.connect(self.cancel_pending_order_manually)
        self.btn_manual_modify_pending_order.clicked.connect(self.modify_pending_order_manually)
        self.btn_early_close.clicked.connect(self.apply_selected_early_close_default)
        self.btn_set_schedule.clicked.connect(self.open_operation_environment_settings)
        self.btn_delete.clicked.connect(self.unregister_selected_auto_trade_stocks)
        self.btn_order_view.clicked.connect(self.open_order_status_window)
        self.btn_log_view.clicked.connect(self.open_log_view_window)
        self.btn_review_view.clicked.connect(self.open_review_required_window)

    def sort_routine_table_by_column(self, column: int) -> None:
        """상단 루틴표 헤더 클릭 정렬."""
        if column < 0 or column >= self.routine_table.columnCount():
            return

        if self._routine_sort_column == column:
            self._routine_sort_order = (
                Qt.DescendingOrder
                if self._routine_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._routine_sort_column = column
            self._routine_sort_order = Qt.AscendingOrder

        selected_routine_name = self.current_selected_routine_name()
        self.routine_table.sortItems(column, self._routine_sort_order)
        if selected_routine_name:
            self.restore_routine_selection(selected_routine_name)

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

    def refresh_all(self) -> None:
        # 자동매매설정 창 전체 갱신 전 하단 종목표 위치를 보존한다.
        # 시간변경/매매시작/강제종료 후 종목표가 맨 위로 튀는 문제를 막는다.
        selected_stock_paths, stock_scroll_value = self.capture_stock_table_view_state()

        normalize_base_stock_single_routine_file()
        ensure_single_real_trade_routine_for_all_stocks()
        selected_routine_name = self.current_selected_routine_name()
        self.load_routine_table()

        if selected_routine_name:
            self.restore_routine_selection(selected_routine_name)

        if self.current_selected_routine_dir() is None and self.routine_table.rowCount() > 0:
            self.routine_table.selectRow(0)

        self.load_selected_routine_stocks()
        self.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
        self._runtime_file_snapshot = self.current_runtime_file_signature()
        self.update_action_buttons()

    def current_time_policy_minute_key(self) -> str:
        return auto_trade_current_time_policy_minute_key(self)

    def current_runtime_file_signature(self) -> tuple[tuple[str, int, int], ...]:
        return auto_trade_current_runtime_file_signature(self)

    def on_runtime_file_timer_tick(self) -> None:
        auto_trade_on_runtime_file_timer_tick(self)

    def on_time_policy_timer_tick(self) -> None:
        auto_trade_on_time_policy_timer_tick(self)

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        parent = self.parent()
        checker = getattr(parent, "startup_recovery_session_ready", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(refresh=refresh))
        except Exception:
            return False

    def require_startup_recovery_session(self, action: str) -> bool:
        if self.startup_recovery_session_ready(refresh=True):
            return True
        parent = self.parent()
        reason_getter = getattr(parent, "startup_recovery_block_reason", None)
        reason = ""
        if callable(reason_getter):
            try:
                reason = str(reason_getter() or "").strip()
            except Exception:
                reason = ""
        message = f"{action} 차단: Startup Recovery 운영 재개 확인이 필요합니다."
        if reason:
            message += f" ({reason})"
        self.statusBarMessage(message)
        self.update_startup_recovery_controls()
        return False

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
            self.btn_start.setEnabled(False)

    def closeEvent(self, event) -> None:
        """창을 닫을 때 시간정책 타이머를 정리한다."""
        try:
            self._time_policy_timer.stop()
        except Exception:
            pass
        try:
            self._runtime_file_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

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

        recovery_ready = self.startup_recovery_session_ready(refresh=False)
        self.btn_start.setEnabled(has_stock and recovery_ready)
        self.btn_stop.setEnabled(has_stock)
        self.btn_early_close.setEnabled(has_stock)
        self.btn_set_schedule.setEnabled(True)
        self.btn_delete.setEnabled(has_stock)
        self.btn_order_view.setEnabled(single_stock)
        self.btn_log_view.setEnabled(single_stock)
        self.btn_review_view.setEnabled(True)
        self.update_startup_recovery_controls()

    def on_stock_selection_changed(self) -> None:
        self.update_action_buttons()

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

        stock_dir = self.operation_stock_dir_from_row(item.row())
        if stock_dir is None:
            return

        self.stock_table.selectRow(item.row())
        config = read_json_dict(stock_dir / "config.json") or default_config()
        current_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

        if current_mode == "CONTINUOUS":
            global_schedule = read_global_schedule()
            self.set_selected_operation_mode(
                "SCHEDULED",
                schedule_config_updates(
                    global_schedule["start_time"],
                    global_schedule["end_buy_time"],
                ),
            )
        else:
            self.set_selected_operation_mode("CONTINUOUS")

    def ensure_context_row_selected(self, row: int) -> None:
        ensure_context_row_selected(self, row)

    def select_all_current_routine_stocks(self) -> None:
        select_all_current_routine_stocks(self)

    def clear_current_routine_stock_selection(self) -> None:
        clear_current_routine_stock_selection(self)

    def on_stock_table_context_menu(self, pos) -> None:
        show_auto_trade_stock_context_menu(self, pos)

    def open_selected_individual_liquidation_settings(self) -> None:
        auto_trade_open_selected_individual_liquidation_settings(self)

    def individual_liquidation_status_text(self, policy_values: dict[str, object]) -> str:
        if not bool(policy_values.get("enabled", False)):
            return "환경설정 사용"
        method = short_close_method_text(policy_values.get("method", "이월"))
        if method == "이월":
            return "청산 안함(이월)"
        minutes = str(policy_values.get("minutes_before_regular_close", "5")).strip() or "5"
        return f"개별 {minutes}분/{method}"

    def save_selected_individual_liquidation_settings(self, policy_values: dict[str, object]) -> int:
        return auto_trade_save_selected_individual_liquidation_settings(self, policy_values)

    def selected_manual_ats_state(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, bool]:
        return auto_trade_selected_manual_ats_state(self, selected)

    def save_selected_manual_ats_state(self, ats_state: dict[str, bool]) -> int:
        return auto_trade_save_selected_manual_ats_state(self, ats_state)

    def open_selected_manual_ats_settings_dialog(self) -> None:
        auto_trade_open_selected_manual_ats_settings_dialog(self)

    def set_selected_manual_ats_flag(self, flag_key: str, enabled: bool, label: str) -> None:
        auto_trade_set_selected_manual_ats_flag(self, flag_key, enabled, label)

    def show_selected_ats_immediate_sell_placeholder(self, method: str) -> None:
        auto_trade_show_selected_ats_immediate_sell_placeholder(self, method)

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

            current_value = bool(manual_override.get(flag_key, False))
            manual_override[flag_key] = not current_value
            config["manual_operation_override"] = manual_override
            config["policy_override_enabled"] = True
            config["policy_override_updated_at"] = now_text()
            config["updated_at"] = now_text()

            try:
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                QMessageBox.critical(self, "저장 오류", f"{code} {name} 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
                continue

            changed.append(f"{code} {name}({label}: {'ON' if manual_override[flag_key] else 'OFF'})")
            append_stock_log(stock_dir, "GUI", f"우클릭 수동운영 설정 변경: {label} -> {'ON' if manual_override[flag_key] else 'OFF'}")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 변경: {' / '.join(changed)}")
            self.statusBarMessage(f"{label} 전환 완료: {len(changed)}개")
            self.refresh_all()
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

            config.pop("manual_operation_override", None)
            config["policy_override_updated_at"] = now_text()
            config["updated_at"] = now_text()

            try:
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                QMessageBox.critical(self, "리셋 오류", f"{code} {name} 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
                continue

            changed.append(f"{code} {name}")
            append_stock_log(stock_dir, "GUI", "우클릭 수동운영 개별설정 리셋")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 리셋: {' / '.join(changed)}")
            self.statusBarMessage(f"수동운영 기본 리셋 완료: {len(changed)}개")
            self.refresh_all()
        else:
            QMessageBox.information(self, "처리 없음", "리셋할 수동운영 개별설정이 없습니다.")

    def load_routine_table(self) -> None:
        routine_dirs = get_routine_dirs()
        self.routine_table.setSortingEnabled(False)
        self.routine_table.setRowCount(len(routine_dirs))

        for row, routine_dir in enumerate(routine_dirs):
            routine_name = routine_display_name(routine_dir)
            budget = read_routine_budget(routine_dir)
            stock_count = len(assigned_stock_dirs_in_routine(routine_dir))

            total_budget = int(budget.get('total_budget', 0))
            used_budget = int(budget.get('used_budget', 0))
            available_budget = int(budget.get('available_budget', 0))
            values = [
                routine_name,
                str(stock_count),
                f"{total_budget:,}",
                f"{used_budget:,}",
                f"{available_budget:,}",
            ]
            sort_values = [routine_name, stock_count, total_budget, used_budget, available_budget]

            for col, value in enumerate(values):
                item = SortableTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                item.setData(Qt.UserRole, str(routine_dir))
                item.setData(SORT_ROLE, sort_values[col])
                self.routine_table.setItem(row, col, item)

        self.routine_table.clearSelection()
        if self._routine_sort_column >= 0:
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)

    def current_selected_routine_name(self) -> str:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return ""

        row = selected_rows[0].row()
        item = self.routine_table.item(row, 0)
        if item is None:
            return ""

        return item.text().strip()

    def current_selected_routine_dir(self) -> Path | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return None

        row = selected_rows[0].row()
        item = self.routine_table.item(row, 0)
        if item is None:
            return None

        path_text = item.data(Qt.UserRole)
        if not path_text:
            return None

        path = Path(str(path_text))
        if not path.exists():
            return None

        return path

    def restore_routine_selection(self, routine_name: str) -> None:
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            if item and item.text().strip() == routine_name:
                self.routine_table.selectRow(row)
                return

    def on_routine_selection_changed(self) -> None:
        self.load_selected_routine_stocks()

    def load_selected_routine_stocks(self) -> None:
        auto_trade_load_selected_routine_stocks(self)

    def selected_stock_dir(self) -> Path | None:
        return selected_stock_dir(self)

    def selected_stock_info(self) -> tuple[Path, str, str] | None:
        return selected_stock_info(self)

    def selected_stock_infos(self) -> list[tuple[Path, str, str]]:
        return selected_stock_infos(self)

    def fetch_minute_candles_for_selected_stock(self) -> None:
        selected = self.selected_stock_info()
        if selected is None:
            self.statusBarMessage("분봉조회할 종목 1개를 선택하세요.")
            return

        _stock_dir, code, name = selected
        parent = self.parent()
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

    def read_order_from_queue_by_id(self, order_id: str, queue_path: Path) -> dict[str, object]:
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": [f"failed to read order_queue json: {exc}"],
            }

        if not isinstance(data, dict):
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": ["order_queue root must be an object"],
            }

        orders = data.get("orders")
        if not isinstance(orders, list):
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": ["order_queue orders must be a list"],
            }

        for item in orders:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "") or "").strip() == order_id:
                return {
                    "ok": True,
                    "stage": "EXECUTION_ENABLE_ORDER_READ",
                    "order": dict(item),
                    "blocked_reasons": [],
                }

        return {
            "ok": False,
            "stage": "EXECUTION_ENABLE_ORDER_READ",
            "order": None,
            "blocked_reasons": ["order_id not found"],
        }

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
        return dialog.exec_() == QDialog.Accepted

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
        return dialog.exec_() == QDialog.Accepted

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

    def real_preflight_stock_config_for_order(self, order: dict[str, object]) -> tuple[dict[str, object], str]:
        code = str(order.get("code", "") or "").strip()
        if not code:
            return {}, "missing_order_code"

        selected_getter = getattr(self, "selected_stock_info", None)
        if callable(selected_getter):
            try:
                selected = selected_getter()
            except Exception:
                selected = None
            if selected is not None:
                stock_dir, selected_code, _selected_name = selected
                if str(selected_code or "").strip() == code:
                    config = read_json_dict(Path(stock_dir) / "config.json")
                    return (config if isinstance(config, dict) else {}, str(stock_dir))

        for routine_dir in get_routine_dirs():
            for stock_dir in get_stock_dirs_in_routine(routine_dir):
                stock_code, _stock_name = parse_stock_folder_name(Path(stock_dir).name)
                if stock_code != code:
                    continue
                config = read_json_dict(Path(stock_dir) / "config.json")
                return (config if isinstance(config, dict) else {}, str(stock_dir))

        return {}, "stock_config_not_found"

    def build_real_preflight_guard_from_gui(
        self,
        order: dict[str, object],
        *,
        operator_confirmed: bool = False,
    ) -> dict[str, object]:
        parent = self.parent()
        api = getattr(parent, "kiwoom_api", None)
        connected = False
        if api is not None:
            try:
                connected = bool(api.is_connected())
            except Exception:
                connected = False

        account_getter = getattr(parent, "selected_account_no", None)
        account_no = ""
        if callable(account_getter):
            try:
                account_no = str(account_getter() or "").strip()
            except Exception:
                account_no = ""

        account_list_getter = getattr(parent, "kiwoom_account_numbers", None)
        accounts: list[str] = []
        if callable(account_list_getter):
            try:
                raw_accounts = account_list_getter()
            except Exception:
                raw_accounts = []
            accounts = [
                str(value or "").strip()
                for value in raw_accounts
                if str(value or "").strip()
            ] if isinstance(raw_accounts, list) else []

        stock_config, stock_config_source = self.real_preflight_stock_config_for_order(order)
        stock_config_found = stock_config_source not in {"missing_order_code", "stock_config_not_found"}
        real_enabled = bool(stock_config_found and real_trade_enabled(stock_config))
        account_selected = bool(account_no and account_no in accounts)

        return {
            "real_trade_enabled": real_enabled,
            "kiwoom_logged_in": connected,
            "account_selected": account_selected,
            "account_no": account_no if account_selected else "",
            "operator_confirmed": bool(operator_confirmed),
            "account_numbers": accounts,
            "selected_account_valid": account_selected,
            "real_trade_source": stock_config_source,
            "real_trade_config_found": stock_config_found,
            "real_trade_guard_source": "gui_session",
        }

    def real_preflight_guard_block_reasons(
        self,
        guard: dict[str, object],
        *,
        include_operator: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if guard.get("kiwoom_logged_in") is not True:
            reasons.append("kiwoom api is not connected")
        accounts = guard.get("account_numbers")
        if not isinstance(accounts, list) or not accounts:
            reasons.append("kiwoom account list is empty")
        if guard.get("account_selected") is not True:
            reasons.append("selected account is missing or stale")
        if guard.get("real_trade_config_found") is not True:
            reasons.append("real trade config for order is not found")
        elif guard.get("real_trade_enabled") is not True:
            reasons.append("real trade is disabled for order stock")
        if include_operator and guard.get("operator_confirmed") is not True:
            reasons.append("operator confirmation is required")
        return reasons

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
        return dialog.exec_() == QDialog.Accepted

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
        return dialog.exec_() == QDialog.Accepted

    def execution_runtime_environment_flags(
        self,
        order: dict[str, object] | None = None,
        guard: dict[str, object] | None = None,
        *,
        order_executions_path: Path = ORDER_EXECUTIONS_PATH,
        order_locks_path: Path = ORDER_LOCKS_PATH,
    ) -> dict[str, object]:
        order_dict = order if isinstance(order, dict) else {}
        guard_dict = guard if isinstance(guard, dict) else {}
        try:
            canonical_executions = order_executions_path.resolve() == ORDER_EXECUTIONS_PATH.resolve()
            canonical_locks = order_locks_path.resolve() == ORDER_LOCKS_PATH.resolve()
        except Exception:
            canonical_executions = False
            canonical_locks = False

        issues: list[str] = []
        if guard_dict.get("kiwoom_logged_in") is not True:
            issues.append("kiwoom api is not connected")
        if guard_dict.get("account_selected") is not True or not str(guard_dict.get("account_no") or "").strip():
            issues.append("selected account is missing or stale")
        if guard_dict.get("real_trade_enabled") is not True:
            issues.append("real trade is disabled for order stock")
        if not canonical_executions or not canonical_locks:
            issues.append("runtime target is not the canonical project runtime path")
        if not str(order_dict.get("id") or "").strip():
            issues.append("order id is missing")

        allowed = not issues
        return {
            "real_runtime_file_init_enabled": allowed,
            "allow_project_runtime_file_init": allowed,
            "real_runtime_commit_enabled": allowed,
            "allow_project_runtime_commit": allowed,
            "source": "gui_real_preflight_guard",
            "order_id": str(order_dict.get("id") or "").strip(),
            "account_no": str(guard_dict.get("account_no") or "").strip(),
            "canonical_order_executions_path": canonical_executions,
            "canonical_order_locks_path": canonical_locks,
            "issues": issues,
        }

    def ensure_execution_runtime_files_ready(
        self,
        *,
        order: dict[str, object] | None = None,
        guard: dict[str, object] | None = None,
        order_executions_path: Path = ORDER_EXECUTIONS_PATH,
        order_locks_path: Path = ORDER_LOCKS_PATH,
        require_runtime_file_init_dialog: bool = True,
    ) -> dict[str, object]:
        executions_exists = order_executions_path.exists()
        locks_exists = order_locks_path.exists()
        if executions_exists and locks_exists:
            storage = ExecutionRuntimeStorage(order_executions_path, order_locks_path)
            read_result = storage.read()
            if read_result.get("ok") is True:
                return {
                    "runtime_files_ready": True,
                    "runtime_file_init_required": False,
                    "runtime_file_init_result": None,
                    "blocked_reasons": [],
                }
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": False,
                "runtime_file_init_result": None,
                "blocked_reasons": list(read_result.get("issues") or ["runtime files are invalid"]),
            }

        environment_flags = self.execution_runtime_environment_flags(
            order,
            guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        )
        allow_project_runtime_path = environment_flags.get("allow_project_runtime_file_init") is True
        file_init_preview = build_execution_runtime_file_init_preview(
            order_executions_path,
            order_locks_path,
            allow_project_runtime_path=allow_project_runtime_path,
        )
        if file_init_preview.get("status") != "READY":
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": file_init_preview.get("status") == "READY",
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(file_init_preview.get("issues") or ["runtime file init preview is not ready"]),
            }

        if require_runtime_file_init_dialog and not self.confirm_execution_runtime_file_init(
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        ):
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": True,
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": ["runtime file initialization cancelled by operator"],
            }

        approval = approve_execution_runtime_file_init(
            file_init_preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )
        orchestrator = run_execution_runtime_file_init_commit_plan_orchestrator(
            file_init_preview,
            approval,
        )
        open_policy = evaluate_execution_runtime_file_init_open_policy(
            file_init_commit_plan_orchestrator_result=orchestrator,
            confirmations={
                "manual_runtime_file_init_commit_confirmed": True,
                "manual_project_runtime_path_confirmed": True,
            },
            environment_flags=environment_flags,
        )
        if open_policy.get("status") != "READY_TO_OPEN_FILE_INIT" or open_policy.get("file_init_allowed") is not True:
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": True,
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_approval_gate_result": approval,
                "runtime_file_init_commit_plan_orchestrator_result": orchestrator,
                "runtime_file_init_open_policy_result": open_policy,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(open_policy.get("issues") or ["runtime file init open policy is not ready"]),
            }
        result = commit_execution_runtime_file_init_plan(
            orchestrator,
            manual_runtime_file_init_commit_confirmed=True,
            manual_temp_file_init_confirmed=True,
            file_init_open_policy_result=open_policy,
            manual_project_runtime_file_init_commit_confirmed=True,
        )
        ready = (
            result.get("status") == "COMMITTED"
            and result.get("committed") is True
            and result.get("read_back_verified") is True
        )
        return {
            "runtime_files_ready": ready,
            "runtime_file_init_required": True,
            "runtime_file_init_preview": file_init_preview,
            "runtime_file_init_approval_gate_result": approval,
            "runtime_file_init_commit_plan_orchestrator_result": orchestrator,
            "runtime_file_init_open_policy_result": open_policy,
            "runtime_file_init_result": result,
            "runtime_environment_flags": environment_flags,
            "blocked_reasons": [] if ready else list(result.get("issues") or ["runtime file initialization failed"]),
        }

    def commit_execution_runtime_for_preview(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        execution_preview_result: dict[str, object],
        *,
        order_executions_path: Path = ORDER_EXECUTIONS_PATH,
        order_locks_path: Path = ORDER_LOCKS_PATH,
        require_runtime_file_init_dialog: bool = True,
    ) -> dict[str, object]:
        del execution_preview_result
        runtime_files = self.ensure_execution_runtime_files_ready(
            order=order,
            guard=guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            require_runtime_file_init_dialog=require_runtime_file_init_dialog,
        )
        if runtime_files.get("runtime_files_ready") is not True:
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_file_init",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "blocked_reasons": list(runtime_files.get("blocked_reasons") or ["runtime files are not ready"]),
            }

        confirmations = {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }
        environment_flags = self.execution_runtime_environment_flags(
            order,
            guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        )
        storage = ExecutionRuntimeStorage(order_executions_path, order_locks_path)
        runtime_dry_run = run_execution_runtime_dry_run(
            order,
            guard,
            storage,
            confirmations=confirmations,
        )
        commit_plan = runtime_dry_run.get("commit_plan") if isinstance(runtime_dry_run, dict) else None
        if not isinstance(commit_plan, dict) or runtime_dry_run.get("status") != "READY":
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_commit_plan",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "runtime_dry_run_result": runtime_dry_run,
                "commit_plan_orchestrator_result": commit_plan,
                "blocked_reasons": list(runtime_dry_run.get("issues") or ["runtime commit plan is not ready"])
                if isinstance(runtime_dry_run, dict)
                else ["runtime commit plan is malformed"],
            }

        real_commit_readiness = evaluate_execution_runtime_real_commit_readiness(
            runtime_api_result=runtime_dry_run,
            commit_plan_orchestrator_result=commit_plan,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            confirmations=confirmations,
            environment_flags=environment_flags,
        )
        if real_commit_readiness.get("status") != "READY_TO_OPEN_RUNTIME_COMMIT":
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_real_commit_readiness",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "runtime_dry_run_result": runtime_dry_run,
                "commit_plan_orchestrator_result": commit_plan,
                "runtime_commit_readiness_policy_result": real_commit_readiness,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(real_commit_readiness.get("issues") or ["runtime real commit readiness is not ready"]),
            }

        runtime_commit_result = commit_execution_runtime_plan(
            commit_plan,
            order_executions_path,
            order_locks_path,
            context=confirmations,
            real_commit_readiness_policy_result=real_commit_readiness,
            manual_project_runtime_commit_confirmed=True,
        )
        required_identity = ("execution_id", "order_id", "request_hash", "lock_id")
        missing_identity = [
            field for field in required_identity if not str(runtime_commit_result.get(field) or "").strip()
        ]
        invalid_reasons: list[str] = []
        if runtime_commit_result.get("status") != "COMMITTED":
            invalid_reasons.append("runtime commit status is not COMMITTED")
        if runtime_commit_result.get("committed") is not True:
            invalid_reasons.append("runtime committed flag is not true")
        if runtime_commit_result.get("runtime_write") is not True:
            invalid_reasons.append("runtime_write flag is not true")
        if runtime_commit_result.get("read_back_verified") is not True:
            invalid_reasons.append("runtime read-back is not verified")
        invalid_reasons.extend(f"missing runtime commit identity: {field}" for field in missing_identity)

        return {
            "runtime_commit_ready": not invalid_reasons,
            "runtime_commit_stage": "runtime_committed" if not invalid_reasons else "runtime_commit_validation",
            "runtime_commit_result": runtime_commit_result,
            "runtime_file_init": runtime_files,
            "runtime_dry_run_result": runtime_dry_run,
            "commit_plan_orchestrator_result": commit_plan,
            "runtime_commit_readiness_policy_result": real_commit_readiness,
            "runtime_environment_flags": environment_flags,
            "blocked_reasons": invalid_reasons,
        }

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

    def queue_file_snapshot(queue_path: Path) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "path": str(queue_path),
            "sha256": None,
            "size": None,
            "mtime": None,
            "orders_count": None,
            "error": None,
        }
        try:
            data = queue_path.read_bytes()
            stat = queue_path.stat()
            snapshot["sha256"] = hashlib.sha256(data).hexdigest().upper()
            snapshot["size"] = stat.st_size
            snapshot["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            try:
                decoded = json.loads(data.decode("utf-8"))
                orders = decoded.get("orders") if isinstance(decoded, dict) else None
                snapshot["orders_count"] = len(orders) if isinstance(orders, list) else None
                snapshot["revision"] = decoded.get("revision") if isinstance(decoded, dict) else None
            except Exception as exc:
                snapshot["error"] = f"orders_count unavailable: {exc}"
        except Exception as exc:
            snapshot["error"] = str(exc)

        return snapshot

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
        return dialog.exec_() == QDialog.Accepted

    def verify_manual_queue_commit_read_back(
        self,
        *,
        queue_path: Path,
        queue_write_preview_result: dict[str, object],
        runtime_commit_result: dict[str, object],
    ) -> dict[str, object]:
        record = queue_write_preview_result.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        expected = {
            "id": str(record_dict.get("id") or "").strip(),
            "order_id": str(record_dict.get("order_id") or "").strip(),
            "execution_id": str(record_dict.get("execution_id") or "").strip(),
            "request_hash": str(record_dict.get("request_hash") or "").strip(),
            "lock_id": str(record_dict.get("lock_id") or "").strip(),
        }
        runtime_expected = {
            "order_id": str(runtime_commit_result.get("order_id") or "").strip(),
            "execution_id": str(runtime_commit_result.get("execution_id") or "").strip(),
            "request_hash": str(runtime_commit_result.get("request_hash") or "").strip(),
            "lock_id": str(runtime_commit_result.get("lock_id") or "").strip(),
        }

        issues: list[str] = []
        for field in ("order_id", "execution_id", "request_hash", "lock_id"):
            if not expected[field] or expected[field] != runtime_expected[field]:
                issues.append(f"runtime/queue identity mismatch before read-back: {field}")
        if not expected["id"]:
            issues.append("order queued record id is missing")
        if issues:
            return {"verified": False, "stage": "identity_precheck", "record": None, "issues": issues}

        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "verified": False,
                "stage": "queue_read",
                "record": None,
                "issues": [f"failed to read queue after commit: {exc}"],
            }
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            return {
                "verified": False,
                "stage": "queue_structure",
                "record": None,
                "issues": ["queue orders must be a list after commit"],
            }

        matches = [
            item for item in orders
            if isinstance(item, dict) and str(item.get("id") or "").strip() == expected["id"]
        ]
        if len(matches) != 1:
            return {
                "verified": False,
                "stage": "record_count",
                "record": None,
                "issues": [f"expected exactly one ORDER_QUEUED record after commit, found {len(matches)}"],
            }

        actual = dict(matches[0])
        for field in ("order_id", "execution_id", "request_hash", "lock_id"):
            if str(actual.get(field) or "").strip() != expected[field]:
                issues.append(f"read-back identity mismatch: {field}")
        if actual.get("status") != "ORDER_QUEUED":
            issues.append("read-back status is not ORDER_QUEUED")
        if actual.get("send_order_called") is True:
            issues.append("read-back send_order_called is true")
        if actual.get("broker_api_called") is True:
            issues.append("read-back broker_api_called is true")
        if actual.get("actual_order_sent") is True:
            issues.append("read-back actual_order_sent is true")

        return {
            "verified": not issues,
            "stage": "verified" if not issues else "record_validation",
            "record": actual,
            "issues": issues,
        }

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
        return dialog.exec_() == QDialog.Accepted

    def build_manual_send_order_environment(self, order: dict[str, object], queue_path: Path) -> dict[str, object]:
        parent = self.parent()
        api = getattr(parent, "kiwoom_api", None)
        selected_account_getter = getattr(parent, "selected_account_no", None)
        selected_account = str(selected_account_getter() or "").strip() if callable(selected_account_getter) else ""
        connected = bool(api is not None and callable(getattr(api, "is_connected", None)) and api.is_connected())
        accounts: list[str] = []
        if api is not None and callable(getattr(api, "account_numbers", None)):
            accounts = [str(item or "").strip() for item in api.account_numbers() if str(item or "").strip()]

        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        order_account = str(order.get("account_no") or "").strip()
        request_account = str(request_preview_dict.get("account_no") or "").strip()

        config, _source = self.real_preflight_stock_config_for_order(order)
        real_trade_enabled = bool(isinstance(config, dict) and config.get("real_trade_enabled") is True)
        try:
            canonical_queue = queue_path.resolve() == ORDER_QUEUE_PATH.resolve()
        except Exception:
            canonical_queue = False

        issues: list[str] = []
        if api is None or not callable(getattr(api, "send_order", None)):
            issues.append("kiwoom api SendOrder callable is unavailable")
        if not connected:
            issues.append("kiwoom api is not connected")
        if not selected_account:
            issues.append("selected account is missing")
        if selected_account and accounts and selected_account not in accounts:
            issues.append("selected account is not in current Kiwoom account list")
        if not order_account:
            issues.append("ORDER_QUEUED account_no is required")
        if not request_account:
            issues.append("execution_request.request_preview.account_no is required")
        if order_account and request_account and order_account != request_account:
            issues.append("ORDER_QUEUED account_no does not match execution request account_no")
        if request_account and selected_account and request_account != selected_account:
            issues.append("selected account does not match execution request account")
        if order_account and selected_account and order_account != selected_account:
            issues.append("selected account does not match ORDER_QUEUED account")
        if not real_trade_enabled:
            issues.append("real trade is disabled for order stock")
        if not canonical_queue:
            issues.append("queue path is not canonical runtime/order_queue.json")

        return {
            "send_order_environment_ready": not issues,
            "issues": issues,
            "kiwoom_connected": connected,
            "selected_account_no": selected_account,
            "order_account_no": order_account,
            "request_account_no": request_account,
            "real_trade_enabled": real_trade_enabled,
            "canonical_queue_path": canonical_queue,
            "send_order_callable": getattr(api, "send_order", None) if api is not None else None,
        }

    def send_order_identity_from_record(self, record: dict[str, object]) -> dict[str, object]:
        return {
            "order_queued_id": str(record.get("id") or record.get("order_queued_id") or "").strip(),
            "source_signal_id": str(record.get("source_signal_id") or "").strip(),
            "order_id": str(record.get("order_id") or "").strip(),
            "candidate_id": str(record.get("candidate_id") or "").strip(),
            "queue_pending_id": str(record.get("queue_pending_id") or "").strip(),
            "execution_id": str(record.get("execution_id") or "").strip(),
            "request_hash": str(record.get("request_hash") or "").strip(),
            "lock_id": str(record.get("lock_id") or "").strip(),
        }

    def build_manual_send_order_call_preview(
        self,
        order: dict[str, object],
        environment: dict[str, object],
        *,
        operator_confirmed: bool,
    ) -> dict[str, object]:
        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        side = str(request_preview_dict.get("side") or order.get("side") or "").strip().upper()
        hoga = str(
            request_preview_dict.get("hoga")
            or request_preview_dict.get("order_type")
            or order.get("hoga")
            or order.get("order_type")
            or ""
        ).strip().upper()
        price = request_preview_dict.get("price", order.get("price", 0))
        quantity = request_preview_dict.get("quantity", order.get("quantity", 0))
        account_no = str(request_preview_dict.get("account_no") or "").strip()
        screen_no = str(request_preview_dict.get("screen_no") or "0101").strip()

        broker_dispatch_preview = {
            "status": "BROKER_DISPATCH_READY",
            "send_order_called": False,
            "broker_called": False,
            "send_order_params_preview": {
                "broker_type": "KIWOOM",
                "dispatch_id": str(order.get("id") or "").strip(),
                "order_id": str(order.get("order_id") or order.get("id") or "").strip(),
                "account_no": account_no,
                "screen_no": screen_no,
                "side": side,
                "order_action": str(request_preview_dict.get("order_action") or request_preview_dict.get("action") or "NEW").strip().upper(),
                "code": str(request_preview_dict.get("code") or order.get("code") or "").strip(),
                "quantity": quantity,
                "price": price,
                "hoga": hoga,
                "original_order_no": str(request_preview_dict.get("original_order_no") or "").strip(),
            },
        }
        adapter_contract = build_kiwoom_send_order_adapter_contract(
            broker_dispatch_preview,
            {"account_no": account_no},
            {"screen_no": screen_no},
        )
        safety = evaluate_kiwoom_send_order_safety(
            adapter_contract,
            {},
            {"connected": environment.get("kiwoom_connected"), "account_no": account_no},
            {"manual_kiwoom_send_order_confirmed": operator_confirmed is True, "emergency_stop": False},
        )
        call_preview = preview_kiwoom_send_order_call(
            safety,
            adapter_contract,
            {"final_call_token": f"GUI_SEND_{uuid4().hex}"},
        )
        call_preview["adapter_contract_result"] = adapter_contract
        call_preview["safety_gate_result"] = safety
        return call_preview

    def build_manual_final_send_gate_result(
        self,
        order: dict[str, object],
        environment: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
        current_queue_snapshot: dict[str, object],
    ) -> dict[str, object]:
        record_review = review_order_queued_record(order)
        if record_review.get("review_ok") is not True:
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "order_queued_record_review",
                "blocked_reasons": list(record_review.get("blocked_reasons") or ["ORDER_QUEUED review failed"]),
                "order_queued_record_review_result": record_review,
            }

        identity = self.send_order_identity_from_record(order)
        queue_committed_review = {
            "review_type": "EXECUTION_QUEUE_COMMITTED_REVIEW",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "order_queued_record": order,
            "identity": {
                "order_id": identity.get("order_id"),
                "source_signal_id": identity.get("source_signal_id"),
                "execution_id": identity.get("execution_id"),
                "request_hash": identity.get("request_hash"),
                "lock_id": identity.get("lock_id"),
            },
            "issues": [],
            "warnings": [],
        }
        adapter_result = adapt_queue_review_to_send_order_preview(queue_committed_review)
        if adapter_result.get("status") != "READY_FOR_FINAL_SEND_GATE":
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "send_order_preview_adapter",
                "blocked_reasons": list(adapter_result.get("issues") or ["SendOrder preview adapter blocked"]),
                "send_order_preview_adapter_result": adapter_result,
            }

        guard = {
            "real_trade_enabled": environment.get("real_trade_enabled") is True,
            "kiwoom_logged_in": environment.get("kiwoom_connected") is True,
            "account_selected": bool(str(environment.get("selected_account_no") or "").strip()),
            "account_no": str(environment.get("selected_account_no") or "").strip(),
            "operator_confirmed": True,
        }
        final_context = {
            "manual_final_send_confirmed": True,
            "queue_path": str(queue_path),
            "queue_snapshot_hash": queue_snapshot.get("sha256"),
        }
        readiness = evaluate_execution_final_send_gate_readiness(adapter_result, guard, context=final_context)
        input_adapter = adapt_final_send_gate_readiness_to_input(readiness, guard, context=final_context)
        orchestrator = orchestrate_final_send_gate_preview(input_adapter)
        if orchestrator.get("status") != "READY_FOR_FINAL_SEND_GATE" or orchestrator.get("final_send_gate_ready") is not True:
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "final_send_gate_orchestrator",
                "blocked_reasons": list(orchestrator.get("issues") or ["Final Send Gate orchestrator blocked"]),
                "final_send_gate_readiness_result": readiness,
                "final_send_gate_input_adapter_result": input_adapter,
                "final_send_gate_orchestrator_result": orchestrator,
            }

        final_input = orchestrator.get("final_send_gate_input")
        final_input_dict = final_input if isinstance(final_input, dict) else {}
        final_gate = evaluate_final_send_gate(
            final_input_dict.get("adapter_preview_result"),
            final_input_dict.get("order_queued_record"),
            final_input_dict.get("current_guard"),
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            context=final_input_dict.get("context"),
        )
        final_gate["final_send_gate_result_type"] = "FINAL_SEND_GATE_SERVICE"
        final_gate["queue_path"] = str(queue_path)
        final_gate["queue_revision"] = current_queue_snapshot.get("revision")
        final_gate["queue_snapshot_hash"] = current_queue_snapshot.get("sha256")
        final_gate["identity"] = identity
        final_gate["order_queued_id"] = identity.get("order_queued_id")
        final_gate["final_send_gate_readiness_result"] = readiness
        final_gate["final_send_gate_input_adapter_result"] = input_adapter
        final_gate["final_send_gate_orchestrator_result"] = orchestrator
        return final_gate

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

    def _queue_data_for_manual_order_action(self, queue_path: Path) -> tuple[dict[str, object], list[object], list[str]]:
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, [], [f"failed to read order_queue json: {exc}"]
        if not isinstance(data, dict):
            return {}, [], ["order_queue root must be an object"]
        orders = data.get("orders")
        if not isinstance(orders, list):
            return data, [], ["order_queue orders must be a list"]
        return data, orders, []

    def _pending_cancel_duplicate_reason(self, orders: list[object], original_order_no: str) -> str:
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

    def _build_manual_cancel_order_queued_preview(
        self,
        source_order: dict[str, object],
        *,
        queue_revision: object,
    ) -> dict[str, object]:
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        source_signal_id = str(source_order.get("source_signal_id") or "").strip()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        account_no = str(source_order.get("account_no") or "").strip()
        code = str(source_order.get("code") or "").strip()
        side = str(source_order.get("side") or "").strip().upper()
        remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        suffix = uuid4().hex[:12]
        order_id = f"{source_order_id}_CANCEL_{suffix}"
        execution_id = f"EXEC_CANCEL_{suffix}"
        lock_id = f"LOCK_CANCEL_{suffix}"
        candidate_id = f"CANCEL_CANDIDATE_{suffix}"
        queue_pending_id = f"QUEUE_PENDING_{candidate_id}"
        hash_payload = {
            "action": "CANCEL",
            "source_order_id": source_order_id,
            "broker_order_no": broker_order_no,
            "account_no": account_no,
            "code": code,
            "side": side,
            "quantity": remaining_quantity,
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
                "screen_no": "0101",
                "side": side,
                "order_action": "CANCEL",
                "code": code,
                "quantity": remaining_quantity,
                "price": 0,
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
                "queue_contract_version": "manual-cancel-1",
                "send_order_called": False,
                "execution_enabled": False,
                "blocked_reasons": [],
                "account_no": account_no,
                "code": code,
                "side": side,
                "quantity": remaining_quantity,
                "price": 0,
                "order_type": "LIMIT",
                "order_action": "CANCEL",
                "cancel_source_order_id": source_order_id,
            },
        }

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
                "screen_no": "0101",
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
        return QMessageBox.question(self, "Manual Cancel", message, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

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
        return QMessageBox.question(self, "Manual Modify", message, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

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
        self.show_manual_send_order_result(result)
        status_text = "completed" if result.get("queue_result_recorded") else "blocked"
        self.statusBarMessage(f"Manual SendOrder {status_text}")

    def auto_trade_runtime_state_for_order(
        self,
        order: dict[str, object],
    ) -> dict[str, object]:
        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        code = str(order.get("code") or request_preview_dict.get("code") or "").strip()
        if not code:
            return {"found": False, "state": {}, "config": {}, "stock_dir": "", "issues": ["order code is required"]}

        try:
            routine_dir = self.current_selected_routine_dir()
        except Exception:
            routine_dir = None
        routine_dirs = [routine_dir] if isinstance(routine_dir, Path) else get_routine_dirs()
        for candidate_routine_dir in routine_dirs:
            if not isinstance(candidate_routine_dir, Path):
                continue
            for stock_dir in get_stock_dirs_in_routine(candidate_routine_dir):
                stock_code, _stock_name = parse_stock_folder_name(Path(stock_dir).name)
                if stock_code != code:
                    continue
                state = read_json_dict(Path(stock_dir) / "state.json")
                config = read_json_dict(Path(stock_dir) / "config.json")
                return {
                    "found": True,
                    "state": state if isinstance(state, dict) else {},
                    "config": config if isinstance(config, dict) else {},
                    "stock_dir": str(stock_dir),
                    "issues": [],
                }
        return {"found": False, "state": {}, "config": {}, "stock_dir": "", "issues": ["runtime stock state is not found"]}

    def auto_trade_execution_block_reasons(self, order: dict[str, object]) -> list[str]:
        runtime = self.auto_trade_runtime_state_for_order(order)
        if runtime.get("found") is not True:
            return list(runtime.get("issues") or ["runtime stock state is not found"])

        state = runtime.get("state")
        state_dict = state if isinstance(state, dict) else {}
        status = str(state_dict.get("status") or "").strip().upper()
        reasons: list[str] = []
        if status != "RUNNING":
            reasons.append("auto trade status is not RUNNING")
        if state_dict.get("trade_enabled") is not True:
            reasons.append("trade_enabled is not true")
        if state_dict.get("real_trade_enabled") is not True:
            reasons.append("real_trade_enabled is not true")
        if state_dict.get("signal_probe_only") is True:
            reasons.append("signal_probe_only is true")
        if state_dict.get("review_required") is True:
            reasons.append("review_required is true")
        if status in {"EMERGENCY_STOPPED", "EMERGENCY_STOP", "EMERGENCY"}:
            reasons.append("emergency stop status is active")
        return reasons

    def order_with_execution_request_defaults(
        self,
        order: dict[str, object],
        *,
        source_order: dict[str, object] | None = None,
    ) -> dict[str, object]:
        enriched = dict(order)
        source = source_order if isinstance(source_order, dict) else {}
        execution_request = enriched.get("execution_request")
        execution_request_dict = deepcopy(execution_request) if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = deepcopy(request_preview) if isinstance(request_preview, dict) else {}
        source_intent = source.get("order_intent")
        source_intent_dict = source_intent if isinstance(source_intent, dict) else {}
        if not str(request_preview_dict.get("side") or "").strip():
            source_side = source.get("side") or source_intent_dict.get("side")
            if source_side:
                request_preview_dict["side"] = source_side
        if str(request_preview_dict.get("hoga") or "").strip().upper() in {"", "UNDECIDED"}:
            source_hoga = source.get("hoga") or source.get("order_type") or source_intent_dict.get("hoga")
            if source_hoga:
                request_preview_dict["hoga"] = source_hoga
        for field in ("code", "quantity", "price", "account_no"):
            if request_preview_dict.get(field) in (None, "") and source.get(field) not in (None, ""):
                request_preview_dict[field] = source.get(field)
        if request_preview_dict:
            execution_request_dict["request_preview"] = request_preview_dict
            enriched["execution_request"] = execution_request_dict
        fallback_fields = {
            "account_no": "account_no",
            "code": "code",
            "side": "side",
            "quantity": "quantity",
            "price": "price",
            "order_type": "hoga",
            "hoga": "hoga",
        }
        for target_key, request_key in fallback_fields.items():
            if enriched.get(target_key) in (None, "") and request_key in request_preview_dict:
                enriched[target_key] = request_preview_dict.get(request_key)
        return enriched

    def send_order_for_order_queued_automatically(
        self,
        order_id: str,
        *,
        queue_path: Path = ORDER_QUEUE_PATH,
        send_order_callable_override=None,
        source_order: dict[str, object] | None = None,
    ) -> dict[str, object]:
        order_id = str(order_id or "").strip()
        if not order_id:
            return {
                "status": "BLOCKED",
                "stage": "order_id",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["ORDER_QUEUED record id is required"],
            }

        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            return {
                "status": "BLOCKED",
                "stage": "read_order",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        if order_dict.get("status") != "ORDER_QUEUED":
            return {
                "status": "BLOCKED",
                "stage": "order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["target record status is not ORDER_QUEUED"],
            }

        order_for_execution = self.order_with_execution_request_defaults(order_dict, source_order=source_order)
        auto_reasons = self.auto_trade_execution_block_reasons(order_for_execution)
        if auto_reasons:
            return {
                "status": "BLOCKED",
                "stage": "auto_trade_runtime_state",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": auto_reasons,
            }

        environment = self.build_manual_send_order_environment(order_for_execution, queue_path)
        if send_order_callable_override is not None:
            environment["send_order_callable"] = send_order_callable_override
        if environment.get("send_order_environment_ready") is not True:
            return {
                "status": "BLOCKED",
                "stage": "send_order_environment",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(environment.get("issues") or []),
            }

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            return {
                "status": "BLOCKED",
                "stage": "stale_queue_snapshot",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["queue file changed before automatic SendOrder dispatch"],
            }

        latest_read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if latest_read_result.get("ok") is not True:
            return {
                "status": "BLOCKED",
                "stage": "latest_order_read",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": latest_read_result.get("blocked_reasons", []),
            }
        latest_order = latest_read_result.get("order")
        latest_order_dict = latest_order if isinstance(latest_order, dict) else {}
        latest_order_for_execution = self.order_with_execution_request_defaults(
            latest_order_dict,
            source_order=source_order,
        )
        if latest_order_dict.get("status") != "ORDER_QUEUED":
            return {
                "status": "BLOCKED",
                "stage": "latest_order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["latest target record status is not ORDER_QUEUED"],
            }

        latest_environment = self.build_manual_send_order_environment(latest_order_for_execution, queue_path)
        if send_order_callable_override is not None:
            latest_environment["send_order_callable"] = send_order_callable_override
        if latest_environment.get("send_order_environment_ready") is not True:
            return {
                "status": "BLOCKED",
                "stage": "send_order_environment_after_recheck",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(latest_environment.get("issues") or []),
            }

        identity = self.send_order_identity_from_record(latest_order_for_execution)
        final_gate = self.build_manual_final_send_gate_result(
            latest_order_for_execution,
            latest_environment,
            queue_path,
            snapshot,
            current_snapshot,
        )
        if final_gate.get("final_send_gate_ok") is not True:
            return {
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

        call_preview = self.build_manual_send_order_call_preview(
            latest_order_for_execution,
            latest_environment,
            operator_confirmed=True,
        )
        if call_preview.get("status") != "SEND_ORDER_CALL_READY":
            return {
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

        claim_token = f"AUTO_CLAIM_{uuid4().hex}"
        claim = claim_order_for_dispatch(
            queue_path,
            identity,
            final_gate,
            claim_token=claim_token,
            claim_owner="AUTO_TRADE_SEND_ORDER",
            claim_source="auto_trade_timer",
            context={
                "dispatch_claim_owner": "AUTO_TRADE_SEND_ORDER",
                "dispatch_claim_source": "auto_trade_timer",
                "dispatch_claim_ttl_sec": 60,
                "queue_path": str(queue_path),
                "queue_snapshot_hash": current_snapshot.get("sha256"),
            },
            expected_revision=current_snapshot.get("revision"),
        )
        if claim.get("claimed") is not True or claim.get("post_write_verified") is not True:
            return {
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

        result = execute_claimed_send_order(
            queue_path,
            identity,
            str(claim.get("dispatch_claim_id") or ""),
            claim_token,
            "AUTO_TRADE_SEND_ORDER",
            claim.get("revision_after"),
            latest_environment.get("send_order_callable"),
            call_preview.get("send_order_args"),
            context={
                "send_order_attempt_owner": "AUTO_TRADE_SEND_ORDER",
                "send_order_attempt_source": "auto_trade_timer",
            },
        )
        result["order_id"] = order_id
        result["dispatch_claim_result"] = claim
        result["final_send_gate_result"] = final_gate
        result["send_order_call_preview_result"] = call_preview
        return result

    def process_executable_order_for_auto_trade(
        self,
        order_id: str,
        *,
        send_order_callable_override=None,
    ) -> dict[str, object]:
        queue_path = ORDER_QUEUE_PATH
        order_id = str(order_id or "").strip()
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            return {"processed": False, "stage": "read_executable_order", "order_id": order_id, "blocked_reasons": read_result.get("blocked_reasons", [])}
        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        if order_dict.get("status") != "EXECUTABLE":
            return {"processed": False, "stage": "executable_status", "order_id": order_id, "blocked_reasons": ["target record status is not EXECUTABLE"]}

        auto_reasons = self.auto_trade_execution_block_reasons(order_dict)
        if auto_reasons:
            return {"processed": False, "stage": "auto_trade_runtime_state", "order_id": order_id, "blocked_reasons": auto_reasons}

        enable_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        enable_preview = preview_execution_enable(order_dict, {"operator_confirmed_for_execution_enable": True})
        if enable_preview.get("enable_preview") is not True:
            return {"processed": False, "stage": "execution_enable_preview", "order_id": order_id, "blocked_reasons": list(enable_preview.get("blocked_reasons") or [])}
        enable_result = commit_execution_enable(
            enable_preview,
            queue_path,
            preview_queue_snapshot=enable_snapshot,
            context={"manual_execution_enable_commit_confirmed": True},
        )
        if enable_result.get("enabled") is not True:
            return {"processed": False, "stage": "execution_enable_commit", "order_id": order_id, "blocked_reasons": list(enable_result.get("blocked_reasons") or []), "execution_enable_result": enable_result}

        enabled_read = self.read_order_from_queue_by_id(order_id, queue_path)
        enabled_order = enabled_read.get("order") if isinstance(enabled_read, dict) else {}
        enabled_order_dict = enabled_order if isinstance(enabled_order, dict) else {}
        guard = self.build_real_preflight_guard_from_gui(enabled_order_dict, operator_confirmed=True)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=False)
        if guard_reasons:
            return {"processed": False, "stage": "real_preflight_guard", "order_id": order_id, "blocked_reasons": guard_reasons, "execution_enable_result": enable_result}

        preflight_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        preflight_preview = preview_real_order_preflight(
            enabled_order_dict,
            guard,
            {"manual_real_preflight_confirmed": True},
        )
        if preflight_preview.get("real_preflight_preview") is not True:
            return {"processed": False, "stage": "real_preflight_preview", "order_id": order_id, "blocked_reasons": list(preflight_preview.get("blocked_reasons") or []), "execution_enable_result": enable_result}
        preflight_result = commit_real_order_preflight(
            preflight_preview,
            queue_path,
            preview_queue_snapshot=preflight_snapshot,
            context={"manual_real_preflight_commit_confirmed": True},
        )
        if preflight_result.get("real_preflight_committed") is not True:
            return {
                "processed": False,
                "stage": "real_preflight_commit",
                "order_id": order_id,
                "blocked_reasons": list(preflight_result.get("blocked_reasons") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
            }

        real_ready_read = self.read_order_from_queue_by_id(order_id, queue_path)
        real_ready_order = real_ready_read.get("order") if isinstance(real_ready_read, dict) else {}
        real_ready_order_dict = real_ready_order if isinstance(real_ready_order, dict) else {}
        execution_preview = preview_execution_for_real_ready_order(order_id, guard, queue_path)
        if execution_preview.get("ok") is not True:
            return {
                "processed": False,
                "stage": "execution_preview",
                "order_id": order_id,
                "blocked_reasons": list(execution_preview.get("blocked_reasons") or execution_preview.get("issues") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
            }

        runtime_commit = self.commit_execution_runtime_for_preview(
            real_ready_order_dict,
            guard,
            execution_preview,
            order_executions_path=ORDER_EXECUTIONS_PATH,
            order_locks_path=ORDER_LOCKS_PATH,
            require_runtime_file_init_dialog=False,
        )
        if runtime_commit.get("runtime_commit_ready") is not True:
            return {
                "processed": False,
                "stage": "runtime_commit",
                "order_id": order_id,
                "blocked_reasons": list(runtime_commit.get("blocked_reasons") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
            }

        preview_result = execution_preview.get("preview_result")
        preview_result_dict = preview_result if isinstance(preview_result, dict) else {}
        queue_write_preview = execution_preview.get("queue_write_preview_result")
        if not isinstance(queue_write_preview, dict):
            queue_write_preview = preview_result_dict.get("queue_write_preview_result")
        if not isinstance(queue_write_preview, dict) or queue_write_preview.get("write_preview") is not True:
            return {
                "processed": False,
                "stage": "queue_write_preview",
                "order_id": order_id,
                "blocked_reasons": ["queue write preview is required"],
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
            }

        runtime_commit_result = runtime_commit.get("runtime_commit_result")
        runtime_commit_result_dict = runtime_commit_result if isinstance(runtime_commit_result, dict) else {}
        queue_commit_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        queue_commit_readiness = evaluate_execution_queue_commit_readiness(
            runtime_commit_result=runtime_commit_result_dict,
            queue_write_preview_result=queue_write_preview,
            queue_path=queue_path,
            confirmations={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
        )
        if queue_commit_readiness.get("status") != "READY_TO_COMMIT_QUEUE":
            return {
                "processed": False,
                "stage": "queue_commit_readiness",
                "order_id": order_id,
                "blocked_reasons": list(queue_commit_readiness.get("issues") or ["queue commit readiness policy is not ready"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
            }

        queue_commit = commit_execution_queue_manually(
            queue_write_preview,
            queue_path,
            context={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
            queue_commit_readiness_policy_result=queue_commit_readiness,
            manual_queue_commit_after_runtime_confirmed=True,
        )
        if queue_commit.get("manual_commit") is not True:
            return {
                "processed": False,
                "stage": "queue_commit",
                "order_id": order_id,
                "blocked_reasons": list(queue_commit.get("blocked_reasons") or ["queue commit failed"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
                "queue_commit_result": queue_commit,
            }
        read_back = self.verify_manual_queue_commit_read_back(
            queue_path=queue_path,
            queue_write_preview_result=queue_write_preview,
            runtime_commit_result=runtime_commit_result_dict,
        )
        if read_back.get("verified") is not True:
            return {
                "processed": False,
                "stage": "queue_commit_read_back",
                "order_id": order_id,
                "blocked_reasons": list(read_back.get("issues") or ["queue commit read-back failed"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
                "queue_commit_result": queue_commit,
                "queue_commit_read_back": read_back,
            }

        record = queue_write_preview.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        order_queued_id = str(record_dict.get("id") or "").strip()
        send_order_result = self.send_order_for_order_queued_automatically(
            order_queued_id,
            queue_path=queue_path,
            send_order_callable_override=send_order_callable_override,
            source_order=real_ready_order_dict,
        )
        return {
            "processed": send_order_result.get("queue_result_recorded") is True,
            "stage": "send_order",
            "order_id": order_id,
            "order_queued_id": order_queued_id,
            "blocked_reasons": list(send_order_result.get("blocked_reasons") or send_order_result.get("issues") or []),
            "execution_enable_result": enable_result,
            "real_preflight_result": preflight_result,
            "execution_preview_result": execution_preview,
            "runtime_commit_result": runtime_commit,
            "queue_commit_readiness_policy_result": queue_commit_readiness,
            "queue_commit_result": queue_commit,
            "queue_commit_read_back": read_back,
            "send_order_result": send_order_result,
        }

    def auto_process_executable_orders_for_real_trade(self, *, limit: int = 5) -> dict[str, object]:
        queue_path = ORDER_QUEUE_PATH
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"processed": 0, "blocked": 1, "results": [], "blocked_reasons": [f"failed to read order_queue json: {exc}"]}
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            return {"processed": 0, "blocked": 1, "results": [], "blocked_reasons": ["order_queue orders must be a list"]}

        results: list[dict[str, object]] = []
        processed = 0
        blocked = 0
        for item in orders:
            if len(results) >= limit:
                break
            record = item if isinstance(item, dict) else {}
            if record.get("status") != "EXECUTABLE":
                continue
            result = self.process_executable_order_for_auto_trade(str(record.get("id") or ""))
            results.append(result)
            if result.get("processed") is True:
                processed += 1
            else:
                blocked += 1

        return {"processed": processed, "blocked": blocked, "results": results, "blocked_reasons": []}

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

    def resume_status_after_pause(self, state: dict[str, object]) -> tuple[str, dict[str, object], str]:
        return auto_trade_resume_status_after_pause(self, state)

    def pre_start_review_check(self, routine_name: str, stock_dir: Path, code: str, name: str) -> dict[str, object]:
        """
        자동매매 시작 전 사전점검.

        프로그램이 먼저 점검하고, 문제 없는 종목만 RUNNING으로 전환한다.
        문제 소지가 있는 종목은 REVIEW_REQUIRED로 전환한 뒤 검토관리창에서 HTS 검토 후 처리한다.
        """
        item = build_review_required_item(routine_name, stock_dir, code, name)
        state = read_json_dict(stock_dir / "state.json")
        before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

        data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
        if data_reasons:
            return build_review_required_item(routine_name, stock_dir, code, name, data_reasons)

        # PAUSED 상태는 일시중지 기간 중 신호 검토 정책을 추가 반영한다.
        if before_status == "PAUSED":
            new_status, metadata, reason = self.resume_status_after_pause(state)
            if new_status == "REVIEW_REQUIRED":
                forced = [reason]
                item = build_review_required_item(routine_name, stock_dir, code, name, forced)
                item["resume_metadata"] = metadata
                return item

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
        reason_text = " / ".join(reasons) if reasons else "수동 검토 필요"
        review_location = str(
            source
            or item.get("review_location", "")
            or item.get("review_source", "")
            or item.get("detected_by", "")
            or "-"
        ).strip() or "-"

        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": review_location,
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "missed_buy_signal_count": safe_int_value(item.get("missed_buy_signal_count"), 0),
            "missed_sell_signal_count": safe_int_value(item.get("missed_sell_signal_count"), 0),
            "last_checked_price": safe_float_value(item.get("current_price"), 0.0),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
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
        dialog = OperationEnvironmentSettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.statusBarMessage("환경설정 저장 완료")
            self.refresh_all()

    def open_selected_stock_policy_settings(self) -> None:
        """종목 우클릭용 개별종목 설정 창."""
        selected = self.selected_stock_info()
        if selected is None:
            QMessageBox.warning(self, "선택 오류", "개별종목 설정은 종목 1개를 선택한 상태에서 사용할 수 있습니다.")
            return
        stock_dir, code, name = selected
        dialog = StockPolicyOverrideDialog(stock_dir, code, name, self)
        if dialog.exec_() == QDialog.Accepted:
            self.refresh_all()

    def set_global_schedule_time(self) -> None:
        """하위 호환용: 스케줄매매관리 창을 연다."""
        self.open_schedule_trade_management_window()

    def open_schedule_trade_management_window(self) -> None:
        dialog = ScheduleTradeManagementDialog(self)
        dialog.exec_()
        self.refresh_all()

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

        self.set_selected_operation_mode(
            "SCHEDULED",
            schedule_config_updates(
                dialog.start_time(),
                dialog.end_buy_time(),
            ),
        )

    def reset_selected_schedule_to_global(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "기본 시간으로 리셋할 종목을 1개 이상 선택하세요.")
            return

        global_schedule = read_global_schedule()
        self.set_selected_operation_mode(
            "SCHEDULED",
            schedule_config_updates(
                global_schedule["start_time"],
                global_schedule["end_buy_time"],
            ),
        )

    def set_selected_schedule_operation_mode(self) -> None:
        auto_trade_set_selected_schedule_operation_mode(self)

    def set_selected_operation_mode(
        self,
        operation_mode: str,
        config_updates: dict[str, object] | None = None,
    ) -> None:
        auto_trade_set_selected_operation_mode(self, operation_mode, config_updates)

    def set_selected_stocks_buy_end(self) -> None:
        auto_trade_set_selected_stocks_buy_end(self)

    def run_current_routine_stability_check(self) -> None:
        auto_trade_run_current_routine_stability_check(self)

    def split_start_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        매매시작 대상과 제외 대상을 분리한다.

        정책:
        - STOPPED: 강제종료/정지 상태이므로 매매시작 가능
        - MONITORING/WATCHING/WATCH/WATCH_BUY: 화면상 감시/대기지만 주문 비활성 상태이므로
          매매시작 버튼으로 현재 시간/운영방식에 맞게 재판정 가능
        - RUNNING/SELL_ONLY/REVIEW_REQUIRED/EMERGENCY 계열은 보호 상태로 제외
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []

        start_allowed_statuses = {
            "STOPPED",
            "STOP",
            "WAIT",
            "WAIT_BUY",
            "WAIT_SELL",
            "MONITORING",
            "WATCHING",
            "WATCH",
            "WATCH_BUY",
        }

        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status in start_allowed_statuses:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")

        return targets, skipped

    def split_stop_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        강제종료 대상과 제외 대상을 분리한다.

        강제종료는 최상위 중지 명령이다.
        조기마감/자동마감/청산중처럼 trade_enabled=False가 될 수 있는 상태도
        현재 동작을 끊기 위해 강제종료 대상에 포함한다.

        제외 기준은 상태값 자체가 STOPPED 계열인 경우만 사용한다.
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []

        stopped_statuses = {
            "STOPPED",
            "STOP",
        }

        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

            if status in stopped_statuses:
                skipped.append(f"{code} {name}(이미 중지됨)")
                continue

            targets.append((stock_dir, code, name))

        return targets, skipped

    def stop_risk_parts(self, stock_dir: Path) -> list[str]:
        """강제종료 시 검토관리로 보내야 하는 보유/미체결 사유."""
        state = read_json_dict(stock_dir / "state.json")
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        pending_exists, pending_qty = pending_order_summary(stock_dir, state)

        parts: list[str] = []
        if holding_qty > 0:
            parts.append(f"보유 {holding_qty:,}주")
        if pending_exists:
            parts.append(f"미체결 {pending_qty:,}주")
        return parts

    def stop_warning_items(self, selected: list[tuple[Path, str, str]]) -> list[str]:
        """
        강제종료 전 검토관리 이동 예정 종목을 반환한다.
        """
        items: list[str] = []
        for stock_dir, code, name in selected:
            parts = self.stop_risk_parts(stock_dir)
            if parts:
                items.append(f"{code} {name}({', '.join(parts)})")
        return items

    def confirm_stop_targets_once(self, selected: list[tuple[Path, str, str]]) -> bool:
        """강제종료 전 확인창은 1개만 표시한다.

        확인창 안에서 일반 중지 대상과 검토관리 대상을 함께 보여준다.
        """
        stop_items: list[str] = []
        review_items: list[str] = []

        for stock_dir, code, name in selected:
            risk_parts = self.stop_risk_parts(stock_dir)
            if risk_parts:
                review_items.append(f"{code} {name}({', '.join(risk_parts)})")
            else:
                stop_items.append(f"{code} {name}")

        def preview_lines(title: str, items: list[str]) -> str:
            if not items:
                return f"{title}: 없음"
            preview = "\n".join(f"- {item}" for item in items[:12])
            if len(items) > 12:
                preview += f"\n- 외 {len(items) - 12}개"
            return f"{title}:\n{preview}"

        dialog = QDialog(self)
        dialog.setWindowTitle("강제종료 확인")
        dialog.resize(420, 360)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title_label = QLabel("강제종료 선택종목")
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(
            f"{preview_lines('중지 대상', stop_items)}\n\n"
            f"{preview_lines('검토관리 대상', review_items)}"
        )
        body.setMinimumHeight(210)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        question_label = QLabel("진행하시겠습니까?")
        layout.addWidget(question_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("진행")
        cancel_button = QPushButton("취소")
        proceed_button.setMinimumWidth(80)
        cancel_button.setMinimumWidth(80)
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        return dialog.exec_() == QDialog.Accepted

    def show_auto_trade_result_dialog(self, title: str, heading: str, lines: list[str]) -> None:
        """강제종료 확인창과 같은 형식의 결과 표시 전용 창."""
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
        auto_trade_start_selected_auto_trades(self)


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
    def stop_selected_auto_trades(self) -> None:
        auto_trade_stop_selected_auto_trades(self)

    def open_review_required_window(self) -> None:
        auto_trade_open_review_required_window(self)

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        """부모 창 상태바에 메시지를 전달한다.

        분리 모듈에서는 MainWindow를 직접 참조하지 않는다.
        """
        parent = self.parent()
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

    def open_order_status_window(self) -> None:
        open_auto_trade_order_status_window(self)

    def open_log_view_window(self) -> None:
        open_auto_trade_log_view_window(self)

    def show_deferred_message(self) -> None:
        show_deferred_config_message(self)


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


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    자동매매설정 표시용 루틴 종목 폴더 목록을 반환한다.

    루틴 폴더 안에 물리 폴더가 남아 있어도 기초종목.txt 에 연결 정보가 없으면
    자동매매설정 창에는 표시하지 않는다.
    """
    routine_name = routine_display_name(routine_dir)
    result: list[Path] = []
    for stock_dir in get_stock_dirs_in_routine(routine_dir):
        code, name = parse_stock_folder_name(stock_dir.name)
        if not is_stock_assigned_to_routine(code, name, routine_name):
            continue
        if is_review_required_stock_dir(stock_dir):
            continue
        result.append(stock_dir)
    return result


def reset_runtime_orders_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 자동매매설정 표에 남는 매결/도결/미체결 흔적을 제거한다.

    정책:
    - 현재 화면과 판단 기준이 되는 orders.json 은 빈 주문 목록으로 초기화한다.
    - 기존 주문 기록은 즉시 삭제하지 않고 orders_archive.json 에 보존한다.
    - config.json, logs 폴더, 루틴 종목 폴더는 건드리지 않는다.
    """
    orders_path = stock_dir / "orders.json"
    archive_path = stock_dir / "orders_archive.json"

    current_orders = read_orders_data(orders_path)

    try:
        if current_orders:
            archive_data = read_json_dict(archive_path)
            archives = archive_data.get("archives", [])
            if not isinstance(archives, list):
                archives = []

            archives.append(
                {
                    "archived_at": now_text(),
                    "reason": "강제 등록해제 상태초기화로 orders.json 현재 표시/판단 흔적 초기화",
                    "orders": current_orders,
                }
            )

            archive_path.write_text(
                json.dumps({"archives": archives}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        orders_path.write_text(json.dumps(default_orders(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def reset_runtime_state_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 runtime 폴더와 설정/로그는 유지하되,
    현재 운영상태(state.json)와 화면 판단용 주문 흔적(orders.json)을 초기화한다.
    """
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    if not state:
        state = default_state()

    reset_values = {
        "status": "STOPPED",
        "trade_set_status": "WAIT_BUY",
        "current_set_no": 1,
        "current_round": 0,
        "avg_price": 0,
        "holding_qty": 0,
        "holding_amount": 0,
        "buy_count": 0,
        "last_buy_price": 0,
        "last_buy_time": "",
        "last_sell_time": "",
        "pending_order": False,
        "pending_qty": 0,
        "remaining_qty": 0,
        "unfilled_qty": 0,
        "buy_pending_qty": 0,
        "sell_pending_qty": 0,
        "paused_at": "",
        "resumed_at": "",
        "review_required": False,
        "review_reason": "",
        "missed_buy_signal_count": 0,
        "missed_sell_signal_count": 0,
        "pause_signal_check_status": "UNCHECKED",
        "ignore_signals_before": "",
        "updated_at": now_text(),
    }
    state.update(reset_values)

    try:
        if not reset_runtime_orders_for_force_unregister(stock_dir):
            return False
        if not write_state_json(stock_dir, state):
            return False
        return True
    except Exception:
        return False
