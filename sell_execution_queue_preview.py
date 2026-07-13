"""Preview-only SELL Execution Queue adapter.

This module adapts SELL Signal Gate Preview output to the existing signal gate
to execution queue writer preview bridge. It validates queue preview contracts
without committing queue writes or selecting candidate priority.
"""

from __future__ import annotations

from copy import deepcopy
from numbers import Number
from typing import Any

from signal_gate_execution_queue_bridge import build_signal_gate_execution_queue_bridge


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"
IGNORE = "IGNORE"

PREVIEW_TYPE = "SELL_EXECUTION_QUEUE_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_SIGNAL_GATE_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Queue Preview"
ROUTINE_DEPENDENCY = None

BRIDGE_STAGE = "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE"
BRIDGE_READY = "QUEUE_WRITER_PREVIEW_READY"
BRIDGE_BLOCKED = "BLOCKED"
BRIDGE_IGNORE = "IGNORE"
QUEUE_WRITE_STAGE_READY = "order_queued_record_preview_created"
QUEUE_WRITE_NEXT_STAGE = "QUEUE_WRITE_REQUIRED"

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "send_order",
    "broker_api_called",
    "real_ready_state_changed",
    "order_request_created",
    "queue_writer_preview_called",
)

_REQUIRED_LIST_FIELDS = (
    "opened_gates",
    "blocked_gates",
    "ignored_gates",
    "candidate_gates",
)

_EXCLUDED_ACTION_SOURCES = {"PENDING", "CANCEL_PENDING_ORDER"}

_REQUIRED_QUEUE_RECORD_TEXT_FIELDS = (
    "id",
    "source_signal_id",
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "request_hash",
    "lock_id",
    "execution_id",
    "queue_contract_version",
)

_REQUIRED_EXECUTION_REQUEST_TEXT_FIELDS = (
    "execution_id",
    "request_hash",
    "lock_id",
)


