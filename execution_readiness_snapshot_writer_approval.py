# -*- coding: utf-8 -*-
"""Preview-only approval gate for readiness snapshot writer.

This module only decides whether a snapshot writer dry-run result is approved
for a future commit step. It never writes files, creates directories, appends
logs, enqueues orders, calls SendOrder, or invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


APPROVAL_TOKEN = "SNAPSHOT_APPROVAL_PREVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
REQUIRED_FIELDS = ("status", "can_write", "validated", "write_plan")


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


def _blocked_reason(dryrun: dict[str, Any], issues: list[Any]) -> str:
    if issues:
        return str(issues[0])
    summary = _clean_text(dryrun.get("summary"))
    if summary:
        return summary
    return "DRYRUN_VALIDATION_FAILED"


def _invalid(reason: str) -> dict[str, Any]:
    return {
        "status": STATUS_INVALID,
        "approved": False,
        "approval_token": None,
        "approval_reason": None,
        "blocked_reason": reason,
        "summary": "SNAPSHOT_WRITE_APPROVAL_INVALID",
        "checks": {
            "DryrunValidated": "SKIP",
            "CanWrite": "SKIP",
            "PreviewOnly": "SKIP",
            "RuntimeWriteDisabled": "SKIP",
        },
        "warnings": [
            "Preview only",
            "Runtime write disabled",
            "Audit write disabled",
            "Commit disabled",
        ],
        "issues": [reason],
        "commit_plan": {
            "approval_token": None,
            "target_path": None,
            "target_filename": None,
            "estimated_size": None,
            "preview_only": True,
        },
    }


def approve_snapshot_write(dryrun_result: Any) -> dict[str, Any]:
    """Approve or block a snapshot writer dry-run result without committing."""
    dryrun = _as_dict(dryrun_result)
    if not dryrun:
        return _invalid("DRYRUN_RESULT_REQUIRED")

    missing = [field for field in REQUIRED_FIELDS if field not in dryrun]
    if missing:
        return _invalid("MISSING_REQUIRED_DRYRUN_FIELDS: " + ", ".join(missing))

    write_plan = _as_dict(dryrun.get("write_plan"))
    issues = [str(item) for item in _as_list(dryrun.get("issues"))]
    dryrun_ready = dryrun.get("status") == "READY"
    can_write = dryrun.get("can_write") is True
    validated = dryrun.get("validated") is True
    preview_only = write_plan.get("preview_only") is True
    runtime_write_disabled = True

    checks = {
        "DryrunValidated": _check(validated),
        "CanWrite": _check(can_write),
        "PreviewOnly": _check(preview_only),
        "RuntimeWriteDisabled": _check(runtime_write_disabled),
    }

    approved = dryrun_ready and can_write and validated
    if approved:
        status = STATUS_APPROVED
        approval_token = APPROVAL_TOKEN
        approval_reason = "DRYRUN_VALIDATION_PASSED"
        blocked_reason = None
        summary = "SNAPSHOT_WRITE_APPROVED_PREVIEW"
    else:
        status = STATUS_BLOCKED
        approval_token = None
        approval_reason = None
        blocked_reason = _blocked_reason(dryrun, issues)
        summary = "SNAPSHOT_WRITE_APPROVAL_BLOCKED"

    return {
        "status": status,
        "approved": approved,
        "approval_token": approval_token,
        "approval_reason": approval_reason,
        "blocked_reason": blocked_reason,
        "summary": summary,
        "checks": checks,
        "warnings": [
            "Preview only",
            "Runtime write disabled",
            "Audit write disabled",
            "Commit disabled",
        ],
        "issues": issues,
        "commit_plan": {
            "approval_token": approval_token,
            "target_path": deepcopy(write_plan.get("target_path")),
            "target_filename": deepcopy(write_plan.get("target_filename")),
            "estimated_size": deepcopy(write_plan.get("estimated_size")),
            "preview_only": True,
        },
        "dryrun_result": deepcopy(dryrun),
    }
