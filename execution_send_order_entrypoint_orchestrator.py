# -*- coding: utf-8 -*-
"""Call SendOrder EntryPoint after preview-only open approval.

This is the first layer that may call send_order_entrypoint.execute_send_order.
It still does not connect Kiwoom OpenAPI, queue commit services, runtime commit
services, GUI, or real execution controllers. When no broker adapter is
explicitly supplied, a local preview adapter is used so the entrypoint path can
be validated without touching a real broker.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from send_order_entrypoint import execute_send_order


ORCHESTRATOR_TYPE = "EXECUTION_SEND_ORDER_ENTRYPOINT_ORCHESTRATOR"
STATUS_PASSED = "SEND_ORDER_ENTRYPOINT_PASSED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
OPEN_POLICY_READY = "READY_TO_OPEN_SEND_ORDER_ENTRYPOINT"
FINAL_GATE_PASSED = "FINAL_SEND_GATE_PASSED"
NEXT_STAGE_REQUIRED = "SEND_ORDER_ENTRYPOINT_REQUIRED"
NEXT_STAGE_BROKER_SEND_REQUIRED = "BROKER_SEND_REQUIRED"
NEXT_STAGE_BLOCKED = "BLOCKED"


class _PreviewBrokerAdapter:
    broker_name = "SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER"

    def send_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "broker_status": "PREVIEW_ACCEPTED",
            "preview_only": True,
            "kiwoom_api_called": False,
            "order_id": request.get("order_id"),
            "request_hash": request.get("request_hash"),
        }


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
    entrypoint_called: bool = False,
    send_order_called: bool = False,
    next_stage: str = NEXT_STAGE_BLOCKED,
    send_order_entrypoint_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "entrypoint_called": entrypoint_called,
        "send_order_called": bool(send_order_called),
        "next_stage": next_stage,
        "send_order_entrypoint_result": deepcopy(send_order_entrypoint_result)
        if isinstance(send_order_entrypoint_result, dict)
        else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _entrypoint_context(policy_result: dict[str, Any], final_input: dict[str, Any]) -> dict[str, Any]:
    context = deepcopy(_as_dict(final_input.get("context")))
    confirmations = _as_dict(policy_result.get("required_confirmations"))
    if confirmations.get("manual_send_order_entrypoint_confirmed") is True:
        context["manual_send_order_entrypoint_confirmed"] = True
    return context


def _entrypoint_final_gate_result(final_gate_result: dict[str, Any], adapter_request: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(final_gate_result)
    if not result.get("source_signal_id") and adapter_request.get("source_signal_id"):
        result["source_signal_id"] = adapter_request.get("source_signal_id")
    return result


def orchestrate_send_order_entrypoint(
    send_order_entrypoint_open_policy_result: Any,
    final_send_gate_call_orchestrator_result: Any,
    broker_adapter: Any = None,
) -> dict[str, Any]:
    """Call SendOrder EntryPoint only after explicit open policy approval."""
    if not isinstance(send_order_entrypoint_open_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_SEND_ORDER_ENTRYPOINT_OPEN_POLICY_RESULT"])
    if not isinstance(final_send_gate_call_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FINAL_SEND_GATE_CALL_ORCHESTRATOR_RESULT"])

    warnings = _as_list(send_order_entrypoint_open_policy_result.get("warnings")) + _as_list(
        final_send_gate_call_orchestrator_result.get("warnings")
    )

    policy_status = _text(send_order_entrypoint_open_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(send_order_entrypoint_open_policy_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_OPEN_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != OPEN_POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(send_order_entrypoint_open_policy_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_OPEN_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if send_order_entrypoint_open_policy_result.get("send_order_entrypoint_allowed") is not True:
        return _blocked("SEND_ORDER_ENTRYPOINT_NOT_ALLOWED", warnings)

    call_status = _text(final_send_gate_call_orchestrator_result.get("status"))
    if call_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(final_send_gate_call_orchestrator_result.get("issues"))
            or ["FINAL_SEND_GATE_CALL_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if call_status != FINAL_GATE_PASSED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(final_send_gate_call_orchestrator_result.get("issues"))
            or ["FINAL_SEND_GATE_CALL_ORCHESTRATOR_NOT_PASSED"],
            warnings=warnings,
        )

    final_gate_result = _as_dict(final_send_gate_call_orchestrator_result.get("final_send_gate_result"))
    if not final_gate_result:
        return _blocked("FINAL_SEND_GATE_RESULT_REQUIRED", warnings)
    if final_gate_result.get("final_send_gate_ok") is not True:
        return _blocked("FINAL_SEND_GATE_RESULT_NOT_OK", warnings)
    if final_gate_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("FINAL_SEND_GATE_RESULT_NEXT_STAGE_REQUIRED", warnings)
    if final_gate_result.get("send_order_called") is not False:
        return _blocked("FINAL_SEND_GATE_RESULT_SEND_ORDER_ALREADY_CALLED", warnings)
    if final_send_gate_call_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("SEND_ORDER_ENTRYPOINT_NEXT_STAGE_REQUIRED", warnings)
    if final_send_gate_call_orchestrator_result.get("send_order_called") is not False:
        return _blocked("SEND_ORDER_ALREADY_CALLED", warnings)

    final_input = _as_dict(final_send_gate_call_orchestrator_result.get("final_send_gate_input"))
    if not final_input:
        return _blocked("FINAL_SEND_GATE_INPUT_REQUIRED", warnings)

    adapter_preview_result = _as_dict(final_input.get("adapter_preview_result"))
    adapter_request = _as_dict(final_input.get("send_order_request_preview")) or _as_dict(
        adapter_preview_result.get("send_order_request_preview")
    )
    order_queued_record = _as_dict(final_input.get("order_queued_record"))
    current_guard = _as_dict(final_input.get("current_guard"))
    context = _entrypoint_context(send_order_entrypoint_open_policy_result, final_input)

    missing: list[str] = []
    if not adapter_request:
        missing.append("SEND_ORDER_REQUEST_PREVIEW_REQUIRED")
    if not order_queued_record:
        missing.append("ORDER_QUEUED_RECORD_REQUIRED")
    if not current_guard:
        missing.append("CURRENT_GUARD_REQUIRED")
    if not context:
        missing.append("CONTEXT_REQUIRED")
    if missing:
        return _result(status=STATUS_BLOCKED, issues=missing, warnings=warnings)

    entrypoint_result = execute_send_order(
        _entrypoint_final_gate_result(final_gate_result, adapter_request),
        deepcopy(adapter_request),
        deepcopy(order_queued_record),
        broker_adapter if broker_adapter is not None else _PreviewBrokerAdapter(),
        queue_path=None,
        queue_snapshot=deepcopy(final_input.get("queue_snapshot")),
        current_queue_snapshot=deepcopy(final_input.get("current_queue_snapshot")),
        current_guard=deepcopy(current_guard),
        context=context,
    )
    if not isinstance(entrypoint_result, dict):
        return _result(
            status=STATUS_INVALID,
            entrypoint_called=True,
            issues=["MALFORMED_SEND_ORDER_ENTRYPOINT_RESULT"],
            warnings=warnings,
        )

    send_order_called = entrypoint_result.get("send_order_called") is True
    if entrypoint_result.get("send_order_executed") is True and send_order_called:
        return _result(
            status=STATUS_PASSED,
            entrypoint_called=True,
            send_order_called=send_order_called,
            next_stage=NEXT_STAGE_BROKER_SEND_REQUIRED,
            send_order_entrypoint_result=entrypoint_result,
            warnings=warnings + _as_list(entrypoint_result.get("warnings")),
        )

    return _result(
        status=STATUS_BLOCKED,
        entrypoint_called=True,
        send_order_called=send_order_called,
        send_order_entrypoint_result=entrypoint_result,
        issues=_as_list(entrypoint_result.get("blocked_reasons")) or ["SEND_ORDER_ENTRYPOINT_BLOCKED"],
        warnings=warnings + _as_list(entrypoint_result.get("warnings")),
    )
