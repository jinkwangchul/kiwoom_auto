# -*- coding: utf-8 -*-
"""
gui_auto_trade_close.py

자동매매설정창의 조기마감/개별청산 처리 헬퍼.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from gui_operation_ui_context import (
    actionable_current_price,
    operation_dialog_parent,
    refresh_auto_trade_views,
)
from gui_window_policy import persistent_feature_owner

from gui_common_utils import safe_int_value
from gui_config_utils import default_config
from gui_order_utils import order_current_pending_qty
from gui_order_utils import order_datetime
from gui_order_utils import order_value
from gui_order_utils import pending_order_side_quantities
from gui_order_utils import read_orders_data
from runtime_io import read_json_dict
from gui_auto_trade_runtime import parse_stock_folder_name
from gui_auto_trade_table_loader import _selected_instance_stock_dirs
from gui_toast import show_toast
from gui_user_reason import user_reason_message
from gui_review_utils import safe_float_value
from state_policy import normalize_operation_mode, seconds_from_hhmmss
from event_journal_production import append_production_event
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
)
from gui_auto_trade_policy import (
    operation_policy_section,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_has_buy_pending_problem,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_effective_liquidation_window,
    active_operation_policy_snapshot,
    effective_liquidation_policy_for_config,
    individual_liquidation_policy_from_state,
    clear_early_close_runtime_metadata_only,
    close_method_from_state_or_policy,
    auto_trade_setting_liquidation_text,
    short_close_method_text,
)
from close_liquidation_transition_service import (
    normalize_direct_close_policy_alias,
)
from close_liquidation_execution_pipeline import (
    build_close_liquidation_candidate_preview,
    commit_close_liquidation_candidate_preview,
    normalize_direct_liquidation_method,
)
from close_liquidation_command import (
    EARLY_CLOSE_CANCEL,
    EARLY_CLOSE_REQUEST,
    INDIVIDUAL_LIQUIDATION,
    execute_early_close_cancel_command,
    execute_early_close_request_command,
    execute_individual_liquidation_command,
    inspect_close_liquidation_availability,
)
from operation_close_completion_check_service import (
    SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
    SOURCE_LIQUIDATION_DURABLE_UPDATE,
    check_global_close_completion_after_durable_update,
)
from transition_production_guard import evaluate_production_transition
from execution_queue_writer import read_execution_queue_records


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
LOGGER = logging.getLogger(__name__)
EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS = frozenset(
    {
        "RECOVERY_CONTEXT_MISSING",
        "RECOVERY_NOT_STARTED",
        "RECOVERY_IN_PROGRESS",
    }
)

_EARLY_CLOSE_TIME_BLOCK_REASONS = frozenset(
    {
        "OUTSIDE_REGULAR_MARKET",
        "SCHEDULED_OPERATION_WINDOW_ENDED",
        "LIQUIDATION_IN_PROGRESS",
    }
)
_CLOSE_METHOD_BLOCK_REASONS = frozenset(
    {
        "MARKET_DOWNGRADE_NOT_ALLOWED",
        "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED",
        "RETURN_TO_CARRY_OVER_NOT_ALLOWED",
        "CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS",
        "EXECUTION_PROGRESS_BLOCKED",
        "TRANSITION_BLOCKED",
    }
)


def _close_liquidation_admission_level(reason_code: object) -> int:
    code = str(reason_code or "").strip().upper()
    if code == "SERVER_NOT_CONNECTED":
        return 1
    if code == "NO_HOLDING":
        return 2
    if code == "NOT_CURRENT_PARTICIPANT":
        return 3
    if code in _EARLY_CLOSE_TIME_BLOCK_REASONS:
        return 4
    if code == "LIQUIDATION_TIME_WINDOW_ENTERED" or code in _CLOSE_METHOD_BLOCK_REASONS:
        return 5
    return 6


def _close_liquidation_user_message(reason_code: object) -> str:
    code = str(reason_code or "").strip().upper()
    if code == "SERVER_NOT_CONNECTED":
        return "키움 서버에 로그인되어 있지 않습니다."
    if code == "NO_HOLDING":
        return "보유수량이 없습니다."
    if code == "NOT_CURRENT_PARTICIPANT":
        return "현재 운영 대상 종목이 아닙니다."
    if code in _EARLY_CLOSE_TIME_BLOCK_REASONS:
        return "현재 조기마감 가능한 시간이 아닙니다."
    if code == "LIQUIDATION_TIME_WINDOW_ENTERED":
        return "청산설정변경이 불가능합니다."
    if code in _CLOSE_METHOD_BLOCK_REASONS:
        return "현재 마감방식으로 변경할 수 없습니다."
    return "현재 상태에서는 실행할 수 없습니다."

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_close_liquidation_block_event(
    stock_dir: Path,
    code: str,
    name: str,
    *,
    requested_action: str,
    reason_code: str,
    source: str,
    requested_policy: dict[str, object] | None = None,
) -> None:
    state = read_json_dict(stock_dir / "state.json")
    config = read_json_dict(stock_dir / "config.json")
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    snapshot = active_operation_policy_snapshot(state)
    boundary = auto_trade_setting_effective_liquidation_window(config, state)
    schedule = snapshot.get("operation_schedule")
    schedule = schedule if isinstance(schedule, dict) else {}
    requested = dict(requested_policy or {})
    append_production_event(
        "OPERATOR_OPERATION_DECISION",
        result="BLOCKED",
        source=source,
        target_type="STOCK",
        target_id=code or None,
        target_name=f"{code} {name}".strip(),
        stock_code=code or None,
        stock_name=name or None,
        routine=str(config.get("assigned_routine_instance_id") or "").strip() or None,
        details={
            "timestamp": now_text(),
            "operation_identity": str(
                snapshot.get("operation_identity")
                or state.get("trade_started_at")
                or ""
            ).strip(),
            "requested_action": str(requested_action or "").strip(),
            "admission_level": _close_liquidation_admission_level(reason_code),
            "requested_policy": requested,
            "requested_method": str(requested.get("method") or "").strip(),
            "reason_code": str(reason_code or "BLOCKED").strip(),
            "operation_mode": normalize_operation_mode(
                snapshot.get("operation_mode")
                or config.get("operation_mode", "SCHEDULED")
            ),
            "relevant_time_boundary": {
                "operation_start": str(schedule.get("start_time") or "").strip(),
                "operation_end": str(schedule.get("end_buy_time") or "").strip(),
                "liquidation_start_seconds": boundary.get(
                    "liquidation_start_seconds"
                ),
                "regular_end_seconds": boundary.get("regular_end_seconds"),
            },
            "current_state": {
                "status": str(state.get("status") or "").strip(),
                "holding_qty": safe_int_value(state.get("holding_qty"), 0),
                "close_source": str(state.get("close_source") or "").strip(),
                "close_method": str(
                    state.get("early_close_method")
                    or state.get("auto_close_method")
                    or ""
                ).strip(),
                "liquidation_phase": str(
                    state.get("liquidation_phase") or ""
                ).strip(),
            },
            "request_source": str(source or "").strip(),
        },
    )


def _production_recovery_gate(window, code: str, caller_name: str):
    parent = persistent_feature_owner(window)
    checker = getattr(type(parent), "production_recovery_gate_for_stock", None)
    if not callable(checker):
        return None
    return checker(parent, code, caller_name=caller_name)


def _log_recovery_block(
    window,
    *,
    code: str,
    caller_name: str,
    recovery,
    routine_instance_id: str = "",
) -> None:
    reason_code = str(getattr(recovery, "reason_code", "") or "").strip()
    evidence = tuple(getattr(recovery, "evidence", ()) or ())
    has_internal_error_evidence = any(
        str(item).startswith(("registry_error=", "gate_exception="))
        for item in evidence
    )
    if (
        reason_code in EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS
        and not has_internal_error_evidence
    ):
        return
    parent = persistent_feature_owner(window)
    api = getattr(parent, "kiwoom_api", None)
    login_session_reader = getattr(api, "login_session_id", None)
    login_session_present = False
    if callable(login_session_reader):
        try:
            login_session_present = bool(
                str(login_session_reader() or "").strip()
            )
        except Exception:
            login_session_present = False
    account_reader = getattr(parent, "selected_account_no", None)
    account_selected = False
    if callable(account_reader):
        try:
            account_selected = bool(str(account_reader() or "").strip())
        except Exception:
            account_selected = False
    LOGGER.warning(
        "Auto-trade operation blocked by Production Recovery: "
        "caller=%s routine_instance=%s stock=%s reason=%s evidence=%s "
        "login_session_present=%s account_selected=%s requested_at=%s",
        caller_name,
        routine_instance_id,
        code,
        reason_code,
        evidence,
        login_session_present,
        account_selected,
        now_text(),
    )


def _recovery_block_user_message(window, recovery) -> str:
    parent = persistent_feature_owner(window)
    formatter = getattr(
        type(parent),
        "production_recovery_block_user_message",
        None,
    )
    if callable(formatter):
        try:
            message = str(formatter(parent, recovery) or "").strip()
            if message:
                return message
        except Exception:
            LOGGER.exception("Production Recovery 사용자 메시지 생성 실패")
    return "운영 상태 확인이 완료되지 않았습니다. 잠시 후 다시 시도해 주세요."


def _transition_trade_date(timestamp: object, fallback: str) -> str:
    text = str(timestamp or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return fallback[:10]


def _close_liquidation_cancel_required(method: object) -> bool:
    return bool(normalize_direct_liquidation_method(method)) or (
        short_close_method_text(method) in {"손/익절", "이월"}
    )


def _operation_mode_from_active_snapshot(state: dict[str, object]) -> str:
    snapshot = active_operation_policy_snapshot(state)
    if not snapshot:
        return ""
    return normalize_operation_mode(snapshot.get("operation_mode", ""))


def _liquidation_runtime_metadata(
    state: dict[str, object],
    *,
    boundary: dict[str, object],
    result: dict[str, object],
    command_id: str,
) -> dict[str, object]:
    stage = str(result.get("stage") or "").strip()
    runtime_status = str(result.get("runtime_status") or "").strip().upper()
    method = short_close_method_text(boundary.get("method")) or "이월"
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    completed = stage in {"completed", "carryover_completed"}
    review_required = runtime_status == "REVIEW_REQUIRED"
    return {
        "liquidation_method": method,
        "liquidation_execution": {
            "operation_identity": str(boundary.get("operation_identity") or "").strip(),
            "regular_end_seconds": int(boundary.get("regular_end_seconds") or 0),
            "liquidation_start_seconds": int(
                boundary.get("liquidation_start_seconds") or 0
            ),
            "policy_source": str(boundary.get("source") or "GLOBAL").strip(),
            "method": method,
            "phase": (
                "REVIEW_REQUIRED"
                if review_required
                else "COMPLETED"
                if completed
                else "CANCEL_CONFIRMATION"
                if stage == "awaiting_cancel_confirmation"
                else "ACTIVE"
            ),
            "cancel_requested": _close_liquidation_cancel_required(method),
            "cancel_confirmed": stage != "awaiting_cancel_confirmation",
            "reconciliation_completed": stage != "awaiting_cancel_confirmation",
            "liquidation_active": not completed and not review_required,
            "liquidation_completed": completed,
            "termination_provenance": (
                "LIQUIDATION_CARRYOVER"
                if stage == "carryover_completed"
                else f"LIQUIDATION_{normalize_direct_liquidation_method(method)}_RESIDUAL"
                if stage == "regular_end_residual" and holding_qty > 0
                else "LIQUIDATION_EXECUTION_FAILURE_RESIDUAL"
                if review_required and holding_qty > 0
                else "LIQUIDATION_EXECUTION_FAILURE"
                if review_required
                else "LIQUIDATION_COMPLETED"
                if completed
                else ""
            ),
            "command_id": command_id,
            "updated_at": now_text(),
        },
    }


def _persist_liquidation_execution_result(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    boundary: dict[str, object],
    result: dict[str, object],
    command_id: str,
) -> bool:
    state = read_json_dict(stock_dir / "state.json")
    stage = str(result.get("stage") or "").strip()
    runtime_status = str(result.get("runtime_status") or "").strip().upper()
    if runtime_status == "REVIEW_REQUIRED":
        next_status = "REVIEW_REQUIRED"
    elif stage in {"completed", "carryover_completed"}:
        next_status = "LIQUIDATED"
    else:
        next_status = "LIQUIDATING"
    metadata = _liquidation_runtime_metadata(
        state,
        boundary=boundary,
        result=result,
        command_id=command_id,
    )
    request = state.get("individual_liquidation_request")
    request = request if isinstance(request, dict) else {}
    if (
        (stage in {"completed", "carryover_completed"} or runtime_status == "REVIEW_REQUIRED")
        and str(request.get("status") or "").strip().upper() == "REQUESTED"
        and str(request.get("operation_identity") or "").strip()
        == str(boundary.get("operation_identity") or "").strip()
    ):
        completed_request = dict(request)
        completed_request.update(
            {
                "status": (
                    "FAILED" if runtime_status == "REVIEW_REQUIRED" else "COMPLETED"
                ),
                "completed_at": now_text(),
                "completion_reason": "LIQUIDATION_TERMINAL",
            }
        )
        metadata["individual_liquidation_request"] = completed_request
    if next_status == "REVIEW_REQUIRED":
        metadata.update(
            {
                "review_required": True,
                "review_reason": "청산 후 보유잔량"
                if safe_int_value(state.get("holding_qty"), 0) > 0
                else "청산 처리 오류",
                "review_location": "청산",
            }
        )
    return bool(
        window.update_stock_status(
            stock_dir,
            code,
            name,
            next_status,
            metadata,
            f"청산/{stage or next_status}",
        )
    )


def _continue_close_method_transition(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    state: dict[str, object],
    routine_instance_id: str,
) -> dict[str, object] | None:
    transition = state.get("close_transition_pending")
    transition = transition if isinstance(transition, dict) else {}
    if not transition:
        return None
    cancel_result = window.queue_pending_order_cancellations_for_stock_automatically(
        code,
        routine_instance_id,
        trading_day=_transition_trade_date(
            transition.get("requested_at"),
            now_text(),
        ),
        started_at=str(state.get("trade_started_at") or "").strip(),
    )
    if cancel_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": "close_transition_cancel",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(cancel_result.get("blocked_reasons") or []),
        }
    if (
        int(cancel_result.get("cancel_requested", 0) or 0) > 0
        or int(cancel_result.get("cancel_pending", 0) or 0) > 0
    ):
        return {
            "ok": True,
            "stage": "close_transition_awaiting_cancel",
            "runtime_status": str(state.get("status") or "EARLY_CLOSE"),
        }
    buy_pending, sell_pending = pending_order_side_quantities(stock_dir, state)
    if buy_pending > 0 or sell_pending > 0:
        return {
            "ok": False,
            "stage": "close_transition_pending_evidence",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": ["close transition has unresolved pending quantity"],
        }
    if str(transition.get("method") or "").strip().upper() == "AUTO_TIMELINE":
        early_requested_at = str(state.get("early_close_requested_at") or "").strip()
        if (
            not early_requested_at
            or _stock_sell_fill_qty_after(stock_dir, early_requested_at) > 0
        ):
            saved = window.update_stock_status(
                stock_dir,
                code,
                name,
                str(state.get("status") or "EARLY_CLOSE"),
                {"close_transition_pending": None},
                "마감변경/자동마감 체결차단",
            )
            return {
                "ok": False,
                "stage": "early_auto_return_filled_blocked",
                "runtime_status": (
                    str(state.get("status") or "EARLY_CLOSE")
                    if saved
                    else "REVIEW_REQUIRED"
                ),
                "blocked_reasons": [
                    "EARLY hard-close SELL fill blocks return to AUTO timeline"
                ],
            }
        metadata = clear_early_close_runtime_metadata_only(dict(state))
        metadata["close_transition_pending"] = None
        metadata["early_auto_returned_at"] = now_text()
        saved = window.update_stock_status(
            stock_dir,
            code,
            name,
            "RUNNING",
            metadata,
            "마감변경/자동마감",
        )
        return {
            "ok": bool(saved),
            "stage": "early_auto_timeline_restored" if saved else "runtime_state_write",
            "runtime_status": "RUNNING" if saved else "REVIEW_REQUIRED",
            "blocked_reasons": [] if saved else ["auto timeline state write failed"],
        }
    method = normalize_direct_close_policy_alias(transition.get("method")) or "이월"
    policy = transition.get("policy")
    policy = policy if isinstance(policy, dict) else {"method": method}
    close_cause = str(transition.get("close_cause") or "EARLY").strip().upper()
    metadata = {
        "liquidation_policy_forced": method in {"시장가", "현재가"},
        "liquidation_policy_reason": (
            "EARLY_CLOSE" if method in {"시장가", "현재가"} else ""
        ),
        "close_transition_pending": None,
        "close_routine_final_sell_ordered": False,
        "close_routine_final_sell_ordered_at": "",
        "close_routine_final_sell_source": "",
        "close_routine_final_sell_reason": "",
    }
    if close_cause == "AUTO":
        metadata.update(
            {
                "auto_close_method": method,
                "auto_close_policy": policy,
            }
        )
        next_status = "AUTO_CLOSE"
    else:
        metadata.update(
            {
                "early_close_requested_at": str(
                    state.get("early_close_requested_at")
                    or transition.get("requested_at")
                    or now_text()
                ),
                "early_close_source": str(
                    state.get("early_close_source")
                    or transition.get("source")
                    or "우클릭"
                ),
                "early_close_method": method,
                "early_close_policy": policy,
            }
        )
        next_status = "EARLY_CLOSE"
    saved = window.update_stock_status(
        stock_dir,
        code,
        name,
        next_status,
        metadata,
        "마감변경",
    )
    return {
        "ok": bool(saved),
        "stage": "close_transition_applied" if saved else "runtime_state_write",
        "runtime_status": next_status if saved else "REVIEW_REQUIRED",
        "blocked_reasons": [] if saved else ["close transition state write failed"],
    }
def _close_execution_result(
    execution_result: dict[str, object],
) -> dict[str, object]:
    send_result = execution_result.get("send_order_result")
    send_result = send_result if isinstance(send_result, dict) else {}
    send_status = str(send_result.get("status") or "").strip().upper()
    if send_status in {"SEND_CALL_REJECTED", "SEND_UNCERTAIN"}:
        return {
            "ok": False,
            "stage": send_status.lower(),
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(
                send_result.get("blocked_reasons")
                or [f"SendOrder result requires review: {send_status}"]
            ),
            "execution_result": execution_result,
        }
    return {
        "ok": execution_result.get("processed") is True,
        "stage": str(execution_result.get("stage") or "execution"),
        "runtime_status": (
            "EARLY_CLOSING"
            if execution_result.get("processed") is True
            else "REVIEW_REQUIRED"
        ),
        "blocked_reasons": list(
            execution_result.get("blocked_reasons") or []
        ),
        "execution_result": execution_result,
    }


def _resume_existing_close_order(
    window,
    record: dict[str, object],
    *,
    holding_qty: int,
) -> dict[str, object]:
    status = str(record.get("status") or "").strip().upper()
    order_id = str(record.get("id") or record.get("order_id") or "").strip()

    if holding_qty <= 0:
        return {
            "ok": True,
            "stage": "completed",
            "runtime_status": "EARLY_CLOSED",
            "order_id": order_id,
            "queue_status": status,
        }
    if status == "EXECUTABLE":
        return _close_execution_result(
            window.process_executable_order_for_auto_trade(order_id)
        )
    if status == "ORDER_QUEUED":
        send_result = window.send_order_for_order_queued_automatically(order_id)
        return _close_execution_result(
            {
                "processed": send_result.get("queue_result_recorded") is True,
                "stage": "send_order",
                "blocked_reasons": list(
                    send_result.get("blocked_reasons")
                    or send_result.get("issues")
                    or []
                ),
                "send_order_result": send_result,
            }
        )
    if status in {
        "SEND_CALL_ACCEPTED",
        "BROKER_ACCEPTED",
        "PARTIALLY_FILLED",
        "DISPATCH_CLAIMED",
        "SEND_ORDER_CALLED",
    }:
        return {
            "ok": True,
            "stage": "order_progress",
            "runtime_status": "EARLY_CLOSING",
            "order_id": order_id,
            "queue_status": status,
        }
    if status == "FILLED":
        return {
            "ok": False,
            "stage": "filled_holding_mismatch",
            "runtime_status": "REVIEW_REQUIRED",
            "order_id": order_id,
            "queue_status": status,
            "blocked_reasons": [
                "filled close order still has a positive Runtime holding quantity"
            ],
        }
    return {
        "ok": False,
        "stage": "order_pipeline_terminal",
        "runtime_status": "REVIEW_REQUIRED",
        "order_id": order_id,
        "queue_status": status,
        "blocked_reasons": [
            f"close order pipeline cannot continue from status {status or 'UNKNOWN'}"
        ],
    }


def _persist_early_close_execution_result(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    result: dict[str, object],
) -> bool:
    runtime_status = str(result.get("runtime_status") or "").strip().upper()
    if runtime_status not in {
        "EARLY_CLOSE",
        "EARLY_CLOSING",
        "EARLY_CLOSED",
        "REVIEW_REQUIRED",
    }:
        return True

    stage = str(result.get("stage") or "").strip()
    notice_by_status = {
        "EARLY_CLOSE": ("EARLY_CLOSE_WAITING", "조기마감 실행 대기"),
        "EARLY_CLOSING": ("EARLY_CLOSE_ORDER_PROGRESS", "조기마감 주문 진행"),
        "EARLY_CLOSED": ("EARLY_CLOSE_COMPLETED", "조기마감 완료"),
        "REVIEW_REQUIRED": ("EARLY_CLOSE_EXECUTION_FAILED", "조기마감 실행 실패"),
    }
    notice, notice_reason = notice_by_status[runtime_status]
    state = read_json_dict(stock_dir / "state.json")
    if (
        str(state.get("status") or "").strip().upper() == runtime_status
        and str(state.get("operation_notice") or "").strip().upper() == notice
    ):
        return True

    metadata: dict[str, object] = {
        "operation_notice": notice,
        "operation_notice_reason": notice_reason,
        "operation_notice_at": now_text(),
    }
    if stage == "carryover_completed":
        metadata["termination_provenance"] = "CLOSE_CARRYOVER"
    request = state.get("individual_liquidation_request")
    request = request if isinstance(request, dict) else {}
    if runtime_status in {"EARLY_CLOSED", "REVIEW_REQUIRED"} and str(
        request.get("status") or ""
    ).strip().upper() == "REQUESTED":
        completed_request = dict(request)
        completed_request.update(
            {
                "status": (
                    "FAILED" if runtime_status == "REVIEW_REQUIRED" else "COMPLETED"
                ),
                "completed_at": now_text(),
                "completion_reason": "OPERATION_ENDED_BY_EARLY_CLOSE",
            }
        )
        metadata["individual_liquidation_request"] = completed_request
    if runtime_status == "REVIEW_REQUIRED":
        metadata.update(
            {
                "review_required": True,
                "review_reason": "청산 처리 오류",
                "review_location": "운영 중",
            }
        )
    return bool(
        window.update_stock_status(
            stock_dir,
            code,
            name,
            runtime_status,
            metadata,
            f"조기마감/{stage or runtime_status}",
        )
    )


def _start_close_liquidation_execution(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    method: str,
    command_id: str,
    requested_at: str,
    routine_instance_id: str,
    reason: str,
    regular_end_reached: bool = False,
) -> dict[str, object]:
    """Enter existing Cancel/Candidate/Final-Gate pipelines for one stock."""

    recovery = _production_recovery_gate(
        window,
        code,
        f"{reason}_EXECUTION",
    )
    if recovery is not None and recovery.allowed is not True:
        _log_recovery_block(
            window,
            code=code,
            caller_name=f"{reason}_EXECUTION",
            recovery=recovery,
            routine_instance_id=routine_instance_id,
        )
        return {
            "ok": False,
            "stage": "production_recovery",
            "runtime_status": "UNCHANGED",
            "blocked_reasons": [recovery.reason_code],
        }

    state = read_json_dict(stock_dir / "state.json")
    liquidation_execution = state.get("liquidation_execution")
    liquidation_execution = (
        liquidation_execution
        if isinstance(liquidation_execution, dict)
        else {}
    )
    handoff_confirmed = (
        reason == "LIQUIDATION_BOUNDARY"
        and liquidation_execution.get("cancel_confirmed") is True
        and not regular_end_reached
    )
    if _close_liquidation_cancel_required(method) and not handoff_confirmed:
        cancel_result = (
            window.queue_pending_order_cancellations_for_stock_automatically(
                code,
                routine_instance_id,
                trading_day=_transition_trade_date(
                    requested_at,
                    requested_at,
                ),
                started_at=str(state.get("trade_started_at") or "").strip(),
            )
        )
        if cancel_result.get("ok") is not True:
            return {
                "ok": False,
                "stage": "pending_cancel",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": list(
                    cancel_result.get("blocked_reasons") or []
                ),
                "cancel_result": cancel_result,
            }
        if (
            int(cancel_result.get("cancel_requested", 0) or 0) > 0
            or int(cancel_result.get("cancel_pending", 0) or 0) > 0
        ):
            return {
                "ok": True,
                "stage": "awaiting_cancel_confirmation",
                "runtime_status": "EARLY_CLOSE",
                "cancel_result": cancel_result,
            }
        buy_pending, sell_pending = pending_order_side_quantities(
            stock_dir,
            state,
        )
        if buy_pending > 0 or sell_pending > 0:
            return {
                "ok": False,
                "stage": "pending_cancel_evidence",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": [
                    "unresolved pending quantity has no confirmed cancel pipeline"
                ],
                "cancel_result": cancel_result,
            }

    normalized_method = short_close_method_text(method)
    direct_method = normalize_direct_liquidation_method(method)
    state = read_json_dict(stock_dir / "state.json")
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    if normalized_method == "이월":
        return {
            "ok": True,
            "stage": "carryover_completed",
            "runtime_status": (
                "EARLY_CLOSED" if reason == "EARLY_CLOSE" else "LIQUIDATED"
            ),
        }
    if regular_end_reached:
        if holding_qty <= 0:
            return {
                "ok": True,
                "stage": "completed",
                "runtime_status": "LIQUIDATED",
            }
        return {
            "ok": False,
            "stage": "regular_end_residual",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": ["liquidation residual holding remains at regular end"],
        }
    if normalized_method == "손/익절":
        if holding_qty <= 0:
            return {
                "ok": True,
                "stage": "completed",
                "runtime_status": "EARLY_CLOSED",
            }
        policy = state.get("early_close_policy")
        policy = policy if isinstance(policy, dict) else {}
        profit_percent = safe_float_value(policy.get("profit_percent"), 0.0)
        loss_percent = safe_float_value(policy.get("loss_percent"), 0.0)
        average_price = safe_float_value(state.get("avg_price"), 0.0)
        current_price = safe_float_value(actionable_current_price(window, code), 0.0)
        if average_price <= 0 or current_price <= 0 or (
            profit_percent <= 0 and loss_percent <= 0
        ):
            return {
                "ok": False,
                "stage": "profit_loss_evidence",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": ["profit/loss close evidence is unavailable"],
            }
        return_percent = ((current_price - average_price) / average_price) * 100.0
        threshold_reached = (
            profit_percent > 0 and return_percent >= profit_percent
        ) or (
            loss_percent > 0 and return_percent <= -loss_percent
        )
        if not threshold_reached:
            return {
                "ok": True,
                "stage": "profit_loss_threshold_wait",
                "runtime_status": "EARLY_CLOSE",
                "return_percent": return_percent,
            }
        direct_method = "CURRENT_PRICE"
    if not direct_method:
        return {
            "ok": True,
            "stage": "policy_runtime_only",
            "runtime_status": "EARLY_CLOSE",
        }

    queue_snapshot = read_execution_queue_records(ORDER_QUEUE_PATH)
    if queue_snapshot.get("ok") is not True:
        return {
            "ok": False,
            "stage": "queue_read",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": ["canonical order queue is unavailable"],
        }
    matching_records = [
        record
        for record in queue_snapshot.get("records", ())
        if isinstance(record, dict)
        and str(record.get("source_signal_id") or "").strip() == command_id
    ]
    if matching_records:
        return _resume_existing_close_order(
            window,
            matching_records[-1],
            holding_qty=holding_qty,
        )

    if holding_qty <= 0:
        return {
            "ok": True,
            "stage": "completed",
            "runtime_status": "EARLY_CLOSED",
        }

    preview = build_close_liquidation_candidate_preview(
        stock_dir,
        code,
        name,
        direct_method,
        command_id=command_id,
        requested_at=requested_at,
        routine_instance_id=routine_instance_id,
        reason=reason,
        current_price_reader=(
            lambda stock_code, _stock_name="": actionable_current_price(
                window,
                stock_code,
            )
        ),
    )
    if preview.get("ok") is not True:
        return {
            "ok": False,
            "stage": "candidate_preview",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(preview.get("blocked_reasons") or []),
            "preview": preview,
        }
    commit_result = commit_close_liquidation_candidate_preview(preview)
    if commit_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": str(commit_result.get("stage") or "candidate_commit"),
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(
                commit_result.get("blocked_reasons") or []
            ),
            "preview": preview,
            "commit_result": commit_result,
        }
    result = _close_execution_result(
        window.process_executable_order_for_auto_trade(
            str(commit_result.get("order_id") or "")
        )
    )
    return {
        **result,
        "preview": preview,
        "commit_result": commit_result,
    }


def auto_trade_continue_pending_close_liquidations(
    window,
    *,
    limit: int | None = 5,
    target_routine_instance_ids: set[str] | tuple[str, ...] | None = None,
    now_dt: datetime | None = None,
) -> dict[str, object]:
    """Resume Command requests after cancel confirmation without new identity."""

    target_instances = {
        str(instance_id or "").strip()
        for instance_id in (target_routine_instance_ids or ())
        if str(instance_id or "").strip()
    }

    try:
        from gui_auto_trade_runtime import all_registered_stock_dirs

        stock_dirs = all_registered_stock_dirs()
    except Exception as exc:
        return {
            "processed": 0,
            "blocked": 1,
            "results": [],
            "blocked_reasons": [f"registered stock lookup failed: {exc}"],
        }

    results: list[dict[str, object]] = []
    processed = 0
    blocked_count = 0
    for stock_dir in stock_dirs:
        if limit is not None and len(results) >= max(0, int(limit)):
            break
        stock_path = Path(stock_dir)
        state = read_json_dict(stock_path / "state.json")
        config = read_json_dict(stock_path / "config.json")
        if not state or not config:
            continue
        liquidation_execution = state.get("liquidation_execution")
        liquidation_execution = (
            liquidation_execution
            if isinstance(liquidation_execution, dict)
            else {}
        )
        if str(liquidation_execution.get("phase") or "").strip().upper() in {
            "COMPLETED",
            "REVIEW_REQUIRED",
        }:
            continue
        code = stock_path.name.split("_", 1)[0]
        name = stock_path.name.split("_", 1)[1] if "_" in stock_path.name else ""
        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        if target_instances and routine_instance_id not in target_instances:
            continue
        transition_result = _continue_close_method_transition(
            window,
            stock_dir=stock_path,
            code=code,
            name=name,
            state=state,
            routine_instance_id=routine_instance_id,
        )
        if transition_result is not None:
            results.append(
                {
                    "stock_dir": str(stock_path),
                    "code": code,
                    "command_id": str(state.get("operation_command_id") or ""),
                    **transition_result,
                }
            )
            if transition_result.get("ok") is True:
                processed += 1
            else:
                blocked_count += 1
            continue

        snapshot = active_operation_policy_snapshot(state)
        request = state.get("individual_liquidation_request")
        request = request if isinstance(request, dict) else {}
        raw_individual_requested = (
            str(request.get("status") or "").strip().upper() == "REQUESTED"
            and str(request.get("reservation_scope") or "").strip().upper()
            != "NEXT_OPERATION"
        )
        individual_requested = bool(individual_liquidation_policy_from_state(state))
        early_requested = bool(
            str(state.get("early_close_requested_at") or "").strip()
        )
        status_text = str(state.get("status") or "").strip().upper()
        active_operation_evidence = bool(
            str(state.get("trade_started_at") or "").strip()
            and status_text
            in {
                "RUNNING",
                "MONITORING",
                "AUTO_CLOSE",
                "AUTO_CLOSING",
                "EARLY_CLOSE",
                "EARLY_CLOSING",
                "LIQUIDATING",
            }
        )
        if not snapshot and (
            active_operation_evidence
            or raw_individual_requested
            or early_requested
        ):
            results.append(
                {
                    "stock_dir": str(stock_path),
                    "code": code,
                    "command_id": str(request.get("command_id") or ""),
                    "ok": False,
                    "stage": "operation_policy_snapshot",
                    "runtime_status": "UNCHANGED",
                    "blocked_reasons": ["ACTIVE_OPERATION_POLICY_SNAPSHOT_MISSING"],
                }
            )
            blocked_count += 1
            continue
        boundary = auto_trade_setting_effective_liquidation_window(
            config,
            state,
            now_dt=now_dt,
        )
        close_carryover_completed = (
            str(state.get("termination_provenance") or "").strip().upper()
            == "CLOSE_CARRYOVER"
        )
        active_mode = _operation_mode_from_active_snapshot(state)
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        buy_pending, sell_pending = pending_order_side_quantities(stock_path, state)
        has_pending = buy_pending not in (0, "0", None) or sell_pending not in (
            0,
            "0",
            None,
        )
        boundary_eligible = not close_carryover_completed and bool(
            boundary.get("entered")
        ) and (
            active_mode == "SCHEDULED" or individual_requested or early_requested
        )
        cleanup_entered = bool(boundary.get("pending_order_cancel_entered"))
        boundary_ended = bool(boundary.get("ended"))
        boundary_method = short_close_method_text(boundary.get("method")) or "이월"
        regular_end_pending_cleanup = bool(
            active_operation_evidence and cleanup_entered and has_pending
        )
        liquidation_carryover_ready = bool(
            boundary_eligible
            and boundary_method == "이월"
            and cleanup_entered
            and holding_qty > 0
            and not has_pending
        )
        boundary_admitted = bool(
            boundary_eligible
            and boundary_method != "이월"
            and holding_qty > 0
            and (not cleanup_entered or boundary_ended)
        )

        individual_no_holding_complete = bool(
            boundary_eligible
            and individual_requested
            and holding_qty <= 0
            and not has_pending
        )

        if individual_no_holding_complete:
            completed_request = dict(request)
            completed_request.update(
                {
                    "status": "COMPLETED",
                    "completed_at": now_text(),
                    "completion_reason": "NO_HOLDING_AT_BOUNDARY",
                }
            )
            persisted = window.update_stock_status(
                stock_path,
                code,
                name,
                str(state.get("status") or "RUNNING"),
                {"individual_liquidation_request": completed_request},
                "개별청산/보유없음 완료",
            )
            result = {
                "ok": bool(persisted),
                "stage": "completed_no_holding",
                "runtime_status": str(state.get("status") or "RUNNING"),
                "orders_created": 0,
            }
            results.append(
                {
                    "stock_dir": str(stock_path),
                    "code": code,
                    "command_id": str(request.get("command_id") or ""),
                    **result,
                }
            )
            if persisted:
                processed += 1
            else:
                blocked_count += 1
            continue

        if regular_end_pending_cleanup:
            method = boundary_method
            command_id = str(
                state.get("operation_command_id")
                or boundary.get("operation_identity")
                or state.get("trade_started_at")
                or "LEGACY"
            ).strip()
            requested_at = str(
                snapshot.get("captured_at")
                or state.get("trade_started_at")
                or now_text()
            ).strip()
            reason = "REGULAR_END_PENDING_CLEANUP"
        elif boundary_admitted or liquidation_carryover_ready:
            method = boundary_method
            command_id = (
                str(request.get("command_id") or "").strip()
                if individual_requested
                else ""
            )
            if not command_id:
                operation_identity = str(
                    boundary.get("operation_identity")
                    or state.get("trade_started_at")
                    or "LEGACY"
                ).strip()
                command_id = f"LIQUIDATION_BOUNDARY_{code}_{operation_identity}"
            requested_at = str(
                (request.get("requested_at") if individual_requested else "")
                or state.get("early_close_requested_at")
                or snapshot.get("captured_at")
                or state.get("trade_started_at")
                or now_text()
            ).strip()
            reason = "LIQUIDATION_BOUNDARY"
        elif individual_requested:
            method = str(request.get("method") or "").strip()
            command_id = str(request.get("command_id") or "").strip()
            requested_at = str(request.get("requested_at") or "").strip()
            reason = "INDIVIDUAL_LIQUIDATION"
            continue
        elif early_requested:
            if str(state.get("status") or "").strip().upper() in {
                "EARLY_CLOSED",
                "REVIEW_REQUIRED",
            }:
                continue
            method = normalize_direct_close_policy_alias(
                state.get("early_close_method")
            )
            command_id = str(state.get("operation_command_id") or "").strip()
            requested_at = str(
                state.get("early_close_requested_at") or ""
            ).strip()
            reason = "EARLY_CLOSE"
        else:
            continue

        recovery = _production_recovery_gate(
            window,
            code,
            f"{reason}_CONTINUE",
        )
        if recovery is not None and recovery.allowed is not True:
            _log_recovery_block(
                window,
                code=code,
                caller_name=f"{reason}_CONTINUE",
                recovery=recovery,
                routine_instance_id=routine_instance_id,
            )
            results.append(
                {
                    "stock_dir": str(stock_path),
                    "code": code,
                    "command_id": command_id,
                    "ok": False,
                    "stage": "production_recovery",
                    "runtime_status": "UNCHANGED",
                    "blocked_reasons": [recovery.reason_code],
                }
            )
            blocked_count += 1
            continue

        try:
            result = _start_close_liquidation_execution(
                window,
                stock_dir=stock_path,
                code=code,
                name=name,
                method=method,
                command_id=command_id,
                requested_at=requested_at,
                routine_instance_id=routine_instance_id,
                reason=reason,
                regular_end_reached=bool(boundary.get("ended")),
            )
        except Exception as exc:
            LOGGER.exception(
                "Close/liquidation execution failed: stock=%s command=%s",
                code,
                command_id,
            )
            result = {
                "ok": False,
                "stage": "execution_exception",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": [f"{type(exc).__name__}: {exc}"],
            }
        if reason == "LIQUIDATION_BOUNDARY":
            persisted = _persist_liquidation_execution_result(
                window,
                stock_dir=stock_path,
                code=code,
                name=name,
                boundary=boundary,
                result=result,
                command_id=command_id,
            )
            if not persisted:
                result = {
                    **result,
                    "ok": False,
                    "stage": "runtime_state_write",
                    "blocked_reasons": ["liquidation Runtime state write failed"],
                }
            else:
                result = {
                    **result,
                    "completion_check_result": check_global_close_completion_after_durable_update(
                        source=SOURCE_LIQUIDATION_DURABLE_UPDATE,
                    ),
                }
        elif reason == "EARLY_CLOSE":
            persisted = _persist_early_close_execution_result(
                window,
                stock_dir=stock_path,
                code=code,
                name=name,
                result=result,
            )
            if not persisted:
                result = {
                    **result,
                    "ok": False,
                    "stage": "runtime_state_write",
                    "blocked_reasons": ["early close Runtime state write failed"],
                }
            else:
                result = {
                    **result,
                    "completion_check_result": check_global_close_completion_after_durable_update(
                        source=SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
                    ),
                }
        results.append(
            {
                "stock_dir": str(stock_path),
                "code": code,
                "command_id": command_id,
                **result,
            }
        )
        if result.get("ok") is True:
            processed += 1
        else:
            blocked_count += 1

    return {
        "processed": processed,
        "blocked": blocked_count,
        "results": results,
        "blocked_reasons": [],
    }


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
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



class ProfitLossEarlyCloseDialog(QDialog):
    """우클릭 조기마감 > 손/익절 입력창.

    환경설정의 입력 방식과 맞춰 한 줄에
    "익절/손절 + [익절] / - [손절]" 형태로 입력한다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("손/익절 조기마감")
        self.resize(330, 120)

        layout = QVBoxLayout()
        guide = QLabel("익절/손절 비율(%)을 입력하세요.")
        layout.addWidget(guide)

        row_layout = QHBoxLayout()
        self.enabled_check = QCheckBox("익절/손절")
        self.enabled_check.setChecked(True)
        self.enabled_check.setEnabled(False)
        row_layout.addWidget(self.enabled_check)

        row_layout.addWidget(QLabel("+"))
        self.profit_edit = QLineEdit()
        self.profit_edit.setPlaceholderText("입력")
        self.profit_edit.setMaximumWidth(70)
        row_layout.addWidget(self.profit_edit)

        row_layout.addWidget(QLabel("/ -"))
        self.loss_edit = QLineEdit()
        self.loss_edit.setPlaceholderText("입력")
        self.loss_edit.setMaximumWidth(70)
        row_layout.addWidget(self.loss_edit)
        row_layout.addStretch(1)
        layout.addLayout(row_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("확인")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self) -> tuple[str, str]:
        return self.profit_edit.text().strip(), self.loss_edit.text().strip()

    def _positive_number_from_text(self, value: str) -> float:
        # 입력창 앞에 + / - 라벨이 있으므로 사용자가 부호를 입력해도 절댓값으로 해석한다.
        return abs(float(value))

    def accept(self) -> None:
        profit_text, loss_text = self.values()
        if not profit_text and not loss_text:
            QMessageBox.warning(
                self,
                "입력 필요",
                "익절 또는 손절 비율 중 최소 1개 값을 입력하세요.",
            )
            self.profit_edit.setFocus()
            return

        for label, value, widget in [
            ("익절", profit_text, self.profit_edit),
            ("손절", loss_text, self.loss_edit),
        ]:
            if not value:
                continue
            try:
                number = self._positive_number_from_text(value)
            except ValueError:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 숫자로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return
            if number <= 0:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 0보다 큰 값으로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return

        super().accept()


