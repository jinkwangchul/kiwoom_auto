# -*- coding: utf-8 -*-
"""Pure, detached contracts for candle-only indicator signal validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
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
                ui_state=signal_ui_state,
            )
        )
        object.__setattr__(self, "settings_snapshot", copied_snapshot)
        object.__setattr__(self, "_ui_state_json", canonical)

    def to_ui_state(self) -> dict[str, Any]:
        value = json.loads(self._ui_state_json)
        if not isinstance(value, dict):
            raise ValueError("canonical UI state must decode to an object")
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
        "basic_duplicate_signal_combo",
        "basic_error_policy_combo",
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
            safe_conditions[group_name] = {
                key: deepcopy(item)
                for key, item in group.items()
                if "_gap_" not in str(key).lower()
            }
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
    }


def project_signal_validation_rules(
    rules: Mapping[str, Any],
    *,
    ui_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return detached candle-only evaluator rules without execution context."""
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    projected = _strip_dependent_conditions(_json_copy(rules))

    for key in ("buy_management", "order_policy", "cancel_policy"):
        projected.pop(key, None)

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
        signals = sell.get("signals")
        if isinstance(signals, dict):
            signals.pop("profit_rate_sell", None)

    if ui_state is None:
        root = projected.get("indicator_follow_ui_state")
        source_state = root.get("state") if isinstance(root, dict) else {}
    else:
        source_state = ui_state
    projected["indicator_follow_ui_state"] = {
        "ui_state_version": "0.1",
        "state": project_signal_validation_ui_state(
            source_state if isinstance(source_state, Mapping) else {}
        ),
    }
    return projected


def build_signal_validation_snapshot(
    rules: Mapping[str, Any],
    *,
    ui_state: Mapping[str, Any] | None = None,
) -> ValidationSettingsSnapshot:
    return ValidationSettingsSnapshot(
        project_signal_validation_rules(rules, ui_state=ui_state)
    )
