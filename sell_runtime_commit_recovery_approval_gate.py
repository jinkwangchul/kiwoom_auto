"""SELL runtime commit recovery approval gate.

Approves a manual recovery plan only when the user approval context exactly
matches a SELL_RUNTIME_COMMIT_RECOVERY_PLAN and the queue/backup files still
match the recovery-ready conditions. This module is read-only and never restores
files, rolls back, sends orders, calls a broker, creates OrderRequest objects,
or mutates REAL_READY state.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"
RECOVERY_READY = "RECOVERY_READY"

APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE"
SOURCE_PLAN_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_PLAN"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Recovery Approval Gate"
ROUTINE_DEPENDENCY = None

_IDENTITY_FIELDS = (
    "order_id",
    "request_hash",
    "lock_id",
    "execution_id",
)

_APPROVAL_IDENTITY_FIELDS = {
    "order_id": "approved_order_id",
    "request_hash": "approved_request_hash",
    "lock_id": "approved_lock_id",
    "execution_id": "approved_execution_id",
}


def approve_sell_runtime_commit_recovery(
    recovery_plan: dict[str, Any],
    approval_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate user approval for a read-only manual recovery plan."""
    result = _base_result(recovery_plan, approval_context)

    if not isinstance(recovery_plan, dict):
        result["status"] = INVALID
        result["reasons"].append("recovery_plan must be a dict")
        return _finish(result)

    if approval_context is None:
        approval_context = {}
    if not isinstance(approval_context, dict):
        result["status"] = INVALID
        result["reasons"].append("approval_context must be a dict")
        return _finish(result)

    result["recovery_plan_snapshot"] = deepcopy(recovery_plan)
    result["approval_context_snapshot"] = deepcopy(approval_context)
    _extend_list(result["warnings"], recovery_plan.get("warnings"))
    _extend_list(result["reasons"], recovery_plan.get("reasons"))

    if recovery_plan.get("plan_type") != SOURCE_PLAN_TYPE:
        result["status"] = INVALID
        result["reasons"].append("plan_type must be SELL_RUNTIME_COMMIT_RECOVERY_PLAN")
        return _finish(result)

    plan_status = recovery_plan.get("status")
    if plan_status == READY:
        result["status"] = BLOCKED
        result["reasons"].append("recovery plan does not require recovery")
        return _finish(result)
    if plan_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("recovery plan is BLOCKED")
        return _finish(result)
    if plan_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("recovery plan is INVALID")
        return _finish(result)
    if plan_status != RECOVERY_READY:
        result["status"] = INVALID
        result["reasons"].append("recovery plan status must be RECOVERY_READY, READY, BLOCKED, or INVALID")
        return _finish(result)

    precheck_errors = _recovery_plan_contract_errors(recovery_plan)
    if precheck_errors:
        result["status"] = INVALID
        result["reasons"].extend(precheck_errors)
        return _finish(result)

    plan = recovery_plan["recovery_plans"][0]
    context_blockers = _approval_blockers(approval_context)
    if context_blockers:
        result["status"] = BLOCKED
        result["reasons"].extend(context_blockers)
        return _finish(result)

    context_errors = _approval_contract_errors(plan, approval_context)
    if context_errors:
        result["status"] = INVALID
        result["reasons"].extend(context_errors)
        return _finish(result)

    file_check = _verify_current_file_state(plan)
    if file_check["status"] != READY:
        result["status"] = INVALID
        _extend_list(result["reasons"], file_check.get("reasons"))
        result["blocked_approval_actions"].append(
            {
                "status": INVALID,
                "queue_path": plan.get("queue_path"),
                "backup_path": plan.get("backup_path"),
                "target_identity": deepcopy(plan.get("target_identity")),
                "reasons": deepcopy(file_check.get("reasons", [])),
            }
        )
        return _finish(result)

    approval_action = {
        "status": READY,
        "approval_action": "MANUAL_RECOVERY_APPROVED",
        "manual_only": True,
        "approval_token": _clean_text(approval_context.get("approval_token")),
        "queue_path": plan["queue_path"],
        "backup_path": plan["backup_path"],
        "target_identity": deepcopy(plan["target_identity"]),
        "queue_backup_diff": deepcopy(file_check["queue_backup_diff"]),
        "source_recovery_plan": deepcopy(plan),
        "restore_executed": False,
        "rollback_executed": False,
        "send_order_called": False,
        "broker_api_called": False,
    }
    result["status"] = READY
    result["approval_granted"] = True
    result["recovery_execution_allowed"] = True
    result["approved_recovery_actions"].append(approval_action)
    result["summary"]["approved_recovery_action_count"] = 1
    return _finish(result)


