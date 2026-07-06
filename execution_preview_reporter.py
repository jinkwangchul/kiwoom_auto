# -*- coding: utf-8 -*-
"""Human-readable execution preview report builder.

This module only converts in-memory preview results into dict/text output for
CLI, logs, debug views, or future GUI screens.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _display(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _first_line(values: list[str]) -> str:
    return values[0] if values else "-"


def _as_diagnostics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _format_keys(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    return ",".join(str(item) for item in value)


def _diagnostic_lines(stage_diagnostics: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in stage_diagnostics:
        keys = item.get("preview_keys")
        key_label = "preview_keys"
        if keys is None:
            keys = item.get("output_keys")
            key_label = "output_keys"
        lines.append(
            f"{_display(item.get('stage'))}: "
            f"ok={_display(item.get('ok'))} "
            f"reason={_display(item.get('reason'))} "
            f"{key_label}={_format_keys(keys)}"
        )
    return lines


def _approval_lines(approval_result: dict[str, Any]) -> list[str]:
    blocked_reasons = _as_list(approval_result.get("blocked_reasons"))
    lines = [
        f"approved: {_display(approval_result.get('approved'))}",
        f"approval_stage: {_display(approval_result.get('approval_stage'))}",
        f"next_stage: {_display(approval_result.get('next_stage'))}",
        "blocked_reasons:",
    ]
    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("-")
    return lines


def _candidate_lines(candidate_result: dict[str, Any]) -> list[str]:
    blocked_reasons = _as_list(candidate_result.get("blocked_reasons"))
    lines = [
        f"candidate: {_display(candidate_result.get('candidate'))}",
        f"candidate_stage: {_display(candidate_result.get('candidate_stage'))}",
        f"candidate_id: {_display(candidate_result.get('candidate_id'))}",
        f"next_stage: {_display(candidate_result.get('next_stage'))}",
        "blocked_reasons:",
    ]
    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("-")
    return lines


def _queue_pending_lines(queue_pending_result: dict[str, Any]) -> list[str]:
    blocked_reasons = _as_list(queue_pending_result.get("blocked_reasons"))
    lines = [
        f"queue_pending: {_display(queue_pending_result.get('queue_pending'))}",
        f"queue_pending_stage: {_display(queue_pending_result.get('queue_pending_stage'))}",
        f"queue_pending_id: {_display(queue_pending_result.get('queue_pending_id'))}",
        f"next_stage: {_display(queue_pending_result.get('next_stage'))}",
        f"preview_only: {_display(queue_pending_result.get('preview_only'))}",
        f"no_write: {_display(queue_pending_result.get('no_write'))}",
        "blocked_reasons:",
    ]
    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("-")
    return lines


def _queue_writer_dry_run_lines(queue_write_preview_result: dict[str, Any]) -> list[str]:
    blocked_reasons = _as_list(queue_write_preview_result.get("blocked_reasons"))
    record_preview = _as_dict(queue_write_preview_result.get("order_queued_record_preview"))
    lines = [
        f"write_preview: {_display(queue_write_preview_result.get('write_preview'))}",
        f"write_stage: {_display(queue_write_preview_result.get('write_stage'))}",
        f"next_stage: {_display(queue_write_preview_result.get('next_stage'))}",
        f"preview_only: {_display(queue_write_preview_result.get('preview_only'))}",
        f"no_write: {_display(queue_write_preview_result.get('no_write'))}",
        f"record_preview_status: {_display(record_preview.get('status'))}",
        "blocked_reasons:",
    ]
    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("-")
    return lines


def _build_text(report: dict[str, Any]) -> str:
    blocked_reasons = report.get("blocked_reasons", [])
    warnings = report.get("warnings", [])
    guard_snapshot = _as_dict(report.get("guard_snapshot"))
    pipeline_status = _as_dict(report.get("pipeline_status"))
    stage_diagnostics = _as_diagnostics(report.get("stage_diagnostics"))
    top_blocked_reason = report.get("blocked_reason") or _first_line(blocked_reasons)

    lines = [
        "[Summary]",
        f"result: {'PREVIEW_INPUTS_RESOLVED' if report['ok'] else 'BLOCKED'}",
        f"blocked_stage: {_display(report.get('blocked_stage'))}",
        f"top_blocked_reason: {_display(top_blocked_reason)}",
        f"ready_for_execution_request_preview: {report['ready_for_execution_request']}",
        "",
        "[Order]",
        f"order_id: {_display(report.get('order_id'))}",
        f"execution_id_preview: {_display(report.get('execution_id'))}",
        f"request_hash_preview: {_display(report.get('request_hash'))}",
        "",
        "[Guard]",
        f"operator_confirmed: {_display(guard_snapshot.get('operator_confirmed'))}",
        f"real_trade_enabled: {_display(guard_snapshot.get('real_trade_enabled'))}",
        f"account_no: {_display(guard_snapshot.get('account_no'))}",
        "",
        "[Pipeline]",
        f"execution_preview: {_display(pipeline_status.get('execution_preview'))}",
        f"final_guard: {_display(pipeline_status.get('final_guard'))}",
        f"lock_preview: {_display(pipeline_status.get('lock_preview'))}",
        f"request_hash_preview: {_display(pipeline_status.get('request_hash_preview'))}",
        f"execution_request_preview: {_display(pipeline_status.get('execution_request_preview'))}",
    ]

    diagnostic_lines = _diagnostic_lines(stage_diagnostics)
    if diagnostic_lines:
        lines.append("stage_diagnostics:")
        lines.extend(f"- {line}" for line in diagnostic_lines)

    lines.extend(["", "[Approval]"])
    lines.extend(_approval_lines(_as_dict(report.get("approval_result"))))

    lines.extend(["", "[Candidate]"])
    lines.extend(_candidate_lines(_as_dict(report.get("candidate_result"))))

    lines.extend(["", "[Queue Pending]"])
    lines.extend(_queue_pending_lines(_as_dict(report.get("queue_pending_result"))))

    lines.extend(["", "[Queue Writer Dry-Run]"])
    lines.extend(_queue_writer_dry_run_lines(_as_dict(report.get("queue_write_preview_result"))))

    lines.extend(["", "[Blocked Reason]"])

    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "[Safety / No-Write]",
            "- preview only",
            "- no SendOrder call",
            "- no ORDER_QUEUED creation",
            "- no runtime file write",
            "- no order_queue.json mutation",
            "- no rules.json mutation",
            "- no status/execution_enabled/guard mutation",
        ]
    )

    lines.append("")
    lines.append("[Warnings]")
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")

    return "\n".join(lines)


def _stage_state(value: Any) -> str:
    item = _as_dict(value)
    if not item:
        return "-"
    if item.get("ok") is True:
        return "ok"
    if item.get("unresolved") is True:
        return "unresolved"
    if item.get("ok") is False:
        return "blocked"
    return "present"


def build_execution_preview_report(preview_result: Any) -> dict[str, Any]:
    """Build a compact human-readable report from preview_execution result."""
    result = _as_dict(preview_result)
    read_result = _as_dict(result.get("read_result"))
    preview_service_result = _as_dict(result.get("preview_result"))
    approval_result = _as_dict(preview_service_result.get("approval_result"))
    candidate_result = _as_dict(preview_service_result.get("candidate_result"))
    queue_pending_result = _as_dict(preview_service_result.get("queue_pending_result"))
    queue_write_preview_result = _as_dict(preview_service_result.get("queue_write_preview_result"))
    summary = _as_dict(preview_service_result.get("summary"))
    pipeline_result = _as_dict(preview_service_result.get("pipeline_result"))
    pipeline = _as_dict(pipeline_result.get("pipeline"))
    execution_request_preview = _as_dict(pipeline.get("execution_request_preview"))
    execution_request = _as_dict(execution_request_preview.get("execution_request"))
    read_order = _as_dict(read_result.get("order"))

    blocked_reasons = []
    blocked_reasons.extend(_as_list(read_result.get("blocked_reasons")))
    blocked_reasons.extend(_as_list(summary.get("blocked_reasons")))

    warnings = []
    warnings.extend(_as_list(summary.get("warnings")))

    read_error = read_result.get("error")
    if read_error:
        blocked_reasons.append(str(read_error))

    ok = bool(result.get("ok")) and bool(summary.get("ok", result.get("ok")))
    report = {
        "ok": ok,
        "order_id": summary.get("order_id") or read_order.get("id"),
        "blocked_stage": summary.get("blocked_stage"),
        "blocked_reason": summary.get("blocked_reason") or pipeline_result.get("blocked_reason"),
        "ready_for_execution_request": bool(summary.get("ready_for_execution_request")),
        "execution_id": summary.get("execution_id"),
        "request_hash": summary.get("request_hash"),
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "guard_snapshot": _as_dict(execution_request.get("guard_snapshot")),
        "pipeline_status": {
            "execution_preview": _stage_state(pipeline.get("execution_preview")),
            "final_guard": _stage_state(pipeline.get("final_guard")),
            "lock_preview": _stage_state(pipeline.get("lock_preview")),
            "request_hash_preview": _stage_state(pipeline.get("request_hash_preview")),
            "execution_request_preview": _stage_state(execution_request_preview),
        },
        "stage_diagnostics": _as_diagnostics(
            summary.get("stage_diagnostics") or pipeline_result.get("stage_diagnostics")
        ),
        "approval_result": approval_result,
        "candidate_result": candidate_result,
        "queue_pending_result": queue_pending_result,
        "queue_write_preview_result": queue_write_preview_result,
        "text": "",
    }
    report["text"] = _build_text(report)
    return report
