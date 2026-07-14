"""SELL runtime commit contract validator.

This module validates SELL_RUNTIME_COMMIT_PREVIEW output at the boundary right
before a runtime commit. It decides only whether the preview contract is
commit-eligible. It never commits runtime state, writes queue records, creates
OrderRequest objects, or calls broker APIs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

VALIDATION_TYPE = "SELL_RUNTIME_COMMIT_VALIDATOR"
SOURCE_PREVIEW_TYPE = "SELL_RUNTIME_COMMIT_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Validator"
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

_REQUIRED_CANDIDATE_FIELDS = (
    "source_signal_id",
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
)


def validate_sell_runtime_commit(runtime_commit_preview: dict[str, Any]) -> dict[str, Any]:
    """Validate SELL runtime commit preview without performing the commit."""
    result = _base_result(runtime_commit_preview)

    if not isinstance(runtime_commit_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview must be a dict")
        return _finish(result)

    result["runtime_commit_preview_snapshot"] = deepcopy(runtime_commit_preview)

    if runtime_commit_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview preview_type is invalid")
        return _finish(result)

    if runtime_commit_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(runtime_commit_preview):
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview safety flag violation")
        return _finish(result)

    _copy_messages(runtime_commit_preview, result)
    _copy_summary(runtime_commit_preview, result)

    upstream_status = _status(runtime_commit_preview.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("runtime_commit_preview status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_preview status is invalid")
        return _finish(result)

    if runtime_commit_preview.get("runtime_commit_ready") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_ready must be True when status is READY")
        return _finish(result)

    candidates = runtime_commit_preview.get("runtime_commit_candidates")
    if not isinstance(candidates, list):
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_candidates must be a list")
        return _finish(result)

    if not candidates:
        result["status"] = BLOCKED
        result["reasons"].append("no runtime commit candidates to validate")
        return _finish(result)

    has_invalid = False
    for index, candidate in enumerate(candidates):
        validated = _validate_candidate(candidate, index)
        result["validated_runtime_commit_candidates"].append(validated)
        if validated["status"] == READY:
            result["summary"]["validator_ready_count"] += 1
        elif validated["status"] == INVALID:
            has_invalid = True
            result["blocked_runtime_commit_candidates"].append(deepcopy(validated))
            result["summary"]["validator_invalid_count"] += 1
        else:
            result["blocked_runtime_commit_candidates"].append(deepcopy(validated))
            result["summary"]["validator_blocked_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["validator_ready_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        result["reasons"].append("no commit-eligible runtime commit candidates")

    return _finish(result)


validate_sell_runtime_commit_preview = validate_sell_runtime_commit


def _base_result(runtime_commit_preview: Any) -> dict[str, Any]:
    return {
        "validation_type": VALIDATION_TYPE,
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
        "runtime_commit_executed": False,
        "commit_allowed": False,
        "status": BLOCKED,
        "runtime_commit_preview_snapshot": deepcopy(runtime_commit_preview)
        if isinstance(runtime_commit_preview, dict)
        else {},
        "validated_runtime_commit_candidates": [],
        "blocked_runtime_commit_candidates": [],
        "warnings": [],
        "reasons": [],
        "summary": {
            "validator_ready_count": 0,
            "validator_blocked_count": 0,
            "validator_invalid_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _validate_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _candidate_result(index, INVALID, ["runtime commit candidate must be a dict"])

    reasons: list[str] = []
    warnings = candidate.get("warnings") if isinstance(candidate.get("warnings"), list) else []

    candidate_status = _status(candidate.get("status"))
    if candidate_status == INVALID:
        reasons.append("candidate status is INVALID")
        return _candidate_result(index, INVALID, reasons, source_candidate=candidate, warnings=warnings)
    if candidate_status == BLOCKED:
        reasons.append("candidate status is BLOCKED")
        return _candidate_result(index, BLOCKED, reasons, source_candidate=candidate, warnings=warnings)
    if candidate_status != READY:
        reasons.append("candidate status is invalid")

    if candidate.get("runtime_commit_ready") is not True:
        reasons.append("candidate runtime_commit_ready must be True")

    if _has_forbidden_safety_flag(candidate):
        reasons.append("candidate safety flag violation")

    for field in _REQUIRED_CANDIDATE_FIELDS:
        if not _clean_text(candidate.get(field)):
            reasons.append(f"{field} is required")

    record = candidate.get("order_queued_record_preview")
    if not isinstance(record, dict) or not record:
        reasons.append("order_queued_record_preview must be a non-empty dict")
        record = {}

    execution_request = candidate.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        reasons.append("execution_request must be a non-empty dict")
        execution_request = {}

    record_request = record.get("execution_request") if isinstance(record.get("execution_request"), dict) else {}
    if not record_request:
        reasons.append("record execution_request must be a non-empty dict")

    for field in ("source_signal_id", "order_id", "candidate_id", "queue_pending_id"):
        _require_match(candidate, record, field, reasons)

    for field in ("execution_id", "request_hash", "lock_id"):
        _require_match(candidate, record, field, reasons)
        _require_match(candidate, execution_request, field, reasons, target_name="execution_request")
        _require_match(candidate, record_request, field, reasons, target_name="record execution_request")

    status = BLOCKED if reasons else READY
    return _candidate_result(
        index,
        status,
        reasons,
        source_candidate=candidate,
        record=record,
        execution_request=execution_request,
        warnings=warnings,
    )


def _candidate_result(
    index: int,
    status: str,
    reasons: list[str],
    *,
    source_candidate: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
    execution_request: dict[str, Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    candidate = source_candidate if isinstance(source_candidate, dict) else {}
    record_dict = record if isinstance(record, dict) else {}
    return {
        "status": status,
        "commit_allowed": status == READY,
        "candidate_index": index,
        "source_candidate": deepcopy(source_candidate),
        "source_signal_id": _clean_text(candidate.get("source_signal_id") or record_dict.get("source_signal_id")),
        "order_id": _clean_text(candidate.get("order_id") or record_dict.get("order_id")),
        "candidate_id": _clean_text(candidate.get("candidate_id") or record_dict.get("candidate_id")),
        "queue_pending_id": _clean_text(candidate.get("queue_pending_id") or record_dict.get("queue_pending_id")),
        "execution_id": _clean_text(candidate.get("execution_id") or record_dict.get("execution_id")),
        "request_hash": _clean_text(candidate.get("request_hash") or record_dict.get("request_hash")),
        "lock_id": _clean_text(candidate.get("lock_id") or record_dict.get("lock_id")),
        "execution_request": deepcopy(execution_request) if isinstance(execution_request, dict) else {},
        "order_queued_record_preview": deepcopy(record) if isinstance(record, dict) else {},
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "reasons": list(reasons),
        "warnings": deepcopy(warnings) if isinstance(warnings, list) else [],
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["commit_allowed"] = result.get("status") == READY
    result["preview_only"] = True
    result["execution_connected"] = False
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["queue_committed"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["runtime_commit_executed"] = False
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["queue_committed"] = False
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    return result


def _copy_messages(source: dict[str, Any], result: dict[str, Any]) -> None:
    if isinstance(source.get("warnings"), list):
        result["warnings"].extend(deepcopy(source["warnings"]))
    if isinstance(source.get("reasons"), list):
        result["reasons"].extend(deepcopy(source["reasons"]))


def _copy_summary(source: dict[str, Any], result: dict[str, Any]) -> None:
    if isinstance(source.get("summary"), dict):
        result["summary"].update(deepcopy(source["summary"]))


def _require_match(
    candidate: dict[str, Any],
    target: dict[str, Any],
    field: str,
    reasons: list[str],
    *,
    target_name: str = "record",
) -> None:
    left = _clean_text(candidate.get(field))
    right = _clean_text(target.get(field)) if isinstance(target, dict) else ""
    if left and right and left != right:
        reasons.append(f"{field} must match {target_name}")


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
