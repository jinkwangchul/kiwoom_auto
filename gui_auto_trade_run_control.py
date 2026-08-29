# -*- coding: utf-8 -*-
"""
gui_auto_trade_run_control.py

자동매매설정창의 운영시작/정지 처리 헬퍼.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from datetime import date, datetime

from PyQt5.QtWidgets import (
    QMessageBox,
    QWidget,
)

from gui_toast import show_toast
from gui_common_utils import safe_int_value
from gui_operation_ui_context import refresh_auto_trade_views
from gui_operation_environment import read_operation_policy, read_review_policy
from event_journal_production import (
    append_production_event,
    observe_owner_failure_transition,
)
from runtime_io import read_json_dict
from runtime_stock_state_mutation import mutate_runtime_stock_state
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_required_state,
    is_review_required_stock_dir,
    restart_initial_review_reason_for_stock,
)
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_register_current_session_operation_participants,
    auto_trade_retire_current_session_operation_participants,
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_trade_started,
    clear_early_close_runtime_metadata_only,
)
from gui_auto_trade_runtime import all_registered_stock_dirs, parse_stock_folder_name
from gui_auto_trade_status_ops import (
    auto_trade_stock_operation_excluded,
    set_auto_trade_stock_operation_excluded,
)
from gui_base_stock_service import read_base_stocks
from gui_order_utils import order_current_pending_qty, pending_order_side_quantities
from gui_review_utils import review_required_for_start
from execution_queue_writer import read_execution_queue_records
from execution_runtime_reader import read_order_executions, read_order_locks
from operation_close_completion_evaluator import (
    ACTIVE_QUEUE_STATUSES,
    CLOSED_QUEUE_STATUSES,
    STATUS_DONE,
    evaluate_operation_close_completion,
)
from operation_close_completion_check_service import (
    mark_end_of_operation_review_required,
)
from stock_long_hold_policy import (
    classify_termination_route,
    long_hold_excludes_holding_review,
)
from operation_policy_gate import (
    is_emergency_stop,
    read_operation_state,
    write_global_operation_running_state,
)
from state_policy import (
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
    auto_trade_status_display,
    normalize_operation_mode,
    status_after_operation_mode_change,
)
from routine_order_permission import canonical_stock_trading_time_status
from gui_ats_utils import (
    auto_trade_operation_session_phase,
    manual_ats_session_definition,
)
from gui_routine_registry import get_group_records
from main_group_projection import (
    build_main_group_projection,
    projected_main_group_stock_targets,
)
from gui_main_table_loader import main_stock_resolved_starting_budget
from routine_instance_registry import load_persisted_routine_instances


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
LOGGER = logging.getLogger(__name__)
START_REQUEST_SINGLE = "single"
START_REQUEST_MULTIPLE = "multiple"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"

_P3_OPERATION_START_RUNTIME_REASONS = frozenset(
    {
        "RUNTIME_MISSING",
        "RUNTIME_DAMAGED",
        "PENDING_ORDER_UNKNOWN",
    }
)
_P3_OPERATION_START_ERROR_REASONS = frozenset(
    {
        "TARGET_COLLECTION_FAILED",
        "TARGET_CLASSIFICATION_FAILED",
        "RECOVERY_CHECK_FAILED",
        "INTERNAL_EXCEPTION",
        "REVIEW_STATE_SAVE_FAILED",
        "STATE_SAVE_FAILED",
    }
)

_ACTIVE_CLOSE_STATUSES = {
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "LIQUIDATION",
    "LIQUIDATING",
}
_LIQUIDATION_REQUEST_KEYS = (
    "individual_liquidation_request",
    "manual_ats_liquidation_request",
)
_LIQUIDATION_REQUEST_TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "ORDER_BLOCKED",
}

_TERMINAL_EXECUTION_STATUSES = {
    "BROKER_RESULT_RECORDED",
    "COMPLETED",
    "FILLED",
    "CANCELED",
    "CANCELLED",
    "REJECTED",
    "FAILED",
    "EXPIRED",
    "LOCAL_RESET",
}
_TERMINAL_LOCK_STATUSES = {
    "RELEASED",
    "COMPLETED",
    "CANCELED",
    "CANCELLED",
    "FAILED",
    "EXPIRED",
    "LOCAL_RESET",
}


def today_global_operation_status(operation_state: dict[str, object]) -> str:
    if not isinstance(operation_state, dict):
        return ""
    operation_date = str(operation_state.get("operation_date") or "").strip()
    if operation_date != date.today().isoformat():
        return ""
    return str(operation_state.get("operation_status") or "").strip().upper()


def today_operation_start_evidence(operation_state: dict[str, object]) -> bool:
    if not isinstance(operation_state, dict):
        return False
    today = date.today().isoformat()
    if str(operation_state.get("operation_date") or "").strip() != today:
        return False
    status = today_global_operation_status(operation_state)
    if status in {"RUNNING", "CLOSING", "NORMAL_ENDED"}:
        return True
    started_at = str(operation_state.get("operation_started_at") or "").strip()
    if started_at.startswith(today):
        return True
    participants = operation_state.get("operation_participant_stock_codes")
    return isinstance(participants, list) and bool(participants)


def auto_trade_registered_operation_targets(window=None) -> list[tuple[Path, str, str]]:
    if window is not None:
        try:
            projection = build_main_group_projection(
                get_group_records(),
                load_persisted_routine_instances(),
                read_base_stocks(),
            )
            return list(
                projected_main_group_stock_targets(
                    projection,
                    project_root=PROJECT_ROOT,
                )
            )
        except Exception:
            return []
    targets: list[tuple[Path, str, str]] = []
    for stock_dir in all_registered_stock_dirs():
        code, name = parse_stock_folder_name(stock_dir.name)
        if code:
            targets.append((stock_dir, code, name))
    return targets


def _start_operation_host_after_explicit_operation_start(window) -> dict[str, object]:
    """Start the existing Recovery-bound host only after an explicit start succeeds."""

    pending = [window]
    seen: set[int] = set()
    while pending and len(seen) < 8:
        owner = pending.pop(0)
        if owner is None or id(owner) in seen:
            continue
        seen.add(id(owner))

        readiness = getattr(owner, "startup_recovery_session_ready", None)
        host_getter = getattr(owner, "main_monitoring_auto_trade_operation_host", None)
        identity = getattr(owner, "_production_recovery_identity", None)
        if callable(readiness) and callable(host_getter) and identity is not None:
            try:
                if not bool(readiness(refresh=False)):
                    return {
                        "started": False,
                        "reason_code": "RECOVERY_NOT_READY",
                    }
                host = host_getter()
                starter = getattr(host, "start_after_recovery", None)
                if not callable(starter):
                    return {
                        "started": False,
                        "reason_code": "OPERATION_HOST_START_UNAVAILABLE",
                    }
                result = starter(identity)
                setattr(owner, "_production_recovery_timer_start_result", result)
                return result if isinstance(result, dict) else {
                    "started": False,
                    "reason_code": "INVALID_OPERATION_HOST_START_RESULT",
                }
            except Exception:
                LOGGER.exception("Explicit operation-start host activation failed")
                return {
                    "started": False,
                    "reason_code": "OPERATION_HOST_START_FAILED",
                }

        nested_owner = getattr(owner, "_owner", None)
        if nested_owner is not None:
            pending.append(nested_owner)
        parent_getter = getattr(owner, "parent", None)
        if callable(parent_getter):
            try:
                parent = parent_getter()
            except Exception:
                parent = None
            if parent is not None:
                pending.append(parent)
    return {
        "started": False,
        "reason_code": "OPERATION_HOST_OWNER_UNAVAILABLE",
    }


def _recalculate_routine_limits_after_new_session(window) -> dict[str, object]:
    recalculator = getattr(
        window,
        "recalculate_routine_limits_for_new_operation_session",
        None,
    )
    if not callable(recalculator):
        return {"ok": False, "reason": "RECALCULATION_OWNER_UNAVAILABLE"}
    try:
        result = recalculator()
    except Exception as exc:
        LOGGER.exception("Routine limit recalculation failed after operation start")
        return {
            "ok": False,
            "reason": "ROUTINE_LIMIT_RECALCULATION_FAILED",
            "error": str(exc),
        }
    return result if isinstance(result, dict) else {
        "ok": False,
        "reason": "INVALID_ROUTINE_LIMIT_RECALCULATION_RESULT",
    }


def _apply_new_session_routine_limit_recalculation(
    window,
    operation_state_write: dict[str, object],
) -> dict[str, object] | None:
    if (
        operation_state_write.get("ok") is not True
        or operation_state_write.get("started_new_session") is not True
    ):
        return None
    return _recalculate_routine_limits_after_new_session(window)


def auto_trade_registered_operation_start_targets(window=None) -> list[tuple[Path, str, str]]:
    target_getter = getattr(window, "registered_operation_targets", None)
    registered = (
        list(target_getter())
        if callable(target_getter)
        else auto_trade_registered_operation_targets(window)
    )
    return [
        target
        for target in registered
        if not auto_trade_stock_operation_excluded(target[0])
    ]


def auto_trade_running_registered_operation_targets(
    window,
    *,
    registered_targets: list[tuple[Path, str, str]] | None = None,
    operation_excluded_by_stock_dir: dict[str, bool] | None = None,
    state_by_stock_dir: dict[str, dict[str, object]] | None = None,
) -> list[tuple[Path, str, str]]:
    running: list[tuple[Path, str, str]] = []
    target_getter = getattr(window, "registered_operation_targets", None)
    registered = (
        list(registered_targets)
        if registered_targets is not None
        else list(target_getter())
        if callable(target_getter)
        else auto_trade_registered_operation_targets(window)
    )
    for target in registered:
        preloaded_snapshot = (
            operation_excluded_by_stock_dir is not None
            or state_by_stock_dir is not None
        )
        stock_dir_key = (
            str(Path(target[0]))
            if preloaded_snapshot
            else str(Path(target[0]).resolve())
        )
        operation_excluded = (
            bool(operation_excluded_by_stock_dir.get(stock_dir_key, False))
            if operation_excluded_by_stock_dir is not None
            else auto_trade_stock_operation_excluded(target[0])
        )
        if operation_excluded:
            continue
        state = (
            state_by_stock_dir.get(stock_dir_key, {})
            if state_by_stock_dir is not None
            else read_json_dict(target[0] / "state.json")
        )
        if auto_trade_setting_current_session_trade_started(
            window,
            auto_trade_setting_trade_started(state),
            target[1],
        ):
            running.append(target)
    return running


def auto_trade_update_global_operation_button_state(window) -> None:
    registered_getter = getattr(window, "registered_operation_targets", None)
    running_getter = getattr(window, "running_registered_operation_targets", None)
    registered = (
        list(registered_getter())
        if callable(registered_getter)
        else auto_trade_registered_operation_targets(window)
    )
    running = (
        list(running_getter())
        if callable(running_getter)
        else auto_trade_running_registered_operation_targets(window)
    )
    operation_state = read_operation_state()
    operation_status = today_global_operation_status(operation_state)
    global_normal_ended = operation_status == "NORMAL_ENDED"
    global_emergency_stop = operation_state.get("emergency_stop") is True
    today_started = today_operation_start_evidence(operation_state)
    if running:
        text, foreground, background, hover_background = (
            "▶ 운영중", "#15803D", "#F8FAFC", "#F8FAFC"
        )
    elif today_started:
        text, foreground, background, hover_background = (
            "● 운영정지", "#111827", "#F8FAFC", "#F8FAFC"
        )
    elif global_emergency_stop:
        text, foreground, background, hover_background = (
            "긴급정지", "#991B1B", "#FEF2F2", "#FEF2F2"
        )
    else:
        text, foreground, background, hover_background = (
            "■ 운영시작", "#1D4ED8", "#F8FAFC", "#F8FAFC"
        )

    window.btn_start.setText(text)
    window.btn_start.setStyleSheet(
        "QPushButton {"
        f"color: {foreground};"
        f"border: 1px solid {foreground};"
        f"background-color: {background};"
        "font-weight: 600;"
        "}"
        "QPushButton:hover {"
        f"color: {foreground};"
        f"border-color: {foreground};"
        f"background-color: {hover_background};"
        "}"
        "QPushButton:pressed {"
        f"color: {foreground};"
        f"border-color: {foreground};"
        f"background-color: {hover_background};"
        "}"
        "QPushButton:disabled {"
        f"color: {foreground};"
        "border-color: #D1D5DB;"
        f"background-color: {background};"
        "}"
    )
    window.btn_start.setEnabled(
        bool(registered)
        and not bool(running)
        and not global_emergency_stop
        and not global_normal_ended
        and not today_started
    )


def current_datetime() -> datetime:
    """운영시작 Guard와 저장 정책이 공유하는 현재시각 경계."""
    return datetime.now()


def _normalized_stock_code(value: object) -> str:
    text = re.sub(r"[^0-9]", "", str(value or ""))
    return text[-6:] if text else ""


def _queue_named_values(value: object, key: str) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if str(current_key).strip().lower() == key:
                found.append(current_value)
            if isinstance(current_value, (dict, list, tuple)):
                found.extend(_queue_named_values(current_value, key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_queue_named_values(item, key))
    return found


def _queue_record_stock_code(record: dict[str, object]) -> str:
    for key in ("stock_code", "code", "종목코드"):
        for value in _queue_named_values(record, key.lower()):
            code = _normalized_stock_code(value)
            if code:
                return code
    return ""


def _queue_record_action(record: dict[str, object]) -> str:
    actions = {
        str(value or "").strip().upper()
        for value in _queue_named_values(record, "order_action")
        if str(value or "").strip()
    }
    if "CANCEL" in actions:
        return "CANCEL"
    return next(iter(actions), "")


def _terminal_approval_blocked_zero_quantity(
    record: dict[str, object],
    *,
    pending_qty: int,
    pending_unknown: bool,
) -> bool:
    """Identify the narrow approval-blocked SELL candidate terminal contract."""

    if str(record.get("status") or "").strip().upper() != "BLOCKED":
        return False
    if str(record.get("approval_status") or "").strip().upper() != "BLOCKED":
        return False
    if record.get("execution_enabled") is not False:
        return False
    if str(record.get("candidate_status") or "").strip().upper() != "NO_HOLDING_QTY":
        return False
    if (
        str(record.get("order_type") or "").strip().upper()
        != "SELL_NO_HOLDING_CANDIDATE"
    ):
        return False
    raw_quantity = record.get("quantity")
    if isinstance(raw_quantity, bool) or raw_quantity is None:
        return False
    try:
        quantity = int(str(raw_quantity).replace(",", "").strip())
    except (TypeError, ValueError):
        return False
    for pending_field in ("pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"):
        for raw_pending in _queue_named_values(record, pending_field.lower()):
            if raw_pending in (None, ""):
                continue
            if isinstance(raw_pending, bool):
                return False
            try:
                parsed_pending = int(str(raw_pending).replace(",", "").strip())
            except (TypeError, ValueError):
                return False
            if parsed_pending < 0:
                return False
    if quantity != 0 or pending_unknown or pending_qty != 0:
        return False
    if record.get("send_order_called") not in (None, False):
        return False
    if record.get("dispatch_claimed") is True:
        return False
    for field in (
        "execution_id",
        "order_id",
        "lock_id",
        "dispatch_claim_id",
        "dispatch_status",
        "send_status",
        "broker_order_no",
        "original_order_no",
    ):
        if any(
            str(value or "").strip()
            for value in _queue_named_values(record, field)
        ):
            return False
    return True


def _today_normal_ended(
    operation_state: dict[str, object],
    now_dt: datetime,
) -> bool:
    return (
        str(operation_state.get("operation_date") or "").strip()
        == now_dt.date().isoformat()
        and str(operation_state.get("operation_status") or "").strip().upper()
        == "NORMAL_ENDED"
    )


def _close_completion_evidence_for_today(
    state: dict[str, object],
    now_dt: datetime,
) -> bool:
    today = now_dt.date().isoformat()
    for key in (
        "liquidation_completed_at",
        "liquidation_finished_at",
        "daily_liquidation_completed_at",
        "ats_sell_completed_at",
    ):
        if str(state.get(key) or "").strip().startswith(today):
            return True
    if str(state.get("daily_liquidation_completed_date") or "").strip() == today:
        return True
    return bool(state.get("daily_liquidation_completed", False)) and not str(
        state.get("daily_liquidation_completed_at") or ""
    ).strip()


def _active_close_or_liquidation(state: dict[str, object], now_dt: datetime) -> bool:
    status = str(state.get("status") or "").strip().upper()
    stale_early_close_status = (
        status in {"EARLY_CLOSE", "EARLY_CLOSING", "EARLY_CLOSED"}
        and not auto_trade_setting_should_preserve_raw_status(state, status)
    )
    if status in _ACTIVE_CLOSE_STATUSES and not stale_early_close_status:
        return True
    if bool(state.get("liquidation_policy_forced", False)):
        return True
    if bool(state.get("close_routine_final_sell_ordered", False)) or str(
        state.get("close_routine_final_sell_ordered_at") or ""
    ).strip():
        return True
    for key in _LIQUIDATION_REQUEST_KEYS:
        request = state.get(key)
        if not isinstance(request, dict):
            continue
        request_status = str(request.get("status") or "REQUESTED").strip().upper()
        if request_status not in _LIQUIDATION_REQUEST_TERMINAL_STATUSES:
            return True

    command_mode = str(state.get("operation_command_mode") or "").strip().upper()
    if command_mode == "EARLY_CLOSE" and not stale_early_close_status:
        notice = str(state.get("operation_notice") or "").strip().upper()
        if notice != "EARLY_CLOSE_NO_TARGET" and not _close_completion_evidence_for_today(
            state, now_dt
        ):
            return True
    return False


def _active_queue_reason(
    stock_code: str,
    order_queue_path: str | Path,
) -> str:
    queue_path = Path(order_queue_path)
    snapshot = read_execution_queue_records(queue_path)
    if snapshot.get("ok") is not True:
        return "RUNTIME_DAMAGED"
    expected_code = _normalized_stock_code(stock_code)
    for record in snapshot.get("records", ()):
        if not isinstance(record, dict):
            return "RUNTIME_DAMAGED"
        if _queue_record_stock_code(record) != expected_code:
            continue
        status = str(
            record.get("status") or record.get("order_status") or ""
        ).strip().upper()
        pending_qty, unknown = order_current_pending_qty(record)
        if _terminal_approval_blocked_zero_quantity(
            record,
            pending_qty=pending_qty,
            pending_unknown=unknown,
        ):
            continue
        unresolved = (
            status in ACTIVE_QUEUE_STATUSES
            or unknown
            or pending_qty > 0
            or not status
            or status not in CLOSED_QUEUE_STATUSES
        )
        if not unresolved:
            continue
        if status == "CANCEL_REQUESTED" or _queue_record_action(record) == "CANCEL":
            return "PENDING_CANCEL"
        return "PENDING_ORDER"
    return ""


def _queue_records_for_stock(
    stock_code: str,
    order_queue_path: str | Path,
) -> tuple[dict[str, object], ...]:
    snapshot = read_execution_queue_records(Path(order_queue_path))
    if snapshot.get("ok") is not True:
        return ()
    expected_code = _normalized_stock_code(stock_code)
    return tuple(
        record
        for record in snapshot.get("records", ())
        if isinstance(record, dict)
        and _queue_record_stock_code(record) == expected_code
    )


def auto_trade_final_session_phase(
    config: dict[str, object],
    state: dict[str, object],
    *,
    now_dt: datetime | None = None,
) -> dict[str, object]:
    """Classify today's real trading windows without using UI status text."""
    return auto_trade_operation_session_phase(
        config,
        state,
        now_dt=now_dt or current_datetime(),
        operation_policy_reader=read_operation_policy,
        ats_session_reader=manual_ats_session_definition,
    )


