# -*- coding: utf-8 -*-
"""
gui_main_emergency_ops.py

MainWindow 긴급정지/정지해제 처리 전용 모듈.

정책:
- 전체 긴급정지: 전역 latch와 비-Review 종목만 정지하며 기존 Review lifecycle은 보존
- 선택 긴급정지: 선택 종목을 EMERGENCY_STOPPED 및 Review 대상으로 전환
- 정지해제: 무결성 확인 후 정상은 STOPPED, 문제 종목은 REVIEW_REQUIRED
- 자동복귀 금지: 정지해제 후에도 매매시작 상태로 자동 복귀하지 않음
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from gui_toast import show_toast
from gui_operation_ui_context import refresh_auto_trade_views
from gui_operation_ui_context import operation_dialog_parent
from gui_window_policy import persistent_feature_owner
from gui_common_utils import safe_int_value
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    is_emergency_stopped_state,
    is_review_required_state,
    operator_review_location,
    operator_review_reason,
)
from gui_auto_trade_run_control import (
    ORDER_QUEUE_PATH,
    _active_close_or_liquidation,
    _active_queue_reason,
)
from gui_order_utils import pending_order_integrity_issue_codes, pending_order_side_quantities
from gui_review_required_window import auto_trade_setting_server_mismatch_detected
from runtime_io import read_json_dict, read_orders_data
from gui_auto_trade_runtime import write_state_json
from gui_auto_trade_status_ops import append_changelog, append_stock_log, now_text
from gui_auto_trade_runtime import parse_stock_folder_name
from runtime_stock_state_mutation import mutate_runtime_stock_state
from gui_base_stock_service import update_base_stock_routines
from operation_policy_gate import read_operation_state, write_global_emergency_stop_state
from event_journal_production import (
    append_production_event,
    observe_owner_failure_transition,
)
from production_recovery_state_registry import (
    check_production_recovery_gate,
    production_recovery_registry,
)
from execution_queue_writer import read_execution_queue_records

REVIEW_RETURN_ALLOWED = "ALLOWED"
REVIEW_RETURN_BLOCKED = "BLOCKED"
EMERGENCY_SCOPE_GLOBAL = "GLOBAL"
EMERGENCY_SCOPE_SELECTED = "SELECTED"
EMERGENCY_SCOPE_UNKNOWN = "UNKNOWN"

RELEASED_NORMAL = "RELEASED_NORMAL"
RELEASED_TO_REVIEW = "RELEASED_TO_REVIEW"
BLOCKED_IN_EMERGENCY = "BLOCKED_IN_EMERGENCY"
RELEASE_FAILED = "FAILED"
RELEASE_SKIPPED = "SKIPPED"
_ACCOUNT_FUNDS_READY = "READY"
_PRE_EMERGENCY_NOT_READY_MESSAGE = "서버 연결 및 계좌 복구 확인 후 사용할 수 있습니다."


def emergency_scope(state: dict[str, object] | None) -> str:
    """Return the persisted emergency ownership scope or UNKNOWN."""
    value = str((state or {}).get("emergency_scope", "") or "").strip().upper()
    if value in {EMERGENCY_SCOPE_GLOBAL, EMERGENCY_SCOPE_SELECTED}:
        return value
    return EMERGENCY_SCOPE_UNKNOWN


def _evaluate_emergency_preflight(window) -> tuple[bool, str]:
    """Verify that preconditions to start emergency/selected stop are satisfied."""
    checker = getattr(window, "startup_recovery_session_ready", None)
    if callable(checker):
        try:
            if checker(refresh=False) is not True:
                return False, "RECOVERY_NOT_READY"
        except Exception:
            return False, "RECOVERY_NOT_READY"

    # Main monitoring operations are routed through an adapter.  Read the
    # connection/account evidence from its owning MainWindow while retaining
    # the adapter's recovery gate above.
    readiness_owner = getattr(window, "_window", window)
    api = getattr(readiness_owner, "kiwoom_api", None)
    is_connected: bool | None = None
    if api is not None:
        checker = getattr(api, "is_connected", None)
        try:
            is_connected = bool(callable(checker) and checker())
        except Exception:
            is_connected = False
        if not is_connected:
            return False, "LOGIN_NOT_READY"
        login_reader = getattr(api, "login_session_id", None)
        try:
            login_session_id = (
                str(login_reader() or "").strip() if callable(login_reader) else ""
            )
        except Exception:
            login_session_id = ""
        if not login_session_id:
            return False, "LOGIN_NOT_READY"

    selected_account = ""
    selector = getattr(readiness_owner, "selected_account_no", None)
    if callable(selector):
        try:
            selected_account = str(selector() or "").strip()
        except Exception:
            selected_account = ""
        if not selected_account:
            return False, "ACCOUNT_NOT_SELECTED"

    auth_states = getattr(readiness_owner, "_account_authentication_states", None)
    if isinstance(auth_states, dict) and selected_account:
        try:
            if str(auth_states.get(selected_account, "")).strip() != _ACCOUNT_FUNDS_READY:
                return False, "ACCOUNT_NOT_AUTHENTICATED"
        except Exception:
            return False, "ACCOUNT_NOT_AUTHENTICATED"

    return True, ""


def has_emergency_stopped_stock(window) -> bool:
    """MainWindow 전체 종목 중 긴급정지 상태가 하나라도 있는지 확인한다."""
    for stock_dir in window.all_runtime_stock_dirs():
        state = read_json_dict(stock_dir / "state.json")
        if is_emergency_stopped_state(state):
            return True
    return False


def update_emergency_button_state(window) -> None:
    """전역 긴급정지 상태에 따라 관제창 긴급정지 버튼 문구를 갱신한다."""
    button = getattr(window, "btn_emergency_stop", None)
    if button is None:
        return
    if read_operation_state().get("emergency_stop") is True:
        button.setText("정지해제")
    else:
        button.setText("긴급정지")


def emergency_review_reason_for_stock(stock_dir: Path) -> tuple[bool, str]:
    """정지해제 시 정상/검토관리 이동 기준을 판정한다."""
    state_path = stock_dir / "state.json"
    config_path = stock_dir / "config.json"
    orders_path = stock_dir / "orders.json"

    state = read_json_dict(state_path)
    config = read_json_dict(config_path)
    read_orders_data(orders_path)

    if not state_path.exists() or not isinstance(state, dict):
        return True, "state.json 이상"
    if not config_path.exists() or not isinstance(config, dict):
        return True, "config.json 이상"
    if not orders_path.exists():
        return True, "orders.json 누락"

    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    if holding_qty > 0:
        return True, "긴급정지 해제 시 보유잔량 존재"

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return True, "긴급정지 해제 시 미체결 매수 존재"
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return True, "긴급정지 해제 시 미체결 매도 존재"
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        issue_codes = pending_order_integrity_issue_codes(stock_dir, state)
        reason = "PENDING_ORDER_DATA_INTEGRITY"
        if issue_codes:
            reason += ": " + " / ".join(issue_codes)
        return True, reason

    data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
    if data_reasons:
        return True, str(data_reasons[0])

    return False, "긴급정지 해제 무결성 정상"


def _recovery_gate_for_emergency_release(window, stock_code: str):
    """Return the existing Production Recovery gate decision for either GUI path."""
    owners = [window]
    logical_owner = persistent_feature_owner(window)
    if logical_owner is not None and logical_owner is not window:
        owners.append(logical_owner)
    parent_getter = getattr(window, "parent", None)
    if callable(parent_getter):
        try:
            parent = parent_getter()
        except Exception:
            parent = None
        if parent is not None and parent is not window and parent not in owners:
            owners.append(parent)

    for owner in owners:
        checker = getattr(owner, "production_recovery_gate_for_stock", None)
        if not callable(checker):
            continue
        try:
            return checker(
                stock_code,
                caller_name="gui_main_emergency_ops.release_emergency_stop_target",
            )
        except Exception:
            return None

    for owner in owners:
        api = getattr(owner, "kiwoom_api", None)
        account_reader = getattr(owner, "selected_account_no", None)
        if api is None or not callable(account_reader):
            continue
        login_reader = getattr(api, "login_session_id", None)
        try:
            context = production_recovery_registry.snapshot()
            return check_production_recovery_gate(
                login_session_id=login_reader() if callable(login_reader) else "",
                account_no=account_reader(),
                trading_day=datetime.now().date().isoformat(),
                stock_code=stock_code,
                recovery_session_id=(
                    context.identity.recovery_session_id if context is not None else ""
                ),
                caller_name="gui_main_emergency_ops.release_emergency_stop_target",
            )
        except Exception:
            return None

    # Lightweight/non-MainWindow callers retain the existing readiness adapter.
    checker = getattr(window, "startup_recovery_session_ready", None)
    if callable(checker):
        try:
            return bool(checker(refresh=False))
        except Exception:
            return False
    return None


def emergency_release_common_guard(
    window,
    stock_dir: Path,
    stock_code: str,
    *,
    order_queue_path: str | Path | None = None,
    now_dt: datetime | None = None,
    check_recovery: bool = True,
) -> tuple[bool, str]:
    """Fail closed on canonical evidence that makes emergency release unsafe."""
    has_stock_problem, stock_reason = emergency_review_reason_for_stock(
        Path(stock_dir)
    )
    if has_stock_problem:
        return False, stock_reason

    state = read_json_dict(Path(stock_dir) / "state.json")
    if not isinstance(state, dict):
        return False, "state.json 이상"

    queue_reason = _active_queue_reason(
        stock_code,
        ORDER_QUEUE_PATH if order_queue_path is None else order_queue_path,
    )
    if queue_reason:
        return False, queue_reason

    if _active_close_or_liquidation(state, now_dt or datetime.now()):
        return False, "ACTIVE_CLOSE_OR_LIQUIDATION"

    if auto_trade_setting_server_mismatch_detected(state):
        return False, "SERVER_MISMATCH"

    if check_recovery:
        recovery = _recovery_gate_for_emergency_release(window, stock_code)
        if isinstance(recovery, bool):
            if not recovery:
                return False, "RECOVERY_NOT_READY"
        elif recovery is None:
            return False, "RECOVERY_NOT_READY"
        elif getattr(recovery, "allowed", False) is not True:
            return False, str(
                getattr(recovery, "reason_code", "RECOVERY_NOT_READY")
                or "RECOVERY_NOT_READY"
            )
    return True, ""


def global_emergency_release_preflight(
    window,
    *,
    order_queue_path: str | Path | None = None,
) -> tuple[bool, str]:
    """Read-only account/runtime readiness check before GLOBAL release writes."""
    checker = getattr(window, "startup_recovery_session_ready", None)
    if not callable(checker):
        return False, "RECOVERY_NOT_READY"
    try:
        if checker(refresh=False) is not True:
            return False, "RECOVERY_NOT_READY"
    except Exception:
        return False, "RECOVERY_NOT_READY"

    queue_snapshot = read_execution_queue_records(
        ORDER_QUEUE_PATH if order_queue_path is None else order_queue_path
    )
    if queue_snapshot.get("ok") is not True:
        return False, "RUNTIME_DAMAGED"
    return True, ""


def review_return_availability(
    window,
    stock_dir: Path,
    stock_code: str,
    *,
    state: dict[str, object] | None = None,
    state_issue_reason: str = "",
    order_queue_path: str | Path | None = None,
    now_dt: datetime | None = None,
) -> dict[str, object]:
    """Project whether a Review row may leave Review using current evidence."""

    stock_dir = Path(stock_dir)
    if str(state_issue_reason or "").strip():
        return {
            "availability": REVIEW_RETURN_BLOCKED,
            "reason": str(state_issue_reason).strip(),
        }

    state_path = stock_dir / "state.json"
    current_state = state if isinstance(state, dict) else read_json_dict(state_path)
    if not state_path.exists() or not isinstance(current_state, dict) or not current_state:
        return {
            "availability": REVIEW_RETURN_BLOCKED,
            "reason": "state.json 이상",
        }
    review_location = str(current_state.get("review_location", "") or "").strip()
    scope = emergency_scope(current_state)
    if read_operation_state().get("emergency_stop") is True:
        return {
            "availability": REVIEW_RETURN_BLOCKED,
            "reason": "EMERGENCY_STOP_ACTIVE",
        }
    emergency_marker_active = bool(
        str(current_state.get("emergency_reason", "") or "").strip()
        or str(current_state.get("emergency_stopped_at", "") or "").strip()
    ) and review_location not in {"긴급정지해제", "긴급정지 해제"}
    if scope != EMERGENCY_SCOPE_SELECTED and (
        is_emergency_stopped_state(current_state) or emergency_marker_active
    ):
        return {
            "availability": REVIEW_RETURN_BLOCKED,
            "reason": "EMERGENCY_STOP_ACTIVE",
        }
    if not is_review_required_state(current_state):
        return {
            "availability": REVIEW_RETURN_BLOCKED,
            "reason": "정상화 대상 상태가 아닙니다.",
        }

    allowed, reason = emergency_release_common_guard(
        window,
        stock_dir,
        str(stock_code or "").strip(),
        order_queue_path=order_queue_path,
        now_dt=now_dt,
    )
    return {
        "availability": REVIEW_RETURN_ALLOWED if allowed else REVIEW_RETURN_BLOCKED,
        "reason": "" if allowed else str(reason or "복귀 안전조건 미충족"),
    }


def _record_emergency_release_guard_failure(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    reason: str,
) -> bool:
    """Persist the existing fail-closed emergency Review contract."""
    operator_reason = operator_review_reason(reason)
    state_before = read_json_dict(Path(stock_dir) / "state.json")
    entered_at = str(state_before.get("review_entered_at", "") or "").strip() or now_text()
    return update_runtime_stock_status(
        window,
        Path(stock_dir),
        code,
        name,
        "EMERGENCY_STOPPED",
        {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": operator_review_location("긴급정지해제"),
            "review_reason": operator_reason,
            "review_entered_at": entered_at,
            "review_checked_at": now_text(),
            "review_routine": _routine_name_for_emergency_release(Path(stock_dir)),
            "review_detail": f"{code} {name} / {operator_reason} / evidence={reason}",
            "trade_enabled": False,
        },
        reason,
        verify_readback=True,
        allow_review_state_transition=True,
    )


def _complete_emergency_release(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    *,
    preserve_review: bool,
) -> bool:
    """Clear emergency evidence without treating release as an automatic Review exit."""

    state_before = read_json_dict(Path(stock_dir) / "state.json")
    routine_name = _routine_name_for_emergency_release(Path(stock_dir))
    metadata: dict[str, object] = {
        "emergency_released_at": now_text(),
        "emergency_release_check": "PASSED",
        "emergency_stopped_at": "",
        "emergency_reason": "",
        "emergency_scope": "",
        "trade_enabled": False,
        "operation_notice": "",
        "operation_notice_reason": "",
        "operation_notice_at": "",
        "review_checked_at": now_text(),
    }
    target_status = "STOPPED"
    allow_review_transition = False
    if preserve_review:
        target_status = "REVIEW_REQUIRED"
        allow_review_transition = True
        metadata.update(
            {
                "review_required": True,
                "review_status": "RESOLVED",
                "review_routine": str(
                    state_before.get("review_routine", "") or routine_name
                ).strip(),
            }
        )
    else:
        metadata.update(
            {
                "review_required": False,
                "review_status": "",
                "review_location": "",
                "review_reason": "",
                "review_detail": "",
                "review_entered_at": "",
                "review_routine": routine_name,
            }
        )
    metadata.update(_preserved_review_return_terminal_close_metadata(state_before))

    return update_runtime_stock_status(
        window,
        Path(stock_dir),
        code,
        name,
        target_status,
        metadata,
        "긴급정지 해제",
        verify_readback=True,
        allow_review_state_transition=allow_review_transition,
    )


def _complete_global_emergency_release_to_review(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    reason: str,
) -> bool:
    """Release GLOBAL ownership while handing a stock-specific block to Review."""
    operator_reason = operator_review_reason(reason)
    state_before = read_json_dict(Path(stock_dir) / "state.json")
    entered_at = str(state_before.get("review_entered_at", "") or "").strip() or now_text()
    return update_runtime_stock_status(
        window,
        Path(stock_dir),
        code,
        name,
        "REVIEW_REQUIRED",
        {
            "emergency_released_at": now_text(),
            "emergency_release_check": "BLOCKED_TO_REVIEW",
            "emergency_stopped_at": "",
            "emergency_reason": "",
            "emergency_scope": "",
            "review_required": True,
            "review_status": "PENDING",
            "review_location": operator_review_location("긴급정지해제"),
            "review_reason": operator_reason,
            "review_entered_at": entered_at,
            "review_checked_at": now_text(),
            "review_routine": _routine_name_for_emergency_release(Path(stock_dir)),
            "review_detail": f"{code} {name} / {operator_reason} / evidence={reason}",
            "trade_enabled": False,
        },
        reason,
        verify_readback=True,
        allow_review_state_transition=True,
    )


def _emergency_release_integrity_reason_code(reason: str) -> str:
    clean_reason = str(reason or "").strip()
    if clean_reason == "state.json 이상":
        return "STOCK_STATE_INVALID"
    if clean_reason == "config.json 이상":
        return "STOCK_CONFIG_INVALID"
    if clean_reason == "orders.json 누락":
        return "STOCK_ORDERS_MISSING"
    if clean_reason.startswith("PENDING_ORDER_DATA_INTEGRITY"):
        return "PENDING_ORDER_DATA_INTEGRITY"
    if clean_reason == "SERVER_MISMATCH":
        return "OPERATION_DATA_SERVER_MISMATCH"
    return ""


def _routine_name_for_emergency_release(stock_dir: Path) -> str:
    """Return persisted routine metadata without depending on a GUI window."""
    config = read_json_dict(Path(stock_dir) / "config.json")
    if not isinstance(config, dict):
        return ""
    for key in (
        "routine_instance_name",
        "routine",
        "routine_name",
        "assigned_routine_instance_id",
    ):
        value = str(config.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _preserved_review_return_terminal_close_metadata(
    state: dict[str, object],
) -> dict[str, object]:
    """Preserve canonical terminal close evidence for Review/Global lifecycle."""
    command_mode = str(state.get("operation_command_mode", "") or "").strip().upper()
    notice = str(state.get("operation_notice", "") or "").strip().upper()
    if command_mode != "EARLY_CLOSE" or notice != "EARLY_CLOSE_NO_TARGET":
        return {}
    return {
        "operation_command_mode": state.get("operation_command_mode", ""),
        "operation_notice": state.get("operation_notice", ""),
        "operation_notice_reason": state.get("operation_notice_reason", ""),
        "operation_notice_at": state.get("operation_notice_at", ""),
    }


def update_runtime_stock_status(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    new_status: str,
    extra_state: dict[str, object] | None = None,
    log_suffix: str = "",
    *,
    verify_readback: bool = False,
    allow_review_state_transition: bool = False,
) -> bool:
    """메인창 긴급정지/정지해제 전용 state.json 상태 저장."""
    mutation_result = mutate_runtime_stock_state(
        stock_dir,
        new_status,
        extra_state,
        updated_at=now_text(),
        verify_readback=verify_readback,
        allow_review_state_transition=allow_review_state_transition,
    )
    before_status = mutation_result.before_status
    failure_scope = f"emergency_stock_state:{str(code or '').strip()}:{new_status}"
    if not mutation_result.ok:
        reason_code = str(mutation_result.reason or "STATE_WRITE_FAILED").strip()
        observe_owner_failure_transition(
            window,
            failure_scope,
            active=True,
            signature=reason_code,
            event_type="PROCESSING_ERROR",
            severity="ERROR",
            result="FAILED",
            source="gui_main_emergency_ops.update_runtime_stock_status",
            template_args={"target": f"{code} {name}".strip()},
            target_type="STOCK",
            target_id=str(code or "").strip(),
            target_name=str(name or "").strip(),
            stock_code=str(code or "").strip(),
            stock_name=str(name or "").strip(),
            reason_code=reason_code,
            details={
                "stage": "emergency_stock_state_write",
                "requested_status": str(new_status or "").strip(),
            },
        )
    else:
        observe_owner_failure_transition(
            window,
            failure_scope,
            active=False,
        )
    if not mutation_result.ok and mutation_result.reason == "WRITE_FAILED":
        QMessageBox.critical(
            window,
            "상태 저장 오류",
            f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
        )
        append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
        return False

    if not mutation_result.ok:
        append_stock_log(
            stock_dir,
            "ERROR",
            f"상태 저장 재조회 불일치: {before_status} -> {new_status}",
        )
        return False

    suffix_text = f" / {log_suffix}" if log_suffix else ""
    append_stock_log(stock_dir, "GUI", f"긴급정지 상태 변경: {before_status} -> {new_status}{suffix_text}")
    return True


def normalize_review_emergency_target(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    *,
    destination: str = "RESTORE",
) -> dict[str, object]:
    """Normalize one reviewed/emergency-stopped stock after the shared integrity check."""
    normalized_destination = str(destination or "RESTORE").strip().upper()
    if normalized_destination not in {"RESTORE", "UNASSIGNED"}:
        return {"status": "FAILED", "reason": "지원하지 않는 정상화 목적지입니다."}

    stock_dir = Path(stock_dir)
    state_before = read_json_dict(stock_dir / "state.json")
    if not isinstance(state_before, dict):
        return {"status": "FAILED", "reason": "state.json 이상"}
    if not (
        is_emergency_stopped_state(state_before)
        or is_review_required_state(state_before)
    ):
        return {"status": "SKIPPED", "reason": "정상화 대상 상태가 아닙니다."}

    availability = review_return_availability(
        window,
        stock_dir,
        code,
        state=state_before,
    )
    if availability.get("availability") != REVIEW_RETURN_ALLOWED:
        return {
            "status": "BLOCKED",
            "reason": str(availability.get("reason") or "복귀 안전조건 미충족"),
        }

    routine_name = _routine_name_for_emergency_release(stock_dir)
    metadata = {
        "emergency_released_at": now_text(),
        "emergency_release_check": "PASSED",
        "emergency_stopped_at": "",
        "emergency_reason": "",
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
        "review_required": False,
        "review_status": "",
        "review_location": "",
        "review_reason": "",
        "review_detail": "",
        "review_entered_at": "",
        "review_checked_at": now_text(),
        "review_routine": routine_name,
        "startup_reset_reason": "",
    }
    metadata.update(_preserved_review_return_terminal_close_metadata(state_before))
    if normalized_destination == "UNASSIGNED":
        metadata.update(
            {"active_routine": "", "routine_name": "", "review_routine": ""}
        )

    if not update_runtime_stock_status(
        window,
        stock_dir,
        code,
        name,
        "STOPPED",
        metadata,
        "검토관리 정상화",
        verify_readback=True,
        allow_review_state_transition=True,
    ):
        return {"status": "FAILED", "reason": "상태 저장 실패"}

    if normalized_destination == "UNASSIGNED" and not update_base_stock_routines(
        code,
        name,
        [],
    ):
        write_state_json(
            stock_dir,
            state_before,
            allow_review_state_transition=True,
        )
        return {"status": "FAILED", "reason": "루틴 연결 해제 실패"}

    return {
        "status": "NORMALIZED",
        "reason": "",
        "destination": normalized_destination,
        "routine_name": routine_name,
    }


def execute_emergency_stop(window) -> None:
    """Enable the global latch and stop non-Review runtime stocks.

    A pre-existing Review row is already isolated from trading.  The global
    latch protects it without replacing its status or Review identity.
    """
    is_ready, _not_ready_code = _evaluate_emergency_preflight(window)
    if not is_ready:
        show_toast(
            parent=operation_dialog_parent(window),
            message=_PRE_EMERGENCY_NOT_READY_MESSAGE,
            duration_ms=2500,
            position="center",
        )
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(_PRE_EMERGENCY_NOT_READY_MESSAGE)
        update_emergency_button_state(window)
        return

    emergency_was_stopped = read_operation_state().get("emergency_stop") is True
    global_result = write_global_emergency_stop_state(
        emergency_stop=True,
        timestamp=now_text(),
    )
    if not global_result.get("ok"):
        observe_owner_failure_transition(
            window,
            "emergency_global_stop_write",
            active=True,
            signature="GLOBAL_EMERGENCY_STOP_WRITE_FAILED",
            event_type="PROCESSING_ERROR",
            severity="ERROR",
            result="FAILED",
            source="gui_main_emergency_ops.execute_emergency_stop",
            template_args={"target": "전역 긴급정지 상태"},
            target_type="GLOBAL_OPERATION",
            target_id="global_operation",
            target_name="전역 긴급정지 상태",
            reason_code="GLOBAL_EMERGENCY_STOP_WRITE_FAILED",
            details={"stage": "emergency_global_stop_write"},
        )
        message = "전역 긴급정지 기록에 실패했습니다. 종목별 긴급정지를 시작하지 않았습니다."
        QMessageBox.critical(window, "긴급정지 오류", message)
        window.statusBar().showMessage(message)
        update_emergency_button_state(window)
        show_toast(
            parent=window,
            message="긴급정지 실패 | 전역 차단 기록 실패",
            duration_ms=2500,
            position="center",
        )
        return

    observe_owner_failure_transition(
        window,
        "emergency_global_stop_write",
        active=False,
    )

    if not emergency_was_stopped:
        append_production_event(
            "EMERGENCY_STOPPED",
            severity="WARNING",
            result="COMPLETED",
            source="gui_main_emergency_ops.execute_emergency_stop",
            target_type="GLOBAL_OPERATION",
            target_id="global_operation",
            reason_code="USER_EMERGENCY_STOP",
        )

    changed_count = 0
    preserved_review_count = 0
    failed_count = 0
    stock_dirs = list(window.all_runtime_stock_dirs())
    for stock_dir in stock_dirs:
        code, name = parse_stock_folder_name(stock_dir.name)
        state_before = read_json_dict(Path(stock_dir) / "state.json")
        if is_review_required_state(state_before):
            preserved_review_count += 1
            continue
        metadata = {
            "emergency_stopped_at": now_text(),
            "emergency_reason": "USER_EMERGENCY_STOP",
            "emergency_scope": EMERGENCY_SCOPE_GLOBAL,
            # 전역 긴급정지는 공통 운영 보호 동작이다. 기존 Review
            # metadata는 병합 writer가 보존하지만 새 Review 진입 증거는
            # 만들지 않는다. 문제 종목은 정지해제 guard에서만 분류한다.
            # 긴급정지는 즉시 매매 시작 플래그를 끈다.
            # 정지해제 후 자동복귀 금지 정책과 현황색 판정이 어긋나지 않도록
            # canonical trade_enabled를 False로 고정한다.
            "trade_enabled": False,
            # 긴급정지 진입 시 과거 마감/청산 표시 잔존 메타도 제거한다.
            # 이 값이 남아 있으면 시작 OFF 상태에서도 현황이 주황으로 보일 수 있다.
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
        metadata.update(
            _preserved_review_return_terminal_close_metadata(
                state_before if isinstance(state_before, dict) else {}
            )
        )
        ok = update_runtime_stock_status(
            window,
            stock_dir,
            code,
            name,
            "EMERGENCY_STOPPED",
            metadata,
            "사용자 긴급정지",
        )
        if ok:
            changed_count += 1
        else:
            failed_count += 1

    changelog_message = f"긴급정지 실행: {changed_count}개 종목"
    if preserved_review_count:
        changelog_message += f" / 기존 검토 유지 {preserved_review_count}개"
    if failed_count:
        changelog_message += f" / 실패 {failed_count}개 / 전역 차단 유지"
    append_changelog("UPDATE", "state.json", changelog_message)
    status_message = f"긴급정지 실행 완료: {changed_count}개 종목"
    if preserved_review_count:
        status_message += f" / 기존 검토 유지 {preserved_review_count}개"
    if failed_count:
        status_message += f" / 실패 {failed_count}개 / 전역 차단 유지"
    window.statusBar().showMessage(status_message)
    refresh_views = getattr(window, "refresh_auto_trade_assignment_views", None)
    if callable(refresh_views):
        refresh_views()
    else:
        window.refresh_all()
    update_emergency_button_state(window)
    show_toast(
        parent=window,
        message=(
            f"긴급정지 완료 | 대상종목 : {changed_count}개 | 매수/매도 : 차단"
            + (f" | 실패 : {failed_count}개" if failed_count else "")
        ),
        duration_ms=2500,
        position="center",
    )


def execute_selected_emergency_stop(
    window,
    selected_targets: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...] | None = None,
) -> dict[str, object]:
    """Isolate selected stocks in Review and project current return safety."""
    if selected_targets is None:
        selected_getter = getattr(window, "selected_stock_infos", None)
        selected_targets = selected_getter() if callable(selected_getter) else []
    if not selected_targets:
        return {
            "changed": tuple(),
            "skipped": tuple(),
            "failed": tuple(),
            "changed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "availability_by_stock": {},
        }

    is_ready, _not_ready_code = _evaluate_emergency_preflight(window)
    if not is_ready:
        show_toast(
            parent=operation_dialog_parent(window),
            message=_PRE_EMERGENCY_NOT_READY_MESSAGE,
            duration_ms=2500,
            position="center",
        )
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(_PRE_EMERGENCY_NOT_READY_MESSAGE)
        return {
            "changed": tuple(),
            "skipped": tuple(),
            "failed": tuple(),
            "changed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "availability_by_stock": {},
        }

    changed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    availability_by_stock: dict[str, dict[str, object]] = {}
    for stock_dir, code, name in selected_targets:
        stock_dir = Path(stock_dir)
        state = read_json_dict(stock_dir / "state.json")
        label = f"{code} {name}".strip()
        if is_emergency_stopped_state(state) or is_review_required_state(state):
            skipped.append(label)
            continue

        ok = update_runtime_stock_status(
            window,
            stock_dir,
            code,
            name,
            "REVIEW_REQUIRED",
            {
                "emergency_stopped_at": "",
                "emergency_reason": "",
                "emergency_scope": EMERGENCY_SCOPE_SELECTED,
                "review_required": True,
                "review_status": "PENDING",
                "review_location": operator_review_location("종목 우클릭 검토정지"),
                "review_reason": "사용자 검토정지",
                "review_entered_at": str(
                    state.get("review_entered_at", "") or ""
                ).strip()
                or now_text(),
                "review_checked_at": now_text(),
                "review_routine": _routine_name_for_emergency_release(stock_dir),
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
            },
            "종목 우클릭 검토정지",
            verify_readback=True,
            allow_review_state_transition=True,
        )
        if ok:
            changed.append(label)
            availability_by_stock[str(code or "").strip()] = review_return_availability(
                window,
                stock_dir,
                code,
            )
        else:
            failed.append(label)

    if changed:
        append_changelog("UPDATE", "state.json", f"종목 검토정지 실행: {' / '.join(changed)}")

    refresh_auto_trade_views(window)
    status_message = getattr(window, "statusBarMessage", None)
    if callable(status_message):
        status_message(
            f"종목 검토정지: 변경 {len(changed)}개"
            + (f" / 이미 검토관리 {len(skipped)}개" if skipped else "")
            + (f" / 실패 {len(failed)}개" if failed else "")
        )
    if changed:
        show_toast(
            parent=operation_dialog_parent(window),
            message=f"검토정지 완료 | 대상종목 : {len(changed)}개 | 매수/매도 : 차단",
            duration_ms=2500,
            position="center",
        )

    return {
        "changed": tuple(changed),
        "skipped": tuple(skipped),
        "failed": tuple(failed),
        "changed_count": len(changed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "availability_by_stock": availability_by_stock,
    }


def release_emergency_stop_target(
    window,
    stock_dir: Path,
    code: str,
    name: str,
) -> str:
    """Apply the SELECTED emergency-release contract to one stock."""
    stock_dir = Path(stock_dir)
    state_before = read_json_dict(stock_dir / "state.json")
    if read_operation_state().get("emergency_stop") is True:
        return BLOCKED_IN_EMERGENCY
    if (
        not is_emergency_stopped_state(state_before)
        or emergency_scope(state_before) != EMERGENCY_SCOPE_SELECTED
    ):
        return RELEASE_SKIPPED
    registry_checker = getattr(
        window,
        "production_recovery_stock_is_review_required",
        None,
    )
    already_in_review = is_review_required_state(state_before) or (
        bool(registry_checker(code)) if callable(registry_checker) else False
    )
    release_allowed, guard_reason = emergency_release_common_guard(
        window,
        stock_dir,
        code,
    )
    if not release_allowed:
        if not _record_emergency_release_guard_failure(
            window,
            stock_dir,
            code,
            name,
            guard_reason,
        ):
            return RELEASE_FAILED
        integrity_reason_code = _emergency_release_integrity_reason_code(
            guard_reason
        )
        if integrity_reason_code:
            observe_owner_failure_transition(
                window,
                f"emergency_release_integrity:{code}",
                active=True,
                signature=integrity_reason_code,
                event_type="INTEGRITY_WARNING",
                severity="WARNING",
                result="BLOCKED",
                source="gui_main_emergency_ops.release_emergency_stop_target",
                template_args={"target": f"{code} {name}".strip()},
                target_type="STOCK",
                target_id=str(code or "").strip(),
                target_name=str(name or "").strip(),
                stock_code=str(code or "").strip(),
                stock_name=str(name or "").strip(),
                reason_code=integrity_reason_code,
                details={"stage": "emergency_release_integrity_guard"},
            )
        return BLOCKED_IN_EMERGENCY

    observe_owner_failure_transition(
        window,
        f"emergency_release_integrity:{code}",
        active=False,
    )

    if not _complete_emergency_release(
        window,
        stock_dir,
        code,
        name,
        preserve_review=already_in_review,
    ):
        return RELEASE_FAILED
    return RELEASED_TO_REVIEW if already_in_review else RELEASED_NORMAL


def execute_selected_emergency_release(
    window,
    selected_targets: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...] | None = None,
) -> dict[str, object]:
    """선택된 긴급정지 종목만 정지해제한다."""
    if selected_targets is None:
        selected_getter = getattr(window, "selected_stock_infos", None)
        selected_targets = selected_getter() if callable(selected_getter) else []

    if read_operation_state().get("emergency_stop") is True:
        message = "전체 긴급정지 상태에서는 상단 정지해제를 사용하십시오."
        status_message = getattr(window, "statusBarMessage", None)
        if callable(status_message):
            status_message(message)
        show_toast(
            parent=operation_dialog_parent(window),
            message=message,
            duration_ms=2500,
            position="center",
        )
        return {
            "normal": (),
            "review": (),
            "blocked": tuple(
                f"{code} {name}".strip()
                for _stock_dir, code, name in selected_targets
            ),
            "skipped": (),
            "failed": (),
            "normal_count": 0,
            "review_count": 0,
            "blocked_count": len(selected_targets),
            "skipped_count": 0,
            "failed_count": 0,
        }

    normal: list[str] = []
    review: list[str] = []
    blocked: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for stock_dir, code, name in selected_targets:
        stock_dir = Path(stock_dir)
        label = f"{code} {name}".strip()
        state = read_json_dict(stock_dir / "state.json")
        if not is_emergency_stopped_state(state):
            skipped.append(label)
            continue
        result = release_emergency_stop_target(window, stock_dir, code, name)
        if result == RELEASED_NORMAL:
            normal.append(label)
        elif result == RELEASED_TO_REVIEW:
            review.append(label)
        elif result == BLOCKED_IN_EMERGENCY:
            blocked.append(label)
        elif result == RELEASE_SKIPPED:
            skipped.append(label)
        else:
            failed.append(label)

    if normal or review or blocked or failed:
        append_changelog(
            "UPDATE",
            "state.json",
            "종목 긴급정지 해제: "
            f"정상 {len(normal)}개 / 검토관리 유지 {len(review)}개"
            f" / 차단 {len(blocked)}개 / 실패 {len(failed)}개",
        )

    refresh_auto_trade_views(window)
    status_message = getattr(window, "statusBarMessage", None)
    if callable(status_message):
        status_message(
            f"종목 정지해제: 정상 {len(normal)}개 / 검토관리 유지 {len(review)}개"
            + (f" / 긴급정지 유지 {len(blocked)}개" if blocked else "")
            + (f" / 대상아님 {len(skipped)}개" if skipped else "")
            + (f" / 실패 {len(failed)}개" if failed else "")
        )
    if normal or review:
        show_toast(
            parent=operation_dialog_parent(window),
            message=(
                f"정지해제 완료 | 감시/대기 전환 : {len(normal)}종목"
                f" | 검토관리 유지 : {len(review)}종목"
            ),
            duration_ms=2500,
            position="center",
        )
    elif blocked:
        show_toast(
            parent=operation_dialog_parent(window),
            message=f"정지해제 차단 | 긴급정지 유지 : {len(blocked)}종목",
            duration_ms=2500,
            position="center",
        )
    elif failed:
        show_toast(
            parent=operation_dialog_parent(window),
            message=f"정지해제 실패 : {len(failed)}종목",
            duration_ms=2500,
            position="center",
        )

    return {
        "normal": tuple(normal),
        "review": tuple(review),
        "blocked": tuple(blocked),
        "skipped": tuple(skipped),
        "failed": tuple(failed),
        "normal_count": len(normal),
        "review_count": len(review),
        "blocked_count": len(blocked),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }


def _release_global_emergency_stop_target(
    window,
    stock_dir: Path,
    code: str,
    name: str,
) -> str:
    """Release one GLOBAL-owned emergency target after common preflight."""
    allowed, reason = emergency_release_common_guard(
        window,
        Path(stock_dir),
        code,
        check_recovery=False,
    )
    if allowed:
        if _complete_emergency_release(
            window,
            Path(stock_dir),
            code,
            name,
            preserve_review=False,
        ):
            return RELEASED_NORMAL
        return RELEASE_FAILED
    if _complete_global_emergency_release_to_review(
        window,
        Path(stock_dir),
        code,
        name,
        reason,
    ):
        return RELEASED_TO_REVIEW
    return RELEASE_FAILED


def release_emergency_stop(window) -> dict[str, object]:
    """긴급정지 해제 시 종목별 무결성을 확인하고 정상/검토관리로 분기한다."""
    emergency_was_stopped = read_operation_state().get("emergency_stop") is True
    global_targets: list[tuple[Path, str, str]] = []
    for stock_dir in window.all_runtime_stock_dirs():
        stock_dir = Path(stock_dir)
        state_before = read_json_dict(stock_dir / "state.json")
        if (
            is_emergency_stopped_state(state_before)
            and emergency_scope(state_before) == EMERGENCY_SCOPE_GLOBAL
        ):
            code, name = parse_stock_folder_name(stock_dir.name)
            global_targets.append((stock_dir, code, name))

    preflight_allowed, preflight_reason = global_emergency_release_preflight(window)
    if not preflight_allowed:
        operator_reason = operator_review_reason(preflight_reason)
        message = f"전체 정지해제 차단 | {operator_reason}"
        window.statusBar().showMessage(message)
        update_emergency_button_state(window)
        show_toast(parent=window, message=message, duration_ms=2500, position="center")
        return {
            "status": "BLOCKED",
            "reason": preflight_reason,
            "normal_count": 0,
            "review_count": 0,
            "remaining_global_count": len(global_targets),
            "failed_count": 0,
        }

    normal_count = 0
    review_count = 0
    failed_count = 0
    for stock_dir, code, name in global_targets:
        result = _release_global_emergency_stop_target(window, stock_dir, code, name)
        if result == RELEASED_NORMAL:
            normal_count += 1
        elif result == RELEASED_TO_REVIEW:
            review_count += 1
        elif result == RELEASE_FAILED:
            failed_count += 1

    remaining_global_count = 0
    for stock_dir in window.all_runtime_stock_dirs():
        state_after = read_json_dict(Path(stock_dir) / "state.json")
        if (
            is_emergency_stopped_state(state_after)
            and emergency_scope(state_after) == EMERGENCY_SCOPE_GLOBAL
        ):
            remaining_global_count += 1

    global_result: dict[str, object] = {"ok": False}
    if failed_count == 0 and remaining_global_count == 0:
        global_result = write_global_emergency_stop_state(
            emergency_stop=False,
            timestamp=now_text(),
        )
        latch_cleared = (
            global_result.get("ok") is True
            and read_operation_state().get("emergency_stop") is False
        )
        if not latch_cleared:
            failed_count += 1
            observe_owner_failure_transition(
                window,
                "emergency_global_release_write",
                active=True,
                signature="GLOBAL_EMERGENCY_RELEASE_WRITE_FAILED",
                event_type="PROCESSING_ERROR",
                severity="ERROR",
                result="FAILED",
                source="gui_main_emergency_ops.release_emergency_stop",
                template_args={"target": "전역 긴급정지 해제 상태"},
                target_type="GLOBAL_OPERATION",
                target_id="global_operation",
                target_name="전역 긴급정지 해제 상태",
                reason_code="GLOBAL_EMERGENCY_RELEASE_WRITE_FAILED",
                details={"stage": "emergency_global_release_write"},
            )
        else:
            observe_owner_failure_transition(
                window,
                "emergency_global_release_write",
                active=False,
            )
            if emergency_was_stopped:
                append_production_event(
                    "EMERGENCY_RELEASED",
                    result="COMPLETED",
                    source="gui_main_emergency_ops.release_emergency_stop",
                    target_type="GLOBAL_OPERATION",
                    target_id="global_operation",
                )

    append_changelog(
        "UPDATE",
        "state.json",
        f"긴급정지 해제 무결성 검사: 정상 {normal_count}개 / 검토관리 {review_count}개"
        + (
            f" / 긴급정지 잔존 {remaining_global_count}개 / 실패 {failed_count}개 / 전역 차단 유지"
            if failed_count or remaining_global_count
            else ""
        ),
    )
    status_message = (
        f"정지해제 완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
    )
    if failed_count or remaining_global_count:
        status_message = (
            f"정지해제 미완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
            f" / 긴급정지 잔존 {remaining_global_count}개"
            f" / 실패 {failed_count}개 / 전역 차단 유지"
        )
    window.statusBar().showMessage(status_message)
    refresh_views = getattr(window, "refresh_auto_trade_assignment_views", None)
    if callable(refresh_views):
        refresh_views()
    else:
        window.refresh_all()
    update_emergency_button_state(window)
    show_toast(
        parent=window,
        message=(
            (
                f"정지해제 완료 | 감시/대기 전환 : {normal_count}종목"
                f" | 검토관리 : {review_count}종목"
            )
            if failed_count == 0 and remaining_global_count == 0
            else (
                f"정지해제 미완료 | 감시/대기 전환 : {normal_count}종목"
                f" | 검토관리 : {review_count}종목"
                f" | 긴급정지 잔존 : {remaining_global_count}종목"
                f" | 실패 : {failed_count}종목"
            )
        ),
        duration_ms=2500,
        position="center",
    )
    return {
        "status": (
            "COMPLETED"
            if failed_count == 0 and remaining_global_count == 0
            else "INCOMPLETE"
        ),
        "reason": "",
        "normal_count": normal_count,
        "review_count": review_count,
        "remaining_global_count": remaining_global_count,
        "failed_count": failed_count,
    }


def on_emergency_stop_clicked(window) -> None:
    """전역 긴급정지 상태를 다시 읽어 긴급정지/정지해제를 분기한다."""
    if read_operation_state().get("emergency_stop") is True:
        release_emergency_stop(window)
        return

    execute_emergency_stop(window)
