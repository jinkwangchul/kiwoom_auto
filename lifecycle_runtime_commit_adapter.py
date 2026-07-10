# -*- coding: utf-8 -*-
"""Adapter from Lifecycle commit requests to the M6 runtime commit executor.

This module is intentionally thin. It does not perform file IO, backup,
rollback, verification, locking, token handling, manifest persistence, journal
writing, GUI updates, broker calls, or runtime path selection. All real runtime
commit behavior belongs to ``runtime_commit_real_executor.execute_runtime_commit``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


ADAPTER_TYPE = "LIFECYCLE_RUNTIME_COMMIT_ADAPTER"

STATUS_COMMITTED = "COMMITTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ABORTED = "ABORTED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

M6_STATUS_MANUAL_RESTORE_REQUIRED = "MANUAL_RESTORE_REQUIRED"

REQUIRED_LIFECYCLE_FIELDS = (
    "lifecycle_id",
    "commit_id",
    "transaction_id",
    "requested_action",
    "source_stage",
    "runtime_commit_boundary_status",
    "preview_only",
    "metadata",
)


def _safety_flags() -> dict[str, bool]:
    return {
        "direct_write_called": False,
        "backup_called_by_adapter": False,
        "rollback_called_by_adapter": False,
        "verify_called_by_adapter": False,
        "lock_called_by_adapter": False,
        "token_called_by_adapter": False,
        "gui_update_called": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_called": False,
        "sqlite_write": False,
        "rules_write": False,
    }


def _status_from_runtime_result(runtime_result: Any) -> str:
    if not isinstance(runtime_result, dict):
        return STATUS_INVALID
    status = runtime_result.get("execute_status") or runtime_result.get("status")
    mapping = {
        STATUS_COMMITTED: STATUS_COMMITTED,
        STATUS_BLOCKED: STATUS_BLOCKED,
        STATUS_INVALID: STATUS_INVALID,
        STATUS_ABORTED: STATUS_ABORTED,
        STATUS_ROLLED_BACK: STATUS_ROLLED_BACK,
        M6_STATUS_MANUAL_RESTORE_REQUIRED: STATUS_REVIEW_REQUIRED,
    }
    return mapping.get(status, STATUS_INVALID)


def _lifecycle_result(
    *,
    status: str,
    lifecycle_id: str,
    commit_id: str,
    transaction_id: str,
    runtime_result: Any,
) -> dict[str, Any]:
    runtime_status = None
    if isinstance(runtime_result, dict):
        runtime_status = runtime_result.get("execute_status") or runtime_result.get("status")
    return {
        "status": status,
        "lifecycle_id": lifecycle_id,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "runtime_commit_status": runtime_status,
    }


def _result(
    *,
    status: str,
    lifecycle_id: str = "",
    commit_id: str = "",
    transaction_id: str = "",
    runtime_result: Any = None,
    executor_called: bool = False,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "adapter_type": ADAPTER_TYPE,
        "adapter_status": status,
        "lifecycle_id": lifecycle_id,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "runtime_commit_result": deepcopy(runtime_result) if isinstance(runtime_result, dict) else runtime_result,
        "lifecycle_commit_result": _lifecycle_result(
            status=status,
            lifecycle_id=lifecycle_id,
            commit_id=commit_id,
            transaction_id=transaction_id,
            runtime_result=runtime_result,
        ),
        "executor_called": executor_called,
        "legacy_executor_called": False,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }
    payload.update(_safety_flags())
    return payload


def _validate_lifecycle_request(value: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, ["lifecycle_commit_request must be a dict"]
    missing = [field for field in REQUIRED_LIFECYCLE_FIELDS if field not in value]
    if missing:
        return value, [f"missing lifecycle field: {field}" for field in missing]
    if value.get("preview_only") is not True:
        return value, ["lifecycle_commit_request.preview_only must be true"]
    if not isinstance(value.get("metadata"), dict):
        return value, ["lifecycle_commit_request.metadata must be a dict"]
    return value, []


def _default_executor() -> Callable[..., dict[str, Any]]:
    from runtime_commit_real_executor import execute_runtime_commit

    return execute_runtime_commit


def adapt_and_execute_lifecycle_runtime_commit(
    *,
    lifecycle_commit_request: Any,
    gate_result: Any,
    transaction_manifest: Any,
    storage_plan: Any,
    guard_plan: Any,
    token_storage_plan: Any,
    expected_targets: Any,
    new_targets: Any,
    consumer_id: Any,
    executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate Lifecycle commit input into an M6 runtime commit call."""
    request, validation_issues = _validate_lifecycle_request(lifecycle_commit_request)
    lifecycle_id = str(request.get("lifecycle_id", "")) if isinstance(request, dict) else ""
    commit_id = str(request.get("commit_id", "")) if isinstance(request, dict) else ""
    transaction_id = str(request.get("transaction_id", "")) if isinstance(request, dict) else ""

    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            lifecycle_id=lifecycle_id,
            commit_id=commit_id,
            transaction_id=transaction_id,
            issues=validation_issues,
        )

    runtime_executor = executor or _default_executor()
    try:
        runtime_result = runtime_executor(
            gate_result=deepcopy(gate_result),
            transaction_manifest=deepcopy(transaction_manifest),
            storage_plan=deepcopy(storage_plan),
            guard_plan=deepcopy(guard_plan),
            token_storage_plan=deepcopy(token_storage_plan),
            expected_targets=deepcopy(expected_targets),
            new_targets=deepcopy(new_targets),
            consumer_id=deepcopy(consumer_id),
        )
    except Exception as exc:
        return _result(
            status=STATUS_ABORTED,
            lifecycle_id=lifecycle_id,
            commit_id=commit_id,
            transaction_id=transaction_id,
            executor_called=True,
            issues=[f"executor exception: {type(exc).__name__}"],
        )

    status = _status_from_runtime_result(runtime_result)
    issues = []
    if status == STATUS_INVALID:
        runtime_status = None
        if isinstance(runtime_result, dict):
            runtime_status = runtime_result.get("execute_status") or runtime_result.get("status")
            issues.extend(runtime_result.get("issues") or [])
        if runtime_status not in {
            STATUS_COMMITTED,
            STATUS_BLOCKED,
            STATUS_INVALID,
            STATUS_ABORTED,
            STATUS_ROLLED_BACK,
            M6_STATUS_MANUAL_RESTORE_REQUIRED,
        }:
            issues.append(f"unknown runtime execute_status: {runtime_status}")

    return _result(
        status=status,
        lifecycle_id=lifecycle_id,
        commit_id=commit_id,
        transaction_id=transaction_id,
        runtime_result=runtime_result,
        executor_called=True,
        issues=issues,
    )
