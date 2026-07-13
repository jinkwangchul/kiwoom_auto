"""Preview-only SELL runtime commit readiness.

This module validates SELL_EXECUTION_FULL_PREVIEW output immediately before a
runtime commit boundary. It never writes runtime files, commits queue records,
creates OrderRequest objects, or calls broker APIs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_RUNTIME_COMMIT_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_EXECUTION_FULL_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Preview"
ROUTINE_DEPENDENCY = None

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "send_order",
    "broker_api_called",
    "queue_committed",
    "actual_order_sent",
    "order_request_created",
    "real_ready_state_changed",
)


def build_sell_runtime_commit_preview(full_preview: dict[str, Any]) -> dict[str, Any]:
    """Validate SELL full preview output without committing runtime state."""
    result = _base_result(full_preview)

    if not isinstance(full_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("full_preview must be a dict")
        return _finish(result)

    result["full_preview_snapshot"] = deepcopy(full_preview)

    if full_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("full_preview preview_type is invalid")
        return _finish(result)

    if full_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("full_preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(full_preview):
        result["status"] = INVALID
        result["reasons"].append("full_preview safety flag violation")
        return _finish(result)

    if isinstance(full_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(full_preview["warnings"]))
    if isinstance(full_preview.get("reasons"), list):
        result["reasons"].extend(deepcopy(full_preview["reasons"]))
    if isinstance(full_preview.get("summary"), dict):
        result["summary"].update(deepcopy(full_preview["summary"]))

    upstream_status = _status(full_preview.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("full_preview status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("full_preview status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("full_preview status is invalid")
        return _finish(result)

    queue_preview = full_preview.get("execution_queue_preview")
    if not isinstance(queue_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("execution_queue_preview must be a dict")
        return _finish(result)

    queue_candidates = queue_preview.get("queue_ready_candidates")
    if not isinstance(queue_candidates, list):
        result["status"] = INVALID
        result["reasons"].append("queue_ready_candidates must be a list")
        return _finish(result)

    if not queue_candidates:
        result["status"] = BLOCKED
        result["reasons"].append("no queue-ready candidates for runtime commit preview")
        return _finish(result)

    has_invalid = False
    for index, candidate in enumerate(queue_candidates):
        commit_candidate = _runtime_commit_candidate(candidate, index)
        result["runtime_commit_candidates"].append(commit_candidate)
        if commit_candidate["status"] == READY:
            result["summary"]["runtime_commit_ready_count"] += 1
        elif commit_candidate["status"] == INVALID:
            has_invalid = True
            result["blocked_runtime_commit_candidates"].append(deepcopy(commit_candidate))
            result["summary"]["runtime_commit_invalid_count"] += 1
        else:
            result["blocked_runtime_commit_candidates"].append(deepcopy(commit_candidate))
            result["summary"]["runtime_commit_blocked_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["runtime_commit_ready_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no READY runtime commit preview candidates")

    return _finish(result)


preview_sell_runtime_commit = build_sell_runtime_commit_preview


def _base_result(full_preview: Any) -> dict[str, Any]:
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
        "queue_committed": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "runtime_commit_ready": False,
        "runtime_commit_candidates": [],
        "blocked_runtime_commit_candidates": [],
        "full_preview_snapshot": deepcopy(full_preview) if isinstance(full_preview, dict) else {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "runtime_commit_ready_count": 0,
            "runtime_commit_blocked_count": 0,
            "runtime_commit_invalid_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _runtime_commit_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _candidate_result(index, INVALID, ["queue-ready candidate must be a dict"])

    record = candidate.get("order_queued_record_preview")
    if not isinstance(record, dict):
        return _candidate_result(
            index,
            INVALID,
            ["order_queued_record_preview must be a dict"],
            source_candidate=candidate,
        )

    execution_request = record.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        return _candidate_result(
            index,
            INVALID,
            ["execution_request must be a non-empty dict"],
            source_candidate=candidate,
            record=record,
        )

    reasons: list[str] = []
    for field in ("source_signal_id", "order_id", "request_hash", "lock_id", "execution_id"):
        if not _clean_text(record.get(field)):
            reasons.append(f"{field} is required")

    for field in ("request_hash", "lock_id", "execution_id"):
        if _clean_text(record.get(field)) != _clean_text(execution_request.get(field)):
            reasons.append(f"{field} must match execution_request")

    status = READY if not reasons else BLOCKED
    return _candidate_result(
        index,
        status,
        reasons,
        source_candidate=candidate,
        record=record,
        execution_request=execution_request,
    )


def _candidate_result(
    index: int,
    status: str,
    reasons: list[str],
    *,
    source_candidate: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
    execution_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "runtime_commit_ready": status == READY,
        "candidate_index": index,
        "source_candidate": deepcopy(source_candidate),
        "source_signal_id": _clean_text(record.get("source_signal_id")) if isinstance(record, dict) else "",
        "order_id": _clean_text(record.get("order_id")) if isinstance(record, dict) else "",
        "candidate_id": _clean_text(record.get("candidate_id")) if isinstance(record, dict) else "",
        "queue_pending_id": _clean_text(record.get("queue_pending_id")) if isinstance(record, dict) else "",
        "execution_id": _clean_text(record.get("execution_id")) if isinstance(record, dict) else "",
        "request_hash": _clean_text(record.get("request_hash")) if isinstance(record, dict) else "",
        "lock_id": _clean_text(record.get("lock_id")) if isinstance(record, dict) else "",
        "execution_request": deepcopy(execution_request) if isinstance(execution_request, dict) else {},
        "order_queued_record_preview": deepcopy(record) if isinstance(record, dict) else {},
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "reasons": list(reasons),
        "warnings": deepcopy(source_candidate.get("warnings")) if isinstance(source_candidate, dict) and isinstance(source_candidate.get("warnings"), list) else [],
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["runtime_commit_ready"] = result.get("status") == READY
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["queue_committed"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["queue_committed"] = False
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    return result


def _status(value: Any) -> str:
    text = _clean_text(value).upper()
    if text in {READY, BLOCKED, INVALID}:
        return text
    return INVALID


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
