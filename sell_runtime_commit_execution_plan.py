"""SELL runtime commit execution plan.

Builds a final preview-only plan from an approval gate result. The plan is the
last structured step before a later runtime commit layer, but this module never
performs that commit or any external side effect.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PLAN_TYPE = "SELL_RUNTIME_COMMIT_EXECUTION_PLAN"
SOURCE_APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_APPROVAL_GATE"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Execution Plan"
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


def build_sell_runtime_commit_execution_plan(runtime_commit_approval: dict[str, Any]) -> dict[str, Any]:
    """Build a final preview-only execution plan from approval gate output."""
    result = _base_result(runtime_commit_approval)

    if not isinstance(runtime_commit_approval, dict):
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval input must be a dict")
        return _finish(result)

    result["approval_snapshot"] = deepcopy(runtime_commit_approval)
    _extend_list(result["warnings"], runtime_commit_approval.get("warnings"))
    _extend_list(result["reasons"], runtime_commit_approval.get("reasons"))
    result["source_summary"] = deepcopy(runtime_commit_approval.get("summary")) if isinstance(runtime_commit_approval.get("summary"), dict) else {}

    if runtime_commit_approval.get("approval_type") != SOURCE_APPROVAL_TYPE:
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval type must be SELL_RUNTIME_COMMIT_APPROVAL_GATE")
        return _finish(result)

    if runtime_commit_approval.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(runtime_commit_approval):
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval safety flag violation")
        return _finish(result)

    approval_status = _status(runtime_commit_approval.get("status"))
    if approval_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval status is INVALID")
        return _finish(result)
    if approval_status == BLOCKED:
        if runtime_commit_approval.get("approval_granted") is False and runtime_commit_approval.get("commit_allowed") is False:
            result["status"] = BLOCKED
            result["reasons"].append("runtime commit approval status is BLOCKED")
            return _finish(result)
        result["status"] = INVALID
        result["reasons"].append("blocked runtime commit approval must not grant commit")
        return _finish(result)
    if approval_status != READY:
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if runtime_commit_approval.get("approval_granted") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit approval must have approval_granted=True")
        return _finish(result)

    if runtime_commit_approval.get("commit_allowed") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit approval must have commit_allowed=True")
        return _finish(result)

    approved_actions = runtime_commit_approval.get("approved_commit_actions")
    if not isinstance(approved_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval approved_commit_actions must be a list")
        return _finish(result)
    if not approved_actions:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit approval approved_commit_actions must not be empty")
        return _finish(result)

    blocked_actions = runtime_commit_approval.get("blocked_approval_actions")
    if not isinstance(blocked_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval blocked_approval_actions must be a list")
        return _finish(result)
    if blocked_actions:
        result["status"] = INVALID
        result["reasons"].append("READY runtime commit approval must not contain blocked_approval_actions")
        return _finish(result)

    if not _ready_summary_counts_match(runtime_commit_approval.get("summary"), approved_actions):
        result["status"] = INVALID
        result["reasons"].append("runtime commit approval summary count mismatch")
        return _finish(result)

    for index, action in enumerate(approved_actions):
        inspected = _build_execution_action(action, index)
        if inspected["status"] == READY:
            result["execution_actions"].append(inspected["plan_action"])
            result["summary"]["execution_ready_count"] += 1
        elif inspected["status"] == INVALID:
            result["blocked_execution_actions"].append(inspected)
            result["summary"]["execution_invalid_count"] += 1
        else:
            result["blocked_execution_actions"].append(inspected)
            result["summary"]["execution_blocked_count"] += 1
        _extend_list(result["warnings"], inspected.get("warnings"))
        _extend_list(result["reasons"], inspected.get("reasons"))

    invalid_count = result["summary"]["execution_invalid_count"]
    blocked_count = result["summary"]["execution_blocked_count"]
    ready_count = result["summary"]["execution_ready_count"]

    if invalid_count > 0:
        result["status"] = INVALID
    elif blocked_count > 0:
        result["status"] = INVALID
    elif ready_count == len(approved_actions) and ready_count > 0:
        result["status"] = READY
    else:
        result["status"] = INVALID
        result["reasons"].append("all approved runtime commit actions must produce execution actions")

    return _finish(result)


def _base_result(runtime_commit_approval: Any) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "preview_only": True,
        "plan_only": True,
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
        "status": BLOCKED,
        "execution_plan_ready": False,
        "commit_allowed": False,
        "approval_snapshot": deepcopy(runtime_commit_approval) if isinstance(runtime_commit_approval, dict) else {},
        "execution_actions": [],
        "blocked_execution_actions": [],
        "source_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "execution_ready_count": 0,
            "execution_blocked_count": 0,
            "execution_invalid_count": 0,
            "execution_action_count": 0,
            "blocked_execution_action_count": 0,
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


def _build_execution_action(action: Any, index: int) -> dict[str, Any]:
    inspected = {
        "status": BLOCKED,
        "action_index": index,
        "plan_action": {},
        "warnings": [],
        "reasons": [],
    }
    if not isinstance(action, dict):
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action must be a dict")
        return inspected

    action_status = _status(action.get("status"))
    if action_status == INVALID:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action status is INVALID")
        return inspected
    if action_status == BLOCKED:
        inspected["status"] = BLOCKED
        inspected["reasons"].append("approved commit action status is BLOCKED")
        return inspected
    if action_status != READY:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action status must be READY, BLOCKED, or INVALID")
        return inspected

    if action.get("dryrun_action_ready") is not True:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action dryrun_action_ready must be True")
        return inspected

    if _has_forbidden_safety_flag(action):
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action safety flag violation")
        return inspected

    missing = [field for field in _IDENTITY_FIELDS if not _present(action.get(field))]
    if missing:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action identity field missing: " + ", ".join(missing))
        return inspected

    execution_request = action.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action execution_request must be a non-empty dict")
        return inspected

    queued_record = action.get("order_queued_record_preview")
    if not isinstance(queued_record, dict) or not queued_record:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action order_queued_record_preview must be a non-empty dict")
        return inspected

    mismatches = _identity_mismatches(action, execution_request, queued_record)
    if mismatches:
        inspected["status"] = INVALID
        inspected["reasons"].append("approved commit action identity mismatch: " + ", ".join(mismatches))
        return inspected

    inspected["status"] = READY
    inspected["plan_action"] = {
        "status": READY,
        "plan_action": "RUNTIME_COMMIT",
        "source_signal_id": action["source_signal_id"],
        "order_id": action["order_id"],
        "candidate_id": action["candidate_id"],
        "queue_pending_id": action["queue_pending_id"],
        "execution_id": action["execution_id"],
        "request_hash": action["request_hash"],
        "lock_id": action["lock_id"],
        "execution_request": deepcopy(execution_request),
        "order_queued_record_preview": deepcopy(queued_record),
        "source_approval_action": deepcopy(action),
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
    }
    return inspected


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


def _ready_summary_counts_match(summary: Any, approved_actions: list[Any]) -> bool:
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("approval_ready_count") == len(approved_actions)
        and summary.get("approval_blocked_count") == 0
        and summary.get("approval_invalid_count") == 0
        and summary.get("approved_action_count") == len(approved_actions)
        and summary.get("blocked_action_count") == 0
    )


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["execution_plan_ready"] = result.get("status") == READY
    result["commit_allowed"] = result.get("status") == READY
    result["preview_only"] = True
    result["plan_only"] = True
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
    result["summary"]["execution_action_count"] = len(result["execution_actions"])
    result["summary"]["blocked_execution_action_count"] = len(result["blocked_execution_actions"])
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
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


build_sell_runtime_execution_plan = build_sell_runtime_commit_execution_plan
plan_sell_runtime_commit_execution = build_sell_runtime_commit_execution_plan
