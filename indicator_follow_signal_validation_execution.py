# -*- coding: utf-8 -*-
"""Pure Validation-only virtual execution for Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from buy_execution_policy import STATUS_READY, evaluate_buy_execution_policy


DEFAULT_VALIDATION_EXECUTION = {
    "schema_version": "1.0",
    "first_buy_quantity": 1,
    "buy_hoga_mode": "SINGLE",
    "sell_hoga_mode": "SINGLE",
    "repeat_mode": "ROUND",
    "round_operator": "ADD",
    "round_budget_value": 0.5,
    "budget_ratio": 0.5,
    "active_direction": "UP",
    "active_ratio": 0.45,
    "active_compare": ">=",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validation_virtual_fill_price(candle: Mapping[str, Any]) -> float | None:
    """Return the approved OHLC/4 Validation-only representative fill price."""
    if not isinstance(candle, Mapping):
        return None
    values: list[float] = []
    for key in ("open", "high", "low", "close"):
        number = _number(candle.get(key))
        if number is None or number <= 0:
            return None
        values.append(number)
    return sum(values) / 4.0


def normalize_validation_execution_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value) if isinstance(value, Mapping) else {}
    policy = dict(DEFAULT_VALIDATION_EXECUTION)
    policy.update({
        key: source[key]
        for key in policy
        if key in source
    })
    policy["schema_version"] = "1.0"
    policy["first_buy_quantity"] = 1
    policy["buy_hoga_mode"] = "SINGLE"
    policy["sell_hoga_mode"] = "SINGLE"

    repeat_mode = str(policy.get("repeat_mode") or "").strip().upper()
    round_operator = str(policy.get("round_operator") or "").strip().upper()
    active_direction = str(policy.get("active_direction") or "").strip().upper()
    active_compare = str(policy.get("active_compare") or "").strip().upper()
    if repeat_mode not in {"ROUND", "BUDGET", "ACTIVE_BUY"}:
        raise ValueError("VALIDATION_REPEAT_MODE_INVALID")
    if round_operator not in {"ADD", "MULTIPLY"}:
        raise ValueError("VALIDATION_ROUND_OPERATOR_INVALID")
    if active_direction not in {"UP", "DOWN", "BOTH"}:
        raise ValueError("VALIDATION_ACTIVE_DIRECTION_INVALID")
    valid_pair = (
        active_direction in {"UP", "DOWN"} and active_compare in {">=", "<="}
    ) or (
        active_direction == "BOTH" and active_compare in {"WITHIN", "OUTSIDE"}
    )
    if not valid_pair:
        raise ValueError("VALIDATION_ACTIVE_COMPARATOR_INVALID")

    for key in ("round_budget_value", "budget_ratio", "active_ratio"):
        number = _number(policy.get(key))
        if number is None or number < 0:
            raise ValueError(f"{key.upper()}_INVALID")
        policy[key] = number
    return policy


@dataclass(frozen=True, slots=True)
class ValidationVirtualFill:
    side: str
    evaluation_index: int
    evaluation_time: str
    price: float
    quantity: int
    amount: float
    buy_round: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ValidationVirtualCycle:
    cycle_number: int
    buy_indexes: tuple[int, ...]
    buy_start_index: int
    buy_end_index: int
    buy_count: int
    buy_quantity: int
    buy_cost: float
    average_buy_price: float
    sell_index: int
    sell_price: float
    sell_quantity: int
    estimated_return_percent: float


@dataclass(frozen=True, slots=True)
class ValidationExecutionSimulation:
    fills: tuple[ValidationVirtualFill, ...]
    cycles: tuple[ValidationVirtualCycle, ...]
    skipped_buys: tuple[tuple[int, str], ...]
    open_quantity: int
    open_average_price: float | None
    confirmed_buy_round: int


def _repeat_policy_rules(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "buy": {
            "execution": {
                "base": {
                    "buy_phase": "BASE",
                    "buy_round": 1,
                    "budget_reference": "STARTING_BUDGET",
                    "hoga_mode": "SINGLE",
                    "order_price_basis": "ORDER_PRICE",
                    "hoga_up": 0,
                    "hoga_down": 0,
                },
                "repeat": {
                    "buy_phase": "REPEAT",
                    "starts_from_round": 2,
                    "apply_all": True,
                    "detail_mode": policy["repeat_mode"],
                    "round_operator": policy["round_operator"],
                    "round_budget_value": policy["round_budget_value"],
                    "budget_ratio": policy["budget_ratio"],
                    "active_direction": policy["active_direction"],
                    "active_ratio": policy["active_ratio"],
                    "active_compare": policy["active_compare"],
                },
            }
        }
    }


def _repeat_quantity(
    *,
    policy: Mapping[str, Any],
    fill_price: float,
    confirmed_round: int,
    cumulative_buy_cost: float,
    base_buy_cost: float,
    previous_buy_cost: float,
    holding_quantity: int,
    average_buy_price: float,
) -> tuple[int | None, str]:
    result = evaluate_buy_execution_policy(
        signal_context={
            "signal_type": "BUY",
            "order_price": fill_price,
            "current_price": fill_price,
        },
        approved_rules=_repeat_policy_rules(policy),
        runtime_state_snapshot={
            "confirmed_current_buy_round": confirmed_round,
            "confirmed_cumulative_buy_budget": cumulative_buy_cost,
        },
        budget_context={
            "starting_budget_type": "QUANTITY",
            "starting_quantity": 1,
            "base_buy_budget": base_buy_cost,
            "previous_buy_budget": previous_buy_cost,
            "position_quantity": holding_quantity,
            "confirmed_average_buy_price": average_buy_price,
            "active_reference_price": fill_price,
            "actionable_acquisition_price": fill_price,
        },
    )
    if result.get("status") == STATUS_READY:
        quantity = result.get("quantity")
        if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
            return quantity, str(result.get("budget_reference") or "")
    evidence = result.get("evidence")
    budget_calc = evidence.get("budget_calculation") if isinstance(evidence, dict) else None
    active_calc = (
        budget_calc.get("active_buy_calculation")
        if isinstance(budget_calc, dict)
        else None
    )
    if isinstance(active_calc, dict):
        reason = str(active_calc.get("reason") or "")
        if reason:
            return None, reason
    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        return None, str(issues[0])
    return None, "VALIDATION_REPEAT_BUY_SKIPPED"


class ValidationVirtualPositionTracker:
    """Incremental, detached Validation Position state for replay prefixes."""

    validation_read_only_fast_path = True

    def __init__(self, policy: Mapping[str, Any] | None = None) -> None:
        self.policy = normalize_validation_execution_policy(policy)
        self.fills: list[ValidationVirtualFill] = []
        self.cycles: list[ValidationVirtualCycle] = []
        self.skipped: list[tuple[int, str]] = []
        self.holding_quantity = 0
        self.average_buy_price: float | None = None
        self.confirmed_round = 0
        self.cumulative_buy_cost = 0.0
        self.base_buy_cost = 0.0
        self.previous_buy_cost = 0.0
        self.buy_indexes: list[int] = []
        self.average_price_series: list[float | None] = []
        self.quantity_series: list[int] = []
        self.contributor_series: list[list[int]] = []
        self._processed_through = -1

    @staticmethod
    def _signals_at(
        index: int,
        candles: list[dict[str, Any]],
        entries: list[Any],
    ) -> set[str]:
        signals: set[str] = set()
        expected_time = str(candles[index].get("time") or "")
        for entry in entries:
            entry_index = getattr(entry, "evaluation_index", None)
            signal = str(getattr(entry, "signal", "") or "").upper()
            if (
                entry_index == index
                and signal in {"BUY", "SELL"}
                and expected_time == str(getattr(entry, "evaluation_time", "") or "")
            ):
                signals.add(signal)
        return signals

    def _record_pre_index_state(self) -> None:
        self.average_price_series.append(self.average_buy_price)
        self.quantity_series.append(self.holding_quantity)
        self.contributor_series.append(list(self.buy_indexes))

    def _apply_index(
        self,
        index: int,
        candles: list[dict[str, Any]],
        entries: list[Any],
    ) -> None:
        candle = candles[index]
        signals = self._signals_at(index, candles, entries)
        fill_price = validation_virtual_fill_price(candle)
        if "SELL" in signals:
            if (
                self.holding_quantity > 0
                and self.average_buy_price is not None
                and fill_price is not None
            ):
                sell_quantity = self.holding_quantity
                sell_amount = fill_price * sell_quantity
                estimated_return = (
                    (fill_price - self.average_buy_price)
                    / self.average_buy_price
                    * 100.0
                )
                self.fills.append(ValidationVirtualFill(
                    "SELL", index, str(candle.get("time") or ""), fill_price,
                    sell_quantity, sell_amount, None, "SINGLE_HOGA",
                ))
                self.cycles.append(ValidationVirtualCycle(
                    len(self.cycles) + 1,
                    tuple(self.buy_indexes),
                    self.buy_indexes[0],
                    self.buy_indexes[-1],
                    len(self.buy_indexes),
                    self.holding_quantity,
                    self.cumulative_buy_cost,
                    self.average_buy_price,
                    index,
                    fill_price,
                    sell_quantity,
                    estimated_return,
                ))
            if fill_price is not None:
                self.holding_quantity = 0
                self.average_buy_price = None
                self.confirmed_round = 0
                self.cumulative_buy_cost = 0.0
                self.base_buy_cost = 0.0
                self.previous_buy_cost = 0.0
                self.buy_indexes = []
            return

        if "BUY" not in signals:
            return
        if fill_price is None:
            self.skipped.append((index, "VALIDATION_VIRTUAL_FILL_PRICE_UNAVAILABLE"))
            return

        if self.holding_quantity == 0:
            quantity = 1
            reason = "FIRST_BUY_ONE_SHARE"
        else:
            quantity, reason = _repeat_quantity(
                policy=self.policy,
                fill_price=fill_price,
                confirmed_round=self.confirmed_round,
                cumulative_buy_cost=self.cumulative_buy_cost,
                base_buy_cost=self.base_buy_cost,
                previous_buy_cost=self.previous_buy_cost,
                holding_quantity=self.holding_quantity,
                average_buy_price=float(self.average_buy_price),
            )
            if quantity is None:
                self.skipped.append((index, reason))
                return

        amount = fill_price * quantity
        previous_cost = float(self.average_buy_price or 0.0) * self.holding_quantity
        self.holding_quantity += quantity
        self.average_buy_price = (
            previous_cost + amount
        ) / self.holding_quantity
        self.confirmed_round += 1
        self.cumulative_buy_cost += amount
        if self.confirmed_round == 1:
            self.base_buy_cost = amount
        self.previous_buy_cost = amount
        self.buy_indexes.append(index)
        self.fills.append(ValidationVirtualFill(
            "BUY", index, str(candle.get("time") or ""), fill_price,
            quantity, amount, self.confirmed_round, reason,
        ))

    def __call__(
        self,
        evaluation_index: int,
        side: str,
        candles: list[dict[str, Any]],
        prior_entries: list[Any],
    ) -> dict[str, Any]:
        return self.context_for(evaluation_index, side, candles, prior_entries)

    def context_for(
        self,
        evaluation_index: int,
        side: str,
        candles: list[dict[str, Any]],
        prior_entries: list[Any],
    ) -> dict[str, Any]:
        if (
            isinstance(evaluation_index, bool)
            or not isinstance(evaluation_index, int)
            or not 0 <= evaluation_index < len(candles)
        ):
            raise ValueError("evaluation_index is outside candles")
        while self._processed_through < evaluation_index - 1:
            next_index = self._processed_through + 1
            if len(self.average_price_series) <= next_index:
                self._record_pre_index_state()
            self._apply_index(next_index, candles, prior_entries)
            self._processed_through = next_index
        if len(self.average_price_series) <= evaluation_index:
            self._record_pre_index_state()
        return {
            "average_price_series": list(
                self.average_price_series[: evaluation_index + 1]
            ),
            "average_price": self.average_buy_price,
            "validation_trace_context": {
                "side": str(side or "").upper(),
                "evaluation_index": evaluation_index,
                "estimated_average_price": self.average_buy_price,
                "position_quantity": self.holding_quantity,
                "contributing_buy_indexes": list(self.buy_indexes),
                "average_source": "VALIDATION_VIRTUAL_POSITION_WEIGHTED_OHLC4",
            },
        }

    def finalize(
        self,
        candles: list[dict[str, Any]],
        entries: list[Any],
    ) -> ValidationExecutionSimulation:
        if candles:
            last_index = len(candles) - 1
            while self._processed_through < last_index:
                next_index = self._processed_through + 1
                if len(self.average_price_series) <= next_index:
                    self._record_pre_index_state()
                self._apply_index(next_index, candles, entries)
                self._processed_through = next_index
        return ValidationExecutionSimulation(
            tuple(self.fills),
            tuple(self.cycles),
            tuple(self.skipped),
            self.holding_quantity,
            self.average_buy_price,
            self.confirmed_round,
        )


def simulate_validation_execution(
    candles: list[dict[str, Any]],
    entries: list[Any],
    policy: Mapping[str, Any] | None = None,
) -> ValidationExecutionSimulation:
    """Replay signal entries into a detached virtual Position; never writes runtime state."""
    tracker = ValidationVirtualPositionTracker(policy)
    return tracker.finalize(candles, entries)
