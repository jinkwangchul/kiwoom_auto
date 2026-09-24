# -*- coding: utf-8 -*-
"""Pure, detached contracts for candle-only indicator signal validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from engines.condition_engine import parse_condition_expression
from indicator_follow_signal_validation_execution import (
    DEFAULT_VALIDATION_EXECUTION,
    normalize_validation_execution_policy,
    validation_virtual_fill_price,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
)
from indicator_follow_validation_timeframe import validation_timeframe_from_rules
from gui_indicator_follow_sell_controls import (
    SELL_PRICE_COMBO_VALUES,
    require_resolved_sell_price_selections,
    sell_price_selection_issues,
)
from gui_indicator_follow_buy_controls import (
    require_resolved_buy_bollinger_sign_selection,
)


_FORBIDDEN_PRICE_TARGETS = {
    "ORDER_PRICE",
    "AVG_PRICE",
    "AVERAGE_PRICE",
    "BUY_PRICE",
    "PURCHASE_PRICE",
}
_DEPENDENCY_FIELDS = {
    "target",
    "compare_target",
    "left_target",
    "right_target",
    "basis",
    "source",
    "price_basis",
}
_SELL_PREVIEW_TARGETS = {
    "sell.signals.ui_preview_condition_a": "ui_condition_a",
    "sell.signals.ui_preview_condition_b": "ui_condition_b",
    "sell.signals.ui_preview_condition_c": "ui_condition_c",
}
_SELL_VALIDATION_SIGNAL_NAMES = frozenset(_SELL_PREVIEW_TARGETS.values())
_SELL_GROUP_KEYS = {
    "A": "condition_a",
    "B": "condition_b",
    "C": "condition_c",
}
DEFAULT_VALIDATION_MARKET_SCOPE = {
    "regular_market_only": False,
}


def _referenced_sell_groups(ui_state: Mapping[str, Any]) -> tuple[str, ...]:
    if not isinstance(ui_state, Mapping):
        raise TypeError("ui_state must be a mapping")
    basic = ui_state.get("basic")
    expression = (
        str(basic.get("sell_signal_expr_line") or "").strip()
        if isinstance(basic, Mapping)
        else ""
    )
    if not expression:
        return ()
    parsed = parse_condition_expression(
        expression,
        allowed_identifiers=set(_SELL_GROUP_KEYS),
        allow_duplicate_identifiers=False,
    )
    if not parsed.get("ok"):
        raise ValueError(f"매도 신호 조합식 오류: {parsed.get('reason')}")
    return tuple(dict.fromkeys(
        str(identifier or "").strip().upper()
        for identifier in parsed.get("identifiers", [])
    ))


def expression_aware_sell_price_selection_issues(
    ui_state: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return unresolved operands only for referenced, active SELL GAP rows."""
    referenced_groups = _referenced_sell_groups(ui_state)
    sell_ui = ui_state.get("sell_ui")
    conditions = (
        sell_ui.get("signal_conditions")
        if isinstance(sell_ui, Mapping)
        else None
    )
    if not isinstance(conditions, Mapping):
        return ()
    issues = []
    for group_name in referenced_groups:
        condition = conditions.get(_SELL_GROUP_KEYS[group_name])
        if not isinstance(condition, Mapping) or condition.get("gap_check") is not True:
            continue
        for field_name, side_name in (
            ("gap_left_combo", "왼쪽"),
            ("gap_right_combo", "오른쪽"),
        ):
            if str(condition.get(field_name) or "").strip() in SELL_PRICE_COMBO_VALUES:
                continue
            issues.append(f"매도조건 {group_name}의 가격비교 {side_name} 기준을 선택하세요.")
    return tuple(issues)


def require_expression_aware_sell_price_selections(
    ui_state: Mapping[str, Any],
) -> None:
    issues = expression_aware_sell_price_selection_issues(ui_state)
    if issues:
        raise ValueError("가격 기준 재선택 필요: " + " ".join(issues))


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


