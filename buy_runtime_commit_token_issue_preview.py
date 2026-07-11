# -*- coding: utf-8 -*-
"""BUY Runtime Commit approval token issue preview.

This module converts an APPROVED Runtime Commit approval session preview into a
token-issue request preview. It never issues, stores, or consumes approval
tokens, calls Runtime Commit Core/Real Executor, acquires locks, creates
backups, writes journals or persistence records, writes runtime/queue files,
calls SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from runtime_commit_approval_token_store import TOKEN_CONTRACT_VERSION, TOKEN_SCOPE


PREVIEW_TYPE = "BUY_RUNTIME_COMMIT_TOKEN_ISSUE_PREVIEW"
TOKEN_ISSUE_VERSION = "BUY_RUNTIME_COMMIT_APPROVAL_TOKEN_ISSUE_V1"
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
    "runtime_commit_core_called",
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


def _token_issue_request_id(
    *,
    approval_session_id: str,
    decision_id: str,
    candidate_id: str,
    transaction_id: str,
    projection_hash: str,
    policy_hash: str,
) -> str:
    digest = _stable_hash({
        "approval_session_id": approval_session_id,
        "decision_id": decision_id,
        "candidate_id": candidate_id,
        "transaction_id": transaction_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_TOKEN_ISSUE_{}".format(digest)


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
    token_issue_preview: dict[str, Any] | None = None,
    token_issue_summary: dict[str, Any] | None = None,
    token_issue_report: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "token_required": True,
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
        "runtime_commit_core_called": False,
        "runtime_commit_real_executor_called": False,
        "lock_acquired": False,
        "backup_created": False,
        "journal_written": False,
        "persistence_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "token_issue_preview": deepcopy(token_issue_preview) if isinstance(token_issue_preview, dict) else None,
        "token_issue_summary": deepcopy(token_issue_summary) if isinstance(token_issue_summary, dict) else None,
        "token_issue_report": deepcopy(token_issue_report) if isinstance(token_issue_report, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
    }


def _report(
    *,
    preview: dict[str, Any],
    summary: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": "BUY Runtime Commit Approval Token Issue Preview",
        "token_issue_request_id": preview.get("token_issue_request_id"),
        "sections": [
            {
                "title": "Decision",
                "lines": [
                    "approval_session_id: {}".format(preview.get("approval_session_id")),
                    "decision_id: {}".format(preview.get("decision_id")),
                    "reviewer_id: {}".format(preview.get("reviewer_id")),
                    "decision_at: {}".format(preview.get("decision_at")),
                ],
            },
            {
                "title": "Token Issue",
                "lines": [
                    "token_required: True",
                    "token_issued: False",
                    "token_stored: False",
                    "token_consumed: False",
                    "execution_allowed: False",
                ],
            },
            {
                "title": "Candidate",
                "lines": [
                    "candidate_id: {}".format(preview.get("candidate_id")),
                    "transaction_id: {}".format(preview.get("transaction_id")),
                    "target: {}".format(preview.get("target")),
                ],
            },
            {
                "title": "Hashes",
                "lines": [
                    "projection_hash: {}".format(preview.get("projection_hash")),
                    "runtime_before_hash: {}".format(preview.get("runtime_before_hash")),
                    "runtime_after_hash: {}".format(preview.get("runtime_after_hash")),
                    "policy_hash: {}".format(preview.get("policy_hash")),
                    "approved_rule_hash: {}".format(preview.get("approved_rule_hash")),
                ],
            },
            {
                "title": "Changed Fields",
                "lines": list(preview.get("changed_fields") or []),
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "summary": deepcopy(summary),
        "preview_only": True,
    }


def _validate_consistency(
    *,
    session: dict[str, Any],
    decision: dict[str, Any],
    summary: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    pairs = (
        ("APPROVAL_SESSION_ID_MISMATCH", decision.get("approval_session_id"), session.get("approval_session_id")),
        ("APPROVAL_REQUEST_ID_MISMATCH", decision.get("approval_request_id"), session.get("approval_request_id")),
        ("SUMMARY_SESSION_ID_MISMATCH", summary.get("approval_session_id"), session.get("approval_session_id")),
        ("SUMMARY_REQUEST_ID_MISMATCH", summary.get("approval_request_id"), session.get("approval_request_id")),
        ("SUMMARY_DECISION_ID_MISMATCH", summary.get("decision_id"), decision.get("decision_id")),
        ("SUMMARY_PROJECTION_HASH_MISMATCH", summary.get("projection_hash"), session.get("projection_hash")),
        ("SUMMARY_POLICY_HASH_MISMATCH", summary.get("policy_hash"), session.get("policy_hash")),
    )
    for issue, left, right in pairs:
        if _clean_text(left) and _clean_text(right) and _clean_text(left) != _clean_text(right):
            issues.append(issue)
    return issues


def build_buy_runtime_commit_token_issue_preview(
    approval_session_preview_result: Any,
) -> dict[str, Any]:
    """Build a preview-only approval token issue request from approval result."""
    approval_result = deepcopy(_as_dict(approval_session_preview_result))
    if not approval_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "approval_session_preview_result is required"}],
            issues=["approval_session_preview_result is required"],
        )

    session = _as_dict(approval_result.get("approval_session_preview"))
    decision = _as_dict(approval_result.get("approval_decision_preview"))
    summary = _as_dict(approval_result.get("approval_summary"))
    execution_snapshot = _as_dict(approval_result.get("execution_snapshot"))
    diagnostics = _as_list(approval_result.get("diagnostics"))
    evidence = _as_dict(approval_result.get("evidence"))
    issues = _as_list(approval_result.get("issues"))
    upstream_status = _clean_text(approval_result.get("status")).upper()

    if upstream_status == STATUS_BLOCKED:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "token_issue", "ok": False, "reason": "approval session status is BLOCKED"}],
            evidence=evidence,
            issues=issues or ["approval session status is BLOCKED"],
        )
    if upstream_status != STATUS_READY:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "token_issue", "ok": False, "reason": "approval session status is not READY"}],
            evidence=evidence,
            issues=issues or ["approval session status is not READY"],
        )

    validation_issues: list[str] = []
    if not session:
        validation_issues.append("APPROVAL_SESSION_PREVIEW_MISSING")
    if not decision:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "token_issue", "ok": False, "reason": "approval decision preview is required"}],
            evidence=evidence,
            issues=issues or ["approval decision preview is required"],
        )
    if _clean_text(session.get("approval_status")).upper() not in {"APPROVED", "READY"}:
        validation_issues.append("APPROVAL_SESSION_NOT_APPROVED")
    if _clean_text(decision.get("approval_status")).upper() != "APPROVED":
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "token_issue", "ok": False, "reason": "approval decision is not APPROVED"}],
            evidence=evidence,
            issues=issues or ["approval decision is not APPROVED"],
        )
    if decision.get("approval_granted") is not True or summary.get("approval_granted") is not True:
        validation_issues.append("APPROVAL_GRANTED_FALSE")
    if not _clean_text(decision.get("reviewer_id")):
        validation_issues.append("REVIEWER_ID_MISSING")
    if not _clean_text(decision.get("decision_id")):
        validation_issues.append("DECISION_ID_MISSING")
    if not execution_snapshot:
        validation_issues.append("EXECUTION_SNAPSHOT_MISSING")
    changed_fields = session.get("changed_fields")
    if not isinstance(changed_fields, list):
        validation_issues.append("CHANGED_FIELDS_MALFORMED")
    validation_issues.extend(_find_forbidden_true_flags(approval_result, "approval_result"))
    validation_issues.extend(_validate_consistency(session=session, decision=decision, summary=summary))

    required_session_fields = (
        "approval_session_id",
        "approval_request_id",
        "dry_run_id",
        "gate_id",
        "preview_id",
        "candidate_id",
        "transaction_id",
        "projection_hash",
        "runtime_before_hash",
        "runtime_after_hash",
        "policy_hash",
        "approved_rule_hash",
        "target",
    )
    for field in required_session_fields:
        if not _clean_text(session.get(field)):
            validation_issues.append(f"{field.upper()}_MISSING")

    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [
                {"stage": "token_issue_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=issues + validation_issues,
        )

    token_issue_request_id = _token_issue_request_id(
        approval_session_id=_clean_text(session.get("approval_session_id")),
        decision_id=_clean_text(decision.get("decision_id")),
        candidate_id=_clean_text(session.get("candidate_id")),
        transaction_id=_clean_text(session.get("transaction_id")),
        projection_hash=_clean_text(session.get("projection_hash")),
        policy_hash=_clean_text(session.get("policy_hash")),
    )
    preview = {
        "token_issue_version": TOKEN_ISSUE_VERSION,
        "token_contract_version": TOKEN_CONTRACT_VERSION,
        "token_scope": TOKEN_SCOPE,
        "token_issue_request_id": token_issue_request_id,
        "approval_session_id": session.get("approval_session_id"),
        "decision_id": decision.get("decision_id"),
        "approval_request_id": session.get("approval_request_id"),
        "reviewer_id": decision.get("reviewer_id"),
        "decision_at": decision.get("decision_at"),
        "dry_run_id": session.get("dry_run_id"),
        "gate_id": session.get("gate_id"),
        "preview_id": session.get("preview_id"),
        "candidate_id": session.get("candidate_id"),
        "transaction_id": session.get("transaction_id"),
        "projection_hash": session.get("projection_hash"),
        "runtime_before_hash": session.get("runtime_before_hash"),
        "runtime_after_hash": session.get("runtime_after_hash"),
        "policy_hash": session.get("policy_hash"),
        "approved_rule_hash": session.get("approved_rule_hash"),
        "target": session.get("target"),
        "changed_fields": deepcopy(changed_fields),
        "risk_level": session.get("risk_level"),
        "single_use": True,
        "token_required": True,
        "token_issued": False,
        "token_stored": False,
        "token_consumed": False,
        "execution_allowed": False,
        "execution_started": False,
        "preview_only": True,
    }
    token_issue_summary = {
        "status": STATUS_READY,
        "token_issue_request_id": token_issue_request_id,
        "approval_session_id": session.get("approval_session_id"),
        "decision_id": decision.get("decision_id"),
        "candidate_id": session.get("candidate_id"),
        "transaction_id": session.get("transaction_id"),
        "risk_level": session.get("risk_level"),
        "token_required": True,
        "token_issued": False,
        "token_stored": False,
        "token_consumed": False,
        "execution_allowed": False,
        "projection_hash": session.get("projection_hash"),
        "policy_hash": session.get("policy_hash"),
        "approved_rule_hash": session.get("approved_rule_hash"),
    }
    report = _report(preview=preview, summary=token_issue_summary, diagnostics=diagnostics)
    return _result(
        status=STATUS_READY,
        token_issue_preview=preview,
        token_issue_summary=token_issue_summary,
        token_issue_report=report,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "token_issue", "ok": True, "reason": "token issue preview ready"}],
        evidence=evidence,
        issues=issues,
    )
