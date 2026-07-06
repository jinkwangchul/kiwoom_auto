# -*- coding: utf-8 -*-
"""Preview-only orchestrator for runtime status updates.

This layer calls an in-memory runtime status updater only after the readiness
policy opens the path. It intentionally does not write runtime files, update
queue records, release locks, recall brokers, or re-run result recording.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


ORCHESTRATOR_TYPE = "EXECUTION_RUNTIME_STATUS_UPDATE_ORCHESTRATOR"
STATUS_UPDATED = "RUNTIME_STATUS_UPDATED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
POLICY_READY = "READY_TO_UPDATE_RUNTIME_STATUS"
RECORDER_RECORDED = "BROKER_RESULT_RECORDED"
NEXT_STAGE_REQUIRED = "RUNTIME_STATUS_UPDATE_REQUIRED"
NEXT_STAGE_QUEUE_STATUS_UPDATE_REQUIRED = "QUEUE_STATUS_UPDATE_REQUIRED"
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
    runtime_status_update_called: bool = False,
    runtime_status_record: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "runtime_status_update_called": runtime_status_update_called,
        "runtime_status_record": deepcopy(runtime_status_record) if isinstance(runtime_status_record, dict) else None,
        "runtime_write": False,
        "queue_write": False,
        "lock_release_called": False,
        "next_stage": next_stage,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _preview_runtime_status_updater(broker_result_recorder_orchestrator_result: dict[str, Any]) -> dict[str, Any]:
    broker_result_record = _as_dict(broker_result_recorder_orchestrator_result.get("broker_result_record"))
    broker_result = _as_dict(broker_result_record.get("broker_result"))
    return {
        "record_type": "RUNTIME_STATUS_UPDATE_PREVIEW",
        "updated": True,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "execution_status": "BROKER_RESULT_RECORDED",
        "order_id": broker_result_record.get("order_id") or broker_result.get("order_id"),
        "request_hash": broker_result_record.get("request_hash") or broker_result.get("request_hash"),
        "broker_order_no": broker_result_record.get("broker_order_no") or broker_result.get("broker_order_no"),
        "broker_result_record": deepcopy(broker_result_record),
        "issues": [],
        "warnings": [],
    }


def orchestrate_runtime_status_update(
    runtime_status_update_readiness_policy_result: Any,
    broker_result_recorder_orchestrator_result: Any,
    runtime_status_updater: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Call an in-memory runtime status updater only after readiness approval."""
    if not isinstance(runtime_status_update_readiness_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_RUNTIME_STATUS_UPDATE_READINESS_POLICY_RESULT"])
    if not isinstance(broker_result_recorder_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_BROKER_RESULT_RECORDER_ORCHESTRATOR_RESULT"])

    warnings = _as_list(runtime_status_update_readiness_policy_result.get("warnings")) + _as_list(
        broker_result_recorder_orchestrator_result.get("warnings")
    )

    policy_status = _text(runtime_status_update_readiness_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(runtime_status_update_readiness_policy_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_READINESS_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(runtime_status_update_readiness_policy_result.get("issues"))
            or ["RUNTIME_STATUS_UPDATE_READINESS_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if runtime_status_update_readiness_policy_result.get("runtime_status_update_allowed") is not True:
        return _blocked("RUNTIME_STATUS_UPDATE_NOT_ALLOWED", warnings)

    recorder_status = _text(broker_result_recorder_orchestrator_result.get("status"))
    if recorder_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(broker_result_recorder_orchestrator_result.get("issues"))
            or ["BROKER_RESULT_RECORDER_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if recorder_status != RECORDER_RECORDED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(broker_result_recorder_orchestrator_result.get("issues"))
            or ["BROKER_RESULT_RECORDER_ORCHESTRATOR_NOT_RECORDED"],
            warnings=warnings,
        )
    if broker_result_recorder_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("RUNTIME_STATUS_UPDATE_NEXT_STAGE_REQUIRED", warnings)
    if broker_result_recorder_orchestrator_result.get("result_record_called") is not True:
        return _blocked("RESULT_RECORD_CALLED_NOT_TRUE", warnings)

    broker_result_record = _as_dict(broker_result_recorder_orchestrator_result.get("broker_result_record"))
    if not broker_result_record:
        return _blocked("BROKER_RESULT_RECORD_REQUIRED", warnings)

    updater = runtime_status_updater or _preview_runtime_status_updater
    if not callable(updater):
        return _blocked("RUNTIME_STATUS_UPDATER_NOT_CALLABLE", warnings)

    try:
        update_result = updater(deepcopy(broker_result_recorder_orchestrator_result))
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _result(
            status=STATUS_BLOCKED,
            runtime_status_update_called=True,
            issues=[f"RUNTIME_STATUS_UPDATER_EXCEPTION: {exc}"],
            warnings=warnings,
        )

    if not isinstance(update_result, dict):
        return _result(
            status=STATUS_INVALID,
            runtime_status_update_called=True,
            issues=["MALFORMED_RUNTIME_STATUS_RECORD"],
            warnings=warnings,
        )
    if update_result.get("updated") is not True:
        return _result(
            status=STATUS_BLOCKED,
            runtime_status_update_called=True,
            runtime_status_record=update_result,
            issues=_as_list(update_result.get("issues")) or ["RUNTIME_STATUS_NOT_UPDATED"],
            warnings=warnings + _as_list(update_result.get("warnings")),
        )

    return _result(
        status=STATUS_UPDATED,
        runtime_status_update_called=True,
        runtime_status_record=update_result,
        next_stage=NEXT_STAGE_QUEUE_STATUS_UPDATE_REQUIRED,
        warnings=warnings + _as_list(update_result.get("warnings")),
    )
