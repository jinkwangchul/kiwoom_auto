# -*- coding: utf-8 -*-
"""Preview-only policy for opening execution queue commits.

This module only decides whether a queue commit may be opened after a runtime
append commit. It never calls queue commit services, writes order_queue.json,
modifies runtime files, calls SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


POLICY_TYPE = "EXECUTION_QUEUE_COMMIT_READINESS_POLICY"
STATUS_READY = "READY_TO_COMMIT_QUEUE"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _queue_record(queue_write_preview_result: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(queue_write_preview_result.get("order_queued_record_preview"))


def _runtime_queue_path(queue_path: Any) -> bool:
    path_text = _text(queue_path)
    if not path_text:
        return False
    path = Path(path_text)
    return path.name == "order_queue.json" and path.parent.name == "runtime"


def _result(
    *,
    status: str,
    queue_commit_allowed: bool,
    identity_checks: dict[str, Any] | None = None,
    required_confirmations: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "queue_commit_allowed": queue_commit_allowed,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "identity_checks": deepcopy(identity_checks or {}),
        "required_confirmations": deepcopy(required_confirmations or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _runtime_identity(runtime_commit_result: dict[str, Any]) -> dict[str, str]:
    return {
        "execution_id": _text(runtime_commit_result.get("execution_id")),
        "order_id": _text(runtime_commit_result.get("order_id")),
        "request_hash": _text(runtime_commit_result.get("request_hash")),
        "lock_id": _text(runtime_commit_result.get("lock_id")),
    }


def _runtime_identity_from_paths(runtime_commit_result: dict[str, Any]) -> dict[str, str]:
    # Older runtime commit results do not expose identity at top level. Tests and
    # future callers may provide the committed records for policy-level checks.
    execution_record = _as_dict(runtime_commit_result.get("execution_record"))
    lock_record = _as_dict(runtime_commit_result.get("lock_record"))
    return {
        "execution_id": _text(execution_record.get("execution_id")) or _text(lock_record.get("execution_id")),
        "order_id": _text(execution_record.get("order_id")) or _text(lock_record.get("order_id")),
        "request_hash": _text(execution_record.get("request_hash")) or _text(lock_record.get("request_hash")),
        "lock_id": _text(lock_record.get("lock_id")) or _text(execution_record.get("lock_id")),
    }


def _queue_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "execution_id": _text(record.get("execution_id")),
        "order_id": _text(record.get("order_id")),
        "request_hash": _text(record.get("request_hash")),
        "lock_id": _text(record.get("lock_id")),
    }


def evaluate_execution_queue_commit_readiness(
    *,
    runtime_commit_result: Any,
    queue_write_preview_result: Any,
    queue_path: Any,
    confirmations: Any = None,
) -> dict[str, Any]:
    """Evaluate whether queue commit can be opened without committing it."""
    if not isinstance(runtime_commit_result, dict):
        return _result(
            status=STATUS_INVALID,
            queue_commit_allowed=False,
            issues=["MALFORMED_RUNTIME_COMMIT_RESULT"],
        )
    if not isinstance(queue_write_preview_result, dict):
        return _result(
            status=STATUS_INVALID,
            queue_commit_allowed=False,
            issues=["MALFORMED_QUEUE_WRITE_PREVIEW_RESULT"],
        )

    queue_path_text = _text(queue_path)
    if not queue_path_text:
        return _result(
            status=STATUS_INVALID,
            queue_commit_allowed=False,
            issues=["MISSING_QUEUE_PATH"],
        )

    confirmation_flags = _as_dict(confirmations)
    required_confirmations = {
        "manual_queue_write_confirmed": confirmation_flags.get("manual_queue_write_confirmed") is True
        or confirmation_flags.get("operator_confirmed_for_queue_write") is True,
        "manual_runtime_queue_write_confirmed": confirmation_flags.get("manual_runtime_queue_write_confirmed") is True,
        "runtime_queue_path": _runtime_queue_path(queue_path_text),
    }

    issues: list[str] = []
    warnings = list(runtime_commit_result.get("warnings") or []) + list(queue_write_preview_result.get("warnings") or [])

    if runtime_commit_result.get("status") != "COMMITTED":
        issues.append("RUNTIME_COMMIT_NOT_COMMITTED")
    if runtime_commit_result.get("committed") is not True:
        issues.append("RUNTIME_COMMITTED_FLAG_NOT_TRUE")
    if runtime_commit_result.get("runtime_write") is not True:
        issues.append("RUNTIME_WRITE_FLAG_NOT_TRUE")
    if runtime_commit_result.get("read_back_verified") is not True:
        issues.append("RUNTIME_READ_BACK_NOT_VERIFIED")

    if queue_write_preview_result.get("write_preview") is not True:
        issues.append("QUEUE_WRITE_PREVIEW_NOT_TRUE")
    if queue_write_preview_result.get("write_stage") != "order_queued_record_preview_created":
        issues.append("QUEUE_WRITE_STAGE_NOT_READY")
    if queue_write_preview_result.get("next_stage") != "QUEUE_WRITE_REQUIRED":
        issues.append("QUEUE_NEXT_STAGE_NOT_QUEUE_WRITE_REQUIRED")
    if queue_write_preview_result.get("preview_only") is not True:
        issues.append("QUEUE_PREVIEW_ONLY_NOT_TRUE")
    if queue_write_preview_result.get("no_write") is not True:
        issues.append("QUEUE_NO_WRITE_NOT_TRUE")

    record = _queue_record(queue_write_preview_result)
    if not record:
        issues.append("MISSING_ORDER_QUEUED_RECORD_PREVIEW")
    elif record.get("status") != "ORDER_QUEUED":
        issues.append("QUEUE_RECORD_STATUS_NOT_ORDER_QUEUED")

    runtime_identity = _runtime_identity(runtime_commit_result)
    if not any(runtime_identity.values()):
        runtime_identity = _runtime_identity_from_paths(runtime_commit_result)
    queue_identity = _queue_identity(record)
    identity_checks: dict[str, Any] = {}
    for field in ("execution_id", "order_id", "request_hash", "lock_id"):
        runtime_value = runtime_identity.get(field, "")
        queue_value = queue_identity.get(field, "")
        match = bool(runtime_value) and bool(queue_value) and runtime_value == queue_value
        identity_checks[field] = {
            "runtime": runtime_value,
            "queue": queue_value,
            "match": match,
        }
        if not match:
            issues.append(f"IDENTITY_MISMATCH_{field.upper()}")

    if not required_confirmations["manual_queue_write_confirmed"]:
        issues.append("MANUAL_QUEUE_WRITE_CONFIRMATION_REQUIRED")
    if required_confirmations["runtime_queue_path"] and not required_confirmations["manual_runtime_queue_write_confirmed"]:
        issues.append("MANUAL_RUNTIME_QUEUE_WRITE_CONFIRMATION_REQUIRED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            queue_commit_allowed=False,
            identity_checks=identity_checks,
            required_confirmations=required_confirmations,
            issues=issues,
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        queue_commit_allowed=True,
        identity_checks=identity_checks,
        required_confirmations=required_confirmations,
        warnings=warnings,
    )