@dataclass(frozen=True, slots=True, init=False)
class IndicatorFollowSignalValidationSeed:
    """Immutable entry payload containing current unsaved settings values."""

    settings_snapshot: ValidationSettingsSnapshot
    _ui_state_json: str

    def __init__(
        self,
        settings_snapshot: ValidationSettingsSnapshot,
        ui_state: Mapping[str, Any],
    ) -> None:
        if not isinstance(settings_snapshot, ValidationSettingsSnapshot):
            raise TypeError("settings_snapshot must be ValidationSettingsSnapshot")
        if not isinstance(ui_state, Mapping):
            raise TypeError("ui_state must be a mapping")
        signal_ui_state = project_signal_validation_ui_state(ui_state)
        canonical = json.dumps(
            signal_ui_state,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied_snapshot = ValidationSettingsSnapshot(
            project_signal_validation_rules(
                settings_snapshot.to_dict(),
            )
        )
        object.__setattr__(self, "settings_snapshot", copied_snapshot)
        object.__setattr__(self, "_ui_state_json", canonical)

    def to_ui_state(self) -> dict[str, Any]:
        value = json.loads(self._ui_state_json)
        if not isinstance(value, dict):
            raise ValueError("canonical UI state must decode to an object")
        return value


@dataclass(frozen=True, slots=True)
class IndicatorFollowSignalValidationRunRequest:
    """Immutable V2 run input: signal settings plus requested Candle count."""

    settings_snapshot: ValidationSettingsSnapshot
    candle_count: int
    force_historical_refresh: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.settings_snapshot, ValidationSettingsSnapshot):
            raise TypeError("settings_snapshot must be ValidationSettingsSnapshot")
        if (
            isinstance(self.candle_count, bool)
            or not isinstance(self.candle_count, int)
            or self.candle_count <= 0
        ):
            raise ValueError("candle_count must be a positive integer")
        if not isinstance(self.force_historical_refresh, bool):
            raise TypeError("force_historical_refresh must be a bool")


@dataclass(frozen=True, slots=True, init=False)
class IndicatorFollowSignalValidationApplyPayload:
    """Immutable signal-only values explicitly requested for source-dialog apply."""

    _ui_state_json: str

    def __init__(self, ui_state: Mapping[str, Any]) -> None:
        if not isinstance(ui_state, Mapping):
            raise TypeError("ui_state must be a mapping")
        require_expression_aware_sell_price_selections(ui_state)
        canonical = json.dumps(
            project_signal_validation_apply_ui_state(ui_state),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "_ui_state_json", canonical)

    def to_ui_state(self) -> dict[str, Any]:
        value = json.loads(self._ui_state_json)
        if not isinstance(value, dict):
            raise ValueError("canonical apply UI state must decode to an object")
        return value


@dataclass(frozen=True, slots=True, init=False)
class IndicatorFollowSignalValidationRestorePayload:
    """Immutable V2-entry values for restore without Candidate approval."""

    _ui_state_json: str

    def __init__(self, ui_state: Mapping[str, Any]) -> None:
        if not isinstance(ui_state, Mapping):
            raise TypeError("ui_state must be a mapping")
        canonical = json.dumps(
            project_signal_validation_restore_ui_state(ui_state),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "_ui_state_json", canonical)

    def to_ui_state(self) -> dict[str, Any]:
        value = json.loads(self._ui_state_json)
        if not isinstance(value, dict):
            raise ValueError("canonical restore UI state must decode to an object")
        return value


def _depends_on_execution_price(value: Mapping[str, Any]) -> bool:
    for key, field_value in value.items():
        if str(key).strip().lower() not in _DEPENDENCY_FIELDS:
            continue
        token = str(field_value or "").strip().upper()
        if token in _FORBIDDEN_PRICE_TARGETS:
            return True
    return False


def _strip_dependent_conditions(value: Any) -> Any:
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if isinstance(item, Mapping) and _depends_on_execution_price(item):
                continue
            cleaned.append(_strip_dependent_conditions(item))
        return cleaned
    if isinstance(value, dict):
        return {
            key: _strip_dependent_conditions(item)
            for key, item in value.items()
        }
    return deepcopy(value)


