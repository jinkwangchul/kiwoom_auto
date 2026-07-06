# -*- coding: utf-8 -*-
"""Preview-only writer dry-run validation for readiness snapshot exports.

This module only validates whether a snapshot export preview has enough data
for a future writer. It never creates files or directories, opens files, writes
text, enqueues orders, appends logs, calls SendOrder, or invokes execution
controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


EXPECTED_EXPORT_TYPE = "EXECUTION_READINESS_PREVIEW"
STATUS_READY = "READY"
STATUS_INVALID = "INVALID"
STATUS_BLOCKED = "BLOCKED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _check(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _target_path(export_path: str, export_filename: str) -> str:
    if not export_path:
        return export_filename
    if export_path.endswith(("/", "\\")):
        return f"{export_path}{export_filename}"
    return f"{export_path}/{export_filename}"


def _estimated_size(content: str) -> int:
    return len(content)


def validate_snapshot_write_dryrun(snapshot_export: Any) -> dict[str, Any]:
    """Validate snapshot export write readiness without writing anything."""
    export = _as_dict(snapshot_export)
    export_filename = _clean_text(export.get("export_filename"))
    export_path = _clean_text(export.get("export_path"))
    content = _clean_text(export.get("content"))
    preview_mode = export.get("preview_mode") is True
    export_type = _clean_text(export.get("export_type"))
    content_type = _clean_text(export.get("content_type"))

    checks = {
        "Filename": _check(bool(export_filename)),
        "ExportPath": _check(bool(export_path)),
        "Content": _check(bool(content)),
        "PreviewMode": _check(preview_mode),
        "ExportType": _check(export_type == EXPECTED_EXPORT_TYPE),
    }

    invalid_issues: list[str] = []
    if not export_filename:
        invalid_issues.append("MISSING_FILENAME")
    if not export_path:
        invalid_issues.append("MISSING_EXPORT_PATH")
    if not content:
        invalid_issues.append("EMPTY_CONTENT")

    blocked_issues: list[str] = []
    if not preview_mode:
        blocked_issues.append("PREVIEW_DISABLED")
    if export_type != EXPECTED_EXPORT_TYPE:
        blocked_issues.append("INVALID_EXPORT_TYPE")

    if blocked_issues:
        status = STATUS_BLOCKED
        issues = blocked_issues
        can_write = False
        validated = False
        summary = "SNAPSHOT_WRITE_BLOCKED"
    elif invalid_issues:
        status = STATUS_INVALID
        issues = invalid_issues
        can_write = False
        validated = False
        summary = "SNAPSHOT_WRITE_INVALID"
    else:
        status = STATUS_READY
        issues = []
        can_write = True
        validated = True
        summary = "SNAPSHOT_WRITE_DRYRUN_READY"

    return {
        "status": status,
        "can_write": can_write,
        "validated": validated,
        "summary": summary,
        "checks": checks,
        "warnings": [
            "Preview only",
            "Runtime write disabled",
            "Audit write disabled",
            "File creation disabled",
        ],
        "issues": issues,
        "write_plan": {
            "target_path": _target_path(export_path, export_filename),
            "target_filename": export_filename,
            "estimated_size": _estimated_size(content),
            "content_type": content_type,
            "preview_only": True,
        },
        "snapshot_export": deepcopy(export),
    }
