# -*- coding: utf-8 -*-
"""Preview-only execution readiness summary.

This module aggregates the Signal Gate Bridge, Execution Preview Report, and
Execution Candidate Inspector outputs into one operator-facing readiness view.
It never writes runtime files, enqueues orders, calls SendOrder, or invokes
execution controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE = "EXECUTION_READINESS_SUMMARY"
STATUS_READY = "READY"
STATUS_PARTIAL = "PARTIAL"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _clean_text(value).upper()


def _order_status(order_candidate: dict[str, Any], queue_preview: dict[str, Any], inspection: dict[str, Any]) -> str:
    return _norm(
        order_candidate.get("status")
        or order_candidate.get("order_status")
        or inspection.get("candidate_status")
        or queue_preview.get("order_status")
    )


def _queue_write_preview(queue_preview: dict[str, Any]) -> dict[str, Any]:
    nested = _as_dict(queue_preview.get("queue_write_preview_result"))
    return nested if nested else queue_preview


def _preview_connected(queue_preview: dict[str, Any], report: dict[str, Any], inspection: dict[str, Any]) -> bool:
    if queue_preview.get("queue_writer_preview_connected") is True:
        return True
    if report.get("preview_connected") is True:
        return True
    return _queue_write_preview(queue_preview).get("write_preview") is True


def _runtime_write(queue_preview: dict[str, Any], report: dict[str, Any], inspection: dict[str, Any]) -> bool:
    if queue_preview.get("runtime_write") is True:
        return True
    if report.get("runtime_write") is True:
        return True
    if inspection.get("runtime_write") is True:
        return True
    return _queue_write_preview(queue_preview).get("no_write") is False


def _connected(queue_preview: dict[str, Any], report: dict[str, Any], inspection: dict[str, Any], name: str) -> bool:
    return (
        queue_preview.get(name) is True
        or report.get(name) is True
        or inspection.get(name) is True
    )


def _check(value: bool, *, available: bool = True) -> str:
    if not available:
        return "SKIP"
    return "PASS" if value else "FAIL"


def _unique_text(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _warnings(
    inspection: dict[str, Any],
    *,
    runtime_write: bool,
    execution_connected: bool,
    send_order_connected: bool,
) -> list[str]:
    warnings = [str(item) for item in _as_list(inspection.get("warnings"))]
    if "Preview mode" not in warnings:
        warnings.append("Preview mode")
    if not runtime_write and "Runtime write disabled" not in warnings:
        warnings.append("Runtime write disabled")
    if not execution_connected and "Execution disabled" not in warnings:
        warnings.append("Execution disabled")
    if not send_order_connected and "SendOrder disabled" not in warnings:
        warnings.append("SendOrder disabled")
    return _unique_text(warnings)


def build_execution_readiness_summary(
    gate_result: Any,
    order_candidate: Any,
    queue_preview_result: Any,
    preview_report: Any,
    inspection_result: Any,
) -> dict[str, Any]:
    """Build a preview-only readiness summary from prior preview layers."""
    gate = _as_dict(gate_result)
    order = _as_dict(order_candidate)
    queue_preview = _as_dict(queue_preview_result)
    report = _as_dict(preview_report)
    inspection = _as_dict(inspection_result)

    gate_open = _clean_text(gate.get("gate_result") or queue_preview.get("gate_result")) == "OPEN"
    real_ready = _order_status(order, queue_preview, inspection) == "REAL_READY"
    preview_connected = _preview_connected(queue_preview, report, inspection)
    preview_report_ok = report.get("ok") is True
    inspector_ready = inspection.get("status") == "READY"
    runtime_write = _runtime_write(queue_preview, report, inspection)
    execution_connected = _connected(queue_preview, report, inspection, "execution_connected")
    send_order_connected = _connected(queue_preview, report, inspection, "send_order_connected")

    checks = {
        "Gate": _check(gate_open),
        "PreviewQueue": _check(preview_connected),
        "PreviewReport": _check(preview_report_ok, available=bool(report)),
        "CandidateInspector": _check(inspector_ready, available=bool(inspection)),
        "RuntimeWriteDisabled": _check(not runtime_write),
        "ExecutionDisabled": _check(not execution_connected),
        "SendOrderDisabled": _check(not send_order_connected),
    }

    score = 0
    score += 20 if gate_open else 0
    score += 20 if real_ready else 0
    score += 20 if preview_connected else 0
    score += 20 if preview_report_ok else 0
    score += 20 if inspector_ready else 0

    issues = [str(item) for item in _as_list(inspection.get("issues"))]
    inspection_status = _clean_text(inspection.get("status"))

    if inspection_status == "INVALID":
        overall_status = STATUS_INVALID
        decision = "INVALID"
        ready = False
    elif not gate_open:
        overall_status = STATUS_BLOCKED
        decision = "BLOCKED"
        ready = False
        if not issues:
            gate_issues = [str(item) for item in _as_list(gate.get("blocked_reasons"))]
            issues = gate_issues or [_clean_text(gate.get("gate_result")) or "BLOCKED"]
    elif (
        gate_open
        and real_ready
        and preview_connected
        and preview_report_ok
        and inspector_ready
        and not runtime_write
        and not execution_connected
        and not send_order_connected
    ):
        overall_status = STATUS_READY
        decision = "READY_FOR_EXECUTION_PREVIEW"
        ready = True
    else:
        overall_status = STATUS_PARTIAL
        decision = "NOT_READY"
        ready = False

    return {
        "ok": True,
        "stage": STAGE,
        "overall_status": overall_status,
        "ready": ready,
        "score": score,
        "decision": decision,
        "summary": decision,
        "checks": checks,
        "warnings": _warnings(
            inspection,
            runtime_write=runtime_write,
            execution_connected=execution_connected,
            send_order_connected=send_order_connected,
        ),
        "issues": issues,
        "gate_result": deepcopy(gate),
        "order_candidate": deepcopy(order),
        "queue_preview_result": deepcopy(queue_preview),
        "preview_report": deepcopy(report),
        "inspection_result": deepcopy(inspection),
    }
