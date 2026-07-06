# -*- coding: utf-8 -*-
"""Preview-only orchestrator for broker result recording.

The existing send_order_result_recorder writes to an explicit queue file, so it
is intentionally not wired here. This layer calls only an in-memory recorder
callable, defaulting to a preview recorder, and keeps runtime/queue/lock-release
side effects closed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


ORCHESTRATOR_TYPE = "EXECUTION_BROKER_RESULT_RECORDER_ORCHESTRATOR"
STATUS_RECORDED = "BROKER_RESULT_RECORDED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
POLICY_READY = "READY_TO_RECORD_BROKER_RESULT"
REVIEW_READY = "READY_FOR_RESULT_RECORD"
NEXT_STAGE_REQUIRED = "BROKER_RESULT_RECORD_REQUIRED"
NEXT_STAGE_RUNTIME_STATUS_UPDATE_REQUIRED = "RUNTIME_STATUS_UPDATE_REQUIRED"
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
    result_record_called: bool = False,
    broker_result_record: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "status": status,
        "result_record_called": result_record_called,
        "runtime_write": False,
        "queue_write": False,
        "lock_release_called": False,
        "broker_result_record": deepcopy(broker_result_record) if isinstance(broker_result_record, dict) else None,
        "next_stage": next_stage,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, issues=[reason], warnings=warnings)


def _preview_result_recorder(broker_result_review_result: dict[str, Any]) -> dict[str, Any]:
    broker_result = _as_dict(broker_result_review_result.get("broker_result"))
    return {
        "record_type": "BROKER_RESULT_RECORD_PREVIEW",
        "recorded": True,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "broker_result": deepcopy(broker_result),
        "order_id": broker_result.get("order_id"),
        "request_hash": broker_result.get("request_hash"),
        "broker_order_no": broker_result.get("broker_order_no"),
        "issues": [],
        "warnings": [],
    }


def orchestrate_broker_result_recording(
    broker_result_record_readiness_policy_result: Any,
    broker_result_review_result: Any,
    result_recorder: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Call an in-memory result recorder only after readiness approval."""
    if not isinstance(broker_result_record_readiness_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_BROKER_RESULT_RECORD_READINESS_POLICY_RESULT"])
    if not isinstance(broker_result_review_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_BROKER_RESULT_REVIEW_RESULT"])

    warnings = _as_list(broker_result_record_readiness_policy_result.get("warnings")) + _as_list(
        broker_result_review_result.get("warnings")
    )

    policy_status = _text(broker_result_record_readiness_policy_result.get("status"))
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(broker_result_record_readiness_policy_result.get("issues"))
            or ["BROKER_RESULT_RECORD_READINESS_POLICY_INVALID"],
            warnings=warnings,
        )
    if policy_status != POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(broker_result_record_readiness_policy_result.get("issues"))
            or ["BROKER_RESULT_RECORD_READINESS_POLICY_NOT_READY"],
            warnings=warnings,
        )
    if broker_result_record_readiness_policy_result.get("result_record_allowed") is not True:
        return _blocked("BROKER_RESULT_RECORD_NOT_ALLOWED", warnings)

    review_status = _text(broker_result_review_result.get("status"))
    if review_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(broker_result_review_result.get("issues")) or ["BROKER_RESULT_REVIEW_INVALID"],
            warnings=warnings,
        )
    if review_status != REVIEW_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(broker_result_review_result.get("issues")) or ["BROKER_RESULT_REVIEW_NOT_READY"],
            warnings=warnings,
        )
    if broker_result_review_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("BROKER_RESULT_RECORD_NEXT_STAGE_REQUIRED", warnings)
    if broker_result_review_result.get("broker_called") is not True:
        return _blocked("BROKER_CALLED_NOT_TRUE", warnings)

    broker_result = _as_dict(broker_result_review_result.get("broker_result"))
    if not broker_result:
        return _blocked("BROKER_RESULT_REQUIRED", warnings)

    recorder = result_recorder or _preview_result_recorder
    if not callable(recorder):
        return _blocked("RESULT_RECORDER_NOT_CALLABLE", warnings)

    try:
        record_result = recorder(deepcopy(broker_result_review_result))
    except Exception as exc:  # pragma: no cover - exercised by tests
        return _result(
            status=STATUS_BLOCKED,
            result_record_called=True,
            issues=[f"BROKER_RESULT_RECORDER_EXCEPTION: {exc}"],
            warnings=warnings,
        )

    if not isinstance(record_result, dict):
        return _result(
            status=STATUS_INVALID,
            result_record_called=True,
            issues=["MALFORMED_BROKER_RESULT_RECORD"],
            warnings=warnings,
        )
    if record_result.get("recorded") is not True:
        return _result(
            status=STATUS_BLOCKED,
            result_record_called=True,
            broker_result_record=record_result,
            issues=_as_list(record_result.get("issues")) or ["BROKER_RESULT_RECORD_NOT_RECORDED"],
            warnings=warnings + _as_list(record_result.get("warnings")),
        )

    return _result(
        status=STATUS_RECORDED,
        result_record_called=True,
        broker_result_record=record_result,
        next_stage=NEXT_STAGE_RUNTIME_STATUS_UPDATE_REQUIRED,
        warnings=warnings + _as_list(record_result.get("warnings")),
    )
