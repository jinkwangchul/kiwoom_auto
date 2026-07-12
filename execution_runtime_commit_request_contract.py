# -*- coding: utf-8 -*-
"""Execution Runtime Commit Request contract.

This module adapts preview-only readiness/open policy results into an immutable
in-memory commit request contract. It never calls commit services, writes
runtime files, commits queues, or calls SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any


CONTRACT_TYPE = "EXECUTION_RUNTIME_COMMIT_REQUEST_CONTRACT"
CONTRACT_VERSION = "EXECUTION_RUNTIME_COMMIT_REQUEST_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


@dataclass(frozen=True)
class ExecutionRuntimeCommitRequest:
    contract_type: str
    contract_version: str
    status: str
    request_fingerprint: str
    source_policy_type: str
    logical_target: str
    operation: str
    runtime_target: str
    relative_path: str
    preview_only: bool
    dry_run: bool
    runtime_write: bool
    allowlist_decision: dict[str, Any]
    validation_summary: dict[str, Any]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_type": self.contract_type,
            "contract_version": self.contract_version,
            "status": self.status,
            "request_fingerprint": self.request_fingerprint,
            "source_policy_type": self.source_policy_type,
            "logical_target": self.logical_target,
            "operation": self.operation,
            "runtime_target": self.runtime_target,
            "relative_path": self.relative_path,
            "preview_only": self.preview_only,
            "dry_run": self.dry_run,
            "runtime_write": self.runtime_write,
            "allowlist_decision": deepcopy(self.allowlist_decision),
            "validation_summary": deepcopy(self.validation_summary),
            "issues": list(self.issues),
            "warnings": list(self.warnings),
        }


def build_execution_runtime_commit_request(
    readiness_policy_result: Any,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build a commit request contract from a readiness/open policy result."""
    if not isinstance(readiness_policy_result, dict):
        return _request(
            status=STATUS_INVALID,
            source_policy_type="",
            allowlist_decision={},
            preview_only=True,
            dry_run=dry_run,
            issues=["MALFORMED_READINESS_POLICY_RESULT"],
            warnings=[],
        ).to_dict()

    policy = deepcopy(readiness_policy_result)
    allowlist_decision = _runtime_target_allowlist_decision(policy)
    policy_issues = _string_list(policy.get("issues"))
    policy_warnings = _string_list(policy.get("warnings"))
    issues = list(policy_issues)

    source_policy_type = _text(policy.get("policy_type"))
    if not source_policy_type:
        issues.append("MISSING_SOURCE_POLICY_TYPE")

    preview_only = policy.get("preview_only") is True
    if not preview_only:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if policy.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")

    if not allowlist_decision:
        issues.append("MISSING_ALLOWLIST_DECISION")
    elif allowlist_decision.get("allowed") is not True:
        reason = (
            _text(allowlist_decision.get("blocked_reason"))
            or _text(allowlist_decision.get("reason"))
            or _text(allowlist_decision.get("status"))
        )
        issues.append(f"ALLOWLIST_DECISION_NOT_ALLOWED: {reason}")

    policy_allows_commit = _policy_allows_commit(policy)
    if policy_allows_commit is not True:
        issues.append("READINESS_POLICY_NOT_ALLOWED")

    logical_target = _text(allowlist_decision.get("logical_target"))
    operation = _text(allowlist_decision.get("operation"))
    runtime_target = _text(allowlist_decision.get("resolved_path") or allowlist_decision.get("normalized_path"))
    relative_path = _text(allowlist_decision.get("relative_path"))

    if not logical_target:
        issues.append("MISSING_LOGICAL_TARGET")
    if not operation:
        issues.append("MISSING_OPERATION")
    if not runtime_target:
        issues.append("MISSING_RUNTIME_TARGET")

    status = STATUS_READY if not issues else STATUS_BLOCKED
    if any(_invalid_issue(issue) for issue in issues):
        status = STATUS_INVALID

    return _request(
        status=status,
        source_policy_type=source_policy_type,
        allowlist_decision=allowlist_decision,
        preview_only=preview_only,
        dry_run=dry_run,
        issues=issues,
        warnings=policy_warnings,
        policy_status=_text(policy.get("status")),
        policy_allowed=policy_allows_commit,
    ).to_dict()


