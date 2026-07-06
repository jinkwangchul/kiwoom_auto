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


def preview_execution_for_real_ready_order(
    order_id: Any,
    guard: Any,
    queue_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read a REAL_READY order by id and run execution preview for it."""
    read_result = read_real_ready_order_by_id(order_id, queue_path=queue_path)
    if read_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": STAGE,
            "read_result": read_result,
            "preview_result": None,
        }

    preview_result = preview_execution_for_order(read_result.get("order"), guard)
    return {
        "ok": bool(preview_result.get("ok")),
        "stage": STAGE,
        "read_result": read_result,
        "preview_result": preview_result,
    }
