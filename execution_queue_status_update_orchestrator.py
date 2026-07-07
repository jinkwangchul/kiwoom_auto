# -*- coding: utf-8 -*-
"""Preview-only orchestrator for queue status updates.

This layer calls an in-memory queue status updater only after the readiness
policy opens the path. It intentionally does not write order_queue.json or any
runtime files, release locks, recall runtime status updates, re-run result
recording, or call broker/Kiwoom flows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


ORCHESTRATOR_TYPE = "EXECUTION_QUEUE_STATUS_UPDATE_ORCHESTRATOR"
STATUS_UPDATED = "QUEUE_STATUS_UPDATED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
POLICY_READY = "READY_TO_UPDATE_QUEUE_STATUS"
RUNTIME_STATUS_UPDATED = "RUNTIME_STATUS_UPDATED"
NEXT_STAGE_REQUIRED = "QUEUE_STATUS_UPDATE_REQUIRED"
NEXT_STAGE_LOCK_RELEASE_REQUIRED = "LOCK_RELEASE_REQUIRED"
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
    queue_status_update_called: bool = False,
    queue_status_record: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "queue_status_update_called": queue_status_update_called,
        "queue_status_record": deepcopy(queue_status_record) if isinstance(queue_status_record, dict) else None,
        "runtime_write": False,
        "queue_write": False,
        "lock_release_called": False,
        "next_stage": next_stage,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _preview_queue_status_updater(runtime_status_update_orchestrator_result: dict[str, Any]) -> dict[str, Any]:
    runtime_status_record = _as_dict(runtime_status_update_orchestrator_result.get("runtime_status_record"))
    return {
        "record_type": "QUEUE_STATUS_UPDATE_PREVIEW",
        "updated": True,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_status": "BROKER_RESULT_RECORDED",
        "order_id": runtime_status_record.get("order_id"),
        "request_hash": runtime_status_record.get("request_hash"),
        "broker_order_no": runtime_status_record.get("broker_order_no"),
        "runtime_status_record": deepcopy(runtime_status_record),
        "issues": [],
        "warnings": [],
    }


def orchestrate_queue_status_update(
    queue_status_update_readiness_policy_result: Any,
    runtime_status_update_orchestrator_result: Any,
    queue_status_updater: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Call an in-memory queue status updater only after readiness approval."""
    if not isinstance(queue_status_update_readiness_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_QUEUE_STATUS_UPDATE_READINESS_POLICY_RESULT"])
    if not isinstance(runtime_status_update_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_RUNTIME_STATUS_UPDATE_ORCHESTRATOR_RESULT"])

    warnings = _as_list(queue_status_update_readiness_policy_result.get("warnings")) + _as_list(
        runtime_status_update_orchestrator_result.get("warnings")
    )

    policy_status = _text(queue_status_update_readiness_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(queue_status_update_readiness_policy_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_READINESS_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(queue_status_update_readiness_policy_result.get("issues"))
            or ["QUEUE_STATUS_UPDATE_READINESS_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if queue_status_update_readiness_policy_result.get("queue_status_update_allowed") is not True:
        return _blocked("QUEUE_STATUS_UPDATE_NOT_ALLOWED", warnings)

    runtime_status = _text(runtime_status_update_orchestrator_result.get("status"))
    if runtime_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(runtime_status_update_orchestrator_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if runtime_status != RUNTIME_STATUS_UPDATED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(runtime_status_update_orchestrator_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_ORCHESTRATOR_NOT_UPDATED"],
            warnings=warnings,
        )
    if runtime_status_update_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("QUEUE_STATUS_UPDATE_NEXT_STAGE_REQUIRED", warnings)
    if runtime_status_update_orchestrator_result.get("runtime_status_update_called") is not True:
        return _blocked("RUNTIME_STATUS_UPDATE_CALLED_NOT_TRUE", warnings)

    runtime_status_record = _as_dict(runtime_status_update_orchestrator_result.get("runtime_status_record"))
    if not runtime_status_record:
        return _blocked("RUNTIME_STATUS_RECORD_REQUIRED", warnings)

    updater = queue_status_updater or _preview_queue_status_updater
    if not callable(updater):
        return _blocked("QUEUE_STATUS_UPDATER_NOT_CALLABLE", warnings)

    try:
        update_result = updater(deepcopy(runtime_status_update_orchestrator_result))
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _result(
            status=STATUS_BLOCKED,
            queue_status_update_called=True,
            issues=[f"QUEUE_STATUS_UPDATER_EXCEPTION: {exc}"],
            warnings=warnings,
        )

    if not isinstance(update_result, dict):
        return _result(
            status=STATUS_INVALID,
            queue_status_update_called=True,
            issues=["MALFORMED_QUEUE_STATUS_RECORD"],
            warnings=warnings,
        )
    if update_result.get("updated") is not True:
        return _result(
            status=STATUS_BLOCKED,
            queue_status_update_called=True,
            queue_status_record=update_result,
            issues=_as_list(update_result.get("issues")) or ["QUEUE_STATUS_NOT_UPDATED"],
            warnings=warnings + _as_list(update_result.get("warnings")),
        )

    return _result(
        status=STATUS_UPDATED,
        queue_status_update_called=True,
        queue_status_record=update_result,
        next_stage=NEXT_STAGE_LOCK_RELEASE_REQUIRED,
        warnings=warnings + _as_list(update_result.get("warnings")),
    )
