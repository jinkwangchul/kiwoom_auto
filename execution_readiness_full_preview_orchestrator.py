# -*- coding: utf-8 -*-
"""Full preview orchestrator for Execution Readiness.

This module runs the preview-only readiness flow:
Preview Report -> Candidate Inspector -> Readiness Summary -> Audit Record ->
Snapshot Pipeline. It never writes runtime files, enqueues orders, commits,
creates TXT files or directories, appends logs, calls SendOrder, or invokes
execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_candidate_inspector import inspect_execution_candidate
from execution_preview_report import build_execution_preview_report
from execution_readiness_audit_record import build_execution_readiness_audit_record
from execution_readiness_snapshot_pipeline_orchestrator import run_snapshot_pipeline_preview
from execution_readiness_summary import build_execution_readiness_summary


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


def _result(
    *,
    status: str,
    completed: bool,
    summary: str,
    preview_report: dict[str, Any] | None,
    inspection_result: dict[str, Any] | None,
    readiness_summary: dict[str, Any] | None,
    audit_record: dict[str, Any] | None,
    snapshot_pipeline: dict[str, Any] | None,
    issues: list[Any] | None = None,
) -> dict[str, Any]:
    report = preview_report or {}
    inspection = inspection_result or {}
    readiness = readiness_summary or {}
    audit = audit_record or {}
    snapshot = snapshot_pipeline or {}
    return {
        "status": status,
        "completed": completed,
        "summary": summary,
        "preview_steps": {
            "ExecutionPreviewReport": _step(report.get("ok") is True, available=bool(report)),
            "CandidateInspector": _step(inspection.get("status") == "READY", available=bool(inspection)),
            "ReadinessSummary": _step(readiness.get("overall_status") == "READY", available=bool(readiness)),
            "AuditRecord": _step(bool(audit), available=bool(audit)),
            "SnapshotPipeline": _step(snapshot.get("status") == "READY", available=bool(snapshot)),
        },
        "preview_report": deepcopy(report),
        "inspection_result": deepcopy(inspection),
        "readiness_summary": deepcopy(readiness),
        "audit_record": deepcopy(audit),
        "snapshot_pipeline": deepcopy(snapshot),
        "warnings": _unique_text(
            report.get("warnings"),
            inspection.get("warnings"),
            readiness.get("warnings"),
            audit.get("warnings"),
            snapshot.get("warnings"),
        ),
        "issues": _unique_text(
            issues or [],
            report.get("blocked_reasons"),
            inspection.get("issues"),
            readiness.get("issues"),
            audit.get("issues"),
            snapshot.get("issues"),
        ),
    }


def _with_runtime_catalog_extension(
    result: dict[str, Any],
    *,
    include_runtime_catalog_preview: bool,
    runtime_catalog_payload: Any,
) -> dict[str, Any]:
    if not include_runtime_catalog_preview:
        return result

    if isinstance(runtime_catalog_payload, dict):
        extended = deepcopy(result)
        extended["extensions"] = {
            "runtime_catalog_preview": deepcopy(runtime_catalog_payload),
        }
        return extended

    extended = deepcopy(result)
    warnings = extended.setdefault("warnings", [])
    warning = "RUNTIME_CATALOG_PREVIEW_MISSING"
    if warning not in warnings:
        warnings.append(warning)
    return extended


def run_execution_readiness_preview(
    gate_result: Any,
    order_candidate: Any,
    queue_preview_result: Any,
    *,
    include_runtime_catalog_preview: bool = False,
    runtime_catalog_payload: Any = None,
) -> dict[str, Any]:
    """Run the full Execution Readiness preview flow without side effects."""
    try:
        preview_report = build_execution_preview_report(
            deepcopy(gate_result),
            deepcopy(order_candidate),
            deepcopy(queue_preview_result),
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=None,
                inspection_result=None,
                readiness_summary=None,
                audit_record=None,
                snapshot_pipeline=None,
                issues=[f"PREVIEW_REPORT_FAILED: {exc}"],
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    report = _as_dict(preview_report)
    if report.get("ok") is not True:
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=report,
                inspection_result=None,
                readiness_summary=None,
                audit_record=None,
                snapshot_pipeline=None,
                issues=["PREVIEW_REPORT_FAILED"],
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    inspection = inspect_execution_candidate(
        deepcopy(gate_result),
        deepcopy(order_candidate),
        deepcopy(queue_preview_result),
    )
    inspection_status = inspection.get("status")
    if inspection_status == "INVALID":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=None,
                audit_record=None,
                snapshot_pipeline=None,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )
    if inspection_status == "BLOCKED":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_BLOCKED,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_BLOCKED",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=None,
                audit_record=None,
                snapshot_pipeline=None,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    readiness = build_execution_readiness_summary(
        deepcopy(gate_result),
        deepcopy(order_candidate),
        deepcopy(queue_preview_result),
        report,
        inspection,
    )
    readiness_status = readiness.get("overall_status")
    if readiness_status == "INVALID":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=None,
                snapshot_pipeline=None,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )
    if readiness_status == "BLOCKED":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_BLOCKED,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_BLOCKED",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=None,
                snapshot_pipeline=None,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    try:
        audit = build_execution_readiness_audit_record(readiness, report, inspection)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=None,
                snapshot_pipeline=None,
                issues=[f"AUDIT_RECORD_FAILED: {exc}"],
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    if not _as_dict(audit):
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_INVALID,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=None,
                snapshot_pipeline=None,
                issues=["AUDIT_RECORD_FAILED"],
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    snapshot = run_snapshot_pipeline_preview(audit)
    snapshot_status = snapshot.get("status")
    if snapshot_status == "READY" and readiness_status == "READY":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_READY,
                completed=True,
                summary="EXECUTION_READINESS_PREVIEW_READY",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=audit,
                snapshot_pipeline=snapshot,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )
    if snapshot_status == "BLOCKED":
        return _with_runtime_catalog_extension(
            _result(
                status=STATUS_BLOCKED,
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_BLOCKED",
                preview_report=report,
                inspection_result=inspection,
                readiness_summary=readiness,
                audit_record=audit,
                snapshot_pipeline=snapshot,
            ),
            include_runtime_catalog_preview=include_runtime_catalog_preview,
            runtime_catalog_payload=runtime_catalog_payload,
        )

    return _with_runtime_catalog_extension(
        _result(
            status=STATUS_INVALID,
            completed=False,
            summary="EXECUTION_READINESS_PREVIEW_INVALID",
            preview_report=report,
            inspection_result=inspection,
            readiness_summary=readiness,
            audit_record=audit,
            snapshot_pipeline=snapshot,
        ),
        include_runtime_catalog_preview=include_runtime_catalog_preview,
        runtime_catalog_payload=runtime_catalog_payload,
    )
