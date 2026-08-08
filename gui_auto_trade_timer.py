# -*- coding: utf-8 -*-
"""
gui_auto_trade_timer.py

자동매매설정창의 타이머/시간정책 재판정 헬퍼.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from gui_ats_utils import manual_ats_market_day_closed
from manual_ats_runtime import reset_expired_manual_ats_runtime_selections
from gui_auto_trade_close import auto_trade_continue_pending_close_liquidations

try:
    from routine_signal_probe import probe_all_enabled_routine_stocks_once
except Exception:
    probe_all_enabled_routine_stocks_once = None

try:
    from routine_signal_consumer import consume_pending_routine_signals_dry_run
except Exception:
    consume_pending_routine_signals_dry_run = None

LOGGER = logging.getLogger(__name__)


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """루틴 폴더 아래 실제 종목 runtime 폴더 목록을 반환한다."""
    if not routine_dir.exists() or not routine_dir.is_dir():
        return []
    result: list[Path] = []
    for child in routine_dir.iterdir():
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and not child.name.startswith("__")
            and (child / "config.json").exists()
        ):
            result.append(child)
    return result


def _read_json_dict(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def auto_trade_signal_probe_only_active(window) -> bool:
    try:
        from gui_auto_trade_runtime import all_registered_stock_dirs
        stock_dirs = all_registered_stock_dirs()
    except Exception:
        stock_dirs = []

    for stock_dir in stock_dirs:
        state = _read_json_dict(Path(stock_dir) / "state.json")
        if state.get("signal_probe_only") is True:
            return True
    return False


def auto_trade_real_execution_active(window) -> bool:
    try:
        from gui_auto_trade_runtime import all_registered_stock_dirs
        stock_dirs = all_registered_stock_dirs()
    except Exception:
        stock_dirs = []

    for stock_dir in stock_dirs:
        state = _read_json_dict(Path(stock_dir) / "state.json")
        if state.get("signal_probe_only") is True:
            continue
        if state.get("review_required") is True:
            continue
        if str(state.get("status") or "").strip().upper() in {
            "REVIEW_REQUIRED",
            "REVIEW",
            "EMERGENCY_STOPPED",
            "EMERGENCY_STOP",
            "EMERGENCY",
        }:
            continue
        if state.get("trade_enabled") is True and state.get("real_trade_enabled") is True:
            return True
    return False


def auto_trade_current_time_policy_minute_key(window) -> str:
    """시간정책 자동 재판정용 분 단위 키."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")



def auto_trade_current_runtime_file_signature(window) -> dict[str, int]:
    """중앙 등록 종목의 runtime 파일 변경 여부를 판단하는 스냅샷."""
    try:
        from gui_auto_trade_runtime import all_registered_stock_dirs

        stock_dirs = all_registered_stock_dirs()
    except Exception:
        stock_dirs = []

    signature: dict[str, int] = {}
    for stock_dir in stock_dirs:
        for filename in ("state.json", "config.json", "orders.json"):
            path = stock_dir / filename
            try:
                signature[str(path)] = path.stat().st_mtime_ns
            except Exception:
                signature[str(path)] = -1
    return signature



def auto_trade_on_runtime_file_timer_tick(window) -> None:
    """외부 파일 수정분을 자동매매설정 표에 반영한다."""
    if not window.isVisible():
        return

    signature = window.current_runtime_file_signature()
    if signature == window._runtime_file_snapshot:
        return

    window._runtime_file_snapshot = signature
    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.load_selected_routine_stocks()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window.update_action_buttons()


def auto_trade_on_time_policy_gui_timer_tick(window) -> None:
    """Refresh the settings UI for time-dependent display state only."""
    if not window.isVisible():
        return

    minute_key = auto_trade_current_time_policy_minute_key(window)
    if minute_key == getattr(window, "_last_time_policy_gui_minute_key", ""):
        return
    window._last_time_policy_gui_minute_key = minute_key

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.refresh_all()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)


