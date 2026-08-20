"""Next-session RoutineInstance buy-limit metadata recalculation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Callable

from gui_operation_environment import (
    floor_money_to_won,
    read_system_total_budget_for_recalculation,
)
from routine_instance_repository import RoutineInstanceRepository


PROJECT_ROOT = Path(__file__).resolve().parent


def _current_recommendation(window, instance_id: str) -> tuple[int | None, int | None]:
    from gui_main_table_loader import routine_instance_suggested_buy_limits

    return routine_instance_suggested_buy_limits(window, instance_id)


def _invalidate_current_assignment_snapshot(window) -> None:
    from gui_main_table_loader import _invalidate_main_pnl_refresh_cache

    _invalidate_main_pnl_refresh_cache(window)


def recalculate_enabled_routine_limits_for_new_session(
    window,
    *,
    repository: RoutineInstanceRepository | None = None,
    recommendation_provider: Callable[
        [object, str], tuple[int | None, int | None]
    ] = _current_recommendation,
    total_budget_provider: Callable[[], int | None] = (
        read_system_total_budget_for_recalculation
    ),
    invalidate_assignments: Callable[[object], None] = (
        _invalidate_current_assignment_snapshot
    ),
) -> dict[str, object]:
    """Recompute enabled routine limits once after a new RUNNING commit."""
    repo = repository or RoutineInstanceRepository(PROJECT_ROOT)
    try:
        total_budget = total_budget_provider()
    except Exception as exc:
        return {
            "ok": False,
            "reason": "TOTAL_BUDGET_UNAVAILABLE",
            "error": str(exc),
            "updated": (),
            "unchanged": (),
            "failed": (),
        }
    if (
        isinstance(total_budget, bool)
        or not isinstance(total_budget, int)
        or total_budget < 0
    ):
        return {
            "ok": False,
            "reason": "TOTAL_BUDGET_UNAVAILABLE",
            "updated": (),
            "unchanged": (),
            "failed": (),
        }

    try:
        invalidate_assignments(window)
        instances = tuple(repo.list_instances())
    except Exception as exc:
        return {
            "ok": False,
            "reason": "ROUTINE_LIMIT_RECALCULATION_UNAVAILABLE",
            "error": str(exc),
            "updated": (),
            "unchanged": (),
            "failed": (),
        }

    updated: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    outcomes: dict[str, str] = {}
    for instance in instances:
        instance_id = str(instance.instance_id)
        if not instance.buy_limit_enabled:
            unchanged.append(instance_id)
            outcomes[instance_id] = "DISABLED_UNCHANGED"
            continue
        try:
            recommended, _minimum = recommendation_provider(window, instance_id)
        except Exception:
            failed.append(instance_id)
            outcomes[instance_id] = "RECOMMENDATION_FAILED"
            continue

        target_enabled = True
        target_amount: int | None = None
        target_ratio = instance.buy_limit_adjustment_ratio
        outcome = "WAITING"
        if recommended is not None:
            if recommended > total_budget:
                target_enabled = False
                target_ratio = None
                outcome = "RECOMMENDATION_EXCEEDS_TOTAL_BUDGET"
            else:
                if target_ratio is None:
                    effective_amount = recommended
                else:
                    effective_amount = floor_money_to_won(
                        Decimal(recommended) * target_ratio
                    )
                if effective_amount is None or effective_amount <= 0:
                    failed.append(instance_id)
                    outcomes[instance_id] = "EFFECTIVE_LIMIT_INVALID"
                    continue
                if effective_amount > total_budget:
                    target_enabled = False
                    target_ratio = None
                    outcome = "EFFECTIVE_LIMIT_EXCEEDS_TOTAL_BUDGET"
                else:
                    target_amount = effective_amount
                    outcome = "APPLIED"

        if (
            instance.buy_limit_enabled == target_enabled
            and instance.buy_limit_amount == target_amount
            and instance.buy_limit_adjustment_ratio == target_ratio
        ):
            unchanged.append(instance_id)
            outcomes[instance_id] = f"{outcome}_UNCHANGED"
            continue
        result = repo.update_buy_limit(
            instance_id,
            enabled=target_enabled,
            amount=target_amount,
            adjustment_ratio=target_ratio,
        )
        if not result.success or result.instance is None:
            failed.append(instance_id)
            outcomes[instance_id] = result.error_code or "PERSISTENCE_FAILED"
            continue
        updated.append(instance_id)
        outcomes[instance_id] = outcome

    return {
        "ok": not failed,
        "reason": "COMPLETED" if not failed else "PARTIAL_FAILURE",
        "updated": tuple(updated),
        "unchanged": tuple(unchanged),
        "failed": tuple(failed),
        "outcomes": outcomes,
    }
