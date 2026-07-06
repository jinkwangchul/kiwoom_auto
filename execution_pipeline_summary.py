# -*- coding: utf-8 -*-
"""Summary helper for execution preview pipeline results.

This module only transforms an in-memory pipeline result into a compact summary
dict for GUI/log/review surfaces. It never writes files and never calls
SendOrder.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _collect_list_field(*items: Any, field: str) -> list[str]:
    result: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values = item.get(field, [])
        if isinstance(values, list):
            result.extend(str(value) for value in values)
    return result


def summarize_execution_preview_pipeline(pipeline_result: Any) -> dict[str, Any]:
    """Build a compact summary from run_execution_preview_pipeline output."""
    result = _as_dict(pipeline_result)
    pipeline = _as_dict(result.get("pipeline"))

    execution_preview = _as_dict(pipeline.get("execution_preview"))
    final_guard = _as_dict(pipeline.get("final_guard"))
    lock_preview = _as_dict(pipeline.get("lock_preview"))
    request_hash_preview = _as_dict(pipeline.get("request_hash_preview"))
    execution_request_preview = _as_dict(pipeline.get("execution_request_preview"))
    execution_request = _as_dict(execution_request_preview.get("execution_request"))

    ok = bool(result.get("ok"))
    blocked_stage = result.get("blocked_stage")
    stage_diagnostics = result.get("stage_diagnostics", [])
    if not isinstance(stage_diagnostics, list):
        stage_diagnostics = []
    ready_for_execution_request = (
        ok
        and blocked_stage is None
        and execution_request_preview.get("ok") is True
        and bool(execution_request)
    )

    request_hash = execution_request.get("request_hash") or request_hash_preview.get("request_hash")

    warnings = _collect_list_field(
        result,
        execution_preview,
        final_guard,
        lock_preview,
        request_hash_preview,
        execution_request_preview,
        field="warnings",
    )
    blocked_reasons = _collect_list_field(
        execution_preview,
        final_guard,
        lock_preview,
        request_hash_preview,
        execution_request_preview,
        field="blocked_reasons",
    )

    return {
        "ok": ok,
        "blocked_stage": blocked_stage,
        "blocked_reason": result.get("blocked_reason"),
        "ready_for_execution_request": ready_for_execution_request,
        "order_id": execution_request.get("order_id") or execution_preview.get("order_id"),
        "execution_id": execution_request.get("execution_id"),
        "request_hash": request_hash,
        "stage_diagnostics": stage_diagnostics,
        "warnings": warnings,
        "blocked_reasons": blocked_reasons,
    }
