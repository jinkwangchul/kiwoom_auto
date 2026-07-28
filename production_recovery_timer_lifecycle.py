# -*- coding: utf-8 -*-
"""Recovery-bound lifecycle helpers for existing GUI timers."""

from __future__ import annotations

from typing import Any, Iterable

from production_recovery_contract import (
    STOCK_RESTORED,
    RecoverySessionIdentity,
)
from production_recovery_state_registry import (
    ProductionRecoveryStateRegistry,
    production_recovery_registry,
    recovery_account_allows_isolated_stock_operation,
)


def _timer_list(timers: Iterable[Any]) -> list[Any]:
    return [timer for timer in timers if timer is not None]


def start_recovery_bound_timers(
    *,
    identity: RecoverySessionIdentity,
    timers: Iterable[Any],
    registry: ProductionRecoveryStateRegistry = production_recovery_registry,
) -> dict[str, Any]:
    """Start existing timers once only when current Recovery has a restored stock."""
    timer_items = _timer_list(timers)
    context = registry.snapshot()
    if context is None:
        return {"started": False, "reason_code": "RECOVERY_NOT_STARTED", "started_count": 0}
    if context.identity != identity:
        return {"started": False, "reason_code": "RECOVERY_IDENTITY_MISMATCH", "started_count": 0}
    if not recovery_account_allows_isolated_stock_operation(context):
        return {"started": False, "reason_code": "RECOVERY_ACCOUNT_NOT_READY", "started_count": 0}
    if not any(
        item.stock_status == STOCK_RESTORED and item.review_required is False
        for item in context.stocks
    ):
        return {"started": False, "reason_code": "RECOVERY_NO_RESTORED_STOCK", "started_count": 0}

    started_count = 0
    for timer in timer_items:
        try:
            if timer.isActive():
                continue
            timer.start()
            started_count += 1
        except Exception as exc:
            stop_recovery_bound_timers(timer_items)
            return {
                "started": False,
                "reason_code": "RECOVERY_TIMER_START_FAILED",
                "started_count": 0,
                "error": str(exc),
            }
    return {
        "started": True,
        "reason_code": "RECOVERY_TIMER_STARTED" if started_count else "RECOVERY_TIMER_ALREADY_ACTIVE",
        "started_count": started_count,
    }


def stop_recovery_bound_timers(timers: Iterable[Any]) -> dict[str, Any]:
    """Stop current timers idempotently when Recovery identity is invalidated."""
    stopped_count = 0
    errors: list[str] = []
    for timer in _timer_list(timers):
        try:
            if not timer.isActive():
                continue
            timer.stop()
            stopped_count += 1
        except Exception as exc:
            errors.append(str(exc))
    return {
        "stopped": not errors,
        "reason_code": "RECOVERY_TIMERS_STOPPED" if stopped_count else "RECOVERY_TIMERS_ALREADY_STOPPED",
        "stopped_count": stopped_count,
        "errors": errors,
    }
