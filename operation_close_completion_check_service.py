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
    STATUS_EVIDENCE_CONFLICT,
    STATUS_HOLDING_REMAINS,
    STATUS_PENDING_ORDER,
    STATUS_UNKNOWN,
    evaluate_operation_close_completion,
)
from operation_policy_gate import write_global_operation_normal_ended_state
from event_journal_trade_observer import observe_liquidation_completed, observe_pnl_cycle_boundaries
from event_journal_production import (
    append_production_event,
    observe_owner_failure_transition,
    observe_production_exception,
)
from confirmable_pnl_cycle_service import record_completion_boundaries
from gui_auto_trade_integrity import operator_review_location, operator_review_reason
from gui_auto_trade_runtime import now_text
from runtime_stock_state_mutation import mutate_runtime_stock_state
from stock_long_hold_policy import ROUTE_CLOSE_INTENT


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
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="operation_close_completion",
            operation="evaluate_operation_close_completion",
            source="operation_close_completion_check_service.check_global_close_completion_after_durable_update",
            target_type="OPERATION",
            target_id="global_operation",
            target_name="전체 운영 종료 검증",
            reason_code="OPERATION_CLOSE_COMPLETION_EVALUATION_FAILED",
            owner=check_global_close_completion_after_durable_update,
            failure_scope=f"completion_evaluation:{clean_source}",
        )
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
    observe_owner_failure_transition(
        check_global_close_completion_after_durable_update,
        f"completion_evaluation:{clean_source}",
        active=False,
    )

    immediate_review_results = _apply_immediate_residual_reviews(
        evaluator_result,
        source=clean_source,
    )
    if any(item.get("changed") is True for item in immediate_review_results):
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
                "immediate_review_results": immediate_review_results,
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
        "immediate_review_results": immediate_review_results,
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
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="operation_close_completion",
            operation="write_global_operation_normal_ended_state",
            source="operation_close_completion_check_service.check_global_close_completion_after_durable_update",
            target_type="OPERATION",
            target_id="global_operation",
            target_name="전체 운영 종료 상태",
            reason_code="OPERATION_NORMAL_END_WRITE_FAILED",
            owner=check_global_close_completion_after_durable_update,
            failure_scope=f"normal_end_write:{clean_source}",
        )
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
    if write_ok:
        observe_owner_failure_transition(
            check_global_close_completion_after_durable_update,
            f"normal_end_write:{clean_source}",
            active=False,
        )
        operation_date = str(evaluator_result.get("operation_date") or "").strip()
        append_production_event(
            "OPERATION_STOPPED",
            event_id=f"operation-stopped:{operation_date or clean_source.lower()}",
            result="COMPLETED",
            source="operation_close_completion_check_service.check_global_close_completion_after_durable_update",
            template_args={"target": "전체"},
            target_type="OPERATION",
            target_id="global_operation",
            target_name="전체",
            reason_code="ALL_PARTICIPANTS_COMPLETE",
            details={"operation_date": operation_date},
        )
    observe_liquidation_completed(final_result)
    final_result["pnl_cycle_boundary_results"] = record_completion_boundaries(
        final_result,
        ledger_path=Path(operation_state_path).resolve().parent / "pnl_cycle_boundaries.json",
    )
    observe_pnl_cycle_boundaries(final_result["pnl_cycle_boundary_results"])
    return final_result


def _apply_immediate_residual_reviews(
    evaluator_result: dict[str, Any],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Persist terminal residual holdings through the existing stock-state writer."""

    results: list[dict[str, Any]] = []
    for item in evaluator_result.get("stock_results", ()):
        if not isinstance(item, dict):
            continue
        item_status = str(item.get("status") or "").strip().upper()
        reason_code_by_status = {
            STATUS_HOLDING_REMAINS: "HOLDING_REMAINS",
            STATUS_PENDING_ORDER: "PENDING_ORDER",
            STATUS_EVIDENCE_CONFLICT: "EVIDENCE_CONFLICT",
            STATUS_UNKNOWN: "EVIDENCE_CONFLICT",
        }
        reason_code = reason_code_by_status.get(item_status, "")
        if not reason_code:
            continue
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        route = evidence.get("termination_route")
        route = route if isinstance(route, dict) else {}
        if route.get("route_completed") is not True:
            continue
        stock_dir = str(evidence.get("stock_dir") or "").strip()
        if not stock_dir:
            results.append(
                {
                    "stock_code": str(item.get("stock_code") or ""),
                    "changed": False,
                    "ok": False,
                    "reason": "STOCK_DIRECTORY_UNAVAILABLE",
                }
            )
            continue
        results.append(
            mark_end_of_operation_review_required(
                stock_dir=stock_dir,
                stock_code=str(item.get("stock_code") or ""),
                reason_code=reason_code,
                termination_route=route,
                source=source,
            )
        )
    return results


def mark_end_of_operation_review_required(
    *,
    stock_dir: str | Path,
    stock_code: str,
    reason_code: str,
    termination_route: dict[str, object] | None,
    source: str,
) -> dict[str, object]:
    """Route terminal residuals through the existing canonical state writer."""

    route = termination_route if isinstance(termination_route, dict) else {}
    timestamp = now_text()
    reason = operator_review_reason(reason_code)
    route_name = str(route.get("route") or "UNKNOWN")
    method = str(route.get("method") or "-")
    route_source = str(route.get("source") or "-")
    mutation = mutate_runtime_stock_state(
        stock_dir,
        "REVIEW_REQUIRED",
        {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": operator_review_location("운영 종료"),
            "review_reason": reason,
            "review_detail": (
                f"{reason_code} / route={route_name} / method={method} / "
                f"route_source={route_source} / trigger={source}"
            ),
            "review_checked_at": timestamp,
            "review_entered_at": timestamp,
        },
        updated_at=timestamp,
        verify_readback=True,
    )
    return {
        "stock_code": str(stock_code or ""),
        "changed": mutation.ok,
        "ok": mutation.ok,
        "reason": mutation.reason,
        "route": route_name,
        "close_intent": route_name == ROUTE_CLOSE_INTENT,
    }


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
