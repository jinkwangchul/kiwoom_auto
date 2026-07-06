# -*- coding: utf-8 -*-
"""Human-readable report for Signal Gate to Execution Queue preview bridge.

This module only formats already-built in-memory preview results. It never
writes runtime files, enqueues orders, calls SendOrder, or invokes execution
controllers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE = "EXECUTION_PREVIEW_REPORT"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _display(value: Any) -> str:
    if value is None or value == "":
        return "None"
    return str(value)


def _bool_text(value: bool) -> str:
    return "True" if value else "False"


def _order_status(order_candidate: dict[str, Any]) -> str:
    return _clean_text(
        order_candidate.get("status")
        or order_candidate.get("order_status")
        or order_candidate.get("candidate_result")
    )


def _queue_write_preview(queue_preview_result: dict[str, Any]) -> dict[str, Any]:
    nested = _as_dict(queue_preview_result.get("queue_write_preview_result"))
    if nested:
        return nested
    return queue_preview_result


def _preview_connected(queue_preview_result: dict[str, Any]) -> bool:
    if queue_preview_result.get("queue_writer_preview_connected") is True:
        return True
    queue_write_preview = _queue_write_preview(queue_preview_result)
    return queue_write_preview.get("write_preview") is True


def _runtime_write(queue_preview_result: dict[str, Any]) -> bool:
    if queue_preview_result.get("runtime_write") is True:
        return True
    queue_write_preview = _queue_write_preview(queue_preview_result)
    return queue_write_preview.get("no_write") is False


def _connected_flag(queue_preview_result: dict[str, Any], name: str) -> bool:
    return queue_preview_result.get(name) is True


def _blocked_reasons(
    gate_result: dict[str, Any],
    queue_preview_result: dict[str, Any],
    *,
    gate_value: str,
    order_status: str,
    eligible: bool,
) -> list[str]:
    if eligible:
        return []

    reasons = _as_list(gate_result.get("blocked_reasons"))
    if reasons:
        return [str(reason) for reason in reasons]

    if gate_value and gate_value != "OPEN":
        return [gate_value]

    if order_status != "REAL_READY":
        return ["order.status is not REAL_READY"]

    bridge_reason = _clean_text(queue_preview_result.get("bridge_reason"))
    if bridge_reason:
        return [bridge_reason]

    queue_write_preview = _queue_write_preview(queue_preview_result)
    queue_reasons = _as_list(queue_write_preview.get("blocked_reasons"))
    if queue_reasons:
        return [str(reason) for reason in queue_reasons]

    return ["PRECHECK_FAILED"]


def _reason(
    *,
    eligible: bool,
    gate_result: dict[str, Any],
    queue_preview_result: dict[str, Any],
    blocked_reasons: list[str],
) -> str:
    if eligible:
        return "READY_FOR_EXECUTION_PREVIEW"

    bridge_reason = _clean_text(queue_preview_result.get("bridge_reason"))
    if bridge_reason:
        return bridge_reason

    gate_reason = _clean_text(gate_result.get("gate_reason"))
    if gate_reason:
        return gate_reason

    return blocked_reasons[0] if blocked_reasons else "PRECHECK_FAILED"


def _build_text(report: dict[str, Any]) -> str:
    blocked_reasons = report["blocked_reasons"]
    lines = [
        "Execution Preview Report",
        "",
        "Decision",
        "---------",
        f"Gate: {_display(report.get('gate'))}",
        "",
        "Signal",
        "---------",
        _display(report.get("signal")),
        "",
        "Candidate",
        "---------",
        _display(report.get("candidate")),
        "",
        "Preview Queue",
        "---------",
        f"Eligible : {_bool_text(bool(report.get('eligible')))}",
        f"Preview Connected : {_bool_text(bool(report.get('preview_connected')))}",
        f"Runtime Write : {_bool_text(bool(report.get('runtime_write')))}",
        f"Execution Connected : {_bool_text(bool(report.get('execution_connected')))}",
        f"SendOrder Connected : {_bool_text(bool(report.get('send_order_connected')))}",
        "",
        "Reason",
        "---------",
        _display(report.get("reason")),
        "",
        "Blocked Reason",
        "---------",
    ]

    if blocked_reasons:
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    else:
        lines.append("None")

    lines.extend(["", "------------------------------------------------"])
    return "\n".join(lines)


def build_execution_preview_report(
    gate_result: Any,
    order_candidate: Any,
    queue_preview_result: Any,
) -> dict[str, Any]:
    """Build a read-only human preview report from existing preview results."""
    gate = _as_dict(gate_result)
    order = _as_dict(order_candidate)
    queue_preview = _as_dict(queue_preview_result)

    gate_value = _clean_text(gate.get("gate_result") or queue_preview.get("gate_result"))
    signal = gate.get("signal", queue_preview.get("signal"))
    order_status = _order_status(order) or _clean_text(queue_preview.get("order_status"))
    preview_connected = _preview_connected(queue_preview)
    runtime_write = _runtime_write(queue_preview)
    execution_connected = _connected_flag(queue_preview, "execution_connected")
    send_order_connected = _connected_flag(queue_preview, "send_order_connected")

    eligible = (
        gate_value == "OPEN"
        and order_status == "REAL_READY"
        and preview_connected
        and not runtime_write
        and not execution_connected
        and not send_order_connected
    )
    blocked_reasons = _blocked_reasons(
        gate,
        queue_preview,
        gate_value=gate_value,
        order_status=order_status,
        eligible=eligible,
    )
    reason = _reason(
        eligible=eligible,
        gate_result=gate,
        queue_preview_result=queue_preview,
        blocked_reasons=blocked_reasons,
    )

    report = {
        "ok": True,
        "stage": STAGE,
        "eligible": eligible,
        "gate": gate_value or None,
        "signal": deepcopy(signal),
        "candidate": order_status or None,
        "preview_connected": preview_connected,
        "runtime_write": runtime_write,
        "execution_connected": execution_connected,
        "send_order_connected": send_order_connected,
        "reason": reason,
        "blocked_reasons": blocked_reasons,
        "gate_result": deepcopy(gate),
        "order_candidate": deepcopy(order),
        "queue_preview_result": deepcopy(queue_preview),
    }
    report["text"] = _build_text(report)
    return report
