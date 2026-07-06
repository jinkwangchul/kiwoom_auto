# -*- coding: utf-8 -*-
"""Manual queue commit service.

This module is a manual-only wrapper around commit_execution_queue_write. It is
not connected to GUI preview buttons, timers, automatic loops, or SendOrder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from execution_queue_writer import commit_execution_queue_write


NEXT_STAGE_BLOCKED = "BLOCKED"
POLICY_TYPE = "EXECUTION_QUEUE_COMMIT_READINESS_POLICY"
POLICY_READY = "READY_TO_COMMIT_QUEUE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_queue_write_confirmed") is True
        or ctx.get("operator_confirmed_for_queue_write") is True
    )


def _runtime_queue_path(queue_path: str | Path | None) -> bool:
    if queue_path is None:
        return False

    path = Path(queue_path)
    return path.name == "order_queue.json" and path.parent.name == "runtime"


def _runtime_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return ctx.get("manual_runtime_queue_write_confirmed") is True


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "manual_commit": False,
        "commit_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "commit_result": None,
        "blocked_reasons": [reason],
    }


def _invalid(stage: str, reason: str) -> dict[str, Any]:
    return {
        "status": "INVALID",
        "manual_commit": False,
        "commit_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "commit_result": None,
        "blocked_reasons": [reason],
    }


def _policy_issue(policy_result: Any) -> dict[str, Any] | None:
    if policy_result is None:
        return _blocked("queue_commit_readiness_policy", "queue commit readiness policy is required")
    if not isinstance(policy_result, dict):
        return _invalid("queue_commit_readiness_policy", "queue commit readiness policy must be a dict")

    policy = _as_dict(policy_result)
    if policy.get("policy_type") != POLICY_TYPE:
        return _invalid("queue_commit_readiness_policy", "queue commit readiness policy type is invalid")
    if policy.get("preview_only") is not True:
        return _invalid("queue_commit_readiness_policy", "queue commit readiness policy preview_only is not true")
    if policy.get("queue_write") is not False:
        return _invalid("queue_commit_readiness_policy", "queue commit readiness policy queue_write is not false")
    if policy.get("runtime_write") is not False:
        return _invalid("queue_commit_readiness_policy", "queue commit readiness policy runtime_write is not false")
    if policy.get("status") == "INVALID":
        return _invalid(
            "queue_commit_readiness_policy",
            "; ".join(list(policy.get("issues") or [])) or "queue commit readiness policy is invalid",
        )
    if policy.get("status") != POLICY_READY:
        return _blocked(
            "queue_commit_readiness_policy",
            "; ".join(list(policy.get("issues") or [])) or "queue commit readiness policy is not ready",
        )
    if policy.get("queue_commit_allowed") is not True:
        return _blocked("queue_commit_readiness_policy", "queue commit readiness policy does not allow queue commit")
    return None


def commit_execution_queue_manually(
    queue_write_preview_result: Any,
    queue_path: str | Path | None,
    context: Any = None,
    backup: bool = True,
    queue_commit_readiness_policy_result: Any = None,
    manual_queue_commit_after_runtime_confirmed: bool = False,
) -> dict[str, Any]:
    """Commit a queue write preview only through an explicit manual path."""
    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual queue write confirmation is required")

    if queue_path is None or not str(queue_path).strip():
        return _blocked("queue_path", "queue_path is required")

    runtime_queue_target = _runtime_queue_path(queue_path)
    if runtime_queue_target and not _runtime_confirmed(context):
        return _blocked("runtime_operator_confirmation", "manual runtime queue write confirmation is required")
    if runtime_queue_target:
        policy_issue = _policy_issue(queue_commit_readiness_policy_result)
        if policy_issue is not None:
            return policy_issue
        if manual_queue_commit_after_runtime_confirmed is not True:
            return _blocked(
                "queue_commit_after_runtime_confirmation",
                "manual queue commit after runtime confirmation is required",
            )

    commit_result = commit_execution_queue_write(
        queue_write_preview_result,
        queue_path,
        backup=backup,
        context=context,
    )
    committed = bool(_as_dict(commit_result).get("committed"))
    blocked_reasons = [] if committed else list(_as_dict(commit_result).get("blocked_reasons", []))
    if runtime_queue_target and not committed:
        blocked_reasons.append("QUEUE_COMMIT_FAILED_AFTER_RUNTIME_COMMIT")

    return {
        "status": "COMMITTED" if committed else "BLOCKED",
        "manual_commit": committed,
        "commit_stage": "committed" if committed else _as_dict(commit_result).get("write_stage", "commit"),
        "next_stage": _as_dict(commit_result).get("next_stage", NEXT_STAGE_BLOCKED),
        "commit_result": commit_result,
        "blocked_reasons": blocked_reasons,
    }
