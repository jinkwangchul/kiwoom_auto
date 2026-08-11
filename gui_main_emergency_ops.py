# -*- coding: utf-8 -*-
"""
gui_main_emergency_ops.py

MainWindow 긴급정지/정지해제 처리 전용 모듈.

정책:
- 긴급정지: 즉시 전체 종목 상태를 EMERGENCY_STOPPED로 전환
- 정지해제: 무결성 확인 후 정상은 STOPPED, 문제 종목은 REVIEW_REQUIRED
- 자동복귀 금지: 정지해제 후에도 매매시작 상태로 자동 복귀하지 않음
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from gui_toast import show_toast
from gui_operation_ui_context import operation_dialog_parent
from gui_common_utils import safe_int_value
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    is_emergency_stopped_state,
    is_review_required_state,
)
from gui_auto_trade_run_control import (
    ORDER_QUEUE_PATH,
    _active_close_or_liquidation,
    _active_queue_reason,
)
from gui_config_utils import default_state
from gui_order_utils import pending_order_integrity_issue_codes, pending_order_side_quantities
from gui_review_required_window import auto_trade_setting_server_mismatch_detected
from runtime_io import read_json_dict, read_orders_data
from gui_auto_trade_runtime import write_state_json
from gui_base_stock_service import update_base_stock_routines
from operation_policy_gate import read_operation_state, write_global_emergency_stop_state
from event_journal_production import append_production_event
from production_recovery_state_registry import (
    check_production_recovery_gate,
    production_recovery_registry,
)
from gui_auto_trade_setting_window import (
    append_changelog,
    append_stock_log,
    now_text,
    parse_stock_folder_name,
)


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
    parent_getter = getattr(window, "parent", None)
    if callable(parent_getter):
        try:
            parent = parent_getter()
        except Exception:
            parent = None
        if parent is not None and parent is not window:
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


def _record_emergency_release_guard_failure(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    reason: str,
) -> bool:
    """Persist the existing fail-closed emergency Review contract."""
    return update_runtime_stock_status(
        window,
        Path(stock_dir),
        code,
        name,
        "EMERGENCY_STOPPED",
        {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": "긴급정지해제",
            "review_reason": reason,
            "review_entered_at": now_text(),
            "review_checked_at": now_text(),
            "review_routine": _routine_name_for_emergency_release(Path(stock_dir)),
            "review_detail": f"{code} {name} / {reason}",
            "trade_enabled": False,
        },
        reason,
        verify_readback=True,
        allow_review_state_transition=True,
    )


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
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    if not isinstance(state, dict):
        state = default_state()

    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    state["status"] = new_status
    state["updated_at"] = now_text()

    if extra_state:
        state.update(extra_state)

    if not write_state_json(
        stock_dir,
        state,
        allow_review_state_transition=allow_review_state_transition,
    ):
        QMessageBox.critical(
            window,
            "상태 저장 오류",
            f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
        )
        append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
        return False

    if verify_readback:
        saved_state = read_json_dict(state_path)
        expected_values = {"status": new_status}
        if extra_state:
            expected_values.update(extra_state)
        if not isinstance(saved_state, dict) or any(
            saved_state.get(key) != value for key, value in expected_values.items()
        ):
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

    has_problem, reason = emergency_review_reason_for_stock(stock_dir)
    if has_problem:
        if not is_review_required_state(state_before):
            current_status = str(
                state_before.get("status", "EMERGENCY_STOPPED")
                or "EMERGENCY_STOPPED"
            ).strip()
            if not update_runtime_stock_status(
                window,
                stock_dir,
                code,
                name,
                current_status,
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_location": "긴급정지해제",
                    "review_reason": reason,
                    "review_entered_at": now_text(),
                    "review_checked_at": now_text(),
                    "review_routine": _routine_name_for_emergency_release(stock_dir),
                    "review_detail": f"{code} {name} / {reason}",
                    "trade_enabled": False,
                },
                reason,
                verify_readback=True,
            ):
                return {"status": "FAILED", "reason": "검토관리 상태 저장 실패"}
        return {"status": "BLOCKED", "reason": reason}

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
        "reason": reason,
        "destination": normalized_destination,
        "routine_name": routine_name,
    }


def execute_emergency_stop(window) -> None:
    """전체 runtime 종목을 긴급정지 상태로 전환한다."""
    emergency_was_stopped = read_operation_state().get("emergency_stop") is True
    global_result = write_global_emergency_stop_state(
        emergency_stop=True,
        timestamp=now_text(),
    )
    if not global_result.get("ok"):
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
    failed_count = 0
    stock_dirs = list(window.all_runtime_stock_dirs())
    for stock_dir in stock_dirs:
        code, name = parse_stock_folder_name(stock_dir.name)
        ok = update_runtime_stock_status(
            window,
            stock_dir,
            code,
            name,
            "EMERGENCY_STOPPED",
            {
                "emergency_stopped_at": now_text(),
                "emergency_reason": "USER_EMERGENCY_STOP",
                "review_required": True,
                "review_status": "PENDING",
                "review_location": "사용자 긴급정지",
                "review_reason": "사용자 긴급정지",
                "review_checked_at": now_text(),
                "review_routine": _routine_name_for_emergency_release(stock_dir),
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
            },
            "사용자 긴급정지",
        )
        if ok:
            changed_count += 1
        else:
            failed_count += 1

    changelog_message = f"긴급정지 실행: {changed_count}개 종목"
    if failed_count:
        changelog_message += f" / 실패 {failed_count}개 / 전역 차단 유지"
    append_changelog("UPDATE", "state.json", changelog_message)
    status_message = f"긴급정지 실행 완료: {changed_count}개 종목"
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
    """선택된 종목만 긴급정지 상태로 전환한다."""
    if selected_targets is None:
        selected_getter = getattr(window, "selected_stock_infos", None)
        selected_targets = selected_getter() if callable(selected_getter) else []

    changed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for stock_dir, code, name in selected_targets:
        stock_dir = Path(stock_dir)
        state = read_json_dict(stock_dir / "state.json")
        label = f"{code} {name}".strip()
        if is_emergency_stopped_state(state):
            skipped.append(label)
            continue

        ok = update_runtime_stock_status(
            window,
            stock_dir,
            code,
            name,
            "EMERGENCY_STOPPED",
            {
                "emergency_stopped_at": now_text(),
                "emergency_reason": "USER_EMERGENCY_STOP",
                "review_required": True,
                "review_status": "PENDING",
                "review_location": "종목 우클릭 긴급정지",
                "review_reason": "사용자 긴급정지",
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
            "종목 우클릭 긴급정지",
            verify_readback=True,
        )
        if ok:
            changed.append(label)
        else:
            failed.append(label)

    if changed:
        append_changelog("UPDATE", "state.json", f"종목 긴급정지 실행: {' / '.join(changed)}")

    refresh_all = getattr(window, "refresh_all", None)
    if callable(refresh_all):
        refresh_all()
    status_message = getattr(window, "statusBarMessage", None)
    if callable(status_message):
        status_message(
            f"종목 긴급정지: 변경 {len(changed)}개"
            + (f" / 이미 긴급정지 {len(skipped)}개" if skipped else "")
            + (f" / 실패 {len(failed)}개" if failed else "")
        )
    if changed:
        show_toast(
            parent=operation_dialog_parent(window),
            message=f"긴급정지 완료 | 대상종목 : {len(changed)}개 | 매수/매도 : 차단",
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
    }


def release_emergency_stop_target(
    window,
    stock_dir: Path,
    code: str,
    name: str,
) -> str:
    """긴급정지 해제 안전성검사를 종목 하나에 적용한다."""
    stock_dir = Path(stock_dir)
    state_before = read_json_dict(stock_dir / "state.json")
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
            return "failed"
        return "review_existing" if already_in_review else "review"

    result = normalize_review_emergency_target(
        window,
        stock_dir,
        code,
        name,
        destination="RESTORE",
    )
    status = str(result.get("status", "") or "")
    if status == "NORMALIZED":
        return "normal"
    if status == "BLOCKED":
        return "review_existing" if already_in_review else "review"
    return "failed"


def execute_selected_emergency_release(
    window,
    selected_targets: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...] | None = None,
) -> dict[str, object]:
    """선택된 긴급정지 종목만 정지해제한다."""
    if selected_targets is None:
        selected_getter = getattr(window, "selected_stock_infos", None)
        selected_targets = selected_getter() if callable(selected_getter) else []

    normal: list[str] = []
    review: list[str] = []
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
        if result == "normal":
            normal.append(label)
        elif result in {"review", "review_existing"}:
            review.append(label)
        else:
            failed.append(label)

    if normal or review or failed:
        append_changelog(
            "UPDATE",
            "state.json",
            "종목 긴급정지 해제: "
            f"정상 {len(normal)}개 / 검토관리 {len(review)}개 / 실패 {len(failed)}개",
        )

    refresh_all = getattr(window, "refresh_all", None)
    if callable(refresh_all):
        refresh_all()
    status_message = getattr(window, "statusBarMessage", None)
    if callable(status_message):
        status_message(
            f"종목 정지해제: 정상 {len(normal)}개 / 검토관리 {len(review)}개"
            + (f" / 대상아님 {len(skipped)}개" if skipped else "")
            + (f" / 실패 {len(failed)}개" if failed else "")
        )
    if normal or review:
        show_toast(
            parent=operation_dialog_parent(window),
            message=(
                f"정지해제 완료 | 감시/대기 전환 : {len(normal)}종목"
                f" | 검토관리 : {len(review)}종목"
            ),
            duration_ms=2500,
            position="center",
        )

    return {
        "normal": tuple(normal),
        "review": tuple(review),
        "skipped": tuple(skipped),
        "failed": tuple(failed),
        "normal_count": len(normal),
        "review_count": len(review),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }


def release_emergency_stop(window) -> None:
    """긴급정지 해제 시 종목별 무결성을 확인하고 정상/검토관리로 분기한다."""
    emergency_was_stopped = read_operation_state().get("emergency_stop") is True
    normal_count = 0
    review_count = 0
    failed_count = 0
    for stock_dir in window.all_runtime_stock_dirs():
        code, name = parse_stock_folder_name(stock_dir.name)
        result = release_emergency_stop_target(window, stock_dir, code, name)
        if result == "normal":
            normal_count += 1
        elif result == "review":
            review_count += 1
        elif result == "failed":
            failed_count += 1

    global_result: dict[str, object] = {"ok": False}
    if failed_count == 0:
        global_result = write_global_emergency_stop_state(
            emergency_stop=False,
            timestamp=now_text(),
        )
        if not global_result.get("ok"):
            failed_count += 1
        elif emergency_was_stopped:
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
        + (f" / 실패 {failed_count}개 / 전역 차단 유지" if failed_count else ""),
    )
    status_message = (
        f"정지해제 완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
    )
    if failed_count:
        status_message = (
            f"정지해제 미완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
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
            if failed_count == 0
            else (
                f"정지해제 미완료 | 감시/대기 전환 : {normal_count}종목"
                f" | 검토관리 : {review_count}종목 | 실패 : {failed_count}종목"
            )
        ),
        duration_ms=2500,
        position="center",
    )


def on_emergency_stop_clicked(window) -> None:
    """전역 긴급정지 상태를 다시 읽어 긴급정지/정지해제를 분기한다."""
    if read_operation_state().get("emergency_stop") is True:
        release_emergency_stop(window)
        return

    execute_emergency_stop(window)
