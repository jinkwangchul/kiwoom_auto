# -*- coding: utf-8 -*-
"""Manual execution preview service helper.

This module exposes one convenience entry point for GUI buttons, CLI helpers,
and tests. It only runs in-memory preview steps and returns their summary.
"""

from __future__ import annotations

from typing import Any

from execution_approval_service import evaluate_execution_approval
from execution_candidate_service import build_execution_candidate
from execution_pipeline_controller import run_execution_preview_pipeline
from execution_pipeline_summary import summarize_execution_preview_pipeline
from execution_queue_pending_service import build_execution_queue_pending
from execution_queue_writer import preview_execution_queue_write


STAGE = "EXECUTION_PREVIEW_SERVICE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _approval_preview_input(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": summary.get("ok"),
        "ready_for_execution_request_preview": summary.get("ready_for_execution_request"),
        "blocked_stage": summary.get("blocked_stage"),
        "blocked_reasons": summary.get("blocked_reasons", []),
    }


def _approval_context(order: Any, guard: Any) -> dict[str, Any]:
    order_dict = _as_dict(order)
    guard_dict = _as_dict(guard)
    return {
        "execution_enabled": order_dict.get("execution_enabled"),
        "operator_confirmed": guard_dict.get("operator_confirmed"),
        "real_trade_guard_ok": guard_dict.get("real_trade_guard_ok", guard_dict.get("real_trade_enabled")),
    }


def preview_execution_for_order(order: Any, guard: Any) -> dict[str, Any]:
    """Run the preview pipeline and attach a compact summary."""
    pipeline_result = run_execution_preview_pipeline(order, guard)
    summary = summarize_execution_preview_pipeline(pipeline_result)
    approval_result = evaluate_execution_approval(
        _approval_preview_input(summary),
        _approval_context(order, guard),
    )
    candidate_result = build_execution_candidate(
        {
            "ok": bool(summary.get("ok")),
            "summary": summary,
            "pipeline_result": pipeline_result,
        },
        approval_result,
    )
    queue_pending_result = build_execution_queue_pending(candidate_result)
    queue_write_preview_result = preview_execution_queue_write(queue_pending_result)

    return {
        "ok": bool(summary.get("ok")),
        "stage": STAGE,
        "pipeline_result": pipeline_result,
        "summary": summary,
        "approval_result": approval_result,
        "candidate_result": candidate_result,
        "queue_pending_result": queue_pending_result,
        "queue_write_preview_result": queue_write_preview_result,
    }
