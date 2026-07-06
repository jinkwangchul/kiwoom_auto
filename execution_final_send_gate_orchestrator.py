# -*- coding: utf-8 -*-
"""Preview-only orchestrator before Final Send Gate service.

This layer validates the standardized Final Send Gate input payload and reports
whether the next layer may call final_send_gate_service later. It never calls
Final Send Gate, SendOrder, queue commit services, runtime commit services, GUI,
or real execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ORCHESTRATOR_TYPE = "EXECUTION_FINAL_SEND_GATE_ORCHESTRATOR"
STATUS_READY = "READY_FOR_FINAL_SEND_GATE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
NEXT_STAGE_FINAL_SEND_GATE_SERVICE_REQUIRED = "FINAL_SEND_GATE_SERVICE_REQUIRED"
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
    final_send_gate_ready: bool = False,
    next_stage: str = NEXT_STAGE_BLOCKED,
    final_send_gate_input: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
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
        "final_send_gate_called": False,
        "final_send_gate_ready": final_send_gate_ready,
        "next_stage": next_stage,
        "final_send_gate_input": deepcopy(final_send_gate_input) if isinstance(final_send_gate_input, dict) else None,
        "identity": deepcopy(identity or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _missing_payload_issues(final_input: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not _as_dict(final_input.get("adapter_preview_result")):
        issues.append("ADAPTER_PREVIEW_RESULT_REQUIRED")
    adapter_preview = _as_dict(final_input.get("adapter_preview_result"))
    if not _as_dict(final_input.get("send_order_request_preview")):
        issues.append("SEND_ORDER_REQUEST_PREVIEW_REQUIRED")
    if adapter_preview and not _as_dict(adapter_preview.get("send_order_request_preview")):
        issues.append("ADAPTER_PREVIEW_SEND_ORDER_REQUEST_PREVIEW_REQUIRED")
    if not _as_dict(final_input.get("order_queued_record")):
        issues.append("ORDER_QUEUED_RECORD_REQUIRED")
    if not _as_dict(final_input.get("identity")):
        issues.append("IDENTITY_REQUIRED")
    if not _as_dict(final_input.get("current_guard")):
        issues.append("CURRENT_GUARD_REQUIRED")
    if not _as_dict(final_input.get("context")):
        issues.append("CONTEXT_REQUIRED")
    return issues


def orchestrate_final_send_gate_preview(final_send_gate_input_adapter_result: Any) -> dict[str, Any]:
    """Validate the Final Send Gate input payload without calling the gate."""
    if not isinstance(final_send_gate_input_adapter_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FINAL_SEND_GATE_INPUT_ADAPTER_RESULT"])

    warnings = _as_list(final_send_gate_input_adapter_result.get("warnings"))
    status = _text(final_send_gate_input_adapter_result.get("status"))
    if status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(final_send_gate_input_adapter_result.get("issues")) or ["FINAL_SEND_GATE_INPUT_ADAPTER_INVALID"],
            warnings=warnings,
        )
    if status != STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(final_send_gate_input_adapter_result.get("issues")) or ["FINAL_SEND_GATE_INPUT_ADAPTER_NOT_READY"],
            warnings=warnings,
        )

    input_issues = _as_list(final_send_gate_input_adapter_result.get("issues"))
    if input_issues:
        return _result(status=STATUS_BLOCKED, issues=input_issues, warnings=warnings)

    final_input = _as_dict(final_send_gate_input_adapter_result.get("final_send_gate_input"))
    if not final_input:
        return _result(status=STATUS_BLOCKED, issues=["FINAL_SEND_GATE_INPUT_REQUIRED"], warnings=warnings)

    issues = _missing_payload_issues(final_input)
    identity = _as_dict(final_send_gate_input_adapter_result.get("identity")) or _as_dict(final_input.get("identity"))
    if issues:
        return _result(
            status=STATUS_BLOCKED,
            final_send_gate_input=final_input,
            identity=identity,
            issues=issues,
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        final_send_gate_ready=True,
        next_stage=NEXT_STAGE_FINAL_SEND_GATE_SERVICE_REQUIRED,
        final_send_gate_input=final_input,
        identity=identity,
        warnings=warnings,
    )
