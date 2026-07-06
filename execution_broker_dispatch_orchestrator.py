# -*- coding: utf-8 -*-
"""Dispatch to an explicitly supplied broker adapter after open approval.

This orchestrator never imports or calls Kiwoom OpenAPI directly. It only calls
the broker_adapter object explicitly supplied by the caller, after the preview
Broker Dispatch Open Policy and SendOrder EntryPoint result are both ready.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ORCHESTRATOR_TYPE = "EXECUTION_BROKER_DISPATCH_ORCHESTRATOR"
STATUS_SUBMITTED = "BROKER_DISPATCH_SUBMITTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
POLICY_READY = "READY_TO_OPEN_BROKER_DISPATCH"
ENTRYPOINT_PASSED = "SEND_ORDER_ENTRYPOINT_PASSED"
NEXT_STAGE_REQUIRED = "BROKER_SEND_REQUIRED"
NEXT_STAGE_REVIEW_REQUIRED = "BROKER_RESULT_REVIEW_REQUIRED"
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
    broker_dispatch_called: bool = False,
    send_order_called: bool = False,
    broker_result: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "broker_dispatch_called": broker_dispatch_called,
        "kiwoom_called": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": bool(send_order_called),
        "broker_result": deepcopy(broker_result) if isinstance(broker_result, dict) else None,
        "next_stage": next_stage,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _broker_request(entrypoint_result: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "order_id",
        "order_queued_id",
        "request_hash",
        "lock_id",
        "execution_id",
        "broker",
    )
    request = {field: entrypoint_result.get(field) for field in fields if entrypoint_result.get(field) is not None}
    broker_result = _as_dict(entrypoint_result.get("broker_result"))
    if broker_result:
        request["entrypoint_broker_result"] = deepcopy(broker_result)
    request["source"] = "send_order_entrypoint_result"
    return request


def orchestrate_broker_dispatch(
    broker_dispatch_open_policy_result: Any,
    send_order_entrypoint_orchestrator_result: Any,
    broker_adapter: Any = None,
) -> dict[str, Any]:
    """Call only an explicitly injected broker adapter when dispatch is open."""
    if not isinstance(broker_dispatch_open_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_BROKER_DISPATCH_OPEN_POLICY_RESULT"])
    if not isinstance(send_order_entrypoint_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_RESULT"])

    warnings = _as_list(broker_dispatch_open_policy_result.get("warnings")) + _as_list(
        send_order_entrypoint_orchestrator_result.get("warnings")
    )

    policy_status = _text(broker_dispatch_open_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(broker_dispatch_open_policy_result.get("issues"))
            or ["BROKER_DISPATCH_OPEN_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(broker_dispatch_open_policy_result.get("issues"))
            or ["BROKER_DISPATCH_OPEN_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if broker_dispatch_open_policy_result.get("broker_dispatch_allowed") is not True:
        return _blocked("BROKER_DISPATCH_NOT_ALLOWED", warnings)

    entrypoint_status = _text(send_order_entrypoint_orchestrator_result.get("status"))
    if entrypoint_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(send_order_entrypoint_orchestrator_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if entrypoint_status != ENTRYPOINT_PASSED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(send_order_entrypoint_orchestrator_result.get("issues"))
            or ["SEND_ORDER_ENTRYPOINT_ORCHESTRATOR_NOT_PASSED"],
            warnings=warnings,
        )
    if send_order_entrypoint_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("BROKER_SEND_NEXT_STAGE_REQUIRED", warnings)

    entrypoint_result = _as_dict(send_order_entrypoint_orchestrator_result.get("send_order_entrypoint_result"))
    if not entrypoint_result:
        return _blocked("SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED", warnings)

    if broker_adapter is None:
        return _blocked("BROKER_ADAPTER_REQUIRED", warnings)
    send_callable = getattr(broker_adapter, "send_order", None)
    if not callable(send_callable):
        return _blocked("BROKER_ADAPTER_SEND_ORDER_REQUIRED", warnings)

    request = _broker_request(entrypoint_result)
    if not request.get("order_id") or not request.get("request_hash"):
        return _blocked("BROKER_REQUEST_REQUIRED_FIELDS_MISSING", warnings)

    try:
        raw_broker_result = send_callable(deepcopy(request))
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _result(
            status=STATUS_BLOCKED,
            broker_dispatch_called=True,
            send_order_called=send_order_entrypoint_orchestrator_result.get("send_order_called") is True,
            issues=[f"BROKER_DISPATCH_EXCEPTION: {exc}"],
            warnings=warnings,
        )

    broker_result = raw_broker_result if isinstance(raw_broker_result, dict) else {"raw_result": raw_broker_result}
    return _result(
        status=STATUS_SUBMITTED,
        broker_dispatch_called=True,
        send_order_called=send_order_entrypoint_orchestrator_result.get("send_order_called") is True,
        broker_result=broker_result,
        next_stage=NEXT_STAGE_REVIEW_REQUIRED,
        warnings=warnings,
    )
