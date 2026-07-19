# -*- coding: utf-8 -*-
"""REAL_READY order execution preview helper.

This helper reads one REAL_READY order from order_queue and, only when the read
passes, runs the in-memory execution preview service. It never mutates the
queue file or runtime state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from execution_preview_service import preview_execution_for_order
from order_queue_reader import read_real_ready_order_by_id


STAGE = "REAL_READY_ORDER_EXECUTION_PREVIEW"


def _as_reason_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


def preview_execution_for_real_ready_order(
    order_id: Any,
    guard: Any,
    queue_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read a REAL_READY order by id and run execution preview for it."""
    read_result = read_real_ready_order_by_id(order_id, queue_path=queue_path)
    if read_result.get("ok") is not True:
        blocked_reasons = _as_reason_list(read_result.get("blocked_reasons") or read_result.get("error"))
        return {
            "ok": False,
            "stage": STAGE,
            "read_result": read_result,
            "preview_result": None,
            "blocked_stage": read_result.get("stage"),
            "blocked_reason": blocked_reasons[0] if blocked_reasons else None,
            "blocked_reasons": blocked_reasons,
            "issues": blocked_reasons,
        }

    preview_result = preview_execution_for_order(read_result.get("order"), guard)
    summary = preview_result.get("summary") if isinstance(preview_result.get("summary"), dict) else {}
    blocked_reasons = _as_reason_list(
        summary.get("blocked_reasons")
        or preview_result.get("blocked_reasons")
        or preview_result.get("issues")
        or summary.get("blocked_reason")
    )
    return {
        "ok": bool(preview_result.get("ok")),
        "stage": STAGE,
        "read_result": read_result,
        "preview_result": preview_result,
        "summary": summary,
        "queue_write_preview_result": preview_result.get("queue_write_preview_result"),
        "blocked_stage": summary.get("blocked_stage"),
        "blocked_reason": summary.get("blocked_reason") or (blocked_reasons[0] if blocked_reasons else None),
        "blocked_reasons": blocked_reasons,
        "issues": blocked_reasons,
    }
