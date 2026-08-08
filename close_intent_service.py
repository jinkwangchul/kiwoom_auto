# -*- coding: utf-8 -*-
"""Shared close-intent boundary for production close paths.

This module deliberately stays free of GUI, queue mutation, SendOrder, and
close/liquidation pipeline execution.  It coordinates the existing production
transition guard with the existing durable stock writers and the canonical
global operation-state writer.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from close_liquidation_transition_service import DOMAIN_CLOSE
from operation_command_service import (
    EarlyCloseCompatibility,
    MODE_EARLY_CLOSE,
    OperationCommandRequest,
    OperationCommandResult,
    OperationCommandService,
    RESULT_FAILED,
    SCOPE_STOCK,
    STOCK_APPLIED,
    STOCK_FAILED,
)
from operation_policy_gate import write_global_operation_closing_state
from transition_evidence_reader import (
    COMMAND_REQUEST_SCOPE,
    TIME_POLICY_SCOPE,
    TransitionEvidenceScope,
)
from transition_production_guard import evaluate_production_transition
from event_journal_trade_observer import observe_close_started


CLOSE_INTENT_AUTO_CLOSE = "AUTO_CLOSE"
CLOSE_INTENT_EARLY_CLOSE = "EARLY_CLOSE"

PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"


def apply_close_intent(*, intent: str, **kwargs: Any) -> dict[str, Any]:
    """Apply a close intent through the shared mutation boundary."""

    normalized_intent = str(intent or "").strip().upper()
    if normalized_intent == CLOSE_INTENT_AUTO_CLOSE:
        return apply_auto_close_intent(**kwargs)
    if normalized_intent == CLOSE_INTENT_EARLY_CLOSE:
        return apply_early_close_intent(**kwargs)
    return _result(
        ok=False,
        intent=normalized_intent,
        stock_code=str(kwargs.get("stock_code") or ""),
        blocked=True,
        reason="UNSUPPORTED_CLOSE_INTENT",
    )


def apply_auto_close_intent(
    *,
    stock_dir: str | Path,
    stock_code: str,
    stock_name: str,
    runtime_state: dict[str, Any],
    runtime_config: dict[str, Any],
    current_status: str,
    requested_status: str,
    metadata: dict[str, Any],
    log_suffix: str,
    status_writer: Callable[
        [Path, str, str, str, dict[str, Any], str],
        bool,
    ],
    read_back_checker: Callable[[Path, str, dict[str, Any]], bool],
    queue_path: str | Path = ORDER_QUEUE_PATH,
    fills_path: str | Path = FILLS_PATH,
    transition_guard: Callable[..., Any] = evaluate_production_transition,
    operation_state_writer: Callable[..., dict[str, Any]] = write_global_operation_closing_state,
    dry_run: bool = False,
) -> dict[str, Any]:
    stock_path = Path(stock_dir)
    code = str(stock_code or "").strip()
    requested = str(requested_status or "").strip().upper()
    snapshot_state = dict(runtime_state or {})
    snapshot_state.update(dict(metadata or {}))
    requested_method = str(snapshot_state.get("auto_close_method") or "").strip()
    requested_at = str(snapshot_state.get("auto_close_requested_at") or "").strip()
    requested_source = str(snapshot_state.get("auto_close_source") or "").strip()
    routine_instance_id = str(
        (runtime_config or {}).get("assigned_routine_instance_id") or ""
    ).strip()

    transition = transition_guard(
        policy_domain=DOMAIN_CLOSE,
        current_policy=requested_method,
        requested_policy=requested_method,
        queue_path=queue_path,
        fills_path=fills_path,
        runtime_state=snapshot_state,
        runtime_routine_instance_id=routine_instance_id,
        scope=TransitionEvidenceScope(
            scope_type=TIME_POLICY_SCOPE,
            stock_code=code,
            trade_date=requested_at[:10],
            routine_instance_id=routine_instance_id,
            auto_close_requested_at=requested_at,
            source=requested_source,
        ),
    )
    if not getattr(transition, "allowed", False):
        return _result(
            ok=False,
            intent=CLOSE_INTENT_AUTO_CLOSE,
            stock_code=code,
            blocked=True,
            reason=str(getattr(transition, "reason_code", "") or "TRANSITION_BLOCKED"),
            transition=transition,
        )
    if dry_run:
        return _result(
            ok=True,
            intent=CLOSE_INTENT_AUTO_CLOSE,
            stock_code=code,
            durable_applied=False,
            blocked=False,
            reason="",
            transition=transition,
        )

    def mutation() -> dict[str, Any]:
        write_ok = bool(
            status_writer(
                stock_path,
                code,
                str(stock_name or ""),
                requested,
                dict(metadata or {}),
                str(log_suffix or ""),
            )
        )
        read_back_ok = False
        if write_ok:
            read_back_ok = bool(read_back_checker(stock_path, requested, dict(metadata or {})))
        return {
            "ok": write_ok and read_back_ok,
            "durable_applied": write_ok and read_back_ok,
            "read_back_verified": read_back_ok,
            "write_result": {
                "status_written": write_ok,
                "read_back_verified": read_back_ok,
                "current_status": str(current_status or "").strip().upper(),
                "requested_status": requested,
            },
        }

    result = _apply_close_intent_mutation(
        CLOSE_INTENT_AUTO_CLOSE,
        stock_code=code,
        mutation=mutation,
        operation_state_writer=operation_state_writer,
    )
    observe_close_started(result, stock_name=stock_name, requested_at=requested_at)
    return result


def apply_early_close_intent(
    *,
    target_scope: str = SCOPE_STOCK,
    target_id: str,
    source: str,
    requested_policy: str,
    has_close_progress_quantity: bool = True,
    extra_policy: dict[str, Any] | None = None,
    stock_code: str = "",
    runtime_state: dict[str, Any] | None = None,
    runtime_routine_instance_id: str = "",
    current_policy: str = "",
    current_started_at: str = "",
    current_command_id: str = "",
    command_id: str = "",
    requested_at: str = "",
    project_root: str | Path = PROJECT_ROOT,
    queue_path: str | Path = ORDER_QUEUE_PATH,
    fills_path: str | Path = FILLS_PATH,
    operation_command_service_factory: Callable[..., OperationCommandService] = OperationCommandService,
    transition_guard: Callable[..., Any] | None = evaluate_production_transition,
    operation_state_writer: Callable[..., dict[str, Any]] = write_global_operation_closing_state,
) -> dict[str, Any]:
    scope = str(target_scope or "").strip().upper()
    code = str(stock_code or "").strip()
    method = str(requested_policy or "").strip()

    if scope == SCOPE_STOCK and transition_guard is not None:
        transition = transition_guard(
            policy_domain=DOMAIN_CLOSE,
            current_policy=str(current_policy or "").strip(),
            requested_policy=method,
            queue_path=queue_path,
            fills_path=fills_path,
            runtime_state=dict(runtime_state or {}),
            runtime_routine_instance_id=str(runtime_routine_instance_id or "").strip(),
            scope=TransitionEvidenceScope(
                scope_type=COMMAND_REQUEST_SCOPE,
                stock_code=code,
                trade_date=_transition_trade_date(current_started_at, requested_at),
                routine_instance_id=str(runtime_routine_instance_id or "").strip(),
                transition_requested_at=str(current_started_at or requested_at or "").strip(),
                operation_command_id=str(current_command_id or "").strip(),
            ),
        )
        if not getattr(transition, "allowed", False):
            return _result(
                ok=False,
                intent=CLOSE_INTENT_EARLY_CLOSE,
                stock_code=code,
                blocked=True,
                reason=str(
                    getattr(transition, "reason_code", "") or "TRANSITION_BLOCKED"
                ),
                transition=transition,
            )

    def mutation() -> dict[str, Any]:
        service = operation_command_service_factory(project_root)
        request = OperationCommandRequest(
            target_scope=scope,
            target_id=str(target_id or "").strip(),
            command=MODE_EARLY_CLOSE,
            source=str(source or "").strip(),
            command_id=str(command_id or "").strip(),
        )
        if scope == SCOPE_STOCK or method or extra_policy or not has_close_progress_quantity:
            command_result = service.apply_early_close(
                request,
                EarlyCloseCompatibility(
                    method=method,
                    policy=dict(extra_policy or {}),
                    has_close_progress_quantity=bool(has_close_progress_quantity),
                ),
            )
        else:
            command_result = service.apply(request)
        stock_results = tuple(getattr(command_result, "stock_results", ()) or ())
        failed_results = tuple(getattr(command_result, "failed", ()) or ())
        if not failed_results:
            failed_results = tuple(
                item
                for item in stock_results
                if str(getattr(item, "status", "") or "").strip().upper() == STOCK_FAILED
            )
        applied_results = tuple(getattr(command_result, "applied", ()) or ())
        if not applied_results:
            applied_results = tuple(
                item
                for item in stock_results
                if str(getattr(item, "status", "") or "").strip().upper() == STOCK_APPLIED
            )
        command_status = str(getattr(command_result, "status", "") or "").strip().upper()
        applied = (
            command_status != RESULT_FAILED
            and not failed_results
            and bool(stock_results)
        )
        reason = str(getattr(command_result, "error", "") or "")
        if failed_results:
            reason = str(getattr(failed_results[0], "error", "") or reason)
        return {
            "ok": bool(applied),
            "durable_applied": bool(applied_results),
            "write_result": command_result,
            "command_result": command_result,
            "read_back_verified": bool(applied),
            "reason": reason,
        }

    result = _apply_close_intent_mutation(
        CLOSE_INTENT_EARLY_CLOSE,
        stock_code=code,
        mutation=mutation,
        operation_state_writer=operation_state_writer,
    )
    observe_close_started(result, command_id=command_id, requested_at=requested_at)
    return result


def _apply_close_intent_mutation(
    intent: str,
    *,
    stock_code: str,
    mutation: Callable[[], dict[str, Any]],
    operation_state_writer: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    mutation_result = mutation()
    ok = bool(mutation_result.get("ok"))
    reason = str(mutation_result.get("reason") or "")
    if not ok and not reason:
        reason = "WRITE_FAILED"
    operation_state_write: dict[str, Any] | None = None
    operation_state_write_failed = False
    operation_closing_applied = False
    durable_applied = bool(mutation_result.get("durable_applied"))
    if durable_applied:
        operation_state_write = operation_state_writer(close_reason=intent)
        operation_closing_applied = bool(operation_state_write.get("ok"))
        operation_state_write_failed = not operation_closing_applied
        if operation_state_write_failed:
            ok = False
            if not reason:
                reason = str(
                    operation_state_write.get("reason")
                    or operation_state_write.get("error")
                    or "OPERATION_STATE_WRITE_FAILED"
                )
    return _result(
        ok=ok,
        intent=intent,
        stock_code=stock_code,
        durable_applied=durable_applied,
        blocked=False,
        reason=reason,
        write_result=mutation_result.get("write_result"),
        read_back_verified=bool(mutation_result.get("read_back_verified")),
        command_result=mutation_result.get("command_result"),
        operation_state_write=operation_state_write,
        operation_state_write_failed=operation_state_write_failed,
        operation_closing_applied=operation_closing_applied,
    )


def _transition_trade_date(timestamp: object, fallback: str) -> str:
    text = str(timestamp or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return str(fallback or "").strip()[:10]


def _result(
    *,
    ok: bool,
    intent: str,
    stock_code: str,
    durable_applied: bool = False,
    blocked: bool = False,
    reason: str = "",
    write_result: Any = None,
    read_back_verified: bool = False,
    command_result: OperationCommandResult | None = None,
    transition: Any = None,
    operation_state_write: dict[str, Any] | None = None,
    operation_state_write_failed: bool = False,
    operation_closing_applied: bool = False,
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "intent": str(intent or "").strip().upper(),
        "stock_code": str(stock_code or "").strip(),
        "durable_applied": bool(durable_applied),
        "blocked": bool(blocked),
        "reason": str(reason or "").strip(),
        "write_result": write_result,
        "read_back_verified": bool(read_back_verified),
        "command_result": command_result,
        "transition": transition,
        "operation_state_write": operation_state_write,
        "operation_state_write_failed": bool(operation_state_write_failed),
        "operation_closing_applied": bool(operation_closing_applied),
    }
