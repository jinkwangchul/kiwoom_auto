"""SELL runtime commit recovery executor.

Executes a user-approved manual queue recovery by restoring the queue file from
the approved backup file. The executor is intentionally limited to queue-file
recovery: it never sends orders, calls a broker, creates OrderRequest objects,
or mutates REAL_READY state.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

EXECUTOR_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR"
SOURCE_APPROVAL_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Recovery Executor"
ROUTINE_DEPENDENCY = None

_IDENTITY_FIELDS = (
    "order_id",
    "request_hash",
    "lock_id",
    "execution_id",
)


def execute_sell_runtime_commit_recovery(recovery_approval: dict[str, Any]) -> dict[str, Any]:
    """Restore the approved queue file from backup after final preflight checks."""
    result = _base_result(recovery_approval)

    if not isinstance(recovery_approval, dict):
        result["status"] = INVALID
        result["reasons"].append("recovery_approval must be a dict")
        return _finish(result)

    result["approval_snapshot"] = deepcopy(recovery_approval)

    preflight = _preflight(recovery_approval)
    if preflight["status"] != READY:
        result["status"] = preflight["status"]
        _extend_list(result["reasons"], preflight.get("reasons"))
        result["blocked_recovery_results"].append(
            {
                "status": preflight["status"],
                "reasons": deepcopy(preflight.get("reasons", [])),
            }
        )
        return _finish(result)

    action = preflight["action"]
    queue_path = Path(action["queue_path"])
    backup_path = Path(action["backup_path"])
    identity = action["target_identity"]
    expected_backup_data = preflight["backup_data"]
    expected_diff = action["queue_backup_diff"]

    execution_result = {
        "status": BLOCKED,
        "queue_path": str(queue_path),
        "backup_path": str(backup_path),
        "safety_backup_path": None,
        "temp_restore_path": None,
        "target_identity": deepcopy(identity),
        "approval_token": action["approval_token"],
        "restore_executed": False,
        "post_restore_verified": False,
        "reasons": [],
        "warnings": [],
    }

    try:
        safety_backup_path = _safety_backup_path(queue_path)
        shutil.copy2(queue_path, safety_backup_path)
        execution_result["safety_backup_path"] = str(safety_backup_path)

        temp_restore_path = _temp_restore_path(queue_path)
        temp_restore_path.write_text(
            json.dumps(expected_backup_data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        execution_result["temp_restore_path"] = str(temp_restore_path)

        temp_data, temp_error = _read_json_object(temp_restore_path)
        if temp_error:
            execution_result["status"] = BLOCKED
            execution_result["reasons"].append("temp restore json invalid: " + temp_error)
            result["status"] = BLOCKED
            result["blocked_recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        if temp_data != expected_backup_data:
            execution_result["status"] = BLOCKED
            execution_result["reasons"].append("temp restore json does not match backup data")
            result["status"] = BLOCKED
            result["blocked_recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        temp_restore_path.replace(queue_path)
        execution_result["restore_executed"] = True
        _mark_restore_effects(result)

        post_data, post_error = _read_json_object(queue_path)
        if post_error:
            execution_result["status"] = INVALID
            execution_result["reasons"].append("restored queue json invalid: " + post_error)
            result["status"] = INVALID
            result["recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        if post_data != expected_backup_data:
            execution_result["status"] = INVALID
            execution_result["reasons"].append("restored queue json does not match backup data")
            result["status"] = INVALID
            result["recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        post_diff = _queue_backup_diff(post_data, expected_backup_data, identity)
        if post_diff["queue_matching_record_count"] != 0:
            execution_result["status"] = INVALID
            execution_result["reasons"].append("target identity still exists after restore")
            result["status"] = INVALID
            result["recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        if post_diff["queue_backup_changed"] is not False:
            execution_result["status"] = INVALID
            execution_result["reasons"].append("restored queue differs from backup after restore")
            result["status"] = INVALID
            result["recovery_results"].append(deepcopy(execution_result))
            return _finish(result)

        execution_result["status"] = READY
        execution_result["post_restore_verified"] = True
        result["status"] = READY
        result["recovery_results"].append(deepcopy(execution_result))
        return _finish(result)
    except Exception as exc:
        execution_result["status"] = INVALID if execution_result["restore_executed"] else BLOCKED
        execution_result["reasons"].append(f"recovery execution failed: {exc}")
        result["status"] = execution_result["status"]
        if execution_result["restore_executed"]:
            _mark_restore_effects(result)
            result["recovery_results"].append(deepcopy(execution_result))
        else:
            result["blocked_recovery_results"].append(deepcopy(execution_result))
        return _finish(result)


def _base_result(recovery_approval: Any) -> dict[str, Any]:
    return {
        "executor_type": EXECUTOR_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "status": BLOCKED,
        "approval_snapshot": deepcopy(recovery_approval) if isinstance(recovery_approval, dict) else {},
        "recovery_results": [],
        "blocked_recovery_results": [],
        "warnings": [],
        "reasons": [],
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "rollback_executed": False,
        "backup_restored": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "summary": {
            "recovery_result_count": 0,
            "blocked_recovery_result_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback_executed": False,
            "backup_restored": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
        },
    }


def _preflight(approval: dict[str, Any]) -> dict[str, Any]:
    result = {"status": BLOCKED, "reasons": [], "action": None, "queue_data": {}, "backup_data": {}}
    if approval.get("approval_type") != SOURCE_APPROVAL_TYPE:
        result["status"] = INVALID
        result["reasons"].append("approval_type must be SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE")
        return result
    if approval.get("status") == INVALID:
        result["status"] = INVALID
        result["reasons"].append("recovery approval status is INVALID")
        return result
    if approval.get("status") != READY:
        result["status"] = BLOCKED
        result["reasons"].append("recovery approval status must be READY")
        return result
    if approval.get("approval_granted") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("approval_granted must be True")
        return result
    if approval.get("recovery_execution_allowed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("recovery_execution_allowed must be True")
        return result

    actions = approval.get("approved_recovery_actions")
    if not isinstance(actions, list) or len(actions) != 1:
        result["status"] = INVALID
        result["reasons"].append("approved_recovery_actions must contain exactly one action")
        return result
    action = actions[0]
    if not isinstance(action, dict):
        result["status"] = INVALID
        result["reasons"].append("approved recovery action must be a dict")
        return result

    action_errors = _action_contract_errors(action)
    if action_errors:
        result["status"] = INVALID
        result["reasons"].extend(action_errors)
        return result

    queue_path = Path(action["queue_path"])
    backup_path = Path(action["backup_path"])
    queue_data, queue_error = _read_json_object(queue_path)
    if queue_error:
        result["status"] = BLOCKED
        result["reasons"].append("queue json unavailable before recovery: " + queue_error)
        return result
    backup_data, backup_error = _read_json_object(backup_path)
    if backup_error:
        result["status"] = BLOCKED
        result["reasons"].append("backup json unavailable before recovery: " + backup_error)
        return result

    current_diff = _queue_backup_diff(queue_data, backup_data, action["target_identity"])
    if current_diff != action["queue_backup_diff"]:
        result["status"] = BLOCKED
        result["reasons"].append("current queue/backup state differs from approval action")
        return result

    readiness_errors = _recovery_readiness_errors(current_diff)
    if readiness_errors:
        result["status"] = BLOCKED
        result["reasons"].extend(readiness_errors)
        return result

    result["status"] = READY
    result["action"] = deepcopy(action)
    result["queue_data"] = queue_data
    result["backup_data"] = backup_data
    return result


def _action_contract_errors(action: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if action.get("status") != READY:
        errors.append("approved recovery action status must be READY")
    if not _clean_text(action.get("approval_token")):
        errors.append("approval_token is required")
    if not _clean_text(action.get("queue_path")):
        errors.append("queue_path is required")
    if not _clean_text(action.get("backup_path")):
        errors.append("backup_path is required")
    identity = action.get("target_identity")
    if not isinstance(identity, dict):
        errors.append("target_identity must be a dict")
    else:
        for field in _IDENTITY_FIELDS:
            if not _clean_text(identity.get(field)):
                errors.append(f"target_identity.{field} is required")
    diff = action.get("queue_backup_diff")
    if not isinstance(diff, dict):
        errors.append("queue_backup_diff must be a dict")
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
        errors.append("queue and backup must differ before recovery")
    if diff.get("target_record_changed") is not True:
        errors.append("target record must differ between queue and backup")
    if diff.get("queue_matching_record_count") != 1:
        errors.append("current queue must contain exactly one target record")
    if diff.get("backup_matching_record_count") != 0:
        errors.append("backup must not contain the target record")
    return errors


def _safety_backup_path(queue_path: Path) -> Path:
    return queue_path.with_name(f"{queue_path.name}.recovery_safety.{uuid4().hex}.bak")


def _temp_restore_path(queue_path: Path) -> Path:
    return queue_path.with_name(f"{queue_path.name}.recovery_restore.{uuid4().hex}.tmp")


def _mark_restore_effects(result: dict[str, Any]) -> None:
    result["runtime_write"] = True
    result["queue_write"] = True
    result["file_write"] = True
    result["rollback_executed"] = True
    result["backup_restored"] = True


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["send_order"] = False
    result["broker_api_called"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["summary"]["recovery_result_count"] = len(result["recovery_results"])
    result["summary"]["blocked_recovery_result_count"] = len(result["blocked_recovery_results"])
    result["summary"]["runtime_write"] = result["runtime_write"]
    result["summary"]["queue_write"] = result["queue_write"]
    result["summary"]["file_write"] = result["file_write"]
    result["summary"]["rollback_executed"] = result["rollback_executed"]
    result["summary"]["backup_restored"] = result["backup_restored"]
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


execute_sell_runtime_recovery = execute_sell_runtime_commit_recovery
run_sell_runtime_commit_recovery_executor = execute_sell_runtime_commit_recovery
