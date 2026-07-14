"""SELL runtime commit approval gate.

This module validates whether a dry-run result may be approved for a later
runtime commit step. It only builds an approval preview and never performs the
commit or any external side effect.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_APPROVAL_GATE"
SOURCE_DRYRUN_TYPE = "SELL_RUNTIME_COMMIT_DRYRUN_EXECUTOR"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Approval Gate"
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


def build_sell_runtime_commit_approval_gate(runtime_commit_dryrun: dict[str, Any]) -> dict[str, Any]:
    """Build a preview-only approval decision from a dry-run commit result."""
    result = _base_result(runtime_commit_dryrun)

    if not isinstance(runtime_commit_dryrun, dict):
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run input must be a dict")
        return _finish(result)

    result["runtime_commit_dryrun_snapshot"] = deepcopy(runtime_commit_dryrun)
    _extend_list(result["warnings"], runtime_commit_dryrun.get("warnings"))
    _extend_list(result["reasons"], runtime_commit_dryrun.get("reasons"))
    result["source_summary"] = deepcopy(runtime_commit_dryrun.get("summary")) if isinstance(runtime_commit_dryrun.get("summary"), dict) else {}

    if runtime_commit_dryrun.get("dryrun_type") != SOURCE_DRYRUN_TYPE:
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run type must be SELL_RUNTIME_COMMIT_DRYRUN_EXECUTOR")
        return _finish(result)

    if runtime_commit_dryrun.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run preview_only must be True")
        return _finish(result)

    if runtime_commit_dryrun.get("dry_run") is not True:
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run dry_run must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(runtime_commit_dryrun):
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run safety flag violation")
        return _finish(result)

    upstream_status = _status(runtime_commit_dryrun.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run upstream status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("runtime commit dry-run upstream status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run upstream status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if runtime_commit_dryrun.get("commit_allowed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("runtime commit dry-run commit_allowed must be True")
        return _finish(result)

    commit_actions = runtime_commit_dryrun.get("commit_actions")
    if not isinstance(commit_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run commit_actions must be a list")
        return _finish(result)
    if not commit_actions:
        result["status"] = BLOCKED
        result["reasons"].append("runtime commit dry-run commit_actions must not be empty")
        return _finish(result)

    blocked_commit_actions = runtime_commit_dryrun.get("blocked_commit_actions")
    if not isinstance(blocked_commit_actions, list):
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run blocked_commit_actions must be a list")
        return _finish(result)

    for index, action in enumerate(commit_actions):
        inspected = _inspect_commit_action(action, index)
        if inspected["status"] == READY:
            result["approved_commit_actions"].append(inspected["normalized_action"])
            result["summary"]["approval_ready_count"] += 1
        elif inspected["status"] == INVALID:
            result["blocked_approval_actions"].append(inspected)
            result["summary"]["approval_invalid_count"] += 1
        else:
            result["blocked_approval_actions"].append(inspected)
            result["summary"]["approval_blocked_count"] += 1
        _extend_list(result["warnings"], inspected.get("warnings"))
        _extend_list(result["reasons"], inspected.get("reasons"))

    if blocked_commit_actions:
        result["summary"]["approval_blocked_count"] += len(blocked_commit_actions)
        result["blocked_approval_actions"].extend(deepcopy(blocked_commit_actions))
        result["reasons"].append("runtime commit dry-run blocked_commit_actions must be empty")

    if not _summary_counts_match(runtime_commit_dryrun.get("summary"), commit_actions):
        result["status"] = INVALID
        result["reasons"].append("runtime commit dry-run summary count mismatch")
        return _finish(result)

    invalid_count = result["summary"]["approval_invalid_count"]
    blocked_count = result["summary"]["approval_blocked_count"]
    ready_count = result["summary"]["approval_ready_count"]

    if invalid_count > 0:
        result["status"] = INVALID
    elif blocked_count > 0:
        result["status"] = BLOCKED
    elif ready_count == len(commit_actions) and ready_count > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        result["reasons"].append("all runtime commit actions must be READY for approval")

    return _finish(result)


def _base_result(runtime_commit_dryrun: Any) -> dict[str, Any]:
    return {
        "approval_type": APPROVAL_TYPE,
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
        "status": BLOCKED,
        "approval_granted": False,
        "commit_allowed": False,
        "runtime_commit_dryrun_snapshot": deepcopy(runtime_commit_dryrun) if isinstance(runtime_commit_dryrun, dict) else {},
        "approved_commit_actions": [],
        "blocked_approval_actions": [],
        "source_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "approval_ready_count": 0,
            "approval_blocked_count": 0,
            "approval_invalid_count": 0,
            "approved_action_count": 0,
            "blocked_action_count": 0,
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


def _inspect_commit_action(action: Any, index: int) -> dict[str, Any]:
    inspected = {
        "status": BLOCKED,
        "action_index": index,
        "normalized_action": {},
        "warnings": [],
        "reasons": [],
    }
    if not isinstance(action, dict):
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action must be a dict")
        return inspected

    inspected["normalized_action"] = deepcopy(action)
    action_status = _status(action.get("status"))
    if action_status == INVALID:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action status is INVALID")
        return inspected
    if action_status == BLOCKED:
        inspected["status"] = BLOCKED
        inspected["reasons"].append("commit action status is BLOCKED")
        return inspected
    if action_status != READY:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action status must be READY, BLOCKED, or INVALID")
        return inspected

    if action.get("dryrun_action_ready") is not True:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action dryrun_action_ready must be True")
        return inspected

    if _has_forbidden_safety_flag(action):
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action safety flag violation")
        return inspected

    missing = [field for field in _IDENTITY_FIELDS if not _present(action.get(field))]
    if missing:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action identity field missing: " + ", ".join(missing))
        return inspected

    execution_request = action.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action execution_request must be a non-empty dict")
        return inspected

    queued_record = action.get("order_queued_record_preview")
    if not isinstance(queued_record, dict) or not queued_record:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action order_queued_record_preview must be a non-empty dict")
        return inspected

    mismatch = _identity_mismatches(action, execution_request, queued_record)
    if mismatch:
        inspected["status"] = INVALID
        inspected["reasons"].append("commit action identity mismatch: " + ", ".join(mismatch))
        return inspected

    inspected["status"] = READY
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


def _summary_counts_match(summary: Any, commit_actions: list[Any]) -> bool:
    if not isinstance(summary, dict):
        return False
    ready_count = sum(1 for action in commit_actions if isinstance(action, dict) and _status(action.get("status")) == READY)
    blocked_count = sum(1 for action in commit_actions if isinstance(action, dict) and _status(action.get("status")) == BLOCKED)
    invalid_count = sum(1 for action in commit_actions if not isinstance(action, dict) or _status(action.get("status")) == INVALID)
    return (
        summary.get("dryrun_ready_count") == ready_count
        and summary.get("dryrun_blocked_count") == blocked_count
        and summary.get("dryrun_invalid_count") == invalid_count
    )


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["approval_granted"] = result.get("status") == READY
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
    result["summary"]["approved_action_count"] = len(result["approved_commit_actions"])
    result["summary"]["blocked_action_count"] = len(result["blocked_approval_actions"])
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


approve_sell_runtime_commit = build_sell_runtime_commit_approval_gate
evaluate_sell_runtime_commit_approval_gate = build_sell_runtime_commit_approval_gate
