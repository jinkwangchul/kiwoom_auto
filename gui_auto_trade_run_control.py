# -*- coding: utf-8 -*-
"""
gui_auto_trade_run_control.py

자동매매설정창의 운영시작/정지 처리 헬퍼.
"""

from __future__ import annotations

import math
import re
import logging
from pathlib import Path
from datetime import date, datetime

from PyQt5.QtWidgets import (
    QMessageBox,
    QWidget,
)

from gui_toast import show_toast
from gui_operation_ui_context import refresh_auto_trade_views
from event_journal_production import append_production_event
from runtime_io import read_json_dict
from runtime_stock_state_mutation import mutate_runtime_stock_state
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_required_state,
    is_review_required_stock_dir,
    restart_initial_review_reason_for_stock,
)
from gui_auto_trade_policy import (
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_trade_started,
)
from gui_auto_trade_runtime import all_registered_stock_dirs, parse_stock_folder_name
from gui_auto_trade_status_ops import (
    auto_trade_stock_operation_excluded,
    set_auto_trade_stock_operation_excluded,
)
from gui_order_utils import order_current_pending_qty
from gui_review_utils import review_required_for_start
from execution_queue_writer import read_execution_queue_records
from operation_close_completion_evaluator import (
    ACTIVE_QUEUE_STATUSES,
    CLOSED_QUEUE_STATUSES,
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
    effective_schedule_times,
    in_regular_manual_session,
    normalize_operation_mode,
    seconds_from_hhmmss,
    status_after_operation_mode_change,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
LOGGER = logging.getLogger(__name__)
START_REQUEST_SINGLE = "single"
START_REQUEST_MULTIPLE = "multiple"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"

_ACTIVE_CLOSE_STATUSES = {
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "LIQUIDATION",
    "LIQUIDATING",
}
_LIQUIDATION_REQUEST_KEYS = (
    "immediate_liquidation_request",
    "individual_liquidation_request",
    "manual_ats_liquidation_request",
)
_LIQUIDATION_REQUEST_TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "ORDER_BLOCKED",
}


def today_global_operation_status(operation_state: dict[str, object]) -> str:
    if not isinstance(operation_state, dict):
        return ""
    operation_date = str(operation_state.get("operation_date") or "").strip()
    if operation_date != date.today().isoformat():
        return ""
    return str(operation_state.get("operation_status") or "").strip().upper()


def auto_trade_registered_operation_targets() -> list[tuple[Path, str, str]]:
    targets: list[tuple[Path, str, str]] = []
    for stock_dir in all_registered_stock_dirs():
        code, name = parse_stock_folder_name(stock_dir.name)
        if code:
            targets.append((stock_dir, code, name))
    return targets


def auto_trade_registered_operation_start_targets(window=None) -> list[tuple[Path, str, str]]:
    target_getter = getattr(window, "registered_operation_targets", None)
    registered = (
        list(target_getter())
        if callable(target_getter)
        else auto_trade_registered_operation_targets()
    )
    return [
        target
        for target in registered
        if not auto_trade_stock_operation_excluded(target[0])
    ]


def auto_trade_running_registered_operation_targets(window) -> list[tuple[Path, str, str]]:
    running: list[tuple[Path, str, str]] = []
    target_getter = getattr(window, "registered_operation_targets", None)
    registered = (
        list(target_getter())
        if callable(target_getter)
        else auto_trade_registered_operation_targets()
    )
    for target in registered:
        if auto_trade_stock_operation_excluded(target[0]):
            continue
        state = read_json_dict(target[0] / "state.json")
        if auto_trade_setting_current_session_trade_started(
            window,
            auto_trade_setting_trade_started(state),
        ):
            running.append(target)
    return running