def _active_execution_runtime_reason(
    stock_code: str,
    *,
    order_executions_path: str | Path,
    order_locks_path: str | Path,
) -> str:
    expected_code = _normalized_stock_code(stock_code)
    executions = read_order_executions(order_executions_path)
    if executions.get("ok") is not True:
        return "EXECUTION_RUNTIME_DAMAGED"
    execution_data = executions.get("data")
    for record in execution_data.get("executions", ()) if isinstance(execution_data, dict) else ():
        if not isinstance(record, dict):
            return "EXECUTION_RUNTIME_DAMAGED"
        if _queue_record_stock_code(record) != expected_code:
            continue
        status = str(record.get("status") or "").strip().upper()
        if status not in _TERMINAL_EXECUTION_STATUSES:
            return "UNRESOLVED_EXECUTION"

    locks = read_order_locks(order_locks_path)
    if locks.get("ok") is not True:
        return "ORDER_LOCK_RUNTIME_DAMAGED"
    lock_data = locks.get("data")
    for record in lock_data.get("locks", ()) if isinstance(lock_data, dict) else ():
        if not isinstance(record, dict):
            return "ORDER_LOCK_RUNTIME_DAMAGED"
        if _queue_record_stock_code(record) != expected_code:
            continue
        status = str(record.get("status") or "").strip().upper()
        if status not in _TERMINAL_LOCK_STATUSES:
            return "ACTIVE_ORDER_LOCK"
    return ""


def auto_trade_time_end_retirement_eligibility(
    *,
    stock_dir: str | Path,
    stock_code: str,
    config: dict[str, object],
    state: dict[str, object],
    operation_state: dict[str, object],
    now_dt: datetime | None = None,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    order_locks_path: str | Path = ORDER_LOCKS_PATH,
    close_completion_status: object = "",
) -> dict[str, object]:
    """Fail-closed read-only eligibility for normal final-time retirement."""

    current = now_dt or current_datetime()
    phase = auto_trade_final_session_phase(config, state, now_dt=current)
    blockers: list[str] = []
    if phase.get("evaluable") is not True:
        blockers.append(str(phase.get("phase") or "SESSION_EVIDENCE_INVALID"))
    elif phase.get("final_session_ended") is not True:
        blockers.append(str(phase.get("phase") or "SESSION_NOT_FINAL"))

    if is_emergency_stop(operation_state):
        blockers.append("GLOBAL_EMERGENCY_STOP")
    operation_date = str(operation_state.get("operation_date") or "").strip()
    operation_status = str(operation_state.get("operation_status") or "").strip().upper()
    if operation_date != current.date().isoformat() or operation_status not in {
        "RUNNING",
        "CLOSING",
        "NORMAL_ENDED",
    }:
        blockers.append("OPERATION_SESSION_EVIDENCE_UNRESOLVED")
    if is_emergency_stopped_state(state):
        blockers.append("EMERGENCY_STOPPED")
    if is_review_required_state(state):
        blockers.append("REVIEW_REQUIRED")

    review_needed, _review_reason, details = restart_initial_review_reason_for_stock(
        Path(stock_dir), state
    )
    holding_qty = details.get("holding_qty")
    holding_present = isinstance(holding_qty, int) and holding_qty > 0
    position_mismatch = (
        holding_qty in (0, None)
        and (
            details.get("holding_amount") not in (0, 0.0, None)
            or details.get("avg_price") not in (0, 0.0, None)
        )
    )
    if position_mismatch:
        blockers.append("HOLDING_OR_POSITION_UNRESOLVED")
    for side, value in (
        ("BUY", details.get("buy_pending_qty")),
        ("SELL", details.get("sell_pending_qty")),
    ):
        if value == "?":
            blockers.append("PENDING_ORDER_UNKNOWN")
        elif isinstance(value, int) and value > 0:
            blockers.append(f"PENDING_{side}")
    if review_needed and not holding_present and not any(
        reason in blockers
        for reason in (
            "HOLDING_OR_POSITION_UNRESOLVED",
            "PENDING_ORDER_UNKNOWN",
            "PENDING_BUY",
            "PENDING_SELL",
        )
    ):
        blockers.append("RUNTIME_EVIDENCE_INCONSISTENT")

    queue_reason = _active_queue_reason(stock_code, order_queue_path)
    if queue_reason:
        blockers.append(queue_reason)
    execution_reason = _active_execution_runtime_reason(
        stock_code,
        order_executions_path=order_executions_path,
        order_locks_path=order_locks_path,
    )
    if execution_reason:
        blockers.append(execution_reason)
    close_completion_status = str(close_completion_status or "").strip().upper()
    close_completion_done = (
        close_completion_status == STATUS_DONE and holding_qty == 0
    )
    close_active = _active_close_or_liquidation(state, current)
    if close_active and not close_completion_done:
        blockers.append("CLOSE_LIQUIDATION_ACTIVE")

    queue_records = _queue_records_for_stock(stock_code, order_queue_path)
    route = classify_termination_route(
        state,
        operation_mode=normalize_operation_mode(config.get("operation_mode", "SCHEDULED")),
        final_session_ended=phase.get("final_session_ended") is True,
        queue_records=queue_records,
    )
    long_hold_enabled = bool(
        read_review_policy().get("long_term_holding_enabled", False)
    )
    pending_or_runtime_issue = any(
        reason in blockers
        for reason in (
            "PENDING_ORDER_UNKNOWN",
            "PENDING_BUY",
            "PENDING_SELL",
            "PENDING_ORDER",
            "PENDING_CANCEL",
            "RUNTIME_DAMAGED",
            "EXECUTION_RUNTIME_DAMAGED",
            "UNRESOLVED_EXECUTION",
            "ORDER_LOCK_RUNTIME_DAMAGED",
            "ACTIVE_ORDER_LOCK",
        )
    )
    long_hold_allowed = holding_present and long_hold_excludes_holding_review(
        long_hold_enabled,
        state,
        holding_qty=int(holding_qty),
        buy_pending_qty=details.get("buy_pending_qty"),
        sell_pending_qty=details.get("sell_pending_qty"),
        safety_issue=(
            position_mismatch
            or pending_or_runtime_issue
            or is_emergency_stopped_state(state)
            or is_emergency_stop(operation_state)
            or route.get("safety_issue") is True
        ),
        operation_mode=normalize_operation_mode(config.get("operation_mode", "SCHEDULED")),
        final_session_ended=phase.get("final_session_ended") is True,
        queue_records=queue_records,
    )
    review_required = False
    terminal_safety_issue = bool(
        route.get("route_completed") is True
        and (
            pending_or_runtime_issue
            or position_mismatch
            or route.get("safety_issue") is True
        )
    )
    if terminal_safety_issue:
        blockers = [
            reason for reason in blockers if reason != "CLOSE_LIQUIDATION_ACTIVE"
        ]
        blockers.append("END_OF_OPERATION_SAFETY_REVIEW_REQUIRED")
        review_required = True
    if holding_present:
        if long_hold_allowed:
            blockers = [
                reason
                for reason in blockers
                if reason not in {
                    "HOLDING_OR_POSITION_UNRESOLVED",
                    "RUNTIME_EVIDENCE_INCONSISTENT",
                    "CLOSE_LIQUIDATION_ACTIVE",
                }
            ]
        elif not terminal_safety_issue and (
            route.get("route_completed") is True
            and not pending_or_runtime_issue
        ):
            blockers = [
                reason for reason in blockers if reason != "CLOSE_LIQUIDATION_ACTIVE"
            ]
            blockers.append("END_OF_OPERATION_RESIDUAL_REVIEW_REQUIRED")
            review_required = True
        else:
            blockers.append("HOLDING_OR_POSITION_UNRESOLVED")

    unique_blockers = tuple(dict.fromkeys(blockers))
    return {
        "eligible": not unique_blockers,
        "stock_code": _normalized_stock_code(stock_code),
        "phase": phase,
        "blockers": unique_blockers,
        "holding_qty": holding_qty,
        "long_term_holding_enabled": long_hold_enabled,
        "long_hold_allowed": long_hold_allowed,
        "review_required": review_required,
        "termination_route": route,
        "close_completion_status": close_completion_status,
        "close_completion_done": close_completion_done,
    }