def auto_trade_apply_selected_individual_liquidation_method(
    window,
    method: str,
    minutes_before_regular_close: str = "5",
    *,
    show_error_dialog: bool = True,
    now_dt: datetime | None = None,
) -> dict[str, object]:
    dialog_parent = operation_dialog_parent(window)
    normalized_method = short_close_method_text(method)
    if normalized_method not in {"시장가", "현재가", "이월"}:
        return {"ok": False, "message": "지원하지 않는 개별청산 방식입니다."}

    selected = window.selected_stock_infos()
    if not selected:
        return {"ok": False, "message": "개별청산할 종목을 선택하세요."}

    minutes = (
        "" if normalized_method == "이월"
        else str(minutes_before_regular_close).strip() or "5"
    )
    setting_label = (
        "이월" if normalized_method == "이월"
        else f"{minutes}분/{normalized_method}"
    )
    completed: list[str] = []
    failed: list[str] = []
    failure_messages: list[str] = []
    liquidation_setting_locked = False
    policy_blocked_failure = False
    for stock_dir, code, name in selected:
        config = read_json_dict(stock_dir / "config.json")
        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        recovery_decisions: dict[str, object] = {}

        def inspect_recovery(stock_code: str, caller_name: str):
            recovery = _production_recovery_gate(window, stock_code, caller_name)
            if recovery is not None:
                recovery_decisions[stock_code] = recovery
            return recovery

        command_result = execute_individual_liquidation_command(
            window,
            stock_dir,
            code,
            method=normalized_method,
            minutes_before_regular_close=minutes,
            source="우클릭",
            now_dt=now_dt,
            project_root=PROJECT_ROOT,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            recovery_inspector=inspect_recovery,
            transition_guard=evaluate_production_transition,
            requested_at_factory=now_text,
        )
        recovery = recovery_decisions.get(code)
        if (
            command_result.availability is not None
            and command_result.availability.recovery_blocked
            and recovery is not None
        ):
            policy_blocked_failure = True
            _log_recovery_block(
                window,
                code=code,
                caller_name="INDIVIDUAL_LIQUIDATION_REQUEST",
                recovery=recovery,
                routine_instance_id=routine_instance_id,
            )
            reason = _close_liquidation_user_message(command_result.reason_code)
            failed.append(f"{code} {name}({reason})")
            failure_messages.append(reason)
            _append_close_liquidation_block_event(
                stock_dir,
                code,
                name,
                requested_action="INDIVIDUAL_LIQUIDATION_SETTING",
                reason_code=command_result.reason_code,
                source="gui_auto_trade_close.auto_trade_apply_selected_individual_liquidation_method",
                requested_policy={
                    "minutes_before_regular_close": minutes,
                    "method": normalized_method,
                },
            )
            continue
        if command_result.ok and command_result.changed:
            completed.append(f"{code} {name}")
            append_stock_log(
                stock_dir,
                "GUI",
                f"개별청산 정책 설정: {setting_label}",
            )
        else:
            if command_result.reason_code == "LIQUIDATION_TIME_WINDOW_ENTERED":
                reason = _close_liquidation_user_message(command_result.reason_code)
                liquidation_setting_locked = True
            else:
                reason = _close_liquidation_user_message(command_result.reason_code)
            transition = command_result.operation_result
            transition_blocked = bool(
                transition is not None
                and hasattr(transition, "allowed")
                and getattr(transition, "allowed", False) is not True
            )
            if (
                command_result.reason_code != "LIQUIDATION_TIME_WINDOW_ENTERED"
                and transition_blocked
            ):
                reason = _close_liquidation_user_message(
                    getattr(transition, "reason_code", command_result.reason_code)
                )
            if getattr(command_result, "allowed", None) is False or transition_blocked:
                policy_blocked_failure = True
                _append_close_liquidation_block_event(
                    stock_dir,
                    code,
                    name,
                    requested_action="INDIVIDUAL_LIQUIDATION_SETTING",
                    reason_code=command_result.reason_code,
                    source="gui_auto_trade_close.auto_trade_apply_selected_individual_liquidation_method",
                    requested_policy={
                        "minutes_before_regular_close": minutes,
                        "method": normalized_method,
                    },
                )
            failed.append(f"{code} {name}({reason})")
            failure_messages.append(reason)

    if not completed:
        if liquidation_setting_locked:
            show_toast(dialog_parent, "청산설정변경이 불가능합니다.", duration_ms=2500)
        elif policy_blocked_failure:
            show_toast(
                dialog_parent,
                failure_messages[0] if failure_messages else "개별청산 설정을 변경할 수 없습니다.",
                duration_ms=2500,
            )
        elif failed and show_error_dialog:
            QMessageBox.critical(
                dialog_parent,
                "개별청산 요청 오류",
                "개별청산 요청을 운영 상태에 반영하지 못했습니다.\n\n"
                + "\n".join(failed),
            )
        return {
            "ok": False,
            "completed_count": 0,
            "failed_count": len(failed),
            "message": failure_messages[0] if failure_messages else "개별청산 요청에 실패했습니다.",
        }

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    refresh_auto_trade_views(window)
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window.update_action_buttons()
    window.statusBarMessage(
        f"개별청산 설정 완료: {setting_label} / 대상 {len(completed)}개"
    )
    if failed and show_error_dialog:
        if policy_blocked_failure:
            show_toast(
                dialog_parent,
                failure_messages[0] if failure_messages else "일부 종목의 개별청산 설정을 변경할 수 없습니다.",
                duration_ms=2500,
            )
        else:
            QMessageBox.warning(
                dialog_parent,
                "개별청산 일부 실패",
                "\n".join(failed),
            )
    return {
        "ok": True,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "message": failure_messages[0] if failure_messages else "",
    }