def validate_execution_runtime_commit_request(commit_request: Any) -> dict[str, Any]:
    """Validate a commit request contract without mutating it."""
    snapshot = deepcopy(commit_request)
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(commit_request, dict):
        return _validation(False, STATUS_INVALID, ["MALFORMED_COMMIT_REQUEST"], warnings)

    if commit_request.get("contract_type") != CONTRACT_TYPE:
        issues.append("INVALID_CONTRACT_TYPE")
    if commit_request.get("contract_version") != CONTRACT_VERSION:
        issues.append("INVALID_CONTRACT_VERSION")
    if commit_request.get("status") not in {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID}:
        issues.append("INVALID_STATUS")
    if not _text(commit_request.get("request_fingerprint")):
        issues.append("MISSING_REQUEST_FINGERPRINT")
    if not _text(commit_request.get("source_policy_type")):
        issues.append("MISSING_SOURCE_POLICY_TYPE")
    if not _text(commit_request.get("logical_target")):
        issues.append("MISSING_LOGICAL_TARGET")
    if not _text(commit_request.get("operation")):
        issues.append("MISSING_OPERATION")
    if not _text(commit_request.get("runtime_target")):
        issues.append("MISSING_RUNTIME_TARGET")
    if commit_request.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_REQUIRED")
    if not isinstance(commit_request.get("dry_run"), bool):
        issues.append("DRY_RUN_MUST_BE_BOOL")
    if commit_request.get("runtime_write") is not False:
        issues.append("RUNTIME_WRITE_MUST_BE_FALSE")
    if not isinstance(commit_request.get("allowlist_decision"), dict):
        issues.append("ALLOWLIST_DECISION_MUST_BE_DICT")
    if not isinstance(commit_request.get("validation_summary"), dict):
        issues.append("VALIDATION_SUMMARY_MUST_BE_DICT")
    if not isinstance(commit_request.get("issues"), list):
        issues.append("ISSUES_MUST_BE_LIST")
    if not isinstance(commit_request.get("warnings"), list):
        issues.append("WARNINGS_MUST_BE_LIST")

    expected_fingerprint = _fingerprint(_fingerprint_payload(commit_request))
    if _text(commit_request.get("request_fingerprint")) and commit_request.get("request_fingerprint") != expected_fingerprint:
        issues.append("REQUEST_FINGERPRINT_MISMATCH")

    if snapshot != commit_request:
        issues.append("COMMIT_REQUEST_MUTATED_DURING_VALIDATION")

    status = STATUS_READY if not issues else STATUS_INVALID
    return _validation(not issues, status, issues, warnings)


def _request(
    *,
    status: str,
    source_policy_type: str,
    allowlist_decision: dict[str, Any],
    preview_only: bool,
    dry_run: bool,
    issues: list[str],
    warnings: list[str],
    policy_status: str = "",
    policy_allowed: bool = False,
) -> ExecutionRuntimeCommitRequest:
    logical_target = _text(allowlist_decision.get("logical_target"))
    operation = _text(allowlist_decision.get("operation"))
    runtime_target = _text(allowlist_decision.get("resolved_path") or allowlist_decision.get("normalized_path"))
    relative_path = _text(allowlist_decision.get("relative_path"))
    validation_summary = {
        "policy_status": policy_status,
        "policy_allowed": policy_allowed is True,
        "allowlist_allowed": allowlist_decision.get("allowed") is True,
        "issue_count": len(issues),
        "warning_count": len(warnings),
    }
    payload = {
        "source_policy_type": source_policy_type,
        "logical_target": logical_target,
        "operation": operation,
        "runtime_target": runtime_target,
        "relative_path": relative_path,
        "preview_only": preview_only is True,
        "dry_run": dry_run is True,
        "runtime_write": False,
        "allowlist_decision": deepcopy(allowlist_decision),
        "validation_summary": deepcopy(validation_summary),
        "issues": list(issues),
        "warnings": list(warnings),
    }
    return ExecutionRuntimeCommitRequest(
        contract_type=CONTRACT_TYPE,
        contract_version=CONTRACT_VERSION,
        status=status,
        request_fingerprint=_fingerprint(payload),
        source_policy_type=source_policy_type,
        logical_target=logical_target,
        operation=operation,
        runtime_target=runtime_target,
        relative_path=relative_path,
        preview_only=preview_only is True,
        dry_run=dry_run is True,
        runtime_write=False,
        allowlist_decision=deepcopy(allowlist_decision),
        validation_summary=validation_summary,
        issues=tuple(issues),
        warnings=tuple(warnings),
    )


def _runtime_target_allowlist_decision(policy: dict[str, Any]) -> dict[str, Any]:
    decisions = policy.get("allowlist_decisions")
    if not isinstance(decisions, dict):
        return {}
    decision = decisions.get("runtime_target")
    return deepcopy(decision) if isinstance(decision, dict) else {}


def _policy_allows_commit(policy: dict[str, Any]) -> bool:
    if "runtime_commit_allowed" in policy:
        return policy.get("runtime_commit_allowed") is True
    if "file_init_allowed" in policy:
        return policy.get("file_init_allowed") is True
    return False


def _fingerprint_payload(commit_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_policy_type": commit_request.get("source_policy_type"),
        "logical_target": commit_request.get("logical_target"),
        "operation": commit_request.get("operation"),
        "runtime_target": commit_request.get("runtime_target"),
        "relative_path": commit_request.get("relative_path"),
        "preview_only": commit_request.get("preview_only"),
        "dry_run": commit_request.get("dry_run"),
        "runtime_write": commit_request.get("runtime_write"),
        "allowlist_decision": deepcopy(commit_request.get("allowlist_decision")),
        "validation_summary": deepcopy(commit_request.get("validation_summary")),
        "issues": list(commit_request.get("issues") or []),
        "warnings": list(commit_request.get("warnings") or []),
    }


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validation(valid: bool, status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": valid,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _invalid_issue(issue: str) -> bool:
    markers = ("MALFORMED", "MISSING", "INVALID", "MUST_BE", "REQUIRED", "MISMATCH")
    return any(marker in issue for marker in markers)
