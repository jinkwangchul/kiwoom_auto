# -*- coding: utf-8 -*-
"""Final validation gate for Execution Runtime Commit Request contracts.

This module validates a commit request immediately before a future commit
service boundary. It never calls commit services, writes runtime files, commits
queues, or calls SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_commit_request_contract import (
    STATUS_READY as REQUEST_STATUS_READY,
    validate_execution_runtime_commit_request,
)


GATE_TYPE = "EXECUTION_RUNTIME_COMMIT_REQUEST_VALIDATION_GATE"
STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _result(
    *,
    status: str,
    commit_request_approved: bool,
    validation: dict[str, Any] | None = None,
    validation_summary: dict[str, Any] | None = None,
    allowlist_decision: dict[str, Any] | None = None,
    request_fingerprint: str = "",
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "gate_type": GATE_TYPE,
        "status": status,
        "commit_request_approved": commit_request_approved,
        "preview_only": True,
        "runtime_write": False,
        "request_fingerprint": request_fingerprint,
        "validation": deepcopy(validation or {}),
        "validation_summary": deepcopy(validation_summary or {}),
        "allowlist_decision": deepcopy(allowlist_decision or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def evaluate_execution_runtime_commit_request_validation_gate(commit_request: Any) -> dict[str, Any]:
    """Approve or block a commit request contract without side effects."""
    if not isinstance(commit_request, dict):
        return _result(
            status=STATUS_INVALID,
            commit_request_approved=False,
            validation=validate_execution_runtime_commit_request(commit_request),
            issues=["MALFORMED_COMMIT_REQUEST"],
        )

    request = deepcopy(commit_request)
    validation = validate_execution_runtime_commit_request(request)
    validation_summary = _as_dict(request.get("validation_summary"))
    allowlist_decision = _as_dict(request.get("allowlist_decision"))
    issues: list[Any] = []
    warnings = _as_list(request.get("warnings"))

    if validation.get("valid") is not True:
        issues.extend(validation.get("issues") or ["COMMIT_REQUEST_VALIDATION_FAILED"])

    if request.get("status") != REQUEST_STATUS_READY:
        issues.append("COMMIT_REQUEST_STATUS_NOT_READY")

    if request.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if request.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")

    if validation_summary.get("policy_allowed") is not True:
        issues.append("READINESS_POLICY_NOT_ALLOWED")
    if validation_summary.get("allowlist_allowed") is not True:
        issues.append("ALLOWLIST_NOT_ALLOWED")
    if allowlist_decision.get("allowed") is not True:
        reason = (
            _text(allowlist_decision.get("blocked_reason"))
            or _text(allowlist_decision.get("reason"))
            or _text(allowlist_decision.get("status"))
        )
        issues.append(f"ALLOWLIST_DECISION_NOT_ALLOWED: {reason}")

    if issues:
        status = STATUS_INVALID if validation.get("valid") is not True else STATUS_BLOCKED
        return _result(
            status=status,
            commit_request_approved=False,
            validation=validation,
            validation_summary=validation_summary,
            allowlist_decision=allowlist_decision,
            request_fingerprint=_text(request.get("request_fingerprint")),
            issues=_dedupe(issues),
            warnings=warnings,
        )

    return _result(
        status=STATUS_APPROVED,
        commit_request_approved=True,
        validation=validation,
        validation_summary=validation_summary,
        allowlist_decision=allowlist_decision,
        request_fingerprint=_text(request.get("request_fingerprint")),
        warnings=warnings,
    )


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