def auto_trade_apply_selected_early_close_default(window) -> None:
    """외부 조기마감 버튼: 좌측 선택 scope의 현재 등록 종목에 디폴트값을 적용한다."""
    method = str(operation_policy_section("early_close").get("method", "루틴")).strip() or "루틴"
    selected = _early_close_scope_stock_infos(window)
    auto_trade_apply_selected_early_close(
        window,
        method,
        source="디폴트값",
        selected=selected,
    )


def _early_close_scope_stock_infos(window) -> list[tuple[Path, str, str]]:
    result: list[tuple[Path, str, str]] = []
    for stock_dir in _selected_instance_stock_dirs(window):
        code, name = parse_stock_folder_name(stock_dir.name)
        if not code:
            config = read_json_dict(stock_dir / "config.json")
            code = str(config.get("code") or config.get("stock_code") or "").strip()
            name = str(config.get("name") or config.get("stock_name") or "").strip()
        if not code:
            continue
        result.append((stock_dir, code, name))
    return result


def _early_close_scope_display_name(window) -> str:
    """Return the operator-visible name of the active early-close scope."""
    if getattr(window, "_all_stocks_scope_active", False) is True:
        return "전체운영"

    metadata_reader = getattr(window, "current_selected_routine_row_metadata", None)
    try:
        metadata = metadata_reader() if callable(metadata_reader) else None
    except Exception:
        metadata = None
    if isinstance(metadata, dict):
        row_kind = str(metadata.get("row_kind") or "").strip()
        if row_kind == "definition":
            group_name = str(metadata.get("definition_name") or "").strip()
            if group_name:
                return group_name
        if row_kind in {"instance", "stock"}:
            routine_name = str(metadata.get("instance_name") or "").strip()
            if routine_name:
                return routine_name

    routine_name_reader = getattr(window, "current_selected_routine_name", None)
    try:
        routine_name = (
            str(routine_name_reader() or "").strip()
            if callable(routine_name_reader)
            else ""
        )
    except Exception:
        routine_name = ""
    return routine_name or "전체운영"


