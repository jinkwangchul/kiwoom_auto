# -*- coding: utf-8 -*-
"""Preview-only Execution Readiness snapshot export builder.

This module only creates an in-memory export preview object. It never creates
files or directories, writes runtime files, appends logs, enqueues orders,
calls SendOrder, or invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable


EXPORT_VERSION = 1
EXPORT_TYPE = "EXECUTION_READINESS_PREVIEW"
CONTENT_TYPE = "text/plain"
EXPORT_PATH = "audit/preview/"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _generated_at(
    audit_record: dict[str, Any],
    now: datetime | str | None,
    datetime_provider: Callable[[], datetime | str] | None,
) -> str:
    value: datetime | str | None = now
    if value is None and datetime_provider is not None:
        value = datetime_provider()
    if value is None:
        value = audit_record.get("created_at")
    if value is None:
        value = datetime.now()

    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return _clean_text(value)


def _filename_timestamp(generated_at: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(generated_at, fmt)
            return parsed.strftime("%Y%m%d_%H%M%S")
        except ValueError:
            continue

    digits = "".join(ch for ch in generated_at if ch.isdigit())
    if len(digits) >= 14:
        return f"{digits[:8]}_{digits[8:14]}"
    return "unknown_time"


def _format_list(title: str, values: list[Any]) -> list[str]:
    lines = [title, ""]
    if values:
        lines.extend(str(value) for value in values)
    else:
        lines.append("None")
    return lines


def _format_checks(checks: dict[str, Any]) -> list[str]:
    lines = ["Checks", ""]
    if not checks:
        lines.append("None")
        return lines
    for name, result in checks.items():
        lines.append(f"{name}: {result}")
    return lines


def _content(audit_record: dict[str, Any]) -> str:
    checks = _as_dict(audit_record.get("checks"))
    warnings = _as_list(audit_record.get("warnings"))
    issues = _as_list(audit_record.get("issues"))

    lines = [
        "Execution Readiness Snapshot",
        "",
        "--------------------------------",
        "",
        "Decision",
        "",
        _clean_text(audit_record.get("decision")) or "None",
        "",
        "Overall Status",
        "",
        _clean_text(audit_record.get("overall_status")) or "None",
        "",
        "Ready",
        "",
        str(audit_record.get("ready") is True),
        "",
        "Score",
        "",
        str(audit_record.get("score")),
        "",
    ]
    lines.extend(_format_checks(checks))
    lines.append("")
    lines.extend(_format_list("Warnings", warnings))
    lines.append("")
    lines.extend(_format_list("Issues", issues))
    lines.extend(["", "--------------------------------", "", "End of Preview"])
    return "\n".join(lines)


def build_execution_readiness_snapshot_export(
    audit_record: Any,
    *,
    now: datetime | str | None = None,
    datetime_provider: Callable[[], datetime | str] | None = None,
) -> dict[str, Any]:
    """Build an in-memory snapshot export preview for a readiness audit record."""
    record = _as_dict(audit_record)
    generated_at = _generated_at(record, now, datetime_provider)
    export_filename = f"execution_readiness_preview_{_filename_timestamp(generated_at)}.txt"

    return {
        "export_version": EXPORT_VERSION,
        "export_type": EXPORT_TYPE,
        "preview_mode": True,
        "generated_at": generated_at,
        "export_filename": export_filename,
        "export_path": EXPORT_PATH,
        "content_type": CONTENT_TYPE,
        "content": _content(record),
        "metadata": {
            "record_version": deepcopy(record.get("record_version")),
            "record_type": deepcopy(record.get("record_type")),
            "generated_source": "execution_readiness_audit_record",
            "project_phase": "execution_readiness_snapshot_export_preview",
            "preview_only": True,
            "test_mode": True,
        },
        "audit_record": deepcopy(record),
    }
