# -*- coding: utf-8 -*-
"""Build Final Send Gate input payloads from readiness results.

This adapter standardizes the inputs needed by final_send_gate_service without
calling it. It does not call SendOrder, queue commit services, runtime writers,
GUI, or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "EXECUTION_FINAL_SEND_GATE_INPUT_ADAPTER"
STATUS_READY = "READY_FOR_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

_IDENTITY_FIELDS = ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id")


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
    final_send_gate_input: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "final_send_gate_called": False,
        "final_send_gate_input": deepcopy(final_send_gate_input) if isinstance(final_send_gate_input, dict) else None,
        "identity": deepcopy(identity or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _missing_identity(identity: dict[str, Any]) -> list[str]:
    return [field for field in _IDENTITY_FIELDS if not _text(identity.get(field))]


def adapt_final_send_gate_readiness_to_input(
    final_send_gate_readiness_result: Any,
    current_guard: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Create the payload required for a future Final Send Gate call."""
    if not isinstance(final_send_gate_readiness_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FINAL_SEND_GATE_READINESS_RESULT"])

    warnings = _as_list(final_send_gate_readiness_result.get("warnings"))
    status = _text(final_send_gate_readiness_result.get("status"))
    if status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(final_send_gate_readiness_result.get("issues")) or ["FINAL_SEND_GATE_READINESS_INVALID"],
            warnings=warnings,
        )
    if status != STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(final_send_gate_readiness_result.get("issues")) or ["FINAL_SEND_GATE_READINESS_NOT_READY"],
            warnings=warnings,
        )
    if final_send_gate_readiness_result.get("final_send_gate_allowed") is not True:
        return _result(status=STATUS_BLOCKED, issues=["FINAL_SEND_GATE_NOT_ALLOWED"], warnings=warnings)

    adapter_preview_result = _as_dict(final_send_gate_readiness_result.get("adapter_preview_result"))
    if not adapter_preview_result:
        return _result(status=STATUS_BLOCKED, issues=["ADAPTER_PREVIEW_RESULT_REQUIRED"], warnings=warnings)

    request_preview = _as_dict(adapter_preview_result.get("send_order_request_preview"))
    if not request_preview:
        return _result(status=STATUS_BLOCKED, issues=["SEND_ORDER_REQUEST_PREVIEW_REQUIRED"], warnings=warnings)

    order_queued_record = _as_dict(final_send_gate_readiness_result.get("order_queued_record"))
    if not order_queued_record:
        return _result(status=STATUS_BLOCKED, issues=["ORDER_QUEUED_RECORD_REQUIRED"], warnings=warnings)

    identity = _as_dict(final_send_gate_readiness_result.get("identity"))
    missing = _missing_identity(identity)
    if missing:
        return _result(
            status=STATUS_BLOCKED,
            identity=identity,
            issues=[f"MISSING_{field.upper()}" for field in missing],
            warnings=warnings,
        )

    guard = _as_dict(current_guard)
    if not guard:
        return _result(status=STATUS_BLOCKED, identity=identity, issues=["CURRENT_GUARD_REQUIRED"], warnings=warnings)

    final_input = {
        "adapter_preview_result": deepcopy(adapter_preview_result),
        "send_order_request_preview": deepcopy(request_preview),
        "order_queued_record": deepcopy(order_queued_record),
        "identity": deepcopy(identity),
        "current_guard": deepcopy(guard),
        "context": deepcopy(_as_dict(context)),
    }
    return _result(
        status=STATUS_READY,
        final_send_gate_input=final_input,
        identity=identity,
        warnings=warnings,
    )
