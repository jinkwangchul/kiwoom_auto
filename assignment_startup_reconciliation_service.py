"""Startup Recovery integration for unfinished Assignment transactions."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Callable

from assignment_episode_linkage import (
    ASSIGNMENT_TRANSACTION_ABORTED,
    ASSIGNMENT_TRANSACTION_COMMITTED,
    ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
    ASSIGNMENT_TRANSACTION_ROLLED_BACK,
    AssignmentReconciliationResult,
    reconcile_incomplete_assignment_transactions,
)
from event_journal_production import append_production_event
from gui_auto_trade_integrity import (
    is_review_required_state,
    operator_review_location,
    operator_review_reason,
)
from production_recovery_contract import ACCOUNT_FAILED, STOCK_REVIEW_REQUIRED
from production_recovery_state_registry import ProductionRecoveryStateRegistry
from runtime_io import read_json_dict
from runtime_stock_state_mutation import mutate_runtime_stock_state
from stock_repository import StockRepository


LOGGER = logging.getLogger(__name__)

ASSIGNMENT_RECONCILIATION_REASON_CODE = "ASSIGNMENT_RECONCILIATION_REQUIRED"
ASSIGNMENT_RECONCILIATION_SOURCE = "PRODUCTION_RECOVERY"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _instance_id(identity: dict[str, str] | None) -> str:
    return str((identity or {}).get("instance_id") or "").strip() or "UNASSIGNED"


def _review_evidence_line(result: AssignmentReconciliationResult) -> str:
    return (
        f"{ASSIGNMENT_RECONCILIATION_REASON_CODE} "
        f"transaction_id={result.transaction_id or '-'} "
        f"journal_state={result.terminal_state or result.previous_state or '-'} "
        f"config_instance_id={_instance_id(result.config_identity)} "
        f"episode_instance_id={_instance_id(result.episode_identity)} "
        f"missing_dependency={'true' if result.classification == 'MISSING_DEPENDENCY' else 'false'} "
        f"reason_code={result.classification or 'UNKNOWN'}"
    )


def _emit_event(
    writer: Callable[..., dict[str, Any]],
    event_type: str,
    *,
    result: str,
    severity: str = "INFO",
    stock_code: str = "",
    transaction_id: str = "",
    reason_code: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    writer(
        event_type,
        severity=severity,
        result=result,
        source="assignment_startup_reconciliation_service.reconcile_assignment_startup",
        target_type=("STOCK" if stock_code else "ASSIGNMENT_TRANSACTION_SCAN"),
        target_id=stock_code or "assignment_startup_reconciliation",
        correlation_id=transaction_id,
        reason_code=reason_code,
        details=dict(details or {}),
    )


def _transition_stock_to_review(
    project_root: Path,
    stock_code: str,
    results: list[AssignmentReconciliationResult],
) -> dict[str, Any]:
    repository = StockRepository(project_root)
    record = repository.find_by_code(stock_code)
    if record is None:
        return {
            "ok": False,
            "changed": False,
            "stock_code": stock_code,
            "reason": "REGISTERED_STOCK_NOT_FOUND",
        }
    stock_dir = repository.resolve_stock_dir(stock_code, record.name)
    state_path = stock_dir / "state.json"
    if not state_path.is_file():
        return {
            "ok": False,
            "changed": False,
            "stock_code": stock_code,
            "reason": "STOCK_STATE_NOT_FOUND",
        }

    state = read_json_dict(state_path)
    evidence_lines = [_review_evidence_line(item) for item in results]
    current_detail = str(state.get("review_detail") or "")
    if is_review_required_state(state) and all(
        line in current_detail for line in evidence_lines
    ):
        return {
            "ok": True,
            "changed": False,
            "stock_code": stock_code,
            "reason": "ALREADY_REVIEW_REQUIRED",
        }

    timestamp = _now_text()
    mutation = mutate_runtime_stock_state(
        stock_dir,
        "REVIEW_REQUIRED",
        {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": operator_review_location(
                ASSIGNMENT_RECONCILIATION_SOURCE
            ),
            "review_reason": operator_review_reason(
                ASSIGNMENT_RECONCILIATION_REASON_CODE
            ),
            "review_detail": "\n".join(evidence_lines),
            "review_checked_at": timestamp,
            "review_entered_at": timestamp,
        },
        updated_at=timestamp,
        verify_readback=True,
    )
    return {
        "ok": mutation.ok,
        "changed": mutation.ok,
        "stock_code": stock_code,
        "reason": mutation.reason,
    }


def reconcile_assignment_startup(
    project_root: str | Path,
    *,
    event_writer: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Terminalize explained journals and isolate unexplained Stocks in Review."""

    root = Path(project_root)
    writer = event_writer or append_production_event
    try:
        reconciliation_results = tuple(
            reconcile_incomplete_assignment_transactions(root)
        )
    except Exception as exc:
        LOGGER.exception("Assignment startup reconciliation scan failed")
        summary = {
            "scanned": 0,
            "committed_terminalized": 0,
            "aborted_terminalized": 0,
            "rolled_back_terminalized": 0,
            "review_required": 0,
            "review_required_results": 0,
            "review_transitions": (),
            "review_transition_failures": 0,
            "review_stock_codes": (),
            "blocked_stock_codes": (),
            "errors": ({"reason": "ASSIGNMENT_JOURNAL_SCAN_FAILED", "error": str(exc)},),
            "results": (),
            "global_fail_closed": True,
            "other_stocks_continue": False,
        }
        _emit_event(
            writer,
            "RECOVERY_FAILED",
            result="FAILED",
            severity="ERROR",
            reason_code="ASSIGNMENT_JOURNAL_SCAN_FAILED",
            details={"error": str(exc)},
        )
        return summary

    review_by_stock: dict[str, list[AssignmentReconciliationResult]] = {}
    errors: list[dict[str, str]] = []
    for item in reconciliation_results:
        if item.review_required:
            if item.stock_code:
                review_by_stock.setdefault(item.stock_code, []).append(item)
            else:
                errors.append(
                    {
                        "transaction_id": item.transaction_id,
                        "reason": item.classification,
                        "error": item.reason,
                    }
                )

        if item.terminal_state in {
            ASSIGNMENT_TRANSACTION_COMMITTED,
            ASSIGNMENT_TRANSACTION_ABORTED,
            ASSIGNMENT_TRANSACTION_ROLLED_BACK,
        }:
            _emit_event(
                writer,
                "RECOVERY_COMPLETED",
                result="COMPLETED",
                stock_code=item.stock_code,
                transaction_id=item.transaction_id,
                reason_code=f"ASSIGNMENT_JOURNAL_{item.terminal_state}",
                details={"previous_state": item.previous_state},
            )
        elif item.review_required:
            _emit_event(
                writer,
                "RECOVERY_WARNING",
                result="REVIEW_REQUIRED",
                severity="WARNING",
                stock_code=item.stock_code,
                transaction_id=item.transaction_id,
                reason_code=ASSIGNMENT_RECONCILIATION_REASON_CODE,
                details={
                    "journal_state": item.previous_state,
                    "classification": item.classification,
                },
            )

    transitions: list[dict[str, Any]] = []
    for stock_code in sorted(review_by_stock):
        transition = _transition_stock_to_review(
            root,
            stock_code,
            review_by_stock[stock_code],
        )
        transitions.append(transition)
        if transition.get("ok") is not True:
            errors.append(
                {
                    "stock_code": stock_code,
                    "reason": "REVIEW_TRANSITION_FAILED",
                    "error": str(transition.get("reason") or "UNKNOWN"),
                }
            )
            _emit_event(
                writer,
                "RECOVERY_FAILED",
                result="FAILED",
                severity="ERROR",
                stock_code=stock_code,
                reason_code="ASSIGNMENT_REVIEW_TRANSITION_FAILED",
                details={"error": str(transition.get("reason") or "UNKNOWN")},
            )

    failed_codes = tuple(
        sorted(
            str(item.get("stock_code") or "")
            for item in transitions
            if item.get("ok") is not True and str(item.get("stock_code") or "")
        )
    )
    review_codes = tuple(sorted(review_by_stock))
    summary = {
        "scanned": len(reconciliation_results),
        "committed_terminalized": sum(
            1
            for item in reconciliation_results
            if item.terminal_state == ASSIGNMENT_TRANSACTION_COMMITTED
        ),
        "aborted_terminalized": sum(
            1
            for item in reconciliation_results
            if item.terminal_state == ASSIGNMENT_TRANSACTION_ABORTED
        ),
        "rolled_back_terminalized": sum(
            1
            for item in reconciliation_results
            if item.terminal_state == ASSIGNMENT_TRANSACTION_ROLLED_BACK
        ),
        "review_required": len(review_codes),
        "review_required_results": sum(
            1 for item in reconciliation_results if item.review_required
        ),
        "review_transitions": tuple(transitions),
        "review_transition_failures": len(failed_codes),
        "review_stock_codes": review_codes,
        "blocked_stock_codes": review_codes,
        "errors": tuple(errors),
        "results": tuple(asdict(item) for item in reconciliation_results),
        "global_fail_closed": any(not item.stock_code for item in reconciliation_results),
        "other_stocks_continue": not any(
            not item.stock_code for item in reconciliation_results
        ),
    }
    _emit_event(
        writer,
        "RECOVERY_WARNING" if errors else "RECOVERY_COMPLETED",
        result="COMPLETED_WITH_REVIEW" if review_codes else "COMPLETED",
        severity="WARNING" if errors or review_codes else "INFO",
        reason_code="ASSIGNMENT_RECONCILIATION_SCAN_COMPLETE",
        details={
            "scanned": summary["scanned"],
            "committed": summary["committed_terminalized"],
            "aborted": summary["aborted_terminalized"],
            "review_required": summary["review_required"],
            "errors": len(errors),
        },
    )
    return summary


