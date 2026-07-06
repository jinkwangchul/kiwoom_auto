# -*- coding: utf-8 -*-
"""Preview-only validator for snapshot writer commit plans.

This module only validates the integrity of an approval gate commit_plan. It
never commits, creates files or directories, writes runtime files, appends logs,
enqueues orders, calls SendOrder, or invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_VALID = "VALID"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
REQUIRED_APPROVAL_FIELDS = ("status", "approved", "commit_plan")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _check(value: bool, *, available: bool = True) -> str:
    if not available:
        return "SKIP"
    return "PASS" if value else "FAIL"


def _estimated_size_valid(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return False


def _invalid_result(reason: str, approval: dict[str, Any] | None = None) -> dict[str, Any]:
    source = approval or {}
    return {
        "status": STATUS_INVALID,
        "valid": False,
        "validated": False,
        "summary": "SNAPSHOT_COMMIT_PLAN_INVALID",
        "checks": {
            "ApprovalStatus": "SKIP",
            "ApprovalToken": "SKIP",
            "CommitPlan": "SKIP",
            "TargetPath": "SKIP",
            "TargetFilename": "SKIP",
            "EstimatedSize": "SKIP",
            "PreviewOnly": "SKIP",
        },
        "warnings": [
            "Preview only",
            "Commit disabled",
            "Runtime write disabled",
            "Audit write disabled",
        ],
        "issues": [reason],
        "validated_commit_plan": deepcopy(_as_dict(source.get("commit_plan"))),
    }


def validate_snapshot_commit_plan(approval_result: Any) -> dict[str, Any]:
    """Validate a snapshot writer approval commit_plan without committing."""
    approval = _as_dict(approval_result)
    if not approval:
        return _invalid_result("APPROVAL_RESULT_REQUIRED")

    missing = [field for field in REQUIRED_APPROVAL_FIELDS if field not in approval]
    if missing:
        return _invalid_result("MISSING_REQUIRED_APPROVAL_FIELDS: " + ", ".join(missing), approval)

    approval_status_ok = approval.get("status") == "APPROVED"
    approved_ok = approval.get("approved") is True
    commit_plan = _as_dict(approval.get("commit_plan"))

    if not approval_status_ok or not approved_ok:
        issues = [str(item) for item in _as_list(approval.get("issues"))]
        if not issues:
            issues = [_clean_text(approval.get("blocked_reason")) or "APPROVAL_NOT_APPROVED"]
        return {
            "status": STATUS_BLOCKED,
            "valid": False,
            "validated": False,
            "summary": "SNAPSHOT_COMMIT_PLAN_BLOCKED",
            "checks": {
                "ApprovalStatus": _check(approval_status_ok),
                "ApprovalToken": "SKIP",
                "CommitPlan": "SKIP",
                "TargetPath": "SKIP",
                "TargetFilename": "SKIP",
                "EstimatedSize": "SKIP",
                "PreviewOnly": "SKIP",
            },
            "warnings": [
                "Preview only",
                "Commit disabled",
                "Runtime write disabled",
                "Audit write disabled",
            ],
            "issues": issues,
            "validated_commit_plan": deepcopy(commit_plan),
            "approval_result": deepcopy(approval),
        }

    if not commit_plan:
        return _invalid_result("MISSING_COMMIT_PLAN", approval)

    approval_token = _clean_text(commit_plan.get("approval_token"))
    target_path = _clean_text(commit_plan.get("target_path"))
    target_filename = _clean_text(commit_plan.get("target_filename"))
    estimated_size = commit_plan.get("estimated_size")
    preview_only = commit_plan.get("preview_only") is True

    issues: list[str] = []
    if not approval_token:
        issues.append("MISSING_APPROVAL_TOKEN")
    if not target_path:
        issues.append("MISSING_TARGET_PATH")
    if not target_filename:
        issues.append("MISSING_TARGET_FILENAME")
    if not _estimated_size_valid(estimated_size):
        issues.append("INVALID_ESTIMATED_SIZE")
    if not preview_only:
        issues.append("PREVIEW_FLAG_DISABLED")

    checks = {
        "ApprovalStatus": _check(approval_status_ok and approved_ok),
        "ApprovalToken": _check(bool(approval_token)),
        "CommitPlan": _check(bool(commit_plan)),
        "TargetPath": _check(bool(target_path)),
        "TargetFilename": _check(bool(target_filename)),
        "EstimatedSize": _check(_estimated_size_valid(estimated_size)),
        "PreviewOnly": _check(preview_only),
    }

    if issues:
        status = STATUS_INVALID
        valid = False
        validated = False
        summary = "SNAPSHOT_COMMIT_PLAN_INVALID"
    else:
        status = STATUS_VALID
        valid = True
        validated = True
        summary = "SNAPSHOT_COMMIT_PLAN_VALID"

    return {
        "status": status,
        "valid": valid,
        "validated": validated,
        "summary": summary,
        "checks": checks,
        "warnings": [
            "Preview only",
            "Commit disabled",
            "Runtime write disabled",
            "Audit write disabled",
        ],
        "issues": issues,
        "validated_commit_plan": deepcopy(commit_plan),
        "approval_result": deepcopy(approval),
    }
