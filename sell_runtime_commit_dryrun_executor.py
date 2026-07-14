"""SELL runtime commit dry-run executor.

This module consumes SELL_RUNTIME_COMMIT_VALIDATOR output and builds the result
that would be produced if a runtime commit were executed. It is intentionally
dry-run-only: no runtime files, queue records, order request objects, order
sending, or broker APIs are touched.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

DRYRUN_TYPE = "SELL_RUNTIME_COMMIT_DRYRUN_EXECUTOR"
SOURCE_VALIDATION_TYPE = "SELL_RUNTIME_COMMIT_VALIDATOR"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Dry Run Executor"
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
    "runtime_commit_executed",
)

_IDENTITY_FIELDS = (
    "source_signal_id",
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
)


def build_sell_runtime_commit_dryrun(runtime_commit_validation: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run runtime commit result without committing anything."""
    result = _base_result(runtime_commit_validation)

    if not isinstance(runtime_commit_validation, dict):
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation must be a dict")
        return _finish(result)

    result["runtime_commit_validation_snapshot"] = deepcopy(runtime_commit_validation)

    if runtime_commit_validation.get("validation_type") != SOURCE_VALIDATION_TYPE:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation validation_type is invalid")
        return _finish(result)

    if runtime_commit_validation.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(runtime_commit_validation):
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation safety flag violation")
        return _finish(result)

    _copy_messages(runtime_commit_validation, result)
    _copy_summary(runtime_commit_validation, result)

    upstream_status = _status(runtime_commit_validation.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("runtime_commit_validation status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("runtime_commit_validation status is invalid")
        return _finish(result)

    if runtime_commit_validation.get("commit_allowed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("commit_allowed must be True for dry-run execution")
        return _finish(result)

    candidates = runtime_commit_validation.get("validated_runtime_commit_candidates")
    if not isinstance(candidates, list):
        result["status"] = INVALID
        result["reasons"].append("validated_runtime_commit_candidates must be a list")
        return _finish(result)

    if not candidates:
        result["status"] = BLOCKED
        result["reasons"].append("no validated runtime commit candidates")
        return _finish(result)

    candidate_count = len(candidates)
    for index, candidate in enumerate(candidates):
        action = _dryrun_action(candidate, index)
        result["commit_actions"].append(action)
        if action["status"] == READY:
            result["summary"]["dryrun_ready_count"] += 1
        elif action["status"] == INVALID:
            result["blocked_commit_actions"].append(deepcopy(action))
            result["summary"]["dryrun_invalid_count"] += 1
        else:
            result["blocked_commit_actions"].append(deepcopy(action))
            result["summary"]["dryrun_blocked_count"] += 1

    invalid_count = result["summary"]["dryrun_invalid_count"]
    blocked_count = result["summary"]["dryrun_blocked_count"]
    ready_count = result["summary"]["dryrun_ready_count"]

    if invalid_count > 0:
        result["status"] = INVALID
    elif blocked_count > 0:
        result["status"] = BLOCKED
        result["reasons"].append("one or more dry-run commit candidates are blocked")
    elif ready_count == candidate_count and candidate_count > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        result["reasons"].append("no dry-run executable commit actions")

    result["runtime_commit_preview"] = _runtime_commit_preview(result)
    result["commit_plan"] = _commit_plan(result)
    result["validation_summary"] = _validation_summary(runtime_commit_validation, result)
    result["execution_summary"] = _execution_summary(result)
    return _finish(result)


execute_sell_runtime_commit_dryrun = build_sell_runtime_commit_dryrun


def _base_result(runtime_commit_validation: Any) -> dict[str, Any]:
    return {
        "dryrun_type": DRYRUN_TYPE,
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
        "dry_run": True,
        "commit_allowed": False,
        "status": BLOCKED,
        "runtime_commit_validation_snapshot": deepcopy(runtime_commit_validation)
        if isinstance(runtime_commit_validation, dict)
        else {},
        "runtime_commit_preview": {},
        "commit_plan": {},
        "commit_actions": [],
        "blocked_commit_actions": [],
        "validation_summary": {},
        "execution_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "dryrun_ready_count": 0,
            "dryrun_blocked_count": 0,
            "dryrun_invalid_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _dryrun_action(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _action_result(index, INVALID, ["validated candidate must be a dict"])

    reasons: list[str] = []
    structural_error = False
    if _status(candidate.get("status")) != READY:
        reasons.append("validated candidate status must be READY")
    if candidate.get("commit_allowed") is not True:
        reasons.append("validated candidate commit_allowed must be True")
    if _has_forbidden_safety_flag(candidate):
        structural_error = True
        reasons.append("validated candidate safety flag violation")

    for field in _IDENTITY_FIELDS:
        if not _clean_text(candidate.get(field)):
            structural_error = True
            reasons.append(f"{field} is required")

    execution_request = candidate.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        structural_error = True
        reasons.append("execution_request must be a non-empty dict")
        execution_request = {}

    record = candidate.get("order_queued_record_preview")
    if not isinstance(record, dict) or not record:
        structural_error = True
        reasons.append("order_queued_record_preview must be a non-empty dict")
        record = {}

    for field in ("execution_id", "request_hash", "lock_id"):
        if _clean_text(candidate.get(field)) != _clean_text(execution_request.get(field)):
            structural_error = True
            reasons.append(f"{field} must match execution_request")
        if _clean_text(candidate.get(field)) != _clean_text(record.get(field)):
            structural_error = True
            reasons.append(f"{field} must match order_queued_record_preview")

    for field in ("source_signal_id", "order_id", "candidate_id", "queue_pending_id"):
        if _clean_text(candidate.get(field)) != _clean_text(record.get(field)):
            structural_error = True
            reasons.append(f"{field} must match order_queued_record_preview")

    status = INVALID if structural_error else BLOCKED if reasons else READY
    return _action_result(
        index,
        status,
        reasons,
        source_candidate=candidate,
        execution_request=execution_request,
        record=record,
    )


def _action_result(
    index: int,
    status: str,
    reasons: list[str],
    *,
    source_candidate: dict[str, Any] | None = None,
    execution_request: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = source_candidate if isinstance(source_candidate, dict) else {}
    return {
        "status": status,
        "dryrun_action_ready": status == READY,
        "candidate_index": index,
        "action": "RUNTIME_COMMIT_DRY_RUN",
        "source_candidate": deepcopy(source_candidate),
        "source_signal_id": _clean_text(candidate.get("source_signal_id")),
        "order_id": _clean_text(candidate.get("order_id")),
        "candidate_id": _clean_text(candidate.get("candidate_id")),
        "queue_pending_id": _clean_text(candidate.get("queue_pending_id")),
        "execution_id": _clean_text(candidate.get("execution_id")),
        "request_hash": _clean_text(candidate.get("request_hash")),
        "lock_id": _clean_text(candidate.get("lock_id")),
        "execution_request": deepcopy(execution_request) if isinstance(execution_request, dict) else {},
        "order_queued_record_preview": deepcopy(record) if isinstance(record, dict) else {},
        "would_commit_runtime": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "reasons": list(reasons),
        "warnings": deepcopy(candidate.get("warnings")) if isinstance(candidate.get("warnings"), list) else [],
    }


def _runtime_commit_preview(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "preview_type": "SELL_RUNTIME_COMMIT_DRYRUN_PREVIEW",
        "status": result["status"],
        "dry_run": True,
        "commit_allowed": result["status"] == READY,
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "runtime_commit_executed": False,
        "commit_action_count": result["summary"]["dryrun_ready_count"],
    }


def _commit_plan(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_type": "SELL_RUNTIME_COMMIT_DRYRUN_PLAN",
        "dry_run": True,
        "status": result["status"],
        "commit_allowed": result["status"] == READY,
        "actions": deepcopy(result["commit_actions"]),
        "action_count": len(result["commit_actions"]),
        "ready_action_count": result["summary"]["dryrun_ready_count"],
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
    }


def _validation_summary(validation: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_validation_type": validation.get("validation_type"),
        "source_status": validation.get("status"),
        "source_commit_allowed": validation.get("commit_allowed"),
        "dryrun_status": result["status"],
        "validated_candidate_count": len(validation.get("validated_runtime_commit_candidates", []))
        if isinstance(validation.get("validated_runtime_commit_candidates"), list)
        else 0,
        "dryrun_ready_count": result["summary"]["dryrun_ready_count"],
        "dryrun_blocked_count": result["summary"]["dryrun_blocked_count"],
        "dryrun_invalid_count": result["summary"]["dryrun_invalid_count"],
    }


def _execution_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "dry_run": True,
        "runtime_commit_executed": False,
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "action_count": len(result["commit_actions"]),
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["commit_allowed"] = result.get("status") == READY
    result["preview_only"] = True
    result["dry_run"] = True
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
    result["summary"]["runtime_commit_executed"] = False
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