def build_sell_execution_queue_preview(
    signal_gate_preview: dict[str, Any],
    *,
    guard_context: dict[str, Any] | None = None,
    existing_orders: Any = None,
) -> dict[str, Any]:
    """Build a preview-only queue adapter result for SELL opened gates."""
    result = _base_result(signal_gate_preview, guard_context, existing_orders)

    if not isinstance(signal_gate_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview must be a dict")
        return _finish(result)

    result["signal_gate_preview_snapshot"] = deepcopy(signal_gate_preview)

    if signal_gate_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview preview_type is invalid")
        return _finish(result)

    if signal_gate_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(signal_gate_preview):
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview safety flag violation")
        return _finish(result)

    for field in _REQUIRED_LIST_FIELDS:
        if not isinstance(signal_gate_preview.get(field), list):
            result["status"] = INVALID
            result["reasons"].append(f"{field} must be a list")
            return _finish(result)

    if not isinstance(signal_gate_preview.get("summary"), dict):
        result["status"] = INVALID
        result["reasons"].append("summary must be a dict")
        return _finish(result)

    if isinstance(signal_gate_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(signal_gate_preview["warnings"]))

    result["upstream_blocked_gates"] = deepcopy(signal_gate_preview.get("blocked_gates"))
    result["upstream_ignored_gates"] = deepcopy(signal_gate_preview.get("ignored_gates"))
    result["summary"]["blocked_count"] += len(result["upstream_blocked_gates"])
    result["summary"]["ignored_count"] += len(result["upstream_ignored_gates"])

    upstream_status = _clean_text(signal_gate_preview.get("status")).upper()
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("signal_gate_preview status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview status is invalid")
        return _finish(result)
    if signal_gate_preview.get("signal_gate_ready") is not True:
        result["status"] = INVALID
        result["reasons"].append("signal_gate_preview signal_gate_ready must be True when status is READY")
        return _finish(result)

    opened_gates = signal_gate_preview.get("opened_gates")
    result["summary"]["candidate_count"] = len(opened_gates)
    if not opened_gates:
        result["status"] = BLOCKED
        result["reasons"].append("no OPEN SELL signal gates")
        return _finish(result)

    has_invalid = False
    for index, gate_candidate in enumerate(opened_gates):
        queue_result = _build_candidate_queue_result(
            gate_candidate,
            index,
            guard_context=guard_context,
            existing_orders=existing_orders,
        )
        result["candidate_queue_results"].append(queue_result)
        status = queue_result.get("status")
        if status == READY:
            result["queue_ready_candidates"].append(deepcopy(queue_result))
            result["summary"]["queue_ready_count"] += 1
            if isinstance(queue_result.get("order_queued_record_preview"), dict):
                result["summary"]["order_queued_preview_count"] += 1
        elif status == INVALID:
            result["blocked_queue_candidates"].append(deepcopy(queue_result))
            result["summary"]["invalid_count"] += 1
            has_invalid = True
        elif status == IGNORE:
            result["ignored_queue_candidates"].append(deepcopy(queue_result))
            result["summary"]["ignored_count"] += 1
        else:
            result["blocked_queue_candidates"].append(deepcopy(queue_result))
            result["summary"]["blocked_count"] += 1
            if _is_duplicate_result(queue_result):
                result["summary"]["duplicate_blocked_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["queue_ready_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no READY SELL execution queue candidates")

    return _finish(result)


def _base_result(
    signal_gate_preview: Any,
    guard_context: dict[str, Any] | None,
    existing_orders: Any,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "queue_committed": False,
        "status": BLOCKED,
        "execution_queue_ready": False,
        "queue_ready_candidates": [],
        "blocked_queue_candidates": [],
        "ignored_queue_candidates": [],
        "candidate_queue_results": [],
        "upstream_blocked_gates": [],
        "upstream_ignored_gates": [],
        "signal_gate_preview_snapshot": deepcopy(signal_gate_preview)
        if isinstance(signal_gate_preview, dict)
        else {},
        "guard_context_snapshot": deepcopy(guard_context) if isinstance(guard_context, dict) else {},
        "existing_orders_snapshot": deepcopy(existing_orders) if isinstance(existing_orders, list) else existing_orders,
        "warnings": [],
        "reasons": [],
        "summary": {
            "candidate_count": 0,
            "queue_ready_count": 0,
            "blocked_count": 0,
            "ignored_count": 0,
            "invalid_count": 0,
            "order_queued_preview_count": 0,
            "duplicate_blocked_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "queue_committed": False,
        },
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["execution_queue_ready"] = result.get("status") == READY
    result["queue_committed"] = False
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    result["summary"]["queue_committed"] = False
    return result


def _build_candidate_queue_result(
    gate_candidate: Any,
    index: int,
    *,
    guard_context: dict[str, Any] | None,
    existing_orders: Any,
) -> dict[str, Any]:
    if not isinstance(gate_candidate, dict):
        return _candidate_result(index=index, status=INVALID, reasons=["gate candidate must be a dict"])

    warnings = deepcopy(gate_candidate.get("warnings")) if isinstance(gate_candidate.get("warnings"), list) else []
    reasons = deepcopy(gate_candidate.get("reasons")) if isinstance(gate_candidate.get("reasons"), list) else []
    action_source = _clean_text(gate_candidate.get("action_source")).upper() or "UNKNOWN"

    if action_source in _EXCLUDED_ACTION_SOURCES:
        reasons.append("PENDING cancel action requires a separate cancel execution path")
        return _candidate_result_from_gate(gate_candidate, index, BLOCKED, reasons, warnings)

    if _clean_text(gate_candidate.get("status")).upper() != READY:
        reasons.append("gate candidate status is not READY")
        return _candidate_result_from_gate(gate_candidate, index, BLOCKED, reasons, warnings)

    if _clean_text(gate_candidate.get("gate_result")).upper() != "OPEN":
        reasons.append("gate_result is not OPEN")
        return _candidate_result_from_gate(gate_candidate, index, BLOCKED, reasons, warnings)

    if gate_candidate.get("signal") != "SELL":
        return _candidate_result_from_gate(gate_candidate, index, INVALID, ["gate candidate signal must be SELL"], warnings)

    gate_preview = gate_candidate.get("gate_preview")
    if not isinstance(gate_preview, dict):
        return _candidate_result_from_gate(gate_candidate, index, INVALID, ["gate_preview must be a dict"], warnings)
    if gate_preview.get("stage") != "SIGNAL_QUEUE_GATE":
        return _candidate_result_from_gate(gate_candidate, index, INVALID, ["gate_preview stage is invalid"], warnings)
    if gate_preview.get("signal") != "SELL":
        return _candidate_result_from_gate(gate_candidate, index, INVALID, ["gate_preview signal must be SELL"], warnings)

    real_ready_order = _extract_real_ready_order(gate_candidate)
    if not isinstance(real_ready_order, dict):
        return _candidate_result_from_gate(
            gate_candidate,
            index,
            INVALID,
            ["real_ready_order must be a dict"],
            warnings,
            gate_preview=gate_preview,
        )

    validation_status, validation_reasons = _validate_real_ready_order(real_ready_order)
    if validation_reasons:
        return _candidate_result_from_gate(
            gate_candidate,
            index,
            validation_status,
            validation_reasons,
            warnings,
            gate_preview=gate_preview,
            real_ready_order=real_ready_order,
        )

    bridge_preview = build_signal_gate_execution_queue_bridge(
        deepcopy(gate_preview),
        deepcopy(real_ready_order),
        guard=deepcopy(guard_context),
        existing_orders=deepcopy(existing_orders),
    )

    if not isinstance(bridge_preview, dict):
        return _candidate_result_from_gate(
            gate_candidate,
            index,
            INVALID,
            ["bridge_preview must be a dict"],
            warnings,
            gate_preview=gate_preview,
            real_ready_order=real_ready_order,
        )

    bridge_validation_status, bridge_reasons = _validate_bridge_preview(bridge_preview, real_ready_order)
    queue_write_preview = (
        bridge_preview.get("queue_write_preview_result")
        if isinstance(bridge_preview.get("queue_write_preview_result"), dict)
        else None
    )
    order_queued_record_preview = (
        queue_write_preview.get("order_queued_record_preview")
        if isinstance(queue_write_preview, dict)
        else None
    )

    return _candidate_result_from_gate(
        gate_candidate,
        index,
        bridge_validation_status,
        bridge_reasons,
        warnings,
        gate_preview=gate_preview,
        real_ready_order=real_ready_order,
        bridge_preview=bridge_preview,
        queue_write_preview_result=queue_write_preview,
        order_queued_record_preview=order_queued_record_preview
        if isinstance(order_queued_record_preview, dict)
        else None,
    )


def _candidate_result_from_gate(
    gate_candidate: dict[str, Any],
    index: int,
    status: str,
    reasons: list[str],
    warnings: list[str],
    *,
    gate_preview: dict[str, Any] | None = None,
    real_ready_order: dict[str, Any] | None = None,
    bridge_preview: dict[str, Any] | None = None,
    queue_write_preview_result: dict[str, Any] | None = None,
    order_queued_record_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if gate_preview is None and isinstance(gate_candidate.get("gate_preview"), dict):
        gate_preview = gate_candidate.get("gate_preview")
    if real_ready_order is None:
        extracted = _extract_real_ready_order(gate_candidate)
        if isinstance(extracted, dict):
            real_ready_order = extracted
    return _candidate_result(
        index=index,
        status=status,
        source_candidate_index=gate_candidate.get("source_candidate_index", index),
        source_signal_id=_clean_text(gate_candidate.get("source_signal_id")),
        source_order_id=_clean_text(gate_candidate.get("source_order_id")),
        action_source=_clean_text(gate_candidate.get("action_source")).upper() or "UNKNOWN",
        signal=gate_candidate.get("signal") or "SELL",
        source_gate_candidate=gate_candidate,
        real_ready_order=real_ready_order,
        bridge_preview=bridge_preview,
        queue_write_preview_result=queue_write_preview_result,
        order_queued_record_preview=order_queued_record_preview,
        reasons=reasons,
        warnings=warnings,
    )


def _candidate_result(
    *,
    index: int,
    status: str,
    reasons: list[str],
    source_candidate_index: Any | None = None,
    source_signal_id: str = "",
    source_order_id: str = "",
    action_source: str = "UNKNOWN",
    signal: Any = "SELL",
    source_gate_candidate: dict[str, Any] | None = None,
    real_ready_order: dict[str, Any] | None = None,
    bridge_preview: dict[str, Any] | None = None,
    queue_write_preview_result: dict[str, Any] | None = None,
    order_queued_record_preview: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_candidate_index": source_candidate_index if source_candidate_index is not None else index,
        "source_signal_id": source_signal_id,
        "source_order_id": source_order_id,
        "action_source": action_source,
        "signal": signal,
        "source_gate_candidate": deepcopy(source_gate_candidate),
        "real_ready_order": deepcopy(real_ready_order),
        "bridge_preview": deepcopy(bridge_preview),
        "queue_write_preview_result": deepcopy(queue_write_preview_result),
        "order_queued_record_preview": deepcopy(order_queued_record_preview),
        "status": status,
        "reasons": list(reasons),
        "warnings": list(warnings or []),
        "priority_selected": False,
        "auto_selected": False,
    }


def _extract_real_ready_order(gate_candidate: dict[str, Any]) -> dict[str, Any] | None:
    readiness = gate_candidate.get("source_readiness_candidate")
    if not isinstance(readiness, dict):
        return None
    candidate_snapshot = readiness.get("candidate_snapshot")
    if not isinstance(candidate_snapshot, dict):
        return None
    order = candidate_snapshot.get("candidate_snapshot")
    if not isinstance(order, dict):
        return None
    return deepcopy(order)


def _validate_real_ready_order(order: dict[str, Any]) -> tuple[str, list[str]]:
    if _clean_text(order.get("status")).upper() != "REAL_READY":
        return BLOCKED, ["order status is not REAL_READY"]
    if _clean_text(order.get("side")).upper() != "SELL":
        return INVALID, ["order side must be SELL"]
    if _clean_text(order.get("order_type")).upper() != "SELL":
        return INVALID, ["order_type must be SELL"]
    if not _clean_text(order.get("source_signal_id")):
        return BLOCKED, ["source_signal_id is required"]
    if not _clean_text(order.get("id") or order.get("order_id")):
        return BLOCKED, ["order id is required"]
    if not _clean_text(order.get("code")):
        return INVALID, ["code is required"]
    if not _positive_number(order.get("quantity")):
        return INVALID, ["quantity must be positive"]
    hoga = _clean_text(order.get("hoga")).upper()
    if not hoga:
        return INVALID, ["hoga is required"]
    if hoga == "MARKET":
        return BLOCKED, ["MARKET candidates stay blocked until queue contract is reviewed"]
    return READY, []


def _validate_bridge_preview(
    bridge_preview: dict[str, Any],
    real_ready_order: dict[str, Any],
) -> tuple[str, list[str]]:
    if bridge_preview.get("stage") != BRIDGE_STAGE:
        return INVALID, ["bridge_preview stage is invalid"]

    bridge_result = _clean_text(bridge_preview.get("bridge_result")).upper()
    if bridge_result == BRIDGE_IGNORE:
        return IGNORE, []

    if bridge_result == BRIDGE_BLOCKED:
        reason = _clean_text(bridge_preview.get("bridge_reason")) or "bridge preview blocked"
        return BLOCKED, [reason]

    if bridge_result != BRIDGE_READY:
        return INVALID, ["bridge_preview bridge_result is invalid"]

    if bridge_preview.get("ok") is not True:
        return BLOCKED, ["bridge_preview ok is not True"]

    if bridge_preview.get("queue_writer_preview_connected") is not True:
        return BLOCKED, ["queue_writer_preview_connected is not True"]

    queue_preview = bridge_preview.get("queue_write_preview_result")
    if not isinstance(queue_preview, dict):
        return INVALID, ["queue_write_preview_result must be a dict"]

    return _validate_queue_write_preview(queue_preview, real_ready_order)


def _validate_queue_write_preview(
    queue_preview: dict[str, Any],
    real_ready_order: dict[str, Any],
) -> tuple[str, list[str]]:
    if queue_preview.get("write_preview") is not True:
        return BLOCKED, _queue_block_reasons(queue_preview, "write_preview is not True")
    if queue_preview.get("write_stage") != QUEUE_WRITE_STAGE_READY:
        return BLOCKED, ["write_stage is not order_queued_record_preview_created"]
    if queue_preview.get("next_stage") != QUEUE_WRITE_NEXT_STAGE:
        return BLOCKED, ["next_stage is not QUEUE_WRITE_REQUIRED"]
    if queue_preview.get("preview_only") is not True:
        return INVALID, ["queue_write_preview_result preview_only must be True"]
    if queue_preview.get("no_write") is not True:
        return INVALID, ["queue_write_preview_result no_write must be True"]

    record = queue_preview.get("order_queued_record_preview")
    if not isinstance(record, dict):
        return INVALID, ["order_queued_record_preview must be a dict"]
    if record.get("status") != "ORDER_QUEUED":
        return INVALID, ["order_queued_record_preview status must be ORDER_QUEUED"]
    if record.get("send_order_called") is not False:
        return INVALID, ["order_queued_record_preview send_order_called must be False"]
    if record.get("execution_enabled") is not False:
        return INVALID, ["order_queued_record_preview execution_enabled must be False"]

    if record.get("source") != "execution_queue_pending":
        return INVALID, ["order_queued_record_preview source must be execution_queue_pending"]

    for field in _REQUIRED_QUEUE_RECORD_TEXT_FIELDS:
        if not _clean_text(record.get(field)):
            return INVALID, [f"order_queued_record_preview {field} is required"]

    execution_request = record.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        return INVALID, ["order_queued_record_preview execution_request must be a non-empty dict"]

    for field in _REQUIRED_EXECUTION_REQUEST_TEXT_FIELDS:
        if not _clean_text(execution_request.get(field)):
            return INVALID, [f"execution_request {field} is required"]

    if _clean_text(record.get("source_signal_id")) != _clean_text(real_ready_order.get("source_signal_id")):
        return INVALID, ["order_queued_record_preview source_signal_id does not match real_ready_order"]

    expected_order_id = _clean_text(real_ready_order.get("id") or real_ready_order.get("order_id"))
    if _clean_text(record.get("order_id")) != expected_order_id:
        return INVALID, ["order_queued_record_preview order_id does not match real_ready_order"]

    for field in _REQUIRED_EXECUTION_REQUEST_TEXT_FIELDS:
        if _clean_text(record.get(field)) != _clean_text(execution_request.get(field)):
            return INVALID, [f"order_queued_record_preview {field} does not match execution_request"]
    return READY, []


def _queue_block_reasons(queue_preview: dict[str, Any], fallback: str) -> list[str]:
    reasons = queue_preview.get("blocked_reasons")
    if isinstance(reasons, list) and reasons:
        return [str(reason) for reason in reasons]
    return [fallback]


def _is_duplicate_result(queue_result: dict[str, Any]) -> bool:
    reasons = queue_result.get("reasons")
    if not isinstance(reasons, list):
        return False
    return any(str(reason).startswith("duplicate ") for reason in reasons)


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _positive_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool) and value > 0


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
