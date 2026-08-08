# -*- coding: utf-8 -*-
"""Close-completion check facade.

It wraps the durable-file evaluator used by production mutation boundaries.  The
only mutation it may perform is the canonical NORMAL_ENDED operation-state write
after the evaluator proves every participant is durably complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from operation_close_completion_evaluator import (
    STATUS_CARRYOVER_DONE,
    STATUS_DONE,
    evaluate_operation_close_completion,
)
from operation_policy_gate import write_global_operation_normal_ended_state
from event_journal_trade_observer import observe_liquidation_completed


SOURCE_ORDER_FILL_STATE_COMMIT = "ORDER_FILL_STATE_COMMIT"
SOURCE_BROKER_HOLDING_COMMIT = "BROKER_HOLDING_COMMIT"
SOURCE_EARLY_CLOSE_DURABLE_UPDATE = "EARLY_CLOSE_DURABLE_UPDATE"
SOURCE_STARTUP_RECOVERY = "STARTUP_RECOVERY"

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
STOCKS_DIR = PROJECT_ROOT / "stocks"
OPERATION_STATE_PATH = RUNTIME_DIR / "operation_state.json"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
BROKER_HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"


def check_global_close_completion_after_durable_update(
    *,
    source: str,
    evaluator: Callable[..., dict[str, Any]] = evaluate_operation_close_completion,
    normal_end_writer: Callable[..., dict[str, Any]] = write_global_operation_normal_ended_state,
    normal_end_timestamp: str | None = None,
    **evaluator_kwargs: Any,
) -> dict[str, Any]:
    """Run the evaluator and record NORMAL_ENDED only when it is proven safe."""

    clean_source = str(source or "").strip().upper()
    try:
        evaluator_result = evaluator(**evaluator_kwargs)
    except Exception as exc:
        return {
            "checked": False,
            "source": clean_source,
            "check_failed": True,
            "global_complete": False,
            "evaluator_result": None,
            "reasons": [str(exc)],
            "normal_end_write": None,
            "normal_end_write_failed": False,
            "normal_ended_applied": False,
            "operation_status_after": "",
        }

    reasons = list(evaluator_result.get("reasons") or [])
    operation_status_after = str(evaluator_result.get("operation_status") or "").strip().upper()
    result = {
        "checked": True,
        "source": clean_source,
        "check_failed": False,
        "global_complete": bool(evaluator_result.get("global_complete")),
        "evaluator_result": evaluator_result,
        "reasons": reasons,
        "normal_end_write": None,
        "normal_end_write_failed": False,
        "normal_ended_applied": False,
        "operation_status_after": operation_status_after,
    }
    if not _normal_end_write_allowed(evaluator_result):
        return result

    operation_state_path = evaluator_kwargs.get("operation_state_path", OPERATION_STATE_PATH)
    try:
        write_result = normal_end_writer(
            timestamp=normal_end_timestamp,
            operation_end_reason="ALL_PARTICIPANTS_COMPLETE",
            operation_state_path=operation_state_path,
        )
    except Exception as exc:
        return {
            **result,
            "normal_end_write": {"ok": False, "error": str(exc)},
            "normal_end_write_failed": True,
            "normal_ended_applied": False,
        }

    write_ok = bool(write_result.get("ok"))
    final_result = {
        **result,
        "normal_end_write": write_result,
        "normal_end_write_failed": not write_ok,
        "normal_ended_applied": write_ok,
        "operation_status_after": str(
            write_result.get("operation_status") or operation_status_after
        ).strip().upper(),
    }
    observe_liquidation_completed(final_result)
    return final_result


def check_global_close_completion_for_runtime_path(
    *,
    source: str,
    runtime_path: str | Path,
    stocks_dir: str | Path | None = None,
    evaluator: Callable[..., dict[str, Any]] = evaluate_operation_close_completion,
) -> dict[str, Any]:
    """Convenience wrapper for writers that know one canonical Runtime path."""

    runtime_dir = Path(runtime_path).resolve().parent
    project_root = runtime_dir.parent
    return check_global_close_completion_after_durable_update(
        source=source,
        operation_state_path=runtime_dir / "operation_state.json",
        stocks_dir=Path(stocks_dir) if stocks_dir is not None else project_root / "stocks",
        order_queue_path=runtime_dir / "order_queue.json",
        positions_path=runtime_dir / "positions.json",
        broker_holdings_path=runtime_dir / "broker_holdings.json",
        evaluator=evaluator,
    )


def _normal_end_write_allowed(evaluator_result: dict[str, Any]) -> bool:
    if evaluator_result.get("blocked"):
        return False
    if evaluator_result.get("global_complete") is not True:
        return False
    operation_status = str(evaluator_result.get("operation_status") or "").strip().upper()
    if operation_status != "CLOSING":
        return False
    participants = evaluator_result.get("participant_stock_codes")
    if not isinstance(participants, list) or not participants:
        return False
    if evaluator_result.get("blocking_stock_codes"):
        return False
    stock_results = evaluator_result.get("stock_results")
    if not isinstance(stock_results, list) or len(stock_results) != len(participants):
        return False
    complete_statuses = {STATUS_DONE, STATUS_CARRYOVER_DONE}
    for item in stock_results:
        if not isinstance(item, dict):
            return False
        if str(item.get("status") or "").strip().upper() not in complete_statuses:
            return False
    return True