def auto_trade_update_global_operation_button_state(window) -> None:
    registered_getter = getattr(window, "registered_operation_targets", None)
    running_getter = getattr(window, "running_registered_operation_targets", None)
    registered = (
        list(registered_getter())
        if callable(registered_getter)
        else auto_trade_registered_operation_targets()
    )
    running = (
        list(running_getter())
        if callable(running_getter)
        else auto_trade_running_registered_operation_targets(window)
    )
    operation_state = read_operation_state()
    operation_status = today_global_operation_status(operation_state)
    global_normal_ended = operation_status == "NORMAL_ENDED"
    global_running = operation_status in {"RUNNING", "CLOSING"}
    global_emergency_stop = operation_state.get("emergency_stop") is True
    if global_normal_ended:
        text, foreground, background, hover_background = (
            "운영종료", "#374151", "#F3F4F6", "#F3F4F6"
        )
    elif global_emergency_stop:
        text, foreground, background, hover_background = (
            "긴급정지", "#991B1B", "#FEF2F2", "#FEF2F2"
        )
    elif running or global_running:
        text, foreground, background, hover_background = (
            "운영중", "#374151", "#F3F4F6", "#F3F4F6"
        )
    else:
        text, foreground, background, hover_background = (
            "▶ 운영시작", "#15803D", "#F0FDF4", "#DCFCE7"
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
        "color: #9CA3AF;"
        "border-color: #D1D5DB;"
        "background-color: #F3F4F6;"
        "}"
    )
    window.btn_start.setEnabled(
        bool(registered)
        and not bool(running)
        and not global_running
        and not global_emergency_stop
        and not global_normal_ended
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
    if status in _ACTIVE_CLOSE_STATUSES:
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
    if command_mode == "EARLY_CLOSE":
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

    if _today_normal_ended(operation_state, current):
        return blocked("NORMAL_ENDED")
    if is_emergency_stop(operation_state):
        return blocked("GLOBAL_EMERGENCY_STOP")
    if is_emergency_stopped_state(state):
        return blocked("EMERGENCY_STOPPED")
    if is_review_required_state(state):
        return blocked("REVIEW_REQUIRED")

    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    if mode == "CONTINUOUS":
        if not in_regular_manual_session(current):
            return blocked("OUTSIDE_OPERATION_TIME")
    else:
        start_time, end_buy_time, _custom = effective_schedule_times(config)
        current_seconds = current.hour * 3600 + current.minute * 60 + current.second
        start_seconds = seconds_from_hhmmss(start_time, "09:00:00")
        end_seconds = seconds_from_hhmmss(end_buy_time, "13:30:00")
        if not start_seconds <= current_seconds < end_seconds:
            return blocked("OUTSIDE_OPERATION_TIME")

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
) -> str:
    reasons = {str(item or "").strip() for item in failure_reasons if str(item or "").strip()}
    if all_emergency or (
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
    if reasons and reasons <= {"PREVIOUS_CLOSE_UNAVAILABLE"}:
        return (
            "전일 종가를 확인할 수 없어 초회 매수 금액을 검증할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오."
        )
    if reasons and reasons <= {"INITIAL_BUY_AMOUNT_BELOW_MINIMUM"}:
        return (
            "초회 매수 금액이 최소 거래금액보다 작습니다.\n"
            "전일 종가의 150% 이상으로 설정한 뒤 다시 시도하십시오."
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
    if reasons & {"OUTSIDE_OPERATION_TIME"}:
        return "현재는 선택한 종목의 운영시작 가능 시간이 아닙니다."
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
    if reason == "PREVIOUS_CLOSE_UNAVAILABLE":
        return (
            f"{label}의 전일 종가를 확인할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오."
        )
    if reason == "INITIAL_BUY_AMOUNT_BELOW_MINIMUM":
        return (
            f"{label}의 초회 매수 금액이 최소 거래금액보다 작습니다.\n"
            "전일 종가의 150% 이상으로 설정한 뒤 다시 시도하십시오."
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


def _show_start_failure_once(window, result: dict[str, object]) -> None:
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


def _positive_price(source: dict[str, object], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = source.get(key)
        try:
            price = abs(float(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def initial_buy_start_validation(
    config: dict[str, object],
    state: dict[str, object],
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

    previous_close = _positive_price(
        state,
        (
            "previous_close",
            "prev_close",
            "yesterday_close",
            "previous_close_price",
        ),
    )
    if previous_close is None:
        previous_close = _positive_price(
            config,
            (
                "previous_close",
                "prev_close",
                "yesterday_close",
                "previous_close_price",
            ),
        )
    if previous_close is None:
        return {
            "allowed": False,
            "reason": "PREVIOUS_CLOSE_UNAVAILABLE",
            "mode": "AMOUNT",
            "configured_value": max(0, int(config.get("buy_amount", 0) or 0)),
        }

    try:
        configured_amount = max(0, int(config.get("buy_amount", 0) or 0))
    except (TypeError, ValueError):
        configured_amount = 0
    minimum_amount = int(math.ceil(previous_close * 1.5))
    return {
        "allowed": configured_amount >= minimum_amount,
        "reason": (
            ""
            if configured_amount >= minimum_amount
            else "INITIAL_BUY_AMOUNT_BELOW_MINIMUM"
        ),
        "mode": "AMOUNT",
        "configured_value": configured_amount,
        "previous_close": previous_close,
        "minimum_amount": minimum_amount,
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

    if request_scope == START_REQUEST_SINGLE and len(selected) != 1:
        request_scope = START_REQUEST_MULTIPLE

    operation_state = read_operation_state()
    request_now = current_datetime()
    if _today_normal_ended(operation_state, request_now):
        requested = tuple(f"{code} {name}" for _stock_dir, code, name in selected)
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
            selected=selected,
            request_source=source,
            global_failure=True,
        )
        _show_start_failure_once(window, result)
        return result

    if is_emergency_stop(operation_state):
        requested = tuple(f"{code} {name}" for _stock_dir, code, name in selected)
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

    requested = tuple(f"{code} {name}" for _stock_dir, code, name in selected)
    review_checker = getattr(window, "start_target_is_review_isolated", None)
    candidate_targets: list[tuple[Path, str, str]] = []
    excluded_review: list[str] = []
    for stock_dir, code, name in selected:
        isolated = (
            bool(review_checker(stock_dir, code))
            if callable(review_checker)
            else is_review_required_stock_dir(stock_dir)
        )
        if isolated:
            excluded_review.append(f"{code} {name}")
        else:
            candidate_targets.append((stock_dir, code, name))

    try:
        start_targets, skipped = window.split_start_targets(candidate_targets)
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
            auto_trade_setting_trade_started(
                read_json_dict(stock_dir / "state.json")
            )
            for stock_dir, _code, _name in candidate_targets
        )
        result = {
            "ok": False,
            "reason": "NO_STARTABLE_TARGETS",
            "user_message": _start_failure_user_message(
                [],
                all_emergency=_all_start_targets_emergency_stopped(selected),
                all_review=all_review,
                all_already_running=all_already_running,
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
            stock_failure_reason=(
                "REVIEW_REQUIRED"
                if all_review
                else ("ALREADY_RUNNING" if all_already_running else "NOT_STARTABLE")
            ),
        )
        _show_start_failure_once(window, result)
        return result

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
            _show_start_failure_once(window, result)
            return result
        excluded_review.extend(
            str(item)
            for item in recovery_result.get("excluded_review", ())
            if str(item) and str(item) not in excluded_review
        )
        if recovery_result.get("allowed") is not True:
            result = {
                "ok": False,
                "reason": str(
                    recovery_result.get("reason")
                    or getattr(window, "_last_operation_block_reason", "")
                    or "RECOVERY_NOT_READY"
                ),
                "user_message": str(
                    recovery_result.get("user_message") or ""
                ).strip(),
                "requested": requested,
                "excluded_review": tuple(excluded_review),
                "eligible": (),
                "completed": (),
                "blocked_validation": (),
                "failed": (),
                "skipped": tuple(skipped),
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
            stock_failure_reason="REVIEW_REQUIRED",
        )
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
            validation = initial_buy_start_validation(config, state)
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

    try:
        refresh_auto_trade_views(window)
        window.stock_table.viewport().update()
        window.stock_table.repaint()
    except Exception:
        LOGGER.exception("운영 시작 후 화면 새로고침 실패")
    if completed or review_required:
        rebind_recovery = getattr(
            window,
            "rebind_startup_recovery_after_trusted_runtime_update",
            None,
        )
        if callable(rebind_recovery):
            rebind_recovery()

    result_lines = [
        f"운영시작: {len(completed)}개",
        f"기운영중: {len(skipped)}개",
        f"검토 대상 제외: {len(excluded_review)}개",
        f"검토관리 이동: {len(review_required)}개",
        f"실패: {len(failed)}개",
    ]

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
        "operation": "START",
        "requested_count": len(requested),
        "started_count": len(completed),
        "excluded_review_count": excluded_review_count,
        "excluded_validation_count": excluded_validation_count,
        "failed_count": non_validation_failed_count,
        "global_failure_reason": "",
        "internal_reason": tuple(dict.fromkeys(failure_reasons)),
    }
    if completed:
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
    _apply_start_request_context(
        result,
        request_scope=request_scope,
        selected=selected,
        request_source=source,
        stock_failure_reason=(failure_reasons[0] if failure_reasons else ""),
    )
    if completed:
        if result.get("operation_state_write_failed"):
            result_lines.append(
                "전역 운영 상태 기록 실패: 종목 시작 상태는 유지됩니다."
            )
        if request_scope != START_REQUEST_SINGLE:
            result["user_message"] = _start_result_summary(
                started_count=len(completed),
                excluded_review_count=excluded_review_count,
                excluded_validation_count=excluded_validation_count,
                failed_count=non_validation_failed_count,
            )
        result["user_action"] = ""
        if request_scope == START_REQUEST_SINGLE:
            window.statusBarMessage(str(result["user_message"]))
            if result.get("operation_state_write_failed"):
                window.statusBarMessage(
                    "전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오."
                )
        elif excluded_review_count or excluded_validation_count or non_validation_failed_count or skipped:
            window.statusBarMessage(str(result["user_message"]))
            if result.get("operation_state_write_failed"):
                window.statusBarMessage(
                    "전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오."
                )
        else:
            window.show_auto_trade_result_dialog(
                "운영시작 처리 완료",
                "운영시작 결과",
                result_lines,
            )
    else:
        if request_scope != START_REQUEST_SINGLE:
            result["user_message"] = _start_failure_user_message(
                failure_reasons,
                all_emergency=_all_start_targets_emergency_stopped(selected),
                all_review=bool(review_required) and not failed,
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
            status_message = getattr(window, "statusBarMessage", None)
            if callable(status_message):
                status_message("정상 운영시작 되었습니다.")
        window.update_global_operation_button_state()
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
        status_message = getattr(window, "statusBarMessage", None)
        if callable(status_message):
            status_message("운영중인 종목입니다.")
        return {"ok": False, "reason": "ALREADY_RUNNING"}

    if selected_running and selected_inactive:
        status_message = getattr(window, "statusBarMessage", None)
        if callable(status_message):
            status_message("운영중인 종목이 포함되어 있습니다.")
        return {"ok": False, "reason": "MIXED_RUNNING_SELECTION"}

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
    )
    if result.get("ok") is True:
        status_message = getattr(window, "statusBarMessage", None)
        if callable(status_message):
            status_message("정상 운영시작 되었습니다.")
    window.update_global_operation_button_state()
    return result


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
    if auto_trade_setting_trade_started(state):
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
