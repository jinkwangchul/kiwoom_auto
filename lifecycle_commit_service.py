# -*- coding: utf-8 -*-
"""Orchestrator to perform a full lifecycle commit using LifecycleCommitWriter.

Flow:
 1. Validate commit_plan_orchestrator_result is READY and commit_ready
 2. Call writer.prepare_commit(...) to persist a prepared lifecycle transition
 3. Execute external side-effects described by commit_plan (runtime writes, queue writes)
 4. If external writes succeed, call writer.finalize_commit(token, success=True)
    otherwise call writer.finalize_commit(token, success=False)

This module intentionally keeps runtime/queue write invocations simple. The
default runtime path delegates to the Lifecycle -> M6 runtime commit adapter,
while explicit callbacks remain supported for tests and compatibility paths.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from lifecycle_commit_writer import LifecycleCommitWriter
from execution_runtime_commit_plan_orchestrator import ORCHESTRATOR_TYPE


SERVICE_TYPE = "ORDER_LIFECYCLE_COMMIT_SERVICE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _runtime_adapter_payload(
    commit_plan: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    for source in (
        commit_plan.get("runtime_commit_adapter"),
        commit_plan.get("runtime_commit_adapter_request"),
        _as_dict(context).get("runtime_commit_adapter"),
        _as_dict(context).get("runtime_commit_adapter_request"),
    ):
        if isinstance(source, dict):
            return deepcopy(source)
    return {}


def _result(*, status: str, issues: list[str] | None = None, warnings: list[str] | None = None, commit_token: str | None = None) -> dict[str, Any]:
    return {
        "service_type": SERVICE_TYPE,
        "status": status,
        "commit_token": commit_token,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def commit_lifecycle(
    commit_contract_preview: Any,
    commit_plan_orchestrator_result: Any,
    writer: LifecycleCommitWriter,
    runtime_commit_executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    queue_commit_executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    *,
    context: Any = None,
) -> dict[str, Any]:
    """Perform a lifecycle commit using the provided writer and executors.

    - commit_contract_preview: expected preview dict (from build_lifecycle_commit_contract_preview)
    - commit_plan_orchestrator_result: orchestrator result for commit plan preview (must be READY)
    - writer: LifecycleCommitWriter instance (connected to lifecycle DB)
    - runtime_commit_executor: function that accepts commit_plan dict and performs runtime writes
      returns a dict with {"ok": True} or {"ok": False, "issues": [...]}.
    - queue_commit_executor: similar, for queue writes.

    Returns a result dict summarizing outcome.
    """
    preview = _as_dict(commit_contract_preview)
    plan_orch = _as_dict(commit_plan_orchestrator_result)

    # Basic validation
    if plan_orch.get("orchestrator_type") != ORCHESTRATOR_TYPE:
        return _result(status="INVALID", issues=["INVALID_COMMIT_PLAN_ORCHESTRATOR_TYPE"])
    if plan_orch.get("status") != "READY" or plan_orch.get("commit_ready") is not True:
        return _result(status="BLOCKED", issues=["COMMIT_PLAN_NOT_READY"])

    commit_plan = deepcopy(plan_orch.get("commit_plan") or {})

    if (
        runtime_commit_executor is None
        and commit_plan.get("planned_records")
        and not _runtime_adapter_payload(commit_plan, context)
    ):
        return _result(status="BLOCKED", issues=["RUNTIME_COMMIT_ADAPTER_INPUT_REQUIRED"])

    # Persist prepared transition
    prep = writer.prepare_commit(preview.get("commit_contract"), commit_plan, preview.get("commit_plan"), context)
    if not prep.get("ok"):
        return _result(status="BLOCKED", issues=list(prep.get("issues") or []))

    token = prep.get("commit_token")

    # Execute external writes
    runtime_ok = True
    queue_ok = True
    runtime_issues: list[str] = []
    queue_issues: list[str] = []

    try:
        # If no explicit runtime executor was provided, call the Lifecycle -> M6
        # adapter only when the caller supplied the full adapter payload. Do not
        # fall back to the legacy execution_runtime_commit_service automatically.
        if runtime_commit_executor is None:
            try:
                from lifecycle_runtime_commit_adapter import (
                    adapt_and_execute_lifecycle_runtime_commit as default_runtime_commit,
                )
            except Exception:
                default_runtime_commit = None

            if default_runtime_commit is not None:
                def _runtime_wrapper(plan: dict[str, Any]) -> dict[str, Any]:
                    payload = _runtime_adapter_payload(plan, context)
                    if not payload:
                        return {"ok": False, "issues": ["RUNTIME_COMMIT_ADAPTER_INPUT_REQUIRED"]}
                    try:
                        res = default_runtime_commit(**payload)
                    except Exception as exc:  # pragma: no cover - defensive
                        return {"ok": False, "issues": [f"RUNTIME_COMMIT_ADAPTER_EXCEPTION: {type(exc).__name__}"]}
                    if isinstance(res, dict) and res.get("adapter_status") == "COMMITTED":
                        return {"ok": True, **res}
                    if isinstance(res, dict):
                        status = res.get("adapter_status") or "RUNTIME_COMMIT_ADAPTER_FAILED"
                        return {"ok": False, **res, "issues": list(res.get("issues") or [status])}
                    return {"ok": False, "issues": ["RUNTIME_COMMIT_ADAPTER_RETURNED_NON_DICT"]}

                runtime_commit_executor = _runtime_wrapper

        if queue_commit_executor is None:
            try:
                from execution_queue_commit_service import commit_execution_queue_manually as default_queue_commit
            except Exception:
                default_queue_commit = None

            if default_queue_commit is not None:
                def _queue_wrapper(plan: dict[str, Any]) -> dict[str, Any]:
                    # There is no generic queue_write_preview in commit_plan; queue
                    # commits are context-specific. For safety, treat default as a
                    # no-op success when we cannot derive necessary params.
                    targets = plan.get("planned_targets") if isinstance(plan, dict) else {}
                    queue_path = targets.get("order_queue") if isinstance(targets, dict) else None
                    if not queue_path:
                        return {"ok": True}
                    try:
                        # default_queue_commit expects (queue_write_preview_result, queue_path, ...)
                        # We don't have a queue_write_preview_result here so we cannot
                        # call it generically. Return failure to be explicit.
                        return {"ok": False, "issues": ["QUEUE_COMMIT_UNSUPPORTED_IN_GENERIC_ORCHESTRATOR"]}
                    except Exception as exc:  # pragma: no cover - defensive
                        return {"ok": False, "issues": [f"QUEUE_COMMIT_EXCEPTION: {exc}"]}

                queue_commit_executor = _queue_wrapper

        if runtime_commit_executor is not None and commit_plan.get("planned_records"):
            runtime_result = runtime_commit_executor(commit_plan)
            if not isinstance(runtime_result, dict) or runtime_result.get("ok") is not True:
                runtime_ok = False
                runtime_issues = list(runtime_result.get("issues") or ["RUNTIME_COMMIT_FAILED"])

        if queue_commit_executor is not None and commit_plan.get("planned_records"):
            queue_result = queue_commit_executor(commit_plan)
            if not isinstance(queue_result, dict) or queue_result.get("ok") is not True:
                queue_ok = False
                queue_issues = list(queue_result.get("issues") or ["QUEUE_COMMIT_FAILED"])

        success = runtime_ok and queue_ok
    except Exception as exc:  # defensive
        success = False
        runtime_ok = False
        runtime_issues = [f"EXTERNAL_WRITE_EXCEPTION: {exc}"]

    # Finalize based on success
    finalize_meta = {"runtime_issues": runtime_issues, "queue_issues": queue_issues}
    finalize_res = writer.finalize_commit(token, success=success, metadata=finalize_meta)
    if not finalize_res.get("ok"):
        # Failed to finalize the lifecycle transition: this is a serious state
        # and should be surfaced to the operator.
        return _result(status="ERROR", issues=list(finalize_res.get("issues") or ["FINALIZE_FAILED"]), commit_token=token)

    if success:
        return _result(status="COMMITTED", warnings=list(plan_orch.get("warnings") or []), commit_token=token)
    else:
        return _result(status="ABORTED", issues=runtime_issues + queue_issues, commit_token=token)
