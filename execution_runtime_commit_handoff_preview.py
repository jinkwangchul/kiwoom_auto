# -*- coding: utf-8 -*-
"""Read-only handoff preview for validated Execution Runtime commit requests.

This module adapts a validation gate result into the shape that may later be
handed to a commit service. It never calls commit services, writes runtime
files, commits queues, or calls SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_commit_request_validation_gate import (
    GATE_TYPE,
    STATUS_APPROVED as GATE_STATUS_APPROVED,
    STATUS_INVALID,
)


HANDOFF_TYPE = "EXECUTION_RUNTIME_COMMIT_HANDOFF_PREVIEW"
SERVICE_HANDOFF_TYPE = "EXECUTION_RUNTIME_COMMIT_SERVICE_HANDOFF_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"


def build_execution_runtime_commit_handoff_preview(validation_gate_result: Any) -> dict[str, Any]:
    """Build a read-only Commit Service handoff preview from a gate result."""
    if not isinstance(validation_gate_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_VALIDATION_GATE_RESULT"])

    gate = deepcopy(validation_gate_result)
    allowlist_decision = _as_dict(gate.get("allowlist_decision"))
    validation_summary = _as_dict(gate.get("validation_summary"))
    gate_issues = _as_list(gate.get("issues"))
    gate_warnings = _as_list(gate.get("warnings"))
    request_fingerprint = _text(gate.get("request_fingerprint"))
    logical_target = _text(allowlist_decision.get("logical_target"))
    operation = _text(allowlist_decision.get("operation"))
    runtime_target = _text(allowlist_decision.get("resolved_path") or allowlist_decision.get("normalized_path"))
    relative_path = _text(allowlist_decision.get("relative_path"))
    issues = list(gate_issues)

    if gate.get("gate_type") != GATE_TYPE:
        issues.append("INVALID_VALIDATION_GATE_TYPE")
    if gate.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if gate.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if gate.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")
    if gate.get("status") != GATE_STATUS_APPROVED or gate.get("commit_request_approved") is not True:
        issues.append("VALIDATION_GATE_NOT_APPROVED")
    if not request_fingerprint:
        issues.append("MISSING_REQUEST_FINGERPRINT")
    if not allowlist_decision:
        issues.append("MISSING_ALLOWLIST_DECISION")
    elif allowlist_decision.get("allowed") is not True:
        reason = (
            _text(allowlist_decision.get("blocked_reason"))
            or _text(allowlist_decision.get("reason"))
            or _text(allowlist_decision.get("status"))
        )
        issues.append(f"ALLOWLIST_DECISION_NOT_ALLOWED: {reason}")
    if validation_summary.get("allowlist_allowed") is not True:
        issues.append("ALLOWLIST_NOT_ALLOWED")
    if validation_summary.get("policy_allowed") is not True:
        issues.append("READINESS_POLICY_NOT_ALLOWED")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")

    status = STATUS_READY if not issues else _blocked_status(gate.get("status"), issues)
    return _result(
        status=status,
        request_fingerprint=request_fingerprint,
        logical_target=logical_target,
        operation=operation,
        runtime_target=runtime_target,
        relative_path=relative_path,
        gate_decision={
            "gate_type": _text(gate.get("gate_type")),
            "status": _text(gate.get("status")),
            "commit_request_approved": gate.get("commit_request_approved") is True,
        },
        allowlist_decision=allowlist_decision,
        validation_summary=validation_summary,
        issues=_dedupe(issues),
        warnings=gate_warnings,
    )


def build_execution_runtime_commit_service_handoff_preview(handoff_preview: Any) -> dict[str, Any]:
    """Prepare a dry-run-only Commit Service input preview from READY handoff."""
    if not isinstance(handoff_preview, dict):
        return _service_result(status=STATUS_INVALID, issues=["MALFORMED_HANDOFF_PREVIEW"])

    handoff = deepcopy(handoff_preview)
    commit_service_input = _as_dict(handoff.get("commit_service_input_preview"))
    request_fingerprint = _text(handoff.get("request_fingerprint"))
    logical_target = _text(handoff.get("logical_target"))
    operation = _text(handoff.get("operation"))
    runtime_target = _text(handoff.get("runtime_target"))
    relative_path = _text(handoff.get("relative_path"))
    issues = _as_list(handoff.get("issues"))

    if handoff.get("handoff_type") != HANDOFF_TYPE:
        issues.append("INVALID_HANDOFF_TYPE")
    if handoff.get("status") != STATUS_READY or handoff.get("handoff_ready") is not True:
        issues.append("HANDOFF_NOT_READY")
    if handoff.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if handoff.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if handoff.get("commit_service_called") is True:
        issues.append("COMMIT_SERVICE_ALREADY_CALLED")
    if not commit_service_input:
        issues.append("MISSING_COMMIT_SERVICE_INPUT_PREVIEW")
    if not request_fingerprint:
        issues.append("MISSING_REQUEST_FINGERPRINT")
    if _text(commit_service_input.get("request_fingerprint")) != request_fingerprint:
        issues.append("REQUEST_FINGERPRINT_MISMATCH")
    if _text(commit_service_input.get("logical_target")) != logical_target:
        issues.append("LOGICAL_TARGET_MISMATCH")
    if _text(commit_service_input.get("operation")) != operation:
        issues.append("OPERATION_MISMATCH")
    if _text(commit_service_input.get("runtime_target")) != runtime_target:
        issues.append("RUNTIME_TARGET_MISMATCH")
    if _text(commit_service_input.get("relative_path")) != relative_path:
        issues.append("RELATIVE_PATH_MISMATCH")
    if commit_service_input.get("preview_only") is not True:
        issues.append("COMMIT_SERVICE_INPUT_PREVIEW_ONLY_REQUIRED")
    if commit_service_input.get("runtime_write") is not False:
        issues.append("COMMIT_SERVICE_INPUT_RUNTIME_WRITE_MUST_BE_FALSE")

    status = STATUS_READY if not issues else _blocked_status(handoff.get("status"), issues)
    return _service_result(
        status=status,
        request_fingerprint=request_fingerprint,
        logical_target=logical_target,
        operation=operation,
        runtime_target=runtime_target,
        relative_path=relative_path,
        handoff_preview=handoff,
        commit_service_input_preview=commit_service_input,
        issues=_dedupe(issues),
        warnings=_as_list(handoff.get("warnings")),
    )


def _result(
    *,
    status: str,
    request_fingerprint: str = "",
    logical_target: str = "",
    operation: str = "",
    runtime_target: str = "",
    relative_path: str = "",
    gate_decision: dict[str, Any] | None = None,
    allowlist_decision: dict[str, Any] | None = None,
    validation_summary: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    handoff_ready = status == STATUS_READY and not issues
    commit_service_input_preview = {
        "request_fingerprint": request_fingerprint,
        "logical_target": logical_target,
        "operation": operation,
        "runtime_target": runtime_target,
        "relative_path": relative_path,
        "allowlist_decision": deepcopy(allowlist_decision or {}),
        "validation_summary": deepcopy(validation_summary or {}),
        "preview_only": True,
        "runtime_write": False,
    }
    return {
        "handoff_type": HANDOFF_TYPE,
        "status": status,
        "handoff_ready": handoff_ready,
        "request_fingerprint": request_fingerprint,
        "logical_target": logical_target,
        "operation": operation,
        "runtime_target": runtime_target,
        "relative_path": relative_path,
        "gate_decision": deepcopy(gate_decision or {}),
        "allowlist_decision": deepcopy(allowlist_decision or {}),
        "validation_summary": deepcopy(validation_summary or {}),
        "commit_service_input_preview": commit_service_input_preview,
        "preview_only": True,
        "runtime_write": False,
        "commit_service_called": False,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _service_result(
    *,
    status: str,
    request_fingerprint: str = "",
    logical_target: str = "",
    operation: str = "",
    runtime_target: str = "",
    relative_path: str = "",
    handoff_preview: dict[str, Any] | None = None,
    commit_service_input_preview: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    service_preview_ready = status == STATUS_READY and not issues
    return {
        "service_handoff_type": SERVICE_HANDOFF_TYPE,
        "status": status,
        "service_preview_ready": service_preview_ready,
        "request_fingerprint": request_fingerprint,
        "logical_target": logical_target,
        "operation": operation,
        "runtime_target": runtime_target,
        "relative_path": relative_path,
        "commit_service_route_preview": {
            "service_type": "EXECUTION_RUNTIME_COMMIT_SERVICE",
            "function": "commit_execution_runtime_plan",
            "dry_run_only": True,
            "preview_only": True,
            "runtime_write": False,
            "call_allowed": False,
        },
        "handoff_preview": deepcopy(handoff_preview or {}),
        "commit_service_input_preview": deepcopy(commit_service_input_preview or {}),
        "preview_only": True,
        "dry_run_only": True,
        "runtime_write": False,
        "commit_service_called": False,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked_status(gate_status: Any, issues: list[Any]) -> str:
    if gate_status == STATUS_INVALID:
        return STATUS_INVALID
    return STATUS_INVALID if any(_invalid_issue(issue) for issue in issues) else STATUS_BLOCKED


def _as_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _invalid_issue(issue: Any) -> bool:
    text = str(issue)
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH", "ALREADY_CALLED")
    return any(marker in text for marker in markers)
