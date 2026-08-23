"""Canonical numeric performance metrics derived from Phase 3 aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class MetricStatus(str, Enum):
    VALID = "VALID"
    VALID_ZERO = "VALID_ZERO"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPLETE = "INCOMPLETE"
    UNDEFINED = "UNDEFINED"


METRIC_CONTRACT_CLASSIFICATIONS: Mapping[str, str] = MappingProxyType(
    {
        "period": "INTENT_RECOVERABLE",
        "profit_amount": "CONTRACT_CONFIRMED",
        "profit_rate": "INTENT_RECOVERABLE",
        "average_amount": "INTENT_RECOVERABLE",
        "average_rate": "CONTRACT_CONFIRMED",
        "efficiency": "CONTRACT_CONFIRMED",
    }
)


@dataclass(frozen=True)
class CanonicalMetricValue:
    name: str
    value: int | float | None
    sort_value: int | float | None
    status: MetricStatus
    reasons: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return self.status in {MetricStatus.VALID, MetricStatus.VALID_ZERO}


@dataclass(frozen=True)
class CanonicalPerformanceMetrics:
    period: CanonicalMetricValue
    gross_profit_amount: CanonicalMetricValue
    net_profit_amount: CanonicalMetricValue
    profit_amount: CanonicalMetricValue
    profit_rate: CanonicalMetricValue
    average_amount: CanonicalMetricValue
    average_rate: CanonicalMetricValue
    efficiency: CanonicalMetricValue
    average_rate_diagnostics: "AverageRateDiagnostics"

    @property
    def completeness(self) -> Mapping[str, MetricStatus]:
        return MappingProxyType(
            {
                "period": self.period.status,
                "gross_profit_amount": self.gross_profit_amount.status,
                "net_profit_amount": self.net_profit_amount.status,
                "profit_amount": self.profit_amount.status,
                "profit_rate": self.profit_rate.status,
                "average_amount": self.average_amount.status,
                "average_rate": self.average_rate.status,
                "efficiency": self.efficiency.status,
            }
        )

    @property
    def unavailable_reasons(self) -> tuple[str, ...]:
        values = (
            self.period,
            self.gross_profit_amount,
            self.net_profit_amount,
            self.profit_amount,
            self.profit_rate,
            self.average_amount,
            self.average_rate,
            self.efficiency,
        )
        return tuple(sorted({reason for metric in values for reason in metric.reasons}))


@dataclass(frozen=True)
class AverageRateDiagnostics:
    period_count: int
    valid_rate_period_count: int
    incomplete_rate_period_count: int
    undefined_rate_period_count: int


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else round(float(value), 6)


def _available(name: str, value: Decimal) -> CanonicalMetricValue:
    number = _number(value)
    status = MetricStatus.VALID_ZERO if value == 0 else MetricStatus.VALID
    return CanonicalMetricValue(
        name=name,
        value=number,
        sort_value=number,
        status=status,
    )


def _missing(name: str, status: MetricStatus, *reasons: str) -> CanonicalMetricValue:
    return CanonicalMetricValue(
        name=name,
        value=None,
        sort_value=None,
        status=status,
        reasons=tuple(reasons),
    )


def _incomplete_value(name: str, value: Decimal, *reasons: str) -> CanonicalMetricValue:
    number = _number(value)
    return CanonicalMetricValue(
        name=name,
        value=number,
        sort_value=number,
        status=MetricStatus.INCOMPLETE,
        reasons=tuple(reasons),
    )


class CanonicalPerformanceMetricEngine:
    """Calculate metrics from an AggregateMetrics-compatible immutable value."""

    def calculate(self, aggregate: object) -> CanonicalPerformanceMetrics:
        event_count = int(getattr(aggregate, "event_count", getattr(aggregate, "performance_event_count", 0)))
        cost_basis = _decimal(getattr(aggregate, "realized_cost_basis"))
        gross_pnl = _decimal(getattr(aggregate, "gross_pnl"))
        net_complete = bool(getattr(aggregate, "net_pnl_complete"))
        net_value = getattr(aggregate, "net_pnl")
        date_complete = bool(getattr(aggregate, "realized_date_complete"))
        period_count = int(getattr(aggregate, "realized_stock_trade_date_count"))
        gross_profit = _decimal(getattr(aggregate, "gross_profit_sum"))
        gross_loss_abs = _decimal(getattr(aggregate, "gross_loss_abs_sum"))

        period = (
            _available("period", Decimal(period_count))
            if date_complete
            else _missing("period", MetricStatus.INCOMPLETE, "REALIZED_DATE_INCOMPLETE")
        )
        gross_amount = _available("gross_profit_amount", gross_pnl)
        if net_complete and net_value is not None:
            net_amount = _available("net_profit_amount", _decimal(net_value))
            profit_amount = _available("profit_amount", _decimal(net_value))
        else:
            net_amount = _missing("net_profit_amount", MetricStatus.INCOMPLETE, "NET_PNL_INCOMPLETE")
            profit_amount = _missing("profit_amount", MetricStatus.INCOMPLETE, "NET_PNL_INCOMPLETE")

        profit_rate = self._profit_rate(profit_amount, cost_basis)
        average_amount = self._average_amount(profit_amount, period)
        average_rate, average_rate_diagnostics = self._average_rate(
            aggregate,
            event_count=event_count,
            period_count=period_count,
            date_complete=date_complete,
        )
        efficiency = self._efficiency(event_count, gross_profit, gross_loss_abs)
        return CanonicalPerformanceMetrics(
            period=period,
            gross_profit_amount=gross_amount,
            net_profit_amount=net_amount,
            profit_amount=profit_amount,
            profit_rate=profit_rate,
            average_amount=average_amount,
            average_rate=average_rate,
            efficiency=efficiency,
            average_rate_diagnostics=average_rate_diagnostics,
        )

    @staticmethod
    def _profit_rate(
        profit_amount: CanonicalMetricValue,
        cost_basis: Decimal,
    ) -> CanonicalMetricValue:
        if not profit_amount.is_available:
            return _missing("profit_rate", profit_amount.status, *profit_amount.reasons)
        if cost_basis == 0:
            return _missing("profit_rate", MetricStatus.UNDEFINED, "ZERO_REALIZED_COST_BASIS")
        value = _decimal(profit_amount.value) / cost_basis * Decimal(100)
        return _available("profit_rate", value)

    @staticmethod
    def _average_amount(
        profit_amount: CanonicalMetricValue,
        period: CanonicalMetricValue,
    ) -> CanonicalMetricValue:
        if not profit_amount.is_available:
            return _missing("average_amount", profit_amount.status, *profit_amount.reasons)
        if not period.is_available:
            return _missing("average_amount", period.status, *period.reasons)
        if period.value == 0:
            return _missing("average_amount", MetricStatus.UNDEFINED, "ZERO_REALIZED_TRADE_DATES")
        return _available(
            "average_amount",
            _decimal(profit_amount.value) / _decimal(period.value),
        )

    @staticmethod
    def _average_rate(
        aggregate: object,
        *,
        event_count: int,
        period_count: int,
        date_complete: bool,
    ) -> tuple[CanonicalMetricValue, AverageRateDiagnostics]:
        facts = tuple(getattr(aggregate, "period_performance_facts", ()))
        valid_rates: list[Decimal] = []
        incomplete_count = 0
        undefined_count = 0
        for fact in facts:
            if not bool(getattr(fact, "net_pnl_complete")) or getattr(fact, "net_pnl") is None:
                incomplete_count += 1
                continue
            cost_basis = _decimal(getattr(fact, "realized_cost_basis"))
            if cost_basis == 0:
                undefined_count += 1
                continue
            valid_rates.append(_decimal(getattr(fact, "net_pnl")) / cost_basis * Decimal(100))

        diagnostics = AverageRateDiagnostics(
            period_count=period_count,
            valid_rate_period_count=len(valid_rates),
            incomplete_rate_period_count=incomplete_count,
            undefined_rate_period_count=undefined_count,
        )
        if event_count == 0:
            return (
                _missing("average_rate", MetricStatus.UNAVAILABLE, "NO_REALIZATION_EVENTS"),
                diagnostics,
            )
        if not facts:
            if not date_complete:
                return (
                    _missing("average_rate", MetricStatus.INCOMPLETE, "PERIOD_DATE_INCOMPLETE"),
                    diagnostics,
                )
            return (
                _missing("average_rate", MetricStatus.UNAVAILABLE, "PERIOD_RATE_FACTS_UNAVAILABLE"),
                diagnostics,
            )
        reasons: list[str] = []
        date_incomplete = not date_complete or len(facts) != period_count
        if date_incomplete:
            reasons.append("PERIOD_DATE_INCOMPLETE")
        if incomplete_count:
            reasons.append("PERIOD_NET_PNL_INCOMPLETE")
        if undefined_count:
            reasons.append("PERIOD_ZERO_COST_BASIS")
        if not valid_rates:
            status = (
                MetricStatus.INCOMPLETE
                if incomplete_count or date_incomplete
                else MetricStatus.UNDEFINED
            )
            return _missing("average_rate", status, *reasons), diagnostics
        average = sum(valid_rates, Decimal(0)) / Decimal(len(valid_rates))
        if reasons:
            return _incomplete_value("average_rate", average, *reasons), diagnostics
        return _available("average_rate", average), diagnostics

    @staticmethod
    def _efficiency(
        event_count: int,
        gross_profit: Decimal,
        gross_loss_abs: Decimal,
    ) -> CanonicalMetricValue:
        if event_count == 0:
            return _missing("efficiency", MetricStatus.UNAVAILABLE, "NO_REALIZATION_EVENTS")
        if gross_loss_abs == 0:
            return _missing("efficiency", MetricStatus.UNDEFINED, "ZERO_GROSS_LOSS_DENOMINATOR")
        return _available("efficiency", gross_profit / gross_loss_abs)


_SORT_STATUS_RANK = {
    MetricStatus.VALID: 0,
    MetricStatus.VALID_ZERO: 0,
    MetricStatus.INCOMPLETE: 1,
    MetricStatus.UNDEFINED: 2,
    MetricStatus.UNAVAILABLE: 3,
}


def canonical_metric_sort_key(
    metric: CanonicalMetricValue,
    *,
    descending: bool = False,
) -> tuple[int, Decimal]:
    """Keep valid metrics first while using the exact displayed numeric result."""
    numeric = _decimal(metric.sort_value) if metric.sort_value is not None else Decimal(0)
    return (_SORT_STATUS_RANK[metric.status], -numeric if descending else numeric)