def build_validation_average_price_context(
    evaluation_index: int,
    side: str,
    candles: list[dict[str, Any]],
    prior_entries: list[Any],
) -> dict[str, Any]:
    """Build a replay-local average from BUY virtual fills after the previous SELL."""
    if (
        isinstance(evaluation_index, bool)
        or not isinstance(evaluation_index, int)
        or not 0 <= evaluation_index < len(candles)
    ):
        raise ValueError("evaluation_index is outside candles")
    signals_by_index: dict[int, set[str]] = {}
    for entry in prior_entries:
        index = getattr(entry, "evaluation_index", None)
        signal = str(getattr(entry, "signal", "") or "").upper()
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < evaluation_index
            and signal in {"BUY", "SELL"}
        ):
            signals_by_index.setdefault(index, set()).add(signal)

    running_fill_prices: list[float] = []
    running_indexes: list[int] = []
    average_series: list[float | None] = []
    contributor_series: list[list[int]] = []
    for index in range(evaluation_index + 1):
        average = (
            sum(running_fill_prices) / len(running_fill_prices)
            if running_fill_prices
            else None
        )
        average_series.append(average)
        contributor_series.append(list(running_indexes))
        signals = signals_by_index.get(index, set())
        if "SELL" in signals:
            running_fill_prices.clear()
            running_indexes.clear()
            continue
        if "BUY" not in signals:
            continue
        fill_price = validation_virtual_fill_price(candles[index])
        if fill_price is not None:
            running_fill_prices.append(fill_price)
            running_indexes.append(index)

    current_average = average_series[evaluation_index]
    return {
        "average_price_series": average_series,
        "average_price": current_average,
        "validation_trace_context": {
            "side": str(side or "").upper(),
            "evaluation_index": evaluation_index,
            "estimated_average_price": current_average,
            "contributing_buy_indexes": contributor_series[evaluation_index],
            "average_source": "VALIDATION_BUY_CLOSE_SIGNAL_PRICE_SEGMENT",
        },
    }



_VALIDATION_REPEAT_MODE_TOKENS = {
    "회차기준": "ROUND",
    "회차증가": "ROUND",
    "예산기준": "BUDGET",
    "금액증가": "BUDGET",
    "능동매수": "ACTIVE_BUY",
}
_VALIDATION_DIRECTION_TOKENS = {
    "상향": "UP",
    "하향": "DOWN",
    "상하": "BOTH",
}
_VALIDATION_COMPARE_TOKENS = {
    "이상": ">=",
    "이하": "<=",
    "이내": "WITHIN",
    "이탈": "OUTSIDE",
}


