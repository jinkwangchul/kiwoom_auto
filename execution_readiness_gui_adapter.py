# -*- coding: utf-8 -*-
"""GUI-independent adapter for Execution Readiness preview text results.

This module only converts formatter output into a plain ViewModel dictionary.
It does not import Qt, touch widgets, print, log, write files, enqueue work, or
call execution/send-order components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
SECTION_NAMES = ("Header", "Pipeline", "Warnings", "Issues", "Checks", "Footer")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _status(value: Any) -> str:
    text = _clean_text(value).upper()
    if text in {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID}:
        return text
    return STATUS_INVALID


def _section_lines(sections: dict[str, Any], name: str) -> list[str]:
    section = sections.get(name)
    if section is None:
        return []
    return str(section).splitlines()


def _section_values(sections: dict[str, Any], name: str) -> list[str]:
    lines = _section_lines(sections, name)
    if lines and lines[0] == name:
        lines = lines[1:]
    return [line for line in lines if line and line != "None"]


def _section_pairs(sections: dict[str, Any], name: str) -> list[tuple[str, str]]:
    values = _section_values(sections, name)
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(values), 2):
        key = values[index]
        value = values[index + 1] if index + 1 < len(values) else ""
        pairs.append((key, value))
    return pairs


def _header_value(sections: dict[str, Any], label: str) -> str:
    lines = _section_lines(sections, "Header")
    for index, line in enumerate(lines):
        if line == label and index + 1 < len(lines):
            return lines[index + 1]
    return ""


def _pipeline_result(sections: dict[str, Any]) -> str:
    values = [value for _, value in _section_pairs(sections, "Pipeline")]
    if values and all(value == "PASS" for value in values):
        return "PASS"
    if any(value == "FAIL" for value in values):
        return "FAIL"
    return "SKIP"


def _badges(status: str) -> list[str]:
    badges = ["Preview", "Runtime Locked", "Execution Disabled", "SendOrder Disabled", "Commit Disabled"]
    if status == STATUS_READY:
        return ["Ready", *badges]
    if status == STATUS_BLOCKED:
        return ["Blocked", *badges]
    return ["Invalid", *badges]


def _table_rows(
    *,
    status: str,
    completed: str,
    summary: str,
    sections: dict[str, Any],
    warnings: list[str],
    issues: list[str],
) -> list[tuple[str, str]]:
    return [
        ("Overall Status", status),
        ("Completed", completed or "False"),
        ("Summary", summary or "None"),
        ("Pipeline", _pipeline_result(sections)),
        ("Warnings", str(len(warnings))),
        ("Issues", str(len(issues))),
    ]


def build_execution_readiness_view_model(preview_text_result: Any) -> dict[str, Any]:
    """Build a GUI-ready ViewModel without touching GUI components."""
    preview_text = _as_dict(preview_text_result)
    formatter_sections = _as_dict(preview_text.get("sections"))
    status = _status(preview_text.get("status"))
    summary = _clean_text(preview_text.get("summary"))
    sections = {name: deepcopy(formatter_sections.get(name, "")) for name in SECTION_NAMES}
    warnings = _section_values(sections, "Warnings")
    issues = _section_values(sections, "Issues")
    completed = _header_value(sections, "Completed")

    return {
        "status": status,
        "title": "Execution Readiness Preview",
        "subtitle": status,
        "summary": summary,
        "ready": status == STATUS_READY,
        "sections": sections,
        "badges": _badges(status),
        "warnings": warnings,
        "issues": issues,
        "table_rows": _table_rows(
            status=status,
            completed=completed,
            summary=summary,
            sections=sections,
            warnings=warnings,
            issues=issues,
        ),
        "footer": sections.get("Footer", ""),
    }