def _time_end_close_completion_status_by_stock(
    targets: dict[str, tuple[Path, str]],
    *,
    operation_state: dict[str, object],
    now_dt: datetime,
    order_queue_path: str | Path,
) -> tuple[dict[str, str], dict[str, object]]:
    """Read existing durable close evidence without applying global completion."""

    operation_status = str(
        operation_state.get("operation_status") or ""
    ).strip().upper()
    if operation_status != "CLOSING" or not targets:
        return {}, {
            "evaluated": False,
            "reason_code": "GLOBAL_OPERATION_NOT_CLOSING",
        }

    runtime_dir = Path(order_queue_path).resolve().parent
    stock_parents = {
        stock_dir.resolve().parent for stock_dir, _name in targets.values()
    }
    stocks_dir = (
        next(iter(stock_parents))
        if len(stock_parents) == 1
        else PROJECT_ROOT / "stocks"
    )
    policy_root = stocks_dir.parent if stocks_dir.name == "stocks" else stocks_dir
    try:
        result = evaluate_operation_close_completion(
            today=now_dt.date().isoformat(),
            operation_state_path=runtime_dir / "operation_state.json",
            stocks_dir=stocks_dir,
            order_queue_path=Path(order_queue_path),
            positions_path=runtime_dir / "positions.json",
            broker_holdings_path=runtime_dir / "broker_holdings.json",
            operation_policy_path=policy_root / "operation_policy.json",
        )
    except Exception as exc:
        LOGGER.exception("Time-end close completion evidence read failed")
        return {}, {
            "evaluated": False,
            "reason_code": "CLOSE_COMPLETION_EVIDENCE_UNAVAILABLE",
            "error": str(exc),
        }

    statuses = {
        _normalized_stock_code(item.get("stock_code")): str(
            item.get("status") or ""
        ).strip().upper()
        for item in result.get("stock_results", ())
        if isinstance(item, dict) and _normalized_stock_code(item.get("stock_code"))
    }
    return statuses, dict(result)


def auto_trade_retire_time_ended_current_session_participants(
    window,
    *,
    now_dt: datetime | None = None,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    order_locks_path: str | Path = ORDER_LOCKS_PATH,
) -> dict[str, object]:
    """Evaluate and retire only obligation-free final-time participants."""

    before = auto_trade_current_session_operation_participant_codes(window)
    targets = {
        _normalized_stock_code(code): (Path(stock_dir), str(name or ""))
        for stock_dir, code, name in auto_trade_registered_operation_targets(window)
        if _normalized_stock_code(code)
    }
    operation_state = read_operation_state()
    current = now_dt or current_datetime()
    close_completion_statuses, close_completion_evidence = (
        _time_end_close_completion_status_by_stock(
            targets,
            operation_state=operation_state,
            now_dt=current,
            order_queue_path=order_queue_path,
        )
    )
    evaluations: list[dict[str, object]] = []
    requested: list[str] = []
    for code in before:
        target = targets.get(_normalized_stock_code(code))
        if target is None:
            evaluations.append(
                {
                    "eligible": False,
                    "stock_code": code,
                    "blockers": ("REGISTERED_TARGET_UNAVAILABLE",),
                }
            )
            continue
        stock_dir, _name = target
        config = read_json_dict(stock_dir / "config.json")
        state = read_json_dict(stock_dir / "state.json")
        if not config or not state:
            evaluations.append(
                {
                    "eligible": False,
                    "stock_code": code,
                    "blockers": ("RUNTIME_DAMAGED",),
                }
            )
            continue
        evaluation = auto_trade_time_end_retirement_eligibility(
            stock_dir=stock_dir,
            stock_code=code,
            config=config,
            state=state,
            operation_state=operation_state,
            now_dt=current,
            order_queue_path=order_queue_path,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            close_completion_status=close_completion_statuses.get(
                _normalized_stock_code(code),
                "",
            ),
        )
        if evaluation.get("review_required") is True:
            blockers = tuple(evaluation.get("blockers", ()))
            review_reason = (
                "PENDING_ORDER"
                if any("PENDING" in str(reason) for reason in blockers)
                else "EVIDENCE_CONFLICT"
                if "END_OF_OPERATION_SAFETY_REVIEW_REQUIRED" in blockers
                else "HOLDING_REMAINS"
            )
            review_result = mark_end_of_operation_review_required(
                stock_dir=stock_dir,
                stock_code=code,
                reason_code=review_reason,
                termination_route=evaluation.get("termination_route"),
                source="TIME_END_PARTICIPANT_RETIREMENT",
            )
            marked = review_result.get("ok") is True
            review_blockers = (
                blockers if marked else (*blockers, "REVIEW_STATE_SAVE_FAILED")
            )
            evaluation = {
                **evaluation,
                "review_marked": marked,
                "review_result": review_result,
                "eligible": False,
                "blockers": tuple(dict.fromkeys(review_blockers)),
            }
        evaluations.append(evaluation)
        if evaluation.get("eligible") is True:
            requested.append(code)

    retirement = auto_trade_retire_current_session_operation_participants(
        window,
        requested,
    )
    return {
        **retirement,
        "evaluations": tuple(evaluations),
        "close_completion_evidence": close_completion_evidence,
        "persisted_operation_state_changed": False,
    }


def auto_trade_same_day_restart_guard(
    *,
    stock_dir: str | Path,
    stock_code: str,
    config: dict[str, object],
    state: dict[str, object],
    operation_state: dict[str, object],
    now_dt: datetime | None = None,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
) -> dict[str, object]:
    """동일 거래일 명시적 운영시작을 위한 read-only 공통 Guard."""
    current = now_dt or current_datetime()

    def blocked(reason: str) -> dict[str, object]:
        return {"allowed": False, "reason": reason}

    if is_emergency_stop(operation_state):
        return blocked("GLOBAL_EMERGENCY_STOP")
    if is_emergency_stopped_state(state):
        return blocked("EMERGENCY_STOPPED")
    if is_review_required_state(state):
        return blocked("REVIEW_REQUIRED")

    review_needed, _review_reason, details = restart_initial_review_reason_for_stock(
        Path(stock_dir), state
    )
    holding_qty = details.get("holding_qty")
    buy_pending_qty = details.get("buy_pending_qty")
    sell_pending_qty = details.get("sell_pending_qty")
    if isinstance(holding_qty, int) and holding_qty > 0:
        return blocked("HOLDING_EXISTS")
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return blocked("PENDING_ORDER_UNKNOWN")
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return blocked("PENDING_BUY")
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return blocked("PENDING_SELL")
    if review_needed:
        return blocked("RUNTIME_DAMAGED")

    queue_reason = _active_queue_reason(stock_code, order_queue_path)
    if queue_reason:
        return blocked(queue_reason)
    if _active_close_or_liquidation(state, current):
        return blocked("CLOSE_LIQUIDATION_ACTIVE")
    return {"allowed": True, "reason": "ALLOWED"}


