"""SELL runtime commit recovery plan.

Builds a read-only manual recovery plan from SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER.
The planner never writes queue files, restores backups, rolls back, sends orders,
calls a broker, creates OrderRequest objects, or mutates REAL_READY state.
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

PLAN_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_PLAN"
SOURCE_VERIFIER_TYPE = "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Recovery Plan"
ROUTINE_DEPENDENCY = None

_IDENTITY_FIELDS = (
    "order_id",
    "request_hash",
    "lock_id",
    "execution_id",
)


def build_sell_runtime_commit_recovery_plan(post_commit_verifier: dict[str, Any]) -> dict[str, Any]:
    """Create a manual recovery plan for a failed post-commit verification."""
    result = _base_result(post_commit_verifier)

    if not isinstance(post_commit_verifier, dict):
        result["status"] = INVALID
        result["reasons"].append("post_commit_verifier must be a dict")
        return _finish(result)

    result["verifier_snapshot"] = deepcopy(post_commit_verifier)
    _extend_list(result["warnings"], post_commit_verifier.get("warnings"))
    _extend_list(result["reasons"], post_commit_verifier.get("reasons"))

    if post_commit_verifier.get("verifier_type") != SOURCE_VERIFIER_TYPE:
        result["status"] = INVALID
        result["reasons"].append("verifier_type must be SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER")
        return _finish(result)

    verifier_status = _status(post_commit_verifier.get("status"))
    if verifier_status is None:
        result["status"] = INVALID
        result["reasons"].append("verifier status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if verifier_status == READY:
        result["status"] = READY
        result["recovery_required"] = False
        result["recovery_available"] = False
        result["reasons"].append("post-commit verifier is READY; recovery is not required")
        return _finish(result)

    if verifier_status == BLOCKED:
        result["status"] = BLOCKED
        result["recovery_required"] = False
        result["reasons"].append("post-commit verifier is BLOCKED; no committed queue state to recover")
        return _finish(result)

    if not _actual_commit_happened(post_commit_verifier):
        result["status"] = BLOCKED
        result["recovery_required"] = False
        result["reasons"].append("actual queue commit did not occur")
        return _finish(result)

    extraction = _extract_recovery_source(post_commit_verifier)
    if extraction["status"] != READY:
        result["status"] = extraction["status"]
        _extend_list(result["warnings"], extraction.get("warnings"))
        _extend_list(result["reasons"], extraction.get("reasons"))
        result["blocked_recovery_plans"].append(extraction["blocked_plan"])
        return _finish(result)

    source = extraction["source"]
    identity_errors = _identity_errors(source["identity"])
    if identity_errors:
        result["status"] = INVALID
        result["reasons"].append("recovery identity is incomplete: " + ", ".join(identity_errors))
        result["blocked_recovery_plans"].append({"status": INVALID, "reasons": deepcopy(result["reasons"])})
        return _finish(result)

    queue_data, queue_error = _read_json_object(Path(source["queue_path"]))
    if queue_error:
        result["status"] = INVALID
        result["reasons"].append("queue json invalid: " + queue_error)
        result["blocked_recovery_plans"].append({"status": INVALID, "queue_path": source["queue_path"], "reasons": deepcopy(result["reasons"])})
        return _finish(result)

    backup_path = source.get("backup_path")
    if not backup_path:
        result["status"] = BLOCKED
        result["reasons"].append("backup_path is required for manual recovery")
        result["blocked_recovery_plans"].append({"status": BLOCKED, "queue_path": source["queue_path"], "reasons": deepcopy(result["reasons"])})
        return _finish(result)

    backup_data, backup_error = _read_json_object(Path(backup_path))
    if backup_error:
        result["status"] = BLOCKED
        result["reasons"].append("backup json unavailable for recovery: " + backup_error)
        result["blocked_recovery_plans"].append({"status": BLOCKED, "backup_path": backup_path, "reasons": deepcopy(result["reasons"])})
        return _finish(result)

    queue_records = _matching_records(queue_data, source["identity"])
    backup_records = _matching_records(backup_data, source["identity"])
    diff = _queue_backup_diff(queue_data, backup_data, source["identity"])

    plan = {
        "status": RECOVERY_READY,
        "manual_only": True,
        "automatic_restore_performed": False,
        "queue_path": source["queue_path"],
        "backup_path": backup_path,
        "target_identity": deepcopy(source["identity"]),
        "queue_matching_record_count": len(queue_records),
        "backup_matching_record_count": len(backup_records),
        "queue_backup_diff": diff,
        "manual_steps": [
            "Stop automated execution before recovery.",
            "Inspect the current queue file and backup file.",
            "Restore the queue file from backup only after human approval.",
            "Re-run post-commit verification after manual recovery.",
        ],
        "expected_result": {
            "queue_file_restored_from_backup": True,
            "target_order_removed_or_reverted": diff["target_record_changed"],
            "send_order_called": False,
            "broker_api_called": False,
            "real_ready_state_changed": False,
        },
        "source_verification": deepcopy(source["source_verification"]),
    }

    result["status"] = RECOVERY_READY
    result["recovery_required"] = True
    result["recovery_available"] = True
    result["recovery_plans"].append(plan)
    result["summary"]["recovery_plan_count"] = 1
    result["summary"]["queue_backup_changed"] = diff["queue_backup_changed"]
    return _finish(result)


def _base_result(post_commit_verifier: Any) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "read_only": True,
        "manual_recovery_only": True,
        "recovery_required": False,
        "recovery_available": False,
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
        "verifier_snapshot": deepcopy(post_commit_verifier) if isinstance(post_commit_verifier, dict) else {},
        "recovery_plans": [],
        "blocked_recovery_plans": [],
        "warnings": [],
        "reasons": [],
        "summary": {
            "recovery_plan_count": 0,
            "blocked_recovery_plan_count": 0,
            "queue_backup_changed": False,
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


def _actual_commit_happened(verifier: dict[str, Any]) -> bool:
    snapshot = verifier.get("executor_snapshot")
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("queue_committed") is True and snapshot.get("runtime_commit_executed") is True:
        return True
    for item in snapshot.get("execution_results", []):
        if isinstance(item, dict) and isinstance(item.get("commit_result"), dict):
            if item["commit_result"].get("committed") is True:
                return True
    return False


def _extract_recovery_source(verifier: dict[str, Any]) -> dict[str, Any]:
    verification = _first_dict(verifier.get("blocked_verifications")) or _first_dict(verifier.get("verified_records"))
    if verification is None:
        return {
            "status": INVALID,
            "reasons": ["post-commit verifier does not include a verification record"],
            "blocked_plan": {"status": INVALID, "reasons": ["post-commit verifier does not include a verification record"]},
        }

    source_execution = verification.get("source_execution_result")
    if not isinstance(source_execution, dict):
        source_execution = verification.get("execution_result")
    if not isinstance(source_execution, dict):
        source_execution = _first_execution_result(verifier.get("executor_snapshot"))
    if not isinstance(source_execution, dict):
        return {
            "status": INVALID,
            "reasons": ["source execution_result is required"],
            "blocked_plan": {"status": INVALID, "source_verification": deepcopy(verification), "reasons": ["source execution_result is required"]},
        }

    commit_result = source_execution.get("commit_result")
    if not isinstance(commit_result, dict):
        commit_result = verification.get("commit_result")
    if not isinstance(commit_result, dict):
        return {
            "status": INVALID,
            "reasons": ["commit_result is required"],
            "blocked_plan": {"status": INVALID, "source_verification": deepcopy(verification), "reasons": ["commit_result is required"]},
        }

    queue_path = _clean_text(commit_result.get("order_queue_path") or verification.get("queue_path") or verification.get("order_queue_path"))
    if not queue_path:
        return {
            "status": INVALID,
            "reasons": ["queue_path is required"],
            "blocked_plan": {"status": INVALID, "source_verification": deepcopy(verification), "reasons": ["queue_path is required"]},
        }

    identity = {field: source_execution.get(field) or commit_result.get(field) for field in _IDENTITY_FIELDS}
    return {
        "status": READY,
        "source": {
            "queue_path": queue_path,
            "backup_path": _clean_text(commit_result.get("backup_path") or verification.get("backup_path")) or None,
            "identity": identity,
            "source_verification": verification,
            "source_execution_result": source_execution,
            "commit_result": commit_result,
        },
    }


def _first_execution_result(executor_snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(executor_snapshot, dict):
        return None
    return _first_dict(executor_snapshot.get("execution_results"))


def _first_dict(items: Any) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


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


def _identity_errors(identity: dict[str, Any]) -> list[str]:
    errors = []
    for field in _IDENTITY_FIELDS:
        if not _clean_text(identity.get(field)):
            errors.append(field)
    return errors


def _finish(result: dict[str, Any]) -> dict[str, Any]:
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
    result["summary"]["recovery_plan_count"] = len(result["recovery_plans"])
    result["summary"]["blocked_recovery_plan_count"] = len(result["blocked_recovery_plans"])
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


def _status(value: Any) -> str | None:
    return value if value in {READY, BLOCKED, INVALID} else None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extend_list(target: list[Any], values: Any) -> None:
    if isinstance(values, list):
        target.extend(deepcopy(values))


build_sell_runtime_recovery_plan = build_sell_runtime_commit_recovery_plan
create_sell_runtime_commit_recovery_plan = build_sell_runtime_commit_recovery_plan
