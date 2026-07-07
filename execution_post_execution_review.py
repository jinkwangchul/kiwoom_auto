# -*- coding: utf-8 -*-
"""Preview-only final review for an execution pipeline.

This layer reviews the completed lock release result and decides whether the
preview pipeline can be treated as complete. It does not write runtime/queue
files, update GUI state, re-release locks, or recall any prior pipeline step.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


REVIEW_TYPE = "EXECUTION_POST_EXECUTION_REVIEW"
STATUS_COMPLETED = "EXECUTION_COMPLETED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
LOCK_RELEASED = "LOCK_RELEASED"
NEXT_STAGE_REQUIRED = "POST_EXECUTION_REVIEW_REQUIRED"
NEXT_STAGE_COMPLETE = "EXECUTION_COMPLETE"
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
    execution_completed: bool = False,
    lock_release_record: dict[str, Any] | None = None,
    next_stage: str = NEXT_STAGE_BLOCKED,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "review_type": REVIEW_TYPE,
        "status": status,
        "execution_completed": execution_completed,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "gui_update_called": False,
        "next_stage": next_stage,
        "lock_release_record": deepcopy(lock_release_record) if isinstance(lock_release_record, dict) else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def review_post_execution(lock_release_orchestrator_result: Any) -> dict[str, Any]:
    """Review a lock release orchestrator result without side effects."""
    if not isinstance(lock_release_orchestrator_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_LOCK_RELEASE_ORCHESTRATOR_RESULT"])

    warnings = _as_list(lock_release_orchestrator_result.get("warnings"))
    lock_status = _text(lock_release_orchestrator_result.get("status"))

    if lock_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(lock_release_orchestrator_result.get("issues")) or ["LOCK_RELEASE_ORCHESTRATOR_INVALID"],
            warnings=warnings,
        )
    if lock_status != LOCK_RELEASED:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(lock_release_orchestrator_result.get("issues")) or ["LOCK_RELEASE_ORCHESTRATOR_NOT_RELEASED"],
            warnings=warnings,
        )

    issues: list[str] = []
    if lock_release_orchestrator_result.get("next_stage") != NEXT_STAGE_REQUIRED:
        issues.append("POST_EXECUTION_REVIEW_NEXT_STAGE_REQUIRED")
    if lock_release_orchestrator_result.get("lock_release_called") is not True:
        issues.append("LOCK_RELEASE_CALLED_NOT_TRUE")
    lock_release_record = _as_dict(lock_release_orchestrator_result.get("lock_release_record"))
    if not lock_release_record:
        issues.append("LOCK_RELEASE_RECORD_REQUIRED")

    if issues:
        return _result(status=STATUS_BLOCKED, issues=issues, warnings=warnings)

    return _result(
        status=STATUS_COMPLETED,
        execution_completed=True,
        lock_release_record=lock_release_record,
        next_stage=NEXT_STAGE_COMPLETE,
        warnings=warnings,
    )