def auto_trade_operation_time_allowed(
    config: dict[str, object],
    *,
    state: dict[str, object] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    """Return whether the target is currently inside its actual trading window.

    Operation Start admission intentionally does not use this projection.
    """
    current = now_dt or current_datetime()
    time_status = canonical_stock_trading_time_status(
        config=config,
        state=state or {},
        now_dt=current,
    )
    return time_status.get("active") is True


def startup_recovery_operation_block_message(action: str, reason: str = "") -> str:
    message = (
        f"{action}할 수 없습니다. "
        "로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오."
    )
    _code, separator, detail = str(reason or "").partition(":")
    clean_detail = detail.strip() if separator else ""
    if clean_detail and not _is_internal_reason_code(clean_detail):
        message += f"\n\n원인: {clean_detail}"
    return message


def _global_start_prerequisite_result(
    window,
    *,
    action: str,
) -> dict[str, object] | None:
    checker = getattr(window, "global_operation_start_prerequisite", None)
    if not callable(checker):
        return None
    try:
        result = checker(action)
    except Exception:
        LOGGER.exception("운영 시작 Global prerequisite 판정 실패")
        return {
            "allowed": False,
            "reason": "GLOBAL_PREREQUISITE_CHECK_FAILED",
            "user_message": (
                "운영 시작 전 서버와 계좌 상태를 확인하는 중 오류가 발생했습니다.\n"
                "로그를 확인한 뒤 다시 시도하십시오."
            ),
        }
    if not isinstance(result, dict):
        return None
    if result.get("allowed") is True:
        return None
    reason = str(result.get("reason") or "GLOBAL_PREREQUISITE_NOT_READY").strip()
    message = str(result.get("user_message") or "").strip()
    if not message:
        message = startup_recovery_operation_block_message(action, reason)
    return {
        "allowed": False,
        "reason": reason,
        "user_message": message,
    }


def _show_operation_warning(window, title: str, message: str) -> None:
    setattr(window, "_last_operation_failure_dialog_shown", True)
    setattr(window, "_last_operation_failure_dialog_title", title)
    setattr(window, "_last_operation_failure_dialog_message", message)
    parent_getter = getattr(window, "operation_message_parent", None)
    parent = parent_getter() if callable(parent_getter) else window
    if not isinstance(parent, QWidget):
        return
    QMessageBox.warning(parent, title, message)


def _show_operation_start_failure_toast(window, message: str) -> None:
    setattr(window, "_last_operation_failure_dialog_shown", True)
    setattr(window, "_last_operation_failure_dialog_title", "운영 시작 불가")
    setattr(window, "_last_operation_failure_dialog_message", message)
    parent_getter = getattr(window, "operation_message_parent", None)
    parent = parent_getter() if callable(parent_getter) else window
    if not isinstance(parent, QWidget):
        return
    show_toast(
        parent=parent,
        message=message,
        duration_ms=2500,
        position="center",
    )


def _target_identity_lines(targets) -> list[str]:
    lines: list[str] = []
    for target in targets or ():
        code = str(getattr(target, "code", "") or "").strip()
        name = str(getattr(target, "name", "") or "").strip()
        identity = " ".join(part for part in (code, name) if part)
        if identity and identity not in lines:
            lines.append(identity)
    return lines


def _is_internal_reason_code(reason: str) -> bool:
    code = str(reason or "").split(":", 1)[0].strip()
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", code))


def format_auto_trade_operation_failure_dialog(
    window,
    action: str,
    result: dict[str, object] | None,
    targets=(),
) -> tuple[str, str] | None:
    result = result if isinstance(result, dict) else {}
    reason = str(result.get("reason") or "").strip()
    if reason == "CANCELLED":
        return None

    user_message = str(result.get("user_message") or "").strip()
    if not user_message:
        user_message = str(
            getattr(window, "_last_operation_user_message", "") or ""
        ).strip()
    if user_message:
        return "운영 시작 불가", user_message

    if reason in {
        "RECOVERY_NOT_READY",
        "BLOCKED_RECOVERY",
        "PRODUCTION_RECOVERY_BLOCKED",
        "REVIEW_REQUIRED",
        "EMERGENCY_STOPPED",
    } or any(
        token in reason
        for token in ("INVALID_RUNTIME", "REVIEW_REQUIRED", "EMERGENCY_STOPPED")
    ):
        message = startup_recovery_operation_block_message(action, reason)
        if reason == "REVIEW_REQUIRED":
            details: list[str] = []
            for target in targets or ():
                stock_dir = getattr(target, "stock_dir", None)
                if stock_dir is None:
                    continue
                state = read_json_dict(Path(stock_dir) / "state.json")
                review_reason = str(state.get("review_reason") or "").strip()
                code = str(getattr(target, "code", "") or "").strip()
                name = str(getattr(target, "name", "") or "").strip()
                identity = " ".join(part for part in (code, name) if part)
                if review_reason:
                    details.append(f"{identity}\n사유: {review_reason}")
            if details:
                message = (
                    f"{action} 불가\n\n복구 검토가 완료되지 않은 종목이 있습니다.\n\n"
                    + "\n\n".join(details)
                    + "\n\n검토관리에서 해당 종목을 처리한 후 다시 시도하십시오."
                )
        return "운영 시작 불가", message

    identities = _target_identity_lines(targets)
    if reason == "NO_TARGETS":
        return "선택 오류", "감시를 시작할 종목을 1개 이상 선택하세요."
    if reason == "NO_STARTABLE_TARGETS":
        message = "운영 시작 가능한 종목이 없습니다."
        if identities:
            message += "\n\n" + "\n".join(identities)
        return "운영 시작 불가", message
    if reason:
        message = f"{action}을 처리하지 못했습니다."
        if not _is_internal_reason_code(reason):
            message += f"\n\n사유: {reason}"
        else:
            message += "\n\n로그인, 계좌 및 운영 상태를 확인한 후 다시 시도하십시오."
        if identities:
            message += "\n\n대상:\n" + "\n".join(identities)
        return "운영 시작 불가", message
    return None


def show_auto_trade_operation_failure_dialog(
    window,
    action: str,
    result: dict[str, object] | None,
    targets=(),
) -> bool:
    if bool(getattr(window, "_last_operation_failure_dialog_shown", False)):
        return False
    dialog = format_auto_trade_operation_failure_dialog(
        window,
        action,
        result,
        targets,
    )
    if dialog is None:
        return False
    title, message = dialog
    _show_operation_start_failure_toast(window, message)
    return True


def _start_failure_user_message(
    failure_reasons: list[str],
    *,
    all_emergency: bool = False,
    all_review: bool = False,
    all_already_running: bool = False,
    time_eligible_targets: bool = False,
    blocked_target_details: tuple[dict[str, object], ...] = (),
    already_running_targets: tuple[tuple[Path, str, str], ...] = (),
) -> str:
    if all_emergency:
        return "모든 종목이 긴급정지 상태입니다."
    grouped_message = _blocked_target_groups_message(
        blocked_target_details,
        already_running_targets=already_running_targets,
    )
    if grouped_message:
        return grouped_message
    reasons = {str(item or "").strip() for item in failure_reasons if str(item or "").strip()}
    if time_eligible_targets:
        reasons.discard("OUTSIDE_OPERATION_TIME")
    if (
        reasons
        and reasons <= {"EMERGENCY_STOPPED", "EMERGENCY_STOP", "EMERGENCY"}
    ):
        return "모든 종목이 긴급정지 상태입니다."
    if all_review:
        return (
            "모든 등록 종목이 검토 대상으로 분리되어 있습니다.\n"
            "검토관리에서 처리한 뒤 다시 시도하십시오."
        )
    if all_already_running:
        return "선택한 루틴은 이미 운영 중입니다."
    if reasons and reasons <= {"STARTING_BUDGET_UNRESOLVED"}:
        return (
            "현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오."
        )
    if reasons and reasons <= {"INVALID_INITIAL_BUY_QUANTITY"}:
        return (
            "초회 매수 주수가 설정되지 않았습니다.\n"
            "자동매매 설정에서 1주 이상으로 설정하십시오."
        )
    if reasons and reasons <= {"MISSING_REQUIRED_SETTINGS"}:
        return (
            "모든 등록 종목의 필수 설정이 완료되지 않았습니다.\n"
            "자동매매 설정을 확인하십시오."
        )
    if reasons and reasons <= {"REVIEW_REQUIRED"}:
        return (
            "모든 등록 종목이 검토 대상으로 분리되었습니다.\n"
            "검토관리에서 처리한 뒤 다시 시도하십시오."
        )
    if "NORMAL_ENDED" in reasons:
        return "오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오."
    if reasons and reasons <= {"OUTSIDE_OPERATION_TIME"}:
        return "현재는 매매 운영 시간이 아닙니다."
    if reasons & {"HOLDING_EXISTS"}:
        return "보유수량이 남아 있어 운영을 다시 시작할 수 없습니다."
    if reasons & {"PENDING_BUY", "PENDING_SELL", "PENDING_ORDER", "PENDING_ORDER_UNKNOWN"}:
        return "미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다."
    if reasons & {"PENDING_CANCEL"}:
        return "취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다."
    if reasons & {"CLOSE_LIQUIDATION_ACTIVE"}:
        return "마감 또는 청산 절차가 진행 중이어서 운영을 다시 시작할 수 없습니다."
    if reasons & {"RUNTIME_MISSING", "RUNTIME_DAMAGED"}:
        return (
            "종목의 운영 상태 데이터를 읽을 수 없습니다.\n"
            "검토관리에서 Runtime 상태를 확인하십시오."
        )
    if reasons & {"STATE_SAVE_FAILED", "REVIEW_STATE_SAVE_FAILED"}:
        return (
            "종목의 운영 상태를 저장하지 못했습니다.\n"
            "로그를 확인한 뒤 다시 시도하십시오."
        )
    if "INTERNAL_EXCEPTION" in reasons:
        return (
            "운영 상태를 확인하는 중 오류가 발생했습니다.\n"
            "로그를 확인한 뒤 다시 시도하십시오."
        )
    return (
        "현재 운영을 시작할 수 있는 종목이 없습니다.\n"
        "검토관리와 자동매매 설정을 확인하십시오."
    )


_START_BLOCK_REASON_LABELS = {
    "FINAL_SESSION_ENDED": "시간운영 종료",
    "TIME_OPERATION_FINAL_END": "시간운영 종료",
    "REVIEW_REQUIRED": "검토관리 필요",
    "RECOVERY_NOT_READY": "복구 준비 미완료",
    "RECOVERY_STOCK_PENDING": "복구 준비 미완료",
    "RECOVERY_STOCK_FAILED": "복구 실패",
    "RECOVERY_STOCK_REVIEW_REQUIRED": "복구 검토 필요",
    "EMERGENCY_STOPPED": "긴급정지",
    "CLOSE_LIQUIDATION_ACTIVE": "마감/청산 진행",
    "ALREADY_RUNNING": "이미 운영중",
}

_START_BLOCK_REASON_SUMMARY_LABELS = {
    "REVIEW_REQUIRED": "검토관리",
    "RECOVERY_NOT_READY": "복구 미완료",
    "RECOVERY_STOCK_PENDING": "복구 미완료",
    "RECOVERY_STOCK_FAILED": "복구 실패",
    "RECOVERY_STOCK_REVIEW_REQUIRED": "복구 검토",
}


def _blocked_target_groups_message(
    blocked_target_details: tuple[dict[str, object], ...] | list[dict[str, object]],
    *,
    already_running_targets: tuple[tuple[Path, str, str], ...] = (),
) -> str:
    groups: dict[tuple[str, str], list[str]] = {}
    for detail in blocked_target_details or ():
        if not isinstance(detail, dict):
            continue
        reason = str(detail.get("reason") or "").strip() or "NOT_STARTABLE"
        operation_mode = ""
        if reason in {"FINAL_SESSION_ENDED", "TIME_OPERATION_FINAL_END"}:
            operation_mode = normalize_operation_mode(
                detail.get("operation_mode", "SCHEDULED")
            )
        label = str(detail.get("display_label") or "").strip()
        if not label:
            label = " ".join(
                part
                for part in (
                    str(detail.get("stock_code") or "").strip(),
                    str(detail.get("stock_name") or "").strip(),
                )
                if part
            )
        if label:
            key = (reason, operation_mode)
            groups.setdefault(key, [])
            if label not in groups[key]:
                groups[key].append(label)

    lines: list[str] = []
    if already_running_targets:
        lines.append(f"운영중 유지: {len(already_running_targets)}종목")
    for (reason, operation_mode), labels in groups.items():
        title = (
            "수동운영 최종 세션 종료"
            if reason in {"FINAL_SESSION_ENDED", "TIME_OPERATION_FINAL_END"}
            and operation_mode == "CONTINUOUS"
            else _START_BLOCK_REASON_LABELS.get(reason, "운영 시작 불가")
        )
        lines.append(f"{title}: {len(labels)}종목")
        lines.extend(f"- {label}" for label in labels)
    return "\n".join(lines)


def _blocked_target_reason_counts(
    blocked_target_details: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> tuple[tuple[str, int], ...]:
    groups: dict[tuple[str, str], set[str]] = {}
    for index, detail in enumerate(blocked_target_details or ()):
        if not isinstance(detail, dict):
            continue
        reason = str(detail.get("reason") or "").strip() or "NOT_STARTABLE"
        operation_mode = ""
        if reason in {"FINAL_SESSION_ENDED", "TIME_OPERATION_FINAL_END"}:
            operation_mode = normalize_operation_mode(
                detail.get("operation_mode", "SCHEDULED")
            )
        identity = str(detail.get("display_label") or "").strip()
        if not identity:
            identity = str(detail.get("stock_code") or "").strip()
        if not identity:
            identity = f"detail:{index}"
        groups.setdefault((reason, operation_mode), set()).add(identity)

    summaries: dict[str, int] = {}
    for (reason, operation_mode), identities in groups.items():
        if reason in {"FINAL_SESSION_ENDED", "TIME_OPERATION_FINAL_END"}:
            title = (
                "수동운영 종료"
                if operation_mode == "CONTINUOUS"
                else "시간운영 종료"
            )
        else:
            title = _START_BLOCK_REASON_SUMMARY_LABELS.get(
                reason,
                _START_BLOCK_REASON_LABELS.get(reason, "운영 시작 불가"),
            )
        summaries[title] = summaries.get(title, 0) + len(identities)
    return tuple(summaries.items())


def operation_start_result_summary_toast_text(result: dict[str, object]) -> str:
    """Project an existing batch result into the fixed four-field summary."""

    blocked_target_details = tuple(
        detail
        for detail in result.get("blocked_target_details", ()) or ()
        if isinstance(detail, dict)
    )
    already_running_targets = tuple(result.get("already_running_targets", ()) or ())
    completed_targets = tuple(result.get("completed", ()) or ())
    failed_targets = tuple(result.get("failed", ()) or ())
    started_count = safe_int_value(
        result.get("started_count"),
        len(completed_targets),
    )
    blocked_count = safe_int_value(
        result.get("blocked_count"),
        len(blocked_target_details),
    )
    failed_count = safe_int_value(
        result.get("failed_count"),
        len(failed_targets),
    )
    already_running_count = safe_int_value(
        result.get("already_running_count"),
        len(already_running_targets),
    )

    requested_targets = tuple(result.get("requested", ()) or ())
    target_count = safe_int_value(
        result.get("requested_count"),
        len(requested_targets)
        or started_count + already_running_count + blocked_count + failed_count,
    )
    unavailable_count = max(0, target_count - already_running_count - started_count)
    parts = [
        f"대상종목 {target_count}",
        f"기운영중 {already_running_count}",
        f"운영시작 {started_count}",
        f"운영불가 {unavailable_count}",
    ]

    reason_counts = _blocked_target_reason_counts(blocked_target_details)
    if not reason_counts:
        return "  |  ".join(parts)
    reason_line = " · ".join(f"{title} {count}" for title, count in reason_counts)
    return f"{'  |  '.join(parts)}\n{reason_line}"



def _show_operation_start_summary_toast(
    window,
    result: dict[str, object],
) -> None:
    """Show the canonical multi-start result without a completion dialog."""

    message = operation_start_result_summary_toast_text(result)
    result["summary_toast_message"] = message
    _record_operation_start_p3_result(window, result)
    status_message = getattr(window, "statusBarMessage", None)
    has_partial_result = any(
        safe_int_value(result.get(key), 0)
        for key in (
            "blocked_count",
            "already_running_count",
            "excluded_review_count",
            "excluded_validation_count",
            "failed_count",
        )
    )
    if has_partial_result and callable(status_message):
        status_message(str(result.get("user_message") or message))

    parent_getter = getattr(window, "operation_message_parent", None)
    parent = parent_getter() if callable(parent_getter) else window
    if not isinstance(parent, QWidget):
        return
    show_toast(
        parent=parent,
        message=message,
        duration_ms=3200 if "\n" in message else 2000,
        position="center",
    )


def _start_target_block_details(window) -> tuple[dict[str, object], ...]:
    getter = getattr(window, "start_target_block_details", None)
    if not callable(getter):
        return ()
    try:
        details = getter()
    except Exception:
        return ()
    return tuple(dict(item) for item in details or () if isinstance(item, dict))


def _all_start_targets_emergency_stopped(
    targets: list[tuple[Path, str, str]],
) -> bool:
    return bool(targets) and all(
        is_emergency_stopped_state(read_json_dict(Path(stock_dir) / "state.json"))
        for stock_dir, _code, _name in targets
    )


def _start_target_identity(
    target: tuple[Path, str, str] | None,
) -> tuple[str, str, str]:
    if target is None:
        return "", "", ""
    _stock_dir, code, name = target
    code = str(code or "").strip()
    name = str(name or "").strip()
    return code, name, " ".join(part for part in (code, name) if part) or "선택 대상"


def _subject_text(label: str) -> str:
    last_character = str(label or "").strip()[-1:]
    if last_character and "\uac00" <= last_character <= "\ud7a3":
        particle = "은" if (ord(last_character) - ord("\uac00")) % 28 else "는"
    else:
        particle = "는"
    return f"{label}{particle}"


def _single_start_failure_user_message(
    target: tuple[Path, str, str],
    reason: str,
) -> str:
    stock_dir, _code, _name = target
    code, name, label = _start_target_identity(target)
    reason = str(reason or "").strip()
    state = read_json_dict(Path(stock_dir) / "state.json")
    status = str(state.get("status") or "").strip().upper()
    review_reason = str(state.get("review_reason") or "").strip()
    subject = _subject_text(label)

    if reason in {"REVIEW_REQUIRED", "RECOVERY_STOCK_REVIEW_REQUIRED"}:
        if is_emergency_stopped_state(state) or "긴급" in review_reason:
            return f"{subject} 긴급정지 상태입니다."
        return (
            f"{subject} 검토관리 대상입니다.\n"
            "검토관리에서 처리한 뒤 다시 시도하십시오."
        )
    if reason == "ALREADY_RUNNING":
        return f"{subject} 이미 운영 중입니다."
    if reason == "MISSING_REQUIRED_SETTINGS":
        return (
            f"{label}의 필수 운영 설정이 완료되지 않았습니다.\n"
            "자동매매 설정을 확인한 뒤 다시 시도하십시오."
        )
    if reason == "STARTING_BUDGET_UNRESOLVED":
        return (
            f"{label}의 현재 세션 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오."
        )
    if reason == "INVALID_INITIAL_BUY_QUANTITY":
        return (
            f"{label}의 초회 매수 주수가 설정되지 않았습니다.\n"
            "자동매매 설정에서 1주 이상으로 설정하십시오."
        )
    if reason in {"RUNTIME_MISSING", "RUNTIME_DAMAGED"}:
        return (
            f"{label}의 운영 상태 데이터를 읽을 수 없습니다.\n"
            "검토관리에서 Runtime 상태를 확인하십시오."
        )
    if reason in {
        "STATE_SAVE_FAILED",
        "REVIEW_STATE_SAVE_FAILED",
        "STATE_READBACK_FAILED",
    }:
        return (
            f"{label}의 운영 상태를 저장하거나 다시 확인하지 못했습니다.\n"
            "로그를 확인한 뒤 다시 시도하십시오."
        )
    if reason == "RECOVERY_STOCK_PENDING":
        return (
            f"{label}의 Recovery가 아직 완료되지 않았습니다.\n"
            "복구가 완료된 뒤 다시 시도하십시오."
        )
    if reason == "RECOVERY_STOCK_FAILED":
        return (
            f"{label}의 Recovery에 실패했습니다.\n"
            "검토관리에서 상태를 확인하십시오."
        )
    if reason == "NORMAL_ENDED":
        return "오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오."
    if reason in {"FINAL_SESSION_ENDED", "TIME_OPERATION_FINAL_END"}:
        return f"{subject} 시간운영 종료로 운영을 시작할 수 없습니다."
    if reason == "OUTSIDE_OPERATION_TIME":
        return f"{subject} 현재 운영시작 가능 시간이 아닙니다."
    if reason == "HOLDING_EXISTS":
        return f"{subject} 보유수량이 남아 있어 운영을 다시 시작할 수 없습니다."
    if reason in {"PENDING_BUY", "PENDING_SELL", "PENDING_ORDER", "PENDING_ORDER_UNKNOWN"}:
        return f"{subject} 미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다."
    if reason == "PENDING_CANCEL":
        return f"{subject} 취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다."
    if reason == "CLOSE_LIQUIDATION_ACTIVE":
        return f"{subject} 마감 또는 청산 절차가 진행 중입니다."
    if reason in {"TARGET_CLASSIFICATION_FAILED", "INTERNAL_EXCEPTION"}:
        return (
            f"{label}의 운영 상태를 확인하는 중 오류가 발생했습니다.\n"
            "로그를 확인한 뒤 다시 시도하십시오."
        )
    if is_emergency_stopped_state(state):
        return f"{subject} 긴급정지 상태입니다."
    return (
        f"{subject} 현재 운영을 시작할 수 없는 상태입니다.\n"
        "자동매매 설정과 검토관리 상태를 확인하십시오."
    )


def _apply_start_request_context(
    result: dict[str, object],
    *,
    request_scope: str,
    selected: list[tuple[Path, str, str]],
    request_source: str = "",
    stock_failure_reason: str = "",
    global_failure: bool = False,
) -> dict[str, object]:
    normalized_scope = (
        START_REQUEST_SINGLE
        if request_scope == START_REQUEST_SINGLE
        else START_REQUEST_MULTIPLE
    )
    result["request_scope"] = normalized_scope
    result["source"] = str(request_source or "").strip()
    result.setdefault("requested_count", len(selected))
    result["global_failure"] = bool(global_failure)

    target = (
        selected[0]
        if len(selected) == 1
        and (
            normalized_scope == START_REQUEST_SINGLE
            or result.get("ok") is not True
        )
        else None
    )
    code, name, _label = _start_target_identity(target)
    result["target_stock_code"] = code
    result["target_stock_name"] = name

    if target is None or global_failure:
        return result
    if result.get("ok") is True:
        result["user_message"] = f"{name or code} 운영을 시작했습니다."
        return result

    reason = str(stock_failure_reason or result.get("reason") or "").strip()
    result["user_message"] = _single_start_failure_user_message(target, reason)
    result["stock_failure"] = reason
    return result


def _start_result_summary(
    *,
    started_count: int,
    excluded_review_count: int,
    excluded_validation_count: int,
    failed_count: int,
) -> str:
    parts = [f"운영 시작 {started_count}개"]
    if excluded_review_count:
        parts.append(f"검토 제외 {excluded_review_count}개")
    if excluded_validation_count:
        parts.append(f"설정 제외 {excluded_validation_count}개")
    if failed_count:
        parts.append(f"실패 {failed_count}개")
    return " · ".join(parts)


def _operation_start_p3_reason(result: dict[str, object]) -> str:
    candidates: list[str] = []
    for key in ("global_failure_reason", "stock_failure"):
        value = str(result.get(key) or "").strip()
        if value:
            candidates.append(value)
    internal = result.get("internal_reason")
    if isinstance(internal, (list, tuple, set)):
        candidates.extend(str(item or "").strip() for item in internal)
    candidates.append(str(result.get("reason") or "").strip())
    supported = _P3_OPERATION_START_RUNTIME_REASONS | _P3_OPERATION_START_ERROR_REASONS
    return next((reason for reason in candidates if reason in supported), "")


def _record_operation_start_p3_result(
    window,
    result: dict[str, object],
) -> None:
    reason = _operation_start_p3_reason(result)
    if not reason:
        return
    event_type = (
        "RUNTIME_WARNING"
        if reason in _P3_OPERATION_START_RUNTIME_REASONS
        else "PROCESSING_ERROR"
    )
    stock_code = str(result.get("target_stock_code") or "").strip()
    stock_name = str(result.get("target_stock_name") or "").strip()
    target_name = " ".join(part for part in (stock_code, stock_name) if part)
    observe_owner_failure_transition(
        window,
        "operation_start_failure",
        active=True,
        signature=f"{event_type}:{reason}:{stock_code}",
        event_type=event_type,
        severity="WARNING" if event_type == "RUNTIME_WARNING" else "ERROR",
        result="BLOCKED" if event_type == "RUNTIME_WARNING" else "FAILED",
        source="gui_auto_trade_run_control.auto_trade_start_selected_auto_trades",
        template_args={"target": target_name or "자동매매 운영 시작"},
        target_type="STOCK" if stock_code else "OPERATION_START",
        target_id=stock_code or "operation_start",
        target_name=target_name or "자동매매 운영 시작",
        stock_code=stock_code,
        stock_name=stock_name,
        reason_code=reason,
        details={
            "stage": "operation_start",
            "requested_count": int(result.get("requested_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
        },
    )


def _show_start_failure_once(window, result: dict[str, object]) -> None:
    _record_operation_start_p3_result(window, result)
    message = str(result.get("user_message") or "").strip()
    status_message = getattr(window, "statusBarMessage", None)
    if message and callable(status_message):
        status_message(message)
    show_auto_trade_operation_failure_dialog(
        window,
        "운영시작",
        result,
    )


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _positive_int_amount(value: object) -> int | None:
    try:
        amount = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _operation_start_resolved_starting_budget(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    config: dict[str, object],
) -> int | None:
    stock = {
        "code": str(code or "").strip(),
        "name": str(name or "").strip(),
        "stock_path": str(Path(stock_dir)),
    }
    try:
        amount = main_stock_resolved_starting_budget(window, stock, config)
    except Exception:
        LOGGER.exception("시작금액 resolved budget 확인 실패: %s %s", code, name)
        return None
    return _positive_int_amount(amount)


def initial_buy_start_validation(
    config: dict[str, object],
    state: dict[str, object],
    *,
    resolved_starting_budget: object = None,
) -> dict[str, object]:
    mode = str(config.get("trade_amount_type", "QUANTITY") or "").strip().upper()
    if mode in {"QUANTITY", "QTY", "SHARES", "SHARE", "주수"}:
        try:
            quantity = int(config.get("buy_qty", 1) or 0)
        except (TypeError, ValueError):
            quantity = 0
        return {
            "allowed": quantity >= 1,
            "reason": "" if quantity >= 1 else "INVALID_INITIAL_BUY_QUANTITY",
            "mode": "QUANTITY",
            "configured_value": quantity,
        }

    try:
        configured_amount = max(0, int(config.get("buy_amount", 0) or 0))
    except (TypeError, ValueError):
        configured_amount = 0
    if configured_amount > 0:
        return {
            "allowed": True,
            "reason": "",
            "mode": "AMOUNT",
            "configured_value": configured_amount,
            "resolved_starting_budget": configured_amount,
            "starting_budget_source": "explicit",
        }

    resolved_amount = _positive_int_amount(resolved_starting_budget)
    if resolved_amount is None:
        return {
            "allowed": False,
            "reason": "STARTING_BUDGET_UNRESOLVED",
            "mode": "AMOUNT",
            "configured_value": configured_amount,
            "resolved_starting_budget": None,
            "starting_budget_source": "fresh_current_price",
        }

    return {
        "allowed": True,
        "reason": "",
        "mode": "AMOUNT",
        "configured_value": configured_amount,
        "resolved_starting_budget": resolved_amount,
        "starting_budget_source": "fresh_current_price",
    }


def append_changelog(change_type: str, filename: str, message: str) -> None:
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


def _refresh_signal_probe_only_window(window) -> None:
    refresh_all = getattr(window, "refresh_all", None)
    if callable(refresh_all):
        refresh_all()

    stock_table = getattr(window, "stock_table", None)
    viewport = getattr(stock_table, "viewport", None)
    if callable(viewport):
        try:
            viewport().update()
        except Exception:
            pass
    repaint = getattr(stock_table, "repaint", None)
    if callable(repaint):
        try:
            repaint()
        except Exception:
            pass


def start_signal_probe_only_for_selected_stocks(window) -> dict[str, object]:
    """Enable selected stocks for routine signal evaluation without real orders."""
    selected = window.selected_stock_infos()
    if not selected:
        status_bar = getattr(window, "statusBarMessage", None)
        if callable(status_bar):
            status_bar("신호평가 전용 전환 대상 없음")
        return {"started": [], "failed": [], "count": 0}

    started: list[str] = []
    failed: list[str] = []
    started_at = now_text()

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        if not isinstance(state, dict):
            state = {}

        state.update(
            {
                "status": "MONITORING",
                "trade_enabled": True,
                "real_trade_enabled": False,
                "review_required": False,
                "review_status": "",
                "review_reason": "",
                "signal_probe_only": True,
                "signal_probe_started_at": started_at,
                "signal_probe_stopped_at": "",
                "updated_at": started_at,
            }
        )

        if mutate_runtime_stock_state(
            stock_dir,
            "MONITORING",
            state,
            updated_at=started_at,
        ).ok:
            started.append(f"{code} {name}")
        else:
            failed.append(f"{code} {name}")

    _refresh_signal_probe_only_window(window)
    status_bar = getattr(window, "statusBarMessage", None)
    if callable(status_bar):
        message = f"신호평가 전용 시작: {len(started)}개"
        if failed:
            message += f" / 실패 {len(failed)}개"
        status_bar(message)

    return {"started": started, "failed": failed, "count": len(started)}


def stop_signal_probe_only_for_selected_stocks(window) -> dict[str, object]:
    """Return selected signal-probe-only stocks to STOPPED without enabling orders."""
    selected = window.selected_stock_infos()
    if not selected:
        status_bar = getattr(window, "statusBarMessage", None)
        if callable(status_bar):
            status_bar("신호평가 전용 중지 대상 없음")
        return {"stopped": [], "failed": [], "count": 0}

    stopped: list[str] = []
    failed: list[str] = []
    stopped_at = now_text()

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        if not isinstance(state, dict):
            state = {}

        state.update(
            {
                "status": "STOPPED",
                "trade_enabled": False,
                "real_trade_enabled": False,
                "signal_probe_only": False,
                "signal_probe_stopped_at": stopped_at,
                "updated_at": stopped_at,
            }
        )

        if mutate_runtime_stock_state(
            stock_dir,
            "STOPPED",
            state,
            updated_at=stopped_at,
        ).ok:
            stopped.append(f"{code} {name}")
        else:
            failed.append(f"{code} {name}")

    _refresh_signal_probe_only_window(window)
    status_bar = getattr(window, "statusBarMessage", None)
    if callable(status_bar):
        message = f"신호평가 전용 중지: {len(stopped)}개"
        if failed:
            message += f" / 실패 {len(failed)}개"
        status_bar(message)

    return {"stopped": stopped, "failed": failed, "count": len(stopped)}


def auto_trade_start_selected_auto_trades(
    window,
    *,
    request_scope: str = START_REQUEST_MULTIPLE,
    selected_targets: list[tuple[Path, str, str]] | None = None,
    source: str = "",
    already_running_targets: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...] = (),
) -> dict[str, object]:
    setattr(window, "_last_operation_failure_dialog_shown", False)
    request_scope = (
        START_REQUEST_SINGLE
        if request_scope == START_REQUEST_SINGLE
        else START_REQUEST_MULTIPLE
    )
    try:
        selected = (
            list(selected_targets)
            if selected_targets is not None
            else window.selected_stock_infos()
        )
    except Exception:
        LOGGER.exception("운영 시작 대상 수집 실패")
        result = {
            "ok": False,
            "reason": "TARGET_COLLECTION_FAILED",
            "user_message": (
                "운영 시작 대상을 확인하는 중 오류가 발생했습니다.\n"
                "화면을 새로고침한 뒤 다시 시도하십시오."
            ),
            "requested": (),
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=[],
            request_source=source,
            global_failure=True,
        )
        _show_start_failure_once(window, result)
        return result

    unique_selected: list[tuple[Path, str, str]] = []
    seen_stock_keys: set[str] = set()
    for stock_dir, code, name in selected:
        key = str(code or "").strip() or str(Path(stock_dir).resolve())
        if key in seen_stock_keys:
            continue
        seen_stock_keys.add(key)
        unique_selected.append((Path(stock_dir), str(code), str(name)))
    selected = unique_selected

    unique_running: list[tuple[Path, str, str]] = []
    running_keys: set[str] = set()
    for stock_dir, code, name in already_running_targets or ():
        key = str(code or "").strip() or str(Path(stock_dir).resolve())
        if key in running_keys:
            continue
        running_keys.add(key)
        unique_running.append((Path(stock_dir), str(code), str(name)))
    already_running_targets = tuple(unique_running)
    reporting_selected = list(already_running_targets) + list(selected)
    start_request_context_targets = (
        reporting_selected if already_running_targets else selected
    )

    if request_scope == START_REQUEST_SINGLE and len(selected) != 1:
        request_scope = START_REQUEST_MULTIPLE

    operation_state = read_operation_state()
    request_now = current_datetime()
    start_source = str(source or "").strip()
    per_stock_restart_source = start_source in {
        "auto_trade_context_menu",
        "main_monitoring_window",
        "auto_trade_status_indicator",
    }
    if not per_stock_restart_source and _today_normal_ended(operation_state, request_now):
        requested = tuple(f"{code} {name}" for _stock_dir, code, name in reporting_selected)
        result = {
            "ok": False,
            "reason": "NORMAL_ENDED",
            "user_message": (
                "오늘의 정상 운영이 이미 종료되었습니다.\n"
                "다음 거래일에 운영을 시작하십시오."
            ),
            "requested": requested,
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "review_required": (),
            "failed": (),
            "skipped": (),
            "operation": "START",
            "requested_count": len(requested),
            "started_count": 0,
            "excluded_review_count": 0,
            "excluded_validation_count": 0,
            "failed_count": 0,
            "global_failure_reason": "NORMAL_ENDED",
            "internal_reason": ("NORMAL_ENDED",),
            "blocked": True,
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=start_request_context_targets,
            request_source=source,
            global_failure=True,
        )
        _show_start_failure_once(window, result)
        return result

    if is_emergency_stop(operation_state):
        requested = tuple(f"{code} {name}" for _stock_dir, code, name in reporting_selected)
        result = {
            "ok": False,
            "reason": "GLOBAL_EMERGENCY_STOP",
            "user_message": (
                "전역 긴급정지 상태입니다. 정지해제 후 운영시작을 다시 시도하십시오."
            ),
            "requested": requested,
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "review_required": (),
            "failed": (),
            "skipped": (),
            "operation": "START",
            "requested_count": len(requested),
            "started_count": 0,
            "excluded_review_count": 0,
            "excluded_validation_count": 0,
            "failed_count": 0,
            "global_failure_reason": "GLOBAL_EMERGENCY_STOP",
            "internal_reason": ("GLOBAL_EMERGENCY_STOP",),
            "blocked": True,
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=selected,
            request_source=source,
            global_failure=True,
        )
        _show_start_failure_once(window, result)
        return result

    global_prerequisite = _global_start_prerequisite_result(
        window,
        action="운영시작",
    )
    if global_prerequisite is not None:
        requested = tuple(f"{code} {name}" for _stock_dir, code, name in reporting_selected)
        reason = str(global_prerequisite.get("reason") or "GLOBAL_PREREQUISITE_NOT_READY")
        result = {
            "ok": False,
            "reason": reason,
            "user_message": str(global_prerequisite.get("user_message") or "").strip(),
            "requested": requested,
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "review_required": (),
            "failed": (),
            "skipped": (),
            "operation": "START",
            "requested_count": len(requested),
            "started_count": 0,
            "excluded_review_count": 0,
            "excluded_validation_count": 0,
            "failed_count": 0,
            "global_failure_reason": reason,
            "internal_reason": (reason,),
            "blocked": True,
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=selected,
            request_source=source,
            global_failure=True,
        )
        _show_start_failure_once(window, result)
        return result

    if not selected:
        result = {
            "ok": False,
            "reason": "NO_TARGETS",
            "user_message": "운영을 시작할 종목을 1개 이상 선택하십시오.",
            "requested": (),
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=[],
            request_source=source,
        )
        _show_start_failure_once(window, result)
        return result

    requested = tuple(f"{code} {name}" for _stock_dir, code, name in reporting_selected)
    review_checker = getattr(window, "start_target_is_review_isolated", None)
    candidate_targets: list[tuple[Path, str, str]] = []
    excluded_review: list[str] = []
    blocked_target_details: list[dict[str, object]] = []
    for stock_dir, code, name in selected:
        isolated = (
            bool(review_checker(stock_dir, code))
            if callable(review_checker)
            else is_review_required_stock_dir(stock_dir)
        )
        if isolated:
            excluded_review.append(f"{code} {name}")
            blocked_target_details.append(
                {
                    "stock_code": str(code),
                    "stock_name": str(name),
                    "reason": "REVIEW_REQUIRED",
                    "display_label": f"{code} {name}".strip(),
                }
            )
        else:
            candidate_targets.append((stock_dir, code, name))

    try:
        start_targets, skipped = window.split_start_targets(candidate_targets)
        blocked_target_details.extend(_start_target_block_details(window))
        blocked_target_details = tuple(blocked_target_details)
    except Exception:
        LOGGER.exception("운영 시작 대상 분류 실패")
        result = {
            "ok": False,
            "reason": "TARGET_CLASSIFICATION_FAILED",
            "user_message": (
                "운영 시작 대상을 확인하는 중 오류가 발생했습니다.\n"
                "화면을 새로고침한 뒤 다시 시도하십시오."
            ),
            "requested": requested,
            "requested_count": len(requested),
            "excluded_review": tuple(excluded_review),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=selected,
            request_source=source,
            stock_failure_reason="TARGET_CLASSIFICATION_FAILED",
        )
        _show_start_failure_once(window, result)
        return result
    if not start_targets:
        all_review = bool(excluded_review) and not skipped
        all_already_running = bool(skipped) and all(
            auto_trade_setting_current_session_trade_started(
                window,
                auto_trade_setting_trade_started(
                    read_json_dict(stock_dir / "state.json")
                ),
                code,
            )
            for stock_dir, code, _name in candidate_targets
        )
        result = {
            "ok": False,
            "reason": "NO_STARTABLE_TARGETS",
            "user_message": _start_failure_user_message(
                [],
                all_emergency=_all_start_targets_emergency_stopped(selected),
                all_review=all_review,
                all_already_running=all_already_running,
                blocked_target_details=blocked_target_details,
                already_running_targets=already_running_targets,
            ),
            "requested": requested,
            "requested_count": len(requested),
            "excluded_review": tuple(excluded_review),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
            "skipped": tuple(skipped),
            "blocked_target_details": blocked_target_details,
            "already_running_targets": already_running_targets,
            "blocked_count": len(blocked_target_details),
            "already_running_count": len(already_running_targets),
            "eligible_count": 0,
            "started_count": 0,
            "excluded_review_count": len(excluded_review),
            "excluded_validation_count": 0,
            "failed_count": 0,
            "global_failure_reason": "",
            "internal_reason": tuple(
                dict.fromkeys(
                    str(detail.get("reason") or "NOT_STARTABLE")
                    for detail in blocked_target_details
                )
            ),
            "time_eligible_targets": (),
            "time_blocked_targets": tuple(
                str(detail.get("display_label") or "").strip()
                for detail in blocked_target_details
                if str(detail.get("display_label") or "").strip()
            ),
            "operation": "START",
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=start_request_context_targets,
            request_source=source,
            stock_failure_reason=(
                "REVIEW_REQUIRED"
                if all_review
                else (
                    "ALREADY_RUNNING"
                    if all_already_running
                    else str(
                        blocked_target_details[0].get("reason")
                        if len(blocked_target_details) == 1
                        else "NOT_STARTABLE"
                    )
                )
            ),
        )
        try:
            refresh_auto_trade_views(window)
            window.stock_table.viewport().update()
            window.stock_table.repaint()
        except Exception:
            LOGGER.exception("운영 시작 차단 후 화면 새로고침 실패")
        _refresh_start_button_state(window)
        if request_scope != START_REQUEST_SINGLE and blocked_target_details:
            _show_operation_start_summary_toast(window, result)
        else:
            _show_start_failure_once(window, result)
        return result

    # Operation Start establishes participation and may run before or between
    # actual trading sessions. Order permission owns the fail-closed time gate.
    time_eligible_targets: list[tuple[Path, str, str]] = list(start_targets)
    time_blocked_targets: list[str] = []

    recovery_filter = getattr(window, "filter_start_targets_by_recovery", None)
    if callable(recovery_filter):
        try:
            recovery_result = recovery_filter(start_targets, action="운영시작")
        except Exception:
            LOGGER.exception("운영 시작 Recovery 판정 실패")
            result = {
                "ok": False,
                "reason": "RECOVERY_CHECK_FAILED",
                "user_message": (
                    "복구 상태를 확인하는 중 오류가 발생했습니다.\n"
                    "로그를 확인한 뒤 Recovery를 다시 실행하십시오."
                ),
                "requested": requested,
                "excluded_review": tuple(excluded_review),
                "eligible": (),
                "completed": (),
                "blocked_validation": (),
                "failed": (),
                "skipped": tuple(skipped),
            }
            _apply_start_request_context(
                result,
                request_scope=request_scope,
                selected=selected,
                request_source=source,
                global_failure=True,
            )
            if request_scope != START_REQUEST_SINGLE and blocked_target_details:
                _show_operation_start_summary_toast(window, result)
            else:
                _show_start_failure_once(window, result)
            return result
        excluded_review.extend(
            str(item)
            for item in recovery_result.get("excluded_review", ())
            if str(item) and str(item) not in excluded_review
        )
        recovery_block_details = tuple(
            dict(item)
            for item in recovery_result.get("blocked_target_details", ())
            if isinstance(item, dict)
        )
        if recovery_block_details:
            blocked_target_details = tuple(blocked_target_details) + recovery_block_details
        if recovery_result.get("allowed") is not True:
            grouped_recovery_message = _blocked_target_groups_message(
                blocked_target_details,
                already_running_targets=already_running_targets,
            )
            result = {
                "ok": False,
                "reason": str(
                    recovery_result.get("reason")
                    or getattr(window, "_last_operation_block_reason", "")
                    or "RECOVERY_NOT_READY"
                ),
                "user_message": str(
                    grouped_recovery_message
                    or recovery_result.get("user_message")
                    or ""
                ).strip(),
                "requested": requested,
                "excluded_review": tuple(excluded_review),
                "eligible": (),
                "completed": (),
                "blocked_validation": (),
                "failed": (),
                "skipped": tuple(skipped),
                "blocked_target_details": tuple(blocked_target_details),
                "already_running_targets": already_running_targets,
                "blocked_count": len(blocked_target_details),
                "already_running_count": len(already_running_targets),
            }
            recovery_reason = str(result["reason"])
            _apply_start_request_context(
                result,
                request_scope=request_scope,
                selected=selected,
                request_source=source,
                stock_failure_reason=recovery_reason,
                global_failure=not recovery_reason.startswith("RECOVERY_STOCK_"),
            )
            if request_scope != START_REQUEST_SINGLE and blocked_target_details:
                _show_operation_start_summary_toast(window, result)
            else:
                _show_start_failure_once(window, result)
            return result
        start_targets = list(recovery_result.get("eligible", ()))
    else:
        recovery_check = getattr(
            type(window),
            "require_startup_recovery_session",
            None,
        )
        if callable(recovery_check) and recovery_check(window, "운영시작") is not True:
            result = {
                "ok": False,
                "reason": str(
                    getattr(window, "_last_operation_block_reason", "")
                    or "RECOVERY_NOT_READY"
                ),
                "requested": requested,
                "excluded_review": tuple(excluded_review),
                "eligible": (),
                "completed": (),
                "blocked_validation": (),
                "failed": (),
                "skipped": tuple(skipped),
            }
            result["user_message"] = str(
                getattr(window, "_last_operation_user_message", "") or ""
            ).strip() or startup_recovery_operation_block_message("운영시작")
            _apply_start_request_context(
                result,
                request_scope=request_scope,
                selected=selected,
                request_source=source,
                global_failure=True,
            )
            _show_start_failure_once(window, result)
            return result

    if not start_targets:
        result = {
            "ok": False,
            "reason": "NO_STARTABLE_TARGETS",
            "user_message": _start_failure_user_message(
                [],
                all_emergency=_all_start_targets_emergency_stopped(selected),
                all_review=bool(excluded_review),
                time_eligible_targets=bool(time_eligible_targets),
                blocked_target_details=blocked_target_details,
                already_running_targets=already_running_targets,
            ),
            "requested": requested,
            "requested_count": len(requested),
            "excluded_review": tuple(excluded_review),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
            "skipped": tuple(skipped),
            "blocked_target_details": blocked_target_details,
            "already_running_targets": already_running_targets,
            "blocked_count": len(blocked_target_details),
            "already_running_count": len(already_running_targets),
            "time_eligible_targets": tuple(time_eligible_targets),
            "time_blocked_targets": tuple(time_blocked_targets),
        }
        _apply_start_request_context(
            result,
            request_scope=request_scope,
            selected=start_request_context_targets,
            request_source=source,
            stock_failure_reason="REVIEW_REQUIRED",
        )
        if request_scope != START_REQUEST_SINGLE and blocked_target_details:
            _show_operation_start_summary_toast(window, result)
        else:
            _show_start_failure_once(window, result)
        return result

    eligible = tuple(f"{code} {name}" for _stock_dir, code, name in start_targets)
    completed: list[str] = []
    completed_codes: list[str] = []
    review_required: list[str] = []
    failed: list[str] = []
    validation_blocked: list[str] = []
    failure_reasons: list[str] = []
    routine_names: list[str] = []

    previous_batch_flag = bool(
        getattr(window, "_operation_start_batch_active", False)
    )
    setattr(window, "_operation_start_batch_active", True)
    for stock_dir, code, name in start_targets:
        config_path = stock_dir / "config.json"
        config = read_json_dict(config_path)
        if not config_path.exists() or not config:
            failed.append(f"{code} {name}")
            validation_blocked.append(f"{code} {name}")
            failure_reasons.append("MISSING_REQUIRED_SETTINGS")
            continue
        instance_id = str(
            config.get("assigned_routine_instance_id", "") or ""
        ).strip()
        routine_name = str(
            config.get("routine_instance_name")
            or config.get("routine")
            or config.get("routine_name")
            or ""
        ).strip()
        if not instance_id or not routine_name:
            failed.append(f"{code} {name}")
            validation_blocked.append(f"{code} {name}")
            failure_reasons.append("MISSING_REQUIRED_SETTINGS")
            continue
        if routine_name not in routine_names:
            routine_names.append(routine_name)

        state_path = stock_dir / "state.json"
        state = read_json_dict(state_path)
        if not state_path.exists():
            failed.append(f"{code} {name}")
            failure_reasons.append("RUNTIME_MISSING")
            continue
        if not state:
            failed.append(f"{code} {name}")
            failure_reasons.append("RUNTIME_DAMAGED")
            continue
        try:
            resolved_starting_budget = _operation_start_resolved_starting_budget(
                window,
                stock_dir,
                code,
                name,
                config,
            )
            validation = initial_buy_start_validation(
                config,
                state,
                resolved_starting_budget=resolved_starting_budget,
            )
        except Exception:
            LOGGER.exception("초회 매수 기준 검증 실패: %s %s", code, name)
            failed.append(f"{code} {name}")
            failure_reasons.append("INTERNAL_EXCEPTION")
            continue
        if validation.get("allowed") is not True:
            failed.append(f"{code} {name}")
            validation_blocked.append(f"{code} {name}")
            failure_reasons.append(
                str(validation.get("reason") or "MISSING_REQUIRED_SETTINGS")
            )
            continue

        try:
            review_item = window.pre_start_review_check(
                routine_name,
                stock_dir,
                code,
                name,
            )
        except Exception:
            LOGGER.exception("운영 시작 전 Runtime 검토 실패: %s %s", code, name)
            failed.append(f"{code} {name}")
            failure_reasons.append("INTERNAL_EXCEPTION")
            continue

        if review_required_for_start(review_item):
            if window.mark_review_required(stock_dir, code, name, review_item, source="운영시작"):
                review_required.append(f"{code} {name}")
                failure_reasons.append("REVIEW_REQUIRED")
            else:
                failed.append(f"{code} {name}")
                failure_reasons.append("REVIEW_STATE_SAVE_FAILED")
            continue

        guard = auto_trade_same_day_restart_guard(
            stock_dir=stock_dir,
            stock_code=code,
            config=config,
            state=state,
            operation_state=operation_state,
            now_dt=request_now,
            order_queue_path=ORDER_QUEUE_PATH,
        )
        if guard.get("allowed") is not True:
            failed.append(f"{code} {name}")
            failure_reasons.append(str(guard.get("reason") or "START_GUARD_BLOCKED"))
            continue

        operation_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        start_status = (
            "RUNNING"
            if real_trade_enabled(config)
            else status_after_operation_mode_change(operation_mode, config)
        )
        mode_display = operation_mode_display(operation_mode)
        trade_permission_text, _, _ = trade_permission_display(config)
        started_at = now_text()

        metadata = {
            "review_required": False,
            "review_status": "",
            "review_reason": "",
            "resumed_at": started_at,
            "ignore_signals_before": started_at,
            # operation_mode는 config.json만 원본으로 사용한다.
            # state.json에는 저장하지 않는다.
            "real_trade_enabled": real_trade_enabled(config),
            "trade_enabled": True,
            "trade_started_at": started_at,
            "startup_reset_reason": "",
            "startup_reset_cleared_at": started_at,
            "operation_notice": "",
            "operation_notice_reason": "",
            "operation_notice_at": "",
            "start_policy_status": start_status,
            "start_policy_checked_at": started_at,
        }
        cleared_close_state = clear_early_close_runtime_metadata_only(dict(state))
        for key in (
            "early_close_requested_at",
            "early_close_source",
            "early_close_method",
            "early_close_policy",
            "liquidation_policy_forced",
            "liquidation_policy_reason",
            "close_routine_final_sell_ordered",
            "close_routine_final_sell_ordered_at",
            "close_routine_final_sell_source",
            "close_routine_final_sell_reason",
        ):
            metadata[key] = cleared_close_state.get(key)
        try:
            result, _, applied_status = (
                window.recalculate_stock_status_by_operation_policy(
                    stock_dir,
                    code,
                    name,
                    "운영시작",
                    metadata,
                )
            )
        except Exception:
            LOGGER.exception("운영 상태 저장 실패: %s %s", code, name)
            failed.append(f"{code} {name}")
            failure_reasons.append("INTERNAL_EXCEPTION")
            continue
        if result in ("changed", "unchanged"):
            completed.append(f"{code} {name}({mode_display}/{trade_permission_text}/{auto_trade_status_display(applied_status)})")
            completed_codes.append(str(code))
            if result == "changed":
                append_production_event(
                    "OPERATION_STARTED",
                    result="SUCCESS",
                    source=str(source or "auto_trade_start_selected_auto_trades"),
                    template_args={"target": f"{code} {name}".strip()},
                    target_type="STOCK",
                    target_id=str(code),
                    target_name=str(name),
                    stock_code=str(code),
                    stock_name=str(name),
                )
        else:
            failed.append(f"{code} {name}")
            failure_reasons.append(
                "STATE_SAVE_FAILED" if result == "failed" else "REVIEW_REQUIRED"
            )
    setattr(window, "_operation_start_batch_active", previous_batch_flag)

    if completed or review_required or failed:
        changelog_parts: list[str] = []
        if completed:
            changelog_parts.append(f"시작: {' / '.join(completed)}")
        if review_required:
            changelog_parts.append(f"검토종목: {' / '.join(review_required)}")
        if failed:
            changelog_parts.append(f"실패: {' / '.join(failed)}")
        if skipped:
            changelog_parts.append(f"제외: {' / '.join(skipped)}")

        try:
            append_changelog(
                "UPDATE",
                "state.json",
                f"운영시작 전 안전검사 및 operation_mode 반영: "
                f"{' / '.join(routine_names) or '종목별 소속 확인 실패'} -> {' | '.join(changelog_parts)}",
            )
        except Exception:
            LOGGER.exception("운영 시작 결과 로그 기록 실패")

    if completed or review_required:
        rebind_recovery = getattr(
            window,
            "rebind_startup_recovery_after_trusted_runtime_update",
            None,
        )
        if callable(rebind_recovery):
            rebind_recovery()

    grouped_result_message = _blocked_target_groups_message(
        blocked_target_details,
        already_running_targets=already_running_targets,
    )

    excluded_review_count = len(excluded_review) + len(review_required)
    excluded_validation_count = len(validation_blocked)
    non_validation_failed_count = max(0, len(failed) - excluded_validation_count)
    result = {
        "ok": bool(completed),
        "reason": (
            "STARTED"
            if completed
            else ("REVIEW_REQUIRED" if review_required else "START_FAILED")
        ),
        "requested": requested,
        "excluded_review": tuple(excluded_review),
        "eligible": eligible,
        "completed": tuple(completed),
        "blocked_validation": tuple(validation_blocked),
        "review_required": tuple(review_required),
        "failed": tuple(failed),
        "skipped": tuple(skipped),
        "blocked_target_details": blocked_target_details,
        "already_running_targets": already_running_targets,
        "blocked_count": len(blocked_target_details),
        "already_running_count": len(already_running_targets),
        "time_eligible_targets": tuple(time_eligible_targets),
        "time_blocked_targets": tuple(time_blocked_targets),
        "operation": "START",
        "requested_count": len(requested),
        "eligible_count": len(eligible),
        "started_count": len(completed),
        "excluded_review_count": excluded_review_count,
        "excluded_validation_count": excluded_validation_count,
        "failed_count": non_validation_failed_count,
        "global_failure_reason": "",
        "internal_reason": tuple(dict.fromkeys(failure_reasons)),
    }
    if completed:
        auto_trade_register_current_session_operation_participants(
            window,
            completed_codes,
        )
        try:
            operation_state_write = write_global_operation_running_state(
                participant_stock_codes=completed_codes,
            )
        except Exception as exc:
            LOGGER.exception("Global operation RUNNING state write failed")
            operation_state_write = {
                "ok": False,
                "error": str(exc),
            }
        result["operation_state_write"] = operation_state_write
        result["operation_state_write_failed"] = (
            operation_state_write.get("ok") is not True
        )
        routine_limit_recalculation = (
            _apply_new_session_routine_limit_recalculation(
                window,
                operation_state_write,
            )
        )
        if routine_limit_recalculation is not None:
            result["routine_limit_recalculation"] = routine_limit_recalculation
        result["operation_host_start_result"] = (
            _start_operation_host_after_explicit_operation_start(window)
        )
    try:
        refresh_auto_trade_views(window)
        window.stock_table.viewport().update()
        window.stock_table.repaint()
    except Exception:
        LOGGER.exception("운영 시작 후 화면 새로고침 실패")
    _refresh_start_button_state(window)
    _apply_start_request_context(
        result,
        request_scope=request_scope,
        selected=start_request_context_targets,
        request_source=source,
        stock_failure_reason=(failure_reasons[0] if failure_reasons else ""),
    )
    if completed:
        observe_owner_failure_transition(
            window,
            "operation_start_failure",
            active=False,
        )
        operation_state_failed = result.get("operation_state_write_failed") is True
        observe_owner_failure_transition(
            window,
            "operation_start_global_state_write",
            active=operation_state_failed,
            signature="GLOBAL_OPERATION_STATE_WRITE_FAILED",
            event_type="PROCESSING_ERROR",
            severity="ERROR",
            result="FAILED",
            source="gui_auto_trade_run_control.auto_trade_start_selected_auto_trades",
            template_args={"target": "전역 운영 상태"},
            target_type="GLOBAL_OPERATION",
            target_id="global_operation",
            target_name="전역 운영 상태",
            reason_code="GLOBAL_OPERATION_STATE_WRITE_FAILED",
            details={"stage": "operation_start_global_state_write"},
        )
        if request_scope != START_REQUEST_SINGLE:
            result["user_message"] = _start_result_summary(
                started_count=len(completed),
                excluded_review_count=excluded_review_count,
                excluded_validation_count=excluded_validation_count,
                failed_count=non_validation_failed_count,
            )
            if grouped_result_message:
                result["user_message"] = (
                    f"{result['user_message']}\n\n{grouped_result_message}"
                )
        result["user_action"] = ""
        if request_scope == START_REQUEST_SINGLE:
            window.statusBarMessage(str(result["user_message"]))
            if result.get("operation_state_write_failed"):
                window.statusBarMessage(
                    "전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오."
                )
        else:
            _show_operation_start_summary_toast(window, result)
            if result.get("operation_state_write_failed"):
                window.statusBarMessage(
                    "전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오."
                )
    else:
        if request_scope != START_REQUEST_SINGLE:
            result["user_message"] = _start_failure_user_message(
                failure_reasons,
                all_emergency=_all_start_targets_emergency_stopped(selected),
                all_review=bool(review_required) and not failed,
                time_eligible_targets=bool(time_eligible_targets),
                blocked_target_details=blocked_target_details,
                already_running_targets=already_running_targets,
            )
        result["user_action"] = str(result["user_message"]).splitlines()[-1]
        result["global_failure_reason"] = str(result["reason"])
        _show_start_failure_once(window, result)
    if review_required:
        window.open_review_required_window()
    return result


def auto_trade_start_selected_rows_auto_trades(window) -> dict[str, object] | None:
    selected_targets = window.selected_stock_infos()
    if not selected_targets:
        return None

    running_targets = window.running_registered_operation_targets()
    global_running = today_global_operation_status(
        read_operation_state()
    ) in {"RUNNING", "CLOSING"}
    running_keys = {
        str(code or "").strip() or str(Path(stock_dir).resolve())
        for stock_dir, code, _name in running_targets
    }

    if not running_targets and not global_running:
        start_targets: list[tuple[Path, str, str]] = []
        selected_keys: set[str] = set()
        for target in selected_targets:
            stock_dir, code, _name = target
            key = str(code or "").strip() or str(Path(stock_dir).resolve())
            selected_keys.add(key)
            if auto_trade_stock_operation_excluded(stock_dir):
                if not set_auto_trade_stock_operation_excluded(stock_dir, False):
                    continue
                if auto_trade_stock_operation_excluded(stock_dir):
                    continue
            start_targets.append(target)

        if not start_targets:
            refresh_auto_trade_views(window)
            return None

        result = auto_trade_start_selected_auto_trades(
            window,
            request_scope="multiple",
            selected_targets=start_targets,
            source="auto_trade_context_menu",
        )
        if result.get("ok") is True:
            for stock_dir, code, _name in window.registered_operation_targets():
                key = str(code or "").strip() or str(Path(stock_dir).resolve())
                if key in selected_keys or is_review_required_stock_dir(stock_dir):
                    continue
                config = read_json_dict(stock_dir / "config.json")
                if not str(config.get("assigned_routine_instance_id", "") or "").strip():
                    continue
                set_auto_trade_stock_operation_excluded(stock_dir, True)
            refresh_auto_trade_views(window)
        _refresh_start_button_state(window)
        return result

    selected_running: list[tuple[Path, str, str]] = []
    selected_inactive: list[tuple[Path, str, str]] = []
    for target in selected_targets:
        stock_dir, code, _name = target
        key = str(code or "").strip() or str(Path(stock_dir).resolve())
        if key in running_keys:
            selected_running.append(target)
        else:
            selected_inactive.append(target)

    if selected_running and not selected_inactive:
        result = {
            "ok": False,
            "reason": "ALREADY_RUNNING",
            "user_message": "선택한 종목이 모두 이미 운영 중입니다.",
            "requested": tuple(
                f"{code} {name}".strip()
                for _stock_dir, code, name in selected_targets
            ),
            "requested_count": len(selected_targets),
            "completed": (),
            "blocked_target_details": (),
            "already_running_targets": tuple(selected_running),
            "blocked_count": 0,
            "already_running_count": len(selected_running),
            "started_count": 0,
            "failed_count": 0,
            "excluded_review_count": 0,
            "excluded_validation_count": 0,
        }
        _show_operation_start_summary_toast(window, result)
        _refresh_start_button_state(window)
        return result

    start_targets: list[tuple[Path, str, str]] = []
    for target in selected_inactive:
        stock_dir, _code, _name = target
        if auto_trade_stock_operation_excluded(stock_dir):
            if not set_auto_trade_stock_operation_excluded(stock_dir, False):
                continue
            if auto_trade_stock_operation_excluded(stock_dir):
                continue
        start_targets.append(target)

    if not start_targets:
        refresh_auto_trade_views(window)
        return None

    result = auto_trade_start_selected_auto_trades(
        window,
        request_scope="multiple",
        selected_targets=start_targets,
        source="auto_trade_context_menu",
        already_running_targets=selected_running,
    )
    _refresh_start_button_state(window)
    return result


def _refresh_start_button_state(window) -> None:
    updater = getattr(window, "update_global_operation_button_state", None)
    if not callable(updater):
        return
    owner = getattr(window, "_window", window)
    try:
        getattr(owner, "btn_start")
    except Exception:
        return
    try:
        updater()
    except Exception:
        LOGGER.exception("운영시작 후 전역 버튼 상태 갱신 실패")


def auto_trade_start_status_indicator(
    window,
    target: tuple[Path, str, str],
    *,
    source: str = "auto_trade_status_indicator",
) -> dict[str, object]:
    stock_dir, code, _name = target
    inflight = getattr(window, "_operation_start_inflight_stock_codes", set())
    if code in inflight:
        return {
            "ok": False,
            "reason": "REQUEST_IN_PROGRESS",
            "request_scope": START_REQUEST_SINGLE,
            "source": source,
            "target_stock_code": code,
        }

    state = read_json_dict(Path(stock_dir) / "state.json")
    if auto_trade_setting_current_session_trade_started(
        window,
        auto_trade_setting_trade_started(state),
        code,
    ):
        result: dict[str, object] = {
            "ok": False,
            "reason": "ALREADY_RUNNING",
            "requested": (f"{code} {target[2]}",),
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (),
        }
        _apply_start_request_context(
            result,
            request_scope=START_REQUEST_SINGLE,
            selected=[target],
            request_source=source,
            stock_failure_reason="ALREADY_RUNNING",
        )
        setattr(window, "_last_operation_failure_dialog_shown", False)
        _show_start_failure_once(window, result)
        return result

    inflight.add(code)
    setattr(window, "_operation_start_inflight_stock_codes", inflight)
    try:
        result = auto_trade_start_selected_auto_trades(
            window,
            request_scope=START_REQUEST_SINGLE,
            selected_targets=[target],
            source=source,
        )
        state_after = read_json_dict(Path(stock_dir) / "state.json")
        if result.get("ok") is not True or auto_trade_setting_trade_started(state_after):
            return result

        failed_result: dict[str, object] = {
            "ok": False,
            "reason": "STATE_READBACK_FAILED",
            "requested": (f"{code} {target[2]}",),
            "excluded_review": (),
            "eligible": (),
            "completed": (),
            "blocked_validation": (),
            "failed": (f"{code} {target[2]}",),
        }
        _apply_start_request_context(
            failed_result,
            request_scope=START_REQUEST_SINGLE,
            selected=[target],
            request_source=source,
            stock_failure_reason="STATE_READBACK_FAILED",
        )
        _show_start_failure_once(window, failed_result)
        return failed_result
    finally:
        inflight.discard(code)
