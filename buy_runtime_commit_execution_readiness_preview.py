# -*- coding: utf-8 -*-
"""BUY Runtime Commit execution readiness preview.

This module converts Runtime Commit Core dry-run output into the final
preview-only readiness layer before real runtime commit execution. It does not
issue or consume approval tokens, acquire locks, create backups, write journals
or persistence records, write runtime/queue files, call the real executor,
SendOrder/Broker/Chejan, or update GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from runtime_commit_transaction_contract import validate_runtime_commit_transaction_manifest


PREVIEW_TYPE = "BUY_RUNTIME_COMMIT_EXECUTION_READINESS_PREVIEW"
APPROVAL_VERSION = "BUY_RUNTIME_COMMIT_APPROVAL_REQUEST_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

FORBIDDEN_TRUE_FLAGS = (
    "runtime_write",
    "queue_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "approval_token_consumed",
    "approval_token_used",
    "token_consumed",
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


def _approval_request_id(
    *,
    dry_run_id: str,
    gate_id: str,
    candidate_id: str,
    projection_hash: str,
    policy_hash: str,
    runtime_after_hash: str,
) -> str:
    digest = _stable_hash({
        "dry_run_id": dry_run_id,
        "gate_id": gate_id,
        "candidate_id": candidate_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
        "runtime_after_hash": runtime_after_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_APPROVAL_{}".format(digest)


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


def _blocking_guard_issues(guard_diagnostics: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for item in guard_diagnostics:
        if not isinstance(item, dict):
            issues.append("guard diagnostic must be dict")
            continue
        if item.get("ok") is False or item.get("blocking") is True:
            issues.append(_clean_text(item.get("name") or item.get("reason") or "blocking guard diagnostic"))
    return issues


def _result(
    *,
    status: str,
    execution_readiness_preview: dict[str, Any] | None = None,
    readiness_summary: dict[str, Any] | None = None,
    readiness_report: dict[str, Any] | None = None,
    approval_request_preview: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "approval_required": True,
        "approval_granted": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_commit_execute": False,
        "runtime_write": False,
        "queue_write": False,
        "runtime_commit_real_executor_called": False,
        "approval_token_issued": False,
        "approval_token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "journal_written": False,
        "persistence_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "execution_readiness_preview": deepcopy(execution_readiness_preview) if isinstance(execution_readiness_preview, dict) else None,
        "readiness_summary": deepcopy(readiness_summary) if isinstance(readiness_summary, dict) else None,
        "readiness_report": deepcopy(readiness_report) if isinstance(readiness_report, dict) else None,
        "approval_request_preview": deepcopy(approval_request_preview) if isinstance(approval_request_preview, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
    }


def _report(
    *,
    readiness: dict[str, Any],
    approval: dict[str, Any],
    transaction: dict[str, Any],
    apply_plan: dict[str, Any],
    verification_plan: dict[str, Any],
    rollback_plan: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "title": "Runtime Commit Execution Readiness",
        "approval_request_id": approval.get("approval_request_id"),
        "sections": [
            {
                "title": "Decision",
                "lines": [
                    "status: {}".format(readiness.get("status")),
                    "ready: {}".format(readiness.get("ready")),
                    "execution_allowed: False",
                ],
            },
            {
                "title": "Approval State",
                "lines": [
                    "approval_required: True",
                    "approval_granted: False",
                    "approval_request_id: {}".format(approval.get("approval_request_id")),
                ],
            },
            {
                "title": "Candidate",
                "lines": [
                    "candidate_id: {}".format(readiness.get("candidate_id")),
                    "dry_run_id: {}".format(readiness.get("dry_run_id")),
                ],
            },
            {
                "title": "Transaction Contract",
                "contract": deepcopy(transaction),
            },
            {
                "title": "Apply Plan",
                "plan": deepcopy(apply_plan),
            },
            {
                "title": "Verification Plan",
                "plan": deepcopy(verification_plan),
            },
            {
                "title": "Rollback Plan",
                "plan": deepcopy(rollback_plan),
            },
            {
                "title": "Changed Fields",
                "lines": list(readiness.get("changed_fields") or []),
            },
            {
                "title": "Hashes",
                "lines": [
                    "projection_hash: {}".format(readiness.get("projection_hash")),
                    "runtime_before_hash: {}".format(readiness.get("runtime_before_hash")),
                    "runtime_after_hash: {}".format(readiness.get("runtime_after_hash")),
                    "policy_hash: {}".format(readiness.get("policy_hash")),
                    "approved_rule_hash: {}".format(readiness.get("approved_rule_hash")),
                ],
            },
            {
                "title": "Warnings",
                "lines": list(warnings),
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "preview_only": True,
    }


def build_buy_runtime_commit_execution_readiness_preview(
    runtime_commit_core_dry_run_result: Any,
    readiness_context: Any = None,
) -> dict[str, Any]:
    """Build execution readiness preview from Runtime Commit Core dry-run."""
    dry_run_result = deepcopy(_as_dict(runtime_commit_core_dry_run_result))
    context = deepcopy(_as_dict(readiness_context))
    if not dry_run_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_commit_core_dry_run_result is required"}],
            issues=["runtime_commit_core_dry_run_result is required"],
        )

    diagnostics = _as_list(dry_run_result.get("diagnostics"))
    evidence = _as_dict(dry_run_result.get("evidence"))
    execution_snapshot = _as_dict(dry_run_result.get("execution_snapshot"))
    dry_run = _as_dict(dry_run_result.get("runtime_commit_dry_run"))
    transaction = _as_dict(dry_run_result.get("transaction_contract_preview"))
    apply_plan = _as_dict(dry_run_result.get("apply_plan_preview"))
    verification_plan = _as_dict(dry_run_result.get("verification_plan_preview"))
    rollback_plan = _as_dict(dry_run_result.get("rollback_plan_preview"))
    guard_diagnostics = _as_list(dry_run_result.get("guard_diagnostics"))
    gate_summary = _as_dict(context.get("gate_summary"))
    issues = _as_list(dry_run_result.get("issues"))
    validation_issues: list[str] = []

    status = _clean_text(dry_run_result.get("status")).upper()
    if status == STATUS_BLOCKED:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "execution_readiness", "ok": False, "reason": "dry-run status is BLOCKED"}],
            evidence=evidence,
            issues=issues or ["dry-run status is BLOCKED"],
        )
    if status != STATUS_READY:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "execution_readiness", "ok": False, "reason": "dry-run status is not READY"}],
            evidence=evidence,
            issues=issues or ["dry-run status is not READY"],
        )

    if dry_run_result.get("dry_run_only") is not True or dry_run.get("dry_run_only") is not True:
        validation_issues.append("DRY_RUN_ONLY_FALSE")
    if dry_run_result.get("runtime_write") is not False or dry_run.get("runtime_write") is not False:
        validation_issues.append("RUNTIME_WRITE_TRUE")
    if dry_run_result.get("queue_write") is not False or dry_run.get("queue_write") is not False:
        validation_issues.append("QUEUE_WRITE_TRUE")
    if dry_run_result.get("runtime_commit_real_executor_called") is not False:
        validation_issues.append("REAL_EXECUTOR_CALLED")
    validation_issues.extend(_find_forbidden_true_flags(dry_run_result, "dry_run_result"))

    dry_run_id = _clean_text(dry_run.get("dry_run_id"))
    gate_id = _clean_text(dry_run.get("gate_id"))
    preview_id = _clean_text(dry_run.get("preview_id"))
    projection_hash = _clean_text(dry_run.get("projection_hash"))
    policy_hash = _clean_text(dry_run.get("policy_hash"))
    approved_rule_hash = _clean_text(dry_run.get("approved_rule_hash"))
    runtime_before_hash = _clean_text(dry_run.get("runtime_before_hash"))
    runtime_after_hash = _clean_text(dry_run.get("runtime_after_hash"))
    target = _clean_text(dry_run.get("target") or apply_plan.get("target"))
    changed_fields = _as_list(dry_run.get("changed_fields"))
    candidate_id = _clean_text(apply_plan.get("source_candidate_id"))

    if not dry_run_id:
        validation_issues.append("DRY_RUN_ID_MISSING")
    if not transaction:
        validation_issues.append("TRANSACTION_CONTRACT_MISSING")
    if not apply_plan:
        validation_issues.append("APPLY_PLAN_MISSING")
    if not verification_plan:
        validation_issues.append("VERIFICATION_PLAN_MISSING")
    if not rollback_plan:
        validation_issues.append("ROLLBACK_PLAN_MISSING")
    if not execution_snapshot:
        validation_issues.append("EXECUTION_SNAPSHOT_MALFORMED")
    if not target:
        validation_issues.append("TARGET_UNKNOWN")
    if not changed_fields:
        validation_issues.append("CHANGED_FIELDS_EMPTY")
    if _blocking_guard_issues(guard_diagnostics):
        validation_issues.extend("BLOCKING_GUARD_DIAGNOSTIC: {}".format(item) for item in _blocking_guard_issues(guard_diagnostics))
    if context.get("approval_granted") is True:
        validation_issues.append("APPROVAL_GRANTED_PRESET")
    if context.get("execution_allowed") is True:
        validation_issues.append("EXECUTION_ALLOWED_PRESET")

    transaction_validation = validate_runtime_commit_transaction_manifest(transaction) if transaction else {"valid": False, "issues": ["missing"]}
    if transaction_validation.get("valid") is not True:
        validation_issues.append("TRANSACTION_CONTRACT_INVALID")
        validation_issues.extend(str(item) for item in transaction_validation.get("issues") or [])

    hash_pairs = (
        ("PROJECTION_HASH_MISMATCH", projection_hash, transaction.get("metadata", {}).get("projection_hash")),
        ("POLICY_HASH_MISMATCH", policy_hash, transaction.get("metadata", {}).get("policy_hash")),
        ("APPROVED_RULE_HASH_MISMATCH", approved_rule_hash, transaction.get("metadata", {}).get("approved_rule_hash")),
        ("RUNTIME_BEFORE_HASH_MISMATCH", runtime_before_hash, apply_plan.get("runtime_state_hash_before")),
        ("RUNTIME_AFTER_HASH_MISMATCH", runtime_after_hash, apply_plan.get("runtime_state_hash_after_candidate")),
    )
    for issue, left, right in hash_pairs:
        if _clean_text(left) and _clean_text(right) and _clean_text(left) != _clean_text(right):
            validation_issues.append(issue)

    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [
                {"stage": "execution_readiness_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=issues + validation_issues,
        )

    approval_request_id = _approval_request_id(
        dry_run_id=dry_run_id,
        gate_id=gate_id,
        candidate_id=candidate_id,
        projection_hash=projection_hash,
        policy_hash=policy_hash,
        runtime_after_hash=runtime_after_hash,
    )
    risk_level = _clean_text(context.get("risk_level")) or "medium"
    approval_request = {
        "approval_version": APPROVAL_VERSION,
        "approval_request_id": approval_request_id,
        "dry_run_id": dry_run_id,
        "gate_id": gate_id,
        "preview_id": preview_id,
        "candidate_id": candidate_id,
        "transaction_id": transaction.get("transaction_id"),
        "projection_hash": projection_hash,
        "runtime_before_hash": runtime_before_hash,
        "runtime_after_hash": runtime_after_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "changed_fields": deepcopy(changed_fields),
        "target": target,
        "risk_level": risk_level,
        "approval_required": True,
        "approval_granted": False,
        "execution_allowed": False,
        "execution_started": False,
        "preview_only": True,
    }
    readiness = {
        "readiness_version": "BUY_RUNTIME_COMMIT_EXECUTION_READINESS_V1",
        "status": STATUS_READY,
        "ready": True,
        "dry_run_id": dry_run_id,
        "gate_id": gate_id,
        "preview_id": preview_id,
        "candidate_id": candidate_id,
        "transaction_id": transaction.get("transaction_id"),
        "projection_hash": projection_hash,
        "runtime_before_hash": runtime_before_hash,
        "runtime_after_hash": runtime_after_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "changed_fields": deepcopy(changed_fields),
        "target": target,
        "approval_required": True,
        "approval_granted": False,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_commit_execute": False,
        "runtime_write": False,
        "preview_only": True,
    }
    summary = {
        "ready": True,
        "approval_required": True,
        "approval_granted": False,
        "execution_allowed": False,
        "changed_fields_count": len(changed_fields),
        "target": target,
        "current_buy_round": gate_summary.get("current_buy_round"),
        "executed_buy_rounds": gate_summary.get("executed_buy_rounds"),
        "cumulative_budget": gate_summary.get("cumulative_budget"),
        "is_last_round": gate_summary.get("is_last_round"),
        "risk_level": risk_level,
        "projection_hash": projection_hash,
        "policy_version": gate_summary.get("policy_version"),
    }
    warnings = _as_list(context.get("warnings"))
    report = _report(
        readiness=readiness,
        approval=approval_request,
        transaction=transaction,
        apply_plan=apply_plan,
        verification_plan=verification_plan,
        rollback_plan=rollback_plan,
        diagnostics=diagnostics,
        warnings=warnings,
    )
    return _result(
        status=STATUS_READY,
        execution_readiness_preview=readiness,
        readiness_summary=summary,
        readiness_report=report,
        approval_request_preview=approval_request,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "execution_readiness", "ok": True, "reason": "readiness preview ready"}],
        evidence=evidence,
        issues=issues,
    )
