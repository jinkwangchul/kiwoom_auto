# -*- coding: utf-8 -*-
"""Text formatter for Execution Readiness full preview results.

This module only converts an in-memory preview result into a reusable ASCII
text payload. It never prints, logs, writes files, enqueues orders, commits, or
calls execution/send-order components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
LINE = "================================================"
SECTION = "------------------------------------------------"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _status(value: Any) -> str:
    text = _clean_text(value).upper()
    if text in {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID}:
        return text
    return STATUS_INVALID


def _bool_text(value: Any) -> str:
    return "True" if value is True else "False"


def _lines_for_values(values: list[Any]) -> list[str]:
    if not values:
        return ["None"]
    return [str(value) for value in values]


def _append_key_values(lines: list[str], values: dict[str, Any]) -> None:
    if not values:
        lines.append("None")
        return
    for key, value in values.items():
        lines.append(str(key))
        lines.append(str(value))


def _pipeline_section(preview: dict[str, Any]) -> list[str]:
    steps = _as_dict(preview.get("preview_steps"))
    labels = (
        ("Execution Preview Report", "ExecutionPreviewReport"),
        ("Candidate Inspector", "CandidateInspector"),
        ("Readiness Summary", "ReadinessSummary"),
        ("Audit Record", "AuditRecord"),
        ("Snapshot Pipeline", "SnapshotPipeline"),
    )
    lines = ["Pipeline"]
    for label, key in labels:
        lines.append(label)
        lines.append(str(steps.get(key, "SKIP")))
    return lines


def _checks_section(preview: dict[str, Any]) -> list[str]:
    readiness = _as_dict(preview.get("readiness_summary"))
    readiness_checks = _as_dict(readiness.get("checks"))
    preview_steps = _as_dict(preview.get("preview_steps"))
    snapshot = _as_dict(preview.get("snapshot_pipeline"))

    checks = {
        "Gate": readiness_checks.get("Gate", "SKIP"),
        "Candidate": readiness_checks.get("CandidateInspector", preview_steps.get("CandidateInspector", "SKIP")),
        "Preview Queue": readiness_checks.get("PreviewQueue", "SKIP"),
        "Preview Report": readiness_checks.get("PreviewReport", preview_steps.get("ExecutionPreviewReport", "SKIP")),
        "Inspector": preview_steps.get("CandidateInspector", "SKIP"),
        "Summary": preview_steps.get("ReadinessSummary", "SKIP"),
        "Snapshot": preview_steps.get("SnapshotPipeline", snapshot.get("status", "SKIP")),
    }

    lines = ["Checks"]
    _append_key_values(lines, checks)
    return lines


def _header_section(preview: dict[str, Any], status: str, summary: str) -> list[str]:
    return [
        LINE,
        "Execution Readiness Preview",
        LINE,
        "Overall Status",
        status,
        "Completed",
        _bool_text(preview.get("completed")),
        "Summary",
        summary or "None",
    ]


def _footer_section(preview: dict[str, Any]) -> list[str]:
    readiness = _as_dict(preview.get("readiness_summary"))
    result_text = _clean_text(readiness.get("decision")) or _clean_text(preview.get("summary")) or "None"
    return [
        "Result",
        result_text,
        LINE,
        "End of Preview",
        LINE,
    ]


def _join_sections(sections: dict[str, str]) -> str:
    ordered = ["Header", "Pipeline", "Warnings", "Issues", "Checks", "Footer"]
    parts: list[str] = []
    for index, name in enumerate(ordered):
        if index:
            parts.append(SECTION)
        value = sections.get(name, "")
        if value:
            parts.extend(value.splitlines())
    return "\n".join(parts)


def format_execution_readiness_preview(preview_result: Any) -> dict[str, Any]:
    """Format a full preview result as reusable fixed-width ASCII text."""
    preview = _as_dict(preview_result)
    status = _status(preview.get("status"))
    summary = _clean_text(preview.get("summary"))

    sections = {
        "Header": "\n".join(_header_section(preview, status, summary)),
        "Pipeline": "\n".join(_pipeline_section(preview)),
        "Warnings": "\n".join(["Warnings", *_lines_for_values(_as_list(preview.get("warnings")))]),
        "Issues": "\n".join(["Issues", *_lines_for_values(_as_list(preview.get("issues")))]),
        "Checks": "\n".join(_checks_section(preview)),
        "Footer": "\n".join(_footer_section(preview)),
    }
    text = _join_sections(sections)

    return {
        "status": status,
        "summary": summary,
        "text": text,
        "sections": sections,
        "line_count": len(text.splitlines()),
    }
