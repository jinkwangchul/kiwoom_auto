# -*- coding: utf-8 -*-
"""Preview-to-execution approval gate.

This module only evaluates in-memory approval inputs. Approval means the
preview result passed the additional gate inputs; it does not mean SendOrder is
allowed, and this module never writes runtime files or creates ORDER_QUEUED.
"""

from __future__ import annotations

from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_CANDIDATE = "EXECUTION_CANDIDATE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "approved": False,
        "approval_stage": stage,
        "blocked_reasons": [reason],
        "next_stage": NEXT_STAGE_BLOCKED,
    }


def evaluate_execution_approval(
    preview_result: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Evaluate preview approval inputs without side effects."""
    preview = _as_dict(preview_result)
    ctx = _as_dict(context)

    if preview.get("ok") is not True:
        return _blocked("preview_result", "preview result is not ok")

    if preview.get("ready_for_execution_request_preview") is not True:
        return _blocked(
            "preview_result",
            "preview result is not ready for execution request preview",
        )

    if preview.get("blocked_stage"):
        return _blocked(
            "preview_result",
            f"preview result blocked_stage is set: {preview.get('blocked_stage')}",
        )

    blocked_reasons = _as_list(preview.get("blocked_reasons"))
    if blocked_reasons:
        return _blocked("preview_result", f"preview result has blocked reasons: {blocked_reasons[0]}")

    if not _truthy(ctx.get("execution_enabled")):
        return _blocked("execution_enabled", "context.execution_enabled is not true")

    if not _truthy(ctx.get("operator_confirmed")):
        return _blocked("operator_confirmed", "context.operator_confirmed is not true")

    if not _truthy(ctx.get("real_trade_guard_ok")):
        return _blocked("real_trade_guard", "context.real_trade_guard_ok is not true")

    return {
        "approved": True,
        "approval_stage": "approved",
        "blocked_reasons": [],
        "next_stage": NEXT_STAGE_CANDIDATE,
    }
