# -*- coding: utf-8 -*-
"""BUY Runtime Commit approval session/decision preview.

This module converts Runtime Commit Execution Readiness Preview into a
preview-only approval session and optional approval decision preview. It never
issues or consumes approval tokens, calls token stores, calls Runtime Commit
Core/Real Executor, acquires locks, creates backups, writes journals or
persistence records, writes runtime/queue files, calls SendOrder/Broker/Chejan,
or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "BUY_RUNTIME_COMMIT_APPROVAL_SESSION_PREVIEW"
SESSION_VERSION = "BUY_RUNTIME_COMMIT_APPROVAL_SESSION_V1"
DECISION_VERSION = "BUY_RUNTIME_COMMIT_APPROVAL_DECISION_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
DECISIONS = {"APPROVED", "REJECTED", "DEFERRED"}

FORBIDDEN_TRUE_FLAGS = (
    "runtime_write",
    "queue_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "approval_token_issued",
    "approval_token_consumed",
    "token_issued",
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


def _session_id(
    *,
    approval_request_id: str,
    dry_run_id: str,
    candidate_id: str,
    projection_hash: str,
    policy_hash: str,
) -> str:
    digest = _stable_hash({
        "approval_request_id": approval_request_id,
        "dry_run_id": dry_run_id,
        "candidate_id": candidate_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_APPROVAL_SESSION_{}".format(digest)


def _decision_id(
    *,
    approval_session_id: str,
    decision: str,
    reviewer_id: str,
    decision_reason: str,
    decision_at: str,
) -> str:
    digest = _stable_hash({
        "approval_session_id": approval_session_id,
        "decision": decision,
        "reviewer_id": reviewer_id,
        "decision_reason": decision_reason,
        "decision_at": decision_at,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_APPROVAL_DECISION_{}".format(digest)


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
    approval_session_preview: dict[str, Any] | None = None,
    approval_decision_preview: dict[str, Any] | None = None,
    approval_summary: dict[str, Any] | None = None,
    approval_report: dict[str, Any] | None = None,
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
        "token_issued": False,
        "token_consumed": False,
        "approval_token_issued": False,
        "approval_token_consumed": False,
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
        "approval_session_preview": deepcopy(approval_session_preview)
        if isinstance(approval_session_preview, dict)
        else None,
        "approval_decision_preview": deepcopy(approval_decision_preview)
        if isinstance(approval_decision_preview, dict)
        else None,
        "approval_summary": deepcopy(approval_summary) if isinstance(approval_summary, dict) else None,
        "approval_report": deepcopy(approval_report) if isinstance(approval_report, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
    }


def _report(
    *,
    session: dict[str, Any],
    decision: dict[str, Any] | None,
    summary: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": "BUY Runtime Commit Approval Session Preview",
        "approval_session_id": session.get("approval_session_id"),
        "approval_decision_id": _as_dict(decision).get("decision_id"),
        "sections": [
            {
                "title": "Session",
                "lines": [
                    "approval_session_id: {}".format(session.get("approval_session_id")),
                    "approval_request_id: {}".format(session.get("approval_request_id")),
                    "approval_status: {}".format(session.get("approval_status")),
                ],
            },
            {
                "title": "Decision",
                "decision": deepcopy(decision),
            },
            {
                "title": "Approval State",
                "lines": [
                    "approval_required: True",
                    "approval_granted: {}".format(summary.get("approval_granted")),
                    "execution_allowed: False",
                    "token_issued: False",
                    "token_consumed: False",
                ],
            },
            {
                "title": "Hashes",
                "lines": [
                    "projection_hash: {}".format(session.get("projection_hash")),
                    "runtime_before_hash: {}".format(session.get("runtime_before_hash")),
                    "runtime_after_hash: {}".format(session.get("runtime_after_hash")),
                    "policy_hash: {}".format(session.get("policy_hash")),
                    "approved_rule_hash: {}".format(session.get("approved_rule_hash")),
                ],
            },
            {
                "title": "Changed Fields",
                "lines": list(session.get("changed_fields") or []),
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "summary": deepcopy(summary),
        "preview_only": True,
    }


def _build_session(request: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    required = (
        "approval_request_id",
        "dry_run_id",
        "gate_id",
        "preview_id",
        "candidate_id",
        "projection_hash",
        "runtime_before_hash",
        "runtime_after_hash",
        "policy_hash",
        "approved_rule_hash",
        "transaction_id",
        "target",
    )
    for field in required:
        if not _clean_text(request.get(field)):
            issues.append(f"{field.upper()}_MISSING")
    changed_fields = request.get("changed_fields")
    if not isinstance(changed_fields, list):
        issues.append("CHANGED_FIELDS_MALFORMED")
    session_id = _session_id(
        approval_request_id=_clean_text(request.get("approval_request_id")),
        dry_run_id=_clean_text(request.get("dry_run_id")),
        candidate_id=_clean_text(request.get("candidate_id")),
        projection_hash=_clean_text(request.get("projection_hash")),
        policy_hash=_clean_text(request.get("policy_hash")),
    )
    return {
        "approval_session_version": SESSION_VERSION,
        "approval_session_id": session_id,
        "approval_request_id": request.get("approval_request_id"),
        "dry_run_id": request.get("dry_run_id"),
        "gate_id": request.get("gate_id"),
        "preview_id": request.get("preview_id"),
        "candidate_id": request.get("candidate_id"),
        "projection_hash": request.get("projection_hash"),
        "runtime_before_hash": request.get("runtime_before_hash"),
        "runtime_after_hash": request.get("runtime_after_hash"),
        "policy_hash": request.get("policy_hash"),
        "approved_rule_hash": request.get("approved_rule_hash"),
        "transaction_id": request.get("transaction_id"),
        "target": request.get("target"),
        "changed_fields": deepcopy(changed_fields) if isinstance(changed_fields, list) else [],
        "risk_level": request.get("risk_level"),
        "approval_required": True,
        "approval_status": "PENDING",
        "approval_granted": False,
        "execution_allowed": False,
        "token_issued": False,
        "token_consumed": False,
        "execution_started": False,
        "preview_only": True,
    }, issues


def _build_decision(session: dict[str, Any], decision_input: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if not decision_input:
        return None, []
    issues: list[str] = []
    decision = _clean_text(decision_input.get("decision")).upper()
    reviewer_id = _clean_text(decision_input.get("reviewer_id"))
    decision_reason = _clean_text(decision_input.get("decision_reason"))
    decision_at = _clean_text(decision_input.get("decision_at"))
    if decision not in DECISIONS:
        issues.append("UNSUPPORTED_DECISION")
    if decision == "APPROVED" and not reviewer_id:
        issues.append("REVIEWER_ID_MISSING")
    if decision == "REJECTED" and not decision_reason:
        issues.append("DECISION_REASON_MISSING")
    if decision_input.get("execution_allowed") is True:
        issues.append("EXECUTION_ALLOWED_PRESET")
    if decision_input.get("token_issued") is True or decision_input.get("token_consumed") is True:
        issues.append("TOKEN_FLAG_PRESET")
    if decision_input.get("approval_granted") is True and decision != "APPROVED":
        issues.append("APPROVAL_GRANTED_PRESET")
    decision_id = _decision_id(
        approval_session_id=session.get("approval_session_id", ""),
        decision=decision,
        reviewer_id=reviewer_id,
        decision_reason=decision_reason,
        decision_at=decision_at,
    )
    approved = decision == "APPROVED" and not issues
    preview = {
        "approval_decision_version": DECISION_VERSION,
        "decision_id": decision_id,
        "approval_session_id": session.get("approval_session_id"),
        "approval_request_id": session.get("approval_request_id"),
        "decision": decision,
        "reviewer_id": reviewer_id,
        "decision_reason": decision_reason,
        "decision_at": decision_at,
        "approval_status": decision if decision in DECISIONS else "INVALID",
        "approval_granted": approved,
        "execution_allowed": False,
        "token_issued": False,
        "token_consumed": False,
        "execution_started": False,
        "preview_only": True,
    }
    return preview, issues


def build_buy_runtime_commit_approval_session_preview(
    execution_readiness_preview_result: Any,
    decision_input: Any = None,
) -> dict[str, Any]:
    """Build approval session and optional decision preview from readiness."""
    readiness_result = deepcopy(_as_dict(execution_readiness_preview_result))
    decision_request = deepcopy(_as_dict(decision_input))
    if not readiness_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "execution_readiness_preview_result is required"}],
            issues=["execution_readiness_preview_result is required"],
        )

    execution_snapshot = _as_dict(readiness_result.get("execution_snapshot"))
    diagnostics = _as_list(readiness_result.get("diagnostics"))
    evidence = _as_dict(readiness_result.get("evidence"))
    issues = _as_list(readiness_result.get("issues"))
    status = _clean_text(readiness_result.get("status")).upper()
    if status == STATUS_BLOCKED:
        return _result(
            status=STATUS_BLOCKED,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "approval_session", "ok": False, "reason": "readiness status is BLOCKED"}],
            evidence=evidence,
            issues=issues or ["readiness status is BLOCKED"],
        )
    if status != STATUS_READY:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [{"stage": "approval_session", "ok": False, "reason": "readiness status is not READY"}],
            evidence=evidence,
            issues=issues or ["readiness status is not READY"],
        )

    readiness = _as_dict(readiness_result.get("execution_readiness_preview"))
    request = _as_dict(readiness_result.get("approval_request_preview"))
    validation_issues: list[str] = []
    if not request:
        validation_issues.append("APPROVAL_REQUEST_PREVIEW_MISSING")
    if request.get("approval_required") is not True:
        validation_issues.append("APPROVAL_REQUIRED_FALSE")
    if request.get("approval_granted") is True or readiness.get("approval_granted") is True:
        validation_issues.append("APPROVAL_GRANTED_PRESET")
    if request.get("execution_allowed") is True or readiness.get("execution_allowed") is True:
        validation_issues.append("EXECUTION_ALLOWED_PRESET")
    if request.get("execution_started") is True or readiness.get("execution_started") is True:
        validation_issues.append("EXECUTION_STARTED_PRESET")
    if request.get("token_issued") is True or request.get("token_consumed") is True:
        validation_issues.append("TOKEN_FLAG_PRESET")
    validation_issues.extend(_find_forbidden_true_flags(readiness_result, "readiness_result"))

    session, session_issues = _build_session(request)
    validation_issues.extend(session_issues)
    for field in (
        "dry_run_id",
        "gate_id",
        "preview_id",
        "candidate_id",
        "projection_hash",
        "runtime_before_hash",
        "runtime_after_hash",
        "policy_hash",
        "approved_rule_hash",
        "transaction_id",
        "target",
    ):
        left = _clean_text(readiness.get(field))
        right = _clean_text(request.get(field))
        if left and right and left != right:
            validation_issues.append(f"{field.upper()}_MISMATCH")

    decision, decision_issues = _build_decision(session, decision_request)
    validation_issues.extend(decision_issues)
    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            diagnostics=diagnostics + [
                {"stage": "approval_session_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            evidence=evidence,
            issues=issues + validation_issues,
        )

    approval_status = _as_dict(decision).get("approval_status") or "PENDING"
    if decision:
        session["approval_status"] = approval_status
        session["approval_granted"] = decision.get("approval_granted") is True
    summary = {
        "approval_status": approval_status,
        "approval_required": True,
        "approval_granted": bool(decision and decision.get("approval_granted") is True),
        "execution_allowed": False,
        "token_issued": False,
        "token_consumed": False,
        "approval_session_id": session.get("approval_session_id"),
        "approval_request_id": session.get("approval_request_id"),
        "decision_id": _as_dict(decision).get("decision_id"),
        "candidate_id": session.get("candidate_id"),
        "projection_hash": session.get("projection_hash"),
        "policy_hash": session.get("policy_hash"),
        "risk_level": session.get("risk_level"),
    }
    report = _report(session=session, decision=decision, summary=summary, diagnostics=diagnostics)
    return _result(
        status=STATUS_READY,
        approval_session_preview=session,
        approval_decision_preview=decision,
        approval_summary=summary,
        approval_report=report,
        execution_snapshot=execution_snapshot,
        diagnostics=diagnostics + [{"stage": "approval_session", "ok": True, "reason": "approval session preview ready"}],
        evidence=evidence,
        issues=issues,
    )
