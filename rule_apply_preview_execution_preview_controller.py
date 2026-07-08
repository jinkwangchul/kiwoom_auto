# -*- coding: utf-8 -*-
"""Preview-only controller hook from rule apply preview to execution preview.

This hook is intentionally not connected to GUI, queue commit, runtime writers,
SendOrder, or Kiwoom. It only adapts an approved rule apply preview into an
in-memory REAL_READY order contract and runs the existing execution preview
service through queue write preview.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_preview_service import preview_execution_for_order
from rule_apply_preview_execution_order_adapter import build_rule_apply_preview_execution_order_contract


CONTROLLER_TYPE = "RULE_APPLY_PREVIEW_EXECUTION_PREVIEW_CONTROLLER"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _result(
    *,
    status: str,
    order_contract: dict[str, Any] | None = None,
    execution_preview: dict[str, Any] | None = None,
    queue_pending_result: dict[str, Any] | None = None,
    queue_write_preview_result: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "controller_type": CONTROLLER_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "queue_commit_called": False,
        "order_contract": deepcopy(order_contract) if isinstance(order_contract, dict) else None,
        "execution_preview": deepcopy(execution_preview) if isinstance(execution_preview, dict) else None,
        "queue_pending_result": deepcopy(queue_pending_result) if isinstance(queue_pending_result, dict) else None,
        "queue_write_preview_result": deepcopy(queue_write_preview_result)
        if isinstance(queue_write_preview_result, dict)
        else None,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _preview_issues(preview_result: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    summary = _as_dict(preview_result.get("summary"))
    if summary.get("blocked_stage"):
        issues.append(f"execution preview blocked at {summary.get('blocked_stage')}")
    issues.extend(str(reason) for reason in _as_list(summary.get("blocked_reasons")))
    pipeline = _as_dict(preview_result.get("pipeline_result"))
    if pipeline.get("blocked_reason"):
        issues.append(str(pipeline.get("blocked_reason")))
    if not issues:
        issues.append("execution preview is not ok")
    return issues


def preview_execution_from_rule_apply_preview(
    apply_preview: Any,
    signal_context: Any,
    guard: Any = None,
    guard_defaults: Any = None,
    order_defaults: Any = None,
) -> dict[str, Any]:
    """Run preview-only execution and queue previews from a rule apply preview."""
    effective_guard = deepcopy(_as_dict(guard_defaults))
    effective_guard.update(deepcopy(_as_dict(guard)))

    try:
        order_contract = build_rule_apply_preview_execution_order_contract(
            deepcopy(apply_preview),
            deepcopy(signal_context),
            guard_defaults=deepcopy(guard_defaults),
            order_defaults=deepcopy(order_defaults),
        )
    except ValueError as exc:
        return _result(status=STATUS_INVALID, issues=[str(exc)])

    preview_result = preview_execution_for_order(deepcopy(order_contract), deepcopy(effective_guard))
    if not isinstance(preview_result, dict):
        return _result(status=STATUS_INVALID, order_contract=order_contract, issues=["execution preview result is malformed"])

    queue_pending_result = _as_dict(preview_result.get("queue_pending_result"))
    queue_write_preview_result = _as_dict(preview_result.get("queue_write_preview_result"))
    warnings = _as_list(preview_result.get("warnings"))

    if preview_result.get("ok") is not True:
        return _result(
            status=STATUS_BLOCKED,
            order_contract=order_contract,
            execution_preview=preview_result,
            queue_pending_result=queue_pending_result,
            queue_write_preview_result=queue_write_preview_result,
            issues=_preview_issues(preview_result),
            warnings=warnings,
        )

    if queue_pending_result.get("queue_pending") is not True:
        return _result(
            status=STATUS_BLOCKED,
            order_contract=order_contract,
            execution_preview=preview_result,
            queue_pending_result=queue_pending_result,
            queue_write_preview_result=queue_write_preview_result,
            issues=_as_list(queue_pending_result.get("blocked_reasons")) or ["queue pending preview is not ready"],
            warnings=warnings + _as_list(queue_pending_result.get("warnings")),
        )

    if queue_write_preview_result.get("write_preview") is not True:
        return _result(
            status=STATUS_BLOCKED,
            order_contract=order_contract,
            execution_preview=preview_result,
            queue_pending_result=queue_pending_result,
            queue_write_preview_result=queue_write_preview_result,
            issues=_as_list(queue_write_preview_result.get("blocked_reasons")) or ["queue write preview is not ready"],
            warnings=warnings + _as_list(queue_write_preview_result.get("warnings")),
        )

    return _result(
        status=STATUS_READY,
        order_contract=order_contract,
        execution_preview=preview_result,
        queue_pending_result=queue_pending_result,
        queue_write_preview_result=queue_write_preview_result,
        warnings=warnings,
    )
