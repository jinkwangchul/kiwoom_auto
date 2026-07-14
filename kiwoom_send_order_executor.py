# -*- coding: utf-8 -*-
"""Execute a Kiwoom SendOrder-like callable from a validated call preview.

The executor is intentionally narrow: it calls only the injected callable once
and returns a structured result. It never imports or touches Kiwoom GUI objects,
runtime writers, queue writers, recorders, or Chejan handlers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_queue_writer import (
    mark_send_order_attempted,
    mark_send_order_call_in_progress,
    record_broker_send_accepted,
    record_broker_send_rejected,
    record_broker_send_uncertain,
)


STATUS_SENT = "SEND_ORDER_SENT"
STATUS_FAILED = "SEND_ORDER_FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"
STATUS_SEND_CALL_ACCEPTED = "SEND_CALL_ACCEPTED"
STATUS_SEND_CALL_REJECTED = "SEND_CALL_REJECTED"
STATUS_SEND_UNCERTAIN = "SEND_UNCERTAIN"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    send_order_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    send_order_called: bool = False,
    broker_called: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "send_order_result": deepcopy(send_order_result) if isinstance(send_order_result, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "send_order_called": bool(send_order_called),
        "broker_called": bool(broker_called),
        "runtime_write": False,
        "queue_write": False,
        "recorded": False,
    }


def _return_code(raw_result: Any) -> int | None:
    if isinstance(raw_result, bool):
        return None
    if isinstance(raw_result, int):
        return raw_result
    if isinstance(raw_result, str):
        try:
            return int(raw_result.strip())
        except ValueError:
            return None
    if isinstance(raw_result, dict):
        for key in ("return_code", "send_order_return_code", "code", "result_code"):
            value = raw_result.get(key)
            if isinstance(value, bool):
                continue
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                continue
    return None


def execute_kiwoom_send_order(
    call_preview_result: Any,
    send_order_adapter: Any,
    execution_context: Any,
) -> dict[str, Any]:
    """Call the injected SendOrder-like adapter once when all gates are open."""
    preview = _as_dict(call_preview_result)
    context = _as_dict(execution_context)

    if not preview:
        return _result(status=STATUS_INVALID, issues=["call_preview_result must be a dict"])
    if not isinstance(execution_context, dict):
        return _result(status=STATUS_INVALID, issues=["execution_context must be a dict"])

    preview_status = _text(preview.get("status")).upper()
    if preview_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["call_preview_result.status is INVALID"] + list(preview.get("issues") or []),
        )
    if preview_status != "SEND_ORDER_CALL_READY":
        return _result(
            status=STATUS_BLOCKED,
            issues=["call_preview_result.status is not SEND_ORDER_CALL_READY"] + list(preview.get("issues") or []),
        )
    if preview.get("send_order_called") is not False or preview.get("broker_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["call preview already called send order or broker"])

    send_order_args = _as_list(preview.get("send_order_args"))
    if len(send_order_args) != 9:
        return _result(status=STATUS_INVALID, issues=["send_order_args must contain 9 values"])

    call_preview = _as_dict(preview.get("send_order_call_preview"))
    final_call_token = _text(call_preview.get("final_call_token") or context.get("final_call_token"))
    if not final_call_token:
        return _result(status=STATUS_BLOCKED, issues=["final_call_token is required"])
    if call_preview and call_preview.get("send_order_args_ready") is not True:
        return _result(status=STATUS_BLOCKED, issues=["send_order_call_preview.send_order_args_ready is not true"])

    if context.get("final_confirmation") is not True:
        return _result(status=STATUS_BLOCKED, issues=["execution_context.final_confirmation is not true"])
    if context.get("environment_send_order_enabled") is not True:
        return _result(status=STATUS_BLOCKED, issues=["execution_context.environment_send_order_enabled is not true"])

    if not callable(send_order_adapter):
        return _result(status=STATUS_INVALID, issues=["send_order_adapter must be callable"])

    args_for_adapter = deepcopy(send_order_args)
    try:
        raw_result = send_order_adapter(*args_for_adapter)
    except Exception as exc:  # pragma: no cover - directly covered by tests
        error_result = {
            "executor_stage": "send_order_adapter_exception",
            "final_call_token": final_call_token,
            "send_order_args": deepcopy(send_order_args),
            "exception": str(exc),
        }
        return _result(
            status=STATUS_ERROR,
            send_order_result=error_result,
            issues=[f"send_order_adapter raised exception: {exc}"],
            send_order_called=True,
            broker_called=True,
        )

    code = _return_code(raw_result)
    wrapped = {
        "executor_stage": "send_order_adapter_called",
        "final_call_token": final_call_token,
        "send_order_args": deepcopy(send_order_args),
        "raw_result": deepcopy(raw_result),
        "return_code": code,
    }
    if code == 0:
        return _result(
            status=STATUS_SENT,
            send_order_result=wrapped,
            warnings=list(preview.get("warnings") or []),
            send_order_called=True,
            broker_called=True,
        )
    return _result(
        status=STATUS_FAILED,
        send_order_result=wrapped,
        issues=["send_order_adapter returned non-zero or unknown return code"],
        warnings=list(preview.get("warnings") or []),
        send_order_called=True,
        broker_called=True,
    )


def _blocked_result(stage: str, reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "executor_type": "KIWOOM_CLAIMED_SEND_ORDER_EXECUTOR",
        "status": STATUS_BLOCKED,
        "executor_stage": stage,
        "callable_executed": False,
        "queue_result_recorded": False,
        "send_order_called": False,
        "broker_call_executed": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "automatic_retry_allowed": False,
        "manual_reconciliation_required": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }
    result.update(extra)
    return result


def _uncertain_result(stage: str, reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "executor_type": "KIWOOM_CLAIMED_SEND_ORDER_EXECUTOR",
        "status": STATUS_SEND_UNCERTAIN,
        "executor_stage": stage,
        "callable_executed": bool(extra.get("callable_executed", True)),
        "queue_result_recorded": False,
        "send_order_called": bool(extra.get("send_order_called", True)),
        "broker_call_executed": bool(extra.get("broker_call_executed", True)),
        "broker_api_called": bool(extra.get("broker_api_called", True)),
        "actual_order_sent": False,
        "automatic_retry_allowed": False,
        "manual_reconciliation_required": True,
        "uncertain_reason": reason,
        "blocked_reasons": [],
        "warnings": ["manual reconciliation required"],
    }
    result.update(extra)
    return result


def _merge_writer_result(prefix: str, writer_result: Any) -> dict[str, Any]:
    data = deepcopy(writer_result) if isinstance(writer_result, dict) else {}
    return {f"{prefix}_result": data}


def execute_claimed_send_order(
    queue_path: Any,
    identity: Any,
    dispatch_claim_id: str,
    claim_token: str,
    claim_owner: str,
    expected_revision: int | None,
    send_order_callable: Any,
    send_order_args: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Execute one already-claimed SendOrder through the durable queue lifecycle."""
    ctx = _as_dict(context)
    if expected_revision is None:
        return _blocked_result("revision_cas", "expected_revision is required")
    if not callable(send_order_callable):
        return _blocked_result("send_order_callable", "send_order_callable must be callable")
    if not isinstance(send_order_args, list) or len(send_order_args) != 9:
        return _blocked_result("send_order_args", "send_order_args must contain 9 values")

    attempt = mark_send_order_attempted(
        queue_path,
        identity,
        dispatch_claim_id=dispatch_claim_id,
        claim_token=claim_token,
        claim_owner=claim_owner,
        attempt_owner=ctx.get("send_order_attempt_owner") or claim_owner,
        attempt_source=ctx.get("send_order_attempt_source") or "claimed_send_order_executor",
        context=ctx,
        expected_revision=expected_revision,
        attempt_id=ctx.get("send_order_attempt_id"),
    )
    if attempt.get("committed") is not True or attempt.get("post_write_verified") is not True:
        return _blocked_result(
            "send_order_attempt",
            _first_reason(attempt, "send order attempt was not recorded"),
            **_merge_writer_result("attempt", attempt),
        )

    attempt_id = _text(attempt.get("send_order_attempt_id"))
    in_progress = mark_send_order_call_in_progress(
        queue_path,
        identity,
        dispatch_claim_id=dispatch_claim_id,
        send_order_attempt_id=attempt_id,
        context=ctx,
        expected_revision=attempt.get("revision_after"),
    )
    if in_progress.get("committed") is not True or in_progress.get("post_write_verified") is not True:
        return _blocked_result(
            "send_call_start",
            _first_reason(in_progress, "send call in-progress marker was not recorded"),
            **_merge_writer_result("attempt", attempt),
            **_merge_writer_result("in_progress", in_progress),
        )

    args_for_callable = deepcopy(send_order_args)
    raw_result: Any = None
    callable_error = ""
    try:
        raw_result = send_order_callable(*args_for_callable)
    except Exception as exc:  # pragma: no cover - covered by tests
        callable_error = str(exc)

    result_revision = in_progress.get("revision_after")
    if callable_error:
        recorded = record_broker_send_uncertain(
            queue_path,
            identity,
            dispatch_claim_id=dispatch_claim_id,
            send_order_attempt_id=attempt_id,
            uncertain_reason=f"send_order_callable raised exception: {callable_error}",
            context=ctx,
            expected_revision=result_revision,
        )
        if recorded.get("committed") is not True or recorded.get("post_write_verified") is not True:
            return _uncertain_result(
                "send_call_result_record",
                "callable executed but uncertain result record failed",
                callable_executed=True,
                callable_exception=callable_error,
                raw_result=None,
                **_merge_writer_result("attempt", attempt),
                **_merge_writer_result("in_progress", in_progress),
                **_merge_writer_result("record", recorded),
            )
        return _finish_claimed_call_result(recorded, attempt, in_progress, raw_result=None, callable_exception=callable_error)

    code = _return_code(raw_result)
    if code == 0:
        recorded = record_broker_send_accepted(
            queue_path,
            identity,
            dispatch_claim_id=dispatch_claim_id,
            send_order_attempt_id=attempt_id,
            broker_return_code=code,
            context=ctx,
            expected_revision=result_revision,
        )
    elif code is None:
        recorded = record_broker_send_uncertain(
            queue_path,
            identity,
            dispatch_claim_id=dispatch_claim_id,
            send_order_attempt_id=attempt_id,
            uncertain_reason="send order return code is unknown",
            context=ctx,
            expected_revision=result_revision,
        )
    else:
        recorded = record_broker_send_rejected(
            queue_path,
            identity,
            dispatch_claim_id=dispatch_claim_id,
            send_order_attempt_id=attempt_id,
            broker_return_code=code,
            broker_error_code=code,
            broker_error_message="send order callable returned non-zero code",
            context=ctx,
            expected_revision=result_revision,
        )

    if recorded.get("committed") is not True or recorded.get("post_write_verified") is not True:
        return _uncertain_result(
            "send_call_result_record",
            "callable executed but durable result record failed",
            callable_executed=True,
            raw_result=deepcopy(raw_result),
            return_code=code,
            **_merge_writer_result("attempt", attempt),
            **_merge_writer_result("in_progress", in_progress),
            **_merge_writer_result("record", recorded),
        )
    return _finish_claimed_call_result(recorded, attempt, in_progress, raw_result=raw_result, return_code=code)