def _base_result(recovery_plan: Any, approval_context: Any) -> dict[str, Any]:
    return {
        "approval_type": APPROVAL_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "read_only": True,
        "manual_recovery_only": True,
        "approval_granted": False,
        "recovery_execution_allowed": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "rollback": False,
        "backup_restored": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "recovery_plan_snapshot": deepcopy(recovery_plan) if isinstance(recovery_plan, dict) else {},
        "approval_context_snapshot": deepcopy(approval_context) if isinstance(approval_context, dict) else {},
        "approved_recovery_actions": [],
        "blocked_approval_actions": [],
        "warnings": [],
        "reasons": [],
        "summary": {
            "approved_recovery_action_count": 0,
            "blocked_approval_action_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback": False,
            "backup_restored": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
        },
    }


def _recovery_plan_contract_errors(recovery_plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if recovery_plan.get("recovery_required") is not True:
        errors.append("recovery_required must be True")
    if recovery_plan.get("recovery_available") is not True:
        errors.append("recovery_available must be True")
    plans = recovery_plan.get("recovery_plans")
    if not isinstance(plans, list) or len(plans) != 1:
        errors.append("recovery_plans must contain exactly one plan")
        return errors
    plan = plans[0]
    if not isinstance(plan, dict):
        errors.append("recovery plan item must be a dict")
        return errors
    if plan.get("status") != RECOVERY_READY:
        errors.append("recovery plan item status must be RECOVERY_READY")
    if not _clean_text(plan.get("queue_path")):
        errors.append("recovery plan queue_path is required")
    if not _clean_text(plan.get("backup_path")):
        errors.append("recovery plan backup_path is required")
    identity = plan.get("target_identity")
    if not isinstance(identity, dict):
        errors.append("recovery plan target_identity must be a dict")
    else:
        for field in _IDENTITY_FIELDS:
            if not _clean_text(identity.get(field)):
                errors.append(f"target_identity.{field} is required")
    diff = plan.get("queue_backup_diff")
    if not isinstance(diff, dict):
        errors.append("recovery plan queue_backup_diff must be a dict")
    else:
        if diff.get("queue_backup_changed") is not True:
            errors.append("queue_backup_changed must be True")
        if diff.get("target_record_changed") is not True:
            errors.append("target_record_changed must be True")
        if diff.get("queue_matching_record_count") != 1:
            errors.append("queue_matching_record_count must be 1")
        if diff.get("backup_matching_record_count") != 0:
            errors.append("backup_matching_record_count must be 0")
    return errors


def _approval_blockers(approval_context: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if approval_context.get("user_approved") is not True:
        blockers.append("user approval is required")
    if not _clean_text(approval_context.get("approval_token")):
        blockers.append("approval_token is required")
    return blockers


def _approval_contract_errors(plan: dict[str, Any], approval_context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if _clean_text(approval_context.get("queue_path")) != _clean_text(plan.get("queue_path")):
        errors.append("approval queue_path does not match recovery plan")
    if _clean_text(approval_context.get("backup_path")) != _clean_text(plan.get("backup_path")):
        errors.append("approval backup_path does not match recovery plan")
    identity = plan.get("target_identity") if isinstance(plan.get("target_identity"), dict) else {}
    for identity_field, approval_field in _APPROVAL_IDENTITY_FIELDS.items():
        if _clean_text(approval_context.get(approval_field)) != _clean_text(identity.get(identity_field)):
            errors.append(f"approval {approval_field} does not match target_identity.{identity_field}")
    return errors


def _verify_current_file_state(plan: dict[str, Any]) -> dict[str, Any]:
    result = {"status": BLOCKED, "reasons": [], "queue_backup_diff": {}}
    queue_path = Path(_clean_text(plan.get("queue_path")))
    backup_path = Path(_clean_text(plan.get("backup_path")))
    identity = plan.get("target_identity") if isinstance(plan.get("target_identity"), dict) else {}

    queue_data, queue_error = _read_json_object(queue_path)
    if queue_error:
        result["status"] = INVALID
        result["reasons"].append("queue json invalid: " + queue_error)
        return result

    backup_data, backup_error = _read_json_object(backup_path)
    if backup_error:
        result["status"] = INVALID
        result["reasons"].append("backup json invalid: " + backup_error)
        return result

    diff = _queue_backup_diff(queue_data, backup_data, identity)
    result["queue_backup_diff"] = diff
    planned_diff = plan.get("queue_backup_diff") if isinstance(plan.get("queue_backup_diff"), dict) else {}
    if diff != planned_diff:
        result["status"] = INVALID
        result["reasons"].append("current queue/backup state differs from recovery plan")
        return result

    readiness_errors = _recovery_readiness_errors(diff)
    if readiness_errors:
        result["status"] = INVALID
        result["reasons"].extend(readiness_errors)
        return result

    result["status"] = READY
    return result


def _read_json_object(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, f"{path} does not exist"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read json: {exc}"
    if not isinstance(data, dict):
        return {}, "json root must be an object"
    if not isinstance(data.get("orders"), list):
        return {}, "json orders must be a list"
    return data, None


def _matching_records(queue_data: dict[str, Any], identity: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in queue_data.get("orders", []):
        if not isinstance(item, dict):
            continue
        if all(item.get(field) == identity.get(field) for field in _IDENTITY_FIELDS):
            records.append(item)
    return records


def _queue_backup_diff(queue_data: dict[str, Any], backup_data: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    queue_orders = [item for item in queue_data.get("orders", []) if isinstance(item, dict)]
    backup_orders = [item for item in backup_data.get("orders", []) if isinstance(item, dict)]
    queue_target = _matching_records(queue_data, identity)
    backup_target = _matching_records(backup_data, identity)
    return {
        "queue_order_count": len(queue_orders),
        "backup_order_count": len(backup_orders),
        "queue_matching_record_count": len(queue_target),
        "backup_matching_record_count": len(backup_target),
        "queue_backup_changed": queue_orders != backup_orders,
        "target_record_changed": queue_target != backup_target,
    }


def _recovery_readiness_errors(diff: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if diff.get("queue_backup_changed") is not True:
        errors.append("queue and backup are identical; restoring would not change target state")
    if diff.get("target_record_changed") is not True:
        errors.append("target record does not differ between queue and backup")
    if diff.get("queue_matching_record_count") != 1:
        errors.append("current queue must contain exactly one target record")
    if diff.get("backup_matching_record_count") != 0:
        errors.append("backup must not contain the target record to prove a safe previous state")
    return errors


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["approval_granted"] = result.get("status") == READY
    result["recovery_execution_allowed"] = result.get("status") == READY
    result["read_only"] = True
    result["manual_recovery_only"] = True
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["rollback"] = False
    result["backup_restored"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["summary"]["approved_recovery_action_count"] = len(result["approved_recovery_actions"])
    result["summary"]["blocked_approval_action_count"] = len(result["blocked_approval_actions"])
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["file_write"] = False
    result["summary"]["rollback"] = False
    result["summary"]["backup_restored"] = False
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["actual_order_sent"] = False
    result["summary"]["order_request_created"] = False
    result["summary"]["real_ready_state_changed"] = False
    return result


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


build_sell_runtime_commit_recovery_approval_gate = approve_sell_runtime_commit_recovery
approve_sell_runtime_recovery = approve_sell_runtime_commit_recovery
