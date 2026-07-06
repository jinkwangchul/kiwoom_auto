# -*- coding: utf-8 -*-
"""Preview-only orchestrator for the readiness snapshot pipeline.

This module runs the in-memory snapshot preview pipeline:
Export Preview -> Writer Dry-run -> Approval Gate -> Commit Plan Validation.
It never commits, creates files or directories, writes runtime files, appends
logs, enqueues orders, calls SendOrder, or invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_readiness_snapshot_commit_plan_validator import validate_snapshot_commit_plan
from execution_readiness_snapshot_export import build_execution_readiness_snapshot_export
from execution_readiness_snapshot_writer_approval import approve_snapshot_write
from execution_readiness_snapshot_writer_dryrun import validate_snapshot_write_dryrun


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _unique_text(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _as_list(value):
            text = str(item)
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
    return result


def _step(result: bool, *, available: bool = True) -> str:
    if not available:
        return "SKIP"
    return "PASS" if result else "FAIL"


def _invalid_result(
    reason: str,
    *,
    snapshot_export: dict[str, Any] | None = None,
    dryrun_result: dict[str, Any] | None = None,
    approval_result: dict[str, Any] | None = None,
    commit_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    export = snapshot_export or {}
    dryrun = dryrun_result or {}
    approval = approval_result or {}
    validation = commit_validation or {}
    return {
        "status": STATUS_INVALID,
        "completed": False,
        "summary": "SNAPSHOT_PIPELINE_INVALID",
        "pipeline_steps": {
            "ExportPreview": _step(bool(export)),
            "WriterDryrun": _step(False, available=bool(dryrun)),
            "ApprovalGate": _step(False, available=bool(approval)),
            "CommitPlanValidation": _step(False, available=bool(validation)),
        },
        "snapshot_export": deepcopy(export),
        "dryrun_result": deepcopy(dryrun),
        "approval_result": deepcopy(approval),
        "commit_validation": deepcopy(validation),
        "warnings": _unique_text(
            export.get("warnings"),
            dryrun.get("warnings"),
            approval.get("warnings"),
            validation.get("warnings"),
        ),
        "issues": _unique_text(
            [reason],
            export.get("issues"),
            dryrun.get("issues"),
            approval.get("issues"),
            validation.get("issues"),
        ),
    }


def run_snapshot_pipeline_preview(audit_record: Any) -> dict[str, Any]:
    """Run the full snapshot preview pipeline without any file side effects."""
    try:
        snapshot_export = build_execution_readiness_snapshot_export(deepcopy(audit_record))
    except Exception as exc:  # pragma: no cover - defensive orchestration boundary
        return _invalid_result(f"EXPORT_PREVIEW_FAILED: {exc}")

    export = _as_dict(snapshot_export)
    if not export:
        return _invalid_result("EXPORT_PREVIEW_FAILED", snapshot_export=export)

    dryrun_result = validate_snapshot_write_dryrun(export)
    dryrun = _as_dict(dryrun_result)
    if dryrun.get("status") == "INVALID":
        return _invalid_result("WRITER_DRYRUN_INVALID", snapshot_export=export, dryrun_result=dryrun)

    approval_result = approve_snapshot_write(dryrun)
    approval = _as_dict(approval_result)
    if approval.get("status") == "INVALID":
        return _invalid_result(
            "APPROVAL_GATE_INVALID",
            snapshot_export=export,
            dryrun_result=dryrun,
            approval_result=approval,
        )

    commit_validation = validate_snapshot_commit_plan(approval)
    validation = _as_dict(commit_validation)
    if validation.get("status") == "INVALID":
        return _invalid_result(
            "COMMIT_PLAN_VALIDATION_INVALID",
            snapshot_export=export,
            dryrun_result=dryrun,
            approval_result=approval,
            commit_validation=validation,
        )

    dryrun_ready = dryrun.get("status") == "READY"
    approval_approved = approval.get("status") == "APPROVED"
    commit_valid = validation.get("status") == "VALID"

    if approval.get("status") == "BLOCKED" or validation.get("status") == "BLOCKED" or dryrun.get("status") == "BLOCKED":
        status = STATUS_BLOCKED
        completed = False
        summary = "SNAPSHOT_PIPELINE_BLOCKED"
    elif dryrun_ready and approval_approved and commit_valid:
        status = STATUS_READY
        completed = True
        summary = "SNAPSHOT_PIPELINE_READY"
    else:
        status = STATUS_INVALID
        completed = False
        summary = "SNAPSHOT_PIPELINE_INVALID"

    return {
        "status": status,
        "completed": completed,
        "summary": summary,
        "pipeline_steps": {
            "ExportPreview": _step(True),
            "WriterDryrun": _step(dryrun_ready),
            "ApprovalGate": _step(approval_approved),
            "CommitPlanValidation": _step(commit_valid),
        },
        "snapshot_export": deepcopy(export),
        "dryrun_result": deepcopy(dryrun),
        "approval_result": deepcopy(approval),
        "commit_validation": deepcopy(validation),
        "warnings": _unique_text(
            dryrun.get("warnings"),
            approval.get("warnings"),
            validation.get("warnings"),
        ),
        "issues": _unique_text(
            dryrun.get("issues"),
            approval.get("issues"),
            validation.get("issues"),
        ),
    }