def _first_reason(result: dict[str, Any], fallback: str) -> str:
    reasons = result.get("blocked_reasons")
    if isinstance(reasons, list) and reasons:
        return _text(reasons[0]) or fallback
    return fallback


def _finish_claimed_call_result(
    recorded: dict[str, Any],
    attempt: dict[str, Any],
    in_progress: dict[str, Any],
    *,
    raw_result: Any,
    return_code: Any = None,
    callable_exception: str = "",
) -> dict[str, Any]:
    status = _text(recorded.get("status")) or STATUS_SEND_UNCERTAIN
    return {
        "executor_type": "KIWOOM_CLAIMED_SEND_ORDER_EXECUTOR",
        "status": status,
        "executor_stage": "send_call_result_recorded",
        "callable_executed": True,
        "queue_result_recorded": True,
        "send_order_called": True,
        "broker_call_executed": True,
        "broker_api_called": True,
        "actual_order_sent": False,
        "send_call_result_known": recorded.get("send_call_result_known") is True,
        "send_call_accepted": recorded.get("send_call_accepted") is True,
        "send_call_rejected": recorded.get("send_call_rejected") is True,
        "send_uncertain": recorded.get("send_uncertain") is True,
        "automatic_retry_allowed": False,
        "manual_reconciliation_required": recorded.get("manual_reconciliation_required") is True,
        "return_code": return_code if return_code is not None else recorded.get("broker_return_code"),
        "raw_result": deepcopy(raw_result),
        "callable_exception": callable_exception,
        "send_order_attempt_id": attempt.get("send_order_attempt_id"),
        "dispatch_claim_id": attempt.get("dispatch_claim_id"),
        "attempt_result": deepcopy(attempt),
        "in_progress_result": deepcopy(in_progress),
        "record_result": deepcopy(recorded),
        "blocked_reasons": [],
        "warnings": list(recorded.get("warnings") or []),
    }