def _early_close_confirmation_message(window, target_count: int) -> str:
    scope_name = _early_close_scope_display_name(window)
    return f"{scope_name} {max(0, int(target_count))}종목을 조기마감합니다. 진행하시겠습니까?"


def _kiwoom_server_login_block_message(window) -> str:
    parent = persistent_feature_owner(window)
    api = getattr(parent, "kiwoom_api", None)
    checker = getattr(api, "is_connected", None)
    try:
        connected = callable(checker) and checker() is True
    except Exception:
        connected = False
    return "" if connected else "키움 서버에 로그인되어 있지 않습니다."



def auto_trade_apply_selected_early_close_profit_loss(window) -> None:
    """우클릭 조기마감 > 손/익절: 익절/손절 비율을 분리 입력 후 전환한다."""
    dialog = ProfitLossEarlyCloseDialog(operation_dialog_parent(window))
    dialog_result = dialog.exec_()
    profit_text, loss_text = dialog.values()
    accepted = dialog_result == QDialog.Accepted
    details: dict[str, object] = {
        "interaction_type": "INPUT",
        "prompt_key": "PROFIT_LOSS_EARLY_CLOSE",
        "prompt_title": "손/익절 조기마감",
        "prompt_summary": "익절/손절 비율을 적용한 조기마감",
        "offered_options": ["확인", "취소"],
        "selected_option": "확인" if accepted else "취소",
    }
    if accepted:
        input_value: dict[str, float] = {}
        if profit_text:
            input_value["profit_percent"] = abs(float(profit_text))
        if loss_text:
            input_value["loss_percent"] = abs(float(loss_text))
        details["input_value"] = input_value
    append_production_event(
        "OPERATOR_OPERATION_DECISION",
        result="ACCEPTED" if accepted else "CANCELLED",
        source="gui_auto_trade_close.auto_trade_apply_selected_early_close_profit_loss",
        target_type="STOCK_SELECTION",
        target_name="손익비율 조기마감 대상",
        details=details,
    )
    if not accepted:
        return

    auto_trade_apply_selected_early_close(
        window,
        "손/익절",
        source="우클릭",
        extra_policy={
            "profit_percent": profit_text,
            "loss_percent": loss_text,
        },
    )



