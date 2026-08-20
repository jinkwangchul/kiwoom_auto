# -*- coding: utf-8 -*-
"""
gui_auto_trade_timer.py

자동매매설정창의 타이머/시간정책 재판정 헬퍼.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from gui_auto_trade_close import auto_trade_continue_pending_close_liquidations
from gui_auto_trade_ats_ops import auto_trade_continue_pending_manual_ats_liquidations
from event_journal_production import (
    observe_owner_failure_transition,
    observe_production_exception,
)
from execution_universe import (
    ExecutionUniverseSnapshot,
    project_execution_universe,
)

try:
    from auto_candle_refresh import refresh_operation_candles
    _CANDLE_REFRESH_IMPORT_ERROR = None
except Exception as exc:
    refresh_operation_candles = None
    _CANDLE_REFRESH_IMPORT_ERROR = exc

try:
    from routine_signal_probe import probe_all_enabled_routine_stocks_once
    _ROUTINE_PROBE_IMPORT_ERROR = None
except Exception as exc:
    probe_all_enabled_routine_stocks_once = None
    _ROUTINE_PROBE_IMPORT_ERROR = exc

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


def auto_trade_signal_probe_only_active(
    window,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
) -> bool:
    try:
        snapshot = execution_universe_snapshot or project_execution_universe(window)
    except Exception:
        return False
    for entry in snapshot.entries:
        if entry.execution_ready and entry.signal_probe_only:
            return True
    return False


def auto_trade_real_execution_active(
    window,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
) -> bool:
    try:
        snapshot = execution_universe_snapshot or project_execution_universe(window)
    except Exception:
        return False
    for entry in snapshot.entries:
        if (
            entry.execution_ready
            and entry.real_trade_enabled
            and not entry.signal_probe_only
        ):
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


def _process_pending_signal_pipeline(
    window,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
) -> dict[str, object]:
    """Reuse the existing Consumer and real executor without routine probing."""
    signal_result: dict[str, object] = {}
    snapshot = execution_universe_snapshot or project_execution_universe(window)
    if not (
        callable(consume_pending_routine_signals_dry_run)
        and (
            auto_trade_signal_probe_only_active(window, snapshot)
            or auto_trade_real_execution_active(window, snapshot)
        )
    ):
        return signal_result

    consumer_result = consume_pending_routine_signals_dry_run(
        limit=5,
        mark_previewed=True,
        write_order_queue=True,
        apply_approval=True,
    )
    summary = consumer_result.get("summary", {}) if isinstance(consumer_result, dict) else {}
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
    observe_owner_failure_transition(
        window,
        "routine_signal_consumer_result",
        active=errors > 0,
        signature=f"ROUTINE_SIGNAL_CONSUMER_FAILED:{errors}",
        event_type="PROCESSING_ERROR",
        severity="ERROR",
        result="FAILED",
        source="gui_auto_trade_timer._process_pending_signal_pipeline",
        template_args={"target": "루틴 신호 후보 처리"},
        target_type="ROUTINE",
        target_id="routine_signal_consumer",
        target_name="루틴 신호 후보 처리",
        reason_code="ROUTINE_SIGNAL_CONSUMER_FAILED",
        component="routine_signal_cycle",
        operation="consume_pending_routine_signals",
        details={
            "checked": checked,
            "blocked": blocked,
            "allowed": allowed,
            "error_count": errors,
        },
    )
    if auto_trade_real_execution_active(window, snapshot):
        auto_executor = getattr(window, "auto_process_executable_orders_for_real_trade", None)
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
    return signal_result


def _auto_trade_run_signal_cycle(window, minute_key: str) -> dict[str, object]:
    signal_result: dict[str, object] = {}
    if not callable(probe_all_enabled_routine_stocks_once):
        if _ROUTINE_PROBE_IMPORT_ERROR is not None:
            observe_production_exception(
                type(_ROUTINE_PROBE_IMPORT_ERROR),
                _ROUTINE_PROBE_IMPORT_ERROR,
                _ROUTINE_PROBE_IMPORT_ERROR.__traceback__,
                component="routine_signal_cycle",
                operation="import_routine_signal_probe",
                source="gui_auto_trade_timer._auto_trade_run_signal_cycle",
                target_type="ROUTINE",
                target_id="routine_signal_probe",
                target_name="루틴 신호 프로브",
                reason_code="ROUTINE_PROBE_IMPORT_FAILED",
                owner=window,
                failure_scope="routine_probe_import",
            )
        return signal_result
    try:
        execution_universe_snapshot = project_execution_universe(window)
        probe_result = probe_all_enabled_routine_stocks_once(
            window,
            minute_key,
            execution_universe_snapshot=execution_universe_snapshot,
        )
        logged_count = int(probe_result.get("logged", 0) or 0)
        error_count = int(probe_result.get("error", 0) or 0)
        if logged_count > 0 or error_count > 0:
            window.statusBarMessage(
                f"루틴 신호 로그: 기록 {logged_count}개"
                + (f" / 오류 {error_count}개" if error_count else "")
            )
        signal_result = _process_pending_signal_pipeline(
            window,
            execution_universe_snapshot,
        )
        observe_owner_failure_transition(
            window,
            "routine_signal_cycle",
            active=False,
        )
        observe_owner_failure_transition(
            window,
            "routine_probe_import",
            active=False,
        )
    except Exception as exc:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="routine_signal_cycle",
            operation="run_signal_cycle",
            source="gui_auto_trade_timer._auto_trade_run_signal_cycle",
            target_type="ROUTINE",
            target_id="routine_signal_cycle",
            target_name="루틴 신호 주기",
            reason_code="ROUTINE_SIGNAL_CYCLE_FAILED",
            owner=window,
            failure_scope="routine_signal_cycle",
        )
        LOGGER.exception("Routine signal operation cycle failed")
        window.statusBarMessage(
            "주문 후보를 검증하는 중 오류가 발생했습니다. 로그를 확인하십시오."
        )
        signal_result = {"errors": 1}
    return signal_result


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
    observe_owner_failure_transition(
        window,
        "operation_policy_recalculation",
        active=failed_count > 0,
        signature=f"OPERATION_POLICY_RECALCULATION_FAILED:{failed_count}",
        event_type="PROCESSING_ERROR",
        severity="ERROR",
        result="FAILED",
        source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
        template_args={"target": "운영 정책 재판정"},
        target_type="OPERATION",
        target_id="operation_policy_recalculation",
        target_name="운영 정책 재판정",
        reason_code="OPERATION_POLICY_RECALCULATION_FAILED",
        component="operation_cycle",
        operation="recalculate_all_status_by_operation_policy",
        details={"failed_count": failed_count},
    )

    rebind_recovery = getattr(
        window,
        "rebind_startup_recovery_after_trusted_runtime_update",
        None,
    )
    if callable(rebind_recovery):
        rebind_recovery()

    close_result = auto_trade_continue_pending_close_liquidations(window, limit=5)
    close_processed = int(close_result.get("processed", 0) or 0)
    close_blocked = int(close_result.get("blocked", 0) or 0)
    if close_processed > 0 or close_blocked > 0:
        window.statusBarMessage(
            "마감·청산 Command 처리: "
            f"진행 {close_processed} / 차단 {close_blocked}"
        )

    ats_result = auto_trade_continue_pending_manual_ats_liquidations(window, limit=5)
    ats_processed = int(ats_result.get("processed", 0) or 0)
    ats_failed = int(ats_result.get("failed", 0) or 0)
    if ats_processed > 0 or ats_failed > 0:
        window.statusBarMessage(
            "ATS 청산 Command 처리: "
            f"진행 {ats_processed} / 실패 {ats_failed}"
        )

    signal_result: dict[str, object] = {}
    candle_refresh_result: dict[str, object] = {}
    signal_cycle_completed = False
    deferred_cycle_completion_pending = False
    deferred_cycle_completion = getattr(
        window,
        "complete_deferred_operation_cycle",
        None,
    )

    def operation_cycle_result() -> dict[str, object]:
        return {
            "processed": True,
            "reason_code": "OPERATION_CYCLE_COMPLETED",
            "minute_key": minute_key,
            "changed": changed_count,
            "failed": failed_count,
            "close_processed": close_processed,
            "close_blocked": close_blocked,
            "candle_refresh_result": dict(candle_refresh_result),
            "signal_result": dict(signal_result),
        }

    def continue_after_candle_refresh(_refresh_result: dict[str, object]) -> None:
        nonlocal candle_refresh_result, signal_result, signal_cycle_completed
        if isinstance(_refresh_result, dict):
            candle_refresh_result = dict(_refresh_result)
            try:
                failed_refreshes = int(candle_refresh_result.get("failed", 0) or 0)
            except (TypeError, ValueError):
                failed_refreshes = 0
                observe_owner_failure_transition(
                    window,
                    "candle_refresh_result_contract",
                    active=True,
                    signature="CANDLE_REFRESH_FAILED_COUNT_MALFORMED",
                    event_type="INTEGRITY_WARNING",
                    severity="ERROR",
                    result="FAILED",
                    source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                    template_args={"target": "분봉 갱신 결과"},
                    target_type="MARKET_DATA",
                    target_id="operation_candle_refresh",
                    target_name="분봉 갱신 결과",
                    reason_code="CANDLE_REFRESH_RESULT_MALFORMED",
                    component="candle_refresh",
                    operation="continue_after_candle_refresh",
                )
            else:
                observe_owner_failure_transition(
                    window,
                    "candle_refresh_result_contract",
                    active=False,
                )
            observe_owner_failure_transition(
                window,
                "candle_refresh_result",
                active=failed_refreshes > 0,
                signature=f"CANDLE_REFRESH_RESULT_FAILED:{failed_refreshes}",
                event_type="PROCESSING_ERROR",
                severity="ERROR",
                result="FAILED",
                source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                template_args={"target": "분봉 갱신"},
                target_type="MARKET_DATA",
                target_id="operation_candle_refresh",
                target_name="분봉 갱신",
                reason_code="CANDLE_REFRESH_RESULT_FAILED",
                component="candle_refresh",
                operation="continue_after_candle_refresh",
                details={"failed_count": failed_refreshes},
            )
        signal_result = _process_pending_signal_pipeline(window)
        signal_cycle_completed = True
        if callable(rebind_recovery):
            rebind_recovery()
        if deferred_cycle_completion_pending and callable(
            deferred_cycle_completion
        ):
            try:
                deferred_cycle_completion(operation_cycle_result())
                observe_owner_failure_transition(
                    window,
                    "deferred_operation_cycle_completion",
                    active=False,
                )
            except Exception as exc:
                observe_production_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    component="operation_cycle_callback",
                    operation="complete_deferred_operation_cycle",
                    source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                    target_type="OPERATION_HOST",
                    target_id="deferred_operation_cycle",
                    target_name="지연 운영 주기 완료 callback",
                    reason_code="DEFERRED_OPERATION_CALLBACK_FAILED",
                    owner=window,
                    failure_scope="deferred_operation_cycle_completion",
                )
                LOGGER.exception("Deferred operation cycle completion notify failed")

    if callable(refresh_operation_candles):
        try:
            candle_refresh_result = refresh_operation_candles(
                window,
                minute_key,
                on_complete=continue_after_candle_refresh,
            )
        except Exception as exc:
            observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="candle_refresh",
                operation="refresh_operation_candles",
                source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                target_type="MARKET_DATA",
                target_id="operation_candle_refresh",
                target_name="분봉 갱신",
                reason_code="CANDLE_REFRESH_FAILED",
                owner=window,
                failure_scope="candle_refresh_request",
            )
            LOGGER.exception("Automatic minute candle refresh failed")
            candle_refresh_result = {
                "accepted": False,
                "completed": False,
                "reason_code": "CANDLE_REFRESH_FAILED",
            }
            signal_result = _process_pending_signal_pipeline(window)
        else:
            observe_owner_failure_transition(
                window,
                "candle_refresh_request",
                active=False,
            )
            if (
                candle_refresh_result.get("accepted") is False
                and candle_refresh_result.get("completed") is False
            ):
                signal_result = _process_pending_signal_pipeline(window)
            elif candle_refresh_result.get("completed") is not True:
                signal_result = {"deferred_for_candle_refresh": True}
                deferred_cycle_completion_pending = True
    else:
        if _CANDLE_REFRESH_IMPORT_ERROR is not None:
            observe_production_exception(
                type(_CANDLE_REFRESH_IMPORT_ERROR),
                _CANDLE_REFRESH_IMPORT_ERROR,
                _CANDLE_REFRESH_IMPORT_ERROR.__traceback__,
                component="candle_refresh",
                operation="import_auto_candle_refresh",
                source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                target_type="MARKET_DATA",
                target_id="operation_candle_refresh",
                target_name="분봉 갱신",
                reason_code="CANDLE_REFRESH_IMPORT_FAILED",
                owner=window,
                failure_scope="candle_refresh_import",
            )
        signal_result = _process_pending_signal_pipeline(window)

    if callable(rebind_recovery) and not signal_cycle_completed:
        rebind_recovery()

    if changed_count > 0 or failed_count > 0:
        window.statusBarMessage(
            f"시간정책 자동반영: 변경 {changed_count}개"
            + (f" / 실패 {failed_count}개" if failed_count else "")
        )

    return operation_cycle_result()



def auto_trade_on_time_policy_timer_tick(window) -> None:
    """Backward-compatible entry point for the durable operation cycle.

    원칙:
    - 초 단위 반복 작업 금지
    - 상태 변화가 없으면 화면 갱신 금지
    - 변경 종목이 있을 때만 현재 창을 갱신
    - 긴급정지/검토종목/조기마감은 재판정 함수에서 보호
    """
    return auto_trade_run_operation_cycle(window)

