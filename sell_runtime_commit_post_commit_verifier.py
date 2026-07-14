"""SELL runtime commit post-commit verifier.

Reads the queue file written by SELL_RUNTIME_COMMIT_REAL_EXECUTOR and validates
that the committed ORDER_QUEUED record matches the executor result. This module
is read-only: it never writes files, rolls back, sends orders, calls a broker,
creates OrderRequest objects, or mutates REAL_READY state.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

VERIFIER_TYPE = "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER"
SOURCE_EXECUTOR_TYPE = "SELL_RUNTIME_COMMIT_REAL_EXECUTOR"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Runtime Commit Post-Commit Verifier"
ROUTINE_DEPENDENCY = None

_IDENTITY_FIELDS = (
    "order_id",
    "request_hash",
    "lock_id",
    "execution_id",
)


def verify_sell_runtime_commit_post_commit(real_executor_result: dict[str, Any]) -> dict[str, Any]:
    """Verify the committed queue record produced by the real executor."""
    result = _base_result(real_executor_result)

    if not isinstance(real_executor_result, dict):
        result["status"] = INVALID
        result["reasons"].append("real executor result must be a dict")
        return _finish(result)

    result["executor_snapshot"] = deepcopy(real_executor_result)
    _extend_list(result["warnings"], real_executor_result.get("warnings"))
    _extend_list(result["reasons"], real_executor_result.get("reasons"))

    if real_executor_result.get("executor_type") != SOURCE_EXECUTOR_TYPE:
        result["status"] = INVALID
        result["reasons"].append("executor_type must be SELL_RUNTIME_COMMIT_REAL_EXECUTOR")
        return _finish(result)

    upstream_status = _status(real_executor_result.get("status"))
    if upstream_status == INVALID:
        result["status"] = INVALID
        result["reasons"].append("real executor status is INVALID")
        return _finish(result)
    if upstream_status == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("real executor status is BLOCKED")
        return _finish(result)
    if upstream_status != READY:
        result["status"] = INVALID
        result["reasons"].append("real executor status must be READY, BLOCKED, or INVALID")
        return _finish(result)

    if real_executor_result.get("queue_committed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("real executor queue_committed must be True")
        return _finish(result)

    if real_executor_result.get("runtime_commit_executed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("real executor runtime_commit_executed must be True")
        return _finish(result)

    execution_results = real_executor_result.get("execution_results")
    if not isinstance(execution_results, list):
        result["status"] = INVALID
        result["reasons"].append("execution_results must be a list")
        return _finish(result)
    if len(execution_results) != 1:
        result["status"] = INVALID
        result["reasons"].append("post-commit verifier requires exactly one execution_result")
        return _finish(result)

    verification = _verify_execution_result(execution_results[0])
    result["verified_records"].extend(verification["verified_records"])
    result["blocked_verifications"].extend(verification["blocked_verifications"])
    _extend_list(result["warnings"], verification.get("warnings"))
    _extend_list(result["reasons"], verification.get("reasons"))

    if verification["status"] == READY:
        result["status"] = READY
        result["summary"]["verified_record_count"] = 1
    elif verification["status"] == BLOCKED:
        result["status"] = BLOCKED
        result["summary"]["blocked_verification_count"] = 1
    else:
        result["status"] = INVALID
        result["summary"]["invalid_verification_count"] = 1

    return _finish(result)


def _base_result(real_executor_result: Any) -> dict[str, Any]:
    return {
        "verifier_type": VERIFIER_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "read_only": True,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "rollback": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "post_commit_verified": False,
        "executor_snapshot": deepcopy(real_executor_result) if isinstance(real_executor_result, dict) else {},
        "verified_records": [],
        "blocked_verifications": [],
        "warnings": [],
        "reasons": [],
        "summary": {
            "verified_record_count": 0,
            "blocked_verification_count": 0,
            "invalid_verification_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
        },
    }


def _verify_execution_result(execution_result: Any) -> dict[str, Any]:
    result = {
        "status": BLOCKED,
        "verified_records": [],
        "blocked_verifications": [],
        "warnings": [],
        "reasons": [],
    }
    if not isinstance(execution_result, dict):
        result["status"] = INVALID
        result["reasons"].append("execution_result must be a dict")
        result["blocked_verifications"].append({"status": INVALID, "reasons": deepcopy(result["reasons"])})
        return result

    commit_result = execution_result.get("commit_result")
    if not isinstance(commit_result, dict):
        result["status"] = INVALID
        result["reasons"].append("commit_result must be a dict")
        result["blocked_verifications"].append({"status": INVALID, "execution_result": deepcopy(execution_result), "reasons": deepcopy(result["reasons"])})
        return result

    if commit_result.get("committed") is not True:
        result["status"] = BLOCKED
        result["reasons"].append("commit_result.committed must be True")
        result["blocked_verifications"].append({"status": BLOCKED, "execution_result": deepcopy(execution_result), "reasons": deepcopy(result["reasons"])})
        return result

    queue_path_text = _clean_text(commit_result.get("order_queue_path"))
    if not queue_path_text:
        result["status"] = INVALID
        result["reasons"].append("commit_result.order_queue_path is required")
        result["blocked_verifications"].append({"status": INVALID, "execution_result": deepcopy(execution_result), "reasons": deepcopy(result["reasons"])})
        return result

    queue_path = Path(queue_path_text)
    if not queue_path.exists():
        result["status"] = INVALID
        result["reasons"].append("order_queue_path does not exist")
        result["blocked_verifications"].append({"status": INVALID, "queue_path": queue_path_text, "reasons": deepcopy(result["reasons"])})
        return result

    queue_data, queue_error = _read_json_object(queue_path)
    if queue_error:
        result["status"] = INVALID
        result["reasons"].append(queue_error)
        result["blocked_verifications"].append({"status": INVALID, "queue_path": queue_path_text, "reasons": deepcopy(result["reasons"])})
        return result

    records = _matching_records(queue_data, execution_result)
    if len(records) != 1:
        result["status"] = INVALID
        result["reasons"].append(f"expected exactly one matching ORDER_QUEUED record, found {len(records)}")
        result["blocked_verifications"].append({"status": INVALID, "queue_path": queue_path_text, "matching_count": len(records), "reasons": deepcopy(result["reasons"])})
        return result

    record = records[0]
    record_errors = _record_errors(record, execution_result)
    if record_errors:
        result["status"] = INVALID
        result["reasons"].append("ORDER_QUEUED record mismatch: " + ", ".join(record_errors))
        result["blocked_verifications"].append({"status": INVALID, "queue_path": queue_path_text, "record": deepcopy(record), "reasons": deepcopy(result["reasons"])})
        return result

    backup_path_text = _clean_text(commit_result.get("backup_path"))
    if backup_path_text:
        backup_data, backup_error = _read_json_object(Path(backup_path_text))
        if backup_error:
            result["status"] = INVALID
            result["reasons"].append("backup json invalid: " + backup_error)
            result["blocked_verifications"].append({"status": INVALID, "backup_path": backup_path_text, "reasons": deepcopy(result["reasons"])})
            return result
        if not isinstance(backup_data.get("orders"), list):
            result["status"] = INVALID
            result["reasons"].append("backup json orders must be a list")
            result["blocked_verifications"].append({"status": INVALID, "backup_path": backup_path_text, "reasons": deepcopy(result["reasons"])})
            return result

    result["status"] = READY
    result["verified_records"].append(
        {
            "status": READY,
            "order_queue_path": queue_path_text,
            "backup_path": backup_path_text or None,
            "record": deepcopy(record),
            "commit_result": deepcopy(commit_result),
            "source_execution_result": deepcopy(execution_result),
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
        return {}, "queue json orders must be a list"
    return data, None


def _matching_records(queue_data: dict[str, Any], execution_result: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in queue_data.get("orders", []):
        if not isinstance(item, dict):
            continue
        if item.get("order_id") == execution_result.get("order_id"):
            matches.append(item)
    return matches


def _record_errors(record: dict[str, Any], execution_result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in _IDENTITY_FIELDS:
        if record.get(field) != execution_result.get(field):
            errors.append(field)
    if record.get("status") != "ORDER_QUEUED":
        errors.append("status")
    if record.get("send_order_called") is not False:
        errors.append("send_order_called")
    if record.get("execution_enabled") is not False:
        errors.append("execution_enabled")
    return sorted(set(errors))


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["post_commit_verified"] = result.get("status") == READY
    result["read_only"] = True
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["rollback"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["summary"]["verified_record_count"] = len(result["verified_records"])
    result["summary"]["blocked_verification_count"] = len(result["blocked_verifications"]) if result.get("status") == BLOCKED else 0
    result["summary"]["invalid_verification_count"] = len(result["blocked_verifications"]) if result.get("status") == INVALID else 0
    result["summary"]["runtime_write"] = False
    result["summary"]["queue_write"] = False
    result["summary"]["file_write"] = False
    result["summary"]["rollback"] = False
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


verify_sell_runtime_post_commit = verify_sell_runtime_commit_post_commit
build_sell_runtime_commit_post_commit_verifier = verify_sell_runtime_commit_post_commit
