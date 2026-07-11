# -*- coding: utf-8 -*-
"""BUY Runtime Commit execution context builder.

This module assembles the final preview-only context used by later token issue
and Real Executor adapters. It never calls the Approval Token Store, Runtime
Commit Real Executor, lock/backup/journal/persistence components, writes
runtime/queue files, calls SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


BUILDER_TYPE = "BUY_RUNTIME_COMMIT_EXECUTION_CONTEXT_BUILDER"
CONTEXT_VERSION = "BUY_RUNTIME_COMMIT_EXECUTION_CONTEXT_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

FORBIDDEN_TRUE_FLAGS = (
    "runtime_write",
    "queue_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "approval_token_issued",
    "approval_token_stored",
    "approval_token_consumed",
    "token_issued",
    "token_stored",
    "token_consumed",
    "execution_allowed",
    "execution_started",
    "lock_acquired",
    "journal_written",
    "manifest_persisted",
    "persistence_write",
    "sqlite_write",
    "send_order_called",
    "broker_called",
    "chejan_connected",
    "gui_updated",
    "runtime_commit_real_executor_called",
    "real_executor_called",
    "actual_execution",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context_id(
    *,
    token_id: str,
    commit_id: str,
    transaction_id: str,
    execution_plan_hash: str,
    consumer_id: str,
    scope: str,
    runtime_after_hash: str,
) -> str:
    digest = _stable_hash({
        "token_id": token_id,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "execution_plan_hash": execution_plan_hash,
        "consumer_id": consumer_id,
        "scope": scope,
        "runtime_after_hash": runtime_after_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_CONTEXT_{}".format(digest)


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _normalize_storage_root(value: Any) -> tuple[str, list[str]]:
    issues: list[str] = []
    if not isinstance(value, str) or not value.strip():
        return "", ["STORAGE_ROOT_MISSING"]
    raw_path = Path(value)
    if any(part == ".." for part in raw_path.parts):
        issues.append("STORAGE_ROOT_PATH_TRAVERSAL")
    root = raw_path.resolve(strict=False)
    project_runtime = (_project_root() / "runtime").resolve(strict=False)
    try:
        root.relative_to(project_runtime)
        issues.append("STORAGE_ROOT_PROJECT_RUNTIME_BLOCKED")
    except ValueError:
        pass
    if "routines" in root.parts:
        issues.append("STORAGE_ROOT_ROUTINES_BLOCKED")
    return str(root), issues


def _find_forbidden_true_flags(value: Any, prefix: str = "input") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for flag in FORBIDDEN_TRUE_FLAGS:
            if value.get(flag) is True:
                issues.append(f"{prefix}.{flag} must be false")
        safety_flags = value.get("safety_flags")
        if isinstance(safety_flags, dict):
            for flag in FORBIDDEN_TRUE_FLAGS:
                if safety_flags.get(flag) is True:
                    issues.append(f"{prefix}.safety_flags.{flag} must be false")
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                issues.extend(_find_forbidden_true_flags(child, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                issues.extend(_find_forbidden_true_flags(child, f"{prefix}[{index}]"))
    return issues


def _result(
    *,
    status: str,
    runtime_commit_execution_context: dict[str, Any] | None = None,
    context_summary: dict[str, Any] | None = None,
    context_report: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "builder_type": BUILDER_TYPE,
        "status": status,
        "preview_only": True,
        "token_issued": False,
        "token_stored": False,
        "token_consumed": False,
        "approval_token_issued": False,
        "approval_token_stored": False,
        "approval_token_consumed": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_commit_execute": False,
        "runtime_write": False,
        "queue_write": False,
        "runtime_commit_real_executor_called": False,
        "lock_acquired": False,
        "backup_created": False,
        "journal_written": False,
        "persistence_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "runtime_commit_execution_context": deepcopy(runtime_commit_execution_context)
        if isinstance(runtime_commit_execution_context, dict)
        else None,
        "context_summary": deepcopy(context_summary) if isinstance(context_summary, dict) else None,
        "context_report": deepcopy(context_report) if isinstance(context_report, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
    }


def _report(*, context: dict[str, Any], summary: dict[str, Any], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": "BUY Runtime Commit Execution Context",
        "context_id": context.get("context_id"),
        "sections": [
            {
                "title": "Context",
                "lines": [
                    "context_id: {}".format(context.get("context_id")),
                    "storage_root: {}".format(context.get("storage_root")),
                    "consumer_id: {}".format(context.get("consumer_id")),
                    "scope: {}".format(context.get("scope")),
                ],
            },
            {
                "title": "Token",
                "lines": [
                    "token_id: {}".format(context.get("token_id")),
                    "token_issue_request_id: {}".format(context.get("token_issue_request_id")),
                    "token_issued: False",
                    "token_consumed: False",
                ],
            },
            {
                "title": "Transaction",
                "lines": [
                    "commit_id: {}".format(context.get("commit_id")),
                    "transaction_id: {}".format(context.get("transaction_id")),
                    "execution_plan_hash: {}".format(context.get("execution_plan_hash")),
                    "target: {}".format(context.get("target")),
                ],
            },
            {
                "title": "Hashes",
                "lines": [
                    "projection_hash: {}".format(context.get("projection_hash")),
                    "runtime_before_hash: {}".format(context.get("runtime_before_hash")),
                    "runtime_after_hash: {}".format(context.get("runtime_after_hash")),
                    "policy_hash: {}".format(context.get("policy_hash")),
                    "approved_rule_hash: {}".format(context.get("approved_rule_hash")),
                ],
            },
            {
                "title": "Changed Fields",
                "lines": list(context.get("changed_fields") or []),
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "summary": deepcopy(summary),
        "preview_only": True,
    }


def _compare(issue: str, left: Any, right: Any) -> list[str]:
    left_text = _clean_text(left)
    right_text = _clean_text(right)
    if left_text and right_text and left_text != right_text:
        return [issue]
    return []


def build_buy_runtime_commit_execution_context(
    *,
    runtime_commit_core_dry_run_result: Any,
    execution_readiness_preview_result: Any,
    approval_session_preview_result: Any,
    token_issue_preview_result: Any,
    context_input: Any,
) -> dict[str, Any]:
    """Build a preview-only execution context for later runtime commit adapters."""
    dry_run_result = deepcopy(_as_dict(runtime_commit_core_dry_run_result))
    readiness_result = deepcopy(_as_dict(execution_readiness_preview_result))
    approval_result = deepcopy(_as_dict(approval_session_preview_result))
    token_result = deepcopy(_as_dict(token_issue_preview_result))
    context_request = deepcopy(_as_dict(context_input))

    if not dry_run_result or not readiness_result or not approval_result or not token_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "all preview inputs are required"}],
            issues=["ALL_PREVIEW_INPUTS_REQUIRED"],
        )

    diagnostics = (
        _as_list(dry_run_result.get("diagnostics"))
        + _as_list(readiness_result.get("diagnostics"))
        + _as_list(approval_result.get("diagnostics"))
        + _as_list(token_result.get("diagnostics"))
    )
    evidence = {
        "dry_run": _as_dict(dry_run_result.get("evidence")),
        "readiness": _as_dict(readiness_result.get("evidence")),
        "approval": _as_dict(approval_result.get("evidence")),
        "token_issue": _as_dict(token_result.get("evidence")),
    }
    issues = (
        _as_list(dry_run_result.get("issues"))
        + _as_list(readiness_result.get("issues"))
        + _as_list(approval_result.get("issues"))
        + _as_list(token_result.get("issues"))
    )
    execution_snapshot = _as_dict(token_result.get("execution_snapshot")) or _as_dict(approval_result.get("execution_snapshot"))

    if _clean_text(token_result.get("status")).upper() != STATUS_READY:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "execution_context", "ok": False, "reason": "token issue preview is not READY"}],
            evidence=evidence,
            issues=issues or ["TOKEN_ISSUE_PREVIEW_NOT_READY"],
        )

    validation_issues: list[str] = []
    for label, result in (
        ("DRY_RUN", dry_run_result),
        ("READINESS", readiness_result),
        ("APPROVAL", approval_result),
        ("TOKEN_ISSUE", token_result),
    ):
        if result.get("preview_only") is False:
            validation_issues.append(f"{label}_PREVIEW_ONLY_FALSE")
        if result.get("runtime_write") is True:
            validation_issues.append(f"{label}_RUNTIME_WRITE_TRUE")
        validation_issues.extend(_find_forbidden_true_flags(result, label.lower()))

    storage_root, storage_issues = _normalize_storage_root(context_request.get("storage_root"))
    validation_issues.extend(storage_issues)
    consumer_id = _clean_text(context_request.get("consumer_id"))
    scope = _clean_text(context_request.get("scope"))
    if not consumer_id:
        validation_issues.append("CONSUMER_ID_MISSING")
    if not scope:
        validation_issues.append("SCOPE_MISSING")

    dry_run = _as_dict(dry_run_result.get("runtime_commit_dry_run"))
    transaction = _as_dict(dry_run_result.get("transaction_contract_preview"))
    apply_plan = _as_dict(dry_run_result.get("apply_plan_preview"))
    verification_plan = _as_dict(dry_run_result.get("verification_plan_preview"))
    rollback_plan = _as_dict(dry_run_result.get("rollback_plan_preview"))
    readiness = _as_dict(readiness_result.get("execution_readiness_preview"))
    approval_request = _as_dict(readiness_result.get("approval_request_preview"))
    session = _as_dict(approval_result.get("approval_session_preview"))
    decision = _as_dict(approval_result.get("approval_decision_preview"))
    approval_summary = _as_dict(approval_result.get("approval_summary"))
    token_issue = _as_dict(token_result.get("token_issue_preview"))

    if not transaction:
        validation_issues.append("TRANSACTION_MANIFEST_PREVIEW_MISSING")
    if not apply_plan:
        validation_issues.append("APPLY_PLAN_PREVIEW_MISSING")
    if not verification_plan:
        validation_issues.append("VERIFICATION_PLAN_PREVIEW_MISSING")
    if not rollback_plan:
        validation_issues.append("ROLLBACK_PLAN_PREVIEW_MISSING")
    if not token_issue:
        validation_issues.append("TOKEN_ISSUE_PREVIEW_MISSING")
    if not execution_snapshot:
        validation_issues.append("EXECUTION_SNAPSHOT_MISSING")

    if _clean_text(decision.get("approval_status")).upper() != "APPROVED":
        validation_issues.append("APPROVAL_DECISION_NOT_APPROVED")
    if decision.get("approval_granted") is not True or approval_summary.get("approval_granted") is not True:
        validation_issues.append("APPROVAL_GRANTED_FALSE")

    token_id = _clean_text(token_issue.get("token_issue_request_id"))
    commit_id = _clean_text(transaction.get("commit_id"))
    transaction_id = _clean_text(transaction.get("transaction_id"))
    candidate_id = _clean_text(token_issue.get("candidate_id"))
    execution_plan_hash = _clean_text(transaction.get("execution_plan_hash"))
    projection_hash = _clean_text(token_issue.get("projection_hash"))
    runtime_before_hash = _clean_text(token_issue.get("runtime_before_hash"))
    runtime_after_hash = _clean_text(token_issue.get("runtime_after_hash"))
    policy_hash = _clean_text(token_issue.get("policy_hash"))
    approved_rule_hash = _clean_text(token_issue.get("approved_rule_hash"))
    target = _clean_text(token_issue.get("target"))
    changed_fields = token_issue.get("changed_fields")

    required_values = {
        "TOKEN_ID_MISSING": token_id,
        "COMMIT_ID_MISSING": commit_id,
        "TRANSACTION_ID_MISSING": transaction_id,
        "CANDIDATE_ID_MISSING": candidate_id,
        "EXECUTION_PLAN_HASH_MISSING": execution_plan_hash,
        "PROJECTION_HASH_MISSING": projection_hash,
        "RUNTIME_BEFORE_HASH_MISSING": runtime_before_hash,
        "RUNTIME_AFTER_HASH_MISSING": runtime_after_hash,
        "POLICY_HASH_MISSING": policy_hash,
        "APPROVED_RULE_HASH_MISSING": approved_rule_hash,
        "TARGET_MISSING": target,
    }
    validation_issues.extend(issue for issue, value in required_values.items() if not value)
    if not isinstance(changed_fields, list) or not changed_fields:
        validation_issues.append("CHANGED_FIELDS_MALFORMED")

    if token_issue.get("token_issued") is True or token_issue.get("token_stored") is True or token_issue.get("token_consumed") is True:
        validation_issues.append("TOKEN_FLAG_PRESET")
    if token_issue.get("execution_allowed") is True or token_issue.get("execution_started") is True:
        validation_issues.append("EXECUTION_FLAG_PRESET")
    if _clean_text(token_issue.get("token_scope")) and _clean_text(token_issue.get("token_scope")) != scope:
        validation_issues.append("SCOPE_MISMATCH")

    validation_issues.extend(_compare("DRY_RUN_ID_MISMATCH", dry_run.get("dry_run_id"), token_issue.get("dry_run_id")))
    validation_issues.extend(_compare("COMMIT_ID_DRY_RUN_MISMATCH", dry_run.get("dry_run_id"), commit_id))
    validation_issues.extend(_compare("TRANSACTION_ID_READINESS_MISMATCH", readiness.get("transaction_id"), transaction_id))
    validation_issues.extend(_compare("TRANSACTION_ID_SESSION_MISMATCH", session.get("transaction_id"), transaction_id))
    validation_issues.extend(_compare("TRANSACTION_ID_TOKEN_MISMATCH", token_issue.get("transaction_id"), transaction_id))
    validation_issues.extend(_compare("CANDIDATE_ID_READINESS_MISMATCH", readiness.get("candidate_id"), candidate_id))
    validation_issues.extend(_compare("CANDIDATE_ID_SESSION_MISMATCH", session.get("candidate_id"), candidate_id))
    validation_issues.extend(_compare("CANDIDATE_ID_APPROVAL_REQUEST_MISMATCH", approval_request.get("candidate_id"), candidate_id))
    validation_issues.extend(_compare("PROJECTION_HASH_DRY_RUN_MISMATCH", dry_run.get("projection_hash"), projection_hash))
    validation_issues.extend(_compare("PROJECTION_HASH_READINESS_MISMATCH", readiness.get("projection_hash"), projection_hash))
    validation_issues.extend(_compare("PROJECTION_HASH_SESSION_MISMATCH", session.get("projection_hash"), projection_hash))
    validation_issues.extend(_compare("POLICY_HASH_DRY_RUN_MISMATCH", dry_run.get("policy_hash"), policy_hash))
    validation_issues.extend(_compare("POLICY_HASH_READINESS_MISMATCH", readiness.get("policy_hash"), policy_hash))
    validation_issues.extend(_compare("POLICY_HASH_SESSION_MISMATCH", session.get("policy_hash"), policy_hash))
    validation_issues.extend(_compare("APPROVED_RULE_HASH_DRY_RUN_MISMATCH", dry_run.get("approved_rule_hash"), approved_rule_hash))
    validation_issues.extend(_compare("APPROVED_RULE_HASH_READINESS_MISMATCH", readiness.get("approved_rule_hash"), approved_rule_hash))
    validation_issues.extend(_compare("APPROVED_RULE_HASH_SESSION_MISMATCH", session.get("approved_rule_hash"), approved_rule_hash))
    validation_issues.extend(_compare("RUNTIME_BEFORE_HASH_DRY_RUN_MISMATCH", dry_run.get("runtime_before_hash"), runtime_before_hash))
    validation_issues.extend(_compare("RUNTIME_AFTER_HASH_DRY_RUN_MISMATCH", dry_run.get("runtime_after_hash"), runtime_after_hash))
    validation_issues.extend(_compare("RUNTIME_BEFORE_HASH_SESSION_MISMATCH", session.get("runtime_before_hash"), runtime_before_hash))
    validation_issues.extend(_compare("RUNTIME_AFTER_HASH_SESSION_MISMATCH", session.get("runtime_after_hash"), runtime_after_hash))
    validation_issues.extend(_compare("APPROVAL_SESSION_ID_MISMATCH", token_issue.get("approval_session_id"), session.get("approval_session_id")))
    validation_issues.extend(_compare("APPROVAL_DECISION_ID_MISMATCH", token_issue.get("decision_id"), decision.get("decision_id")))
    validation_issues.extend(_compare("APPROVAL_REQUEST_ID_MISMATCH", token_issue.get("approval_request_id"), session.get("approval_request_id")))

    future_token_path = Path(storage_root) / "approval_tokens" / f"{token_id}.json" if storage_root and token_id else None
    if future_token_path is not None:
        try:
            future_token_path.resolve(strict=False).relative_to(Path(storage_root).resolve(strict=False))
        except ValueError:
            validation_issues.append("TOKEN_PATH_ESCAPES_STORAGE_ROOT")

    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [
                {"stage": "execution_context_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=issues + validation_issues,
        )

    context_id = _context_id(
        token_id=token_id,
        commit_id=commit_id,
        transaction_id=transaction_id,
        execution_plan_hash=execution_plan_hash,
        consumer_id=consumer_id,
        scope=scope,
        runtime_after_hash=runtime_after_hash,
    )
    context = {
        "context_version": CONTEXT_VERSION,
        "context_id": context_id,
        "storage_root": storage_root,
        "token_id": token_id,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "candidate_id": candidate_id,
        "consumer_id": consumer_id,
        "scope": scope,
        "execution_plan_hash": execution_plan_hash,
        "projection_hash": projection_hash,
        "runtime_before_hash": runtime_before_hash,
        "runtime_after_hash": runtime_after_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "approval_session_id": session.get("approval_session_id"),
        "approval_decision_id": decision.get("decision_id"),
        "approval_request_id": session.get("approval_request_id"),
        "approval_granted": True,
        "reviewer_id": decision.get("reviewer_id"),
        "token_issue_request_id": token_id,
        "target": target,
        "changed_fields": deepcopy(changed_fields),
        "transaction_manifest_preview": deepcopy(transaction),
        "apply_plan_preview": deepcopy(apply_plan),
        "verification_plan_preview": deepcopy(verification_plan),
        "rollback_plan_preview": deepcopy(rollback_plan),
        "execution_snapshot": deepcopy(execution_snapshot),
        "token_metadata": {
            "approval_session_id": session.get("approval_session_id"),
            "approval_decision_id": decision.get("decision_id"),
            "approval_request_id": session.get("approval_request_id"),
            "reviewer_id": decision.get("reviewer_id"),
            "candidate_id": candidate_id,
            "transaction_id": transaction_id,
            "projection_hash": projection_hash,
            "runtime_before_hash": runtime_before_hash,
            "runtime_after_hash": runtime_after_hash,
            "policy_hash": policy_hash,
            "approved_rule_hash": approved_rule_hash,
            "target": target,
            "changed_fields": deepcopy(changed_fields),
        },
        "preview_only": True,
        "token_issued": False,
        "token_stored": False,
        "token_consumed": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_write": False,
    }
    summary = {
        "status": STATUS_READY,
        "context_id": context_id,
        "storage_root": storage_root,
        "token_id": token_id,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "candidate_id": candidate_id,
        "consumer_id": consumer_id,
        "scope": scope,
        "execution_plan_hash": execution_plan_hash,
        "changed_fields_count": len(changed_fields),
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
    }
    report = _report(context=context, summary=summary, diagnostics=diagnostics)
    return _result(
        status=STATUS_READY,
        runtime_commit_execution_context=context,
        context_summary=summary,
        context_report=report,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "execution_context", "ok": True, "reason": "execution context ready"}],
        evidence=evidence,
        issues=issues,
    )