def _read_runtime_order_queue() -> tuple[list[dict[str, object]], str]:
    if not ORDER_QUEUE_PATH.exists():
        return [], ""
    try:
        data = json.loads(ORDER_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"queue_read_failed:{exc}"
    if not isinstance(data, dict):
        return [], "queue_root_invalid"
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return [], "queue_orders_invalid"
    if not all(isinstance(order, dict) for order in orders):
        return [], "queue_order_invalid"
    return orders, ""


def _nested_text_values(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(_nested_text_values(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(_nested_text_values(child))
        return result
    return [str(value or "").strip()]


def _record_mentions_code(record: dict[str, object], code: str) -> bool:
    if not code:
        return False
    return any(text == code for text in _nested_text_values(record))


def _truthy_record_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "none", "no", "n"}


def _queue_execution_evidence_for_code(code: str) -> str:
    records, read_error = _read_runtime_order_queue()
    if read_error:
        return read_error
    for record in records:
        if not _record_mentions_code(record, code):
            continue
        status = str(record.get("status") or "").strip().upper()
        if status == "ORDER_QUEUED":
            return "ORDER_QUEUED"
        for key in (
            "dispatch_id",
            "claim_token",
            "claimed_at",
            "send_order_attempt_id",
            "send_order_called_at",
            "broker_order_no",
            "broker_status",
        ):
            if str(record.get(key) or "").strip():
                return key
        for key in ("send_order_called", "execution_enabled"):
            if _truthy_record_value(record.get(key)):
                return key
    return ""


def _order_is_after_early_close(order: dict[str, object], requested_at: str) -> tuple[bool, str]:
    order_dt = order_datetime(order)
    if order_dt is None:
        return False, "order_time_unknown"
    try:
        requested_dt = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except Exception:
        try:
            requested_dt = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False, "early_close_time_unknown"
    if order_dt.tzinfo is not None and requested_dt.tzinfo is None:
        order_dt = order_dt.replace(tzinfo=None)
    if order_dt.tzinfo is None and requested_dt.tzinfo is not None:
        requested_dt = requested_dt.replace(tzinfo=None)
    return order_dt >= requested_dt, ""


def _stock_order_execution_evidence(stock_dir: Path, requested_at: str) -> str:
    for order in read_orders_data(stock_dir / "orders.json"):
        side = str(order_value(order, ["side", "order_side", "구분", "매매구분"], "")).strip().upper()
        if side not in {"SELL", "매도", "S"}:
            continue
        is_after, time_error = _order_is_after_early_close(order, requested_at)
        evidence_fields = (
            "id",
            "order_id",
            "broker_order_no",
            "dispatch_id",
            "send_order_attempt_id",
            "send_order_called_at",
            "send_order_status",
        )
        has_identity = any(str(order.get(key) or "").strip() for key in evidence_fields)
        has_send_order = _truthy_record_value(order.get("send_order_called"))
        filled_qty = safe_int_value(order_value(order, ["filled_qty", "executed_qty", "체결수량"], 0), 0)
        pending_qty, pending_unknown = order_current_pending_qty(order)
        if time_error and (has_identity or has_send_order or filled_qty > 0 or pending_qty > 0 or pending_unknown):
            return time_error
        if not is_after:
            continue
        if has_identity:
            return "order_identity"
        if has_send_order:
            return "send_order_called"
        if filled_qty > 0:
            return "filled_qty"
        if pending_unknown or pending_qty > 0:
            return "pending_order"
    return ""


def _stock_sell_fill_qty_after(stock_dir: Path, requested_at: str) -> int:
    total = 0
    for order in read_orders_data(stock_dir / "orders.json"):
        side = str(
            order_value(order, ["side", "order_side", "구분", "매매구분"], "")
        ).strip().upper()
        if side not in {"SELL", "매도", "S"}:
            continue
        is_after, time_error = _order_is_after_early_close(order, requested_at)
        if time_error or not is_after:
            continue
        total += max(
            0,
            safe_int_value(
                order_value(order, ["filled_qty", "executed_qty", "체결수량"], 0),
                0,
            ),
        )
    return total


def auto_trade_return_selected_early_close_to_auto(
    window,
    *,
    now_dt: datetime | None = None,
) -> dict[str, object]:
    """Withdraw EARLY intervention back to the frozen SCHEDULED timeline."""

    notify = getattr(window, "showAutoTradePopupMessage", None)
    if not callable(notify):
        notify = window.statusBarMessage
    selected = window.selected_stock_infos()
    blocked_message = "현재 상태는 자동마감 복귀 대상이 아닙니다."
    if not selected:
        notify(blocked_message)
        return {"ok": False, "reason": "NO_SELECTION"}

    prepared: list[tuple[Path, str, str, dict[str, object]]] = []
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    for stock_dir, code, name in selected:
        state = read_json_dict(Path(stock_dir) / "state.json")
        snapshot = active_operation_policy_snapshot(state)
        schedule = snapshot.get("operation_schedule") if isinstance(snapshot, dict) else None
        schedule = schedule if isinstance(schedule, dict) else {}
        requested_at = str(state.get("early_close_requested_at") or "").strip()
        if (
            normalize_operation_mode(snapshot.get("operation_mode", "")) != "SCHEDULED"
            or str(state.get("status") or "").strip().upper()
            not in {"EARLY_CLOSE", "EARLY_CLOSING"}
            or not requested_at
            or str(state.get("auto_close_requested_at") or "").strip()
            or current_seconds
            >= seconds_from_hhmmss(schedule.get("end_buy_time"), "00:00:00")
            or _stock_sell_fill_qty_after(Path(stock_dir), requested_at) > 0
        ):
            notify(blocked_message)
            return {"ok": False, "reason": "EARLY_AUTO_RETURN_BLOCKED"}
        prepared.append((Path(stock_dir), code, name, state))

    for stock_dir, code, name, state in prepared:
        method = short_close_method_text(state.get("early_close_method"))
        if method == "루틴":
            metadata = clear_early_close_runtime_metadata_only(dict(state))
            metadata["close_transition_pending"] = None
            metadata["early_auto_returned_at"] = now_text()
            saved = window.update_stock_status(
                stock_dir, code, name, "RUNNING", metadata, "마감변경/자동마감"
            )
        else:
            saved = window.update_stock_status(
                stock_dir,
                code,
                name,
                str(state.get("status") or "EARLY_CLOSE"),
                {
                    "close_transition_pending": {
                        "method": "AUTO_TIMELINE",
                        "source": "우클릭",
                        "requested_at": now_text(),
                    }
                },
                "마감변경/자동마감 취소확정대기",
            )
        if not saved:
            notify("자동마감 복귀 상태를 저장하지 못했습니다.")
            return {"ok": False, "reason": "RUNTIME_STATE_WRITE"}
    refresh_auto_trade_views(window)
    notify("자동마감 일정으로 복귀했습니다.")
    return {"ok": True, "count": len(prepared)}


def _early_close_cancel_irreversible_evidence(
    stock_dir: Path,
    code: str,
    state: dict[str, object],
) -> str:
    requested_at = str(state.get("early_close_requested_at") or "").strip()
    if str(state.get("close_routine_final_sell_ordered_at") or "").strip():
        return "final_sell_ordered_at"
    if bool(state.get("close_routine_final_sell_ordered", False)):
        return "final_sell_ordered"
    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return "pending_unknown"
    if safe_int_value(buy_pending_qty, 0) > 0 or safe_int_value(sell_pending_qty, 0) > 0:
        return "pending_order"
    queue_evidence = _queue_execution_evidence_for_code(code)
    if queue_evidence:
        return queue_evidence
    order_evidence = _stock_order_execution_evidence(stock_dir, requested_at)
    if order_evidence:
        return order_evidence
    return ""


def auto_trade_cancel_selected_early_close(window) -> None:
    """우클릭 조기마감 > 취소.

    실행 증거가 없는 조기마감 명령만 shared application command를 통해
    기존 NORMAL 적용 경로로 철회한다. 안전성을 확인할 수 없으면 fail-closed로 차단한다.
    """
    blocked_message = "현재 상태는 마감정책 취소 대상이 아닙니다."
    success_message = "마감정책이 취소되었습니다."
    notify = getattr(window, "showAutoTradePopupMessage", None)
    if not callable(notify):
        notify = window.statusBarMessage
    selected = window.selected_stock_infos()
    if not selected:
        notify(blocked_message)
        return

    states: list[tuple[Path, str, str, str]] = []
    block_reasons: list[tuple[Path, str, str, str]] = []

    def inspect_recovery(stock_code: str, caller_name: str):
        return _production_recovery_gate(window, stock_code, caller_name)

    for stock_dir, code, name in selected:
        availability = inspect_close_liquidation_availability(
            window,
            stock_dir,
            code,
            intent=EARLY_CLOSE_CANCEL,
            recovery_inspector=inspect_recovery,
            irreversible_evidence_reader=_early_close_cancel_irreversible_evidence,
        )
        if not availability.allowed:
            block_reasons.append(
                (stock_dir, code, name, availability.reason_code)
            )
            continue
        states.append((stock_dir, code, name, availability.command_id))

    if block_reasons or not states:
        for stock_dir, code, name, reason in block_reasons:
            append_stock_log(stock_dir, "GUI", f"조기마감 취소 차단: {code} {name} / {reason}")
        notify(blocked_message)
        return

    completed: list[str] = []
    failed: list[tuple[Path, str, str, str]] = []
    for stock_dir, code, name, expected_command_id in states:
        result = execute_early_close_cancel_command(
            window,
            stock_dir,
            code,
            source="우클릭",
            project_root=PROJECT_ROOT,
            recovery_inspector=inspect_recovery,
            expected_command_id=expected_command_id,
            irreversible_evidence_reader=_early_close_cancel_irreversible_evidence,
        )
        if not result.ok or not result.changed:
            failed.append(
                (stock_dir, code, name, result.reason_code or "cancel_failed")
            )
            continue
        completed.append(f"{code} {name}")
        append_stock_log(stock_dir, "GUI", "조기마감 취소 완료")

    if failed or not completed:
        for stock_dir, code, name, reason in failed:
            append_stock_log(stock_dir, "GUI", f"조기마감 취소 차단: {code} {name} / {reason}")
        notify(blocked_message)
        return

    append_changelog("UPDATE", "state.json", f"조기마감 취소: {' / '.join(completed)}")
    refresh_auto_trade_views(window)
    window.stock_table.viewport().update()
    window.stock_table.repaint()
    notify(success_message)


def auto_trade_apply_selected_early_close(
    window,
    method: str,
    source: str = "우클릭",
    extra_policy: dict[str, object] | None = None,
    selected: list[tuple[Path, str, str]] | None = None,
    *,
    show_error_dialog: bool = True,
    show_result_toast: bool = True,
    show_confirmation: bool = True,
) -> dict[str, object]:
    """선택 종목에 조기마감 명령을 적용한다.

    조기마감은 보유수량을 0으로 만드는 1차 리셋 절차다.
    대상 기준은 보유수량이며, 미수/미도/미체결은 대상 판정 기준으로 쓰지 않는다.
    루틴 방식 조기마감은 첫 매도신호 전까지 매수/매도 신호를 허용하고,
    첫 매도주문 접수 이후 추가 주문 차단은 메인 주문판정 계층에서 처리한다.
    """
    dialog_parent = operation_dialog_parent(window)
    selected = selected if selected is not None else window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()
    scope_name = _early_close_scope_display_name(window)

    def show_ok_message(icon, title: str, message: str) -> None:
        if not show_error_dialog:
            return
        box = QMessageBox(dialog_parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(message)
        ok_button = box.addButton("확인", QMessageBox.AcceptRole)
        box.setDefaultButton(ok_button)
        box.exec_()

    if not selected:
        show_ok_message(
            QMessageBox.Warning,
            "선택 오류",
            "조기마감할 종목을 1개 이상 선택하세요.",
        )
        return {"ok": False, "message": "조기마감할 종목을 선택하세요."}

    method_text = normalize_direct_close_policy_alias(method) or "루틴"

    close_targets: list[tuple[Path, str, str]] = []
    skipped: list[str] = []
    blocked_user_messages: list[str] = []
    early_close_applied_count = 0
    preflight_recovery_decisions: dict[str, object] = {}

    def inspect_recovery(stock_code: str, caller_name: str):
        recovery = _production_recovery_gate(window, stock_code, caller_name)
        if recovery is not None:
            preflight_recovery_decisions[stock_code] = recovery
        return recovery

    for stock_dir, code, name in selected:
        availability = inspect_close_liquidation_availability(
            window,
            stock_dir,
            code,
            intent=EARLY_CLOSE_REQUEST,
            requested_method=method_text,
            recovery_inspector=inspect_recovery,
        )
        if availability.allowed:
            close_targets.append((stock_dir, code, name))
            continue
        recovery = preflight_recovery_decisions.get(code)
        if availability.recovery_blocked and recovery is not None:
            config = read_json_dict(stock_dir / "config.json")
            config = config if isinstance(config, dict) else {}
            _log_recovery_block(
                window,
                code=code,
                caller_name="EARLY_CLOSE_REQUEST",
                recovery=recovery,
                routine_instance_id=str(
                    config.get("assigned_routine_instance_id") or ""
                ).strip(),
            )
            reason_text = _close_liquidation_user_message(
                availability.reason_code
            )
        else:
            reason_text = _close_liquidation_user_message(
                availability.reason_code
            )
        skipped.append(f"{code} {name}({reason_text})")
        blocked_user_messages.append(reason_text)
        _append_close_liquidation_block_event(
            stock_dir,
            code,
            name,
            requested_action="EARLY_CLOSE_REQUEST",
            reason_code=availability.reason_code,
            source="gui_auto_trade_close.auto_trade_apply_selected_early_close",
            requested_policy={"method": method_text},
        )

    if not close_targets:
        toast_message = (
            blocked_user_messages[0]
            if blocked_user_messages
            else "조기마감 대상이 없습니다."
        )
        if show_result_toast:
            show_toast(dialog_parent, toast_message, duration_ms=2500)
        return {
            "ok": False,
            "completed_count": 0,
            "failed_count": len(skipped),
            "message": toast_message,
        }

    if close_targets and show_confirmation:
        box = QMessageBox(dialog_parent)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("조기마감 확인")
        box.setText(_early_close_confirmation_message(window, len(close_targets)))
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        accepted = box.clickedButton() == proceed_button
        single_target = close_targets[0] if len(close_targets) == 1 else None
        append_production_event(
            "OPERATOR_OPERATION_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_close.auto_trade_apply_selected_early_close",
            target_type="STOCK_SELECTION",
            target_id=single_target[1] if single_target is not None else None,
            target_name=single_target[2] if single_target is not None else scope_name,
            stock_code=single_target[1] if single_target is not None else None,
            stock_name=single_target[2] if single_target is not None else None,
            routine=routine_name or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EARLY_CLOSE_CONFIRM",
                "prompt_title": "조기마감 확인",
                "prompt_summary": f"{scope_name} 조기마감",
                "offered_options": ["진행", "취소"],
                "selected_option": "진행" if accepted else "취소",
                "method": method_text,
                "target_count": len(close_targets),
            },
        )
        if not accepted:
            window.statusBarMessage("조기마감 취소")
            return {"ok": False, "cancelled": True, "message": "조기마감 취소"}

    completed: list[str] = []
    for stock_dir, code, name in close_targets:
        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()
        recovery_decisions: dict[str, object] = {}

        def final_recovery(stock_code: str, caller_name: str):
            recovery = _production_recovery_gate(window, stock_code, caller_name)
            if recovery is not None:
                recovery_decisions[stock_code] = recovery
            return recovery

        application_result = execute_early_close_request_command(
            window,
            stock_dir,
            code,
            method=method_text,
            source=source,
            extra_policy=dict(extra_policy or {}),
            project_root=PROJECT_ROOT,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            recovery_inspector=final_recovery,
            transition_guard=evaluate_production_transition,
            requested_at_factory=now_text,
        )
        recovery = recovery_decisions.get(code)
        if (
            application_result.availability is not None
            and application_result.availability.recovery_blocked
            and recovery is not None
        ):
            _log_recovery_block(
                window,
                code=code,
                caller_name="EARLY_CLOSE_REQUEST",
                recovery=recovery,
                routine_instance_id=str(
                    config.get("assigned_routine_instance_id") or ""
                ).strip(),
            )
            skipped.append(
                f"{code} {name}({_close_liquidation_user_message(application_result.reason_code)})"
            )
            _append_close_liquidation_block_event(
                stock_dir,
                code,
                name,
                requested_action="EARLY_CLOSE_REQUEST",
                reason_code=application_result.reason_code,
                source="gui_auto_trade_close.auto_trade_apply_selected_early_close",
                requested_policy={"method": method_text},
            )
            continue
        if not application_result.ok:
            reason = application_result.reason_code or "명령 적용 실패"
            reason_text = _close_liquidation_user_message(reason)
            skipped.append(f"{code} {name}({reason_text})")
            if getattr(application_result, "allowed", None) is False:
                _append_close_liquidation_block_event(
                    stock_dir,
                    code,
                    name,
                    requested_action="EARLY_CLOSE_REQUEST",
                    reason_code=reason,
                    source="gui_auto_trade_close.auto_trade_apply_selected_early_close",
                    requested_policy={"method": method_text},
                )
            continue

        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        intent_result = application_result.operation_result
        intent_result = intent_result if isinstance(intent_result, dict) else {}
        command_result = intent_result.get("command_result")
        if command_result is None:
            skipped.append(
                f"{code} {name}({intent_result.get('reason') or '명령 적용 실패'})"
            )
            continue

        saved_state = read_json_dict(stock_dir / "state.json")
        transition_requested_at = str(
            saved_state.get("early_close_requested_at") or now_text()
        ).strip()
        has_close_progress_qty = bool(
            application_result.availability
            and application_result.availability.holding_qty > 0
        )
        execution = _start_close_liquidation_execution(
            window,
            stock_dir=stock_dir,
            code=code,
            name=name,
            method=method_text,
            command_id=command_result.command_id,
            requested_at=str(
                saved_state.get("early_close_requested_at")
                or transition_requested_at
            ).strip(),
            routine_instance_id=routine_instance_id,
            reason="EARLY_CLOSE",
        )
        persisted = _persist_early_close_execution_result(
            window,
            stock_dir=stock_dir,
            code=code,
            name=name,
            result=execution,
        )
        if not persisted:
            skipped.append(f"{code} {name}(조기마감 상태 저장 실패)")
            continue
        execution = {
            **execution,
            "completion_check_result": check_global_close_completion_after_durable_update(
                source=SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
            ),
        }
        if execution.get("ok") is not True:
            skipped.append(
                f"{code} {name}("
                f"{execution.get('stage') or '실행 연결 실패'})"
            )
            continue

        completed.append(f"{code} {name}")
        early_close_applied_count += 1
        if application_result.changed:
            log_reason = (
                f"조기마감/{method_text}/{execution.get('stage')}"
                if has_close_progress_qty
                else "조기마감 대상 없음"
            )
            append_stock_log(stock_dir, "GUI", f"자동매매 상태 변경: {log_reason}")

    if completed:
        changelog_parts: list[str] = []
        if completed:
            changelog_parts.append(f"조기마감({method_text}): {' / '.join(completed)}")
        if skipped:
            changelog_parts.append(f"제외: {' / '.join(skipped)}")
        append_changelog(
            "UPDATE",
            "state.json",
            f"조기마감 상태 변경: {routine_name or '전체'} -> {' | '.join(changelog_parts)}",
        )

    refresh_auto_trade_views(window)
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    message = f"조기마감 적용: {len(completed)}개"
    if skipped:
        message += f" / 제외 {len(skipped)}개"
    window.statusBarMessage(message)
    if early_close_applied_count > 0:
        toast_message = f"{early_close_applied_count}종목을 조기마감 적용하였습니다."
    elif not close_targets:
        toast_message = "조기마감 대상이 없습니다."
    elif skipped:
        toast_message = skipped[0]
    else:
        toast_message = "조기마감 대상이 없습니다."
    if show_result_toast:
        show_toast(dialog_parent, toast_message, duration_ms=2500)
    failure_message = ""
    if skipped:
        failure_message = str(skipped[0])
        if "(" in failure_message and failure_message.endswith(")"):
            failure_message = failure_message.split("(", 1)[1][:-1].strip()
    return {
        "ok": bool(completed) and not skipped,
        "completed_count": len(completed),
        "failed_count": len(skipped),
        "message": failure_message or ("" if completed else toast_message),
    }
