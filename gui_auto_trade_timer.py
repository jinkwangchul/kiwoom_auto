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

try:
    from routine_signal_probe import probe_all_enabled_routine_stocks_once
    _ROUTINE_PROBE_IMPORT_ERROR = None
except Exception as exc:
    probe_all_enabled_routine_stocks_once = None
    _ROUTINE_PROBE_IMPORT_ERROR = exc

try:
    from routine_signal_consumer import (
        consume_pending_routine_signals_dry_run,
        enqueue_eligible_ratio_slice,
        enqueue_final_residual_sell_exit,
        enqueue_price_reset_generation,
        enqueue_repeat_sell_generation,
        enqueue_replanned_execution_intents,
        enqueue_scheduled_time_slice,
        record_final_residual_sell_exit_completion,
        record_repeat_sell_exit,
        record_buy_repeat_exit_completion,
    )
except Exception:
    consume_pending_routine_signals_dry_run = None
    enqueue_eligible_ratio_slice = None
    enqueue_final_residual_sell_exit = None
    enqueue_price_reset_generation = None
    enqueue_repeat_sell_generation = None
    enqueue_replanned_execution_intents = None
    enqueue_scheduled_time_slice = None
    record_final_residual_sell_exit_completion = None
    record_repeat_sell_exit = None
    record_buy_repeat_exit_completion = None

try:
    from execution_process_supplement import inspect_execution_process_supplements
except Exception:
    inspect_execution_process_supplements = None

try:
    from execution_time_slice_due import inspect_due_time_slices
except Exception:
    inspect_due_time_slices = None

try:
    from execution_ratio_slice_eligibility import inspect_eligible_ratio_slices
except Exception:
    inspect_eligible_ratio_slices = None

try:
    from execution_unfilled_cancel_eligibility import (
        inspect_unfilled_sell_cancel_eligibility,
    )
except Exception:
    inspect_unfilled_sell_cancel_eligibility = None

try:
    from execution_price_reset import inspect_buy_price_resets, inspect_sell_price_resets
except Exception:
    inspect_buy_price_resets = None
    inspect_sell_price_resets = None

try:
    from execution_sell_repeat import inspect_sell_repeat_exits, inspect_sell_repeat_generations
except Exception:
    inspect_sell_repeat_exits = None
    inspect_sell_repeat_generations = None

try:
    from execution_buy_exit import inspect_buy_repeat_exits
except Exception:
    inspect_buy_repeat_exits = None

try:
    from execution_sell_final_exit import inspect_sell_final_residual_exits