def _validation_token(value: Any, mapping: Mapping[str, str], default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    upper = text.upper()
    if upper in set(mapping.values()):
        return upper
    return mapping.get(text, default)


def normalize_validation_market_scope(
    value: Mapping[str, Any] | None,
) -> dict[str, bool]:
    source = dict(value) if isinstance(value, Mapping) else {}
    regular_market_only = source.get("regular_market_only", False)
    if not isinstance(regular_market_only, bool):
        raise ValueError("VALIDATION_REGULAR_MARKET_ONLY_INVALID")
    return {"regular_market_only": regular_market_only}


def project_validation_market_scope(
    ui_state: Mapping[str, Any],
) -> dict[str, bool]:
    if not isinstance(ui_state, Mapping):
        raise TypeError("ui_state must be a mapping")
    existing = ui_state.get("validation_market_scope")
    return normalize_validation_market_scope(
        existing if isinstance(existing, Mapping) else None
    )


def project_validation_execution_state(
    ui_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Project only the approved simplified Validation execution controls."""
    if not isinstance(ui_state, Mapping):
        raise TypeError("ui_state must be a mapping")
    existing = ui_state.get("validation_execution")
    if isinstance(existing, Mapping):
        return normalize_validation_execution_policy(existing)

    buy_ui = ui_state.get("buy_ui")
    repeat = buy_ui.get("repeat") if isinstance(buy_ui, Mapping) else None
    repeat = repeat if isinstance(repeat, Mapping) else {}
    candidate = dict(DEFAULT_VALIDATION_EXECUTION)
    candidate.update({
        "repeat_mode": _validation_token(
            repeat.get("detail_mode_combo"),
            _VALIDATION_REPEAT_MODE_TOKENS,
            "ROUND",
        ),
        "round_operator": _validation_token(
            repeat.get("round_operator_combo"),
            {"+": "ADD", "x": "MULTIPLY", "X": "MULTIPLY", "*": "MULTIPLY"},
            "ADD",
        ),
        "round_budget_value": repeat.get("round_budget_line", 0.5),
        "budget_ratio": repeat.get("budget_ratio_line", 2.0),
        "active_direction": _validation_token(
            repeat.get("active_direction_combo"),
            _VALIDATION_DIRECTION_TOKENS,
            "UP",
        ),
        "active_ratio": repeat.get("active_ratio_line", 0.45),
        "active_compare": _validation_token(
            repeat.get("active_compare_combo"),
            _VALIDATION_COMPARE_TOKENS,
            ">=",
        ),
    })
    return normalize_validation_execution_policy(candidate)


def project_signal_validation_ui_state(
    ui_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only controls that can change candle/indicator signal locations."""
    if not isinstance(ui_state, Mapping):
        raise TypeError("ui_state must be a mapping")
    state = _json_copy(ui_state)
    basic = state.get("basic") if isinstance(state.get("basic"), dict) else {}
    safe_basic_names = {
        "basic_signal_interval_combo",
        "buy_signal_expr_line",
        "sell_signal_expr_line",
    }
    signal_filter = (
        state.get("buy_ui", {}).get("signal_filter", {})
        if isinstance(state.get("buy_ui"), dict)
        else {}
    )
    signal_conditions = (
        state.get("sell_ui", {}).get("signal_conditions", {})
        if isinstance(state.get("sell_ui"), dict)
        else {}
    )
    safe_conditions: dict[str, Any] = {}
    if isinstance(signal_conditions, dict):
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = signal_conditions.get(group_name)
            if not isinstance(group, dict):
                continue
            safe_conditions[group_name] = deepcopy(dict(group))
    safe_signal_filter = (
        deepcopy(signal_filter) if isinstance(signal_filter, dict) else {}
    )
    composite = safe_signal_filter.get("buy_composite")
    if isinstance(composite, dict):
        for group in composite.get("groups", []):
            if isinstance(group, dict) and isinstance(group.get("filters"), list):
                group["filters"] = [
                    item for item in group["filters"]
                    if str(item).strip().lower() != "price_compare"
                ]
    return {
        "basic": {
            key: deepcopy(value)
            for key, value in basic.items()
            if key in safe_basic_names
        },
        "buy_ui": {"signal_filter": safe_signal_filter},
        "sell_ui": {"signal_conditions": safe_conditions},
        "validation_execution": project_validation_execution_state(state),
        "validation_market_scope": project_validation_market_scope(state),
    }


def project_signal_validation_apply_ui_state(
    ui_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Return V2-visible values approved for source-dialog apply."""
    require_expression_aware_sell_price_selections(ui_state)
    require_resolved_buy_bollinger_sign_selection(dict(ui_state))
    projected = project_signal_validation_ui_state(ui_state)
    # Validation execution/averaging settings are chart-local.  Applying
    # validation results back to the routine dialog must not overwrite the
    # routine's BUY execution policy; only the existing signal/filter
    # projection crosses this boundary.
    projected.pop("validation_execution", None)
    projected.pop("validation_market_scope", None)
    signal_filter = projected.get("buy_ui", {}).get("signal_filter")
    if isinstance(signal_filter, dict):
        signal_filter.pop("buy_composite", None)
    return projected


def project_signal_validation_restore_ui_state(
    ui_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Return detached V2-visible entry values without Candidate validation."""
    return project_signal_validation_ui_state(ui_state)


def project_signal_validation_rules(
    rules: Mapping[str, Any],
    *,
    ui_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return detached candle-only evaluator rules without execution context."""
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    if ui_state is not None:
        require_expression_aware_sell_price_selections(ui_state)
        require_resolved_buy_bollinger_sign_selection(dict(ui_state))
    source_rules = _json_copy(rules)
    source_buy = source_rules.get("buy") if isinstance(source_rules.get("buy"), dict) else {}
    source_sell = source_rules.get("sell") if isinstance(source_rules.get("sell"), dict) else {}
    validation_visualization_rules = {
        "buy": {
            "filters": deepcopy(source_buy.get("filters", {})),
            "groups": deepcopy(source_buy.get("groups", [])),
        },
        "sell": {
            "filters": deepcopy(source_sell.get("filters", {})),
            "signals": _materialize_validation_sell_signals(source_rules),
        },
    }
    projected = _strip_dependent_conditions(source_rules)

    for key in ("buy_management", "order_policy", "cancel_policy"):
        projected.pop(key, None)
    projected.pop("signal_runtime_policy", None)

    principle = projected.get("principle")
    if isinstance(principle, dict):
        principle.pop("execution_enabled", None)
        principle.pop("order_execution_owner", None)
    projected.pop("execution_enabled", None)

    buy = projected.get("buy")
    if isinstance(buy, dict):
        buy.pop("execution", None)
        filters = buy.get("filters")
        if isinstance(filters, dict):
            filters.pop("price_compare", None)
            composite = filters.get("composite")
            if isinstance(composite, dict):
                for group in composite.get("groups", []):
                    if isinstance(group, dict) and isinstance(group.get("filters"), list):
                        group["filters"] = [
                            item for item in group["filters"]
                            if str(item).strip().lower() != "price_compare"
                        ]

    sell = projected.get("sell")
    if isinstance(sell, dict):
        sell.pop("method", None)
        filters = sell.get("filters")
        if isinstance(filters, dict):
            filters.pop("price_compare", None)
        sell["signals"] = _materialize_validation_sell_signals(source_rules)

    projected["validation_visualization_rules"] = validation_visualization_rules

    if ui_state is None:
        root = projected.get("indicator_follow_ui_state")
        source_state = root.get("state") if isinstance(root, dict) else {}
    else:
        source_state = ui_state
    projected_ui_state = project_signal_validation_ui_state(
        source_state if isinstance(source_state, Mapping) else {}
    )
    if ui_state is None and isinstance(source_rules.get("validation_timeframe"), Mapping):
        projected["validation_timeframe"] = validation_timeframe_from_rules(source_rules)
    else:
        projected["validation_timeframe"] = validation_timeframe_from_rules(
            source_rules,
            ui_state=projected_ui_state,
        )
    projected["validation_execution"] = deepcopy(
        projected_ui_state["validation_execution"]
    )
    if (
        ui_state is None
        and isinstance(source_rules.get("validation_market_scope"), Mapping)
    ):
        projected["validation_market_scope"] = normalize_validation_market_scope(
            source_rules.get("validation_market_scope")
        )
    else:
        projected["validation_market_scope"] = deepcopy(
            projected_ui_state["validation_market_scope"]
        )
    projected["indicator_follow_ui_state"] = {
        "ui_state_version": "0.1",
        "state": projected_ui_state,
    }
    projected.pop("indicator_follow_rule_preview", None)
    return projected


def _materialize_validation_sell_signals(
    source_rules: Mapping[str, Any],
) -> dict[str, Any]:
    preview_root = source_rules.get("indicator_follow_rule_preview")
    candidates = (
        preview_root.get("candidates") if isinstance(preview_root, Mapping) else None
    )
    sell_candidates = (
        candidates.get("sell") if isinstance(candidates, Mapping) else None
    )
    add_candidates = (
        sell_candidates.get("add_signal_candidates")
        if isinstance(sell_candidates, Mapping)
        else None
    )
    materialized: dict[str, Any] = {}
    if isinstance(add_candidates, Mapping):
        for preview_path, signal_name in _SELL_PREVIEW_TARGETS.items():
            candidate = add_candidates.get(preview_path)
            value = candidate.get("value") if isinstance(candidate, Mapping) else None
            if isinstance(value, Mapping) and value.get("preview_candidate") is True:
                materialized_value = _json_copy(value)
                materialized_value.pop("preview_candidate", None)
                materialized[signal_name] = materialized_value

        expression_contract = next(
            (
                signal.get("signal_expression")
                for signal in materialized.values()
                if isinstance(signal, Mapping)
                and isinstance(signal.get("signal_expression"), Mapping)
            ),
            None,
        )
        if isinstance(expression_contract, Mapping):
            identifier_map = expression_contract.get("identifier_map")
            identifiers = expression_contract.get("identifiers")
            if isinstance(identifier_map, Mapping) and isinstance(identifiers, list):
                missing = [
                    str(identifier)
                    for identifier in identifiers
                    if str(identifier_map.get(str(identifier).upper()) or "")
                    not in materialized
                ]
                if missing:
                    raise ValueError(
                        "SELL validation candidate is missing: " + ", ".join(missing)
                    )
        return materialized

    if isinstance(preview_root, Mapping):
        return {}

    sell = source_rules.get("sell")
    signals = sell.get("signals") if isinstance(sell, Mapping) else None
    if not isinstance(signals, Mapping):
        return {}
    return {
        name: _json_copy(value)
        for name, value in signals.items()
        if name in _SELL_VALIDATION_SIGNAL_NAMES and isinstance(value, Mapping)
    }


def build_signal_validation_snapshot(
    rules: Mapping[str, Any],
    *,
    ui_state: Mapping[str, Any] | None = None,
) -> ValidationSettingsSnapshot:
    return ValidationSettingsSnapshot(
        project_signal_validation_rules(rules, ui_state=ui_state)
    )
