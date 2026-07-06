# -*- coding: utf-8 -*-
"""Preview-only Execution Readiness audit record builder.

This module only creates an in-memory record object for future audit logging.
It never writes runtime files, creates audit log files, appends logs, enqueues
orders, calls SendOrder, or invokes execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable


RECORD_VERSION = 1
RECORD_TYPE = "EXECUTION_READINESS_PREVIEW"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _created_at(
    now: datetime | str | None,
    datetime_provider: Callable[[], datetime | str] | None,
) -> str:
    value: datetime | str
    if now is not None:
        value = now
    elif datetime_provider is not None:
        value = datetime_provider()
    else:
        value = datetime.now()

    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return _clean_text(value)


def _metadata(
    readiness_summary: dict[str, Any],
    preview_report: dict[str, Any],
    inspection_result: dict[str, Any],
) -> dict[str, Any]:
    summary_gate = _as_dict(readiness_summary.get("gate_result"))
    summary_order = _as_dict(readiness_summary.get("order_candidate"))
    summary_queue = _as_dict(readiness_summary.get("queue_preview_result"))

    return {
        "gate_result": (
            _clean_text(summary_gate.get("gate_result"))
            or _clean_text(preview_report.get("gate"))
            or _clean_text(inspection_result.get("gate"))
            or None
        ),
        "candidate_state": (
            _clean_text(summary_order.get("status"))
            or _clean_text(preview_report.get("candidate"))
            or _clean_text(inspection_result.get("candidate_status"))
            or None
        ),
        "preview_connected": (
            readiness_summary.get("checks", {}).get("PreviewQueue") == "PASS"
            if isinstance(readiness_summary.get("checks"), dict)
            else preview_report.get("preview_connected") is True
        ),
        "project_phase": "execution_readiness_preview",
        "test_mode": True,
        "source": "execution_readiness_summary",
        "queue_preview_stage": summary_queue.get("stage"),
        "preview_report_stage": preview_report.get("stage"),
        "inspection_stage": inspection_result.get("stage"),
    }


def build_execution_readiness_audit_record(
    readiness_summary: Any,
    preview_report: Any,
    inspection_result: Any,
    *,
    now: datetime | str | None = None,
    datetime_provider: Callable[[], datetime | str] | None = None,
) -> dict[str, Any]:
    """Build an immutable-style in-memory readiness audit record."""
    summary = _as_dict(readiness_summary)
    report = _as_dict(preview_report)
    inspection = _as_dict(inspection_result)

    return {
        "record_version": RECORD_VERSION,
        "created_at": _created_at(now, datetime_provider),
        "record_type": RECORD_TYPE,
        "decision": deepcopy(summary.get("decision")),
        "overall_status": deepcopy(summary.get("overall_status")),
        "ready": summary.get("ready") is True,
        "score": deepcopy(summary.get("score")),
        "summary": deepcopy(summary.get("summary")),
        "checks": deepcopy(_as_dict(summary.get("checks"))),
        "warnings": _as_list(summary.get("warnings")),
        "issues": _as_list(summary.get("issues")),
        "preview_mode": True,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "metadata": _metadata(summary, report, inspection),
    }
