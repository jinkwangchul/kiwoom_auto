# -*- coding: utf-8 -*-
"""Execute a Kiwoom SendOrder-like callable from a validated call preview.

The executor is intentionally narrow: it calls only the injected callable once
and returns a structured result. It never imports or touches Kiwoom GUI objects,
runtime writers, queue writers, recorders, or Chejan handlers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_SENT = "SEND_ORDER_SENT"
STATUS_FAILED = "SEND_ORDER_FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"


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
