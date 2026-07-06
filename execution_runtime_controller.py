# -*- coding: utf-8 -*-
"""Dry-run controller for execution runtime commit planning.

This controller bridges the existing execution preview pipeline to the runtime
catalog and storage preview layers. It never calls SendOrder, commits queues,
commits runtime files, creates directories, or writes runtime files.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_pipeline_controller import run_execution_preview_pipeline
from execution_runtime_catalog_orchestrator import run_execution_runtime_catalog_orchestrator_preview


CONTROLLER_TYPE = "EXECUTION_RUNTIME_DRY_RUN_CONTROLLER"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _base_result(
    *,
    status: str,
    execution_preview: Any = None,
    catalog_orchestrator: Any = None,
    commit_plan: Any = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "controller_type": CONTROLLER_TYPE,
        "status": status,
        "dry_run": True,
        "preview_only": True,
        "runtime_write": False,
        "send_order_called": False,
        "queue_commit_called": False,
        "runtime_commit_called": False,
        "execution_preview": deepcopy(execution_preview),
        "catalog_orchestrator": deepcopy(catalog_orchestrator),
        "commit_plan": deepcopy(commit_plan),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _queue_write_preview(order: dict[str, Any], pipeline_result: dict[str, Any]) -> dict[str, Any]:
    execution_request = _as_dict(
        _as_dict(_as_dict(pipeline_result.get("pipeline")).get("execution_request_preview")).get(
            "execution_request"
        )
    )
    return {
        "write_preview": True,
        "preview_only": True,
        "no_write": True,
        "runtime_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
        "order_id": execution_request.get("order_id") or order.get("id") or order.get("order_id"),
        "execution_id": execution_request.get("execution_id"),
        "request_hash": execution_request.get("request_hash"),
        "lock_id": execution_request.get("lock_id"),
        "source": "execution_runtime_dry_run_controller",
    }


def _pipeline_blocked_result(pipeline_result: dict[str, Any]) -> dict[str, Any]:
    blocked_stage = pipeline_result.get("blocked_stage")
    issue = "EXECUTION_PREVIEW_BLOCKED"
    if blocked_stage == "final_guard":
        issue = "FINAL_GUARD_BLOCKED"
    return _base_result(
        status="BLOCKED",
        execution_preview=pipeline_result,
        issues=[issue],
        warnings=_as_list(pipeline_result.get("warnings")),
    )


def run_execution_runtime_dry_run(
    order: Any,
    guard: Any,
    storage: Any,
    confirmations: Any = None,
) -> dict[str, Any]:
    """Build a runtime commit plan preview without committing anything."""
    if not isinstance(order, dict):
        return _base_result(status="INVALID", issues=["ORDER_MUST_BE_DICT"])
    if not isinstance(guard, dict):
        return _base_result(status="INVALID", issues=["GUARD_MUST_BE_DICT"])
    if not hasattr(storage, "preview_commit_plan") or not callable(getattr(storage, "preview_commit_plan")):
        return _base_result(status="INVALID", issues=["STORAGE_PREVIEW_COMMIT_PLAN_REQUIRED"])

    order_snapshot = deepcopy(order)
    guard_snapshot = deepcopy(guard)
    confirmations_snapshot = deepcopy(confirmations) if isinstance(confirmations, dict) else {}

    pipeline_result = run_execution_preview_pipeline(order_snapshot, guard_snapshot)
    if pipeline_result.get("ok") is not True:
        return _pipeline_blocked_result(pipeline_result)

    pipeline = _as_dict(pipeline_result.get("pipeline"))
    catalog_orchestrator = run_execution_runtime_catalog_orchestrator_preview(
        execution_request_preview=pipeline.get("execution_request_preview"),
        lock_preview=pipeline.get("lock_preview"),
        request_hash_preview=pipeline.get("request_hash_preview"),
        queue_write_preview_result=_queue_write_preview(order_snapshot, pipeline_result),
        order_candidate=order_snapshot,
    )
    catalog_status = catalog_orchestrator.get("status")
    if catalog_status == "INVALID":
        return _base_result(
            status="INVALID",
            execution_preview=pipeline_result,
            catalog_orchestrator=catalog_orchestrator,
            issues=_unique(_as_list(catalog_orchestrator.get("issues")) or ["CATALOG_ORCHESTRATOR_INVALID"]),
            warnings=_unique(_as_list(pipeline_result.get("warnings")) + _as_list(catalog_orchestrator.get("warnings"))),
        )
    if catalog_status == "BLOCKED":
        return _base_result(
            status="BLOCKED",
            execution_preview=pipeline_result,
            catalog_orchestrator=catalog_orchestrator,
            issues=_unique(_as_list(catalog_orchestrator.get("issues")) or ["CATALOG_ORCHESTRATOR_BLOCKED"]),
            warnings=_unique(_as_list(pipeline_result.get("warnings")) + _as_list(catalog_orchestrator.get("warnings"))),
        )

    commit_plan = storage.preview_commit_plan(catalog_orchestrator, confirmations_snapshot)
    commit_status = _as_dict(commit_plan).get("status")
    if commit_status == "READY":
        status = "READY"
    elif commit_status == "BLOCKED":
        status = "BLOCKED"
    else:
        status = "INVALID"

    return _base_result(
        status=status,
        execution_preview=pipeline_result,
        catalog_orchestrator=catalog_orchestrator,
        commit_plan=commit_plan,
        issues=_unique(
            _as_list(catalog_orchestrator.get("issues"))
            + _as_list(_as_dict(commit_plan).get("issues"))
        ),
        warnings=_unique(
            _as_list(pipeline_result.get("warnings"))
            + _as_list(catalog_orchestrator.get("warnings"))
            + _as_list(_as_dict(commit_plan).get("warnings"))
        ),
    )
