# -*- coding: utf-8 -*-
"""Approval token validation and Runtime Commit Real Executor handoff preview.

This module reads and validates an issued approval token, then prepares the
input shape a later step can hand to ``execute_runtime_commit``. It never
consumes tokens, calls the Real Executor, acquires locks, creates backups,
writes journals or persistence records, writes runtime/queue files, calls
SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from runtime_commit_approval_token_store import (
    READ_OK,
    TOKEN_STATUS_ISSUED,
    VALIDATION_VALID,
    read_runtime_commit_approval_token,
    validate_runtime_commit_approval_token,
)
from runtime_commit_execution_gate import STATUS_APPROVED
from runtime_commit_guard import create_runtime_commit_guard_plan
from runtime_commit_transaction_persistence import create_runtime_transaction_storage_plan


PREVIEW_TYPE = "BUY_RUNTIME_COMMIT_TOKEN_VALIDATION_HANDOFF_PREVIEW"
VALIDATION_VERSION = "BUY_RUNTIME_COMMIT_TOKEN_VALIDATION_PREVIEW_V1"
HANDOFF_VERSION = "BUY_RUNTIME_COMMIT_REAL_EXECUTOR_HANDOFF_PREVIEW_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _handoff_id(
    *,
    context_id: str,
    token_id: str,
    commit_id: str,
    plan_hash: str,
    consumer_id: str,
    runtime_after_hash: str,
) -> str:
    digest = _stable_hash({
        "context_id": context_id,
        "token_id": token_id,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "consumer_id": consumer_id,
        "runtime_after_hash": runtime_after_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_HANDOFF_{}".format(digest)


def _result(
    *,
    status: str,
    token_validation_preview: dict[str, Any] | None = None,
    real_executor_handoff_preview: dict[str, Any] | None = None,
    handoff_summary: dict[str, Any] | None = None,
    handoff_report: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "token_validation_preview": deepcopy(token_validation_preview)
        if isinstance(token_validation_preview, dict)
        else None,
        "real_executor_handoff_preview": deepcopy(real_executor_handoff_preview)
        if isinstance(real_executor_handoff_preview, dict)
        else None,
        "handoff_summary": deepcopy(handoff_summary) if isinstance(handoff_summary, dict) else None,
        "handoff_report": deepcopy(handoff_report) if isinstance(handoff_report, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
        "token_consumed": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_commit_real_executor_called": False,
        "runtime_write": False,
        "queue_write": False,
        "lock_acquired": False,
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


def _metadata_issues(context: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    pairs = (
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
    for issue, field in pairs:
        issues.extend(_compare(issue, context.get(field), metadata.get(field)))
    if context.get("changed_fields") != metadata.get("changed_fields"):
        issues.append("CHANGED_FIELDS_METADATA_MISMATCH")
    return issues


def _context_validation_issues(context: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if _clean_text(context.get("context_version")) != "BUY_RUNTIME_COMMIT_EXECUTION_CONTEXT_V1":
        issues.append("MALFORMED_EXECUTION_CONTEXT")
    if context.get("preview_only") is not True:
        issues.append("PREVIEW_ONLY_FALSE")
    if context.get("runtime_write") is True:
        issues.append("RUNTIME_WRITE_TRUE")
    if context.get("execution_allowed") is True or context.get("execution_started") is True:
        issues.append("EXECUTION_FLAG_PRESET")
    if context.get("token_consumed") is True:
        issues.append("TOKEN_CONSUMED_PRESET")
    required = {
        "CONTEXT_ID_MISSING": context.get("context_id"),
        "TOKEN_ID_MISSING": context.get("token_id"),
        "COMMIT_ID_MISSING": context.get("commit_id"),
        "TRANSACTION_ID_MISSING": context.get("transaction_id"),
        "CONSUMER_ID_MISSING": context.get("consumer_id"),
        "SCOPE_MISSING": context.get("scope"),
        "EXECUTION_PLAN_HASH_MISSING": context.get("execution_plan_hash"),
        "RUNTIME_AFTER_HASH_MISSING": context.get("runtime_after_hash"),
    }
    issues.extend(issue for issue, value in required.items() if not _clean_text(value))
    if not _as_dict(context.get("transaction_manifest_preview")):
        issues.append("TRANSACTION_MANIFEST_MISSING")
    return issues


def _build_validation_preview(
    *,
    token: dict[str, Any] | None,
    context: dict[str, Any],
    storage_plan: dict[str, Any],
    validation_result: dict[str, Any] | None,
    token_valid: bool,
    reason: str,
) -> dict[str, Any]:
    token_dict = _as_dict(token)
    return {
        "validation_version": VALIDATION_VERSION,
        "token_id": token_dict.get("token_id") or context.get("token_id"),
        "commit_id": token_dict.get("commit_id") or context.get("commit_id"),
        "plan_hash": token_dict.get("plan_hash") or context.get("execution_plan_hash"),
        "scope": token_dict.get("scope") or context.get("scope"),
        "consumer_id": context.get("consumer_id"),
        "token_status": token_dict.get("token_status", ""),
        "token_valid": token_valid,
        "single_use": token_dict.get("single_use"),
        "token_consumed": False,
        "validation_result": deepcopy(validation_result or {}),
        "validation_reason": reason,
        "token_metadata": deepcopy(_as_dict(token_dict.get("metadata"))),
        "storage_plan": deepcopy(storage_plan),
        "execution_snapshot": deepcopy(_as_dict(context.get("execution_snapshot"))),
        "preview_only": True,
    }


def _report(*, handoff: dict[str, Any], summary: dict[str, Any], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": "BUY Runtime Commit Real Executor Handoff Preview",
        "handoff_id": handoff.get("handoff_id"),
        "sections": [
            {
                "title": "Token",
                "lines": [
                    "token_id: {}".format(handoff.get("token_id")),
                    "token_valid: True",
                    "token_consumed: False",
                ],
            },
            {
                "title": "Transaction",
                "lines": [
                    "commit_id: {}".format(handoff.get("commit_id")),
                    "transaction_id: {}".format(handoff.get("transaction_id")),
                    "plan_hash: {}".format(handoff.get("plan_hash")),
                ],
            },
            {
                "title": "Hashes",
                "lines": [
                    "projection_hash: {}".format(handoff.get("projection_hash")),
                    "runtime_before_hash: {}".format(handoff.get("runtime_before_hash")),
                    "runtime_after_hash: {}".format(handoff.get("runtime_after_hash")),
                    "policy_hash: {}".format(handoff.get("policy_hash")),
                    "approved_rule_hash: {}".format(handoff.get("approved_rule_hash")),
                ],
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "summary": deepcopy(summary),
        "preview_only": True,
    }


def build_runtime_commit_token_validation_handoff_preview(
    *,
    runtime_commit_execution_context: Any,
    approval_token_issue_result: Any,
    token_storage_plan: Any,
    issued_token: Any = None,
) -> dict[str, Any]:
    """Read/validate the issued token and build a Real Executor handoff preview."""
    context = deepcopy(_as_dict(runtime_commit_execution_context))
    issue_result = deepcopy(_as_dict(approval_token_issue_result))
    storage_plan = deepcopy(_as_dict(token_storage_plan))
    issued = deepcopy(_as_dict(issued_token))

    if not context:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_commit_execution_context is required"}],
            issues=["RUNTIME_COMMIT_EXECUTION_CONTEXT_REQUIRED"],
        )
    execution_snapshot = _as_dict(context.get("execution_snapshot"))
    evidence = {"runtime_commit_execution_context": {"context_id": context.get("context_id")}}
    diagnostics: list[dict[str, Any]] = []
    context_issues = _context_validation_issues(context)
    if context_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=[
                {"stage": "handoff_context_validation", "ok": False, "reason": issue}
                for issue in context_issues
            ],
            evidence=evidence,
            issues=context_issues,
        )

    if issue_result.get("status") != STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=[{"stage": "token_issue_result", "ok": False, "reason": "token issue result is not READY"}],
            evidence=evidence,
            issues=["TOKEN_ISSUE_RESULT_NOT_READY"],
        )
    if not storage_plan or storage_plan.get("plan_status") != "READY":
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=[{"stage": "token_storage_plan", "ok": False, "reason": "malformed storage plan"}],
            evidence=evidence,
            issues=["MALFORMED_STORAGE_PLAN"],
        )

    read_result = read_runtime_commit_approval_token(storage_plan=storage_plan)
    if read_result.get("read_status") != READ_OK:
        preview = _build_validation_preview(
            token=None,
            context=context,
            storage_plan=storage_plan,
            validation_result=read_result,
            token_valid=False,
            reason="token read failed: {}".format(read_result.get("read_status")),
        )
        return _result(
            status=STATUS_BLOCKED,
            token_validation_preview=preview,
            execution_snapshot=execution_snapshot,
            diagnostics=[{"stage": "token_read", "ok": False, "reason": preview["validation_reason"]}],
            evidence=evidence,
            issues=_as_list(read_result.get("issues")) or ["TOKEN_NOT_FOUND_OR_INVALID"],
        )

    token = _as_dict(read_result.get("token"))
    validation_result = validate_runtime_commit_approval_token(
        token=token,
        expected_commit_id=context.get("commit_id"),
        expected_plan_hash=context.get("execution_plan_hash"),
        expected_scope=context.get("scope"),
    )
    validation_issues: list[str] = []
    if validation_result.get("validation_status") != VALIDATION_VALID or validation_result.get("valid_for_execution") is not True:
        validation_issues.extend(_as_list(validation_result.get("issues")) or ["TOKEN_VALIDATION_FAILED"])
    if token.get("token_status") != TOKEN_STATUS_ISSUED:
        validation_issues.append("TOKEN_STATUS_NOT_ISSUED")
    if token.get("single_use") is not True:
        validation_issues.append("SINGLE_USE_FALSE")
    if token.get("consumed_by") is not None or token.get("consumed_at") is not None or token.get("consumption_id") is not None:
        validation_issues.append("TOKEN_CONSUMED_FIELDS_PRESENT")
    validation_issues.extend(_compare("TOKEN_ID_MISMATCH", token.get("token_id"), context.get("token_id")))
    validation_issues.extend(_compare("CONSUMER_ID_MISMATCH", token.get("issued_for"), context.get("consumer_id")))
    validation_issues.extend(_metadata_issues(context, _as_dict(token.get("metadata"))))
    if issued:
        validation_issues.extend(_compare("ISSUED_TOKEN_ID_MISMATCH", issued.get("token_id"), token.get("token_id")))

    token_valid = not validation_issues
    reason = "token valid" if token_valid else "; ".join(validation_issues)
    validation_preview = _build_validation_preview(
        token=token,
        context=context,
        storage_plan=storage_plan,
        validation_result=validation_result,
        token_valid=token_valid,
        reason=reason,
    )
    if validation_issues:
        return _result(
            status=STATUS_BLOCKED,
            token_validation_preview=validation_preview,
            execution_snapshot=execution_snapshot,
            diagnostics=[
                {"stage": "token_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=validation_issues,
        )

    transaction = _as_dict(context.get("transaction_manifest_preview"))
    target = _clean_text(context.get("target"))
    storage_plan_preview = create_runtime_transaction_storage_plan(
        storage_root=context.get("storage_root"),
        commit_id=context.get("commit_id"),
        transaction_id=context.get("transaction_id"),
    )
    guard_plan = create_runtime_commit_guard_plan(
        storage_root=context.get("storage_root"),
        commit_id=context.get("commit_id"),
        transaction_id=context.get("transaction_id"),
        target_set_hash=transaction.get("target_set_hash"),
        owner_id=context.get("consumer_id"),
    )
    handoff_id = _handoff_id(
        context_id=_clean_text(context.get("context_id")),
        token_id=_clean_text(context.get("token_id")),
        commit_id=_clean_text(context.get("commit_id")),
        plan_hash=_clean_text(context.get("execution_plan_hash")),
        consumer_id=_clean_text(context.get("consumer_id")),
        runtime_after_hash=_clean_text(context.get("runtime_after_hash")),
    )
    gate_result = {
        "gate_status": STATUS_APPROVED,
        "commit_id": context.get("commit_id"),
        "preview_only": True,
        "execution_allowed": False,
        "actual_execution": False,
        "token_consumed": False,
        "ready_for_real_executor": True,
        "gate_metadata": {
            "plan_hash": context.get("execution_plan_hash"),
            "preview_only": True,
            "actual_execution": False,
        },
        "issues": [],
        "warnings": [],
    }
    expected_targets = {target: {}} if target else {}
    new_targets = {target: deepcopy(_as_dict(context.get("estimated_runtime_state")))} if target else {}
    handoff = {
        "handoff_version": HANDOFF_VERSION,
        "handoff_id": handoff_id,
        "context_id": context.get("context_id"),
        "token_id": context.get("token_id"),
        "commit_id": context.get("commit_id"),
        "transaction_id": context.get("transaction_id"),
        "candidate_id": context.get("candidate_id"),
        "consumer_id": context.get("consumer_id"),
        "scope": context.get("scope"),
        "plan_hash": context.get("execution_plan_hash"),
        "projection_hash": context.get("projection_hash"),
        "policy_hash": context.get("policy_hash"),
        "approved_rule_hash": context.get("approved_rule_hash"),
        "runtime_before_hash": context.get("runtime_before_hash"),
        "runtime_after_hash": context.get("runtime_after_hash"),
        "token_storage_plan": deepcopy(storage_plan),
        "transaction_manifest": deepcopy(transaction),
        "gate_result": gate_result,
        "storage_plan": storage_plan_preview,
        "guard_plan": guard_plan,
        "expected_targets": expected_targets,
        "new_targets": new_targets,
        "execution_snapshot": deepcopy(execution_snapshot),
        "preview_only": True,
        "token_valid": True,
        "token_consumed": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_write": False,
    }
    summary = {
        "handoff_id": handoff_id,
        "context_id": context.get("context_id"),
        "token_id": context.get("token_id"),
        "commit_id": context.get("commit_id"),
        "transaction_id": context.get("transaction_id"),
        "candidate_id": context.get("candidate_id"),
        "consumer_id": context.get("consumer_id"),
        "token_valid": True,
        "token_consumed": False,
        "runtime_write": False,
    }
    report = _report(handoff=handoff, summary=summary, diagnostics=diagnostics)
    return _result(
        status=STATUS_READY,
        token_validation_preview=validation_preview,
        real_executor_handoff_preview=handoff,
        handoff_summary=summary,
        handoff_report=report,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "token_validation_handoff", "ok": True, "reason": "handoff preview ready"}],
        evidence=evidence,
        issues=[],
    )
