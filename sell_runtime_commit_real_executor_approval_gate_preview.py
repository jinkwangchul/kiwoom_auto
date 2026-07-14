"""SELL runtime commit real executor approval gate preview.

Validates a SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW result plus explicit
approval context immediately before a future real runtime/queue commit. This
module never calls the commit boundary and never writes runtime or queue state.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Real Executor Approval Gate Preview"
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


def build_sell_runtime_commit_real_executor_approval_gate_preview(
    real_executor_preview: dict[str, Any],
    approval_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a preview-only final approval gate for real executor actions."""
    result = _base_result(real_executor_preview, approval_context)

    if not isinstance(real_executor_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("real executor preview input must be a dict")
        return _finish(result)

    result["real_executor_preview_snapshot"] = deepcopy(real_executor_preview)
    result["source_summary"] = deepcopy(real_executor_preview.get("summary")) if isinstance(real_executor_preview.get("summary"), dict) else {}
    _extend_list(result["warnings"], real_executor_preview.get("warnings"))
    _extend_list(result["reasons"], real_executor_preview.get("reasons"))

    if real_executor_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("real executor preview type must be SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW")
        return _finish(result)

    if real_executor_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("real executor preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(real_executor_preview):
        result["status"] = INVALID
        result["reasons"].append("real executor preview safety flag violation")
        return _finish(result)

    upstream_status = _status(real_executor_preview.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("real executor preview status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("real executor preview status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("real executor preview status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if real_executor_preview.get("real_executor_preview_ready") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY real executor preview must have real_executor_preview_ready=True")
        return _finish(result)

    if real_executor_preview.get("commit_allowed") is not True:
        result["status"] = INVALID
        result["reasons"].append("READY real executor preview must have commit_allowed=True")
        return _finish(result)

    blocked_actions = real_executor_preview.get("blocked_real_executor_actions")
    if not isinstance(blocked_actions, list):
        result["status"] = INVALID
        result["reasons"].append("blocked_real_executor_actions must be a list")
        return _finish(result)
    if blocked_actions:
        result["status"] = INVALID
        result["reasons"].append("READY real executor preview must not contain blocked_real_executor_actions")
        return _finish(result)

    real_actions = real_executor_preview.get("real_executor_actions")
    if not isinstance(real_actions, list):
        result["status"] = INVALID
        result["reasons"].append("real_executor_actions must be a list")
        return _finish(result)
    if not real_actions:
        result["status"] = INVALID
        result["reasons"].append("READY real executor preview real_executor_actions must not be empty")
        return _finish(result)

    if not _ready_summary_counts_match(real_executor_preview.get("summary"), real_actions):
        result["status"] = INVALID
        result["reasons"].append("real executor preview summary count mismatch")
        return _finish(result)

    context_status, context_reasons = _validate_approval_context(approval_context)
    if context_status != READY:
        result["status"] = context_status
        result["reasons"].extend(context_reasons)
        return _finish(result)

    context = approval_context if isinstance(approval_context, dict) else {}
    approved_candidate_ids = list(context.get("approved_candidate_ids", []))
    actual_candidate_ids = [action.get("candidate_id") for action in real_actions if isinstance(action, dict)]
    if approved_candidate_ids != actual_candidate_ids:
        result["status"] = INVALID
        result["reasons"].append("approved_candidate_ids must exactly match all candidate_ids in order")
        return _finish(result)

    queue_path = _clean_text(context.get("queue_path"))
    approval_token = _clean_text(context.get("approval_token"))

    for index, action in enumerate(real_actions):
        inspected = _approve_real_executor_action(action, index, queue_path, approval_token)
        if inspected["status"] == READY:
            result["approved_real_executor_actions"].append(inspected["approval_action"])
            result["summary"]["approval_ready_count"] += 1
        elif inspected["status"] == INVALID:
            result["blocked_approval_actions"].append(inspected)
            result["summary"]["approval_invalid_count"] += 1
        else:
            result["blocked_approval_actions"].append(inspected)
            result["summary"]["approval_blocked_count"] += 1
        _extend_list(result["warnings"], inspected.get("warnings"))
        _extend_list(result["reasons"], inspected.get("reasons"))

    invalid_count = result["summary"]["approval_invalid_count"]
    blocked_count = result["summary"]["approval_blocked_count"]
    ready_count = result["summary"]["approval_ready_count"]

    if invalid_count > 0:
        result["status"] = INVALID
    elif blocked_count > 0:
        result["status"] = BLOCKED
    elif ready_count == len(real_actions) and ready_count > 0:
        result["status"] = READY
    else:
        result["status"] = INVALID
        result["reasons"].append("all real executor actions must be approved")

    return _finish(result)


def _base_result(real_executor_preview: Any, approval_context: Any) -> dict[str, Any]:
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
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "status": BLOCKED,
        "approval_granted": False,
        "commit_allowed": False,
        "real_executor_preview_snapshot": deepcopy(real_executor_preview) if isinstance(real_executor_preview, dict) else {},
        "approval_context_snapshot": deepcopy(approval_context) if isinstance(approval_context, dict) else {},
        "approved_real_executor_actions": [],
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
            "file_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


def _validate_approval_context(approval_context: Any) -> tuple[str, list[str]]:
    if approval_context is None:
        return BLOCKED, ["approval_context is required"]
    if not isinstance(approval_context, dict):
        return INVALID, ["approval_context must be a dict"]
    if approval_context.get("user_approved") is not True:
        return BLOCKED, ["approval_context.user_approved must be True"]
    if not _clean_text(approval_context.get("queue_path")):
        return BLOCKED, ["approval_context.queue_path is required"]
    if not _clean_text(approval_context.get("approval_token")):
        return BLOCKED, ["approval_context.approval_token is required"]
    approved_candidate_ids = approval_context.get("approved_candidate_ids")
    if not isinstance(approved_candidate_ids, list):
        return INVALID, ["approval_context.approved_candidate_ids must be a list"]
    if not approved_candidate_ids:
        return BLOCKED, ["approval_context.approved_candidate_ids must not be empty"]
    if not all(isinstance(item, str) and item.strip() for item in approved_candidate_ids):
        return INVALID, ["approval_context.approved_candidate_ids must contain non-empty strings"]
    return READY, []


def _approve_real_executor_action(
    action: Any,
    index: int,
    queue_path: str,
    approval_token: str,
) -> dict[str, Any]:
    inspected = {
        "status": BLOCKED,
        "action_index": index,
        "approval_action": {},
        "warnings": [],
        "reasons": [],
    }
    if not isinstance(action, dict):
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action must be a dict")
        return inspected

    action_status = _status(action.get("status"))
    if action_status == INVALID:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action status is INVALID")
        return inspected
    if action_status == BLOCKED:
        inspected["status"] = BLOCKED
        inspected["reasons"].append("real executor action status is BLOCKED")
        return inspected
    if action_status != READY:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action status must be READY, BLOCKED, or INVALID")
        return inspected

    if _has_forbidden_safety_flag(action):
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action safety flag violation")
        return inspected

    missing = [field for field in _IDENTITY_FIELDS if not _present(action.get(field))]
    if missing:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action identity field missing: " + ", ".join(missing))
        return inspected

    if action.get("commit_boundary_function") != COMMIT_BOUNDARY_FUNCTION:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action commit boundary function mismatch")
        return inspected

    if action.get("commit_boundary_called") is not False:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action commit_boundary_called must be False")
        return inspected

    if action.get("function_called") is not False:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action function_called must be False")
        return inspected

    commit_payload = action.get("commit_payload")
    if not isinstance(commit_payload, dict) or not commit_payload:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action commit_payload must be a non-empty dict")
        return inspected

    payload_mismatches = _commit_payload_mismatches(action, commit_payload)
    if payload_mismatches:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action commit payload mismatch: " + ", ".join(payload_mismatches))
        return inspected

    execution_request = action.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action execution_request must be a non-empty dict")
        return inspected

    queued_record = action.get("order_queued_record_preview")
    if not isinstance(queued_record, dict) or not queued_record:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action order_queued_record_preview must be a non-empty dict")
        return inspected

    identity_mismatches = _identity_mismatches(action, execution_request, queued_record)
    if identity_mismatches:
        inspected["status"] = INVALID
        inspected["reasons"].append("real executor action identity mismatch: " + ", ".join(identity_mismatches))
        return inspected

    inspected["status"] = READY
    inspected["approval_action"] = {
        "status": READY,
        "approval_action": "APPROVE_REAL_EXECUTOR_COMMIT_PREVIEW",
        "source_signal_id": action["source_signal_id"],
        "order_id": action["order_id"],
        "candidate_id": action["candidate_id"],
        "queue_pending_id": action["queue_pending_id"],
        "execution_id": action["execution_id"],
        "request_hash": action["request_hash"],
        "lock_id": action["lock_id"],
        "commit_boundary_function": COMMIT_BOUNDARY_FUNCTION,
        "commit_payload": _approved_commit_payload(commit_payload, queue_path, approval_token),
        "approval_token": approval_token,
        "queue_path": queue_path,
        "source_real_executor_action": deepcopy(action),
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


def _approved_commit_payload(commit_payload: dict[str, Any], queue_path: str, approval_token: str) -> dict[str, Any]:
    approved = deepcopy(commit_payload)
    approved.setdefault("args", {})
    approved.setdefault("kwargs", {})
    approved["args"]["queue_path"] = queue_path
    approved["kwargs"]["context"] = {
        "manual_queue_write_confirmed": True,
        "approval_token": approval_token,
    }
    approved["called"] = False
    approved["queue_path_required"] = True
    approved["manual_queue_write_confirmation_required"] = True
    return approved


def _commit_payload_mismatches(action: dict[str, Any], commit_payload: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if commit_payload.get("function") != COMMIT_BOUNDARY_FUNCTION:
        mismatches.append("function")
    if commit_payload.get("called") is not False:
        mismatches.append("called")
    if commit_payload.get("queue_path_required") is not True:
        mismatches.append("queue_path_required")
    if commit_payload.get("manual_queue_write_confirmation_required") is not True:
        mismatches.append("manual_queue_write_confirmation_required")

    args = commit_payload.get("args")
    if not isinstance(args, dict):
        mismatches.append("args")
        return sorted(set(mismatches))

    if args.get("queue_path") is not None:
        mismatches.append("args.queue_path")

    queue_write_preview = args.get("queue_write_preview_result")
    if not isinstance(queue_write_preview, dict) or not queue_write_preview:
        mismatches.append("args.queue_write_preview_result")
        return sorted(set(mismatches))

    record = queue_write_preview.get("order_queued_record_preview")
    if not isinstance(record, dict) or not record:
        mismatches.append("args.queue_write_preview_result.order_queued_record_preview")
        return sorted(set(mismatches))

    for field in _IDENTITY_FIELDS:
        if record.get(field) != action.get(field):
            mismatches.append(f"payload.record.{field}")

    return sorted(set(mismatches))


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


def _ready_summary_counts_match(summary: Any, real_actions: list[Any]) -> bool:
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("real_executor_ready_count") == len(real_actions)
        and summary.get("real_executor_blocked_count") == 0
        and summary.get("real_executor_invalid_count") == 0
        and summary.get("real_executor_action_count") == len(real_actions)
        and summary.get("blocked_real_executor_action_count") == 0
    )


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["approval_granted"] = result.get("status") == READY
    result["commit_allowed"] = result.get("status") == READY
    result["preview_only"] = True
    result["execution_connected"] = False
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["queue_committed"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["runtime_commit_executed"] = False
    result["summary"]["approved_action_count"] = len(result["approved_real_executor_actions"])
    result["summary"]["blocked_action_count"] = len(result["blocked_approval_actions"])
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


def _clean_text(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return ""
    return str(value).strip()


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


approve_sell_runtime_commit_real_executor_preview = build_sell_runtime_commit_real_executor_approval_gate_preview
build_sell_runtime_real_executor_approval_gate_preview = build_sell_runtime_commit_real_executor_approval_gate_preview
