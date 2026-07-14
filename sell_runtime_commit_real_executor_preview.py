"""SELL runtime commit real executor preview.

This module is the preview-only boundary immediately before the real runtime
commit executor. It validates a SELL_RUNTIME_COMMIT_EXECUTION_PLAN and builds
the exact commit function/payload preview for a later step, but never calls the
commit function and never writes runtime or queue state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW"
SOURCE_PLAN_TYPE = "SELL_RUNTIME_COMMIT_EXECUTION_PLAN"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Real Executor Preview"
ROUTINE_DEPENDENCY = None
COMMIT_BOUNDARY_FUNCTION = "execution_queue_writer.commit_execution_queue_write"

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "queue_committed",
    "send_order",
    "broker_api_called",
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


def build_sell_runtime_commit_real_executor_preview(runtime_commit_execution_plan: dict[str, Any]) -> dict[str, Any]:
    """Build a preview of the real runtime commit executor call."""
    result = _base_result(runtime_commit_execution_plan)

    if not isinstance(runtime_commit_execution_plan, dict):
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan input must be a dict")
        return _finish(result)

    result["execution_plan_snapshot"] = deepcopy(runtime_commit_execution_plan)
    result["source_summary"] = deepcopy(runtime_commit_execution_plan.get("summary")) if isinstance(runtime_commit_execution_plan.get("summary"), dict) else {}
    _extend_list(result["warnings"], runtime_commit_execution_plan.get("warnings"))
    _extend_list(result["reasons"], runtime_commit_execution_plan.get("reasons"))

    if runtime_commit_execution_plan.get("plan_type") != SOURCE_PLAN_TYPE:
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan type must be SELL_RUNTIME_COMMIT_EXECUTION_PLAN")
        return _finish(result)

    if runtime_commit_execution_plan.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(runtime_commit_execution_plan):
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan safety flag violation")
        return _finish(result)

    plan_status = _status(runtime_commit_execution_plan.get("status"))
    if plan_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan status is INVALID")
        return _finish(result)
    if plan_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("runtime commit execution plan status is BLOCKED")
        return _finish(result)
    if plan_status != READY:
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if runtime_commit_execution_plan.get("commit_allowed") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit execution plan must have commit_allowed=True")
        return _finish(result)

    if runtime_commit_execution_plan.get("execution_plan_ready") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit execution plan must have execution_plan_ready=True")
        return _finish(result)

    blocked_execution_actions = runtime_commit_execution_plan.get("blocked_execution_actions")
    if not isinstance(blocked_execution_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan blocked_execution_actions must be a list")
        return _finish(result)
    if blocked_execution_actions:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit execution plan must not contain blocked_execution_actions")
        return _finish(result)

    execution_actions = runtime_commit_execution_plan.get("execution_actions")
    if not isinstance(execution_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan execution_actions must be a list")
        return _finish(result)
    if not execution_actions:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit execution plan execution_actions must not be empty")
        return _finish(result)

    if not _ready_summary_counts_match(runtime_commit_execution_plan.get("summary"), execution_actions):
        result["status"] = INVALID
        result["reasons"].append("runtime commit execution plan summary count mismatch")
        return _finish(result)

    for index, action in enumerate(execution_actions):
        inspected = _build_real_executor_action(action, index)
        if inspected["status"] == READY:
            result["real_executor_actions"].append(inspected["real_executor_action"])
            result["summary"]["real_executor_ready_count"] += 1
        elif inspected["status"] == INVALID:
            result["blocked_real_executor_actions"].append(inspected)
            result["summary"]["real_executor_invalid_count"] += 1
        else:
            result["blocked_real_executor_actions"].append(inspected)
            result["summary"]["real_executor_blocked_count"] += 1
        _extend_list(result["warnings"], inspected.get("warnings"))
        _extend_list(result["reasons"], inspected.get("reasons"))

    invalid_count = result["summary"]["real_executor_invalid_count"]
    blocked_count = result["summary"]["real_executor_blocked_count"]
    ready_count = result["summary"]["real_executor_ready_count"]

    if invalid_count > 0:
        result["status"] = INVALID
    elif blocked_count > 0:
        result["status"] = INVALID
    elif ready_count == len(execution_actions) and ready_count > 0:
        result["status"] = READY
    else:
        result["status"] = INVALID
        result["reasons"].append("all execution plan actions must produce real executor previews")

    return _finish(result)


def _base_result(runtime_commit_execution_plan: Any) -> dict[str, Any]:
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
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "status": BLOCKED,
        "real_executor_preview_ready": False,
        "commit_allowed": False,
        "commit_boundary": {
            "function": COMMIT_BOUNDARY_FUNCTION,
            "called": False,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
        },
        "execution_plan_snapshot": deepcopy(runtime_commit_execution_plan) if isinstance(runtime_commit_execution_plan, dict) else {},
        "real_executor_actions": [],
        "blocked_real_executor_actions": [],
        "source_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "real_executor_ready_count": 0,
            "real_executor_blocked_count": 0,
            "real_executor_invalid_count": 0,
            "real_executor_action_count": 0,
            "blocked_real_executor_action_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _build_real_executor_action(action: Any, index: int) -> dict[str, Any]:
    inspected = {
        "status": BLOCKED,
        "action_index": index,
        "real_executor_action": {},
        "warnings": [],
        "reasons": [],
    }
    if not isinstance(action, dict):
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action must be a dict")
        return inspected

    action_status = _status(action.get("status"))
    if action_status == INVALID:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action status is INVALID")
        return inspected
    if action_status == BLOCKED:
        inspected["status"] = BLOCKED
        inspected["reasons"].append("execution plan action status is BLOCKED")
        return inspected
    if action_status != READY:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action status must be READY, BLOCKED, or INVALID")
        return inspected

    if action.get("plan_action") != "RUNTIME_COMMIT":
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action plan_action must be RUNTIME_COMMIT")
        return inspected

    if _has_forbidden_safety_flag(action):
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action safety flag violation")
        return inspected

    missing = [field for field in _IDENTITY_FIELDS if not _present(action.get(field))]
    if missing:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action identity field missing: " + ", ".join(missing))
        return inspected

    execution_request = action.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action execution_request must be a non-empty dict")
        return inspected

    queued_record = action.get("order_queued_record_preview")
    if not isinstance(queued_record, dict) or not queued_record:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action order_queued_record_preview must be a non-empty dict")
        return inspected

    mismatches = _identity_mismatches(action, execution_request, queued_record)
    if mismatches:
        inspected["status"] = INVALID
        inspected["reasons"].append("execution plan action identity mismatch: " + ", ".join(mismatches))
        return inspected

    queue_write_preview = _queue_write_preview_payload(queued_record)
    inspected["status"] = READY
    inspected["real_executor_action"] = {
        "status": READY,
        "executor_action": "PREVIEW_COMMIT_EXECUTION_QUEUE_WRITE",
        "commit_boundary_function": COMMIT_BOUNDARY_FUNCTION,
        "commit_boundary_called": False,
        "function_called": False,
        "source_signal_id": action["source_signal_id"],
        "order_id": action["order_id"],
        "candidate_id": action["candidate_id"],
        "queue_pending_id": action["queue_pending_id"],
        "execution_id": action["execution_id"],
        "request_hash": action["request_hash"],
        "lock_id": action["lock_id"],
        "execution_request": deepcopy(execution_request),
        "order_queued_record_preview": deepcopy(queued_record),
        "commit_payload": {
            "function": COMMIT_BOUNDARY_FUNCTION,
            "args": {
                "queue_write_preview_result": queue_write_preview,
                "queue_path": None,
            },
            "kwargs": {
                "backup": True,
                "context": None,
            },
            "queue_path_required": True,
            "manual_queue_write_confirmation_required": True,
            "called": False,
        },
        "source_execution_action": deepcopy(action),
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
    }
    return inspected


def _queue_write_preview_payload(queued_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "write_preview": True,
        "write_stage": "order_queued_record_preview_created",
        "next_stage": "QUEUE_WRITE_REQUIRED",
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "order_queued_record_preview": deepcopy(queued_record),
    }


def _identity_mismatches(action: dict[str, Any], execution_request: dict[str, Any], queued_record: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for field in _IDENTITY_FIELDS:
        if queued_record.get(field) != action.get(field):
            mismatches.append(f"record.{field}")

    for field in ("execution_id", "request_hash", "lock_id"):
        if execution_request.get(field) != action.get(field):
            mismatches.append(f"execution_request.{field}")
        if queued_record.get(field) != execution_request.get(field):
            mismatches.append(f"record.execution_request.{field}")

    record_request = queued_record.get("execution_request")
    if not isinstance(record_request, dict) or not record_request:
        mismatches.append("record.execution_request")
    else:
        for field in ("execution_id", "request_hash", "lock_id"):
            if record_request.get(field) != action.get(field):
                mismatches.append(f"record.execution_request.{field}")

    return sorted(set(mismatches))


def _ready_summary_counts_match(summary: Any, execution_actions: list[Any]) -> bool:
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("execution_ready_count") == len(execution_actions)
        and summary.get("execution_blocked_count") == 0
        and summary.get("execution_invalid_count") == 0
        and summary.get("execution_action_count") == len(execution_actions)
        and summary.get("blocked_execution_action_count") == 0
    )


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["real_executor_preview_ready"] = result.get("status") == READY
    result["commit_allowed"] = result.get("status") == READY
    result["preview_only"] = True
    result["execution_connected"] = False
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["queue_committed"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["runtime_commit_executed"] = False
    result["commit_boundary"]["function"] = COMMIT_BOUNDARY_FUNCTION
    result["commit_boundary"]["called"] = False
    result["commit_boundary"]["runtime_write"] = False
    result["commit_boundary"]["queue_write"] = False
    result["commit_boundary"]["queue_committed"] = False
    result["summary"]["real_executor_action_count"] = len(result["real_executor_actions"])
    result["summary"]["blocked_real_executor_action_count"] = len(result["blocked_real_executor_actions"])
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["file_write"] = False
    result["summary"]["queue_committed"] = False
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["runtime_commit_executed"] = False
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    return result


def _status(value: Any) -> str | None:
    return value if value in {READY, BLOCKED, INVALID} else None


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


build_sell_runtime_real_executor_preview = build_sell_runtime_commit_real_executor_preview
preview_sell_runtime_commit_real_executor = build_sell_runtime_commit_real_executor_preview
