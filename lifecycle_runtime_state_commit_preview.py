# -*- coding: utf-8 -*-
"""Preview-only runtime state commit boundary.

This module converts Runtime File Writer Preview into commit candidate,
boundary, token, and post-commit verification previews. It never executes a
commit, writes files, creates backups, rolls back, writes SQLite, updates GUI,
calls SendOrder, or connects Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PREVIEW_TYPE = "LIFECYCLE_RUNTIME_STATE_COMMIT_PREVIEW"
STATUS_READY = "STATE_COMMIT_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

FILE_WRITER_PREVIEW_READY = "FILE_WRITER_PREVIEW_READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _validation(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }


def _empty_candidate_preview() -> dict[str, Any]:
    return {
        "runtime_commit_candidates": [],
        "position_commit_candidates": [],
        "balance_commit_candidates": [],
        "audit_commit_candidates": [],
        "candidate_count": 0,
        "preview_only": True,
    }


def _commit_candidate_preview(file_writer: dict[str, Any]) -> dict[str, Any]:
    candidates = _as_dict(file_writer.get("write_candidate_preview"))
    runtime_candidates = _as_list(candidates.get("runtime_write_candidates"))
    position_candidates = _as_list(candidates.get("position_write_candidates"))
    balance_candidates = _as_list(candidates.get("balance_write_candidates"))
    audit_candidates = _as_list(candidates.get("audit_write_candidates"))
    return {
        "runtime_commit_candidates": deepcopy(runtime_candidates),
        "position_commit_candidates": deepcopy(position_candidates),
        "balance_commit_candidates": deepcopy(balance_candidates),
        "audit_commit_candidates": deepcopy(audit_candidates),
        "candidate_count": len(runtime_candidates) + len(position_candidates) + len(balance_candidates) + len(audit_candidates),
        "preview_only": True,
    }


def _commit_boundary_preview(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "boundary_id": _text(context.get("commit_boundary_id")) or "RUNTIME_STATE_COMMIT_BOUNDARY_{}".format(uuid4().hex),
        "boundary_type": "RUNTIME_STATE_COMMIT_PREVIEW_BOUNDARY",
        "atomic": True,
        "all_or_nothing": True,
        "requires_backup": True,
        "requires_rollback": True,
        "preview_only": True,
        "commit_executed": False,
    }


def _commit_token_preview(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "token_preview_id": _text(context.get("commit_token_preview_id")) or "RUNTIME_STATE_COMMIT_TOKEN_{}".format(uuid4().hex),
        "token_required": True,
        "token_issued": False,
        "token_consumed": False,
        "preview_only": True,
    }


def _post_commit_verification_preview(file_writer: dict[str, Any]) -> dict[str, Any]:
    targets = _as_dict(file_writer.get("file_target_preview"))
    verification_targets = {
        "runtime_targets": deepcopy(_as_list(targets.get("runtime_targets"))),
        "position_targets": deepcopy(_as_list(targets.get("position_targets"))),
        "balance_targets": deepcopy(_as_list(targets.get("balance_targets"))),
        "audit_targets": deepcopy(_as_list(targets.get("audit_targets"))),
    }
    return {
        "verification_required": True,
        "verification_executed": False,
        "verification_targets": verification_targets,
        "preview_only": True,
    }


def _final_commit_decision(status: str, issues: list[str]) -> dict[str, Any]:
    approved = status == STATUS_READY
    return {
        "approved": approved,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "commit_allowed": False,
        "commit_executed": False,
        "approval_reason": "file writer preview is ready and commit preflight validation passed" if approved else "",
        "rejection_reason": "; ".join(issues) if not approved else "",
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    commit_candidate_preview: dict[str, Any],
    commit_boundary_preview: dict[str, Any],
    commit_token_preview: dict[str, Any],
    post_commit_verification_preview: dict[str, Any],
    commit_preflight_validation: dict[str, Any],
    final_commit_decision: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    now: str,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "commit_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "commit_candidate_preview": deepcopy(commit_candidate_preview),
        "commit_boundary_preview": deepcopy(commit_boundary_preview),
        "commit_token_preview": deepcopy(commit_token_preview),
        "post_commit_verification_preview": deepcopy(post_commit_verification_preview),
        "commit_preflight_validation": deepcopy(commit_preflight_validation),
        "final_commit_decision": deepcopy(final_commit_decision),
        "generated_at": now,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _validate_file_writer_preview(file_writer: dict[str, Any]) -> tuple[str, list[str]]:
    if not file_writer:
        return STATUS_INVALID, ["file_writer_preview must be a dict"]

    status = _text(file_writer.get("status")).upper()
    upstream_issues = list(file_writer.get("issues") or [])
    if status == STATUS_BLOCKED:
        return STATUS_BLOCKED, ["file writer preview is BLOCKED"] + upstream_issues
    if status == STATUS_INVALID:
        return STATUS_INVALID, ["file writer preview is INVALID"] + upstream_issues
    if status != FILE_WRITER_PREVIEW_READY:
        return STATUS_INVALID, ["file writer preview status is not FILE_WRITER_PREVIEW_READY"]

    if file_writer.get("preview_only") is not True:
        return STATUS_INVALID, ["file writer preview_only must be true"]
    for flag in (
        "runtime_write",
        "position_write",
        "balance_write",
        "audit_write",
        "file_write_called",
        "backup_created",
        "rollback_executed",
        "gui_update_called",
        "send_order_called",
        "chejan_called",
    ):
        if file_writer.get(flag) is not False:
            return STATUS_INVALID, ["file writer {} must be false".format(flag)]

    if not _as_dict(file_writer.get("write_candidate_preview")):
        return STATUS_INVALID, ["write_candidate_preview is required"]
    if not _as_dict(file_writer.get("file_target_preview")):
        return STATUS_INVALID, ["file_target_preview is required"]

    validation = _as_dict(file_writer.get("writer_preflight_validation"))
    if validation.get("ready") is not True:
        return STATUS_BLOCKED, ["writer_preflight_validation.ready must be true"]

    decision = _as_dict(file_writer.get("final_writer_decision"))
    if decision.get("approved") is not True:
        return STATUS_BLOCKED, ["final_writer_decision.approved must be true"]
    if decision.get("file_write_allowed") is not False:
        return STATUS_INVALID, ["final_writer_decision.file_write_allowed must be false"]

    return STATUS_READY, []


def build_runtime_state_commit_preview(
    file_writer_preview: Any,
    commit_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only runtime state commit payload from file writer preview."""
    file_writer = deepcopy(_as_dict(file_writer_preview))
    context = deepcopy(_as_dict(commit_context))
    now = _text(context.get("generated_at")) or _now_text()
    warnings = list(file_writer.get("warnings") or [])

    status, issues = _validate_file_writer_preview(file_writer)
    candidates = _commit_candidate_preview(file_writer) if status == STATUS_READY else _empty_candidate_preview()
    boundary = _commit_boundary_preview(context)
    token = _commit_token_preview(context)
    verification = _post_commit_verification_preview(file_writer) if status == STATUS_READY else {
        "verification_required": True,
        "verification_executed": False,
        "verification_targets": {},
        "preview_only": True,
    }
    validation = _validation(status, issues, warnings)
    decision = _final_commit_decision(status, issues)
    return _result(
        status=status,
        commit_candidate_preview=candidates,
        commit_boundary_preview=boundary,
        commit_token_preview=token,
        post_commit_verification_preview=verification,
        commit_preflight_validation=validation,
        final_commit_decision=decision,
        issues=issues,
        warnings=warnings,
        now=now,
    )