except Exception:
    inspect_sell_final_residual_exits = None

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

    # BUY Exit has priority over reset, timeout, and scheduled progression.
    buy_exit_summary: dict[str, object] = {
        "cancel_proposals": 0,
        "cancel_requested": 0,
        "cancel_pending": 0,
        "completion_proposals": 0,
        "completions_recorded": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "blocked_execution_process_ids": [],
        "executable_order_ids": [],
    }
    buy_exit_priority_ready = False
    if callable(inspect_buy_repeat_exits):
        try:
            buy_exit_inspection = inspect_buy_repeat_exits(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code={
                    code: actionable_current_price(window, code)
                    for code in eligible_execution_codes
                },
                proposal_limit=5,
            )
            # A failed/partial Exit inspection cannot authorize the lower-priority
            # reset path.  Missing authoritative evidence must remain fail-closed.
            buy_exit_priority_ready = buy_exit_inspection.get("ok") is True
            buy_exit_summary["cancel_proposals"] = len(buy_exit_inspection.get("cancel_proposals") or [])
            buy_exit_summary["completion_proposals"] = len(buy_exit_inspection.get("completion_proposals") or [])
            buy_exit_summary["waiting"] = len(buy_exit_inspection.get("waiting") or [])
            buy_exit_summary["errors"] = len(buy_exit_inspection.get("errors") or [])
            buy_exit_summary["blocked_execution_process_ids"] = list(
                buy_exit_inspection.get("blocked_execution_process_ids") or []
            )
            for review in buy_exit_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is not None and callable(mark_review) and mark_review(
                    entry.stock_dir, entry.stock_code, entry.stock_name, review,
                    source="BUY_EXIT_RECONCILIATION",
                ):
                    buy_exit_summary["reviews"] = int(buy_exit_summary["reviews"]) + 1
            cancel_requester = getattr(window, "queue_open_order_cancel_automatically", None)
            if not callable(cancel_requester):
                host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
                host = host_reader() if callable(host_reader) else None
                cancel_requester = getattr(host, "queue_open_order_cancel_automatically", None)
            for proposal in buy_exit_inspection.get("cancel_proposals") or []:
                if not isinstance(proposal, dict) or not callable(cancel_requester):
                    buy_exit_summary["errors"] = int(buy_exit_summary["errors"]) + 1
                    continue
                trigger = proposal.get("trigger_snapshot") if isinstance(proposal.get("trigger_snapshot"), dict) else {}
                cancel_result = cancel_requester(
                    str(proposal.get("order_queued_id") or ""),
                    expected_account_no=str(proposal.get("account_no") or ""),
                    expected_code=str(proposal.get("code") or ""),
                    expected_side="BUY",
                    expected_broker_order_no=str(proposal.get("broker_order_no") or ""),
                    cancel_evidence={
                        "trigger": "BUY_REPEAT_EXIT",
                        "trigger_snapshot_hash": trigger.get("snapshot_hash"),
                        "trigger_snapshot": trigger,
                        "original_remaining_quantity": proposal.get("remaining_quantity"),
                    },
                )
                buy_exit_summary["cancel_requested"] = int(buy_exit_summary["cancel_requested"]) + int(cancel_result.get("cancel_requested", 0) or 0)
                buy_exit_summary["cancel_pending"] = int(buy_exit_summary["cancel_pending"]) + int(cancel_result.get("cancel_pending", 0) or 0)
                if cancel_result.get("ok") is not True:
                    buy_exit_summary["errors"] = int(buy_exit_summary["errors"]) + 1
            if callable(record_buy_repeat_exit_completion):
                for proposal in buy_exit_inspection.get("completion_proposals") or []:
                    recorded = record_buy_repeat_exit_completion(proposal)
                    if recorded.get("ok") is True:
                        buy_exit_summary["completions_recorded"] = int(buy_exit_summary["completions_recorded"]) + 1
                    else:
                        buy_exit_summary["errors"] = int(buy_exit_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("BUY repeat-exit inspection failed")
            buy_exit_summary["errors"] = int(buy_exit_summary["errors"]) + 1
    else:
        buy_exit_summary["errors"] = int(buy_exit_summary["errors"]) + 1
    buy_exit_blocked_process_ids = tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in buy_exit_summary.get("blocked_execution_process_ids", [])
            if str(value or "").strip()
        )
    )

    # SELL repeat-exit is a terminal policy for further generations.  Inspect
    # and persist it before the lower-priority price-reset coordinator, while
    # leaving repeat generation itself in its existing later phase.
    sell_exit_precedence_summary: dict[str, object] = {
        "exit_proposals": 0,
        "exits_recorded": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "blocked_execution_process_ids": [],
    }
    sell_exit_priority_ready = False
    if callable(inspect_sell_repeat_exits):
        try:
            sell_exit_inspection = inspect_sell_repeat_exits(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code={
                    code: actionable_current_price(window, code)
                    for code in eligible_execution_codes
                },
                proposal_limit=5,
            )
            sell_exit_priority_ready = sell_exit_inspection.get("ok") is True
            sell_exit_precedence_summary["exit_proposals"] = len(
                sell_exit_inspection.get("exit_proposals") or []
            )
            sell_exit_precedence_summary["waiting"] = len(
                sell_exit_inspection.get("waiting") or []
            )
            sell_exit_precedence_summary["errors"] = len(
                sell_exit_inspection.get("errors") or []
            )
            sell_exit_precedence_summary["blocked_execution_process_ids"] = list(
                sell_exit_inspection.get("blocked_execution_process_ids") or []
            )
            for review in sell_exit_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is not None and callable(mark_review) and mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="SELL_REPEAT_EXIT_RECONCILIATION",
                ):
                    sell_exit_precedence_summary["reviews"] = int(
                        sell_exit_precedence_summary["reviews"]
                    ) + 1
            for exit_proposal in sell_exit_inspection.get("exit_proposals") or []:
                if not isinstance(exit_proposal, dict) or not callable(record_repeat_sell_exit):
                    sell_exit_precedence_summary["errors"] = int(
                        sell_exit_precedence_summary["errors"]
                    ) + 1
                    continue
                recorded = record_repeat_sell_exit(exit_proposal)
                if recorded.get("ok") is True:
                    sell_exit_precedence_summary["exits_recorded"] = int(
                        sell_exit_precedence_summary["exits_recorded"]
                    ) + 1
                else:
                    sell_exit_precedence_summary["errors"] = int(
                        sell_exit_precedence_summary["errors"]
                    ) + 1
        except Exception:
            LOGGER.exception("SELL repeat-exit precedence inspection failed")
            sell_exit_precedence_summary["errors"] = int(
                sell_exit_precedence_summary["errors"]
            ) + 1
    else:
        sell_exit_precedence_summary["errors"] = int(
            sell_exit_precedence_summary["errors"]
        ) + 1
    sell_exit_blocked_process_ids = tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in sell_exit_precedence_summary.get(
                "blocked_execution_process_ids", []
            )
            if str(value or "").strip()
        )
    )

    price_reset_summary: dict[str, object] = {
        "cancel_proposals": 0,
        "cancel_requested": 0,
        "cancel_pending": 0,
        "replan_proposals": 0,
        "orders_created": 0,
        "deferred_replans": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "blocked_execution_process_ids": [],
        "executable_order_ids": [],
    }
    if callable(inspect_sell_price_resets) and sell_exit_priority_ready:
        try:
            reset_prices = {
                code: actionable_current_price(window, code)
                for code in eligible_execution_codes
            }
            reset_inspection = inspect_sell_price_resets(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code=reset_prices,
                blocked_execution_process_ids=sell_exit_blocked_process_ids,
                cancel_limit=5,
            )
            price_reset_summary["cancel_proposals"] = len(
                reset_inspection.get("cancel_proposals") or []
            )
            price_reset_summary["replan_proposals"] = len(
                reset_inspection.get("replan_proposals") or []
            )
            price_reset_summary["waiting"] = len(
                reset_inspection.get("waiting") or []
            )
            price_reset_summary["errors"] = len(
                reset_inspection.get("errors") or []
            )
            price_reset_summary["blocked_execution_process_ids"] = list(
                reset_inspection.get("blocked_execution_process_ids") or []
            )
            for review in reset_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="SELL_PRICE_RESET_RECONCILIATION",
                ):
                    price_reset_summary["reviews"] = int(price_reset_summary["reviews"]) + 1

            cancel_requester = getattr(window, "queue_open_order_cancel_automatically", None)
            if not callable(cancel_requester):
                host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
                host = host_reader() if callable(host_reader) else None
                cancel_requester = getattr(host, "queue_open_order_cancel_automatically", None)
            for proposal in reset_inspection.get("cancel_proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                if not callable(cancel_requester):
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
                    break
                trigger_snapshot = proposal.get("trigger_snapshot")
                trigger_snapshot = trigger_snapshot if isinstance(trigger_snapshot, dict) else {}
                cancel_result = cancel_requester(
                    str(proposal.get("order_queued_id") or ""),
                    expected_account_no=str(proposal.get("account_no") or ""),
                    expected_code=str(proposal.get("code") or ""),
                    expected_side="SELL",
                    expected_broker_order_no=str(proposal.get("broker_order_no") or ""),
                    cancel_evidence={
                        "trigger": "SELL_PRICE_CHANGE_RESET",
                        "source_plan_generation": proposal.get("source_plan_generation"),
                        "trigger_snapshot_hash": trigger_snapshot.get("snapshot_hash"),
                        "trigger_snapshot": trigger_snapshot,
                        "original_remaining_quantity": proposal.get("remaining_quantity"),
                    },
                )
                price_reset_summary["cancel_requested"] = int(price_reset_summary["cancel_requested"]) + int(cancel_result.get("cancel_requested", 0) or 0)
                price_reset_summary["cancel_pending"] = int(price_reset_summary["cancel_pending"]) + int(cancel_result.get("cancel_pending", 0) or 0)
                if cancel_result.get("ok") is not True:
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
            if callable(enqueue_price_reset_generation):
                for proposal in reset_inspection.get("replan_proposals") or []:
                    if not isinstance(proposal, dict):
                        continue
                    appended = enqueue_price_reset_generation(proposal, apply_approval=True)
                    price_reset_summary["orders_created"] = int(price_reset_summary["orders_created"]) + int(appended.get("orders_created", 0) or 0)
                    price_reset_summary["deferred_replans"] = int(price_reset_summary["deferred_replans"]) + int(appended.get("deferred") is True)
                    price_reset_summary["executable_order_ids"].extend(
                        str(value or "").strip()
                        for value in appended.get("executable_order_ids", [])
                        if str(value or "").strip()
                    )
                    if appended.get("ok") is not True:
                        price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("SELL price-change reset inspection failed")
            price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1

    # BUY price-reset uses the same read-only inspector and the existing
    # generic cancel/replan pipeline.  Keep it alongside SELL so one stock's
    # reset state cannot stop unrelated execution.
    if callable(inspect_buy_price_resets) and buy_exit_priority_ready:
        try:
            reset_prices = {
                code: actionable_current_price(window, code)
                for code in eligible_execution_codes
            }
            buy_reset_inspection = inspect_buy_price_resets(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code=reset_prices,
                blocked_execution_process_ids=buy_exit_blocked_process_ids,
                current_orderable_cash=(
                    window.current_orderable_cash_for_budget()
                    if callable(getattr(window, "current_orderable_cash_for_budget", None))
                    else None
                ),
                cancel_limit=5,
            )
            price_reset_summary["cancel_proposals"] = int(price_reset_summary["cancel_proposals"]) + len(
                buy_reset_inspection.get("cancel_proposals") or []
            )
            price_reset_summary["replan_proposals"] = int(price_reset_summary["replan_proposals"]) + len(
                buy_reset_inspection.get("replan_proposals") or []
            )
            price_reset_summary["waiting"] = int(price_reset_summary["waiting"]) + len(
                buy_reset_inspection.get("waiting") or []
            )
            price_reset_summary["errors"] = int(price_reset_summary["errors"]) + len(
                buy_reset_inspection.get("errors") or []
            )
            price_reset_summary["blocked_execution_process_ids"] = list(
                dict.fromkeys(
                    list(price_reset_summary.get("blocked_execution_process_ids") or [])
                    + list(buy_reset_inspection.get("blocked_execution_process_ids") or [])
                )
            )
            for review in buy_reset_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="BUY_PRICE_RESET_RECONCILIATION",
                ):
                    price_reset_summary["reviews"] = int(price_reset_summary["reviews"]) + 1

            cancel_requester = getattr(window, "queue_open_order_cancel_automatically", None)
            if not callable(cancel_requester):
                host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
                host = host_reader() if callable(host_reader) else None
                cancel_requester = getattr(host, "queue_open_order_cancel_automatically", None)
            for proposal in buy_reset_inspection.get("cancel_proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                if not callable(cancel_requester):
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
                    break
                trigger_snapshot = proposal.get("trigger_snapshot")
                trigger_snapshot = trigger_snapshot if isinstance(trigger_snapshot, dict) else {}
                cancel_result = cancel_requester(
                    str(proposal.get("order_queued_id") or ""),
                    expected_account_no=str(proposal.get("account_no") or ""),
                    expected_code=str(proposal.get("code") or ""),
                    expected_side="BUY",
                    expected_broker_order_no=str(proposal.get("broker_order_no") or ""),
                    cancel_evidence={
                        "trigger": "BUY_PRICE_CHANGE_RESET",
                        "source_plan_generation": proposal.get("source_plan_generation"),
                        "trigger_snapshot_hash": trigger_snapshot.get("snapshot_hash"),
                        "trigger_snapshot": trigger_snapshot,
                        "original_remaining_quantity": proposal.get("remaining_quantity"),
                    },
                )
                price_reset_summary["cancel_requested"] = int(price_reset_summary["cancel_requested"]) + int(cancel_result.get("cancel_requested", 0) or 0)
                price_reset_summary["cancel_pending"] = int(price_reset_summary["cancel_pending"]) + int(cancel_result.get("cancel_pending", 0) or 0)
                if cancel_result.get("ok") is not True:
                    price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
            if callable(enqueue_price_reset_generation):
                for proposal in buy_reset_inspection.get("replan_proposals") or []:
                    if not isinstance(proposal, dict):
                        continue
                    appended = enqueue_price_reset_generation(proposal, apply_approval=True)
                    price_reset_summary["orders_created"] = int(price_reset_summary["orders_created"]) + int(appended.get("orders_created", 0) or 0)
                    price_reset_summary["deferred_replans"] = int(price_reset_summary["deferred_replans"]) + int(appended.get("deferred") is True)
                    price_reset_summary["executable_order_ids"].extend(
                        str(value or "").strip()
                        for value in appended.get("executable_order_ids", [])
                        if str(value or "").strip()
                    )
                    if appended.get("ok") is not True:
                        price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("BUY price-change reset inspection failed")
            price_reset_summary["errors"] = int(price_reset_summary["errors"]) + 1

    reset_blocked_process_ids = tuple(dict.fromkeys(
        str(value or "").strip()
        for value in (
            list(buy_exit_blocked_process_ids)
            + list(sell_exit_blocked_process_ids)
            + list(price_reset_summary.get("blocked_execution_process_ids", []))
        )
        if str(value or "").strip()
    ))

    unfilled_cancel_summary: dict[str, object] = {
        "proposals": 0,
        "cancel_requested": 0,
        "cancel_pending": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
    }
    if callable(inspect_unfilled_sell_cancel_eligibility):
        try:
            cancel_inspection = inspect_unfilled_sell_cancel_eligibility(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                limit=5,
            )
            unfilled_cancel_summary["proposals"] = len(
                cancel_inspection.get("proposals") or []
            )
            unfilled_cancel_summary["waiting"] = len(
                cancel_inspection.get("waiting") or []
            )
            unfilled_cancel_summary["errors"] = len(
                cancel_inspection.get("errors") or []
            )
            for review in cancel_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    unfilled_cancel_summary["errors"] = int(
                        unfilled_cancel_summary["errors"]
                    ) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="UNFILLED_TIMEOUT_CANCEL_RECONCILIATION",
                ):
                    unfilled_cancel_summary["reviews"] = int(
                        unfilled_cancel_summary["reviews"]
                    ) + 1

            cancel_requester = getattr(
                window,
                "queue_open_order_cancel_automatically",
                None,
            )
            if not callable(cancel_requester):
                host_reader = getattr(
                    window,
                    "main_monitoring_auto_trade_operation_host",
                    None,
                )
                host = host_reader() if callable(host_reader) else None
                cancel_requester = getattr(
                    host,
                    "queue_open_order_cancel_automatically",
                    None,
                )
            for proposal in cancel_inspection.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                if not callable(cancel_requester):
                    unfilled_cancel_summary["errors"] = int(
                        unfilled_cancel_summary["errors"]
                    ) + 1
                    break
                cancel_result = cancel_requester(
                    str(proposal.get("order_queued_id") or ""),
                    expected_account_no=str(proposal.get("account_no") or ""),
                    expected_code=str(proposal.get("code") or ""),
                    expected_side=str(proposal.get("side") or ""),
                    expected_broker_order_no=str(
                        proposal.get("broker_order_no") or ""
                    ),
                    cancel_evidence={
                        "trigger": "UNFILLED_TIMEOUT",
                        "scope": proposal.get("scope"),
                        "timeout_ms": proposal.get("timeout_ms"),
                        "timeout_anchor": proposal.get("timeout_anchor"),
                        "timeout_anchor_at": proposal.get("timeout_anchor_at"),
                        "timeout_due_at": proposal.get("timeout_due_at"),
                        "original_remaining_quantity": proposal.get(
                            "remaining_quantity"
                        ),
                    },
                )
                unfilled_cancel_summary["cancel_requested"] = int(
                    unfilled_cancel_summary["cancel_requested"]
                ) + int(cancel_result.get("cancel_requested", 0) or 0)
                unfilled_cancel_summary["cancel_pending"] = int(
                    unfilled_cancel_summary["cancel_pending"]
                ) + int(cancel_result.get("cancel_pending", 0) or 0)
                if cancel_result.get("ok") is not True:
                    unfilled_cancel_summary["errors"] = int(
                        unfilled_cancel_summary["errors"]
                    ) + 1
        except Exception:
            LOGGER.exception("SELL unfilled-timeout cancellation inspection failed")
            unfilled_cancel_summary["errors"] = int(
                unfilled_cancel_summary["errors"]
            ) + 1

    time_slice_summary: dict[str, object] = {
        "proposals": 0,
        "orders_created": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "executable_order_ids": [],
    }
    if callable(inspect_due_time_slices) and callable(enqueue_scheduled_time_slice):
        try:
            due_result = inspect_due_time_slices(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                blocked_execution_process_ids=reset_blocked_process_ids,
                actionable_prices_by_code={code: actionable_current_price(window, code) for code in eligible_execution_codes},
                current_orderable_cash=(
                    window.current_orderable_cash_for_budget()
                    if callable(getattr(window, "current_orderable_cash_for_budget", None)) else None
                ),
            )
            time_slice_summary["proposals"] = len(due_result.get("proposals") or [])
            time_slice_summary["waiting"] = len(due_result.get("waiting") or [])
            time_slice_summary["errors"] = len(due_result.get("errors") or [])
            for review in due_result.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    time_slice_summary["errors"] = int(time_slice_summary["errors"]) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="MULTI_TIME_DUE_RECONCILIATION",
                ):
                    time_slice_summary["reviews"] = int(time_slice_summary["reviews"]) + 1
            for proposal in due_result.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                appended = enqueue_scheduled_time_slice(proposal, apply_approval=True)
                time_slice_summary["orders_created"] = int(
                    time_slice_summary["orders_created"]
                ) + int(appended.get("orders_created", 0) or 0)
                time_slice_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in appended.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                if appended.get("ok") is not True:
                    time_slice_summary["errors"] = int(time_slice_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("MULTI_TIME due-child inspection failed")
            time_slice_summary["errors"] = int(time_slice_summary["errors"]) + 1

    ratio_slice_summary: dict[str, object] = {
        "proposals": 0,
        "orders_created": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "executable_order_ids": [],
    }
    if callable(inspect_eligible_ratio_slices) and callable(enqueue_eligible_ratio_slice):
        try:
            actionable_prices = {
                code: actionable_current_price(window, code)
                for code in eligible_execution_codes
            }
            ratio_result = inspect_eligible_ratio_slices(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code=actionable_prices,
                blocked_execution_process_ids=reset_blocked_process_ids,
                current_orderable_cash=(
                    window.current_orderable_cash_for_budget()
                    if callable(getattr(window, "current_orderable_cash_for_budget", None)) else None
                ),
            )
            ratio_slice_summary["proposals"] = len(ratio_result.get("proposals") or [])
            ratio_slice_summary["waiting"] = len(ratio_result.get("waiting") or [])
            ratio_slice_summary["errors"] = len(ratio_result.get("errors") or [])
            for review in ratio_result.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    ratio_slice_summary["errors"] = int(ratio_slice_summary["errors"]) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="MULTI_RATIO_ELIGIBILITY_RECONCILIATION",
                ):
                    ratio_slice_summary["reviews"] = int(ratio_slice_summary["reviews"]) + 1
            for proposal in ratio_result.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                appended = enqueue_eligible_ratio_slice(proposal, apply_approval=True)
                ratio_slice_summary["orders_created"] = int(
                    ratio_slice_summary["orders_created"]
                ) + int(appended.get("orders_created", 0) or 0)
                ratio_slice_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in appended.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                if appended.get("ok") is not True:
                    ratio_slice_summary["errors"] = int(ratio_slice_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("MULTI_RATIO eligible-child inspection failed")
            ratio_slice_summary["errors"] = int(ratio_slice_summary["errors"]) + 1

    supplement_summary: dict[str, object] = {
        "proposals": 0,
        "orders_created": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "executable_order_ids": [],
    }
    if callable(inspect_execution_process_supplements) and callable(
        enqueue_replanned_execution_intents
    ):
        try:
            inspected = inspect_execution_process_supplements(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                blocked_execution_process_ids=reset_blocked_process_ids,
            )
            supplement_summary["proposals"] = len(inspected.get("proposals") or [])
            supplement_summary["waiting"] = len(inspected.get("waiting") or [])
            supplement_summary["errors"] = len(inspected.get("errors") or [])
            for review in inspected.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    supplement_summary["errors"] = int(supplement_summary["errors"]) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="MULTI_HOGA_POST_BATCH_RECONCILIATION",
                ):
                    supplement_summary["reviews"] = int(supplement_summary["reviews"]) + 1
            for proposal in inspected.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                appended = enqueue_replanned_execution_intents(
                    proposal.get("signal"),
                    proposal.get("execution_intents"),
                    apply_approval=True,
                )
                supplement_summary["orders_created"] = int(
                    supplement_summary["orders_created"]
                ) + int(appended.get("orders_created", 0) or 0)
                supplement_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in appended.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                if appended.get("ok") is not True:
                    supplement_summary["errors"] = int(supplement_summary["errors"]) + 1
        except Exception:
            LOGGER.exception("MULTI_HOGA post-batch supplement inspection failed")
            supplement_summary["errors"] = int(supplement_summary["errors"]) + 1

    repeat_sell_summary: dict[str, object] = {
        "proposals": 0,
        "exit_proposals": int(sell_exit_precedence_summary["exit_proposals"]),
        "exits_recorded": int(sell_exit_precedence_summary["exits_recorded"]),
        "orders_created": 0,
        "deferred_replans": 0,
        "reviews": int(sell_exit_precedence_summary["reviews"]),
        "waiting": int(sell_exit_precedence_summary["waiting"]),
        "errors": int(sell_exit_precedence_summary["errors"]),
        "executable_order_ids": [],
    }
    if callable(inspect_sell_repeat_generations) and callable(
        enqueue_repeat_sell_generation
    ):
        try:
            repeat_prices = {
                code: actionable_current_price(window, code)
                for code in eligible_execution_codes
            }
            repeat_inspection = inspect_sell_repeat_generations(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                actionable_prices_by_code=repeat_prices,
                blocked_execution_process_ids=reset_blocked_process_ids,
                proposal_limit=5,
            )
            repeat_sell_summary["proposals"] = len(
                repeat_inspection.get("proposals") or []
            )
            repeat_sell_summary["exit_proposals"] = int(
                repeat_sell_summary["exit_proposals"]
            ) + len(
                repeat_inspection.get("exit_proposals") or []
            )
            repeat_sell_summary["waiting"] = int(
                repeat_sell_summary["waiting"]
            ) + len(
                repeat_inspection.get("waiting") or []
            )
            repeat_sell_summary["errors"] = int(
                repeat_sell_summary["errors"]
            ) + len(
                repeat_inspection.get("errors") or []
            )
            for review in repeat_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    repeat_sell_summary["errors"] = int(
                        repeat_sell_summary["errors"]
                    ) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="SELL_REPEAT_RECONCILIATION",
                ):
                    repeat_sell_summary["reviews"] = int(
                        repeat_sell_summary["reviews"]
                    ) + 1
            for exit_proposal in repeat_inspection.get("exit_proposals") or []:
                if not isinstance(exit_proposal, dict):
                    continue
                if not callable(record_repeat_sell_exit):
                    repeat_sell_summary["errors"] = int(
                        repeat_sell_summary["errors"]
                    ) + 1
                    continue
                recorded = record_repeat_sell_exit(exit_proposal)
                if recorded.get("ok") is True:
                    repeat_sell_summary["exits_recorded"] = int(
                        repeat_sell_summary["exits_recorded"]
                    ) + 1
                else:
                    repeat_sell_summary["errors"] = int(
                        repeat_sell_summary["errors"]
                    ) + 1
            for proposal in repeat_inspection.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                appended = enqueue_repeat_sell_generation(
                    proposal,
                    apply_approval=True,
                )
                repeat_sell_summary["orders_created"] = int(
                    repeat_sell_summary["orders_created"]
                ) + int(appended.get("orders_created", 0) or 0)
                repeat_sell_summary["deferred_replans"] = int(
                    repeat_sell_summary["deferred_replans"]
                ) + int(appended.get("deferred") is True)
                repeat_sell_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in appended.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                if appended.get("ok") is not True:
                    repeat_sell_summary["errors"] = int(
                        repeat_sell_summary["errors"]
                    ) + 1
        except Exception:
            LOGGER.exception("SELL follow-up repeat inspection failed")
            repeat_sell_summary["errors"] = int(repeat_sell_summary["errors"]) + 1

    final_residual_exit_summary: dict[str, object] = {
        "proposals": 0,
        "completion_proposals": 0,
        "completions_recorded": 0,
        "orders_created": 0,
        "reviews": 0,
        "waiting": 0,
        "errors": 0,
        "executable_order_ids": [],
    }
    if callable(inspect_sell_final_residual_exits) and callable(
        enqueue_final_residual_sell_exit
    ):
        try:
            final_inspection = inspect_sell_final_residual_exits(
                selected_account_no=selected_account_no,
                allowed_stock_codes=eligible_execution_codes,
                proposal_limit=5,
            )
            final_residual_exit_summary["proposals"] = len(
                final_inspection.get("proposals") or []
            )
            final_residual_exit_summary["completion_proposals"] = len(
                final_inspection.get("completion_proposals") or []
            )
            final_residual_exit_summary["waiting"] = len(
                final_inspection.get("waiting") or []
            )
            final_residual_exit_summary["errors"] = len(
                final_inspection.get("errors") or []
            )
            for review in final_inspection.get("reviews") or []:
                if not isinstance(review, dict):
                    continue
                entry = entries_by_code.get(str(review.get("code") or "").strip())
                if entry is None or not callable(mark_review):
                    final_residual_exit_summary["errors"] = int(
                        final_residual_exit_summary["errors"]
                    ) + 1
                    continue
                if mark_review(
                    entry.stock_dir,
                    entry.stock_code,
                    entry.stock_name,
                    review,
                    source="SELL_FINAL_RESIDUAL_EXIT_RECONCILIATION",
                ):
                    final_residual_exit_summary["reviews"] = int(
                        final_residual_exit_summary["reviews"]
                    ) + 1
            for completion in final_inspection.get("completion_proposals") or []:
                if not isinstance(completion, dict):
                    continue
                if not callable(record_final_residual_sell_exit_completion):
                    final_residual_exit_summary["errors"] = int(
                        final_residual_exit_summary["errors"]
                    ) + 1
                    continue
                recorded = record_final_residual_sell_exit_completion(completion)
                if recorded.get("ok") is True:
                    final_residual_exit_summary["completions_recorded"] = int(
                        final_residual_exit_summary["completions_recorded"]
                    ) + 1
                else:
                    final_residual_exit_summary["errors"] = int(
                        final_residual_exit_summary["errors"]
                    ) + 1
            for proposal in final_inspection.get("proposals") or []:
                if not isinstance(proposal, dict):
                    continue
                appended = enqueue_final_residual_sell_exit(
                    proposal,
                    apply_approval=True,
                )
                final_residual_exit_summary["orders_created"] = int(
                    final_residual_exit_summary["orders_created"]
                ) + int(appended.get("orders_created", 0) or 0)
                final_residual_exit_summary["executable_order_ids"].extend(
                    str(value or "").strip()
                    for value in appended.get("executable_order_ids", [])
                    if str(value or "").strip()
                )
                if appended.get("ok") is not True:
                    final_residual_exit_summary["errors"] = int(
                        final_residual_exit_summary["errors"]
                    ) + 1
        except Exception:
            LOGGER.exception("SELL final residual MARKET exit inspection failed")
            final_residual_exit_summary["errors"] = int(
                final_residual_exit_summary["errors"]
            ) + 1

    consumer_result = consume_pending_routine_signals_dry_run(
        limit=5,
        mark_previewed=True,
        write_order_queue=True,
        apply_approval=True,
        allowed_stock_codes=allowed_stock_codes,
        signal_cutoff_by_stock_code=signal_cutoff_by_stock_code,
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
            executable_order_ids = [
                str(value or "").strip()
                for value in (
                    list(time_slice_summary.get("executable_order_ids") or [])
                    + list(price_reset_summary.get("executable_order_ids") or [])
                    + list(ratio_slice_summary.get("executable_order_ids") or [])
                    + list(supplement_summary.get("executable_order_ids") or [])
                    + list(repeat_sell_summary.get("executable_order_ids") or [])
                    + list(buy_exit_summary.get("executable_order_ids") or [])
                    + list(final_residual_exit_summary.get("executable_order_ids") or [])
                    + list(summary.get("executable_order_ids", []) or [])
                )
                if str(value or "").strip()
            ]
            if executable_order_ids:
                auto_result = auto_executor(
                    limit=max(5, len(executable_order_ids)),
                    order_ids=executable_order_ids,
                )
            else:
                auto_result = auto_executor(limit=5)
            processed = int(auto_result.get("processed", 0) or 0)
            auto_blocked = int(auto_result.get("blocked", 0) or 0)
            signal_result["orders_processed"] = processed
            signal_result["orders_blocked"] = auto_blocked
            if processed > 0 or auto_blocked > 0:
                window.statusBarMessage(
                    f"실자동매매 주문처리: 실행 {processed} / 차단 {auto_blocked}"
                )
    signal_result["supplement"] = dict(supplement_summary)
    signal_result["time_slice"] = dict(time_slice_summary)
    signal_result["ratio_slice"] = dict(ratio_slice_summary)
    signal_result["unfilled_cancel"] = dict(unfilled_cancel_summary)
    signal_result["price_reset"] = dict(price_reset_summary)
    signal_result["repeat_sell"] = dict(repeat_sell_summary)
    signal_result["buy_exit"] = dict(buy_exit_summary)
    signal_result["final_residual_exit"] = dict(final_residual_exit_summary)
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
