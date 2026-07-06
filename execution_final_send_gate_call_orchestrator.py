# -*- coding: utf-8 -*-
"""Call Final Send Gate service after preview-only open approval.

This is the first layer that may call final_send_gate_service.evaluate_final_send_gate.
It still never calls SendOrder, queue commit services, runtime commit services,
GUI, or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from final_send_gate_service import evaluate_final_send_gate


ORCHESTRATOR_TYPE = "EXECUTION_FINAL_SEND_GATE_CALL_ORCHESTRATOR"
STATUS_PASSED = "FINAL_SEND_GATE_PASSED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
OPEN_POLICY_READY = "READY_TO_OPEN_FINAL_SEND_GATE"
FINAL_SEND_GATE_READY = "READY_FOR_FINAL_SEND_GATE"
FINAL_SEND_GATE_SERVICE_REQUIRED = "FINAL_SEND_GATE_SERVICE_REQUIRED"
NEXT_STAGE_SEND_ORDER_ENTRYPOINT_REQUIRED = "SEND_ORDER_ENTRYPOINT_REQUIRED"
NEXT_STAGE_BLOCKED = "BLOCKED"


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
    final_send_gate_called: bool = False,
    next_stage: str = NEXT_STAGE_BLOCKED,
    final_send_gate_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "final_send_gate_called": final_send_gate_called,
        "next_stage": next_stage,
        "final_send_gate_result": deepcopy(final_send_gate_result) if isinstance(final_send_gate_result, dict) else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked_from(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def call_final_send_gate_after_open_policy(
    final_send_gate_open_policy_result: Any,
    final_send_gate_orchestrator_result: Any,
) -> dict[str, Any]:
    """Call Final Send Gate only after explicit open policy approval."""
    if not isinstance(final_send_gate_open_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FINAL_SEND_GATE_OPEN_POLICY_RESULT"])
    if not isinstance(final_send_gate_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FINAL_SEND_GATE_ORCHESTRATOR_RESULT"])

    warnings = _as_list(final_send_gate_open_policy_result.get("warnings")) + _as_list(
        final_send_gate_orchestrator_result.get("warnings")
    )

    open_status = _text(final_send_gate_open_policy_result.get("status"))
    if open_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(final_send_gate_open_policy_result.get("issues")) or ["FINAL_SEND_GATE_OPEN_POLICY_INVALID"],
            warnings=warnings,
        )
    if open_status != OPEN_POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(final_send_gate_open_policy_result.get("issues")) or ["FINAL_SEND_GATE_OPEN_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if final_send_gate_open_policy_result.get("final_send_gate_call_allowed") is not True:
        return _blocked_from("FINAL_SEND_GATE_CALL_NOT_ALLOWED", warnings)

    orchestrator_status = _text(final_send_gate_orchestrator_result.get("status"))
    if orchestrator_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(final_send_gate_orchestrator_result.get("issues")) or ["FINAL_SEND_GATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if orchestrator_status != FINAL_SEND_GATE_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(final_send_gate_orchestrator_result.get("issues")) or ["FINAL_SEND_GATE_ORCHESTRATOR_NOT_READY"],
            warnings=warnings,
        )
    if final_send_gate_orchestrator_result.get("final_send_gate_ready") is not True:
        return _blocked_from("FINAL_SEND_GATE_READY_NOT_TRUE", warnings)
    if final_send_gate_orchestrator_result.get("next_stage") != FINAL_SEND_GATE_SERVICE_REQUIRED:
        return _blocked_from("FINAL_SEND_GATE_NEXT_STAGE_NOT_SERVICE_REQUIRED", warnings)

    final_input = _as_dict(final_send_gate_orchestrator_result.get("final_send_gate_input"))
    if not final_input:
        return _blocked_from("FINAL_SEND_GATE_INPUT_REQUIRED", warnings)

    adapter_preview_result = _as_dict(final_input.get("adapter_preview_result"))
    order_queued_record = _as_dict(final_input.get("order_queued_record"))
    current_guard = _as_dict(final_input.get("current_guard"))
    context = _as_dict(final_input.get("context"))
    missing = []
    if not adapter_preview_result:
        missing.append("ADAPTER_PREVIEW_RESULT_REQUIRED")
    if not order_queued_record:
        missing.append("ORDER_QUEUED_RECORD_REQUIRED")
    if not current_guard:
        missing.append("CURRENT_GUARD_REQUIRED")
    if not context:
        missing.append("CONTEXT_REQUIRED")
    if missing:
        return _result(status=STATUS_BLOCKED, issues=missing, warnings=warnings)

    gate_result = evaluate_final_send_gate(
        deepcopy(adapter_preview_result),
        deepcopy(order_queued_record),
        deepcopy(current_guard),
        context=deepcopy(context),
    )
    if not isinstance(gate_result, dict):
        return _result(
            status=STATUS_INVALID,
            final_send_gate_called=True,
            issues=["MALFORMED_FINAL_SEND_GATE_RESULT"],
            warnings=warnings,
        )

    if gate_result.get("final_send_gate_ok") is True and gate_result.get("next_stage") == NEXT_STAGE_SEND_ORDER_ENTRYPOINT_REQUIRED:
        return _result(
            status=STATUS_PASSED,
            final_send_gate_called=True,
            next_stage=NEXT_STAGE_SEND_ORDER_ENTRYPOINT_REQUIRED,
            final_send_gate_result=gate_result,
            warnings=warnings + _as_list(gate_result.get("warnings")),
        )

    return _result(
        status=STATUS_BLOCKED,
        final_send_gate_called=True,
        final_send_gate_result=gate_result,
        issues=_as_list(gate_result.get("blocked_reasons")) or ["FINAL_SEND_GATE_BLOCKED"],
        warnings=warnings + _as_list(gate_result.get("warnings")),
    )
