# -*- coding: utf-8 -*-
"""Backtest parity contracts for Indicator Follow validation.

The chart/backtest path may use optimized scans for speed, but emitted signals
are authoritative only when they match the full production evaluator replay.
Execution parity is reported separately because Validation uses a detached
virtual broker model rather than the production order lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Iterable, Mapping

from indicator_follow_strategy_price_contract import (
    canonicalize_buy_strategy_price_comparison_rules,
)
from indicator_follow_validation_timeframe import normalize_validation_timeframe


SIGNAL_PARITY_PASS = "SIGNAL_PARITY_PASS"
SIGNAL_PARITY_FAIL = "SIGNAL_PARITY_FAIL"
EXECUTION_MODEL_SIMPLIFIED_CLOSE_FILL = "SIMPLIFIED_CLOSE_FILL"
EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT = "NOT_PRODUCTION_EQUIVALENT"
FORMING_BAR_PARITY_NOT_VERIFIED = "FORMING_BAR_PARITY_NOT_VERIFIED"
COMPLETED_BAR_PRODUCTION_PARITY_PASS = "COMPLETED_BAR_PRODUCTION_PARITY_PASS"
COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED = (
    "COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED"
)
CANDLE_PROJECTION_PARITY_UNKNOWN_INVALID = "UNKNOWN/INVALID"
PRODUCTION_DECISION_SCOPE_FILTER_SIGNAL_ONLY = "FILTER_SIGNAL_ONLY"
MARKET_SESSION_PARITY_FAIL = "MARKET_SESSION_PARITY_FAIL"
MARKET_SESSION_PARITY_PASS = "MARKET_SESSION_PARITY_PASS"
MARKET_SESSION_PARITY_UNKNOWN = "MARKET_SESSION_PARITY_UNKNOWN"
FILTER_SIGNAL_BACKTEST_AUTHORIZED = "FILTER_SIGNAL_BACKTEST_AUTHORIZED"
FILTER_SIGNAL_BACKTEST_BLOCKED = "FILTER_SIGNAL_BACKTEST_BLOCKED"
BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET = (
    "BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET"
)
LEGACY_VALIDATION_REGULAR_0900_1530 = (
    "LEGACY_VALIDATION_REGULAR_0900_1530"
)

# Compatibility aliases for the in-progress local tests/callers.  Reports use
# the explicit public tokens above.
PARITY_PASS = SIGNAL_PARITY_PASS
PARITY_FAIL = SIGNAL_PARITY_FAIL
EXECUTION_SIMPLIFIED = EXECUTION_MODEL_SIMPLIFIED_CLOSE_FILL

_SIGNAL_DEPENDENCY_FIELDS = {
    "target",
    "compare_target",
    "left_target",
    "right_target",
    "price_basis",
    "left_source",
    "right_source",
}
_UNSUPPORTED_SIGNAL_PRICE_TARGETS = {
    "ORDER_PRICE",
    "AVERAGE_PRICE",
    "BUY_PRICE",
    "PURCHASE_PRICE",
}


def _entry_value(entry: Any, name: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def emitted_signal_entries(entries: Iterable[Any]) -> tuple[Any, ...]:
    """Return only BUY/SELL entries emitted for their evaluated side."""
    result = []
    for entry in entries:
        side = str(_entry_value(entry, "evaluation_side") or "").strip().upper()
        signal = str(_entry_value(entry, "signal") or "").strip().upper()
        if side in {"BUY", "SELL"} and signal == side:
            result.append(entry)
    return tuple(result)


def signal_entry_identity(entry: Any) -> tuple[str, int, str]:
    side = str(_entry_value(entry, "evaluation_side") or "").strip().upper()
    index = _entry_value(entry, "evaluation_index")
    time = str(_entry_value(entry, "evaluation_time") or "")
    if (
        side not in {"BUY", "SELL"}
        or isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or not time
    ):
        raise ValueError("BACKTEST_SIGNAL_ENTRY_IDENTITY_INVALID")
    return side, index, time


def signal_entry_payload(entry: Any) -> tuple[Any, ...]:
    return (
        str(_entry_value(entry, "signal") or "").strip().upper(),
        _entry_value(entry, "signal_index"),
        str(_entry_value(entry, "signal_time") or ""),
        _entry_value(entry, "delay_bar"),
        tuple(_entry_value(entry, "matched_groups") or ()),
        tuple(_entry_value(entry, "details") or ()),
    )


_PAYLOAD_FIELD_NAMES = (
    "signal",
    "signal_index",
    "signal_time",
    "delay_bar",
    "matched_groups",
    "details",
)


def _payload_evidence(payload: tuple[Any, ...]) -> dict[str, Any]:
    return {
        name: list(value) if isinstance(value, tuple) else value
        for name, value in zip(_PAYLOAD_FIELD_NAMES, payload)
    }


@dataclass(frozen=True, slots=True)
class SignalReplayParityReport:
    status: str
    authoritative_count: int
    optimized_count: int
    missing: tuple[tuple[str, int, str], ...] = ()
    extra: tuple[tuple[str, int, str], ...] = ()
    mismatched: tuple[tuple[str, int, str], ...] = ()
    mismatch_evidence: tuple[
        tuple[
            tuple[str, int, str],
            tuple[Any, ...],
            tuple[Any, ...],
        ],
        ...,
    ] = ()

    @property
    def ok(self) -> bool:
        return self.status == PARITY_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "authoritative_count": self.authoritative_count,
            "optimized_count": self.optimized_count,
            "missing": [list(item) for item in self.missing],
            "extra": [list(item) for item in self.extra],
            "mismatched": [list(item) for item in self.mismatched],
            "mismatch_evidence": [
                {
                    "identity": list(identity),
                    "authoritative": _payload_evidence(authoritative),
                    "optimized": _payload_evidence(optimized),
                }
                for identity, authoritative, optimized in self.mismatch_evidence
            ],
        }


def compare_signal_replay_entries(
    authoritative_entries: Iterable[Any],
    optimized_entries: Iterable[Any],
) -> SignalReplayParityReport:
    authoritative = {
        signal_entry_identity(entry): signal_entry_payload(entry)
        for entry in emitted_signal_entries(authoritative_entries)
    }
    optimized = {
        signal_entry_identity(entry): signal_entry_payload(entry)
        for entry in emitted_signal_entries(optimized_entries)
    }
    authoritative_keys = set(authoritative)
    optimized_keys = set(optimized)
    missing = tuple(sorted(authoritative_keys - optimized_keys))
    extra = tuple(sorted(optimized_keys - authoritative_keys))
    mismatched = tuple(
        sorted(
            key
            for key in authoritative_keys & optimized_keys
            if authoritative[key] != optimized[key]
        )
    )
    mismatch_evidence = tuple(
        (key, authoritative[key], optimized[key])
        for key in mismatched
    )
    return SignalReplayParityReport(
        PARITY_PASS if not (missing or extra or mismatched) else PARITY_FAIL,
        len(authoritative),
        len(optimized),
        missing,
        extra,
        mismatched,
        mismatch_evidence,
    )


def require_signal_replay_parity(
    authoritative_entries: Iterable[Any],
    optimized_entries: Iterable[Any],
) -> SignalReplayParityReport:
    report = compare_signal_replay_entries(
        authoritative_entries,
        optimized_entries,
    )
    if not report.ok:
        summary = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
        raise ValueError(f"BACKTEST_SIGNAL_PARITY_MISMATCH: {summary}")
    return report



_UI_SELL_SIGNAL_NAMES = frozenset({
    "ui_condition_a",
    "ui_condition_b",
    "ui_condition_c",
})


def _production_relevant_sell_rules(sell: Mapping[str, Any]) -> dict[str, Any]:
    """Detach SELL rules while pruning only UI signals ignored by production expression aggregation."""
    result = deepcopy(dict(sell))
    signals = result.get("signals")
    if not isinstance(signals, dict):
        return result

    active_ui_names = [
        name
        for name in _UI_SELL_SIGNAL_NAMES
        if isinstance(signals.get(name), dict)
        and bool(signals[name].get("enabled", True))
    ]
    if not active_ui_names:
        return result

    contracts = [
        signals[name].get("signal_expression")
        for name in active_ui_names
    ]
    if (
        len(contracts) != len(active_ui_names)
        or not contracts
        or not all(isinstance(value, Mapping) for value in contracts)
        or not all(value == contracts[0] for value in contracts[1:])
    ):
        return result

    contract = contracts[0]
    identifier_map = contract.get("identifier_map")
    identifiers = contract.get("identifiers")
    if (
        not isinstance(identifier_map, Mapping)
        or not isinstance(identifiers, list)
        or not identifiers
    ):
        return result

    referenced: set[str] = set()
    for raw_identifier in identifiers:
        identifier = str(raw_identifier or "").strip().upper()
        signal_name = str(identifier_map.get(identifier) or "").strip()
        if signal_name not in _UI_SELL_SIGNAL_NAMES or signal_name not in signals:
            return result
        referenced.add(signal_name)

    for name in _UI_SELL_SIGNAL_NAMES:
        if name in signals and name not in referenced:
            signals.pop(name, None)
    return result

def signal_rule_support_report(
    rules: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed when signal semantics depend on unreplayable execution prices."""
    source = canonicalize_buy_strategy_price_comparison_rules(
        dict(rules) if isinstance(rules, Mapping) else {}
    )
    issues: list[dict[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                normalized_key = str(key or "").strip().lower()
                if normalized_key in _SIGNAL_DEPENDENCY_FIELDS:
                    token = str(item or "").strip().upper()
                    if token in _UNSUPPORTED_SIGNAL_PRICE_TARGETS:
                        issues.append({
                            "path": child,
                            "target": token,
                            "reason": "EXECUTION_PRICE_NOT_REPLAYABLE",
                        })
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    # Execution/method sections are not signal conditions and are graded by the
    # execution-model report instead.
    signal_rules = deepcopy(source)
    buy = signal_rules.get("buy")
    if isinstance(buy, dict):
        buy.pop("execution", None)
    sell = signal_rules.get("sell")
    if isinstance(sell, dict):
        normalized_sell = _production_relevant_sell_rules(sell)
        normalized_sell.pop("method", None)
        signal_rules["sell"] = normalized_sell
    for key in (
        "buy_management",
        "order_policy",
        "cancel_policy",
        "signal_runtime_policy",
        "indicator_follow_ui_state",
        "indicator_follow_rule_preview",
        "validation_visualization_rules",
        "validation_execution",
    ):
        signal_rules.pop(key, None)
    walk(signal_rules, "")

    return {
        "status": PARITY_FAIL if issues else PARITY_PASS,
        "signal_backtest_authorized": not issues,
        "issues": issues,
    }


def require_signal_rule_support(
    rules: Mapping[str, Any] | None,
) -> dict[str, Any]:
    report = signal_rule_support_report(rules)
    if report["status"] != PARITY_PASS:
        summary = json.dumps(report, ensure_ascii=False, sort_keys=True)
        raise ValueError(f"BACKTEST_SIGNAL_RULE_UNSUPPORTED: {summary}")
    return report


def execution_model_report(rules: Mapping[str, Any] | None) -> dict[str, Any]:
    """Describe current P&L-model confidence without pretending broker parity."""
    source = deepcopy(dict(rules)) if isinstance(rules, Mapping) else {}
    buy = source.get("buy") if isinstance(source.get("buy"), dict) else {}
    sell = source.get("sell") if isinstance(source.get("sell"), dict) else {}
    limitations = [
        "MULTI_HOGA_NOT_REPRODUCED",
        "MULTI_TIME_NOT_REPRODUCED",
        "MULTI_RATIO_NOT_REPRODUCED",
        "PARTIAL_FILL_NOT_REPRODUCED",
        "UNFILLED_ORDER_NOT_REPRODUCED",
        "CANCEL_NOT_REPRODUCED",
        "RESET_NOT_REPRODUCED",
        "BROKER_FILLS_NOT_REPRODUCED",
    ]
    observed_policy_gaps = []
    if isinstance(buy.get("execution"), dict) and buy["execution"]:
        observed_policy_gaps.append("BUY_EXECUTION_POLICY_SIMPLIFIED")
    if isinstance(sell.get("method"), dict) and sell["method"]:
        observed_policy_gaps.append("SELL_EXECUTION_POLICY_SIMPLIFIED")
    if isinstance(source.get("signal_runtime_policy"), dict):
        observed_policy_gaps.append("SIGNAL_RUNTIME_POLICY_NOT_EXECUTION_REPLAYED")
    return {
        "status": EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT,
        "model_name": EXECUTION_MODEL_SIMPLIFIED_CLOSE_FILL,
        "execution_parity": EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT,
        "fill_assumption": "CANDLE_CLOSE",
        "production_pnl_realistic": False,
        "pnl_backtest_authorized": False,
        "signal_backtest_requires_parity_gate": True,
        "limitations": limitations,
        "limitations_text": (
            "Does not reproduce multi-hoga, multi-time, multi-ratio, partial "
            "or unfilled orders, cancellation/reset behavior, or broker fills."
        ),
        "pnl_statement": (
            "Validation PnL uses a simplified close-fill model and is not "
            "production-realistic."
        ),
        "observed_policy_gaps": observed_policy_gaps,
    }


def candle_projection_parity_report(
    historical_snapshot: Any,
    *,
    as_of: datetime | None = None,
    calculation_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the unverified source boundary for Validation candle rows."""
    validation_source = "BROKER_DIRECT_TIMEFRAME"
    production_source = "MINUTE_PLUS_REALTIME_SHADOW_FORMING_BASE_BAR"
    base_report = {
        "validation_source": validation_source,
        "production_source": production_source,
        "latest_row_forming": False,
        "production_equivalent": False,
    }
    try:
        # Keep validation-row normalization and market-period math owned by the
        # replay boundary rather than creating a second candle-time model here.
        from routines.지표추종매매.routine_validation_replay import (
            _latest_candle_is_forming,
            project_validation_candles,
        )

        candles, _dropped_count = project_validation_candles(
            historical_snapshot,
            as_of=as_of,
        )
        if not candles:
            raise ValueError("NO_VALID_VALIDATION_CANDLES")
        latest_time = str(candles[-1].get("time") or "")
        latest_row_forming = _latest_candle_is_forming(
            latest_time,
            historical_snapshot.timeframe_minutes,
            timeframe_key=historical_snapshot.timeframe_key,
            as_of=as_of,
        )
    except (AttributeError, TypeError, ValueError):
        return {
            "status": CANDLE_PROJECTION_PARITY_UNKNOWN_INVALID,
            **base_report,
            "reason": (
                "The historical snapshot is invalid or has no valid candle rows; "
                "projection-source parity cannot be assessed."
            ),
        }

    provenance = (
        calculation_provenance
        if isinstance(calculation_provenance, Mapping)
        else {}
    )
    if provenance.get("calculation_source") == "PRODUCTION_M1_SESSION_AGGREGATION":
        source_ready = (
            provenance.get("source_timeframe_key") == "M1"
            and provenance.get("timeframe_key") == historical_snapshot.timeframe_key
            and provenance.get("source_coverage_sufficient") is True
            and bool(provenance.get("production_calculation_cache_identity"))
        )
        forming_included = provenance.get("forming_bar_included") is True
        completed_equivalent = source_ready and not forming_included
        return {
            "status": (
                COMPLETED_BAR_PRODUCTION_PARITY_PASS
                if completed_equivalent
                else FORMING_BAR_PARITY_NOT_VERIFIED
                if forming_included
                else COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED
            ),
            "validation_source": "PRODUCTION_M1_SESSION_AGGREGATION",
            "production_source": production_source,
            "latest_row_forming": forming_included,
            "forming_bar_status": FORMING_BAR_PARITY_NOT_VERIFIED,
            "production_equivalent": completed_equivalent,
            "reason": "" if completed_equivalent else "Production completed-bar source coverage is unverified.",
        }

    return {
        "status": (
            FORMING_BAR_PARITY_NOT_VERIFIED
            if latest_row_forming
            else COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED
        ),
        **base_report,
        "latest_row_forming": latest_row_forming,
        "reason": (
            "Validation uses broker-direct selected-timeframe rows while production "
            "uses minute candles plus the realtime shadow projected as a forming "
            "base bar; equality between those projection paths is not verified."
        ),
    }


def market_session_parity_report(
    production_contract: Mapping[str, Any] | None,
    validation_market_scope: Mapping[str, Any] | None,
    timeframe_key: object = "",
    timeframe_minutes: object = None,
    *,
    calculation_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare current Validation market scope with resolved production sessions."""
    fallback_minutes = (
        timeframe_minutes
        if (
            isinstance(timeframe_minutes, int)
            and not isinstance(timeframe_minutes, bool)
            and timeframe_minutes > 0
        )
        else 5
    )
    timeframe = normalize_validation_timeframe(
        timeframe_key,
        fallback_minutes=fallback_minutes,
    )
    scope = (
        dict(validation_market_scope)
        if isinstance(validation_market_scope, Mapping)
        else {}
    )
    regular_market_only = scope.get("regular_market_only") is True
    validation_mode = BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET
    production_ready = (
        isinstance(production_contract, Mapping)
        and production_contract.get("status") == "PRODUCTION_SESSION_READY"
        and production_contract.get("ready") is True
    )
    provenance = (
        calculation_provenance
        if isinstance(calculation_provenance, Mapping)
        else {}
    )
    calculation_contract = provenance.get("production_session_contract")
    matching_production_session = (
        production_ready
        and provenance.get("calculation_source") == "PRODUCTION_M1_SESSION_AGGREGATION"
        and isinstance(calculation_contract, Mapping)
        and calculation_contract.get("status") == "PRODUCTION_SESSION_READY"
        and calculation_contract.get("ready") is True
        and calculation_contract.get("session_windows")
        == production_contract.get("session_windows")
        and calculation_contract.get("selected_ats")
        == production_contract.get("selected_ats")
        and calculation_contract.get("selection_source")
        == production_contract.get("selection_source")
    )
    if provenance.get("calculation_source") == "PRODUCTION_M1_SESSION_AGGREGATION":
        validation_mode = "PRODUCTION_M1_SESSION_AGGREGATION"
    return {
        "status": (
            MARKET_SESSION_PARITY_PASS
            if matching_production_session
            else MARKET_SESSION_PARITY_FAIL
            if production_ready
            else MARKET_SESSION_PARITY_UNKNOWN
        ),
        "validation_mode": validation_mode,
        "validation_source": validation_mode,
        "validation_regular_market_only": regular_market_only,
        "timeframe_key": str(timeframe["key"]),
        "timeframe_minutes": timeframe["minutes"],
        "production_session_status": (
            str(production_contract.get("status") or "")
            if isinstance(production_contract, Mapping)
            else ""
        ),
        "production_session_ready": production_ready,
        "production_session_windows": deepcopy(
            list(production_contract.get("session_windows") or ())
            if isinstance(production_contract, Mapping)
            else []
        ),
        "production_equivalent": matching_production_session,
        "reason": (
            ""
            if matching_production_session
            else "Current Validation market-session mode is not production-equivalent."
            if production_ready
            else "Production market-session metadata is unavailable."
        ),
    }


def filter_signal_backtest_authorization_report(
    signal_rule_support: Mapping[str, Any] | None,
    signal_parity: Mapping[str, Any] | None,
    market_session_parity: Mapping[str, Any] | None,
    candle_projection_parity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Authorize filter-signal backtest claims only when all four trust gates pass."""
    gate_authorization = {
        "signal_rule_support": (
            isinstance(signal_rule_support, Mapping)
            and signal_rule_support.get("signal_backtest_authorized") is True
        ),
        "signal_parity": (
            isinstance(signal_parity, Mapping)
            and signal_parity.get("status") == SIGNAL_PARITY_PASS
        ),
        "market_session_parity": (
            isinstance(market_session_parity, Mapping)
            and market_session_parity.get("production_equivalent") is True
        ),
        "candle_projection_parity": (
            isinstance(candle_projection_parity, Mapping)
            and candle_projection_parity.get("production_equivalent") is True
        ),
    }
    blockers = [
        gate.upper()
        for gate, authorized in gate_authorization.items()
        if not authorized
    ]
    authorized = not blockers
    return {
        "status": (
            FILTER_SIGNAL_BACKTEST_AUTHORIZED
            if authorized
            else FILTER_SIGNAL_BACKTEST_BLOCKED
        ),
        "filter_signal_backtest_authorized": authorized,
        "required_gates": gate_authorization,
        "blockers": blockers,
    }


def production_decision_scope_report(
    rules: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Describe the production decisions intentionally outside chart replay."""
    return {
        "status": PRODUCTION_DECISION_SCOPE_FILTER_SIGNAL_ONLY,
        "production_order_signal_equivalent": False,
        "covered": [
            "RAW_FILTER_EVALUATOR",
            "BUY_SELL_CONFLICT_SELECTOR",
        ],
        "gaps": [
            "CYCLE_UNRESOLVED_GATE_NOT_REPLAYED",
            "BUY_PHASE_COMPLETED_GATE_NOT_REPLAYED",
            "BUY_EXECUTION_INTENT_READINESS_NOT_REPLAYED",
            "SELL_EXECUTION_INTENT_READINESS_NOT_REPLAYED",
        ],
    }
