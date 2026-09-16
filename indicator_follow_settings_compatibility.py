"""Pure compatibility rules for indicator-follow persisted UI state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_SELL_SELECTED_SET_WIDGET_FIELDS = {
    "a": "sell_method_select_a_check",
    "b": "sell_method_select_b_check",
    "c": "sell_method_select_c_check",
}


def get_canonical_fresh_defaults(definition_id: str) -> dict[str, Any] | None:
    """Return configured fresh defaults, or ``None`` while none are approved.

    This is the single future authority slot.  It intentionally contains no
    fallback to the legacy template and no strategy values today.
    """
    _ = str(definition_id or "").strip()
    return None


def normalize_sell_selected_set_authority(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Make ``sell_ui.selected_sets`` authoritative and mirrors derived.

    Older snapshots may have persisted the same checkboxes in ``basic`` or
    ``sell_ui.legacy_summary``.  Those fields are accepted only when the
    canonical selected-set mapping is absent.  An explicit canonical mapping
    always wins, including an invalid mapping that must remain visible to
    validation rather than being silently repaired.
    """
    normalized = deepcopy(state) if isinstance(state, dict) else state
    if not isinstance(normalized, dict):
        return normalized

    basic = normalized.get("basic")
    if not isinstance(basic, dict):
        basic = {}
        normalized["basic"] = basic
    sell_ui = normalized.get("sell_ui")
    if not isinstance(sell_ui, dict):
        sell_ui = {}
        normalized["sell_ui"] = sell_ui
    legacy_summary = sell_ui.get("legacy_summary")
    if not isinstance(legacy_summary, dict):
        legacy_summary = {}

    selected_sets = sell_ui.get("selected_sets")
    if not isinstance(selected_sets, dict):
        source = None
        if any(name in basic for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values()):
            source = basic
        elif any(
            name in legacy_summary
            for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values()
        ):
            source = legacy_summary
        if source is not None:
            selected_sets = {
                key: bool(source.get(name))
                for key, name in _SELL_SELECTED_SET_WIDGET_FIELDS.items()
            }
            sell_ui["selected_sets"] = selected_sets

    for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values():
        basic.pop(name, None)

    if isinstance(selected_sets, dict):
        derived_summary = dict(legacy_summary)
        for key, name in _SELL_SELECTED_SET_WIDGET_FIELDS.items():
            derived_summary[name] = bool(selected_sets.get(key))
        sell_ui["legacy_summary"] = derived_summary
    return normalized


def canonical_indicator_follow_ui_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Return the stable state shape used by exact round-trip comparisons."""
    normalized = normalize_sell_selected_set_authority(state)
    if not isinstance(normalized, dict):
        return normalized
    price_compare = (
        normalized.get("buy_ui", {}).get("price_compare", {})
        if isinstance(normalized.get("buy_ui"), dict)
        else {}
    )
    if (
        isinstance(price_compare, dict)
        and price_compare.get("condition_combo") == "=<"
    ):
        price_compare["condition_combo"] = "<="
    return normalized


def legacy_buy_bollinger_sign(signal_filter: dict[str, Any]) -> str | None:
    """Return the historical implicit BUY Bollinger sign, if it is knowable."""
    direction = str(signal_filter.get("buy_bollinger_direction_combo") or "").strip()
    return {
        "하향": "-",
        "상향": "+",
    }.get(direction)


def legacy_sell_signed_percent_sign(
    condition: dict[str, Any],
    *,
    compare_field: str,
) -> str | None:
    """Return the historical implicit SELL offset sign, if it is knowable."""
    compare = str(condition.get(compare_field) or "").strip().upper()
    return {
        "이상": "+",
        ">=": "+",
        "GTE": "+",
        "이하": "-",
        "<=": "-",
        "LTE": "-",
    }.get(compare)
