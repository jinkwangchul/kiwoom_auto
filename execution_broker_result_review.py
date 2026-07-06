# -*- coding: utf-8 -*-
"""Preview-only review for broker dispatch results.

This layer reviews a Broker Dispatch Orchestrator result and decides whether it
can move to a future result-recording layer. It does not write runtime files,
commit queue changes, release locks, or record execution results.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


REVIEW_TYPE = "EXECUTION_BROKER_RESULT_REVIEW"
STATUS_READY = "READY_FOR_RESULT_RECORD"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
DISPATCH_STATUS_SUBMITTED = "BROKER_DISPATCH_SUBMITTED"
NEXT_STAGE_REQUIRED = "BROKER_RESULT_REVIEW_REQUIRED"
NEXT_STAGE_RESULT_RECORD_REQUIRED = "BROKER_RESULT_RECORD_REQUIRED"
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
    broker_called: bool = True,
    broker_result: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "review_type": REVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "broker_called": bool(broker_called),
        "kiwoom_called": False,
        "next_stage": next_stage,
        "broker_result": deepcopy(broker_result) if isinstance(broker_result, dict) else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _blocked(reason: str, *, broker_called: bool = True, warnings: list[str] | None = None) -> dict[str, Any]:
    return _result(status=STATUS_BLOCKED, broker_called=broker_called, issues=[reason], warnings=warnings)


def _broker_result_has_success_signal(broker_result: dict[str, Any]) -> bool:
    status_values = [
        _text(broker_result.get("broker_status")).upper(),
        _text(broker_result.get("status")).upper(),
        _text(broker_result.get("result")).upper(),
        _text(broker_result.get("dispatch_status")).upper(),
    ]
    blocked_words = ("BLOCK", "FAIL", "ERROR", "REJECT", "EXCEPTION", "INVALID")
    if any(any(word in status for word in blocked_words) for status in status_values if status):
        return False
    status = next((value for value in status_values if value), "")
    if status:
        return True
    return bool(broker_result)


def review_broker_dispatch_result(broker_dispatch_orchestrator_result: Any) -> dict[str, Any]:
    """Review broker dispatch output without recording or writing anything."""
    if not isinstance(broker_dispatch_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, broker_called=False, issues=["MALFORMED_BROKER_DISPATCH_ORCHESTRATOR_RESULT"])

    warnings = _as_list(broker_dispatch_orchestrator_result.get("warnings"))
    broker_called = broker_dispatch_orchestrator_result.get("broker_dispatch_called") is True
    dispatch_status = _text(broker_dispatch_orchestrator_result.get("status"))

    if dispatch_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            broker_called=broker_called,
            issues=_as_list(broker_dispatch_orchestrator_result.get("issues")) or ["BROKER_DISPATCH_INVALID"],
            warnings=warnings,
        )
    if dispatch_status != DISPATCH_STATUS_SUBMITTED:
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            issues=_as_list(broker_dispatch_orchestrator_result.get("issues")) or ["BROKER_DISPATCH_NOT_SUBMITTED"],
            warnings=warnings,
        )

    if broker_dispatch_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        return _blocked("BROKER_RESULT_REVIEW_NEXT_STAGE_REQUIRED", broker_called=broker_called, warnings=warnings)
    if not broker_called:
        return _blocked("BROKER_DISPATCH_CALLED_NOT_TRUE", broker_called=False, warnings=warnings)

    broker_result = broker_dispatch_orchestrator_result.get("broker_result")
    if broker_result is None:
        return _blocked("BROKER_RESULT_REQUIRED", broker_called=broker_called, warnings=warnings)
    if not isinstance(broker_result, dict):
        return _result(status=STATUS_INVALID, broker_called=broker_called, issues=["MALFORMED_BROKER_RESULT"], warnings=warnings)
    if not broker_result:
        return _result(status=STATUS_INVALID, broker_called=broker_called, issues=["EMPTY_BROKER_RESULT"], warnings=warnings)

    issues = _as_list(broker_dispatch_orchestrator_result.get("issues"))
    if issues:
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            broker_result=broker_result,
            issues=issues,
            warnings=warnings,
        )

    if any(key in broker_result for key in ("exception", "error", "blocked_reason", "blocked_reasons")):
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            broker_result=broker_result,
            issues=["BROKER_RESULT_CONTAINS_BLOCKING_FIELD"],
            warnings=warnings,
        )

    if not _broker_result_has_success_signal(broker_result):
        return _result(
            status=STATUS_BLOCKED,
            broker_called=broker_called,
            broker_result=broker_result,
            issues=["BROKER_RESULT_NOT_SUCCESSFUL"],
            warnings=warnings,
        )

    return _result(
        status=STATUS_READY,
        broker_called=True,
        broker_result=broker_result,
        next_stage=NEXT_STAGE_RESULT_RECORD_REQUIRED,
        warnings=warnings,
    )
