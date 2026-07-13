"""Preview-only SELL Signal Gate adapter.

This module adapts SELL Execution Readiness Preview output to the common
Signal Queue Gate preview. It preserves SELL candidate identity outside the
common gate result and never connects to queue, runtime, or broker dispatch
paths.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from signal_queue_gate_service import build_signal_queue_gate


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"
IGNORE = "IGNORE"

PREVIEW_TYPE = "SELL_SIGNAL_GATE_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_EXECUTION_READINESS_PREVIEW"

OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Signal Gate Preview"
ROUTINE_DEPENDENCY = None

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "send_order",
    "broker_api_called",
    "real_ready_state_changed",
    "order_request_created",
)

_REQUIRED_LIST_FIELDS = (
    "ready_candidates",
    "candidate_readiness",
    "blocked_candidate_readiness",
)

_EXCLUDED_ACTION_SOURCES = {"PENDING", "CANCEL_PENDING_ORDER"}


def build_sell_signal_gate_preview(
    execution_readiness_preview: dict[str, Any],
) -> dict[str, Any]:
    """Build a SELL Signal Gate preview from SELL readiness output."""
    result = _base_result(execution_readiness_preview)

    if not isinstance(execution_readiness_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview must be a dict")
        return _finish(result)

    result["execution_readiness_preview_snapshot"] = deepcopy(execution_readiness_preview)

    if execution_readiness_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview preview_type is invalid")
        return _finish(result)

    if execution_readiness_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(execution_readiness_preview):
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview safety flag violation")
        return _finish(result)

    for field in _REQUIRED_LIST_FIELDS:
        if not isinstance(execution_readiness_preview.get(field), list):
            result["status"] = INVALID
            result["reasons"].append(f"{field} must be a list")
            return _finish(result)

    if not isinstance(execution_readiness_preview.get("summary"), dict):
        result["status"] = INVALID
        result["reasons"].append("summary must be a dict")
        return _finish(result)

    if isinstance(execution_readiness_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(execution_readiness_preview["warnings"]))

    result["upstream_blocked_candidates"] = deepcopy(
        execution_readiness_preview.get("blocked_candidate_readiness")
    )
    result["summary"]["upstream_blocked_count"] = len(result["upstream_blocked_candidates"])

    upstream_status = _clean_text(execution_readiness_preview.get("status")).upper()
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("execution_readiness_preview status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview status is invalid")
        return _finish(result)
    if execution_readiness_preview.get("readiness_ready") is not True:
        result["status"] = INVALID
        result["reasons"].append("execution_readiness_preview readiness_ready must be True when status is READY")
        return _finish(result)

    ready_candidates = execution_readiness_preview.get("ready_candidates")
    result["summary"]["candidate_count"] = len(ready_candidates)

    if not ready_candidates:
        result["status"] = BLOCKED
        result["reasons"].append("no READY readiness candidates")
        return _finish(result)

    has_invalid = False
    for index, candidate in enumerate(ready_candidates):
        gate_result = _build_candidate_gate(candidate, index)
        result["candidate_gates"].append(gate_result)

        status = gate_result.get("status")
        if status == READY:
            result["opened_gates"].append(deepcopy(gate_result))
            result["summary"]["opened_gate_count"] += 1
        elif status == INVALID:
            result["blocked_gates"].append(deepcopy(gate_result))
            result["summary"]["invalid_candidate_count"] += 1
            has_invalid = True
        elif status == IGNORE:
            result["ignored_gates"].append(deepcopy(gate_result))
            result["summary"]["ignored_gate_count"] += 1
        else:
            result["blocked_gates"].append(deepcopy(gate_result))
            result["summary"]["blocked_gate_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["opened_gate_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no OPEN SELL signal gates")

    return _finish(result)


def _base_result(execution_readiness_preview: Any) -> dict[str, Any]:
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
        "queue_writer_preview_called": False,
        "status": BLOCKED,
        "signal_gate_ready": False,
        "opened_gates": [],
        "blocked_gates": [],
        "ignored_gates": [],
        "candidate_gates": [],
        "upstream_blocked_candidates": [],
        "execution_readiness_preview_snapshot": deepcopy(execution_readiness_preview)
        if isinstance(execution_readiness_preview, dict)
        else {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "candidate_count": 0,
            "opened_gate_count": 0,
            "blocked_gate_count": 0,
            "ignored_gate_count": 0,
            "invalid_candidate_count": 0,
            "upstream_blocked_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "queue_preview_called": False,
        },
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["signal_gate_ready"] = result.get("status") == READY
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    result["summary"]["queue_preview_called"] = False
    result["queue_writer_preview_called"] = False
    return result


def _build_candidate_gate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _candidate_gate_result(
            index=index,
            status=INVALID,
            action_source="UNKNOWN",
            source_readiness_candidate=None,
            signal_queue_candidate=None,
            gate_preview=None,
            reasons=["readiness candidate must be a dict"],
        )

    action_source = _clean_text(candidate.get("action_source")).upper() or "UNKNOWN"
    warnings = deepcopy(candidate.get("warnings")) if isinstance(candidate.get("warnings"), list) else []
    reasons = deepcopy(candidate.get("reasons")) if isinstance(candidate.get("reasons"), list) else []

    if _clean_text(candidate.get("status")).upper() != READY:
        reasons.append("readiness candidate status is not READY")
        return _candidate_gate_result_from_candidate(candidate, index, BLOCKED, reasons, warnings)

    if candidate.get("readiness_ready") is not True:
        reasons.append("readiness candidate readiness_ready must be True")
        return _candidate_gate_result_from_candidate(candidate, index, BLOCKED, reasons, warnings)

    if action_source in _EXCLUDED_ACTION_SOURCES:
        reasons.append("PENDING cancel action requires a separate cancel execution path")
        return _candidate_gate_result_from_candidate(candidate, index, BLOCKED, reasons, warnings)

    source_snapshot = candidate.get("candidate_snapshot")
    if not isinstance(source_snapshot, dict):
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["candidate_snapshot must be a dict"],
            warnings,
        )

    order_candidate = source_snapshot.get("candidate_snapshot")
    if not isinstance(order_candidate, dict):
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["nested order candidate_snapshot must be a dict"],
            warnings,
        )

    side = _clean_text(order_candidate.get("side")).upper()
    if side != "SELL":
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["order candidate side must be SELL"],
            warnings,
            order_candidate=order_candidate,
        )

    source_signal_id = _clean_text(order_candidate.get("source_signal_id"))
    source_order_id = _clean_text(order_candidate.get("id") or order_candidate.get("order_id"))
    if not source_signal_id:
        reasons.append("source_signal_id is required")
    if not source_order_id:
        reasons.append("order id is required")

    hoga = _clean_text(order_candidate.get("hoga")).upper()
    if hoga == "MARKET":
        reasons.append("MARKET candidates stay blocked until common price contract is reviewed")

    if reasons:
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            BLOCKED,
            reasons,
            warnings,
            order_candidate=order_candidate,
        )

    signal_queue_candidate = _signal_queue_candidate(candidate, order_candidate, action_source, index)
    gate_preview = build_signal_queue_gate(deepcopy(signal_queue_candidate))

    if not isinstance(gate_preview, dict):
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["gate_preview must be a dict"],
            warnings,
            order_candidate=order_candidate,
            signal_queue_candidate=signal_queue_candidate,
            gate_preview=None,
        )

    if gate_preview.get("stage") != "SIGNAL_QUEUE_GATE":
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["gate_preview stage is invalid"],
            warnings,
            order_candidate=order_candidate,
            signal_queue_candidate=signal_queue_candidate,
            gate_preview=gate_preview,
        )

    if gate_preview.get("signal") != "SELL":
        return _candidate_gate_result_from_candidate(
            candidate,
            index,
            INVALID,
            ["gate_preview signal is invalid"],
            warnings,
            order_candidate=order_candidate,
            signal_queue_candidate=signal_queue_candidate,
            gate_preview=gate_preview,
        )

    gate_result = _clean_text(gate_preview.get("gate_result")).upper()
    if gate_result == "OPEN" and gate_preview.get("ok") is True:
        status = READY
    elif gate_result == "IGNORE":
        status = IGNORE
    elif gate_result == "BLOCKED":
        status = BLOCKED
    else:
        status = INVALID
        reasons = ["gate_preview gate_result is invalid"]

    if status == BLOCKED:
        gate_reasons = gate_preview.get("blocked_reasons")
        if isinstance(gate_reasons, list) and gate_reasons:
            reasons.extend(str(reason) for reason in gate_reasons)
        elif gate_preview.get("gate_reason"):
            reasons.append(str(gate_preview["gate_reason"]))

    return _candidate_gate_result_from_candidate(
        candidate,
        index,
        status,
        reasons,
        warnings,
        order_candidate=order_candidate,
        signal_queue_candidate=signal_queue_candidate,
        gate_preview=gate_preview,
    )


def _candidate_gate_result_from_candidate(
    candidate: dict[str, Any],
    index: int,
    status: str,
    reasons: list[str],
    warnings: list[str],
    *,
    order_candidate: dict[str, Any] | None = None,
    signal_queue_candidate: dict[str, Any] | None = None,
    gate_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_source = _clean_text(candidate.get("action_source")).upper() or "UNKNOWN"
    source_snapshot = candidate.get("candidate_snapshot")
    if order_candidate is None and isinstance(source_snapshot, dict):
        nested = source_snapshot.get("candidate_snapshot")
        if isinstance(nested, dict):
            order_candidate = nested
    return _candidate_gate_result(
        index=index,
        status=status,
        action_source=action_source,
        source_readiness_candidate=candidate,
        signal_queue_candidate=signal_queue_candidate,
        gate_preview=gate_preview,
        reasons=reasons,
        warnings=warnings,
        order_candidate=order_candidate,
    )


def _candidate_gate_result(
    *,
    index: int,
    status: str,
    action_source: str,
    source_readiness_candidate: dict[str, Any] | None,
    signal_queue_candidate: dict[str, Any] | None,
    gate_preview: dict[str, Any] | None,
    reasons: list[str],
    warnings: list[str] | None = None,
    order_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate_result = gate_preview.get("gate_result") if isinstance(gate_preview, dict) else None
    source_index = (
        source_readiness_candidate.get("candidate_index")
        if isinstance(source_readiness_candidate, dict)
        else index
    )
    return {
        "source_candidate_index": source_index,
        "source_signal_id": _clean_text(order_candidate.get("source_signal_id")) if isinstance(order_candidate, dict) else "",
        "source_order_id": _clean_text(order_candidate.get("id") or order_candidate.get("order_id"))
        if isinstance(order_candidate, dict)
        else "",
        "action_source": action_source,
        "signal": "SELL",
        "source_readiness_candidate": deepcopy(source_readiness_candidate),
        "signal_queue_candidate": deepcopy(signal_queue_candidate),
        "gate_preview": deepcopy(gate_preview),
        "gate_result": gate_result,
        "status": status,
        "reasons": list(reasons),
        "warnings": list(warnings or []),
        "priority_selected": False,
        "auto_selected": False,
    }


def _signal_queue_candidate(
    readiness_candidate: dict[str, Any],
    order_candidate: dict[str, Any],
    action_source: str,
    fallback_index: int,
) -> dict[str, Any]:
    source_index = readiness_candidate.get("candidate_index")
    if source_index is None:
        source_index = fallback_index

    candidate = {
        "stage": "SIGNAL_QUEUE_CANDIDATE",
        "candidate_result": "READY",
        "signal": "SELL",
        "decision": "SELL",
        "signal_index": source_index,
        "rule_source": action_source,
        "blocked_policy": None,
        "matched_rule_paths": [],
        "condition_summary": [],
        "applied_policies": [],
    }

    for key in ("method_set", "policy_result", "delay_bar"):
        value = readiness_candidate.get(key)
        if value is None and isinstance(readiness_candidate.get("candidate_snapshot"), dict):
            value = readiness_candidate["candidate_snapshot"].get(key)
        if value is None:
            value = order_candidate.get(key)
        if value is not None:
            candidate[key] = deepcopy(value)

    return candidate


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