def apply_assignment_reconciliation_to_production_registry(
    summary: dict[str, Any] | None,
    *,
    identity: Any,
    registry: ProductionRecoveryStateRegistry,
) -> dict[str, Any]:
    """Merge fail-closed Assignment results into the existing Recovery registry."""

    data = summary if isinstance(summary, dict) else {}
    if data.get("global_fail_closed") is True:
        failed = registry.fail_account(identity)
        return {
            "ok": False,
            "global_fail_closed": True,
            "registry_result": failed,
        }

    blocked_stock_codes = tuple(data.get("blocked_stock_codes") or ())
    if not blocked_stock_codes:
        return {
            "ok": True,
            "global_fail_closed": False,
            "updates": (),
            "registry_result": {"ok": True, "status": "UNCHANGED"},
        }
    context = registry.snapshot()
    if context is not None and context.account_status == ACCOUNT_FAILED:
        return {
            "ok": False,
            "global_fail_closed": True,
            "updates": (),
            "registry_result": {"ok": False, "status": ACCOUNT_FAILED},
        }

    updates: list[dict[str, Any]] = []
    for stock_code in blocked_stock_codes:
        update = registry.set_stock_result(
            identity,
            stock_code=stock_code,
            stock_status=STOCK_REVIEW_REQUIRED,
            review_required=True,
            reason_codes=(ASSIGNMENT_RECONCILIATION_REASON_CODE,),
        )
        updates.append(update)
        if update.get("ok") is not True:
            registry.fail_account(identity)
            return {
                "ok": False,
                "global_fail_closed": True,
                "updates": tuple(updates),
                "registry_result": update,
            }

    completed = registry.complete_account(identity)
    return {
        "ok": completed.get("ok") is True,
        "global_fail_closed": False,
        "updates": tuple(updates),
        "registry_result": completed,
    }
