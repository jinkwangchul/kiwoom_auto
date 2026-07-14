"""SELL runtime commit real executor.

Consumes SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW and performs
the explicitly approved queue commit through execution_queue_writer. This is
the first SELL runtime layer that may commit the queue file, but it still never
sends orders, calls a broker API, creates OrderRequest objects, or mutates
REAL_READY state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_queue_writer import commit_execution_queue_write


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

EXECUTOR_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR"
SOURCE_APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Real Executor"
ROUTINE_DEPENDENCY = None
COMMIT_BOUNDARY_FUNCTION = "execution_queue_writer.commit_execution_queue_write"

_SAFETY_FLAGS = (
    "execution_connected",
    "send_order",
    "broker_api_called",
    "actual_order_sent",
    "order_request_created",
    "real_ready_state_changed",
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


def execute_sell_runtime_commit(real_executor_approval: dict[str, Any]) -> dict[str, Any]:
    """Execute exactly one approved SELL runtime queue commit."""
    result = _base_result(real_executor_approval)

    if not isinstance(real_executor_approval, dict):
        result["status"] = INVALID
        result["reasons"].append("real executor approval input must be a dict")
        return _finish(result)

    result["approval_snapshot"] = deepcopy(real_executor_approval)
    result["source_summary"] = deepcopy(real_executor_approval.get("summary")) if isinstance(real_executor_approval.get("summary"), dict) else {}
    _extend_list(result["warnings"], real_executor_approval.get("warnings"))
    _extend_list(result["reasons"], real_executor_approval.get("reasons"))

    if real_executor_approval.get("approval_type") != SOURCE_APPROVAL_TYPE:
        result["status"] = INVALID
        result["reasons"].append("real executor approval type must be SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW")
        return _finish(result)

    if real_executor_approval.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("real executor approval preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(real_executor_approval):
        result["status"] = INVALID
        result["reasons"].append("real executor approval safety flag violation")
        return _finish(result)

    approval_status = _status(real_executor_approval.get("status"))
    if approval_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("real executor approval status is INVALID")
        return _finish(result)
    if approval_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("real executor approval status is BLOCKED")
        return _finish(result)
    if approval_status != READY:
        result["status"] = INVALID
        result["reasons"].append("real executor approval status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if real_executor_approval.get("approval_granted") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("real executor approval_granted must be True")
        return _finish(result)

    if real_executor_approval.get("commit_allowed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("real executor commit_allowed must be True")
        return _finish(result)

    blocked_actions = real_executor_approval.get("blocked_approval_actions")
    if not isinstance(blocked_actions, list):
        result["status"] = INVALID
        result["reasons"].append("blocked_approval_actions must be a list")
        return _finish(result)
    if blocked_actions:
        result["status"] = INVALID
        result["reasons"].append("READY real executor approval must not contain blocked_approval_actions")
        return _finish(result)

    approved_actions = real_executor_approval.get("approved_real_executor_actions")
    if not isinstance(approved_actions, list):
        result["status"] = INVALID
        result["reasons"].append("approved_real_executor_actions must be a list")
        return _finish(result)
    if not approved_actions:
        result["status"] = INVALID
        result["reasons"].append("approved_real_executor_actions must not be empty")
        return _finish(result)
    if len(approved_actions) > 1:
        result["status"] = BLOCKED
        result["reasons"].append("multi-candidate real runtime commit requires atomic all-or-nothing support")
        return _finish(result)

    if not _ready_summary_counts_match(real_executor_approval.get("summary"), approved_actions):
        result["status"] = INVALID
        result["reasons"].append("real executor approval summary count mismatch")
        return _finish(result)

    action_check = _validate_approved_action(approved_actions[0])
    if action_check["status"] != READY:
        result["status"] = action_check["status"]
        result["blocked_execution_results"].append(action_check)
        _extend_list(result["reasons"], action_check.get("reasons"))
        return _finish(result)

    action = action_check["normalized_action"]
    payload = action["commit_payload"]
    args = payload["args"]
    kwargs = payload["kwargs"]
    commit_result = commit_execution_queue_write(
        deepcopy(args["queue_write_preview_result"]),
        args["queue_path"],
        backup=kwargs.get("backup", True),
        context=deepcopy(kwargs.get("context")),
    )

    execution_result = _normalize_commit_result(action, commit_result)
    result["execution_results"].append(execution_result)
    if execution_result["status"] == READY:
        result["summary"]["execution_ready_count"] += 1
        result["status"] = READY
    elif execution_result["status"] == INVALID:
        result["blocked_execution_results"].append(execution_result)
        result["summary"]["execution_invalid_count"] += 1
        result["status"] = INVALID
    else:
        result["blocked_execution_results"].append(execution_result)
        result["summary"]["execution_blocked_count"] += 1
        result["status"] = BLOCKED

    return _finish(result)


def _base_result(real_executor_approval: Any) -> dict[str, Any]:
    return {
        "executor_type": EXECUTOR_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "preview_only": False,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "status": BLOCKED,
        "commit_allowed": False,
        "approval_snapshot": deepcopy(real_executor_approval) if isinstance(real_executor_approval, dict) else {},
        "execution_results": [],
        "blocked_execution_results": [],
        "source_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "execution_ready_count": 0,
            "execution_blocked_count": 0,
            "execution_invalid_count": 0,
            "execution_result_count": 0,
            "blocked_execution_result_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _validate_approved_action(action: Any) -> dict[str, Any]:
    checked = {"status": BLOCKED, "normalized_action": {}, "reasons": [], "warnings": []}
    if not isinstance(action, dict):
        checked["status"] = INVALID
        checked["reasons"].append("approved action must be a dict")
        return checked

    if _status(action.get("status")) != READY:
        checked["status"] = INVALID if _status(action.get("status")) == INVALID else BLOCKED
        checked["reasons"].append("approved action status must be READY")
        return checked

    if action.get("approval_action") != "APPROVE_REAL_EXECUTOR_COMMIT_PREVIEW":
        checked["status"] = INVALID
        checked["reasons"].append("approved action approval_action mismatch")
        return checked

    if _has_forbidden_safety_flag(action):
        checked["status"] = INVALID
        checked["reasons"].append("approved action safety flag violation")
        return checked

    missing = [field for field in _IDENTITY_FIELDS if not _present(action.get(field))]
    if missing:
        checked["status"] = INVALID
        checked["reasons"].append("approved action identity field missing: " + ", ".join(missing))
        return checked

    if action.get("commit_boundary_function") != COMMIT_BOUNDARY_FUNCTION:
        checked["status"] = INVALID
        checked["reasons"].append("approved action commit boundary function mismatch")
        return checked

    if not _present(action.get("approval_token")):
        checked["status"] = INVALID
        checked["reasons"].append("approved action approval_token is required")
        return checked

    if not _present(action.get("queue_path")):
        checked["status"] = INVALID
        checked["reasons"].append("approved action queue_path is required")
        return checked

    payload = action.get("commit_payload")
    if not isinstance(payload, dict) or not payload:
        checked["status"] = INVALID
        checked["reasons"].append("approved action commit_payload must be a non-empty dict")
        return checked

    payload_errors = _payload_errors(action, payload)
    if payload_errors:
        checked["status"] = INVALID
        checked["reasons"].append("approved action commit payload mismatch: " + ", ".join(payload_errors))
        return checked

    checked["status"] = READY
    checked["normalized_action"] = deepcopy(action)
    return checked


def _payload_errors(action: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("function") != COMMIT_BOUNDARY_FUNCTION:
        errors.append("function")
    if payload.get("called") is not False:
        errors.append("called")
    if payload.get("queue_path_required") is not True:
        errors.append("queue_path_required")
    if payload.get("manual_queue_write_confirmation_required") is not True:
        errors.append("manual_queue_write_confirmation_required")

    args = payload.get("args")
    if not isinstance(args, dict):
        errors.append("args")
        return sorted(set(errors))
    if _clean_text(args.get("queue_path")) != _clean_text(action.get("queue_path")):
        errors.append("args.queue_path")

    queue_write_preview = args.get("queue_write_preview_result")
    if not isinstance(queue_write_preview, dict) or not queue_write_preview:
        errors.append("args.queue_write_preview_result")
        return sorted(set(errors))

    record = queue_write_preview.get("order_queued_record_preview")
    if not isinstance(record, dict) or not record:
        errors.append("args.queue_write_preview_result.order_queued_record_preview")
        return sorted(set(errors))

    for field in _IDENTITY_FIELDS:
        if record.get(field) != action.get(field):
            errors.append(f"record.{field}")

    execution_request = record.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        errors.append("record.execution_request")
    else:
        for field in ("execution_id", "request_hash", "lock_id"):
            if execution_request.get(field) != action.get(field):
                errors.append(f"record.execution_request.{field}")

    kwargs = payload.get("kwargs")
    if not isinstance(kwargs, dict):
        errors.append("kwargs")
        return sorted(set(errors))

    context = kwargs.get("context")
    if not isinstance(context, dict):
        errors.append("kwargs.context")
    else:
        if context.get("manual_queue_write_confirmed") is not True:
            errors.append("kwargs.context.manual_queue_write_confirmed")
        if _clean_text(context.get("approval_token")) != _clean_text(action.get("approval_token")):
            errors.append("kwargs.context.approval_token")

    return sorted(set(errors))


def _normalize_commit_result(action: dict[str, Any], commit_result: Any) -> dict[str, Any]:
    if not isinstance(commit_result, dict):
        return {
            "status": INVALID,
            "commit_result": {},
            "source_approved_action": deepcopy(action),
            "reasons": ["commit_execution_queue_write result must be a dict"],
        }

    status = READY if commit_result.get("committed") is True else BLOCKED
    reasons = deepcopy(commit_result.get("blocked_reasons")) if isinstance(commit_result.get("blocked_reasons"), list) else []
    if status == READY:
        mismatches = _commit_result_mismatches(action, commit_result)
        if mismatches:
            status = INVALID
            reasons.append("commit result identity mismatch: " + ", ".join(mismatches))

    return {
        "status": status,
        "commit_boundary_function": COMMIT_BOUNDARY_FUNCTION,
        "source_signal_id": action["source_signal_id"],
        "order_id": action["order_id"],
        "candidate_id": action["candidate_id"],
        "queue_pending_id": action["queue_pending_id"],
        "execution_id": action["execution_id"],
        "request_hash": action["request_hash"],
        "lock_id": action["lock_id"],
        "commit_result": deepcopy(commit_result),
        "source_approved_action": deepcopy(action),
        "runtime_write": status == READY,
        "queue_write": status == READY,
        "file_write": status == READY,
        "queue_committed": status == READY,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": status == READY,
        "reasons": reasons,
        "warnings": deepcopy(commit_result.get("warnings")) if isinstance(commit_result.get("warnings"), list) else [],
    }


def _commit_result_mismatches(action: dict[str, Any], commit_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if commit_result.get("order_id") != action.get("order_id"):
        mismatches.append("order_id")
    if commit_result.get("request_hash") != action.get("request_hash"):
        mismatches.append("request_hash")
    if commit_result.get("lock_id") != action.get("lock_id"):
        mismatches.append("lock_id")
    if commit_result.get("send_order_called") is not False:
        mismatches.append("send_order_called")
    if commit_result.get("execution_enabled") is not False:
        mismatches.append("execution_enabled")
    if commit_result.get("status") != "ORDER_QUEUED":
        mismatches.append("status")
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
    ready = result.get("status") == READY
    result["commit_allowed"] = ready
    result["execution_connected"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["summary"]["execution_result_count"] = len(result["execution_results"])
    result["summary"]["blocked_execution_result_count"] = len(result["blocked_execution_results"])
    result["summary"]["runtime_write"] = ready
    result["summary"]["queue_write"] = ready
    result["summary"]["file_write"] = ready
    result["summary"]["queue_committed"] = ready
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["actual_order_sent"] = False
    result["summary"]["order_request_created"] = False
    result["summary"]["real_ready_state_changed"] = False
    result["summary"]["runtime_commit_executed"] = ready
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    result["runtime_write"] = ready
    result["queue_write"] = ready
    result["file_write"] = ready
    result["queue_committed"] = ready
    result["runtime_commit_executed"] = ready
    return result


def _status(value: Any) -> str | None:
    return value if value in {READY, BLOCKED, INVALID} else None


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


execute_sell_runtime_commit_real = execute_sell_runtime_commit
run_sell_runtime_commit_real_executor = execute_sell_runtime_commit
