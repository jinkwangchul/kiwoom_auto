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
from gui_operation_ui_context import actionable_current_price, refresh_auto_trade_views
from runtime_io import read_json_dict
from routine_package_contract import evaluate_routine_lifecycle
from routine_lifecycle_executor import execute_routine_lifecycle_decision

try:
    from routine_signal_probe import probe_all_enabled_routine_stocks_once
    _ROUTINE_PROBE_IMPORT_ERROR = None
except Exception as exc:
    probe_all_enabled_routine_stocks_once = None
    _ROUTINE_PROBE_IMPORT_ERROR = exc

try:
    from routine_signal_consumer import (
        consume_pending_routine_signals_dry_run,
        holding_consistency_from_main_facts,
    )
except Exception:
    consume_pending_routine_signals_dry_run = None
    holding_consistency_from_main_facts = None

from routine_main_facts import capture_routine_main_facts

LOGGER = logging.getLogger(__name__)




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
            and not entry.signal_probe_only
        ):
            return True
    return False


def auto_trade_current_time_policy_minute_key(window) -> str:
    """시간정책 자동 재판정용 분 단위 키."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")



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

    allowed_stock_codes = tuple(
        entry.stock_code for entry in snapshot.entries if entry.execution_ready
    )
    if not allowed_stock_codes:
        return signal_result

    signal_cutoff_by_stock_code: dict[str, str] = {}
    for entry in snapshot.entries:
        if not entry.execution_ready:
            continue
        stock_dir = getattr(entry, "stock_dir", None)
        state = (
            read_json_dict(Path(stock_dir) / "state.json")
            if stock_dir is not None
            else {}
        )
        signal_cutoff_by_stock_code[entry.stock_code] = str(
            state.get("ignore_signals_before", "") or ""
        ).strip()

    account_getter = getattr(window, "current_selected_account_no", None)
    if not callable(account_getter):
        account_getter = getattr(window, "_selected_account_no", None)
    selected_account_no = (
        str(account_getter() or "").strip() if callable(account_getter) else ""
    )
    eligible_execution_codes = tuple(
        entry.stock_code
        for entry in snapshot.entries
        if getattr(entry, "execution_ready", False)
        and not getattr(entry, "signal_probe_only", False)
    )
    entries_by_code = {
        entry.stock_code: entry
        for entry in snapshot.entries
        if getattr(entry, "execution_ready", False)
    }
    mark_review = getattr(window, "mark_review_required", None)
    stock_dirs = {
        entry.stock_code: Path(entry.stock_dir)
        for entry in snapshot.entries
        if getattr(entry, "execution_ready", False)
        and getattr(entry, "stock_dir", None) is not None
        and (Path(entry.stock_dir) / "config.json").is_file()
        and (Path(entry.stock_dir) / "state.json").is_file()
    }

    def capture_current_facts() -> dict[str, object]:
        current_prices = {
            code: actionable_current_price(window, code)
            for code in eligible_execution_codes
        }
        current_cash = (
            window.current_orderable_cash_for_budget()
            if callable(getattr(window, "current_orderable_cash_for_budget", None))
            else None
        )
        return capture_routine_main_facts(
            stock_dirs=stock_dirs,
            selected_account_no=selected_account_no,
            allowed_stock_codes=allowed_stock_codes,
            actionable_prices_by_code=current_prices,
            current_orderable_cash=current_cash,
        ).to_payload()

    try:
        main_facts = capture_current_facts()
    except Exception as exc:
        LOGGER.exception("routine Main facts capture failed")
        for entry in entries_by_code.values():
            if callable(mark_review):
                mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    {"review_reasons": [f"MAIN_FACTS_UNAVAILABLE:{type(exc).__name__}"],
                     "review_location": "ROUTINE_LIFECYCLE_FACTS"},
                    source="ROUTINE_LIFECYCLE_FACTS",
                )
        return {"errors": 1, "reason": "MAIN_FACTS_UNAVAILABLE"}

    duplicate_cancel_requester = getattr(window, "queue_open_order_cancel_automatically", None)
    if not callable(duplicate_cancel_requester):
        host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
        host = host_reader() if callable(host_reader) else None
        duplicate_cancel_requester = getattr(host, "queue_open_order_cancel_automatically", None)

    assigned_instance_by_code = {
        str(code or "").strip(): str(config.get("assigned_routine_instance_id") or "").strip()
        for code, config in main_facts.get("stock_configs", {}).items()
        if isinstance(config, dict)
        and str(config.get("assigned_routine_instance_id") or "").strip()
    }
    assigned_instances = tuple(dict.fromkeys(assigned_instance_by_code.values()))
    unassigned_execution_codes = tuple(
        code for code in eligible_execution_codes
        if code not in assigned_instance_by_code
    )
    if assigned_instances:
        lifecycle_summary: dict[str, object] = {
            "decisions": 0,
            "mutations": 0,
            "waiting": 0,
            "reviews": 0,
            "errors": 0,
            "executable_order_ids": [],
            "facts_revisions": [],
        }
        lifecycle_waiting_codes: set[str] = set()
        lifecycle_blocked_codes: set[str] = set(unassigned_execution_codes)
        consumer_summary: dict[str, object] = {}
        for _ in range(8):
            lifecycle_summary["facts_revisions"].append(main_facts.get("revision"))
            decisions: list[dict[str, object]] = []
            lifecycle_failed = False
            for instance_id in assigned_instances:
                evaluated = evaluate_routine_lifecycle(
                    instance_id=instance_id,
                    main_facts=main_facts,
                )
                if evaluated.get("ok") is not True:
                    lifecycle_failed = True
                    lifecycle_summary["errors"] = int(lifecycle_summary["errors"]) + 1
                    affected = [
                        entry for entry in entries_by_code.values()
                        if main_facts.get("stock_configs", {}).get(entry.stock_code, {}).get(
                            "assigned_routine_instance_id"
                        ) == instance_id
                    ]
                    for entry in affected:
                        if callable(mark_review) and mark_review(
                            entry.stock_dir,
                            entry.stock_code,
                            entry.stock_name,
                            {"review_reasons": [evaluated.get("reason") or "ROUTINE_LIFECYCLE_UNAVAILABLE"],
                             "review_location": "ROUTINE_LIFECYCLE_CONTRACT"},
                            source="ROUTINE_LIFECYCLE_CONTRACT",
                        ):
                            lifecycle_summary["reviews"] = int(lifecycle_summary["reviews"]) + 1
                    continue
                decisions.extend(
                    value for value in evaluated.get("decisions", []) if isinstance(value, dict)
                )
            if lifecycle_failed:
                return {"lifecycle": lifecycle_summary, "errors": lifecycle_summary["errors"]}
            lifecycle_summary["decisions"] = int(lifecycle_summary["decisions"]) + len(decisions)
            actionable_decision = next(
                (value for value in decisions if str(value.get("command") or "").upper() != "WAIT"),
                None,
            )
            lifecycle_waiting_codes = {
                str(value.get("stock_code") or "").strip()
                for value in decisions
                if str(value.get("command") or "").upper() == "WAIT"
            }
            lifecycle_summary["waiting"] = len(lifecycle_waiting_codes)
            if actionable_decision is None:
                lifecycle_allowed_codes = tuple(
                    code for code in allowed_stock_codes
                    if code not in lifecycle_waiting_codes and code not in lifecycle_blocked_codes
                )
                refreshed_for_consumer: dict[str, object] = {}

                def refresh_consumer_facts() -> dict[str, object]:
                    refreshed = capture_current_facts()
                    refreshed_for_consumer["facts"] = refreshed
                    return refreshed

                consumer_result = consume_pending_routine_signals_dry_run(
                    limit=5,
                    mark_previewed=True,
                    write_order_queue=True,
                    apply_approval=True,
                    allowed_stock_codes=lifecycle_allowed_codes,
                    signal_cutoff_by_stock_code=signal_cutoff_by_stock_code,
                    duplicate_cancel_requester=None,
                    holding_consistency_reader=(
                        (lambda code: holding_consistency_from_main_facts(main_facts, code))
                        if callable(holding_consistency_from_main_facts)
                        else None
                    ),
                    main_facts=main_facts,
                    fresh_main_facts_provider=refresh_consumer_facts,
                )
                consumer_summary = (
                    consumer_result.get("summary", {})
                    if isinstance(consumer_result, dict) else {}
                )
                if consumer_summary.get("facts_stale") is True:
                    refreshed = refreshed_for_consumer.get("facts")
                    if not isinstance(refreshed, dict):
                        lifecycle_summary["errors"] = int(lifecycle_summary["errors"]) + 1
                        return {"lifecycle": lifecycle_summary, "errors": lifecycle_summary["errors"]}
                    if consumer_summary.get("reason") == "SIGNAL_EVALUATION_FACTS_STALE":
                        return {
                            "lifecycle": lifecycle_summary,
                            "consumer": dict(consumer_summary),
                            "signal_reevaluation_required": True,
                        }
                    main_facts = refreshed
                    continue
                checked = int(consumer_summary.get("signals_checked", 0) or 0)
                blocked = int(consumer_summary.get("blocked", 0) or 0)
                allowed = int(consumer_summary.get("allowed", 0) or 0)
                errors = int(consumer_summary.get("errors", 0) or 0)
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
                lifecycle_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in consumer_summary.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                break
            code = str(actionable_decision.get("stock_code") or "").strip()
            if str(actionable_decision.get("command") or "").upper() == "STOP_AND_REVIEW":
                lifecycle_blocked_codes.add(code)
            executed = execute_routine_lifecycle_decision(
                actionable_decision,
                main_facts=main_facts,
                cancel_requester=duplicate_cancel_requester,
                review_marker=mark_review,
                stock_entry=entries_by_code.get(code),
            )
            if executed.get("ok") is not True:
                lifecycle_summary["errors"] = int(lifecycle_summary["errors"]) + 1
                return {"lifecycle": lifecycle_summary, "errors": lifecycle_summary["errors"]}
            lifecycle_summary["executable_order_ids"].extend(
                str(value or "").strip()
                for value in executed.get("executable_order_ids", [])
                if str(value or "").strip()
            )
            if executed.get("mutated") is not True:
                break
            lifecycle_summary["mutations"] = int(lifecycle_summary["mutations"]) + 1
            try:
                main_facts = capture_current_facts()
            except Exception:
                lifecycle_summary["errors"] = int(lifecycle_summary["errors"]) + 1
                return {"lifecycle": lifecycle_summary, "errors": lifecycle_summary["errors"]}
        else:
            # A bounded timer tick must never fall through to Candidate creation
            # when lifecycle mutations may still remain.  The next tick starts
            # from a fresh facts revision and resumes those stocks.
            lifecycle_blocked_codes.update(entries_by_code)
        if auto_trade_real_execution_active(window, snapshot):
            auto_executor = getattr(window, "auto_process_executable_orders_for_real_trade", None)
            executable_ids = list(dict.fromkeys(lifecycle_summary["executable_order_ids"]))
            if callable(auto_executor) and executable_ids:
                auto_result = auto_executor(limit=max(5, len(executable_ids)), order_ids=executable_ids)
                lifecycle_summary["orders_processed"] = int(auto_result.get("processed", 0) or 0)
                lifecycle_summary["orders_blocked"] = int(auto_result.get("blocked", 0) or 0)
        return {"lifecycle": lifecycle_summary, "consumer": dict(consumer_summary)}

    # No assigned Routine may be interpreted by Main. An execution-ready
    # unassigned stock is skipped fail-closed and must be repaired through the
    # existing assignment workflow before any strategy lifecycle can advance.
    return {
        "lifecycle": {
            "decisions": 0,
            "mutations": 0,
            "waiting": 0,
            "reviews": 0,
            "errors": 0,
            "executable_order_ids": [],
            "facts_revisions": [main_facts.get("revision")],
            "skipped_unassigned_stock_codes": list(unassigned_execution_codes),
            "reason": "ROUTINE_ASSIGNMENT_MISSING",
        },
        "consumer": {
            "processed": 0,
            "executable_order_ids": [],
            "reason": "ROUTINE_ASSIGNMENT_MISSING",
        },
    }


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
        if signal_result.get("signal_reevaluation_required") is True:
            # Candidate creation observed a post-evaluation Main-facts change.
            # Re-run the owning routine from a fresh sealed snapshot; the queue
            # refreshes the same signal identity before one bounded retry.
            probe_all_enabled_routine_stocks_once(
                window,
                minute_key,
                execution_universe_snapshot=execution_universe_snapshot,
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

    retirement_result: dict[str, object] = {}
    retire_time_ended = getattr(
        window,
        "retire_time_ended_current_session_participants",
        None,
    )
    if callable(retire_time_ended):
        try:
            retired = retire_time_ended(now_dt=datetime.now())
            if isinstance(retired, dict):
                retirement_result = dict(retired)
        except Exception as exc:
            observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="participant_retirement",
                operation="retire_time_ended_current_session_participants",
                source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                target_type="OPERATION",
                target_id="time_end_participant_retirement",
                target_name="거래시간 종료 참가자 정리",
                reason_code="PARTICIPANT_RETIREMENT_FAILED",
                owner=window,
                failure_scope="time_end_participant_retirement",
            )
            retirement_result = {
                "removed": (),
                "reason_code": "PARTICIPANT_RETIREMENT_FAILED",
                "error": str(exc),
            }
    if tuple(retirement_result.get("removed", ())):
        try:
            refresh_auto_trade_views(window)
        except Exception as exc:
            observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="participant_retirement",
                operation="refresh_auto_trade_views",
                source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
                target_type="OPERATION",
                target_id="time_end_participant_retirement",
                target_name="거래시간 종료 참가자 화면 갱신",
                reason_code="PARTICIPANT_RETIREMENT_UI_REFRESH_FAILED",
                owner=window,
                failure_scope="participant_retirement_ui_refresh",
            )

    realtime_shadow_result: dict[str, object] = {}
    market_data_cycle_result: dict[str, object] = {}
    market_data_getter = getattr(window, "market_data_host", None)
    market_data_host = market_data_getter() if callable(market_data_getter) else None
    execution_universe_snapshot = retirement_result.get(
        "execution_universe_snapshot"
    )
    try:
        if not isinstance(execution_universe_snapshot, ExecutionUniverseSnapshot):
            execution_universe_snapshot = project_execution_universe(window)
        retirement_sync = retirement_result.get("execution_shadow_sync_result")
        if isinstance(retirement_sync, dict):
            realtime_shadow_result = dict(retirement_sync)
        else:
            sync_targets = getattr(market_data_host, "sync_targets", None)
            if callable(sync_targets):
                synced = sync_targets(execution_universe_snapshot)
                if isinstance(synced, dict):
                    realtime_shadow_result = dict(synced)
    except Exception as exc:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="realtime_shadow",
            operation="sync_operation_targets",
            source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
            target_type="MARKET_DATA",
            target_id="realtime_shadow",
            target_name="Realtime shadow target sync",
            reason_code="REALTIME_SHADOW_SYNC_FAILED",
            owner=window,
            failure_scope="realtime_shadow_target_sync",
        )
        realtime_shadow_result = {
            "ok": False,
            "changed": False,
            "active": False,
            "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
            "error": str(exc),
        }
    try:
        prepare_market_data = getattr(market_data_host, "prepare_operation_cycle", None)
        if callable(prepare_market_data):
            prepared = prepare_market_data(
                execution_universe_snapshot,
                minute_key,
            )
            if isinstance(prepared, dict):
                market_data_cycle_result = dict(prepared)
    except Exception as exc:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="market_data_authority",
            operation="prepare_operation_cycle",
            source="gui_auto_trade_timer.auto_trade_run_operation_cycle",
            target_type="MARKET_DATA",
            target_id="market_data_authority",
            target_name="Market data authority",
            reason_code="MARKET_DATA_CYCLE_PREPARATION_FAILED",
            owner=window,
            failure_scope="market_data_cycle_preparation",
        )
        market_data_cycle_result = {
            "promoted_count": 0,
            "reason_code": "MARKET_DATA_CYCLE_PREPARATION_FAILED",
            "error": str(exc),
        }

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
            "participant_retirement_result": dict(retirement_result),
            "close_processed": close_processed,
            "close_blocked": close_blocked,
            "realtime_shadow_result": dict(realtime_shadow_result),
            "market_data_cycle_result": dict(market_data_cycle_result),
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

    refresh_market_data = getattr(market_data_host, "refresh_operation_candles", None)
    if callable(refresh_market_data):
        try:
            refreshed = refresh_market_data(
                minute_key,
                on_complete=continue_after_candle_refresh,
            )
            candle_refresh_result = (
                dict(refreshed)
                if isinstance(refreshed, dict)
                else {
                    "accepted": False,
                    "completed": False,
                    "reason_code": "CANDLE_REFRESH_RESULT_MALFORMED",
                }
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
        signal_result = _process_pending_signal_pipeline(window)

    if callable(rebind_recovery) and not signal_cycle_completed:
        rebind_recovery()

    if changed_count > 0 or failed_count > 0:
        window.statusBarMessage(
            f"시간정책 자동반영: 변경 {changed_count}개"
            + (f" / 실패 {failed_count}개" if failed_count else "")
        )

    return operation_cycle_result()
