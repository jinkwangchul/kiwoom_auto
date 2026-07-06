# -*- coding: utf-8 -*-
"""Preview-only execution pipeline controller.

This module wires the execution preview components into a single in-memory
entry point. It never writes runtime files, never creates ORDER_QUEUED records,
never changes execution_enabled, and never calls SendOrder.
"""

from __future__ import annotations

from typing import Any

from execution_controller import build_execution_preview
from final_execution_guard import evaluate_final_execution_guard
from order_execution_request import build_execution_request_preview
from order_lock_manager import build_order_lock_preview
from order_request_hash import build_order_request_hash_preview


STAGE = "EXECUTION_PREVIEW_PIPELINE"


def _collect_warnings(*items: Any) -> list[str]:
    warnings: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_warnings = item.get("warnings", [])
        if isinstance(item_warnings, list):
            warnings.extend(str(warning) for warning in item_warnings)
    return warnings


def _blocked_stage(
    execution_preview: dict[str, Any],
    final_guard_result: dict[str, Any],
    lock_preview: dict[str, Any],
    request_hash_preview: dict[str, Any],
    execution_request_preview: dict[str, Any],
) -> str | None:
    if bool(execution_preview.get("unresolved")):
        return "execution_preview"
    if final_guard_result.get("ok") is not True:
        return "final_guard"
    if bool(lock_preview.get("unresolved")):
        return "lock_preview"
    if bool(request_hash_preview.get("unresolved")):
        return "request_hash_preview"
    if bool(execution_request_preview.get("unresolved")):
        return "execution_request_preview"
    return None


def _dict_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())


def _list_first(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _reason_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return "missing result"

    blocked_reason = _list_first(result.get("blocked_reasons"))
    if blocked_reason:
        return blocked_reason

    warning = _list_first(result.get("warnings"))
    if warning:
        return warning

    if bool(result.get("unresolved")):
        return "unresolved"

    if result.get("ok") is False:
        return "blocked"

    if result.get("ok") is True:
        return "ok"

    return "present"


def _ok_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is False:
        return False
    if bool(result.get("unresolved")):
        return False
    return True


def _diagnostic(stage: str, result: Any, *, key_name: str = "preview_keys") -> dict[str, Any]:
    return {
        "stage": stage,
        "ok": _ok_from_result(result),
        "reason": _reason_from_result(result),
        key_name: _dict_keys(result),
    }


def _build_all_stage_diagnostics(
    execution_preview: dict[str, Any],
    final_guard_result: dict[str, Any],
    lock_preview: dict[str, Any],
    request_hash_preview: dict[str, Any],
    execution_request_preview: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _diagnostic("hoga_mapper", execution_preview.get("hoga_preview")),
        _diagnostic("order_type_mapper", execution_preview.get("order_type_preview")),
        _diagnostic("guard", final_guard_result, key_name="output_keys"),
        _diagnostic("lock_preview", lock_preview),
        _diagnostic("request_hash_preview", request_hash_preview),
        _diagnostic("execution_request_preview", execution_request_preview),
    ]


def _truncate_stage_diagnostics(
    diagnostics: list[dict[str, Any]],
    blocked_stage: str | None,
) -> list[dict[str, Any]]:
    if blocked_stage is None:
        return diagnostics

    blocked_stage_to_diagnostic_stage = {
        "execution_preview": {"hoga_mapper", "order_type_mapper"},
        "final_guard": {"guard"},
        "lock_preview": {"lock_preview"},
        "request_hash_preview": {"request_hash_preview"},
        "execution_request_preview": {"execution_request_preview"},
    }
    stop_stages = blocked_stage_to_diagnostic_stage.get(blocked_stage, set())
    if not stop_stages:
        return diagnostics

    truncated: list[dict[str, Any]] = []
    for item in diagnostics:
        truncated.append(item)
        if item.get("stage") in stop_stages and item.get("ok") is False:
            break
        if blocked_stage != "execution_preview" and item.get("stage") in stop_stages:
            break
    return truncated


def _blocked_reason_from_diagnostics(diagnostics: list[dict[str, Any]]) -> str | None:
    for item in diagnostics:
        if item.get("ok") is False:
            return str(item.get("reason") or "blocked")
    return None


def run_execution_preview_pipeline(order: Any, guard: Any) -> dict[str, Any]:
    """Run the full SendOrder-adjacent preview pipeline in memory."""
    execution_preview = build_execution_preview(order, guard)
    final_guard_result = evaluate_final_execution_guard(order, guard, execution_preview)
    lock_preview = build_order_lock_preview(order, execution_preview)
    request_hash_preview = build_order_request_hash_preview(order, execution_preview, lock_preview)
    execution_request_preview = build_execution_request_preview(
        order,
        guard,
        execution_preview,
        final_guard_result,
        lock_preview,
        request_hash_preview,
    )

    blocked_stage = _blocked_stage(
        execution_preview,
        final_guard_result,
        lock_preview,
        request_hash_preview,
        execution_request_preview,
    )
    all_stage_diagnostics = _build_all_stage_diagnostics(
        execution_preview,
        final_guard_result,
        lock_preview,
        request_hash_preview,
        execution_request_preview,
    )
    stage_diagnostics = _truncate_stage_diagnostics(all_stage_diagnostics, blocked_stage)

    return {
        "ok": blocked_stage is None,
        "stage": STAGE,
        "blocked_stage": blocked_stage,
        "blocked_reason": _blocked_reason_from_diagnostics(stage_diagnostics),
        "stage_diagnostics": stage_diagnostics,
        "pipeline": {
            "execution_preview": execution_preview,
            "final_guard": final_guard_result,
            "lock_preview": lock_preview,
            "request_hash_preview": request_hash_preview,
            "execution_request_preview": execution_request_preview,
        },
        "warnings": _collect_warnings(
            execution_preview,
            final_guard_result,
            lock_preview,
            request_hash_preview,
            execution_request_preview,
        ),
    }
