# -*- coding: utf-8 -*-
"""SELL dispatch executor boundary previews.

The chain turns a SELL dispatch execution audit preview into an executor plan,
an approval boundary, a dry-run executor result, and a post-execution
verification preview. It never calls SendOrder or mutates queue/runtime state.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import hashlib
from typing import Any

from execution_queue_committed_review import review_execution_queue_committed


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

AUDIT_TYPE = "SELL_DISPATCH_EXECUTION_AUDIT_PREVIEW"
PLAN_TYPE = "SELL_DISPATCH_EXECUTOR_PLAN"
APPROVAL_TYPE = "SELL_DISPATCH_EXECUTOR_APPROVAL_BOUNDARY"
DRYRUN_TYPE = "SELL_DISPATCH_DRY_RUN_EXECUTOR"
POST_VERIFY_TYPE = "SELL_DISPATCH_POST_EXECUTION_VERIFICATION_PREVIEW"

IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
)
SAFETY_FALSE_FIELDS = (
    "send_order_called",
    "broker_api_called",
    "actual_order_sent",
    "order_request_created",
    "queue_write",
    "runtime_write",
    "queue_status_changed",
    "execution_started",
    "partial_execution",
    "real_ready_state_changed",
)


def build_sell_dispatch_executor_plan(audit_preview: Any) -> dict[str, Any]:
    result = _base_result(PLAN_TYPE, "plan_type", audit_preview)
    result.update(
        {
            "executor_plan_ready": False,
            "plan_hash": "",
            "queue_path": None,
            "masked_account_no": "",
            "send_order_call_preview_hash": "",
            "execution_actions": [],
            "candidate_ids": [],
            "expected_result": "DRY_RUN_ONLY",
            "failure_policy": "STOP_ALL_ON_FIRST_FAILURE",
            "partial_dispatch_allowed": False,
            "source_audit_snapshot": deepcopy(audit_preview) if isinstance(audit_preview, dict) else None,
        }
    )
    if not isinstance(audit_preview, dict):
        return _finish(result, INVALID, "audit_preview must be a dict")
    if audit_preview.get("audit_type") != AUDIT_TYPE:
        return _finish(result, INVALID, "audit_type is not SELL_DISPATCH_EXECUTION_AUDIT_PREVIEW")
    if _has_safety_violation(audit_preview):
        return _finish(result, INVALID, "audit_preview safety flag violation")
    if _status(audit_preview.get("status")) != READY:
        return _finish(result, BLOCKED if _status(audit_preview.get("status")) == BLOCKED else INVALID, "audit_preview is not READY")
    if audit_preview.get("audit_preview_ready") is not True:
        return _finish(result, INVALID, "audit_preview_ready must be True")
    if audit_preview.get("execution_not_started") is not True:
        return _finish(result, INVALID, "execution_not_started must be True")

    call_snapshot = _as_dict(audit_preview.get("source_call_preview_snapshot"))
    calls = _as_list(call_snapshot.get("call_previews"))
    if not calls:
        return _finish(result, INVALID, "source call_previews must be non-empty")
    if audit_preview.get("expected_dispatch_count") != len(calls):
        return _finish(result, INVALID, "expected_dispatch_count mismatch")
    if audit_preview.get("send_order_call_preview_hash") != _stable_hash(call_snapshot):
        return _finish(result, INVALID, "send_order_call_preview_hash mismatch")

    actions: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            return _finish(result, INVALID, "call preview item must be a dict")
        if call.get("function_boundary") != "kiwoom.SendOrder":
            return _finish(result, INVALID, "function_boundary must be kiwoom.SendOrder")
        args = call.get("send_order_args")
        if not isinstance(args, list) or len(args) != 9:
            return _finish(result, INVALID, "send_order_args must contain 9 values")
        actions.append(
            {
                "sequence": index + 1,
                "plan_action": "SEND_ORDER_DRY_RUN",
                "function_boundary": "kiwoom.SendOrder",
                "send_order_args_snapshot": deepcopy(args),
                "call_preview": deepcopy(call),
                "identity": _identity_from(call),
                "candidate_id": _text(call.get("candidate_id")),
                "send_order_called": False,
            }
        )
    if [_identity_from(call) for call in calls] != audit_preview.get("candidate_identities"):
        return _finish(result, INVALID, "candidate identity order mismatch")

    result["status"] = READY
    result["executor_plan_ready"] = True
    result["queue_path"] = audit_preview.get("queue_path")
    result["masked_account_no"] = audit_preview.get("masked_account_no")
    result["send_order_call_preview_hash"] = audit_preview.get("send_order_call_preview_hash")
    result["execution_actions"] = actions
    result["candidate_ids"] = [action["candidate_id"] for action in actions]
    result["summary"] = {
        "expected_dispatch_count": len(actions),
        "execution_action_count": len(actions),
        "partial_dispatch_allowed": False,
    }
    result["plan_hash"] = _stable_hash(
        {
            "queue_path": result["queue_path"],
            "masked_account_no": result["masked_account_no"],
            "send_order_call_preview_hash": result["send_order_call_preview_hash"],
            "actions": actions,
        }
    )
    return _finalize(result)


def build_sell_dispatch_executor_approval_boundary(executor_plan: Any, approval_context: Any = None) -> dict[str, Any]:
    result = _base_result(APPROVAL_TYPE, "approval_type", executor_plan)
    result.update(
        {
            "execution_allowed": False,
            "approval_token_present": False,
            "approval_token_hash": "",
            "plan_hash": "",
            "queue_path": None,
            "approved_candidate_ids": [],
            "approved_execution_actions": [],
            "source_plan_snapshot": deepcopy(executor_plan) if isinstance(executor_plan, dict) else None,
        }
    )
    context = _as_dict(approval_context)
    if not isinstance(executor_plan, dict):
        return _finish(result, INVALID, "executor_plan must be a dict")
    if executor_plan.get("plan_type") != PLAN_TYPE:
        return _finish(result, INVALID, "plan_type is not SELL_DISPATCH_EXECUTOR_PLAN")
    if _has_safety_violation(executor_plan):
        return _finish(result, INVALID, "executor_plan safety flag violation")
    if _status(executor_plan.get("status")) != READY:
        return _finish(result, BLOCKED if _status(executor_plan.get("status")) == BLOCKED else INVALID, "executor_plan is not READY")
    if executor_plan.get("executor_plan_ready") is not True:
        return _finish(result, INVALID, "executor_plan_ready must be True")
    if context.get("user_approved") is not True:
        return _finish(result, BLOCKED, "user approval is required")

    token = _text(context.get("approval_token"))
    if not token:
        return _finish(result, BLOCKED, "approval_token is required")
    actions = _as_list(executor_plan.get("execution_actions"))
    expected_ids = [_text(action.get("candidate_id")) for action in actions if isinstance(action, dict)]
    if context.get("approved_candidate_ids") != expected_ids:
        return _finish(result, INVALID, "approved_candidate_ids must match all candidates in order")
    if context.get("plan_hash") != executor_plan.get("plan_hash"):
        return _finish(result, INVALID, "plan_hash mismatch")
    if _text(context.get("queue_path")) != _text(executor_plan.get("queue_path")):
        return _finish(result, INVALID, "queue_path mismatch")
    if _mask_account(_text(context.get("account_no"))) != _text(executor_plan.get("masked_account_no")):
        return _finish(result, INVALID, "account mismatch")

    queue_data, queue_error = _read_queue(_text(executor_plan.get("queue_path")))
    if queue_error:
        return _finish(result, INVALID, queue_error)
    orders = _as_list(queue_data.get("orders"))
    for action in actions:
        identity = _as_dict(action.get("identity"))
        matches = [_as_dict(order) for order in orders if _identity_matches(_as_dict(order), identity)]
        if len(matches) != 1:
            return _finish(result, BLOCKED, f"queue matching record count is {len(matches)} for {identity.get('order_id')}")
        queue_review = review_execution_queue_committed(_queue_commit_result(matches[0]))
        if _status(queue_review.get("status")) != "READY_FOR_FINAL_SEND_GATE":
            return _finish(result, BLOCKED, "queue record is not ORDER_QUEUED-ready")

    result["status"] = READY
    result["execution_allowed"] = True
    result["approval_token_present"] = True
    result["approval_token_hash"] = _sha256_text(token)
    result["plan_hash"] = executor_plan.get("plan_hash")
    result["queue_path"] = executor_plan.get("queue_path")
    result["approved_candidate_ids"] = list(expected_ids)
    result["approved_execution_actions"] = deepcopy(actions)
    result["summary"] = {"approved_count": len(actions), "partial_dispatch_allowed": False}
    return _finalize(result)


def build_sell_dispatch_dry_run_executor(approval_boundary: Any) -> dict[str, Any]:
    result = _base_result(DRYRUN_TYPE, "executor_type", approval_boundary)
    result.update(
        {
            "dry_run_ready": False,
            "simulated_dispatch_count": 0,
            "simulated_candidate_ids": [],
            "per_candidate_results": [],
            "blocked_candidate_results": [],
            "source_approval_boundary_snapshot": deepcopy(approval_boundary) if isinstance(approval_boundary, dict) else None,
        }
    )
    if not isinstance(approval_boundary, dict):
        return _finish(result, INVALID, "approval_boundary must be a dict")
    if approval_boundary.get("approval_type") != APPROVAL_TYPE:
        return _finish(result, INVALID, "approval_type is not SELL_DISPATCH_EXECUTOR_APPROVAL_BOUNDARY")
    if _has_safety_violation(approval_boundary):
        return _finish(result, INVALID, "approval_boundary safety flag violation")
    if _status(approval_boundary.get("status")) != READY:
        return _finish(result, BLOCKED if _status(approval_boundary.get("status")) == BLOCKED else INVALID, "approval_boundary is not READY")
    if approval_boundary.get("execution_allowed") is not True:
        return _finish(result, BLOCKED, "execution_allowed must be True for dry-run")

    actions = _as_list(approval_boundary.get("approved_execution_actions"))
    if not actions:
        return _finish(result, INVALID, "approved_execution_actions must be non-empty")
    results: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            return _finish(result, INVALID, "approved action must be a dict")
        reason = _dryrun_action_error(action)
        item = {
            "candidate_id": _text(action.get("candidate_id")),
            "identity": deepcopy(_as_dict(action.get("identity"))),
            "function_boundary": action.get("function_boundary"),
            "send_order_args_snapshot": deepcopy(action.get("send_order_args_snapshot")),
            "simulated_only": True,
            "send_order_called": False,
            "broker_api_called": False,
            "actual_order_sent": False,
        }
        if reason:
            item["status"] = INVALID
            item["reason"] = reason
            blocked.append(item)
        else:
            item["status"] = READY
            item["expected_result"] = "SEND_ORDER_NOT_CALLED_DRY_RUN"
            results.append(item)

    result["per_candidate_results"] = deepcopy(results)
    result["blocked_candidate_results"] = deepcopy(blocked)
    result["simulated_dispatch_count"] = len(results)
    result["simulated_candidate_ids"] = [_text(item.get("candidate_id")) for item in results]
    result["summary"] = {
        "expected_dispatch_count": len(actions),
        "simulated_dispatch_count": len(results),
        "blocked_count": len(blocked),
        "partial_execution": False,
        "execution_started": False,
    }
    if blocked:
        return _finish(result, INVALID, "one or more dry-run candidates failed")
    result["status"] = READY
    result["dry_run_ready"] = True
    return _finalize(result)


def build_sell_dispatch_post_execution_verification_preview(dry_run_executor: Any) -> dict[str, Any]:
    result = _base_result(POST_VERIFY_TYPE, "verification_type", dry_run_executor)
    result.update(
        {
            "post_execution_verified": False,
            "verified_candidate_results": [],
            "source_dry_run_snapshot": deepcopy(dry_run_executor) if isinstance(dry_run_executor, dict) else None,
        }
    )
    if not isinstance(dry_run_executor, dict):
        return _finish(result, INVALID, "dry_run_executor must be a dict")
    if dry_run_executor.get("executor_type") != DRYRUN_TYPE:
        return _finish(result, INVALID, "executor_type is not SELL_DISPATCH_DRY_RUN_EXECUTOR")
    if _has_safety_violation(dry_run_executor):
        return _finish(result, INVALID, "dry_run_executor safety flag violation")
    if _status(dry_run_executor.get("status")) != READY:
        return _finish(result, INVALID if _status(dry_run_executor.get("status")) == INVALID else BLOCKED, "dry_run_executor is not READY")
    if dry_run_executor.get("dry_run_ready") is not True:
        return _finish(result, INVALID, "dry_run_ready must be True")

    snapshot = _as_dict(dry_run_executor.get("source_approval_boundary_snapshot"))
    plan_snapshot = _as_dict(snapshot.get("source_plan_snapshot"))
    expected_count = _as_dict(plan_snapshot.get("summary")).get("expected_dispatch_count")
    results = _as_list(dry_run_executor.get("per_candidate_results"))
    if expected_count != len(results) or dry_run_executor.get("simulated_dispatch_count") != len(results):
        return _finish(result, INVALID, "dry-run result count mismatch")
    if any(item.get("simulated_only") is not True for item in results if isinstance(item, dict)):
        return _finish(result, INVALID, "all dry-run results must be simulated only")

    queue_path = _text(snapshot.get("queue_path"))
    queue_data, queue_error = _read_queue(queue_path)
    if queue_error:
        return _finish(result, INVALID, queue_error)
    orders = _as_list(queue_data.get("orders"))
    verified: list[dict[str, Any]] = []
    for result_item in results:
        identity = _as_dict(result_item.get("identity"))
        matches = [_as_dict(order) for order in orders if _identity_matches(_as_dict(order), identity)]
        if len(matches) != 1:
            return _finish(result, INVALID, f"queue matching record count is {len(matches)} for {identity.get('order_id')}")
        queue_review = review_execution_queue_committed(_queue_commit_result(matches[0]))
        if _status(queue_review.get("status")) != "READY_FOR_FINAL_SEND_GATE":
            return _finish(result, INVALID, "queue record changed after dry-run")
        verified.append({"identity": deepcopy(identity), "queue_review": queue_review, "status": READY})

    result["status"] = READY
    result["post_execution_verified"] = True
    result["verified_candidate_results"] = verified
    result["summary"] = {
        "verified_count": len(verified),
        "expected_dispatch_count": expected_count,
        "actual_order_sent": False,
        "broker_api_called": False,
        "send_order_called": False,
    }
    return _finalize(result)


def _dryrun_action_error(action: dict[str, Any]) -> str | None:
    if action.get("function_boundary") != "kiwoom.SendOrder":
        return "function_boundary must be kiwoom.SendOrder"
    if not isinstance(action.get("send_order_args_snapshot"), list) or len(action.get("send_order_args_snapshot")) != 9:
        return "send_order_args_snapshot must contain 9 values"
    if _as_dict(action.get("identity")) != _identity_from(_as_dict(action.get("call_preview"))):
        return "identity mismatch"
    return None


def _queue_commit_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "COMMITTED",
        "manual_commit": True,
        "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
        "commit_result": {
            "committed": True,
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "order_queued_record": deepcopy(record),
            "send_order_called": False,
            "execution_enabled": False,
        },
    }


def _base_result(result_type: str, type_field: str, source: Any) -> dict[str, Any]:
    result = {
        type_field: result_type,
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Dispatch Executor Boundary",
        "routine_dependency": None,
        "status": BLOCKED,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "queue_status_changed": False,
        "file_write": False,
        "send_order_called": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "execution_started": False,
        "partial_execution": False,
        "real_ready_state_changed": False,
        "priority_selected": False,
        "auto_selected": False,
        "reasons": [],
        "warnings": [],
        "summary": {},
    }
    if isinstance(source, dict):
        result["warnings"].extend(_as_list(source.get("warnings")))
        result["reasons"].extend(_as_list(source.get("reasons")))
    return result


def _finish(result: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    result["status"] = status
    result["reasons"].append(reason)
    return _finalize(result)


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    for field in SAFETY_FALSE_FIELDS:
        result[field] = False
    result["send_order"] = False
    result["file_write"] = False
    result["execution_connected"] = False
    result["priority_selected"] = False
    result["auto_selected"] = False
    return result


def _read_queue(path_text: str) -> tuple[dict[str, Any], str | None]:
    try:
        path = Path(path_text)
        if not path.exists():
            return {}, "queue_path does not exist"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"queue_path is not readable JSON: {exc}"
    if not isinstance(data, dict):
        return {}, "queue JSON root must be an object"
    return data, None


def _has_safety_violation(payload: dict[str, Any]) -> bool:
    return any(payload.get(field) is True for field in SAFETY_FALSE_FIELDS)


def _identity_from(value: dict[str, Any]) -> dict[str, str]:
    return {field: _text(value.get(field)) for field in IDENTITY_FIELDS}


def _identity_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_text(left.get(field)) == _text(right.get(field)) for field in IDENTITY_FIELDS)


def _mask_account(account_no: str) -> str:
    text = _text(account_no)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + ("*" * (len(text) - 4)) + text[-2:]


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _status(value: Any) -> str:
    return _text(value).upper()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
