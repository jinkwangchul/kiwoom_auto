# -*- coding: utf-8 -*-
"""Preview-only orchestrator for lock release.

This layer calls an in-memory lock releaser only after the readiness policy
opens the path. It intentionally does not write order_locks.json or any
runtime/queue files, recall queue/runtime status updates, re-run result
recording, or call broker/Kiwoom flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


ORCHESTRATOR_TYPE = "EXECUTION_LOCK_RELEASE_ORCHESTRATOR"
STATUS_RELEASED = "LOCK_RELEASED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
POLICY_READY = "READY_TO_RELEASE_LOCK"
QUEUE_STATUS_UPDATED = "QUEUE_STATUS_UPDATED"
NEXT_STAGE_REQUIRED = "LOCK_RELEASE_REQUIRED"
NEXT_STAGE_POST_EXECUTION_REVIEW_REQUIRED = "POST_EXECUTION_REVIEW_REQUIRED"
NEXT_STAGE_BLOCKED = "BLOCKED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    lock_release_called: bool = False,
    lock_release_record: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "lock_release_called": lock_release_called,
        "lock_release_record": deepcopy(lock_release_record) if isinstance(lock_release_record, dict) else None,
        "runtime_write": False,
        "queue_write": False,
        "next_stage": next_stage,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _preview_lock_releaser(queue_status_update_orchestrator_result: dict[str, Any]) -> dict[str, Any]:
    queue_status_record = _as_dict(queue_status_update_orchestrator_result.get("queue_status_record"))
    runtime_status_record = _as_dict(queue_status_record.get("runtime_status_record"))
    return {
        "record_type": "LOCK_RELEASE_PREVIEW",
        "released": True,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "lock_status": "RELEASED",
        "order_id": queue_status_record.get("order_id") or runtime_status_record.get("order_id"),
        "request_hash": queue_status_record.get("request_hash") or runtime_status_record.get("request_hash"),
        "broker_order_no": queue_status_record.get("broker_order_no") or runtime_status_record.get("broker_order_no"),
        "queue_status_record": deepcopy(queue_status_record),
        "issues": [],
        "warnings": [],
    }


def orchestrate_lock_release(
    lock_release_readiness_policy_result: Any,
    queue_status_update_orchestrator_result: Any,
    lock_releaser: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Call an in-memory lock releaser only after readiness approval."""
    if not isinstance(lock_release_readiness_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_LOCK_RELEASE_READINESS_POLICY_RESULT"])
    if not isinstance(queue_status_update_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_QUEUE_STATUS_UPDATE_ORCHESTRATOR_RESULT"])

    warnings = _as_list(lock_release_readiness_policy_result.get("warnings")) + _as_list(
        queue_status_update_orchestrator_result.get("warnings")
    )

    policy_status = _text(lock_release_readiness_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(lock_release_readiness_policy_result.get("issues"))
            or ["LOCK_RELEASE_READINESS_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(lock_release_readiness_policy_result.get("issues"))
            or ["LOCK_RELEASE_READINESS_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if lock_release_readiness_policy_result.get("lock_release_allowed") is not True:
        return _blocked("LOCK_RELEASE_NOT_ALLOWED", warnings)

    queue_status = _text(queue_status_update_orchestrator_result.get("status"))
    if queue_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(queue_status_update_orchestrator_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if queue_status != QUEUE_STATUS_UPDATED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(queue_status_update_orchestrator_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_ORCHESTRATOR_NOT_UPDATED"],
            warnings=warnings,
        )
    if queue_status_update_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("LOCK_RELEASE_NEXT_STAGE_REQUIRED", warnings)
    if queue_status_update_orchestrator_result.get("queue_status_update_called") is not True:
        return _blocked("QUEUE_STATUS_UPDATE_CALLED_NOT_TRUE", warnings)

    queue_status_record = _as_dict(queue_status_update_orchestrator_result.get("queue_status_record"))
    if not queue_status_record:
        return _blocked("QUEUE_STATUS_RECORD_REQUIRED", warnings)

    releaser = lock_releaser or _preview_lock_releaser
    if not callable(releaser):
        return _blocked("LOCK_RELEASER_NOT_CALLABLE", warnings)

    try:
        release_result = releaser(deepcopy(queue_status_update_orchestrator_result))
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _result(
            status=STATUS_BLOCKED,
            lock_release_called=True,
            issues=[f"LOCK_RELEASER_EXCEPTION: {exc}"],
            warnings=warnings,
        )

    if not isinstance(release_result, dict):
        return _result(
            status=STATUS_INVALID,
            lock_release_called=True,
            issues=["MALFORMED_LOCK_RELEASE_RECORD"],
            warnings=warnings,
        )
    if release_result.get("released") is not True:
        return _result(
            status=STATUS_BLOCKED,
            lock_release_called=True,
            lock_release_record=release_result,
            issues=_as_list(release_result.get("issues")) or ["LOCK_NOT_RELEASED"],
            warnings=warnings + _as_list(release_result.get("warnings")),
        )

    return _result(
        status=STATUS_RELEASED,
        lock_release_called=True,
        lock_release_record=release_result,
        next_stage=NEXT_STAGE_POST_EXECUTION_REVIEW_REQUIRED,
        warnings=warnings + _as_list(release_result.get("warnings")),
    )