def auto_trade_run_operation_cycle(window) -> dict[str, object]:
    """Run the durable operation cycle independently from GUI visibility."""
    recovery_check = getattr(window, "startup_recovery_session_ready", None)
    if callable(recovery_check) and recovery_check(refresh=True) is not True:
        stop_timers = getattr(window, "stop_operation_timers", None)
        if callable(stop_timers):
            stop_timers()
        return {"processed": False, "reason_code": "RECOVERY_NOT_READY"}

    minute_key = auto_trade_current_time_policy_minute_key(window)
    if minute_key == getattr(window, "_last_time_policy_minute_key", ""):
        return {"processed": False, "reason_code": "MINUTE_ALREADY_PROCESSED"}

    window._last_time_policy_minute_key = minute_key
    result = window.recalculate_all_status_by_operation_policy(
        "시간 경과 자동 재판정",
        silent_unchanged=True,
        write_changelog_when_unchanged=False,
    )
    changed_count = int(result.get("changed", 0) or 0)
    failed_count = int(result.get("failed", 0) or 0)

    rebind_recovery = getattr(
        window,
        "rebind_startup_recovery_after_trusted_runtime_update",
        None,
    )
    if callable(rebind_recovery):
        rebind_recovery()

    reset_expired_manual_ats_runtime_selections(
        Path(__file__).resolve().parent / "stocks",
        market_closed=manual_ats_market_day_closed(),
    )

    close_result = auto_trade_continue_pending_close_liquidations(window, limit=5)
    close_processed = int(close_result.get("processed", 0) or 0)
    close_blocked = int(close_result.get("blocked", 0) or 0)
    if close_processed > 0 or close_blocked > 0:
        window.statusBarMessage(
            "마감·청산 Command 처리: "
            f"진행 {close_processed} / 차단 {close_blocked}"
        )

    signal_result: dict[str, object] = {}
    if callable(probe_all_enabled_routine_stocks_once):
        try:
            probe_result = probe_all_enabled_routine_stocks_once(window, minute_key)
            logged_count = int(probe_result.get("logged", 0) or 0)
            error_count = int(probe_result.get("error", 0) or 0)
            if logged_count > 0 or error_count > 0:
                window.statusBarMessage(
                    f"루틴 신호 로그: 기록 {logged_count}개"
                    + (f" / 오류 {error_count}개" if error_count else "")
                )
            if (
                callable(consume_pending_routine_signals_dry_run)
                and (
                    auto_trade_signal_probe_only_active(window)
                    or auto_trade_real_execution_active(window)
                )
            ):
                consumer_result = consume_pending_routine_signals_dry_run(
                    limit=5,
                    mark_previewed=True,
                    write_order_queue=True,
                    apply_approval=True,
                )
                summary = (
                    consumer_result.get("summary", {})
                    if isinstance(consumer_result, dict)
                    else {}
                )
                checked = int(summary.get("signals_checked", 0) or 0)
                blocked = int(summary.get("blocked", 0) or 0)
                allowed = int(summary.get("allowed", 0) or 0)
                errors = int(summary.get("errors", 0) or 0)
                orders_created = int(summary.get("orders_created", 0) or 0)
                approval_checked = int(summary.get("approval_checked", 0) or 0)
                approved = int(summary.get("approved", 0) or 0)
                if checked > 0 or errors > 0:
                    window.statusBarMessage(
                        f"주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}"
                        f" / 후보 {orders_created} / 승인검사 {approval_checked} / 승인 {approved}"
                    )
                signal_result = dict(summary)
                if auto_trade_real_execution_active(window):
                    auto_executor = getattr(
                        window,
                        "auto_process_executable_orders_for_real_trade",
                        None,
                    )
                    if callable(auto_executor):
                        auto_result = auto_executor(limit=5)
                        processed = int(auto_result.get("processed", 0) or 0)
                        auto_blocked = int(auto_result.get("blocked", 0) or 0)
                        signal_result["orders_processed"] = processed
                        signal_result["orders_blocked"] = auto_blocked
                        if processed > 0 or auto_blocked > 0:
                            window.statusBarMessage(
                                f"실자동매매 주문처리: 실행 {processed} / 차단 {auto_blocked}"
                            )
        except Exception:
            LOGGER.exception("Routine signal operation cycle failed")
            window.statusBarMessage(
                "주문 후보를 검증하는 중 오류가 발생했습니다. 로그를 확인하십시오."
            )
            signal_result = {"errors": 1}

    if callable(rebind_recovery):
        rebind_recovery()

    if changed_count > 0 or failed_count > 0:
        window.statusBarMessage(
            f"시간정책 자동반영: 변경 {changed_count}개"
            + (f" / 실패 {failed_count}개" if failed_count else "")
        )

    return {
        "processed": True,
        "reason_code": "OPERATION_CYCLE_COMPLETED",
        "minute_key": minute_key,
        "changed": changed_count,
        "failed": failed_count,
        "close_processed": close_processed,
        "close_blocked": close_blocked,
        "signal_result": signal_result,
    }



def auto_trade_on_time_policy_timer_tick(window) -> None:
    """Backward-compatible entry point for the durable operation cycle.

    원칙:
    - 초 단위 반복 작업 금지
    - 상태 변화가 없으면 화면 갱신 금지
    - 변경 종목이 있을 때만 현재 창을 갱신
    - 긴급정지/검토종목/조기마감은 재판정 함수에서 보호
    """
    return auto_trade_run_operation_cycle(window)

