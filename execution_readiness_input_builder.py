# -*- coding: utf-8 -*-
"""Input builder for Execution Readiness preview.

This layer prepares gate_result, order_candidate, and queue_preview_result for
the controller facade. It is intentionally GUI-independent and preview-only:
no Qt imports, no widget access, no runtime writes, no queue enqueue, no
execution controller calls, no SendOrder calls, and no file output.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from signal_gate_execution_queue_bridge import build_signal_gate_execution_queue_bridge


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
SUMMARY_READY = "INPUTS_READY"
SUMMARY_BLOCKED = "INPUTS_BLOCKED"
SUMMARY_INVALID = "INPUTS_INVALID"
SUMMARY_LEGACY_READY = "INPUTS_READY_FROM_LEGACY_PREVIEW"
SUMMARY_LEGACY_BLOCKED = "INPUTS_BLOCKED_FROM_LEGACY_PREVIEW"
SUMMARY_LEGACY_INVALID = "INPUTS_INVALID_FROM_LEGACY_PREVIEW"
BUILDER_VERSION = 1
LEGACY_WARNINGS = [
    "Preview mode",
    "Legacy preview adapter",
    "Runtime write disabled",
    "Execution disabled",
    "SendOrder disabled",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_dict(context: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = context.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _metadata(
    *,
    context: dict[str, Any],
    order_id: str,
    input_type: str,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "source": _clean_text(source) or _clean_text(context.get("source")) or "execution_readiness_input_builder",
        "preview_mode": True,
        "builder_version": BUILDER_VERSION,
        "project_phase": "EXECUTION_READINESS_PREVIEW",
        "input_type": input_type,
        "order_id": order_id or None,
    }
    if extra:
        metadata.update(deepcopy(extra))
    return metadata


def _result(
    *,
    status: str,
    summary: str,
    metadata: dict[str, Any],
    gate_result: Any = None,
    order_candidate: Any = None,
    queue_preview_result: Any = None,
    warnings: list[Any] | None = None,
    issues: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "gate_result": deepcopy(gate_result) if isinstance(gate_result, dict) else None,
        "order_candidate": deepcopy(order_candidate) if isinstance(order_candidate, dict) else None,
        "queue_preview_result": deepcopy(queue_preview_result) if isinstance(queue_preview_result, dict) else None,
        "metadata": deepcopy(metadata),
        "warnings": [str(item) for item in (warnings or [])],
        "issues": [str(item) for item in (issues or [])],
    }


def _order_id_matches(order_id: str, order: dict[str, Any]) -> bool:
    if not order_id:
        return True
    candidate_id = _clean_text(order.get("id") or order.get("order_id"))
    return candidate_id == order_id


def _norm(value: Any) -> str:
    return _clean_text(value).upper().replace("-", "_").replace(" ", "_")


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _legacy_path_requested(context: dict[str, Any]) -> bool:
    return (
        "legacy_execution_preview_result" in context
        or _clean_text(context.get("source")) == "gui_execution_preview_button"
    )


def _legacy_summary(legacy: dict[str, Any], preview: dict[str, Any]) -> str:
    summary = _as_dict(preview.get("summary"))
    return (
        _clean_text(summary.get("summary"))
        or _clean_text(summary.get("decision"))
        or _clean_text(summary.get("blocked_stage"))
        or _clean_text(legacy.get("stage"))
        or "LEGACY_EXECUTION_PREVIEW"
    )


def _legacy_blocked_reason(
    legacy: dict[str, Any],
    read_result: dict[str, Any],
    preview: dict[str, Any],
    queue_preview: dict[str, Any],
) -> str:
    summary = _as_dict(preview.get("summary"))
    values: list[Any] = []
    values.extend(_as_list(read_result.get("blocked_reasons")))
    values.extend(_as_list(summary.get("blocked_reasons")))
    values.extend(_as_list(queue_preview.get("blocked_reasons")))
    values.append(summary.get("blocked_reason"))
    values.append(legacy.get("blocked_reason"))
    values.append(legacy.get("error"))
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return "LEGACY_RESULT_NOT_READY"


def _legacy_order(legacy: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    read_result = _as_dict(legacy.get("read_result"))
    order = _as_dict(read_result.get("order"))
    if order:
        return order

    for key in ("order_candidate", "order", "real_ready_order"):
        order = _as_dict(legacy.get(key))
        if order:
            return order

    return _as_dict(preview.get("order_candidate"))


def _intent(order: dict[str, Any]) -> dict[str, Any]:
    intent = order.get("order_intent")
    return intent if isinstance(intent, dict) else {}


def _legacy_order_value(order: dict[str, Any], *keys: str) -> Any:
    intent = _intent(order)
    for key in keys:
        if order.get(key) not in (None, ""):
            return order.get(key)
        if intent.get(key) not in (None, ""):
            return intent.get(key)
    return None


def _legacy_signal(order: dict[str, Any]) -> str:
    return _norm(_legacy_order_value(order, "signal", "side", "order_type"))


def _legacy_hoga(order: dict[str, Any]) -> str:
    return _clean_text(_legacy_order_value(order, "hoga", "order_hoga", "order_method"))


def _legacy_qty(order: dict[str, Any]) -> Any:
    return _legacy_order_value(order, "quantity", "qty")


def _legacy_candidate_issues(order: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if order.get("price") in (None, ""):
        issues.append("MISSING_ORDER_PRICE")
    qty = _legacy_qty(order)
    try:
        if qty in (None, "") or float(qty) <= 0:
            issues.append("MISSING_ORDER_QTY")
    except (TypeError, ValueError):
        issues.append("MISSING_ORDER_QTY")
    if _legacy_signal(order) not in {"BUY", "SELL"}:
        issues.append("INVALID_ORDER_TYPE")
    if not _legacy_hoga(order):
        issues.append("INVALID_HOGA")
    return issues


def _legacy_queue_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(preview.get("queue_write_preview_result"))


def _legacy_gate(*, opened: bool, signal: str, reason: str) -> dict[str, Any]:
    gate_value = "OPEN" if opened else "BLOCKED"
    return {
        "ok": opened,
        "stage": "SIGNAL_QUEUE_GATE",
        "gate": gate_value,
        "status": gate_value,
        "result": gate_value,
        "gate_result": gate_value,
        "gate_reason": reason,
        "reason": reason,
        "source": "legacy_execution_preview_result",
        "candidate_result": "READY" if opened else "BLOCKED",
        "signal": signal or None,
        "decision": "ACCEPT" if opened else "REJECT",
        "policy_result": "PASS" if opened else "REJECT",
        "blocked_reasons": [] if opened else [reason],
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


def _legacy_order_candidate(order: dict[str, Any], *, signal: str) -> dict[str, Any]:
    candidate = deepcopy(order)
    candidate["id"] = candidate.get("id") or candidate.get("order_id")
    candidate["order_id"] = candidate.get("order_id") or candidate.get("id")
    candidate["status"] = _clean_text(candidate.get("status") or candidate.get("candidate_state") or candidate.get("state"))
    candidate["signal"] = signal or candidate.get("signal")
    candidate["quantity"] = candidate.get("quantity", candidate.get("qty"))
    candidate["qty"] = candidate.get("qty", candidate.get("quantity"))
    candidate["order_type"] = candidate.get("order_type") or signal or candidate.get("side")
    candidate["hoga"] = candidate.get("hoga") or _legacy_hoga(candidate)
    intent = _intent(candidate)
    if not intent:
        candidate["order_intent"] = {
            "side": signal or candidate.get("side"),
            "hoga": candidate.get("hoga"),
        }
    return candidate


def _legacy_queue_preview_result(
    *,
    opened: bool,
    gate: dict[str, Any],
    order: dict[str, Any],
    queue_write_preview: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    connected = opened and queue_write_preview.get("write_preview") is True
    return {
        "ok": connected,
        "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
        "bridge_result": "QUEUE_WRITER_PREVIEW_READY" if connected else "BLOCKED",
        "bridge_reason": "LEGACY_EXECUTION_PREVIEW_READY" if connected else reason,
        "gate_result": gate.get("gate_result"),
        "gate_stage": gate.get("stage"),
        "candidate_result": gate.get("candidate_result"),
        "signal": gate.get("signal"),
        "decision": gate.get("decision"),
        "policy_result": gate.get("policy_result"),
        "order_id": order.get("id") or order.get("order_id"),
        "order_status": order.get("status"),
        "preview_connected": connected,
        "queue_writer_preview_connected": connected,
        "queue_write_preview_result": deepcopy(queue_write_preview) if queue_write_preview else {
            "write_preview": False,
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [reason],
        },
        "source": "legacy_execution_preview_result",
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


def _build_from_legacy_preview(
    *,
    requested_order_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    legacy = _as_dict(context.get("legacy_execution_preview_result"))
    if not legacy:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_LEGACY_INVALID,
            metadata=_metadata(
                context=context,
                order_id=requested_order_id,
                input_type="legacy_execution_preview_result",
                source="legacy_execution_preview_result",
                extra={"legacy_status": None, "legacy_summary": None},
            ),
            warnings=LEGACY_WARNINGS,
            issues=["MISSING_LEGACY_PREVIEW_RESULT"],
        )

    read_result = _as_dict(legacy.get("read_result"))
    preview = _as_dict(legacy.get("preview_result"))
    legacy_status = "READY" if legacy.get("ok") is True else "BLOCKED"
    legacy_summary = _legacy_summary(legacy, preview)
    metadata = _metadata(
        context=context,
        order_id=requested_order_id,
        input_type="legacy_execution_preview_result",
        source="legacy_execution_preview_result",
        extra={"legacy_status": legacy_status, "legacy_summary": legacy_summary},
    )

    if not read_result and not preview:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_LEGACY_INVALID,
            metadata=metadata,
            warnings=LEGACY_WARNINGS,
            issues=["INVALID_LEGACY_RESULT"],
        )

    order = _legacy_order(legacy, preview)
    if not order:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_LEGACY_INVALID,
            metadata=metadata,
            warnings=LEGACY_WARNINGS,
            issues=["INVALID_LEGACY_RESULT"],
        )

    if not _order_id_matches(requested_order_id, order):
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_LEGACY_INVALID,
            metadata=metadata,
            order_candidate=order,
            warnings=LEGACY_WARNINGS,
            issues=["ORDER_ID_MISMATCH"],
        )

    signal = _legacy_signal(order)
    order_candidate = _legacy_order_candidate(order, signal=signal)
    candidate_issues = _legacy_candidate_issues(order_candidate)
    if candidate_issues:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_LEGACY_INVALID,
            metadata=metadata,
            order_candidate=order_candidate,
            warnings=LEGACY_WARNINGS,
            issues=candidate_issues,
        )

    order_status = _norm(order_candidate.get("status") or order_candidate.get("candidate_state") or order_candidate.get("state"))
    if order_status != "REAL_READY":
        reason = "LEGACY_RESULT_NOT_READY"
        gate = _legacy_gate(opened=False, signal=signal, reason=reason)
        queue_preview_result = _legacy_queue_preview_result(
            opened=False,
            gate=gate,
            order=order_candidate,
            queue_write_preview={},
            reason=reason,
        )
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_LEGACY_BLOCKED,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order_candidate,
            queue_preview_result=queue_preview_result,
            warnings=LEGACY_WARNINGS,
            issues=[reason],
        )

    queue_write_preview = _legacy_queue_preview(preview)
    queue_available = queue_write_preview.get("write_preview") is True
    preview_summary = _as_dict(preview.get("summary"))
    legacy_ready = legacy.get("ok") is True and preview_summary.get("ok", True) is not False
    if not legacy_ready:
        reason = _legacy_blocked_reason(legacy, read_result, preview, queue_write_preview)
        gate = _legacy_gate(opened=False, signal=signal, reason=reason)
        queue_preview_result = _legacy_queue_preview_result(
            opened=False,
            gate=gate,
            order=order_candidate,
            queue_write_preview=queue_write_preview,
            reason=reason,
        )
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_LEGACY_BLOCKED,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order_candidate,
            queue_preview_result=queue_preview_result,
            warnings=LEGACY_WARNINGS,
            issues=[reason],
        )

    if not queue_available:
        reason = "LEGACY_QUEUE_PREVIEW_UNAVAILABLE"
        gate = _legacy_gate(opened=False, signal=signal, reason=reason)
        queue_preview_result = _legacy_queue_preview_result(
            opened=False,
            gate=gate,
            order=order_candidate,
            queue_write_preview=queue_write_preview,
            reason=reason,
        )
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_LEGACY_BLOCKED,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order_candidate,
            queue_preview_result=queue_preview_result,
            warnings=LEGACY_WARNINGS,
            issues=[reason],
        )

    gate = _legacy_gate(opened=True, signal=signal, reason="LEGACY_EXECUTION_PREVIEW_READY")
    queue_preview_result = _legacy_queue_preview_result(
        opened=True,
        gate=gate,
        order=order_candidate,
        queue_write_preview=queue_write_preview,
        reason="LEGACY_EXECUTION_PREVIEW_READY",
    )
    return _result(
        status=STATUS_READY,
        summary=SUMMARY_LEGACY_READY,
        metadata=metadata,
        gate_result=gate,
        order_candidate=order_candidate,
        queue_preview_result=queue_preview_result,
        warnings=LEGACY_WARNINGS,
    )


def build_execution_readiness_inputs(
    *,
    order_id: Any = None,
    preview_context: Any = None,
) -> dict[str, Any]:
    """Build the three controller inputs from preview context only."""
    context = _as_dict(preview_context)
    requested_order_id = _clean_text(order_id or context.get("order_id"))

    if not requested_order_id:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_INVALID,
            metadata=_metadata(context=context, order_id=requested_order_id, input_type="missing_order_id"),
            issues=["MISSING_ORDER_ID"],
        )

    if not context:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_INVALID,
            metadata=_metadata(context=context, order_id=requested_order_id, input_type="missing_preview_context"),
            issues=["MISSING_PREVIEW_CONTEXT"],
        )

    if _legacy_path_requested(context):
        return _build_from_legacy_preview(
            requested_order_id=requested_order_id,
            context=context,
        )

    gate = _first_dict(context, "gate_result", "gate_preview", "gate")
    order = _first_dict(context, "order_candidate", "real_ready_order", "order")
    queue_preview = _first_dict(context, "queue_preview_result", "queue_preview", "bridge_result")
    metadata = _metadata(context=context, order_id=requested_order_id, input_type="preview_context")

    if not order:
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_INVALID,
            metadata=metadata,
            issues=["MISSING_ORDER_CANDIDATE"],
        )

    if not _order_id_matches(requested_order_id, order):
        return _result(
            status=STATUS_INVALID,
            summary=SUMMARY_INVALID,
            metadata=metadata,
            order_candidate=order,
            issues=["ORDER_ID_MISMATCH"],
        )

    if not gate:
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_BLOCKED,
            metadata=metadata,
            order_candidate=order,
            issues=["MISSING_GATE_RESULT"],
        )

    if queue_preview:
        return _result(
            status=STATUS_READY,
            summary=SUMMARY_READY,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order,
            queue_preview_result=queue_preview,
        )

    execution_preview_builder = context.get("execution_preview_builder")
    if not callable(execution_preview_builder):
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_BLOCKED,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order,
            issues=["MISSING_QUEUE_PREVIEW_RESULT"],
        )

    bridge_builder: Callable[..., dict[str, Any]] = build_signal_gate_execution_queue_bridge
    built_queue_preview = bridge_builder(
        deepcopy(gate),
        deepcopy(order),
        guard=deepcopy(context.get("guard")),
        existing_orders=deepcopy(context.get("existing_orders")),
        execution_preview_builder=execution_preview_builder,
    )
    if built_queue_preview.get("ok") is not True:
        reason = _clean_text(built_queue_preview.get("bridge_reason")) or "QUEUE_PREVIEW_BLOCKED"
        return _result(
            status=STATUS_BLOCKED,
            summary=SUMMARY_BLOCKED,
            metadata=metadata,
            gate_result=gate,
            order_candidate=order,
            queue_preview_result=built_queue_preview,
            issues=[reason],
        )

    return _result(
        status=STATUS_READY,
        summary=SUMMARY_READY,
        metadata=metadata,
        gate_result=gate,
        order_candidate=order,
        queue_preview_result=built_queue_preview,
    )
