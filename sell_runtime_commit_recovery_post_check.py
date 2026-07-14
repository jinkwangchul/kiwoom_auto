"""SELL runtime commit recovery post-check.

Read-only verification for SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR results. It
confirms the restored queue file matches the approved backup and that the
pre-restore safety backup still contains the recovered target identity.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

POST_CHECK_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_POST_CHECK"
SOURCE_EXECUTOR_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Recovery Post-Check"
ROUTINE_DEPENDENCY = None

_IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "request_hash",
    "lock_id",
    "execution_id",
)


def check_sell_runtime_commit_recovery_post_commit(recovery_executor_result: dict[str, Any]) -> dict[str, Any]:
    """Verify recovery side effects after the recovery executor runs."""
    result = _base_result(recovery_executor_result)

    if not isinstance(recovery_executor_result, dict):
        result["status"] = INVALID
        result["reasons"].append("recovery_executor_result must be a dict")
        return _finish(result)

    result["executor_snapshot"] = deepcopy(recovery_executor_result)
    _apply_observed_effects(result, recovery_executor_result)
    _extend_list(result["warnings"], recovery_executor_result.get("warnings"))
    _extend_list(result["reasons"], recovery_executor_result.get("reasons"))

    if recovery_executor_result.get("executor_type") != SOURCE_EXECUTOR_TYPE:
        result["status"] = INVALID
        result["reasons"].append("executor_type must be SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR")
        return _finish(result)

    safety_errors = _executor_safety_errors(recovery_executor_result)
    if safety_errors:
        result["status"] = INVALID
        result["reasons"].extend(safety_errors)
        return _finish(result)

    if recovery_executor_result.get("status") == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("recovery executor is BLOCKED")
        return _finish(result)

    if recovery_executor_result.get("status") not in {READY, INVALID}:
        result["status"] = INVALID
        result["reasons"].append("recovery executor status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if _restore_executed(recovery_executor_result) is not True:
        result["status"] = BLOCKED
        result["reasons"].append("recovery restore was not executed")
        return _finish(result)

    execution_result = _single_execution_result(recovery_executor_result)
    if not isinstance(execution_result, dict):
        result["status"] = INVALID
        result["reasons"].append("recovery executor must include exactly one recovery_result")
        return _finish(result)

    check = _verify_files(execution_result)
    result["checked_records"].extend(check["checked_records"])
    result["blocked_checks"].extend(check["blocked_checks"])
    _extend_list(result["warnings"], check.get("warnings"))
    _extend_list(result["reasons"], check.get("reasons"))

    if check["status"] == READY and recovery_executor_result.get("status") == READY:
        result["status"] = READY
    elif check["status"] == BLOCKED:
        result["status"] = BLOCKED
    else:
        result["status"] = INVALID
    return _finish(result)


def _base_result(recovery_executor_result: Any) -> dict[str, Any]:
    return {
        "post_check_type": POST_CHECK_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "read_only": True,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "rollback": False,
        "backup_restored": False,
        "observed_runtime_write": False,
        "observed_queue_write": False,
        "observed_file_write": False,
        "observed_rollback_executed": False,
        "observed_backup_restored": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "post_recovery_verified": False,
        "executor_snapshot": deepcopy(recovery_executor_result) if isinstance(recovery_executor_result, dict) else {},
        "checked_records": [],
        "blocked_checks": [],
        "warnings": [],
        "reasons": [],
        "summary": {
            "checked_record_count": 0,
            "blocked_check_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback": False,
            "backup_restored": False,
            "observed_runtime_write": False,
            "observed_queue_write": False,
            "observed_file_write": False,
            "observed_rollback_executed": False,
            "observed_backup_restored": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
        },
    }


def _executor_safety_errors(executor: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if executor.get("send_order") is not False:
        errors.append("send_order must be False")
    if executor.get("broker_api_called") is not False:
        errors.append("broker_api_called must be False")
    if executor.get("actual_order_sent") is not False:
        errors.append("actual_order_sent must be False")
    if executor.get("order_request_created") is not False:
        errors.append("order_request_created must be False")
    if executor.get("real_ready_state_changed") is not False:
        errors.append("real_ready_state_changed must be False")
    return errors


def _restore_executed(executor: dict[str, Any]) -> bool:
    if executor.get("backup_restored") is True and executor.get("rollback_executed") is True:
        return True
    for item in executor.get("recovery_results", []):
        if isinstance(item, dict) and item.get("restore_executed") is True:
            return True
    return False


def _single_execution_result(executor: dict[str, Any]) -> dict[str, Any] | None:
    items = executor.get("recovery_results")
    if not isinstance(items, list) or len(items) != 1:
        return None
    item = items[0]
    return item if isinstance(item, dict) else None


def _verify_files(execution_result: dict[str, Any]) -> dict[str, Any]:
    result = {"status": BLOCKED, "checked_records": [], "blocked_checks": [], "warnings": [], "reasons": []}
    queue_path_text = _clean_text(execution_result.get("queue_path"))
    backup_path_text = _clean_text(execution_result.get("backup_path"))
    safety_backup_path_text = _clean_text(execution_result.get("safety_backup_path"))
    identities = _target_identities(execution_result)

    if not queue_path_text or not backup_path_text or not safety_backup_path_text:
        result["status"] = INVALID
        result["reasons"].append("queue_path, backup_path, and safety_backup_path are required")
        result["blocked_checks"].append({"status": INVALID, "reasons": deepcopy(result["reasons"])})
        return result
    identity_errors = _identities_errors(identities)
    if identity_errors:
        result["status"] = INVALID
        result["reasons"].append("target_identities incomplete: " + ", ".join(identity_errors))
        result["blocked_checks"].append({"status": INVALID, "reasons": deepcopy(result["reasons"])})
        return result

    queue_data, queue_error = _read_json_object(Path(queue_path_text))
    backup_data, backup_error = _read_json_object(Path(backup_path_text))
    safety_data, safety_error = _read_json_object(Path(safety_backup_path_text))
    errors = []
    if queue_error:
        errors.append("queue json invalid: " + queue_error)
    if backup_error:
        errors.append("backup json invalid: " + backup_error)
    if safety_error:
        errors.append("safety backup json invalid: " + safety_error)
    if errors:
        result["status"] = INVALID
        result["reasons"].extend(errors)
        result["blocked_checks"].append({"status": INVALID, "reasons": deepcopy(result["reasons"])})
        return result

    queue_counts = [len(_matching_records(queue_data, identity)) for identity in identities]
    safety_counts = [len(_matching_records(safety_data, identity)) for identity in identities]
    check_errors = []
    if queue_data != backup_data:
        check_errors.append("queue json must match backup json after recovery")
    if sum(queue_counts) != 0:
        check_errors.append("target identity must be absent from queue after recovery")
    if sum(safety_counts) != len(identities) or any(count != 1 for count in safety_counts):
        check_errors.append("safety backup must contain exactly one record for each target identity")
    if check_errors:
        result["status"] = INVALID
        result["reasons"].extend(check_errors)
        result["blocked_checks"].append(
            {
                "status": INVALID,
                "queue_path": queue_path_text,
                "backup_path": backup_path_text,
                "safety_backup_path": safety_backup_path_text,
                "queue_matching_record_count": sum(queue_counts),
                "safety_backup_matching_record_count": sum(safety_counts),
                "queue_matching_counts": queue_counts,
                "safety_backup_matching_counts": safety_counts,
                "reasons": deepcopy(check_errors),
            }
        )
        return result

    result["status"] = READY
    result["checked_records"].append(
        {
            "status": READY,
            "queue_path": queue_path_text,
            "backup_path": backup_path_text,
            "safety_backup_path": safety_backup_path_text,
            "target_identity": deepcopy(identities[0]),
            "target_identities": deepcopy(identities),
            "target_count": len(identities),
            "queue_matching_record_count": 0,
            "safety_backup_matching_record_count": len(identities),
            "queue_matching_counts": queue_counts,
            "safety_backup_matching_counts": safety_counts,
            "queue_matches_backup": True,
        }
    )
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
    fields = [field for field in _IDENTITY_FIELDS if _clean_text(identity.get(field))]
    for item in queue_data.get("orders", []):
        if not isinstance(item, dict):
            continue
        if all(item.get(field) == identity.get(field) for field in fields):
            records.append(item)
    return records


def _target_identities(execution_result: dict[str, Any]) -> list[dict[str, Any]]:
    identities = execution_result.get("target_identities")
    if isinstance(identities, list) and identities:
        return [item for item in identities if isinstance(item, dict)]
    identity = execution_result.get("target_identity")
    return [identity] if isinstance(identity, dict) else []


def _identities_errors(identities: list[dict[str, Any]]) -> list[str]:
    errors = []
    if not identities:
        return ["target_identities"]
    for index, identity in enumerate(identities):
        for field in _IDENTITY_FIELDS:
            if len(identities) == 1 and field in {"candidate_id", "queue_pending_id"}:
                continue
            if not _clean_text(identity.get(field)):
                errors.append(f"target_identities[{index}].{field}")
    return errors


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["post_recovery_verified"] = result.get("status") == READY
    result["read_only"] = True
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
    result["summary"]["checked_record_count"] = len(result["checked_records"])
    result["summary"]["blocked_check_count"] = len(result["blocked_checks"])
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["file_write"] = False
    result["summary"]["rollback"] = False
    result["summary"]["backup_restored"] = False
    result["summary"]["observed_runtime_write"] = result["observed_runtime_write"]
    result["summary"]["observed_queue_write"] = result["observed_queue_write"]
    result["summary"]["observed_file_write"] = result["observed_file_write"]
    result["summary"]["observed_rollback_executed"] = result["observed_rollback_executed"]
    result["summary"]["observed_backup_restored"] = result["observed_backup_restored"]
    result["summary"]["send_order"] = False
    result["summary"]["broker_api_called"] = False
    result["summary"]["actual_order_sent"] = False
    result["summary"]["order_request_created"] = False
    result["summary"]["real_ready_state_changed"] = False
    return result


def _apply_observed_effects(result: dict[str, Any], executor: dict[str, Any]) -> None:
    restore_seen = _restore_executed(executor)
    result["observed_runtime_write"] = executor.get("runtime_write") is True or restore_seen
    result["observed_queue_write"] = executor.get("queue_write") is True or restore_seen
    result["observed_file_write"] = executor.get("file_write") is True
    result["observed_rollback_executed"] = executor.get("rollback_executed") is True or restore_seen
    result["observed_backup_restored"] = executor.get("backup_restored") is True or restore_seen


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


verify_sell_runtime_commit_recovery_post_check = check_sell_runtime_commit_recovery_post_commit
build_sell_runtime_commit_recovery_post_check = check_sell_runtime_commit_recovery_post_commit
