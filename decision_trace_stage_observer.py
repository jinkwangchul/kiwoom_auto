# -*- coding: utf-8 -*-
"""Fail-open observation of Approval, Policy, and Execution readiness results."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
from typing import Any

from decision_trace_contract import build_trace_record
from decision_trace_correlation import (
    DecisionTraceCorrelationResolver,
    default_trace_correlation_resolver,
)
from decision_trace_writer import DecisionTraceWriter


LOGGER = logging.getLogger(__name__)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _identity(order: Any, result: Any) -> dict[str, str]:
    order_dict = order if isinstance(order, dict) else {}
    result_dict = result if isinstance(result, dict) else {}
    return {
        "signal_id": _clean(result_dict.get("source_signal_id") or order_dict.get("source_signal_id")),
        "order_id": _clean(result_dict.get("order_id") or order_dict.get("order_id") or order_dict.get("id")),
        "execution_id": _clean(result_dict.get("execution_id") or order_dict.get("execution_id")),
    }


class ProductionDecisionTraceStageObserver:
    def __init__(
        self,
        *,
        resolver: DecisionTraceCorrelationResolver | None = None,
        writer: DecisionTraceWriter | None = None,
        now_factory=None,
    ) -> None:
        self.resolver = resolver or default_trace_correlation_resolver()
        self.writer = writer or DecisionTraceWriter()
        self._now_factory = now_factory or (lambda: datetime.now().astimezone())

    def append_stage(
        self,
        *,
        stage: str,
        stage_result: str,
        order: Any,
        result: Any,
        reason: str = "",
        reason_code: str = "",
        details: Any = None,
    ) -> dict[str, Any]:
        identities = _identity(order, result)
        correlation = self.resolver.resolve(**identities)
        if correlation is None:
            return {"status": "skipped", "reason": "TRACE_CORRELATION_UNAVAILABLE"}
        clean_result = _clean(stage_result).upper()
        if correlation.trace_level == "NORMAL" and clean_result != "BLOCKED":
            return {"status": "skipped", "reason": "NORMAL_PASSED_STAGE"}
        order_dict = order if isinstance(order, dict) else {}
        record = build_trace_record(
            trace_id=correlation.trace_id,
            recorded_at=self._now_factory().isoformat(),
            environment="LIVE",
            trace_level=correlation.trace_level,
            stage=stage,
            stage_result=clean_result,
            signal_id=identities["signal_id"] or None,
            order_id=identities["order_id"] or None,
            execution_id=identities["execution_id"] or None,
            stock_code=_clean(order_dict.get("code")) or None,
            stock_name=_clean(order_dict.get("name")) or None,
            routine_instance_id=_clean(order_dict.get("routine_instance_id") or order_dict.get("routine")) or None,
            reason_code=_clean(reason_code) or None,
            reason=_clean(reason) or None,
            details=deepcopy(details) if details is not None else None,
        )
        appended = self.writer.append_record(record)
        if not appended.get("appended") and not appended.get("duplicate"):
            return {"status": "failed", "writer_result": appended}
        self.resolver.register(
            trace_id=correlation.trace_id,
            trace_level=correlation.trace_level,
            **identities,
        )
        return {
            "status": "appended" if appended.get("appended") else "duplicate",
            "trace_id": correlation.trace_id,
            "record": record,
            "writer_result": appended,
        }

    def observe_approval(self, order: Any, result: Any) -> dict[str, Any]:
        value = result if isinstance(result, dict) else {}
        status = _clean(value.get("approval_status")).upper()
        if status not in {"APPROVED", "BLOCKED"}:
            return {"status": "skipped", "reason": "APPROVAL_NOT_FINAL"}
        reason = _clean(value.get("approval_reason"))
        return self.append_stage(
            stage="APPROVAL",
            stage_result="PASSED" if status == "APPROVED" else "BLOCKED",
            order=order,
            result=value,
            reason=reason,
            reason_code=status,
            details={"approval_status": status},
        )

    def observe_policy(self, order: Any, result: Any, *, gate_input: Any = None) -> dict[str, Any]:
        value = result if isinstance(result, dict) else {}
        status = _clean(value.get("policy_status") or value.get("after_status")).upper()
        if status not in {"EXECUTABLE", "BLOCKED_POLICY"}:
            return {"status": "skipped", "reason": "POLICY_NOT_FINAL"}
        reason = _clean(value.get("policy_reason") or value.get("reason"))
        details = {"policy_status": status}
        if isinstance(gate_input, dict):
            details["gate_input"] = deepcopy(gate_input)
        return self.append_stage(
            stage="POLICY",
            stage_result="PASSED" if status == "EXECUTABLE" else "BLOCKED",
            order=order,
            result=value,
            reason=reason,
            reason_code=status,
            details=details,
        )

    def observe_execution(
        self,
        order: Any,
        result: Any,
        *,
        execution_step: str,
        passed: bool,
    ) -> dict[str, Any]:
        value = result if isinstance(result, dict) else {}
        blocked = value.get("blocked_reasons") or value.get("issues") or []
        if not isinstance(blocked, list):
            blocked = [str(blocked)]
        reason = " / ".join(str(item) for item in blocked if str(item).strip())
        return self.append_stage(
            stage="EXECUTION",
            stage_result="PASSED" if passed else "BLOCKED",
            order=order,
            result=value,
            reason=reason,
            reason_code=_clean(execution_step).upper(),
            details={
                "execution_step": _clean(execution_step).upper(),
                "blocked_reasons": list(blocked),
            },
        )


_DEFAULT_OBSERVER = ProductionDecisionTraceStageObserver()


def _fail_open(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return getattr(_DEFAULT_OBSERVER, method)(*args, **kwargs)
    except Exception as exc:
        LOGGER.exception("Decision Trace %s observation failed open", method)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def observe_approval_result(order: Any, result: Any) -> dict[str, Any]:
    trace_result = _fail_open("observe_approval", order, result)
    try:
        from event_journal_trade_observer import observe_approval_blocked

        observe_approval_blocked(order, result)
    except Exception:
        LOGGER.exception("Event Journal approval observation failed open")
    return trace_result


def observe_policy_result(order: Any, result: Any, *, gate_input: Any = None) -> dict[str, Any]:
    trace_result = _fail_open("observe_policy", order, result, gate_input=gate_input)
    try:
        from event_journal_trade_observer import observe_policy_blocked

        observe_policy_blocked(order, result)
    except Exception:
        LOGGER.exception("Event Journal policy observation failed open")
    return trace_result


def observe_execution_result(
    order: Any,
    result: Any,
    *,
    execution_step: str,
    passed: bool,
) -> dict[str, Any]:
    trace_result = _fail_open(
        "observe_execution",
        order,
        result,
        execution_step=execution_step,
        passed=passed,
    )
    try:
        from event_journal_trade_observer import observe_execution_blocked

        observe_execution_blocked(
            order,
            result,
            execution_step=execution_step,
            passed=passed,
        )
    except Exception:
        LOGGER.exception("Event Journal execution observation failed open")
    return trace_result
