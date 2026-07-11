# -*- coding: utf-8 -*-
"""Runtime Commit approval token issue adapter.

This adapter translates a preview-only BUY Runtime Commit execution context into
the existing Approval Token Store issue API. It only issues the token file via
the Token Store. It never consumes or validates tokens, calls the Real Executor,
acquires locks, creates backups, writes journals/persistence/runtime/queue
files, calls SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from runtime_commit_approval_token_store import (
    ISSUE_BLOCKED,
    ISSUE_ISSUED,
    ISSUE_INVALID,
    TOKEN_SCOPE,
    create_runtime_commit_token_storage_plan,
    issue_runtime_commit_approval_token,
)


ADAPTER_TYPE = "BUY_RUNTIME_COMMIT_APPROVAL_TOKEN_ISSUE_ADAPTER"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _result(
    *,
    status: str,
    approval_token_issue_result: dict[str, Any] | None = None,
    token_storage_plan: dict[str, Any] | None = None,
    issued_token_preview: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "approval_token_issue_result": deepcopy(approval_token_issue_result)
        if isinstance(approval_token_issue_result, dict)
        else None,
        "token_storage_plan": deepcopy(token_storage_plan) if isinstance(token_storage_plan, dict) else None,
        "issued_token_preview": deepcopy(issued_token_preview) if isinstance(issued_token_preview, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
        "token_issued": status == STATUS_READY,
        "token_consumed": False,
        "runtime_commit_real_executor_called": False,
        "runtime_write": False,
        "queue_write": False,
        "backup_created": False,
        "rollback_executed": False,
        "journal_written": False,
        "persistence_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
    }


def _compare(issue: str, left: Any, right: Any) -> list[str]:
    left_text = _clean_text(left)
    right_text = _clean_text(right)
    if left_text and right_text and left_text != right_text:
        return [issue]
    return []


def _validate_context(context: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if _clean_text(context.get("context_version")) != "BUY_RUNTIME_COMMIT_EXECUTION_CONTEXT_V1":
        issues.append("CONTEXT_NOT_READY")
    if context.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_FALSE")
    if context.get("runtime_write") is True:
        issues.append("RUNTIME_WRITE_TRUE")
    if context.get("approval_granted") is not True:
        issues.append("APPROVAL_GRANTED_FALSE")
    if context.get("execution_allowed") is True or context.get("execution_started") is True:
        issues.append("EXECUTION_FLAG_PRESET")
    if context.get("token_issued") is True or context.get("token_stored") is True or context.get("token_consumed") is True:
        issues.append("TOKEN_FLAG_PRESET")

    required = {
        "STORAGE_ROOT_MISSING": context.get("storage_root"),
        "CONSUMER_ID_MISSING": context.get("consumer_id"),
        "SCOPE_MISSING": context.get("scope"),
        "COMMIT_ID_MISSING": context.get("commit_id"),
        "EXECUTION_PLAN_HASH_MISSING": context.get("execution_plan_hash"),
        "TOKEN_ISSUE_REQUEST_ID_MISSING": context.get("token_issue_request_id"),
        "TOKEN_ID_MISSING": context.get("token_id"),
    }
    issues.extend(issue for issue, value in required.items() if not _clean_text(value))
    if _clean_text(context.get("scope")) and _clean_text(context.get("scope")) != TOKEN_SCOPE:
        issues.append("SCOPE_UNSUPPORTED")

    transaction = _as_dict(context.get("transaction_manifest_preview"))
    if not transaction:
        issues.append("TRANSACTION_MANIFEST_PREVIEW_MISSING")
    issues.extend(_compare("TOKEN_ID_MISMATCH", context.get("token_id"), context.get("token_issue_request_id")))
    issues.extend(_compare("COMMIT_ID_MISMATCH", context.get("commit_id"), transaction.get("commit_id")))
    issues.extend(_compare("TRANSACTION_ID_MISMATCH", context.get("transaction_id"), transaction.get("transaction_id")))
    issues.extend(_compare("EXECUTION_PLAN_HASH_MISMATCH", context.get("execution_plan_hash"), transaction.get("execution_plan_hash")))

    metadata = _as_dict(context.get("token_metadata"))
    metadata_pairs = (
        ("APPROVAL_SESSION_ID_MISMATCH", "approval_session_id"),
        ("APPROVAL_DECISION_ID_MISMATCH", "approval_decision_id"),
        ("APPROVAL_REQUEST_ID_MISMATCH", "approval_request_id"),
        ("CANDIDATE_ID_MISMATCH", "candidate_id"),
        ("TRANSACTION_ID_METADATA_MISMATCH", "transaction_id"),
        ("PROJECTION_HASH_MISMATCH", "projection_hash"),
        ("POLICY_HASH_MISMATCH", "policy_hash"),
        ("APPROVED_RULE_HASH_MISMATCH", "approved_rule_hash"),
        ("RUNTIME_BEFORE_HASH_MISMATCH", "runtime_before_hash"),
        ("RUNTIME_AFTER_HASH_MISMATCH", "runtime_after_hash"),
        ("TARGET_MISMATCH", "target"),
    )
    for issue, field in metadata_pairs:
        issues.extend(_compare(issue, context.get(field), metadata.get(field)))
    if context.get("changed_fields") != metadata.get("changed_fields"):
        issues.append("CHANGED_FIELDS_METADATA_MISMATCH")
    if not _clean_text(context.get("reviewer_id")):
        issues.append("REVIEWER_ID_MISSING")
    return issues


def issue_runtime_commit_approval_token_from_context(
    runtime_commit_execution_context: Any,
) -> dict[str, Any]:
    """Issue an approval token by translating execution context to Token Store input."""
    context = deepcopy(_as_dict(runtime_commit_execution_context))
    if not context:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_commit_execution_context is required"}],
            issues=["RUNTIME_COMMIT_EXECUTION_CONTEXT_REQUIRED"],
        )

    execution_snapshot = _as_dict(context.get("execution_snapshot"))
    evidence = {"runtime_commit_execution_context": {"context_id": context.get("context_id")}}
    diagnostics: list[dict[str, Any]] = []
    validation_issues = _validate_context(context)
    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=[
                {"stage": "approval_token_issue_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=validation_issues,
        )

    token_id = _clean_text(context.get("token_issue_request_id"))
    commit_id = _clean_text(context.get("transaction_manifest_preview", {}).get("commit_id"))
    plan_hash = _clean_text(context.get("transaction_manifest_preview", {}).get("execution_plan_hash"))
    storage_plan = create_runtime_commit_token_storage_plan(
        storage_root=_clean_text(context.get("storage_root")),
        token_id=token_id,
        commit_id=commit_id,
    )
    if storage_plan.get("plan_status") != "READY":
        plan_issues = _as_list(storage_plan.get("issues")) or ["TOKEN_STORAGE_PLAN_NOT_READY"]
        return _result(
            status=STATUS_INVALID if storage_plan.get("plan_status") == "INVALID" else STATUS_BLOCKED,
            token_storage_plan=storage_plan,
            execution_snapshot=execution_snapshot,
            diagnostics=[
                {"stage": "token_storage_plan", "ok": False, "reason": issue}
                for issue in plan_issues
            ],
            evidence=evidence,
            issues=plan_issues,
        )

    token_payload = {
        "token_id": token_id,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "issued_for": _clean_text(context.get("consumer_id")),
        "issued_by": _clean_text(context.get("reviewer_id")),
        "scope": _clean_text(context.get("scope")),
        "single_use": True,
        "metadata": deepcopy(_as_dict(context.get("token_metadata"))),
    }
    issue_result = issue_runtime_commit_approval_token(
        storage_plan=storage_plan,
        token=token_payload,
    )
    issue_status = issue_result.get("issue_status")
    if issue_status != ISSUE_ISSUED:
        result_status = STATUS_INVALID if issue_status == ISSUE_INVALID else STATUS_BLOCKED
        if issue_status == ISSUE_BLOCKED:
            result_status = STATUS_BLOCKED
        issue_issues = _as_list(issue_result.get("issues")) or [f"token issue failed: {issue_status}"]
        return _result(
            status=result_status,
            approval_token_issue_result=issue_result,
            token_storage_plan=storage_plan,
            execution_snapshot=execution_snapshot,
            diagnostics=[
                {"stage": "approval_token_issue", "ok": False, "reason": issue}
                for issue in issue_issues
            ],
            evidence=evidence,
            issues=issue_issues,
        )

    token = _as_dict(issue_result.get("token"))
    return _result(
        status=STATUS_READY,
        approval_token_issue_result=issue_result,
        token_storage_plan=storage_plan,
        issued_token_preview=token,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "approval_token_issue", "ok": True, "reason": "approval token issued"}],
        evidence=evidence,
        issues=[],
    )
